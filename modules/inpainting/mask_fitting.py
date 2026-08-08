"""Mask fitting by border-colour uniformity.

Derived from PanelCleaner (https://github.com/VoxelCubes/PanelCleaner),
Copyright (c) VoxelCubes, licensed under GPL-3.0-or-later. The algorithms in
this module (mask growth steps, border standard deviation, heuristic median
colour, geometric median, best-mask selection) follow `pcleaner/image_ops.py`.

This file, and therefore this project as a whole, is distributed under
GPL-3.0-or-later. See LICENSE and NOTICE.md.

Adaptations from upstream:
- `cv2` structuring elements are obtained through `imkit` instead.
- Upstream grows masks with `scipy.signal.convolve2d` followed by a `> 0`
  threshold. Convolving a non-negative mask with a non-negative kernel is
  positive exactly where binary dilation is, so iterated dilation is used
  here to produce the identical sequence without adding a SciPy dependency.
"""

from collections import Counter

import numpy as np
import imkit as imk


class BlankMaskError(Exception):
    """Raised when a mask has no edge pixels to sample."""


def make_growth_kernel(thickness: int) -> np.ndarray:
    """Structuring element used to grow a mask outline by `thickness` pixels.

    Corners are rounded off for small kernels; larger ones use an ellipse.
    """
    diameter = thickness * 2 + 1

    if diameter <= 5:
        kernel = np.ones((diameter, diameter), dtype=np.uint8)
        kernel[0, 0] = 0
        kernel[0, -1] = 0
        kernel[-1, 0] = 0
        kernel[-1, -1] = 0
        return kernel

    return np.asarray(
        imk.get_structuring_element(imk.MORPH_ELLIPSE, (diameter, diameter))
    ).astype(np.uint8)


def make_mask_steps(mask: np.ndarray, growth_step: int, steps: int, min_thickness: int):
    """Yield progressively grown versions of `mask` with their thickness.

    The first step grows by `min_thickness`, each later step by `growth_step`,
    so a small step size stays accurate without producing masks so thin they
    only clean a glyph's outline.
    """
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)

    grown = imk.dilate(binary, make_growth_kernel(min_thickness))
    yield grown, min_thickness

    kernel = make_growth_kernel(growth_step)
    for index in range(steps - 1):
        grown = imk.dilate(grown, kernel)
        yield grown, min_thickness + (index + 1) * growth_step


def fit_mask_growth(
    image: np.ndarray,
    mask: np.ndarray,
    max_growth: int,
    *,
    step: int = 2,
    off_white_threshold: int = 240,
    allow_color: bool = True,
    max_deviation: float = 15.0,
    min_growth: int = 1,
) -> np.ndarray:
    """Grow `mask` only as far as its border stays on flat background.

    Text sitting in a speech bubble can be grown generously — the border stays on
    the bubble fill the whole way, so the mask safely covers glyph antialiasing.
    Text sitting on artwork cannot: growing it there drags the mask over drawing,
    which the cleaning stage then destroys. Dilating by a fixed amount cannot tell
    those two cases apart, so it has to be tuned for the worse one and is wrong
    for both.

    Each candidate is scored the same way `pick_best_mask` scores its own: the
    spread of the image colours the mask's outline sits on. The largest growth
    whose border is still uniform wins, and when even the tightest candidate sits
    on busy pixels the mask is grown by `min_growth` only — enough for
    antialiasing, not enough to eat the art behind it.
    """
    if max_growth <= 0 or not mask.any():
        return np.where(mask > 0, 255, 0).astype(np.uint8)

    binary = np.where(mask > 0, 255, 0).astype(np.uint8)

    # The ungrown mask is never a candidate. Its outline runs through the glyph's
    # own pixels, which are a single colour, so it always scores as perfectly
    # uniform and would win everywhere — leaving the glyph's antialiased fringe
    # behind. Every candidate is grown by at least `min_growth`.
    floor = max(1, min(min_growth, max_growth))
    grown = imk.dilate(binary, make_growth_kernel(floor))
    tightest = grown
    candidates: list[tuple[int, np.ndarray]] = [(floor, grown)]

    growth = floor
    kernel = make_growth_kernel(step)
    while growth + step <= max_growth:
        grown = imk.dilate(grown, kernel)
        growth += step
        candidates.append((growth, grown))

    best = None
    for growth, candidate in candidates:
        try:
            deviation, _ = border_std_deviation(
                image, candidate, off_white_threshold, allow_color
            )
        except BlankMaskError:
            # Candidate fills the crop; nothing left to sample and growing
            # further cannot help.
            break
        if deviation <= max_deviation:
            best = candidate

    # Every candidate's border landed on busy pixels: keep the mask tight, but
    # still grown enough to swallow the glyph's antialiased fringe.
    return best if best is not None else tightest


def heuristic_median_color(colors: np.ndarray) -> np.ndarray | None:
    """Return the colour occupying more than half the samples, if any.

    A cheap exact answer for flat backgrounds, which is the common case.
    """
    color_counts = Counter(tuple(color) for color in colors)
    total = len(colors)
    for color, count in color_counts.items():
        if count > total / 2:
            return np.array(color)
    return None


def geometric_median(points: np.ndarray, epsilon: float = 1e-5, max_iterations: int = 500) -> np.ndarray:
    """Geometric median of a point set via Weiszfeld's algorithm.

    More robust than a per-channel median for colours, which is what makes
    fills blend on textured-but-uniform backgrounds.
    """
    median = np.mean(points, axis=0)

    for _ in range(max_iterations):
        distances = np.linalg.norm(points - median, axis=1)
        non_zero = distances > 0
        if not np.any(non_zero):
            return median

        inverse = 1 / distances[non_zero]
        weights = inverse / np.sum(inverse)
        new_median = np.sum(weights[:, np.newaxis] * points[non_zero], axis=0)

        if np.linalg.norm(new_median - median) < epsilon:
            return new_median
        median = new_median

    return median


def color_std(colors: np.ndarray) -> float:
    """Standard deviation of the distances from each colour to the mean colour."""
    colors = colors.astype(np.float64)
    mean_color = np.mean(colors, axis=0)
    distances = np.linalg.norm(colors - mean_color, axis=1)
    return float(np.std(distances, ddof=1)) if len(distances) > 1 else 0.0


def _edge_pixels(mask: np.ndarray) -> np.ndarray:
    """Boolean array marking the mask's outline (its own edge pixels)."""
    binary = (mask > 0).astype(np.uint8) * 255
    eroded = imk.erode(binary, np.ones((3, 3), dtype=np.uint8))
    return (binary > 0) & (eroded == 0)


def border_std_deviation(
    image: np.ndarray,
    mask: np.ndarray,
    off_white_threshold: int,
    allow_color: bool,
) -> tuple[float, tuple[int, int, int]]:
    """Uniformity of the image colours along a mask's border.

    Samples the image where the mask has edge pixels and returns the spread of
    those colours plus their median. A low spread means the mask sits entirely
    on flat background, so filling it with that median is safe.

    :raises BlankMaskError: if the mask has no edge pixels.
    """
    edges = _edge_pixels(mask)
    if not edges.any():
        raise BlankMaskError

    if not allow_color and image.ndim == 3:
        # Grayscale comparison: collapse to luminance.
        sample = image[..., :3].mean(axis=2).astype(np.uint8)
        border_pixels = sample[edges]
    else:
        border_pixels = image[edges]

    if len(border_pixels) == 0:
        raise BlankMaskError

    if border_pixels.ndim == 1:
        std = float(np.std(border_pixels))
        gray = int(np.median(border_pixels))
        median_color = (gray, gray, gray)
    else:
        border_pixels = border_pixels.reshape(-1, border_pixels.shape[-1])[:, :3]
        std = color_std(border_pixels)
        median = heuristic_median_color(border_pixels)
        if median is None:
            median = geometric_median(border_pixels.astype(np.float64))
        median_color = tuple(int(channel) for channel in median)

    # Round near-white up to pure white so slightly off-white paper doesn't
    # leave a visible patch.
    if min(median_color) > off_white_threshold:
        median_color = (255, 255, 255)

    return std, median_color


def pick_best_mask(
    image: np.ndarray,
    precise_mask: np.ndarray,
    box_mask: np.ndarray | None,
    masker_conf,
) -> tuple[np.ndarray | None, tuple[int, int, int] | None, float | None]:
    """Choose the mask whose border colours are most uniform.

    Generates progressively grown versions of `precise_mask`, optionally
    considers `box_mask` as well, and ranks them. "Best" is the lowest border
    deviation at the greatest size: a larger mask only wins if it improves on
    the incumbent by `mask_improvement_threshold`.

    Returns (mask, fill colour, deviation). The mask is None when even the best
    candidate exceeds `mask_max_standard_deviation`, meaning the region is not
    flat enough to fill and should be inpainted instead.
    """
    if not precise_mask.any():
        return None, None, None

    mask_steps = list(
        make_mask_steps(
            precise_mask,
            masker_conf.mask_growth_step_pixels,
            masker_conf.mask_growth_steps,
            masker_conf.min_mask_thickness,
        )
    )
    if box_mask is not None:
        # Thickness doesn't apply to the box mask; it isn't grown from the
        # precise mask.
        mask_steps.append((box_mask, None))

    best_mask = None
    best_color = None
    best_deviation = None

    for candidate, _thickness in mask_steps:
        try:
            deviation, color = border_std_deviation(
                image,
                candidate,
                masker_conf.off_white_max_threshold,
                masker_conf.allow_colored_masks,
            )
        except BlankMaskError:
            # Candidate covers the whole crop: nothing left to sample, and
            # growing further cannot help.
            break

        if best_deviation is None or deviation <= best_deviation * (
            1 - masker_conf.mask_improvement_threshold
        ):
            best_deviation = deviation
            best_color = color
            best_mask = candidate

        if masker_conf.mask_selection_fast and deviation == 0:
            break

    if best_deviation is None or best_deviation > masker_conf.mask_max_standard_deviation:
        return None, best_color, best_deviation

    return best_mask, best_color, best_deviation
