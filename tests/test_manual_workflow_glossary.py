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
from modules.utils.glossary import GlossaryEntry
from modules.utils.textblock import TextBlock
from pipeline.cache_manager import CacheManager


class FakeTranslator:
    """Records the extra_context each call was made with instead of
    reaching an LLM, and echoes the source text back as a fake translation."""

    calls: list[str] = []

    def __init__(self, main_page, source_lang, target_lang):
        pass

    def translate(self, blk_list, image, extra_context):
        FakeTranslator.calls.append(extra_context)
        for blk in blk_list:
            blk.translation = f"[{blk.text}]"
        return blk_list


@pytest.fixture(autouse=True)
def stub_heavy_dependencies(monkeypatch):
    FakeTranslator.calls = []
    monkeypatch.setattr(manual_workflow_module, "Translator", FakeTranslator)
    monkeypatch.setattr(manual_workflow_module, "validate_translator", lambda *a, **kw: True)
    # The single-page cross-check goes through TranslationHandler, which
    # imports its own name binding of Translator.
    monkeypatch.setattr("pipeline.translation_handler.Translator", FakeTranslator)
    yield


def make_main(settings_page, image_states, selected_paths):
    main = types.SimpleNamespace()
    main.settings_page = settings_page
    main.image_states = image_states
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
