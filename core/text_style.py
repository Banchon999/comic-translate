"""Qt-free description of a rendered text item.

The pipeline's last reason to import Qt was building `TextItemProperties` and
`OutlineInfo` so it could call `to_dict()` and drop the result into
`image_states[path]['viewer_state']['text_items_state']`. It never needed the
Qt objects — it needed the dict.

`build_text_item_state` produces that dict directly, and
`TextItemProperties.from_dict` reads it back on the Qt side: it already accepts
a colour as a string and a direction or alignment as an int, so nothing had to
change to receive one built here.

`OutlineInfo` and `OutlineType` move here whole. They were plain dataclasses
whose only Qt was the type annotation on `color`, and the Qt layer re-exports
them so the project encoder and `TextBlockItem` keep the identical types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Sequence


class OutlineType(Enum):
    Full_Document = 'full_document'
    Selection = 'selection'


@dataclass
class OutlineInfo:
    """One outlined range of a text item.

    `color` is a QColor when the Qt layer built this and a hex string when the
    pipeline did. Consumers coerce — see `app.ui.qt_values.to_qt_color` — rather
    than requiring one or the other, because both reach the same items.
    """

    start: int
    end: int
    color: Any
    width: float
    type: OutlineType


@dataclass
class TextItemState:
    """Everything needed to recreate one rendered text item.

    Field names and defaults match `TextItemProperties`, because `to_dict` on
    that class and `as_dict` here have to produce interchangeable payloads —
    `tests/test_text_item_state.py` asserts they do.
    """

    text: str = ""
    font_family: str = ""
    font_size: float = 20
    text_color: Any = None
    alignment: Any = None
    line_spacing: float = 1.2
    outline_color: Any = None
    outline_width: float = 1
    outline: bool = False
    bold: bool = False
    italic: bool = False
    underline: bool = False
    direction: Any = None

    position: tuple = (0, 0)
    rotation: float = 0
    scale: float = 1.0
    transform_origin: Optional[tuple] = None

    width: Optional[float] = None
    height: Optional[float] = None
    vertical: bool = False

    letter_spacing: float = 0.0
    shadow_enabled: bool = False
    shadow_color: Any = None
    shadow_offset: tuple = (4.0, 4.0)
    shadow_blur: float = 0.0
    gradient_enabled: bool = False
    gradient_color: Any = None
    gradient_angle: float = 90.0
    curvature: float = 0.0

    selection_outlines: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            'text': self.text,
            'font_family': self.font_family,
            'font_size': self.font_size,
            'text_color': self.text_color,
            'alignment': self.alignment,
            'line_spacing': self.line_spacing,
            'outline_color': self.outline_color,
            'outline_width': self.outline_width,
            'outline': self.outline,
            'bold': self.bold,
            'italic': self.italic,
            'underline': self.underline,
            'direction': self.direction,
            'position': self.position,
            'rotation': self.rotation,
            'scale': self.scale,
            'transform_origin': self.transform_origin,
            'width': self.width,
            'height': self.height,
            'vertical': self.vertical,
            'letter_spacing': self.letter_spacing,
            'shadow_enabled': self.shadow_enabled,
            'shadow_color': self.shadow_color,
            'shadow_offset': self.shadow_offset,
            'shadow_blur': self.shadow_blur,
            'gradient_enabled': self.gradient_enabled,
            'gradient_color': self.gradient_color,
            'gradient_angle': self.gradient_angle,
            'curvature': self.curvature,
            'selection_outlines': self.selection_outlines,
        }


def full_document_outline(text: str, color: Any, width: float) -> list[OutlineInfo]:
    """The single outline spanning a whole item, as the renderers build it."""
    return [OutlineInfo(0, len(text), color, width, OutlineType.Full_Document)]


def build_text_item_state(
    *,
    text: str,
    font_family: str,
    font_size: float,
    text_color: Any,
    alignment: Any,
    line_spacing: float,
    outline_color: Any,
    outline_width: float,
    bold: bool,
    italic: bool,
    underline: bool,
    position: Sequence[float],
    rotation: float,
    scale: float,
    transform_origin: Optional[Sequence[float]],
    width: Optional[float],
    height: Optional[float],
    direction: Any,
    vertical: bool,
    outline: bool,
    # The drawn effects. Optional because the batch renderers do not set them —
    # they render freshly translated blocks, which carry no effects yet. They
    # are in the signature so that anything reconstructing a *saved* item, and
    # every test, can build the states users actually create. Leaving them out
    # is why a doubled drop shadow went unnoticed: the harness could not
    # express a block that had one.
    letter_spacing: float = 0.0,
    shadow_enabled: bool = False,
    shadow_color: Any = None,
    shadow_offset: Sequence[float] = (4.0, 4.0),
    shadow_blur: float = 0.0,
    gradient_enabled: bool = False,
    gradient_color: Any = None,
    gradient_angle: float = 90.0,
    curvature: float = 0.0,
) -> dict:
    """State dict for one rendered block, as the batch renderers produce it.

    Keyword-only: the call sites pass twenty-odd fields, and a positional slip
    between `width`/`height` or `bold`/`italic` would be silent.
    """
    return TextItemState(
        text=text,
        font_family=font_family,
        font_size=font_size,
        text_color=text_color,
        alignment=alignment,
        line_spacing=line_spacing,
        outline_color=outline_color,
        outline_width=outline_width,
        outline=outline,
        bold=bold,
        italic=italic,
        underline=underline,
        direction=direction,
        position=tuple(position),
        rotation=rotation,
        scale=scale,
        transform_origin=tuple(transform_origin) if transform_origin else None,
        width=width,
        height=height,
        vertical=vertical,
        letter_spacing=letter_spacing,
        shadow_enabled=shadow_enabled,
        shadow_color=shadow_color,
        shadow_offset=tuple(shadow_offset),
        shadow_blur=shadow_blur,
        gradient_enabled=gradient_enabled,
        gradient_color=gradient_color,
        gradient_angle=gradient_angle,
        curvature=curvature,
        selection_outlines=(
            full_document_outline(text, outline_color, outline_width) if outline else []
        ),
    ).as_dict()
