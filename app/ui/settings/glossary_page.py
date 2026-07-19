from PySide6 import QtWidgets, QtCore
from PySide6.QtCore import QThreadPool

from app.thread_worker import GenericWorker
from ..dayu_widgets.label import MLabel
from ..dayu_widgets.line_edit import MLineEdit
from ..dayu_widgets.check_box import MCheckBox
from ..dayu_widgets.push_button import MPushButton
from ..dayu_widgets.combo_box import MComboBox
from ..dayu_widgets.text_edit import MTextEdit

from modules.utils.glossary import (
    GlossaryManager, GlossaryEntry, GLOSSARY_PRESET_TYPES, GLOSSARY_GENDERS
)


class GlossaryEntryDialog(QtWidgets.QDialog):
    """Dialog to add or edit a single glossary entry."""

    def __init__(self, types: list[str], entry: GlossaryEntry | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Edit Term") if entry else self.tr("Add Term"))
        self.setMinimumWidth(420)

        layout = QtWidgets.QFormLayout(self)

        self.source_input = MLineEdit()
        self.target_input = MLineEdit()

        self.type_combo = MComboBox()
        self.type_combo.setEditable(True)
        self.type_combo.addItems(types)

        self.gender_combo = MComboBox()
        self.gender_combo.addItems([self.tr("(none)"), "male", "female", "neutral"])

        self.note_input = MTextEdit()
        self.note_input.setMaximumHeight(70)

        layout.addRow(MLabel(self.tr("Original Term:")), self.source_input)
        layout.addRow(MLabel(self.tr("Translation:")), self.target_input)
        layout.addRow(MLabel(self.tr("Type:")), self.type_combo)
        self.gender_label = MLabel(self.tr("Gender:"))
        layout.addRow(self.gender_label, self.gender_combo)
        layout.addRow(MLabel(self.tr("Note:")), self.note_input)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self.type_combo.currentTextChanged.connect(self._sync_gender_visibility)

        if entry:
            self.source_input.setText(entry.source)
            self.target_input.setText(entry.target)
            self.type_combo.setCurrentText(entry.type)
            index = GLOSSARY_GENDERS.index(entry.gender) if entry.gender in GLOSSARY_GENDERS else 0
            self.gender_combo.setCurrentIndex(index)
        self._sync_gender_visibility(self.type_combo.currentText())
        if entry:
            self.note_input.setPlainText(entry.note)

    def _sync_gender_visibility(self, entry_type: str):
        is_character = entry_type.strip() == "character"
        self.gender_label.setVisible(is_character)
        self.gender_combo.setVisible(is_character)

    def _on_accept(self):
        if not self.source_input.text().strip() or not self.target_input.text().strip():
            QtWidgets.QMessageBox.warning(
                self, self.tr("Glossary"),
                self.tr("Both the original term and its translation are required.")
            )
            return
        self.accept()

    def get_entry(self) -> GlossaryEntry:
        entry_type = self.type_combo.currentText().strip() or "term"
        gender = ""
        if entry_type == "character" and self.gender_combo.currentIndex() > 0:
            gender = self.gender_combo.currentText()
        return GlossaryEntry(
            source=self.source_input.text().strip(),
            target=self.target_input.text().strip(),
            type=entry_type,
            gender=gender,
            note=self.note_input.toPlainText().strip(),
        )


class GlossaryPage(QtWidgets.QWidget):
    """Settings tab for managing the translation glossary."""

    COLUMNS = ["source", "target", "type", "gender", "note"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = GlossaryManager()

        layout = QtWidgets.QVBoxLayout(self)

        # Profile (one glossary per series/story)
        profile_layout = QtWidgets.QHBoxLayout()
        profile_layout.addWidget(MLabel(self.tr("Series:")).strong())

        self.profile_combo = MComboBox().small()
        self.profile_combo.setMinimumWidth(200)
        profile_layout.addWidget(self.profile_combo)

        new_profile_button = MPushButton(self.tr("New")).small()
        new_profile_button.clicked.connect(self.new_profile)
        rename_profile_button = MPushButton(self.tr("Rename")).small()
        rename_profile_button.clicked.connect(self.rename_profile)
        delete_profile_button = MPushButton(self.tr("Delete")).small()
        delete_profile_button.clicked.connect(self.delete_profile)
        for b in (new_profile_button, rename_profile_button, delete_profile_button):
            profile_layout.addWidget(b)
        profile_layout.addStretch(1)
        layout.addLayout(profile_layout)

        # Options
        self.enabled_checkbox = MCheckBox(self.tr("Use Glossary during AI Translation"))
        self.enabled_checkbox.setChecked(self.manager.enabled)
        self.enabled_checkbox.stateChanged.connect(self._on_options_changed)

        self.match_only_checkbox = MCheckBox(
            self.tr("Only send terms that appear in the detected text")
        )
        self.match_only_checkbox.setChecked(self.manager.match_only)
        self.match_only_checkbox.stateChanged.connect(self._on_options_changed)

        self.log_ocr_checkbox = MCheckBox(
            self.tr("Save OCR'd text to this series' log (for glossary extraction)")
        )
        self.log_ocr_checkbox.setChecked(self.manager.log_ocr)
        self.log_ocr_checkbox.stateChanged.connect(self._on_options_changed)

        layout.addWidget(self.enabled_checkbox)
        layout.addWidget(self.match_only_checkbox)
        layout.addWidget(self.log_ocr_checkbox)

        # OCR log → glossary extraction
        extract_layout = QtWidgets.QHBoxLayout()
        self.extract_button = MPushButton(self.tr("Extract Glossary from OCR Log")).small()
        self.extract_button.clicked.connect(self.extract_from_log)
        clear_log_button = MPushButton(self.tr("Clear Log")).small()
        clear_log_button.clicked.connect(self.clear_ocr_log)
        self.log_status_label = MLabel("").secondary()
        extract_layout.addWidget(self.extract_button)
        extract_layout.addWidget(clear_log_button)
        extract_layout.addWidget(self.log_status_label)
        extract_layout.addStretch(1)
        layout.addLayout(extract_layout)

        # Search / filter row
        filter_layout = QtWidgets.QHBoxLayout()
        self.search_input = MLineEdit().small()
        self.search_input.setPlaceholderText(self.tr("Search terms..."))
        self.search_input.textChanged.connect(self.refresh_table)

        self.type_filter_combo = MComboBox().small()
        self.type_filter_combo.setFixedWidth(140)
        self.type_filter_combo.currentIndexChanged.connect(self.refresh_table)

        self.count_label = MLabel("").secondary()

        filter_layout.addWidget(self.search_input, 1)
        filter_layout.addWidget(self.type_filter_combo)
        filter_layout.addWidget(self.count_label)
        layout.addLayout(filter_layout)

        # Table
        self.table = QtWidgets.QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([
            self.tr("Original"), self.tr("Translation"), self.tr("Type"),
            self.tr("Gender"), self.tr("Note"),
        ])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 160)
        self.table.setColumnWidth(1, 160)
        self.table.setColumnWidth(2, 90)
        self.table.setColumnWidth(3, 70)
        self.table.setMinimumHeight(280)
        self.table.doubleClicked.connect(lambda _: self.edit_selected())
        layout.addWidget(self.table, 1)

        # Action buttons
        buttons_layout = QtWidgets.QHBoxLayout()
        add_button = MPushButton(self.tr("Add")).small()
        add_button.clicked.connect(self.add_entry)
        edit_button = MPushButton(self.tr("Edit")).small()
        edit_button.clicked.connect(self.edit_selected)
        delete_button = MPushButton(self.tr("Delete")).small()
        delete_button.clicked.connect(self.delete_selected)
        import_button = MPushButton(self.tr("Import...")).small()
        import_button.clicked.connect(self.import_file)
        export_json_button = MPushButton(self.tr("Export JSON")).small()
        export_json_button.clicked.connect(lambda: self.export_file("json"))
        export_csv_button = MPushButton(self.tr("Export CSV")).small()
        export_csv_button.clicked.connect(lambda: self.export_file("csv"))

        for b in (add_button, edit_button, delete_button, import_button,
                  export_json_button, export_csv_button):
            buttons_layout.addWidget(b)
        buttons_layout.addStretch(1)
        layout.addLayout(buttons_layout)

        self._refresh_profiles()
        self._refresh_type_filter()
        self.refresh_table()
        self._refresh_log_status()
        self.profile_combo.currentTextChanged.connect(self._on_profile_selected)

    # Profiles

    def _refresh_profiles(self):
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItems(self.manager.list_profiles())
        self.profile_combo.setCurrentText(self.manager.active_profile)
        self.profile_combo.blockSignals(False)

    def _on_profile_selected(self, name: str):
        if not name or name == self.manager.active_profile:
            return
        self.manager.switch_profile(name)
        self._refresh_type_filter()
        self.refresh_table()
        self._refresh_log_status()

    def _ask_profile_name(self, title: str, initial: str = "") -> str:
        name, ok = QtWidgets.QInputDialog.getText(
            self, title, self.tr("Series name:"), text=initial
        )
        return name.strip() if ok else ""

    def new_profile(self):
        name = self._ask_profile_name(self.tr("New Glossary"))
        if not name:
            return
        self.manager.create_profile(name)
        self._refresh_profiles()
        self._refresh_type_filter()
        self.refresh_table()
        self._refresh_log_status()

    def rename_profile(self):
        name = self._ask_profile_name(self.tr("Rename Glossary"), self.manager.active_profile)
        if not name:
            return
        self.manager.rename_profile(name)
        self._refresh_profiles()

    def delete_profile(self):
        answer = QtWidgets.QMessageBox.question(
            self, self.tr("Glossary"),
            self.tr('Delete the glossary "{0}" and all its terms?').format(
                self.manager.active_profile
            ),
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.manager.delete_profile()
        self._refresh_profiles()
        self._refresh_type_filter()
        self.refresh_table()
        self._refresh_log_status()

    # Options

    def _on_options_changed(self):
        self.manager.enabled = self.enabled_checkbox.isChecked()
        self.manager.match_only = self.match_only_checkbox.isChecked()
        self.manager.log_ocr = self.log_ocr_checkbox.isChecked()
        self.manager.save()

    # OCR log → glossary extraction

    def _refresh_log_status(self):
        count = self.manager.ocr_log_line_count()
        self.log_status_label.setText(self.tr("{0} logged lines").format(count))
        self.extract_button.setEnabled(count > 0)

    def clear_ocr_log(self):
        answer = QtWidgets.QMessageBox.question(
            self, self.tr("Glossary"),
            self.tr('Clear the OCR log for "{0}"?').format(self.manager.active_profile),
        )
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            self.manager.clear_ocr_log()
            self._refresh_log_status()

    def extract_from_log(self):
        log_text = self.manager.read_ocr_log()
        if not log_text.strip():
            QtWidgets.QMessageBox.information(
                self, self.tr("Glossary"),
                self.tr("The OCR log is empty. Run OCR on some pages first."),
            )
            return

        from modules.utils.glossary_extractor import extract_glossary_terms
        main_page = self.window()
        existing = {entry.source for entry in self.manager.entries}

        self.extract_button.setEnabled(False)
        self.extract_button.setText(self.tr("Extracting..."))

        worker = GenericWorker(extract_glossary_terms, main_page, log_text, existing)
        worker.signals.result.connect(self._on_extraction_done)
        worker.signals.error.connect(self._on_extraction_error)
        QThreadPool.globalInstance().start(worker)

    def _reset_extract_button(self):
        self.extract_button.setText(self.tr("Extract Glossary from OCR Log"))
        self._refresh_log_status()

    def _on_extraction_done(self, entries):
        self._reset_extract_button()
        if not entries:
            QtWidgets.QMessageBox.information(
                self, self.tr("Glossary"),
                self.tr("No new terms were found in the OCR log."),
            )
            return
        for entry in entries:
            self.manager.upsert(entry, save=False)
        self.manager.save()
        self._refresh_type_filter()
        self.refresh_table()
        QtWidgets.QMessageBox.information(
            self, self.tr("Glossary"),
            self.tr("Added {0} new term(s) to \"{1}\".").format(
                len(entries), self.manager.active_profile
            ),
        )

    def _on_extraction_error(self, error_info):
        self._reset_extract_button()
        _, value, _ = error_info
        QtWidgets.QMessageBox.warning(
            self, self.tr("Glossary"),
            self.tr("Glossary extraction failed:\n{0}").format(str(value)),
        )

    # Table

    def _refresh_type_filter(self):
        current = self.type_filter_combo.currentText()
        self.type_filter_combo.blockSignals(True)
        self.type_filter_combo.clear()
        self.type_filter_combo.addItem(self.tr("All types"))
        self.type_filter_combo.addItems(self.manager.types_in_use())
        index = self.type_filter_combo.findText(current)
        if index != -1:
            self.type_filter_combo.setCurrentIndex(index)
        self.type_filter_combo.blockSignals(False)

    def _visible_entries(self):
        query = self.search_input.text().strip().lower()
        type_filter = (
            self.type_filter_combo.currentText()
            if self.type_filter_combo.currentIndex() > 0 else ""
        )
        entries = self.manager.entries
        if query:
            entries = [
                e for e in entries
                if query in e.source.lower() or query in e.target.lower()
                or query in e.note.lower()
            ]
        if type_filter:
            entries = [e for e in entries if e.type == type_filter]
        return entries

    def refresh_table(self):
        entries = self._visible_entries()
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = [entry.source, entry.target, entry.type, entry.gender, entry.note]
            for col, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                if col == 0:
                    # Keep the source key on the row for edit/delete lookups
                    item.setData(QtCore.Qt.ItemDataRole.UserRole, entry.source)
                self.table.setItem(row, col, item)
        self.count_label.setText(
            self.tr("{0} of {1} terms").format(len(entries), len(self.manager.entries))
        )

    def _selected_sources(self) -> list[str]:
        sources = []
        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), 0)
            if item:
                sources.append(item.data(QtCore.Qt.ItemDataRole.UserRole))
        return sources

    # Actions

    def add_entry(self):
        dialog = GlossaryEntryDialog(self.manager.types_in_use(), parent=self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.manager.upsert(dialog.get_entry())
            self._refresh_type_filter()
            self.refresh_table()

    def edit_selected(self):
        sources = self._selected_sources()
        if not sources:
            return
        entry = self.manager.find(sources[0])
        if not entry:
            return
        dialog = GlossaryEntryDialog(self.manager.types_in_use(), entry=entry, parent=self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.manager.upsert(dialog.get_entry(), original_source=entry.source)
            self._refresh_type_filter()
            self.refresh_table()

    def delete_selected(self):
        sources = self._selected_sources()
        if not sources:
            return
        answer = QtWidgets.QMessageBox.question(
            self, self.tr("Glossary"),
            self.tr("Delete {0} selected term(s)?").format(len(sources)),
        )
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            self.manager.remove(sources)
            self._refresh_type_filter()
            self.refresh_table()

    def import_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, self.tr("Import Glossary"), "",
            self.tr("Glossary Files (*.json *.csv);;JSON Files (*.json);;CSV Files (*.csv)"),
        )
        if not path:
            return
        try:
            if path.lower().endswith(".csv"):
                count = self.manager.import_csv(path)
            else:
                count = self.manager.import_json(path)
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, self.tr("Glossary"),
                self.tr("Failed to import glossary: {0}").format(str(e)),
            )
            return
        self._refresh_type_filter()
        self.refresh_table()
        QtWidgets.QMessageBox.information(
            self, self.tr("Glossary"),
            self.tr("Imported {0} term(s).").format(count),
        )

    def export_file(self, file_format: str):
        if not self.manager.entries:
            QtWidgets.QMessageBox.information(
                self, self.tr("Glossary"), self.tr("The glossary is empty.")
            )
            return
        if file_format == "csv":
            caption, default_name, file_filter = (
                self.tr("Export Glossary as CSV"), "glossary.csv", self.tr("CSV Files (*.csv)")
            )
        else:
            caption, default_name, file_filter = (
                self.tr("Export Glossary as JSON"), "glossary.json", self.tr("JSON Files (*.json)")
            )
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, caption, default_name, file_filter)
        if not path:
            return
        try:
            if file_format == "csv":
                self.manager.export_csv(path)
            else:
                self.manager.export_json(path)
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, self.tr("Glossary"),
                self.tr("Failed to export glossary: {0}").format(str(e)),
            )
