"""Rasterise a styled text block with Skia.

Qt-free: the input is a plain description and the output is an RGBA array, so
this is testable and usable headlessly. `app/ui/canvas/skia_paint.py` adapts a
`TextBlockItem` to it and wraps the result in a QImage.

## Draw order

Shadow, then outlines widest-first, then the fill. That is the order the Qt
painter uses and it is not arbitrary: a narrower outline layered over a wider
one stays visible, and the fill must land on top of every stroke or a thick
outline eats the glyph.

Skia strokes centred on the glyph contour, so half of a stroke of width *w*
falls inside the glyph and is then covered by the fill. Qt's outline
implementation displaces whole copies of the text instead. To match the visual
weight, the stroke is drawn at twice the requested width — the visible half is
then *w*, which is what the user asked for.

## Surfaces

One surface per text block, sized to the block plus the room its shadow and
outlines spill into. `MAX_SURFACE_PIXELS` caps it: a webtoon strip is tens of
thousands of pixels tall, and a caller that hands the whole strip to one
surface allocates gigabytes. Anything larger is refused rather than attempted,
so the failure is a clear error and not an OOM kill.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Optional, Sequence

import numpy as np

from core.color import to_rgba
from core.enums import Alignment
from core.skia_text import (
    SkiaTextMeasurer,
    UNCONSTRAINED_WIDTH,
    _font_collection,
    _unicode,
    _font_style,
    _vertical_clusters,
    apply_base_direction,
    direction_isolates,
    is_available,
    skia,
    unavailable_reason,
)
from core.text_measure import TextStyle

#: 64 megapixels — 256 MB at RGBA8888. Comfortably above any single text block
#: and far below a full webtoon strip.
MAX_SURFACE_PIXELS = 64 * 1024 * 1024

#: Laying out at exactly the measured width re-wraps the last word.
#: `LongestLine` is a float advance and Skia's line breaker compares against it
#: with its own rounding, so "Hello world" measured at 179.4 and laid out at
#: 179.4 comes back as two lines. One pixel of slack removes that without
#: moving centred text by more than half a pixel.
LAYOUT_SLACK_PX = 1.0

#: Ceiling on the device scale a text raster is rendered at.
#:
#: The export renderer works at 2x, and a hidpi canvas can ask for more. Past
#: about 3x the extra samples stop being visible after the downscale while the
#: surface keeps growing with the square of the scale, so this bounds both the
#: memory and the time a single block can cost.
MAX_RENDER_SCALE = 3.0

#: Qt's shadow blur radius to Skia's Gaussian sigma.
#:
#: Fitted, not derived. QGraphicsDropShadowEffect's "blur radius" is not a
#: standard deviation and Qt approximates the blur with stacked box passes, so
#: there is no clean algebraic conversion. Measured against Qt over blur radii
#: of 4, 10 and 20 by counting softly-shaded pixels: the textbook radius/2 came
#: out 2.3-2.6x too diffuse at every radius, which reads as a muddy shadow that
#: washes over the glyph. This lands within 15% of Qt across that range.
SHADOW_BLUR_TO_SIGMA = 0.15


class SurfaceTooLarge(ValueError):
    """Raised rather than attempting an allocation that would exhaust memory."""


@dataclass(frozen=True)
class OutlineLayer:
    """One outlined range.

    `start`/`end` are offsets into `TextRenderSpec.text`; `end=None` means "to
    the end". They matter: the app lets a user outline a selection rather than
    the whole block, and an outline that forgets its range strokes every word.
    """

    width: float
    color: str
    start: int = 0
    end: Optional[int] = None

    def covers(self, run_start: int, run_end: int) -> bool:
        end = self.end if self.end is not None else run_end
        return run_start < end and run_end > self.start


@dataclass(frozen=True)
class CharRun:
    """A span of text sharing one character format.

    Produced from the item's QTextDocument on the Qt side. Without these the
    renderer flattens a block to a single style, so bolding or recolouring one
    word — which the app supports through `update_text_format` — is lost the
    moment Skia paints.
    """

    start: int
    end: int
    font_family: Optional[str] = None
    font_size: Optional[float] = None
    color: Optional[str] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    underline: Optional[bool] = None


@dataclass(frozen=True)
class ShadowSpec:
    color: str = "#000000"
    offset: tuple[float, float] = (4.0, 4.0)
    blur: float = 0.0


@dataclass(frozen=True)
class GradientSpec:
    color: str
    angle: float = 90.0


@dataclass(frozen=True)
class TextRenderSpec:
    """Everything needed to draw one text block, in plain values."""

    text: str
    style: TextStyle
    fill_color: str = "#000000"
    #: Widest first is enforced at draw time; order here does not matter.
    outlines: Sequence[OutlineLayer] = field(default_factory=tuple)
    shadow: Optional[ShadowSpec] = None
    gradient: Optional[GradientSpec] = None
    #: Per-range character formats. Empty means the whole block shares
    #: `style`/`fill_color`, which is what freshly rendered translations look
    #: like; a user who restyled one word produces runs here.
    char_runs: Sequence[CharRun] = field(default_factory=tuple)
    #: Box to lay out within. None measures the text and uses that.
    box: Optional[tuple[float, float]] = None
    #: Whether the source document actually wraps a line onto the next.
    #:
    #: `box` is measured by Qt — it is the text item's own rect — while the
    #: layout below is Skia's, and the two disagree about a string's width by
    #: about a pixel. Laying out inside a box a hair narrower than Skia's own
    #: measurement pushes the last word onto a second line that Qt never had,
    #: which then paints outside the box and gets clipped. So the wrap decision
    #: is taken where the document lives, on the Qt side, and carried here:
    #: False means "this did not wrap, do not let it", and `_layout_width`
    #: widens to Skia's own measurement to guarantee that.
    soft_wrapped: bool = True

    def runs_for(self, text: str) -> list[CharRun]:
        """The spans to build the paragraph from, one pushStyle each.

        Split at every character-format boundary **and** at every outline
        boundary. The outline boundaries matter even when the text is uniformly
        styled: a block with one run is entirely covered by any outline that
        touches it, so an outline scoped to one word would stroke the whole
        block. Splitting first gives the range something to land on.
        """
        length = len(text)
        if length == 0:
            return []

        base = list(self.char_runs) or [CharRun(0, length)]

        cuts = {0, length}
        for run in base:
            cuts.add(max(0, min(length, run.start)))
            cuts.add(max(0, min(length, run.end)))
        for outline in self.outlines:
            cuts.add(max(0, min(length, outline.start)))
            end = outline.end if outline.end is not None else length
            cuts.add(max(0, min(length, end)))
        edges = sorted(cuts)

        def formatting_at(index: int) -> CharRun:
            for run in base:
                if run.start <= index < run.end:
                    return run
            return base[-1]

        pieces: list[CharRun] = []
        for start, end in zip(edges, edges[1:]):
            if start >= end:
                continue
            source = formatting_at(start)
            pieces.append(replace(source, start=start, end=end))
        return pieces


def _skia_color(value, default=(0, 0, 0, 255)):
    rgba = to_rgba(value)
    if rgba is None:
        rgba = default
    r, g, b, a = rgba
    return skia.Color4f(r / 255.0, g / 255.0, b / 255.0, a / 255.0).toColor()


class SkiaTextRenderer:
    """Draws a `TextRenderSpec` into an RGBA array."""

    def __init__(self, measurer: Optional[SkiaTextMeasurer] = None):
        if not is_available():
            raise RuntimeError(unavailable_reason())
        self.measurer = measurer or SkiaTextMeasurer()

    # -- geometry ----------------------------------------------------------

    def content_size(self, spec: TextRenderSpec) -> tuple[float, float]:
        if spec.box is not None:
            return spec.box
        return self.measurer.measure(spec.text, spec.style)

    def bleed(self, spec: TextRenderSpec) -> tuple[float, float, float, float]:
        """Extra room needed on each side: (left, top, right, bottom).

        Outlines grow symmetrically; a shadow grows only in the direction it is
        offset, plus its blur radius in every direction.
        """
        widest_outline = max((o.width for o in spec.outlines), default=0.0)
        left = top = right = bottom = widest_outline

        if spec.shadow is not None:
            dx, dy = spec.shadow.offset
            blur = spec.shadow.blur
            left = max(left, blur - min(dx, 0.0) + widest_outline)
            top = max(top, blur - min(dy, 0.0) + widest_outline)
            right = max(right, blur + max(dx, 0.0) + widest_outline)
            bottom = max(bottom, blur + max(dy, 0.0) + widest_outline)

        return left, top, right, bottom

    # -- painting ----------------------------------------------------------

    def render(
        self, spec: TextRenderSpec, scale: float = 1.0
    ) -> tuple[np.ndarray, tuple[float, float], float]:
        """Return ``(rgba, (offset_x, offset_y), scale)``.

        The offset is where the text box's own top-left sits inside the
        returned array, in **logical** units; it is non-zero whenever a shadow
        or outline bleeds to the left or above. Callers blit at
        `item_pos - offset` and size the target rect logically.

        `scale` renders the same layout into a surface that many times larger,
        with the canvas scaled to match, so the geometry is untouched and only
        the sampling gets finer. Pass the painter's device scale: the export
        renderer draws the scene into a 2x surface and scales it back down, and
        a bitmap rasterised at 1x is *upscaled* by that transform, so the pass
        that sharpens Qt's glyphs was softening Skia's. It matters on the
        canvas too, where zooming in otherwise magnifies a bitmap.

        The returned scale is the one actually used, which may be less than the
        one asked for: a large block at 2x is four times the pixels, and
        clamping to the surface cap keeps a big shadowed block rendering
        slightly soft instead of failing over to Qt mid-page.
        """
        content_w, content_h = self.content_size(spec)
        left, top, right, bottom = self.bleed(spec)

        # Size the surface from the width the text is actually laid out at, so
        # a centred line is centred in the pixels that get returned. `spec` has
        # to go in for the same reason it does at draw time: if the surface is
        # sized from the box while the text is laid out wider, the last word is
        # drawn off the edge and clipped away.
        layout_width = self._layout_width(content_w, spec)
        padded_w = layout_width + 2 * self.measurer.document_margin

        logical_w = padded_w + left + right
        logical_h = content_h + top + bottom

        base_w = int(math.ceil(logical_w)) or 1
        base_h = int(math.ceil(logical_h)) or 1

        if base_w * base_h > MAX_SURFACE_PIXELS:
            raise SurfaceTooLarge(
                f"{base_w}x{base_h} exceeds the {MAX_SURFACE_PIXELS}px surface cap. "
                "Render long strips in chunks rather than as one surface."
            )

        # Clamp rather than raise: at 1x this block already fits, so the only
        # question is how finely to sample it. Dropping to Qt for one block
        # mid-page would be a visible change of typeface, which is worse than
        # rendering that block at 1x.
        scale = max(1.0, min(float(scale), MAX_RENDER_SCALE))
        while scale > 1.0 and (math.ceil(logical_w * scale)
                               * math.ceil(logical_h * scale)) > MAX_SURFACE_PIXELS:
            scale -= 0.25

        width = int(math.ceil(logical_w * scale)) or 1
        height = int(math.ceil(logical_h * scale)) or 1

        surface = skia.Surface.MakeRasterN32Premul(width, height)
        canvas = surface.getCanvas()
        canvas.clear(skia.Color4f(0, 0, 0, 0))
        if scale != 1.0:
            # Everything below draws in logical units; this is the only place
            # that knows about the device scale.
            canvas.scale(scale, scale)

        origin = (left, top)
        if spec.style.vertical:
            self._draw_vertical(canvas, spec, origin, content_w)
        else:
            self._draw_horizontal(canvas, spec, origin, content_w)

        image = surface.makeImageSnapshot()
        rgba = image.convert(
            colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kUnpremul_AlphaType
        ).toarray()
        return rgba, origin, scale

    # -- horizontal --------------------------------------------------------

    def _layout_width(self, content_w: float, spec: Optional[TextRenderSpec] = None) -> float:
        """Inner width to lay out at, given a measured outer box width.

        When the source document does not wrap, the width is widened to Skia's
        own measurement of the longest line so that Skia cannot wrap either.
        `LAYOUT_SLACK_PX` alone is not enough for that: it was fitted against
        Skia-measured boxes, and a Qt-measured box runs about 1.1-1.3 px
        narrower than Skia reads the same string, which is over the slack.
        """
        inner = content_w - 2 * self.measurer.document_margin
        width = max(1.0, math.ceil(inner) + LAYOUT_SLACK_PX)
        if spec is not None and not spec.soft_wrapped:
            for line in spec.text.split("\n"):
                natural_w, _ = self.measurer._natural_size(line, spec.style)
                width = max(width, math.ceil(natural_w) + LAYOUT_SLACK_PX)
        return width

    def _draw_horizontal(self, canvas, spec, origin, content_w):
        """Paint one line at a time, advancing by the spaced line height.

        Not as a single Paragraph: `line_spacing` is applied to the measured
        height, and skia-python's StrutStyle cannot express a height multiplier
        to make the painted lines match. Painting a multi-line block as one
        Paragraph therefore draws it tight at the top of a box measured tall.
        Placing each line puts the ink exactly where the measurement said it
        would be.
        """
        margin = self.measurer.document_margin
        x = origin[0] + margin
        y = origin[1] + margin
        layout_width = self._layout_width(content_w, spec)

        for line in spec.text.split("\n"):
            line_spec = _with_text(spec, line)
            _, natural_height = self.measurer._natural_size(line, spec.style)

            if spec.shadow is not None:
                self._paint_pass(canvas, line_spec, x, y, layout_width,
                                 self._shadow_paint(spec), ink=spec.shadow.color)
            for outline in sorted(spec.outlines, key=lambda o: o.width, reverse=True):
                if outline.width <= 0:
                    continue
                self._paint_pass(canvas, line_spec, x, y, layout_width,
                                 self._outline_paint(outline), ink=outline.color,
                                 outline=outline)
            painted = self._paint_pass(canvas, line_spec, x, y, layout_width,
                                       self._fill_paint(spec), ink=spec.fill_color,
                                       run_colors=True)

            # One source line can still occupy several visual lines when it is
            # too wide for the box. Advancing by a single line height then
            # paints the *next* source line on top of this one's continuation —
            # two lines of text superimposed. Advance by what was really drawn.
            y += natural_height * spec.style.line_spacing * _visual_lines(
                painted, natural_height)

    def _paint_pass(self, canvas, spec, x, y, layout_width, paint, ink=None,
                    outline=None, run_colors=False):
        paragraph = self._paragraph(spec, paint, layout_width, ink=ink,
                                    outline=outline, run_colors=run_colors)
        paragraph.paint(canvas, x, y)
        return paragraph

    def _paragraph(self, spec: TextRenderSpec, paint, layout_width: float, ink=None,
                   outline: Optional[OutlineLayer] = None, run_colors: bool = False):
        """Lay `spec.text` out, one pushStyle/pop per character run.

        `paint` is the pass's paint — shadow, one outline width, or the fill.
        When `outline` is given, only the runs that outline actually covers get
        it; everything else is drawn fully transparent. That mirrors what the Qt
        path does (clone the document, hide it, then colour the covered range),
        and it is what stops a selection-scoped outline stroking the whole
        block.
        """
        style = spec.style
        text = spec.text
        paragraph_style = skia.textlayout.ParagraphStyle()
        paragraph_style.setTextAlign(_skia_align(style.alignment))
        paragraph_style.setTextStyle(self._text_style(spec, None, paint, ink))

        builder = skia.textlayout.ParagraphBuilder.make(
            paragraph_style, _font_collection(), _unicode()
        )

        # The directional isolate has to wrap the whole run sequence, not each
        # run, or every run becomes its own bidi paragraph.
        opening, closing = direction_isolates(style)
        if opening:
            builder.addText(opening)

        for run in spec.runs_for(text):
            fragment = text[run.start:run.end]
            if not fragment:
                continue
            covered = outline is None or outline.covers(run.start, run.end)
            run_paint = paint if covered else _transparent_paint()
            run_ink = ink if covered else None
            if run_colors and covered and run.color and spec.gradient is None:
                # The fill pass honours each run's own colour. A gradient is
                # item-wide by design and replaces per-range colours while on,
                # which is what the Qt path does too.
                run_paint = _solid_paint(run.color)
                run_ink = run.color
            builder.pushStyle(self._text_style(spec, run, run_paint, run_ink))
            builder.addText(fragment)
            builder.pop()

        if closing:
            builder.addText(closing)

        paragraph = builder.Build()
        paragraph.layout(layout_width)
        return paragraph

    def _text_style(self, spec: TextRenderSpec, run: Optional[CharRun], paint, ink):
        """A Skia TextStyle for one run, falling back to the block's own style."""
        style = spec.style
        pick = lambda attr, default: (
            default if run is None or getattr(run, attr) is None
            else getattr(run, attr)
        )

        family = pick("font_family", style.font_family) or style.font_family
        bold = bool(pick("bold", style.bold))
        italic = bool(pick("italic", style.italic))
        underline = bool(pick("underline", style.underline))
        size = float(pick("font_size", style.font_size))

        text_style = skia.textlayout.TextStyle()
        text_style.setFontFamilies(self.measurer._families(replace(style, font_family=family)))
        text_style.setFontSize(self.measurer.points_to_pixels(size))
        text_style.setFontStyle(_font_style(replace(style, bold=bold, italic=italic)))
        if style.letter_spacing:
            text_style.setLetterSpacing(float(style.letter_spacing))
        if underline:
            text_style.setDecoration(skia.textlayout.TextDecoration.kUnderline)
            # Skia does not take the decoration colour from the foreground
            # paint the way it takes it from setColor, so without this the
            # underline is laid out and never drawn.
            text_style.setDecorationColor(
                _skia_color(ink) if ink is not None else skia.ColorBLACK
            )
        text_style.setForegroundPaint(paint)
        return text_style

    # -- paints ------------------------------------------------------------

    def _fill_paint(self, spec: TextRenderSpec):
        paint = skia.Paint(AntiAlias=True)
        paint.setStyle(skia.Paint.kFill_Style)
        if spec.gradient is not None:
            paint.setShader(self._gradient_shader(spec))
        else:
            paint.setColor(_skia_color(spec.fill_color))
        return paint

    def _outline_paint(self, outline: OutlineLayer):
        paint = skia.Paint(AntiAlias=True)
        paint.setStyle(skia.Paint.kStroke_Style)
        # Doubled: Skia centres the stroke on the contour, so only half shows
        # once the fill lands on top. See the module docstring.
        paint.setStrokeWidth(float(outline.width) * 2.0)
        paint.setStrokeJoin(skia.Paint.kRound_Join)
        paint.setColor(_skia_color(outline.color, default=(255, 255, 255, 255)))
        return paint

    def _shadow_paint(self, spec: TextRenderSpec):
        shadow = spec.shadow
        paint = skia.Paint(AntiAlias=True)
        paint.setStyle(skia.Paint.kFill_Style)
        paint.setColor(_skia_color(shadow.color))
        if shadow.blur > 0:
            # See SHADOW_BLUR_TO_SIGMA — the conversion is fitted, because
            # Qt's blur radius is not a standard deviation.
            sigma = max(0.0, shadow.blur * SHADOW_BLUR_TO_SIGMA)
            paint.setMaskFilter(
                skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, sigma)
            )
        paint.setImageFilter(
            skia.ImageFilters.Offset(float(shadow.offset[0]), float(shadow.offset[1]))
        )
        return paint

    def _gradient_shader(self, spec: TextRenderSpec):
        content_w, content_h = self.content_size(spec)
        angle = math.radians(spec.gradient.angle)
        # A line through the box centre at `angle`, long enough to span it —
        # matching gradient_line() in modules/rendering/text_effects.py, which
        # is what the Qt path uses.
        half = math.hypot(content_w, content_h) / 2.0
        cx, cy = content_w / 2.0, content_h / 2.0
        dx, dy = math.cos(angle) * half, math.sin(angle) * half
        return skia.GradientShader.MakeLinear(
            points=[(cx - dx, cy - dy), (cx + dx, cy + dy)],
            colors=[_skia_color(spec.fill_color), _skia_color(spec.gradient.color)],
        )

    # -- vertical ----------------------------------------------------------

    def _draw_vertical(self, canvas, spec, origin, content_w):
        """Stack clusters down each column, columns laid out right to left.

        Right to left because that is how vertical CJK reads, and it is what
        `VerticalTextDocumentLayout` does on the Qt side.
        """
        style = spec.style
        margin = self.measurer.document_margin
        columns = spec.text.split("\n")

        column_x = origin[0] + content_w - margin
        for column in columns:
            clusters = _vertical_clusters(column)
            widest = 0.0
            for cluster in clusters:
                cluster_w, _ = self.measurer._natural_size(cluster, style)
                widest = max(widest, cluster_w)
            column_x -= widest * style.line_spacing

            y = origin[1] + margin
            for cluster in clusters:
                cluster_w, cluster_h = self.measurer._natural_size(cluster, style)
                cluster_layout_w = math.ceil(cluster_w) + LAYOUT_SLACK_PX
                cluster_spec = TextRenderSpec(
                    text=cluster,
                    style=_horizontal(style),
                    fill_color=spec.fill_color,
                    outlines=spec.outlines,
                    shadow=spec.shadow,
                    gradient=spec.gradient,
                )
                # Centre each glyph in its column, as vertical text does.
                x = column_x + (widest - cluster_w) / 2.0
                if spec.shadow is not None:
                    self._paint_pass(canvas, cluster_spec, x, y, cluster_layout_w,
                                     self._shadow_paint(cluster_spec),
                                     ink=spec.shadow.color)
                for outline in sorted(spec.outlines, key=lambda o: o.width, reverse=True):
                    if outline.width <= 0:
                        continue
                    self._paint_pass(canvas, cluster_spec, x, y, cluster_layout_w,
                                     self._outline_paint(outline), ink=outline.color,
                                     outline=outline)
                self._paint_pass(canvas, cluster_spec, x, y, cluster_layout_w,
                                 self._fill_paint(cluster_spec), ink=spec.fill_color,
                                 run_colors=True)
                y += cluster_h


def _with_text(spec: TextRenderSpec, text: str) -> TextRenderSpec:
    """The same spec carrying one line of its text."""
    from dataclasses import replace

    return replace(spec, text=text)


def _horizontal(style: TextStyle) -> TextStyle:
    """The same style with vertical off, for drawing one stacked cluster."""
    from dataclasses import replace

    return replace(style, vertical=False)


def _skia_align(alignment):
    mapping = {
        Alignment.Left: skia.textlayout.TextAlign.kLeft,
        Alignment.Right: skia.textlayout.TextAlign.kRight,
        Alignment.Center: skia.textlayout.TextAlign.kCenter,
        Alignment.Justify: skia.textlayout.TextAlign.kJustify,
    }
    try:
        return mapping[Alignment(int(alignment))]
    except (ValueError, KeyError, TypeError):
        return skia.textlayout.TextAlign.kCenter


def _visual_lines(paragraph, natural_height: float) -> int:
    """How many lines a laid-out paragraph actually occupies.

    skia-python 144 exposes no line count on `Paragraph` — only `Height` — so
    it is divided out. Rounding rather than flooring because a wrapped
    paragraph's height is a whole multiple of the line height up to float
    error, and never less than one line.
    """
    if natural_height <= 0:
        return 1
    try:
        height = float(paragraph.Height)
    except (AttributeError, TypeError, ValueError):
        return 1
    return max(1, int(round(height / natural_height)))


def _solid_paint(color):
    paint = skia.Paint(AntiAlias=True)
    paint.setStyle(skia.Paint.kFill_Style)
    paint.setColor(_skia_color(color))
    return paint


def _transparent_paint():
    """Draws nothing. Used to hide runs an outline does not cover."""
    paint = skia.Paint(AntiAlias=True)
    paint.setStyle(skia.Paint.kFill_Style)
    paint.setColor(skia.Color4f(0, 0, 0, 0).toColor())
    return paint
