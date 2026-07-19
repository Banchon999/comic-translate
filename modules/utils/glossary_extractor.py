import json
import logging

from .glossary import GlossaryEntry

logger = logging.getLogger(__name__)


EXTRACTION_SYSTEM_PROMPT = """You are a terminology extraction assistant for comic/manga/manhwa/webtoon translation.
You will receive raw OCR'd text from a series in {source_lang}. Extract a translation glossary for {target_lang}:
- Proper nouns: character names, places, organizations, titles.
- Recurring special terms: skills, ranks, items, world-specific jargon.
- Skip ordinary everyday words, sentences, numbers, and OCR garbage.
Translate/romanize each term the way a professional {target_lang} localization would, and keep it consistent.

Respond with ONLY a JSON array, no explanations, in this exact shape:
[{{"source": "<term as it appears in the text>", "target": "<translation in {target_lang}>", "type": "<character|place|organization|skill|item|term>", "gender": "<male|female|neutral or empty, characters only>", "note": "<short note, optional>"}}]"""


def _parse_json_array(response: str) -> list[dict]:
    text = (response or "").strip()
    if text.startswith("```"):
        # Strip a markdown code fence
        text = text.split("```", 2)[1]
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("The AI response did not contain a JSON array.")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, list):
        raise ValueError("The AI response was not a JSON array.")
    return [item for item in data if isinstance(item, dict)]


def extract_glossary_terms(main_page, log_text: str, existing_sources: set[str]) -> list[GlossaryEntry]:
    """Ask the configured LLM translator to extract glossary terms from OCR text.

    Returns new entries only (terms already in the glossary are skipped).
    Raises RuntimeError with a user-friendly message when no LLM translator
    is selected.
    """
    from modules.translation.processor import Translator
    from modules.translation.base import LLMTranslation

    source_lang = main_page.s_combo.currentText()
    target_lang = main_page.t_combo.currentText()
    translator = Translator(main_page, source_lang, target_lang)
    engine = translator.engine

    if not isinstance(engine, LLMTranslation):
        raise RuntimeError(
            "Glossary extraction needs an AI translator. "
            "Select an LLM translator (GPT/Claude/Gemini/OpenRouter/Custom) in Settings > Tools first."
        )

    # No page image involved in extraction.
    engine.img_as_llm_input = False

    system_prompt = EXTRACTION_SYSTEM_PROMPT.format(
        source_lang=translator.source_lang_en or "the source language",
        target_lang=translator.target_lang_en or "the target language",
    )
    skip_list = ", ".join(sorted(existing_sources)[:300])
    user_prompt = (
        (f"Terms already in the glossary (do NOT repeat them): {skip_list}\n\n" if skip_list else "")
        + "OCR TEXT:\n" + log_text
    )

    response = engine._perform_translation(user_prompt, system_prompt, None)
    entries = []
    for item in _parse_json_array(response):
        entry = GlossaryEntry.from_dict(item)
        if entry and entry.source not in existing_sources:
            entries.append(entry)
    return entries
