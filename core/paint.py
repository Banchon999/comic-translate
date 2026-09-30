"""Brush maths for the pixel tools: dabs, strokes, sampling (Qt-free).

A stroke is a run of round dabs stamped into a float *coverage* map, 0..1,
the size of the image being painted. Each dab adds ``flow`` of what is still
uncovered (``c += (1 - c) * dab * flow``), so going over the same place builds
up towards full coverage and never past it — which is what makes a low-flow
brush feel like airbrushing rather than like stacking opaque stamps. The
stroke's *opacity* is applied once, at the end, as a ceiling on the whole
coverage map: a 50% stroke stays 50% however many times it crosses itself.

Kept free of Qt so the maths is tested as arrays; the canvas session feeds it
points and turns the coverage into a patch.
"""

from __future__ import annotations

import math

import numpy as np


def dab(radius: float, hardness: float = 1.0) -> np.ndarray:
    """A round brush tip as float alpha, (2r+1) square, 1 at the centre.

    Hardness 1 is a solid disc with a one-pixel anti-aliased rim; hardness 0
    falls off smoothly from the centre to the edge; in between, the solid core
    covers `hardness` of the radius and the rest ramps down. A radius below
    half a pixel is still a one-pixel dab, so a tiny brush always paints.
    """
    radius = max(0.5, float(radius))
    hardness = min(1.0, max(0.0, float(hardness)))
    size = int(math.ceil(radius))
    yy, xx = np.mgrid[-size:size + 1, -size:size + 1].astype(np.float32)
    distance = np.sqrt(xx * xx + yy * yy)
    if hardness >= 1.0:
        # One-pixel ramp across the edge: anti-aliased, but otherwise solid.
        alpha = np.clip(radius + 0.5 - distance, 0.0, 1.0)
    else:
        core = radius * hardness
        span = max(radius - core, 1e-6)
        t = np.clip((distance - core) / span, 0.0, 1.0)
        # Smoothstep down from the core to the rim, then the same one-pixel
        # anti-aliasing at the rim so a soft brush has no hard outer ring.
        alpha = 1.0 - t * t * (3.0 - 2.0 * t)
        alpha *= np.clip(radius + 0.5 - distance, 0.0, 1.0)
    alpha[size, size] = max(alpha[size, size], 1.0)
    return alpha.astype(np.float32)


def stamp(coverage: np.ndarray, x: float, y: float, radius: float,
          hardness: float = 1.0, flow: float = 1.0):
    """Add one dab centred at (x, y) into `coverage` in place.

    Returns the touched rect as (x0, y0, x1, y1), exclusive ends, clipped to
    the map, or None when the dab lies entirely outside it.
    """
    flow = min(1.0, max(0.0, float(flow)))
    if flow <= 0.0:
        return None
    tip = dab(radius, hardness) * flow
    half = tip.shape[0] // 2
    cx, cy = int(round(x)), int(round(y))
    height, width = coverage.shape[:2]
    x0, y0 = cx - half, cy - half
    x1, y1 = x0 + tip.shape[1], y0 + tip.shape[0]
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(width, x1), min(height, y1)
    if cx0 >= cx1 or cy0 >= cy1:
        return None
    region = coverage[cy0:cy1, cx0:cx1]
    piece = tip[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0]
    region += (1.0 - region) * piece
    np.clip(region, 0.0, 1.0, out=region)
    return cx0, cy0, cx1, cy1


def spacing_for(radius: float) -> float:
    """Distance between dabs: 15% of the diameter, at least a pixel. Closer
    wastes time for no visible gain; further shows the dabs as beads."""
    return max(1.0, 0.3 * float(radius))


def dab_positions(start, end, spacing: float, carry: float = 0.0):
    """Dab centres from `start` to `end`, every `spacing` pixels.

    `carry` is how far along the previous segment's last gap the brush already
    was, so dabs stay evenly spaced across the many short segments a mouse
    drag arrives as. Returns (positions, new_carry).
    """
    sx, sy = float(start[0]), float(start[1])
    ex, ey = float(end[0]), float(end[1])
    length = math.hypot(ex - sx, ey - sy)
    spacing = max(1e-3, float(spacing))
    positions = []
    travelled = spacing - carry if carry > 0 else spacing
    while travelled <= length + 1e-9:
        t = travelled / length if length else 0.0
        positions.append((sx + (ex - sx) * t, sy + (ey - sy) * t))
        travelled += spacing
    new_carry = (length - (travelled - spacing)) if positions else carry + length
    return positions, new_carry


def sample(image: np.ndarray, x: float, y: float, size: int = 3):
    """The mean colour of a size×size window centred on (x, y), clipped to
    the image, as an (r, g, b) tuple of ints — or None outside the image."""
    height, width = image.shape[:2]
    cx, cy = int(round(x)), int(round(y))
    if not (0 <= cx < width and 0 <= cy < height):
        return None
    half = max(0, int(size) // 2)
    window = image[max(0, cy - half):cy + half + 1, max(0, cx - half):cx + half + 1]
    if window.ndim == 2:
        value = int(round(float(window.mean())))
        return value, value, value
    mean = window[..., :3].reshape(-1, 3).astype(np.float64).mean(axis=0)
    return tuple(int(round(v)) for v in mean)


def pressure_scale(pressure, enabled: bool) -> float:
    """How much a tablet pressure reading scales size or flow. A mouse (no
    reading) or the option off is full; a reading is clamped so a feather-light
    touch still leaves a mark."""
    if not enabled or pressure is None:
        return 1.0
    return min(1.0, max(0.05, float(pressure)))
