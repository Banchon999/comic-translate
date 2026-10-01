from PySide6 import QtWidgets, QtGui
from ..dayu_widgets.label import MLabel
from ..dayu_widgets.spin_box import MSpinBox
from ..dayu_widgets.browser import MClickBrowserFileToolButton
from ..dayu_widgets.check_box import MCheckBox
from ..dayu_widgets.combo_box import MComboBox, MFontComboBox


class ColorPickerButton(QtWidgets.QPushButton):
    """Small swatch button that opens a color dialog and remembers the choice."""

    def __init__(self, color: str = "#000000", parent=None):
        super().__init__(parent)
        self.setFixedSize(30, 30)
        self.set_color(color)
        self.clicked.connect(self._pick_color)

    def set_color(self, color: str) -> None:
        color = color or "#000000"
        self.setProperty("selected_color", color)
        self.setStyleSheet(
            f"background-color: {color}; border: 1px solid gray; border-radius: 5px;"
        )

    def get_color(self) -> str:
        return self.property("selected_color") or "#000000"

    def _pick_color(self):
        dialog = QtWidgets.QColorDialog(QtGui.QColor(self.get_color()), self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            chosen = dialog.selectedColor()
            if chosen.isValid():
                self.set_color(chosen.name())


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

        # Default style for newly rendered text.
        # The toolbar colour buttons follow whatever text item is selected, so
        # they can't hold a "preset". These do.
        style_label = MLabel(self.tr("Default Style for Rendered Text:")).h4()
        self.use_style_defaults_checkbox = MCheckBox(
            self.tr("Always render new text with the colours set here")
        )
        self.use_style_defaults_checkbox.setToolTip(self.tr(
            "When enabled, translated text is rendered with these colours instead of\n"
            "whatever the toolbar happens to show (the toolbar follows the selected text)."
        ))

        default_text_color_layout = QtWidgets.QHBoxLayout()
        self.default_text_color_label = MLabel(self.tr("Text Colour:"))
        self.default_text_color_button = ColorPickerButton("#000000")
        default_text_color_layout.addWidget(self.default_text_color_label)
        default_text_color_layout.addWidget(self.default_text_color_button)
        default_text_color_layout.addStretch()

        default_outline_layout = QtWidgets.QHBoxLayout()
        self.default_outline_color_label = MLabel(self.tr("Outline Colour:"))
        self.default_outline_color_button = ColorPickerButton("#FFFFFF")
        self.default_outline_width_label = MLabel(self.tr("Width:"))
        self.default_outline_width_combo = MComboBox().small()
        self.default_outline_width_combo.setFixedWidth(70)
        self.default_outline_width_combo.addItems(
            ["0.5", "1.0", "1.15", "1.3", "1.4", "1.5", "2.0", "2.5", "3.0"]
        )
        self.default_outline_width_combo.setCurrentText("1.0")
        self.default_outline_width_combo.set_editable(True)
        default_outline_layout.addWidget(self.default_outline_color_label)
        default_outline_layout.addWidget(self.default_outline_color_button)
        default_outline_layout.addSpacing(10)
        default_outline_layout.addWidget(self.default_outline_width_label)
        default_outline_layout.addWidget(self.default_outline_width_combo)
        default_outline_layout.addStretch()

        self._style_default_widgets = (
            self.default_text_color_label, self.default_text_color_button,
            self.default_outline_color_label, self.default_outline_color_button,
            self.default_outline_width_label, self.default_outline_width_combo,
        )
        self.use_style_defaults_checkbox.stateChanged.connect(self._sync_style_default_widgets)
        self._sync_style_default_widgets()

        layout.addWidget(self.uppercase_checkbox)
        layout.addSpacing(10)
        layout.addLayout(font_layout)
        layout.addSpacing(10)
        layout.addWidget(style_label)
        layout.addWidget(self.use_style_defaults_checkbox)
        layout.addLayout(default_text_color_layout)
        layout.addLayout(default_outline_layout)
        layout.addSpacing(10)

        # Default text style (preset) per text type. Only its look — colour,
        # outline, strokes, shadow, gradient, warp — reaches new text; the
        # renderer has already fitted the text in its own font and size.
        preset_label = MLabel(self.tr("Default Text Style:")).h4()
        self.default_bubble_preset_combo = MComboBox().small()
        self.default_free_preset_combo = MComboBox().small()
        preset_hint = MLabel(self.tr(
            "Applied to newly rendered text: colours, outline, extra strokes, shadow, gradient and warp."
        )).secondary()
        preset_hint.setWordWrap(True)
        bubble_preset_layout = QtWidgets.QHBoxLayout()
        bubble_preset_layout.addWidget(MLabel(self.tr("Speech bubbles:")))
        bubble_preset_layout.addWidget(self.default_bubble_preset_combo)
        bubble_preset_layout.addStretch()
        free_preset_layout = QtWidgets.QHBoxLayout()
        free_preset_layout.addWidget(MLabel(self.tr("Free text and SFX:")))
        free_preset_layout.addWidget(self.default_free_preset_combo)
        free_preset_layout.addStretch()
        for combo in (self.default_bubble_preset_combo, self.default_free_preset_combo):
            combo.setMinimumWidth(160)
        layout.addWidget(preset_label)
        layout.addWidget(preset_hint)
        layout.addLayout(bubble_preset_layout)
        layout.addLayout(free_preset_layout)
        self.refresh_preset_choices([])
        layout.addStretch(1)

    def refresh_preset_choices(self, names, bubble: str | None = None, free: str | None = None):
        """Fill both default-style combos; keep (or set) what each one shows."""
        from app.ui.canvas.text.presets import preset_label

        for combo, wanted in ((self.default_bubble_preset_combo, bubble),
                              (self.default_free_preset_combo, free)):
            keep = combo.currentData() if wanted is None else wanted
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(self.tr("None"), "")
            for name in names:
                combo.addItem(preset_label(name), name)
            index = combo.findData(keep or "")
            combo.setCurrentIndex(max(0, index))
            combo.blockSignals(False)

    def _sync_per_class_font_widgets(self, *args):
        enabled = self.per_class_fonts_checkbox.isChecked()
        for widget in self._per_class_font_widgets:
            widget.setEnabled(enabled)

    def _sync_style_default_widgets(self, *args):
        enabled = self.use_style_defaults_checkbox.isChecked()
        for widget in self._style_default_widgets:
            widget.setEnabled(enabled)
