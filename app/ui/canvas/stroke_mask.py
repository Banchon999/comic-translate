"""Rasterise saved canvas strokes into an inpainting mask.

This lives in the Qt layer because its input is Qt: a saved stroke's `path` is
the `QPainterPath` that `drawing_manager` read off a `QGraphicsPathItem`, and
there is no rendering it without a painter. `pipeline/inpainting.py` calls in
here only on the one path where the app has handed it canvas strokes.

Two mask layers, not one, because the two kinds of stroke need different
growth: a hand-drawn brush stroke is dilated to cover the lettering it was
swiped over, while a generated region stroke already traces the shape and is
grown further only to close the seam around it.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen

import imkit as imk

# The brush colour drawing_manager gives a generated region stroke (magic wand,
# lasso, segmentation), as opposed to a freehand brush stroke.
GENERATED_REGION_BRUSH = "#80ff0000"


def qimage_to_np(qimg: QImage) -> np.ndarray:
    """Grayscale QImage to a 2-D uint8 array, dropping the row padding.

    `bytesPerLine` is not `width` — Qt pads each row to a 4-byte boundary — so
    the buffer is reshaped by the stride and then trimmed. Reshaping by width
    instead shears the image.
    """
    if qimg.width() <= 0 or qimg.height() <= 0:
        return np.zeros((max(1, qimg.height()), max(1, qimg.width())), dtype=np.uint8)
    ptr = qimg.constBits()
    arr = np.array(ptr).reshape(qimg.height(), qimg.bytesPerLine())
    return arr[:, :qimg.width()]


def mask_from_saved_strokes(strokes: list[dict], image: np.ndarray):
    """Build a binary inpainting mask from saved strokes, or None if empty."""
    if image is None or not strokes:
        return None
    height, width = image.shape[:2]
    if width <= 0 or height <= 0:
        return None

    human_qimg = QImage(width, height, QImage.Format_Grayscale8)
    gen_qimg = QImage(width, height, QImage.Format_Grayscale8)
    human_qimg.fill(0)
    gen_qimg.fill(0)

    human_painter = QPainter(human_qimg)
    gen_painter = QPainter(gen_qimg)

    human_painter.setPen(QPen(QColor(255, 255, 255), 1, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    gen_painter.setPen(QPen(QColor(255, 255, 255), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    human_painter.setBrush(QBrush(QColor(255, 255, 255)))
    gen_painter.setBrush(QBrush(QColor(255, 255, 255)))

    has_any = False
    for stroke in strokes:
        path = stroke.get('path')
        if path is None:
            continue
        brush_hex = QColor(stroke.get('brush', '#00000000')).name(QColor.HexArgb)
        if brush_hex == GENERATED_REGION_BRUSH:
            gen_painter.drawPath(path)
            has_any = True
            continue

        width_px = max(1, int(stroke.get('width', 25)))
        human_pen = QPen(QColor(255, 255, 255), width_px, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        human_painter.setPen(human_pen)
        human_painter.drawPath(path)
        has_any = True

    human_painter.end()
    gen_painter.end()

    if not has_any:
        return None

    human_mask = qimage_to_np(human_qimg)
    gen_mask = qimage_to_np(gen_qimg)
    kernel = np.ones((5, 5), np.uint8)
    human_mask = imk.dilate(human_mask, kernel, iterations=2)
    gen_mask = imk.dilate(gen_mask, kernel, iterations=3)
    mask = np.where((human_mask > 0) | (gen_mask > 0), 255, 0).astype(np.uint8)
    if np.count_nonzero(mask) == 0:
        return None
    return mask
