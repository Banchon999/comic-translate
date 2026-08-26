"""Cleaning an area marked with the box tool.

Automatic cleaning sometimes leaves residual lettering behind. The obvious
recovery is to draw a box over what is left and press Clean — and that did
nothing at all: no patch, no error, no message, which reads as a broken button.

Two reasons, both fixed here:

* `generate_mask_from_strokes` only rasterised `QGraphicsPathItem` strokes, so
  a `MoveableRectItem` from the box tool contributed nothing and the mask came
  back empty. `manual_inpaint` then returned `None` and `inpaint_and_set` fell
  off the end of an `if` with no `else`.
* Nothing said so.

The load-bearing subtlety is *which* boxes count. `viewer.rectangles` also
holds every box detection produced; cleaning all of them would wipe each
detected region wholesale. Only **selected** boxes are cleaned, and a box drawn
by hand is selected as it is created while a detected one never is — so these
tests pin both halves, not just the one that makes the feature work.
"""

import numpy as np
import pytest
from PySide6.QtCore import QPointF, QRectF


@pytest.fixture
def viewer(qapp):
    from app.ui.canvas.image_viewer import ImageViewer

    view = ImageViewer(None)
    view.resize(400, 300)
    view.display_image_array(np.full((200, 300, 3), 200, dtype=np.uint8))
    yield view
    view.close()


def test_nothing_marked_means_nothing_to_clean(viewer):
    assert not viewer.has_drawn_elements()
    assert viewer.get_mask_for_inpainting() is None


def test_an_unselected_box_is_not_cleaned(viewer):
    """A detected box must not be inpainted just because Clean was pressed.

    Detection adds boxes through `add_rectangle` and never selects them. If
    those counted, one Clean press would erase every detected region on the
    page — far more destructive than the bug being fixed.
    """
    viewer.add_rectangle(QRectF(0, 0, 80, 40), QPointF(50, 60))

    assert not viewer.has_drawn_elements(), (
        "an unselected box counted as marked — pressing Clean would wipe every "
        "box the detector added"
    )
    assert viewer.get_mask_for_inpainting() is None


def test_a_selected_box_produces_a_mask_over_it(viewer):
    """The case the user hit: mark the leftover text, press Clean."""
    rect = viewer.add_rectangle(QRectF(0, 0, 80, 40), QPointF(50, 60))
    viewer.select_rectangle(rect)

    assert viewer.has_drawn_elements()
    mask = viewer.get_mask_for_inpainting()
    assert mask is not None, "a selected box still produced no mask"

    ys, xs = np.nonzero(mask)
    assert xs.size, "the mask is empty"
    # The region layer grows the shape to close its seam, as it does for the
    # lasso and wand, so the mask covers at least the box rather than exactly it.
    assert xs.min() <= 50 and xs.max() >= 130 - 1
    assert ys.min() <= 60 and ys.max() >= 100 - 1
    # ...but it must stay local, not flood the page.
    assert xs.min() > 20 and xs.max() < 180
    assert ys.min() > 30 and ys.max() < 150


def test_a_box_drawn_by_hand_is_selected_so_clean_finds_it(viewer):
    """Draw, then Clean — with no click in between.

    Selection is what separates a hand-drawn box from a detected one, so the
    box tool has to select what it creates or the whole feature needs an
    undiscoverable extra step.
    """
    from app.ui.canvas.rectangle import MoveableRectItem

    viewer.set_tool('box')
    rect = MoveableRectItem(QRectF(0, 0, 60, 30), None)
    viewer._scene.addItem(rect)
    rect.setPos(QPointF(40, 40))
    viewer.current_rect = rect

    viewer.event_handler._release_handle_box_creation()

    assert rect in viewer.rectangles
    assert rect.selected, "the box tool did not select the box it just created"
    assert viewer.has_drawn_elements()


def test_a_zero_sized_box_is_discarded_not_cleaned(viewer):
    """A stray click makes an empty rect; it must not become a clean region."""
    from app.ui.canvas.rectangle import MoveableRectItem

    viewer.set_tool('box')
    rect = MoveableRectItem(QRectF(0, 0, 0, 0), None)
    viewer._scene.addItem(rect)
    viewer.current_rect = rect

    viewer.event_handler._release_handle_box_creation()

    assert rect not in viewer.rectangles
    assert not viewer.has_drawn_elements()
