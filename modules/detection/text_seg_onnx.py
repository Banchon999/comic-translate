"""Pixel-level text segmentation with comic-text-detector.

Detection gives boxes; cleaning needs to know which *pixels* inside a box are
glyph. Thresholding a crop guesses at that from the crop alone, which fails
whenever the text is not simply the darkest or lightest thing in the box —
over gradients, over screentone, on coloured or outlined lettering.

This runs the segmentation head of comic-text-detector
(https://github.com/dmMaze/comic-text-detector, GPL-3.0), the same network
PanelCleaner uses, over the whole page once and returns its text-probability
map. `modules.inpainting.text_mask_refine` then uses that map as the referee
for a per-box binarisation.

Only the model's `seg` output is used. Its box and text-line outputs are
ignored: this project has its own detector, and feeding those boxes into the
refinement is strictly better than feeding the ones this network found.
"""

from __future__ import annotations

import threading

import numpy as np
from PIL import Image

from modules.utils.device import get_providers
from modules.utils.download import ModelDownloader, ModelID
from modules.utils.onnx import make_session

INPUT_SIZE = 1024


class ComicTextSegmenter:
    """Runs the segmentation head and caches the last page's result.

    Detection, cleaning and rendering all reach for the same page's mask, so
    the last result is kept — the model costs a second or two per page on CPU
    and recomputing it three times over is the whole cost again, twice.
    """

    def __init__(self):
        self.session = None
        self._device = None
        self._lock = threading.Lock()
        self._cache_key = None
        self._cache_value = None

    def initialize(self, device: str = 'cpu') -> None:
        with self._lock:
            if self.session is not None and self._device == device:
                return
            file_path = ModelDownloader.get_file_path(
                ModelID.COMIC_TEXT_SEG_ONNX, 'comic-text-detector.onnx'
            )
            self.session = make_session(file_path, providers=get_providers(device))
            self._device = device
            self._cache_key = None
            self._cache_value = None

    @staticmethod
    def _letterbox(image: np.ndarray) -> tuple[np.ndarray, int, int]:
        """Scale to fit 1024x1024 and pad the bottom and right with black.

        Padding only two sides, rather than centring, is what upstream does —
        it keeps the mapping back to page coordinates a plain division.
        """
        height, width = image.shape[:2]
        ratio = min(INPUT_SIZE / height, INPUT_SIZE / width)
        new_w = int(round(width * ratio))
        new_h = int(round(height * ratio))

        resized = np.asarray(
            Image.fromarray(image).resize((new_w, new_h), Image.Resampling.BILINEAR)
        )
        canvas = np.zeros((INPUT_SIZE, INPUT_SIZE, 3), dtype=np.uint8)
        canvas[:new_h, :new_w] = resized[..., :3]
        return canvas, INPUT_SIZE - new_w, INPUT_SIZE - new_h

    def segment(self, image: np.ndarray) -> np.ndarray:
        """Return a page-sized uint8 text-probability map (0-255)."""
        if self.session is None:
            raise RuntimeError("ComicTextSegmenter.initialize() must be called first")

        if image.ndim == 2:
            image = np.repeat(image[..., None], 3, axis=2)

        canvas, pad_w, pad_h = self._letterbox(image)

        # The network was trained on BGR-ordered arrays; this project carries
        # RGB everywhere, so the channels are reversed on the way in.
        tensor = canvas[..., ::-1].astype(np.float32) / 255.0
        tensor = np.ascontiguousarray(tensor.transpose(2, 0, 1)[np.newaxis, ...])

        input_name = self.session.get_inputs()[0].name
        outputs = self.session.run(['seg'], {input_name: tensor})
        seg = np.clip(outputs[0].squeeze() * 255.0, 0, 255).astype(np.uint8)

        # Drop the padding, then stretch back to the page.
        seg = seg[: INPUT_SIZE - pad_h, : INPUT_SIZE - pad_w]
        height, width = image.shape[:2]
        return np.asarray(
            Image.fromarray(seg).resize((width, height), Image.Resampling.BILINEAR)
        )

    @staticmethod
    def _key(image: np.ndarray) -> tuple:
        return (image.shape, int(image.dtype.num), _quick_hash(image))

    def peek(self, image: np.ndarray) -> np.ndarray | None:
        """The cached mask for this page, without computing one."""
        with self._lock:
            if self._cache_key is not None and self._key(image) == self._cache_key:
                return self._cache_value
        return None

    def segment_cached(self, image: np.ndarray) -> np.ndarray:
        key = self._key(image)
        with self._lock:
            if key == self._cache_key:
                return self._cache_value
        value = self.segment(image)
        with self._lock:
            self._cache_key = key
            self._cache_value = value
        return value


def _quick_hash(image: np.ndarray) -> int:
    """Cheap content fingerprint.

    Hashing every byte of a webtoon strip costs more than it saves, so this
    samples a coarse grid — enough to tell two pages apart, and a collision
    only ever means a stale mask for one page.
    """
    sample = image[:: max(1, image.shape[0] // 64), :: max(1, image.shape[1] // 64)]
    return hash(np.ascontiguousarray(sample).tobytes())


_segmenter: ComicTextSegmenter | None = None
_segmenter_lock = threading.Lock()


def get_segmenter(device: str = 'cpu') -> ComicTextSegmenter:
    """The shared segmenter, downloading and loading the model on first use."""
    global _segmenter
    with _segmenter_lock:
        if _segmenter is None:
            _segmenter = ComicTextSegmenter()
    _segmenter.initialize(device)
    return _segmenter


def peek_cached_mask(image: np.ndarray) -> np.ndarray | None:
    """This page's mask if it is already in hand — never loads or runs a model."""
    with _segmenter_lock:
        segmenter = _segmenter
    return segmenter.peek(image) if segmenter is not None else None
