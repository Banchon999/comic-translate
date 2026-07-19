from PySide6 import QtWidgets
from ..dayu_widgets.label import MLabel
from ..dayu_widgets.spin_box import MSpinBox
from ..dayu_widgets.browser import MClickBrowserFileToolButton
from ..dayu_widgets.check_box import MCheckBox
from ..dayu_widgets.combo_box import MFontComboBox

class TextRenderingPage(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QtWidgets.QVBoxLayout(self)

        # Font section
        font_layout = QtWidgets.QVBoxLayout()
        min_font_layout = QtWidgets.QHBoxLayout()
        max_font_layout = QtWidgets.QHBoxLayout()
        min_font_label = MLabel(self.tr("Minimum Font Size:"))
        max_font_label = MLabel(self.tr("Maximum Font Size:"))

        self.min_font_spinbox = MSpinBox().small()
        self.min_font_spinbox.setFixedWidth(60)
        self.min_font_spinbox.setMaximum(100)
        self.min_font_spinbox.setValue(9)

        self.max_font_spinbox = MSpinBox().small()
        self.max_font_spinbox.setFixedWidth(60)
        self.max_font_spinbox.setMaximum(100)
        self.max_font_spinbox.setValue(40)

        min_font_layout.addWidget(min_font_label)
        min_font_layout.addWidget(self.min_font_spinbox)
        min_font_layout.addStretch()

        max_font_layout.addWidget(max_font_label)
        max_font_layout.addWidget(self.max_font_spinbox)
        max_font_layout.addStretch()

        font_label = MLabel(self.tr("Font:")).h4()

        font_browser_layout = QtWidgets.QHBoxLayout()
        import_font_label = MLabel(self.tr("Import Font:"))
        self.font_browser = MClickBrowserFileToolButton(multiple=True)
        self.font_browser.set_dayu_filters([".ttf", ".ttc", ".otf", ".woff", ".woff2"])
        self.font_browser.setToolTip(self.tr("Import the Font to use for Rendering Text on Images"))

        font_browser_layout.addWidget(import_font_label)
        font_browser_layout.addWidget(self.font_browser)
        font_browser_layout.addStretch()

        # Per-text-type fonts: bubbles vs free text (SFX/narration)
        self.per_class_fonts_checkbox = MCheckBox(
            self.tr("Use different fonts for Speech Bubbles and Free Text")
        )

        bubble_font_layout = QtWidgets.QHBoxLayout()
        bubble_font_label = MLabel(self.tr("Speech Bubble Font:"))
        self.bubble_font_combo = MFontComboBox().small()
        self.bubble_font_combo.setMinimumWidth(220)
        bubble_font_layout.addWidget(bubble_font_label)
        bubble_font_layout.addWidget(self.bubble_font_combo)
        bubble_font_layout.addStretch()

        free_font_layout = QtWidgets.QHBoxLayout()
        free_font_label = MLabel(self.tr("Free Text / SFX Font:"))
        self.free_font_combo = MFontComboBox().small()
        self.free_font_combo.setMinimumWidth(220)
        free_font_layout.addWidget(free_font_label)
        free_font_layout.addWidget(self.free_font_combo)
        free_font_layout.addStretch()

        self._per_class_font_widgets = (
            bubble_font_label, self.bubble_font_combo,
            free_font_label, self.free_font_combo,
        )
        self.per_class_fonts_checkbox.stateChanged.connect(self._sync_per_class_font_widgets)
        self._sync_per_class_font_widgets()

        font_layout.addWidget(font_label)
        font_layout.addLayout(font_browser_layout)
        font_layout.addWidget(self.per_class_fonts_checkbox)
        font_layout.addLayout(bubble_font_layout)
        font_layout.addLayout(free_font_layout)
        font_layout.addLayout(min_font_layout)
        font_layout.addLayout(max_font_layout)

        # Uppercase
        self.uppercase_checkbox = MCheckBox(self.tr("Render Text in UpperCase"))

        layout.addWidget(self.uppercase_checkbox)
        layout.addSpacing(10)
        layout.addLayout(font_layout)
        layout.addSpacing(10)
        layout.addStretch(1)

    def _sync_per_class_font_widgets(self, *args):
        enabled = self.per_class_fonts_checkbox.isChecked()
        for widget in self._per_class_font_widgets:
            widget.setEnabled(enabled)
