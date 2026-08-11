"""The document's layer stack, as a control the user can actually read.

A page is three stacked layers — the artwork, the patches that cover the
original lettering, and the translated text drawn on top — plus two things
that only exist while you work: the detection boxes and the brush strokes.
Those five used to be four unlabelled checkboxes in the toolbar, in no
particular order, with the artwork missing altogether.

Here they are one list in stacking order, output layers first, each with an
eye and (for the output layers) an opacity slider. Fading the cleaner layer
is how you check what it covered without undoing it.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ..dayu_widgets.check_box import MCheckBox
from ..dayu_widgets.divider import MDivider
from ..dayu_widgets.label import MLabel
from ..dayu_widgets.slider import MSlider


class LayerRow(QtWidgets.QWidget):
    """One layer: a visibility box and, optionally, an opacity slider."""

    visibility_changed = QtCore.Signal(str, bool)
    opacity_changed = QtCore.Signal(str, float)

    def __init__(self, layer: str, label: str, tooltip: str, with_opacity: bool, parent=None):
        super().__init__(parent)
        self.layer = layer

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(8)

        self.visible_checkbox = MCheckBox(label)
        self.visible_checkbox.setChecked(True)
        self.visible_checkbox.setToolTip(tooltip)
        self.visible_checkbox.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.visible_checkbox.toggled.connect(
            lambda checked: self.visibility_changed.emit(self.layer, checked)
        )
        row.addWidget(self.visible_checkbox, 1)

        self.opacity_slider = None
        if with_opacity:
            self.opacity_slider = MSlider(QtCore.Qt.Orientation.Horizontal)
            self.opacity_slider.setRange(0, 100)
            self.opacity_slider.setValue(100)
            self.opacity_slider.setFixedWidth(90)
            self.opacity_slider.setToolTip(QtCore.QCoreApplication.translate(
                "LayerRow", "Layer opacity"
            ))
            self.opacity_slider.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
            self.opacity_slider.valueChanged.connect(
                lambda value: self.opacity_changed.emit(self.layer, value / 100.0)
            )
            row.addWidget(self.opacity_slider)


class DocumentLayersPanel(QtWidgets.QWidget):
    """The five layers of a page, output ones on top."""

    visibility_changed = QtCore.Signal(str, bool)
    opacity_changed = QtCore.Signal(str, float)

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        self.rows: dict[str, LayerRow] = {}

        # Listed top-down the way they stack on the page.
        output = [
            ('text', self.tr("Text"),
             self.tr("The translated text drawn on top of the page.")),
            ('patches', self.tr("Cleaner"),
             self.tr("The patches that cover the original lettering.\n"
                     "Fade this to check what was painted over.")),
            ('image', self.tr("Original image"),
             self.tr("The untouched artwork underneath everything.")),
        ]
        working = [
            ('boxes', self.tr("Detection boxes"),
             self.tr("The boxes the detector found. Not part of the exported page.")),
            ('strokes', self.tr("Brush strokes"),
             self.tr("Your segmentation strokes. Not part of the exported page.")),
        ]

        layout.addWidget(MLabel(self.tr("Page")).secondary())
        for layer, label, tooltip in output:
            self._add_row(layout, layer, label, tooltip, with_opacity=True)

        layout.addSpacing(4)
        layout.addWidget(MDivider())
        layout.addWidget(MLabel(self.tr("While editing")).secondary())
        for layer, label, tooltip in working:
            self._add_row(layout, layer, label, tooltip, with_opacity=False)

    def _add_row(self, layout, layer, label, tooltip, with_opacity):
        row = LayerRow(layer, label, tooltip, with_opacity, self)
        row.visibility_changed.connect(self.visibility_changed)
        row.opacity_changed.connect(self.opacity_changed)
        self.rows[layer] = row
        layout.addWidget(row)

    def sync_from(self, visibility: dict, opacity: dict) -> None:
        """Match the controls to the viewer's current state."""
        for layer, row in self.rows.items():
            row.visible_checkbox.blockSignals(True)
            row.visible_checkbox.setChecked(bool(visibility.get(layer, True)))
            row.visible_checkbox.blockSignals(False)

            if row.opacity_slider is not None:
                row.opacity_slider.blockSignals(True)
                row.opacity_slider.setValue(int(round(opacity.get(layer, 1.0) * 100)))
                row.opacity_slider.blockSignals(False)
