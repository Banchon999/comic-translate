"""The other half of the cloud cleaner: a RunPod serverless worker.

`modules/inpainting/remote.py` posts a page and its mask here; this runs LaMa on
the pod's GPU and posts the cleaned page back. It exists so "bring your own
endpoint" has something you can actually deploy, rather than a documented
contract and a shrug.

**It imports the app's own LaMa engine rather than reimplementing it.** That is
the point: client and server cannot drift on padding, mask polarity or colour
order, because there is only one implementation and it is the same one the
local Clean button uses. It also means a model fix lands on both sides at once.

Deploy:

    docker build -f runpod/Dockerfile.serverless -t <you>/comic-translate:cleaner .
    docker push <you>/comic-translate:cleaner

Then make a RunPod **serverless** endpoint from that image and paste its URL and
your API key into Settings > Credentials > Cloud Cleaner. See runpod/README.md.

The contract, which `remote.py` is the only client of:

    input:  {"image": <base64 JPEG>, "mask": <base64 PNG>, "hd_strategy": str}
    output: {"image": <base64 PNG>}          on success
            {"error": str}                   on failure

The reply is PNG, not JPEG. The cleaned page is pasted straight over the
original and then re-encoded by whatever the user exports to; putting a lossy
generation in the middle would show up as ringing around exactly the regions
that were just cleaned.
"""

from __future__ import annotations

import base64
import logging
import os

import numpy as np

import imkit as imk
from modules.inpainting.lama import LaMa
from modules.inpainting.schema import Config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cleaner")

# Loading LaMa takes seconds and a serverless worker handles many jobs, so it is
# built once at import and reused. RunPod keeps a warm worker alive between
# jobs; paying that cost per job would roughly double the bill for short pages.
_model: LaMa | None = None


def _get_model() -> LaMa:
    global _model
    if _model is None:
        device = os.environ.get("CLEANER_DEVICE", "cuda")
        backend = os.environ.get("CLEANER_BACKEND", "onnx")
        logger.info("loading LaMa on %s (%s)", device, backend)
        _model = LaMa(device, backend=backend)
    return _model


def _decode(b64: str) -> np.ndarray:
    return imk.decode_image(base64.b64decode(b64))


def _encode_png(image: np.ndarray) -> str:
    return base64.b64encode(imk.encode_image(image, ".png")).decode("utf-8")


def clean(job_input: dict) -> dict:
    """Run one page. Kept separate from `handler` so it is testable offline."""
    image_b64 = job_input.get("image")
    mask_b64 = job_input.get("mask")
    if not image_b64 or not mask_b64:
        return {"error": "both 'image' and 'mask' are required"}

    image = _decode(image_b64)
    mask = _decode(mask_b64)

    # A PNG mask can decode as [H, W, 3] or [H, W, 4] depending on how it was
    # written. LaMa wants [H, W]; taking one channel is right because every
    # channel of a greyscale PNG holds the same value.
    if mask.ndim == 3:
        mask = mask[:, :, 0]

    if mask.shape[:2] != image.shape[:2]:
        return {
            "error": (
                f"mask is {mask.shape[1]}x{mask.shape[0]} but the image is "
                f"{image.shape[1]}x{image.shape[0]}"
            )
        }

    config = Config(hd_strategy=job_input.get("hd_strategy") or "Original")
    cleaned = _get_model()(image, mask, config)
    return {"image": _encode_png(np.asarray(cleaned, dtype=np.uint8))}


def handler(job: dict) -> dict:
    """RunPod's entry point. Never raises: a traceback would reach the client
    as an opaque FAILED with no reason, and the client cannot act on that."""
    try:
        return clean(job.get("input") or {})
    except Exception as exc:  # noqa: BLE001 - the boundary of the worker
        logger.exception("cleaning failed")
        return {"error": f"{type(exc).__name__}: {exc}"}


if __name__ == "__main__":
    import runpod  # provided by the base image, not by this repo

    runpod.serverless.start({"handler": handler})
