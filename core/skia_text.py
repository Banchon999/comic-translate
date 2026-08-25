"""Skia implementation of the text measurement seam.

Qt-free, and therefore usable from a headless process — which is half the point
of the seam. `skia-python` is an optional dependency: `is_available()` reports
whether it imported, and the app falls back to the Qt measurer when it did not,
the same way the optional OCR engines degrade.

## Two places Skia and Qt do not line up

**Wrapping.** Qt's `QTextDocument.size()` with no text width set reports the
laid-out size with breaks only where the string has them. Skia's `Paragraph`
has no such mode — `layout(width)` always wraps to `width`. So layout runs at
`UNCONSTRAINED_WIDTH` and the width comes from `LongestLine`, which reproduces
Qt's "break only at newlines" reading. Passing the real ROI width here instead
would double-wrap: the greedy wrapper above has already inserted the breaks it
wants, and Skia would add more inside them.

**Line spacing.** Qt sets `ProportionalHeight(spacing * 100)`, making each line
box that multiple of its *natural* height. skia-python's `StrutStyle` exposes
only `setLeading` and `setStrutEnabled` — no height multiplier — and leading
below roughly 0.5 has no visible effect at all, so it cannot express that.
Instead the paragraph's own height is scaled: `Paragraph.Height` is already the
sum of the natural per-line heights, so multiplying it by the spacing applies
exactly the definition Qt is applying.

Reading the paragraph's own height, rather than assuming one line height for
everything, is what keeps non-Latin scripts correct. A Thai font run has a
taller line box than a Latin one in the same nominal size — 59px against 46px
at 24pt, because the face leaves room above and below for vowel and tone marks
whether or not the string uses them. Assuming the Latin figure would measure
every Thai block short and clip it.

## Units and margin

Two systematic offsets have to be corrected or every measurement comes out
about 30% small, which would shrink the text on every rendered page.

**Points versus pixels.** `font_size` means points everywhere in this codebase,
because `QFont(family, size)` takes points. Skia's `setFontSize` takes pixels.
At the 96 DPI Qt reports as its logical DPI, 24pt is 32px — so a Skia measurer
fed the raw number reads 25% narrow. `POINTS_TO_PIXELS` does the conversion and
`dpi` is a constructor argument for the machines where 96 is wrong.

**Document margin.** `QTextDocument` carries a 4px margin by default, adding 8
to each dimension of everything Qt has ever measured here. Skia has no such
notion, so it is added explicitly. Both numbers are constructor arguments
rather than constants so the parity tests can pin them.

## Vertical CJK

Skia has no vertical writing mode. A vertical block is measured as columns of
stacked glyphs: each source line becomes one column, a column is as wide as its
widest glyph and as tall as the stack, and columns are laid out across. This
mirrors what `VerticalTextDocumentLayout` does on the Qt side rather than
inventing a second convention.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache
from typing import Optional

from core.enums import LayoutDirection
from core.text_measure import TextMeasurer, TextStyle

try:  # pragma: no cover - exercised by whether the import lands
    import skia

    _SKIA_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover
    skia = None
    _SKIA_IMPORT_ERROR = exc


# Wide enough that Skia never breaks a line the caller did not ask for, and
# small enough to stay exact in float32 — Skia's scalars are single precision,
# and 1e9 there starts losing integer resolution.
UNCONSTRAINED_WIDTH = 1e6

#: Qt reports 96 as its logical DPI on a normal desktop, making a point 4/3 of
#: a pixel. See the module docstring.
DEFAULT_DPI = 96.0
POINTS_PER_INCH = 72.0

#: QTextDocument's default documentMargin, which every Qt measurement in this
#: codebase has included.
DEFAULT_DOCUMENT_MARGIN = 4.0


def is_available() -> bool:
    """True when skia-python imported and the measurer can be constructed."""
    return skia is not None


def unavailable_reason() -> Optional[str]:
    if skia is None:
        return f"skia-python is not importable: {_SKIA_IMPORT_ERROR}"
    return None


@lru_cache(maxsize=1)
def _font_collection():
    collection = skia.textlayout.FontCollection()
    collection.setDefaultFontManager(skia.FontMgr())
    return collection


@lru_cache(maxsize=1)
def _unicode():
    # ParagraphBuilder.make requires an explicit Unicode in this binding; one
    # instance is enough and building it is not cheap.
    return skia.Unicode.ICU_Make()


#: Unicode directional isolates. Wrapping a run in one of these sets the base
#: direction for what is inside it, and they are zero-width so they change no
#: metric. RLI/LRI open; PDI closes.
LRI, RLI, PDI = "\u2066", "\u2067", "\u2069"


def apply_base_direction(text: str, style: TextStyle) -> str:
    """Force `text` to lay out in the block's own direction.

    skia-python 144 exposes no direction setter — `ParagraphStyle` offers only
    `setTextAlign`, `setStrutStyle` and `setTextStyle`, and there is no
    `TextDirection` enum at all — so the direction has to be carried in the
    text itself.

    Left alone, ICU guesses the base direction from the first strong character.
    That is right most of the time and wrong exactly where it matters: an
    Arabic block that opens with a Latin name or a digit lays out
    left-to-right, against the direction the user explicitly chose.

    Both the measurer and the renderer call this, and they must keep doing so —
    the wrap alters glyph order, so measuring the bare string and painting the
    wrapped one is a way to make the two disagree.
    """
    if not text:
        return text
    rtl = int(getattr(style.direction, "value", style.direction)) == int(
        LayoutDirection.RightToLeft
    )
    return (RLI if rtl else LRI) + text + PDI


def _font_style(style: TextStyle):
    return skia.FontStyle(
        skia.FontStyle.kBold_Weight if style.bold else skia.FontStyle.kNormal_Weight,
        skia.FontStyle.kNormal_Width,
        skia.FontStyle.kItalic_Slant if style.italic else skia.FontStyle.kUpright_Slant,
    )


class SkiaTextMeasurer(TextMeasurer):
    name = "skia"

    #: Used when a style names no family. Skia's font manager resolves the
    #: first that exists, so this is a preference order, not a single choice.
    DEFAULT_FAMILIES = ("DejaVu Sans", "Noto Sans", "Arial", "sans-serif")

    #: Cleared wholesale when reached. A page's fit searches share candidate
    #: strings heavily, so hit rate matters far more than eviction order.
    CACHE_LIMIT = 8192

    def __init__(
        self,
        default_families: Optional[tuple[str, ...]] = None,
        dpi: float = DEFAULT_DPI,
        document_margin: float = DEFAULT_DOCUMENT_MARGIN,
    ):
        if skia is None:
            raise RuntimeError(unavailable_reason())
        self.default_families = tuple(default_families or self.DEFAULT_FAMILIES)
        self.dpi = dpi
        self.document_margin = document_margin
        self._size_cache: dict[tuple[str, TextStyle], tuple[float, float]] = {}

    def points_to_pixels(self, points: float) -> float:
        return float(points) * self.dpi / POINTS_PER_INCH

    # -- font resolution ---------------------------------------------------

    def resolve_font_family(self, font_family: str) -> str:
        if isinstance(font_family, str) and font_family.strip():
            return font_family.strip()
        return self.default_families[0]

    def _families(self, style: TextStyle) -> list[str]:
        named = style.font_family.strip() if isinstance(style.font_family, str) else ""
        if named:
            # Keep the fallbacks behind the requested family so a missing font
            # still shapes rather than rendering tofu.
            return [named, *self.default_families]
        return list(self.default_families)

    # -- paragraph construction -------------------------------------------

    def _paragraph(self, text: str, style: TextStyle):
        paragraph_style = skia.textlayout.ParagraphStyle()
        text_style = skia.textlayout.TextStyle()
        text_style.setFontFamilies(self._families(style))
        text_style.setFontSize(self.points_to_pixels(style.font_size))
        text_style.setFontStyle(_font_style(style))
        if style.letter_spacing:
            text_style.setLetterSpacing(float(style.letter_spacing))
        text_style.setColor(skia.ColorBLACK)
        if style.underline:
            text_style.setDecoration(skia.textlayout.TextDecoration.kUnderline)
        paragraph_style.setTextStyle(text_style)

        builder = skia.textlayout.ParagraphBuilder.make(
            paragraph_style, _font_collection(), _unicode()
        )
        builder.addText(apply_base_direction(text, style))
        paragraph = builder.Build()
        paragraph.layout(UNCONSTRAINED_WIDTH)
        return paragraph

    def _natural_size(self, text: str, style: TextStyle) -> tuple[float, float]:
        """Skia's own laid-out size, before line spacing is applied.

        Cached on (text, style) because the fit search measures the same
        candidate strings repeatedly as it narrows the point size, and building
        a Paragraph is the expensive part of this class. The cache lives on the
        instance rather than behind `lru_cache`, which on a method would hold
        `self` alive for the life of the process.
        """
        key = (text, style)
        hit = self._size_cache.get(key)
        if hit is not None:
            return hit

        paragraph = self._paragraph(text, style)
        height = float(paragraph.Height)

        # LongestLine is left at -FLT_MAX when there is nothing to lay out,
        # which is not a width. Empty text occupies no horizontal space.
        width = float(paragraph.LongestLine) if text else 0.0
        if width < 0:
            width = 0.0

        if len(self._size_cache) >= self.CACHE_LIMIT:
            self._size_cache.clear()
        self._size_cache[key] = (width, height)
        return width, height

    # -- the interface -----------------------------------------------------

    def measure(self, text: str, style: TextStyle) -> tuple[float, float]:
        if style.vertical:
            return self._measure_vertical(text, style)
        return self._measure_horizontal(text, style)

    def _measure_horizontal(self, text: str, style: TextStyle) -> tuple[float, float]:
        width, natural_height = self._natural_size(text, style)
        return self._with_margin(width, natural_height * style.line_spacing)

    def _with_margin(self, width: float, height: float) -> tuple[float, float]:
        margin = 2 * self.document_margin
        return width + margin, height + margin

    def _measure_vertical(self, text: str, style: TextStyle) -> tuple[float, float]:
        """Columns of stacked glyphs, as CJK vertical text runs.

        Each cluster is measured on its own because a column is as wide as its
        widest glyph and as tall as the sum of their heights — neither of which
        a single horizontal layout of the line reports.
        """
        columns = text.split("\n")

        column_widths = []
        column_heights = []
        for column in columns:
            widest = 0.0
            stacked = 0.0
            for cluster in _vertical_clusters(column):
                cluster_width, cluster_height = self._natural_size(cluster, style)
                widest = max(widest, cluster_width)
                stacked += cluster_height
            column_widths.append(widest)
            column_heights.append(stacked)

        if not column_widths:
            return 0.0, 0.0

        # Columns sit side by side, so the spacing that separates lines in
        # horizontal text separates columns here — it applies across the width.
        width = sum(column_widths) * style.line_spacing
        height = max(column_heights)
        return self._with_margin(float(width), float(height))


def _vertical_clusters(line: str) -> list[str]:
    """Split a line into the units that stack as one glyph each.

    A combining mark rides with the base character it modifies rather than
    taking a cell of its own — the same rule the Thai wrap path applies for
    horizontal text.
    """
    clusters: list[str] = []
    for char in line:
        if clusters and unicodedata.combining(char):
            clusters[-1] += char
        else:
            clusters.append(char)
    return clusters
