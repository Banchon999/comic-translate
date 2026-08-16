"""Extraction sends the whole text, and never the same term twice.

Both properties were broken in real use: the batch pre-pass truncated its input
at 60,000 characters, and the dedup filter compared against a set captured
before the call that was never updated as results came in.
"""

import json
import types
import unicodedata

import pytest

from modules.translation.base import LLMTranslation
from modules.utils import glossary_extractor as gx

NAME = "홍길동"
LINE = "홍길동: 가나다라마바사아자차카타파하 대사입니다\n"


class StubEngine(LLMTranslation):
    """Answers with a scripted reply per call and records what it was asked."""

    def __init__(self, replies):
        self.replies = replies
        self.prompts = []
        self.img_as_llm_input = True

    def initialize(self, *args, **kwargs):
        pass

    def translate(self, *args, **kwargs):
        return {}

    def _perform_translation(self, user_prompt, system_prompt, image):
        self.prompts.append(user_prompt)
        reply = self.replies[min(len(self.prompts) - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply, ensure_ascii=False)


@pytest.fixture
def extract(monkeypatch):
    """Run extraction against a stub model, returning entries and prompts."""

    def run(replies, text, existing=()):
        engine = StubEngine(replies)

        class FakeTranslator:
            def __init__(self, *args, **kwargs):
                self.engine = engine
                self.source_lang_en = "Korean"
                self.target_lang_en = "Thai"

        import modules.translation.processor as processor

        monkeypatch.setattr(processor, "Translator", FakeTranslator)
        main_page = types.SimpleNamespace(
            s_combo=types.SimpleNamespace(currentText=lambda: "Korean"),
            t_combo=types.SimpleNamespace(currentText=lambda: "Thai"),
        )
        entries = gx.extract_glossary_terms(main_page, text, set(existing))
        return entries, engine.prompts

    return run


class TestChunking:
    def test_nothing_is_dropped(self):
        text = LINE * (gx.CHUNK_LIMIT * 3 // len(LINE))
        chunks = gx.split_into_chunks(text)
        assert "".join(chunks) == text
        assert len(chunks) > 1

    def test_no_chunk_exceeds_the_limit(self):
        text = LINE * (gx.CHUNK_LIMIT * 3 // len(LINE))
        assert all(len(chunk) <= gx.CHUNK_LIMIT for chunk in gx.split_into_chunks(text))

    def test_cuts_land_on_line_boundaries(self):
        text = LINE * (gx.CHUNK_LIMIT * 3 // len(LINE))
        chunks = gx.split_into_chunks(text)
        assert all(chunk.endswith("\n") for chunk in chunks[:-1])

    def test_text_with_no_newlines_is_still_split_and_lossless(self):
        text = "가" * (gx.CHUNK_LIMIT * 2 + 5)
        chunks = gx.split_into_chunks(text)
        assert "".join(chunks) == text
        assert all(len(chunk) <= gx.CHUNK_LIMIT for chunk in chunks)

    @pytest.mark.parametrize("empty", ["", "   ", "\n \n"])
    def test_empty_input_produces_no_chunks(self, empty):
        assert gx.split_into_chunks(empty) == []

    def test_short_input_is_one_chunk(self):
        assert gx.split_into_chunks(NAME) == [NAME]


class TestExtraction:
    def test_every_chunk_is_sent(self, extract):
        text = LINE * (gx.CHUNK_LIMIT * 3 // len(LINE))
        expected = len(gx.split_into_chunks(text))
        _, prompts = extract([[{"source": NAME, "target": "ฮงกิลดง"}]], text)
        assert len(prompts) == expected > 1

    def test_a_term_found_early_is_on_the_skip_list_later(self, extract):
        text = LINE * (gx.CHUNK_LIMIT * 3 // len(LINE))
        _, prompts = extract([[{"source": NAME, "target": "ฮงกิลดง"}]], text)
        assert "do NOT repeat" not in prompts[0]
        assert all(NAME in prompt.split("OCR TEXT:")[0] for prompt in prompts[1:])

    def test_the_same_term_repeated_in_one_reply_is_kept_once(self, extract):
        reply = [
            {"source": NAME, "target": "ฮงกิลดง"},
            {"source": NAME, "target": "ฮงกิลดง"},
            {"source": unicodedata.normalize("NFD", NAME), "target": "ฮง กิลดง"},
            {"source": NAME + " ", "target": "ฮงกิลดง"},
        ]
        entries, _ = extract([reply], LINE * 5)
        assert [e.source for e in entries] == [NAME]

    def test_a_term_already_in_the_glossary_is_not_re_added(self, extract):
        reply = [{"source": unicodedata.normalize("NFD", NAME), "target": "ฮงกิลดง"}]
        entries, _ = extract([reply], LINE * 5, existing={NAME})
        assert entries == []

    def test_a_failing_chunk_does_not_lose_the_others(self, extract):
        text = LINE * (gx.CHUNK_LIMIT * 3 // len(LINE))
        entries, prompts = extract(
            [
                [{"source": NAME, "target": "ฮงกิลดง"}],
                RuntimeError("provider returned 503"),
                [{"source": "김철수", "target": "คิมชอลซู"}],
                [{"source": "박영희", "target": "พักยองฮี"}],
            ],
            text,
        )
        assert len(prompts) > 2
        assert NAME in [e.source for e in entries]
        assert "김철수" in [e.source for e in entries]

    def test_unparseable_json_is_survivable(self, extract):
        text = LINE * (gx.CHUNK_LIMIT * 3 // len(LINE))
        entries, _ = extract(
            [ValueError("not json"), [{"source": "김철수", "target": "คิมชอลซู"}]],
            text,
        )
        assert [e.source for e in entries] == ["김철수"]

    def test_progress_is_reported_for_every_chunk(self, monkeypatch):
        engine = StubEngine([[]])

        class FakeTranslator:
            def __init__(self, *args, **kwargs):
                self.engine = engine
                self.source_lang_en = "Korean"
                self.target_lang_en = "Thai"

        import modules.translation.processor as processor

        monkeypatch.setattr(processor, "Translator", FakeTranslator)
        main_page = types.SimpleNamespace(
            s_combo=types.SimpleNamespace(currentText=lambda: "Korean"),
            t_combo=types.SimpleNamespace(currentText=lambda: "Thai"),
        )
        text = LINE * (gx.CHUNK_LIMIT * 3 // len(LINE))
        seen = []
        gx.extract_glossary_terms(main_page, text, set(), progress=lambda i, n: seen.append((i, n)))
        total = len(gx.split_into_chunks(text))
        assert seen == [(i, total) for i in range(1, total + 1)]

    def test_no_text_means_no_model_call(self, extract):
        entries, prompts = extract([[]], "   \n  ")
        assert entries == [] and prompts == []


def test_json_array_survives_a_markdown_fence():
    parsed = gx._parse_json_array('```json\n[{"source": "a", "target": "b"}]\n```')
    assert parsed == [{"source": "a", "target": "b"}]


def test_json_array_survives_chatter_around_it():
    parsed = gx._parse_json_array('Sure! Here you go:\n[{"source": "a", "target": "b"}] Hope that helps.')
    assert parsed == [{"source": "a", "target": "b"}]


def test_a_reply_with_no_array_is_an_error():
    with pytest.raises(ValueError):
        gx._parse_json_array("I could not find any terms.")
