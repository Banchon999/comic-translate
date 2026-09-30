"""Toon Studio editor layout: new places, same widgets.

Phase D moved the canvas tools into a rail, the pipeline into a step bar and
the text controls, Layers panel and a new Glossary tab into a tabbed
inspector. Every widget kept its attribute name, so the controllers did not
change; these tests check the new arrangement and the few behaviours it
added (step marks, tab/button sync, the Glossary tab, the status line).
"""

import numpy as np
import pytest

from core.editor_state import STEP_KEYS, step_flags
from modules.utils.glossary import GlossaryEntry, GlossaryIssue
from modules.utils.textblock import TextBlock


def _block(text="", translation=""):
    blk = TextBlock(text_bbox=np.array([0, 0, 10, 10]), text=text)
    blk.translation = translation
    return blk


class TestStepFlags:
    def test_an_empty_page_has_done_nothing(self):
        assert step_flags([], False, False, False) == (False,) * len(STEP_KEYS)

    def test_each_step_reads_its_own_evidence(self):
        detected = [_block()]
        assert step_flags(detected, False, False, False)[:3] == (True, False, False)
        recognized = [_block("김철수")]
        assert step_flags(recognized, False, False, False)[:3] == (True, True, False)
        translated = [_block("김철수", "คิมชอลซู")]
        assert step_flags(translated, True, True, True) == (True,) * 6

    def test_whitespace_is_not_text(self):
        assert step_flags([_block("  ", " ")], False, False, False)[1:3] == (False, False)


@pytest.fixture
def win(qapp):
    import controller as controller_mod

    window = controller_mod.ComicTranslate()
    window.image_viewer.display_image_array(np.full((300, 400, 3), 220, np.uint8))
    yield window
    window._skip_close_prompt = True
    window.close()


def test_the_layout_holds_every_old_widget_in_its_new_place(win):
    rail_tools = ("pan", "box", "type", "brush", "eraser", "paint", "fill", "aibrush", "restore", "eyedropper",
                  "marquee", "balloon", "wand", "lasso")
    def inside(widget, name):
        # Bars group their parts in rows (and the rail scrolls its tools), so
        # ask whether the bar holds the widget, not whether it is the parent.
        while widget is not None:
            if widget.objectName() == name:
                return True
            widget = widget.parentWidget()
        return False

    for key in rail_tools:
        assert inside(win.tool_buttons[key], "toonToolRail"), key
    for button in (win.delete_button, win.clear_rectangles_button, win.clear_brush_strokes_button,
                   win.file_tree_button, win.layers_button, win.webtoon_toggle):
        assert inside(button, "toonToolRail")

    def in_options_bar(widget):
        return inside(widget, "toonOptionsBar")

    assert in_options_bar(win.brush_eraser_slider)
    assert in_options_bar(win.change_all_blocks_size_diff)
    assert in_options_bar(win.clean_selection_button)

    tabs = win.inspector_tabs
    assert tabs.count() == 3
    assert tabs.widget(1) is win.layers_panel
    assert tabs.widget(2) is win.glossary_peek
    # The text controls are inside the Text tab.
    assert tabs.widget(0).isAncestorOf(win.font_dropdown)
    assert tabs.widget(0).isAncestorOf(win.s_combo)

    steps = win.hbutton_group.get_button_group().buttons()
    assert len(steps) == 6
    assert all(not b.icon().isNull() for b in steps)
    assert win.translate_button.property("dayu_type") == "primary"


def test_the_layers_button_and_tab_stay_in_step(win):
    tabs = win.inspector_tabs
    win.layers_button.setChecked(True)
    assert tabs.currentWidget() is win.layers_panel
    win.layers_button.setChecked(False)
    assert tabs.currentIndex() == 0
    tabs.setCurrentWidget(win.layers_panel)
    assert win.layers_button.isChecked()
    tabs.setCurrentWidget(win.glossary_peek)
    assert not win.layers_button.isChecked()


def test_the_step_bar_marks_what_the_page_has_been_through(win):
    steps = win.hbutton_group.get_button_group().buttons()
    win.blk_list = []
    win.refresh_step_bar()
    assert [b.property("toon_step") for b in steps] == ["todo"] * 6

    win.blk_list = [_block("김철수", "คิมชอลซู")]
    win.enable_hbutton_group()  # every operation ends here
    states = [b.property("toon_step") for b in steps]
    assert states[:3] == ["done", "done", "done"]
    assert states[3:] == ["todo", "todo", "todo"]


def test_the_glossary_tab_explains_this_page(win):
    manager = win.settings_page.ui.glossary_page.manager
    manager.save = lambda: None
    manager.enabled = True
    manager.entries = [
        GlossaryEntry(source="김철수", target="คิมชอลซู"),
        GlossaryEntry(source="철수", target="เชลซี"),
    ]
    win.blk_list = [_block("김철 수가 왔다", "ชอลซูมาแล้ว")]
    win.last_glossary_issues = [GlossaryIssue(0, "김철수", "คิมชอลซู", "김철 수")]
    win.inspector_tabs.setCurrentWidget(win.glossary_peek)
    rows = win.glossary_peek.rows.toPlainText()
    assert "김철수 → คิมชอลซู" in rows
    assert "철수 → เชลซี" in rows and "«김철수»" in rows  # shown as suppressed
    assert not win.glossary_peek.warning.isHidden()
    assert "«คิมชอลซู»" in win.glossary_peek.warning.text()


def test_the_status_line_names_the_page_and_engines(win, qapp):
    win.image_files = ["/a/1.png", "/a/2.png", "/a/3.png"]
    win.curr_img_idx = 1
    win.show()
    win.show_main_page()
    qapp.processEvents()
    bar = win.editor_status_bar
    bar.refresh()
    assert "2" in bar.page_label.text() and "3" in bar.page_label.text()
    assert bar.translator_label.text()
    assert bar.glossary_label.text()


def test_a_theme_switch_recolours_the_step_marks(win, monkeypatch):
    """Icons coloured at build time must follow the mode: done marks take the
    new mode's success colour, the run icon the new ink."""
    from app.ui.main_window import editor_chrome
    from core import theme_tokens

    win.blk_list = [_block("김철수", "คิมชอลซู")]
    win.refresh_step_bar()
    seen = []
    real = editor_chrome.MIcon
    monkeypatch.setattr(editor_chrome, "MIcon", lambda path, color=None: (seen.append((path, color)), real(path, color))[1])
    try:
        win.apply_theme(win.settings_page.ui.tr("Light"))
        assert ("step-done.svg", theme_tokens.LIGHT["ok"]) in seen
    finally:
        win.apply_theme(win.settings_page.ui.tr("Dark"))
    assert ("step-done.svg", theme_tokens.DARK["ok"]) in seen


def test_the_options_bar_follows_the_tool(win):
    win.set_tool("brush")
    assert not win.brush_options.isHidden() and win.selection_options.isHidden()
    win.set_tool("wand")
    assert win.brush_options.isHidden() and not win.selection_options.isHidden()
    win.set_tool(None)
    assert not win.brush_options.isHidden() and win.selection_options.isHidden()


def test_the_options_bar_never_sets_the_window_width(win):
    """With every section showing, the bar is wider than the canvas column;
    it must be clipped, not widen the window past the screen."""
    from PySide6.QtWidgets import QApplication, QSizePolicy

    win.show()
    win.show_main_page()
    # Every section at once: the widest the bar can get (~900 px in Thai).
    win.brush_options.setVisible(True)
    win.selection_options.setVisible(True)
    QApplication.processEvents()
    bar = win.clean_selection_button
    while bar.objectName() != "toonOptionsBar":
        bar = bar.parentWidget()
    assert bar.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored
    # Measured before the fix: the column's minimum became the bar's full
    # width and the window's minimum 1529 px.
    assert bar.parentWidget().minimumSizeHint().width() < bar.sizeHint().width()
    assert win.minimumSizeHint().width() <= 1280


def test_the_tool_rail_never_sets_the_window_height(win):
    """Every tool on the rail must not make the window taller than a laptop
    screen: the tools scroll instead, and none of them is lost."""
    from PySide6.QtWidgets import QApplication

    win.show()
    win.show_main_page()
    QApplication.processEvents()
    # Measured before the fix: 932 px, taller than a 1366×768 screen.
    assert win.minimumSizeHint().height() <= 700
    win.resize(1280, 640)
    QApplication.processEvents()
    scroll = win.tool_rail_scroll
    assert scroll.verticalScrollBar().maximum() > 0, "the tools scroll when short"
    # The panel toggles stay reachable without scrolling.
    for button in (win.file_tree_button, win.layers_button, win.webtoon_toggle):
        assert not scroll.isAncestorOf(button)
    # Every tool button still fits the rail's width next to the scroll bar.
    tools = scroll.widget()
    assert tools.minimumSizeHint().width() <= scroll.viewport().width()


@pytest.mark.parametrize("tool", ["wand", "brush", "paint", "fill", "aibrush", "restore", "eyedropper", None])
def test_the_options_bar_fits_its_column_for_each_tool(win, tool):
    """Every row the bar shows must fit a 1440 px window without squeezing
    its labels — a Modify button once clipped "Box size" and "Pick into"."""
    from PySide6.QtWidgets import QApplication

    win.resize(1440, 900)
    win.show()
    win.show_main_page()
    win.set_tool(tool)
    QApplication.processEvents()
    bar = win.clean_selection_button
    while bar.objectName() != "toonOptionsBar":
        bar = bar.parentWidget()
    assert bar.layout().sizeHint().width() <= bar.width()



def test_the_paint_tools_show_their_options(win):
    win.set_tool("paint")
    assert not win.paint_options.isHidden() and not win.brush_options.isHidden()
    assert win.selection_options.isHidden() and win.box_options.isHidden()
    win.set_tool("eyedropper")
    assert not win.paint_options.isHidden() and win.brush_options.isHidden()
