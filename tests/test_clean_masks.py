"""Which pixels get cleaned: two misses found against PanelCleaner on real pages.

1. A caption box or text on a flat panel detected as a *bubble* has no
   outline, so the flood that finds a bubble's inside leaks. The clip then
   fell back to an ellipse inscribed in the box, which cut off the text in
   its corners: credits and "READ AT …" lines came out half cleaned.
2. A site watermark — white lettering with a black outline over artwork —
   gets no text from the segmentation network, and the block was left alone.
"""

import numpy as np

from modules.utils.image_utils import build_bubble_clip_mask, lettering_rows


class TestBorderlessBubbleClip:
    def _caption(self):
        page = np.full((120, 300, 3), 20, np.uint8)       # one flat dark panel
        return page, (40, 20, 260, 100)

    def test_a_leaking_flood_clips_to_the_box_not_an_ellipse(self):
        page, box = self._caption()
        clip = build_bubble_clip_mask((120, 300), (0, 0, 300, 120), box, inset=7,
                                      image=page, seed_bbox=(60, 40, 240, 80))
        # The box's corners, where text runs, are inside the clip …
        assert clip[28, 48] and clip[91, 252]
        # … and nothing outside the inset box is.
        assert not clip[20, 40] and not clip[110, 150] and clip.sum() == (206 * 66)

    def test_an_outlined_bubble_still_follows_its_outline(self):
        page = np.full((120, 300, 3), 255, np.uint8)
        yy, xx = np.mgrid[:120, :300]
        inside = ((xx - 150) / 110) ** 2 + ((yy - 60) / 40) ** 2
        page[(inside > 1.0) & (inside < 1.15)] = 0
        clip = build_bubble_clip_mask((120, 300), (0, 0, 300, 120), (40, 20, 260, 100),
                                      inset=7, image=page, seed_bbox=(100, 50, 200, 70))
        assert clip[60, 150]
        assert not clip[22, 42]          # a corner of the box, outside the outline

    def test_without_an_image_the_ellipse_is_kept(self):
        clip = build_bubble_clip_mask((120, 300), (0, 0, 300, 120), (40, 20, 260, 100), inset=7)
        assert clip[60, 150] and not clip[28, 48]


def _glyphs(mask, x, top, height, width=10):
    mask[top:top + height, x:x + width] = 255


class TestLetteringRows:
    def test_a_row_of_letters_is_kept_and_art_dropped(self):
        mask = np.zeros((80, 200), np.uint8)
        for x in range(30, 170, 16):
            _glyphs(mask, x, 40, 18)
        mask[2:38, 40:70] = 255                  # a tall shape above the row: artwork
        rows = lettering_rows(mask)
        assert rows is not None
        assert rows[49, 35] and rows[49, 160]    # first and last letter
        assert not rows[10, 55]                  # the artwork is gone

    def test_the_mask_reaches_past_each_letter_to_cover_an_outline(self):
        mask = np.zeros((80, 200), np.uint8)
        for x in range(30, 170, 16):
            _glyphs(mask, x, 40, 18)
        rows = lettering_rows(mask)
        assert rows[37, 35] and rows[61, 35]     # a few px above and below the glyph
        assert rows[49, 42]                      # the gap between two letters is joined

    def test_hatching_is_not_lettering(self):
        mask = np.zeros((80, 200), np.uint8)
        for x in range(30, 170, 8):
            _glyphs(mask, x, 40, 18, width=2)
        assert lettering_rows(mask) is None

    def test_two_pieces_are_not_a_row(self):
        mask = np.zeros((80, 200), np.uint8)
        _glyphs(mask, 30, 40, 18)
        _glyphs(mask, 50, 40, 18)
        assert lettering_rows(mask) is None

    def test_scattered_pieces_are_not_a_row(self):
        mask = np.zeros((200, 200), np.uint8)
        for i, x in enumerate(range(20, 180, 40)):
            _glyphs(mask, x, 10 + 45 * i, 18)
        assert lettering_rows(mask) is None

    def test_nothing_in_nothing_out(self):
        assert lettering_rows(None) is None
        assert lettering_rows(np.zeros((10, 10), np.uint8)) is None
