import logging

import numpy as np

from .base import OCREngine
from ..utils.textblock import TextBlock, adjust_text_line_coordinates

logger = logging.getLogger(__name__)


class EasyOCREngine(OCREngine):
    """OCR engine backed by the optional `easyocr` package (torch-based).

    Only offered in the UI when easyocr is installed. Each text block is
    cropped and recognized separately; multi-line results are joined in
    reading order.
    """

    LANGUAGE_CODE_MAP = {
        'Korean': 'ko', 'Japanese': 'ja', 'Chinese': 'ch_sim', 'English': 'en',
        'Russian': 'ru', 'French': 'fr', 'Spanish': 'es', 'Italian': 'it',
        'German': 'de', 'Dutch': 'nl',
        # Script buckets used when the source language is "Auto"
        'korean': 'ko', 'japanese': 'ja', 'chinese': 'ch_sim',
        'cyrillic': 'ru', 'latin': 'en',
    }
    NO_SPACE_CODES = ('ja', 'ch_sim')

    def __init__(self):
        self.reader = None
        self.expansion_percentage = 5
        self._joiner = ' '

    def initialize(
        self,
        source_lang_english: str = 'English',
        device: str = 'cpu',
        expansion_percentage: int = 5,
    ) -> None:
        import easyocr  # optional dependency; the UI hides this engine without it

        code = self.LANGUAGE_CODE_MAP.get(source_lang_english, 'en')
        languages = [code] if code == 'en' else [code, 'en']
        self.reader = easyocr.Reader(languages, gpu=device != 'cpu')
        self.expansion_percentage = expansion_percentage
        self._joiner = '' if code in self.NO_SPACE_CODES else ' '

    def process_image(self, img: np.ndarray, blk_list: list[TextBlock]) -> list[TextBlock]:
        for blk in blk_list:
            if blk.xyxy is None:
                blk.text = ""
                continue
            x1, y1, x2, y2 = adjust_text_line_coordinates(
                blk.xyxy, self.expansion_percentage, self.expansion_percentage, img
            )
            if not (0 <= x1 < x2 <= img.shape[1] and 0 <= y1 < y2 <= img.shape[0]):
                blk.text = ""
                continue
            try:
                results = self.reader.readtext(img[y1:y2, x1:x2])
            except Exception:
                logger.exception("EasyOCR failed on block")
                blk.text = ""
                continue
            # Reading order: top-to-bottom, then left-to-right
            results = sorted(
                results,
                key=lambda r: (min(p[1] for p in r[0]), min(p[0] for p in r[0])),
            )
            blk.text = self._joiner.join(text for _, text, _ in results).strip()
        return blk_list
