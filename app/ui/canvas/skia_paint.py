"""Draw a `TextBlockItem` through the Skia renderer.

The adapter between the Qt item and `core.skia_render`, which knows nothing
about Qt. It reads the item's current visual state into a plain
`TextRenderSpec`, renders an RGBA array, and blits it through the painter the
scene handed us — so the item stays an ordinary `QGraphicsTextItem` as far as
selection, handles, editing, undo and the layer panels are concerned. Only the
pixels change.

The QImage wraps the numpy buffer rather than copying it, so the array has to
outlive the draw. It is kept alive on the returned image via an attribute; a
local would be collected while Qt still held the pointer, which shows up as
torn or garbage text rather than a crash.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

from PySide6 import QtCore
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter

from core import render_guard
from core.enums import Alignment, LayoutDirection
from core.skia_render import (
    CharRun,
    GradientSpec,
    OutlineLayer,
    ShadowSpec,
    SkiaTextRenderer,
    SurfaceTooLarge,
    TextRenderSpec,
)
from core.text_measure import TextStyle

logger = logging.getLogger(__name__)

#: Reasons already reported. A paint failure repeats on every frame, and one
#: bad item would otherwise fill the log at the refresh rate.
_reported: set[str] = set()

_renderer: Optional[SkiaTextRenderer] = None

#: The crash guard only wraps the first render of the session.
_first_render_done = False


def _enum_int(value, default: int) -> int:
    """A Qt enum, a core IntEnum or a plain number as an int.

    `int()` alone is not enough: PySide6's `Qt.LayoutDirection` is not an
    IntEnum and raises. This is the conversion in the other direction from
    `app.ui.qt_values`.
    """
    if value is None:
        return default
    inner = getattr(value, "value", value)
    try:
        return int(inner)
    except (TypeError, ValueError):
        return default


def renderer() -> SkiaTextRenderer:
    global _renderer
    if _renderer is None:
        _renderer = SkiaTextRenderer()
    return _renderer


def _hex(color) -> Optional[str]:
    """A QColor (or anything colour-shaped) as the hex string core wants."""
    if color is None:
        return None
    name = getattr(color, "name", None)
    if callable(name):
        try:
            from PySide6.QtGui import QColor

            return color.name(QColor.NameFormat.HexArgb)
        except Exception:
            return name()
    return str(color)


def spec_for_item(item) -> TextRenderSpec:
    """Read a TextBlockItem's visual state into a Qt-free description."""
    document_font = item.document().defaultFont()

    style = TextStyle(
        font_family=document_font.family(),
        # pointSizeF is -1 when the font was set in pixels; the item always
        # sets points, and font_size is the value the rest of the app agrees on.
        font_size=float(item.font_size),
        bold=bool(item.bold),
        italic=bool(item.italic),
        underline=bool(item.underline),
        line_spacing=float(getattr(item, "line_spacing", 1.0) or 1.0),
        letter_spacing=float(getattr(item, "letter_spacing", 0.0) or 0.0),
        alignment=_alignment(item),
        direction=LayoutDirection(_enum_int(item.direction, LayoutDirection.LeftToRight)),
        vertical=bool(getattr(item, "vertical", False)),
    )

    outlines = tuple(
        OutlineLayer(
            width=float(o.width),
            color=_hex(o.color) or "#ffffff",
            start=int(getattr(o, "start", 0) or 0),
            end=int(o.end) if getattr(o, "end", None) is not None else None,
        )
        for o in getattr(item, "selection_outlines", []) or []
        if float(o.width) > 0
    )

    shadow = None
    if getattr(item, "shadow_enabled", False):
        offset = getattr(item, "shadow_offset", (4.0, 4.0)) or (4.0, 4.0)
        shadow = ShadowSpec(
            color=_hex(getattr(item, "shadow_color", None)) or "#000000",
            offset=(float(offset[0]), float(offset[1])),
            blur=float(getattr(item, "shadow_blur", 0.0) or 0.0),
        )

    gradient = None
    if getattr(item, "gradient_enabled", False):
        gradient = GradientSpec(
            color=_hex(getattr(item, "gradient_color", None)) or "#ffffff",
            angle=float(getattr(item, "gradient_angle", 90.0) or 90.0),
        )

    rect = item.text_rect()
    text = _plain_text(item)
    return TextRenderSpec(
        text=text,
        char_runs=_char_runs(item, text),
        style=style,
        fill_color=_hex(getattr(item, "text_color", None)) or "#000000",
        outlines=outlines,
        shadow=shadow,
        gradient=gradient,
        box=(rect.width(), rect.height()) if rect.width() > 0 else None,
        soft_wrapped=_soft_wraps(item, text),
    )


def _soft_wraps(item, text: str) -> bool:
    """Does the document lay a source line out over more than one visual line?

    Asked of Qt because Qt owns the document, and answered by counting laid-out
    lines rather than by comparing widths: `QTextDocument.idealWidth()` is the
    width of the *longest wrapped line* once wrapping happens, so it reads
    narrower than the text width exactly when the text did wrap, which is the
    wrong way round to test against.

    Defaulting to True on any surprise keeps the previous behaviour, since the
    only thing this unlocks is permission to widen the layout box.
    """
    try:
        document = item.document()
        laid_out = sum(
            document.findBlockByNumber(index).layout().lineCount()
            for index in range(document.blockCount())
        )
    except Exception:
        return True
    if laid_out <= 0:
        return True
    return laid_out > text.count("\n") + 1


def _alignment(item) -> Alignment:
    try:
        return Alignment(_enum_int(item.alignment, Alignment.Center))
    except ValueError:
        return Alignment.Center


def paint_item(painter: QPainter, item) -> bool:
    """Draw `item` with Skia. False if it could not be drawn this way.

    Returning False rather than raising lets the caller fall back to the Qt
    painter for this one frame: a text block that cannot be rendered should
    look wrong at worst, never take the editor down mid-paint.
    """
    # Only around the *first* render of the session: enough to catch a crash
    # that reproduces, and free on a page with forty blocks. If the process
    # dies inside the call below there is no exception and no exit path, so the
    # marker staying on disk is the only evidence the next launch will have.
    global _first_render_done
    if _first_render_done:
        return _paint_item(painter, item)

    render_guard.mark_render_started()
    try:
        return _paint_item(painter, item)
    finally:
        # Reached however the render ended — drawn, refused, or raised. Any of
        # those means the process survived it, which is all the marker claims.
        render_guard.mark_render_finished()
        _first_render_done = True


def _paint_item(painter: QPainter, item) -> bool:
    """The render itself, with the crash guard already handled by the caller."""
    try:
        spec = spec_for_item(item)
        rgba, (offset_x, offset_y), scale = renderer().render(
            spec, scale=_device_scale(painter)
        )
    except SurfaceTooLarge as exc:
        _report_once("surface-too-large", exc)
        return False
    except Exception as exc:
        # Reported, not swallowed. A silent fallback here looks exactly like
        # the Skia path working, which is how a plain TypeError in the adapter
        # went unnoticed while every render quietly came from Qt.
        _report_once(f"{type(exc).__name__}: {exc}", exc)
        return False

    height, width = rgba.shape[:2]
    if width <= 0 or height <= 0:
        return True

    buffer = rgba if rgba.flags["C_CONTIGUOUS"] else rgba.copy()
    image = QImage(
        buffer.data, width, height, buffer.strides[0], QImage.Format.Format_RGBA8888
    )
    # Qt does not take ownership of the buffer; without this the array is
    # collected while the image still points at it.
    image._ct_buffer = buffer

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    # The raster carries `scale` device pixels per logical pixel, so it is
    # placed by logical rectangle rather than at a point. Drawing it at a point
    # would put it down one device pixel per sample and blow the block up by
    # the scale factor.
    painter.drawImage(
        QRectF(-offset_x, -offset_y, width / scale, height / scale),
        image,
        QRectF(0, 0, width, height),
    )
    painter.restore()
    return True


def _device_scale(painter: QPainter) -> float:
    """Device pixels per logical pixel under the painter's current transform.

    The export renderer draws the scene into a surface twice the page size and
    scales the result back down, which sharpens glyphs Qt rasterises into it.
    A Skia raster made at 1x is *upscaled* by that same transform instead, so
    the pass that sharpens Qt's text was softening Skia's — measurably: 56%
    more half-lit edge pixels per unit of ink, and visible as a grey halo at
    any zoom. Canvas zoom has the same shape of problem.

    The larger of the two axes, so anisotropic or rotated transforms sample at
    least finely enough; 1.0 on anything unreadable.
    """
    try:
        transform = painter.transform()
        scale = max(
            math.hypot(transform.m11(), transform.m12()),
            math.hypot(transform.m21(), transform.m22()),
        )
    except Exception:
        return 1.0
    if not math.isfinite(scale) or scale <= 0:
        return 1.0
    return scale


def _report_once(key: str, exc: BaseException) -> None:
    if key in _reported:
        return
    _reported.add(key)
    logger.warning(
        "Skia text painting failed, falling back to Qt for this item: %s",
        exc,
        exc_info=True,
    )


# ---------------------------------------------------------------------------
# Per-range character formats
# ---------------------------------------------------------------------------

def _plain_text(item) -> str:
    """The item's text with Qt's separators normalised to newlines.

    `toPlainText()` uses U+2029 between paragraphs and U+2028 for a soft break;
    every offset downstream is counted against this normalised form.
    """
    return (
        item.toPlainText()
        .replace("\u2028", "\n")
        .replace("\u2029", "\n")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def _char_runs(item, text: str) -> tuple[CharRun, ...]:
    """Walk the item's document into spans that share a character format.

    Returns an empty tuple when the block is uniform, when nothing can be read,
    or when the walk does not line up with `text` — the renderer then styles
    the whole block at once, which is what it did before runs existed. Runs
    whose offsets are even slightly wrong would paint the right styles onto the
    wrong words, so a mismatch degrades rather than guesses.
    """
    try:
        document = item.document()
    except Exception:
        return ()
    if document is None:
        return ()

    runs: list[CharRun] = []
    position = 0
    block = document.begin()
    first_block = True

    while block.isValid():
        if not first_block:
            position += 1  # the newline that joins two blocks
        first_block = False

        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid():
                fragment_text = fragment.text() or ""
                if fragment_text:
                    runs.append(
                        _run_from_format(
                            position, position + len(fragment_text),
                            fragment.charFormat(),
                        )
                    )
                    position += len(fragment_text)
            iterator += 1
        block = block.next()

    if position != len(text):
        # The walk and the plain text disagree; do not risk misplacing styles.
        return ()
    if len(runs) <= 1:
        return ()
    return tuple(runs)


def _run_from_format(start: int, end: int, char_format) -> CharRun:
    font = char_format.font()
    brush = char_format.foreground()
    color = None
    if brush is not None and brush.style() != QtCore.Qt.BrushStyle.NoBrush:
        color = _hex(brush.color())
    size = font.pointSizeF()
    return CharRun(
        start=start,
        end=end,
        font_family=font.family() or None,
        font_size=float(size) if size and size > 0 else None,
        color=color,
        bold=font.bold(),
        italic=font.italic(),
        underline=font.underline(),
    )
