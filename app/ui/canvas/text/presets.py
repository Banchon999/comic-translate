"""Apply a text preset to a canvas item, or capture one from it.

The preset itself is Qt-free (`modules/utils/text_presets.py`); this is the
half that knows which TextBlockItem setter each field goes through, so a
preset applied here looks exactly like the same values set from the toolbar.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor

from modules.utils.text_presets import TextPreset


def preset_label(name: str) -> str:
    """What a combo shows for a preset. Built-in names are stored in English
    (they are the key a project or setting refers to) and shown translated;
    the user's own names are shown as typed."""
    labels = {
        "Speech": QCoreApplication.translate("TextPresets", "Speech"),
        "Shout": QCoreApplication.translate("TextPresets", "Shout"),
        "Thought": QCoreApplication.translate("TextPresets", "Thought"),
        "Narration": QCoreApplication.translate("TextPresets", "Narration"),
        "SFX": QCoreApplication.translate("TextPresets", "SFX"),
    }
    return labels.get(name, name)


def _hex(color) -> str | None:
    if color is None:
        return None
    return QColor(color).name(QColor.NameFormat.HexArgb)


def apply_preset_to_item(item, preset: TextPreset, only=None) -> None:
    """Write the preset's fields onto `item` (all of them, or just `only`)."""
    values = preset.values(only)
    if not values:
        return
    # Setters act on the selection when there is one; a style is item-wide.
    cursor = item.textCursor()
    if cursor.hasSelection():
        cursor.clearSelection()
        item.setTextCursor(cursor)

    if "font_family" in values or "font_size" in values:
        item.set_font(values.get("font_family", item.font_family), values.get("font_size", item.font_size))
    if "color" in values:
        item.set_color(QColor(values["color"]))
    if "bold" in values:
        item.set_bold(bool(values["bold"]))
    if "italic" in values:
        item.set_italic(bool(values["italic"]))
    if "underline" in values:
        item.set_underline(bool(values["underline"]))
    if "line_spacing" in values:
        item.set_line_spacing(float(values["line_spacing"]))
    if "letter_spacing" in values:
        item.set_letter_spacing(float(values["letter_spacing"]))

    if values.get("outline") is False:
        item.set_outline(None, 0)
    elif any(key in values for key in ("outline", "outline_color", "outline_width")):
        color = values.get("outline_color") or item.outline_color or "#ffffff"
        width = values.get("outline_width", item.outline_width or 1.0)
        item.set_outline(QColor(color), float(width))

    if "stroke_layers" in values:
        item.set_stroke_layers(values["stroke_layers"])

    if any(key.startswith("shadow_") for key in values):
        item.set_shadow(
            values.get("shadow_enabled", item.shadow_enabled),
            QColor(values["shadow_color"]) if "shadow_color" in values else None,
            values.get("shadow_offset"),
            values.get("shadow_blur"),
        )
    if any(key.startswith("gradient_") for key in values):
        item.set_gradient(
            values.get("gradient_enabled", item.gradient_enabled),
            QColor(values["gradient_color"]) if "gradient_color" in values else None,
            values.get("gradient_angle"),
        )
    if "curvature" in values:
        item.set_curvature(float(values["curvature"]))
    if "warp_style" in values or "warp_bend" in values:
        item.set_warp(values.get("warp_style", item.warp_style), values.get("warp_bend", item.warp_bend))
    item.update()


def preset_from_item(item, name: str) -> TextPreset:
    """The item's look as a named preset. The size is left out on purpose:
    rendered text is fitted to its bubble, so a style that carried a size would
    overflow the next bubble it is applied to."""
    return TextPreset(
        name=name,
        font_family=item.font_family or None,
        color=_hex(item.text_color),
        bold=bool(item.bold),
        italic=bool(item.italic),
        underline=bool(item.underline),
        line_spacing=float(item.line_spacing),
        letter_spacing=float(getattr(item, "letter_spacing", 0.0) or 0.0),
        outline=bool(item.outline),
        outline_color=_hex(item.outline_color) if item.outline else None,
        outline_width=float(item.outline_width) if item.outline else None,
        stroke_layers=[{"color": layer.color, "width": layer.width} for layer in item.stroke_layers],
        shadow_enabled=bool(item.shadow_enabled),
        shadow_color=_hex(item.shadow_color),
        shadow_offset=tuple(float(v) for v in item.shadow_offset),
        shadow_blur=float(item.shadow_blur),
        gradient_enabled=bool(item.gradient_enabled),
        gradient_color=_hex(item.gradient_color),
        gradient_angle=float(item.gradient_angle),
        warp_style=item.warp_style or "",
        warp_bend=float(item.warp_bend or 0.0),
        curvature=float(item.curvature or 0.0),
    )
