"""The canvas selection: a region the next action is limited to.

A selection is not a mask stroke. The red stroke marks what the Clean step will
inpaint; a selection marks where an action — clean it, turn it into a mask,
later paint inside it — applies, and nothing happens to the page until one is
chosen. It is drawn as marching ants and is never saved, exported or listed as
a layer.

It is kept as a ``QPainterPath`` in **scene** coordinates. In webtoon mode the
image a consumer works on is only the visible slice of the strip, and which
slice that is changes as the user scrolls; a path in scene coordinates means
the same pixels whatever is loaded, and each consumer rasterises it into its
own image space (``rasterise``). Combining and refining work on pixels —
``core.selection`` — over the path's bounding box only, and come back as a path.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QUndoCommand
from PySide6.QtWidgets import QGraphicsItem

from core import selection as selection_ops
from modules.utils.flood_select import mask_to_polygons

from .stroke_mask import qimage_to_np

REPLACE = "replace"
ADD = "add"
SUBTRACT = "subtract"
INTERSECT = "intersect"

#: Above every page item (text and boxes sit at 1), below nothing that matters.
OVERLAY_Z = 10_000

#: Milliseconds per step of the marching ants.
ANT_INTERVAL_MS = 120


def mode_for_modifiers(modifiers) -> str:
    """Shift adds, Alt subtracts, both intersect — as in every image editor."""
    shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
    alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
    if shift and alt:
        return INTERSECT
    if shift:
        return ADD
    if alt:
        return SUBTRACT
    return REPLACE


def rasterise_path(path: QPainterPath, x: int, y: int, width: int, height: int) -> np.ndarray:
    """The path's pixels inside the rect (x, y, width, height) as a 0/255 mask."""
    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        return np.zeros((max(height, 0), max(width, 0)), np.uint8)
    image = QImage(width, height, QImage.Format.Format_Grayscale8)
    image.fill(0)
    if path is not None and not path.isEmpty():
        painter = QPainter(image)
        # No antialiasing: a selection is in or out. Softness is feather's job,
        # applied where the selection is used, not baked into its outline.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.translate(-x, -y)
        painter.fillPath(path, QColor(255, 255, 255))
        # Outlines traced from a mask run through the centres of its boundary
        # pixels, and a plain fill only takes pixels whose centre is strictly
        # inside — so every refine would lose the right and bottom edge. A
        # one-pixel stroke along the outline puts exactly those pixels back,
        # which makes mask → path → mask exact (tests/test_selection.py).
        painter.setPen(QPen(QColor(255, 255, 255), 1))
        painter.drawPath(path)
        painter.end()
    return qimage_to_np(image).copy()


def path_from_mask(mask: np.ndarray, x: int = 0, y: int = 0) -> QPainterPath:
    """A mask's outlines as a path, offset to (x, y).

    Enclosed gaps come back from ``find_contours`` wound opposite to their
    outer contour, so ``WindingFill`` keeps them empty — a selection with a
    hole punched out of it stays that way.
    """
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    for points in mask_to_polygons(mask, min_area=1):
        path.moveTo(QPointF(float(points[0][0] + x), float(points[0][1] + y)))
        for px, py in points[1:]:
            path.lineTo(QPointF(float(px + x), float(py + y)))
        path.closeSubpath()
    return path


class SelectionOverlay(QGraphicsItem):
    """Marching ants around the selection.

    Deliberately not a ``QGraphicsPathItem``: dozens of places treat any path
    item as a mask stroke (mask generation, saving, the eraser, undo matching),
    and the selection must be none of those. Its shape is empty, so it is never
    hit by a click and never stands between the cursor and a text item.
    """

    def __init__(self, path: QPainterPath):
        super().__init__()
        self._path = QPainterPath(path)
        self._dash_offset = 0.0
        self.setZValue(OVERLAY_Z)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, False)

    def set_path(self, path: QPainterPath) -> None:
        self.prepareGeometryChange()
        self._path = QPainterPath(path)
        self.update()

    def advance_ants(self) -> None:
        self._dash_offset = (self._dash_offset + 1.0) % 8.0
        self.update()

    def _margin(self) -> float:
        # Cosmetic pens are one screen pixel wide whatever the zoom, so the
        # repaint margin has to be worked out in scene units from the view.
        for view in self.scene().views() if self.scene() else ():
            scale = view.transform().m11() or 1.0
            return 3.0 / abs(scale)
        return 3.0

    def boundingRect(self) -> QRectF:
        if self._path.isEmpty():
            return QRectF()
        m = self._margin()
        return self._path.boundingRect().adjusted(-m, -m, m, m)

    def shape(self) -> QPainterPath:
        return QPainterPath()

    def paint(self, painter, option, widget=None):
        if self._path.isEmpty():
            return
        light = QPen(QColor(255, 255, 255), 1)
        light.setCosmetic(True)
        painter.setPen(light)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._path)
        dark = QPen(QColor(0, 0, 0), 1, Qt.PenStyle.CustomDashLine)
        dark.setCosmetic(True)
        dark.setDashPattern([4.0, 4.0])
        dark.setDashOffset(self._dash_offset)
        painter.setPen(dark)
        painter.drawPath(self._path)


class SelectionCommand(QUndoCommand):
    """One change to the selection, undoable like any other canvas edit."""

    def __init__(self, manager: "SelectionManager", old, new, text: str = "Selection"):
        super().__init__(text)
        self.manager = manager
        self.old = old
        self.new = new

    def redo(self):
        self.manager._apply(*self.new)

    def undo(self):
        self.manager._apply(*self.old)


class SelectionManager:
    """Owns the viewer's selection, its overlay and the ants timer."""

    def __init__(self, viewer):
        self.viewer = viewer
        self.path = QPainterPath()
        self.feather = 0
        self._overlay: SelectionOverlay | None = None
        self._timer = QTimer(viewer)
        self._timer.setInterval(ANT_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

    # -- state -------------------------------------------------------------
    def is_empty(self) -> bool:
        return self.path.isEmpty() or self.path.boundingRect().isEmpty()

    def snapshot(self):
        return QPainterPath(self.path), int(self.feather)

    def _apply(self, path: QPainterPath, feather: int) -> None:
        self.path = QPainterPath(path)
        self.path.setFillRule(Qt.FillRule.WindingFill)
        self.feather = int(feather)
        self._sync_overlay()
        self.viewer.selection_changed.emit(not self.is_empty())

    def _change(self, path: QPainterPath, feather: int | None = None, text: str = "Selection") -> None:
        """Record a change as an undo step (or apply it directly when there is
        no stack to record it on, e.g. a viewer used on its own)."""
        feather = self.feather if feather is None else int(feather)
        old = self.snapshot()
        new = (QPainterPath(path), feather)
        command = SelectionCommand(self, old, new, text)
        self.viewer.command_emitted.emit(command)
        # command_emitted pushes, and pushing runs redo(); without a listener
        # nothing ran it, so apply here — _apply is idempotent.
        if self.path != new[0] or self.feather != feather:
            self._apply(*new)

    def forget(self) -> None:
        """Drop the selection without an undo step: the page it belonged to is
        gone (page switch, project load, clear_scene)."""
        self.path = QPainterPath()
        self.feather = 0
        self._timer.stop()
        overlay, self._overlay = self._overlay, None
        if overlay is not None:
            try:
                if overlay.scene() is not None:
                    overlay.scene().removeItem(overlay)
            except RuntimeError:
                pass  # already deleted along with the scene
        self.viewer.selection_changed.emit(False)

    def shift_below(self, threshold_y: float, dy: float) -> None:
        """Move the selection by `dy` if it lies below `threshold_y` — a webtoon
        page above it loaded at a different height than estimated, and the
        content it outlines moved. Not an undo step: nothing the user did."""
        if self.is_empty() or not dy:
            return
        if self.path.boundingRect().top() < threshold_y:
            return
        self.path.translate(0.0, float(dy))
        self._sync_overlay()

    # -- geometry ------------------------------------------------------------
    def page_area(self) -> QRectF:
        """The pixels a selection can cover: the page, or in webtoon mode the
        loaded slice consumers work on. Everything is clipped to this, so
        Select All and Invert never produce a strip-sized array."""
        viewer = self.viewer
        if viewer.webtoon_mode:
            image, mappings = viewer.get_visible_area_image()
            if image is None or not mappings:
                return QRectF()
            top = float(min(m['scene_y_start'] for m in mappings))
            return QRectF(0.0, top, float(image.shape[1]), float(image.shape[0]))
        if viewer.photo is None or viewer.photo.pixmap().isNull():
            return QRectF()
        return viewer.photo.sceneBoundingRect()

    def rasterise(self, x: int, y: int, width: int, height: int) -> np.ndarray:
        """The hard selection inside an image placed at scene (x, y)."""
        return rasterise_path(self.path, x, y, width, height)

    def alpha(self, x: int, y: int, width: int, height: int) -> np.ndarray:
        """The selection as 0..1 alpha, feathered, inside the given image rect.

        The edge of the image is not an outline of the selection: a selection
        running off the page (or off the loaded webtoon slice) continues past
        it, so the hard mask is extended with its own edge values before
        feathering. Padding with empty space instead faded every selection
        where it met the border, and cleaning left the old lettering there.
        """
        mask = rasterise_path(self.path, x, y, width, height)
        pad = int(self.feather)
        if pad <= 0 or mask.size == 0:
            return selection_ops.feather_alpha(mask, 0)
        extended = np.pad(mask, pad, mode="edge")
        alpha = selection_ops.feather_alpha(extended, pad)
        return alpha[pad:pad + mask.shape[0], pad:pad + mask.shape[1]]

    def _work_rect(self, *paths: QPainterPath, margin: int = 0) -> QRect | None:
        area = self.page_area()
        if area.isEmpty():
            return None
        rect = QRectF()
        for path in paths:
            if path is not None and not path.isEmpty():
                rect = rect.united(path.boundingRect())
        if rect.isEmpty():
            return None
        rect = rect.adjusted(-margin, -margin, margin, margin).intersected(area)
        if rect.isEmpty():
            return None
        return rect.toAlignedRect()

    # -- operations ------------------------------------------------------------
    def combine(self, path: QPainterPath, mode: str = REPLACE, text: str = "Selection") -> None:
        """Bring a new region into the selection the way the modifiers asked."""
        if mode == REPLACE or self.is_empty():
            if mode in (SUBTRACT, INTERSECT):
                return  # nothing to take away from, or to keep
            rect = self._work_rect(path)
            if rect is None:
                return
            mask = rasterise_path(path, rect.x(), rect.y(), rect.width(), rect.height())
            self._change(path_from_mask(mask, rect.x(), rect.y()), 0, text)
            return

        rect = self._work_rect(self.path, path)
        if rect is None:
            return
        current = rasterise_path(self.path, rect.x(), rect.y(), rect.width(), rect.height()) > 0
        incoming = rasterise_path(path, rect.x(), rect.y(), rect.width(), rect.height()) > 0
        if mode == ADD:
            result = current | incoming
        elif mode == SUBTRACT:
            result = current & ~incoming
        else:
            result = current & incoming
        self._change(path_from_mask(result.astype(np.uint8) * 255, rect.x(), rect.y()), None, text)

    def select_all(self) -> None:
        area = self.page_area()
        if area.isEmpty():
            return
        path = QPainterPath()
        path.addRect(area)
        self._change(path, 0, "Select All")

    def deselect(self) -> None:
        if not self.is_empty():
            self._change(QPainterPath(), 0, "Deselect")

    def invert(self) -> None:
        rect = self._work_rect(self._area_path())
        if rect is None:
            return
        mask = self.rasterise(rect.x(), rect.y(), rect.width(), rect.height())
        self._change(path_from_mask(selection_ops.invert(mask), rect.x(), rect.y()), None, "Invert Selection")

    def _area_path(self) -> QPainterPath:
        path = QPainterPath()
        area = self.page_area()
        if not area.isEmpty():
            path.addRect(area)
        return path

    def refine(self, operation: str, px: int) -> None:
        """Grow, shrink or smooth the outline by `px`, or set its feather."""
        if self.is_empty():
            return
        px = max(0, int(px))
        if operation == "feather":
            if px != self.feather:
                self._change(self.path, px, "Feather Selection")
            return
        ops = {"grow": selection_ops.grow, "shrink": selection_ops.shrink, "smooth": selection_ops.smooth}
        if operation not in ops or px == 0:
            return
        rect = self._work_rect(self.path, margin=px + 2)
        if rect is None:
            return
        mask = self.rasterise(rect.x(), rect.y(), rect.width(), rect.height())
        result = ops[operation](mask, px)
        labels = {"grow": "Grow Selection", "shrink": "Shrink Selection", "smooth": "Smooth Selection"}
        self._change(path_from_mask(result, rect.x(), rect.y()), None, labels[operation])

    # -- overlay -------------------------------------------------------------
    def _sync_overlay(self) -> None:
        scene = self.viewer._scene
        if self.is_empty():
            self._timer.stop()
            if self._overlay is not None:
                try:
                    if self._overlay.scene() is not None:
                        self._overlay.scene().removeItem(self._overlay)
                except RuntimeError:
                    pass
                self._overlay = None
            return
        overlay = self._overlay
        try:
            alive = overlay is not None and overlay.scene() is scene
        except RuntimeError:
            alive = False
        if not alive:
            overlay = self._overlay = SelectionOverlay(self.path)
            scene.addItem(overlay)
        else:
            overlay.set_path(self.path)
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self) -> None:
        overlay = self._overlay
        if overlay is None or not self.viewer.isVisible():
            return
        try:
            overlay.advance_ants()
        except RuntimeError:
            self._overlay = None
            self._timer.stop()

    def overlay(self) -> SelectionOverlay | None:
        return self._overlay
