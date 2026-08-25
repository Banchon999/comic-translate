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
from typing import Optional

from PySide6.QtGui import QImage, QPainter

from core.enums import Alignment, LayoutDirection
from core.skia_render import (
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
        OutlineLayer(width=float(o.width), color=_hex(o.color) or "#ffffff")
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
    return TextRenderSpec(
        text=item.toPlainText(),
        style=style,
        fill_color=_hex(getattr(item, "text_color", None)) or "#000000",
        outlines=outlines,
        shadow=shadow,
        gradient=gradient,
        box=(rect.width(), rect.height()) if rect.width() > 0 else None,
    )


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
    try:
        spec = spec_for_item(item)
        rgba, (offset_x, offset_y) = renderer().render(spec)
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
    painter.drawImage(-offset_x, -offset_y, image)
    painter.restore()
    return True


def _report_once(key: str, exc: BaseException) -> None:
    if key in _reported:
        return
    _reported.add(key)
    logger.warning(
        "Skia text painting failed, falling back to Qt for this item: %s",
        exc,
        exc_info=True,
    )
