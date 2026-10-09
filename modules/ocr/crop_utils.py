from __future__ import annotations

from typing import Sequence

import numpy as np

from modules.utils.textblock import TextBlock, adjust_text_line_coordinates


#: A bubble line crop stays this far (fraction of the bubble's width, at least
#: BUBBLE_EDGE_MIN_INSET px) inside the bubble, clear of its outline.
BUBBLE_EDGE_INSET = 0.06
BUBBLE_EDGE_MIN_INSET = 4
#: How far a bubble line crop reaches past the line on each side, as a
#: fraction of the line's height. Measured with PaddleOCR on two real webtoon
#: pages (Korean and English): blank room at the sides makes the recogniser
#: drop a trailing comma or full stop (1.0 lost both Korean commas), none at
#: all misread a letter (어메 for 어머); 0.15-0.3 read every Korean letter
#: right. Two pages is a small sample — a measured choice, not an optimum.
BUBBLE_LINE_REACH = 0.3


def expanded_ocr_line_bounds(
    img: np.ndarray,
    blk: TextBlock,
    line: Sequence,
    expansion_percentage: int = 5,
    min_padding: int = 0,
) -> tuple[int, int, int, int] | None:
    """Return OCR crop bounds for a text line without mutating the block.

    For text inside a speech bubble, the crop reaches sideways toward the
    bubble's edges (up to the line's height, stopping short of the outline)
    while keeping the line's vertical span padded. This gives OCR more context
    on tightly detected webtoon/manga text without changing the block geometry
    used by cleaning, rendering, or the editor.
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
        # Room on each side, but not the bubble's own outline: widened to the
        # bubble's full width, every line crop carried the curved edge at both
        # ends, and the recogniser read it as "[" — on a real page
        # "안녕하세요, 공녀님!" came back with "[공녀님 [이너!]" appended.
        # Never narrower than the line itself.
        reach = max(int((y2 - y1) * BUBBLE_LINE_REACH), pad_y)
        inset = max(BUBBLE_EDGE_MIN_INSET, int((bx2 - bx1) * BUBBLE_EDGE_INSET))
        x1 = min(x1, max(bx1 + inset, x1 - reach))
        x2 = max(x2, min(bx2 - inset, x2 + reach))
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


def llm_ocr_bounds(
    img: np.ndarray,
    blk: TextBlock,
    blk_list: Sequence[TextBlock],
    expansion_percentage: int = 5,
    min_padding: int = 0,
) -> tuple[int, int, int, int] | None:
    """The crop a vision model reads for one block.

    The whole speech bubble when the block has it to itself — the model reads
    a bubble better than a tight box. But when another block sits in the same
    bubble, each block gets its own box instead: sent the bubble, both blocks
    came back from a real model holding the bubble's entire text, so the line
    would have been translated and rendered twice.
    """
    bubble = getattr(blk, "bubble_xyxy", None)
    if bubble is not None and len(bubble) >= 4 and not _shares_bubble(blk, blk_list):
        x1, y1, x2, y2 = (int(round(float(v))) for v in bubble[:4])
    elif getattr(blk, "xyxy", None) is not None:
        x1, y1, x2, y2 = adjust_text_line_coordinates(
            blk.xyxy, expansion_percentage, expansion_percentage, img, min_padding
        )
    else:
        return None
    return _clamp_box(x1, y1, x2, y2, img)


def _shares_bubble(blk: TextBlock, blk_list: Sequence[TextBlock]) -> bool:
    """True when another block's centre lies inside blk's bubble."""
    bx1, by1, bx2, by2 = (float(v) for v in blk.bubble_xyxy[:4])
    for other in blk_list:
        if other is blk or getattr(other, "xyxy", None) is None:
            continue
        ox1, oy1, ox2, oy2 = (float(v) for v in other.xyxy[:4])
        cx, cy = (ox1 + ox2) / 2, (oy1 + oy2) / 2
        if bx1 <= cx <= bx2 and by1 <= cy <= by2:
            return True
    return False


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
