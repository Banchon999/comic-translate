"""OCR through PaddleOCR-VL, a vision-language document parser.

Unlike the CTC recognisers used elsewhere in this project, this is a 0.9B
generative model: it reads a cropped region and writes out what it says. That
buys robustness the small models do not have — stylised lettering, low
contrast, unusual layouts, and a hundred-odd languages from one checkpoint —
at the cost of a 1.9 GB download and a generation pass per block, which is
slow on a CPU.

Optional, like EasyOCR: it needs `transformers` 5 or newer plus `torch` and
`torchvision`, none of which this project depends on, so the settings list
only offers it when all three are importable.

Model: https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6 (Apache-2.0)
"""

from __future__ import annotations

import logging

import numpy as np
from PIL import Image

from modules.utils.download import ModelDownloader, ModelID
from modules.utils.textblock import TextBlock
from modules.utils.language_utils import is_no_space_lang
from .base import OCREngine
from .crop_utils import crop_with_bounds, expanded_ocr_block_bounds

logger = logging.getLogger(__name__)

# The model is prompt-selected per task; plain text recognition on an already
# cropped region is the one that matches how this pipeline works.
OCR_PROMPT = "OCR:"

MAX_NEW_TOKENS = 512


#: transformers 5 carries this architecture natively; torchvision is what its
#: image processor is built on, and its absence only surfaces as a failure
#: half-way through loading the processor.
REQUIRED_PACKAGES = ("torch", "torchvision", "transformers")


def dependencies_available() -> bool:
    """Whether every optional package this engine needs is importable."""
    import importlib.util

    return all(importlib.util.find_spec(name) is not None for name in REQUIRED_PACKAGES)


class PaddleOCRVLEngine(OCREngine):
    """Recognises each text block by generating its text with PaddleOCR-VL."""

    def __init__(self):
        self.model = None
        self.processor = None
        self.device = "cpu"
        self.expansion_percentage = 5
        self.min_expansion_px = 0

    def initialize(self, device: str = "cpu", expansion_percentage: int = 5) -> None:
        if not dependencies_available():
            import importlib.util

            missing = [
                name for name in REQUIRED_PACKAGES
                if importlib.util.find_spec(name) is None
            ]
            raise RuntimeError(
                f"PaddleOCR-VL needs {', '.join(missing)}, which are not installed. "
                'Run: pip install torch torchvision "transformers>=5"'
            )

        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor

        self.expansion_percentage = expansion_percentage

        ModelDownloader.get(ModelID.PADDLEOCR_VL)
        model_dir = ModelDownloader.get_save_dir(ModelID.PADDLEOCR_VL)

        # bfloat16 halves the memory and is what the model was released in;
        # CPU kernels for it are uneven, so float32 is used off the GPU.
        on_gpu = device not in ("cpu", "")
        dtype = torch.bfloat16 if on_gpu else torch.float32

        # transformers 5 implements this architecture itself, so the checkpoint
        # loads without trust_remote_code — the repo also ships its own
        # modelling code, but mixing that config with the built-in model class
        # fails, and not executing downloaded Python is the better trade anyway.
        self.model = AutoModelForImageTextToText.from_pretrained(model_dir, dtype=dtype)
        self.model = self.model.to("cuda" if on_gpu else "cpu").eval()
        self.processor = AutoProcessor.from_pretrained(model_dir)
        self.device = "cuda" if on_gpu else "cpu"

    def process_image(self, img: np.ndarray, blk_list: list[TextBlock]) -> list[TextBlock]:
        if self.model is None or self.processor is None:
            return blk_list

        for blk in blk_list:
            crop = crop_with_bounds(
                img,
                expanded_ocr_block_bounds(
                    img, blk, self.expansion_percentage, self.min_expansion_px
                ),
            )
            if crop is None:
                blk.text = ""
                continue
            try:
                blk.text = self._recognize(crop, blk)
            except Exception:
                logger.exception("PaddleOCR-VL failed on a block")
                blk.text = ""

        return blk_list

    def _recognize(self, crop: np.ndarray, blk: TextBlock) -> str:
        import torch

        image = Image.fromarray(crop).convert("RGB")
        messages = [{
            "role": "user",
            "content": [
                {"type": "image", "image": image},
                {"type": "text", "text": OCR_PROMPT},
            ],
        }]

        # The checkpoint's own preprocessor_config already caps images at
        # 1280*28*28 pixels, which is what upstream recommends for this task,
        # so the processor's configured size is used as-is.
        inputs = self.processor.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        ).to(self.model.device)

        with torch.inference_mode():
            outputs = self.model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS)

        # Everything after the prompt is the answer; the last token is the
        # end-of-turn marker.
        generated = outputs[0][inputs["input_ids"].shape[-1]:-1]
        text = self.processor.decode(generated).strip()
        return self._join_lines(text, blk)

    @staticmethod
    def _join_lines(text: str, blk: TextBlock) -> str:
        """Collapse the model's line breaks the way the rest of the pipeline does."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return ""
        joiner = "" if is_no_space_lang(getattr(blk, "source_lang", "")) else " "
        return joiner.join(lines)
