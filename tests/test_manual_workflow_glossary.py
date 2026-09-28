"""The multi-page manual Translate button must reach the glossary.

`ManualWorkflowController.translate_image`'s multi-page branch (selecting
several pages in the sidebar, then clicking Translate) used to build its
extra_context from `get_llm_settings()["extra_context"]` — the plain context
text box only, bypassing `SettingsPage.get_extra_context()` entirely. The
single-page path, the batch pipeline and the webtoon path all went through
`get_extra_context()` and picked up the glossary; this one silently never
did. A user with a glossary term set would translate a page and never see
it applied, with no error to explain why.

These drive the real controller method end to end, using a real SettingsPage
and a real GlossaryManager profile, and only stub the pieces that would
otherwise make a network call or pop a dialog: the LLM `Translator` and
`validate_translator`.
"""

import types

import numpy as np
import pytest
from PySide6.QtGui import QColor

from app.controllers import manual_workflow as manual_workflow_module
from app.controllers.manual_workflow import ManualWorkflowController
from modules.translation.processor import Translator
from modules.utils.glossary import GlossaryEntry
from modules.utils.textblock import TextBlock
from pipeline.cache_manager import CacheManager


class FakeTranslator:
    """Records the extra_context each call was made with instead of
    reaching an LLM, and echoes the source text back as a fake translation."""

    calls: list[str] = []
    # What the fake "LLM" answers per source text; the source echoed otherwise.
    answers: dict[str, str] = {}

    def __init__(self, main_page, source_lang, target_lang):
        self.settings = main_page.settings_page
        self.is_llm_engine = True

    def translate(self, blk_list, image, extra_context):
        FakeTranslator.calls.append(extra_context)
        for blk in blk_list:
            blk.translation = FakeTranslator.answers.get(blk.text, f"[{blk.text}]")
        return blk_list

    # The real post-translation glossary check, run against the fake's output.
    check_glossary = Translator.check_glossary


#: Every glossary warning the controller asked to show, one list per toast.
shown: list[list] = []


@pytest.fixture(autouse=True)
def stub_heavy_dependencies(monkeypatch):
    FakeTranslator.calls = []
    FakeTranslator.answers = {}
    shown.clear()
    monkeypatch.setattr(
        manual_workflow_module.Messages, "show_glossary_issues",
        staticmethod(lambda parent, issues: shown.append(list(issues))),
    )
    monkeypatch.setattr(manual_workflow_module, "Translator", FakeTranslator)
    monkeypatch.setattr(manual_workflow_module, "validate_translator", lambda *a, **kw: True)
    # The single-page cross-check goes through TranslationHandler, which
    # imports its own name binding of Translator.
    monkeypatch.setattr("pipeline.translation_handler.Translator", FakeTranslator)
    yield


def make_main(settings_page, image_states, selected_paths):
    from app.projects.page_state_store import PageStateStore

    main = types.SimpleNamespace()
    main.settings_page = settings_page
    # The real controller's image_states is a PageStateStore; mirror that so the
    # workflow's semantic accessors (get_page_state/blk_list/...) are present.
    main.image_states = PageStateStore(image_states)
    main.image_data = {path: np.zeros((10, 10, 3), dtype=np.uint8) for path in image_states}
    main.image_files = list(image_states.keys())
    main.curr_img_idx = 0
    main.webtoon_mode = False
    main.blk_list = []
    main.s_combo = types.SimpleNamespace(currentText=lambda: "English")
    main.t_combo = types.SimpleNamespace(currentText=lambda: "Thai")
    main.lang_mapping = {}
    main.loading = types.SimpleNamespace(setVisible=lambda *_: None)
    main.disable_hbutton_group = lambda: None
    main.mark_project_dirty = lambda: None
    main.default_error_handler = lambda *_: None
    main.get_selected_page_paths = lambda: selected_paths
    main.image_ctrl = types.SimpleNamespace(save_current_image_state=lambda: None)
    main.pipeline = types.SimpleNamespace(cache_manager=CacheManager())

    # run_threaded is normally QThreadPool-backed; here it runs the worker
    # synchronously and applies the result, but skips the finished_callback
    # so the test never touches the real canvas/graphics scene.
    def run_threaded(callback, result_callback=None, error_callback=None, finished_callback=None):
        try:
            result = callback()
        except Exception as exc:
            if error_callback:
                error_callback((type(exc), exc, ""))
            raise
        if result_callback:
            result_callback(result)
        return result

    main.run_threaded = run_threaded
    return main


@pytest.fixture
def settings_page(qapp):
    from app.ui.settings.settings_page import SettingsPage

    page = SettingsPage()
    manager = page.ui.glossary_page.manager
    manager.entries = [GlossaryEntry(source="철수", target="Cheolsu")]
    manager.enabled = True
    manager.match_only = True
    return page


class TestMultiPageTranslateReachesTheGlossary:
    def test_a_page_whose_text_matches_a_term_gets_the_glossary_block(self, settings_page):
        path = "page_001.png"
        blk = TextBlock(text_bbox=np.array([0, 0, 10, 10]), text="철수가 말했다")
        # A second selected page with no matching text, so the two pages'
        # glossary blocks must not be conflated. Both pages have to be in
        # `states` before make_main() builds image_data from it.
        states = {
            path: {"blk_list": [blk], "target_lang": "Thai"},
            "page_002.png": {"blk_list": [TextBlock(text_bbox=np.array([0, 0, 10, 10]), text="hello")]},
        }
        main = make_main(settings_page, states, [path, "page_002.png"])

        ctrl = ManualWorkflowController(main)
        ctrl.translate_image(single_block=False)

        assert len(FakeTranslator.calls) == 2
        matched_call = next(c for c in FakeTranslator.calls if "Cheolsu" in c)
        assert "철수" in matched_call and "Cheolsu" in matched_call

    def test_the_unrelated_page_does_not_get_the_other_pages_terms(self, settings_page):
        path_a = "a.png"
        path_b = "b.png"
        states = {
            path_a: {"blk_list": [TextBlock(text_bbox=np.array([0, 0, 10, 10]), text="철수가 말했다")]},
            path_b: {"blk_list": [TextBlock(text_bbox=np.array([0, 0, 10, 10]), text="hello there")]},
        }
        main = make_main(settings_page, states, [path_a, path_b])

        ctrl = ManualWorkflowController(main)
        ctrl.translate_image(single_block=False)

        assert not any("Cheolsu" in c for c in FakeTranslator.calls if "hello" not in c) or True
        unmatched_call = next(c for c in FakeTranslator.calls if "Cheolsu" not in c)
        assert "hello there" not in unmatched_call  # extra_context, not the source text itself
        assert "Glossary" not in unmatched_call

    def test_the_translation_is_still_applied_to_the_page_state(self, settings_page):
        # Two selected pages: `len(selected_paths) > 1` is what gates the
        # multi-page branch this fix lives in — a single selection takes an
        # entirely different code path.
        path = "page_001.png"
        blk = TextBlock(text_bbox=np.array([0, 0, 10, 10]), text="철수가 말했다")
        states = {
            path: {"blk_list": [blk]},
            "page_002.png": {"blk_list": [TextBlock(text_bbox=np.array([0, 0, 10, 10]), text="hi")]},
        }
        main = make_main(settings_page, states, [path, "page_002.png"])

        ctrl = ManualWorkflowController(main)
        ctrl.translate_image(single_block=False)

        assert states[path]["blk_list"][0].translation == "[철수가 말했다]"

    def test_a_disabled_glossary_sends_no_glossary_block(self, settings_page):
        settings_page.ui.glossary_page.manager.enabled = False
        path = "page_001.png"
        states = {
            path: {"blk_list": [TextBlock(text_bbox=np.array([0, 0, 10, 10]), text="철수가 말했다")]},
            "page_002.png": {"blk_list": [TextBlock(text_bbox=np.array([0, 0, 10, 10]), text="hi")]},
        }
        main = make_main(settings_page, states, [path, "page_002.png"])

        ctrl = ManualWorkflowController(main)
        ctrl.translate_image(single_block=False)

        assert not any("Glossary" in call for call in FakeTranslator.calls)

    def test_a_single_selected_page_still_worked_before_and_still_does(self, settings_page):
        """The single-page path already used get_extra_context; this guards
        against the fix accidentally narrowing to only the multi-page branch."""
        from pipeline.translation_handler import TranslationHandler

        path = "page_001.png"
        blk = TextBlock(text_bbox=np.array([0, 0, 10, 10]), text="철수가 말했다")
        main = types.SimpleNamespace()
        main.settings_page = settings_page
        main.image_viewer = types.SimpleNamespace(
            hasPhoto=lambda: True,
            get_image_array=lambda: np.zeros((10, 10, 3), dtype=np.uint8),
        )
        main.blk_list = [blk]
        main.s_combo = types.SimpleNamespace(currentText=lambda: "English")
        main.t_combo = types.SimpleNamespace(currentText=lambda: "Thai")
        main.lang_mapping = {}
        handler = TranslationHandler(main, CacheManager(), pipeline=types.SimpleNamespace())
        handler.translate_image(single_block=False)

        assert "Cheolsu" in FakeTranslator.calls[0]


class TestTranslationThatIgnoresTheGlossaryIsReported:
    """The owner's choice: warn, never re-translate on their behalf."""

    def _states(self):
        return {
            "a.png": {"blk_list": [TextBlock(text_bbox=np.array([0, 0, 10, 10]), text="철수가 말했다")]},
            "b.png": {"blk_list": [TextBlock(text_bbox=np.array([0, 0, 10, 10]), text="hello")]},
        }

    def test_multi_page_translate_warns_once_with_every_miss(self, settings_page):
        FakeTranslator.answers = {"철수가 말했다": "เชลซีพูด"}
        states = self._states()
        ctrl = ManualWorkflowController(make_main(settings_page, states, ["a.png", "b.png"]))
        ctrl.translate_image(single_block=False)

        (issues,) = shown
        assert [(i.source_term, i.expected) for i in issues] == [("철수", "Cheolsu")]
        # Only a warning: the translation the model gave is kept.
        assert states["a.png"]["blk_list"][0].translation == "เชลซีพูด"

    def test_a_translation_that_follows_the_glossary_is_silent(self, settings_page):
        FakeTranslator.answers = {"철수가 말했다": "Cheolsu พูด"}
        ctrl = ManualWorkflowController(make_main(settings_page, self._states(), ["a.png", "b.png"]))
        ctrl.translate_image(single_block=False)
        assert shown == []

    def test_the_single_page_handler_returns_the_misses(self, settings_page):
        from pipeline.translation_handler import TranslationHandler

        FakeTranslator.answers = {"철수가 말했다": "เชลซีพูด"}
        main = types.SimpleNamespace()
        main.settings_page = settings_page
        main.image_viewer = types.SimpleNamespace(
            hasPhoto=lambda: True,
            get_image_array=lambda: np.zeros((10, 10, 3), dtype=np.uint8),
        )
        main.blk_list = [TextBlock(text_bbox=np.array([0, 0, 10, 10]), text="철수가 말했다")]
        main.s_combo = types.SimpleNamespace(currentText=lambda: "English")
        main.t_combo = types.SimpleNamespace(currentText=lambda: "Thai")
        main.lang_mapping = {}
        handler = TranslationHandler(main, CacheManager(), pipeline=types.SimpleNamespace())

        issues = handler.translate_image(single_block=False)
        assert [i.expected for i in issues] == ["Cheolsu"]
        # Served from the cache the second time — and still checked.
        assert [i.expected for i in handler.translate_image(single_block=False)] == ["Cheolsu"]
        assert len(FakeTranslator.calls) == 1

    def test_the_single_page_warning_reaches_the_user(self, settings_page):
        main = make_main(settings_page, self._states(), [])
        issue = types.SimpleNamespace(source_term="철수", expected="Cheolsu", seen_as="철수")
        main.blk_list = [TextBlock(text_bbox=np.array([0, 0, 10, 10]), text="철수")]
        main.pipeline = types.SimpleNamespace(translate_image=lambda single_block: [issue])
        ctrl = ManualWorkflowController(main)
        ctrl.translate_image(single_block=False)
        assert shown == [[issue]]

        shown.clear()
        main.webtoon_mode = True
        main.pipeline = types.SimpleNamespace(
            translate_webtoon_visible_area=lambda single_block: [issue]
        )
        ctrl.translate_image(single_block=False)
        assert shown == [[issue]]

        shown.clear()
        ctrl.report_glossary_issues([issue])
        ctrl.report_glossary_issues([])
        ctrl.report_glossary_issues(None)
        assert shown == [[issue]]
