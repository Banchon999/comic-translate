"""Join webtoon slices into one strip and re-cut it where the page is empty (Qt-free).

Raw webtoon chapters usually arrive as dozens of slices whose edges fall
wherever the host's uploader put them — often straight through a speech
bubble, which then reaches detection and OCR as two half-bubbles on two
pages. This stacks the slices and cuts the strip again near a target height,
choosing rows that carry no artwork: a gutter between panels is a run of rows
in which neighbouring pixels barely differ.

The detector is the one SmartStitch popularised: a row's score is the largest
difference between horizontally adjacent grey pixels across it, so a flat
white, black or tinted gutter scores ~0 and any line art or lettering scores
high. Scores are computed per slice and concatenated — the strip itself is
never materialised just to decide where to cut; pages are assembled from row
ranges of the original slices, one at a time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Iterator, Sequence

import numpy as np
from PIL import Image

JPEG_MAX_SIDE = 65500
WIDTH_MODES = ("min", "max", "none")
FORMATS = ("png", "jpg")
GUTTER_INSET = 40


@dataclass
class StitchSettings:
    target_height: int = 5000   # 0 = do not split; one long image
    sensitivity: int = 90       # 0..100; 100 accepts only perfectly flat rows
    scan_step: int = 5          # rows skipped between candidate cut rows
    margin: int = 0             # columns ignored at each edge (e.g. a border line)
    width_mode: str = "min"     # see normalise_widths
    fmt: str = "png"
    quality: int = 92           # JPEG only

    @property
    def threshold(self) -> float:
        return 255.0 * (1.0 - max(0, min(100, self.sensitivity)) / 100.0)


@dataclass
class StitchPlan:
    slices: list[np.ndarray]
    bounds: list[int]                        # page i is strip rows [bounds[i], bounds[i+1])
    forced: list[int] = field(default_factory=list)  # cut rows where no quiet row was found

    @property
    def total_height(self) -> int:
        return self.bounds[-1]

    @property
    def page_count(self) -> int:
        return len(self.bounds) - 1

    @property
    def page_heights(self) -> list[int]:
        return [b - a for a, b in zip(self.bounds, self.bounds[1:])]

    @property
    def width(self) -> int:
        return int(self.slices[0].shape[1]) if self.slices else 0


def _as_rgb(img: np.ndarray) -> np.ndarray:
    arr = np.asarray(img)
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    elif arr.shape[2] == 4:
        arr = arr[:, :, :3]
    return np.ascontiguousarray(arr, dtype=np.uint8)


def _border_colour(img: np.ndarray) -> np.ndarray:
    edges = np.concatenate([img[:, 0], img[:, -1], img[0], img[-1]])
    return np.median(edges, axis=0).astype(np.uint8)


def normalise_widths(images: Sequence[np.ndarray], mode: str = "min") -> list[np.ndarray]:
    """Make every slice the same width.

    ``min`` scales wider slices down to the narrowest (no invented detail);
    ``max`` scales narrower ones up; ``none`` keeps pixels untouched and pads
    narrower slices, centred, with their own border colour.
    """
    if mode not in WIDTH_MODES:
        raise ValueError(f"unknown width mode {mode!r}")
    slices = [_as_rgb(img) for img in images]
    if not slices:
        return []
    widths = [s.shape[1] for s in slices]
    if len(set(widths)) == 1:
        return slices
    if mode == "none":
        target = max(widths)
        out = []
        for s in slices:
            if s.shape[1] == target:
                out.append(s)
                continue
            canvas = np.empty((s.shape[0], target, 3), np.uint8)
            canvas[:] = _border_colour(s)
            left = (target - s.shape[1]) // 2
            canvas[:, left:left + s.shape[1]] = s
            out.append(canvas)
        return out
    target = min(widths) if mode == "min" else max(widths)
    out = []
    for s in slices:
        h, w = s.shape[:2]
        if w == target:
            out.append(s)
            continue
        new_h = max(1, round(h * target / w))
        out.append(np.asarray(Image.fromarray(s).resize((target, new_h), Image.LANCZOS)))
    return out


def row_busyness(img: np.ndarray, margin: int = 0) -> np.ndarray:
    """One score per row: the largest jump between horizontally adjacent grey pixels."""
    rgb = _as_rgb(img)
    grey = (rgb[:, :, 0].astype(np.int32) * 299
            + rgb[:, :, 1].astype(np.int32) * 587
            + rgb[:, :, 2].astype(np.int32) * 114) // 1000
    w = grey.shape[1]
    if margin > 0 and w - 2 * margin >= 2:
        grey = grey[:, margin:w - margin]
    if grey.shape[1] < 2:
        return np.zeros(grey.shape[0], np.float32)
    return np.abs(np.diff(grey, axis=1)).max(axis=1).astype(np.float32)


def strip_profile(slices: Sequence[np.ndarray], margin: int = 0) -> np.ndarray:
    return np.concatenate([row_busyness(s, margin) for s in slices]) if slices else np.zeros(0, np.float32)


def find_cuts(profile: np.ndarray, settings: StitchSettings) -> tuple[list[int], list[int]]:
    """Cut rows for a strip, and which of them were forced.

    From each cut, aim ``target_height`` further on; scan *up* from there in
    ``scan_step`` rows (never closer than half a page to the previous cut),
    then *down* by at most a quarter page, for a row at or under the
    threshold. The cut goes at the point of that row's quiet run nearest the
    target, at least ``GUTTER_INSET`` rows (or half the run) from either end,
    so a page neither ends flush against a panel nor shrinks for a wide
    blank stretch. With no
    quiet row the cut is made at the target and reported as forced. A tail
    shorter than a quarter page is merged into the page before it.
    """
    total = int(len(profile))
    target = int(settings.target_height)
    if target <= 0 or total <= target:
        return [], []
    step = max(1, int(settings.scan_step))
    threshold = settings.threshold
    quiet = profile <= threshold
    min_page = max(1, target // 2)
    max_shift = max(step, target // 4)

    cuts: list[int] = []
    forced: list[int] = []
    last = 0
    while total - last > target:
        goal = last + target
        low, high = last + min_page, min(total - 1, goal + max_shift)
        found = None
        for row in range(goal, low - 1, -step):
            if quiet[row]:
                found = row
                break
        if found is None:
            for row in range(goal + step, high + 1, step):
                if quiet[row]:
                    found = row
                    break
        if found is None:
            cut = goal
            forced.append(cut)
        else:
            top = found
            while top - 1 >= low and quiet[top - 1]:
                top -= 1
            bottom = found
            while bottom + 1 <= high and quiet[bottom + 1]:
                bottom += 1
            # As close to the target as the quiet run allows, but kept off
            # its edges so the page does not end flush against a panel.
            inset = min((bottom - top) // 2, GUTTER_INSET)
            cut = min(max(goal, top + inset), bottom + 1 - inset)
        cuts.append(cut)
        last = cut
    if cuts and total - cuts[-1] < target // 4:
        tail = cuts.pop()
        if tail in forced:
            forced.remove(tail)
    return cuts, forced


def plan(images: Sequence[np.ndarray], settings: StitchSettings) -> StitchPlan:
    slices = normalise_widths(images, settings.width_mode)
    total = sum(int(s.shape[0]) for s in slices)
    cuts, forced = find_cuts(strip_profile(slices, settings.margin), settings)
    return StitchPlan(slices=slices, bounds=[0, *cuts, total], forced=forced)


def assemble(stitch_plan: StitchPlan) -> Iterator[np.ndarray]:
    """Yield each output page, copied from row ranges of the source slices."""
    offsets = np.cumsum([0] + [int(s.shape[0]) for s in stitch_plan.slices])
    for top, bottom in zip(stitch_plan.bounds, stitch_plan.bounds[1:]):
        pieces = []
        for i, s in enumerate(stitch_plan.slices):
            s_top, s_bottom = int(offsets[i]), int(offsets[i + 1])
            if s_bottom <= top or s_top >= bottom:
                continue
            pieces.append(s[max(top, s_top) - s_top:min(bottom, s_bottom) - s_top])
        yield pieces[0] if len(pieces) == 1 else np.concatenate(pieces, axis=0)


def page_names(count: int, prefix: str, fmt: str) -> list[str]:
    pad = max(3, len(str(count)))
    ext = "jpg" if fmt == "jpg" else "png"
    return [f"{prefix}{i:0{pad}d}.{ext}" for i in range(1, count + 1)]


def write_pages(
    stitch_plan: StitchPlan,
    out_dir: str,
    settings: StitchSettings,
    prefix: str = "",
    progress: Callable[[int, int], None] | None = None,
) -> list[str]:
    """Write the pages as ``001.png``… into ``out_dir``; returns their paths."""
    if settings.fmt not in FORMATS:
        raise ValueError(f"unknown format {settings.fmt!r}")
    if settings.fmt == "jpg" and max(stitch_plan.page_heights, default=0) > JPEG_MAX_SIDE:
        raise ValueError(f"JPEG cannot store an image taller than {JPEG_MAX_SIDE} px; use PNG or split the strip")
    os.makedirs(out_dir, exist_ok=True)
    names = page_names(stitch_plan.page_count, prefix, settings.fmt)
    paths = []
    for i, (name, page) in enumerate(zip(names, assemble(stitch_plan))):
        path = os.path.join(out_dir, name)
        im = Image.fromarray(page)
        if settings.fmt == "jpg":
            im.save(path, quality=int(settings.quality), subsampling=0)
        else:
            im.save(path, optimize=False)
        paths.append(path)
        if progress is not None:
            progress(i + 1, len(names))
    return paths


def slice_edges(stitch_plan: StitchPlan) -> list[int]:
    """Strip rows where one source slice ended and the next began."""
    return [int(v) for v in np.cumsum([int(sl.shape[0]) for sl in stitch_plan.slices])[:-1]]


def preview_pages(stitch_plan: StitchPlan, max_height: int = 1200) -> tuple[list[np.ndarray], float]:
    """Small copies of the output pages, all at one scale, and that scale."""
    tallest = max(stitch_plan.page_heights, default=0)
    if not tallest:
        return [], 1.0
    scale = min(1.0, max_height / tallest)
    pages = []
    for pg in assemble(stitch_plan):
        h, w = pg.shape[:2]
        if scale < 1.0:
            size = (max(1, round(w * scale)), max(1, round(h * scale)))
            pg = np.asarray(Image.fromarray(pg).resize(size, Image.BILINEAR))
        pages.append(np.ascontiguousarray(pg))
    return pages, scale
