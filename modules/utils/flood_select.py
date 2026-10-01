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


#: Colour distance for the balloon tool. A bubble's white is one flat colour
#: plus JPEG noise and a faint gradient; looser than the wand, because the
#: region is bounded by the outline (or the detected box) rather than by the
#: tolerance.
BALLOON_TOLERANCE = 48

#: A seed whose darkest channel is below this is not the inside of a speech
#: bubble (white, or a pale tint: pale yellow bottoms out near 190). Clicking a
#: black caption box or a panel is refused rather than guessed at.
BALLOON_MIN_LIGHTNESS = 180

#: Without a detected box to hold it, a region larger than this share of the
#: page is a leak through a gap in the outline, or a light panel, not a
#: bubble. At 0.4 a screentone panel covering 39% of a page was accepted as
#: one; a bubble bigger than a quarter of the page can still be selected once
#: Detect has bounded it.
BALLOON_MAX_SHARE = 0.25

#: Grow a detected bubble box by this much before bounding the flood: the
#: detector's box is tight to its mask and can shave the anti-aliased rim.
BALLOON_BOUND_PAD = 4


def balloon_select(image: np.ndarray, seed_x: int, seed_y: int, bound=None,
                   tolerance: int = BALLOON_TOLERANCE):
    """One click inside a speech bubble: its interior, lettering included.

    Returns ``(mask, reason)``. `mask` is uint8 0/255 over the whole image, or
    None; `reason` is ``"ok"``, ``"outside"``, ``"not-light"`` or ``"leak"``.

    `bound` (x1, y1, x2, y2) is the bubble box detection found around the
    click, when there is one: the flood is confined to it, so a gap in the
    outline cannot pour the selection into the panel. Without one, a region
    that reaches the page edge or covers more than `BALLOON_MAX_SHARE` of the
    page is refused — selecting the whole page is never what "click the
    bubble" meant. Unlike the wand the region is not grown: the anti-aliased
    rim belongs to the outline, and cleaning must leave the outline alone.
    """
    if image is None or image.size == 0:
        return None, "outside"
    height, width = image.shape[:2]
    seed_x, seed_y = int(seed_x), int(seed_y)
    if not (0 <= seed_x < width and 0 <= seed_y < height):
        return None, "outside"
    rgb = _as_rgb(image)
    if int(np.asarray(rgb[seed_y, seed_x], dtype=np.int32).min()) < BALLOON_MIN_LIGHTNESS:
        return None, "not-light"

    x1, y1, x2, y2 = 0, 0, width, height
    if bound is not None:
        bx1, by1, bx2, by2 = (int(round(v)) for v in bound)
        x1 = max(0, bx1 - BALLOON_BOUND_PAD)
        y1 = max(0, by1 - BALLOON_BOUND_PAD)
        x2 = min(width, bx2 + BALLOON_BOUND_PAD)
        y2 = min(height, by2 + BALLOON_BOUND_PAD)
        if not (x1 <= seed_x < x2 and y1 <= seed_y < y2):
            return None, "outside"

    crop = rgb[y1:y2, x1:x2]
    region = flood_select(crop, seed_x - x1, seed_y - y1, tolerance=tolerance,
                          feather=0, contiguous=True, fill_holes=True)
    if region is None or not region.any():
        return None, "outside"
    selected = region > 0
    if bound is None:
        touches_edge = selected[0, :].any() or selected[-1, :].any() or selected[:, 0].any() or selected[:, -1].any()
        if touches_edge or selected.sum() > BALLOON_MAX_SHARE * width * height:
            return None, "leak"
    mask = np.zeros((height, width), np.uint8)
    mask[y1:y2, x1:x2] = region
    return mask, "ok"


def _box_sum3(values: np.ndarray) -> np.ndarray:
    """Sum over each pixel's 3×3 neighbourhood (zero outside the array)."""
    padded = np.pad(values, [(1, 1), (1, 1)] + [(0, 0)] * (values.ndim - 2))
    height, width = values.shape[:2]
    total = np.zeros_like(values, dtype=np.float64)
    for dy in range(3):
        for dx in range(3):
            total += padded[dy:dy + height, dx:dx + width]
    return total


def fill_alpha(image: np.ndarray, region: np.ndarray) -> np.ndarray:
    """How much of each pixel a fill of `region` should cover, 0..1.

    The region itself is covered fully. The one-pixel ring around it is the
    question: an anti-aliased edge leaves pixels there that are part region
    colour and part whatever lies beyond, and leaving them as they are shows a
    fringe of the old colour round the fill. But on a hard edge that ring *is*
    the neighbour — a bubble's black outline — and covering it eats the line.
    So each ring pixel is covered by how far its colour sits from the colour
    just beyond it, relative to how far it sits from the region's colour:
    about half for a true in-between rim pixel, nothing for a hard edge.
    """
    region = np.asarray(region) > 0
    alpha = region.astype(np.float32)
    if not region.any():
        return alpha
    rgb = _as_rgb(image).astype(np.float32)
    seed = np.median(rgb[region], axis=0)

    kernel = imk.get_structuring_element(imk.MORPH_RECT, (3, 3))
    grown1 = imk.dilate(region.astype(np.uint8) * 255, kernel) > 0
    grown2 = imk.dilate(grown1.astype(np.uint8) * 255, kernel) > 0
    ring = grown1 & ~region
    outer = grown2 & ~grown1
    if not ring.any():
        return alpha

    counts = _box_sum3(outer.astype(np.float64))
    sums = _box_sum3(rgb * outer[..., None])
    beyond = np.where(counts[..., None] > 0, sums / np.maximum(counts, 1.0)[..., None], rgb)

    d_in = np.abs(rgb - seed).max(axis=2)
    d_out = np.abs(rgb - beyond).max(axis=2)
    total = d_in + d_out
    share = np.where(total > 0, d_out / np.maximum(total, 1e-6), 1.0)
    alpha[ring] = np.clip(share[ring], 0.0, 1.0)
    return alpha
