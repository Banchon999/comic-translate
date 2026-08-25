"""The text measurement seam.

`pyside_word_wrap` decides what a rendered page looks like, and it now asks
`core.text_measure` rather than QTextDocument directly. Two things have to hold
for that to be safe, and neither is obvious from reading the call site:

* the wrap really does go through the registered measurer, so Phase 1 can swap
  Skia in and have it take effect;
* the outline still pads the box the fit search works against, because it moved
  out of the measurer and into the caller when the seam was introduced.

Absolute glyph metrics are not asserted — they depend on the fonts installed on
the machine. The refactor that introduced the seam was checked against the
previous implementation over 256 text/ROI/orientation/outline combinations with
zero differences; these tests guard the structure that made that true.
"""

import pytest

from core.enums import Alignment, LayoutDirection
from core.text_measure import TextMeasurer, TextStyle, get_measurer, set_measurer
from modules.rendering.render import pyside_word_wrap


class GridMeasurer(TextMeasurer):
    """Every glyph is `font_size` wide and tall. Makes the maths checkable."""

    name = "grid"

    def __init__(self):
        self.calls = []

    def measure(self, text, style):
        self.calls.append((text, style))
        lines = text.split("\n") or [""]
        width = max((len(line) for line in lines), default=0) * style.font_size
        height = len(lines) * style.font_size * style.line_spacing
        return float(width), float(height)


@pytest.fixture
def grid_measurer():
    measurer = GridMeasurer()
    set_measurer(measurer)
    yield measurer
    set_measurer(None)


def _wrap(**overrides):
    args = dict(
        text="aaa bbb ccc ddd",
        font_input="",
        roi_width=100,
        roi_height=100,
        line_spacing=1.0,
        outline_width=0.0,
        bold=False,
        italic=False,
        underline=False,
        alignment=Alignment.Center,
        direction=LayoutDirection.LeftToRight,
        init_font_size=40,
        min_font_size=5,
    )
    args.update(overrides)
    return pyside_word_wrap(**args)


def test_wrap_consults_the_registered_measurer(grid_measurer):
    _wrap()
    assert grid_measurer.calls, "pyside_word_wrap never called the measurer"


def test_wrap_respects_the_registered_measurer_metrics(grid_measurer):
    # 10 chars wide at size 10 exactly fills a 100px box; size 11 cannot.
    _, size = _wrap(text="abcdefghij", roi_width=100, roi_height=1000,
                    init_font_size=40, min_font_size=5)
    assert size == 10


def test_outline_is_added_by_the_caller_not_the_measurer(grid_measurer):
    """The measurer reports the glyph box; the outline pads it afterwards."""
    _, plain = _wrap(text="abcdefghij", roi_width=100, roi_height=1000)
    _, outlined = _wrap(text="abcdefghij", roi_width=100, roi_height=1000,
                        outline_width=10.0)
    assert outlined < plain, "a thick outline must force a smaller fit"

    # And the measurer itself was never told about it.
    assert all(not hasattr(style, "outline_width")
               for _text, style in grid_measurer.calls)


def test_metrics_returned_for_persistence_exclude_the_outline(grid_measurer):
    _, size, w, h = _wrap(text="abcde", outline_width=7.0, return_metrics=True)
    assert (w, h) == (5 * size, 1 * size), "persisted box must be the text box"


def test_style_with_size_does_not_mutate_the_original():
    style = TextStyle(font_size=12.0, font_family="Comic")
    bigger = style.with_size(30.0)
    assert style.font_size == 12.0
    assert bigger.font_size == 30.0
    assert bigger.font_family == "Comic"


def test_default_measurer_is_qt_when_qt_is_present(qapp):
    set_measurer(None)
    try:
        assert get_measurer().name == "qt"
    finally:
        set_measurer(None)


def test_qt_measurer_grows_with_font_size(qapp):
    set_measurer(None)
    try:
        measurer = get_measurer()
        small = measurer.measure("Hello", TextStyle(font_size=10))
        large = measurer.measure("Hello", TextStyle(font_size=40))
        assert large[0] > small[0] and large[1] > small[1]
    finally:
        set_measurer(None)


def test_qt_measurer_resolves_a_blank_family(qapp):
    set_measurer(None)
    try:
        assert get_measurer().resolve_font_family("   ")
    finally:
        set_measurer(None)
