"""A long webtoon strip folded into columns, for an LLM translator to look at.

Webtoon batch translates a whole strip in one request, so the model sees the
whole conversation and can tell who says what. Its image cannot be the strip
as it is: a 1000 x 25 000 page shrunk to fit a vision model's input is a few
dozen pixels wide. Cut into columns laid side by side it comes out roughly
square, and each column keeps several times the width.

The text the model translates comes from OCR, not from this image; the image
is there for who is speaking — faces, bubble tails, the panel a line sits in.

Pure numpy + Pillow, no Qt.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image

#: Long side of the folded sheet. Vision models resize to about this anyway;
#: sending more only costs upload time.
MAX_SIDE = 2048
#: A strip no taller than this many widths is sent as one column.
SINGLE_COLUMN_RATIO = 2.0
GAP = 16


def column_count(height: int, width: int) -> int:
    """How many columns make a strip of this size come out about square."""
    if width <= 0 or height <= 0 or height <= width * SINGLE_COLUMN_RATIO:
        return 1
    return max(1, int(round(math.sqrt(height / width))))


def strip_sheet(image: np.ndarray, max_side: int = MAX_SIDE) -> tuple[np.ndarray, int]:
    """(sheet, columns): image folded into columns and scaled to max_side.

    Columns run left to right, each read top to bottom. Neighbouring columns
    overlap by a little, so a bubble cut at a column boundary appears whole
    in one of them.
    """
    if image.ndim == 2:
        image = np.repeat(image[..., None], 3, axis=2)
    image = image[..., :3]
    height, width = image.shape[:2]
    columns = column_count(height, width)
    if columns == 1:
        sheet = image
    else:
        step = int(math.ceil(height / columns))
        overlap = min(step // 10, 300)
        col_h = min(height, step + overlap)
        sheet = np.full(
            (col_h, columns * width + (columns - 1) * GAP, 3), 255, dtype=np.uint8
        )
        for c in range(columns):
            top = c * step
            part = image[top:min(height, top + col_h)]
            x = c * (width + GAP)
            sheet[: part.shape[0], x : x + width] = part
    scale = max_side / max(sheet.shape[:2])
    if scale < 1:
        size = (max(1, int(round(sheet.shape[1] * scale))), max(1, int(round(sheet.shape[0] * scale))))
        sheet = np.asarray(Image.fromarray(np.ascontiguousarray(sheet)).resize(size, Image.Resampling.LANCZOS))
    return np.ascontiguousarray(sheet), columns


def sheet_note(columns: int) -> str:
    """How to read the sheet, for the translation prompt ("" for one column)."""
    if columns <= 1:
        return ""
    return (
        f"The image is one long vertical strip cut into {columns} columns: read each column "
        "top to bottom, and the columns from left to right. The text blocks are listed in "
        "that same reading order."
    )
