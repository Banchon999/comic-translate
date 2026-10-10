"""Line breaks a translator puts in its answer never reach the renderer.

Reported by people using LLM OCR: the model answered "มีบางคนที่คิดอะไร\\n
แปลกแยกไม่เหมือน\\nคนปกติอยู่น่ะ", the renderer took every break as a forced new
line and wrapped each piece again to the bubble, and the page came out in
fragments that had to be fixed by deleting the breaks by hand. A bubble's line
breaks belong to its layout; the renderer fits the text to the bubble itself.
"""

import json

import numpy as np

from modules.rendering.render import _segment_no_space_paragraph, _wrap_no_space_text_greedily
from modules.translation.processor import Translator
from modules.utils.textblock import TextBlock
from modules.utils.translator_utils import get_raw_text, join_line_breaks


class TestJoin:
    def test_thai_lines_join_as_if_the_breaks_were_deleted(self):
        assert join_line_breaks("มีบางคนที่คิดอะไร\nแปลกแยกไม่เหมือน\nคนปกติอยู่น่ะ") == \
            "มีบางคนที่คิดอะไรแปลกแยกไม่เหมือนคนปกติอยู่น่ะ"

    def test_a_thai_sentence_end_keeps_its_space(self):
        assert join_line_breaks("ไม่ได้ค่ะ!\nแล้วไง") == "ไม่ได้ค่ะ! แล้วไง"

    def test_latin_lines_join_with_a_space(self):
        assert join_line_breaks("I CANNOT\nDO THIS") == "I CANNOT DO THIS"
        assert join_line_breaks("WAIT  \r\n  WHAT?") == "WAIT WHAT?"

    def test_cjk_joins_with_nothing(self):
        assert join_line_breaks("一緒に\n行こう") == "一緒に行こう"

    def test_blank_lines_and_edges_vanish(self):
        assert join_line_breaks("\nสวัสดี\n\n\nครับ\n") == "สวัสดีครับ"

    def test_text_without_breaks_is_untouched(self):
        for text in ("ok", "มี  สอง ช่อง", "", None):
            assert join_line_breaks(text) == text


def _block(text, translation=""):
    blk = TextBlock(text_bbox=np.array([0, 0, 1, 1]), text=text)
    blk.translation = translation
    return blk


def _translator(engine, is_llm):
    translator = Translator.__new__(Translator)
    translator.engine = engine
    translator.is_llm_engine = is_llm
    return translator


class _Engine:
    """Answers with the line breaks a model mirrors from line-by-line OCR."""

    def translate(self, blk_list, *args):
        for blk in blk_list:
            blk.translation = "มีบางคนที่คิดอะไร\nแปลกแยกไม่เหมือน"
        return blk_list


class TestEveryEngine:
    def test_an_llm_answer_reaches_the_block_as_running_text(self):
        blk = _block("A FEW OF THEM\nTHINK IN WAYS")
        _translator(_Engine(), True).translate([blk], None, "")
        assert blk.translation == "มีบางคนที่คิดอะไรแปลกแยกไม่เหมือน"

    def test_a_traditional_translator_answer_too(self):
        blk = _block("A FEW OF THEM\nTHINK IN WAYS")
        _translator(_Engine(), False).translate([blk])
        assert "\n" not in blk.translation

    def test_the_model_is_sent_the_source_without_line_breaks(self):
        # Handed a bubble line by line, a model answers line by line.
        sent = json.loads(get_raw_text([_block("A FEW OF THEM\nTHINK IN WAYS")]))
        assert sent == {"block_0": "A FEW OF THEM THINK IN WAYS"}


class TestPunctuationStaysWithItsWord:
    def test_closing_and_opening_marks_join_their_word(self):
        assert _segment_no_space_paragraph("แม้กระทั่ง...") == ["แม้กระทั่ง..."]
        tokens = _segment_no_space_paragraph("เธอพูดว่า (ไม่) นะ!")
        assert "(ไม่)" in tokens and "นะ!" in tokens

    def test_no_line_starts_with_an_ellipsis(self):
        tokens = [_segment_no_space_paragraph("แต่ถึงอย่างนั้นกลับบอกฉันแม้กระทั่ง...")]
        width_of_text = len("แต่ถึงอย่างนั้นกลับบอกฉันแม้กระทั่ง")
        wrapped = _wrap_no_space_text_greedily(tokens, len, width_of_text)
        assert not any(line.startswith(".") for line in wrapped.split("\n"))
