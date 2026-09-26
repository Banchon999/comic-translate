"""Slice 4: the canvas honours layer props.

Pinned here: hide / opacity / lock / order reach the scene item; group props
combine with object props; locked items are out of reach of the click
hit-test and the eraser; object edits are undoable and land in stored state;
new items pick up an existing group state; batch processing keeps locked text;
and processing input does not change when a layer is hidden.
"""

import numpy as np
import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem

from core.layers import DocumentLayers, LayerGroup, merge_preserving_locked


def _text_props(**kw):
    from app.ui.canvas.text.text_item_properties import TextItemProperties

    base = dict(text="<p>hi</p>", position=(10, 10), width=100,
                text_color=QColor("#101010"), outline_color=QColor("#ffffff"))
    base.update(kw)
    return TextItemProperties(**base)


def _stroke(viewer, x=0):
    path = QPainterPath()
    path.moveTo(x, 0)
    path.lineTo(x + 40, 40)
    pen = QPen(QColor("#80ff0000"))
    pen.setWidth(6)
    return viewer._scene.addPath(path, pen)


# --- a bare viewer: apply_layer_state -------------------------------------------

@pytest.fixture
def viewer(qapp):
    from app.ui.canvas.image_viewer import ImageViewer

    view = ImageViewer(None)
    view.display_image_array(np.full((200, 300, 3), 220, dtype=np.uint8))
    doc = DocumentLayers()
    view.document_layers_getter = lambda: doc
    view._doc = doc
    yield view
    view.close()


def test_hidden_object_is_not_visible(viewer):
    from app.ui.canvas.scene_registry import set_item_layer

    item = viewer.add_text_item(_text_props())
    set_item_layer(item, {"visible": False})
    viewer.refresh_layers()
    assert not item.isVisible()
    set_item_layer(item, None)
    viewer.refresh_layers()
    assert item.isVisible()


def test_group_and_object_opacity_multiply(viewer):
    from app.ui.canvas.scene_registry import set_item_layer

    item = viewer.add_text_item(_text_props())
    set_item_layer(item, {"opacity": 0.5})
    viewer._doc.group(LayerGroup.TEXT).opacity = 0.5
    viewer.refresh_layers()
    assert item.opacity() == pytest.approx(0.25)


def test_hiding_a_group_hides_every_member_and_raw_hides_the_art(viewer):
    a = viewer.add_text_item(_text_props())
    b = viewer.add_text_item(_text_props(position=(50, 50)))
    viewer._doc.group(LayerGroup.TEXT).visible = False
    viewer._doc.group(LayerGroup.RAW).visible = False
    viewer.refresh_layers()
    assert not a.isVisible() and not b.isVisible()
    assert not viewer.photo.isVisible()


def test_lock_blocks_mouse_and_moving_and_unlock_restores(viewer):
    from app.ui.canvas.scene_registry import set_item_layer

    text = viewer.add_text_item(_text_props())
    rect = viewer.add_rectangle(QRectF(0, 0, 20, 20), QPointF(100, 100))
    for item in (text, rect):
        set_item_layer(item, {"locked": True})
    viewer.refresh_layers()
    for item in (text, rect):
        assert item.acceptedMouseButtons() == Qt.MouseButton.NoButton
        assert not item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable

    for item in (text, rect):
        set_item_layer(item, None)
    viewer.refresh_layers()
    for item in (text, rect):
        assert item.acceptedMouseButtons() != Qt.MouseButton.NoButton
        assert item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    assert text.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable


def test_order_stays_inside_its_group_window(viewer):
    from app.ui.canvas.scene_registry import set_item_layer
    from app.ui.canvas.layer_apply import GROUP_Z

    s1, s2 = _stroke(viewer), _stroke(viewer, 10)
    set_item_layer(s1, {"z": 2})
    set_item_layer(s2, {"z": 1})
    viewer.refresh_layers()
    assert s1.zValue() > s2.zValue()
    assert GROUP_Z[LayerGroup.STROKES] <= s2.zValue() < GROUP_Z[LayerGroup.BOXES]
    set_item_layer(s1, None)
    viewer.refresh_layers()
    assert s1.zValue() == GROUP_Z[LayerGroup.STROKES]


def test_eraser_leaves_locked_and_hidden_strokes_alone(viewer):
    from app.ui.canvas.scene_registry import set_item_layer

    locked, hidden, free = _stroke(viewer, 0), _stroke(viewer, 100), _stroke(viewer, 200)
    set_item_layer(locked, {"locked": True})
    set_item_layer(hidden, {"visible": False})
    viewer.refresh_layers()
    before = {id(s): s.path().elementCount() for s in (locked, hidden)}
    free_before = free.path()
    for x in (0, 100, 200):  # on each stroke's first point
        viewer.drawing_manager.erase_at(QPointF(x, 0))
    assert locked.path().elementCount() == before[id(locked)]
    assert hidden.path().elementCount() == before[id(hidden)]
    assert free.scene() is None or free.path() != free_before


def test_hit_test_skips_a_locked_item(viewer):
    from app.ui.canvas.scene_registry import set_item_layer

    viewer.resize(400, 300)
    viewer.resetTransform()
    rect = viewer.add_rectangle(QRectF(0, 0, 60, 60), QPointF(20, 20))
    view_pos = viewer.mapFromScene(QPointF(50, 50))
    assert viewer.event_handler._item_at(view_pos) is rect
    set_item_layer(rect, {"locked": True})
    viewer.refresh_layers()
    assert viewer.event_handler._item_at(view_pos) is not rect


def test_processing_input_ignores_hidden_patches(viewer):
    from app.ui.commands.base import PatchCommandBase
    from app.ui.canvas.scene_registry import set_item_layer

    patch = PatchCommandBase.create_patch_item(
        {"bbox": [5, 5, 10, 10], "image": np.full((10, 10, 3), 7, np.uint8),
         "hash": "h", "object_id": "P"}, viewer)
    visible = viewer.get_image_array(include_patches=True)
    set_item_layer(patch, {"visible": False})
    viewer._doc.group(LayerGroup.RAW).visible = False
    viewer.refresh_layers()
    assert np.array_equal(viewer.get_image_array(include_patches=True), visible)
    assert visible[10, 10].tolist() == [7, 7, 7]


# --- the real controller: undo, persistence, new items --------------------------

@pytest.fixture
def ct(qapp, tmp_path):
    import controller as controller_mod
    from PySide6.QtGui import QUndoStack

    win = controller_mod.ComicTranslate()
    win.image_viewer.display_image_array(np.full((200, 300, 3), 220, np.uint8))
    stack = QUndoStack(win.undo_group)
    win.undo_group.addStack(stack)
    win.undo_group.setActiveStack(stack)
    yield win
    win._skip_close_prompt = True  # edits mark it modified; don't block on the save prompt
    win.close()


def test_hide_is_undoable(ct):
    item = ct.image_viewer.add_text_item(_text_props())
    assert ct.layer_ctrl.set_object_props([item.object_id], visible=False)
    assert not item.isVisible()
    ct.undo_group.undo()
    assert item.isVisible()
    ct.undo_group.redo()
    assert not item.isVisible()
    assert ct.image_viewer.save_state()["text_items_state"][0]["layer"] == {"visible": False}


def test_no_op_change_makes_no_undo_step(ct):
    item = ct.image_viewer.add_text_item(_text_props())
    count = ct.undo_group.activeStack().count()
    assert not ct.layer_ctrl.set_object_props([item.object_id], visible=True)
    assert ct.undo_group.activeStack().count() == count


def test_patch_edit_reaches_the_patch_store(ct):
    from app.ui.commands.inpaint import PatchInsertCommand

    page = "p.png"
    ct.undo_group.activeStack().push(PatchInsertCommand(
        ct, [{"bbox": [2, 2, 6, 6], "image": np.full((6, 6, 3), 5, np.uint8)}], page))
    oid = ct.image_patches[page][0]["object_id"]
    ct.layer_ctrl.set_object_props([oid], locked=True)
    assert ct.image_patches[page][0]["layer"] == {"locked": True}
    ct.undo_group.undo()
    assert "layer" not in ct.image_patches[page][0]


def test_edit_to_an_object_not_on_screen_lands_in_stored_state(ct):
    """Undo after the page was left (or a webtoon page unloaded)."""
    from app.ui.commands.layers import SetLayerPropsCommand

    ct.image_states["other.png"] = {"viewer_state": {"text_items_state": [
        {"object_id": "OFF", "text": "<p>x</p>"}], "rectangles": []}, "brush_strokes": []}
    cmd = SetLayerPropsCommand(ct, [("OFF", None, {"visible": False})])
    cmd.redo()
    assert ct.image_states["other.png"]["viewer_state"]["text_items_state"][0]["layer"] == {"visible": False}
    cmd.undo()
    assert "layer" not in ct.image_states["other.png"]["viewer_state"]["text_items_state"][0]


def test_group_toggle_is_not_an_undo_step_but_applies(ct):
    item = ct.image_viewer.add_text_item(_text_props())
    count = ct.undo_group.activeStack().count()
    assert ct.layer_ctrl.set_group_props(LayerGroup.TEXT, visible=False)
    assert not item.isVisible()
    assert ct.undo_group.activeStack().count() == count


def test_new_item_after_a_command_takes_the_group_state(ct):
    """Anything a command adds is re-applied when the stack index moves."""
    from app.ui.commands.box import AddTextItemCommand

    ct.layer_ctrl.set_group_props(LayerGroup.TEXT, visible=False)
    item = ct.image_viewer.add_text_item(_text_props())
    ct.undo_group.activeStack().push(AddTextItemCommand(ct, item))
    assert not item.isVisible()


# --- batch keeps locked text ------------------------------------------------------

def test_merge_preserving_locked():
    existing = [
        {"object_id": "A", "text": "mine", "layer": {"locked": True}},
        {"object_id": "B", "text": "old"},
    ]
    fresh = [{"object_id": "A", "text": "rerender"}, {"object_id": "C", "text": "new"}]
    out = merge_preserving_locked(existing, fresh)
    assert [s["text"] for s in out] == ["mine", "new"]
    assert merge_preserving_locked(None, fresh) == fresh
    assert merge_preserving_locked([{"object_id": "B"}], []) == []


# --- PySide6 ownership: parentItem() on a parentless item deletes it -------------

def _stroke_and_patch(view):
    from app.ui.commands.base import PatchCommandBase
    _stroke(view, 10)
    PatchCommandBase.create_patch_item(
        {"bbox": [5, 150, 8, 8], "image": np.full((8, 8, 3), 3, np.uint8), "hash": "hp"}, view)


def test_refresh_does_not_delete_items(qapp):
    """iter_items used to call parentItem(); in PySide6 that handed each
    parentless item to its Python wrapper and it vanished when the wrapper was
    collected — a refresh emptied the page of strokes and patches."""
    import gc
    from app.ui.canvas.image_viewer import ImageViewer

    view = ImageViewer(None)
    try:
        view.display_image_array(np.full((200, 300, 3), 220, np.uint8))
        _stroke_and_patch(view)
        gc.collect()
        before = len(view._scene.items())
        view.refresh_layers()
        gc.collect()
        assert len(view._scene.items()) == before
    finally:
        view.close()


def test_webtoon_height_correction_keeps_strokes_and_patches(qapp):
    """Pre-existing on main: LazyImageLoader._adjust_scene_items_for_layout_change
    called parentItem() on every scene item, so once a page loaded taller or
    shorter than estimated, the strokes (and unreferenced patches) below it were
    deleted as soon as Python collected their wrappers."""
    import gc
    from types import SimpleNamespace
    from app.ui.canvas.image_viewer import ImageViewer
    from app.ui.canvas.webtoons.image_loader import LazyImageLoader

    view = ImageViewer(None)
    try:
        view.display_image_array(np.full((200, 300, 3), 220, np.uint8))
        _stroke_and_patch(view)
        gc.collect()
        before = len(view._scene.items())

        loader = LazyImageLoader.__new__(LazyImageLoader)
        loader._scene = view._scene
        loader.image_items = {}
        loader.placeholder_items = {}
        loader.layout_manager = SimpleNamespace(image_positions=[0.0, 5.0])
        loader._adjust_scene_items_for_layout_change(0, 40)
        gc.collect()
        assert len(view._scene.items()) == before
    finally:
        view.close()
