"""Edit a text item's stacked strokes: colour and width per layer, innermost first."""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from core.text_style import StrokeLayer, stroke_layers_from

from .dayu_widgets.push_button import MPushButton
from .dayu_widgets.qt import MIcon


class _LayerRow(QtWidgets.QWidget):
    removed = QtCore.Signal(object)

    def __init__(self, layer: StrokeLayer, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.index_label = QtWidgets.QLabel()
        self.index_label.setFixedWidth(22)
        self.color_button = QtWidgets.QPushButton()
        self.color_button.setFixedSize(30, 30)
        self.color_button.setToolTip(self.tr("Stroke Color"))
        self.width_spin = QtWidgets.QDoubleSpinBox()
        self.width_spin.setRange(0.5, 60.0)
        self.width_spin.setSingleStep(0.5)
        self.width_spin.setDecimals(1)
        self.width_spin.setSuffix(" px")
        self.width_spin.setToolTip(self.tr("Stroke Width"))
        self.width_spin.setValue(float(layer.width))
        self.remove_button = MPushButton()
        self.remove_button.setIcon(MIcon("trash_line.svg"))
        self.remove_button.setToolTip(self.tr("Remove Layer"))
        lay.addWidget(self.index_label)
        lay.addWidget(self.color_button)
        lay.addWidget(self.width_spin, 1)
        lay.addWidget(self.remove_button)
        self._color = QtGui.QColor(layer.color)
        self._paint_color()
        self.color_button.clicked.connect(self._pick_color)
        self.remove_button.clicked.connect(lambda: self.removed.emit(self))

    def _paint_color(self):
        self.color_button.setStyleSheet(
            f"background-color: {self._color.name()}; border: 1px solid rgba(128,128,128,0.6); border-radius: 5px;"
        )

    def _pick_color(self):
        color = QtWidgets.QColorDialog.getColor(
            self._color, self, self.tr("Stroke Color"),
            QtWidgets.QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if color.isValid():
            self._color = color
            self._paint_color()

    def layer(self) -> StrokeLayer:
        return StrokeLayer(self._color.name(QtGui.QColor.NameFormat.HexArgb), float(self.width_spin.value()))


class StrokeLayersDialog(QtWidgets.QDialog):
    """Rows of (colour, width). Row 1 sits just outside the outline, each next row outside that."""

    DEFAULT_COLORS = ("#ff000000", "#ffffffff")

    def __init__(self, layers=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Stroke Layers"))
        self.setMinimumWidth(340)
        outer = QtWidgets.QVBoxLayout(self)
        hint = QtWidgets.QLabel(self.tr(
            "Extra strokes stacked outside the outline. The first layer sits just outside it, "
            "each next one outside the layer before."
        ))
        hint.setWordWrap(True)
        outer.addWidget(hint)
        self.rows_layout = QtWidgets.QVBoxLayout()
        self.rows_layout.setSpacing(6)
        outer.addLayout(self.rows_layout)
        self.add_button = MPushButton(self.tr("Add Layer"))
        self.add_button.setIcon(MIcon("add_line.svg"))
        self.add_button.clicked.connect(self.add_layer)
        outer.addWidget(self.add_button)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self._rows: list[_LayerRow] = []
        for layer in stroke_layers_from(layers):
            self._add_row(layer)

    def add_layer(self):
        color = self.DEFAULT_COLORS[len(self._rows) % len(self.DEFAULT_COLORS)]
        self._add_row(StrokeLayer(color, 3.0))

    def _add_row(self, layer: StrokeLayer):
        row = _LayerRow(layer, self)
        row.removed.connect(self._remove_row)
        self._rows.append(row)
        self.rows_layout.addWidget(row)
        self._renumber()

    def _remove_row(self, row):
        self._rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._renumber()

    def _renumber(self):
        for index, row in enumerate(self._rows, start=1):
            row.index_label.setText(str(index))

    def layers(self) -> list[StrokeLayer]:
        return [row.layer() for row in self._rows]
