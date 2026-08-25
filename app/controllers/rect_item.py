from __future__ import annotations

import numpy as np
from typing import TYPE_CHECKING
from PySide6.QtCore import QRectF, QPointF

from app.ui.canvas.rectangle import MoveableRectItem
from app.ui.commands.box import (
    AddRectangleCommand,
    BoxesChangeCommand,
    ReplaceDetectedBlocksCommand,
)

from modules.detection.utils.geometry import do_rectangles_overlap
from modules.utils.textblock import TextBlock

if TYPE_CHECKING:
    from controller import ComicTranslate


class RectItemController:
    def __init__(self, main: ComicTranslate):
        self.main = main

    def connect_rect_item_signals(self, rect_item: MoveableRectItem, force_reconnect: bool = False):
        if getattr(rect_item, "_ct_signals_connected", False) and not force_reconnect:
            return

        if force_reconnect:
            try:
                rect_item.signals.change_undo.disconnect(self.rect_change_undo)
            except (TypeError, RuntimeError):
                pass
            if hasattr(rect_item, "_ct_ocr_slot"):
                try:
                    rect_item.signals.ocr_block.disconnect(rect_item._ct_ocr_slot)
                except (TypeError, RuntimeError):
                    pass
            if hasattr(rect_item, "_ct_translate_slot"):
                try:
                    rect_item.signals.translate_block.disconnect(rect_item._ct_translate_slot)
                except (TypeError, RuntimeError):
                    pass

        if not hasattr(rect_item, "_ct_ocr_slot"):
            rect_item._ct_ocr_slot = lambda: self.main.ocr(True)
        if not hasattr(rect_item, "_ct_translate_slot"):
            rect_item._ct_translate_slot = lambda: self.main.translate_image(True)

        rect_item.signals.change_undo.connect(self.rect_change_undo)
        rect_item.signals.ocr_block.connect(rect_item._ct_ocr_slot)
        rect_item.signals.translate_block.connect(rect_item._ct_translate_slot)
        rect_item._ct_signals_connected = True

    def handle_rectangle_selection(self, rect: QRectF):
        rect = rect.getCoords()
        self.main.curr_tblock = self.find_corresponding_text_block(rect, 0.5)
        if self.main.curr_tblock:
            self.main.s_text_edit.blockSignals(True)
            self.main.t_text_edit.blockSignals(True)
            self.main.s_text_edit.setPlainText(self.main.curr_tblock.text)
            self.main.t_text_edit.setPlainText(self.main.curr_tblock.translation)
            self.main.s_text_edit.blockSignals(False)
            self.main.t_text_edit.blockSignals(False)
        else:
            self.main.s_text_edit.clear()
            self.main.t_text_edit.clear()
            self.main.curr_tblock = None

    def handle_rectangle_creation(self, rect_item: MoveableRectItem):
        self.connect_rect_item_signals(rect_item)
        new_rect = rect_item.mapRectToScene(rect_item.rect())
        x1, y1, w, h = new_rect.getRect()
        x1, y1, w, h = int(x1), int(y1), int(w), int(h)
        new_rect_coords = (x1, y1, x1 + w, y1 + h)

        new_blk = TextBlock(text_bbox=np.array(new_rect_coords))
        self.main.blk_list.append(new_blk)
        command = AddRectangleCommand(self.main, rect_item, new_blk, self.main.blk_list)
        self.main.undo_group.activeStack().push(command)

    def handle_rectangle_deletion(self, rect: QRectF):
        rect_coords = rect.getCoords()
        current_text_block = self.find_corresponding_text_block(rect_coords, 0.5)
        self.main.blk_list.remove(current_text_block)

    def handle_rectangle_change(
            self, 
            old_rect_coords: tuple, 
            new_rect_coords: tuple, 
            new_angle: float, 
            new_tr_origin: QPointF
        ):
        # Find the corresponding TextBlock in blk_list
        for blk in self.main.blk_list:
            if do_rectangles_overlap(blk.xyxy, old_rect_coords, 0.2):
                # Update the TextBlock coordinates
                blk.xyxy[:] = [int(new_rect_coords[0]), 
                               int(new_rect_coords[1]),
                               int(new_rect_coords[2]), 
                               int(new_rect_coords[3])]
                blk.angle = new_angle if new_angle else 0
                blk.tr_origin_point = (new_tr_origin.x(), new_tr_origin.y()) if new_tr_origin else ()
                break

    def rect_change_undo(self, old_state, new_state):
        command = BoxesChangeCommand(self.main.image_viewer, old_state,
                                         new_state, self.main.blk_list)
        self.main.undo_group.activeStack().push(command)
        self.handle_rectangle_change(
            old_state.rect, 
            new_state.rect,
            new_state.rotation,
            new_state.transform_origin
        )


    def find_corresponding_text_block(self, rect: tuple[float], iou_threshold: int = 0.5):
        for blk in self.main.blk_list:
            if do_rectangles_overlap(rect, blk.xyxy, iou_threshold):
                return blk
        return None

    def find_corresponding_rect(self, tblock: TextBlock, iou_threshold: int):
        for rect in self.main.image_viewer.rectangles:
            mp_rect = rect.mapRectToScene(rect.rect())
            x1, y1, w, h = mp_rect.getRect()
            rect_coord = (x1, y1, x1 + w, y1 + h)
            if do_rectangles_overlap(rect_coord, tblock.xyxy, iou_threshold):
                return rect
        return None

    def load_box_coords(self, blk_list: list[TextBlock]):
        """Draw a rectangle over every detected block and select the first.

        Moved here from `pipeline/block_detection.py`: it builds QRectF/QPointF,
        drives the viewer and switches the active tool, none of which the
        pipeline should need Qt to do.
        """
        viewer = self.main.image_viewer

        # Clear rectangles appropriately based on mode
        if self.main.webtoon_mode:
            viewer.clear_rectangles_in_visible_area()
        else:
            viewer.clear_rectangles()

        if not (viewer.hasPhoto() and blk_list):
            return

        for blk in blk_list:
            x1, y1, x2, y2 = blk.xyxy
            rect = QRectF(0, 0, x2 - x1, y2 - y1)
            transform_origin = QPointF(*blk.tr_origin_point) if blk.tr_origin_point else None

            rect_item = viewer.add_rectangle(
                rect, QPointF(x1, y1), blk.angle, transform_origin
            )
            self.connect_rect_item_signals(rect_item)

        # In webtoon mode, use first visible block instead of just first block
        if self.main.webtoon_mode:
            from pipeline.webtoon_utils import get_first_visible_block

            first_block = get_first_visible_block(self.main.blk_list, viewer)
            if first_block is None:
                first_block = self.main.blk_list[0]
        else:
            first_block = self.main.blk_list[0]

        rect = self.find_corresponding_rect(first_block, 0.5)
        viewer.select_rectangle(rect)
        self.main.set_tool('box')

    def push_replace_detected_blocks(self, previous_blocks, new_blocks) -> bool:
        """Push a re-detection onto the undo stack. False if there is no stack."""
        stack = self.main.undo_group.activeStack()
        if stack is None:
            return False
        stack.push(ReplaceDetectedBlocksCommand(self.main, previous_blocks, new_blocks))
        return True
