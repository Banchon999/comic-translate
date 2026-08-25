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
from app.ui.canvas.text_item import OutlineInfo, OutlineType, TextBlockItem
from core import skia_text, text_engine
from core.enums import LayoutDirection
from core.skia_render import CharRun, OutlineLayer, TextRenderSpec
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


# ---------------------------------------------------------------------------
# Per-range character formats and per-range outlines (commit 9652d17)
# ---------------------------------------------------------------------------
#
# Both defects had one cause: the Skia renderer built a single Skia paragraph
# from `item.toPlainText()` and one `TextStyle`, so anything `update_text_format`
# had applied to a range of characters was gone the instant the engine
# switched to Skia — a bolded/recoloured word rendered uniformly, and an
# outline scoped to a selection stroked the entire block.

def _render_item(item):
    """Render one TextBlockItem onto a blank white page and return RGB pixels."""
    renderer = ImageSaveRenderer(np.full((CANVAS_H, CANVAS_W, 3), 255, dtype=np.uint8))
    renderer.scene.addItem(item)
    return renderer.render_to_image()


def _mixed_format_item():
    """"red BOLD plain" with chars 0-3 coloured red and chars 4-8 bold+blue.

    Reproduces the per-range formatting `update_text_format` applies to a
    user's selection: two `QTextCursor` ranges merged onto the same document
    that `set_plain_text` just populated uniformly.
    """
    item = TextBlockItem(text="")
    item.set_plain_text("red BOLD plain")
    item.setTextWidth(260.0)
    item.setPos(20, 20)

    cursor = QtGui.QTextCursor(item.document())
    cursor.setPosition(0)
    cursor.setPosition(3, QtGui.QTextCursor.MoveMode.KeepAnchor)
    red_format = QtGui.QTextCharFormat()
    red_format.setForeground(QtGui.QColor("#dd0000"))
    cursor.mergeCharFormat(red_format)

    cursor.setPosition(4)
    cursor.setPosition(8, QtGui.QTextCursor.MoveMode.KeepAnchor)
    blue_bold_format = QtGui.QTextCharFormat()
    blue_bold_format.setForeground(QtGui.QColor("#0044dd"))
    blue_bold_format.setFontWeight(QtGui.QFont.Weight.Bold)
    cursor.mergeCharFormat(blue_bold_format)

    return item


def _reddish_mask(rgb):
    r = rgb[..., 0].astype(np.int32)
    g = rgb[..., 1].astype(np.int32)
    b = rgb[..., 2].astype(np.int32)
    return (r > 150) & (g < 100) & (b < 100)


def _blueish_mask(rgb):
    r = rgb[..., 0].astype(np.int32)
    g = rgb[..., 1].astype(np.int32)
    b = rgb[..., 2].astype(np.int32)
    return (b > 150) & (r < 100) & (g < 100)


class TestCharRuns:
    """Unit tests for `skia_paint._char_runs`."""

    @requires_skia
    def test_char_runs_splits_at_format_boundaries(self, qapp):
        """A block with two restyled ranges must come back as four runs at the exact boundaries, or Skia paints it uniformly and the restyling is lost."""
        item = _mixed_format_item()
        text = skia_paint._plain_text(item)
        runs = skia_paint._char_runs(item, text)

        assert len(runs) > 1
        boundaries = [(run.start, run.end) for run in runs]
        assert boundaries == [(0, 3), (3, 4), (4, 8), (8, 14)]

        red_run, gap_run, bold_run, tail_run = runs
        assert red_run.color == "#ffdd0000"
        assert red_run.bold is False
        assert bold_run.color == "#ff0044dd"
        assert bold_run.bold is True
        assert gap_run.color is None
        assert tail_run.color is None

    @requires_skia
    def test_char_runs_degrades_to_empty_on_length_mismatch(self, qapp):
        """If the document walk's length disagrees with the text it is given, `_char_runs` must return no runs rather than risk painting a style onto the wrong word."""
        item = _mixed_format_item()
        text = skia_paint._plain_text(item)
        assert () == skia_paint._char_runs(item, text + "xx")


@requires_skia
def test_skia_renders_mixed_character_formats_with_distinct_colours(qapp):
    """Bolding/recolouring one word in a block must still show under Skia, not flatten to the block's single fill colour."""
    text_engine.set_engine(text_engine.SKIA)
    item = _mixed_format_item()
    image = _render_item(item)

    red = int(_reddish_mask(image).sum())
    blue = int(_blueish_mask(image).sum())
    assert red > 20, f"only {red} red pixels rendered — per-range colour lost under Skia"
    assert blue > 20, f"only {blue} blue pixels rendered — per-range colour lost under Skia"


# ---------------------------------------------------------------------------
# `TextRenderSpec.runs_for`
# ---------------------------------------------------------------------------


@requires_skia
def test_runs_for_splits_uniform_text_at_outline_boundary():
    """A uniformly-styled spec with no `char_runs` must still split at an outline's own start/end, or the outline has only one run to attach to and covers it entirely."""
    text = "x" * 14
    spec = TextRenderSpec(
        text=text,
        style=TextStyle(),
        outlines=(OutlineLayer(width=2.0, color="#ffffff", start=0, end=3),),
    )
    runs = spec.runs_for(text)

    assert len(runs) > 1
    boundaries = sorted({run.start for run in runs} | {run.end for run in runs})
    assert 3 in boundaries


# ---------------------------------------------------------------------------
# A selection-scoped outline must not stroke the whole block.
# ---------------------------------------------------------------------------

def _uniformly_styled_item(text="red BOLD plain"):
    """A block with no per-range character formats — the case that broke.

    A uniform block is exactly one character run, and any outline touching
    that one run used to cover it entirely regardless of the outline's own
    start/end.
    """
    item = TextBlockItem(text="")
    item.set_plain_text(text)
    item.setTextWidth(260.0)
    item.setPos(20, 20)
    return item


def _green_selection_outline_item():
    item = _uniformly_styled_item()
    item.selection_outlines = [
        OutlineInfo(0, 3, QtGui.QColor("#00cc00"), 4.0, OutlineType.Selection)
    ]
    return item


def _column_span(mask):
    """(min, max) column index where `mask` is set anywhere, or None."""
    columns = np.where(mask.any(axis=0))[0]
    if columns.size == 0:
        return None
    return int(columns.min()), int(columns.max())


def _greenish_mask(rgb):
    r = rgb[..., 0].astype(np.int32)
    g = rgb[..., 1].astype(np.int32)
    b = rgb[..., 2].astype(np.int32)
    return (g > 120) & (r < 120) & (b < 120)


def _ink_mask(rgb):
    """Any pixel visibly darker than the blank white page behind it."""
    r = rgb[..., 0].astype(np.int32)
    g = rgb[..., 1].astype(np.int32)
    b = rgb[..., 2].astype(np.int32)
    return (r < 230) | (g < 230) | (b < 230)


def _assert_outline_covers_only_first_word(image):
    green_span = _column_span(_greenish_mask(image))
    ink_span = _column_span(_ink_mask(image))
    assert green_span is not None, "no green outline pixels rendered at all"
    assert ink_span is not None, "no text ink rendered at all"

    ink_width = ink_span[1] - ink_span[0]
    green_right_relative = green_span[1] - ink_span[0]
    assert green_right_relative < ink_width / 2, (
        f"green outline reaches {green_right_relative}px into a {ink_width}px-wide "
        "block — it should cover only the first word, not the whole block"
    )


@requires_skia
def test_skia_scoped_outline_does_not_stroke_the_whole_block(qapp):
    """An outline applied to only "red" must not stroke the whole uniformly-styled block once Skia paints it."""
    text_engine.set_engine(text_engine.SKIA)
    item = _green_selection_outline_item()
    image = _render_item(item)
    _assert_outline_covers_only_first_word(image)


def test_qt_scoped_outline_does_not_stroke_the_whole_block(qapp):
    """Sibling of the Skia test under the Qt engine, so a Skia-only regression here is distinguishable from a shared one."""
    text_engine.set_engine(text_engine.QT)
    item = _green_selection_outline_item()
    image = _render_item(item)
    _assert_outline_covers_only_first_word(image)


# ---------------------------------------------------------------------------
# Wrapping: Skia must reach the same line breakdown Qt did.
#
# `TextRenderSpec.box` is measured by Qt — it is the item's own rect — while
# the layout inside it is Skia's, and the two read the same string about 1.1 px
# differently. Two defects came out of that seam, both found by rendering the
# feature matrix and looking at it rather than by any assertion:
#
#   * a line Qt fits wrapped under Skia, and the wrapped remainder fell outside
#     the surface (sized from the same too-narrow width) and was clipped away;
#   * when a source line did wrap, `_draw_horizontal` still advanced by a
#     single line height, painting the next source line on top of the
#     continuation of the previous one.
# ---------------------------------------------------------------------------


def _wrap_state(text, **overrides):
    args = dict(
        text=text, font_family="", font_size=24.0, text_color="#000000",
        alignment=QtCore.Qt.AlignmentFlag.AlignHCenter, line_spacing=1.2,
        outline_color=None, outline_width=0.0, bold=False, italic=False,
        underline=False, position=(20, 20), rotation=0.0, scale=1.0,
        transform_origin=(0, 0), width=250.0, height=120.0,
        direction=QtCore.Qt.LayoutDirection.LeftToRight, vertical=False,
        outline=False,
    )
    args.update(overrides)
    return build_text_item_state(**args)


def _ink_rows(state_dict, height=400, width=400):
    """Which pixel rows carry ink, rendered large enough that nothing clips."""
    renderer = ImageSaveRenderer(
        np.full((height, width, 3), 255, dtype=np.uint8)
    )
    renderer.add_state_to_image({"text_items_state": [state_dict]})
    image = renderer.render_to_image()
    return (image[:, :, :3].min(axis=2) < 128).any(axis=1)


def _ink_bands(rows):
    """Count runs of inked rows — one band per painted line of text."""
    bands, previous = 0, False
    for row in rows:
        if row and not previous:
            bands += 1
        previous = bool(row)
    return bands


@requires_skia
def test_skia_does_not_wrap_a_line_qt_fits_on_one(qapp):
    """A short line must stay one line under Skia.

    Qt sizes the box to its own measurement of the text; Skia reads the same
    string a little wider, so laying out inside that box wrapped the last word
    onto a line Qt never had — and the surface, sized from the same width,
    then clipped it off entirely.
    """
    state = _wrap_state("small print here", font_size=10.0)

    text_engine.set_engine(text_engine.QT)
    qt_bands = _ink_bands(_ink_rows(state))
    text_engine.set_engine(text_engine.SKIA)
    skia_bands = _ink_bands(_ink_rows(state))

    assert qt_bands == 1, f"the Qt baseline itself wrapped ({qt_bands} lines)"
    assert skia_bands == 1, (
        f"Skia split a single line into {skia_bands} — it is wrapping text Qt "
        "did not wrap"
    )


@requires_skia
def test_skia_does_not_clip_a_line_it_wrapped(qapp):
    """Whatever Skia lays out must fit the surface it was given.

    The surface is sized from the same layout width as the draw, so widening
    one without the other silently cuts the overflow off. Rendering the full
    string must therefore never produce *less* ink than rendering a prefix of
    it.
    """
    text_engine.set_engine(text_engine.SKIA)
    full = int(_ink_rows(_wrap_state("日本語のテキスト", font_size=22.0)).sum())
    prefix = int(_ink_rows(_wrap_state("日本語のテキス", font_size=22.0)).sum())
    assert full >= prefix, (
        "adding a character reduced the inked area — the last one is being "
        "clipped off the surface"
    )


@requires_skia
def test_wrapped_source_line_does_not_collide_with_the_next(qapp):
    """A source line that wraps must push the following line down, not overlap it.

    `letter_spacing` widens "Sphinx of black" past the box so it wraps to two
    visual lines; the explicit newline then adds a third. Advancing by a single
    line height painted "quartz" directly on top of "black".
    """
    state = _wrap_state("Sphinx of black\nquartz", letter_spacing=4.0)

    text_engine.set_engine(text_engine.QT)
    qt_bands = _ink_bands(_ink_rows(state))
    text_engine.set_engine(text_engine.SKIA)
    skia_bands = _ink_bands(_ink_rows(state))

    assert qt_bands == 3, f"the Qt baseline drew {qt_bands} lines, expected 3"
    assert skia_bands == qt_bands, (
        f"Skia drew {skia_bands} bands of ink where Qt drew {qt_bands} — a "
        "wrapped line is being painted over by the one after it"
    )


@requires_skia
def test_layout_width_widens_to_skia_measurement_only_when_unwrapped(qapp):
    """The widening is conditional: a genuinely wrapped block must still wrap.

    Guards against a fix that simply lets every block lay out at its natural
    width, which would stop a user-narrowed block from wrapping at all.
    """
    from core.skia_render import SkiaTextRenderer, TextRenderSpec
    from core.text_measure import TextStyle

    renderer = SkiaTextRenderer()
    style = TextStyle(font_family="", font_size=24.0)
    narrow_box = 80.0

    unwrapped = TextRenderSpec(text="Sphinx of black quartz", style=style,
                               soft_wrapped=False)
    wrapped = TextRenderSpec(text="Sphinx of black quartz", style=style,
                             soft_wrapped=True)

    assert renderer._layout_width(narrow_box, wrapped) < renderer._layout_width(
        narrow_box, unwrapped
    ), "soft_wrapped is not being honoured — both cases lay out the same width"


# ---------------------------------------------------------------------------
# Vertical CJK: the two engines must agree on the *algorithm*, even where the
# font metrics they read differ.
#
# They do differ, measurably: for CJK text Qt reports the line box of the
# primary (Latin) font and substitutes glyphs at draw time, while Skia resolves
# the font that actually contains the glyphs and reads its taller line box.
# On this machine that is DejaVu Sans against WenQuanYi Zen Hei, and it makes
# Skia's measurement 6.9% taller for a horizontal CJK line — compounding to
# 19.3% for a vertical one, where the error is carried by every stacked glyph.
#
# Those numbers are font-stack specific and would be a fragile thing to assert.
# What is not font-specific, and what actually pins the layout contract, is how
# `line_spacing` is applied: in vertical CJK it is the gap *between columns*
# (行間), not between stacked glyphs. Both engines must do that, or the same
# document lays out differently depending on which one drew it.
# ---------------------------------------------------------------------------


@requires_skia
def test_vertical_line_spacing_widens_columns_without_stretching_glyphs(qapp):
    """In vertical CJK, line spacing separates columns, not the glyphs in them."""
    from core.skia_text import SkiaTextMeasurer
    from core.text_measure import TextStyle

    measurer = SkiaTextMeasurer()
    text = "縦書きの\nテキスト"

    def measured(line_spacing):
        return measurer.measure(
            text,
            TextStyle(font_family="", font_size=22.0, vertical=True,
                      line_spacing=line_spacing),
        )

    tight_w, tight_h = measured(1.0)
    loose_w, loose_h = measured(2.0)

    assert loose_w > tight_w * 1.5, (
        "doubling line spacing did not widen the columns — vertical line "
        f"spacing is not being applied ({tight_w:.1f} -> {loose_w:.1f})"
    )
    assert loose_h == pytest.approx(tight_h), (
        "line spacing stretched the glyph stack; in vertical text it belongs "
        f"between columns, not between glyphs ({tight_h:.1f} -> {loose_h:.1f})"
    )


def test_qt_vertical_line_spacing_has_the_same_meaning(qapp):
    """The sibling half, so the two engines are pinned to one another's rule."""
    from app.ui.canvas.text_item import TextBlockItem

    def measured(line_spacing):
        item = TextBlockItem(text="x")
        item.set_font_size(22.0)
        item.set_line_spacing(line_spacing)
        item.set_text("縦書きの\nテキスト", 250.0)
        item.set_vertical(True)
        rect = item.text_rect()
        return rect.width(), rect.height()

    tight_w, tight_h = measured(1.0)
    loose_w, loose_h = measured(2.0)

    assert loose_w > tight_w * 1.3
    assert loose_h == pytest.approx(tight_h)


@requires_skia
def test_latin_measurement_agrees_between_engines(qapp):
    """Latin text is the common case and the engines must measure it alike.

    Loose enough not to break on a runner with a different font stack, tight
    enough to catch a real divergence: the observed gap is about 1% of width
    and none of height.
    """
    from core.skia_text import SkiaTextMeasurer
    from core.text_measure import TextStyle
    from app.ui.canvas.text_item import TextBlockItem

    text = "Sphinx of black"
    item = TextBlockItem(text="x")
    item.set_font_size(22.0)
    item.set_text(text, 250.0)
    qt_rect = item.text_rect()

    skia_w, skia_h = SkiaTextMeasurer().measure(
        text, TextStyle(font_family="", font_size=22.0, line_spacing=1.2)
    )

    assert skia_w == pytest.approx(qt_rect.width(), rel=0.05)
    assert skia_h == pytest.approx(qt_rect.height(), rel=0.05)


# ---------------------------------------------------------------------------
# Device scale: the raster has to be made at the resolution it will be drawn at.
#
# `ImageSaveRenderer` draws the scene into a surface twice the page size and
# scales the result back down. Glyphs Qt rasterises into that surface are
# sharpened by the pass. A Skia raster made at 1x is *upscaled* by the same
# transform and then downscaled again, so the pass that sharpens Qt's text was
# softening Skia's — 56% more half-lit edge pixels per unit of ink, visible as a
# grey halo around every glyph in an exported page.
#
# Two things have to hold together, and breaking either is a visible bug:
# render at the painter's scale, and blit by logical rectangle. Rendering at
# scale while blitting at a point would draw the block at twice its size.
# ---------------------------------------------------------------------------


@requires_skia
def test_render_at_scale_produces_a_denser_raster_for_the_same_layout(qapp):
    """Scaling samples the same layout more finely; it does not re-lay it out."""
    from core.skia_render import SkiaTextRenderer, TextRenderSpec
    from core.text_measure import TextStyle

    renderer = SkiaTextRenderer()
    spec = TextRenderSpec(text="Sharpness", style=TextStyle(font_size=30.0))

    one, origin_one, used_one = renderer.render(spec, scale=1.0)
    two, origin_two, used_two = renderer.render(spec, scale=2.0)

    assert used_one == 1.0
    assert used_two == 2.0
    assert two.shape[0] == pytest.approx(one.shape[0] * 2, abs=2)
    assert two.shape[1] == pytest.approx(one.shape[1] * 2, abs=2)
    # The offset is logical, so it must not scale with the surface.
    assert origin_two == pytest.approx(origin_one)


@requires_skia
def test_render_scale_is_clamped_not_refused(qapp):
    """An unreasonable scale is clamped, so no block ever falls back over it."""
    from core.skia_render import MAX_RENDER_SCALE, SkiaTextRenderer, TextRenderSpec
    from core.text_measure import TextStyle

    renderer = SkiaTextRenderer()
    spec = TextRenderSpec(text="Sharpness", style=TextStyle(font_size=30.0))

    _, _, used = renderer.render(spec, scale=99.0)
    assert used == MAX_RENDER_SCALE

    _, _, floor = renderer.render(spec, scale=0.1)
    assert floor == 1.0, "scaling below 1x would render coarser than the page"


@requires_skia
def test_scaled_raster_is_blitted_at_its_logical_size(qapp):
    """A block painted under a 2x transform must not come out twice as big.

    The whole point of the scaled raster is more samples in the same space.
    Blitting it at a point instead of into a logical rectangle puts down one
    device pixel per sample and doubles the block.
    """
    item = TextBlockItem(text="Sharpness")
    item.set_font_size(28.0)
    text_engine.set_engine(text_engine.SKIA)

    def painted_extent(scale):
        size = int(400 * scale)
        image = QtGui.QImage(size, size, QtGui.QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QtGui.QPainter(image)
        painter.scale(scale, scale)
        try:
            item.paint(painter, QtWidgets.QStyleOptionGraphicsItem())
        finally:
            painter.end()
        buffer = image.constBits()
        array = np.frombuffer(buffer, dtype=np.uint8).reshape(size, size, 4)
        columns = np.nonzero(array[:, :, 3].any(axis=0))[0]
        assert columns.size, "nothing was painted"
        # Back into logical units so the two scales are comparable.
        return (columns.max() - columns.min()) / scale

    assert painted_extent(2.0) == pytest.approx(painted_extent(1.0), rel=0.05), (
        "the block changed size with the painter scale — the scaled raster is "
        "not being blitted at its logical size"
    )


@requires_skia
def test_exported_skia_glyphs_are_no_softer_than_qt_glyphs(qapp):
    """The quality claim itself, measured on the export the user ships.

    Edge softness is counted as half-lit pixels per unit of solid ink, and
    compared against Qt in the same run rather than against a fixed number, so
    the assertion does not depend on which fonts the machine happens to have.
    Rendering the raster at 1x while the export works at 2x measured 1.56x
    Qt's softness; sampling at the painter's scale brings it to 1.17x.
    """
    def softness(engine):
        text_engine.set_engine(engine)
        state = build_text_item_state(
            text="Sharpness", font_family="", font_size=44.0,
            text_color="#000000", alignment=QtCore.Qt.AlignmentFlag.AlignLeft,
            line_spacing=1.2, outline_color=None, outline_width=0.0, bold=True,
            italic=False, underline=False, position=(20, 30), rotation=0.0,
            scale=1.0, transform_origin=(0, 0), width=380.0, height=90.0,
            direction=QtCore.Qt.LayoutDirection.LeftToRight, vertical=False,
            outline=False,
        )
        renderer = ImageSaveRenderer(np.full((140, 420, 3), 255, dtype=np.uint8))
        renderer.add_state_to_image({"text_items_state": [state]})
        grey = renderer.render_to_image().min(axis=2).astype(int)
        ink = int((grey < 64).sum())
        half_lit = int(((grey >= 64) & (grey <= 192)).sum())
        assert ink > 0, f"{engine} rendered no text"
        return half_lit / ink

    ratio = softness(text_engine.SKIA) / softness(text_engine.QT)
    assert ratio < 1.35, (
        f"Skia's glyph edges are {ratio:.2f}x as soft as Qt's — the raster is "
        "being made at a coarser resolution than it is drawn at"
    )


# ---------------------------------------------------------------------------
# Drop-shadow fidelity.
#
# SHADOW_BLUR_TO_SIGMA converts Qt's blur radius to Skia's Gaussian sigma. It
# is fitted, and it is easy to fit wrongly — see the note on the constant for
# the three metrics that give the wrong answer. This asserts the property that
# actually matters, on the path that ships: the two engines' shadows must cover
# comparable area at comparable darkness.
#
# Measured at two thresholds rather than one, because a single threshold reads
# a change of shape as a change of amount: a tight dark shadow and a wide faint
# one differ in where their pixels sit, not how many are "ink".
# ---------------------------------------------------------------------------


def _shadow_layer(engine, blur):
    """How much darker the page got when the shadow was switched on."""
    def render(shadow_enabled):
        text_engine.set_engine(engine)
        state = build_text_item_state(
            text="Shadow", font_family="", font_size=40.0, text_color="#000000",
            alignment=QtCore.Qt.AlignmentFlag.AlignLeft, line_spacing=1.2,
            outline_color=None, outline_width=0.0, bold=True, italic=False,
            underline=False, position=(40, 60), rotation=0.0, scale=1.0,
            transform_origin=(0, 0), width=360.0, height=110.0,
            direction=QtCore.Qt.LayoutDirection.LeftToRight, vertical=False,
            outline=False, shadow_enabled=shadow_enabled,
            shadow_color="#ff000000", shadow_offset=(18.0, 18.0),
            shadow_blur=blur,
        )
        renderer = ImageSaveRenderer(np.full((260, 460, 3), 255, dtype=np.uint8))
        renderer.add_state_to_image({"text_items_state": [state]})
        return renderer.render_to_image().min(axis=2).astype(float)

    return np.clip(render(False) - render(True), 0, 255)


@requires_skia
@pytest.mark.parametrize("blur", [3.0, 10.0, 20.0])
def test_skia_shadow_covers_comparable_area_to_qt(qapp, blur):
    """Skia's drop shadow must not be markedly tighter or wider than Qt's."""
    qt_layer = _shadow_layer(text_engine.QT, blur)
    skia_layer = _shadow_layer(text_engine.SKIA, blur)

    assert qt_layer.sum() > 0, "the Qt baseline drew no shadow"
    assert skia_layer.sum() > 0, "Skia drew no shadow at all"

    mass = skia_layer.sum() / qt_layer.sum()
    assert 0.85 < mass < 1.15, (
        f"Skia's shadow carries {mass:.2f}x Qt's total darkness at blur {blur}"
    )

    for threshold in (64, 200):
        qt_area = int((qt_layer > threshold).sum())
        skia_area = int((skia_layer > threshold).sum())
        ratio = skia_area / max(qt_area, 1)
        assert 0.7 < ratio < 1.4, (
            f"at blur {blur}, Skia covers {ratio:.2f}x Qt's area above "
            f"darkness {threshold} — the blur conversion is off"
        )
