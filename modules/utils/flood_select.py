"""Pick the region under the cursor, instead of scribbling over it.

The brush is a round stamp, which is the wrong shape for almost everything it
gets used on: the inside of a speech bubble, a flat panel gutter, a block of
solid tone behind a sound effect. Those are regions, and a region is one click
if you grow it from a seed instead of tracing it.

Kept free of Qt so it can be reasoned about — and tested — as array maths. The
canvas turns the mask this returns into the same kind of stroke the brush makes,
so everything downstream (mask generation, undo, layers) is unchanged.
"""

from __future__ import annotations

import numpy as np

import imkit as imk

#: Default colour distance, per channel, for two pixels to count as the same
#: region. Comic art is mostly flat fills, so this can be tight; it is loose
#: enough to survive JPEG ringing on a screentone edge.
DEFAULT_TOLERANCE = 24

#: How far to grow the result, in pixels. Anti-aliasing leaves a one- or
#: two-pixel ramp between a bubble's white and its black outline, and those
#: in-between pixels match neither. Without this the fill stops just short of
#: the line and leaves a halo when the region is inpainted.
DEFAULT_FEATHER = 2


def _as_rgb(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return np.stack([image] * 3, axis=-1)
    if image.shape[2] == 4:
        return image[:, :, :3]
    return image


def _fill_holes(selected: np.ndarray) -> np.ndarray:
    """Close gaps fully enclosed by the selection.

    Clicking the white inside of a speech bubble selects everything except the
    lettering, because the lettering is a different colour — so the region comes
    back with the words punched out of it. Those holes are precisely what the
    user is about to clean, so they belong in the selection.

    A hole is a piece of the *inverse* that never reaches the edge of the image.
    Anything touching the edge is outside the region, not enclosed by it.

    Only holes smaller than the region enclosing them are filled. Every closed
    shape encloses something, so without that bound clicking a bubble's outline
    would hand back the bubble, and clicking a panel border would hand back the
    panel — a thin line selecting a huge area is not what anyone meant by
    clicking it. Lettering inside a bubble is a fraction of the bubble; a panel
    dwarfs its own border, so the comparison separates the two cleanly with
    nothing to tune.
    """
    inverse = (~selected).astype(np.uint8)
    count, labels = imk.connected_components(inverse, connectivity=4)
    if count <= 1:
        return selected

    border = np.concatenate([labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]])
    outside = set(np.unique(border).tolist())
    region_area = int(selected.sum())

    filled = selected.copy()
    for label in range(1, count):
        if label in outside:
            continue
        hole = labels == label
        if int(hole.sum()) <= region_area:
            filled |= hole
    return filled


def flood_select(
    image: np.ndarray,
    seed_x: int,
    seed_y: int,
    tolerance: int = DEFAULT_TOLERANCE,
    feather: int = DEFAULT_FEATHER,
    contiguous: bool = True,
    fill_holes: bool = True,
) -> np.ndarray | None:
    """The region of `image` that the pixel at (seed_x, seed_y) belongs to.

    Returns a uint8 mask of 0 and 255, or None if the seed is outside the
    image. `contiguous` False selects every pixel of a similar colour anywhere
    on the page rather than only the connected blob — which is how you catch
    every panel gutter at once. `fill_holes` closes anything the region fully
    encloses, which is what turns "the white of the bubble" into "the bubble,
    lettering and all" — see _fill_holes.

    Similarity is Chebyshev distance in RGB: a pixel joins if *no* channel is
    further than `tolerance` from the seed. Euclidean would let a large change
    in one channel hide behind two small ones, and on flat comic colour that
    shows up as a fill leaking through a shaded edge.
    """
    if image is None or image.size == 0:
        return None

    height, width = image.shape[:2]
    seed_x, seed_y = int(seed_x), int(seed_y)
    if not (0 <= seed_x < width and 0 <= seed_y < height):
        return None

    rgb = _as_rgb(image).astype(np.int16)
    seed_colour = rgb[seed_y, seed_x]

    distance = np.abs(rgb - seed_colour).max(axis=2)
    similar = distance <= int(tolerance)

    if contiguous:
        count, labels = imk.connected_components(similar.astype(np.uint8), connectivity=4)
        seed_label = labels[seed_y, seed_x]
        if seed_label == 0:
            # The seed is background in the labelling, which happens only if it
            # failed its own similarity test — it cannot, but guard anyway.
            return None
        selected = labels == seed_label
    else:
        selected = similar

    if fill_holes:
        selected = _fill_holes(selected)

    mask = (selected.astype(np.uint8)) * 255
    if feather > 0:
        size = 2 * int(feather) + 1
        kernel = imk.get_structuring_element(imk.MORPH_ELLIPSE, (size, size))
        mask = imk.dilate(mask, kernel, iterations=1)

    return mask


def mask_to_polygons(mask: np.ndarray, min_area: int = 4) -> list[np.ndarray]:
    """Outlines of a mask, as arrays of (x, y) points.

    Specks below `min_area` are dropped: a tolerance that catches a bubble also
    catches a scatter of single pixels in the screentone next to it, and each
    one would otherwise become its own subpath.
    """
    if mask is None or not np.any(mask):
        return []

    contours, _ = imk.find_contours(mask)
    polygons = []
    for contour in contours:
        points = np.asarray(contour)
        if points.ndim == 3:
            points = points.squeeze(1)
        if points.ndim != 2 or points.shape[0] < 3:
            continue
        if imk.contour_area(contour) < min_area:
            continue
        polygons.append(points)
    return polygons
