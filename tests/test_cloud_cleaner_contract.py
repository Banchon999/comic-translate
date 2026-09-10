"""Client and server have to agree, and they are in different directories.

`modules/inpainting/remote.py` encodes the request; `runpod/serverless_handler.py`
decodes it. Nothing imports one from the other, so a change to either side's
encoding is invisible until a real endpoint returns a smeared page. These tests
run both halves against each other in-process, with the model faked — the one
thing they cannot check is whether LaMa produces a good result, which is not
what this seam is for.

The handler is loaded by path because `runpod/` is deployment code, not a
package: it is copied into a Docker image, never imported by the app.
"""

import base64
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

import imkit as imk
from modules.inpainting import remote

REPO_ROOT = Path(__file__).resolve().parent.parent
HANDLER_PATH = REPO_ROOT / "runpod" / "serverless_handler.py"


@pytest.fixture(scope="module")
def handler():
    spec = importlib.util.spec_from_file_location("cleaner_handler", HANDLER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["cleaner_handler"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def no_model(handler, monkeypatch):
    """Replace LaMa with something that records what it was handed.

    The contract is about encoding, not about inpainting quality, and loading a
    real model would put a 200 MB download in the test suite.
    """

    class Recorder:
        def __init__(self):
            self.image = None
            self.mask = None

        def __call__(self, image, mask, config):
            self.image, self.mask = image, mask
            out = image.copy()
            out[mask > 127] = 255
            return out

    recorder = Recorder()
    monkeypatch.setattr(handler, "_get_model", lambda: recorder)
    return recorder


def a_page(width=64, height=48):
    page = np.full((height, width, 3), 180, dtype=np.uint8)
    page[10:30, 10:50] = 20
    return page


def a_mask(width=64, height=48):
    mask = np.zeros((height, width), dtype=np.uint8)
    mask[12:28, 12:48] = 255
    return mask


def client_request(page, mask):
    """Exactly what remote.py puts in the `input` object."""
    return {
        "image": remote._b64_jpeg(page),
        "mask": remote._b64_png(mask),
        "hd_strategy": "Original",
    }


def test_the_server_can_read_what_the_client_sends(handler, no_model):
    page, mask = a_page(), a_mask()

    result = handler.clean(client_request(page, mask))

    assert "error" not in result, result.get("error")
    assert no_model.image.shape == page.shape
    assert no_model.mask.shape == mask.shape


def test_the_mask_arrives_binary_at_the_model(handler, no_model):
    """The whole reason the mask is PNG.

    LaMa thresholds at 127, so grey pixels from a lossy round trip would move
    the boundary of the cleaned region rather than fail outright — a defect you
    would see as a halo, not as an exception.
    """
    handler.clean(client_request(a_page(), a_mask()))

    values = set(np.unique(no_model.mask).tolist())
    assert values <= {0, 255}, f"mask reached the model as {sorted(values)}"


def test_the_client_can_read_what_the_server_sends(handler, no_model):
    page, mask = a_page(), a_mask()

    result = handler.clean(client_request(page, mask))
    decoded = remote._decode_image(result["image"])

    assert decoded.shape[:2] == page.shape[:2]


def test_the_server_reply_is_lossless(handler, no_model):
    """PNG on the way back.

    The cleaned page is pasted over the original and re-encoded later by
    whatever the user exports to. A lossy hop here would put ringing around
    exactly the regions that were just cleaned.
    """
    page, mask = a_page(), a_mask()
    result = handler.clean(client_request(page, mask))

    decoded = remote._decode_image(result["image"])
    assert np.array_equal(decoded[mask > 127], np.full((int((mask > 127).sum()), 3), 255, np.uint8))


def test_a_mismatched_mask_is_refused_with_a_reason(handler, no_model):
    result = handler.clean(
        {
            "image": remote._b64_jpeg(a_page(64, 48)),
            "mask": remote._b64_png(a_mask(32, 24)),
        }
    )
    assert "error" in result and "32x24" in result["error"]


def test_missing_fields_do_not_crash_the_worker(handler, no_model):
    assert "error" in handler.clean({})
    assert "error" in handler.clean({"image": remote._b64_jpeg(a_page())})


def test_the_handler_never_raises(handler, monkeypatch):
    """A traceback reaches the client as an opaque FAILED with no reason."""

    def boom():
        raise RuntimeError("gpu fell over")

    monkeypatch.setattr(handler, "_get_model", boom)
    result = handler.handler({"input": client_request(a_page(), a_mask())})

    assert "error" in result and "gpu fell over" in result["error"]


def test_base64_is_plain_not_a_data_uri():
    """A `data:image/png;base64,` prefix would decode to garbage server-side."""
    encoded = remote._b64_png(a_mask())
    assert not encoded.startswith("data:")
    imk.decode_image(base64.b64decode(encoded))
