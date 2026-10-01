"""The window side of the pixel tools: options, colour, and strokes → patches.

A finished stroke arrives from the viewer as a `PixelEdit` (the page composite
with the stroke blended in, and a mask of what changed). It goes through the
seam every clean already uses — `InpaintingHandler.get_inpainted_patches` cuts
patches and maps them to their pages (webtoon included), `apply_patch_list`
pushes them — inside one undo macro, so one stroke is one undo step.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog

SETTINGS_GROUP = "paint"


class PaintController:
    def __init__(self, main):
        self.main = main

    @property
    def drawing(self):
        return self.main.image_viewer.drawing_manager

    def connect(self) -> None:
        main = self.main
        self._load()
        main.image_viewer.pixel_edit_finished.connect(self.apply_edit)
        main.image_viewer.colour_picked.connect(self.show_colour)
        main.paint_colour_button.clicked.connect(self.choose_colour)
        main.paint_hardness_slider.valueChanged.connect(self._on_hardness)
        main.paint_opacity_spin.valueChanged.connect(self._on_opacity)
        main.paint_pressure_size_check.toggled.connect(self._on_pressure_size)
        main.paint_pressure_flow_check.toggled.connect(self._on_pressure_flow)

    # -- strokes ------------------------------------------------------------
    def apply_edit(self, edit) -> bool:
        """Turn a stroke into patches on its page(s). False when it changed
        nothing that could be kept."""
        inpainting = self.main.pipeline.inpainting
        patches = inpainting.get_inpainted_patches(edit.mask, edit.image, mappings=edit.mappings, denoise=False)
        if not patches:
            return False
        label = (QCoreApplication.translate("PaintController", "Restore")
                 if edit.mode == "restore" else QCoreApplication.translate("PaintController", "Paint"))
        stack = self.main.undo_group.activeStack()
        if stack is not None:
            stack.beginMacro(label)
        try:
            inpainting.apply_patch_list(patches)
        finally:
            if stack is not None:
                stack.endMacro()
        self.main.mark_project_dirty()
        return True

    # -- options ------------------------------------------------------------
    def choose_colour(self) -> None:
        colour = QColorDialog.getColor(self.drawing.paint_colour, self.main,
                                       QCoreApplication.translate("PaintController", "Paint Colour"))
        if colour.isValid():
            self.drawing.paint_colour = QColor(colour)
            self.show_colour(colour)

    def show_colour(self, colour: QColor) -> None:
        button = self.main.paint_colour_button
        # The user's paint colour, not a theme colour: shown as the swatch fill.
        button.setStyleSheet(f"background-color: {QColor(colour).name()}; border-radius: 4px;")
        button.setProperty("selected_color", QColor(colour).name())
        self._save("colour", QColor(colour).name())

    def _on_hardness(self, value: int) -> None:
        self.drawing.paint_hardness = value / 100.0
        self._save("hardness", value)

    def _on_opacity(self, value: int) -> None:
        self.drawing.paint_opacity = value / 100.0
        self._save("opacity", value)

    def _on_pressure_size(self, on: bool) -> None:
        self.drawing.paint_pressure_size = bool(on)
        self._save("pressure_size", bool(on))

    def _on_pressure_flow(self, on: bool) -> None:
        self.drawing.paint_pressure_flow = bool(on)
        self._save("pressure_flow", bool(on))

    # -- persistence ----------------------------------------------------------
    def _save(self, key: str, value) -> None:
        if getattr(self, "_loading", False):
            return
        settings = QSettings("ComicLabs", "ComicTranslate")
        settings.beginGroup(SETTINGS_GROUP)
        settings.setValue(key, value)
        settings.endGroup()

    def _load(self) -> None:
        main, drawing = self.main, self.drawing
        settings = QSettings("ComicLabs", "ComicTranslate")
        settings.beginGroup(SETTINGS_GROUP)
        colour = QColor(settings.value("colour", "#ffffff", type=str))
        hardness = settings.value("hardness", 80, type=int)
        opacity = settings.value("opacity", 100, type=int)
        pressure_size = settings.value("pressure_size", True, type=bool)
        pressure_flow = settings.value("pressure_flow", False, type=bool)
        settings.endGroup()
        self._loading = True
        try:
            drawing.paint_colour = colour if colour.isValid() else QColor(255, 255, 255)
            self.show_colour(drawing.paint_colour)
            main.paint_hardness_slider.setValue(hardness)
            main.paint_opacity_spin.setValue(opacity)
            main.paint_pressure_size_check.setChecked(pressure_size)
            main.paint_pressure_flow_check.setChecked(pressure_flow)
            drawing.paint_hardness = hardness / 100.0
            drawing.paint_opacity = opacity / 100.0
            drawing.paint_pressure_size = pressure_size
            drawing.paint_pressure_flow = pressure_flow
        finally:
            self._loading = False
