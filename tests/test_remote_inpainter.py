"""The cloud cleaner talks to a paid endpoint, so its mistakes cost money.

Two things here are worth more than the rest. `force_full_image_inpainting`
decides whether cleaning a page is one billed job or eight, and the mask has to
survive the round trip as a binary image — a JPEG'd mask comes back with a grey
fringe the server would read as "partly clean this".

The transport is faked the way `test_openrouter_ocr.py` does it: a callable that
records the request and hands back a `SimpleNamespace`. Nothing here reaches the
network.
"""

import base64
import types

import numpy as np
import pytest

import imkit as imk
from modules.inpainting.remote import (
    RemoteInpaintError,
    RemoteInpainter,
    _normalise_endpoint,
)
from modules.inpainting.schema import Config

ENDPOINT = "https://api.runpod.ai/v2/abc123"


def a_page(width=64, height=48):
    page = np.full((height, width, 3), 200, dtype=np.uint8)
    page[10:30, 10:50] = 20
    return page


def a_mask(width=64, height=48):
    """255 where the text is, 0 elsewhere — the contract in base.py."""
    mask = np.zeros((height, width), dtype=np.uint8)
    mask[12:28, 12:48] = 255
    return mask


class FakeTransport:
    """Stands in for the engine's requests.Session."""

    def __init__(self, output=None, status=200, body=None):
        self.output = output
        self.status = status
        self.body = body
        self.posts = []
        self.gets = []

    def post(self, url, json=None, headers=None, timeout=None, **kw):
        self.posts.append({"url": url, "payload": json, "headers": headers})
        body = self.body if self.body is not None else {
            "id": "job-1",
            "status": "COMPLETED",
            "executionTime": 1234,
            "delayTime": 56,
            "output": {"image": self.output},
        }
        return types.SimpleNamespace(
            status_code=self.status, text="err", json=lambda: body
        )

    def get(self, url, headers=None, timeout=None, **kw):
        self.gets.append(url)
        return types.SimpleNamespace(
            status_code=self.status, text="err", json=lambda: self.body or {}
        )


def engine_with(transport, endpoint=ENDPOINT, api_key="k"):
    engine = RemoteInpainter("cpu", endpoint=endpoint, api_key=api_key)
    engine._session = transport
    return engine


def encoded(image):
    return base64.b64encode(imk.encode_image(image, ".png")).decode("utf-8")


# ---------------------------------------------------------------------------
# The cost argument
# ---------------------------------------------------------------------------


def test_it_asks_the_pipeline_for_one_call_per_page():
    """The flag that makes a page one billed job instead of eight.

    `pipeline/inpainting.py` branches on `force_full_image_inpainting` to choose
    between `_inpaint_full_image` (one call for the whole page) and
    `_inpaint_by_patches` (one call per merged mask region). RunPod bills queue
    plus execution per job and each pays its own cold start, so a real page —
    which merges into roughly eight regions — would cost eight times the
    overhead for the same pixels. Drop this flag and the bill multiplies with
    nothing else failing.
    """
    assert RemoteInpainter.force_full_image_inpainting is True


def test_the_pipeline_actually_reads_that_flag():
    """Guards the other half: the flag only matters if the branch still exists."""
    import inspect

    from pipeline import inpainting

    source = inspect.getsource(inpainting)
    assert "force_full_image_inpainting" in source, (
        "pipeline/inpainting.py no longer consults the flag, so the cloud cleaner "
        "is silently back to one billed job per patch"
    )


def test_an_empty_mask_costs_nothing():
    """Nothing marked means no request — paying a GPU to copy an image is absurd."""
    transport = FakeTransport()
    engine = engine_with(transport)

    page = a_page()
    out = engine(page, np.zeros(page.shape[:2], dtype=np.uint8), Config())

    assert transport.posts == []
    assert np.array_equal(out, page)


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------


def test_it_wraps_the_payload_the_way_runpod_requires():
    transport = FakeTransport(output=encoded(a_page()))
    engine_with(transport)(a_page(), a_mask(), Config())

    sent = transport.posts[0]
    assert sent["url"] == f"{ENDPOINT}/runsync"
    assert set(sent["payload"]) == {"input"}, "RunPod requires the input wrapper"
    assert sent["headers"]["Authorization"] == "Bearer k"


def test_the_mask_survives_as_a_binary_image():
    """PNG, not JPEG.

    A JPEG'd mask comes back with ringing around every hard edge, and the
    server would read those grey pixels as a partial instruction — smearing the
    boundary of every cleaned region. This decodes what was actually sent and
    checks nothing between 0 and 255 appeared.
    """
    transport = FakeTransport(output=encoded(a_page()))
    engine_with(transport)(a_page(), a_mask(), Config())

    round_tripped = imk.decode_image(
        base64.b64decode(transport.posts[0]["payload"]["input"]["mask"])
    )
    values = set(np.unique(round_tripped).tolist())
    assert values <= {0, 255}, f"the mask picked up intermediate values: {sorted(values)}"


def test_a_pasted_runsync_url_still_works():
    """People paste whichever URL the console showed them."""
    assert _normalise_endpoint(f"{ENDPOINT}/runsync") == ENDPOINT
    assert _normalise_endpoint(f"{ENDPOINT}/run") == ENDPOINT
    assert _normalise_endpoint(f"{ENDPOINT}/") == ENDPOINT
    assert _normalise_endpoint(ENDPOINT) == ENDPOINT


# A fake key, shaped like RunPod's. The real one this reproduces was pasted by
# a user into the endpoint box and came back out of an error dialog.
PASTED_KEY = "rpa_NOTAREALKEY0000000000000"

PASTED_CURL = (
    f"curl -X POST {ENDPOINT}/runsync \\\n"
    "    -H 'Content-Type: application/json' \\\n"
    f"    -H 'Authorization: Bearer {PASTED_KEY}' \\\n"
    '    -d \'{"input":{"prompt":"Your prompt"}}\''
)


def test_the_consoles_whole_curl_command_still_finds_the_endpoint():
    """RunPod's console offers the curl snippet with a copy button, so it gets
    pasted into the URL box. Extracting the URL beats failing much later."""
    assert _normalise_endpoint(PASTED_CURL) == ENDPOINT


def test_the_key_inside_a_pasted_curl_is_not_adopted():
    """It is right there and using it would work. Quietly storing a credential
    somebody typed into a *URL* field is worse than telling them where it goes."""
    engine = RemoteInpainter("cpu", endpoint=PASTED_CURL)

    assert engine.api_key == ""


def test_something_that_is_not_a_url_says_what_to_paste():
    """The old behaviour let any string through and failed as "could not reach
    the cloud cleaner", which sends people to check their network — the one
    place the fault is not."""
    assert _normalise_endpoint("not a url at all") == ""

    engine = RemoteInpainter("cpu", endpoint="not a url at all", api_key="k")
    with pytest.raises(RemoteInpaintError, match="does not look like an endpoint URL"):
        engine(a_page(), a_mask(), Config())


def test_an_empty_endpoint_still_says_it_is_unset():
    """Distinct from the above: nothing typed is a different problem from
    something typed wrong, and the message has to tell them apart."""
    engine = RemoteInpainter("cpu", endpoint="", api_key="k")
    with pytest.raises(RemoteInpaintError, match="No cloud cleaner endpoint"):
        engine(a_page(), a_mask(), Config())


def test_a_transport_failure_never_republishes_a_credential():
    """The regression guard for a real leak.

    `requests` puts the full request URL in its exception messages. A user
    pasted the console's curl command — key included — into the endpoint box,
    and the resulting `InvalidSchema` carried the live Bearer token into an
    error dialog and a traceback, which was then sent on to someone else.

    So the exception text must not reach the message. This drives the failure
    with a secret in it deliberately: if anyone reinstates `f"...: {exc}"`,
    this fails.
    """
    import requests

    class Exploding:
        def post(self, *args, **kwargs):
            raise requests.exceptions.InvalidSchema(
                f"No connection adapters were found for "
                f"'curl ... -H 'Authorization: Bearer {PASTED_KEY}' ...'"
            )

    engine = RemoteInpainter("cpu", endpoint=ENDPOINT, api_key="k")
    engine._requests = lambda: (requests, Exploding())

    with pytest.raises(RemoteInpaintError) as caught:
        engine._post(f"{ENDPOINT}/runsync", {"input": {}})

    message = str(caught.value)
    assert PASTED_KEY not in message
    assert "Bearer" not in message
    # The class name survives, because ConnectTimeout and SSLError want
    # different responses from whoever is reading.
    assert "InvalidSchema" in message


# ---------------------------------------------------------------------------
# The response
# ---------------------------------------------------------------------------


def test_it_returns_the_cleaned_page():
    cleaned = a_page()
    cleaned[12:28, 12:48] = 200
    transport = FakeTransport(output=encoded(cleaned))

    out = engine_with(transport)(a_page(), a_mask(), Config())
    assert out.shape == cleaned.shape


def test_a_wrong_sized_result_is_refused():
    """It would be pasted over the page, so the size has to match."""
    transport = FakeTransport(output=encoded(a_page(width=32, height=24)))
    with pytest.raises(RemoteInpaintError, match="same size"):
        engine_with(transport)(a_page(), a_mask(), Config())


def test_timings_are_recorded_for_the_cost_display():
    transport = FakeTransport(output=encoded(a_page()))
    engine = engine_with(transport)
    engine(a_page(), a_mask(), Config())

    assert engine.last_execution_ms == 1234
    assert engine.last_delay_ms == 56


def test_a_missing_timing_is_not_a_confident_zero():
    """RunPod documents these without pinning the field names.

    If they are absent the cost display must stay hidden rather than telling
    someone their job was free.
    """
    transport = FakeTransport(
        body={"status": "COMPLETED", "output": {"image": encoded(a_page())}}
    )
    engine = engine_with(transport)
    engine(a_page(), a_mask(), Config())

    assert engine.last_execution_ms is None


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


def test_no_endpoint_says_where_to_set_one():
    with pytest.raises(RemoteInpaintError, match="Credentials"):
        engine_with(FakeTransport(), endpoint="")(a_page(), a_mask(), Config())


@pytest.mark.parametrize(
    "status, fragment",
    [(401, "API key"), (404, "endpoint URL"), (429, "rate limiting")],
)
def test_http_failures_say_what_to_do(status, fragment):
    transport = FakeTransport(status=status)
    with pytest.raises(RemoteInpaintError, match=fragment) as caught:
        engine_with(transport)(a_page(), a_mask(), Config())
    assert caught.value.status_code == status


def test_a_failed_job_is_cancelled_so_it_stops_billing():
    """A worker keeps running, and keeps charging, after we stop reading."""
    transport = FakeTransport(
        body={"id": "job-9", "status": "FAILED", "error": "boom"}
    )
    engine = engine_with(transport)

    with pytest.raises(RemoteInpaintError, match="FAILED"):
        engine(a_page(), a_mask(), Config())

    cancelled = [p["url"] for p in transport.posts if "/cancel/" in p["url"]]
    assert cancelled == [f"{ENDPOINT}/cancel/job-9"]


def test_a_reply_without_an_image_is_an_error_not_a_blank_page():
    transport = FakeTransport(body={"status": "COMPLETED", "output": {}})
    with pytest.raises(RemoteInpaintError, match="no image data"):
        engine_with(transport)(a_page(), a_mask(), Config())


def test_nothing_needs_downloading():
    assert RemoteInpainter.is_downloaded() is True
