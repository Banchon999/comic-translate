"""Gender in Thai translations: ครับ/ค่ะ, pronouns and forms of address.

Thai marks the speaker's gender in nearly every polite sentence — the closing
particle (ครับ male, ค่ะ/คะ female) and the self-pronoun (ผม male, ดิฉัน
female) — and its third-person pronouns (เขา male, เธอ/นาง female) where the
Korean, Japanese and Chinese sources usually say nothing at all. A model left
to guess picks the particle of the narrator or the listener, mixes both in one
bubble, or calls a young master คุณหนู once and then copies it for the rest of
the series.

Adapted from the NovelTrans gender-particle system, which was built for
novels. Two things are different in a comic, and they decide what was kept:

- A speech bubble *is* one line of dialogue by one speaker, and the translator
  is shown the page, so the bubble's tail says who speaks. The novel system's
  quote-mark extraction and separate speaker-identification call have nothing
  to work on here; the rules tell the model to read the image instead.
- There are no dialogue tags ("…" Lina said), so a checker cannot tell who
  spoke a bubble from the text. Instead the translator reports it: into
  Thai, every block comes back as {"speaker", "gender", "translation"}
  (`speaker_output_rules`), read from the whole page or strip at once. The
  local check then flags a block whose ครับ/ค่ะ contradicts the gender the
  model itself gave, as well as what is wrong whoever said it: both genders'
  particles in one bubble, or a self-pronoun of one gender with the other's
  particle. It only warns — a person fixes the line.

Everything here is pure text and Qt-free.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_THAI = re.compile("[฀-๿]")


def is_thai(text: str) -> bool:
    return bool(_THAI.search(text or ""))


def targets_thai(target_lang: str) -> bool:
    """True when the (English) target language name is Thai."""
    return "thai" in (target_lang or "").lower()


# Forms of address


# A form of address follows the gender of the person ADDRESSED. Models file
# 도련님 (young master) as คุณหนู (young lady) often enough, and once it is in
# the glossary every later page copies it — so a fixed table overrides a
# wrong-gender Thai rendering wherever one is stored or sent.
_ADDRESS_GENDER = [
    (
        re.compile(
            r"^(도련님|공자님|도령님?|소공자님?|젊은 ?주인님|公子|少爷|少爺|少主|"
            r"お?坊っ?ちゃ[んま]|若様|若旦那|young (?:master|lord))$",
            re.IGNORECASE,
        ),
        re.compile("คุณหนู|คุณหญิง|ท่านหญิง|คุณนาย"),
        "คุณชาย",
    ),
    (
        re.compile(
            r"^(아가씨|영애님?|공녀님?|아씨|小姐|お嬢様|お嬢さま|お嬢さん|young lady|miss)$",
            re.IGNORECASE,
        ),
        re.compile("คุณชาย|ท่านชาย|นายน้อย"),
        "คุณหนู",
    ),
]


def fix_address_gender(source: str, target: str) -> str:
    """target, unless it gives a gendered form of address the wrong gender."""
    key = " ".join(str(source or "").split())
    for pattern, wrong, right in _ADDRESS_GENDER:
        if pattern.match(key) and wrong.search(str(target or "")):
            return right
    return target


# Glossary lines


# Spelling out what a gender *means* in Thai is what makes the model use it:
# "gender: female" alone still came back with ครับ.
_GENDER_DETAILS = {
    "male": ["gender: male/ชาย", "3rd→เขา/ของเขา", "1st→ผม/ข้า", "particle→ครับ/ขอรับ"],
    "female": ["gender: female/หญิง", "3rd→เธอ/นาง/ของเธอ", "1st→ฉัน/ดิฉัน/ข้า", "particle→ค่ะ/คะ"],
    "neutral": ["gender: neutral/กลาง"],
}


def thai_gender_details(gender: str) -> list[str]:
    """Prompt details for a character of this gender, when translating into Thai."""
    return list(_GENDER_DETAILS.get(gender or "", []))


# Translation rules


# Per source language: which forms of address are male and female, and what
# decides whether a bubble takes a polite particle at all. Examples from one
# language are never sent with another's source — a model handed Korean rules
# for a Chinese page applies them, and the translation gets worse.
_SOURCE_NOTES = {
    "Korean": (
        "도련님/공자님 → คุณชาย · 아가씨/영애 → คุณหนู · 부인 → ท่านหญิง/คุณนาย",
        "Korean speech level decides whether a bubble takes a polite particle at all: "
        "존댓말 (…요/…니다/…니까) → ครับ/ค่ะ; 반말 → no polite particle.",
    ),
    "Japanese": (
        "坊ちゃん/若様 → คุณชาย · お嬢様/お嬢さん → คุณหนู · 奥様 → คุณนาย",
        "Japanese politeness decides whether a bubble takes a polite particle at all: "
        "です/ます/ございます → ครับ/ค่ะ; plain form → no polite particle. "
        "Gendered speech (俺/僕/ぜ/ぞ male · あたし/わ/のよ female) is evidence of the speaker's "
        "gender; 私 proves nothing.",
    ),
    "Chinese": (
        "公子/少爷 → คุณชาย · 小姐/姑娘 → คุณหนู/แม่นาง",
        "Chinese has no polite particles: choose ครับ/ค่ะ (or none) from the speaker's "
        "gender and how formal the situation is.",
    ),
    "English": (
        "young master → คุณชาย · young lady/miss → คุณหนู",
        "English has no polite particles: choose ครับ/ค่ะ (or none) from the speaker's "
        "gender and how formal the situation is.",
    ),
}

_GENERIC_NOTES = (
    "a young master → คุณชาย · a young lady → คุณหนู",
    "Use ครับ/ค่ะ only where the source is polite; casual speech takes none.",
)


def _source_notes(source_lang: str) -> tuple[str, str]:
    lang = (source_lang or "").lower()
    for name, notes in _SOURCE_NOTES.items():
        if name.lower() in lang:
            return notes
    return _GENERIC_NOTES


SPEECH_PARTICLE_HEADER = "SPEECH PARTICLE RULES (คำลงท้าย ครับ/ค่ะ) — CRITICAL"


def speech_particle_rules(source_lang: str = "") -> str:
    """The rules block added to the system prompt when translating into Thai."""
    address, register = _source_notes(source_lang)
    return "\n".join([
        SPEECH_PARTICLE_HEADER,
        "• Thai polite particles follow the gender of the SPEAKER of that block — never the "
        "listener, never the narrator.",
        "• Each text block is normally one speech bubble, spoken by one character. Before "
        "translating it, work out who says it — the bubble's tail in the page image, the turn "
        "order of the conversation, who is being addressed, the glossary gender — then choose "
        "the particle and the self-pronoun.",
        "• Male speaker: ครับ / นะครับ / ครับผม / ขอรับ · Female speaker: ค่ะ / คะ / นะคะ / เจ้าค่ะ.",
        "• Royal register: male speaker → พ่ะย่ะค่ะ · female speaker → เพคะ.",
        "• NEVER mix male and female particles inside one block.",
        "• The particle must agree with the speaker's self-pronoun in the same block "
        "(ผม/กระผม → ครับ · ดิฉัน/อิฉัน → ค่ะ/คะ).",
        f"• {register}",
        "• If the speaker or their gender is unclear, do NOT guess: use a gender-neutral ending "
        "(นะ, จ้ะ, or no particle) instead.",
        "• Narration boxes, captions and sound effects take no polite particle.",
        f"• Forms of address follow the gender of the person ADDRESSED ({address}); never call "
        "a male character คุณหนู or a female one คุณชาย.",
        "• Third-person pronouns follow each character's gender: male → เขา · female → เธอ/นาง "
        "— check the glossary before every เขา/เธอ/นาง.",
        "• Second-person words follow the gender of the person ADDRESSED: นาย and แก (casual) "
        "only to a male; to a female say เธอ, หนู or her name — never call a girl นาย.",
        "• Each translation is final text: never correct yourself inside it "
        "(no \"…เอ๊ย…\" or \"I mean\"), and never show a rejected word.",
        "• If the source uses a wrong-gender pronoun for a character whose gender is in the "
        "glossary, follow the glossary gender — silently: never add notes, brackets or "
        "explanations to the translation.",
    ])


SPEAKER_OUTPUT_HEADER = "OUTPUT FORMAT — SPEAKER AND GENDER PER BLOCK"


def speaker_output_rules() -> str:
    """Into Thai: ask for each block's speaker and gender with its translation.

    This replaces the plain "return the JSON with the texts translated" shape
    for Thai only; `translator_utils.set_texts_from_json` reads both shapes.
    """
    return "\n".join([
        SPEAKER_OUTPUT_HEADER,
        "This overrides the output shape described above. Return a JSON object with the same "
        "keys; the value of every key is an object, not a string:",
        '{"block_0": {"speaker": "<who says it>", "gender": "male" | "female" | "unknown", '
        '"translation": "<the Thai translation>"}}',
        "• speaker: the character's name as written in the glossary when they are in it, "
        'otherwise a short description ("the guard", "Lena\'s mother"); "narration" for a '
        'caption box, "sfx" for a sound effect, "unknown" when you cannot tell who speaks.',
        "• Decide the speaker before translating, from the whole page: the bubble's tail in "
        "the image, the order of turns in the conversation, who is addressed, the glossary "
        "gender. The same character keeps the same name in every block.",
        "• gender is the SPEAKER's gender, and the particle and self-pronoun in translation "
        'must agree with it. "unknown" when you cannot tell — then use a gender-neutral '
        "ending. Narration and sound effects are always \"unknown\".",
        "• translation holds only the Thai text: no speaker names, notes or brackets in it.",
    ])


# Glossary extraction


_GENDER_EVIDENCE = {
    "Korean": (
        "그/그는/그가 → male, 그녀 → female; kinship or role words used FOR the person "
        "(형/오빠/아버지/아들/왕자 male · 언니/누나/어머니/딸/영애/하녀/공주 female). "
        "나/저 prove nothing."
    ),
    "Japanese": (
        "彼 → male, 彼女 → female; kinship or role words used FOR the person "
        "(兄/お兄ちゃん/父/息子/王子/坊ちゃん male · 姉/お姉ちゃん/母/娘/王女/姫/お嬢様 female); "
        "俺/僕 suggest male and あたし suggests female, weakly. 私 proves nothing."
    ),
    "Chinese": (
        "他 → male, 她 → female; 少年/公子/少爷/老者/师兄/父/子 male · "
        "少女/小姐/姑娘/仙子/师姐/师妹/母/女 female. 我 proves nothing."
    ),
    "English": (
        "he/him/his → male, she/her → female; Mr/Sir/Lord/King/Prince/Duke → male; "
        "Ms/Mrs/Miss/Lady/Queen/Princess/Duchess → female. I/me prove nothing."
    ),
}


def extraction_gender_rules(source_lang: str = "") -> str:
    """How the extraction prompt should decide a character's gender."""
    lang = (source_lang or "").lower()
    evidence = next(
        (text for name, text in _GENDER_EVIDENCE.items() if name.lower() in lang),
        "third-person pronouns, kinship and role words used FOR the person, titles.",
    )
    return (
        '"gender" is REQUIRED for type "character": male | female | neutral. '
        "Evidence, strongest first: " + evidence + " "
        "How others address them also counts. A speaker's own self-pronoun proves nothing. "
        'If you are not sure, answer "neutral" — a wrong gender is copied into every later '
        "page, an unknown one is not."
    )


# Local check


# A particle ends a word: followed by the end, a space, punctuation or ๆ.
# This is what keeps "คะแนน" (score) from counting as the particle คะ and
# "ขอรับเงิน" (to receive money) from counting as ขอรับ.
_P_END = r"(?=$|[\s!?.…,~ๆ\-—)”\"’」』])"
_ROYAL_MALE = "\u0001"
_MALE_PARTICLE = re.compile(r"(ครับ(?:ผม)?|ขอรับ|" + _ROYAL_MALE + ")" + _P_END)
_FEMALE_PARTICLE = re.compile(r"(เจ้าค่ะ|ค่ะ|คะ|เพคะ)" + _P_END)
# พ่ะย่ะค่ะ is what a man says to royalty, and it ends in ค่ะ.
_ROYAL_MALE_WORDS = re.compile("พ่ะย่ะค่ะ|พะยะค่ะ")
# ผม is also hair: "ผมยาว" (long hair) in a woman's line is not a self-pronoun.
_HAIR_WORDS = re.compile(
    "เส้นผม|ทรงผม|สระผม|หวีผม|ผมเผ้า|ผมยาว|ผมสั้น|ผมสี|ปอยผม|มัดผม|ผมหงอก|ผมดำ|"
    "ผมทอง|ผมขาว|ผมแดง|ผมเงิน|ไรผม|โคนผม|ปลายผม|เกล้าผม|ถักผม|ตัดผม|ทำผม"
)
_MALE_SELF = re.compile(r"(^|[\s“\"「『‘(])(กระผม|ผม)")
_FEMALE_SELF = re.compile("ดิฉัน|อิฉัน")
_NEUTRAL_SELF = re.compile("ฉัน|หนู")

MIXED = "mixed"
MALE_SELF_FEMALE_PARTICLE = "male_self_female_particle"
FEMALE_SELF_MALE_PARTICLE = "female_self_male_particle"
# The translator said who speaks, and the line contradicts that gender.
FEMALE_SPEAKER_MALE_SPEECH = "female_speaker_male_speech"
MALE_SPEAKER_FEMALE_SPEECH = "male_speaker_female_speech"


@dataclass
class SpeechIssue:
    """A translated block whose gendered words contradict each other, or
    contradict the speaker's gender as the translator reported it."""

    block_index: int
    seen_as: str  # the translation, as it stands
    reason: str   # one of the reason constants above
    speaker: str = ""  # who the translator said speaks, for the speaker reasons


def particle_counts(text: str) -> tuple[int, int]:
    """(male, female) polite particles in text."""
    t = _ROYAL_MALE_WORDS.sub(_ROYAL_MALE, text or "")
    return len(_MALE_PARTICLE.findall(t)), len(_FEMALE_PARTICLE.findall(t))


def speech_problem(text: str) -> str | None:
    """Why a block's ครับ/ค่ะ cannot be right whoever says it, or None.

    Only contradictions inside the block are reported. Whether ครับ suits the
    character who speaks it needs to know who that is, and a bubble does not
    say; a warning that is often wrong teaches people to ignore all of them.
    """
    male, female = particle_counts(text)
    if not male and not female:
        return None
    if male and female:
        return MIXED
    t = _HAIR_WORDS.sub(lambda m: "#" * len(m.group(0)), text)
    male_self = bool(_MALE_SELF.search(t))
    female_self = bool(_FEMALE_SELF.search(t))
    if female and male_self and not female_self and not _NEUTRAL_SELF.search(t):
        return MALE_SELF_FEMALE_PARTICLE
    if male and female_self and not male_self:
        return FEMALE_SELF_MALE_PARTICLE
    return None


def speaker_problem(text: str, gender: str) -> str | None:
    """Why a block's gendered speech contradicts its speaker's gender, or None.

    gender is what the translator reported ("male"/"female"; anything else
    means unknown and is never judged). A polite particle of the other gender
    counts, and so does a self-pronoun that only one gender uses (กระผม,
    ดิฉัน, อิฉัน). Plain ผม does not: without a particle beside it, it is as
    likely to be hair ("ผมเปียก") as "I".
    """
    if gender not in ("male", "female"):
        return None
    male, female = particle_counts(text)
    t = text or ""
    if gender == "female" and (male or "กระผม" in t):
        return FEMALE_SPEAKER_MALE_SPEECH
    if gender == "male" and (female or _FEMALE_SELF.search(t)):
        return MALE_SPEAKER_FEMALE_SPEECH
    return None


def check_speech(blk_list) -> list[SpeechIssue]:
    """Blocks whose Thai translation mixes male and female speech markers, or
    whose markers contradict the speaker's gender the translator reported.

    A block contradicting itself is reported as that and nothing else: which
    half is wrong is the question, and the speaker's gender only answers it
    when someone reads the line.
    """
    issues: list[SpeechIssue] = []
    for index, blk in enumerate(blk_list):
        translation = getattr(blk, "translation", "") or ""
        seen_as = " ".join(translation.split())
        reason = speech_problem(translation)
        if reason:
            issues.append(SpeechIssue(index, seen_as, reason))
            continue
        reason = speaker_problem(translation, getattr(blk, "speaker_gender", "") or "")
        if reason:
            issues.append(SpeechIssue(index, seen_as, reason, getattr(blk, "speaker", "") or ""))
    return issues
