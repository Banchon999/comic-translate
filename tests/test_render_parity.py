"""Preview and export must draw the same page.

Two separately-maintained code paths build a `TextBlockItem` from the same
saved state: `ImageViewer.add_text_item`, which is what the editing canvas
shows, and `ImageSaveRenderer.add_state_to_image`, which is what every export
format is rasterised from. Nothing keeps them in step, and a property applied
in one and forgotten in the other is invisible until someone compares a
screenshot to a delivered file.

These tests render the same state through both and compare pixels. PNG, WebP
and the PSD merged-image section all come from `render_to_image`, so parity
there is parity for every raster export; the PSD *text layers* are a separate
concern covered by `test_psd_export.py`.
"""

import numpy as np
import pytest

from PySide6 import QtCore, QtGui, QtWidgets

from app.ui.canvas.save_renderer import ImageSaveRenderer
from app.ui.canvas.text.text_item_properties import TextItemProperties
from app.ui.canvas.text_item import TextBlockItem
from core.text_style import build_text_item_state

CANVAS_W, CANVAS_H = 320, 200


def _blank_page():
    # Mid grey, so both dark text and a white outline are visible against it.
    return np.full((CANVAS_H, CANVAS_W, 3), 128, dtype=np.uint8)


def _state(**overrides):
    args = dict(
        text="Sample text\nsecond line",
        font_family="",
        font_size=18.0,
        text_color="#101014",
        alignment=QtCore.Qt.AlignmentFlag.AlignHCenter,
        line_spacing=1.3,
        outline_color="#ffffff",
        outline_width=2.0,
        bold=False,
        italic=False,
        underline=False,
        position=(24, 30),
        rotation=0.0,
        scale=1.0,
        transform_origin=(0, 0),
        width=220.0,
        height=70.0,
        direction=QtCore.Qt.LayoutDirection.LeftToRight,
        vertical=False,
        outline=True,
    )
    args.update(overrides)
    return build_text_item_state(**args)


def _render_via_export(state_dict):
    renderer = ImageSaveRenderer(_blank_page())
    renderer.add_state_to_image({'text_items_state': [state_dict]})
    return renderer.render_to_image()


def _render_via_viewer_construction(state_dict):
    """Render an item built the way the editing canvas builds it.

    The canvas normally lives inside an ImageViewer with a full scene; what
    matters for parity is how the *item* is constructed from the state, so the
    item is built through the same code and then drawn on an identical
    surface.
    """
    from app.ui.canvas.image_viewer import ImageViewer

    viewer = ImageViewer(None)
    props = TextItemProperties.from_dict(state_dict)
    item = viewer.add_text_item(props)

    renderer = ImageSaveRenderer(_blank_page())
    # add_text_item parents the item into the viewer's own scene; move it.
    if item.scene() is not None:
        item.scene().removeItem(item)
    renderer.scene.addItem(item)
    return renderer.render_to_image()


def _difference(a, b):
    assert a.shape == b.shape, f"shape mismatch {a.shape} vs {b.shape}"
    return np.abs(a.astype(np.int32) - b.astype(np.int32))


CASES = {
    "plain": {},
    "bold_italic": {"bold": True, "italic": True},
    "no_outline": {"outline": False, "outline_color": None},
    "thick_outline": {"outline_width": 5.0},
    "rotated": {"rotation": 12.0, "transform_origin": (110, 35)},
    "scaled": {"scale": 1.4},
    "right_aligned": {"alignment": QtCore.Qt.AlignmentFlag.AlignRight},
    "wide_spacing": {"line_spacing": 2.0},
    "thai": {"text": "สวัสดีครับ\nยินดีที่ได้รู้จัก"},
    "japanese": {"text": "ありがとう\nございます"},
    "arabic": {
        "text": "مرحبا بك",
        "direction": QtCore.Qt.LayoutDirection.RightToLeft,
    },
    "vertical_cjk": {"text": "ありがとう", "vertical": True},
    "single_char": {"text": "A"},
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_preview_and_export_render_identically(qapp, name):
    state = _state(**CASES[name])

    export = _render_via_export(state)
    preview = _render_via_viewer_construction(state)

    diff = _difference(preview, export)
    changed = int((diff.max(axis=2) > 0).sum())
    assert changed == 0, (
        f"{name}: {changed} pixels differ between the canvas preview and the "
        f"export (max channel delta {int(diff.max())})"
    )


def test_the_harness_can_actually_see_a_difference(qapp):
    """Guard against a parity test that passes because it compares nothing."""
    export = _render_via_export(_state())
    altered = _render_via_export(_state(text="Different words entirely"))
    assert int((_difference(export, altered).max(axis=2) > 0).sum()) > 0


def test_export_actually_drew_the_text(qapp):
    """A blank canvas would satisfy parity trivially. It must not be blank."""
    blank = ImageSaveRenderer(_blank_page()).render_to_image()
    drawn = _render_via_export(_state())
    assert int((_difference(blank, drawn).max(axis=2) > 0).sum()) > 100
