"""Slice 6: exports match what the canvas shows.

Flattened outputs (Save, CBZ/PDF pages, the PSD's own flattened preview) go
through ImageSaveRenderer: a hidden object, or one in a hidden group, is not
drawn, and opacity applies. The PSD keeps everything, writing a hidden object
as a *hidden layer* so it can be shown again in Photoshop/Photopea, with its
opacity, its name and its order.

The PSD is read back with psd-tools, not PhotoshopAPI (which wrote it): a
writer agreeing with itself proves nothing (see CLAUDE.md, Verification).
"""

import numpy as np
import pytest

from core.layers import DocumentLayers, LayerGroup

W, H = 160, 120
ART = 200


def _art():
    return np.full((H, W, 3), ART, dtype=np.uint8)


def _patch(x=10, y=10, value=30, layer=None):
    p = {"bbox": (x, y, 40, 30), "image": np.full((30, 40, 3), value, np.uint8),
         "hash": f"h{x}{y}", "object_id": f"P{x}{y}"}
    if layer:
        p["layer"] = layer
    return p


def _text_state(layer=None, text="HELLO"):
    s = {
        "object_id": "T1", "text": f"<p>{text}</p>", "font_family": "Arial",
        "font_size": 28.0, "text_color": "#000000", "position": (60.0, 70.0),
        "width": 90.0, "height": 40.0, "alignment": 1, "line_spacing": 1.0,
        "outline_color": "#FFFFFF", "outline_width": 0.0, "bold": True,
        "italic": False, "underline": False, "rotation": 0.0, "scale": 1.0,
        "transform_origin": (0.0, 0.0), "selection_outlines": [],
    }
    if layer:
        s["layer"] = layer
    return s


def _render(patches=(), text=(), doc=None):
    from app.ui.canvas.save_renderer import ImageSaveRenderer

    r = ImageSaveRenderer(_art(), doc)
    r.apply_patches(list(patches))
    r.add_state_to_image({"text_items_state": list(text)})
    return r.render_to_image()


def _dark_pixels(img, box):
    x1, y1, x2, y2 = box
    return int((img[y1:y2, x1:x2] < 100).any(axis=2).sum())


TEXT_BOX = (60, 70, 150, 110)


# --- flattened renders ------------------------------------------------------------

def test_visible_patch_and_text_are_drawn(qapp):
    img = _render([_patch()], [_text_state()])
    assert img[20, 20].tolist() == [30, 30, 30]
    assert _dark_pixels(img, TEXT_BOX) > 20


def test_hidden_patch_is_not_drawn(qapp):
    img = _render([_patch(layer={"visible": False})])
    assert img[20, 20].tolist() == [ART] * 3


def test_hidden_text_is_not_drawn(qapp):
    img = _render(text=[_text_state(layer={"visible": False})])
    assert _dark_pixels(img, TEXT_BOX) == 0


def test_hidden_group_hides_its_members(qapp):
    doc = DocumentLayers()
    doc.group(LayerGroup.TEXT).visible = False
    doc.group(LayerGroup.PATCHES).visible = False
    img = _render([_patch()], [_text_state()], doc)
    assert img[20, 20].tolist() == [ART] * 3
    assert _dark_pixels(img, TEXT_BOX) == 0


def test_patch_opacity_blends(qapp):
    img = _render([_patch(value=0, layer={"opacity": 0.5})])
    assert abs(int(img[20, 20, 0]) - ART // 2) <= 3


def test_hidden_raw_image_leaves_paper_and_keeps_patches(qapp):
    """Patches are Qt children of the artwork; hiding the artwork must not
    take them with it, and the flattened page is white, not black."""
    doc = DocumentLayers()
    doc.group(LayerGroup.RAW).visible = False
    img = _render([_patch()], doc=doc)
    assert img[20, 20].tolist() == [30, 30, 30]
    assert img[100, 150].tolist() == [255, 255, 255]


def test_group_props_accepted_as_a_dict(qapp):
    img = _render([_patch()], doc={"patches": {"visible": False}})
    assert img[20, 20].tolist() == [ART] * 3


# --- the PSD --------------------------------------------------------------------------

psd_tools = pytest.importorskip("psd_tools", reason="psd-tools is a dev dependency")


def _export(sandbox_dir, page):
    from app.controllers import psd_exporter

    if not psd_exporter.psd_support_available():
        pytest.skip("PhotoshopAPI has no wheel for this platform")
    path = psd_exporter.export_psd_pages(str(sandbox_dir), [page], "layers")
    return psd_tools.PSDImage.open(path)


def _page(patches, text, groups=None):
    from app.controllers import psd_exporter

    return psd_exporter.PsdPageData(
        file_path="001.png", rgb_image=_art(),
        viewer_state={"text_items_state": text}, patches=patches,
        document_layers=groups,
    )


def _group(psd, name):
    return next(layer for layer in psd if layer.name == name)


def test_psd_writes_hidden_objects_as_hidden_layers(sandbox_dir, qapp):
    psd = _export(sandbox_dir, _page(
        [_patch(10, 10, layer={"visible": False, "name": "Cleaned SFX"}),
         _patch(80, 60, layer={"opacity": 0.5})],
        [_text_state(layer={"visible": False, "name": "Title"})],
    ))
    patches = list(_group(psd, "Inpaint Patches"))
    by_name = {layer.name: layer for layer in patches}
    assert "Cleaned SFX" in by_name
    assert by_name["Cleaned SFX"].visible is False
    other = next(layer for layer in patches if layer.name != "Cleaned SFX")
    assert other.visible is True
    assert abs(other.opacity - 128) <= 1  # psd-tools reports 0..255

    (text_layer,) = list(_group(psd, "Editable Text"))
    assert text_layer.name == "Title"
    assert text_layer.visible is False


def test_psd_writes_group_props_on_the_groups(sandbox_dir, qapp):
    psd = _export(sandbox_dir, _page(
        [_patch()], [_text_state()],
        groups={"text": {"visible": False}, "patches": {"opacity": 0.25}, "raw": {"visible": False}},
    ))
    assert _group(psd, "Editable Text").visible is False
    assert abs(_group(psd, "Inpaint Patches").opacity - 64) <= 1
    assert _group(psd, "Raw Image").visible is False


def test_psd_orders_objects_by_their_layer_order(sandbox_dir, qapp):
    psd = _export(sandbox_dir, _page(
        [_patch(10, 10, layer={"z": 1.0, "name": "low"}),
         _patch(80, 60, layer={"z": 5.0, "name": "high"})],
        [],
    ))
    names = [layer.name for layer in _group(psd, "Inpaint Patches")]
    # psd-tools iterates a group bottom to top; "high" must be above "low".
    assert names.index("high") > names.index("low")


def test_untouched_page_exports_as_before(sandbox_dir, qapp):
    """No layer props anywhere: every layer visible at full opacity, in the
    saved order — what every export looked like before layers existed."""
    psd = _export(sandbox_dir, _page([_patch(10, 10), _patch(80, 60)], [_text_state()]))
    for layer in psd.descendants():
        assert layer.visible is True
        assert layer.opacity == 255
