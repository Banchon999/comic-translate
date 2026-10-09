import os
import re
import csv
import json
import logging
import unicodedata
from dataclasses import dataclass, asdict

from .paths import get_user_data_dir
from .thai_speech import SpeechIssue, fix_address_gender, is_thai, thai_gender_details

logger = logging.getLogger(__name__)

# Preset entry types; users can also type their own custom types.
GLOSSARY_PRESET_TYPES = ["term", "character", "place", "skill", "item", "organization"]
GLOSSARY_GENDERS = ["", "male", "female", "neutral"]

# Accepted aliases when importing files from other tools
# (e.g. Novel-Tran-Pro exports use korean/thai keys).
_SOURCE_KEYS = ("source", "korean", "src", "original")
_TARGET_KEYS = ("target", "thai", "tgt", "translation", "translated")

# Zero-width and bidirectional marks. OCR picks these up from stylised
# lettering and they are invisible on screen, so a term carrying one looks
# exactly like the same term without it.
_INVISIBLE = re.compile(r"[​-‏‪-‮⁠﻿]")


def normalize_term(text: str) -> str:
    """Clean up a term for storage: the visible form, with nothing hidden in it.

    NFC matters most for Korean. Hangul has two encodings — one code point per
    composed syllable, or a sequence of jamo — and they render identically
    while comparing unequal. OCR engines and other tools disagree about which
    to emit, so without this the same name is stored twice and neither the
    glossary nor the prompt matcher can tell.
    """
    if not text:
        return ""
    cleaned = _INVISIBLE.sub("", unicodedata.normalize("NFC", str(text)))
    # Collapse NBSP, ideographic space and runs of whitespace to one space.
    return " ".join(cleaned.split())


def term_key(text: str) -> str:
    """The identity of a term — what makes two entries the same entry.

    Case-folded on top of normalize_term, so "Gate" and "gate" are one term.
    Case folding is a no-op for Korean, Japanese, Chinese and Thai, so this
    only ever merges Latin-script variants, which is what you want.
    """
    return normalize_term(text).casefold()


@dataclass
class GlossaryEntry:
    source: str
    target: str
    type: str = "term"
    gender: str = ""  # male / female / neutral, only meaningful for characters
    note: str = ""

    @property
    def key(self) -> str:
        """What this entry is, for comparison against other entries."""
        return term_key(self.source)

    @property
    def prompt_target(self) -> str:
        """The translation to ask for: target, with a wrong-gender form of address fixed."""
        return fix_address_gender(self.source, self.target)

    @property
    def needs_gender(self) -> bool:
        """A character whose gender nobody has decided yet."""
        return self.type == "character" and self.gender not in ("male", "female", "neutral")

    @classmethod
    def from_dict(cls, data: dict) -> "GlossaryEntry | None":
        source = normalize_term(next((data[k] for k in _SOURCE_KEYS if data.get(k)), ""))
        target = normalize_term(next((data[k] for k in _TARGET_KEYS if data.get(k)), ""))
        if not source or not target:
            return None
        gender = str(data.get("gender", "") or "").strip().lower()
        if gender not in GLOSSARY_GENDERS:
            gender = ""
        return cls(
            source=source,
            target=target,
            type=str(data.get("type", "") or "term").strip() or "term",
            gender=gender,
            note=str(data.get("note", "") or "").strip(),
        )


# Separates text blocks in collect_source_text. It is whitespace to Python but
# the matcher treats it as a wall, so a name never "appears" spliced together
# from the end of one speech bubble and the start of the next.
BLOCK_BREAK = " "

# Scripts written without spaces between words (or whose OCR spacing cannot be
# trusted): Hangul, CJK ideographs, kana, Thai. A term containing any of them
# is matched with every space removed from both sides — PaddleOCR joins the
# lines of a wrapped Korean name with a space, manga-ocr may drop one from a
# title, and either way it is still the same word.
_SPACELESS = re.compile(
    "[฀-๿ᄀ-ᇿ぀-ヿ㄰-㆏ㇰ-ㇿ"
    "㐀-䶿一-鿿가-힣ｦ-ﾟ]"
)

# Fuzzy matching (one wrong, missing or extra character) is how a term OCR
# misread still reaches the prompt. Below these lengths a single edit turns
# almost any word into almost any other, so short terms match exactly only.
FUZZY_MIN_SPACELESS = 3  # a 3-syllable Korean name tolerates one misread syllable
FUZZY_MIN_LATIN = 5      # "Kai"/"Kat", "gate"/"late": Latin needs more letters

# Korean particles. A two-syllable word plus a particle looks exactly like a
# three-syllable term with its last syllable misread ("그림을" = picture +
# object marker, one edit from "그림자"), so a fuzzy window whose only
# difference is a particle in the last place is a different word, not a typo.
_KOREAN_PARTICLES = set("은는이가을를의에도와과로만야아께랑")


def compact_key(text: str) -> str:
    """term_key with every space removed, for scripts where spacing is noise."""
    return "".join(term_key(text).split())


def _is_spaceless_term(key: str) -> bool:
    return bool(_SPACELESS.search(key))


def _is_letter(ch: str) -> bool:
    return unicodedata.category(ch)[0] in "LMN"


def _is_word_char(ch: str) -> bool:
    """A character that would continue a Latin word (for word boundaries).

    Hangul and CJK next to a Latin term do not count, so "HP를" still
    contains the term "HP".
    """
    return _is_letter(ch) and not _SPACELESS.match(ch)


def _squash(text: str) -> str:
    """Letters and digits only, case-folded: how a target is found in a translation.

    Thai is written without spaces and translators disagree about hyphens in
    romanised names, so neither may decide whether a term was used.
    """
    return "".join(ch for ch in term_key(text) if _is_letter(ch))


def _within_one_edit(a: str, b: str) -> bool:
    """True when a and b differ by at most one substitution, insertion or deletion."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = 0
    while i < la and a[i] == b[i]:
        i += 1
    if la == lb:
        return a[i + 1:] == b[i + 1:]
    return a[i:] == b[i + 1:]


class _IndexedText:
    """Source text keyed for matching, with every keyed character mapped back.

    Matching happens on a case-folded (and, for spaceless scripts, space-free)
    copy; the map lets a match be reported as the text it was actually found
    in, which is what the prompt and the test box show the user.
    """

    def __init__(self, text: str, drop_spaces: bool):
        self.display = _INVISIBLE.sub("", unicodedata.normalize("NFC", text or ""))
        chars: list[str] = []
        self.index: list[int] = []
        pending_space = False
        for i, ch in enumerate(self.display):
            if ch == BLOCK_BREAK:
                chars.append(BLOCK_BREAK)
                self.index.append(i)
                pending_space = False
                continue
            if ch.isspace():
                pending_space = not drop_spaces and bool(chars)
                continue
            if pending_space:
                chars.append(" ")
                self.index.append(i)
                pending_space = False
            for folded in ch.casefold():
                chars.append(folded)
                self.index.append(i)
        self.keyed = "".join(chars)

    def span(self, start: int, end: int) -> tuple[int, int]:
        """Display-text span of keyed[start:end]."""
        return self.index[start], self.index[end - 1] + 1

    def seen_as(self, span: tuple[int, int]) -> str:
        return " ".join(self.display[span[0]:span[1]].split())


@dataclass
class GlossaryMatch:
    """A glossary term found in a piece of source text."""

    entry: GlossaryEntry
    seen_as: str          # the text as it actually appears (OCR spelling)
    fuzzy: bool = False   # found with one character wrong, missing or extra
    suppressed_by: "GlossaryEntry | None" = None  # only ever inside a longer term


@dataclass
class GlossaryIssue:
    """A translated block whose source uses a term the translation left out."""

    block_index: int
    source_term: str
    expected: str
    seen_as: str


@dataclass
class GlossaryOverlap:
    """Two terms where one is contained in the other.

    Harmless when the translations agree ("철수" -> "ชอลซู" inside
    "김철수" -> "คิมชอลซู"); when they do not, the shorter one would
    contradict the longer wherever both apply.
    """

    short: GlossaryEntry
    long: GlossaryEntry
    consistent: bool


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _exact_spans(key: str, spaceless: bool, text: "_IndexedText") -> list[tuple[int, int]]:
    """Display spans of every occurrence of a term's key in the text."""
    needle = "".join(key.split()) if spaceless else key
    if not needle:
        return []
    hay = text.keyed
    spans = []
    start = hay.find(needle)
    while start != -1:
        end = start + len(needle)
        if spaceless or (
            (start == 0 or not _is_word_char(hay[start - 1]))
            and (end == len(hay) or not _is_word_char(hay[end]))
        ):
            spans.append(text.span(start, end))
        start = hay.find(needle, start + 1)
    return spans


def _fuzzy_spans_spaceless(
    needle: str, text: "_IndexedText", may_span_spaces: bool = False
) -> list[tuple[int, int]]:
    """Windows of the space-free text within one edit of the needle.

    One edit leaves either the first or the second half of the term intact, so
    only windows around an exact occurrence of a half are ever compared.

    A window reaching across a space is only a candidate when the term itself
    has one. Exact matching ignores spaces because a wrapped line puts one
    inside a name; a fuzzy match across one as well would make "김철 부장"
    (Manager Kim Cheol) a misreading of "김철수".
    """
    hay = text.keyed
    m = len(needle)
    half = m // 2
    left, right = needle[:half], needle[half:]
    # A window one shorter than a 3-character term is a 2-character word, and
    # a 2-character word is exactly what fuzzy matching must never accept.
    lengths = (m, m + 1, m - 1) if m - 1 >= FUZZY_MIN_SPACELESS else (m, m + 1)
    starts: set[tuple[int, int]] = set()
    pos = hay.find(left)
    while pos != -1:
        for n in lengths:
            starts.add((pos, n))
        pos = hay.find(left, pos + 1)
    pos = hay.find(right)
    while pos != -1:
        end = pos + len(right)
        for n in lengths:
            starts.add((end - n, n))
        pos = hay.find(right, pos + 1)
    found = []
    for start, n in sorted(starts, key=lambda s: (lengths.index(s[1]), s[0])):
        end = start + n
        if start < 0 or end > len(hay):
            continue
        window = hay[start:end]
        # A misread letter is still a letter: a window reaching into
        # punctuation or the next bubble is not the term.
        if not all(_is_letter(ch) for ch in window):
            continue
        if (
            n == m and window[:-1] == needle[:-1]
            and window[-1] in _KOREAN_PARTICLES and needle[-1] not in _KOREAN_PARTICLES
        ):
            continue
        if not _within_one_edit(window, needle):
            continue
        span = text.span(start, end)
        if not may_span_spaces and any(ch.isspace() for ch in text.display[span[0]:span[1]]):
            continue
        found.append(span)
    return found


def _fuzzy_spans_latin(key: str, text: "_IndexedText") -> list[tuple[int, int]]:
    """Whole-word windows (as many words as the term) within one edit of it."""
    hay = text.keyed
    words: list[tuple[int, int]] = []
    i = 0
    while i < len(hay):
        if _is_word_char(hay[i]):
            j = i
            while j < len(hay) and _is_word_char(hay[j]):
                j += 1
            words.append((i, j))
            i = j
        else:
            i += 1
    n = len(key.split())
    found = []
    for w in range(len(words) - n + 1):
        group = words[w:w + n]
        window = " ".join(hay[s:e] for s, e in group)
        if _within_one_edit(window, key):
            found.append(text.span(group[0][0], group[-1][1]))
    return found


def collect_source_text(blk_list) -> str:
    """Join the raw text of text blocks for glossary term matching."""
    return BLOCK_BREAK.join(blk.text for blk in blk_list if getattr(blk, "text", ""))


class GlossaryManager:
    """Stores the user's term glossaries and formats them for LLM prompts.

    Glossaries are organized as named profiles — one per series/story —
    each persisted as its own JSON file under ``<user data>/glossaries/``.
    A small ``config.json`` in the same directory remembers the active
    profile and the global enable/match options. A legacy single-file
    ``glossary.json`` is migrated into the "Default" profile automatically.
    """

    DEFAULT_PROFILE = "Default"
    _META_FILE = "config.json"

    def __init__(self, base_dir: str | None = None):
        self.base_dir = base_dir or os.path.join(get_user_data_dir(), "glossaries")
        self.enabled: bool = True
        self.match_only: bool = True  # only send terms found in the source text
        self.log_ocr: bool = True  # keep OCR'd text for glossary extraction
        self.batch_extract: bool = False  # batch mode: OCR all pages + extract before translating
        # Extract terms from each page as soon as it is recognised, so the
        # glossary fills in while you work instead of only at the end.
        self.auto_extract: bool = False
        self.active_profile: str = self.DEFAULT_PROFILE
        self.entries: list[GlossaryEntry] = []
        self._load_meta_and_migrate()
        self.load()

    # Profiles

    @staticmethod
    def _safe_filename(name: str) -> str:
        cleaned = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
        return cleaned or GlossaryManager.DEFAULT_PROFILE

    @property
    def _meta_path(self) -> str:
        return os.path.join(self.base_dir, self._META_FILE)

    @property
    def file_path(self) -> str:
        return self._profile_path(self.active_profile)

    def _profile_path(self, name: str) -> str:
        return os.path.join(self.base_dir, f"{self._safe_filename(name)}.json")

    def list_profiles(self) -> list[str]:
        names = set()
        if os.path.isdir(self.base_dir):
            for file_name in os.listdir(self.base_dir):
                if file_name.endswith(".json") and file_name != self._META_FILE:
                    names.add(file_name[:-len(".json")])
        names.add(self.active_profile)
        return sorted(names)

    def switch_profile(self, name: str) -> None:
        name = self._safe_filename(name)
        if name == self.active_profile:
            return
        self.active_profile = name
        self._save_meta()
        self.entries = []
        self.load()

    def create_profile(self, name: str) -> None:
        name = self._safe_filename(name)
        if os.path.exists(self._profile_path(name)):
            self.switch_profile(name)
            return
        self.active_profile = name
        self.entries = []
        self.save()

    def rename_profile(self, new_name: str) -> None:
        new_name = self._safe_filename(new_name)
        if new_name == self.active_profile:
            return
        old_path, new_path = self.file_path, self._profile_path(new_name)
        if os.path.exists(old_path) and not os.path.exists(new_path):
            os.rename(old_path, new_path)
        self.active_profile = new_name
        self.save()

    def delete_profile(self) -> None:
        try:
            if os.path.exists(self.file_path):
                os.remove(self.file_path)
        except OSError as e:
            logger.error(f"Failed to delete glossary profile {self.active_profile}: {e}")
        remaining = [p for p in self.list_profiles() if p != self.active_profile]
        self.active_profile = remaining[0] if remaining else self.DEFAULT_PROFILE
        self._save_meta()
        self.entries = []
        self.load()

    # Persistence

    def _load_meta_and_migrate(self) -> None:
        if os.path.exists(self._meta_path):
            try:
                with open(self._meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                self.enabled = bool(meta.get("enabled", True))
                self.match_only = bool(meta.get("match_only", True))
                self.log_ocr = bool(meta.get("log_ocr", True))
                self.batch_extract = bool(meta.get("batch_extract", False))
                self.auto_extract = bool(meta.get("auto_extract", False))
                self.active_profile = self._safe_filename(
                    str(meta.get("active_profile", self.DEFAULT_PROFILE))
                )
            except Exception as e:
                logger.error(f"Failed to load glossary config: {e}")
            return

        # First run of the profile system: migrate the legacy single file.
        legacy_path = os.path.join(os.path.dirname(self.base_dir), "glossary.json")
        if os.path.exists(legacy_path):
            try:
                with open(legacy_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.enabled = bool(data.get("enabled", True))
                self.match_only = bool(data.get("match_only", True))
                self.entries = self._parse_entries(data.get("entries", []))
                self.save()
                logger.info("Migrated legacy glossary.json into the Default profile")
            except Exception as e:
                logger.error(f"Failed to migrate legacy glossary: {e}")
        else:
            self._save_meta()

    def _save_meta(self) -> None:
        try:
            os.makedirs(self.base_dir, exist_ok=True)
            with open(self._meta_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "enabled": self.enabled,
                        "match_only": self.match_only,
                        "log_ocr": self.log_ocr,
                        "batch_extract": self.batch_extract,
                        "auto_extract": self.auto_extract,
                        "active_profile": self.active_profile,
                    },
                    f, ensure_ascii=False, indent=2,
                )
        except Exception as e:
            logger.error(f"Failed to save glossary config: {e}")

    def load(self) -> None:
        if not os.path.exists(self.file_path):
            return
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load glossary from {self.file_path}: {e}")
            return
        self.entries = self._parse_entries(data.get("entries", []))

    def save(self) -> None:
        try:
            os.makedirs(self.base_dir, exist_ok=True)
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"entries": [asdict(e) for e in self.entries]},
                    f, ensure_ascii=False, indent=2,
                )
        except Exception as e:
            logger.error(f"Failed to save glossary to {self.file_path}: {e}")
        self._save_meta()

    # Editing

    def find(self, source: str) -> GlossaryEntry | None:
        key = term_key(source)
        return next((e for e in self.entries if e.key == key), None)

    def keys(self) -> set[str]:
        """Identities of every stored term, for callers filtering candidates."""
        return {e.key for e in self.entries}

    def upsert(self, entry: GlossaryEntry, original_source: str | None = None, save: bool = True) -> None:
        """Add an entry, or replace the one it edits/duplicates."""
        if original_source and term_key(original_source) != entry.key:
            old_key = term_key(original_source)
            self.entries = [e for e in self.entries if e.key != old_key]
        existing = self.find(entry.source)
        if existing:
            self.entries[self.entries.index(existing)] = entry
        else:
            self.entries.append(entry)
        if save:
            self.save()

    def remove(self, sources: list[str]) -> int:
        before = len(self.entries)
        keys = {term_key(s) for s in sources}
        self.entries = [e for e in self.entries if e.key not in keys]
        removed = before - len(self.entries)
        if removed:
            self.save()
        return removed

    def deduplicate(self) -> int:
        """Collapse entries that are the same term, keeping the first of each.

        Glossaries built before terms were normalised can hold the same name
        twice — most often a Korean name stored once composed and once
        decomposed. Returns how many entries were dropped.
        """
        seen: set[str] = set()
        kept: list[GlossaryEntry] = []
        for entry in self.entries:
            if entry.key in seen:
                continue
            seen.add(entry.key)
            entry.source = normalize_term(entry.source)
            entry.target = normalize_term(entry.target)
            kept.append(entry)
        removed = len(self.entries) - len(kept)
        self.entries = kept
        if removed:
            self.save()
        return removed

    def types_in_use(self) -> list[str]:
        seen = list(GLOSSARY_PRESET_TYPES)
        for e in self.entries:
            if e.type and e.type not in seen:
                seen.append(e.type)
        return seen

    # Extraction

    def extraction_skip_sources(self) -> set[str]:
        """Terms an extraction run must not report again.

        Characters with no gender yet are left off on purpose: the model then
        reports them again, with a gender, and merge_extracted fills it in.
        """
        return {e.source for e in self.entries if e.source and not e.needs_gender}

    def merge_extracted(self, entries: list[GlossaryEntry], save: bool = True) -> tuple[int, int]:
        """Add extracted terms; returns (added, characters whose gender was filled in).

        A term already in the glossary is never overwritten — the user may have
        corrected it — except to give a gender-less character the male or
        female the model found. "neutral" fills nothing: it means the model
        could not tell either, so the next run may ask again.
        """
        added = filled = 0
        for entry in entries:
            if not entry.key:
                continue
            existing = self.find(entry.source)
            if existing is not None:
                if (
                    existing.needs_gender and entry.type == "character"
                    and entry.gender in ("male", "female")
                ):
                    existing.gender = entry.gender
                    filled += 1
                continue
            if entry.type != "character":
                entry.gender = ""
            entry.target = fix_address_gender(entry.source, entry.target)
            self.entries.append(entry)
            added += 1
        if save and (added or filled):
            self.save()
        return added, filled

    # OCR log (per profile) — raw source text collected during OCR so a
    # glossary can later be extracted from it by an LLM.

    def ocr_log_path(self) -> str:
        return os.path.join(
            self.base_dir, f"{self._safe_filename(self.active_profile)}.ocrlog.txt"
        )

    def append_ocr_log(self, texts: list[str]) -> None:
        if not self.log_ocr:
            return
        lines = [t.strip().replace("\n", " ") for t in texts if t and t.strip()]
        if not lines:
            return
        try:
            os.makedirs(self.base_dir, exist_ok=True)
            with open(self.ocr_log_path(), "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except OSError as e:
            logger.error(f"Failed to append OCR log: {e}")

    def read_ocr_log(self, max_chars: int = 60000) -> str:
        path = self.ocr_log_path()
        if not os.path.exists(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            logger.error(f"Failed to read OCR log: {e}")
            return ""
        # Keep the most recent portion when the log grows very large.
        return content[-max_chars:] if len(content) > max_chars else content

    def ocr_log_line_count(self) -> int:
        path = self.ocr_log_path()
        if not os.path.exists(path):
            return 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
        except OSError:
            return 0

    def clear_ocr_log(self) -> None:
        try:
            if os.path.exists(self.ocr_log_path()):
                os.remove(self.ocr_log_path())
        except OSError as e:
            logger.error(f"Failed to clear OCR log: {e}")

    # Import / Export

    @staticmethod
    def _parse_entries(raw_entries) -> list[GlossaryEntry]:
        entries: list[GlossaryEntry] = []
        for raw in raw_entries or []:
            if isinstance(raw, dict):
                entry = GlossaryEntry.from_dict(raw)
                if entry:
                    entries.append(entry)
        return entries

    def _merge(self, new_entries: list[GlossaryEntry]) -> int:
        count = 0
        for entry in new_entries:
            self.upsert(entry, save=False)
            count += 1
        if count:
            self.save()
        return count

    def import_json(self, path: str) -> int:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Accept either a bare list of entries or a full glossary file
        raw = data.get("entries", data.get("glossary", [])) if isinstance(data, dict) else data
        return self._merge(self._parse_entries(raw))

    def import_csv(self, path: str) -> int:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            sample_row = next(csv.reader(f), None)
            if sample_row is None:
                return 0
            f.seek(0)
            cells = [cell.strip().lower() for cell in sample_row]
            has_header = (
                any(cell in _SOURCE_KEYS for cell in cells)
                and any(cell in _TARGET_KEYS for cell in cells)
            )
            if has_header:
                rows = list(csv.DictReader(f))
            else:
                fields = ["source", "target", "type", "gender", "note"]
                rows = [
                    dict(zip(fields, row))
                    for row in csv.reader(f) if row
                ]
        return self._merge(self._parse_entries(rows))

    def export_json(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"entries": [asdict(e) for e in self.entries]}, f, ensure_ascii=False, indent=2)

    def export_csv(self, path: str) -> None:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["source", "target", "type", "gender", "note"])
            for e in self.entries:
                writer.writerow([e.source, e.target, e.type, e.gender, e.note])

    # Matching

    def match_report(self, source_text: str) -> list[GlossaryMatch]:
        """Every term found in source_text, including ones a longer term hides.

        Exact matches come first, longest term first, and claim the text they
        cover: "철수" inside "김철수" is reported as suppressed rather than
        sent, because when the two translations differ the shorter one would
        contradict the longer. Only terms with no exact match are then tried
        fuzzily (one wrong, missing or extra character).

        A fuzzy match may take over text a shorter exact term claimed only
        when it agrees with the text on at least one letter beyond that term.
        "그림자군쥬" is the misread title "그림자 군주" (그림자 + 군 agree), and
        "그림자" inside it is suppressed; "박철수" is not "김철수" when the
        glossary knows "철수", since everything they share is "철수" itself.
        """
        entries = [e for e in self.entries if e.key]
        if not entries or not source_text:
            return []
        spaced = _IndexedText(source_text, drop_spaces=False)
        compact = _IndexedText(source_text, drop_spaces=True)

        def text_for(entry):
            return compact if _is_spaceless_term(entry.key) else spaced

        def letters(entry_or_text) -> int:
            if isinstance(entry_or_text, GlossaryEntry):
                return len(compact_key(entry_or_text.source))
            return len(compact_key(entry_or_text))

        order = sorted(entries, key=letters, reverse=True)
        # entry key -> (entry, spans it holds); what a match "claims".
        claims: dict[str, tuple[GlossaryEntry, list[tuple[int, int]]]] = {}
        results: dict[str, GlossaryMatch] = {}

        def holder_of(span):
            for key, (entry, spans) in claims.items():
                for held in spans:
                    if _overlaps(span, held):
                        return key, entry, held
            return None

        unmatched: list[GlossaryEntry] = []
        for entry in order:
            text = text_for(entry)
            spans = _exact_spans(entry.key, _is_spaceless_term(entry.key), text)
            if not spans:
                unmatched.append(entry)
                continue
            free = [sp for sp in spans if holder_of(sp) is None]
            if free:
                results[entry.key] = GlossaryMatch(entry, text.seen_as(free[0]))
                claims[entry.key] = (entry, free)
            else:
                blocker = holder_of(spans[0])[1]
                results[entry.key] = GlossaryMatch(
                    entry, text.seen_as(spans[0]), suppressed_by=blocker
                )

        exact_keys = set(claims)
        for entry in unmatched:
            needle = compact_key(entry.source)
            if _is_spaceless_term(entry.key):
                if len(needle) < FUZZY_MIN_SPACELESS:
                    continue
                spans = _fuzzy_spans_spaceless(
                    needle, compact, may_span_spaces=" " in normalize_term(entry.source)
                )
                text = compact
            else:
                if len(needle) < FUZZY_MIN_LATIN:
                    continue
                spans = _fuzzy_spans_latin(entry.key, spaced)
                text = spaced
            for span in spans:
                window_letters = letters(text.seen_as(span))
                overlapping = [
                    (key, held)
                    for key, (_, held_spans) in claims.items()
                    for held in held_spans
                    if _overlaps(span, held)
                ]
                if any(
                    key not in exact_keys
                    or not (span[0] <= held[0] and held[1] <= span[1])
                    or window_letters - letters(claims[key][0]) < 2
                    for key, held in overlapping
                ):
                    continue
                for key, held in overlapping:
                    held_entry, held_spans = claims[key]
                    held_spans.remove(held)
                    if not held_spans:
                        results[key] = GlossaryMatch(
                            held_entry, results[key].seen_as, suppressed_by=entry
                        )
                results[entry.key] = GlossaryMatch(entry, text.seen_as(span), fuzzy=True)
                claims[entry.key] = (entry, [span])
                break
        return [results[e.key] for e in order if e.key in results]

    def find_matches(self, source_text: str) -> list[GlossaryMatch]:
        """The terms that apply to source_text: match_report minus suppressed ones."""
        return [m for m in self.match_report(source_text) if m.suppressed_by is None]

    def check_translation(self, blk_list) -> list[GlossaryIssue]:
        """Blocks whose source uses a term and whose translation leaves it out.

        Only exact matches are checked. A fuzzy match is a guess about what
        OCR meant; warning about it would cry wolf on every near-miss word.
        """
        if not self.enabled or not self.entries:
            return []
        issues: list[GlossaryIssue] = []
        for index, blk in enumerate(blk_list):
            source = getattr(blk, "text", "") or ""
            translation = _squash(getattr(blk, "translation", "") or "")
            if not source.strip() or not translation:
                continue
            for match in self.find_matches(source):
                if match.fuzzy:
                    continue
                expected = _squash(match.entry.prompt_target)
                if expected and expected not in translation:
                    issues.append(GlossaryIssue(
                        block_index=index,
                        source_term=match.entry.source,
                        expected=match.entry.prompt_target,
                        seen_as=match.seen_as,
                    ))
        return issues

    def find_overlaps(self) -> list[GlossaryOverlap]:
        """Pairs of terms where the shorter appears inside the longer."""
        entries = [e for e in self.entries if e.key]
        info = []
        for e in entries:
            spaceless = _is_spaceless_term(e.key)
            info.append((e, compact_key(e.source), spaceless))
        info.sort(key=lambda item: len(item[1]))
        overlaps: list[GlossaryOverlap] = []
        for i, (short, short_compact, spaceless) in enumerate(info):
            for long, long_compact, long_spaceless in info[i + 1:]:
                # Cheap necessary condition first: containment of the key
                # implies containment with the spaces taken out.
                if short_compact not in long_compact:
                    continue
                text = _IndexedText(long.source, drop_spaces=spaceless)
                if _exact_spans(short.key, spaceless, text):
                    short_target = _squash(short.target)
                    consistent = bool(short_target) and short_target in _squash(long.target)
                    overlaps.append(GlossaryOverlap(short, long, consistent))
        return overlaps

    # Prompt building

    def build_prompt(self, source_text: str = "") -> str:
        """Format glossary entries as an instruction block for LLM translators.

        When match_only is set and source_text is given, only terms that
        actually appear in the text are included to keep prompts small.
        """
        if not self.enabled or not self.entries:
            return ""

        if self.match_only and source_text:
            matches = self.find_matches(source_text)
        else:
            matches = [GlossaryMatch(e, e.source) for e in self.entries]
        if not matches:
            return ""

        lines = [
            "Glossary (mandatory): translate each of these terms exactly as given, "
            "even where different wording would sound more natural."
        ]
        for m in matches:
            e = m.entry
            target = e.prompt_target
            details = []
            if e.type and e.type != "term":
                details.append(e.type)
            if e.gender and e.type == "character":
                # Into Thai, a gender decides the particle and both pronouns,
                # and the model only uses it reliably when told which ones.
                if is_thai(target):
                    details.extend(thai_gender_details(e.gender))
                else:
                    details.append(f"gender: {e.gender}")
            if e.note:
                details.append(e.note)
            suffix = f" ({'; '.join(details)})" if details else ""
            if m.fuzzy:
                suffix += (
                    f" [the text has «{m.seen_as}», probably this term misread by OCR;"
                    " apply only if it means this term]"
                )
            lines.append(f"- {e.source} => {target}{suffix}")
        return "\n".join(lines)


def issue_rows(issues) -> list[tuple]:
    """Translation warnings as the batch report stores them.

    A glossary term is (as seen, expected); a speech issue is
    (translation, reason, "speech", speaker), since it has no single expected
    word — speaker is who the translator said speaks, or "".
    """
    rows: list[tuple] = []
    for issue in issues or []:
        if isinstance(issue, SpeechIssue):
            rows.append((issue.seen_as, issue.reason, "speech", issue.speaker))
        else:
            rows.append((issue.seen_as or issue.source_term, issue.expected))
    return rows
