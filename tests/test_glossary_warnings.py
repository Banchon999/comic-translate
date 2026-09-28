"""Where a translation that ignored the glossary gets reported, and how.

The owner chose warn-only: nothing is re-translated, but a miss must be seen —
a toast after a manual Translate, a section in the Batch Report after a batch.
Plus the prompt order the check exists to back up, and the Glossary page's
overlap marks and match tester.
"""

import types

import numpy as np
import pytest
from PySide6 import QtWidgets

from app.controllers.batch_report import BatchReportController
from app.ui.messages import Messages
from modules.translation.llm.base import build_user_prompt
from modules.translation.processor import Translator
from modules.utils.glossary import GlossaryEntry, GlossaryIssue
from modules.utils.textblock import TextBlock


class TestPromptOrder:
    def test_the_glossary_comes_after_the_naturalness_request(self):
        prompt = build_user_prompt('{"block_0": "김철수"}', "Glossary (mandatory): ...")
        assert prompt.index("natural") < prompt.index("Glossary") < prompt.index("block_0")

    def test_the_text_is_last(self):
        prompt = build_user_prompt("RAW", "ctx")
        assert prompt.endswith("Translate this:\nRAW")

    def test_no_context_leaves_no_empty_section(self):
        prompt = build_user_prompt("RAW", "  ")
        assert "\n\n\n" not in prompt
        assert prompt == "Make the translation sound as natural as possible.\n\nTranslate this:\nRAW"

    def test_the_llm_base_sends_it(self):
        from modules.translation.llm.base import BaseLLMTranslation

        class Fake(BaseLLMTranslation):
            def _perform_translation(self, user_prompt, system_prompt, image):
                self.user_prompt = user_prompt
                return '{"block_0": "x"}'

            def get_system_prompt(self, source_lang, target_lang):
                return ""

        engine = Fake()
        blk = TextBlock(text_bbox=np.array([0, 0, 1, 1]), text="김철수")
        engine.translate([blk], None, "Glossary (mandatory): ...")
        assert engine.user_prompt.index("natural") < engine.user_prompt.index("Glossary")


def _translator(is_llm, manager):
    translator = Translator.__new__(Translator)
    translator.is_llm_engine = is_llm
    translator.settings = types.SimpleNamespace(
        ui=types.SimpleNamespace(glossary_page=types.SimpleNamespace(manager=manager))
    )
    return translator


@pytest.fixture
def manager(tmp_path):
    from modules.utils.glossary import GlossaryManager

    m = GlossaryManager(str(tmp_path))
    m.save = lambda: None
    m.entries = [GlossaryEntry(source="김철수", target="คิมชอลซู")]
    return m


def _block(text, translation):
    blk = TextBlock(text_bbox=np.array([0, 0, 1, 1]), text=text)
    blk.translation = translation
    return blk


class TestTranslatorCheck:
    def test_an_llm_translation_is_checked(self, manager):
        issues = _translator(True, manager).check_glossary([_block("김철수", "ชอลซู")])
        assert [i.expected for i in issues] == ["คิมชอลซู"]

    def test_a_traditional_translator_is_not(self, manager):
        # DeepL and friends are never sent the glossary; flagging them would
        # warn on every page for something they could not have known.
        assert _translator(False, manager).check_glossary([_block("김철수", "ชอลซู")]) == []


class TestMessages:
    def test_the_warning_names_the_term_as_seen_and_the_expected_translation(self, qapp):
        issues = [GlossaryIssue(0, "김철수", "คิมชอลซู", "김철 수")]
        text = Messages.glossary_issues_text(issues)
        assert "1" in text and "«김철 수»" in text and "«คิมชอลซู»" in text

    def test_repeats_are_listed_once_and_long_lists_are_cut(self, qapp):
        issues = [GlossaryIssue(0, "김철수", "คิมชอลซู", "김철수")] * 3 + [
            GlossaryIssue(i, f"term{i}", f"t{i}", f"term{i}") for i in range(10)
        ]
        lines = Messages.glossary_issues_text(issues).splitlines()
        assert sum("คิมชอลซู" in line for line in lines) == 1
        assert lines[-1] == "…"
        assert len(lines) == 1 + 5 + 1


def _report_controller(paths):
    main = types.SimpleNamespace(
        image_states=types.SimpleNamespace(is_skipped=lambda path: False),
        batch_report_button=types.SimpleNamespace(setEnabled=lambda *_: None),
        mark_project_dirty=lambda: None,
        image_files=list(paths),
        tr=lambda text: text,
    )
    ctrl = BatchReportController(main)
    ctrl.start_batch_report(list(paths))
    return ctrl


class TestBatchReport:
    def test_misses_are_recorded_per_page_and_counted(self):
        ctrl = _report_controller(["/a/002.png", "/a/001.png"])
        ctrl.register_glossary_issues("/a/002.png", [("김철수", "คิมชอลซู")])
        ctrl.register_glossary_issues("/a/002.png", [("김철수", "คิมชอลซู"), ("ナルト", "นารูโตะ")])
        ctrl.register_glossary_issues("/a/001.png", [])
        ctrl.register_glossary_issues("/elsewhere.png", [("x", "y")])
        report = ctrl.finalize_batch_report(was_cancelled=False)

        (entry,) = report["glossary_entries"]
        assert entry["image_name"] == "002.png"
        assert entry["issues"] == [["김철수", "คิมชอลซู"], ["ナルト", "นารูโตะ"]]
        assert report["glossary_count"] == 2
        # A warning is not a skip: the page counts as completed.
        assert report["skipped_count"] == 0 and report["completed_count"] == 2

    def test_the_report_survives_a_project_save(self):
        import msgpack

        ctrl = _report_controller(["/a/002.png"])
        ctrl.register_glossary_issues("/a/002.png", [("김철수", "คิมชอลซู")])
        report = ctrl.finalize_batch_report(was_cancelled=False)
        from app.projects.project_state_v2 import _remap_batch_report_paths_for_loaded_project

        packed = {k: v for k, v in report.items() if k not in ("started_at", "finished_at")}
        restored = msgpack.unpackb(msgpack.packb(packed, use_bin_type=True), strict_map_key=True)
        remapped = _remap_batch_report_paths_for_loaded_project(
            restored, {"original_image_files": ["/a/002.png"]}, ["/tmp/unique/1/002.png"]
        )
        assert remapped["glossary_entries"][0]["image_path"] == "/tmp/unique/1/002.png"
        assert remapped["glossary_entries"][0]["issues"] == [["김철수", "คิมชอลซู"]]

    def test_the_report_widget_lists_the_pages(self, qapp):
        ctrl = _report_controller(["/a/002.png"])
        ctrl.register_glossary_issues("/a/002.png", [("김철수", "คิมชอลซู")])
        report = ctrl.finalize_batch_report(was_cancelled=False)
        widget = ctrl._build_batch_report_widget(report)
        tables = widget.findChildren(QtWidgets.QTableWidget)
        texts = [t.item(0, 1).text() for t in tables if t.rowCount()]
        assert any("«김철수»" in text and "«คิมชอลซู»" in text for text in texts)
        widget.deleteLater()

    def test_an_old_report_without_the_section_still_opens(self, qapp):
        ctrl = _report_controller(["/a/002.png"])
        report = ctrl.finalize_batch_report(was_cancelled=False)
        del report["glossary_entries"], report["glossary_count"]
        ctrl._build_batch_report_widget(report).deleteLater()


class TestBatchPipelineEmits:
    def test_the_batch_processor_sends_misses_to_the_report(self):
        from pipeline.batch_processor import BatchProcessor

        emitted = []
        proc = BatchProcessor.__new__(BatchProcessor)
        proc.main_page = types.SimpleNamespace(
            glossary_issues_found=types.SimpleNamespace(emit=lambda *a: emitted.append(a))
        )
        proc._report_glossary_issues("/a/1.png", [GlossaryIssue(0, "김철수", "คิมชอลซู", "김철 수")])
        proc._report_glossary_issues("/a/2.png", [])
        assert emitted == [("/a/1.png", [("김철 수", "คิมชอลซู")])]

    def test_the_webtoon_chunk_sends_misses_to_the_report(self, monkeypatch, manager):
        from pipeline.webtoon_batch import chunk

        class FakeTranslator:
            def __init__(self, main_page, source_lang, target_lang):
                self.is_llm_engine = True
                self.settings = main_page.settings_page

            def translate(self, blocks, image, extra_context):
                for blk in blocks:
                    blk.translation = "ชอลซู"

            check_glossary = Translator.check_glossary

        monkeypatch.setattr(chunk, "Translator", FakeTranslator)
        emitted = []
        settings_page = types.SimpleNamespace(
            ui=types.SimpleNamespace(glossary_page=types.SimpleNamespace(manager=manager)),
            get_extra_context=lambda text: "",
        )
        proc = types.SimpleNamespace(main_page=types.SimpleNamespace(
            settings_page=settings_page,
            glossary_issues_found=types.SimpleNamespace(emit=lambda *a: emitted.append(a)),
        ))
        blocks = [_block("김철수", "")]
        chunk.ChunkMixin._run_translation_on_blocks(
            proc, np.zeros((4, 4, 3), np.uint8), blocks, "Korean", "Thai", "/w/1.png"
        )
        assert emitted == [("/w/1.png", [("김철수", "คิมชอลซู")])]


class TestGlossaryPage:
    @pytest.fixture
    def page(self, qapp):
        from app.ui.settings.glossary_page import GlossaryPage

        page = GlossaryPage()
        page.manager.save = lambda: None
        yield page
        page.deleteLater()

    def _row_of(self, page, source):
        for row in range(page.table.rowCount()):
            item = page.table.item(row, 0)
            if item.data(0x0100) == source:
                return item
        raise AssertionError(source)

    def test_a_conflicting_overlap_is_marked_on_both_terms(self, page):
        page.manager.entries = [
            GlossaryEntry(source="김철수", target="คิมชอลซู"),
            GlossaryEntry(source="철수", target="เชลซี"),
            GlossaryEntry(source="ナルト", target="นารูโตะ"),
        ]
        page.refresh_table()
        assert "⚠" in self._row_of(page, "김철수").text()
        assert "⚠" in self._row_of(page, "철수").text()
        assert "«김철수»" in self._row_of(page, "철수").toolTip()
        assert "⚠" not in self._row_of(page, "ナルト").text()

    def test_a_consistent_overlap_is_explained_but_not_flagged(self, page):
        page.manager.entries = [
            GlossaryEntry(source="김철수", target="คิมชอลซู"),
            GlossaryEntry(source="철수", target="ชอลซู"),
        ]
        page.refresh_table()
        item = self._row_of(page, "철수")
        assert "⚠" not in item.text() and "«김철수»" in item.toolTip()

    def test_editing_a_marked_row_still_finds_its_entry(self, page):
        page.manager.entries = [
            GlossaryEntry(source="김철수", target="คิมชอลซู"),
            GlossaryEntry(source="철수", target="เชลซี"),
        ]
        page.refresh_table()
        page.table.selectRow(self._row_of(page, "철수").row())
        assert page._selected_sources() == ["철수"]

    def test_the_match_tester_shows_what_is_sent_and_why(self, page):
        from app.ui.settings.glossary_page import GlossaryMatchTestDialog

        page.manager.enabled = True
        page.manager.match_only = True
        page.manager.entries = [
            GlossaryEntry(source="김철수", target="คิมชอลซู"),
            GlossaryEntry(source="철수", target="ชอลซู"),
            GlossaryEntry(source="그림자 군주", target="ราชาเงา"),
        ]
        dialog = GlossaryMatchTestDialog(page.manager, "김철수 왔어\n그림자군쥬님", parent=page)
        results = dialog.results.toPlainText()
        assert "✓ 김철수" in results
        assert "– 철수" in results and "«김철수»" in results
        assert "≈ 그림자 군주" in results and "«그림자군쥬»" in results
        prompt = dialog.prompt_view.toPlainText()
        assert "- 김철수 => คิมชอลซู" in prompt and "- 철수 =>" not in prompt

        dialog.input.setPlainText("nothing here")
        assert "No glossary terms" in dialog.results.toPlainText()
        dialog.deleteLater()

    def test_check_overlaps_lists_the_pairs(self, page, monkeypatch):
        page.manager.entries = [
            GlossaryEntry(source="김철수", target="คิมชอลซู"),
            GlossaryEntry(source="철수", target="เชลซี"),
        ]
        shown = []
        monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda box: shown.append(box.detailedText()))
        page.show_overlaps()
        assert shown and "⚠ 철수" in shown[0] and "김철수" in shown[0]
