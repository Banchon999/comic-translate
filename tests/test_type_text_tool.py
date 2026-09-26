"""The 'type' tool: click the page to drop your own editable text box.

Two layers are covered. The viewer/event layer only *reports* where you clicked
(`add_text_requested`) — building the box is the controller's job, because that
is where the font settings and the undo stack live. The controller layer builds
the box, makes it editable, and (the part that actually broke in development)
keeps its geometry stable so a single AddTextItemCommand can undo it even after
text is typed.

Building the whole `ComicTranslate` window in a test is unusual here, but the
feature spans the viewer, the event handler, the toolbar and the text
controller, and the geometry-stability bug only shows once they are wired
together.
"""

import numpy as np
import pytest
from PySide6 import QtWidgets, QtGui
from PySide6.QtCore import QPointF, QEvent, Qt


@pytest.fixture
def viewer(qapp):
    from app.ui.canvas.image_viewer import ImageViewer

    view = ImageViewer(None)
    view.resize(400, 300)
    view.display_image_array(np.full((200, 300, 3), 220, dtype=np.uint8))
    view.fitInView()
    yield view
    view.close()


def _press_at_scene(view, scene_pt: QPointF):
    """Synthesize a left-button press at the viewport point for a scene point."""
    vp = view.mapFromScene(scene_pt)
    event = QtGui.QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(vp),
        view.viewport().mapToGlobal(vp),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    view.event_handler.handle_mouse_press(event)


def test_the_tool_reports_a_click_on_the_page(viewer):
    """With the type tool active, a click asks the controller for a box there —
    and the viewer alone creates none (that stays the controller's job)."""
    viewer.set_tool("type")
    seen = []
    viewer.add_text_requested.connect(lambda p: seen.append(p))

    _press_at_scene(viewer, QPointF(150, 100))

    assert len(seen) == 1
    assert viewer.event_handler._is_on_image(seen[0])
    assert abs(seen[0].x() - 150) < 2 and abs(seen[0].y() - 100) < 2
    assert viewer.text_items == []  # viewer did not build anything itself


def test_no_request_without_the_tool(viewer):
    """A click with no tool selected must not drop text boxes."""
    viewer.set_tool(None)
    seen = []
    viewer.add_text_requested.connect(lambda p: seen.append(p))

    _press_at_scene(viewer, QPointF(150, 100))

    assert seen == []


# --- the full path, through the real window --------------------------------

@pytest.fixture
def window(qapp):
    import controller

    win = controller.ComicTranslate()
    win.image_viewer.display_image_array(np.full((300, 400, 3), 220, dtype=np.uint8))
    # A loaded page owns an undo stack; give the group one so commands land.
    stack = QtGui.QUndoStack()
    win.undo_group.addStack(stack)
    win.undo_group.setActiveStack(stack)
    win._test_stack = stack
    yield win
    win.close()


def test_placing_types_an_editable_box_that_survives_typing_and_undo(window):
    viewer = window.image_viewer
    window.type_text_button.setChecked(True)
    window.toggle_type_text_tool()
    assert viewer.current_tool == "type"

    viewer.add_text_requested.emit(QPointF(100, 80))
    assert len(viewer.text_items) == 1
    item = viewer.text_items[-1]
    assert item.editing_mode  # ready to type immediately
    assert (round(item.pos().x()), round(item.pos().y())) == (100, 80)

    width_empty = item.boundingRect().width()
    item.textCursor().insertText("HELLO WORLD")
    QtWidgets.QApplication.instance().processEvents()
    width_typed = item.boundingRect().width()
    # The whole reason for the fixed wrap width: geometry must not drift with
    # text, or the add command can no longer find the box to undo it.
    assert abs(width_empty - width_typed) < 1.0

    # Typing becomes its own undo step once the 400 ms text-edit debounce
    # fires. Whether it already has depends on how long processEvents() above
    # took (it also runs deferred deletions of windows earlier tests closed),
    # so commit it explicitly: the stack is then always [add, edit], and
    # undoing both must remove the box — the add command still finding it
    # after the text changed is what this test is about.
    window.text_ctrl._commit_pending_text_command()
    stack = window._test_stack
    while stack.canUndo():
        stack.undo()
    assert len(viewer.text_items) == 0
    while stack.canRedo():
        stack.redo()
    assert len(viewer.text_items) == 1


def test_the_typed_box_is_saved_with_the_page(window):
    viewer = window.image_viewer
    window.type_text_button.setChecked(True)
    window.toggle_type_text_tool()
    viewer.add_text_requested.emit(QPointF(60, 50))
    viewer.text_items[-1].textCursor().insertText("MINE")

    state = viewer.save_state()
    texts = state.get("text_items_state", [])
    assert len(texts) == 1
    # save_state serialises scene text items directly, so a typed box needs no
    # TextBlock to persist — its position rides along.
    assert tuple(round(v) for v in texts[0]["position"]) == (60, 50)
