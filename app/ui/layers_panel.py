"""The Layers panel: the document's groups, and every object on screen as a layer.

Rows are the five fixed groups, topmost first (Editable Text ... Raw Image),
each holding one row per object currently on the canvas (the page on screen, or
the loaded pages in webtoon mode, each object labelled with its page). Listing
every object of a 200-page strip would mean thousands of rows nobody scrolls.

Each row has an eye (visible), a lock and a name. Clicking the eye or lock of a
group row changes that group across the whole document; on an object row it
changes that object only, as an undo step. Double-click an object's name to
rename it. The slider sets opacity for the selected rows, and the arrows move
the selected object up or down within its group.

The panel owns no state. It is rebuilt from the scene whenever layers, the undo
stack or the page change (coalesced into one rebuild per event-loop turn, and
skipped while the panel is hidden), and every edit goes through LayerController.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from app.ui.dayu_widgets.qt import MIcon
from core.layers import DEFAULT_GROUP_NAMES, PAINT_ORDER, LayerGroup

COL_VIS, COL_LOCK, COL_NAME = 0, 1, 2
ROLE_GROUP = Qt.ItemDataRole.UserRole
ROLE_OBJECT = Qt.ItemDataRole.UserRole + 1


class LayersPanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._ctrl = None
        self._rebuilding = False
        self._icons = {
            "eye": MIcon("eye.svg"),
            "eye-off": MIcon("eye-off.svg"),
            "lock": MIcon("lock.svg"),
            "lock-open": MIcon("lock-open.svg"),
        }

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        # Expand arrows and indentation on the name column, so the eye and
        # lock columns stay aligned at the left edge as in an image editor.
        self.tree.setTreePosition(COL_NAME)
        self.tree.setIndentation(14)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked)
        header = self.tree.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(COL_VIS, QtWidgets.QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_LOCK, QtWidgets.QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(COL_VIS, 26)
        self.tree.setColumnWidth(COL_LOCK, 26)

        self.opacity_label = QtWidgets.QLabel(self.tr("Opacity"))
        self.opacity_slider = QtWidgets.QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(100)
        self.opacity_slider.setTracking(False)  # one undo step per drag, on release
        self.opacity_value = QtWidgets.QLabel("100%")
        self.opacity_value.setMinimumWidth(36)

        self.up_button = QtWidgets.QToolButton()
        self.up_button.setIcon(MIcon("up_line.svg"))
        self.up_button.setToolTip(self.tr("Move the selected layer up within its group"))
        self.down_button = QtWidgets.QToolButton()
        self.down_button.setIcon(MIcon("down_line.svg"))
        self.down_button.setToolTip(self.tr("Move the selected layer down within its group"))

        title = QtWidgets.QLabel(self.tr("Layers"))
        title.setStyleSheet("font-weight: bold;")

        bottom = QtWidgets.QHBoxLayout()
        bottom.addWidget(self.opacity_label)
        bottom.addWidget(self.opacity_slider, 1)
        bottom.addWidget(self.opacity_value)
        bottom.addWidget(self.up_button)
        bottom.addWidget(self.down_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.addWidget(title)
        layout.addWidget(self.tree, 1)
        layout.addLayout(bottom)

        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self.rebuild)

        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.opacity_slider.valueChanged.connect(self._on_opacity_committed)
        self.opacity_slider.sliderMoved.connect(lambda v: self.opacity_value.setText(f"{v}%"))
        self.up_button.clicked.connect(lambda: self._move_selected(+1))
        self.down_button.clicked.connect(lambda: self._move_selected(-1))
        self._update_controls()

    # --- wiring ------------------------------------------------------------------

    def bind(self, layer_ctrl) -> None:
        self._ctrl = layer_ctrl
        layer_ctrl.layers_changed.connect(self.schedule_refresh)
        layer_ctrl.main.undo_group.indexChanged.connect(self.schedule_refresh)
        layer_ctrl.main.undo_group.activeStackChanged.connect(self.schedule_refresh)
        layer_ctrl.viewer.layers_refreshed.connect(self.schedule_refresh)
        self.schedule_refresh()

    def schedule_refresh(self, *_):
        if self.isVisible():
            self._refresh_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.schedule_refresh()

    # --- building ----------------------------------------------------------------

    def _group_label(self, group: LayerGroup) -> str:
        # Literal tr() calls so lupdate can see them; the English matches
        # core.layers.DEFAULT_GROUP_NAMES (and the PSD export's group names).
        labels = {
            LayerGroup.TEXT: self.tr("Editable Text"),
            LayerGroup.BOXES: self.tr("Boxes"),
            LayerGroup.STROKES: self.tr("Brush Strokes"),
            LayerGroup.PATCHES: self.tr("Inpaint Patches"),
            LayerGroup.RAW: self.tr("Raw Image"),
        }
        return labels.get(group, DEFAULT_GROUP_NAMES[group])

    def _default_object_name(self, group: LayerGroup, item, number: int) -> str:
        if group is LayerGroup.TEXT and hasattr(item, "toPlainText"):
            text = " ".join(item.toPlainText().split())
            if text:
                return text if len(text) <= 32 else text[:31] + "…"
            return self.tr("Text")
        base = {
            LayerGroup.BOXES: self.tr("Box"),
            LayerGroup.STROKES: self.tr("Stroke"),
            LayerGroup.PATCHES: self.tr("Patch"),
        }.get(group, self.tr("Layer"))
        return f"{base} {number}"

    def _page_suffix(self, item) -> str:
        viewer = self._ctrl.viewer
        if not getattr(viewer, "webtoon_mode", False):
            return ""
        layout = getattr(getattr(viewer, "webtoon_manager", None), "layout_manager", None)
        if layout is None:
            return ""
        idx = layout.get_page_at_position(item.sceneBoundingRect().center().y())
        return f"  · p{idx + 1}"

    def _set_state_icons(self, row, visible: bool, locked: bool, lockable: bool = True):
        row.setIcon(COL_VIS, self._icons["eye" if visible else "eye-off"])
        row.setToolTip(COL_VIS, self.tr("Hide") if visible else self.tr("Show"))
        if lockable:
            row.setIcon(COL_LOCK, self._icons["lock" if locked else "lock-open"])
            row.setToolTip(COL_LOCK, self.tr("Unlock") if locked else self.tr("Lock"))

    def rebuild(self) -> None:
        if self._ctrl is None:
            return
        doc = self._ctrl.document()
        selected = {r.data(COL_NAME, ROLE_OBJECT) or r.data(COL_NAME, ROLE_GROUP)
                    for r in self.tree.selectedItems()}
        expanded = {self.tree.topLevelItem(i).data(COL_NAME, ROLE_GROUP)
                    for i in range(self.tree.topLevelItemCount())
                    if self.tree.topLevelItem(i).isExpanded()}
        first_build = self.tree.topLevelItemCount() == 0

        by_group: dict[LayerGroup, list] = {g: [] for g in LayerGroup}
        for group, oid, item in self._ctrl.page_objects():
            by_group[group].append((oid, item))

        self._rebuilding = True
        self.tree.setUpdatesEnabled(False)
        try:
            self.tree.clear()
            for group in reversed(PAINT_ORDER):  # topmost first, like an editor
                gprops = doc.group(group)
                grow = QtWidgets.QTreeWidgetItem(self.tree)
                members = by_group[group]
                label = self._group_label(group)
                grow.setText(COL_NAME, f"{label} ({len(members)})" if group is not LayerGroup.RAW else label)
                grow.setData(COL_NAME, ROLE_GROUP, group.value)
                font = grow.font(COL_NAME)
                font.setBold(True)
                grow.setFont(COL_NAME, font)
                self._set_state_icons(grow, gprops.visible, gprops.locked,
                                      lockable=group is not LayerGroup.RAW)

                # scene.items() is topmost-first, the order an editor lists layers.
                count = len(members)
                for i, (oid, item) in enumerate(members):
                    props = self._ctrl.object_props(oid)
                    orow = QtWidgets.QTreeWidgetItem(grow)
                    name = props.name or self._default_object_name(group, item, count - i)
                    orow.setText(COL_NAME, name + self._page_suffix(item))
                    orow.setData(COL_NAME, ROLE_OBJECT, oid)
                    orow.setData(COL_NAME, ROLE_GROUP, group.value)
                    orow.setFlags(orow.flags() | Qt.ItemFlag.ItemIsEditable)
                    self._set_state_icons(orow, props.visible, props.locked)
                    if not gprops.visible or not props.visible:
                        # Shown greyed while it is not on the canvas, whether
                        # it or its whole group is hidden.
                        orow.setForeground(COL_NAME, self.palette().brush(
                            QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.Text))
                    if props.opacity < 1.0:
                        orow.setToolTip(COL_NAME, self.tr("Opacity") + f" {round(props.opacity * 100)}%")
                    if oid in selected:
                        orow.setSelected(True)
                if first_build or group.value in expanded:
                    grow.setExpanded(group is not LayerGroup.RAW)
                if group.value in selected:
                    grow.setSelected(True)
        finally:
            self.tree.setUpdatesEnabled(True)
            self._rebuilding = False
        self._update_controls()

    # --- reactions ---------------------------------------------------------------

    @staticmethod
    def _row_target(row):
        oid = row.data(COL_NAME, ROLE_OBJECT)
        group = LayerGroup(row.data(COL_NAME, ROLE_GROUP))
        return oid, group

    def _on_item_clicked(self, row, column):
        if self._ctrl is None or column not in (COL_VIS, COL_LOCK):
            return
        oid, group = self._row_target(row)
        if oid is None:
            gprops = self._ctrl.document().group(group)
            if column == COL_VIS:
                self._ctrl.set_group_props(group, visible=not gprops.visible)
            elif group is not LayerGroup.RAW:
                self._ctrl.set_group_props(group, locked=not gprops.locked)
            return
        props = self._ctrl.object_props(oid)
        if props is None:
            return
        if column == COL_VIS:
            self._ctrl.set_object_props([oid], text=self.tr("Toggle layer visibility"),
                                        visible=not props.visible)
        else:
            self._ctrl.set_object_props([oid], text=self.tr("Toggle layer lock"),
                                        locked=not props.locked)

    def _on_item_changed(self, row, column):
        if self._rebuilding or self._ctrl is None or column != COL_NAME:
            return
        oid, _group = self._row_target(row)
        if oid is None:
            return
        text = row.text(COL_NAME).split("  · p")[0].strip()
        self._ctrl.set_object_props([oid], text=self.tr("Rename layer"), name=text)

    def _selected_targets(self):
        return [self._row_target(r) for r in self.tree.selectedItems()]

    def _on_selection_changed(self):
        if self._rebuilding:
            return
        self._update_controls()
        targets = self._selected_targets()
        if self._ctrl is not None and len(targets) == 1 and targets[0][0]:
            self._ctrl.select_on_canvas(targets[0][0])

    def _update_controls(self):
        targets = self._selected_targets() if self._ctrl is not None else []
        has = bool(targets)
        self.opacity_slider.setEnabled(has)
        one_object = len(targets) == 1 and targets[0][0] is not None
        self.up_button.setEnabled(one_object)
        self.down_button.setEnabled(one_object)
        if not has:
            return
        oid, group = targets[0]
        if oid is None:
            value = self._ctrl.document().group(group).opacity
        else:
            props = self._ctrl.object_props(oid)
            value = props.opacity if props is not None else 1.0
        self.opacity_slider.blockSignals(True)
        self.opacity_slider.setValue(round(value * 100))
        self.opacity_slider.blockSignals(False)
        self.opacity_value.setText(f"{round(value * 100)}%")

    def _on_opacity_committed(self, value: int):
        if self._ctrl is None:
            return
        self.opacity_value.setText(f"{value}%")
        opacity = value / 100.0
        targets = self._selected_targets()
        object_ids = [oid for oid, _g in targets if oid]
        for oid, group in targets:
            if oid is None:
                self._ctrl.set_group_props(group, opacity=opacity)
        if object_ids:
            self._ctrl.set_object_props(object_ids, text=self.tr("Change layer opacity"),
                                        opacity=opacity)

    def _move_selected(self, direction: int):
        targets = self._selected_targets()
        if self._ctrl is None or len(targets) != 1 or targets[0][0] is None:
            return
        oid, _group = targets[0]
        row = self.tree.selectedItems()[0]
        parent = row.parent()
        ids_top_first = [parent.child(i).data(COL_NAME, ROLE_OBJECT) for i in range(parent.childCount())]
        i = ids_top_first.index(oid)
        j = i - direction  # "up" means towards the top of the list
        if not 0 <= j < len(ids_top_first):
            return
        ids_top_first[i], ids_top_first[j] = ids_top_first[j], ids_top_first[i]
        self._ctrl.reorder(list(reversed(ids_top_first)))
