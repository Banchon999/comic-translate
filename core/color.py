"""Colour handling without QColor.

Colours cross this codebase in three shapes: an ``(r, g, b)`` or
``(r, g, b, a)`` tuple from pixel analysis (``extract_foreground_color``), a
hex string from the settings colour pickers, and ``()``/``None``/``""`` for
"nothing detected". `TextBlock.font_color` is typed `str|tuple` and carries all
of them.

`normalize_color` collapses that to one canonical form — a ``#RRGGBB`` or
``#RRGGBBAA`` string — or `None` when the value does not name a colour.

**Hex strings follow Qt's convention on input**: an 8-digit ``#AARRGGBB`` is
read alpha-first, because that is what `QColor` does with the strings this app
has been writing and reading. Output is always ``#RRGGBB``, or ``#RRGGBBAA``
when alpha is not opaque, matching the wire format in
`docs/architecture/proto`. Round-tripping an 8-digit string therefore reorders
it; that is the point, not a bug.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence, Union

ColorLike = Union[str, Sequence[int], Sequence[float], None]

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def _clamp_channel(value) -> int:
    """Coerce one channel to 0-255, the way QColor clamps rather than raising."""
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        raise ValueError(f"not a colour channel: {value!r}")
    return max(0, min(255, number))


def _from_hex(text: str) -> Optional[tuple[int, int, int, int]]:
    text = text.strip()
    if not _HEX_RE.match(text):
        return None
    digits = text[1:]

    if len(digits) in (3, 4):
        # #RGB / #ARGB — each digit doubled, alpha first, as Qt reads them.
        expanded = "".join(ch * 2 for ch in digits)
        digits = expanded

    if len(digits) == 6:
        r, g, b = (int(digits[i:i + 2], 16) for i in (0, 2, 4))
        return r, g, b, 255
    # 8 digits: #AARRGGBB, alpha first.
    a, r, g, b = (int(digits[i:i + 2], 16) for i in (0, 2, 4, 6))
    return r, g, b, a


def to_rgba(value: ColorLike) -> Optional[tuple[int, int, int, int]]:
    """Return ``(r, g, b, a)`` for anything colour-shaped, else None.

    Returns None — rather than raising — for the empty values that mean "not
    detected", because every caller treats those as "fall back to the setting".
    """
    if value is None:
        return None

    if isinstance(value, str):
        if not value.strip():
            return None
        return _from_hex(value)

    try:
        channels = list(value)
    except TypeError:
        return None

    if len(channels) not in (3, 4):
        return None
    try:
        r, g, b = (_clamp_channel(c) for c in channels[:3])
        a = _clamp_channel(channels[3]) if len(channels) == 4 else 255
    except ValueError:
        return None
    return r, g, b, a


def to_hex(value: ColorLike) -> Optional[str]:
    """Canonical ``#RRGGBB``/``#RRGGBBAA``, or None if `value` names no colour."""
    rgba = to_rgba(value)
    if rgba is None:
        return None
    r, g, b, a = rgba
    if a >= 255:
        return f"#{r:02x}{g:02x}{b:02x}"
    return f"#{r:02x}{g:02x}{b:02x}{a:02x}"


# Kept under its historical name because call sites read better with it.
normalize_color = to_hex


def is_valid(value: ColorLike) -> bool:
    return to_rgba(value) is not None


def resolve_text_color(detected: ColorLike, fallback: ColorLike) -> Optional[str]:
    """Pick between a detected text colour and the user's configured one.

    Detection wins when it produced something usable — it came from actual
    pixel analysis and is most likely right for this block. Anything else
    falls back to the setting, unparsed and unchanged in meaning.

    Returns a canonical hex string; the Qt layer wraps it in a QColor at the
    point of use.
    """
    detected_hex = to_hex(detected)
    if detected_hex is not None:
        return detected_hex
    return to_hex(fallback)
