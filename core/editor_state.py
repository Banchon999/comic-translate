"""Which pipeline steps the current page has been through (Qt-free).

The editor's step bar shows Detect → Recognize → Translate → Segment → Clean →
Render with a mark on the steps already done. There is no stored "step done"
flag anywhere: each step leaves evidence on the page, and this reads it —
so a page loaded from a project, undone, or partly redone shows the truth
rather than what the last button press claimed.
"""

from __future__ import annotations

STEP_KEYS = ("detect", "recognize", "translate", "segment", "clean", "render")


def step_flags(blk_list, has_strokes: bool, has_patches: bool, has_text_items: bool) -> tuple[bool, ...]:
    """One bool per STEP_KEYS entry: whether that step's output is on the page."""
    blocks = list(blk_list or [])
    detected = bool(blocks)
    recognized = any((getattr(b, "text", "") or "").strip() for b in blocks)
    translated = any((getattr(b, "translation", "") or "").strip() for b in blocks)
    return (detected, recognized, translated, bool(has_strokes), bool(has_patches), bool(has_text_items))
