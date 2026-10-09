"""Gender in Thai translations: ครับ/ค่ะ, pronouns and forms of address.

Ported from the NovelTrans gender-particle system's own test list, minus what
needs a dialogue tag to know who spoke — a speech bubble has none.
"""

import types

import numpy as np
import pytest

from modules.utils import thai_speech as ts
from modules.utils.glossary import GlossaryEntry, GlossaryManager, issue_rows
from modules.utils.prompts import PromptManager
from modules.utils.textblock import TextBlock


def _block(text, translation):
    blk = TextBlock(text_bbox=np.array([0, 0, 1, 1]), text=text)
    blk.translation = translation
    return blk


@pytest.fixture
def manager(tmp_path):
    m = GlossaryManager(str(tmp_path))
    m.save = lambda: None
    return m


class TestSpeechCheck:
    @pytest.mark.parametrize("text, reason", [
        ("ได้ครับ ขอบคุณค่ะ", ts.MIXED),
        ("ผมจะไปค่ะ", ts.MALE_SELF_FEMALE_PARTICLE),
        ("ดิฉันเข้าใจแล้วครับ", ts.FEMALE_SELF_MALE_PARTICLE),
        ("ไม่เป็นไรนะครับ\nผมเข้าใจค่ะ", ts.MIXED),
    ])
    def test_contradictions_inside_a_bubble_are_flagged(self, text, reason):
        assert ts.speech_problem(text) == reason

    @pytest.mark.parametrize("text", [
        "ผมได้คะแนนเต็ม",                  # คะแนน (score) is not the particle คะ
        "กระหม่อมเข้าใจแล้วพ่ะย่ะค่ะ",          # a man to royalty ends in ค่ะ
        "ผมยาวสวยจังค่ะ",                   # ผม here is hair, not "I"
        "ไปกันเถอะครับ",                    # who says it is unknown: not guessed
        "ฉันไปก่อนนะคะ",
        "ขอรับเงินก่อนค่ะ",                  # ขอรับ as "to receive" is not a particle
        "ครับผม! เข้าใจแล้วครับ",
        "",
    ])
    def test_consistent_or_unknown_lines_are_left_alone(self, text):
        assert ts.speech_problem(text) is None

    def test_blocks_are_reported_by_index(self):
        issues = ts.check_speech([_block("a", "ได้ค่ะ"), _block("b", "ผมไปค่ะ")])
        assert [(i.block_index, i.reason) for i in issues] == [(1, ts.MALE_SELF_FEMALE_PARTICLE)]


class TestAddressGender:
    @pytest.mark.parametrize("source, target, expected", [
        ("도련님", "คุณหนู", "คุณชาย"),
        ("아가씨", "คุณชาย", "คุณหนู"),
        ("young master", "คุณหนู", "คุณชาย"),
        ("Young Lady", "นายน้อย", "คุณหนู"),
        ("お嬢様", "คุณชาย", "คุณหนู"),
        ("お坊ちゃま", "คุณหนู", "คุณชาย"),
        ("公子", "คุณหญิง", "คุณชาย"),
        ("도련님", "นายน้อย", "นายน้อย"),   # right gender, the user's wording stays
        ("리나", "คุณหนู", "คุณหนู"),         # not a form of address
    ])
    def test_a_wrong_gender_form_of_address_is_corrected(self, source, target, expected):
        assert ts.fix_address_gender(source, target) == expected


class TestGlossaryPrompt:
    def test_into_thai_a_gender_names_its_pronouns_and_particles(self, manager):
        manager.entries = [GlossaryEntry("리나", "ลีน่า", "character", "female", "นางเอก")]
        line = manager.build_prompt("리나").splitlines()[1]
        assert line.startswith("- 리나 => ลีน่า (character; gender: female/หญิง;")
        assert "3rd→เธอ/นาง" in line and "particle→ค่ะ/คะ" in line and line.endswith("นางเอก)")

    def test_other_targets_keep_the_plain_gender(self, manager):
        manager.entries = [GlossaryEntry("리나", "Lina", "character", "female")]
        assert manager.build_prompt("리나").splitlines()[1] == "- 리나 => Lina (character; gender: female)"

    def test_a_wrong_address_form_is_never_sent_or_expected(self, manager):
        manager.entries = [GlossaryEntry("도련님", "คุณหนู", "term")]
        assert "- 도련님 => คุณชาย" in manager.build_prompt("도련님")
        (issue,) = manager.check_translation([_block("도련님", "คุณหนู")])
        assert issue.expected == "คุณชาย"
        assert manager.check_translation([_block("도련님", "คุณชายคะ")]) == []


class TestExtractionMerge:
    def test_gender_is_filled_in_without_touching_the_translation(self, manager):
        manager.entries = [GlossaryEntry("리나", "ลีน่า", "character")]
        added, filled = manager.merge_extracted([
            GlossaryEntry("리나", "รีนา", "character", "female"),
            GlossaryEntry("도련님", "คุณหนู", "term"),
        ])
        assert (added, filled) == (1, 1)
        assert (manager.entries[0].target, manager.entries[0].gender) == ("ลีน่า", "female")
        assert manager.entries[1].target == "คุณชาย"

    def test_neutral_fills_nothing_and_a_set_gender_is_kept(self, manager):
        manager.entries = [
            GlossaryEntry("리나", "ลีน่า", "character"),
            GlossaryEntry("강", "คัง", "character", "male"),
        ]
        assert manager.merge_extracted([
            GlossaryEntry("리나", "ลีน่า", "character", "neutral"),
            GlossaryEntry("강", "คัง", "character", "female"),
        ]) == (0, 0)
        assert [e.gender for e in manager.entries] == ["", "male"]

    def test_only_characters_carry_a_gender(self, manager):
        manager.merge_extracted([GlossaryEntry("왕궁", "พระราชวัง", "place", "female")])
        assert manager.entries[0].gender == ""

    def test_characters_without_a_gender_are_asked_about_again(self, manager):
        manager.entries = [
            GlossaryEntry("A", "a", "character"),
            GlossaryEntry("B", "b", "term"),
            GlossaryEntry("C", "c", "character", "neutral"),
        ]
        assert manager.extraction_skip_sources() == {"B", "C"}

    def test_the_extraction_prompt_asks_for_gender_in_the_source_language(self):
        korean, chinese = ts.extraction_gender_rules("Korean"), ts.extraction_gender_rules("Chinese")
        assert "그녀" in korean and "她" not in korean
        assert "她" in chinese and "그녀" not in chinese
        assert '"neutral"' in korean


class TestSystemPrompt:
    def test_thai_targets_get_the_particle_rules_once(self, tmp_path):
        prompt = PromptManager(str(tmp_path / "p.json")).build_system_prompt("Korean", "Thai")
        assert prompt.count(ts.SPEECH_PARTICLE_HEADER) == 1
        assert "도련님" in prompt

    def test_examples_follow_the_source_language(self, tmp_path):
        prompt = PromptManager(str(tmp_path / "p.json")).build_system_prompt("Simplified Chinese", "Thai")
        assert "公子" in prompt and "도련님" not in prompt

    def test_a_custom_preset_gets_them_too(self, tmp_path):
        manager = PromptManager(str(tmp_path / "p.json"))
        manager.save_custom("Mine", "Translate {source_lang} to {target_lang}.")
        assert ts.SPEECH_PARTICLE_HEADER in manager.build_system_prompt("Japanese", "Thai")

    def test_other_targets_do_not(self, tmp_path):
        prompt = PromptManager(str(tmp_path / "p.json")).build_system_prompt("Korean", "English")
        assert ts.SPEECH_PARTICLE_HEADER not in prompt and "ครับ" not in prompt


def _translator(is_llm, target, manager):
    from modules.translation.processor import Translator

    translator = Translator.__new__(Translator)
    translator.is_llm_engine = is_llm
    translator.target_lang_en = target
    translator.settings = types.SimpleNamespace(
        ui=types.SimpleNamespace(glossary_page=types.SimpleNamespace(manager=manager))
    )
    return translator


class TestAfterTranslation:
    def test_thai_speech_is_checked_whatever_engine_wrote_it(self, manager):
        for is_llm in (True, False):
            (issue,) = _translator(is_llm, "Thai", manager).check_glossary([_block("x", "ผมไปค่ะ")])
            assert isinstance(issue, ts.SpeechIssue)

    def test_other_targets_are_not(self, manager):
        assert _translator(True, "English", manager).check_glossary([_block("x", "ผมไปค่ะ")]) == []

    def test_the_batch_report_gets_a_speech_row(self):
        rows = issue_rows([ts.SpeechIssue(0, "ได้ครับ ขอบคุณค่ะ", ts.MIXED)])
        assert rows == [("ได้ครับ ขอบคุณค่ะ", ts.MIXED, "speech", "")]

    def test_the_report_and_the_toast_describe_it(self, qapp):
        from PySide6 import QtWidgets

        from app.controllers.batch_report import BatchReportController
        from app.ui.messages import Messages

        issue = ts.SpeechIssue(0, "ได้ครับ ขอบคุณค่ะ", ts.MIXED)
        text = Messages.glossary_issues_text([issue])
        assert "«ได้ครับ ขอบคุณค่ะ»" in text and "glossary term" not in text

        main = types.SimpleNamespace(
            image_states=types.SimpleNamespace(is_skipped=lambda path: False),
            batch_report_button=types.SimpleNamespace(setEnabled=lambda *_: None),
            mark_project_dirty=lambda: None,
            image_files=["/a/1.png"],
            tr=lambda text: text,
        )
        ctrl = BatchReportController(main)
        ctrl.start_batch_report(["/a/1.png"])
        ctrl.register_glossary_issues("/a/1.png", issue_rows([issue]))
        report = ctrl.finalize_batch_report(was_cancelled=False)
        assert report["glossary_entries"][0]["issues"] == [["ได้ครับ ขอบคุณค่ะ", ts.MIXED, "speech", ""]]
        widget = ctrl._build_batch_report_widget(report)
        cells = [t.item(0, 1).text() for t in widget.findChildren(QtWidgets.QTableWidget) if t.rowCount()]
        assert any(Messages.speech_reason_text(ts.MIXED) in cell for cell in cells)
        widget.deleteLater()
