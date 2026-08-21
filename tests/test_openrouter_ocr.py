"""OCR routed through OpenRouter, so it bills OpenRouter credit.

The point is to reach a vision model — Gemini Flash Lite, say — without
holding an account with whoever runs it. What matters is that the request
goes to OpenRouter with the user's chosen routing id, and that the three
things OpenRouter does differently from OpenAI are all handled.
"""

import types

import numpy as np
import pytest

from modules.ocr.gpt_ocr import GPTOCR
from modules.ocr.openrouter_ocr import OpenRouterOCR


def settings(api_key="sk-or-test", model="google/gemini-2.5-flash-lite"):
    return types.SimpleNamespace(
        ui=types.SimpleNamespace(tr=lambda s: s),
        get_credentials=lambda service: {"api_key": api_key, "model": model},
    )


class Captured:
    """Stands in for requests.post and records what was sent."""

    def __init__(self, status=200, text="ข้อความทดสอบ"):
        self.status, self.text, self.calls = status, text, []

    def __call__(self, url, headers=None, data=None, **kw):
        import json as _json
        self.calls.append({"url": url, "headers": headers, "payload": _json.loads(data)})
        body = {"choices": [{"message": {"content": self.text}}]}
        return types.SimpleNamespace(
            status_code=self.status, text="err", json=lambda: body
        )


@pytest.fixture
def post(monkeypatch):
    captured = Captured()
    monkeypatch.setattr("modules.ocr.gpt_ocr.requests.post", captured)
    return captured


@pytest.fixture
def engine():
    e = OpenRouterOCR()
    e.initialize(settings())
    return e


class TestRouting:
    def test_it_posts_to_openrouter_not_openai(self, engine, post):
        engine._get_gpt_ocr("Zm9v")
        assert post.calls[0]["url"].startswith("https://openrouter.ai/api/v1")

    def test_it_sends_the_model_id_from_the_credentials_page(self, engine, post):
        engine._get_gpt_ocr("Zm9v")
        assert post.calls[0]["payload"]["model"] == "google/gemini-2.5-flash-lite"

    def test_the_routing_id_is_used_verbatim(self, post):
        """A routing id is not one of this app's own model names."""
        e = OpenRouterOCR()
        e.initialize(settings(model="anthropic/claude-4.5-haiku"))
        e._get_gpt_ocr("Zm9v")
        assert post.calls[0]["payload"]["model"] == "anthropic/claude-4.5-haiku"

    def test_it_authenticates_with_the_openrouter_key(self, engine, post):
        engine._get_gpt_ocr("Zm9v")
        assert post.calls[0]["headers"]["Authorization"] == "Bearer sk-or-test"

    def test_it_identifies_itself_for_attribution(self, engine, post):
        headers = (engine._get_gpt_ocr("Zm9v"), post.calls[0]["headers"])[1]
        assert headers["X-Title"] == "Comic Translate"
        assert headers["HTTP-Referer"].startswith("https://")


class TestTheOpenAiDifferences:
    def test_it_uses_max_tokens_not_max_completion_tokens(self, engine, post):
        """OpenAI renamed the parameter; OpenRouter kept the original."""
        engine._get_gpt_ocr("Zm9v")
        payload = post.calls[0]["payload"]
        assert "max_tokens" in payload
        assert "max_completion_tokens" not in payload

    def test_plain_gpt_still_uses_the_openai_name(self, post):
        e = GPTOCR()
        e.initialize(api_key="sk-test", model="GPT-4.1-mini")
        e._get_gpt_ocr("Zm9v")
        payload = post.calls[0]["payload"]
        assert "max_completion_tokens" in payload
        assert "max_tokens" not in payload

    def test_plain_gpt_still_posts_to_openai(self, post):
        e = GPTOCR()
        e.initialize(api_key="sk-test", model="GPT-4.1-mini")
        e._get_gpt_ocr("Zm9v")
        assert post.calls[0]["url"].startswith("https://api.openai.com")


class TestGuards:
    def test_no_model_says_so_rather_than_posting(self, post):
        e = OpenRouterOCR()
        e.initialize(settings(model=""))
        with pytest.raises(ValueError, match="model id"):
            e._get_gpt_ocr("Zm9v")
        assert post.calls == []

    def test_no_key_says_so_rather_than_posting(self, post):
        e = OpenRouterOCR()
        e.initialize(settings(api_key=""))
        with pytest.raises(ValueError, match="API key"):
            e._get_gpt_ocr("Zm9v")
        assert post.calls == []

    def test_an_api_error_returns_nothing_rather_than_raising(self, engine, monkeypatch):
        monkeypatch.setattr("modules.ocr.gpt_ocr.requests.post", Captured(status=402))
        assert engine._get_gpt_ocr("Zm9v") == ""

    def test_missing_credentials_do_not_blow_up_at_init(self):
        blank = types.SimpleNamespace(
            ui=types.SimpleNamespace(tr=lambda s: s),
            get_credentials=lambda service: {},
        )
        e = OpenRouterOCR()
        e.initialize(blank)
        assert e.api_key == "" and e.model == ""


class TestItReadsABlock:
    def test_the_recognised_text_lands_on_the_block(self, engine, post):
        from modules.utils.textblock import TextBlock

        image = np.full((80, 120, 3), 220, dtype=np.uint8)
        block = TextBlock(text_bbox=np.array([10, 10, 60, 40]))
        engine.process_image(image, [block])
        assert block.text == "ข้อความทดสอบ"
        assert len(post.calls) == 1


class TestTheFactoryKnowsIt:
    def test_it_is_listed_as_an_llm_engine(self):
        from modules.ocr.factory import OCRFactory

        assert OCRFactory.LLM_ENGINE_IDENTIFIERS["OpenRouter"] is OpenRouterOCR

    def test_the_settings_dropdown_offers_it(self, qapp):
        from app.ui.settings.settings_ui import SettingsPageUI

        ui = SettingsPageUI()
        assert "OpenRouter" in [ui.value_mappings.get(n, n) for n in ui.ocr_engines]
