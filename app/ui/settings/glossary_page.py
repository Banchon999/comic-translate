import logging

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

logger = logging.getLogger(__name__)

#: Characters of page text to gather before asking the model for terms.
#:
#: One page of a manhwa is a dozen short lines. Asked to pull proper nouns out
#: of that alone, a model has no way to tell a recurring character from someone
#: shouting once, and it cannot see that two spellings on facing pages are the
#: same person. Roughly ten pages is enough context to make those calls, and it
#: cuts the number of API requests by the same factor.
PAGE_BATCH_CHARS = 4000

#: How long to wait after the last page before extracting whatever has piled
#: up. Without this the tail end of a batch — anything under PAGE_BATCH_CHARS —
#: would sit in the buffer forever.
PAGE_FLUSH_IDLE_MS = 15000


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

    # Emitted from the OCR worker thread once a page's text is recognised.
    # Qt queues it onto the GUI thread, which is where the extraction queue lives.
    page_text_recognized = QtCore.Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = GlossaryManager()

        self._pending_pages: list[str] = []
        self._page_extraction_running = False
        # Pages are gathered up rather than extracted one at a time — see
        # _queue_page. The timer is what closes a batch that stopped arriving.
        self._page_flush_timer = QtCore.QTimer(self)
        self._page_flush_timer.setSingleShot(True)
        self._page_flush_timer.setInterval(PAGE_FLUSH_IDLE_MS)
        self._page_flush_timer.timeout.connect(self._flush_pending_pages)
        self.page_text_recognized.connect(self._on_page_text_recognized)

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

        self.batch_extract_checkbox = MCheckBox(
            self.tr("Batch mode: OCR all pages and extract glossary BEFORE translating")
        )
        self.batch_extract_checkbox.setChecked(self.manager.batch_extract)
        self.batch_extract_checkbox.stateChanged.connect(self._on_options_changed)

        self.auto_extract_checkbox = MCheckBox(
            self.tr("Extract terms from each page as soon as it is recognised")
        )
        self.auto_extract_checkbox.setChecked(self.manager.auto_extract)
        self.auto_extract_checkbox.setToolTip(self.tr(
            "Names and special terms are added to this series' glossary while you work,\n"
            "so by the time the last page is recognised the glossary is already complete.\n"
            "One page is extracted at a time in the background; it never blocks the pipeline."
        ))
        self.auto_extract_checkbox.stateChanged.connect(self._on_options_changed)

        layout.addWidget(self.enabled_checkbox)
        layout.addWidget(self.match_only_checkbox)
        layout.addWidget(self.log_ocr_checkbox)
        layout.addWidget(self.batch_extract_checkbox)
        layout.addWidget(self.auto_extract_checkbox)

        # OCR log → glossary extraction
        extract_layout = QtWidgets.QHBoxLayout()
        self.extract_button = MPushButton(self.tr("Extract Glossary from OCR Log")).small()
        self.extract_button.clicked.connect(self.extract_from_log)
        self.extract_page_button = MPushButton(self.tr("Extract from This Page")).small()
        self.extract_page_button.setToolTip(self.tr(
            "Extract terms from the text recognised on the page you are editing."
        ))
        self.extract_page_button.clicked.connect(self.extract_from_current_page)
        clear_log_button = MPushButton(self.tr("Clear Log")).small()
        clear_log_button.clicked.connect(self.clear_ocr_log)
        self.log_status_label = MLabel("").secondary()
        extract_layout.addWidget(self.extract_button)
        extract_layout.addWidget(self.extract_page_button)
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
        dedupe_button = MPushButton(self.tr("Merge Duplicates")).small()
        dedupe_button.setToolTip(self.tr(
            "Collapse entries that are the same term written differently —\n"
            "most often a Korean name stored twice in different Unicode forms."
        ))
        dedupe_button.clicked.connect(self.merge_duplicates)
        import_button = MPushButton(self.tr("Import...")).small()
        import_button.clicked.connect(self.import_file)
        export_json_button = MPushButton(self.tr("Export JSON")).small()
        export_json_button.clicked.connect(lambda: self.export_file("json"))
        export_csv_button = MPushButton(self.tr("Export CSV")).small()
        export_csv_button.clicked.connect(lambda: self.export_file("csv"))

        for b in (add_button, edit_button, delete_button, dedupe_button,
                  import_button, export_json_button, export_csv_button):
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
        self.manager.batch_extract = self.batch_extract_checkbox.isChecked()
        self.manager.auto_extract = self.auto_extract_checkbox.isChecked()
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

    def _main_window(self):
        """The application main window, however this page is hosted.

        This page lives in the Settings stack but is re-parented into a plain
        QDialog when opened from the editor's nav rail, so `self.window()` is
        sometimes that dialog. The extractor needs the real main window (its
        language combos and translator config), so walk up out of any wrapper,
        and fall back to scanning the top-level widgets.
        """
        widget = self.window()
        while widget is not None:
            if hasattr(widget, 's_combo'):
                return widget
            widget = widget.parentWidget()
        for top_level in QtWidgets.QApplication.topLevelWidgets():
            if hasattr(top_level, 's_combo'):
                return top_level
        return None

    def extract_from_log(self):
        log_text = self.manager.read_ocr_log()
        if not log_text.strip():
            QtWidgets.QMessageBox.information(
                self, self.tr("Glossary"),
                self.tr("The OCR log is empty. Run OCR on some pages first."),
            )
            return

        from modules.utils.glossary_extractor import extract_glossary_terms
        main_page = self._main_window()
        if main_page is None:
            QtWidgets.QMessageBox.warning(
                self, self.tr("Glossary"),
                self.tr("The main window is not available yet."),
            )
            return
        existing = {entry.source for entry in self.manager.entries}

        self.extract_button.setEnabled(False)
        self.extract_button.setText(self.tr("Extracting..."))

        worker = GenericWorker(extract_glossary_terms, main_page, log_text, existing)
        worker.signals.result.connect(self._on_extraction_done)
        worker.signals.error.connect(self._on_extraction_error)
        QThreadPool.globalInstance().start(worker)

    # Per-page extraction

    def extract_from_current_page(self):
        """Extract terms from the page currently open in the editor."""
        main_page = self._main_window()
        lines = []
        if main_page is not None:
            lines = [
                blk.text for blk in (getattr(main_page, 'blk_list', None) or [])
                if getattr(blk, 'text', '')
            ]
        if not lines:
            QtWidgets.QMessageBox.information(
                self, self.tr("Glossary"),
                self.tr("This page has no recognised text yet. Run Recognize first."),
            )
            return
        # Asked for explicitly, so do it now rather than waiting for company.
        self._queue_page("\n".join(lines), flush_now=True)

    def _on_page_text_recognized(self, lines: list):
        """A page finished OCR. Queue it when auto-extraction is on."""
        if not self.manager.auto_extract:
            return
        text = "\n".join(line for line in lines if line)
        if text.strip():
            self._queue_page(text)

    def _queue_page(self, text: str, flush_now: bool = False):
        """Add a page to the buffer, and extract once there is enough of it."""
        self._pending_pages.append(text)
        buffered = sum(len(page) for page in self._pending_pages)
        if flush_now or buffered >= PAGE_BATCH_CHARS:
            self._page_flush_timer.stop()
            self._flush_pending_pages()
        else:
            # Restart the clock: a batch still running will overtake it.
            self._page_flush_timer.start()
            self._update_queue_status()

    def _flush_pending_pages(self):
        """Send everything buffered so far as one extraction."""
        # One call at a time: each is an LLM request, and firing one per page of
        # a batch in parallel would rate-limit the account instantly. Whatever
        # arrives while this runs is picked up by the next flush.
        if self._page_extraction_running or not self._pending_pages:
            return

        main_page = self._main_window()
        if main_page is None:
            self._pending_pages.clear()
            return

        text = "\n".join(self._pending_pages)
        self._pending_pages.clear()
        existing = {entry.source for entry in self.manager.entries}
        self._page_extraction_running = True
        self._update_queue_status()

        from modules.utils.glossary_extractor import extract_glossary_terms
        worker = GenericWorker(extract_glossary_terms, main_page, text, existing)
        worker.signals.result.connect(self._on_page_extraction_done)
        worker.signals.error.connect(self._on_page_extraction_error)
        QThreadPool.globalInstance().start(worker)

    def _on_page_extraction_done(self, entries):
        self._page_extraction_running = False
        for entry in entries or []:
            self.manager.upsert(entry, save=False)
        if entries:
            self.manager.save()
            self._refresh_type_filter()
            self.refresh_table()
        self._update_queue_status()
        self._resume_pending_pages()

    def _on_page_extraction_error(self, error_info):
        # A failed batch must not stop the rest, and must not interrupt the user
        # with a dialog mid-run: the log status line carries the news.
        self._page_extraction_running = False
        _, value, _ = error_info
        logger.warning("Per-page glossary extraction failed: %s", value)
        self._update_queue_status()
        self._resume_pending_pages()

    def _resume_pending_pages(self):
        """Deal with pages that arrived while the last extraction was running."""
        if not self._pending_pages:
            return
        if sum(len(page) for page in self._pending_pages) >= PAGE_BATCH_CHARS:
            self._page_flush_timer.stop()
            self._flush_pending_pages()
        else:
            self._page_flush_timer.start()

    def _update_queue_status(self):
        if self._page_extraction_running:
            self.log_status_label.setText(self.tr("Extracting terms…"))
        elif self._pending_pages:
            # Waiting for more pages is not the same as working, and saying
            # "extracting" while nothing happens for 15 seconds reads as a hang.
            self.log_status_label.setText(
                self.tr("Gathering context… {0} page(s) collected").format(len(self._pending_pages))
            )
        else:
            self._refresh_log_status()

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

    def merge_duplicates(self):
        """Clean up a glossary built before terms were normalised."""
        removed = self.manager.deduplicate()
        self._refresh_type_filter()
        self.refresh_table()
        QtWidgets.QMessageBox.information(
            self, self.tr("Glossary"),
            self.tr("Merged {0} duplicate term(s).").format(removed) if removed
            else self.tr("No duplicate terms found."),
        )

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
