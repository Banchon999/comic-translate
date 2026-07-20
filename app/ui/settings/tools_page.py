from PySide6 import QtWidgets
from ..dayu_widgets.label import MLabel
from ..dayu_widgets.check_box import MCheckBox
from ..dayu_widgets.spin_box import MSpinBox
from .utils import create_title_and_combo, set_combo_box_width
from modules.utils.device import is_gpu_available

class ToolsPage(QtWidgets.QWidget):
    def __init__(
        self, 
        translators: list[str], 
        ocr_engines: list[str], 
        detectors: list[str],
        inpainters: list[str], 
        inpaint_strategy: list[str], 
        parent=None
    ):
        super().__init__(parent)
        self.translators = translators
        self.ocr_engines = ocr_engines
        self.detectors = detectors
        self.inpainters = inpainters
        self.inpaint_strategy = inpaint_strategy

        layout = QtWidgets.QVBoxLayout(self)

        translator_widget, self.translator_combo = create_title_and_combo(self.tr("Translator"), self.translators, h4=True)
        set_combo_box_width(self.translator_combo, self.translators)

        ocr_widget, self.ocr_combo = create_title_and_combo(self.tr("Text Recognition"), self.ocr_engines, h4=True)
        set_combo_box_width(self.ocr_combo, self.ocr_engines)

        detector_widget, self.detector_combo = create_title_and_combo(self.tr("Text Detector"), self.detectors, h4=True)
        set_combo_box_width(self.detector_combo, self.detectors)

        detector_conf_layout = QtWidgets.QHBoxLayout()
        detector_conf_label = MLabel(self.tr("Detector Confidence (%):"))
        self.detector_conf_spinbox = MSpinBox().small()
        self.detector_conf_spinbox.setFixedWidth(70)
        self.detector_conf_spinbox.setRange(5, 95)
        self.detector_conf_spinbox.setSingleStep(5)
        self.detector_conf_spinbox.setValue(30)
        self.detector_conf_spinbox.setToolTip(self.tr(
            "Lower values detect more (possibly noisy) text; higher values keep only confident detections."
        ))
        detector_conf_layout.addWidget(detector_conf_label)
        detector_conf_layout.addWidget(self.detector_conf_spinbox)
        detector_conf_layout.addStretch()

        self.stitch_detection_checkbox = MCheckBox(
            self.tr("Stitch neighboring pages during batch detection (long-strip chapters)")
        )
        self.stitch_detection_checkbox.setToolTip(self.tr(
            "For webtoon/strip chapters split into image files: adds part of the previous and next\n"
            "page as context when detecting text, so speech bubbles cut off at page borders are still found.\n"
            "Only pages with the same width are stitched."
        ))

        inpainting_label = MLabel(self.tr("Image Cleaning")).h4()

        mask_padding_layout = QtWidgets.QHBoxLayout()
        mask_padding_label = MLabel(self.tr("Text Mask Expansion (px):"))
        self.mask_padding_spinbox = MSpinBox().small()
        self.mask_padding_spinbox.setFixedWidth(70)
        self.mask_padding_spinbox.setRange(0, 20)
        self.mask_padding_spinbox.setValue(5)
        self.mask_padding_spinbox.setToolTip(self.tr(
            "How far the text-removal mask grows past the detected glyphs."
        ))
        mask_padding_layout.addWidget(mask_padding_label)
        mask_padding_layout.addWidget(self.mask_padding_spinbox)
        mask_padding_layout.addStretch()

        bubble_inset_layout = QtWidgets.QHBoxLayout()
        bubble_inset_label = MLabel(self.tr("Bubble Protection Inset (px):"))
        self.bubble_inset_spinbox = MSpinBox().small()
        self.bubble_inset_spinbox.setFixedWidth(70)
        self.bubble_inset_spinbox.setRange(1, 20)
        self.bubble_inset_spinbox.setValue(5)
        self.bubble_inset_spinbox.setToolTip(self.tr(
            "Keeps the cleaning mask this many pixels away from the speech bubble outline.\n"
            "Increase it if cleaning eats into bubble borders."
        ))
        bubble_inset_layout.addWidget(bubble_inset_label)
        bubble_inset_layout.addWidget(self.bubble_inset_spinbox)
        bubble_inset_layout.addStretch()

        inpainter_widget, self.inpainter_combo = create_title_and_combo(self.tr("Inpainter"), self.inpainters, h4=False)
        set_combo_box_width(self.inpainter_combo, self.inpainters)
        self.inpainter_combo.setCurrentText(self.tr("AOT"))

        inpaint_strategy_widget, self.inpaint_strategy_combo = create_title_and_combo(self.tr("HD Strategy"), self.inpaint_strategy, h4=False)
        set_combo_box_width(self.inpaint_strategy_combo, self.inpaint_strategy)
        self.inpaint_strategy_combo.setCurrentText(self.tr("Resize"))

        # HD Strategy detail widgets
        self.hd_strategy_widgets = QtWidgets.QWidget()
        self.hd_strategy_layout = QtWidgets.QVBoxLayout(self.hd_strategy_widgets)

        # Resize panel
        self.resize_widget = QtWidgets.QWidget()
        about_resize_layout = QtWidgets.QVBoxLayout(self.resize_widget)
        resize_layout = QtWidgets.QHBoxLayout()
        resize_label = MLabel(self.tr("Resize Limit:"))
        about_resize_label = MLabel(self.tr("Resize the longer side of the image to a specific size,\nthen do inpainting on the resized image."))
        self.resize_spinbox = MSpinBox().small()
        self.resize_spinbox.setFixedWidth(70)
        self.resize_spinbox.setMaximum(3000)
        self.resize_spinbox.setValue(960)
        resize_layout.addWidget(resize_label)
        resize_layout.addWidget(self.resize_spinbox)
        resize_layout.addStretch()
        about_resize_layout.addWidget(about_resize_label)
        about_resize_layout.addLayout(resize_layout)
        about_resize_layout.setContentsMargins(5, 5, 5, 5)
        about_resize_layout.addStretch()

        # Crop panel
        self.crop_widget = QtWidgets.QWidget()
        crop_layout = QtWidgets.QVBoxLayout(self.crop_widget)
        about_crop_label = MLabel(self.tr("Crop masking area from the original image to do inpainting."))
        crop_margin_layout = QtWidgets.QHBoxLayout()
        crop_margin_label = MLabel(self.tr("Crop Margin:"))
        self.crop_margin_spinbox = MSpinBox().small()
        self.crop_margin_spinbox.setFixedWidth(70)
        self.crop_margin_spinbox.setMaximum(3000)
        self.crop_margin_spinbox.setValue(512)
        crop_margin_layout.addWidget(crop_margin_label)
        crop_margin_layout.addWidget(self.crop_margin_spinbox)
        crop_margin_layout.addStretch()

        crop_trigger_layout = QtWidgets.QHBoxLayout()
        crop_trigger_label = MLabel(self.tr("Crop Trigger Size:"))
        self.crop_trigger_spinbox = MSpinBox().small()
        self.crop_trigger_spinbox.setFixedWidth(70)
        self.crop_trigger_spinbox.setMaximum(3000)
        self.crop_trigger_spinbox.setValue(512)
        crop_trigger_layout.addWidget(crop_trigger_label)
        crop_trigger_layout.addWidget(self.crop_trigger_spinbox)
        crop_trigger_layout.addStretch()

        crop_layout.addWidget(about_crop_label)
        crop_layout.addLayout(crop_margin_layout)
        crop_layout.addLayout(crop_trigger_layout)
        crop_layout.setContentsMargins(5, 5, 5, 5)

        self.hd_strategy_layout.addWidget(self.resize_widget)
        self.hd_strategy_layout.addWidget(self.crop_widget)

        self.resize_widget.show()
        self.crop_widget.hide()
        self.inpaint_strategy_combo.currentIndexChanged.connect(self._update_hd_strategy_widgets)


        self.use_gpu_checkbox = MCheckBox(self.tr("Use GPU"))
        if not is_gpu_available():
            self.use_gpu_checkbox.setVisible(False)


        layout.addWidget(translator_widget)
        layout.addSpacing(10)
        layout.addWidget(detector_widget)
        layout.addLayout(detector_conf_layout)
        layout.addWidget(self.stitch_detection_checkbox)
        layout.addSpacing(10)
        layout.addWidget(ocr_widget)
        layout.addSpacing(10)
        layout.addWidget(inpainting_label)
        layout.addLayout(mask_padding_layout)
        layout.addLayout(bubble_inset_layout)
        layout.addWidget(inpainter_widget)
        layout.addWidget(inpaint_strategy_widget)
        layout.addWidget(self.hd_strategy_widgets)
        layout.addSpacing(10)
        layout.addWidget(self.use_gpu_checkbox)
        layout.addStretch(1)

        self._update_hd_strategy_widgets(self.inpaint_strategy_combo.currentIndex())

    def _update_hd_strategy_widgets(self, index: int):
        strategy = self.inpaint_strategy_combo.itemText(index)
        self.resize_widget.setVisible(strategy == self.tr("Resize"))
        self.crop_widget.setVisible(strategy == self.tr("Crop"))
        if strategy == self.tr("Original"):
            self.hd_strategy_widgets.setFixedHeight(0)
        else:
            self.hd_strategy_widgets.setFixedHeight(self.hd_strategy_widgets.sizeHint().height())
