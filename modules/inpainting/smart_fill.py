import logging

import numpy as np
import imkit as imk

from .base import InpaintModel
from .schema import Config

logger = logging.getLogger(__name__)


class SmartFill(InpaintModel):
    """Solid-fill cleaner inspired by PanelCleaner's masker (independent implementation).

    For each connected region of the inpaint mask, the mask is grown in
    small steps. At every step the colors of the image pixels along the
    mask border are sampled; if they are uniform (low standard deviation),
    the text sits on a flat background — typically a speech bubble — and
    the region is filled with the border's median color for a perfectly
    crisp result. Regions whose border never becomes uniform (busy art
    backgrounds) are inpainted with LaMa instead.
    """

    name = "smart_fill"

    # Mask growth: first step thickness, extra growth per step, total steps.
    MIN_THICKNESS = 2
    GROWTH_STEP = 2
    GROWTH_STEPS = 5
    # A border is "uniform" when the std of its pixel colors is below this.
    MAX_BORDER_STD = 15.0
    # A larger mask is only preferred if its border std improves by this factor.
    IMPROVEMENT_FACTOR = 0.10
    # Median border colors at least this bright snap to pure white.
    OFF_WHITE_THRESHOLD = 240
    # Skip huge regions (full-page masks); solid fill only makes sense locally.
    MAX_REGION_AREA_RATIO = 0.5

    def init_model(self, device, **kwargs):
        self.backend = kwargs.get("backend", "onnx")
        self._fill_device = device
        self._fallback_model = None

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
        ys, xs = np.nonzero(component)
        margin = self.MIN_THICKNESS + self.GROWTH_STEP * self.GROWTH_STEPS + 2
        height, width = component.shape[:2]
        y1 = max(0, ys.min() - margin); y2 = min(height, ys.max() + 1 + margin)
        x1 = max(0, xs.min() - margin); x2 = min(width, xs.max() + 1 + margin)

        image_crop = result[y1:y2, x1:x2]
        mask_crop = np.where(component[y1:y2, x1:x2], 255, 0).astype(np.uint8)

        best_mask = None
        best_std = None
        best_color = None

        grown = mask_crop
        for step in range(self.GROWTH_STEPS):
            thickness = self.MIN_THICKNESS if step == 0 else self.GROWTH_STEP
            kernel = imk.get_structuring_element(
                imk.MORPH_ELLIPSE, (thickness * 2 + 1, thickness * 2 + 1)
            )
            grown = imk.dilate(grown, kernel)

            border_pixels = self._border_pixels(image_crop, grown)
            if border_pixels is None:
                # The grown mask swallowed the whole crop; larger steps won't help.
                break

            std, color = self._border_stats(border_pixels)
            if best_std is None or std <= best_std * (1 - self.IMPROVEMENT_FACTOR):
                best_std, best_color, best_mask = std, color, grown

        if best_std is None or best_std > self.MAX_BORDER_STD:
            return False

        if min(best_color) >= self.OFF_WHITE_THRESHOLD:
            best_color = (255, 255, 255)
        image_crop[best_mask > 0] = np.array(best_color, dtype=np.uint8)
        return True

    @staticmethod
    def _border_pixels(image_crop: np.ndarray, mask: np.ndarray) -> np.ndarray | None:
        """Image pixels along the outer edge ring of the mask."""
        kernel = np.ones((3, 3), dtype=np.uint8)
        eroded = imk.erode(mask, kernel)
        ring = (mask > 0) & (eroded == 0)
        if not ring.any():
            return None
        return image_crop[ring].reshape(-1, image_crop.shape[-1]).astype(np.float64)

    @staticmethod
    def _border_stats(border_pixels: np.ndarray) -> tuple[float, tuple[int, ...]]:
        """Std of color distances from the mean, and the median border color."""
        mean_color = border_pixels.mean(axis=0)
        distances = np.linalg.norm(border_pixels - mean_color, axis=1)
        std = float(distances.std())
        median_color = tuple(int(c) for c in np.median(border_pixels, axis=0))
        return std, median_color

    def _get_fallback_model(self):
        if self._fallback_model is None:
            from .lama import LaMa
            logger.info("Smart Fill: initializing LaMa fallback inpainter")
            self._fallback_model = LaMa(self._fill_device, backend=self.backend)
        return self._fallback_model
