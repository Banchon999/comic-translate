"""Toon Studio home screen and settings: what phase E changed.

The home screen leads with the active workspace and two action cards; the
settings sections are icon buttons sharing the tool buttons' states; the
Glossary page's toolbar sits above the table and counts conflicting overlaps.
"""

import pytest
from PySide6 import QtWidgets

from modules.utils.glossary import GlossaryEntry
from modules.utils.workspaces import Workspace


class TestHomeScreen:
    @pytest.fixture
    def home(self, qapp):
        from app.ui.startup_home import StartupHomeScreen

        screen = StartupHomeScreen()
        screen.apply_theme(True)
        yield screen
        screen.deleteLater()

    def test_the_heading_names_the_workspace(self, home):
        home.set_workspace_summary("Gatekeeper", "Korean → Thai  ·  Glossary: Gatekeeper")
        assert home._new_hdr.text() == "Gatekeeper"
        assert "Korean → Thai" in home._summary.text()
        assert not home._summary.isHidden()

    def test_no_workspace_falls_back_to_a_plain_heading(self, home):
        home.set_workspace_summary("Gatekeeper", "x")
        home.set_workspace_summary("")
        assert home._new_hdr.text() == home.tr("Start translating")
        assert home._summary.isHidden()

    def test_the_new_project_card_leads_and_still_starts_a_project(self, home, qapp):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        assert home._card_new._primary and not home._card_open._primary
        assert not home._card_new._preview.pixmap().isNull()
        emitted = []
        home.sig_open_files.connect(emitted.append)
        press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        home._card_new.mousePressEvent(press)
        assert emitted == [[]]

    def test_the_cards_follow_the_theme(self, home):
        from core import theme_tokens

        home.apply_theme(False)
        assert theme_tokens.LIGHT["accent"].lower() in home._card_new.styleSheet().lower()
        home.apply_theme(True)
        assert theme_tokens.DARK["accent"].lower() in home._card_new.styleSheet().lower()


class TestSettingsNav:
    @pytest.fixture
    def ui(self, qapp):
        from app.ui.settings.settings_page import SettingsPage

        page = SettingsPage()
        yield page.ui
        page.deleteLater()

    def test_every_section_has_an_icon_and_one_is_selected(self, ui):
        assert len(ui.nav_cards) == ui.stacked_widget.count() - 1 or len(ui.nav_cards) == 10
        assert all(not b.icon().isNull() for b in ui.nav_cards)
        assert [b.isChecked() for b in ui.nav_cards].count(True) == 1
        assert ui.nav_cards[0].isChecked()

    def test_clicking_a_section_switches_the_page_and_the_selection(self, ui):
        ui.nav_cards[2].click()
        assert ui.stacked_widget.currentIndex() == 2
        assert ui.nav_cards[2].isChecked() and not ui.nav_cards[0].isChecked()
        assert ui.current_highlighted_nav is ui.nav_cards[2]


def test_the_glossary_toolbar_counts_conflicting_overlaps(qapp):
    from app.ui.settings.glossary_page import GlossaryPage

    page = GlossaryPage()
    page.manager.save = lambda: None
    page.manager.entries = [GlossaryEntry("김철수", "คิมชอลซู"), GlossaryEntry("철수", "เชลซี")]
    page.refresh_table()
    assert page.overlaps_button.text().endswith("2")
    assert page.overlaps_button.property("dayu_type") == "danger"
    page.manager.entries = [GlossaryEntry("김철수", "คิมชอลซู"), GlossaryEntry("철수", "ชอลซู")]
    page.refresh_table()
    assert page.overlaps_button.property("dayu_type") == "default"
    page.deleteLater()


def test_the_window_tells_the_home_screen_its_workspace(qapp):
    import controller as controller_mod

    win = controller_mod.ComicTranslate()
    try:
        manager = win.file_tree_panel.workspaces
        manager.save(Workspace(name="Gatekeeper", source_language="Korean", target_language="Thai",
                               glossary_profile="Gatekeeper"))
        manager.set_active("Gatekeeper")
        win.refresh_home_summary()
        assert win.startup_home._new_hdr.text() == "Gatekeeper"
        assert "Gatekeeper" in win.startup_home._summary.text()
    finally:
        # The manager is a process-wide singleton: leave no workspace behind.
        win.file_tree_panel.workspaces.set_active("")
        win.file_tree_panel.workspaces.delete("Gatekeeper")
        win._skip_close_prompt = True
        win.close()
