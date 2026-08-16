"""Clean up the JPEG mess a cleaned area leaves behind.

Derived from PanelCleaner's denoiser (pcleaner/denoiser.py and the
generate_noise_mask half of pcleaner/image_ops.py) by VoxelCubes, GPL-3.0-or-
later. See NOTICE.md.

The structure is the valuable part and is followed closely: denoise a *ring
around the mask*, never the page, and fade that ring out at its edge so there
is no seam where it stops. Inpainting reconstructs a patch from its
surroundings, and on a low-quality JPEG those surroundings carry ringing and
block artefacts around every piece of lettering — which the patch then blends
with, leaving a visible halo exactly where the text used to be.

One deliberate difference: PanelCleaner calls OpenCV's fastNlMeansDenoising.
This project has no OpenCV — imkit is a partial replacement and does not
implement non-local means — so a median filter does the smoothing instead. It
is weaker on fine grain and better on the speckle that JPEG actually produces
around hard edges, and it keeps line art because a median cannot invent a value
that was not already in the window.
"""

from __future__ import annotations

import numpy as np

import imkit as imk

#: How far past the mask the denoised ring reaches, in pixels.
DEFAULT_OUTLINE_SIZE = 6

#: How far the ring's edge is faded out. Without a fade the denoised area ends
#: on a hard line, which is more visible than the noise it removed.
DEFAULT_FADE_RADIUS = 4

#: Median window. Larger removes more and costs more line detail.
DEFAULT_FILTER_SIZE = 3

#: Below this much variation, the area around the mask is a flat fill and has
#: no noise worth removing — so skip it and keep the original pixels exactly.
DEFAULT_MIN_STD = 4.0


def _grown_mask(mask: np.ndarray, outline_size: int) -> np.ndarray:
    if outline_size <= 0:
        return (mask > 0).astype(np.uint8) * 255
    size = 2 * int(outline_size) + 1
    kernel = imk.get_structuring_element(imk.MORPH_ELLIPSE, (size, size))
    return imk.dilate((mask > 0).astype(np.uint8) * 255, kernel, iterations=1)


def _faded_alpha(grown: np.ndarray, fade_radius: int) -> np.ndarray:
    """The ring as a 0..1 weight, soft at its outer edge."""
    if fade_radius <= 0:
        return np.clip(grown.astype(np.float32) / 255.0, 0.0, 1.0)
    # imkit.gaussian_blur takes a radius, not cv2's (ksize, sigma) — the two
    # libraries agree on the name and not on the arguments.
    blurred = imk.gaussian_blur(grown, float(fade_radius))
    return np.clip(blurred.astype(np.float32) / 255.0, 0.0, 1.0)


def _median(image: np.ndarray, size: int) -> np.ndarray:
    import mahotas

    if size <= 1:
        return image.copy()
    window = np.ones((size, size), dtype=bool)
    if image.ndim == 2:
        return mahotas.median_filter(image, window)
    channels = [mahotas.median_filter(image[:, :, i], window) for i in range(image.shape[2])]
    return np.stack(channels, axis=-1)


def _merge_boxes(boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """Collapse overlapping `(top, bottom, left, right)` boxes into their unions."""
    merged: list[list[int]] = []
    for box in boxes:
        top, bottom, left, right = box
        for other in merged:
            if top < other[1] and other[0] < bottom and left < other[3] and other[2] < right:
                other[0] = min(other[0], top)
                other[1] = max(other[1], bottom)
                other[2] = min(other[2], left)
                other[3] = max(other[3], right)
                break
        else:
            merged.append([top, bottom, left, right])
            continue
        # A merge can make the enlarged box touch one it did not before, so
        # keep going until a pass changes nothing.
        while True:
            for i, a in enumerate(merged):
                hit = next(
                    (
                        b for b in merged[i + 1:]
                        if a[0] < b[1] and b[0] < a[1] and a[2] < b[3] and b[2] < a[3]
                    ),
                    None,
                )
                if hit is not None:
                    a[0], a[1] = min(a[0], hit[0]), max(a[1], hit[1])
                    a[2], a[3] = min(a[2], hit[2]), max(a[3], hit[3])
                    merged.remove(hit)
                    break
            else:
                break
    return [(a, b, c, d) for a, b, c, d in merged]


def _mask_windows(mask: np.ndarray, margin: int) -> list[tuple[slice, slice]]:
    """One padded window per region of the mask, clipped to the image.

    Everything this module does is local to the lettering, but a median over a
    whole webtoon strip costs seconds and all but a few thousand of those
    pixels are then multiplied by a zero weight. A single bounding box is not
    enough either — the page mask covers every bubble on the page, so its box
    is very nearly the page. Cropping per region is what makes this cheap
    enough to run on every page.

    The windows are padded and then merged where they overlap, so the lettering
    of one speech bubble ends up as one window rather than one per glyph.
    """
    contours, _ = imk.find_contours((mask > 0).astype(np.uint8) * 255)
    if not contours:
        return []
    height, width = mask.shape[:2]
    boxes = []
    for contour in contours:
        x, y, w, h = imk.bounding_rect(contour)
        boxes.append((
            max(0, y - margin),
            min(height, y + h + margin),
            max(0, x - margin),
            min(width, x + w + margin),
        ))
    return [
        (slice(top, bottom), slice(left, right))
        for top, bottom, left, right in _merge_boxes(boxes)
    ]


def denoise_around_mask(
    image: np.ndarray,
    mask: np.ndarray,
    outline_size: int = DEFAULT_OUTLINE_SIZE,
    fade_radius: int = DEFAULT_FADE_RADIUS,
    filter_size: int = DEFAULT_FILTER_SIZE,
    min_std: float = DEFAULT_MIN_STD,
) -> np.ndarray:
    """Return `image` with the area around `mask` denoised and blended back.

    Pixels outside the grown mask are returned untouched, bit for bit. That
    matters: this runs over a whole page, and anything it does to the artwork
    away from the lettering is damage.
    """
    if image is None or mask is None or image.size == 0:
        return image
    if not np.any(mask):
        return image.copy()

    # Enough room for the dilation, the blur's tail and the median window to
    # see real pixels rather than the crop's edge.
    margin = int(outline_size) + int(fade_radius) * 3 + int(filter_size) + 2
    result = image.copy()

    for window in _mask_windows(mask, margin):
        view = image[window]
        # The mask is cropped, not the region — a neighbouring bubble that
        # reaches into this window is denoised with it rather than clipped in
        # half, which is also why overlapping windows agree where they meet.
        grown = _grown_mask(mask[window], outline_size)
        alpha = _faded_alpha(grown, fade_radius)
        if not np.any(alpha > 0):
            continue

        # Judge the noise on the ring itself — the pixels that will actually be
        # blended — rather than on the whole page, which a dark panel would skew.
        ring = view[grown > 0]
        if ring.size == 0 or float(np.std(ring)) < min_std:
            continue

        smoothed = _median(view, filter_size)

        weight = alpha if view.ndim == 2 else alpha[:, :, None]
        blended = view.astype(np.float32) * (1.0 - weight) + smoothed.astype(np.float32) * weight
        result[window] = np.clip(blended, 0, 255).astype(image.dtype)

    return result
