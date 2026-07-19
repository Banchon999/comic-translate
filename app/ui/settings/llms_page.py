from PySide6 import QtWidgets, QtCore
from ..dayu_widgets.label import MLabel
from ..dayu_widgets.text_edit import MTextEdit
from ..dayu_widgets.check_box import MCheckBox
from ..dayu_widgets.collapse import MCollapse
from ..dayu_widgets.combo_box import MComboBox
from ..dayu_widgets.push_button import MPushButton

from modules.utils.prompts import PromptManager

class LlmsPage(QtWidgets.QWidget):
    DEFAULT_EXTRA_CONTEXT_LIMIT = 1000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._extra_context_limit: int | None = self.DEFAULT_EXTRA_CONTEXT_LIMIT
        self.prompt_manager = PromptManager.instance()

        v = QtWidgets.QVBoxLayout(self)
        main_layout = QtWidgets.QHBoxLayout()

        self.image_checkbox = MCheckBox(self.tr("Provide Image as Input to AI"))
        self.image_checkbox.setChecked(False)

        # Left
        left_layout = QtWidgets.QVBoxLayout()
        prompt_label = MLabel(self.tr("Extra Context:"))
        self.extra_context = MTextEdit()
        self.extra_context.setMinimumHeight(200)
        left_layout.addWidget(prompt_label)
        left_layout.addWidget(self.extra_context)
        left_layout.addWidget(self.image_checkbox)
        left_layout.addStretch(1)

        # Right
        right_layout = QtWidgets.QVBoxLayout()

        # Advanced settings

        right_layout.addSpacing(10)
        right_layout.addStretch(1)

        main_layout.addLayout(left_layout, 3)
        main_layout.addLayout(right_layout, 1)

        v.addLayout(main_layout)

        # Translation prompt presets (manga / manhwa / webtoon focused)
        v.addSpacing(15)
        v.addWidget(MLabel(self.tr("Translation Prompt")).h4())
        v.addWidget(MLabel(self.tr(
            "The style instructions sent to the AI translator. Pick a preset for your medium "
            "or save your own. The JSON output rules are always appended automatically."
        )).secondary())

        preset_layout = QtWidgets.QHBoxLayout()
        self.prompt_preset_combo = MComboBox().small()
        self.prompt_preset_combo.setMinimumWidth(180)

        self.prompt_save_button = MPushButton(self.tr("Save As...")).small()
        self.prompt_save_button.clicked.connect(self._save_prompt_as)
        self.prompt_delete_button = MPushButton(self.tr("Delete")).small()
        self.prompt_delete_button.clicked.connect(self._delete_prompt)

        preset_layout.addWidget(MLabel(self.tr("Preset:")))
        preset_layout.addWidget(self.prompt_preset_combo)
        preset_layout.addWidget(self.prompt_save_button)
        preset_layout.addWidget(self.prompt_delete_button)
        preset_layout.addStretch(1)
        v.addLayout(preset_layout)

        self.prompt_editor = MTextEdit()
        self.prompt_editor.setMinimumHeight(160)
        v.addWidget(self.prompt_editor)

        self._refresh_prompt_presets()
        self.prompt_preset_combo.currentTextChanged.connect(self._on_prompt_preset_selected)

        v.addStretch(1)

        self.extra_context.textChanged.connect(self._limit_extra_context)

    # Prompt presets

    def _refresh_prompt_presets(self):
        self.prompt_preset_combo.blockSignals(True)
        self.prompt_preset_combo.clear()
        self.prompt_preset_combo.addItems(self.prompt_manager.list_presets())
        self.prompt_preset_combo.setCurrentText(self.prompt_manager.active_preset)
        self.prompt_preset_combo.blockSignals(False)
        self._load_active_prompt()

    def _load_active_prompt(self):
        name = self.prompt_manager.active_preset
        self.prompt_editor.setPlainText(self.prompt_manager.get_template(name))
        self.prompt_delete_button.setEnabled(not self.prompt_manager.is_builtin(name))

    def _on_prompt_preset_selected(self, name: str):
        if not name:
            return
        self.prompt_manager.set_active(name)
        self._load_active_prompt()

    def _save_prompt_as(self):
        current = self.prompt_manager.active_preset
        suggestion = current if not self.prompt_manager.is_builtin(current) else ""
        name, ok = QtWidgets.QInputDialog.getText(
            self, self.tr("Save Prompt Preset"), self.tr("Preset name:"), text=suggestion
        )
        if not ok or not name.strip():
            return
        self.prompt_manager.save_custom(name.strip(), self.prompt_editor.toPlainText())
        self._refresh_prompt_presets()

    def _delete_prompt(self):
        name = self.prompt_manager.active_preset
        if self.prompt_manager.is_builtin(name):
            return
        answer = QtWidgets.QMessageBox.question(
            self, self.tr("Prompt Presets"),
            self.tr('Delete the preset "{0}"?').format(name),
        )
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            self.prompt_manager.delete_custom(name)
            self._refresh_prompt_presets()

    def set_extra_context_unlimited(self, enabled: bool) -> None:
        self._extra_context_limit = None if enabled else self.DEFAULT_EXTRA_CONTEXT_LIMIT
        self._limit_extra_context()

    def _limit_extra_context(self):
        max_length = self._extra_context_limit
        if max_length is None:
            return
        text = self.extra_context.toPlainText()
        if len(text) > max_length:
            # Preserve cursor position
            cursor = self.extra_context.textCursor()
            position = cursor.position()
            
            # Truncate
            self.extra_context.setPlainText(text[:max_length])
            
            # Restore cursor (clamped to end)
            new_position = min(position, max_length)
            cursor.setPosition(new_position)
            self.extra_context.setTextCursor(cursor)

