"""The fast bubble fill: what replaces lettering in a speech bubble before any model runs.

Compared with PanelCleaner on seven real webtoon pages, the old fill painted
every bubble's text in one colour. On a gradient bubble that left a patch
shaped like the text's mask; on lettering with a glow it took the glow's
colour; on a bubble printed over dots it left a block. These pin the fix:
a smooth fill from the surroundings, grown past a halo, and texture left to
the inpainter.
"""

import imkit as imk
import numpy as np

from core.paint import plane_residual, smooth_fill
from modules.utils.textblock import TextBlock
from pipeline.inpainting import FAST_FILL_MAX_TEXTURE, InpaintingHandler


def _gradient(h, w, top=200, bottom=250):
    column = np.linspace(top, bottom, h)[:, None, None]
    return np.broadcast_to(column, (h, w, 3)).astype(np.uint8).copy()


def _ring(shape, y1, x1, y2, x2, width=3):
    ring = np.zeros(shape, bool)
    ring[y1 - width:y2 + width, x1 - width:x2 + width] = True
    ring[y1:y2, x1:x2] = False
    return ring


class TestCore:
    def test_smooth_fill_reproduces_a_gradient(self):
        image = _gradient(60, 60)
        damaged = image.copy()
        region = np.zeros((60, 60), bool)
        region[20:40, 15:45] = True
        damaged[region] = 0
        filled = smooth_fill(damaged, region)
        assert np.abs(filled.astype(int) - image.astype(int)).max() <= 2
        assert np.array_equal(filled[~region], damaged[~region])

    def test_plane_residual_tells_gradient_from_texture(self):
        ring = _ring((60, 60), 20, 15, 40, 45)
        assert plane_residual(_gradient(60, 60), ring) < 1.0
        dots = _gradient(60, 60)
        dots[::4, ::4] = 40
        assert plane_residual(dots, ring) > FAST_FILL_MAX_TEXTURE

    def test_plane_residual_needs_enough_pixels_to_judge(self):
        ring = np.zeros((10, 10), bool)
        ring[0, :5] = True
        assert plane_residual(_gradient(10, 10), ring) == float("inf")


class TestGrowPastHalo:
    def test_growth_clears_a_glow_around_the_text(self):
        crop = np.full((80, 120, 3), 230, np.uint8)
        text = np.zeros((80, 120), bool)
        text[35:45, 30:90] = True
        glow = np.zeros_like(text)
        glow[31:49, 26:94] = True
        crop[glow] = 120          # a dark halo four pixels wide
        crop[text] = 20
        region, texture = InpaintingHandler._grow_past_halo(crop, text, None, np.array([230, 230, 230]))
        assert region[glow].all()                 # the halo is filled too
        assert texture < 1.0                       # judged against the clean background
        assert not region[10, 10] and region.sum() < 3 * glow.sum()

    def test_no_halo_means_little_growth(self):
        crop = np.full((80, 120, 3), 230, np.uint8)
        text = np.zeros((80, 120), bool)
        text[35:45, 30:90] = True
        crop[text] = 20
        region, _texture = InpaintingHandler._grow_past_halo(crop, text, None, np.array([230, 230, 230]))
        assert region.sum() <= (10 + 2) * (60 + 2)

    def test_a_solid_halo_alone_looks_like_background(self):
        # Why the background colour is needed: without it the flat glow wins.
        crop = np.full((80, 120, 3), 230, np.uint8)
        text = np.zeros((80, 120), bool)
        text[35:45, 30:90] = True
        crop[31:49, 26:94] = 120
        crop[text] = 20
        region, _texture = InpaintingHandler._grow_past_halo(crop, text, None)
        assert not region[31, 26]

    def test_component_rects_bound_each_piece(self):
        region = np.zeros((40, 40), bool)
        region[5, 5:15] = True
        region[5:15, 5] = True
        region[30, 30] = True
        rects = InpaintingHandler._component_rects(region)
        assert rects[5:15, 5:15].all() and rects[30, 30]
        assert rects.sum() == 100 + 1


def _bubble_page(background):
    """A 200x300 page: an ellipse bubble with a black outline and text inside."""
    page = background.copy()
    yy, xx = np.mgrid[:200, :300]
    inside = ((xx - 150) / 120) ** 2 + ((yy - 100) / 80) ** 2
    page[(inside > 1.0) & (inside < 1.12)] = 0
    page[inside >= 1.12] = 255
    text = np.zeros((200, 300), bool)
    for x in range(100, 200, 14):           # a row of letter-sized strokes
        text[85:115, x:x + 6] = True
    page[text] = 15
    block = TextBlock(text_bbox=np.array([95, 80, 205, 120]),
                      bubble_bbox=np.array([30, 20, 270, 180]), text_class="text_bubble")
    # The mask covers the lettering and its anti-aliased edge, as segmentation does.
    mask = imk.dilate(text.astype(np.uint8) * 255, np.ones((3, 3), np.uint8), iterations=1)
    return page, mask, block


class TestBubbleCleanup:
    def test_a_gradient_bubble_is_filled_with_the_gradient(self):
        background = _gradient(200, 300, 190, 250)
        page, mask, block = _bubble_page(background)
        handler = InpaintingHandler(main_page=None)
        cleaned, residual, count = handler._apply_fast_bubble_cleanup(page, mask, [block])
        assert count == 1 and not residual.any()
        where = mask > 0
        error = np.abs(cleaned[where].astype(int) - background[where].astype(int))
        # One flat colour would be ~10 levels off at the top and bottom of the text.
        assert error.max() <= 4
        assert np.array_equal(cleaned[180:, :], page[180:, :])   # nothing outside touched

    def test_a_bubble_over_dots_is_left_to_the_inpainter(self):
        background = np.full((200, 300, 3), 235, np.uint8)
        for dy in range(2):                        # screentone: 2x2 dots every 4 px
            for dx in range(2):
                background[dy::4, dx::4] = 90
        page, mask, block = _bubble_page(background)
        handler = InpaintingHandler(main_page=None)
        success, reason = handler._fast_fill_block(page.copy(), mask.copy(), block,
                                                   (0, 0, 300, 200), mask.copy())
        assert not success
        assert reason.startswith("textured:") or reason.startswith("selected-")
