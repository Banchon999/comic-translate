"""Selection mask maths: grow, shrink, smooth, feather (Qt-free).

The canvas keeps a selection as a path in scene coordinates, because a path
survives webtoon scrolling and zoom where a pixel array tied to one image space
would not. Operations that are naturally about pixels — growing an outline by
a few pixels, softening its edge — rasterise the path, run here, and turn the
result back into a path. Keeping them free of Qt means they can be tested as
plain array maths.

Masks are 2-D uint8 arrays, 0 or 255. A feathered selection is a float alpha in
0..1, used by whatever consumes the selection (cleaning, painting) to blend its
result into the untouched pixels around it.
"""

from __future__ import annotations

import numpy as np

import imkit as imk


def _binary(mask: np.ndarray) -> np.ndarray:
    return ((np.asarray(mask) > 0).astype(np.uint8)) * 255


def _disc(radius: int) -> np.ndarray:
    size = 2 * int(radius) + 1
    return imk.get_structuring_element(imk.MORPH_ELLIPSE, (size, size))


def _padded(mask: np.ndarray, pad: int) -> np.ndarray:
    """Morphology near the array edge treats outside as empty, which makes a
    shrink eat inwards from the edge of the crop. Pad by the radius so only the
    real outline moves; callers crop back."""
    return np.pad(_binary(mask), pad, mode="constant")


def grow(mask: np.ndarray, px: int) -> np.ndarray:
    """Every pixel within `px` of the selection joins it."""
    px = int(px)
    if px <= 0:
        return _binary(mask)
    out = imk.dilate(_padded(mask, px), _disc(px))
    return out[px:-px, px:-px]


def shrink(mask: np.ndarray, px: int) -> np.ndarray:
    """Every pixel within `px` of the outside leaves the selection.

    Only the selection's own outline moves: the crop edge is not an outline, so
    the mask is padded with its edge values rather than with empty space.
    """
    px = int(px)
    if px <= 0:
        return _binary(mask)
    padded = np.pad(_binary(mask), px, mode="edge")
    out = imk.erode(padded, _disc(px))
    return out[px:-px, px:-px]


def smooth(mask: np.ndarray, px: int) -> np.ndarray:
    """Round off jagged edges: an opening (drops spurs and specks thinner than
    the radius) followed by a closing (fills notches and pinholes)."""
    px = int(px)
    if px <= 0:
        return _binary(mask)
    return _close(_open(mask, px), px)


def _open(mask: np.ndarray, px: int) -> np.ndarray:
    return grow(shrink(mask, px), px)


def _close(mask: np.ndarray, px: int) -> np.ndarray:
    # Closing must not be clipped by the crop either: pad empty so growth past
    # the edge is kept long enough to shrink back.
    padded = np.pad(_binary(mask), px, mode="constant")
    closed = shrink(grow(padded, px), px)
    return closed[px:-px, px:-px]


def invert(mask: np.ndarray) -> np.ndarray:
    return 255 - _binary(mask)


def _gaussian_1d(radius: int) -> np.ndarray:
    """A normalised Gaussian truncated at `radius` (two standard deviations).

    Truncating and renormalising is what makes the ramp end *exactly*: a pixel
    further than `radius` inside the edge sees only selected pixels and comes
    out 1.0, one further outside sees none and comes out 0.0. An untruncated
    blur leaves the middle of a selection at 0.98, so "clean the selection"
    would leave 2% of the old lettering everywhere.
    """
    sigma = max(radius / 2.0, 1e-6)
    x = np.arange(-radius, radius + 1, dtype=np.float32)
    kernel = np.exp(-(x * x) / (2.0 * sigma * sigma))
    return kernel / kernel.sum()


def _convolve_axis(values: np.ndarray, kernel: np.ndarray, axis: int) -> np.ndarray:
    radius = kernel.size // 2
    pad = [(0, 0), (0, 0)]
    pad[axis] = (radius, radius)
    padded = np.pad(values, pad, mode="constant")
    windows = np.lib.stride_tricks.sliding_window_view(padded, kernel.size, axis=axis)
    return windows @ kernel


def feather_alpha(mask: np.ndarray, px: float) -> np.ndarray:
    """A soft-edged alpha, 0..1, centred on the outline.

    The ramp is symmetric about the edge and `px` wide on each side — the same
    place an image editor puts it. Zero feather is the hard mask as 0.0/1.0.
    """
    hard = (_binary(mask) > 0).astype(np.float32)
    radius = int(round(px or 0))
    if radius <= 0:
        return hard
    kernel = _gaussian_1d(radius)
    alpha = _convolve_axis(_convolve_axis(hard, kernel, 0), kernel, 1)
    alpha = np.clip(alpha, 0.0, 1.0).astype(np.float32)
    # Summation leaves the saturated ends a float epsilon off 0 and 1.
    alpha[alpha > 1.0 - 1e-5] = 1.0
    alpha[alpha < 1e-5] = 0.0
    return alpha


def bbox(mask: np.ndarray):
    """(x, y, w, h) of the selected pixels, or None when nothing is selected."""
    ys, xs = np.nonzero(np.asarray(mask) > 0)
    if xs.size == 0:
        return None
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    return x0, y0, x1 - x0 + 1, y1 - y0 + 1


def blend(original: np.ndarray, edited: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """`edited` where the selection is, `original` elsewhere, mixed by alpha.

    Both images are the same shape; alpha is 2-D 0..1. The result keeps the
    original's dtype, so an opaque RGB patch stays an opaque RGB patch.
    """
    a = np.asarray(alpha, dtype=np.float32)
    if original.ndim == 3:
        a = a[..., None]
    mixed = original.astype(np.float32) * (1.0 - a) + edited.astype(np.float32) * a
    return np.clip(np.rint(mixed), 0, 255).astype(original.dtype)


#: Colour distance (per channel) under which a pixel counts as the selection's
#: background colour. Loose enough for JPEG noise on a white bubble.
BACKGROUND_TOLERANCE = 32

#: Share of the selection that must be background for it to count as a flat
#: area with things on it (a bubble with lettering), rather than artwork.
FLAT_SHARE = 0.6

#: With the background this even (largest per-channel standard deviation),
#: the lettering is simply painted over in the background colour — no model
#: involved. How much lettering there is does not matter: dense text can
#: cover a third of a bubble.
SOLID_SPREAD = 8.0

#: How far the lettering mask is grown: anti-aliased glyph edges and JPEG
#: ringing sit a pixel or two outside the glyph itself.
LETTERING_GROW = 2

#: The background is read from this band just inside the selection's edge —
#: what surrounds whatever sits on it — not from the whole selection. Wide
#: enough that the wand's 2 px of outline sliver is a minority of it.
RING_WIDTH = 6


def _enclosed(foreign: np.ndarray, selected: np.ndarray) -> np.ndarray:
    """The parts of `foreign` that sit *on* the selection, not across its edge.

    The magic wand grows its region a couple of pixels to cover the
    anti-aliased edge, so a bubble selection includes a sliver of the bubble's
    own outline. That sliver differs from the white as much as lettering does,
    and painting it over thins the outline. Lettering is surrounded by
    background; the outline runs off the edge of the selection. So components
    touching the selection's boundary are left alone. The edge of the image is
    not a boundary — the selection continues past it.
    """
    if not foreign.any():
        return foreign
    inner = shrink(selected.astype(np.uint8) * 255, 1) > 0
    boundary = selected & ~inner
    count, labels = imk.connected_components(foreign.astype(np.uint8), connectivity=8)
    touching = np.unique(labels[boundary & foreign])
    touching = touching[touching != 0]
    if touching.size == 0:
        return foreign
    return foreign & ~np.isin(labels, touching)


def plan_clean(image: np.ndarray, alpha: np.ndarray) -> dict:
    """Decide what cleaning a selection should actually touch.

    A selection is usually a region *with things on it* — the magic wand fills
    a bubble's lettering holes on purpose, so clicking a bubble selects all of
    its white along with the words. Inpainting all of that makes the model
    invent the whole bubble from its outline, which comes out as a grey smear.
    So when most of the selection is one colour, only the pixels that differ
    from it are cleaned: painted in that colour when the background is truly
    even, handed to the inpainter otherwise. A selection over varied artwork
    has no background to keep and is inpainted whole.

    Returns ``{"mode": "solid" | "inpaint" | "none", "mask": uint8 0/255,
    "colour": (r, g, b) or None}``. The mask stays inside the selection.
    """
    alpha = np.asarray(alpha, dtype=np.float32)
    selected = alpha > 0
    core = alpha >= 0.5
    rgb = image[..., :3] if image.ndim == 3 else np.stack([image] * 3, axis=-1)
    none = {"mode": "none", "mask": np.zeros(selected.shape, np.uint8), "colour": None}
    if not selected.any():
        return none
    whole = {"mode": "inpaint", "mask": selected.astype(np.uint8) * 255, "colour": None}
    region = core if core.any() else selected
    if int(region.sum()) < 16:
        return whole

    # The background is what lies just inside the selection's edge. Taking
    # the median of the whole selection instead picked the *lettering* as the
    # background whenever it covered most of a tight marquee (bold SFX), and
    # then cleaned nothing. The page edge is not an edge of the selection:
    # shrink pads with edge values.
    inner = shrink(region.astype(np.uint8) * 255, RING_WIDTH) > 0
    ring = region & ~inner
    if int(ring.sum()) < 16:
        ring = region
    background = np.median(rgb[ring].astype(np.float32), axis=0)
    distance = np.abs(rgb.astype(np.float32) - background).max(axis=2)
    near = distance <= BACKGROUND_TOLERANCE
    share = float(near[ring].mean())
    if share < FLAT_SHARE:
        return whole

    lettering = _enclosed(selected & ~near, selected)
    if not lettering.any():
        return none
    grown = grow(lettering.astype(np.uint8) * 255, LETTERING_GROW) > 0
    mask = (grown & selected).astype(np.uint8) * 255
    colour = tuple(int(round(v)) for v in background)
    spread = float(rgb[selected & near].astype(np.float32).std(axis=0).max())
    if spread <= SOLID_SPREAD:
        return {"mode": "solid", "mask": mask, "colour": colour}
    return {"mode": "inpaint", "mask": mask, "colour": colour}
