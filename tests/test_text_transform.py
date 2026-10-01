"""Four-corner perspective on text: the quad maths, the item, dragging, undo.

A text item's perspective is four corners given as fractions of its box
(TL, TR, BR, BL), so resizing the box keeps the distortion's shape. It is the
item's own QTransform; rotation and scale stay item properties on top.
"""

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor

from modules.rendering.text_effects import (
    IDENTITY_QUAD,
    normalise_quad,
    quad_corner_from_point,
    quad_points,
)

SKEWED = ((0.1, 0.0), (1.0, 0.15), (0.95, 1.0), (0.0, 0.9))  # not a parallelogram


class TestMaths:
    def test_identity_and_junk_normalise_to_none(self):
        assert normalise_quad(None) is None
        assert normalise_quad(IDENTITY_QUAD) is None
        assert normalise_quad([[0, 0], [1, 0], [1, 1]]) is None
        assert normalise_quad([["a", 0], [1, 0], [1, 1], [0, 1]]) is None
        assert normalise_quad([[0, 0], [1, 0], [float("nan"), 1], [0, 1]]) is None

    def test_a_real_quad_is_kept_as_floats(self):
        assert normalise_quad([[0.1, 0], [1, 0.15], [0.95, 1], [0, 0.9]]) == SKEWED

    def test_points_scale_with_the_box(self):
        assert quad_points(10, 20, 100, 50, SKEWED) == [(20.0, 20.0), (110.0, 27.5), (105.0, 70.0), (10.0, 65.0)]
        assert quad_points(0, 0, 10, 10, None) == [(0, 0), (10, 0), (10, 10), (0, 10)]

    def test_convexity(self):
        from modules.rendering.text_effects import quad_is_convex

        assert quad_is_convex(SKEWED) and quad_is_convex(None)
        assert not quad_is_convex(((1, 1), (0, 0), (1, 0), (0, 1)))
        assert not quad_is_convex(((0, 0), (1, 0), (0.2, 0.2), (0, 1)))  # folded in

    def test_a_point_back_to_a_fraction(self):
        assert quad_corner_from_point(10, 20, 100, 50, 60, 45) == (0.5, 0.5)


@pytest.fixture
def item(qapp):
    from app.ui.canvas.text_item import TextBlockItem

    block = TextBlockItem(text="PERSPECTIVE", font_size=24, render_color=QColor(0, 0, 0))
    block.set_font("DejaVu Sans", 24)
    block.setTextWidth(240)
    yield block
    TextBlockItem.perspective_editing = False


def corners_on_screen(block):
    rect = block.text_rect()
    return [block.transform().map(p) for p in (rect.topLeft(), rect.topRight(),
                                               rect.bottomRight(), rect.bottomLeft())]


def expected(block, quad):
    rect = block.text_rect()
    return [QPointF(x, y) for x, y in quad_points(rect.x(), rect.y(), rect.width(), rect.height(), quad)]


def close(a, b, tol=0.01):
    return all(abs(p.x() - q.x()) < tol and abs(p.y() - q.y()) < tol for p, q in zip(a, b))


class TestItem:
    def test_the_box_corners_land_on_the_quad(self, item):
        item.set_quad(SKEWED)
        assert not item.transform().isAffine()
        assert close(corners_on_screen(item), expected(item, SKEWED))

    def test_clearing_it_restores_the_identity(self, item):
        item.set_quad(SKEWED)
        item.set_quad(None)
        assert item.transform().isIdentity() and item.quad is None

    def test_a_new_width_keeps_the_shape(self, item):
        item.set_quad(SKEWED)
        item.setTextWidth(400)
        assert close(corners_on_screen(item), expected(item, SKEWED))

    def test_a_crossing_quad_keeps_the_last_good_transform(self, item):
        item.set_quad(SKEWED)
        good = item.transform()
        item.set_quad(((1, 1), (0, 0), (1, 0), (0, 1)))  # self-crossing
        assert item.transform() == good and item.quad == SKEWED

    def test_the_box_itself_does_not_move(self, item):
        box = item.text_rect()
        item.set_quad(SKEWED)
        assert item.text_rect() == box


class TestDragging:
    @pytest.fixture
    def scene_item(self, qapp, item):
        from app.ui.canvas.image_viewer import ImageViewer

        viewer = ImageViewer(None)
        viewer._scene.addItem(item)
        viewer._scene.setSceneRect(0, 0, 2000, 2000)
        item.setPos(50, 40)
        yield viewer, item

    def drag(self, item, handle, start, end):
        item.resize_handle = handle
        item.init_resize(start)
        item.resize_item(end)
        item.resizing = False

    def test_a_corner_follows_the_cursor(self, scene_item):
        from app.ui.canvas.text_item import TextBlockItem

        _viewer, item = scene_item
        TextBlockItem.perspective_editing = True
        rect = item.text_rect()
        start = item.mapToScene(rect.topRight())
        self.drag(item, "top_right", start, start + QPointF(20, 15))
        on_screen = item.mapToScene(rect.topRight())
        assert abs(on_screen.x() - (start.x() + 20)) < 0.5
        assert abs(on_screen.y() - (start.y() + 15)) < 0.5
        # The opposite corner has not moved.
        assert item.quad[3] == (0.0, 1.0)

    def test_an_edge_skews_both_its_corners(self, scene_item):
        from app.ui.canvas.text_item import TextBlockItem

        _viewer, item = scene_item
        TextBlockItem.perspective_editing = True
        rect = item.text_rect()
        start = item.mapToScene(rect.center().x(), rect.top())
        self.drag(item, "top", start, start + QPointF(30, 0))
        du = 30 / rect.width()
        assert item.quad[0] == pytest.approx((du, 0.0))
        assert item.quad[1] == pytest.approx((1.0 + du, 0.0))
        assert item.quad[2] == (1.0, 1.0)

    def test_without_perspective_mode_a_corner_resizes(self, scene_item):
        _viewer, item = scene_item
        width = item.text_rect().width()
        start = item.mapToScene(item.text_rect().topRight())
        self.drag(item, "right", start, start + QPointF(40, 0))
        assert item.quad is None
        assert item.text_rect().width() > width

    def test_a_drag_is_one_undo_step(self, qapp):
        import controller as controller_mod
        from app.ui.canvas.text_item import TextBlockItem

        win = controller_mod.ComicTranslate()
        try:
            from PySide6.QtGui import QUndoStack

            stack = QUndoStack(win.undo_group)
            win.undo_group.setActiveStack(stack)
            block = TextBlockItem(text="TILT", font_size=30, render_color=QColor(0, 0, 0))
            win.image_viewer._scene.addItem(block)
            block.setPos(100, 100)
            block.setSelected(True)
            block.selected = True
            TextBlockItem.perspective_editing = True
            handler = win.image_viewer.event_handler
            corner = block.mapToScene(block.text_rect().topLeft())
            block.resize_handle = "top_left"
            block.init_resize(corner)
            from app.ui.commands.textformat import TextFormatCommand
            handler._perspective_command = TextFormatCommand(win.image_viewer, block)
            block.resize_item(corner + QPointF(12, 8))
            handler._release_handle_item_interaction()
            assert block.quad is not None
            stack.undo()
            assert block.quad is None and block.transform().isIdentity()
            stack.redo()
            assert block.quad is not None
        finally:
            TextBlockItem.perspective_editing = False
            win._skip_close_prompt = True
            win.close()


class TestSaveLoad:
    def test_round_trip(self, item):
        from app.ui.canvas.save_renderer import build_text_item
        from app.ui.canvas.text.text_item_properties import TextItemProperties

        item.set_quad(SKEWED)
        state = TextItemProperties.from_text_item(item).to_dict()
        assert state["quad"] == [list(corner) for corner in SKEWED]
        rebuilt = build_text_item(state)
        assert rebuilt.quad == SKEWED
        assert close(corners_on_screen(rebuilt), expected(rebuilt, SKEWED))

    def test_flat_text_saves_as_before(self, item):
        from app.ui.canvas.text.text_item_properties import TextItemProperties

        assert "quad" not in TextItemProperties.from_text_item(item).to_dict()

    def test_the_export_raster_follows_the_perspective(self, item):
        """The PSD's cached type raster is rendered through the item's transform."""
        from app.ui.canvas.save_renderer import render_text_item_rgba
        from app.ui.canvas.text.text_item_properties import TextItemProperties

        item.setPos(40, 40)
        flat = render_text_item_rgba(TextItemProperties.from_text_item(item).to_dict(), 600, 400)
        item.set_quad(((0.0, 0.0), (1.0, 0.0), (1.4, 1.6), (-0.4, 1.6)))
        tall = render_text_item_rgba(TextItemProperties.from_text_item(item).to_dict(), 600, 400)
        assert tall[2].shape[0] > flat[2].shape[0] * 1.3
        assert tall[2].shape[1] > flat[2].shape[1]


def test_the_toolbar_toggles_perspective_editing(qapp):
    import controller as controller_mod
    from app.ui.canvas.text_item import TextBlockItem

    win = controller_mod.ComicTranslate()
    try:
        from PySide6.QtGui import QUndoStack

        win.undo_group.setActiveStack(QUndoStack(win.undo_group))
        win.perspective_button.setChecked(True)
        assert TextBlockItem.perspective_editing is True
        block = TextBlockItem(text="X", font_size=20, render_color=QColor(0, 0, 0))
        win.image_viewer._scene.addItem(block)
        block.set_quad(SKEWED)
        win.curr_tblock_item = block
        win.reset_transform_button.click()
        assert block.quad is None
        win.undo_group.activeStack().undo()
        assert block.quad == SKEWED
        win.perspective_button.setChecked(False)
        assert TextBlockItem.perspective_editing is False
    finally:
        TextBlockItem.perspective_editing = False
        win._skip_close_prompt = True
        win.close()
