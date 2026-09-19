"""Stable object identity (Slice 0).

Every editable logical object carries an ``object_id`` that survives its whole
lifecycle — create, edit/move, undo/redo, save/load, and webtoon page clipping —
independent of geometry, text or list index. These tests pin that invariant for
each object type and, especially, prove that splitting an object across a page
boundary does not turn one logical object into several unrelated identities.

Patches are deliberately absent: an inpaint patch's persisted content ``hash``
already is its collision-safe identity (see PatchCommandBase), so it is not given
a second one. tests/test_psd_export.py and the inpaint commands already exercise
that hash.
"""

from collections import defaultdict

import numpy as np
import pytest
from PySide6 import QtWidgets
from PySide6.QtCore import QRectF, QPointF
from PySide6.QtGui import QColor, QPainterPath, QPen

from modules.utils.common_utils import new_object_id
from modules.utils.textblock import TextBlock
from app.ui.canvas.text.text_item_properties import TextItemProperties


# --- identity basics, no Qt scene needed -------------------------------------

def test_new_object_id_is_unique_and_hex():
    ids = {new_object_id() for _ in range(2000)}
    assert len(ids) == 2000
    assert all(len(i) == 32 and int(i, 16) >= 0 for i in ids)


def test_textblock_is_born_with_a_distinct_id():
    a, b = TextBlock(), TextBlock()
    assert a.object_id and b.object_id
    # Two independently-created blocks are two logical objects.
    assert a.object_id != b.object_id


def test_textblock_deep_copy_keeps_identity():
    a = TextBlock(text="hi")
    # A deep copy is the same logical block (history snapshot, page move).
    assert a.deep_copy().object_id == a.object_id


def test_textblock_survives_the_project_serialiser():
    from app.projects.parsers import ProjectEncoder, ProjectDecoder

    blk = TextBlock(text="hi")
    restored = ProjectDecoder.decode_textblock(ProjectEncoder.encode_textblock(blk))
    assert restored.object_id == blk.object_id


def test_old_textblock_without_an_id_gets_one_on_load():
    # A block saved before identity existed: its data has no object_id. The
    # decoder builds a TextBlock (which mints one) then applies the old data,
    # and the minted id must survive because the data does not overwrite it.
    from app.projects.parsers import ProjectDecoder

    restored = ProjectDecoder.decode_textblock({"type": "textblock", "data": {"text": "old"}})
    assert restored.object_id


def test_text_props_roundtrip_preserves_id():
    props = TextItemProperties(object_id="abc123", text="x")
    assert TextItemProperties.from_dict(props.to_dict()).object_id == "abc123"


def test_text_props_from_dict_without_id_is_blank():
    # Blank on the way in; the viewer mints one when it builds the item.
    assert TextItemProperties.from_dict({"text": "x"}).object_id == ""


def test_batch_text_state_carries_identity():
    from core.text_style import build_text_item_state

    state = build_text_item_state(
        object_id="blk-1", text="hi", font_family="Arial", font_size=20,
        text_color="#000000", alignment=None, line_spacing=1.0,
        outline_color="#ffffff", outline_width=1.0, bold=False, italic=False,
        underline=False, position=(0, 0), rotation=0, scale=1.0,
        transform_origin=(0, 0), width=100, height=40, direction=None,
        vertical=False, outline=False,
    )
    assert state["object_id"] == "blk-1"
    # And it reads back through the canvas-side loader unchanged.
    assert TextItemProperties.from_dict(state).object_id == "blk-1"


# --- viewer-backed lifecycle -------------------------------------------------

@pytest.fixture
def viewer(qapp):
    from app.ui.canvas.image_viewer import ImageViewer

    view = ImageViewer(None)
    view.display_image_array(np.full((200, 300, 3), 220, dtype=np.uint8))
    yield view
    view.close()


def _fresh_viewer():
    from app.ui.canvas.image_viewer import ImageViewer

    view = ImageViewer(None)
    view.display_image_array(np.full((200, 300, 3), 220, dtype=np.uint8))
    return view


def _props(**kw):
    """Valid text-item properties (a real caller always supplies colours)."""
    base = dict(
        text="<p>hi</p>", position=(10, 10), width=100,
        text_color=QColor("#101010"), outline_color=QColor("#ffffff"),
    )
    base.update(kw)
    return TextItemProperties(**base)


def test_added_text_item_gets_an_id(viewer):
    item = viewer.add_text_item(_props())
    assert item.object_id


def test_two_independent_text_items_have_distinct_ids(viewer):
    a = viewer.add_text_item(_props(text="<p>a</p>", position=(0, 0), width=50))
    b = viewer.add_text_item(_props(text="<p>a</p>", position=(0, 0), width=50))
    assert a.object_id != b.object_id


def test_text_item_identity_survives_save_and_load(viewer):
    item = viewer.add_text_item(_props())
    oid = item.object_id
    state = viewer.save_state()
    assert state["text_items_state"][0]["object_id"] == oid

    other = _fresh_viewer()
    try:
        other.load_state(state)
        assert other.text_items[-1].object_id == oid
    finally:
        other.close()


def test_old_text_state_without_id_gets_one_and_persists(viewer):
    legacy = {"rectangles": [], "text_items_state": [
        {"text": "<p>x</p>", "position": (5, 5), "width": 80, "text_color": "#101010"}]}
    viewer.load_state(legacy)
    item = viewer.text_items[-1]
    assert item.object_id  # minted on load
    # ...and it is written on the next save, so the id is stable thereafter.
    assert viewer.save_state()["text_items_state"][0]["object_id"] == item.object_id


def test_rectangle_identity_survives_save_and_load(viewer):
    rect = viewer.add_rectangle(QRectF(0, 0, 50, 40), QPointF(10, 20))
    oid = rect.object_id
    assert oid
    state = viewer.save_state()
    assert state["rectangles"][0]["object_id"] == oid

    other = _fresh_viewer()
    try:
        other.load_state(state)
        assert other.rectangles[-1].object_id == oid
    finally:
        other.close()


def test_brush_stroke_identity_survives_save_and_load(viewer):
    from app.ui.commands.base import OBJECT_ID_KEY

    path = QPainterPath()
    path.moveTo(0, 0)
    path.lineTo(20, 20)
    pen = QPen(QColor("#80ff0000"))
    pen.setWidth(5)
    item = viewer._scene.addPath(path, pen)

    strokes = viewer.save_brush_strokes()
    oid = strokes[0]["object_id"]
    # save stamps the live item so both sides agree.
    assert item.data(OBJECT_ID_KEY) == oid

    viewer.load_brush_strokes(strokes)
    from PySide6.QtWidgets import QGraphicsPathItem
    reloaded = [i for i in viewer._scene.items()
                if isinstance(i, QGraphicsPathItem) and i is not viewer.photo]
    assert any(i.data(OBJECT_ID_KEY) == oid for i in reloaded)


# --- matchers locate by id, not geometry -------------------------------------

def test_rect_matcher_finds_by_id_after_a_move(viewer):
    from app.ui.commands.base import RectCommandBase

    rect = viewer.add_rectangle(QRectF(0, 0, 50, 40), QPointF(10, 20))
    props = RectCommandBase.save_rect_properties(rect)
    rect.setPos(QPointF(250, 150))  # geometry no longer matches the snapshot
    assert RectCommandBase.find_matching_rect(viewer._scene, props) is rect


def test_text_matcher_finds_by_id_after_a_move(viewer):
    from app.ui.commands.base import RectCommandBase

    item = viewer.add_text_item(_props())
    props = RectCommandBase.save_txt_item_properties(item)
    item.setPos(QPointF(240, 160))
    assert RectCommandBase.find_matching_txt_item(viewer._scene, props) is item


def test_brush_matcher_finds_by_id_after_a_reshape(viewer):
    from app.ui.commands.base import PathCommandBase

    path = QPainterPath()
    path.moveTo(0, 0)
    path.lineTo(20, 20)
    pen = QPen(QColor("#80ff0000"))
    pen.setWidth(5)
    item = viewer._scene.addPath(path, pen)

    props = PathCommandBase.save_path_properties(item)  # stamps + captures id
    reshaped = QPainterPath()
    reshaped.moveTo(5, 5)
    reshaped.lineTo(99, 99)
    item.setPath(reshaped)  # path no longer matches the snapshot
    assert PathCommandBase.find_matching_item(viewer._scene, props) is item


def test_text_item_id_survives_undo_redo(viewer):
    from app.ui.commands.box import AddTextItemCommand

    item = viewer.add_text_item(_props())
    oid = item.object_id
    stub_main = type("M", (), {"image_viewer": viewer})()
    cmd = AddTextItemCommand(stub_main, item)

    cmd.undo()  # removes the item
    assert all(i.object_id != oid for i in viewer.text_items)
    cmd.redo()  # ...and recreates it with the same identity
    assert viewer.text_items[-1].object_id == oid


# --- webtoon: page-boundary split must not fork identity ----------------------

class _FakeLayout:
    """Two 100px-tall pages stacked in a 300px-wide strip."""
    image_positions = [0, 100]
    image_heights = [100, 100]
    webtoon_width = 300

    def get_page_at_position(self, y):
        return 0 if y < 100 else 1

    def get_pages_for_scene_bounds(self, rect):
        return [0, 1]


class _FakeLoader:
    image_file_paths = ["p0", "p1"]
    image_data = {}
    loaded_pages = [0, 1]

    def is_page_loaded(self, i):
        return True


def test_webtoon_brush_split_keeps_one_identity(viewer):
    from app.ui.canvas.webtoons.scene_items.brush_stroke_manager import BrushStrokeManager
    from app.ui.canvas.webtoons.coordinate_converter import CoordinateConverter

    layout, loader = _FakeLayout(), _FakeLoader()
    mgr = BrushStrokeManager(viewer, layout, CoordinateConverter(layout, loader), loader)

    path = QPainterPath()  # spans page 0 (y<100) and page 1 (y>=100)
    for y in (20, 60, 130, 180):
        (path.moveTo if y == 20 else path.lineTo)(150, y)
    stroke = {"object_id": "STROKE-1", "path": path,
              "pen": "#80ff0000", "brush": "#00000000", "width": 5}

    buckets = defaultdict(lambda: {"brush_strokes": []})
    mgr._process_single_brush_stroke(stroke, buckets)

    fragments = [f for page in buckets.values() for f in page["brush_strokes"]]
    assert len(fragments) >= 2, "the stroke should have been clipped onto both pages"
    assert {f["object_id"] for f in fragments} == {"STROKE-1"}


def test_webtoon_rectangle_redistribute_keeps_one_identity(viewer):
    from app.ui.canvas.webtoons.scene_items.rectangle_manager import RectangleManager
    from app.ui.canvas.webtoons.coordinate_converter import CoordinateConverter

    layout, loader = _FakeLayout(), _FakeLoader()
    mgr = RectangleManager(viewer, layout, CoordinateConverter(layout, loader), loader)

    # A box on page 0 whose height (80) runs it off the bottom into page 1.
    rect_data = {"object_id": "RECT-1", "rect": (150, 50, 20, 80),
                 "rotation": 0, "transform_origin": (0, 0)}
    buckets = defaultdict(lambda: {"rectangles": []})
    mgr.redistribute_existing_rectangles([(rect_data, 0)], buckets)

    fragments = [f for page in buckets.values() for f in page["rectangles"]]
    assert len(fragments) >= 2, "the box should have been clipped onto both pages"
    assert {f["object_id"] for f in fragments} == {"RECT-1"}
