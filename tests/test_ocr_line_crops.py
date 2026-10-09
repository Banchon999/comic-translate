"""What OCR is handed: line boxes inside a block, and the crop around each.

Every box here is taken from a real Korean webtoon page where local OCR came
back with junk appended ("안녕하세요, 공녀님!" read as "안녕하세요, 인당아재표,
[공녀님 [이너!] [이니!]", "공녀님." as "괴녀니 아니마"), and where an LLM OCR
returned one bubble's whole text for two blocks.
"""

import numpy as np

from modules.detection.heuristic_lines.clustering import (
    _absorb_glyph_fragments,
    _join_split_line_halves,
)
from modules.ocr.crop_utils import (
    BUBBLE_EDGE_INSET,
    expanded_ocr_line_bounds,
    llm_ocr_bounds,
)
from modules.utils.textblock import TextBlock

# "안녕하세요," / "공녀님!": two real lines, a comma and the cut-off bottoms of
# 공 and 님! found as three more "lines".
GREETING = [[280, 533, 467, 597], [428, 565, 475, 592], [317, 597, 440, 633],
            [398, 625, 439, 651], [322, 626, 355, 652]]
# "좋은 아침입니다," / "공녀님." with the second line cut into two halves.
MORNING = [[163, 542, 369, 589], [221, 591, 302, 613], [225, 610, 309, 629]]


class TestGlyphFragments:
    def test_pieces_of_glyphs_fold_back_into_their_lines(self):
        assert _absorb_glyph_fragments(GREETING) == [[280, 533, 475, 597], [317, 597, 440, 652]]

    def test_two_real_lines_stay_two(self):
        lines = [[280, 533, 467, 597], [317, 598, 440, 640]]
        assert _absorb_glyph_fragments(lines) == lines

    def test_a_short_second_line_is_not_a_fragment(self):
        # Narrow, but it does not overlap the line above it.
        lines = [[100, 0, 400, 40], [200, 42, 260, 80]]
        assert _absorb_glyph_fragments(lines) == lines


class TestSplitHalves:
    def test_a_line_cut_in_two_halves_is_joined(self):
        assert _join_split_line_halves(MORNING) == [[163, 542, 369, 589], [221, 591, 309, 629]]

    def test_three_full_height_lines_are_left_alone(self):
        lines = [[163, 542, 369, 589], [170, 592, 360, 636], [175, 640, 350, 684]]
        assert _join_split_line_halves(lines) == lines

    def test_lines_of_one_small_font_are_left_alone(self):
        # Every line equally short: none is under 60% of the tallest.
        lines = [[10, 0, 200, 20], [12, 21, 190, 40], [15, 42, 180, 60]]
        assert _join_split_line_halves(lines) == lines

    def test_two_lines_alone_are_never_joined(self):
        lines = [[221, 591, 302, 613], [225, 610, 309, 629]]
        assert _join_split_line_halves(lines) == lines


def _bubble_block(xyxy, bubble):
    return TextBlock(text_bbox=np.array(xyxy), bubble_bbox=np.array(bubble), text_class="text_bubble")


class TestLineCrop:
    img = np.zeros((1000, 800, 3), np.uint8)

    def test_a_bubble_line_crop_stays_clear_of_the_outline(self):
        """Widened to the whole bubble, every crop carried its curved edge,
        read as "["."""
        blk = _bubble_block([267, 520, 493, 670], [160, 369, 600, 821])
        x1, _y1, x2, _y2 = expanded_ocr_line_bounds(self.img, blk, [280, 533, 475, 597])
        inset = (600 - 160) * BUBBLE_EDGE_INSET
        assert 160 + inset <= x1 < 280 and 475 < x2 <= 600 - inset

    def test_it_is_never_narrower_than_the_line(self):
        # Text running right up to the bubble's edge keeps all of its width.
        blk = _bubble_block([100, 100, 300, 140], [95, 80, 305, 160])
        x1, _y1, x2, _y2 = expanded_ocr_line_bounds(self.img, blk, [100, 100, 300, 140])
        assert x1 <= 100 and x2 >= 300

    def test_text_outside_a_bubble_keeps_its_own_box(self):
        blk = TextBlock(text_bbox=np.array([100, 100, 300, 140]), text_class="text_free")
        x1, _y1, x2, _y2 = expanded_ocr_line_bounds(self.img, blk, [100, 100, 300, 140])
        assert 90 <= x1 <= 100 and 300 <= x2 <= 310


class TestLlmCrop:
    img = np.zeros((2000, 800, 3), np.uint8)

    def test_a_block_alone_in_its_bubble_sends_the_bubble(self):
        blk = _bubble_block([267, 520, 493, 670], [160, 369, 600, 821])
        assert llm_ocr_bounds(self.img, blk, [blk]) == (160, 369, 600, 821)

    def test_two_blocks_in_one_bubble_each_send_their_own_box(self):
        """Sent the bubble, both came back with the bubble's whole text."""
        bubble = [300, 1400, 700, 1720]
        exclaim = _bubble_block([355, 1430, 474, 1506], bubble)
        line = _bubble_block([400, 1542, 654, 1697], bubble)
        a = llm_ocr_bounds(self.img, exclaim, [exclaim, line])
        b = llm_ocr_bounds(self.img, line, [exclaim, line])
        assert a[3] < 1542 and b[1] > 1506   # neither crop holds the other's text
        assert a != (300, 1400, 700, 1720) and b != (300, 1400, 700, 1720)

    def test_a_block_in_another_bubble_does_not_count(self):
        one = _bubble_block([267, 520, 493, 670], [160, 369, 600, 821])
        other = _bubble_block([152, 1300, 382, 1400], [75, 1200, 454, 1500])
        assert llm_ocr_bounds(self.img, one, [one, other]) == (160, 369, 600, 821)

    def test_without_a_bubble_the_block_box_is_used(self):
        blk = TextBlock(text_bbox=np.array([100, 100, 300, 140]), text_class="text_free")
        x1, y1, x2, y2 = llm_ocr_bounds(self.img, blk, [blk])
        assert x1 <= 100 and y1 <= 100 and x2 >= 300 and y2 >= 140
