import os
import re
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
