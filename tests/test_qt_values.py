"""The core-enum to Qt-flag boundary.

`core.enums.LayoutDirection` has the same integer values as
`Qt.LayoutDirection`, which makes it tempting to pass one straight to a Qt API.
PySide6 rejects that with a TypeError naming the right value and the wrong
type, and the RTL path that does it — picking Arabic, Hebrew or Persian as the
target language — is not otherwise covered. These tests pin the conversion so
the failure cannot come back silently.
"""

import pytest

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextOption

from core.enums import Alignment, LayoutDirection
from app.ui.qt_values import to_qt_alignment, to_qt_layout_direction


@pytest.mark.parametrize("core_value, qt_value", [
    (LayoutDirection.LeftToRight, Qt.LayoutDirection.LeftToRight),
    (LayoutDirection.RightToLeft, Qt.LayoutDirection.RightToLeft),
    (LayoutDirection.Auto, Qt.LayoutDirection.LayoutDirectionAuto),
])
def test_layout_direction_converts_to_the_matching_qt_flag(core_value, qt_value):
    assert to_qt_layout_direction(core_value) == qt_value
    assert isinstance(to_qt_layout_direction(core_value), Qt.LayoutDirection)


@pytest.mark.parametrize("core_value, qt_value", [
    (Alignment.Left, Qt.AlignmentFlag.AlignLeft),
    (Alignment.Right, Qt.AlignmentFlag.AlignRight),
    (Alignment.Center, Qt.AlignmentFlag.AlignHCenter),
    (Alignment.Justify, Qt.AlignmentFlag.AlignJustify),
])
def test_alignment_converts_to_the_matching_qt_flag(core_value, qt_value):
    assert to_qt_alignment(core_value) == qt_value


def test_conversion_is_idempotent_for_values_already_qt():
    assert to_qt_layout_direction(Qt.LayoutDirection.RightToLeft) == Qt.LayoutDirection.RightToLeft
    assert to_qt_alignment(Qt.AlignmentFlag.AlignHCenter) == Qt.AlignmentFlag.AlignHCenter


def test_qt_rejects_the_unconverted_core_enum(qapp):
    """The reason this module exists.

    If a future PySide6 starts accepting the plain IntEnum this will fail, and
    the conversion becomes optional rather than load-bearing — worth knowing.
    """
    option = QTextOption()
    with pytest.raises(TypeError):
        option.setTextDirection(LayoutDirection.RightToLeft)


def test_converted_value_is_accepted(qapp):
    option = QTextOption()
    option.setTextDirection(to_qt_layout_direction(LayoutDirection.RightToLeft))
    assert option.textDirection() == Qt.LayoutDirection.RightToLeft
