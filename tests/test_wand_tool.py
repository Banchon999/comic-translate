"""The wand puts a real stroke on the canvas, indistinguishable from a brushed one.

That equivalence is the whole design: mask generation, undo, the layer panels
and project saving all already handle brush strokes, so producing the same kind
of item means none of them needed changing.
"""

import numpy as np
import pytest
from PySide6 import QtWidgets
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QGraphicsPathItem


@pytest.fixture
def viewer(qapp):
    from app.ui.canvas.image_viewer import ImageViewer

    view = ImageViewer(None)
    view.resize(400, 300)

    page = np.full((120, 200, 3), 128, dtype=np.uint8)
    page[20:70, 20:100] = 0      # bubble outline
    page[24:66, 24:96] = 255     # bubble interior
    view.display_image_array(page)
    return view


def stroke_items(view):
    photo = getattr(view, "photo", None)
    return [
        item for item in view._scene.items()
        if isinstance(item, QGraphicsPathItem) and item is not photo
    ]


def test_a_click_inside_the_bubble_adds_one_stroke(viewer):
    assert stroke_items(viewer) == []
    viewer.drawing_manager.flood_fill_at(QPointF(60, 45))
    assert len(stroke_items(viewer)) == 1


def test_the_stroke_covers_the_bubble_and_not_the_page(viewer):
    viewer.drawing_manager.flood_fill_at(QPointF(60, 45))
    path = stroke_items(viewer)[0].path()
    assert path.contains(QPointF(60, 45)), "did not cover the point clicked"
    assert not path.contains(QPointF(5, 5)), "leaked out onto the page"


def test_the_stroke_is_filled_so_it_becomes_a_mask(viewer):
    """An unfilled outline would generate an empty inpainting mask."""
    viewer.drawing_manager.flood_fill_at(QPointF(60, 45))
    item = stroke_items(viewer)[0]
    assert item.brush().color().alpha() > 0


def test_it_emits_an_undo_command(viewer):
    commands = []
    viewer.command_emitted.connect(commands.append)
    viewer.drawing_manager.flood_fill_at(QPointF(60, 45))
    assert len(commands) == 1
    from app.ui.commands.brush import BrushStrokeCommand

    assert isinstance(commands[0], BrushStrokeCommand)


def test_it_respects_the_strokes_layer_being_hidden(viewer):
    viewer.set_layer_visibility('strokes', False)
    viewer.drawing_manager.flood_fill_at(QPointF(60, 45))
    assert not stroke_items(viewer)[0].isVisible()


def test_clicking_the_outline_selects_the_outline_instead(viewer):
    viewer.drawing_manager.flood_fill_at(QPointF(22, 45))
    path = stroke_items(viewer)[0].path()
    assert path.contains(QPointF(22, 45))
    assert not path.contains(QPointF(60, 45))


def test_lettering_inside_the_bubble_is_covered_rather_than_left_as_a_hole(qapp):
    """Clicking a bubble must mask the text in it, not mask around it.

    The selection itself excludes the lettering — it is a different colour — so
    the region comes back with holes punched through it. flood_select closes
    them. Relying on the path's fill rule instead does not work: find_contours
    winds an enclosed gap opposite to its outer contour, so WindingFill leaves
    it empty, and the mask would clean everything except the words.
    """
    from app.ui.canvas.image_viewer import ImageViewer

    view = ImageViewer(None)
    page = np.full((100, 160, 3), 128, dtype=np.uint8)
    page[10:90, 10:150] = 255    # bubble interior
    page[40:60, 30:130] = 30     # lettering inside it
    view.display_image_array(page)

    view.drawing_manager.flood_fill_at(QPointF(20, 20))
    mask = view.get_mask_for_inpainting()
    assert mask[20, 20] > 0, "the interior is not masked"
    assert mask[50, 80] > 0, "the lettering was left as a hole in the mask"


def test_a_click_off_the_image_adds_nothing(viewer):
    assert viewer.drawing_manager.flood_fill_at(QPointF(-50, -50)) is None
    assert stroke_items(viewer) == []


def test_the_wand_contributes_to_the_inpainting_mask(viewer):
    """The point of the tool: what it selects is what gets cleaned."""
    viewer.drawing_manager.flood_fill_at(QPointF(60, 45))
    mask = viewer.get_mask_for_inpainting()
    assert mask is not None
    assert mask[45, 60] > 0, "the selected region is not in the mask"
    assert mask[5, 5] == 0, "the mask covers untouched page"


def test_selecting_the_tool_sets_a_crosshair(viewer):
    from PySide6.QtCore import Qt

    viewer.set_tool('wand')
    assert viewer.current_tool == 'wand'
    assert viewer.cursor().shape() == Qt.CursorShape.CrossCursor


def test_non_contiguous_reaches_a_separate_region(qapp):
    from app.ui.canvas.image_viewer import ImageViewer

    view = ImageViewer(None)
    page = np.full((100, 200, 3), 128, dtype=np.uint8)
    page[10:40, 10:60] = 255
    page[60:90, 120:180] = 255
    view.display_image_array(page)

    view.drawing_manager.flood_fill_at(QPointF(30, 25), contiguous=False)
    path = stroke_items(view)[0].path()
    assert path.contains(QPointF(30, 25))
    assert path.contains(QPointF(150, 75)), "should have taken the far region too"
