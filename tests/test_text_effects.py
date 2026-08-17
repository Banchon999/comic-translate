"""Gradient axis and curved-baseline geometry.

Plain arithmetic, so it is checked here rather than by looking at a rendered
page. What a glyph looks like is the canvas's problem; where it goes is this.
"""

import math

import pytest

from modules.rendering.text_effects import (
    GlyphPlacement,
    arc_bulge,
    arc_placements,
    gradient_line,
)


class TestGradientLine:
    def test_horizontal_spans_the_full_width(self):
        x1, y1, x2, y2 = gradient_line(200, 100, 0)
        assert (x1, x2) == (0.0, 200.0)
        assert y1 == y2 == 50.0

    def test_vertical_spans_the_full_height(self):
        x1, y1, x2, y2 = gradient_line(200, 100, 90)
        assert (round(y1, 6), round(y2, 6)) == (0.0, 100.0)
        assert round(x1, 6) == round(x2, 6) == 100.0

    def test_180_degrees_is_the_reverse_of_0(self):
        forward = gradient_line(200, 100, 0)
        backward = gradient_line(200, 100, 180)
        assert (round(backward[0]), round(backward[1])) == (round(forward[2]), round(forward[3]))
        assert (round(backward[2]), round(backward[3])) == (round(forward[0]), round(forward[1]))

    def test_a_diagonal_reaches_the_far_corner(self):
        """A gradient anchored to the width alone finishes early on a diagonal."""
        width, height = 200.0, 100.0
        x1, y1, x2, y2 = gradient_line(width, height, 45)
        length = math.hypot(x2 - x1, y2 - y1)
        # The rectangle's own diagonal projected onto the 45-degree direction.
        expected = (width + height) / math.sqrt(2)
        assert length == pytest.approx(expected)

    def test_the_line_is_centred_on_the_rectangle(self):
        for angle in (0, 30, 90, 137, 180, 270):
            x1, y1, x2, y2 = gradient_line(200, 100, angle)
            assert (x1 + x2) / 2 == pytest.approx(100.0)
            assert (y1 + y2) / 2 == pytest.approx(50.0)

    def test_an_empty_rectangle_gives_a_degenerate_line(self):
        assert gradient_line(0, 100, 0) == (0.0, 0.0, 0.0, 0.0)
        assert gradient_line(200, 0, 0) == (0.0, 0.0, 0.0, 0.0)


EVEN = [10.0] * 8


class TestStraightBaseline:
    def test_zero_curvature_is_an_ordinary_line(self):
        placements = arc_placements(EVEN, 0.0)
        assert all(p.y == 0.0 and p.angle == 0.0 for p in placements)

    def test_the_glyphs_are_centred_on_the_origin(self):
        placements = arc_placements(EVEN, 0.0)
        assert placements[0].x == pytest.approx(-35.0)
        assert placements[-1].x == pytest.approx(35.0)

    def test_uneven_advances_are_respected(self):
        placements = arc_placements([10.0, 30.0, 10.0], 0.0)
        # Midpoints at 5, 25 and 45 along a 50-wide line centred on 0.
        assert [round(p.x, 6) for p in placements] == [-20.0, 0.0, 20.0]

    def test_no_text_places_nothing(self):
        assert arc_placements([], 0.5) == []


class TestArc:
    def test_a_positive_curvature_arches_upward(self):
        """Screen coordinates point y down, so an arch has the ends below."""
        placements = arc_placements(EVEN, 0.5)
        assert placements[0].y > 0 and placements[-1].y > 0
        middle = min(placements, key=lambda p: abs(p.x))
        assert middle.y < placements[0].y

    def test_a_negative_curvature_sags(self):
        placements = arc_placements(EVEN, -0.5)
        assert placements[0].y < 0 and placements[-1].y < 0

    def test_the_glyphs_turn_with_the_curve(self):
        placements = arc_placements(EVEN, 0.5)
        assert placements[0].angle < 0 < placements[-1].angle
        # Evenly spaced glyphs turn by an equal step.
        steps = [b.angle - a.angle for a, b in zip(placements, placements[1:])]
        assert max(steps) - min(steps) < 1e-9

    def test_full_curvature_is_half_a_turn(self):
        placements = arc_placements(EVEN, 1.0)
        assert placements[-1].angle - placements[0].angle == pytest.approx(
            math.degrees(math.pi) * (1 - 1 / len(EVEN)), rel=1e-6
        )

    def test_the_text_keeps_its_own_length(self):
        """Bending must not stretch or crowd the letters."""
        placements = arc_placements(EVEN, 0.6)
        along = [
            math.hypot(b.x - a.x, b.y - a.y)
            for a, b in zip(placements, placements[1:])
        ]
        # Chords are slightly shorter than the 10pt arc they subtend, never longer.
        assert all(9.0 < step <= 10.0 for step in along)

    def test_a_bend_takes_up_less_width_not_smaller_letters(self):
        straight = arc_placements(EVEN, 0.0)
        curved = arc_placements(EVEN, 0.8)
        assert curved[-1].x - curved[0].x < straight[-1].x - straight[0].x

    def test_the_arc_is_symmetric(self):
        placements = arc_placements(EVEN, 0.7)
        for left, right in zip(placements, reversed(placements)):
            assert left.x == pytest.approx(-right.x)
            assert left.y == pytest.approx(right.y)
            assert left.angle == pytest.approx(-right.angle)

    def test_curvature_is_clamped_rather_than_wrapping_round(self):
        assert arc_placements(EVEN, 5.0) == arc_placements(EVEN, 1.0)
        assert arc_placements(EVEN, -5.0) == arc_placements(EVEN, -1.0)

    def test_a_gentler_curve_bulges_less(self):
        gentle = arc_bulge(arc_placements(EVEN, 0.2))
        steep = arc_bulge(arc_placements(EVEN, 0.9))
        assert 0 < gentle < steep


class TestBulge:
    def test_a_straight_line_does_not_bulge(self):
        assert arc_bulge(arc_placements(EVEN, 0.0)) == 0.0

    def test_the_glyph_height_is_included(self):
        placements = arc_placements(EVEN, 0.5)
        assert arc_bulge(placements, 20.0) == pytest.approx(arc_bulge(placements) + 20.0)

    def test_a_sag_bulges_as_much_as_an_arch(self):
        assert arc_bulge(arc_placements(EVEN, -0.5)) == pytest.approx(
            arc_bulge(arc_placements(EVEN, 0.5))
        )

    def test_nothing_placed_bulges_by_nothing(self):
        assert arc_bulge([]) == 0.0
        assert arc_bulge([GlyphPlacement(0.0, 0.0, 0.0)]) == 0.0
