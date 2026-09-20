"""PageStateStore — the single access boundary for per-page editing state.

Slice 1 of the document-model roadmap. Today every page's editing state lives in
``ComicTranslate.image_states`` as a plain ``dict[str, dict]`` keyed by working
file path, and ~130 sites across the app read and mutate it by hand, reaching
deep into a schema (``viewer_state``, ``blk_list``, ``brush_strokes``, ``skip``,
languages…) that only ``_build_image_state`` ever wrote in full.

``PageStateStore`` is that same mapping, made into a named type that subsystems
go through instead of poking the raw structure. It is a **``dict`` subclass**, so
it is behaviourally identical to the dict it replaces — live references, nested
in-place mutation, insertion order, ``get``/``setdefault``/``pop``/``clear``/
``keys``/``values``/``items``/indexing/iteration all work exactly as before, and
nothing here returns a defensive copy. That compatibility is the point: the
store can be dropped in with zero behaviour change now, and a later slice can put
DocumentModel-backed logic *inside* it without touching every call site again.

Live-reference semantics are non-negotiable for Slice 1: an accessor that returns
a page state, its ``viewer_state`` or its ``blk_list`` returns the **live**
object, so mutating what you got back mutates the backing store, exactly as
reaching into the dict does today. The only "default" returns that are not stored
(``get_page_state(path)`` when absent, ``blk_list`` of a missing page) mirror the
existing ``.get(fp, {}).get('blk_list', [])`` idiom, which was already a throwaway
empty on a miss.
"""

from __future__ import annotations

from typing import Any, Optional


class PageStateStore(dict):
    """A ``dict[str, dict]`` of page path -> page state, with a semantic API.

    Subclasses ``dict`` on purpose: every existing raw access keeps working while
    call sites migrate to the methods below one cluster at a time.
    """

    # --- canonical creation --------------------------------------------------

    @staticmethod
    def build_page_state(
        *,
        viewer_state: dict,
        source_lang: str,
        target_lang: str,
        brush_strokes: list,
        blk_list: list,
        skip: bool,
        export_group_name: str,
        existing: Optional[dict] = None,
    ) -> dict:
        """Assemble one page-state dict with the canonical shape and defaults.

        This is the single place that knows the full page-state schema. The
        controller's ``_build_image_state`` gathers the UI-derived values (the
        language combos, the export group name) and delegates the assembly here,
        so the schema lives in one spot rather than being re-implied wherever a
        page is created. Fields carried on ``existing`` that are not overwritten
        here (e.g. a batch-only ``skip_render``) are preserved, matching today's
        ``dict(existing_state); state.update({...})`` behaviour.
        """
        state = dict(existing or {})
        state.update({
            "viewer_state": viewer_state,
            "source_lang": source_lang,
            "target_lang": target_lang,
            "brush_strokes": brush_strokes,
            "blk_list": blk_list,
            "skip": skip,
            "export_group_name": export_group_name,
        })
        return state

    # --- page-level access ---------------------------------------------------

    def has_page(self, path: str) -> bool:
        return path in self

    def get_page_state(self, path: str, default: Any = None) -> Any:
        """The live page-state dict for ``path``, or ``default`` if absent.

        Returns the stored object, not a copy — mutating it mutates the store.
        """
        return self.get(path, default)

    def ensure_page(self, path: str) -> dict:
        """Return the live page state for ``path``, creating an empty one if
        needed. Idempotent: an existing page state is returned untouched, never
        replaced (mirrors ``setdefault(path, {})``)."""
        return self.setdefault(path, {})

    def set_page_state(self, path: str, state: dict) -> None:
        """Replace the whole page state for ``path`` (e.g. the result of
        ``build_page_state``)."""
        self[path] = state

    def remove_page(self, path: str) -> Optional[dict]:
        """Drop ``path`` if present; returns the removed state or ``None``."""
        return self.pop(path, None)

    # --- nested sub-state, returned live -------------------------------------

    def viewer_state(self, path: str, default: Any = None) -> Any:
        """The live ``viewer_state`` of ``path`` (rectangles / text items /
        transform), or ``default`` if the page or the key is absent."""
        page = self.get(path)
        if page is None:
            return default
        return page.get("viewer_state", default)

    def ensure_viewer_state(self, path: str) -> dict:
        """The live ``viewer_state`` of ``path``, creating the page and the
        ``viewer_state`` dict if needed. For writers that mutate it in place."""
        page = self.ensure_page(path)
        return page.setdefault("viewer_state", {})

    def blk_list(self, path: str) -> list:
        """The live ``blk_list`` of ``path``, or a throwaway empty list if the
        page or the key is absent (mirrors ``.get(fp, {}).get('blk_list', [])``;
        the empty is not stored, so use ``set_blk_list`` to persist)."""
        page = self.get(path)
        if page is None:
            return []
        return page.get("blk_list", [])

    def set_blk_list(self, path: str, blk_list: list) -> None:
        """Store ``blk_list`` on ``path``, creating the page if needed."""
        self.ensure_page(path)["blk_list"] = blk_list

    def is_skipped(self, path: str) -> bool:
        """Whether ``path`` is flagged to skip processing (default ``False``)."""
        page = self.get(path)
        if page is None:
            return False
        return bool(page.get("skip", False))

    def set_skip(self, path: str, skip: bool) -> None:
        """Flag/unflag ``path`` to skip processing, creating the page if needed."""
        self.ensure_page(path)["skip"] = skip

    def languages(self, path: str) -> tuple[Optional[str], Optional[str]]:
        """The (source, target) language pair for ``path`` (``None`` for a
        missing page or an unset language)."""
        page = self.get(path)
        if page is None:
            return (None, None)
        return (page.get("source_lang"), page.get("target_lang"))

    def set_languages(self, path: str, source_lang: str, target_lang: str) -> None:
        """Set the source/target language pair for ``path``, creating the page
        if needed."""
        page = self.ensure_page(path)
        page["source_lang"] = source_lang
        page["target_lang"] = target_lang
