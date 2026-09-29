"""Toon Studio icons: every icon the code asks for exists and follows the theme.

MIcon recolours an SVG by replacing ``#555555`` in its text with the theme's
icon colour (or the hover/accent colour). An icon drawn in anything else is
stuck at that colour in both modes — three of the old icons were, including
the Type tool's, which rendered black on the dark theme.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "resources" / "static"

# Icons used as fixed-colour images rather than through MIcon: the stylesheet's
# checkbox marks, the rotate cursor, and the file-type logos.
FIXED_COLOUR = {
    "check.svg", "minus.svg", "circle.svg", "sphere.svg",
    "rotate-arrow-top.svg", "psd-file.svg", "ct-file-icon.svg", "loading.svg", "empty.svg",
}


def _referenced_svgs():
    names = set()
    for path in list((REPO / "app").rglob("*.py")) + [REPO / "controller.py", REPO / "comic.py"]:
        names.update(re.findall(r"[\"']([A-Za-z0-9_.\-]+\.svg)[\"']", path.read_text(encoding="utf-8")))
    return sorted(names)


REFERENCED = _referenced_svgs()


def test_the_scan_found_the_tool_icons():
    for name in ("pan_tool.svg", "brush-fill.svg", "wand.svg", "lasso.svg", "type-text.svg", "layers.svg"):
        assert name in REFERENCED


@pytest.mark.parametrize("name", REFERENCED)
def test_every_referenced_icon_exists(name):
    assert (STATIC / name).is_file(), f"code asks for {name}, which is not in resources/static"


@pytest.mark.parametrize("name", [n for n in REFERENCED if n not in FIXED_COLOUR])
def test_every_themed_icon_uses_the_colour_micon_replaces(name):
    text = (STATIC / name).read_text(encoding="utf-8")
    assert "#555555" in text, f"{name} would not follow the theme"


def test_the_icon_files_match_the_table():
    out = subprocess.run(
        [sys.executable, "scripts/build_icons.py", "--check"], cwd=REPO, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stdout + out.stderr


def test_every_icon_in_the_table_parses(qapp):
    from PySide6.QtSvg import QSvgRenderer

    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import build_icons
    finally:
        sys.path.pop(0)
    bad = [name for name in build_icons.icons() if not QSvgRenderer(str(STATIC / name)).isValid()]
    assert bad == []


def test_micon_recolours_a_new_icon(qapp):
    from app.ui.dayu_widgets.qt import MPixmap

    pix = MPixmap("brush-fill.svg", "#ff0000")
    image = pix.toImage()
    colours = {image.pixelColor(x, y).name() for x in range(0, image.width(), 2) for y in range(0, image.height(), 2)
               if image.pixelColor(x, y).alpha() > 200}
    assert "#ff0000" in colours


class TestToolButtonStates:
    @pytest.fixture
    def calls(self, monkeypatch):
        from app.ui.dayu_widgets import tool_button

        recorded = []
        real = tool_button.MIcon

        def spy(path, color=None):
            recorded.append((path, color))
            return real(path, color)

        monkeypatch.setattr(tool_button, "MIcon", spy)
        return recorded

    def _button(self, checked):
        from app.ui.dayu_widgets.tool_button import MToolButton

        button = MToolButton().svg("brush-fill.svg")
        button.setCheckable(True)
        button.setChecked(checked)
        return button

    def test_hover_brightens_instead_of_using_the_accent(self, qapp, calls):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QEnterEvent

        from app.ui.dayu_widgets import dayu_theme

        button = self._button(checked=False)
        calls.clear()
        button.enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
        assert calls[-1] == ("brush-fill.svg", dayu_theme.primary_text_color)
        button.deleteLater()

    def test_the_selected_tool_keeps_the_accent_under_the_pointer(self, qapp, calls):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QEnterEvent

        from app.ui.dayu_widgets import dayu_theme

        button = self._button(checked=True)
        assert calls[-1] == ("brush-fill.svg", dayu_theme.primary_color)
        calls.clear()
        button.enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
        assert calls == []
        button.deleteLater()
