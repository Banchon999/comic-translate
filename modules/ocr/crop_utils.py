from __future__ import annotations

from typing import Sequence

import numpy as np

from modules.utils.textblock import TextBlock, adjust_text_line_coordinates


def expanded_ocr_line_bounds(
    img: np.ndarray,
    blk: TextBlock,
    line: Sequence,
    expansion_percentage: int = 5,
    min_padding: int = 0,
) -> tuple[int, int, int, int] | None:
    """Return OCR crop bounds for a text line without mutating the block.

    For text inside a speech bubble, the crop uses the bubble's left/right
    edges while keeping the line's vertical span padded.  This gives OCR more
    context on tightly detected webtoon/manga text without changing the block
    geometry used by cleaning, rendering, or the editor.
    """
    arr = np.asarray(line)
    if arr.ndim == 2 and arr.shape[0] >= 4 and arr.shape[1] == 2:
        xs = arr[:4, 0]
        ys = arr[:4, 1]
        x1, y1, x2, y2 = (
            int(round(float(xs.min()))),
            int(round(float(ys.min()))),
            int(round(float(xs.max()))),
            int(round(float(ys.max()))),
        )
    elif arr.size == 4:
        x1, y1, x2, y2 = [int(round(float(v))) for v in arr.reshape(-1)[:4]]
    else:
        return None

    if x2 <= x1 or y2 <= y1:
        return None

    if _can_use_bubble_width(blk):
        bx1, _, bx2, _ = [int(round(float(v))) for v in blk.bubble_xyxy[:4]]
        pad_y = _axis_padding(y2 - y1, expansion_percentage, min_padding)
        x1, x2 = bx1, bx2
        y1 -= pad_y
        y2 += pad_y
    else:
        x1, y1, x2, y2 = _expand_axis_box(
            x1, y1, x2, y2, img, expansion_percentage, min_padding
        )

    return _clamp_box(x1, y1, x2, y2, img)


def expanded_ocr_block_bounds(
    img: np.ndarray,
    blk: TextBlock,
    expansion_percentage: int = 5,
    min_padding: int = 0,
) -> tuple[int, int, int, int] | None:
    """Return OCR crop bounds for a whole block without changing blk.xyxy."""
    if getattr(blk, "xyxy", None) is None:
        return None
    return expanded_ocr_line_bounds(
        img,
        blk,
        blk.xyxy,
        expansion_percentage=expansion_percentage,
        min_padding=min_padding,
    )


def crop_with_bounds(img: np.ndarray, bounds: tuple[int, int, int, int] | None) -> np.ndarray | None:
    if bounds is None:
        return None
    x1, y1, x2, y2 = bounds
    if x2 <= x1 or y2 <= y1:
        return None
    crop = img[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    return crop


def _can_use_bubble_width(blk: TextBlock) -> bool:
    return (
        getattr(blk, "text_class", None) == "text_bubble"
        and getattr(blk, "bubble_xyxy", None) is not None
        and len(blk.bubble_xyxy) >= 4
    )


def _axis_padding(size: int, expansion_percentage: int, min_padding: int) -> int:
    percentage_padding = int(((size * expansion_percentage) / 100) / 2)
    return max(percentage_padding, int(min_padding))


def _expand_axis_box(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    img: np.ndarray,
    expansion_percentage: int,
    min_padding: int,
) -> tuple[int, int, int, int]:
    if img.ndim == 3:
        return adjust_text_line_coordinates(
            [x1, y1, x2, y2],
            expansion_percentage,
            expansion_percentage,
            img,
            min_padding,
        )

    pad_x = _axis_padding(x2 - x1, expansion_percentage, min_padding)
    pad_y = _axis_padding(y2 - y1, expansion_percentage, min_padding)
    return x1 - pad_x, y1 - pad_y, x2 + pad_x, y2 + pad_y


def _clamp_box(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    img: np.ndarray,
) -> tuple[int, int, int, int] | None:
    height, width = img.shape[:2]
    x1 = max(0, min(width, int(x1)))
    x2 = max(0, min(width, int(x2)))
    y1 = max(0, min(height, int(y1)))
    y2 = max(0, min(height, int(y2)))
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2
