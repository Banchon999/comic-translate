"""The glossary path through the real window, with only the LLM faked.

The unit tests prove each piece; this proves they are connected: the page's
OCR text (wrapped, the way PaddleOCR joins Korean lines) reaches the prompt
through the real SettingsPage, the real TranslationHandler and the real LLM
base class, and a reply that ignores the term comes back to the user as a
warning — from the manual Translate button's own thread and callback.
"""

import json
import re

import numpy as np
import pytest
from PySide6 import QtCore

from modules.translation.llm.base import BaseLLMTranslation
from modules.utils.glossary import GlossaryEntry
from modules.utils.textblock import TextBlock


class FakeLLM(BaseLLMTranslation):
    """Answers every block with a fixed reply and remembers what it was asked."""

    prompts: list[str] = []
    reply = "ชอลซูมาแล้ว"

    def get_system_prompt(self, source_lang, target_lang):
        return ""

    def _perform_translation(self, user_prompt, system_prompt, image):
        FakeLLM.prompts.append(user_prompt)
        blocks = json.loads(re.search(r"\{[\s\S]*\}", user_prompt.split("Translate this:")[-1]).group(0))
        return json.dumps({key: FakeLLM.reply for key in blocks}, ensure_ascii=False)


@pytest.fixture
def window(qapp, monkeypatch):
    import controller as controller_mod
    from app.controllers import manual_workflow
    from modules.translation import factory

    FakeLLM.prompts = []
    monkeypatch.setattr(
        factory.TranslationFactory, "create_engine",
        classmethod(lambda cls, *a, **k: FakeLLM()),
    )
    monkeypatch.setattr(manual_workflow, "validate_translator", lambda *a, **k: True)
    shown = []
    real_show = manual_workflow.Messages.show_glossary_issues
    monkeypatch.setattr(
        manual_workflow.Messages, "show_glossary_issues",
        staticmethod(lambda parent, issues: (shown.append(list(issues)), real_show(parent, issues))),
    )

    win = controller_mod.ComicTranslate()
    win.image_viewer.display_image_array(np.full((300, 400, 3), 230, np.uint8))
    manager = win.settings_page.ui.glossary_page.manager
    manager.save = lambda: None
    manager.enabled = True
    manager.match_only = True
    manager.entries = [
        GlossaryEntry(source="김철수", target="คิมชอลซู", type="character"),
        GlossaryEntry(source="철수", target="เชลซี", type="character"),
    ]
    blk = TextBlock(text_bbox=np.array([20, 20, 200, 80]), text="김철 수가 왔다")
    win.blk_list = [blk]
    yield win, shown
    win._skip_close_prompt = True
    win.close()


def _wait_for(predicate, qapp, timeout_ms=10000):
    deadline = QtCore.QDeadlineTimer(timeout_ms)
    while not predicate() and not deadline.hasExpired():
        qapp.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 20)
    return predicate()


def test_translate_sends_the_wrapped_term_and_warns_when_the_reply_ignores_it(window, qapp):
    win, shown = window
    win.manual_workflow_ctrl.translate_image()

    assert _wait_for(lambda: shown, qapp), "no glossary warning reached the user"
    (prompt,) = FakeLLM.prompts
    # The wrapped name was matched; the shorter term inside it was not sent.
    assert "- 김철수 => คิมชอลซู" in prompt
    assert "- 철수 =>" not in prompt
    # The glossary is the last instruction before the text.
    assert prompt.index("natural") < prompt.index("Glossary") < prompt.index("Translate this:")
    (issues,) = shown
    assert [(i.seen_as, i.expected) for i in issues] == [("김철 수", "คิมชอลซู")]
    # Warn only: the model's translation is kept as it came back.
    assert win.blk_list[0].translation == "ชอลซูมาแล้ว"
    assert _wait_for(lambda: not win.loading.isVisible(), qapp)


def test_a_reply_that_follows_the_glossary_is_silent(window, qapp):
    win, shown = window
    FakeLLM.reply = "คิมชอลซูมาแล้ว"
    try:
        win.manual_workflow_ctrl.translate_image()
        assert _wait_for(lambda: win.blk_list[0].translation == "คิมชอลซูมาแล้ว", qapp)
        assert _wait_for(lambda: not win.loading.isVisible(), qapp)
    finally:
        FakeLLM.reply = "ชอลซูมาแล้ว"
    assert shown == []
