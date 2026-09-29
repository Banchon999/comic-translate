"""Toon Studio editor chrome: the step bar's state, the Glossary tab and the status bar.

The widgets these decorate (the step buttons in ``hbutton_group``, the tool
buttons, the text controls) are built in ``builders/workspace.py`` under
their old attribute names, so every controller keeps working; this module
only adds what the new layout shows on top of them.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from app.ui.canvas.scene_registry import iter_items
from app.ui.dayu_widgets import dayu_theme
from app.ui.dayu_widgets.qt import MIcon
from core.editor_state import step_flags
from core.layers import LayerGroup

# The step bar's buttons, in hbutton_group order.
STEP_ICONS = ("detect.svg", "ocr.svg", "translate.svg", "segment.svg", "clean.svg", "render.svg")
DONE_ICON = "step-done.svg"


def style_step_buttons(group) -> None:
    """Give each step button its icon and the object name the stylesheet targets."""
    for icon, button in zip(STEP_ICONS, group.get_button_group().buttons()):
        button.setObjectName("toonStep")
        button.setIcon(MIcon(icon))
        button.setIconSize(QtCore.QSize(16, 16))
        button.setProperty("toon_step", "todo")
        button.setProperty("_toon_icon", icon)


def page_step_flags(main) -> tuple[bool, ...]:
    """What the current page shows of each step (see core/editor_state.py)."""
    viewer = getattr(main, "image_viewer", None)
    scene = getattr(viewer, "_scene", None)
    has_strokes = has_patches = False
    if scene is not None:
        has_strokes = any(True for _ in iter_items(scene, LayerGroup.STROKES, viewer))
        has_patches = any(True for _ in iter_items(scene, LayerGroup.PATCHES, viewer))
    has_text = bool(getattr(viewer, "text_items", None))
    return step_flags(getattr(main, "blk_list", None), has_strokes, has_patches, has_text)


def apply_step_states(main) -> tuple[bool, ...]:
    """Mark the done steps (check icon in the success colour) and return the flags."""
    flags = page_step_flags(main)
    ok = dayu_theme.tokens["ok"]
    for done, button in zip(flags, main.hbutton_group.get_button_group().buttons()):
        state = "done" if done else "todo"
        if button.property("toon_step") == state:
            continue
        button.setProperty("toon_step", state)
        icon = button.property("_toon_icon") or ""
        button.setIcon(MIcon(DONE_ICON, ok) if done else MIcon(icon))
        button.style().unpolish(button)
        button.style().polish(button)
    return flags


class GlossaryPeekPanel(QtWidgets.QWidget):
    """The inspector's Glossary tab: which terms this page sends, and why.

    The same rows as the Glossary page's Test Matching dialog, for the page on
    screen, plus the last translation's warning — so "why did it ignore my
    glossary" is answered without leaving the editor.
    """

    open_glossary = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        self.warning = QtWidgets.QLabel()
        self.warning.setObjectName("toonGlossaryWarning")
        self.warning.setWordWrap(True)
        self.warning.setVisible(False)
        layout.addWidget(self.warning)
        self.heading = QtWidgets.QLabel(self.tr("Terms on this page"))
        self.heading.setObjectName("toonSectionLabel")
        layout.addWidget(self.heading)
        self.rows = QtWidgets.QTextBrowser()
        self.rows.setObjectName("toonGlossaryRows")
        self.rows.setOpenLinks(False)
        layout.addWidget(self.rows, 1)
        self.open_button = QtWidgets.QPushButton(self.tr("Open Glossary"))
        self.open_button.clicked.connect(self.open_glossary)
        layout.addWidget(self.open_button)

    def refresh(self, main) -> None:
        from modules.utils.glossary import collect_source_text

        manager = getattr(getattr(getattr(main, "settings_page", None), "ui", None), "glossary_page", None)
        manager = getattr(manager, "manager", None)
        t = dayu_theme.tokens
        issues = getattr(main, "last_glossary_issues", None) or []
        if issues:
            lines = [self.tr("{0} term(s) not translated as set:").format(len(issues))]
            lines += [
                self.tr("«{0}» should be «{1}»").format(i.seen_as or i.source_term, i.expected)
                for i in issues[:5]
            ]
            self.warning.setText("\n".join(lines))
            self.warning.setStyleSheet(
                f"color: {t['warn']}; background: transparent; padding: 8px;"
                f"border: 1px solid {t['warn']}; border-radius: 8px;"
            )
            self.warning.setVisible(True)
        else:
            self.warning.setVisible(False)
        if manager is None:
            self.rows.setHtml("")
            return
        text = collect_source_text(getattr(main, "blk_list", None) or [])
        report = manager.match_report(text) if text.strip() and manager.enabled else []
        rows = []
        for m in report:
            src, tgt = _html(m.entry.source), _html(m.entry.target)
            if m.suppressed_by is not None:
                rows.append(
                    f"<span style='color:{t['text_3']}'>– {src} → {tgt} · "
                    + self.tr("inside «{0}»").format(_html(m.suppressed_by.source)) + "</span>"
                )
            elif m.fuzzy:
                rows.append(
                    f"<span style='color:{t['info']}'>≈</span> {src} → {tgt} · "
                    + self.tr("seen as «{0}»").format(_html(m.seen_as))
                )
            else:
                rows.append(f"<span style='color:{t['ok']}'>✓</span> {src} → {tgt}")
        if not manager.enabled:
            rows = [f"<span style='color:{t['text_3']}'>" + self.tr("The glossary is turned off.") + "</span>"]
        elif not rows:
            rows = [f"<span style='color:{t['text_3']}'>" + self.tr("No glossary terms on this page.") + "</span>"]
        self.rows.setHtml("<br>".join(rows))


def _html(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class EditorStatusBar(QtWidgets.QWidget):
    """Page position, engines and glossary profile, along the bottom edge."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("toonStatusBar")
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(18)
        self.page_label = QtWidgets.QLabel()
        self.ocr_label = QtWidgets.QLabel()
        self.translator_label = QtWidgets.QLabel()
        self.glossary_label = QtWidgets.QLabel()
        for label in (self.page_label, self.ocr_label, self.translator_label, self.glossary_label):
            label.setObjectName("toonStatusText")
            layout.addWidget(label)
        layout.addStretch(1)
        self.setFixedHeight(26)
        self._main = None
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)

    def bind(self, main) -> None:
        self._main = main
        self.refresh()
        self._timer.start()

    def refresh(self) -> None:
        main = self._main
        if main is None or not self.isVisible():
            return
        files = getattr(main, "image_files", None) or []
        index = getattr(main, "curr_img_idx", -1)
        if files and 0 <= index < len(files):
            self.page_label.setText(self.tr("Page {0} / {1}").format(index + 1, len(files)))
        else:
            self.page_label.setText(self.tr("No pages"))
        settings = getattr(main, "settings_page", None)
        if settings is None:
            return
        try:
            self.ocr_label.setText(self.tr("OCR · {0}").format(settings.get_tool_selection("ocr")))
            self.translator_label.setText(
                self.tr("Translator · {0}").format(settings.get_tool_selection("translator"))
            )
            manager = settings.ui.glossary_page.manager
            self.glossary_label.setText(
                self.tr("Glossary · {0} ({1})").format(manager.active_profile, len(manager.entries))
                if manager.enabled else self.tr("Glossary · off")
            )
        except Exception:  # a status line must never take the editor down
            pass
