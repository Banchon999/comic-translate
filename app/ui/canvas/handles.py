"""Resize handles for the selectable items on the canvas.

Two things were wrong with how these worked. Nothing drew them, so the only
way to find a corner was to sweep the mouse along the edge until the cursor
changed; and the grab area was measured in image pixels, so it shrank with the
zoom — 20px at 1:1 became 5px at 25%, which is when you are most likely to be
dragging a box around.

Both the drawing and the hit test are sized here in *screen* pixels, so a
handle is the same size to the hand no matter how far the page is zoomed out.
The grab area is deliberately larger than the drawn square: aiming at a mark
is easier when the target is more forgiving than it looks.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

#: Side of the drawn handle, in screen pixels.
HANDLE_PX = 8.0
#: Side of the area that responds to the mouse, in screen pixels.
HANDLE_HIT_PX = 16.0

CORNERS = ('top_left', 'top_right', 'bottom_left', 'bottom_right')
EDGES = ('top', 'bottom', 'left', 'right')

HANDLE_FILL = QColor(255, 255, 255)
HANDLE_BORDER = QColor(40, 44, 52)
HANDLE_BORDER_PX = 1.0


def handle_centres(rect: QRectF) -> dict[str, QPointF]:
    """Where each handle sits, in the item's own coordinates."""
    left, right = rect.left(), rect.right()
    top, bottom = rect.top(), rect.bottom()
    mid_x, mid_y = rect.center().x(), rect.center().y()

    return {
        'top_left': QPointF(left, top),
        'top_right': QPointF(right, top),
        'bottom_left': QPointF(left, bottom),
        'bottom_right': QPointF(right, bottom),
        'top': QPointF(mid_x, top),
        'bottom': QPointF(mid_x, bottom),
        'left': QPointF(left, mid_y),
        'right': QPointF(right, mid_y),
    }


def _square(centre: QPointF, side: float) -> QRectF:
    return QRectF(centre.x() - side / 2, centre.y() - side / 2, side, side)


#: Most of the smallest side a handle may take up.
#:
#: Below this, the handles meet in the middle and the box has no interior left
#: to grab — every drag resizes it and it can never be moved. That is not a
#: hypothetical: at 10% zoom a nominal handle is 160 image pixels, which is
#: wider than most detection boxes. Anything under 1 leaves the centre free
#: (a corner only reaches the centre once its side exceeds the longer edge),
#: and 0.8 is high enough that a normal one-line text box, where the handle is
#: already about as tall as the box, is not made harder to hit.
_MAX_SIDE_FRACTION = 0.8


def _hit_side(rect: QRectF, scale: float) -> float:
    """The grab square's side in item space, kept off the centre of the box."""
    nominal = HANDLE_HIT_PX / scale
    if rect.isEmpty():
        return nominal
    return min(nominal, _MAX_SIDE_FRACTION * min(rect.width(), rect.height()))


def handle_at(pos: QPointF, rect: QRectF, scale: float) -> str | None:
    """Which handle is under `pos`, or None.

    Corners are tested first: they overlap the edge handles, and a corner is
    the more useful thing to hit when the two compete.
    """
    if scale <= 0:
        return None

    side = _hit_side(rect, scale)
    centres = handle_centres(rect)

    for name in CORNERS:
        if _square(centres[name], side).contains(pos):
            return name

    # Edge handles span the whole side so the middle of an edge can be grabbed
    # anywhere along it, which is how every other editor behaves.
    half = side / 2
    edges = {
        'top': QRectF(rect.left(), rect.top() - half, rect.width(), side),
        'bottom': QRectF(rect.left(), rect.bottom() - half, rect.width(), side),
        'left': QRectF(rect.left() - half, rect.top(), side, rect.height()),
        'right': QRectF(rect.right() - half, rect.top(), side, rect.height()),
    }
    for name in EDGES:
        if edges[name].contains(pos):
            return name

    return None


def paint_handles(painter: QPainter, rect: QRectF, scale: float) -> None:
    """Draw the eight handles around `rect`.

    They are drawn just inside the rect rather than centred on its edge, so
    they stay within the item's bounding rectangle — painting outside it leaves
    stale pixels behind, since that is the only region Qt repaints.
    """
    if scale <= 0 or rect.isEmpty():
        return

    # Derived from the grab size rather than computed separately, so what is
    # drawn keeps its usual proportion to what responds even on a small box,
    # where _hit_side shrinks the target.
    side = _hit_side(rect, scale) * (HANDLE_PX / HANDLE_HIT_PX)
    # Pull the centres inward by half a handle so the squares sit flush against
    # the inside of the border.
    inset = rect.adjusted(side / 2, side / 2, -side / 2, -side / 2)
    if inset.width() <= 0 or inset.height() <= 0:
        # The item is smaller than the handles; corners alone, unclamped, would
        # overlap into a smear. Fall back to the four corners of what there is.
        inset = rect

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(HANDLE_BORDER)
    pen.setWidthF(HANDLE_BORDER_PX / scale)
    pen.setCosmetic(False)
    painter.setPen(pen)
    painter.setBrush(HANDLE_FILL)

    for centre in handle_centres(inset).values():
        painter.drawRect(_square(centre, side))

    painter.restore()


def item_view_scale(item, option=None, painter: QPainter | None = None) -> float:
    """The item's local-to-screen scale factor.

    Uses Qt's level-of-detail when painting, which stays correct for rotated
    items where a single matrix element does not.
    """
    if option is not None and painter is not None:
        try:
            from PySide6.QtWidgets import QStyleOptionGraphicsItem

            lod = QStyleOptionGraphicsItem.levelOfDetailFromTransform(
                painter.worldTransform()
            )
            if lod > 0:
                return lod
        except Exception:
            pass

    scene = item.scene() if hasattr(item, 'scene') else None
    views = scene.views() if scene is not None else []
    if not views:
        return 1.0
    transform = item.deviceTransform(views[0].viewportTransform())
    return max(1e-6, (transform.m11() ** 2 + transform.m12() ** 2) ** 0.5)
