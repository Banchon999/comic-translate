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
        # Through a lambda: clicked(bool) would land in detect_if_needed.
        main.clean_balloons_button.clicked.connect(lambda: self.clean_white_balloons())

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

    # -- white balloons ---------------------------------------------------------
    def clean_white_balloons(self, detect_if_needed: bool = True) -> None:
        """Paint over the lettering of every white speech bubble on the page
        (the loaded slice, in webtoon mode) in one undo step. Runs Detect first
        when the page has no detected bubbles yet. No model cleans anything:
        a bubble that is not white and flat is skipped and counted."""
        main = self.main
        viewer = main.image_viewer
        if not viewer.hasPhoto():
            return
        if self.drawing.pixel_busy:
            self.say_busy()
            return
        if not self._bubble_blocks():
            if detect_if_needed:
                self._detect_then_clean()
            else:
                self._say_no_bubbles()
            return
        image = viewer.get_image_array(include_patches=True)
        if image is None:
            return
        image = image[..., :3].copy()
        mappings = None
        if viewer.webtoon_mode:
            _, mappings = viewer.get_visible_area_image()
            if not mappings:
                return
            blocks = self._visible_bubble_blocks(mappings)
        else:
            blocks = [blk.deep_copy() for blk in self._bubble_blocks()]
        if not blocks:
            self._say_no_bubbles()
            return
        inpainting = main.pipeline.inpainting
        settings = main.settings_page
        drawing = self.drawing
        drawing.pixel_busy = True
        main.loading.setVisible(True)
        main.disable_hbutton_group()

        def run():
            from modules.utils.text_segmentation import segment_page

            return inpainting.clean_white_balloons(
                image, blocks, mappings, page_text_mask=segment_page(image, settings)
            )

        def done(result):
            patches, cleaned, skipped = result
            self._push(patches, "balloons")
            self._report_balloons(cleaned, skipped)

        def finished():
            drawing.pixel_busy = False
            main.on_manual_finished()

        main.run_threaded(run, done, main.default_error_handler, finished)

    def _bubble_blocks(self) -> list:
        return [
            blk for blk in (getattr(self.main, "blk_list", None) or [])
            if getattr(blk, "text_class", None) == "text_bubble" and getattr(blk, "bubble_xyxy", None) is not None
        ]

    def _visible_bubble_blocks(self, mappings) -> list:
        """The page's bubble blocks in the loaded slice's coordinates, as
        copies. The conversion edits blocks in place, so every block it
        touched is restored before returning, even if it raised halfway."""
        from pipeline.webtoon_utils import filter_and_convert_visible_blocks, restore_original_block_coordinates

        main = self.main
        try:
            converted = filter_and_convert_visible_blocks(main, main.pipeline, mappings)
            return [
                blk.deep_copy() for blk in converted
                if getattr(blk, "text_class", None) == "text_bubble" and getattr(blk, "bubble_xyxy", None) is not None
            ]
        finally:
            touched = [blk for blk in (getattr(main, "blk_list", None) or []) if hasattr(blk, "_original_xyxy")]
            if touched:
                restore_original_block_coordinates(touched)

    def _detect_then_clean(self) -> None:
        main = self.main
        failed = []
        main.loading.setVisible(True)
        main.disable_hbutton_group()

        def on_error(error):
            failed.append(error)
            main.default_error_handler(error)

        def finished():
            main.on_manual_finished()
            if not failed:
                self.clean_white_balloons(detect_if_needed=False)

        main.run_threaded(main.pipeline.detect_blocks, main.pipeline.on_blk_detect_complete, on_error, finished, True)

    def _report_balloons(self, cleaned: int, skipped: list) -> None:
        if not cleaned and not skipped:
            return
        text = QCoreApplication.translate("PaintController", "Cleaned %n white balloon(s).", "", cleaned)
        if skipped:
            # A plain name for the count: lupdate drops a translate() call whose
            # count is an expression such as len(...), and the string never
            # reaches the catalogue.
            refused = len(skipped)
            skipped_text = QCoreApplication.translate(
                "PaintController",
                "Skipped %n that are not plain white — clean those with the AI brush or Clean.",
                "", refused,
            )
            text = f"{text} {skipped_text}"
        (MMessage.warning if skipped and not cleaned else MMessage.info)(text=text, parent=self.main)

    def _say_no_bubbles(self) -> None:
        MMessage.info(
            text=QCoreApplication.translate("PaintController", "No speech bubbles were found on this page."),
            parent=self.main,
        )

    def say_busy(self) -> None:
        MMessage.info(
            text=QCoreApplication.translate(
                "PaintController", "Wait for the cleaning in progress to finish, then try again."
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
            "balloons": QCoreApplication.translate("PaintController", "Clean White Balloons"),
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
