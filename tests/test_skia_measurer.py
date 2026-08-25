"""The Skia text measurer.

Skia is the engine Phase 2 paints the canvas with, so what it measures has to
agree with what Qt measures — otherwise switching the measurer alone reflows
every page. Two systematic offsets had to be corrected before it did, and both
are pinned here because either regressing is a silent ~30% change to the size
of text on every rendered page:

* `font_size` means **points** in this codebase and **pixels** to Skia;
* `QTextDocument` carries a 4px margin that every Qt measurement includes.

With both applied, Skia and Qt agree on width **exactly** for upright Latin,
Thai and Japanese, and to within a pixel for Arabic. Height agrees to within
about 4% — Qt rounds line heights to whole pixels and Skia does not, and the
CJK line box differs slightly between the two.

Italic is the one real divergence: the two pick different italic faces for the
same family, and Skia comes out about 3.5% narrower. It has its own tolerance
below rather than loosening the others, so a regression elsewhere still fails.

Vertical CJK is a deliberate exception: Qt uses `VerticalTextDocumentLayout`
and Skia has no vertical writing mode at all, so the two stack glyphs by
different rules. Phase 2 makes Skia the painter as well, at which point its own
metrics are the source of truth and preview/export parity is the gate that
matters — see `test_render_parity.py`.
"""

import pytest

from core.text_measure import TextStyle
from core import skia_text
from core.skia_text import SkiaTextMeasurer

pytestmark = pytest.mark.skipif(
    not skia_text.is_available(),
    reason=f"skia-python unavailable: {skia_text.unavailable_reason()}",
)

# Width agreement is exact except where noted; height carries Qt's integer
# line rounding plus a small CJK line-box difference.
WIDTH_TOLERANCE_PX = 1.5
HEIGHT_TOLERANCE_FRACTION = 0.05

#: Cases where the engines genuinely disagree, with the reason.
WIDTH_TOLERANCE_OVERRIDES = {
    # Qt and Skia resolve different italic faces for the same family.
    "italic": 8.0,
}

HORIZONTAL_CASES = {
    "latin": {"text": "Hello world"},
    "latin_two_lines": {"text": "Hello\nworld"},
    "latin_caps": {"text": "THE QUICK BROWN FOX"},
    "bold": {"text": "Hello world", "bold": True},
    "italic": {"text": "Hello world", "italic": True},
    "wide_spacing": {"text": "Hello\nworld", "line_spacing": 2.0},
    "thai": {"text": "สวัสดีครับ"},
    "japanese": {"text": "ありがとうございます"},
    "arabic": {"text": "مرحبا بك"},
}


@pytest.fixture(scope="module")
def skia_measurer():
    return SkiaTextMeasurer()


@pytest.fixture(scope="module")
def qt_measurer(qapp):
    from app.ui.canvas.text.qt_measurer import QtTextMeasurer

    return QtTextMeasurer()


@pytest.mark.parametrize("name", sorted(HORIZONTAL_CASES))
def test_agrees_with_qt_on_horizontal_text(name, skia_measurer, qt_measurer):
    case = dict(HORIZONTAL_CASES[name])
    text = case.pop("text")
    style = TextStyle(font_size=24, font_family="", **case)

    qt_w, qt_h = qt_measurer.measure(text, style)
    sk_w, sk_h = skia_measurer.measure(text, style)

    tolerance = WIDTH_TOLERANCE_OVERRIDES.get(name, WIDTH_TOLERANCE_PX)
    assert abs(sk_w - qt_w) <= tolerance, (
        f"{name}: width {sk_w:.2f} vs Qt {qt_w:.2f} (tolerance {tolerance})"
    )
    assert abs(sk_h - qt_h) <= qt_h * HEIGHT_TOLERANCE_FRACTION, (
        f"{name}: height {sk_h:.2f} vs Qt {qt_h:.2f}"
    )


def test_points_are_converted_to_pixels(skia_measurer):
    """The correction worth ~25% of every measurement."""
    assert skia_measurer.points_to_pixels(24) == pytest.approx(32.0)
    assert skia_measurer.points_to_pixels(72) == pytest.approx(96.0)


def test_document_margin_is_included(skia_measurer):
    """Qt's 4px margin, on both sides of both axes."""
    style = TextStyle(font_size=24, font_family="")
    with_margin = skia_measurer.measure("Hello", style)

    bare = SkiaTextMeasurer(document_margin=0.0).measure("Hello", style)
    assert with_margin[0] - bare[0] == pytest.approx(8.0)
    assert with_margin[1] - bare[1] == pytest.approx(8.0)


def test_empty_text_has_no_width(skia_measurer):
    """Skia leaves LongestLine at -FLT_MAX with nothing to lay out."""
    width, height = skia_measurer.measure("", TextStyle(font_size=24))
    assert width == pytest.approx(2 * skia_measurer.document_margin)
    assert height > 0


def test_line_spacing_scales_height_not_width(skia_measurer):
    style = TextStyle(font_size=24, font_family="")
    single = skia_measurer.measure("a\nb", style)
    doubled = skia_measurer.measure(
        "a\nb", TextStyle(font_size=24, font_family="", line_spacing=2.0)
    )
    assert doubled[0] == pytest.approx(single[0])
    assert doubled[1] > single[1]


def test_thai_has_a_taller_line_box_than_latin(skia_measurer):
    """Why the height is read rather than assumed.

    The Thai face reserves room above and below for vowel and tone marks, so
    its line box is taller than Latin's at the same nominal size — whether or
    not a given string uses those marks. Assuming one line height for
    everything would measure every Thai block short and clip it.
    """
    style = TextStyle(font_size=24, font_family="")
    latin = skia_measurer.measure("Hello", style)
    thai_without_marks = skia_measurer.measure("กาน", style)
    thai_with_marks = skia_measurer.measure("สวัสดีครับ", style)

    assert thai_without_marks[1] > latin[1]
    # It is the face, not the marks: both Thai strings get the same box.
    assert thai_with_marks[1] == pytest.approx(thai_without_marks[1])


def test_vertical_stacks_into_columns(skia_measurer):
    style = TextStyle(font_size=24, font_family="", vertical=True)
    one_column = skia_measurer.measure("ありがとう", style)
    two_columns = skia_measurer.measure("ありがとう\nこんにちは", style)

    assert two_columns[0] > one_column[0], "a second column must add width"
    assert two_columns[1] == pytest.approx(one_column[1], rel=0.2), (
        "equal-length columns should be about equally tall"
    )


def test_vertical_is_taller_than_wide_for_a_single_column(skia_measurer):
    style = TextStyle(font_size=24, font_family="", vertical=True)
    width, height = skia_measurer.measure("ありがとう", style)
    assert height > width


def test_combining_marks_ride_with_their_base_glyph():
    from core.skia_text import _vertical_clusters

    # U+0301 COMBINING ACUTE ACCENT must not take a cell of its own.
    assert _vertical_clusters("éa") == ["é", "a"]
    assert _vertical_clusters("abc") == ["a", "b", "c"]
    assert _vertical_clusters("") == []


def test_measurements_are_cached_but_bounded(skia_measurer):
    style = TextStyle(font_size=24, font_family="")
    first = skia_measurer.measure("cache me", style)
    second = skia_measurer.measure("cache me", style)
    assert first == second
    assert len(skia_measurer._size_cache) <= skia_measurer.CACHE_LIMIT


def test_blank_family_resolves_to_something(skia_measurer):
    assert skia_measurer.resolve_font_family("  ") in skia_measurer.default_families
    assert skia_measurer.resolve_font_family("Comic Sans MS") == "Comic Sans MS"
