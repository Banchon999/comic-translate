import numpy as np

from modules.ocr.base import OCREngine
from modules.utils.textblock import TextBlock
from modules.utils.download import ModelDownloader, ModelID
from modules.utils.language_utils import is_no_space_lang
from modules.ocr.crop_utils import crop_with_bounds, expanded_ocr_block_bounds, expanded_ocr_line_bounds
from .pororo.models.brainOCR.utils import reformat_input
from modules.ocr.ppocr.preprocessing import crop_quad


class PororoOCREngine(OCREngine):
    """OCR engine using PororoOCR for Korean text."""
    
    def __init__(self):
        self.model = None
        self.expansion_percentage = 5
        self.device = None
        self.use_text_lines = True
        
    def initialize(
        self, 
        lang: str = 'ko', 
        expansion_percentage: int = 5, 
        device: str = 'cpu',
        use_text_lines: bool = True,
    ) -> None:
        """
        Initialize the PororoOCR engine.
        
        Args:
            lang: Language code for OCR model - default is 'ko' (Korean)
            expansion_percentage: Percentage to expand text bounding boxes
            device: Device to run the model on ('cpu', 'cuda', etc.). If None, auto-detects.
        """

        from .main import PororoOcr

        self.expansion_percentage = expansion_percentage
        self.device = device
        self.use_text_lines = use_text_lines
        if self.model is None:
            ModelDownloader.get(ModelID.PORORO)
            self.model = PororoOcr(lang=lang, device=device, use_text_lines=use_text_lines)
        
    def process_image(self, img: np.ndarray, blk_list: list[TextBlock]) -> list[TextBlock]:
        if self.use_text_lines and any(getattr(blk, 'lines', None) for blk in blk_list):
            _, img_cv_grey = reformat_input(img)
            reader = _reader_from_pororo_ocr(self.model)
            if reader is not None:
                _set_reader_recognition_defaults(reader.opt2val)
                for blk in blk_list:
                    lines = getattr(blk, 'lines', None) or [blk.xyxy]
                    texts = []
                    for line in lines:
                        crop = _crop_line_grey(
                            img_cv_grey,
                            line,
                            blk,
                            self.expansion_percentage,
                            getattr(self, "min_expansion_px", 0),
                        )
                        if crop is None or crop.size == 0:
                            continue
                        result = reader.recognize(crop, None, None, reader.opt2val)
                        texts.extend(text.strip() for _, text, _ in result if text and text.strip())
                    blk.texts = texts
                    blk.text = ''.join(texts) if is_no_space_lang(getattr(blk, 'source_lang', '')) else ' '.join(texts)
                return blk_list

        for blk in blk_list:
            # Get box coordinates
            bounds = expanded_ocr_block_bounds(
                img,
                blk,
                self.expansion_percentage,
                getattr(self, "min_expansion_px", 0),
            )
            
            # Check if coordinates are valid
            cropped_img = crop_with_bounds(img, bounds)
            if cropped_img is not None:
                # Crop image and run OCR
                self.model.run_ocr(cropped_img)
                result = self.model.get_ocr_result()
                descriptions = result.get('description', [])
                blk.text = ' '.join(descriptions)
            else:
                print('Invalid textbbox to target img')
                blk.text = ""
                
        return blk_list


def _reader_from_pororo_ocr(model):
    ocr_task = getattr(model, '_ocr', None)
    return getattr(ocr_task, '_model', None)


def _set_reader_recognition_defaults(opt2val: dict) -> None:
    opt2val.setdefault("batch_size", 1)
    opt2val.setdefault("n_workers", 0)
    opt2val.setdefault("contrast_ths", 0.1)
    opt2val.setdefault("adjust_contrast", 0.5)
    opt2val["skip_details"] = False
    opt2val["paragraph"] = False


def _crop_line_grey(
    img_cv_grey: np.ndarray,
    line,
    blk: TextBlock | None = None,
    expansion_percentage: int = 5,
    min_padding: int = 0,
) -> np.ndarray | None:
    arr = np.asarray(line)
    if blk is None and arr.ndim == 2 and arr.shape[0] >= 4 and arr.shape[1] == 2:
        return crop_quad(img_cv_grey, arr[:4].astype(np.float32))

    if blk is not None:
        crop = crop_with_bounds(
            img_cv_grey,
            expanded_ocr_line_bounds(
                img_cv_grey,
                blk,
                line,
                expansion_percentage=expansion_percentage,
                min_padding=min_padding,
            ),
        )
    else:
        if arr.size != 4:
            return None
        x1, y1, x2, y2 = [int(round(float(v))) for v in arr.reshape(-1)[:4]]

        x1 = max(0, min(img_cv_grey.shape[1], x1))
        x2 = max(0, min(img_cv_grey.shape[1], x2))
        y1 = max(0, min(img_cv_grey.shape[0], y1))
        y2 = max(0, min(img_cv_grey.shape[0], y2))
        if x2 <= x1 or y2 <= y1:
            return None
        crop = img_cv_grey[y1:y2, x1:x2]

    if crop is None or crop.size == 0:
        return None
    h, w = crop.shape[:2]
    if h > 0 and w > 0 and h / float(w) >= 1.5:
        crop = np.rot90(crop)
    return crop
