import os
import json
import logging

from .paths import get_user_data_dir

logger = logging.getLogger(__name__)


# Always appended after the (editable) style template so custom prompts can
# never break the JSON in/out contract the pipeline depends on.
OUTPUT_CONTRACT = """Specifically, you will be translating text OCR'd from a comic. The OCR is not perfect so you may receive text with typos or other mistakes.
To aid you and provide context, you may be given the image of the page and/or extra context about the comic. You will be given a JSON string of the detected text blocks and the text to translate. Return the JSON string with the texts translated. DO NOT translate the keys of the JSON. For each block:
- If it's already in {target_lang} or looks like gibberish, OUTPUT IT AS IT IS instead
- DO NOT give explanations"""


BUILTIN_PRESETS = {
    "Default": (
        "You are an expert translator who translates {source_lang} to {target_lang}. "
        "You pay attention to style, formality, idioms, slang etc and try to convey it in the way "
        "a {target_lang} speaker would understand. BE MORE NATURAL. "
        "NEVER USE 당신, 그녀, 그 or their Japanese equivalents."
    ),
    "Manga": (
        "You are an expert manga translator and localizer working from {source_lang} to {target_lang}.\n"
        "- Dialogue must sound like natural spoken {target_lang}; keep lines short and punchy so they fit speech bubbles.\n"
        "- Preserve each character's voice: speech level (polite/casual/rough), verbal tics, and personality.\n"
        "- Japanese honorifics (-san, -kun, -chan, senpai): keep them only when they carry meaning; otherwise localize naturally.\n"
        "- Sound effects/onomatopoeia: translate the feeling with an equivalent {target_lang} onomatopoeia, not a literal description.\n"
        "- NEVER translate pronouns literally; drop or rephrase them the way {target_lang} naturally does. NEVER USE stiff textbook pronouns.\n"
        "- Keep name order and readings consistent throughout the series."
    ),
    "Manhwa": (
        "You are an expert manhwa translator and localizer working from {source_lang} to {target_lang}.\n"
        "- Dialogue must be natural, modern spoken {target_lang}; keep lines compact for speech bubbles.\n"
        "- Respect Korean speech levels: jondaemal (formal) vs banmal (casual) should show as tone differences in {target_lang}.\n"
        "- Address terms (hyung, noona, oppa, sunbae, ajusshi): keep only when meaningful to the story; otherwise localize.\n"
        "- Genre terms (hunter, gate, dungeon, cultivation ranks, martial arts titles) must stay consistent everywhere.\n"
        "- NEVER USE 당신, 그녀, 그 literally; rephrase the way {target_lang} actually refers to people.\n"
        "- Romanized names must stay consistent across the whole series."
    ),
    "Webtoon": (
        "You are an expert webtoon translator and localizer working from {source_lang} to {target_lang}.\n"
        "- Webtoons scroll vertically with small bubbles: keep translations SHORT, snappy, and easy to read on a phone.\n"
        "- Use contemporary, casual {target_lang}; internet slang is fine when the original is slangy.\n"
        "- Keep the emotional beat of each line: comedic timing, dramatic pauses, cliffhanger lines.\n"
        "- Sound effects: use short, punchy {target_lang} onomatopoeia.\n"
        "- Keep character names, nicknames, and ranks consistent across episodes.\n"
        "- NEVER translate pronouns stiffly; make dialogue sound like real chat between {target_lang} speakers."
    ),
    "Comic (Western)": (
        "You are an expert comic translator and localizer working from {source_lang} to {target_lang}.\n"
        "- Preserve humor, wordplay, and idioms by finding equivalent expressions in {target_lang}, not literal renderings.\n"
        "- Keep superhero/fantasy jargon and character catchphrases consistent.\n"
        "- Match the register: narration can be literary, dialogue must sound spoken.\n"
        "- Keep lines short enough to fit their speech balloons."
    ),
}


def _fill(template: str, source_lang: str, target_lang: str) -> str:
    # Plain replace (not str.format) so braces in user templates can't crash.
    return template.replace("{source_lang}", source_lang).replace("{target_lang}", target_lang)


class PromptManager:
    """Stores translation prompt presets and the active selection.

    Built-in presets target comic mediums (manga/manhwa/webtoon); users can
    save their own. Persisted as prompts.json in the user data directory.
    """

    _instance = None

    def __init__(self, file_path: str | None = None):
        self.file_path = file_path or os.path.join(get_user_data_dir(), "prompts.json")
        self.active_preset: str = "Default"
        self.custom_presets: dict[str, str] = {}
        self.load()

    @classmethod
    def instance(cls) -> "PromptManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # Persistence

    def load(self) -> None:
        if not os.path.exists(self.file_path):
            return
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.custom_presets = {
                str(k): str(v) for k, v in (data.get("custom", {}) or {}).items()
            }
            active = str(data.get("active", "Default"))
            self.active_preset = active if active in self.list_presets() else "Default"
        except Exception as e:
            logger.error(f"Failed to load prompt presets: {e}")

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"active": self.active_preset, "custom": self.custom_presets},
                    f, ensure_ascii=False, indent=2,
                )
        except Exception as e:
            logger.error(f"Failed to save prompt presets: {e}")

    # Presets

    def list_presets(self) -> list[str]:
        return list(BUILTIN_PRESETS) + sorted(
            name for name in self.custom_presets if name not in BUILTIN_PRESETS
        )

    def is_builtin(self, name: str) -> bool:
        return name in BUILTIN_PRESETS

    def get_template(self, name: str | None = None) -> str:
        name = name or self.active_preset
        if name in self.custom_presets:
            return self.custom_presets[name]
        return BUILTIN_PRESETS.get(name, BUILTIN_PRESETS["Default"])

    def set_active(self, name: str) -> None:
        if name in self.list_presets():
            self.active_preset = name
            self.save()

    def save_custom(self, name: str, template: str) -> None:
        name = name.strip()
        if not name or not template.strip():
            return
        self.custom_presets[name] = template.strip()
        self.active_preset = name
        self.save()

    def delete_custom(self, name: str) -> None:
        if name in self.custom_presets:
            del self.custom_presets[name]
            if self.active_preset == name:
                self.active_preset = "Default"
            self.save()

    # Prompt building

    def fingerprint(self) -> str:
        """Short id of the active prompt for cache keys."""
        import hashlib
        return hashlib.md5(
            f"{self.active_preset}:{self.get_template()}".encode("utf-8")
        ).hexdigest()[:12]

    def build_system_prompt(self, source_lang: str, target_lang: str) -> str:
        style = _fill(self.get_template(), source_lang, target_lang)
        contract = _fill(OUTPUT_CONTRACT, source_lang, target_lang)
        return f"{style}\n\n{contract}\n\nDo your best! I'm really counting on you."
