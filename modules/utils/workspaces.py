"""Workspaces: one saved setup per series.

Translating a series is not one job, it is fifty chapters that all want the
same glossary, the same prompt preset, the same language pair and the same
source folders — and the moment you switch to a different series every one of
those has to be changed by hand, in four different places, or the output comes
out wrong in ways that are not obvious until you read it.

A workspace names that whole set. Switching workspace switches all of it at
once, and remembers where each series left off.

Stored one JSON file per workspace under ``<user data>/workspaces/``, with a
``config.json`` holding the active name — the same shape the glossary profiles
use, so there is one storage convention in the project rather than two.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, asdict

from .paths import get_user_data_dir

logger = logging.getLogger(__name__)


@dataclass
class Workspace:
    """Everything that should follow a series around."""

    name: str
    folders: list[str] = field(default_factory=list)
    glossary_profile: str = ""
    prompt_preset: str = ""
    source_language: str = ""
    target_language: str = ""
    # The .ctpr this series was last saved to, so reopening resumes the work
    # rather than reimporting the pages.
    project_file: str = ""
    last_opened: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Workspace":
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in (data or {}).items() if k in known}
        filtered["name"] = str(filtered.get("name") or "")
        filtered["folders"] = [str(p) for p in (filtered.get("folders") or [])]
        return cls(**filtered)

    def existing_folders(self) -> list[str]:
        """Folders that are still on disk.

        A workspace outlives the drive it was made on, so a missing folder is
        expected rather than an error.
        """
        return [path for path in self.folders if os.path.isdir(path)]


class WorkspaceManager:
    """The saved workspaces, and which one is active."""

    _META_FILE = "config.json"
    _instance = None

    def __init__(self, base_dir: str | None = None):
        self.base_dir = base_dir or os.path.join(get_user_data_dir(), "workspaces")
        self.active_name: str = ""
        self._load_meta()

    @classmethod
    def instance(cls) -> "WorkspaceManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # Storage

    @staticmethod
    def safe_filename(name: str) -> str:
        cleaned = re.sub(r'[\\/:*?"<>|]', "_", str(name or "")).strip()
        return cleaned

    def _path_for(self, name: str) -> str:
        return os.path.join(self.base_dir, f"{self.safe_filename(name)}.json")

    @property
    def _meta_path(self) -> str:
        return os.path.join(self.base_dir, self._META_FILE)

    def _load_meta(self) -> None:
        try:
            with open(self._meta_path, "r", encoding="utf-8") as handle:
                self.active_name = str(json.load(handle).get("active", "") or "")
        except (OSError, ValueError):
            self.active_name = ""

    def _save_meta(self) -> None:
        try:
            os.makedirs(self.base_dir, exist_ok=True)
            with open(self._meta_path, "w", encoding="utf-8") as handle:
                json.dump({"active": self.active_name}, handle, ensure_ascii=False, indent=2)
        except OSError:
            logger.exception("Could not save the active workspace")

    # Queries

    def list_names(self) -> list[str]:
        if not os.path.isdir(self.base_dir):
            return []
        names = [
            file_name[: -len(".json")]
            for file_name in os.listdir(self.base_dir)
            if file_name.endswith(".json") and file_name != self._META_FILE
        ]
        return sorted(names, key=str.casefold)

    def load(self, name: str) -> Workspace | None:
        if not name:
            return None
        try:
            with open(self._path_for(name), "r", encoding="utf-8") as handle:
                workspace = Workspace.from_dict(json.load(handle))
        except (OSError, ValueError):
            return None
        # The file name is the identity; a stale name field inside must not win.
        workspace.name = name
        return workspace

    def active(self) -> Workspace | None:
        return self.load(self.active_name)

    # Mutations

    def save(self, workspace: Workspace) -> None:
        if not self.safe_filename(workspace.name):
            raise ValueError("A workspace needs a name")
        try:
            os.makedirs(self.base_dir, exist_ok=True)
            with open(self._path_for(workspace.name), "w", encoding="utf-8") as handle:
                json.dump(workspace.to_dict(), handle, ensure_ascii=False, indent=2)
        except OSError:
            logger.exception("Could not save workspace %s", workspace.name)

    def create(self, name: str, **fields) -> Workspace | None:
        safe = self.safe_filename(name)
        if not safe or safe in self.list_names():
            return None
        workspace = Workspace(name=safe, last_opened=time.time(), **fields)
        self.save(workspace)
        self.set_active(safe)
        return workspace

    def rename(self, old_name: str, new_name: str) -> bool:
        safe_new = self.safe_filename(new_name)
        if not safe_new or safe_new == old_name or safe_new in self.list_names():
            return False
        workspace = self.load(old_name)
        if workspace is None:
            return False

        workspace.name = safe_new
        self.save(workspace)
        try:
            os.remove(self._path_for(old_name))
        except OSError:
            logger.exception("Could not remove the old workspace file for %s", old_name)
        if self.active_name == old_name:
            self.set_active(safe_new)
        return True

    def delete(self, name: str) -> None:
        try:
            os.remove(self._path_for(name))
        except OSError:
            logger.exception("Could not delete workspace %s", name)
        if self.active_name == name:
            remaining = self.list_names()
            self.set_active(remaining[0] if remaining else "")

    def set_active(self, name: str) -> None:
        self.active_name = self.safe_filename(name)
        self._save_meta()

    def touch(self, name: str) -> None:
        """Record that this workspace was just opened."""
        workspace = self.load(name)
        if workspace is None:
            return
        workspace.last_opened = time.time()
        self.save(workspace)

    def add_folder(self, name: str, folder: str) -> Workspace | None:
        """Remember a chapter folder against a workspace, keeping order."""
        workspace = self.load(name)
        if workspace is None or not folder:
            return None
        folder = os.path.abspath(folder)
        if folder not in workspace.folders:
            workspace.folders.append(folder)
            self.save(workspace)
        return workspace
