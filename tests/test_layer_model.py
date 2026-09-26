"""Slice 3: the layer model, and layer props riding along with every object.

Nothing here is visible yet (Slice 4 applies the props to the canvas). What is
pinned: the model's defaults and tolerance, that one classifier decides every
item's group, that an object's layer props survive every path its identity
already survives (save/load, undo snapshots, webtoon split), that group props
survive a v2 project, and that an untouched object serialises exactly as it
did before layers existed.
"""

import subprocess
import sys
from collections import defaultdict

import numpy as np
import pytest
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QPainterPath, QPen

from core.layers import (
    DocumentLayers,
    GroupProps,
    LayerGroup,
    LayerProps,
    PAINT_ORDER,
    layer_dict,
)

HIDDEN = {"visible": False, "name": "SFX", "z": 2.0}


# --- the model ---------------------------------------------------------------

def test_default_props_serialise_to_nothing():
    assert LayerProps().to_dict() is None
    assert LayerProps().is_default()
    assert GroupProps().to_dict() is None
    assert DocumentLayers().to_dict() == {}


def test_props_roundtrip_keeps_only_what_differs():
    p = LayerProps(visible=False, locked=True, opacity=0.5, name="SFX", z=3.0)
    d = p.to_dict()
    assert d == {"visible": False, "locked": True, "opacity": 0.5, "name": "SFX", "z": 3.0}
    assert LayerProps.from_dict(d) == p
    assert LayerProps(opacity=0.25).to_dict() == {"opacity": 0.25}


@pytest.mark.parametrize("bad", [None, "x", 3, {"opacity": "nope"}, {"opacity": float("nan")}])
def test_from_dict_is_tolerant(bad):
    assert LayerProps.from_dict(bad).opacity == 1.0


def test_opacity_is_clamped():
    assert LayerProps.from_dict({"opacity": 7}).opacity == 1.0
    assert LayerProps.from_dict({"opacity": -1}).opacity == 0.0


def test_layer_dict_normalises():
    assert layer_dict(None) is None
    assert layer_dict({"visible": True, "junk": 1}) is None
    assert layer_dict(LayerProps(locked=True)) == {"locked": True}


def test_effective_combines_group_and_object():
    doc = DocumentLayers()
    doc.group(LayerGroup.TEXT).opacity = 0.5
    doc.group(LayerGroup.TEXT).locked = True
    eff = doc.effective(LayerGroup.TEXT, {"opacity": 0.5})
    assert (eff.visible, eff.locked, eff.opacity) == (True, True, 0.25)

    doc.group(LayerGroup.PATCHES).visible = False
    assert doc.effective(LayerGroup.PATCHES).visible is False
    assert doc.effective(LayerGroup.TEXT, {"visible": False}).visible is False


def test_document_layers_roundtrip_and_unknown_groups():
    doc = DocumentLayers()
    doc.group(LayerGroup.STROKES).visible = False
    data = doc.to_dict()
    assert data == {"strokes": {"visible": False}}
    data["some_future_group"] = {"visible": False}
    back = DocumentLayers.from_dict(data)
    assert back.group(LayerGroup.STROKES).visible is False
    assert back.group(LayerGroup.TEXT).visible is True


def test_paint_order_covers_every_group_once():
    assert sorted(PAINT_ORDER, key=lambda g: g.value) == sorted(LayerGroup, key=lambda g: g.value)
    assert PAINT_ORDER[0] is LayerGroup.RAW and PAINT_ORDER[-1] is LayerGroup.TEXT


def test_core_layers_imports_without_qt():
    code = "import sys, core.layers; assert not any(m.startswith('PySide6') for m in sys.modules)"
    subprocess.run([sys.executable, "-c", code], check=True, cwd=str(__import__("pathlib").Path(__file__).parents[1]))


# --- one classifier, and the item-side store -----------------------------------

@pytest.fixture
def viewer(qapp):
    from app.ui.canvas.image_viewer import ImageViewer

    view = ImageViewer(None)
    view.display_image_array(np.full((200, 300, 3), 220, dtype=np.uint8))
    yield view
    view.close()


def _text_props(**kw):
    from app.ui.canvas.text.text_item_properties import TextItemProperties

    base = dict(text="<p>hi</p>", position=(10, 10), width=100,
                text_color=QColor("#101010"), outline_color=QColor("#ffffff"))
    base.update(kw)
    return TextItemProperties(**base)


def _add_stroke(viewer):
    path = QPainterPath()
    path.moveTo(0, 0)
    path.lineTo(20, 20)
    pen = QPen(QColor("#80ff0000"))
    pen.setWidth(5)
    return viewer._scene.addPath(path, pen)


def _add_patch(viewer, **extra):
    from app.ui.commands.base import PatchCommandBase

    prop = {"bbox": [5, 5, 4, 4], "image": np.full((4, 4, 3), 9, np.uint8),
            "hash": "h1", "object_id": "PATCH-1", **extra}
    return PatchCommandBase.create_patch_item(prop, viewer)


def test_kind_of_classifies_every_page_item(viewer):
    from app.ui.canvas.scene_registry import kind_of

    text = viewer.add_text_item(_text_props())
    rect = viewer.add_rectangle(QRectF(0, 0, 10, 10), QPointF(0, 0))
    stroke = _add_stroke(viewer)
    patch = _add_patch(viewer)
    assert kind_of(text) is LayerGroup.TEXT
    assert kind_of(rect) is LayerGroup.BOXES
    assert kind_of(stroke) is LayerGroup.STROKES
    assert kind_of(patch) is LayerGroup.PATCHES
    assert kind_of(viewer.photo) is LayerGroup.RAW


def test_find_by_id_reads_attribute_and_data_ids(viewer):
    from app.ui.canvas.scene_registry import find_by_id, iter_items

    text = viewer.add_text_item(_text_props())
    patch = _add_patch(viewer)
    assert find_by_id(viewer._scene, text.object_id) is text
    assert find_by_id(viewer._scene, "PATCH-1") is patch
    assert find_by_id(viewer._scene, "") is None
    assert list(iter_items(viewer._scene, LayerGroup.PATCHES)) == [patch]


def test_untouched_objects_serialise_without_a_layer_key(viewer):
    viewer.add_text_item(_text_props())
    viewer.add_rectangle(QRectF(0, 0, 10, 10), QPointF(0, 0))
    _add_stroke(viewer)
    state = viewer.save_state()
    assert "layer" not in state["text_items_state"][0]
    assert "layer" not in state["rectangles"][0]
    assert "layer" not in viewer.save_brush_strokes()[0]


# --- props survive every path identity survives --------------------------------

def _fresh(qapp):
    from app.ui.canvas.image_viewer import ImageViewer

    v = ImageViewer(None)
    v.display_image_array(np.full((200, 300, 3), 220, dtype=np.uint8))
    return v


def test_text_layer_survives_save_and_load(viewer, qapp):
    from app.ui.canvas.scene_registry import get_item_layer

    viewer.add_text_item(_text_props(layer=HIDDEN))
    state = viewer.save_state()
    assert state["text_items_state"][0]["layer"] == HIDDEN
    other = _fresh(qapp)
    try:
        other.load_state(state)
        assert get_item_layer(other.text_items[-1]) == HIDDEN
    finally:
        other.close()


def test_rect_layer_survives_save_and_load(viewer, qapp):
    from app.ui.canvas.scene_registry import get_item_layer, set_item_layer

    rect = viewer.add_rectangle(QRectF(0, 0, 10, 10), QPointF(3, 4))
    set_item_layer(rect, HIDDEN)
    state = viewer.save_state()
    other = _fresh(qapp)
    try:
        other.load_state(state)
        assert get_item_layer(other.rectangles[-1]) == HIDDEN
    finally:
        other.close()


def test_stroke_layer_survives_save_and_load(viewer):
    from PySide6.QtWidgets import QGraphicsPathItem
    from app.ui.canvas.scene_registry import get_item_layer, set_item_layer

    set_item_layer(_add_stroke(viewer), HIDDEN)
    strokes = viewer.save_brush_strokes()
    assert strokes[0]["layer"] == HIDDEN
    viewer.load_brush_strokes(strokes)
    reloaded = [i for i in viewer._scene.items() if type(i) is QGraphicsPathItem]
    assert [get_item_layer(i) for i in reloaded] == [HIDDEN]


def test_undo_snapshots_carry_layer(viewer):
    from app.ui.commands.base import PathCommandBase, RectCommandBase
    from app.ui.canvas.scene_registry import get_item_layer, set_item_layer

    stroke = _add_stroke(viewer)
    set_item_layer(stroke, HIDDEN)
    snap = PathCommandBase.save_path_properties(stroke)
    assert get_item_layer(PathCommandBase.create_path_item(snap)) == HIDDEN

    rect = viewer.add_rectangle(QRectF(0, 0, 10, 10), QPointF(3, 4))
    set_item_layer(rect, HIDDEN)
    snap = RectCommandBase.save_rect_properties(rect)
    viewer._scene.removeItem(rect)
    viewer.rectangles.remove(rect)
    assert get_item_layer(RectCommandBase.create_rect_item(snap, viewer)) == HIDDEN


def test_patch_item_takes_layer_from_its_properties(viewer):
    from app.ui.canvas.scene_registry import get_item_layer

    assert get_item_layer(_add_patch(viewer, layer=HIDDEN)) == HIDDEN


# --- webtoon split: every fragment keeps the props -----------------------------

class _FakeLayout:
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


def test_webtoon_stroke_split_keeps_layer(viewer):
    from app.ui.canvas.webtoons.scene_items.brush_stroke_manager import BrushStrokeManager
    from app.ui.canvas.webtoons.coordinate_converter import CoordinateConverter

    layout, loader = _FakeLayout(), _FakeLoader()
    mgr = BrushStrokeManager(viewer, layout, CoordinateConverter(layout, loader), loader)
    path = QPainterPath()
    for y in (20, 60, 130, 180):
        (path.moveTo if y == 20 else path.lineTo)(150, y)
    stroke = {"object_id": "S", "path": path, "pen": "#80ff0000",
              "brush": "#00000000", "width": 5, "layer": HIDDEN}
    buckets = defaultdict(lambda: {"brush_strokes": []})
    mgr._process_single_brush_stroke(stroke, buckets)
    frags = [f for page in buckets.values() for f in page["brush_strokes"]]
    assert len(frags) >= 2
    assert all(f["layer"] == HIDDEN for f in frags)


def test_webtoon_rect_redistribute_keeps_layer(viewer):
    from app.ui.canvas.webtoons.scene_items.rectangle_manager import RectangleManager
    from app.ui.canvas.webtoons.coordinate_converter import CoordinateConverter

    layout, loader = _FakeLayout(), _FakeLoader()
    mgr = RectangleManager(viewer, layout, CoordinateConverter(layout, loader), loader)
    rect_data = {"object_id": "R", "rect": (150, 50, 20, 80), "rotation": 0,
                 "transform_origin": (0, 0), "layer": HIDDEN}
    buckets = defaultdict(lambda: {"rectangles": []})
    mgr.redistribute_existing_rectangles([(rect_data, 0)], buckets)
    frags = [f for page in buckets.values() for f in page["rectangles"]]
    assert len(frags) >= 2
    assert all(f["layer"] == HIDDEN for f in frags)


def test_webtoon_text_split_keeps_layer(viewer):
    from app.ui.canvas.webtoons.scene_items.text_item_manager import TextItemManager
    from app.ui.canvas.webtoons.coordinate_converter import CoordinateConverter

    layout, loader = _FakeLayout(), _FakeLoader()
    mgr = TextItemManager(viewer, layout, CoordinateConverter(layout, loader), loader)
    text_item = {
        "object_id": "T", "position": (150, 60), "width": 40, "font_size": 60,
        "line_spacing": 1.2, "text": "<p>HELLO</p>", "font_family": "Arial",
        "bold": False, "italic": False, "text_color": "#101010",
        "outline_color": "#ffffff", "layer": HIDDEN,
    }
    buckets = defaultdict(lambda: {"text_items": []})
    mgr.redistribute_existing_text_items({0: [text_item]}, buckets)
    frags = [f for page in buckets.values() for f in page["text_items"]]
    assert len(frags) >= 2
    assert all(f.get("layer") == HIDDEN for f in frags)


# --- group props persist with the project ---------------------------------------

def test_v2_project_keeps_group_props(qapp, tmp_path):
    """Save, reset to defaults, load back into the same window (one window per
    test: several full ComicTranslate instances in one process make a native Qt
    font-database hang likelier)."""
    import controller as controller_mod
    from PIL import Image
    from app.projects.project_state_v2 import (
        load_state_from_proj_file_v2,
        save_state_to_proj_file_v2,
    )

    win = controller_mod.ComicTranslate()
    try:
        page = tmp_path / "001.png"
        Image.fromarray(np.full((20, 30, 3), 200, np.uint8)).save(page)
        win.image_files = [str(page)]
        win.image_data = {str(page): np.full((20, 30, 3), 200, np.uint8)}
        win.document_layers.group(LayerGroup.TEXT).visible = False
        win.document_layers.group(LayerGroup.PATCHES).opacity = 0.5

        proj = tmp_path / "p.ctpr"
        save_state_to_proj_file_v2(win, str(proj))
        win.document_layers = DocumentLayers()
        load_state_from_proj_file_v2(win, str(proj))
        assert win.document_layers.group(LayerGroup.TEXT).visible is False
        assert win.document_layers.group(LayerGroup.PATCHES).opacity == 0.5
        assert win.document_layers.group(LayerGroup.RAW).visible is True
    finally:
        win._skip_close_prompt = True
        win.close()
