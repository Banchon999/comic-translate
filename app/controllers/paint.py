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

from app.ui.dayu_widgets.message import MMessage

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
        main.image_viewer.pixel_tool_busy.connect(self.say_busy)
        main.paint_colour_button.clicked.connect(self.choose_colour)
        main.paint_hardness_slider.valueChanged.connect(self._on_hardness)
        main.paint_opacity_spin.valueChanged.connect(self._on_opacity)
        main.paint_pressure_size_check.toggled.connect(self._on_pressure_size)
        main.paint_pressure_flow_check.toggled.connect(self._on_pressure_flow)
        main.fill_tolerance_spin.valueChanged.connect(self._on_fill_tolerance)
        main.clone_aligned_check.toggled.connect(self._on_clone_aligned)
        main.clone_lock_check.toggled.connect(self._on_clone_lock)
        main.image_viewer.clone_source_missing.connect(self.say_no_source)

    # -- strokes ------------------------------------------------------------
    def apply_edit(self, edit) -> bool:
        """Turn a stroke into patches on its page(s). False when it changed
        nothing that could be kept."""
        inpainting = self.main.pipeline.inpainting
        if edit.mode == "aibrush":
            return self._run_ai_brush(edit)
        patches = inpainting.get_inpainted_patches(edit.mask, edit.image, mappings=edit.mappings, denoise=False)
        return self._push(patches, edit.mode)

    def _run_ai_brush(self, edit) -> bool:
        """Inpaint the stroke on a worker thread; the patches land when done.
        The model is not something to wait for on the GUI thread."""
        main = self.main
        inpainting = main.pipeline.inpainting
        drawing = self.drawing
        drawing.pixel_busy = True
        main.loading.setVisible(True)
        main.disable_hbutton_group()

        def run():
            return inpainting.inpaint_region(edit.image, edit.mask, edit.mappings)

        def finished():
            drawing.pixel_busy = False
            main.on_manual_finished()

        main.run_threaded(run, lambda patches: self._push(patches, edit.mode),
                          main.default_error_handler, finished)
        return True

    def say_busy(self) -> None:
        MMessage.info(
            text=QCoreApplication.translate(
                "PaintController", "Wait for the AI brush to finish before painting again."
            ),
            parent=self.main,
        )

    def say_no_source(self) -> None:
        MMessage.info(
            text=QCoreApplication.translate(
                "PaintController", "Alt+click the page first, to choose where to copy from."
            ),
            parent=self.main,
        )

    def _push(self, patches, mode: str) -> bool:
        if not patches:
            return False
        labels = {
            "restore": QCoreApplication.translate("PaintController", "Restore"),
            "fill": QCoreApplication.translate("PaintController", "Fill"),
            "aibrush": QCoreApplication.translate("PaintController", "AI Inpaint"),
            "clone": QCoreApplication.translate("PaintController", "Clone Stamp"),
            "heal": QCoreApplication.translate("PaintController", "Healing Brush"),
        }
        label = labels.get(mode, QCoreApplication.translate("PaintController", "Paint"))
        inpainting = self.main.pipeline.inpainting
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

    def _on_fill_tolerance(self, value: int) -> None:
        self.drawing.fill_tolerance = int(value)
        self._save("fill_tolerance", int(value))

    def _on_clone_aligned(self, on: bool) -> None:
        self.drawing.clone_aligned = bool(on)
        if not on and not self.drawing.clone_lock:
            # The next stroke starts again from the Alt+clicked point.
            self.drawing.clone_offset = None
        self._save("clone_aligned", bool(on))

    def _on_clone_lock(self, on: bool) -> None:
        self.drawing.clone_lock = bool(on)
        if not on and not self.drawing.clone_aligned:
            self.drawing.clone_offset = None
        self._save("clone_lock", bool(on))

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
        fill_tolerance = settings.value("fill_tolerance", 32, type=int)
        clone_aligned = settings.value("clone_aligned", True, type=bool)
        clone_lock = settings.value("clone_lock", False, type=bool)
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
            main.fill_tolerance_spin.setValue(fill_tolerance)
            drawing.fill_tolerance = fill_tolerance
            main.clone_aligned_check.setChecked(clone_aligned)
            main.clone_lock_check.setChecked(clone_lock)
            drawing.clone_aligned = clone_aligned
            drawing.clone_lock = clone_lock
        finally:
            self._loading = False
