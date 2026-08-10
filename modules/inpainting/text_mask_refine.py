"""Refine a neural text-probability map into a pixel-accurate glyph mask.

Ported from comic-text-detector (https://github.com/dmMaze/comic-text-detector),
Copyright (c) dmMaze, licensed under GPL-3.0. The algorithms here — top-k
colour candidates, per-channel Otsu candidates, XOR-scored component merging
and hole filling — follow `utils/textmask.py`, which PanelCleaner vendors and
runs as its own text segmentation step.

This file, and therefore this project as a whole, is distributed under
GPL-3.0-or-later. See LICENSE and NOTICE.md.

Why this beats plain thresholding, which is what the rest of this codebase
used to do on its own: Otsu splits a crop into two classes and hopes the
darker one is the text. Over a gradient, over screentone, or on coloured text
that is neither the darkest nor the lightest thing in the box, it is simply
wrong, and there is nothing in the picture to tell it so. Here the network's
probability map is the referee. Several candidate binarisations are generated,
and each connected component is admitted only if adding it moves the mask
*closer* to what the network predicted. A component that thresholding invented
— a chunk of artwork, a bubble edge, a screentone dot — makes the agreement
worse and is dropped.

Adaptations from upstream:
- `cv2` calls are replaced with `imkit`/NumPy equivalents:
  `cv2.connectedComponentsWithStats` becomes `imk.connected_components_with_stats`,
  `cv2.inRange` becomes a NumPy range comparison, and the bitwise ops become
  the identical NumPy ops on uint8 arrays.
- Upstream erodes the *grayscale* probability map and thresholds the result;
  `imk.erode` only does binary morphology. Thresholding first is equivalent —
  erosion is a minimum filter, so min(window) > t holds exactly when every
  pixel in the window exceeds t — so each such pair is reordered instead.
- Per-component XOR bookkeeping is kept as a running delta rather than
  recomputing `bitwise_xor(...).sum()` over the region twice per component.
  Only the pixels a component adds can change the score, and each flips it by
  the same amount, so the sign of the delta is the same decision.
"""

from __future__ import annotations

import numpy as np
import imkit as imk

# Upstream's two refine modes. INPAINT dilates the result by one pixel, which
# is what you want when the mask feeds an inpainter; ANNOTATION leaves it tight.
REFINEMASK_INPAINT = 0
REFINEMASK_ANNOTATION = 1


def _as_binary(mask: np.ndarray) -> np.ndarray:
    return np.where(mask > 0, 255, 0).astype(np.uint8)


def _xor_sum(a: np.ndarray, b: np.ndarray) -> int:
    """Disagreement score between two masks, as upstream measures it.

    The candidates are 0/255 but the prediction is still a probability map
    here, so this is a genuine per-byte XOR rather than a pixel count.
    """
    return int(np.bitwise_xor(a, b).sum())


def get_topk_color(
    color_list: np.ndarray,
    bins: np.ndarray,
    k: int = 3,
    color_var: int = 10,
    bin_tol: float = 0.001,
) -> list:
    """The k most populated grey levels, forced at least `color_var` apart."""
    order = np.argsort(bins * -1)
    color_list, bins = color_list[order], bins[order]
    top_colors = [color_list[0]]
    bin_tol = np.sum(bins) * bin_tol
    for color, count in zip(color_list[1:], bins[1:]):
        if np.abs(np.array(top_colors) - color).min() > color_var:
            top_colors.append(color)
        if len(top_colors) >= k or count < bin_tol:
            break
    return top_colors


def minxor_thresh(threshed: np.ndarray, mask: np.ndarray, dilate: bool = False):
    """Return whichever of a binarisation or its negative agrees with `mask`.

    A threshold has no idea whether text is the dark side or the light side.
    The prediction does, so both polarities are scored and the closer one wins.
    """
    neg_threshed = 255 - threshed
    if dilate:
        element = imk.get_structuring_element(imk.MORPH_RECT, (3, 3))
        neg_threshed = imk.dilate(neg_threshed, element, iterations=1)
        threshed = imk.dilate(threshed, element, iterations=1)

    neg_xor_sum = _xor_sum(neg_threshed, mask)
    xor_sum = _xor_sum(threshed, mask)
    if neg_xor_sum < xor_sum:
        return neg_threshed, neg_xor_sum
    return threshed, xor_sum


def get_otsuthresh_masklist(img: np.ndarray, pred_mask: np.ndarray, per_channel: bool = False) -> list:
    """Otsu candidates, one per colour channel."""
    if img.ndim == 2:
        channels = [img]
    else:
        channels = [img[..., 0], img[..., 1], img[..., 2]]

    mask_list = []
    for channel in channels:
        _, threshed = imk.otsu_threshold(np.ascontiguousarray(channel))
        threshed, xor_sum = minxor_thresh(threshed, pred_mask, dilate=False)
        mask_list.append([threshed, xor_sum])

    mask_list.sort(key=lambda item: item[1])
    return mask_list if per_channel else [mask_list[0]]


def get_topk_masklist(im_grey: np.ndarray, pred_mask: np.ndarray) -> list:
    """Candidates built from the grey levels the prediction actually covers.

    Sampling inside the eroded prediction is what makes this work on coloured
    text: the glyph's own grey level is read out of the picture rather than
    assumed to be an extreme.
    """
    if im_grey.ndim == 3 and im_grey.shape[-1] == 3:
        im_grey = imk.to_gray(im_grey)

    msk = np.ascontiguousarray(pred_mask)
    # Upstream: erode(msk) > 127. Same set of pixels, thresholded first.
    eroded = imk.erode(_as_binary(msk > 127), np.ones((3, 3), np.uint8), iterations=1)
    candidate_grey_px = im_grey[eroded > 0]
    if candidate_grey_px.size == 0:
        return []

    counts, edges = np.histogram(candidate_grey_px, bins=255)
    topk_color = get_topk_color(edges, counts, color_var=10, k=3)

    color_range = 30
    mask_list = []
    for color in topk_color:
        c_top = min(color + color_range, 255)
        c_bottom = c_top - 2 * color_range
        threshed = np.where((im_grey >= c_bottom) & (im_grey <= c_top), 255, 0).astype(np.uint8)
        threshed, xor_sum = minxor_thresh(threshed, msk)
        mask_list.append([threshed, xor_sum])
    return mask_list


def _accept_components_reducing_xor(
    mask_merged: np.ndarray,
    pred_mask: np.ndarray,
    labels: np.ndarray,
    stats: np.ndarray,
    *,
    skip_background: bool,
    area_limit: int | None = None,
    min_box_area: int = 3,
) -> None:
    """Admit each component into `mask_merged` when it improves agreement.

    Mutates `mask_merged` in place. A component is kept only if unioning it in
    lowers the XOR distance to `pred_mask` inside that component's own bounding
    box — which is exactly upstream's rule, since pixels outside the box are
    identical either way and cancel out of the comparison.
    """
    for label_index in range(len(stats)):
        if skip_background and label_index == 0:
            continue

        x, y, w, h, area = (
            stats[label_index][imk.CC_STAT_LEFT],
            stats[label_index][imk.CC_STAT_TOP],
            stats[label_index][imk.CC_STAT_WIDTH],
            stats[label_index][imk.CC_STAT_HEIGHT],
            stats[label_index][imk.CC_STAT_AREA],
        )
        if w * h < min_box_area:
            continue
        if area_limit is not None and area >= area_limit:
            continue

        x1, y1, x2, y2 = x, y, x + w, y + h
        region_pred = pred_mask[y1:y2, x1:x2]
        region_merged = mask_merged[y1:y2, x1:x2]
        component = labels[y1:y2, x1:x2] == label_index

        # Only pixels the component adds can change the comparison.
        added = component & (region_merged == 0)
        if not added.any():
            continue

        # Each added pixel flips agreement one way or the other: towards the
        # prediction where it predicted text, away from it where it did not.
        delta = int(np.count_nonzero(added & (region_pred == 0))) - int(
            np.count_nonzero(added & (region_pred != 0))
        )
        if delta < 0:
            region_merged[component] = 255


def merge_mask_list(
    mask_list: list,
    pred_mask: np.ndarray,
    pred_thresh: int = 30,
    refine_mode: int = REFINEMASK_INPAINT,
) -> np.ndarray:
    """Build one mask out of the candidates, component by component."""
    mask_list = sorted(mask_list, key=lambda item: item[1])

    if pred_thresh > 0:
        # Upstream: erode the probability map, then threshold at 60.
        element = imk.get_structuring_element(imk.MORPH_ELLIPSE, (3, 3))
        pred_mask = imk.erode(_as_binary(pred_mask > 60), element, iterations=1)
    else:
        pred_mask = _as_binary(pred_mask)

    mask_merged = np.zeros_like(pred_mask)
    for candidate_mask, _xor_score in mask_list:
        num_labels, labels, stats, _ = imk.connected_components_with_stats(
            candidate_mask, connectivity=8
        )
        if num_labels <= 1:
            continue
        _accept_components_reducing_xor(
            mask_merged, pred_mask, labels, stats, skip_background=True
        )

    if refine_mode == REFINEMASK_INPAINT:
        mask_merged = imk.dilate(mask_merged, np.ones((3, 3), np.uint8), iterations=1)

    # Fill holes: the inside of an 'o' is background to the thresholder but text
    # to the prediction, so the same agreement test recovers it.
    num_labels, labels, stats, _ = imk.connected_components_with_stats(
        255 - mask_merged, connectivity=8
    )
    if num_labels > 1:
        sorted_area = np.sort(stats[:, imk.CC_STAT_AREA])
        # Everything except the page background itself, which is the largest.
        area_thresh = sorted_area[-2] if len(sorted_area) > 1 else sorted_area[-1]
        _accept_components_reducing_xor(
            mask_merged, pred_mask, labels, stats,
            skip_background=False, area_limit=area_thresh, min_box_area=0,
        )

    return mask_merged


def expand_textwindow(
    img_shape: tuple[int, int],
    xyxy,
    expand_r: int = 16,
) -> tuple[int, int, int, int]:
    """Grow a text box outwards, clamped to the image."""
    height, width = img_shape[:2]
    x1, y1, x2, y2 = [int(v) for v in xyxy[:4]]
    return (
        max(0, x1 - expand_r),
        max(0, y1 - expand_r),
        min(width, x2 + expand_r),
        min(height, y2 + expand_r),
    )


def refine_mask_in_box(
    img: np.ndarray,
    pred_mask: np.ndarray,
    xyxy,
    *,
    expand_r: int = 16,
    refine_mode: int = REFINEMASK_ANNOTATION,
) -> tuple[np.ndarray, tuple[int, int, int, int]] | tuple[None, None]:
    """Refine one text box. Returns the crop mask and the crop bounds."""
    bx1, by1, bx2, by2 = expand_textwindow(img.shape, xyxy, expand_r=expand_r)
    if bx2 <= bx1 or by2 <= by1:
        return None, None

    crop = np.ascontiguousarray(img[by1:by2, bx1:bx2])
    crop_pred = np.ascontiguousarray(pred_mask[by1:by2, bx1:bx2])
    if not crop_pred.any():
        return None, None

    mask_list = get_topk_masklist(crop, crop_pred)
    mask_list += get_otsuthresh_masklist(crop, crop_pred, per_channel=False)
    if not mask_list:
        return None, None

    merged = merge_mask_list(mask_list, crop_pred, refine_mode=refine_mode)
    return merged, (bx1, by1, bx2, by2)


def refine_mask(
    img: np.ndarray,
    pred_mask: np.ndarray,
    boxes,
    *,
    expand_r: int = 16,
    refine_mode: int = REFINEMASK_ANNOTATION,
) -> np.ndarray:
    """Refine every box on a page into one full-page mask."""
    mask_refined = np.zeros(pred_mask.shape[:2], dtype=np.uint8)
    for xyxy in boxes:
        merged, bounds = refine_mask_in_box(
            img, pred_mask, xyxy, expand_r=expand_r, refine_mode=refine_mode
        )
        if merged is None:
            continue
        bx1, by1, bx2, by2 = bounds
        region = mask_refined[by1:by2, bx1:bx2]
        np.bitwise_or(region, merged, out=region)
    return mask_refined
