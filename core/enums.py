"""Enumerations that used to be Qt flags.

Values deliberately match the Qt constants they replace, so the conversion at
the UI boundary is `Qt.LayoutDirection(direction)` rather than a lookup table
that can drift. `IntEnum` rather than `Enum` for the same reason: a Qt call
that still receives one of these keeps working.
"""

from __future__ import annotations

from enum import IntEnum


class LayoutDirection(IntEnum):
    """Mirrors `Qt.LayoutDirection`."""

    LeftToRight = 0
    RightToLeft = 1
    Auto = 2


class Alignment(IntEnum):
    """Mirrors the horizontal half of `Qt.AlignmentFlag`.

    Only the horizontal flags appear here because that is all the renderer
    ever sets — vertical placement comes from the block box, not the format.
    """

    Left = 0x0001
    Right = 0x0002
    Center = 0x0004
    Justify = 0x0008


class TextDirection(IntEnum):
    """Writing mode of a block, as detection reports it.

    Distinct from `LayoutDirection`, which is about RTL scripts. A block can be
    vertical *and* right-to-left; `TextBlock.source_lang_direction` combines
    them into the `'ver_rtl'` / `'hor_ltr'` strings the OCR engines expect.
    """

    Horizontal = 0
    Vertical = 1
