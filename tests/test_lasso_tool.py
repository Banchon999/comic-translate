"""Lasso and polygon selection.

One tool told apart by what the mouse does: drag a loop freehand, or click
corner to corner and close it. Straight edges are hard to draw by hand and
curves are tedious to click, and a panel needs one while a character's outline
needs the other.
"""

import numpy as np
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QGraphicsPathItem


@pytest.fixture
def viewer(qapp):
    from app.ui.canvas.image_viewer import ImageViewer

    view = ImageViewer(None)
    view.resize(400, 300)
    view.display_image_array(np.full((120, 200, 3), 200, dtype=np.uint8))
    view.set_tool('lasso')
    yield view
    view.close()


def committed(view):
    """Filled region items — the preview outline is unfilled, so it is excluded.

    Filtered on brush *style*, not on the brush colour's alpha: an item with no
    brush reports Qt's default opaque black, so testing the alpha would count
    the preview as a finished selection.
    """
    photo = getattr(view, "photo", None)
    return [
        item for item in view._scene.items()
        if isinstance(item, QGraphicsPathItem)
        and item is not photo
        and item.brush().style() != Qt.BrushStyle.NoBrush
    ]


def drag(manager, points):
    manager.lasso_press(points[0])
    for point in points[1:]:
        manager.lasso_move(point, buttons_held=True)
    manager.lasso_release(points[-1])


def click_polygon(manager, points):
    for point in points:
        manager.lasso_press(point)
        manager.lasso_release(point)


SQUARE = [QPointF(20, 20), QPointF(90, 20), QPointF(90, 90), QPointF(20, 90)]


class TestFreehand:
    def test_a_dragged_loop_commits_on_release(self, viewer):
        drag(viewer.drawing_manager, SQUARE + [QPointF(21, 21)])
        assert len(committed(viewer)) == 1

    def test_the_region_covers_what_was_enclosed(self, viewer):
        drag(viewer.drawing_manager, SQUARE + [QPointF(21, 21)])
        path = committed(viewer)[0].path()
        assert path.contains(QPointF(55, 55))
        assert not path.contains(QPointF(150, 100))

    def test_it_reaches_the_inpainting_mask(self, viewer):
        drag(viewer.drawing_manager, SQUARE + [QPointF(21, 21)])
        mask = viewer.get_mask_for_inpainting()
        assert mask[55, 55] > 0
        assert mask[110, 180] == 0

    def test_a_tiny_movement_is_a_click_not_a_drag(self, viewer):
        manager = viewer.drawing_manager
        manager.lasso_press(QPointF(20, 20))
        manager.lasso_move(QPointF(21, 21), buttons_held=True)
        manager.lasso_release(QPointF(21, 21))
        # Still collecting polygon vertices rather than having committed.
        assert committed(viewer) == []
        assert manager.lasso_points


class TestPolygon:
    def test_clicked_vertices_commit_on_close(self, viewer):
        click_polygon(viewer.drawing_manager, SQUARE)
        assert committed(viewer) == [], "should wait to be closed"
        viewer.drawing_manager.lasso_close()
        assert len(committed(viewer)) == 1

    def test_the_closed_polygon_covers_its_inside(self, viewer):
        click_polygon(viewer.drawing_manager, SQUARE)
        viewer.drawing_manager.lasso_close()
        assert committed(viewer)[0].path().contains(QPointF(55, 55))

    def test_enter_closes_it(self, viewer):
        click_polygon(viewer.drawing_manager, SQUARE)
        event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
        assert viewer.event_handler.handle_key_press(event) is True
        assert len(committed(viewer)) == 1

    def test_escape_abandons_it(self, viewer):
        click_polygon(viewer.drawing_manager, SQUARE)
        event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        assert viewer.event_handler.handle_key_press(event) is True
        assert committed(viewer) == []
        assert viewer.drawing_manager.lasso_points == []

    def test_keys_are_ignored_when_nothing_is_being_drawn(self, viewer):
        event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
        assert viewer.event_handler.handle_key_press(event) is False


class TestGuards:
    def test_two_points_are_a_line_and_commit_nothing(self, viewer):
        click_polygon(viewer.drawing_manager, SQUARE[:2])
        viewer.drawing_manager.lasso_close()
        assert committed(viewer) == []

    def test_closing_with_nothing_drawn_is_harmless(self, viewer):
        assert viewer.drawing_manager.lasso_close() is None
        assert committed(viewer) == []

    def test_switching_tool_abandons_an_unfinished_outline(self, viewer):
        click_polygon(viewer.drawing_manager, SQUARE)
        viewer.set_tool('brush')
        assert viewer.drawing_manager.lasso_points == []
        assert committed(viewer) == []

    def test_the_preview_is_removed_once_committed(self, viewer):
        click_polygon(viewer.drawing_manager, SQUARE)
        assert viewer.drawing_manager.lasso_preview is not None
        viewer.drawing_manager.lasso_close()
        assert viewer.drawing_manager.lasso_preview is None

    def test_the_preview_is_not_filled(self, viewer):
        """An outline still being drawn must not look committed."""
        click_polygon(viewer.drawing_manager, SQUARE)
        preview = viewer.drawing_manager.lasso_preview
        assert preview.brush().style() == Qt.BrushStyle.NoBrush
        assert preview.pen().style() == Qt.PenStyle.DashLine

    def test_an_unfinished_outline_does_not_reach_the_mask(self, viewer):
        """Otherwise abandoning a polygon still cleans the area it covered.

        The preview is a path item in the scene like any other, so mask
        generation has to know to skip it.
        """
        click_polygon(viewer.drawing_manager, SQUARE)
        assert viewer.drawing_manager.lasso_preview is not None
        assert viewer.get_mask_for_inpainting() is None

    def test_an_unfinished_outline_alongside_a_finished_one(self, viewer):
        click_polygon(viewer.drawing_manager, SQUARE)
        viewer.drawing_manager.lasso_close()
        # Start a second, larger one and leave it open.
        click_polygon(viewer.drawing_manager, [
            QPointF(110, 20), QPointF(180, 20), QPointF(180, 90), QPointF(110, 90),
        ])
        mask = viewer.get_mask_for_inpainting()
        assert mask[55, 55] > 0, "the committed region should be masked"
        assert mask[55, 145] == 0, "the unfinished one should not"

    def test_it_emits_one_undo_command(self, viewer):
        commands = []
        viewer.command_emitted.connect(commands.append)
        click_polygon(viewer.drawing_manager, SQUARE)
        viewer.drawing_manager.lasso_close()
        assert len(commands) == 1

    def test_it_respects_the_strokes_layer_being_hidden(self, viewer):
        viewer.set_layer_visibility('strokes', False)
        click_polygon(viewer.drawing_manager, SQUARE)
        viewer.drawing_manager.lasso_close()
        assert not committed(viewer)[0].isVisible()

    def test_double_click_closes_it(self, viewer):
        click_polygon(viewer.drawing_manager, SQUARE)
        assert viewer.event_handler.handle_mouse_double_click(None) is True
        assert len(committed(viewer)) == 1

    def test_double_click_is_left_alone_by_other_tools(self, viewer):
        viewer.set_tool('brush')
        assert viewer.event_handler.handle_mouse_double_click(None) is False
