"""A folder tree over the loaded pages.

The page list is a flat strip of every page in reading order, which is right
for a single chapter and unusable once a whole series is open: three hundred
rows of "001.jpg" with nothing saying which chapter each belongs to.

This groups the same pages by the folder they came from, the way an editor's
file explorer would, so a chapter can be collapsed, filtered, or acted on as a
unit. It is a view, not a second model — every rebuild reads `image_files` and
`image_states` afresh, and every action is emitted as file paths for the
existing page-list handlers to carry out.
"""

from __future__ import annotations

import os

from PySide6 import QtCore, QtGui, QtWidgets

from modules.utils.workspaces import WorkspaceManager

from .dayu_widgets.combo_box import MComboBox
from .dayu_widgets.line_edit import MLineEdit
from .dayu_widgets.menu import MMenu
from .dayu_widgets.push_button import MPushButton

PATH_ROLE = QtCore.Qt.ItemDataRole.UserRole
KIND_ROLE = QtCore.Qt.ItemDataRole.UserRole + 1

NO_WORKSPACE = ""


class FileTreePanel(QtWidgets.QWidget):
    """Pages grouped by source folder, under the workspace they belong to."""

    page_activated = QtCore.Signal(str)
    delete_requested = QtCore.Signal(list)
    skip_requested = QtCore.Signal(list, bool)
    translate_requested = QtCore.Signal(list)
    add_folder_requested = QtCore.Signal()
    workspace_activated = QtCore.Signal(str)
    workspace_folder_opened = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._paths: list[str] = []
        self._states: dict[str, dict] = {}
        self._originals: dict[str, str] = {}
        self._items_by_path: dict[str, QtWidgets.QTreeWidgetItem] = {}
        self._selecting = False
        self.workspaces = WorkspaceManager.instance()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        workspace_row = QtWidgets.QHBoxLayout()
        workspace_row.setSpacing(4)
        self.workspace_combo = MComboBox().small()
        self.workspace_combo.setToolTip(self.tr(
            "The series being worked on. Switching one loads its chapters and\n"
            "its glossary, prompt preset and languages together."
        ))
        self.workspace_combo.currentIndexChanged.connect(self._on_workspace_chosen)
        workspace_row.addWidget(self.workspace_combo, 1)

        self.workspace_menu_button = MPushButton("⋯").small()
        self.workspace_menu_button.setToolTip(self.tr("Manage workspaces"))
        self.workspace_menu_button.clicked.connect(self._show_workspace_menu)
        workspace_row.addWidget(self.workspace_menu_button)
        layout.addLayout(workspace_row)

        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(4)
        self.filter_input = MLineEdit()
        self.filter_input.setPlaceholderText(self.tr("Filter pages…"))
        self.filter_input.textChanged.connect(self._apply_filter)
        controls.addWidget(self.filter_input, 1)

        self.add_folder_button = MPushButton(self.tr("+ Folder")).small()
        self.add_folder_button.setToolTip(self.tr("Add every image in a folder as a new chapter"))
        self.add_folder_button.clicked.connect(self.add_folder_requested)
        controls.addWidget(self.add_folder_button)
        layout.addLayout(controls)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        layout.addWidget(self.tree, 1)

        self.summary_label = QtWidgets.QLabel("")
        self.summary_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.summary_label)

        self.refresh_workspaces()

    # Workspaces

    def refresh_workspaces(self) -> None:
        """Reload the list of workspaces and show the active one."""
        names = self.workspaces.list_names()
        self.workspace_combo.blockSignals(True)
        try:
            self.workspace_combo.clear()
            self.workspace_combo.addItem(self.tr("No workspace"), NO_WORKSPACE)
            for name in names:
                self.workspace_combo.addItem(name, name)

            active = self.workspaces.active_name if self.workspaces.active_name in names else NO_WORKSPACE
            index = self.workspace_combo.findData(active)
            self.workspace_combo.setCurrentIndex(max(0, index))
        finally:
            self.workspace_combo.blockSignals(False)

    def current_workspace_name(self) -> str:
        return self.workspace_combo.currentData() or NO_WORKSPACE

    def _on_workspace_chosen(self, _index: int) -> None:
        self.workspace_activated.emit(self.current_workspace_name())

    def _show_workspace_menu(self) -> None:
        menu = MMenu(parent=self)
        current = self.current_workspace_name()

        menu.addAction(self.tr("New workspace…")).triggered.connect(self._create_workspace)

        rename_action = menu.addAction(self.tr("Rename…"))
        rename_action.setEnabled(bool(current))
        rename_action.triggered.connect(lambda: self._rename_workspace(current))

        delete_action = menu.addAction(self.tr("Delete"))
        delete_action.setEnabled(bool(current))
        delete_action.triggered.connect(lambda: self._delete_workspace(current))

        workspace = self.workspaces.load(current) if current else None
        if workspace and workspace.folders:
            menu.addSeparator()
            for folder in workspace.folders:
                label = os.path.basename(folder.rstrip(os.sep)) or folder
                exists = os.path.isdir(folder)
                action = menu.addAction(
                    self.tr("Open {0}").format(label) if exists
                    else self.tr("{0} (missing)").format(label)
                )
                action.setEnabled(exists)
                action.triggered.connect(
                    lambda _checked=False, path=folder: self.workspace_folder_opened.emit(path)
                )

        menu.exec_(self.workspace_menu_button.mapToGlobal(
            QtCore.QPoint(0, self.workspace_menu_button.height())
        ))

    def _create_workspace(self) -> None:
        name, accepted = QtWidgets.QInputDialog.getText(
            self, self.tr("New Workspace"), self.tr("Series name:")
        )
        if not accepted or not name.strip():
            return
        if self.workspaces.create(name.strip()) is None:
            QtWidgets.QMessageBox.information(
                self, self.tr("New Workspace"),
                self.tr('A workspace called "{0}" already exists.').format(name.strip()),
            )
            return
        self.refresh_workspaces()
        self.workspace_activated.emit(self.current_workspace_name())

    def _rename_workspace(self, current: str) -> None:
        name, accepted = QtWidgets.QInputDialog.getText(
            self, self.tr("Rename Workspace"), self.tr("Series name:"), text=current
        )
        if not accepted or not name.strip():
            return
        if not self.workspaces.rename(current, name.strip()):
            QtWidgets.QMessageBox.information(
                self, self.tr("Rename Workspace"),
                self.tr('A workspace called "{0}" already exists.').format(name.strip()),
            )
            return
        self.refresh_workspaces()

    def _delete_workspace(self, current: str) -> None:
        answer = QtWidgets.QMessageBox.question(
            self, self.tr("Delete Workspace"),
            self.tr('Delete the workspace "{0}"? The images and translations stay '
                    'on disk — only the saved setup is removed.').format(current),
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.workspaces.delete(current)
        self.refresh_workspaces()
        self.workspace_activated.emit(self.current_workspace_name())

    # Building

    def set_pages(
        self,
        file_paths: list[str],
        image_states: dict[str, dict],
        path_originals: dict[str, str] | None = None,
    ) -> None:
        """Rebuild from the current page list.

        `path_originals` maps a page's working path to where it originally came
        from. Pages restored from a saved project live in per-id temp
        directories — grouping by those shows one numbered folder per page —
        so grouping and labels use the original path while every emitted
        action still carries the working path.
        """
        self._paths = list(file_paths)
        self._states = image_states or {}
        self._originals = dict(path_originals or {})

        expanded = {
            self.tree.topLevelItem(i).data(0, PATH_ROLE)
            for i in range(self.tree.topLevelItemCount())
            if self.tree.topLevelItem(i).isExpanded()
        }
        selected = self.selected_paths()

        self.tree.setUpdatesEnabled(False)
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            self._items_by_path.clear()

            for folder, paths in self._group_by_folder(self._paths).items():
                group = QtWidgets.QTreeWidgetItem(self.tree)
                group.setData(0, KIND_ROLE, "folder")
                group.setData(0, PATH_ROLE, folder)
                self._style_group(group, folder, paths)

                for path in paths:
                    item = QtWidgets.QTreeWidgetItem(group)
                    item.setData(0, KIND_ROLE, "page")
                    item.setData(0, PATH_ROLE, path)
                    self._style_page(item, path)
                    self._items_by_path[path] = item

                # A single chapter is the common case and gains nothing from
                # being collapsed; many chapters open collapsed but for the
                # ones that were already open.
                group.setExpanded(folder in expanded or self.tree.topLevelItemCount() == 1)
        finally:
            self.tree.blockSignals(False)
            self.tree.setUpdatesEnabled(True)

        self._apply_filter(self.filter_input.text())
        self._update_summary()
        if selected:
            self.select_paths(selected)

    def _display_path(self, path: str) -> str:
        return self._originals.get(path, path)

    def _group_by_folder(self, paths: list[str]) -> dict[str, list[str]]:
        """Folders in the order their first page appears, keeping page order."""
        groups: dict[str, list[str]] = {}
        for path in paths:
            folder = os.path.dirname(self._display_path(path)) or os.sep
            groups.setdefault(folder, []).append(path)
        return groups

    def _folder_label(self, folder: str) -> str:
        """The folder's name, with its parent when two chapters share a name.

        Series laid out as `Series/Ch01/images` would otherwise show a column
        of identical "images" rows.
        """
        name = os.path.basename(folder.rstrip(os.sep)) or folder
        siblings = {
            os.path.basename(other.rstrip(os.sep)) or other
            for other in self._group_by_folder(self._paths)
            if other != folder
        }
        if name not in siblings:
            return name
        parent = os.path.basename(os.path.dirname(folder.rstrip(os.sep)))
        return f"{parent}/{name}" if parent else name

    def _style_group(self, item: QtWidgets.QTreeWidgetItem, folder: str, paths: list[str]) -> None:
        done = sum(1 for path in paths if self._is_translated(path))
        label = self._folder_label(folder)
        item.setText(0, f"{label}    {done}/{len(paths)}")
        item.setToolTip(0, folder)

        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        item.setIcon(
            0, self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DirIcon)
        )

    def _style_page(self, item: QtWidgets.QTreeWidgetItem, path: str) -> None:
        display = self._display_path(path)
        item.setText(0, os.path.basename(display))
        item.setToolTip(0, display)

        state = self._states.get(path, {}) or {}
        font = item.font(0)
        font.setStrikeOut(bool(state.get("skip")))
        item.setFont(0, font)

        if self._is_translated(path):
            item.setIcon(
                0, self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogApplyButton)
            )
        else:
            item.setIcon(0, QtGui.QIcon())

    def _is_translated(self, path: str) -> bool:
        blocks = (self._states.get(path, {}) or {}).get("blk_list") or []
        return any(getattr(block, "translation", "") for block in blocks)

    def _update_summary(self) -> None:
        total = len(self._paths)
        done = sum(1 for path in self._paths if self._is_translated(path))
        chapters = self.tree.topLevelItemCount()
        if not total:
            self.summary_label.setText("")
            return
        self.summary_label.setText(
            self.tr("{0} chapters · {1}/{2} pages translated").format(chapters, done, total)
        )

    # Selection

    def selected_paths(self) -> list[str]:
        paths = []
        for item in self.tree.selectedItems():
            if item.data(0, KIND_ROLE) == "page":
                paths.append(item.data(0, PATH_ROLE))
            else:
                paths.extend(
                    item.child(i).data(0, PATH_ROLE) for i in range(item.childCount())
                )
        return paths

    def select_paths(self, paths: list[str]) -> None:
        self._selecting = True
        try:
            self.tree.clearSelection()
            for path in paths:
                item = self._items_by_path.get(path)
                if item is not None:
                    item.setSelected(True)
        finally:
            self._selecting = False

    def follow_current_page(self, path: str) -> None:
        """Highlight and reveal the page the editor is showing."""
        item = self._items_by_path.get(path)
        if item is None:
            return
        self._selecting = True
        try:
            self.tree.setCurrentItem(item)
            parent = item.parent()
            if parent is not None and not parent.isExpanded():
                parent.setExpanded(True)
            self.tree.scrollToItem(item)
        finally:
            self._selecting = False

    def _on_selection_changed(self) -> None:
        if self._selecting:
            return
        item = self.tree.currentItem()
        if item is not None and item.data(0, KIND_ROLE) == "page":
            self.page_activated.emit(item.data(0, PATH_ROLE))

    # Filtering

    def _apply_filter(self, text: str) -> None:
        needle = (text or "").strip().lower()
        for index in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(index)
            group_matches = needle in group.text(0).lower()
            visible_children = 0
            for child_index in range(group.childCount()):
                child = group.child(child_index)
                matches = (
                    not needle
                    or group_matches
                    or needle in child.text(0).lower()
                )
                child.setHidden(not matches)
                visible_children += int(matches)
            group.setHidden(bool(needle) and visible_children == 0)
            if needle and visible_children:
                group.setExpanded(True)

    # Actions

    def _show_context_menu(self, position: QtCore.QPoint) -> None:
        paths = self.selected_paths()
        clicked = self.tree.itemAt(position)
        if not paths and clicked is not None:
            if clicked.data(0, KIND_ROLE) == "folder":
                paths = [clicked.child(i).data(0, PATH_ROLE) for i in range(clicked.childCount())]
            else:
                paths = [clicked.data(0, PATH_ROLE)]
        if not paths:
            return

        menu = MMenu(parent=self)
        translate_action = menu.addAction(self.tr("Translate"))
        translate_action.triggered.connect(lambda: self.translate_requested.emit(paths))

        all_skipped = all(
            (self._states.get(path, {}) or {}).get("skip") for path in paths
        )
        skip_action = menu.addAction(self.tr("Unskip") if all_skipped else self.tr("Skip"))
        skip_action.triggered.connect(
            lambda: self.skip_requested.emit(paths, not all_skipped)
        )

        delete_action = menu.addAction(self.tr("Remove from project"))
        delete_action.triggered.connect(lambda: self.delete_requested.emit(paths))

        menu.addSeparator()
        reveal_action = menu.addAction(self.tr("Show in file manager"))
        reveal_action.triggered.connect(lambda: self._reveal(paths[0]))

        menu.exec_(self.tree.viewport().mapToGlobal(position))

    @staticmethod
    def _reveal(path: str) -> None:
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(folder))
