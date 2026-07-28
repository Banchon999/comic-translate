"""Solid-fill cleaning stage.

Uses the mask-fitting algorithm ported from PanelCleaner; see
`mask_fitting.py` for attribution. Distributed under GPL-3.0-or-later.
"""

import logging
from dataclasses import dataclass

import numpy as np
import imkit as imk

from .base import InpaintModel
from .mask_fitting import pick_best_mask
from .schema import Config

logger = logging.getLogger(__name__)


@dataclass
class MaskerConfig:
    """Tuning for mask fitting. Defaults match PanelCleaner's masker."""

    mask_growth_step_pixels: int = 2
    mask_growth_steps: int = 11
    min_mask_thickness: int = 4
    allow_colored_masks: bool = True
    off_white_max_threshold: int = 240
    mask_max_standard_deviation: float = 15.0
    mask_improvement_threshold: float = 0.1
    mask_selection_fast: bool = False


class SmartFill(InpaintModel):
    """Solid-fill cleaner using PanelCleaner's mask fitting.

    For each connected region of the inpaint mask, progressively grown masks
    are ranked by how uniform the image colours along their border are. A
    uniform border means the text sits on flat background — typically a speech
    bubble — so the region is filled with that border's median colour for a
    perfectly crisp result. Regions whose border never becomes uniform (busy
    art) are handed to LaMa for inpainting instead.
    """

    name = "smart_fill"

    # Skip huge regions (full-page masks); solid fill only makes sense locally.
    MAX_REGION_AREA_RATIO = 0.5

    def init_model(self, device, **kwargs):
        self.backend = kwargs.get("backend", "onnx")
        self._fill_device = device
        self._fallback_model = None
        self.masker_conf = kwargs.get("masker_conf") or MaskerConfig()

    @staticmethod
    def is_downloaded() -> bool:
        return True

    def forward(self, image, mask, config: Config):
        # Not used: __call__ is overridden and never pads/forwards.
        raise NotImplementedError

    def __call__(self, image: np.ndarray, mask: np.ndarray, config: Config):
        """
        image: [H, W, C] RGB, uint8
        mask: [H, W] with 255 marking regions to clean
        return: RGB image
        """
        result = image.copy()
        binary_mask = np.where(mask > 127, 255, 0).astype(np.uint8)
        if not binary_mask.any():
            return result

        num_labels, labels = imk.connected_components(binary_mask)
        residual_mask = np.zeros_like(binary_mask)
        height, width = binary_mask.shape[:2]
        max_region_area = int(height * width * self.MAX_REGION_AREA_RATIO)

        filled = 0
        for label in range(1, num_labels):
            component = labels == label
            area = int(component.sum())
            if area == 0:
                continue
            if area > max_region_area or not self._fill_component(result, component):
                residual_mask[component] = 255
            else:
                filled += 1

        logger.info(
            "Smart Fill: filled %d/%d regions, %s",
            filled, num_labels - 1,
            "no fallback needed" if not residual_mask.any() else "falling back to LaMa for the rest",
        )

        if residual_mask.any():
            result = self._get_fallback_model()(result, residual_mask, config)
        return result

    def _fill_component(self, result: np.ndarray, component: np.ndarray) -> bool:
        """Try to solid-fill one mask component. Returns True when filled."""
        conf = self.masker_conf
        ys, xs = np.nonzero(component)
        margin = (
            conf.min_mask_thickness
            + conf.mask_growth_step_pixels * conf.mask_growth_steps
            + 2
        )
        height, width = component.shape[:2]
        y1 = max(0, ys.min() - margin); y2 = min(height, ys.max() + 1 + margin)
        x1 = max(0, xs.min() - margin); x2 = min(width, xs.max() + 1 + margin)

        image_crop = result[y1:y2, x1:x2]
        mask_crop = np.where(component[y1:y2, x1:x2], 255, 0).astype(np.uint8)

        # The filled bounding box is offered alongside the grown masks: masks
        # derived from connected components are ragged, so on a flat bubble the
        # solid box often has a more uniform border and fills more cleanly.
        best_mask, best_color, _deviation = pick_best_mask(
            image_crop,
            mask_crop,
            self._bounding_box_mask(mask_crop, conf.min_mask_thickness),
            conf,
        )
        if best_mask is None or best_color is None:
            return False

        image_crop[best_mask > 0] = np.array(best_color, dtype=np.uint8)
        return True

    @staticmethod
    def _bounding_box_mask(mask_crop: np.ndarray, pad: int) -> np.ndarray:
        """A solid rectangle covering the component, padded so it clears glyph
        antialiasing."""
        height, width = mask_crop.shape[:2]
        ys, xs = np.nonzero(mask_crop)
        box = np.zeros_like(mask_crop)
        y1 = max(0, ys.min() - pad); y2 = min(height, ys.max() + 1 + pad)
        x1 = max(0, xs.min() - pad); x2 = min(width, xs.max() + 1 + pad)
        box[y1:y2, x1:x2] = 255
        return box

    def _get_fallback_model(self):
        if self._fallback_model is None:
            from .lama import LaMa
            logger.info("Smart Fill: initializing LaMa fallback inpainter")
            self._fallback_model = LaMa(self._fill_device, backend=self.backend)
        return self._fallback_model
