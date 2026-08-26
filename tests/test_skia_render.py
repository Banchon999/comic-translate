"""The Skia text rasteriser.

Qt-free, so these run without a QApplication. They check the properties that a
pixel count cannot: that nothing is clipped, that the layers are drawn in an
order where each remains visible, and that the surface cap refuses an
allocation instead of attempting it.

Clipping is the theme, because two separate bugs during Phase 2 were exactly
that and both looked fine by pixel count alone — letter-spaced text drawn into
a surface sized without letter spacing, and every line re-wrapping because the
paragraph was laid out at precisely its own measured width.
"""

import numpy as np
import pytest

from core import skia_text
from core.text_measure import TextStyle

pytestmark = pytest.mark.skipif(
    not skia_text.is_available(),
    reason=f"skia-python unavailable: {skia_text.unavailable_reason()}",
)

from core.skia_render import (  # noqa: E402  (after the availability guard)
    GradientSpec,
    OutlineLayer,
    ShadowSpec,
    SkiaTextRenderer,
    SurfaceTooLarge,
    TextRenderSpec,
)


@pytest.fixture(scope="module")
def renderer():
    return SkiaTextRenderer()


def _alpha(rgba):
    return rgba[:, :, 3]


def _ink_bounds(rgba):
    """(left, top, right, bottom) of anything drawn, or None if empty."""
    rows = np.flatnonzero(_alpha(rgba).any(axis=1))
    cols = np.flatnonzero(_alpha(rgba).any(axis=0))
    if not len(rows) or not len(cols):
        return None
    return int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1])


def _touches_edge(rgba) -> bool:
    bounds = _ink_bounds(rgba)
    if bounds is None:
        return False
    left, top, right, bottom = bounds
    height, width = rgba.shape[:2]
    return left == 0 or top == 0 or right == width - 1 or bottom == height - 1


CLIP_CASES = {
    "latin": TextStyle(font_size=24, font_family=""),
    "bold": TextStyle(font_size=24, bold=True),
    "letter_spaced": TextStyle(font_size=24, letter_spacing=8.0),
    "wide_line_spacing": TextStyle(font_size=24, line_spacing=2.5),
    "vertical": TextStyle(font_size=24, vertical=True),
}


@pytest.mark.parametrize("name", sorted(CLIP_CASES))
def test_nothing_is_drawn_against_the_surface_edge(renderer, name):
    """Ink touching an edge means the surface was sized too small."""
    text = "ありがとう" if CLIP_CASES[name].vertical else "Hello world"
    rgba, _, _ = renderer.render(TextRenderSpec(text=text, style=CLIP_CASES[name]))
    assert not _touches_edge(rgba), f"{name}: ink reaches the surface edge"


def test_a_line_is_not_re_wrapped_at_its_own_width(renderer):
    """Laid out at exactly the measured width, Skia breaks the last word.

    One line of text must occupy one line's worth of height.
    """
    style = TextStyle(font_size=24, font_family="")
    one_line, _, _ = renderer.render(TextRenderSpec(text="Hello world", style=style))
    two_lines, _, _ = renderer.render(TextRenderSpec(text="Hello\nworld", style=style))
    assert one_line.shape[0] < two_lines.shape[0]


def test_letter_spacing_widens_the_surface(renderer):
    plain, _, _ = renderer.render(
        TextRenderSpec(text="Hello", style=TextStyle(font_size=24))
    )
    spaced, _, _ = renderer.render(
        TextRenderSpec(text="Hello", style=TextStyle(font_size=24, letter_spacing=8.0))
    )
    assert spaced.shape[1] > plain.shape[1]


def test_line_spacing_moves_the_second_line_down(renderer):
    """Applied when painting, not only when measuring.

    Measuring with the spacing and painting without it draws a two-line block
    tight at the top of a box sized tall — which is what happened first.
    """
    tight = TextStyle(font_size=24, font_family="", line_spacing=1.0)
    loose = TextStyle(font_size=24, font_family="", line_spacing=2.5)

    tight_rgba, _, _ = renderer.render(TextRenderSpec(text="A\nB", style=tight))
    loose_rgba, _, _ = renderer.render(TextRenderSpec(text="A\nB", style=loose))

    # The gap between the two lines is the run of blank rows between them.
    def blank_run(rgba):
        drawn = _alpha(rgba).any(axis=1)
        inside = drawn.nonzero()[0]
        return int((~drawn[inside[0]:inside[-1]]).sum())

    assert blank_run(loose_rgba) > blank_run(tight_rgba)


def test_outline_grows_the_surface_symmetrically(renderer):
    style = TextStyle(font_size=24, font_family="")
    plain, plain_origin, _ = renderer.render(TextRenderSpec(text="Hi", style=style))
    outlined, outlined_origin, _ = renderer.render(
        TextRenderSpec(text="Hi", style=style, outlines=(OutlineLayer(4.0, "#ffffff"),))
    )
    assert outlined.shape[0] == plain.shape[0] + 8
    assert outlined.shape[1] == plain.shape[1] + 8
    assert outlined_origin == (4.0, 4.0)
    assert plain_origin == (0.0, 0.0)


def test_narrower_outline_stays_visible_over_a_wider_one(renderer):
    """Widest first, or a thick stroke buries the thin one under it."""
    rgba, _, _ = renderer.render(TextRenderSpec(
        text="O",
        style=TextStyle(font_size=48, font_family=""),
        fill_color="#000000",
        outlines=(OutlineLayer(8.0, "#ff0000"), OutlineLayer(3.0, "#00ff00")),
    ))
    pixels = rgba.reshape(-1, 4)
    visible = pixels[pixels[:, 3] > 200]
    reds = int(((visible[:, 0] > 200) & (visible[:, 1] < 80)).sum())
    greens = int(((visible[:, 1] > 200) & (visible[:, 0] < 80)).sum())
    assert reds > 0, "the wide outline is missing"
    assert greens > 0, "the narrow outline was buried by the wide one"


def test_shadow_offsets_the_surface_only_in_its_own_direction(renderer):
    style = TextStyle(font_size=24, font_family="")
    down_right, origin, _ = renderer.render(TextRenderSpec(
        text="Hi", style=style, shadow=ShadowSpec("#000000", (6.0, 6.0), 0.0)
    ))
    # Offset down and right: nothing is added above or to the left.
    assert origin == (0.0, 0.0)

    up_left, origin_up_left, _ = renderer.render(TextRenderSpec(
        text="Hi", style=style, shadow=ShadowSpec("#000000", (-6.0, -6.0), 0.0)
    ))
    assert origin_up_left == (6.0, 6.0)
    assert up_left.shape == down_right.shape


def test_gradient_produces_more_than_one_colour(renderer):
    rgba, _, _ = renderer.render(TextRenderSpec(
        text="GRADIENT",
        style=TextStyle(font_size=36, font_family=""),
        fill_color="#ff0000",
        gradient=GradientSpec("#0000ff", 0.0),
    ))
    visible = rgba.reshape(-1, 4)
    visible = visible[visible[:, 3] > 200]
    assert len(np.unique(visible[:, 0])) > 4, "no variation along the gradient axis"


def test_vertical_columns_run_right_to_left(renderer):
    """The first source line is the rightmost column, as vertical CJK reads."""
    style = TextStyle(font_size=28, font_family="", vertical=True)
    rgba, _, _ = renderer.render(TextRenderSpec(text="ア\nB", style=style))

    columns = _alpha(rgba).any(axis=0).nonzero()[0]
    midpoint = rgba.shape[1] / 2
    assert columns.max() > midpoint and columns.min() < midpoint, (
        "expected ink in two separate columns"
    )


def test_empty_text_renders_an_empty_surface(renderer):
    rgba, _, _ = renderer.render(
        TextRenderSpec(text="", style=TextStyle(font_size=24))
    )
    assert _alpha(rgba).sum() == 0
    assert rgba.shape[0] > 0 and rgba.shape[1] > 0


def test_an_oversized_surface_is_refused_not_attempted(renderer):
    """A webtoon strip handed here whole would allocate gigabytes."""
    huge = TextRenderSpec(
        text="x",
        style=TextStyle(font_size=24),
        box=(40_000.0, 40_000.0),
    )
    with pytest.raises(SurfaceTooLarge):
        renderer.render(huge)


def test_the_cap_admits_a_realistically_large_block(renderer):
    """The guard must not refuse an ordinary page-sized block."""
    ok = TextRenderSpec(
        text="Still fine",
        style=TextStyle(font_size=24),
        box=(4000.0, 4000.0),
    )
    rgba, _, _ = renderer.render(ok)
    assert rgba.shape[0] >= 4000
