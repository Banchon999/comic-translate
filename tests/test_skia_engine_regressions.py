"""Regression tests for three Skia text-engine defects.

Each test guards one fix and fails if that fix is reverted:

0. The Skia renderer must set a decoration colour when underline is on —
   Skia does not derive one from `setForegroundPaint` the way it does from
   `setColor`, so a text style carrying a paint drew the glyphs and silently
   omitted the underline. Every underlined block lost its underline the moment
   the engine was switched.
1. `TextBlockItem.paint()` must not take the Skia branch while the item is
   being edited — Qt's caret and selection band are drawn by `super().paint()`,
   and the Skia branch returns before reaching it, so a block being typed into
   would show no cursor and no selection highlight.
2. `TextBlockItem.apply_shadow()` must not attach Qt's
   `QGraphicsDropShadowEffect` when Skia is painting — Skia already bakes the
   shadow into its raster, so stacking Qt's effect on top doubled it.
3. `core.skia_text.apply_base_direction()` must wrap text in the matching
   Unicode directional isolate — skia-python 144 has no direction setter, so
   without the isolate, Skia's bidi algorithm guesses the base direction from
   the first strong character and an RTL block that opens with a Latin word or
   digit lays out left-to-right, against the direction the user chose.

Conventions follow `tests/test_render_parity.py`: the session-scoped `qapp`
fixture from `tests/conftest.py`, `core.text_style.build_text_item_state(...)`
to build state dicts, and `app.ui.canvas.save_renderer.ImageSaveRenderer` to
rasterise them.
"""

import numpy as np
import pytest

from PySide6 import QtCore, QtGui, QtWidgets

from app.ui.canvas import skia_paint
from app.ui.canvas.save_renderer import ImageSaveRenderer
from app.ui.canvas.text_item import TextBlockItem
from core import skia_text, text_engine
from core.enums import LayoutDirection
from core.text_measure import TextStyle
from core.text_style import build_text_item_state

requires_skia = pytest.mark.skipif(
    not skia_text.is_available(), reason="skia-python unavailable"
)

CANVAS_W, CANVAS_H = 320, 200


@pytest.fixture(autouse=True)
def _restore_engine():
    """Never let one test's engine choice leak into the next."""
    yield
    text_engine.set_engine(text_engine.QT)


def _blank_page():
    return np.full((CANVAS_H, CANVAS_W, 3), 128, dtype=np.uint8)


def _direction_state(direction):
    return build_text_item_state(
        # Starts with a strong LTR character ("a"), so plain implicit bidi
        # could never flip the layout on its own — only an explicit isolate
        # tied to the chosen direction can.
        text="abc مرحبا",
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
        direction=direction,
        vertical=False,
        outline=True,
    )


def _render_via_export(state_dict):
    renderer = ImageSaveRenderer(_blank_page())
    renderer.add_state_to_image({"text_items_state": [state_dict]})
    return renderer.render_to_image()


def _changed_pixel_count(a, b):
    assert a.shape == b.shape, f"shape mismatch {a.shape} vs {b.shape}"
    diff = np.abs(a.astype(np.int32) - b.astype(np.int32))
    return int((diff.max(axis=2) > 0).sum())


def _shadowed_item():
    item = TextBlockItem(text="Shadow test")
    item.set_shadow(True, QtGui.QColor(0, 0, 0, 160), (4.0, 4.0), 6.0)
    return item


# ---------------------------------------------------------------------------
# 1. Editing must stay on the Qt paint path.
# ---------------------------------------------------------------------------


@requires_skia
def test_editing_mode_does_not_invoke_the_skia_painter(qapp, monkeypatch):
    """A block being typed into must show Qt's caret/selection, not a Skia raster with neither."""
    item = TextBlockItem(text="Hello")
    text_engine.set_engine(text_engine.SKIA)

    calls = []

    def fake_paint_item(painter, painted_item):
        calls.append(painted_item)
        return True

    monkeypatch.setattr(skia_paint, "paint_item", fake_paint_item)

    image = QtGui.QImage(80, 40, QtGui.QImage.Format.Format_ARGB32)
    image.fill(0)
    option = QtWidgets.QStyleOptionGraphicsItem()

    painter = QtGui.QPainter(image)
    try:
        item.editing_mode = True
        item.paint(painter, option)
    finally:
        painter.end()
    assert calls == [], "the Skia painter ran while the block was being edited"


@requires_skia
def test_non_editing_paint_still_uses_the_skia_painter(qapp, monkeypatch):
    """Guard against a fix so broad it disables Skia painting altogether once the item has ever been edited."""
    item = TextBlockItem(text="Hello")
    text_engine.set_engine(text_engine.SKIA)

    calls = []

    def fake_paint_item(painter, painted_item):
        calls.append(painted_item)
        return True

    monkeypatch.setattr(skia_paint, "paint_item", fake_paint_item)

    image = QtGui.QImage(80, 40, QtGui.QImage.Format.Format_ARGB32)
    image.fill(0)
    option = QtWidgets.QStyleOptionGraphicsItem()

    painter = QtGui.QPainter(image)
    try:
        item.editing_mode = False
        item.paint(painter, option)
    finally:
        painter.end()
    assert calls == [item], "Skia did not paint a block that was not being edited"


# ---------------------------------------------------------------------------
# 2. Drop shadow must not be doubled when Skia is painting.
# ---------------------------------------------------------------------------


def test_qt_engine_keeps_the_qt_drop_shadow_effect(qapp):
    """With the Qt engine active, a shadowed block keeps its Qt drop-shadow effect so the shadow is visible at all."""
    text_engine.set_engine(text_engine.QT)
    item = _shadowed_item()
    assert item.graphicsEffect() is not None


@requires_skia
def test_skia_engine_clears_the_qt_drop_shadow_effect(qapp):
    """With the Skia engine active, a shadowed block must not also carry Qt's drop-shadow effect, or the shadow renders twice (darker and double-offset)."""
    text_engine.set_engine(text_engine.SKIA)
    item = _shadowed_item()
    assert item.graphicsEffect() is None


@requires_skia
def test_apply_shadow_reacts_to_switching_engines(qapp):
    """Switching engines on an already-shadowed item must add or remove the Qt effect immediately, not just on freshly created items."""
    text_engine.set_engine(text_engine.QT)
    item = _shadowed_item()
    assert item.graphicsEffect() is not None

    text_engine.set_engine(text_engine.SKIA)
    item.apply_shadow()
    assert item.graphicsEffect() is None, "switching to Skia left Qt's shadow effect attached"

    text_engine.set_engine(text_engine.QT)
    item.apply_shadow()
    assert item.graphicsEffect() is not None, "switching back to Qt did not restore the shadow effect"


# ---------------------------------------------------------------------------
# 3. Skia must honour the block's text direction.
# ---------------------------------------------------------------------------


class TestApplyBaseDirection:
    """Unit tests for `core.skia_text.apply_base_direction`."""

    @requires_skia
    def test_rtl_style_wraps_in_rli_isolate(self, qapp):
        """An RTL block's text must be wrapped so Skia's bidi algorithm lays it out right-to-left regardless of its first character."""
        style = TextStyle(direction=LayoutDirection.RightToLeft)
        result = skia_text.apply_base_direction("hello", style)
        assert result == skia_text.RLI + "hello" + skia_text.PDI

    @requires_skia
    def test_ltr_style_wraps_in_lri_isolate(self, qapp):
        """An LTR block's text must be wrapped so Skia's bidi algorithm lays it out left-to-right regardless of its first character."""
        style = TextStyle(direction=LayoutDirection.LeftToRight)
        result = skia_text.apply_base_direction("hello", style)
        assert result == skia_text.LRI + "hello" + skia_text.PDI

    @requires_skia
    def test_empty_string_is_returned_unchanged(self, qapp):
        """An empty block must not be turned into a two-character isolate pair, which would corrupt emptiness checks elsewhere."""
        style = TextStyle(direction=LayoutDirection.RightToLeft)
        assert skia_text.apply_base_direction("", style) == ""


@requires_skia
def test_skia_render_changes_with_text_direction(qapp):
    """Rendering the same mixed-script block LTR vs RTL under Skia must produce visibly different output, proving the chosen direction is actually honoured rather than ignored."""
    text_engine.set_engine(text_engine.SKIA)

    ltr_render = _render_via_export(
        _direction_state(QtCore.Qt.LayoutDirection.LeftToRight)
    )
    rtl_render = _render_via_export(
        _direction_state(QtCore.Qt.LayoutDirection.RightToLeft)
    )

    changed = _changed_pixel_count(ltr_render, rtl_render)
    assert changed > 200, (
        f"only {changed} pixels differ between LTR and RTL renders of the same "
        "text — direction is being ignored"
    )


# ---------------------------------------------------------------------------
# Underline
# ---------------------------------------------------------------------------

def _underline_state(underline, **overrides):
    args = dict(
        text="Rendered", font_family="", font_size=30.0, text_color="#000000",
        alignment=QtCore.Qt.AlignmentFlag.AlignLeft, line_spacing=1.2,
        outline_color=None, outline_width=0.0, bold=False, italic=False,
        underline=underline, position=(30, 30), rotation=0.0, scale=1.0,
        transform_origin=(0, 0), width=260.0, height=60.0,
        direction=QtCore.Qt.LayoutDirection.LeftToRight, vertical=False,
        outline=False,
    )
    args.update(overrides)
    return build_text_item_state(**args)


def _ink(state_dict):
    renderer = ImageSaveRenderer(
        np.full((CANVAS_H, CANVAS_W, 3), 255, dtype=np.uint8)
    )
    renderer.add_state_to_image({'text_items_state': [state_dict]})
    image = renderer.render_to_image()
    return int((image[:, :, :3].min(axis=2) < 128).sum())


@requires_skia
def test_skia_draws_the_underline(qapp):
    """Underlined text loses its underline entirely when Skia paints it.

    Skia takes the decoration colour from setColor but not from
    setForegroundPaint, and the renderer uses a paint — so without an explicit
    setDecorationColor the underline is laid out and never drawn.
    """
    text_engine.set_engine(text_engine.SKIA)
    added = _ink(_underline_state(True)) - _ink(_underline_state(False))
    assert added > 100, (
        f"underline added only {added} px of ink under Skia — it is not drawn"
    )


def test_qt_draws_the_underline(qapp):
    """The sibling half: Qt must keep drawing it, so the test can tell the two apart."""
    text_engine.set_engine(text_engine.QT)
    added = _ink(_underline_state(True)) - _ink(_underline_state(False))
    assert added > 100


@requires_skia
def test_underline_is_drawn_in_each_pass_not_only_the_fill(qapp):
    """An outlined block must outline its underline too, not leave it bare.

    Each paint pass sets its own decoration colour; if only the fill pass did,
    an underline under outlined text would have no outline around it.
    """
    text_engine.set_engine(text_engine.SKIA)
    outlined = dict(outline=True, outline_color="#ff0000", outline_width=3.0)
    added = (
        _ink(_underline_state(True, **outlined))
        - _ink(_underline_state(False, **outlined))
    )
    assert added > 50, f"underline contributed only {added} px with an outline on"
