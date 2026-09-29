"""The stitch engine: where it cuts a joined webtoon strip, and what it writes.

Synthetic slices stand in for a chapter: white pages with "panels" (noisy
blocks, which score high everywhere) separated by white gutters. A slice edge
through a panel is exactly the case the tool exists for.
"""

import numpy as np
import pytest
from PIL import Image

from modules.utils.stitch import (
    StitchSettings,
    assemble,
    find_cuts,
    normalise_widths,
    page_names,
    plan,
    preview_pages,
    row_busyness,
    slice_edges,
    strip_profile,
    write_pages,
)

RNG = np.random.default_rng(7)


def page(height, width=400, panels=()):
    """White slice with noisy panels at the given (top, bottom) row ranges."""
    img = np.full((height, width, 3), 255, np.uint8)
    for top, bottom in panels:
        img[top:bottom, 20:width - 20] = RNG.integers(0, 255, (bottom - top, width - 40, 3), dtype=np.uint8)
    return img


def strip_rows_of(stitch_plan):
    return np.concatenate(stitch_plan.slices, axis=0)


class TestDetector:
    def test_a_flat_row_scores_zero_and_line_art_scores_high(self):
        img = page(10, panels=[(5, 10)])
        scores = row_busyness(img)
        assert scores[:5].max() == 0
        assert scores[5:].min() > 50

    def test_a_tinted_gutter_is_as_quiet_as_a_white_one(self):
        img = np.full((4, 50, 3), (30, 60, 200), np.uint8)
        assert row_busyness(img).max() == 0

    def test_the_edge_margin_ignores_a_border_line(self):
        img = page(6, width=100)
        img[:, 2] = 0  # a vertical frame line near the edge
        assert row_busyness(img).max() > 200
        assert row_busyness(img, margin=5).max() == 0


class TestCuts:
    def test_the_cut_lands_in_the_gutter_not_through_the_panel(self):
        # Panels 0-900 and 1000-2000; the target (1000) falls inside the second panel.
        profile = strip_profile([page(2000, panels=[(0, 900), (1000, 2000)])])
        cuts, forced = find_cuts(profile, StitchSettings(target_height=1000, scan_step=5))
        assert len(cuts) == 1 and not forced
        assert 900 <= cuts[0] < 1000

    def test_the_cut_keeps_off_the_panel_edge(self):
        # Target 1100 is inside the lower panel; the nearest quiet row is 999,
        # right against it. The cut moves up into the gutter by the inset.
        profile = strip_profile([page(2000, panels=[(0, 800), (1000, 2000)])])
        cuts, _ = find_cuts(profile, StitchSettings(target_height=1100, scan_step=5))
        assert 840 <= cuts[0] <= 960

    def test_a_blank_stretch_is_cut_at_the_target(self):
        profile = np.zeros(3000, np.float32)
        cuts, _ = find_cuts(profile, StitchSettings(target_height=1000))
        assert cuts == [1000, 2000]

    def test_no_quiet_row_forces_a_cut_at_the_target(self):
        profile = strip_profile([page(3000, panels=[(0, 3000)])])
        cuts, forced = find_cuts(profile, StitchSettings(target_height=1000))
        assert cuts == [1000, 2000] and forced == [1000, 2000]

    def test_a_short_tail_is_merged_into_the_last_page(self):
        profile = np.zeros(2100, np.float32)
        cuts, _ = find_cuts(profile, StitchSettings(target_height=1000))
        assert cuts == [1000]  # a 100-row page 3 would be useless

    def test_zero_target_means_one_long_page(self):
        profile = strip_profile([page(5000)])
        assert find_cuts(profile, StitchSettings(target_height=0)) == ([], [])

    def test_sensitivity_100_accepts_only_perfectly_flat_rows(self):
        profile = np.full(3000, 3.0, np.float32)  # faint noise everywhere
        _, forced = find_cuts(profile, StitchSettings(target_height=1000, sensitivity=100))
        assert forced == [1000, 2000]
        _, forced = find_cuts(profile, StitchSettings(target_height=1000, sensitivity=95))
        assert forced == []

    def test_a_cut_never_makes_a_page_shorter_than_half_the_target(self):
        # The only gutter is at 200, far above the target: better to force than to make a stub.
        profile = strip_profile([page(2500, panels=[(0, 190), (210, 2500)])])
        cuts, forced = find_cuts(profile, StitchSettings(target_height=1000))
        assert all(c >= 500 for c in cuts)
        assert forced


class TestPlanAndAssemble:
    def test_a_page_spanning_three_slices_is_copied_exactly(self):
        slices = [page(300, panels=[(0, 300)]), page(300, panels=[(0, 300)]), page(300, panels=[(0, 300)])]
        p = plan(slices, StitchSettings(target_height=800, scan_step=1))
        pages = list(assemble(p))
        strip = np.concatenate(slices, axis=0)
        assert p.bounds == [0, 900]  # tail of 100 rows merged
        assert np.array_equal(pages[0], strip)

    def test_page_heights_add_up_to_the_strip(self):
        slices = [page(h, panels=[(10, h - 10)]) for h in (700, 1300, 450, 2200, 900)]
        p = plan(slices, StitchSettings(target_height=1500))
        pages = list(assemble(p))
        assert sum(pg.shape[0] for pg in pages) == sum(s.shape[0] for s in slices)
        assert np.array_equal(np.concatenate(pages, axis=0), strip_rows_of(p))

    def test_a_bubble_across_a_slice_edge_ends_up_on_one_page(self):
        # Slice 1 ends mid-panel (rows 800-1000 of the strip are one panel split 200/.. by the edge).
        a = page(1000, panels=[(100, 700), (800, 1000)])
        b = page(1000, panels=[(0, 300), (400, 950)])
        p = plan([a, b], StitchSettings(target_height=1000))
        cut = p.bounds[1]
        assert 700 <= cut < 800  # the gutter above the split panel, not the slice edge at 1000


class TestWidths:
    def test_min_scales_the_wider_slice_down(self):
        out = normalise_widths([page(100, 400), page(200, 800)], "min")
        assert [o.shape[:2] for o in out] == [(100, 400), (100, 400)]

    def test_max_scales_the_narrower_slice_up(self):
        out = normalise_widths([page(100, 400), page(200, 800)], "max")
        assert [o.shape[:2] for o in out] == [(200, 800), (200, 800)]

    def test_none_pads_with_the_border_colour(self):
        narrow = np.full((50, 100, 3), (10, 20, 30), np.uint8)
        out = normalise_widths([narrow, page(50, 300)], "none")
        assert out[0].shape == (50, 300, 3)
        assert (out[0][:, :100] == (10, 20, 30)).all()
        assert (out[0][:, 100:200] == (10, 20, 30)).all()

    def test_grey_and_rgba_slices_become_rgb(self):
        grey = np.zeros((10, 20), np.uint8)
        rgba = np.zeros((10, 20, 4), np.uint8)
        assert [o.shape for o in normalise_widths([grey, rgba])] == [(10, 20, 3), (10, 20, 3)]


class TestWrite:
    def test_pages_are_written_in_order_with_padded_names(self, tmp_path):
        slices = [page(900, panels=[(50, 850)]) for _ in range(4)]
        p = plan(slices, StitchSettings(target_height=900))
        seen = []
        paths = write_pages(p, str(tmp_path), StitchSettings(), prefix="ch1_", progress=lambda i, n: seen.append((i, n)))
        assert [path.rsplit("/", 1)[-1] for path in paths] == ["ch1_001.png", "ch1_002.png", "ch1_003.png", "ch1_004.png"]
        assert seen[-1] == (4, 4)
        back = np.concatenate([np.asarray(Image.open(pth).convert("RGB")) for pth in paths], axis=0)
        assert np.array_equal(back, strip_rows_of(p))

    def test_jpeg_refuses_a_page_taller_than_it_can_store(self, tmp_path):
        p = plan([page(10)], StitchSettings(target_height=0))
        p.bounds = [0, 70000]  # stand-in for a very long unsplit strip
        with pytest.raises(ValueError, match="JPEG"):
            write_pages(p, str(tmp_path), StitchSettings(fmt="jpg"))

    def test_names_pad_to_the_page_count(self):
        assert page_names(1200, "", "jpg")[0] == "0001.jpg"


class TestPreview:
    def test_preview_pages_share_one_scale_and_keep_the_page_shapes(self):
        slices = [page(1500, panels=[(100, 1400)]), page(1500, panels=[(100, 1400)])]
        p = plan(slices, StitchSettings(target_height=1500))
        pages, scale = preview_pages(p, max_height=300)
        assert scale == pytest.approx(300 / max(p.page_heights))
        assert [pg.shape[0] for pg in pages] == [round(h * scale) for h in p.page_heights]

    def test_slice_edges_are_where_the_sources_met(self):
        p = plan([page(100), page(250), page(40)], StitchSettings(target_height=0))
        assert slice_edges(p) == [100, 350]
