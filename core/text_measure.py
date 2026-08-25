"""The text measurement seam.

Auto-fit decides what a rendered page looks like: `pyside_word_wrap` binary
-searches point sizes and asks, at each one, how large a candidate string lays
out. Today that answer comes from `QTextDocument`; in Phase 1 it comes from
Skia. Everything else about wrapping — the greedy line breaking, the Thai
cluster-safe path, the no-space-language segmentation — is arithmetic on top of
that one answer, and does not care who gives it.

So the pipeline depends on this interface rather than on a text engine, which
is what lets `modules/rendering/render.py` import without Qt.

**Whoever measures must also paint.** A page measured with one engine and drawn
with another disagrees about line breaks at the same font size, and the preview
stops matching the export. Registering a measurer is therefore a statement
about the whole rendering path, not a local choice.
"""

from __future__ import annotations

import importlib.util
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import Optional

from core.enums import Alignment, LayoutDirection


@dataclass(frozen=True)
class TextStyle:
    """Everything that changes how a string lays out.

    Frozen because the fit search reuses one style across many candidate
    strings and only varies the size — `with_size` returns a copy rather than
    mutating one shared object out from under a cache.

    Colour, the outline and the drawn effects are deliberately absent: none of
    them move a glyph, so none of them belong to measurement. The outline
    *width* is the near miss — the caller adds it to the measured box itself,
    because it pads the drawn result without changing the layout.

    Letter spacing is here precisely because it does move glyphs. Note that
    `pyside_word_wrap` does not set it, so auto-fit ignores letter spacing
    exactly as it always has; the renderer sets it, which is what stops
    letter-spaced text being drawn into a surface too small for it.
    """

    font_family: str = ""
    font_size: float = 20.0
    bold: bool = False
    italic: bool = False
    underline: bool = False
    line_spacing: float = 1.0
    letter_spacing: float = 0.0
    alignment: Alignment = Alignment.Center
    direction: LayoutDirection = LayoutDirection.LeftToRight
    vertical: bool = False

    def with_size(self, font_size: float) -> "TextStyle":
        return replace(self, font_size=font_size)


class TextMeasurer(ABC):
    """Lays text out and reports the box it occupies."""

    #: Short identifier used in logs and in golden-image test names.
    name: str = "abstract"

    @abstractmethod
    def measure(self, text: str, style: TextStyle) -> tuple[float, float]:
        """Return ``(width, height)`` of `text` laid out under `style`.

        Includes the space between lines that `line_spacing` asks for, and for
        a vertical style the box is the rotated one — tall text measures narrow
        and long. Excludes any outline.
        """

    def resolve_font_family(self, font_family: str) -> str:
        """The family actually used when `font_family` is blank.

        Split out because the fallback belongs to the engine — Qt answers
        `QApplication.font().family()`, Skia answers from its own font
        manager — and callers should not have to know which is in play.
        """
        return font_family


_measurer: Optional[TextMeasurer] = None


def set_measurer(measurer: Optional[TextMeasurer]) -> None:
    """Install the measurer for this process. None restores the default."""
    global _measurer
    _measurer = measurer


def get_measurer() -> TextMeasurer:
    """The installed measurer, defaulting to Qt's when Qt is available.

    The default exists so that every caller that worked before this seam
    existed still works without an explicit registration at startup — an
    ordering bug there would be silent and would only show up as wrong text
    layout. Phase 1 replaces it by calling `set_measurer` with the Skia
    implementation.
    """
    global _measurer
    if _measurer is None:
        _measurer = _default_measurer()
    return _measurer


def _default_measurer() -> TextMeasurer:
    if importlib.util.find_spec("PySide6") is None:
        raise RuntimeError(
            "No text measurer is registered and PySide6 is not available. "
            "A headless process that needs to lay text out must call "
            "core.text_measure.set_measurer() with an implementation."
        )
    from app.ui.canvas.text.qt_measurer import QtTextMeasurer

    return QtTextMeasurer()
