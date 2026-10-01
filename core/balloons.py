"""Clean white balloons: decide whether a speech bubble can simply be painted
over, and paint it (Qt-free).

A white (or near-white, flat) bubble needs no inpainter: its lettering is
replaced by the bubble's own colour. The decision is read from the bubble's
*background* — the pixels inside the bubble that are clear of the lettering
mask — never from the whole bubble, whose majority in a small balloon can be
the lettering itself. A bubble is refused when that background is not light
(a coloured or dark bubble), when it is not flat (screentone, a gradient, art
showing through), or when too little of it is visible to judge. Refused
bubbles are left untouched and reported, so the user can clean them another
way; nothing here ever calls a model.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from core.selection import _enclosed, blend, feather_alpha, grow

#: The darkest channel of the background's median must reach this for the
#: bubble to count as white. Cream and pale-grey bubbles pass; mid-grey fails.
MIN_LIGHT = 200
#: The spread (95th − 5th percentile of grey) the background may show and
#: still be flat. JPEG noise on white is a few levels; screentone is dozens.
MAX_SPREAD = 24
#: Background pixels needed to judge at all, absolute and as a share of the
#: bubble inside the crop.
MIN_BACKGROUND = 40
MIN_BACKGROUND_SHARE = 0.10
#: The background is read this far clear of the lettering mask, so the glyphs'
#: anti-aliased rims do not count as texture.
CLEARANCE = 2
#: A pixel further than this (largest channel difference) from the bubble's
#: colour is "odd": outline, tone dot, art.
ODD_TOLERANCE = 32
#: Odd specks enclosed by the background — tone dots, art — may make up at
#: most this share of it. Odd pixels joined to the bubble's edge are its
#: outline and do not count.
MAX_SPECKS = 0.01

OK = "ok"
NOT_WHITE = "not-white"
TEXTURED = "textured"
TOO_LITTLE = "too-little-background"
NO_TEXT = "no-text"


@dataclass
class BalloonPlan:
    colour: tuple[int, int, int] | None
    reason: str
    #: The bubble's outline as it reaches into the bubble mask: never painted.
    protect: np.ndarray | None = None

    @property
    def ok(self) -> bool:
        return self.reason == OK


def plan_balloon(crop: np.ndarray, text_mask: np.ndarray, bubble_mask: np.ndarray) -> BalloonPlan:
    """Whether the bubble in `crop` can be filled flat, and with what colour.

    `text_mask` marks the lettering to remove, `bubble_mask` the bubble's
    inside; both have the crop's height and width.
    """
    text = np.asarray(text_mask) > 0
    bubble = np.asarray(bubble_mask) > 0
    if not (text & bubble).any():
        return BalloonPlan(None, NO_TEXT)
    clear = bubble & ~(grow(text, CLEARANCE) > 0)
    count = int(clear.sum())
    if count < MIN_BACKGROUND or count < MIN_BACKGROUND_SHARE * int(bubble.sum()):
        return BalloonPlan(None, TOO_LITTLE)
    rgb = crop[..., :3] if crop.ndim == 3 else np.repeat(crop[..., None], 3, axis=2)
    colour = tuple(int(round(v)) for v in np.median(rgb[clear].astype(np.float32), axis=0))
    if min(colour) < MIN_LIGHT:
        return BalloonPlan(colour, NOT_WHITE)
    # The bubble mask usually takes in the outline stroke (a segmented bubble
    # includes its line): a dark ring of about the same share of pixels as
    # screentone dots, so no percentile can tell them apart. Where they sit
    # can: the outline runs off the mask's edge, tone dots are enclosed.
    deviation = np.abs(rgb.astype(np.int16) - np.array(colour, np.int16)).max(axis=2)
    odd = bubble & ~text & (deviation > ODD_TOLERANCE)
    specks = _enclosed(odd, bubble)
    outline = odd & ~specks
    judged = clear & ~outline
    if int(judged.sum()) < MIN_BACKGROUND:
        return BalloonPlan(colour, TOO_LITTLE)
    if int((specks & clear).sum()) > MAX_SPECKS * int(judged.sum()):
        return BalloonPlan(colour, TEXTURED)
    grey = rgb[judged].astype(np.float32).mean(axis=1)
    spread = float(np.percentile(grey, 95) - np.percentile(grey, 5))
    if spread > MAX_SPREAD:
        return BalloonPlan(colour, TEXTURED)
    return BalloonPlan(colour, OK, protect=outline)


def fill_balloon(crop: np.ndarray, text_mask: np.ndarray, bubble_mask: np.ndarray,
                 colour: tuple[int, int, int], protect: np.ndarray | None = None) -> np.ndarray:
    """The crop with its lettering painted over in `colour`.

    The mask is grown by a pixel and feathered by one, so the glyphs'
    anti-aliased rims go too, and it never reaches past the bubble's inside
    or onto `protect` (the outline, from `plan_balloon`) — the outline stays
    exactly as drawn.
    """
    bubble = np.asarray(bubble_mask) > 0
    if protect is not None:
        bubble = bubble & ~(np.asarray(protect) > 0)
    region = (grow(np.asarray(text_mask) > 0, 1) > 0) & bubble
    alpha = feather_alpha(region, 1) * bubble
    # Full strength on the lettering itself: the feather only softens the rim.
    alpha[np.asarray(text_mask) > 0] = np.where(bubble[np.asarray(text_mask) > 0], 1.0, 0.0)
    flat = np.empty_like(crop)
    flat[...] = colour if crop.ndim == 3 else int(round(sum(colour) / 3))
    return blend(crop, flat, alpha)
