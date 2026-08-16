"""What makes two glossary terms the same term.

These exist because of a bug found in real use on a Korean series: the same
name kept being added twice. Identity was raw string equality, and Hangul has
two encodings — one code point per composed syllable (NFC) or a sequence of
jamo (NFD) — which render identically and compare unequal.
"""

import unicodedata

import pytest

from modules.utils.glossary import (
    GlossaryEntry,
    GlossaryManager,
    normalize_term,
    term_key,
)

NAME = "홍길동"
NAME_NFD = unicodedata.normalize("NFD", NAME)


@pytest.mark.parametrize(
    "label, variant",
    [
        ("identical", NAME),
        ("decomposed jamo", NAME_NFD),
        ("trailing space", NAME + " "),
        ("leading space", " " + NAME),
        ("zero-width space inside", NAME[:1] + "​" + NAME[1:]),
        ("non-breaking space", NAME + " "),
        ("ideographic space", NAME + "　"),
        ("newline", NAME + "\n"),
        ("left-to-right mark", "‎" + NAME),
    ],
)
def test_invisible_and_encoding_variants_are_one_term(label, variant):
    assert term_key(variant) == term_key(NAME), label


def test_different_names_stay_different():
    assert term_key("홍길동") != term_key("홍길순")
    assert term_key("Gate") != term_key("Gates")


def test_case_folds_for_latin_only():
    assert term_key("Gate") == term_key("gate")
    # Case folding is a no-op for scripts without case, so nothing merges that
    # should not.
    assert term_key("ダンジョン") == normalize_term("ダンジョン")


def test_normalize_keeps_the_visible_form():
    # The stored text is what the user sees, not a case-folded key.
    assert normalize_term("  Gate   Keeper ") == "Gate Keeper"
    assert normalize_term(NAME_NFD) == NAME


def test_from_dict_normalises_both_sides():
    entry = GlossaryEntry.from_dict({"source": NAME_NFD, "target": " ฮงกิลดง "})
    assert entry.source == NAME
    assert entry.target == "ฮงกิลดง"


def test_from_dict_accepts_novel_tran_pro_keys():
    entry = GlossaryEntry.from_dict({"korean": NAME, "thai": "ฮงกิลดง"})
    assert (entry.source, entry.target) == (NAME, "ฮงกิลดง")


def test_from_dict_rejects_half_an_entry():
    assert GlossaryEntry.from_dict({"source": NAME}) is None
    assert GlossaryEntry.from_dict({"target": "ฮงกิลดง"}) is None


class TestManager:
    def test_upsert_of_a_different_encoding_replaces_rather_than_adds(self):
        manager = GlossaryManager()
        manager.entries = []
        manager.upsert(GlossaryEntry(source=NAME, target="ฮงกิลดง"), save=False)
        manager.upsert(GlossaryEntry(source=NAME_NFD, target="ฮง กิลดง"), save=False)
        assert len(manager.entries) == 1
        assert manager.entries[0].target == "ฮง กิลดง"

    def test_find_and_remove_work_across_encodings(self):
        manager = GlossaryManager()
        manager.entries = [GlossaryEntry(source=NAME, target="ฮงกิลดง")]
        assert manager.find(NAME_NFD) is not None
        manager.save = lambda: None
        assert manager.remove([NAME_NFD]) == 1
        assert manager.entries == []

    def test_deduplicate_collapses_a_glossary_saved_before_the_fix(self):
        manager = GlossaryManager()
        manager.save = lambda: None
        manager.entries = [
            GlossaryEntry(source=NAME, target="ฮงกิลดง"),
            GlossaryEntry(source=NAME_NFD, target="ฮง กิลดง"),
            GlossaryEntry(source=NAME + " ", target="ฮงกิลดง"),
            GlossaryEntry(source="김철수", target="คิมชอลซู"),
        ]
        assert manager.deduplicate() == 2
        assert [e.source for e in manager.entries] == [NAME, "김철수"]

    def test_deduplicate_reports_nothing_to_do_on_a_clean_glossary(self):
        manager = GlossaryManager()
        manager.save = lambda: None
        manager.entries = [GlossaryEntry(source=NAME, target="ฮงกิลดง")]
        assert manager.deduplicate() == 0


class TestPromptMatching:
    """match_only decides which terms reach the translator's prompt.

    It compared raw strings too, so a composed entry never matched decomposed
    OCR text — the glossary looked correct and silently did not apply.
    """

    def _manager(self):
        manager = GlossaryManager()
        manager.save = lambda: None
        manager.enabled = True
        manager.match_only = True
        manager.entries = [GlossaryEntry(source=NAME, target="ฮงกิลดง", type="character")]
        return manager

    def test_matches_text_in_the_other_encoding(self):
        prompt = self._manager().build_prompt(f"안녕 {NAME_NFD} 씨")
        assert "ฮงกิลดง" in prompt

    def test_matches_text_in_the_same_encoding(self):
        prompt = self._manager().build_prompt(f"안녕 {NAME} 씨")
        assert "ฮงกิลดง" in prompt

    def test_leaves_out_terms_the_page_does_not_mention(self):
        assert self._manager().build_prompt("안녕하세요") == ""

    def test_disabled_glossary_contributes_nothing(self):
        manager = self._manager()
        manager.enabled = False
        assert manager.build_prompt(NAME) == ""
