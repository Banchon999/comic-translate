import os
import csv
import json
import logging
from dataclasses import dataclass, asdict

from .paths import get_user_data_dir

logger = logging.getLogger(__name__)

# Preset entry types; users can also type their own custom types.
GLOSSARY_PRESET_TYPES = ["term", "character", "place", "skill", "item", "organization"]
GLOSSARY_GENDERS = ["", "male", "female", "neutral"]

# Accepted aliases when importing files from other tools
# (e.g. Novel-Tran-Pro exports use korean/thai keys).
_SOURCE_KEYS = ("source", "korean", "src", "original")
_TARGET_KEYS = ("target", "thai", "tgt", "translation", "translated")


@dataclass
class GlossaryEntry:
    source: str
    target: str
    type: str = "term"
    gender: str = ""  # male / female / neutral, only meaningful for characters
    note: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "GlossaryEntry | None":
        source = next((str(data[k]).strip() for k in _SOURCE_KEYS if data.get(k)), "")
        target = next((str(data[k]).strip() for k in _TARGET_KEYS if data.get(k)), "")
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


def collect_source_text(blk_list) -> str:
    """Join the raw text of text blocks for glossary term matching."""
    return "\n".join(blk.text for blk in blk_list if getattr(blk, "text", ""))


class GlossaryManager:
    """Stores the user's term glossary and formats it for LLM prompts.

    Persisted as a standalone JSON file in the user data directory so the
    glossary survives independently of QSettings and is easy to share.
    """

    def __init__(self, file_path: str | None = None):
        self.file_path = file_path or os.path.join(get_user_data_dir(), "glossary.json")
        self.enabled: bool = True
        self.match_only: bool = True  # only send terms found in the source text
        self.entries: list[GlossaryEntry] = []
        self.load()

    # Persistence

    def load(self) -> None:
        if not os.path.exists(self.file_path):
            return
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load glossary from {self.file_path}: {e}")
            return
        self.enabled = bool(data.get("enabled", True))
        self.match_only = bool(data.get("match_only", True))
        self.entries = self._parse_entries(data.get("entries", []))

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "enabled": self.enabled,
                        "match_only": self.match_only,
                        "entries": [asdict(e) for e in self.entries],
                    },
                    f, ensure_ascii=False, indent=2,
                )
        except Exception as e:
            logger.error(f"Failed to save glossary to {self.file_path}: {e}")

    # Editing

    def find(self, source: str) -> GlossaryEntry | None:
        return next((e for e in self.entries if e.source == source), None)

    def upsert(self, entry: GlossaryEntry, original_source: str | None = None, save: bool = True) -> None:
        """Add an entry, or replace the one it edits/duplicates."""
        if original_source and original_source != entry.source:
            self.entries = [e for e in self.entries if e.source != original_source]
        existing = self.find(entry.source)
        if existing:
            self.entries[self.entries.index(existing)] = entry
        else:
            self.entries.append(entry)
        if save:
            self.save()

    def remove(self, sources: list[str]) -> int:
        before = len(self.entries)
        sources_set = set(sources)
        self.entries = [e for e in self.entries if e.source not in sources_set]
        removed = before - len(self.entries)
        if removed:
            self.save()
        return removed

    def types_in_use(self) -> list[str]:
        seen = list(GLOSSARY_PRESET_TYPES)
        for e in self.entries:
            if e.type and e.type not in seen:
                seen.append(e.type)
        return seen

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

    # Prompt building

    def build_prompt(self, source_text: str = "") -> str:
        """Format glossary entries as an instruction block for LLM translators.

        When match_only is set and source_text is given, only terms that
        actually appear in the text are included to keep prompts small.
        """
        if not self.enabled or not self.entries:
            return ""

        entries = self.entries
        if self.match_only and source_text:
            entries = [e for e in entries if e.source and e.source in source_text]
        if not entries:
            return ""

        lines = ["Glossary (always translate these terms exactly as specified):"]
        for e in entries:
            details = []
            if e.type and e.type != "term":
                details.append(e.type)
            if e.gender and e.type == "character":
                details.append(f"gender: {e.gender}")
            if e.note:
                details.append(e.note)
            suffix = f" ({'; '.join(details)})" if details else ""
            lines.append(f"- {e.source} => {e.target}{suffix}")
        return "\n".join(lines)
