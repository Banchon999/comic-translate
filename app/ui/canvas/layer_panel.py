"""Per-item layer list for the editor canvas.

The Layers bar in the editor header switches whole pipeline stages on and off.
This panel goes one level down: every rectangle, brush stroke, inpaint patch and
rendered text block gets its own row that can be hidden, locked or faded, the way
a layer list in an image editor works.

Rows are rebuilt from the scene rather than mirrored into a second model, so the
panel cannot drift out of sync with what is actually on the canvas.
"""

from PySide6 import QtCore, QtWidgets
from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import QGraphicsPixmapItem

from .layer_state import item_opacity, item_visible, set_item_opacity, set_item_visible
from .rectangle import MoveableRectItem
from .text_item import TextBlockItem


# layer key -> (heading, does this item belong to the layer?)
LAYER_GROUPS = (
    ('text', 'Text'),
    ('boxes', 'Boxes'),
    ('strokes', 'Segment'),
    ('patches', 'Clean'),
)

ITEM_ROLE = QtCore.Qt.ItemDataRole.UserRole
LAYER_ROLE = QtCore.Qt.ItemDataRole.UserRole + 1


def layer_of(item, photo) -> str | None:
    """Which layer a scene item belongs to, or None if it is not a layer item."""
    if isinstance(item, TextBlockItem):
        return 'text'
    if isinstance(item, MoveableRectItem):
        return 'boxes'
    if isinstance(item, QtWidgets.QGraphicsPathItem) and item is not photo:
        return 'strokes'
    if (
        isinstance(item, QGraphicsPixmapItem)
        and item is not photo
        and item.data(0) is not None  # inpaint patches carry a hash key
    ):
        return 'patches'
    return None


def describe(item, index: int) -> str:
    if isinstance(item, TextBlockItem):
        text = item.toPlainText().strip().replace('\n', ' ')
        return text[:40] if text else f"Text {index}"
    if isinstance(item, MoveableRectItem):
        rect = item.boundingRect()
        return f"Box {index} — {int(rect.width())}×{int(rect.height())}"
    if isinstance(item, QtWidgets.QGraphicsPathItem):
        return f"Stroke {index}"
    return f"Patch {index}"


class ItemPropertyCommand(QUndoCommand):
    """Undoable change to one scene item's visibility, lock state or opacity."""

    def __init__(self, item, prop: str, old_value, new_value, text: str, viewer=None):
        super().__init__(text)
        self.item = item
        self.prop = prop
        self.old_value = old_value
        self.new_value = new_value
        self.viewer = viewer

    def _apply(self, value):
        try:
            if self.prop == 'visible':
                # Recorded on the item, not pushed straight to Qt: the layer it
                # belongs to has a say too, and the viewer combines the two.
                set_item_visible(self.item, value)
            elif self.prop == 'locked':
                self.item.setFlag(
                    QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not value
                )
                self.item.setFlag(
                    QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, not value
                )
                return
            elif self.prop == 'opacity':
                set_item_opacity(self.item, value)
            else:
                return
        except RuntimeError:
            # The item was removed from the scene; nothing to restore.
            return

        if self.viewer is not None:
            self.viewer.apply_layer_visibility()
        else:
            self.item.setVisible(item_visible(self.item))
            self.item.setOpacity(item_opacity(self.item))

    def redo(self):
        self._apply(self.new_value)

    def undo(self):
        self._apply(self.old_value)


class LayerPanel(QtWidgets.QWidget):
    """Tree of the canvas items, grouped by pipeline stage."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.viewer = None
        self.undo_stack_provider = None
        self._updating = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels([self.tr("Layer"), self.tr("Show"), self.tr("Lock")])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        header = self.tree.header()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.tree)

        opacity_row = QtWidgets.QHBoxLayout()
        self.opacity_label = QtWidgets.QLabel(self.tr("Opacity"))
        self.opacity_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(100)
        self.opacity_slider.setEnabled(False)
        self.opacity_slider.sliderReleased.connect(self._on_opacity_committed)
        self.opacity_slider.valueChanged.connect(self._on_opacity_preview)
        opacity_row.addWidget(self.opacity_label)
        opacity_row.addWidget(self.opacity_slider)
        layout.addLayout(opacity_row)

        self._opacity_before_drag = None

    def attach(self, viewer, undo_stack_provider):
        """Point the panel at a viewer; `undo_stack_provider` returns the live stack."""
        self.viewer = viewer
        self.undo_stack_provider = undo_stack_provider
        self.refresh()

    def _push(self, command):
        stack = self.undo_stack_provider() if self.undo_stack_provider else None
        if stack is not None:
            stack.push(command)
        else:
            command.redo()

    # Building

    def refresh(self):
        if self.viewer is None:
            return

        selected = self._selected_item()

        self._updating = True
        self.tree.clear()
        try:
            photo = getattr(self.viewer, 'photo', None)
            grouped = {key: [] for key, _ in LAYER_GROUPS}
            for item in self.viewer._scene.items():
                key = layer_of(item, photo)
                if key is not None:
                    grouped[key].append(item)

            for key, heading in LAYER_GROUPS:
                items = grouped[key]
                group_row = QtWidgets.QTreeWidgetItem(
                    self.tree, [f"{self.tr(heading)} ({len(items)})", "", ""]
                )
                group_row.setData(0, LAYER_ROLE, key)
                group_row.setFlags(
                    QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsUserCheckable
                )
                group_row.setCheckState(
                    1,
                    QtCore.Qt.CheckState.Checked
                    if self.viewer.layer_visibility.get(key, True)
                    else QtCore.Qt.CheckState.Unchecked,
                )

                # Topmost first, like a layer stack.
                for index, item in enumerate(reversed(items), start=1):
                    row = QtWidgets.QTreeWidgetItem(group_row, [describe(item, index), "", ""])
                    row.setData(0, ITEM_ROLE, item)
                    row.setFlags(
                        QtCore.Qt.ItemFlag.ItemIsEnabled
                        | QtCore.Qt.ItemFlag.ItemIsSelectable
                        | QtCore.Qt.ItemFlag.ItemIsUserCheckable
                    )
                    row.setCheckState(
                        1,
                        QtCore.Qt.CheckState.Checked
                        if item_visible(item)
                        else QtCore.Qt.CheckState.Unchecked,
                    )
                    movable = bool(
                        item.flags() & QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                    )
                    row.setCheckState(
                        2,
                        QtCore.Qt.CheckState.Unchecked
                        if movable
                        else QtCore.Qt.CheckState.Checked,
                    )
                    if item is selected:
                        self.tree.setCurrentItem(row)

                group_row.setExpanded(True)
        finally:
            self._updating = False

        self._sync_opacity_slider()

    def _selected_item(self):
        row = self.tree.currentItem()
        item = row.data(0, ITEM_ROLE) if row is not None else None
        if item is None:
            return None
        try:
            item.isVisible()
        except RuntimeError:
            # Deleted from the scene since the row was built; the next refresh
            # drops the row.
            return None
        return item

    # Reacting to the user

    def _on_item_changed(self, row: QtWidgets.QTreeWidgetItem, column: int):
        if self._updating or self.viewer is None or column not in (1, 2):
            return

        checked = row.checkState(column) == QtCore.Qt.CheckState.Checked
        layer_key = row.data(0, LAYER_ROLE)

        if layer_key is not None:
            if column == 1:
                # Defer to the existing per-stage toggle so the header bar agrees.
                self.viewer.set_layer_visibility(layer_key, checked)
                self.refresh()
            return

        item = row.data(0, ITEM_ROLE)
        if item is None:
            return
        try:
            item.isVisible()
        except RuntimeError:
            return

        if column == 1:
            self._push(
                ItemPropertyCommand(
                    item, 'visible', not checked, checked,
                    self.tr("Show/Hide Layer"), self.viewer,
                )
            )
        else:
            movable = bool(
                item.flags() & QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            )
            self._push(
                ItemPropertyCommand(
                    item, 'locked', not movable, checked,
                    self.tr("Lock Layer"), self.viewer,
                )
            )

    def _on_selection_changed(self):
        if self._updating:
            return
        self._sync_opacity_slider()

        item = self._selected_item()
        if item is None or self.viewer is None:
            return
        scene = self.viewer._scene
        scene.clearSelection()
        if item.flags() & QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable:
            item.setSelected(True)

    def _sync_opacity_slider(self):
        item = self._selected_item()
        self.opacity_slider.blockSignals(True)
        self.opacity_slider.setEnabled(item is not None)
        self.opacity_slider.setValue(int(round((item_opacity(item) if item else 1.0) * 100)))
        self.opacity_slider.blockSignals(False)

    def _on_opacity_preview(self, value: int):
        """Follow the slider live; the undo entry is only pushed on release."""
        item = self._selected_item()
        if item is None:
            return
        if self._opacity_before_drag is None:
            self._opacity_before_drag = item_opacity(item)
        set_item_opacity(item, value / 100.0)
        if self.viewer is not None:
            self.viewer.apply_layer_visibility()
        else:
            item.setOpacity(value / 100.0)

    def _on_opacity_committed(self):
        item = self._selected_item()
        if item is None or self._opacity_before_drag is None:
            return
        old_value = self._opacity_before_drag
        self._opacity_before_drag = None
        new_value = self.opacity_slider.value() / 100.0
        if old_value == new_value:
            return
        set_item_opacity(item, old_value)  # let the command apply the change
        self._push(
            ItemPropertyCommand(
                item, 'opacity', old_value, new_value, self.tr("Layer Opacity"), self.viewer
            )
        )
