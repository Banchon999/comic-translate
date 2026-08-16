"""Flood selection picks the region under the cursor and stops at its edge."""

import numpy as np
import pytest

from modules.utils.flood_select import (
    DEFAULT_TOLERANCE,
    flood_select,
    mask_to_polygons,
)


def a_page():
    """A grey page with a white speech bubble outlined in black, and a
    separate white box that must not be selected with it."""
    page = np.full((120, 200, 3), 128, dtype=np.uint8)
    page[20:70, 20:100] = 0        # bubble outline
    page[24:66, 24:96] = 255       # bubble interior
    page[85:110, 140:190] = 255    # an unrelated white box
    return page


class TestHoleFilling:
    """A region's enclosed gaps belong to it, for this tool's purpose.

    Clicking a bubble's white interior selects everything but the lettering,
    since the lettering is a different colour. The lettering is exactly what is
    about to be cleaned, so leaving it out produces a mask shaped like a ring
    around the words.
    """

    def _bubble_with_text(self):
        page = np.full((100, 160, 3), 128, dtype=np.uint8)
        page[10:90, 10:150] = 255
        page[40:60, 30:130] = 30
        return page

    def test_lettering_inside_the_region_is_included(self):
        mask = flood_select(self._bubble_with_text(), 20, 20, feather=0)
        assert mask[50, 80] == 255

    def test_turning_it_off_leaves_the_hole(self):
        mask = flood_select(self._bubble_with_text(), 20, 20, feather=0, fill_holes=False)
        assert mask[50, 80] == 0

    def test_it_does_not_reach_past_the_region(self):
        mask = flood_select(self._bubble_with_text(), 20, 20, feather=0)
        assert mask[5, 5] == 0, "filled outside the bubble entirely"

    def test_a_gap_open_to_the_edge_is_not_a_hole(self):
        # A C-shape: the gap in it reaches the image border, so it is outside
        # the region rather than enclosed by it.
        page = np.full((60, 60, 3), 0, dtype=np.uint8)
        page[10:50, 10:50] = 255
        page[20:40, 30:60] = 0  # notch cut out to the right edge
        mask = flood_select(page, 15, 30, feather=0)
        assert mask[30, 55] == 0


def test_selects_the_bubble_interior():
    mask = flood_select(a_page(), seed_x=60, seed_y=45, feather=0)
    assert mask[45, 60] == 255
    # Every interior pixel, and nothing outside the outline.
    assert mask[25:65, 25:95].all()
    assert mask[10, 10] == 0


def test_stops_at_the_outline():
    mask = flood_select(a_page(), seed_x=60, seed_y=45, feather=0)
    assert mask[45, 22] == 0, "leaked into the black outline"
    assert mask[45, 10] == 0, "leaked out onto the page"


def test_does_not_jump_to_a_disconnected_region_of_the_same_colour():
    mask = flood_select(a_page(), seed_x=60, seed_y=45, feather=0)
    assert mask[95, 160] == 0, "selected the unrelated white box too"


def test_non_contiguous_takes_every_similar_region():
    mask = flood_select(a_page(), seed_x=60, seed_y=45, feather=0, contiguous=False)
    assert mask[45, 60] == 255
    assert mask[95, 160] == 255, "non-contiguous should reach the other white box"


def test_feather_grows_the_selection_over_the_antialiased_edge():
    page = a_page()
    tight = flood_select(page, 60, 45, feather=0)
    grown = flood_select(page, 60, 45, feather=2)
    assert grown.sum() > tight.sum()
    # It grows outward from the same region, not somewhere else.
    assert np.all(grown[tight > 0] == 255)


def test_seeding_on_the_outline_selects_the_outline():
    """And must not swallow what the outline encloses.

    Hole filling is bounded to holes smaller than the region enclosing them,
    which is what keeps this true. Without that bound a 4px bubble border would
    hand back the whole bubble — and, on a real page, clicking a panel border
    would hand back the entire panel.
    """
    mask = flood_select(a_page(), seed_x=22, seed_y=45, feather=0)
    assert mask[45, 22] == 255
    assert mask[45, 60] == 0, "should not have crossed into the interior"


def test_tolerance_controls_how_far_a_gradient_is_followed():
    # A left-to-right ramp: with a tight tolerance only nearby columns join.
    ramp = np.tile(np.linspace(0, 255, 200, dtype=np.uint8), (60, 1))
    page = np.stack([ramp] * 3, axis=-1)
    tight = flood_select(page, 100, 30, tolerance=4, feather=0)
    loose = flood_select(page, 100, 30, tolerance=64, feather=0)
    assert loose.sum() > tight.sum()


@pytest.mark.parametrize("x, y", [(-1, 10), (10, -1), (500, 10), (10, 500)])
def test_a_seed_outside_the_image_selects_nothing(x, y):
    assert flood_select(a_page(), x, y) is None


def test_an_empty_image_selects_nothing():
    assert flood_select(np.zeros((0, 0, 3), dtype=np.uint8), 0, 0) is None
    assert flood_select(None, 0, 0) is None


def test_a_greyscale_page_works_like_a_colour_one():
    grey = np.full((60, 60), 128, dtype=np.uint8)
    grey[20:40, 20:40] = 255
    mask = flood_select(grey, 30, 30, feather=0)
    assert mask[30, 30] == 255
    assert mask[5, 5] == 0


def test_an_rgba_page_ignores_the_alpha_channel():
    page = np.dstack([a_page(), np.full((120, 200), 255, np.uint8)])
    mask = flood_select(page, 60, 45, feather=0)
    assert mask[45, 60] == 255


def test_a_flat_page_selects_everything():
    flat = np.full((40, 40, 3), 200, dtype=np.uint8)
    mask = flood_select(flat, 20, 20, feather=0)
    assert mask.all()


class TestPolygons:
    def test_the_bubble_becomes_one_outline(self):
        mask = flood_select(a_page(), 60, 45, feather=0)
        polygons = mask_to_polygons(mask)
        assert len(polygons) == 1
        assert polygons[0].shape[1] == 2

    def test_specks_are_dropped(self):
        mask = np.zeros((60, 60), dtype=np.uint8)
        mask[10:40, 10:40] = 255  # a real region
        mask[50, 50] = 255        # a single-pixel speck
        assert len(mask_to_polygons(mask, min_area=4)) == 1

    def test_an_empty_mask_has_no_outlines(self):
        assert mask_to_polygons(np.zeros((10, 10), dtype=np.uint8)) == []
        assert mask_to_polygons(None) == []


def test_default_tolerance_is_tight_enough_to_respect_an_outline():
    """Regression guard: a loose default makes the tool useless on comic art."""
    page = a_page()
    mask = flood_select(page, 60, 45, tolerance=DEFAULT_TOLERANCE, feather=0)
    assert mask[45, 10] == 0
