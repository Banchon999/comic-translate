"""Which glossary terms reach the translator, and whether it used them.

The owner's report was "the translation doesn't follow my glossary". Measured
on real OCR shapes, the glossary was mostly never *sent*: match-only compared
the page text to each term as a plain substring, and OCR routinely breaks that
— a name wrapped over two lines comes back with a space in it, one syllable is
misread, a title loses its space. Each of those left the term out of the
prompt, so no amount of prompt wording could have helped. The first five tests
below failed on the old matcher.
"""

import pytest

from modules.utils.glossary import (
    BLOCK_BREAK,
    GlossaryEntry,
    GlossaryManager,
    collect_source_text,
    compact_key,
)

KIM = GlossaryEntry(source="김철수", target="คิมชอลซู", type="character")
CHULSOO = GlossaryEntry(source="철수", target="ชอลซู", type="character")
SHADOW_LORD = GlossaryEntry(source="그림자 군주", target="ราชาเงา")
NARUTO = GlossaryEntry(source="ナルト", target="นารูโตะ", type="character")


@pytest.fixture
def manager(tmp_path):
    m = GlossaryManager(str(tmp_path))
    m.save = lambda: None
    m.enabled = True
    m.match_only = True
    return m


def sent(manager, text):
    """The terms the prompt for this text would carry, by source."""
    prompt = manager.build_prompt(text)
    return {e.source for e in manager.entries if f"- {e.source} =>" in prompt}


class TestOcrShapesStillReachThePrompt:
    def test_a_name_wrapped_over_two_lines(self, manager):
        # PaddleOCR joins a block's lines with a space for Korean.
        manager.entries = [KIM]
        assert sent(manager, "김철 수가 왔다") == {"김철수"}

    def test_one_misread_syllable(self, manager):
        manager.entries = [KIM]
        assert sent(manager, "김철슈 왔어?") == {"김철수"}

    def test_a_title_written_without_its_space(self, manager):
        manager.entries = [SHADOW_LORD]
        assert sent(manager, "그림자군주님!") == {"그림자 군주"}

    def test_japanese_with_a_stray_space(self, manager):
        manager.entries = [NARUTO]
        assert sent(manager, "ナル トだ！") == {"ナルト"}

    def test_only_the_longer_of_two_overlapping_terms_is_sent(self, manager):
        # "철수" inside "김철수": sending both hands the model two instructions
        # for one name, and when their translations differ it may pick either.
        manager.entries = [CHULSOO, KIM]
        assert sent(manager, "김철수 왔어") == {"김철수"}

    def test_the_shorter_term_still_applies_where_it_stands_alone(self, manager):
        manager.entries = [CHULSOO, KIM]
        assert sent(manager, "김철수 왔어. 철수야!") == {"김철수", "철수"}

    def test_the_prompt_says_where_a_fuzzy_match_came_from(self, manager):
        manager.entries = [KIM]
        prompt = manager.build_prompt("김철슈 왔어?")
        assert "«김철슈»" in prompt


class TestNoFalseMatches:
    def test_latin_terms_need_whole_words(self, manager):
        manager.entries = [GlossaryEntry(source="ice", target="น้ำแข็ง")]
        assert sent(manager, "Did you notice?") == set()
        assert sent(manager, "Ice, please.") == {"ice"}

    def test_a_latin_term_next_to_hangul_is_still_a_word(self, manager):
        manager.entries = [GlossaryEntry(source="HP", target="HP")]
        assert sent(manager, "HP를 회복했다") == {"HP"}

    def test_two_letter_terms_never_match_fuzzily(self, manager):
        manager.entries = [CHULSOO]
        assert sent(manager, "철이 왔다") == set()

    def test_a_two_letter_word_is_not_a_three_letter_term_missing_one(self, manager):
        manager.entries = [KIM]
        assert sent(manager, "김철 부장님") == set()

    def test_a_word_plus_a_particle_is_not_a_misread_term(self, manager):
        # 그림 + 을 is one substitution away from 그림자 — and a different word.
        manager.entries = [GlossaryEntry(source="그림자", target="เงา")]
        assert sent(manager, "그림을 그려") == set()

    def test_short_latin_terms_match_exactly_only(self, manager):
        manager.entries = [GlossaryEntry(source="Kai", target="ไค")]
        assert sent(manager, "The cat sat") == set()
        assert sent(manager, "Kat said") == set()

    def test_long_latin_terms_tolerate_one_letter(self, manager):
        manager.entries = [GlossaryEntry(source="Gold Dragon", target="มังกรทอง")]
        assert sent(manager, "the gold dragan roars") == {"Gold Dragon"}
        assert sent(manager, "Gold\ndragon") == {"Gold Dragon"}

    def test_a_term_is_not_spliced_from_two_speech_bubbles(self, manager):
        class Blk:
            def __init__(self, text):
                self.text = text

        manager.entries = [KIM]
        joined = collect_source_text([Blk("안녕 김철"), Blk("수고했어")])
        assert BLOCK_BREAK in joined
        assert sent(manager, joined) == set()

    def test_fuzzy_never_claims_text_an_exact_term_owns(self, manager):
        # 박철수 is one edit from 김철수, but the glossary knows 철수 exactly.
        manager.entries = [KIM, CHULSOO]
        assert sent(manager, "박철수 씨") == {"철수"}

    def test_punctuation_is_not_a_misread_letter(self, manager):
        manager.entries = [KIM]
        assert sent(manager, "김철, 수고했어") == set()


def test_match_report_explains_suppressed_terms(manager):
    manager.entries = [CHULSOO, KIM]
    report = {m.entry.source: m for m in manager.match_report("김철수")}
    assert report["김철수"].suppressed_by is None
    assert report["철수"].suppressed_by is KIM


def test_seen_as_is_the_text_as_ocr_wrote_it(manager):
    manager.entries = [KIM]
    (match,) = manager.find_matches("그래, 김철\n수!")
    assert match.seen_as == "김철 수"
    assert not match.fuzzy


def test_compact_key_drops_every_space():
    assert compact_key(" 그림자  군주 ") == "그림자군주"


def test_match_only_off_still_sends_everything(manager):
    manager.entries = [KIM, NARUTO]
    manager.match_only = False
    assert sent(manager, "nothing relevant") == {"김철수", "ナルト"}


def test_the_prompt_puts_the_glossary_above_naturalness(manager):
    manager.entries = [KIM]
    prompt = manager.build_prompt("김철수")
    assert "mandatory" in prompt.lower()
    assert "natural" in prompt.lower()


class Blk:
    def __init__(self, text, translation):
        self.text = text
        self.translation = translation


class TestCheckTranslation:
    def test_flags_a_term_the_translation_left_out(self, manager):
        manager.entries = [KIM]
        (issue,) = manager.check_translation([Blk("김철수가 왔다", "ชอลซูมาแล้ว")])
        assert (issue.block_index, issue.source_term, issue.expected) == (0, "김철수", "คิมชอลซู")

    def test_thai_spacing_does_not_matter(self, manager):
        manager.entries = [KIM]
        assert manager.check_translation([Blk("김철수가 왔다", "คิม ชอล ซู มาแล้ว")]) == []

    def test_case_and_hyphens_do_not_matter(self, manager):
        manager.entries = [GlossaryEntry(source="김철수", target="Kim Cheol-su")]
        assert manager.check_translation([Blk("김철수", "KIM CHEOLSU is here")]) == []

    def test_blocks_without_the_term_or_translation_are_ignored(self, manager):
        manager.entries = [KIM]
        blocks = [Blk("안녕하세요", "สวัสดี"), Blk("김철수", ""), Blk("", "x")]
        assert manager.check_translation(blocks) == []

    def test_reports_the_block_it_happened_in(self, manager):
        manager.entries = [KIM, NARUTO]
        blocks = [Blk("김철수", "คิมชอลซู"), Blk("ナルト!", "นารุโตะ!")]
        (issue,) = manager.check_translation(blocks)
        assert issue.block_index == 1 and issue.source_term == "ナルト"

    def test_a_wrapped_name_is_checked_too(self, manager):
        manager.entries = [KIM]
        (issue,) = manager.check_translation([Blk("김철 수가 왔다", "ชอลซูมาแล้ว")])
        assert issue.seen_as == "김철 수"

    def test_fuzzy_matches_are_not_warned_about(self, manager):
        manager.entries = [KIM]
        assert manager.check_translation([Blk("김철슈 왔어", "มีคนมา")]) == []

    def test_a_disabled_glossary_warns_about_nothing(self, manager):
        manager.entries = [KIM]
        manager.enabled = False
        assert manager.check_translation([Blk("김철수", "x")]) == []

    def test_a_suppressed_shorter_term_is_not_checked(self, manager):
        # The translation used the longer term's target, which does not contain
        # the shorter term's target — that is correct, not a miss.
        manager.entries = [KIM, GlossaryEntry(source="철수", target="เชลซี")]
        assert manager.check_translation([Blk("김철수", "คิมชอลซู")]) == []


class TestFindOverlaps:
    def test_consistent_pair(self, manager):
        manager.entries = [KIM, CHULSOO]
        (o,) = manager.find_overlaps()
        assert (o.short.source, o.long.source, o.consistent) == ("철수", "김철수", True)

    def test_inconsistent_pair(self, manager):
        manager.entries = [KIM, GlossaryEntry(source="철수", target="เชลซี")]
        (o,) = manager.find_overlaps()
        assert not o.consistent

    def test_spacing_does_not_hide_an_overlap(self, manager):
        manager.entries = [SHADOW_LORD, GlossaryEntry(source="그림자", target="เงา")]
        (o,) = manager.find_overlaps()
        assert o.long is SHADOW_LORD and o.consistent

    def test_latin_overlaps_need_whole_words(self, manager):
        manager.entries = [
            GlossaryEntry(source="ice", target="น้ำแข็ง"),
            GlossaryEntry(source="Notice Board", target="กระดานประกาศ"),
            GlossaryEntry(source="Ice Queen", target="ราชินีน้ำแข็ง"),
        ]
        pairs = {(o.short.source, o.long.source) for o in manager.find_overlaps()}
        assert pairs == {("ice", "Ice Queen")}

    def test_unrelated_terms_do_not_overlap(self, manager):
        manager.entries = [KIM, NARUTO]
        assert manager.find_overlaps() == []


def test_a_misread_long_term_wins_over_a_shorter_exact_term_inside_it(manager):
    # Found in the Test Matching screenshot: "그림자" matched exactly inside
    # "그림자군쥬" and blocked the fuzzy match of the title it belongs to.
    shadow = GlossaryEntry(source="그림자", target="เงา")
    manager.entries = [SHADOW_LORD, shadow]
    report = {m.entry.source: m for m in manager.match_report("그림자군쥬님!")}
    assert report["그림자 군주"].fuzzy and report["그림자 군주"].suppressed_by is None
    assert report["그림자"].suppressed_by is SHADOW_LORD
    assert sent(manager, "그림자군쥬님!") == {"그림자 군주"}


def test_a_short_exact_term_elsewhere_survives_the_takeover(manager):
    shadow = GlossaryEntry(source="그림자", target="เงา")
    manager.entries = [SHADOW_LORD, shadow]
    assert sent(manager, "그림자군쥬님! 그림자가 없어") == {"그림자 군주", "그림자"}
