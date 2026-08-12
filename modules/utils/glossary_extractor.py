import json
import logging
from typing import Callable

from .glossary import GlossaryEntry, term_key

logger = logging.getLogger(__name__)


#: How much text goes into one extraction call.
#:
#: The point of a large chunk is context. A single page of a manhwa is a dozen
#: short lines with no indication of who anyone is, and a model asked to pull
#: proper nouns out of that has nothing to reason from. Fifteen thousand
#: characters is a few chapters' worth of dialogue, which is enough to tell a
#: recurring character name from a one-off shout.
CHUNK_LIMIT = 15000

#: Cap on how many known terms are listed in the prompt. The result is filtered
#: against the *full* set regardless, so overflowing this costs a wasted slot in
#: the model's answer, never a duplicate entry.
SKIP_LIST_LIMIT = 1200


EXTRACTION_SYSTEM_PROMPT = """You are a terminology extraction assistant for comic/manga/manhwa/webtoon translation.
You will receive raw OCR'd text from a series in {source_lang}. Extract a translation glossary for {target_lang}:
- Proper nouns: character names, places, organizations, titles.
- Recurring special terms: skills, ranks, items, world-specific jargon.
- Skip ordinary everyday words, sentences, numbers, and OCR garbage.

Give every term in its dictionary form. Strip grammatical particles, honorific
suffixes and inflection from the source term, so a Korean name written 홍길동이,
홍길동은 and 홍길동을 in the text is reported once, as 홍길동. Never report the same
term twice, and never report a term that only differs from another by a
particle, spacing or an attached suffix.

Translate/romanize each term the way a professional {target_lang} localization would, and keep it consistent.

Respond with ONLY a JSON array, no explanations, in this exact shape:
[{{"source": "<term in dictionary form>", "target": "<translation in {target_lang}>", "type": "<character|place|organization|skill|item|term>", "gender": "<male|female|neutral or empty, characters only>", "note": "<short note, optional>"}}]"""


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


def split_into_chunks(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """Cut text into pieces of at most `limit` characters, on line boundaries.

    Splitting mid-sentence would hand the model half a name, so each cut walks
    back to the last newline — but only if that newline is in the second half of
    the chunk. Walking back further than that would produce chunks small enough
    to lose the context the size was chosen for, so a stretch with no newlines
    is cut bluntly instead.
    """
    text = text or ""
    if len(text) <= limit:
        return [text] if text.strip() else []

    chunks: list[str] = []
    position = 0
    while position < len(text):
        end = position + limit
        if end < len(text):
            boundary = text.rfind("\n", position, end)
            if boundary > position + limit // 2:
                end = boundary + 1
        else:
            end = len(text)
        piece = text[position:end]
        if piece.strip():
            chunks.append(piece)
        position = end
    return chunks


def extract_glossary_terms(
    main_page,
    log_text: str,
    existing_sources: set[str],
    progress: Callable[[int, int], None] | None = None,
) -> list[GlossaryEntry]:
    """Ask the configured LLM translator to extract glossary terms from OCR text.

    The whole text is processed — long input is split into chunks rather than
    truncated, and terms found in one chunk are added to the skip list for the
    next, so the model is never asked to re-report something already found.

    Returns new entries only. Raises RuntimeError with a user-friendly message
    when no LLM translator is selected; a chunk that fails is logged and
    skipped, so one bad response does not lose the whole run.
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

    chunks = split_into_chunks(log_text)
    if not chunks:
        return []

    # Keyed, not raw: two spellings of the same Korean name are one entry, and
    # the model must not be handed both as things it has already seen.
    seen = {term_key(source) for source in existing_sources if source}
    seen.discard("")
    known_display = [source for source in existing_sources if source]

    entries: list[GlossaryEntry] = []
    for index, chunk in enumerate(chunks, start=1):
        if progress is not None:
            progress(index, len(chunks))

        skip_list = ", ".join(sorted(known_display)[:SKIP_LIST_LIMIT])
        user_prompt = (
            (f"Terms already in the glossary (do NOT repeat them): {skip_list}\n\n" if skip_list else "")
            + "OCR TEXT:\n" + chunk
        )

        try:
            response = engine._perform_translation(user_prompt, system_prompt, None)
            items = _parse_json_array(response)
        except Exception:
            logger.exception(
                "Glossary extraction failed on chunk %d/%d; continuing with the rest",
                index, len(chunks),
            )
            continue

        for item in items:
            entry = GlossaryEntry.from_dict(item)
            if entry is None or entry.key in seen:
                continue
            # Added before the next chunk is built, so a term found here is on
            # the skip list for the rest of the run — and a model that repeats
            # itself inside one response only gets counted once.
            seen.add(entry.key)
            known_display.append(entry.source)
            entries.append(entry)

    return entries
