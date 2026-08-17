"""Geometry for the two decorative text effects: gradient fill and curved text.

Kept free of Qt so it can be tested as plain arithmetic. Nothing here knows
what a glyph looks like — the caller measures the text and applies the result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

#: A curvature of 1.0 bends the baseline through this angle. Half a turn puts
#: the first and last glyph pointing at each other, which is as far as text
#: stays readable; anything beyond starts to overlap itself.
MAX_SWEEP = math.pi


def gradient_line(
    width: float,
    height: float,
    angle_degrees: float,
) -> tuple[float, float, float, float]:
    """Endpoints of a gradient axis across a `width` x `height` rectangle.

    Returned as (x1, y1, x2, y2) in the rectangle's own coordinates, with the
    y axis pointing down as it does on screen. 0 degrees runs left to right, 90
    runs top to bottom.

    The line is long enough that the gradient's first and last colour land
    exactly on the rectangle's corners at any angle — the same rule CSS uses.
    Anchoring instead to the rectangle's width alone would make a diagonal
    gradient finish before it reached the far corner.
    """
    if width <= 0 or height <= 0:
        return (0.0, 0.0, 0.0, 0.0)

    angle = math.radians(angle_degrees)
    dx, dy = math.cos(angle), math.sin(angle)
    # Half the rectangle's shadow on the gradient direction.
    half = (abs(width * dx) + abs(height * dy)) / 2.0
    cx, cy = width / 2.0, height / 2.0
    return (cx - dx * half, cy - dy * half, cx + dx * half, cy + dy * half)


@dataclass(frozen=True)
class GlyphPlacement:
    """Where one glyph sits on the curve.

    `x`/`y` are the midpoint of the glyph's baseline, relative to the midpoint
    of the straight baseline it would have had. `angle` is how far to turn the
    glyph, in degrees clockwise, matching a y-axis that points down.
    """

    x: float
    y: float
    angle: float


def arc_placements(
    advances: Sequence[float],
    curvature: float,
) -> list[GlyphPlacement]:
    """Lay glyphs of the given advance widths along a circular arc.

    Positive curvature arches upward like a rainbow, negative sags into a
    valley, zero is an ordinary straight line. The arc keeps the text's own
    length, so the glyphs neither stretch nor crowd — a bend makes the line
    span less horizontal distance rather than squeezing the letters.
    """
    advances = [float(a) for a in advances]
    if not advances:
        return []

    total = sum(advances)
    # Midpoint of each glyph, measured along the baseline from its centre.
    offsets = []
    running = 0.0
    for advance in advances:
        offsets.append(running + advance / 2.0 - total / 2.0)
        running += advance

    curvature = max(-1.0, min(1.0, float(curvature)))
    if curvature == 0.0 or total <= 0.0:
        return [GlyphPlacement(offset, 0.0, 0.0) for offset in offsets]

    # An arc of the same length as the text, sweeping `curvature` of half a
    # turn. Its centre sits `radius` below the midpoint, so the midpoint stays
    # put and the ends fall away from it.
    radius = total / (curvature * MAX_SWEEP)

    placements = []
    for offset in offsets:
        phi = offset / radius
        placements.append(GlyphPlacement(
            x=radius * math.sin(phi),
            y=radius * (1.0 - math.cos(phi)),
            angle=math.degrees(phi),
        ))
    return placements


def arc_bulge(placements: Sequence[GlyphPlacement], glyph_height: float = 0.0) -> float:
    """How far the arc reaches above and below the straight baseline.

    Used to widen the area the canvas repaints. `glyph_height` covers the
    corners a rotated glyph throws outside its own baseline point, so a nearly
    upright glyph at the end of a steep arc is not clipped.
    """
    if not placements:
        return 0.0
    return max(abs(placement.y) for placement in placements) + max(0.0, glyph_height)
