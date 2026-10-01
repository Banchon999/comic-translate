"""What the user can do with a canvas selection.

The selection itself lives on the viewer (``app/ui/canvas/selection.py``);
this is the window side: the options-bar buttons, the shortcuts, and running
Clean Selection on a worker thread without touching the viewer from it.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QInputDialog

from app.ui.canvas.drawing_manager import REGION_MASK, REGION_SELECTION
from app.ui.dayu_widgets.message import MMessage

logger = logging.getLogger(__name__)


class SelectionController:
    def __init__(self, main):
        self.main = main

    @property
    def viewer(self):
        return self.main.image_viewer

    @property
    def selection(self):
        return self.viewer.selection

    # -- wiring ---------------------------------------------------------------
    def connect(self) -> None:
        main = self.main
        self.viewer.selection_changed.connect(self.on_selection_changed)
        main.region_to_selection_button.toggled.connect(self._on_region_output_toggled)
        main.clean_selection_button.clicked.connect(self.clean_selection)
        main.selection_to_mask_button.clicked.connect(self.selection_to_mask)
        main.invert_selection_button.clicked.connect(self.invert)
        main.deselect_button.clicked.connect(self.deselect)
        main.grow_selection_action.triggered.connect(lambda: self.modify("grow"))
        main.shrink_selection_action.triggered.connect(lambda: self.modify("shrink"))
        main.smooth_selection_action.triggered.connect(lambda: self.modify("smooth"))
        main.feather_selection_action.triggered.connect(lambda: self.modify("feather"))
        self.viewer.balloon_refused.connect(self.on_balloon_refused)
        self.on_selection_changed(not self.selection.is_empty())

    def on_selection_changed(self, has_selection: bool) -> None:
        for button in self.main.selection_action_buttons:
            button.setEnabled(bool(has_selection))
        self.main.refresh_options_bar()

    def _on_region_output_toggled(self, to_selection: bool) -> None:
        self.viewer.drawing_manager.region_output = REGION_SELECTION if to_selection else REGION_MASK

    def on_balloon_refused(self, reason: str) -> None:
        messages = {
            "not-light": QCoreApplication.translate("SelectionController",
                "That spot is not the inside of a speech bubble. "
                "Click the bubble's light background, or use the magic wand.",
            ),
            "leak": QCoreApplication.translate("SelectionController",
                "Couldn't find where this bubble ends — its outline may have a gap. "
                "Run Detect first, or use the magic wand or the lasso.",
            ),
        }
        text = messages.get(reason)
        if text:
            MMessage.info(text=text, parent=self.main)

    # Last amount entered per refine operation, offered again next time.
    DEFAULT_AMOUNTS = {"grow": 2, "shrink": 2, "smooth": 2, "feather": 3}

    def modify(self, operation: str, amount: int | None = None) -> bool:
        """Grow, shrink, smooth or feather the selection. Asks for the amount
        unless given one; returns False when nothing changed."""
        if self.selection.is_empty():
            return False
        amounts = self.__dict__.setdefault("_amounts", dict(self.DEFAULT_AMOUNTS))
        if amount is None:
            titles = {
                "grow": QCoreApplication.translate("SelectionController", "Grow Selection"),
                "shrink": QCoreApplication.translate("SelectionController", "Shrink Selection"),
                "smooth": QCoreApplication.translate("SelectionController", "Smooth Selection"),
                "feather": QCoreApplication.translate("SelectionController", "Feather Selection"),
            }
            value, ok = QInputDialog.getInt(
                self.main, titles[operation], QCoreApplication.translate("SelectionController", "Pixels:"),
                amounts[operation], 0 if operation == "feather" else 1, 200,
            )
            if not ok:
                return False
            amount = value
        amounts[operation] = int(amount)
        before = self.selection.snapshot()
        self.selection.refine(operation, int(amount))
        after = self.selection.snapshot()
        return before[1] != after[1] or before[0] != after[0]

    # -- simple actions ---------------------------------------------------------
    def select_all(self) -> None:
        if self.viewer.hasPhoto():
            self.selection.select_all()

    def deselect(self) -> None:
        self.selection.deselect()

    def invert(self) -> None:
        if self.viewer.hasPhoto():
            self.selection.invert()

    def selection_to_mask(self):
        """Hand the selection to the Clean step as a red region stroke, and drop
        it: the marked area now shows as the mask, and leaving the ants on top
        would suggest two things are pending where there is one."""
        if self.selection.is_empty():
            return None
        stack = self.main.undo_group.activeStack()
        if stack is not None:
            stack.beginMacro(QCoreApplication.translate("SelectionController", "Selection to Mask"))
        try:
            item = self.viewer.drawing_manager.add_region_stroke(self.selection.path)
            self.selection.deselect()
        finally:
            if stack is not None:
                stack.endMacro()
        return item

    # -- clean ----------------------------------------------------------------
    def selection_input(self):
        """(image, alpha, mappings) for cleaning the selection, read on the GUI
        thread. None when the selection covers none of the image."""
        viewer = self.viewer
        if not viewer.hasPhoto() or self.selection.is_empty():
            return None
        mappings = None
        top = 0
        if viewer.webtoon_mode:
            image, mappings = viewer.get_visible_area_image()
            if image is None or not mappings:
                return None
            top = int(min(m['scene_y_start'] for m in mappings))
        else:
            image = viewer.get_image_array()
            if image is None:
                return None
        height, width = image.shape[:2]
        alpha = self.selection.alpha(0, top, width, height)
        if not (alpha > 0).any():
            return None
        return image, alpha, mappings

    def clean_selection(self) -> bool:
        """Inpaint the selection. Returns False when there was nothing to do."""
        main = self.main
        payload = self.selection_input()
        if payload is None:
            if not self.selection.is_empty():
                MMessage.info(
                    text=QCoreApplication.translate(
                        "SelectionController",
                        "The selection is outside the part of the page that is loaded. "
                        "Scroll to it and try again.",
                    ),
                    parent=main,
                )
            return False
        image, alpha, mappings = payload
        inpainting = main.pipeline.inpainting

        main.text_ctrl.clear_text_edits()
        main.loading.setVisible(True)
        main.disable_hbutton_group()

        def run():
            return inpainting.inpaint_selection(image, alpha, mappings)

        def done(patches):
            if not patches:
                return
            stack = main.undo_group.activeStack()
            if stack is not None:
                stack.beginMacro(QCoreApplication.translate("SelectionController", "Clean Selection"))
            try:
                inpainting.apply_patch_list(patches)
            finally:
                if stack is not None:
                    stack.endMacro()
            main.mark_project_dirty()

        main.run_threaded(run, done, main.default_error_handler, main.on_manual_finished)
        return True
