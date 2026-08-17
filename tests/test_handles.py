"""Resize handles stay the same size to the hand at every zoom.

The grab area used to be measured in image pixels, so a 20px margin was 5px at
25% zoom — which is exactly when boxes get dragged around. Everything here is
geometry against an explicit scale, so no view or scene is needed.
"""

import pytest
from PySide6.QtCore import QPointF, QRectF

from app.ui.canvas import handles

RECT = QRectF(0, 0, 160, 90)
ALL_HANDLES = handles.CORNERS + handles.EDGES


def centre_of(name, rect=RECT):
    return handles.handle_centres(rect)[name]


@pytest.mark.parametrize("zoom", [4.0, 2.0, 1.0, 0.5, 0.25, 0.1])
def test_grab_area_is_constant_in_screen_pixels(zoom):
    # side is in item space; multiplied back by the zoom it is what the hand sees.
    side_in_item_space = handles.HANDLE_HIT_PX / zoom
    assert side_in_item_space * zoom == pytest.approx(handles.HANDLE_HIT_PX)


@pytest.mark.parametrize("zoom", [4.0, 1.0, 0.25, 0.1])
@pytest.mark.parametrize("name", ALL_HANDLES)
def test_every_handle_is_reachable_at_every_zoom(zoom, name):
    assert handles.handle_at(centre_of(name), RECT, zoom) == name


@pytest.mark.parametrize("zoom", [4.0, 1.0, 0.25, 0.1, 0.01])
@pytest.mark.parametrize(
    "rect",
    [
        QRectF(0, 0, 160, 90),
        QRectF(0, 0, 400, 20),  # a one-line text box
        QRectF(0, 0, 24, 24),  # a tiny detection box
        QRectF(0, 0, 2000, 3000),  # a whole webtoon strip
    ],
)
def test_the_middle_of_the_box_is_always_grabbable(rect, zoom):
    """Otherwise the box can only ever be resized, never moved.

    Zoomed far out, a handle sized in screen pixels is enormous in image space —
    wide enough to swallow a whole detection box — so the grab area has to be
    clamped against the box rather than the zoom alone.
    """
    assert handles.handle_at(rect.center(), rect, zoom) is None


def test_corners_win_where_they_overlap_an_edge():
    # A corner and the edges either side of it both cover this point; the corner
    # is the more useful thing to hit.
    just_inside = QPointF(RECT.left() + 1, RECT.top() + 1)
    assert handles.handle_at(just_inside, RECT, 1.0) == "top_left"


def test_an_edge_can_be_grabbed_anywhere_along_it():
    quarter = QPointF(RECT.left() + RECT.width() * 0.25, RECT.top())
    three_quarters = QPointF(RECT.left() + RECT.width() * 0.75, RECT.top())
    assert handles.handle_at(quarter, RECT, 1.0) == "top"
    assert handles.handle_at(three_quarters, RECT, 1.0) == "top"


def test_a_point_well_outside_hits_nothing():
    assert handles.handle_at(QPointF(-500, -500), RECT, 1.0) is None


def test_a_nonsense_scale_is_refused_rather_than_dividing_by_zero():
    assert handles.handle_at(RECT.topLeft(), RECT, 0.0) is None
    assert handles.handle_at(RECT.topLeft(), RECT, -1.0) is None


def test_handle_centres_sit_on_the_rect():
    centres = handles.handle_centres(RECT)
    assert centres["top_left"] == RECT.topLeft()
    assert centres["bottom_right"] == RECT.bottomRight()
    assert centres["top"].y() == RECT.top()
    assert centres["left"].x() == RECT.left()
    assert len(centres) == len(ALL_HANDLES)


def test_painting_stays_inside_the_bounding_rect(qapp):
    """Qt only repaints boundingRect, so anything drawn outside leaves smears."""
    from PySide6.QtGui import QImage, QPainter

    image = QImage(400, 300, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    painter.translate(100, 100)
    handles.paint_handles(painter, RECT, 1.0)
    painter.end()

    painted = [
        (x, y)
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() > 0
    ]
    assert painted, "nothing was drawn at all"
    xs = [x - 100 for x, _ in painted]
    ys = [y - 100 for _, y in painted]
    # One pixel of slack for the antialiased pen edge.
    assert min(xs) >= RECT.left() - 1 and max(xs) <= RECT.right() + 1
    assert min(ys) >= RECT.top() - 1 and max(ys) <= RECT.bottom() + 1


def test_an_empty_rect_paints_nothing_rather_than_crashing(qapp):
    from PySide6.QtGui import QImage, QPainter

    image = QImage(50, 50, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    handles.paint_handles(painter, QRectF(), 1.0)
    painter.end()
