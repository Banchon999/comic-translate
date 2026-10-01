"""A stroke of a pixel tool (paint, restore, AI, clone, heal), from press to patch.

The session reads everything it needs on the press: the page composite as
the user sees it, the source it paints from (a flat colour, or the raw page for
the restore eraser), the canvas selection as alpha, and — in webtoon mode —
where the loaded slice sits. Moves stamp dabs into a coverage map
(``core.paint``) and repaint a preview of just the touched rectangle. The
release blends once and hands back a `PixelEdit`: the edited image and a mask
of what changed, which the window turns into ordinary inpaint patches through
the same seam Clean Selection uses — so a painted stroke is undoable, listed in
the Layers panel, exported as a patch layer, and never touches the raw image.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainterPath
from PySide6.QtWidgets import QGraphicsItem

from core import paint
from core.selection import blend

PAINT = "paint"
RESTORE = "restore"
#: The AI inpaint brush: the stroke is a mask for the inpainter, not paint.
AI = "aibrush"
#: The clone stamp paints the page from elsewhere on it; the healing brush
#: does the same, then takes the tone of the stroke's surroundings.
CLONE = "clone"
HEAL = "heal"
CLONE_MODES = (CLONE, HEAL)
#: The pixel tools that paint a stroke with the brush (press, drag, release).
BRUSH_TOOLS = (PAINT, RESTORE, AI, CLONE, HEAL)

#: How strongly the AI brush's preview tints what it covers (the mask
#: brush's translucent red), so it reads as "to be removed", not as paint.
AI_PREVIEW_ALPHA = 0.45

#: Above inpaint patches (0.5), below mask strokes (0.8) and text (1): where
#: the result will live once it is a patch.
PREVIEW_Z = 0.55


@dataclass
class PixelEdit:
    """The outcome of one stroke, in the image space of the session."""

    image: np.ndarray        # the composite with the stroke blended in
    mask: np.ndarray         # uint8 0/255, the pixels the stroke changed
    mappings: list | None    # webtoon visible-area mappings, or None
    mode: str


class PixelPreviewItem(QGraphicsItem):
    """The stroke so far, drawn over the page while the button is held.

    A plain QGraphicsItem, not a pixmap item: `scene_registry.kind_of` reads
    an un-hashed pixmap item as the raw image layer. The shape is empty so it
    never takes a click.
    """

    def __init__(self, x: int, y: int, width: int, height: int):
        super().__init__()
        self._buffer = np.zeros((height, width, 4), np.uint8)
        self._image = QImage(self._buffer.data, width, height, 4 * width, QImage.Format.Format_RGBA8888)
        self.setPos(float(x), float(y))
        self.setZValue(PREVIEW_Z)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self._image.width(), self._image.height())

    def shape(self) -> QPainterPath:
        return QPainterPath()

    def paint(self, painter, option, widget=None):
        painter.drawImage(0, 0, self._image)

    def put(self, x0: int, y0: int, rgb: np.ndarray, alpha: np.ndarray) -> None:
        """Write a rectangle of blended pixels; alpha 0 stays transparent."""
        h, w = alpha.shape
        region = self._buffer[y0:y0 + h, x0:x0 + w]
        region[..., :3] = rgb[..., :3]
        region[..., 3] = np.where(alpha > 0, 255, 0).astype(np.uint8)
        self.update(QRectF(x0, y0, w, h))


class CloneSourceMarker(QGraphicsItem):
    """A crosshair where the clone stamp / healing brush samples from.

    Drawn at a fixed size on screen (it ignores the view's zoom), in two pens
    so it shows on light and dark artwork alike. Not a layer, not a stroke,
    never hit by a click: a plain QGraphicsItem with an empty shape, like the
    stroke preview.
    """

    RADIUS = 9.0

    def __init__(self):
        super().__init__()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setZValue(5.0)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def boundingRect(self) -> QRectF:
        r = self.RADIUS + 3.0
        return QRectF(-r, -r, 2 * r, 2 * r)

    def shape(self) -> QPainterPath:
        return QPainterPath()

    def paint(self, painter, option, widget=None):
        from PySide6.QtGui import QPen

        r = self.RADIUS
        for colour, width in ((QColor(255, 255, 255), 3.0), (QColor(0, 0, 0), 1.0)):
            pen = QPen(colour, width)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0, 0), r * 0.6, r * 0.6)
            painter.drawLine(QPointF(-r, 0), QPointF(r, 0))
            painter.drawLine(QPointF(0, -r), QPointF(0, r))


class PixelSession:
    """One stroke. `begin` → `move`… → `finish` (or `cancel`)."""

    def __init__(self, viewer, mode: str, colour: QColor, diameter: float, hardness: float,
                 opacity: float, pressure_size: bool = False, pressure_flow: bool = False,
                 clone_offset: tuple[float, float] | None = None):
        self.viewer = viewer
        # Clone/heal: where the source is relative to the brush, in pixels.
        self.clone_offset = clone_offset
        self.mode = mode
        self.colour = QColor(colour)
        self.diameter = max(1.0, float(diameter))
        # The AI brush marks pixels in or out: a soft edge would only make the
        # mask's boundary depend on a threshold.
        self.hardness = 1.0 if mode == AI else float(hardness)
        self.opacity = min(1.0, max(0.0, float(opacity)))
        self.pressure_size = pressure_size
        self.pressure_flow = pressure_flow
        self.preview: PixelPreviewItem | None = None
        self._last: tuple[float, float] | None = None
        self._carry = 0.0

    # -- lifecycle -------------------------------------------------------------
    def begin(self, scene_pos: QPointF, pressure=None) -> bool:
        viewer = self.viewer
        composite = viewer.get_image_array(include_patches=True)
        if composite is None:
            return False
        self.mappings = None
        if viewer.webtoon_mode:
            _, self.mappings = viewer.get_visible_area_image()
        self.offset = viewer.drawing_manager._visible_area_offset()
        self.composite = composite[..., :3].copy()
        height, width = self.composite.shape[:2]
        if self.mode == RESTORE:
            raw = viewer.get_image_array(include_patches=False)
            if raw is None or raw.shape[:2] != (height, width):
                return False
            self.source = raw[..., :3].copy()
        elif self.mode in CLONE_MODES:
            if self.clone_offset is None:
                return False
            # Sampled from the page as it was at the press, so the stroke
            # never clones what it has just painted.
            self.source, self.source_valid = paint.shifted(self.composite, *self.clone_offset)
        else:
            colour = QColor(255, 0, 0) if self.mode == AI else self.colour
            self.source = np.empty_like(self.composite)
            self.source[...] = (colour.red(), colour.green(), colour.blue())
        if self.mode not in CLONE_MODES:
            self.source_valid = None
        ox, oy = self.offset
        self.selection_alpha = None
        if not viewer.selection.is_empty():
            self.selection_alpha = viewer.selection.alpha(ox, oy, width, height)
        self.coverage = np.zeros((height, width), np.float32)
        self.preview = PixelPreviewItem(ox, oy, width, height)
        viewer._scene.addItem(self.preview)
        point = self._local(scene_pos)
        self._stamp(point, pressure)
        self._last = point
        return True

    def move(self, scene_pos: QPointF, pressure=None) -> None:
        if self._last is None:
            return
        point = self._local(scene_pos)
        radius = self._radius(pressure)
        positions, self._carry = paint.dab_positions(
            self._last, point, paint.spacing_for(radius), self._carry
        )
        for position in positions:
            self._stamp(position, pressure)
        self._last = point

    def finish(self) -> PixelEdit | None:
        """Blend the stroke into the composite. None when nothing changed."""
        if self._last is None:
            return None
        self._remove_preview()
        alpha = self._alpha(np.s_[:, :])
        if not (alpha > 0).any():
            self._last = None
            return None
        if self.mode == AI:
            # Nothing is blended: the stroke is what the inpainter must fill.
            self._last = None
            mask = (alpha > 0).astype(np.uint8) * 255
            return PixelEdit(image=self.composite, mask=mask, mappings=self.mappings, mode=self.mode)
        source = self.source
        if self.mode == HEAL:
            source = paint.heal(self.composite, self.source, alpha > 0, valid=self.source_valid)
        image = blend(self.composite, source, alpha)
        changed = np.any(image != self.composite, axis=2)
        self._last = None
        if not changed.any():
            return None
        mask = changed.astype(np.uint8) * 255
        return PixelEdit(image=image, mask=mask, mappings=self.mappings, mode=self.mode)

    def cancel(self) -> None:
        self._remove_preview()
        self._last = None

    @property
    def active(self) -> bool:
        return self._last is not None

    # -- internals -------------------------------------------------------------
    def _local(self, scene_pos: QPointF) -> tuple[float, float]:
        ox, oy = self.offset
        return float(scene_pos.x()) - ox, float(scene_pos.y()) - oy

    def _radius(self, pressure) -> float:
        return self.diameter / 2.0 * paint.pressure_scale(pressure, self.pressure_size)

    def _alpha(self, region) -> np.ndarray:
        opacity = 1.0 if self.mode == AI else self.opacity
        alpha = self.coverage[region] * opacity
        if self.selection_alpha is not None:
            alpha = alpha * self.selection_alpha[region]
        if self.source_valid is not None:
            # Nothing to clone from beyond the page's edge.
            alpha = alpha * self.source_valid[region]
        return alpha

    def _stamp(self, point, pressure) -> None:
        flow = paint.pressure_scale(pressure, self.pressure_flow)
        rect = paint.stamp(self.coverage, point[0], point[1], self._radius(pressure), self.hardness, flow)
        if rect is None or self.preview is None:
            return
        x0, y0, x1, y1 = rect
        region = np.s_[y0:y1, x0:x1]
        alpha = self._alpha(region)
        shown = alpha * AI_PREVIEW_ALPHA if self.mode == AI else alpha
        rgb = blend(self.composite[region], self.source[region], shown)
        try:
            self.preview.put(x0, y0, rgb, alpha)
        except RuntimeError:
            self.preview = None   # the scene was cleared mid-stroke

    def _remove_preview(self) -> None:
        preview, self.preview = self.preview, None
        if preview is None:
            return
        try:
            if preview.scene() is not None:
                preview.scene().removeItem(preview)
        except RuntimeError:
            pass
