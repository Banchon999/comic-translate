"""Toon Studio theme: one token table, readable in both modes, used everywhere.

The tokens live Qt-free in core/theme_tokens.py; the dayu theme, the home
screen, the title bar and the panels read them. These tests pin the three
things that silently rot in a theme: a colour pair that stops being readable,
a mode missing a key the other has, and a hand-styled widget going back to a
hard-coded colour that ignores the theme.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from core import theme_tokens as tt

REPO = Path(__file__).resolve().parent.parent


def test_both_modes_define_the_same_tokens():
    assert set(tt.DARK) == set(tt.LIGHT)


@pytest.mark.parametrize("mode", ["dark", "light"])
@pytest.mark.parametrize("fg, bg, minimum", tt.CONTRAST_RULES)
def test_every_text_pair_is_readable(mode, fg, bg, minimum):
    p = tt.palette(dark=mode == "dark")
    ratio = tt.contrast(p[fg], p[bg])
    assert ratio >= minimum, f"{mode}: {fg} {p[fg]} on {bg} {p[bg]} is {ratio:.2f}:1, needs {minimum}:1"


def test_white_on_the_dark_accent_would_fail():
    # Why on_accent is dark ink: this is the pairing dayu used by default.
    assert tt.contrast("#FFFFFF", tt.DARK["accent"]) < 4.5
    assert tt.contrast(tt.DARK["on_accent"], tt.DARK["accent"]) >= 4.5


def test_palette_is_a_copy():
    p = tt.palette(dark=True)
    p["accent"] = "#000000"
    assert tt.DARK["accent"] != "#000000"


def test_rgba_formats_a_token():
    assert tt.rgba("#FF5C8A", 0.16) == "rgba(255, 92, 138, 0.16)"


def test_tokens_import_without_qt():
    code = "import sys; sys.modules['PySide6'] = None; import core.theme_tokens as t; print(t.DARK['accent'])"
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == tt.DARK["accent"]


class TestDayuThemeReadsTheTokens:
    @pytest.fixture
    def theme(self, qapp):
        from app.ui.dayu_widgets import dayu_theme

        yield dayu_theme
        dayu_theme.set_primary_color(tt.DARK["accent"])
        dayu_theme.set_theme("dark")

    @pytest.mark.parametrize("mode", ["dark", "light"])
    def test_colours_follow_the_mode(self, theme, mode):
        p = tt.palette(dark=mode == "dark")
        theme.set_primary_color(p["accent"])
        theme.set_theme(mode)
        assert theme.is_dark == (mode == "dark")
        assert theme.background_color == p["panel"]
        assert theme.background_in_color == p["raised"]
        assert theme.primary_text_color == p["text_1"]
        assert theme.secondary_text_color == p["text_2"]
        assert theme.border_color == p["line"]
        assert theme.on_primary_color == p["on_accent"]
        assert theme.error_color == p["danger"]
        assert theme.warning_color == p["warn"]
        assert theme.tokens == p

    def test_the_stylesheet_renders_with_the_new_names(self, theme):
        from app.ui.dayu_widgets.theme import get_theme_size

        values = get_theme_size()
        values.update(vars(theme))
        qss = theme.default_qss.substitute(values)
        assert tt.DARK["on_accent"].lower() in qss.lower()
        assert "@" not in re.sub(r"@media|url\([^)]*\)", "", qss), "an @token was left unsubstituted"

    def test_the_default_accent_is_the_toon_pink(self, theme):
        assert theme.primary_color == tt.DARK["accent"]


def test_the_ui_font_loads(qapp):
    from app.ui.fonts import load_ui_fonts

    assert tt.UI_FONT_FAMILY in load_ui_fonts()


def test_a_missing_font_folder_is_not_fatal(qapp, tmp_path):
    from app.ui.fonts import load_ui_fonts

    assert load_ui_fonts(str(tmp_path)) == []


def test_the_font_licence_ships_with_the_fonts():
    folder = REPO / "resources" / "fonts" / "ui"
    for name in tt.UI_FONT_FILES:
        assert (folder / name).is_file(), name
    licence = (folder / "OFL-IBMPlexSansThai.txt").read_text(encoding="utf-8")
    assert "SIL Open Font License" in licence


# Old dayu/antd accent blues that ignored the theme. None may come back.
_OLD_ACCENTS = re.compile(r"#(1890ff|1677ff|2a85ff|4da6ff|3b82f6)\b", re.IGNORECASE)
_HEX = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")


def test_no_old_accent_blue_is_left_in_the_ui():
    offenders = []
    # The Qt app's own code; dayu's colour-name constants (MTheme.blue) and the
    # sign-in web page served to the browser are not the app's theme.
    paths = list((REPO / "app" / "ui").rglob("*.py")) + list((REPO / "app" / "controllers").rglob("*.py"))
    for path in paths + [REPO / "resources" / "static" / "main.qss"]:
        if "dayu_widgets" in path.parts:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _OLD_ACCENTS.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{n}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize("relpath", ["app/ui/startup_home.py", "app/ui/search_replace_panel.py"])
def test_hand_styled_widgets_take_colours_from_tokens(relpath):
    text = (REPO / relpath).read_text(encoding="utf-8")
    assert not _HEX.findall(text), f"{relpath} hard-codes {set(_HEX.findall(text))}"


def test_the_selected_page_row_keeps_its_text_readable(qapp):
    """Found in the Phase A screenshot: the selected page name was drawn in the
    palette's highlightedText — the dark ink meant for a solid accent fill —
    on a grey row, and nearly vanished. The row is now an accent tint under
    ordinary body text."""
    from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

    from app.ui.dayu_widgets import dayu_theme
    from app.ui.list_view import PageListItemDelegate

    from PySide6.QtWidgets import QListWidget

    view = QListWidget()
    dayu_theme.apply(view)  # the app stylesheet, which sets highlightedText to the ink
    view.ensurePolished()
    option = QStyleOptionViewItem()
    option.initFrom(view)
    option.state = QStyle.StateFlag.State_Selected
    delegate = PageListItemDelegate()
    fill = delegate._background_color(option)
    text = delegate._text_color(option, selected=True, strike_out=False)
    ratio = tt.contrast(text.name(), fill.name())
    assert ratio >= 4.5, f"selected row text {text.name()} on {fill.name()} is {ratio:.2f}:1"
    view.deleteLater()
