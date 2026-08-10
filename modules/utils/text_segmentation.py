"""Choosing how the precise text mask for a page gets made.

Two ways exist. The fast one thresholds each detected box on its own; the
precise one runs a text segmentation network over the whole page first and
uses its prediction to referee the thresholding. This module is the single
place that answers "which one, and on what device", so call sites do not each
have to reach into settings for it.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


def model_segmentation_enabled(settings) -> bool:
    if settings is None:
        return False
    getter = getattr(settings, "get_use_text_segmentation_model", None)
    if getter is None:
        return False
    try:
        return bool(getter())
    except Exception:
        return False


def segment_page(image: np.ndarray, settings) -> np.ndarray | None:
    """The page's text-probability map, or None to fall back to thresholding.

    Returns None rather than raising when the model is off, unavailable or
    fails: cleaning with a slightly worse mask beats not cleaning at all.
    """
    if image is None or not model_segmentation_enabled(settings):
        return None

    try:
        from modules.detection.text_seg_onnx import get_segmenter
        from modules.utils.device import resolve_device

        device = resolve_device(settings.is_gpu_enabled(), backend="onnx")
        return get_segmenter(device).segment_cached(image)
    except Exception:
        logger.exception("Text segmentation model unavailable; falling back to thresholding")
        return None


def peek_page_mask(image: np.ndarray, settings) -> np.ndarray | None:
    """The page's prediction if it has already been computed, else None.

    For interactive code paths. Running the model takes seconds, which is fine
    inside a batch job and not fine between a brush stroke and its result — so
    the editor takes the better mask when cleaning has already produced one and
    thresholds otherwise, rather than freezing.
    """
    if image is None or not model_segmentation_enabled(settings):
        return None
    try:
        from modules.detection.text_seg_onnx import peek_cached_mask

        return peek_cached_mask(image)
    except Exception:
        return None
