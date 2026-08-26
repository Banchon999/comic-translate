"""Convert `core` enums into the Qt flags Qt actually accepts.

`core.enums` mirrors Qt's integer values so the mapping is trivial, but PySide6
does not accept a look-alike: passing a plain `IntEnum` to
`QTextOption.setTextDirection` raises

    TypeError: ... called with wrong argument types

even when the value is right. So every place a `core` enum reaches a Qt API has
to convert, and it converts here rather than by hand at each site.

Both functions are idempotent — handing them a real Qt flag returns it
unchanged — so a call site does not have to know which side its value came
from.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor


def to_qt_layout_direction(value) -> Qt.LayoutDirection:
    """`core.enums.LayoutDirection` (or an int, or a Qt flag) as a Qt flag."""
    if isinstance(value, Qt.LayoutDirection):
        return value
    return Qt.LayoutDirection(int(value))


def to_qt_alignment(value) -> Qt.AlignmentFlag:
    """`core.enums.Alignment` (or an int, or a Qt flag) as a Qt flag."""
    if isinstance(value, Qt.AlignmentFlag):
        return value
    return Qt.AlignmentFlag(int(value))


def to_qt_color(value) -> QColor:
    """A colour as a QColor, whichever side of the seam it came from.

    The pipeline builds outline colours as hex strings because it has no Qt;
    the canvas builds them as QColor. Both end up on the same text items, so
    the paint sites coerce rather than requiring one form.
    """
    if isinstance(value, QColor):
        return value
    if value is None:
        return QColor()
    return QColor(value)
