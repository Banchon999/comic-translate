"""Denoising is confined to a faded ring around the mask.

The risk with a denoiser on a comic page is not that it fails to smooth — it is
that it smooths artwork it was never asked to touch. Most of these check what it
leaves alone.
"""

import numpy as np
import pytest

from modules.inpainting.denoise import (
    DEFAULT_OUTLINE_SIZE,
    denoise_around_mask,
)


def noisy_page(seed=0):
    rng = np.random.default_rng(seed)
    page = np.full((120, 200, 3), 180, dtype=np.uint8)
    noise = rng.integers(-40, 40, page.shape, dtype=np.int16)
    return np.clip(page.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def centre_mask():
    mask = np.zeros((120, 200), dtype=np.uint8)
    mask[50:70, 80:120] = 255
    return mask


def test_pixels_far_from_the_mask_are_untouched_bit_for_bit():
    page = noisy_page()
    out = denoise_around_mask(page, centre_mask())
    far = (slice(0, 20), slice(0, 20))
    assert np.array_equal(out[far], page[far])


def test_the_masked_area_is_smoothed():
    page = noisy_page()
    out = denoise_around_mask(page, centre_mask())
    region = (slice(55, 65), slice(90, 110))
    assert out[region].std() < page[region].std()


def test_the_ring_reaches_past_the_mask_but_not_far():
    page = noisy_page()
    mask = centre_mask()
    out = denoise_around_mask(page, mask, outline_size=DEFAULT_OUTLINE_SIZE)
    changed = np.any(out != page, axis=2)
    ys, xs = np.nonzero(changed)
    assert ys.size, "nothing was changed at all"
    # The mask spans rows 50..70, cols 80..120. Allow the outline plus the fade.
    assert ys.min() >= 50 - 20 and ys.max() <= 70 + 20
    assert xs.min() >= 80 - 20 and xs.max() <= 120 + 20


def test_a_flat_area_is_left_exactly_alone():
    """Nothing to remove, so nothing should be touched."""
    flat = np.full((80, 80, 3), 200, dtype=np.uint8)
    out = denoise_around_mask(flat, np.pad(np.full((20, 20), 255, np.uint8), 30))
    assert np.array_equal(out, flat)


def test_an_empty_mask_changes_nothing():
    page = noisy_page()
    out = denoise_around_mask(page, np.zeros((120, 200), dtype=np.uint8))
    assert np.array_equal(out, page)


def test_the_result_keeps_the_input_shape_and_dtype():
    page = noisy_page()
    out = denoise_around_mask(page, centre_mask())
    assert out.shape == page.shape and out.dtype == page.dtype


def test_greyscale_pages_work():
    rng = np.random.default_rng(1)
    page = np.clip(np.full((80, 80), 180, np.int16) + rng.integers(-40, 40, (80, 80)), 0, 255).astype(np.uint8)
    mask = np.zeros((80, 80), np.uint8)
    mask[30:50, 30:50] = 255
    out = denoise_around_mask(page, mask)
    assert out.shape == page.shape
    assert out[35:45, 35:45].std() < page[35:45, 35:45].std()


def test_line_art_through_the_mask_survives():
    """A median cannot invent a value, so a hard black line stays black."""
    page = np.full((80, 120, 3), 220, dtype=np.uint8)
    page[38:42, :] = 0  # a line straight through the region
    mask = np.zeros((80, 120), np.uint8)
    mask[30:50, 40:80] = 255
    out = denoise_around_mask(page, mask, min_std=0.0)
    assert out[39, 60].max() < 60, "the line was washed out"
    assert out[10, 60].min() > 200, "the paper around it darkened"


class TestManyRegions:
    """A page mask covers every bubble at once, so one bounding box is the page.

    Working per region is what keeps the cost proportional to the lettering
    rather than to the paper around it — and it has to give the same answer.
    """

    @staticmethod
    def two_far_apart():
        mask = np.zeros((120, 200), np.uint8)
        mask[20:40, 20:50] = 255
        mask[80:100, 150:180] = 255
        return mask

    def test_both_regions_are_denoised(self):
        page = noisy_page()
        out = denoise_around_mask(page, self.two_far_apart())
        for region in ((slice(25, 35), slice(25, 45)), (slice(85, 95), slice(155, 175))):
            assert out[region].std() < page[region].std()

    def test_the_gap_between_them_is_untouched(self):
        """A single bounding box would have swept the whole page in with them."""
        page = noisy_page()
        out = denoise_around_mask(page, self.two_far_apart())
        gap = (slice(55, 70), slice(80, 130))
        assert np.array_equal(out[gap], page[gap])

    def test_a_region_is_unaffected_by_a_distant_second_one(self):
        """Splitting the page into windows must not change any pixel's value.

        This is the property the whole optimisation rests on: a region is
        denoised from the original image using the mask cropped around it, so
        what else is on the page cannot move its result.
        """
        page = noisy_page()
        alone = np.zeros((120, 200), np.uint8)
        alone[20:40, 20:50] = 255

        one = denoise_around_mask(page, alone)
        both = denoise_around_mask(page, self.two_far_apart())
        near_the_first = (slice(0, 60), slice(0, 100))
        assert np.array_equal(both[near_the_first], one[near_the_first])

    def test_merging_two_close_regions_does_not_smooth_between_them(self):
        """Their windows merge into one, but the ring is still the ring."""
        page = noisy_page()
        near = np.zeros((120, 200), np.uint8)
        near[50:52, 20:22] = 255
        near[50:52, 90:92] = 255
        out = denoise_around_mask(page, near)
        between = (slice(50, 52), slice(45, 65))
        assert np.array_equal(out[between], page[between])

    def test_a_flat_region_is_skipped_while_a_noisy_one_is_not(self):
        """The noise test is per region, so flat art next to grain is kept."""
        page = np.full((120, 200, 3), 200, dtype=np.uint8)
        rng = np.random.default_rng(2)
        noisy = (slice(0, 120), slice(120, 200))
        page[noisy] = np.clip(
            page[noisy].astype(np.int16) + rng.integers(-40, 40, page[noisy].shape), 0, 255
        ).astype(np.uint8)

        out = denoise_around_mask(page, self.two_far_apart())
        flat = (slice(20, 40), slice(20, 50))
        assert np.array_equal(out[flat], page[flat]), "flat art should be left alone"
        grainy = (slice(85, 95), slice(155, 175))
        assert out[grainy].std() < page[grainy].std()

    def test_hundreds_of_glyphs_stay_within_the_page(self):
        """Text masks are per-glyph, so a page really does have hundreds."""
        page = noisy_page(3)
        mask = np.zeros((120, 200), np.uint8)
        for y in range(10, 110, 20):
            for x in range(10, 190, 12):
                mask[y:y + 10, x:x + 6] = 255
        out = denoise_around_mask(page, mask)
        assert out.shape == page.shape
        assert out.std() < page.std()


@pytest.mark.parametrize("bad", [None])
def test_missing_input_is_handled(bad):
    assert denoise_around_mask(bad, centre_mask()) is bad
    assert denoise_around_mask(noisy_page(), bad) is not None


def test_zero_outline_still_covers_the_mask_itself():
    page = noisy_page()
    out = denoise_around_mask(page, centre_mask(), outline_size=0, fade_radius=0)
    region = (slice(55, 65), slice(90, 110))
    assert out[region].std() < page[region].std()
