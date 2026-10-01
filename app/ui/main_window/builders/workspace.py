from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import QSettings
from PySide6.QtGui import QIntValidator

from app.ui.dayu_widgets import dayu_theme
from app.ui.dayu_widgets.browser import MDragFileButton
from app.ui.dayu_widgets.button_group import MPushButtonGroup, MToolButtonGroup
from app.ui.dayu_widgets.check_box import MCheckBox
from app.ui.dayu_widgets.combo_box import MComboBox, MFontComboBox
from app.ui.dayu_widgets.divider import MDivider
from app.ui.dayu_widgets.label import MLabel
from app.ui.dayu_widgets.line_edit import MLineEdit
from app.ui.dayu_widgets.loading import MLoading
from app.ui.dayu_widgets.progress_bar import MProgressBar
from app.ui.dayu_widgets.push_button import MPushButton
from app.ui.dayu_widgets.radio_button import MRadioButton
from app.ui.dayu_widgets.slider import MSlider
from app.ui.dayu_widgets.text_edit import MTextEdit
from app.ui.dayu_widgets.tool_button import MToolButton
from app.ui.file_tree_panel import FileTreePanel
from app.ui.layers_panel import LayersPanel
from app.ui.search_replace_panel import SearchReplacePanel
from app.ui.main_window.constants import supported_source_languages, supported_target_languages
from app.ui.main_window.editor_chrome import EditorStatusBar, GlossaryPeekPanel, style_step_buttons
from app.ui.dayu_widgets.qt import MIcon


class WorkspaceMixin:
    def _create_main_content(self):
        content_widget = QtWidgets.QWidget()

        header_layout = QtWidgets.QHBoxLayout()

        self.undo_tool_group = MToolButtonGroup(orientation=QtCore.Qt.Horizontal, exclusive=True)
        undo_tools = [
            {"svg": "undo.svg", "checkable": False, "tooltip": self.tr("Undo")},
            {"svg": "redo.svg", "checkable": False, "tooltip": self.tr("Redo")},
        ]
        self.undo_tool_group.set_button_list(undo_tools)

        button_config_list = [
            {"text": self.tr("Detect"), "dayu_type": MPushButton.DefaultType, "enabled": False},
            {"text": self.tr("Recognize"), "dayu_type": MPushButton.DefaultType, "enabled": False},
            {"text": self.tr("Translate"), "dayu_type": MPushButton.DefaultType, "enabled": False},
            {"text": self.tr("Segment"), "dayu_type": MPushButton.DefaultType, "enabled": False},
            {"text": self.tr("Clean"), "dayu_type": MPushButton.DefaultType, "enabled": False},
            {"text": self.tr("Render"), "dayu_type": MPushButton.DefaultType, "enabled": False},
        ]

        self.hbutton_group = MPushButtonGroup()
        self.hbutton_group.set_dayu_size(dayu_theme.small)
        self.hbutton_group.set_button_list(button_config_list)
        self.hbutton_group.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        for button in self.hbutton_group.get_button_group().buttons():
            button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        self.progress_bar = MProgressBar().auto_color()
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)

        self.loading = MLoading().small()
        self.loading.setVisible(False)

        self.manual_radio = MRadioButton(self.tr("Manual"))
        self.manual_radio.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        self.automatic_radio = MRadioButton(self.tr("Automatic"))
        self.automatic_radio.setChecked(True)
        self.automatic_radio.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        self.webtoon_toggle = MToolButton()
        self.webtoon_toggle.set_dayu_svg("webtoon-toggle.svg")
        self.webtoon_toggle.huge()
        self.webtoon_toggle.setCheckable(True)
        self.webtoon_toggle.setToolTip(
            self.tr("Toggle Webtoon Mode. " "For comics that are read in long vertical strips")
        )
        self.webtoon_toggle.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        self.translate_button = MPushButton(self.tr("Translate All"))
        self.translate_button.setEnabled(True)
        self.translate_button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.cancel_button = MPushButton(self.tr("Cancel"))
        self.cancel_button.setEnabled(True)
        self.cancel_button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.batch_report_button = MPushButton(self.tr("Report"))
        self.batch_report_button.setEnabled(False)
        self.batch_report_button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        self.layers_button = MToolButton().svg("layers.svg").icon_only()
        self.layers_button.setCheckable(True)
        self.layers_button.setToolTip(self.tr("Show the Layers panel"))
        self.layers_button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        self.file_tree_button = MToolButton().svg("folder-open.svg").icon_only()
        self.file_tree_button.setCheckable(True)
        self.file_tree_button.setToolTip(self.tr("Group the pages by the folder they came from"))
        self.file_tree_button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        # Top bar: the pipeline as a step bar (each step shows whether this
        # page has been through it), then mode and the batch run.
        style_step_buttons(self.hbutton_group)
        self.hbutton_group.setObjectName("toonStepBar")
        self.translate_button.set_dayu_type(MPushButton.PrimaryType)
        self.translate_button.setIcon(MIcon("run.svg", dayu_theme.on_primary_color))
        self.translate_button.setObjectName("toonRunButton")
        mode_switch = QtWidgets.QWidget()
        mode_switch.setObjectName("toonModeSwitch")
        mode_layout = QtWidgets.QHBoxLayout(mode_switch)
        mode_layout.setContentsMargins(8, 2, 8, 2)
        mode_layout.setSpacing(10)
        mode_layout.addWidget(self.manual_radio)
        mode_layout.addWidget(self.automatic_radio)

        header_layout.setContentsMargins(12, 6, 12, 6)
        header_layout.setSpacing(8)
        header_layout.addWidget(self.hbutton_group)
        header_layout.addWidget(self.loading)
        header_layout.addStretch()
        header_layout.addWidget(mode_switch)
        header_layout.addWidget(self.translate_button)
        header_layout.addWidget(self.cancel_button)
        header_layout.addWidget(self.batch_report_button)

        self.search_panel = SearchReplacePanel(self)
        self.search_panel.setVisible(False)

        left_layout = QtWidgets.QVBoxLayout()
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.image_card_layout = QtWidgets.QVBoxLayout()
        self.image_card_layout.addStretch(1)

        self.page_list.setLayout(self.image_card_layout)

        self.file_tree_panel = FileTreePanel()
        self.file_tree_panel.setVisible(False)

        # The Layers panel lives in the inspector's Layers tab (below); the
        # layers button switches to that tab.
        self.layers_panel = LayersPanel()

        # Pages and the folder tree share the column; a splitter lets
        # whichever the user is working in take the space.
        left_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        left_splitter.addWidget(self.file_tree_panel)
        left_splitter.addWidget(self.page_list)
        left_splitter.setStretchFactor(0, 2)
        left_splitter.setStretchFactor(1, 2)

        left_layout.addWidget(left_splitter)
        left_layout.addWidget(self.search_panel)
        left_widget = QtWidgets.QWidget()
        left_widget.setLayout(left_layout)

        self.central_stack = QtWidgets.QStackedWidget()

        self.drag_browser = MDragFileButton(text=self.tr("Click or drag files here"), multiple=True)
        self.drag_browser.set_dayu_svg("attachment_line.svg")
        self.drag_browser.set_dayu_filters(
            [
                ".png",
                ".jpg",
                ".jpeg",
                ".webp",
                ".bmp",
                ".avif",
                ".zip",
                ".cbz",
                ".cbr",
                ".cb7",
                ".cbt",
                ".pdf",
                ".epub",
                ".ctpr",
                ".psd",
            ]
        )
        self.drag_browser.setToolTip(
            self.tr("Import Images, PDFs, Epubs or Comic Book Archive Files(cbr, cbz, etc)")
        )
        self.central_stack.addWidget(self.drag_browser)
        self.central_stack.addWidget(self.image_viewer)

        central_widget = QtWidgets.QWidget()
        central_layout = QtWidgets.QVBoxLayout(central_widget)
        central_layout.addWidget(self.central_stack)
        central_layout.setContentsMargins(10, 10, 10, 10)

        input_layout = QtWidgets.QHBoxLayout()

        s_combo_text_layout = QtWidgets.QVBoxLayout()
        self.s_combo = MComboBox().medium()
        self.s_combo.addItems([self.tr(lang) for lang in supported_source_languages])
        self.s_combo.setToolTip(self.tr("Source Language"))
        s_combo_text_layout.addWidget(self.s_combo)
        self.s_text_edit = MTextEdit()
        self.s_text_edit.setFixedHeight(120)
        s_combo_text_layout.addWidget(self.s_text_edit)
        input_layout.addLayout(s_combo_text_layout)

        t_combo_text_layout = QtWidgets.QVBoxLayout()
        self.t_combo = MComboBox().medium()
        self.t_combo.addItems([self.tr(lang) for lang in supported_target_languages])
        self.t_combo.setToolTip(self.tr("Target Language"))
        t_combo_text_layout.addWidget(self.t_combo)
        self.t_text_edit = MTextEdit()
        self.t_text_edit.setFixedHeight(120)
        t_combo_text_layout.addWidget(self.t_text_edit)
        input_layout.addLayout(t_combo_text_layout)

        text_render_layout = QtWidgets.QVBoxLayout()
        font_settings_layout = QtWidgets.QHBoxLayout()

        self.font_dropdown = MFontComboBox().small()
        self.font_dropdown.setToolTip(self.tr("Font"))

        # Favourite fonts: a star that adds/removes the current font, and a
        # dropdown that jumps straight to one — so the handful you use are not
        # buried in the full system list every time.
        self.font_favourite_toggle = QtWidgets.QToolButton()
        self.font_favourite_toggle.setCheckable(True)
        self.font_favourite_toggle.setText("☆")  # ☆, becomes ★ when on
        self.font_favourite_toggle.setToolTip(self.tr("Add this font to favourites"))
        self.font_favourite_toggle.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.font_favourites_button = QtWidgets.QToolButton()
        self.font_favourites_button.setText("★▾")  # ★▾
        self.font_favourites_button.setToolTip(self.tr("Pick a favourite font"))
        self.font_favourites_button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.font_favourites_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.font_favourites_menu = QtWidgets.QMenu(self.font_favourites_button)
        self.font_favourites_button.setMenu(self.font_favourites_menu)

        self.font_size_dropdown = MComboBox().small()
        self.font_size_dropdown.setToolTip(self.tr("Font Size"))
        self.font_size_dropdown.addItems(
            ["4", "6", "8", "9", "10", "11", "12", "14", "16", "18", "20", "22", "24", "28", "32", "36", "48", "72"]
        )
        self.font_size_dropdown.setCurrentText("12")
        self.font_size_dropdown.setFixedWidth(60)
        self.font_size_dropdown.set_editable(True)

        self.line_spacing_dropdown = MComboBox().small()
        self.line_spacing_dropdown.setToolTip(self.tr("Line Spacing"))
        self.line_spacing_dropdown.addItems(["1.0", "1.1", "1.2", "1.3", "1.4", "1.5"])
        self.line_spacing_dropdown.setFixedWidth(60)
        self.line_spacing_dropdown.set_editable(True)

        self.letter_spacing_dropdown = MComboBox().small()
        self.letter_spacing_dropdown.setToolTip(self.tr("Letter Spacing"))
        self.letter_spacing_dropdown.addItems(["0", "0.5", "1", "1.5", "2", "3", "4", "-0.5", "-1"])
        self.letter_spacing_dropdown.setFixedWidth(60)
        self.letter_spacing_dropdown.set_editable(True)

        # Two rows, so the controls fit the one-column inspector: the face,
        # then its metrics.
        self.font_dropdown.setMinimumWidth(120)
        font_settings_layout.addWidget(self.font_dropdown, 1)
        font_settings_layout.addWidget(self.font_favourite_toggle)
        font_settings_layout.addWidget(self.font_favourites_button)
        font_metrics_layout = QtWidgets.QHBoxLayout()
        font_metrics_layout.addWidget(self.font_size_dropdown)
        font_metrics_layout.addWidget(self.line_spacing_dropdown)
        font_metrics_layout.addWidget(self.letter_spacing_dropdown)
        font_metrics_layout.addStretch()

        main_text_settings_layout = QtWidgets.QHBoxLayout()

        settings = QSettings("ComicLabs", "ComicTranslate")
        settings.beginGroup("text_rendering")
        dflt_clr = settings.value("color", "#000000")
        dflt_outline_check = settings.value("outline", True, type=bool)
        settings.endGroup()

        self.block_font_color_button = QtWidgets.QPushButton()
        self.block_font_color_button.setToolTip(self.tr("Font Color"))
        self.block_font_color_button.setFixedSize(30, 30)
        self.block_font_color_button.setStyleSheet(f"background-color: {dflt_clr}; border: none; border-radius: 5px;")
        self.block_font_color_button.setProperty("selected_color", dflt_clr)

        self.alignment_tool_group = MToolButtonGroup(orientation=QtCore.Qt.Horizontal, exclusive=True)
        alignment_tools = [
            {"svg": "tabler--align-left.svg", "checkable": True, "tooltip": "Align Left"},
            {"svg": "tabler--align-center.svg", "checkable": True, "tooltip": "Align Center"},
            {"svg": "tabler--align-right.svg", "checkable": True, "tooltip": "Align Right"},
        ]
        self.alignment_tool_group.set_button_list(alignment_tools)
        self.alignment_tool_group.set_dayu_checked(1)

        self.bold_button = self.create_tool_button(svg="bold.svg", checkable=True)
        self.bold_button.setToolTip(self.tr("Bold"))
        self.italic_button = self.create_tool_button(svg="italic.svg", checkable=True)
        self.italic_button.setToolTip(self.tr("Italic"))
        self.underline_button = self.create_tool_button(svg="underline.svg", checkable=True)
        self.underline_button.setToolTip(self.tr("Underline"))

        main_text_settings_layout.addWidget(self.block_font_color_button)
        main_text_settings_layout.addWidget(self.alignment_tool_group)
        main_text_settings_layout.addWidget(self.bold_button)
        main_text_settings_layout.addWidget(self.italic_button)
        main_text_settings_layout.addWidget(self.underline_button)
        main_text_settings_layout.addStretch()

        outline_settings_layout = QtWidgets.QHBoxLayout()

        self.outline_checkbox = MCheckBox(self.tr("Outline"))
        self.outline_checkbox.setChecked(dflt_outline_check)

        self.outline_font_color_button = QtWidgets.QPushButton()
        self.outline_font_color_button.setToolTip(self.tr("Outline Color"))
        self.outline_font_color_button.setFixedSize(30, 30)
        self.outline_font_color_button.setStyleSheet("background-color: white; border: none; border-radius: 5px;")
        self.outline_font_color_button.setProperty("selected_color", "#ffffff")

        self.outline_width_dropdown = MComboBox().small()
        self.outline_width_dropdown.setFixedWidth(60)
        self.outline_width_dropdown.setToolTip(self.tr("Outline Width"))
        self.outline_width_dropdown.addItems(
            ["0.5", "0.75", "1.0", "1.15", "1.3", "1.4", "1.5", "2.0", "2.5", "3.0"]
        )
        self.outline_width_dropdown.set_editable(True)

        self.stroke_layers_button = MPushButton(self.tr("+ Strokes")).small()
        self.stroke_layers_button.setToolTip(self.tr(
            "Stack extra strokes outside the outline, e.g. white then black."
        ))

        outline_settings_layout.addWidget(self.outline_checkbox)
        outline_settings_layout.addWidget(self.outline_font_color_button)
        outline_settings_layout.addWidget(self.outline_width_dropdown)
        outline_settings_layout.addWidget(self.stroke_layers_button)
        outline_settings_layout.addStretch()

        shadow_settings_layout = QtWidgets.QHBoxLayout()

        self.shadow_checkbox = MCheckBox(self.tr("Shadow"))

        self.shadow_color_button = QtWidgets.QPushButton()
        self.shadow_color_button.setToolTip(self.tr("Shadow Color"))
        self.shadow_color_button.setFixedSize(30, 30)
        self.shadow_color_button.setStyleSheet("background-color: #000000; border: none; border-radius: 5px;")
        self.shadow_color_button.setProperty("selected_color", "#000000")

        self.shadow_offset_x_dropdown = MComboBox().small()
        self.shadow_offset_x_dropdown.setToolTip(self.tr("Shadow Offset X"))
        self.shadow_offset_x_dropdown.addItems(["0", "1", "2", "3", "4", "6", "8", "-2", "-4"])
        self.shadow_offset_x_dropdown.setCurrentText("4")
        self.shadow_offset_x_dropdown.setFixedWidth(60)
        self.shadow_offset_x_dropdown.set_editable(True)

        self.shadow_offset_y_dropdown = MComboBox().small()
        self.shadow_offset_y_dropdown.setToolTip(self.tr("Shadow Offset Y"))
        self.shadow_offset_y_dropdown.addItems(["0", "1", "2", "3", "4", "6", "8", "-2", "-4"])
        self.shadow_offset_y_dropdown.setCurrentText("4")
        self.shadow_offset_y_dropdown.setFixedWidth(60)
        self.shadow_offset_y_dropdown.set_editable(True)

        self.shadow_blur_dropdown = MComboBox().small()
        self.shadow_blur_dropdown.setToolTip(self.tr("Shadow Blur"))
        self.shadow_blur_dropdown.addItems(["0", "2", "4", "6", "8", "12", "16", "24"])
        self.shadow_blur_dropdown.setCurrentText("0")
        self.shadow_blur_dropdown.setFixedWidth(60)
        self.shadow_blur_dropdown.set_editable(True)

        self.shadow_opacity_dropdown = MComboBox().small()
        self.shadow_opacity_dropdown.setToolTip(self.tr("Shadow Opacity (%)"))
        self.shadow_opacity_dropdown.addItems(["100", "80", "63", "50", "35", "20"])
        self.shadow_opacity_dropdown.setCurrentText("63")
        self.shadow_opacity_dropdown.setFixedWidth(60)
        self.shadow_opacity_dropdown.set_editable(True)

        shadow_settings_layout.addWidget(self.shadow_checkbox)
        shadow_settings_layout.addWidget(self.shadow_color_button)
        shadow_settings_layout.addWidget(self.shadow_opacity_dropdown)
        shadow_settings_layout.addWidget(self.shadow_offset_x_dropdown)
        shadow_settings_layout.addWidget(self.shadow_offset_y_dropdown)
        shadow_settings_layout.addWidget(self.shadow_blur_dropdown)
        shadow_settings_layout.addStretch()

        effects_settings_layout = QtWidgets.QHBoxLayout()

        self.gradient_checkbox = MCheckBox(self.tr("Gradient"))
        self.gradient_checkbox.setToolTip(self.tr(
            "Fade the fill from the text colour to a second one, across the whole text."
        ))

        self.gradient_color_button = QtWidgets.QPushButton()
        self.gradient_color_button.setToolTip(self.tr("Gradient End Color"))
        self.gradient_color_button.setFixedSize(30, 30)
        self.gradient_color_button.setStyleSheet("background-color: #ffffff; border: none; border-radius: 5px;")
        self.gradient_color_button.setProperty("selected_color", "#ffffff")

        self.gradient_angle_dropdown = MComboBox().small()
        self.gradient_angle_dropdown.setToolTip(self.tr("Gradient Angle"))
        self.gradient_angle_dropdown.addItems(["0", "45", "90", "135", "180", "225", "270", "315"])
        self.gradient_angle_dropdown.setCurrentText("90")
        self.gradient_angle_dropdown.setFixedWidth(60)
        self.gradient_angle_dropdown.set_editable(True)

        curve_label = MLabel(self.tr("Curve"))
        curve_label.setToolTip(self.tr(
            "Bend the baseline into an arc: positive arches up, negative sags."
        ))
        self.curvature_dropdown = MComboBox().small()
        self.curvature_dropdown.setToolTip(self.tr("Text Curve"))
        self.curvature_dropdown.addItems(
            ["-100", "-75", "-50", "-25", "0", "25", "50", "75", "100"]
        )
        self.curvature_dropdown.setCurrentText("0")
        self.curvature_dropdown.setFixedWidth(60)
        self.curvature_dropdown.set_editable(True)

        effects_settings_layout.addWidget(self.gradient_checkbox)
        effects_settings_layout.addWidget(self.gradient_color_button)
        effects_settings_layout.addWidget(self.gradient_angle_dropdown)
        effects_settings_layout.addStretch()
        self.perspective_button = MPushButton(self.tr("Perspective")).small()
        self.perspective_button.setCheckable(True)
        self.perspective_button.setToolTip(self.tr(
            "Drag a corner handle to move that corner on its own, or an edge handle to skew."
        ))
        self.reset_transform_button = MPushButton(self.tr("Reset")).small()
        self.reset_transform_button.setToolTip(self.tr("Remove the perspective and skew from the selected text."))

        curve_layout = QtWidgets.QHBoxLayout()
        curve_layout.addWidget(curve_label)
        curve_layout.addWidget(self.curvature_dropdown)
        curve_layout.addWidget(self.perspective_button)
        curve_layout.addWidget(self.reset_transform_button)
        curve_layout.addStretch()

        rendering_divider_top = MDivider()
        text_render_layout.addWidget(rendering_divider_top)
        text_render_layout.addLayout(font_settings_layout)
        text_render_layout.addLayout(font_metrics_layout)
        text_render_layout.addLayout(main_text_settings_layout)
        text_render_layout.addLayout(outline_settings_layout)
        text_render_layout.addLayout(shadow_settings_layout)
        text_render_layout.addLayout(effects_settings_layout)
        text_render_layout.addLayout(curve_layout)

        self.pan_button = self.create_tool_button(svg="pan_tool.svg", checkable=True)
        self.pan_button.setToolTip(self.tr("Pan Image"))
        self.pan_button.clicked.connect(self.toggle_pan_tool)
        self.tool_buttons["pan"] = self.pan_button

        self.set_all_button = MPushButton(self.tr("Set for all"))
        self.set_all_button.setToolTip(
            self.tr("Sets the Source and Target Language on the current page for all pages")
        )


        self.box_button = self.create_tool_button(svg="select.svg", checkable=True)
        self.box_button.setToolTip(self.tr("Draw or Select Text Boxes"))
        self.box_button.clicked.connect(self.toggle_box_tool)
        self.tool_buttons["box"] = self.box_button

        self.type_text_button = self.create_tool_button(svg="type-text.svg", checkable=True)
        self.type_text_button.setToolTip(
            self.tr("Type your own text: click on the page to place a text box and start typing")
        )
        self.type_text_button.clicked.connect(self.toggle_type_text_tool)
        self.tool_buttons["type"] = self.type_text_button

        self.delete_button = self.create_tool_button(svg="trash_line.svg", checkable=False)
        self.delete_button.setToolTip(self.tr("Delete Selected Box"))

        self.clear_rectangles_button = self.create_tool_button(svg="clear-outlined.svg")
        self.clear_rectangles_button.setToolTip(self.tr("Remove all the Boxes on the Image"))

        self.draw_blklist_blks = self.create_tool_button(svg="gridicons--create.svg")
        self.draw_blklist_blks.setToolTip(
            self.tr(
                "Draws all the Text Blocks in the existing Text Block List\n"
                "back on the Image (for further editing)"
            )
        )


        self.change_all_blocks_size_dec = self.create_tool_button(svg="minus_line.svg")
        self.change_all_blocks_size_dec.setToolTip(self.tr("Reduce the size of all blocks"))

        self.change_all_blocks_size_diff = MLineEdit()
        self.change_all_blocks_size_diff.setFixedWidth(30)
        self.change_all_blocks_size_diff.setText("3")

        int_validator = QIntValidator()
        self.change_all_blocks_size_diff.setValidator(int_validator)
        self.change_all_blocks_size_diff.setAlignment(QtCore.Qt.AlignCenter)

        self.change_all_blocks_size_inc = self.create_tool_button(svg="add_line.svg")
        self.change_all_blocks_size_inc.setToolTip(self.tr("Increase the size of all blocks"))


        self.brush_button = self.create_tool_button(svg="brush-fill.svg", checkable=True)
        self.brush_button.setToolTip(self.tr("Draw Brush Strokes for Cleaning Image"))
        self.brush_button.clicked.connect(self.toggle_brush_tool)
        self.tool_buttons["brush"] = self.brush_button

        self.eraser_button = self.create_tool_button(svg="eraser_fill.svg", checkable=True)
        self.eraser_button.setToolTip(self.tr("Erase Brush Strokes"))
        self.eraser_button.clicked.connect(self.toggle_eraser_tool)
        self.tool_buttons["eraser"] = self.eraser_button

        self.wand_button = self.create_tool_button(svg="wand.svg", checkable=True)
        self.wand_button.setToolTip(self.tr(
            "Select a whole region with one click — the inside of a bubble, a "
            "panel gutter, a flat area behind a sound effect.\n"
            "Hold Ctrl to take every region of that colour on the page at once."
        ))
        self.wand_button.clicked.connect(self.toggle_wand_tool)
        self.tool_buttons["wand"] = self.wand_button

        self.lasso_button = self.create_tool_button(svg="lasso.svg", checkable=True)
        self.lasso_button.setToolTip(self.tr(
            "Draw around an irregular shape a round brush cannot follow.\n"
            "Drag to trace it freehand, or click corner to corner for straight edges.\n"
            "Double-click or press Enter to close it; Escape to start over."
        ))
        self.lasso_button.clicked.connect(self.toggle_lasso_tool)
        self.tool_buttons["lasso"] = self.lasso_button

        self.clear_brush_strokes_button = self.create_tool_button(svg="clear-outlined.svg")
        self.clear_brush_strokes_button.setToolTip(self.tr("Remove all the brush strokes on the Image"))


        self.brush_eraser_slider = MSlider()
        self.brush_eraser_slider.setMinimum(1)
        self.brush_eraser_slider.setMaximum(100)
        self.brush_eraser_slider.setValue(10)
        self.brush_eraser_slider.setToolTip(self.tr("Brush/Eraser Size Slider"))
        self.brush_eraser_slider.valueChanged.connect(self.set_brush_eraser_size)

        # --- Tool rail: every canvas tool, grouped, down the left edge. ---
        tool_rail = QtWidgets.QWidget()
        tool_rail.setObjectName("toonToolRail")
        rail = QtWidgets.QVBoxLayout(tool_rail)
        rail.setContentsMargins(6, 8, 6, 8)
        rail.setSpacing(4)
        groups = (
            (self.pan_button, self.box_button, self.type_text_button),
            (self.brush_button, self.eraser_button, self.wand_button, self.lasso_button),
            (self.delete_button, self.clear_rectangles_button, self.draw_blklist_blks,
             self.clear_brush_strokes_button),
        )
        for index, group in enumerate(groups):
            if index:
                rail.addWidget(_rail_divider())
            for button in group:
                _rail_button(button)
                rail.addWidget(button, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)
        rail.addStretch(1)
        for button in (self.file_tree_button, self.layers_button, self.webtoon_toggle):
            _rail_button(button)
            rail.addWidget(button, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)
        tool_rail.setFixedWidth(52)

        # --- Options bar above the canvas: the settings of the active tools. ---
        options_bar = QtWidgets.QWidget()
        options_bar.setObjectName("toonOptionsBar")
        options = QtWidgets.QHBoxLayout(options_bar)
        options.setContentsMargins(12, 4, 12, 4)
        options.setSpacing(8)
        box_label = MLabel(self.tr("Box size"))
        box_label.setObjectName("toonOptionLabel")
        options.addWidget(box_label)
        options.addWidget(self.change_all_blocks_size_dec)
        options.addWidget(self.change_all_blocks_size_diff)
        options.addWidget(self.change_all_blocks_size_inc)
        options.addSpacing(18)
        brush_label = MLabel(self.tr("Brush size"))
        brush_label.setObjectName("toonOptionLabel")
        options.addWidget(brush_label)
        self.brush_eraser_slider.setFixedWidth(180)
        options.addWidget(self.brush_eraser_slider)
        options.addStretch(1)
        central_layout.insertWidget(0, options_bar)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        # --- Inspector: Text · Layers · Glossary tabs on the right. ---
        text_tab = QtWidgets.QWidget()
        text_tab_layout = QtWidgets.QVBoxLayout(text_tab)
        text_tab_layout.setContentsMargins(10, 10, 10, 10)
        # Source over target: the inspector is one column wide.
        input_layout.setDirection(QtWidgets.QBoxLayout.Direction.TopToBottom)
        self.s_text_edit.setFixedHeight(84)
        self.t_text_edit.setFixedHeight(84)
        text_tab_layout.addLayout(input_layout)
        set_all_row = QtWidgets.QHBoxLayout()
        set_all_row.addStretch(1)
        set_all_row.addWidget(self.set_all_button)
        text_tab_layout.addLayout(set_all_row)
        text_tab_layout.addLayout(text_render_layout)
        text_tab_layout.addStretch(1)
        text_scroll = QtWidgets.QScrollArea()
        text_scroll.setWidgetResizable(True)
        text_scroll.setWidget(text_tab)
        text_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        text_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        text_scroll.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        self.glossary_peek = GlossaryPeekPanel()
        self.glossary_peek.open_glossary.connect(self.show_glossary_dialog)

        self.inspector_tabs = QtWidgets.QTabWidget()
        self.inspector_tabs.setObjectName("toonInspector")
        self.inspector_tabs.setDocumentMode(True)
        self.inspector_tabs.addTab(text_scroll, self.tr("Text"))
        if self.layers_panel.layout() is not None:
            self.layers_panel.layout().setContentsMargins(10, 8, 10, 8)
        self.inspector_tabs.addTab(self.layers_panel, self.tr("Layers"))
        self.inspector_tabs.addTab(self.glossary_peek, self.tr("Glossary"))
        self.inspector_tabs.currentChanged.connect(self._on_inspector_tab_changed)

        right_widget = self.inspector_tabs

        splitter = QtWidgets.QSplitter()
        splitter.addWidget(left_widget)
        splitter.addWidget(central_widget)
        splitter.addWidget(right_widget)

        right_widget.setMinimumWidth(340)
        left_widget.setMinimumWidth(170)

        splitter.setStretchFactor(0, 20)
        splitter.setStretchFactor(1, 80)
        splitter.setStretchFactor(2, 10)
        splitter.setSizes([210, 890, 340])

        body = QtWidgets.QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(tool_rail)
        body.addWidget(splitter, 1)

        top_bar = QtWidgets.QWidget()
        top_bar.setObjectName("toonTopBar")
        top_bar.setLayout(header_layout)

        self.editor_status_bar = EditorStatusBar()

        content_layout = QtWidgets.QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(top_bar)
        content_layout.addWidget(self.progress_bar)
        content_layout.addLayout(body, 1)
        content_layout.addWidget(self.editor_status_bar)

        content_widget.setLayout(content_layout)

        return content_widget

    def _on_inspector_tab_changed(self, index: int) -> None:
        """Keep the layers button in step with the Layers tab, refresh the Glossary tab."""
        tabs = self.inspector_tabs
        on_layers = tabs.widget(index) is self.layers_panel
        if self.layers_button.isChecked() != on_layers:
            self.layers_button.blockSignals(True)
            self.layers_button.setChecked(on_layers)
            self.layers_button.blockSignals(False)
        if tabs.widget(index) is self.glossary_peek:
            self.glossary_peek.refresh(self)

    def show_layers_tab(self, show: bool) -> None:
        """The layers button: open the Layers tab, or go back to Text."""
        target = self.layers_panel if show else self.inspector_tabs.widget(0)
        self.inspector_tabs.setCurrentWidget(target)
    def create_tool_button(self, text: str = "", svg: str = "", checkable: bool = False):
        if text:
            button = MToolButton().svg(svg).text_beside_icon()
            button.setText(text)
        else:
            button = MToolButton().svg(svg)

        button.setCheckable(True) if checkable else button.setCheckable(False)

        return button


def _rail_button(button) -> None:
    """A tool rail button: 40 px square, 22 px icon (touch-sized, 4 px apart)."""
    button.setFixedSize(40, 40)
    button.setIconSize(QtCore.QSize(22, 22))
    button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)


def _rail_divider() -> QtWidgets.QFrame:
    line = QtWidgets.QFrame()
    line.setObjectName("toonRailDivider")
    line.setFixedSize(24, 1)
    return line
