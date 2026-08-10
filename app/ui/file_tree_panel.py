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

from .dayu_widgets.line_edit import MLineEdit
from .dayu_widgets.menu import MMenu
from .dayu_widgets.push_button import MPushButton

PATH_ROLE = QtCore.Qt.ItemDataRole.UserRole
KIND_ROLE = QtCore.Qt.ItemDataRole.UserRole + 1


class FileTreePanel(QtWidgets.QWidget):
    """Pages grouped by source folder."""

    page_activated = QtCore.Signal(str)
    delete_requested = QtCore.Signal(list)
    skip_requested = QtCore.Signal(list, bool)
    translate_requested = QtCore.Signal(list)
    add_folder_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._paths: list[str] = []
        self._states: dict[str, dict] = {}
        self._items_by_path: dict[str, QtWidgets.QTreeWidgetItem] = {}
        self._selecting = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

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

    # Building

    def set_pages(self, file_paths: list[str], image_states: dict[str, dict]) -> None:
        """Rebuild from the current page list."""
        self._paths = list(file_paths)
        self._states = image_states or {}

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

    def _group_by_folder(self, paths: list[str]) -> dict[str, list[str]]:
        """Folders in the order their first page appears, keeping page order."""
        groups: dict[str, list[str]] = {}
        for path in paths:
            groups.setdefault(os.path.dirname(path) or os.sep, []).append(path)
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
        item.setText(0, os.path.basename(path))
        item.setToolTip(0, path)

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
