"""Cleaning on a GPU you rent by the second, instead of the local CPU.

LaMa on a laptop CPU is the slowest step in the pipeline — it is the reason the
remote-desktop route in `runpod/` exists. This sends the page to a RunPod
serverless endpoint instead, so a machine with no GPU gets GPU-speed cleaning
and pays only for the seconds the job actually runs.

You bring the endpoint. There is no billing service of ours in the middle: the
endpoint is yours, the API key is yours, and RunPod bills you directly. That is
what makes it pay-as-you-go rather than a subscription — and it is also why
nothing here touches `InsufficientCreditsException` or `settings.user_credits`,
which belong to the managed OCR/translation backend and would put a "Buy
Credits" button in front of an error RunPod raised.

Three things here are load-bearing:

**One request per page, not one per patch.** `force_full_image_inpainting`
sends `pipeline/inpainting.py` down `_inpaint_full_image` — a single call for
the whole page — instead of `_inpaint_by_patches`, which calls the engine once
per merged mask region. A real page merges into roughly eight patches, and
RunPod bills queue time plus execution *per job*, each paying its own cold
start. Eight jobs to clean one page is eight times the overhead for the same
pixels. The flag is read at `inpainting.py:650` and no local engine sets it.

**The mask goes as PNG, the page as JPEG.** The page is photographic and JPEG
keeps the upload small, which is what the hosted OCR and translation engines
already do. The mask is not photographic: it is binary, and JPEG's ringing
would turn its hard edges into a grey fringe that the server would then read as
"partly inpaint this", smearing the boundary of every cleaned region.

**A job that is running is a job that is billing.** Abandoning a request does
not stop the worker, so anything that gives up calls `/cancel/{id}`. Dropping
the socket just means paying for a result nobody reads.
"""

from __future__ import annotations

import base64
import logging
import re
import time
from typing import Any, Optional

import numpy as np

import imkit as imk

from .base import InpaintModel
from .schema import Config

logger = logging.getLogger(__name__)

# RunPod's synchronous call holds the connection until the job finishes. A cold
# worker can spend a while pulling the image before it starts, so this is a
# ceiling on "something is wrong", not on a normal job.
DEFAULT_TIMEOUT_SECONDS = 300

# Long jobs are better submitted and polled: /runsync can return a job id with
# status IN_QUEUE rather than a result, and then this is how often we ask.
POLL_INTERVAL_SECONDS = 1.0


class RemoteInpaintError(RuntimeError):
    """The endpoint could not clean the page.

    Carries `status_code` when the failure came back as HTTP rather than as a
    dead connection, so callers can tell "your key is wrong" from "the network
    is down" without parsing the message.
    """

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class RemoteInpainter(InpaintModel):
    """Posts the page and its mask to a RunPod serverless endpoint."""

    name = "remote"

    # The whole reason this class exists as a separate engine rather than a
    # backend flag on LaMa: see the module docstring.
    force_full_image_inpainting = True

    def init_model(self, device, **kwargs):
        # `device` is meaningless here — the GPU is someone else's. It is
        # accepted because `_ensure_inpainter` constructs every engine the same
        # way, and refusing the argument would make this the odd one out.
        self.endpoint = _normalise_endpoint(kwargs.get("endpoint"))
        # Only whether something was typed, never the text itself: a user who
        # pasted the console's curl command has their API key in that string,
        # and a bool cannot be accidentally logged or shown.
        self._endpoint_was_given = bool((kwargs.get("endpoint") or "").strip())
        self.api_key = (kwargs.get("api_key") or "").strip()
        self.timeout = kwargs.get("timeout") or DEFAULT_TIMEOUT_SECONDS

        # Populated after each successful job so the UI can show what the page
        # cost. None when the endpoint did not report timings — see
        # `last_execution_ms`.
        self.last_execution_ms: Optional[int] = None
        self.last_delay_ms: Optional[int] = None

        self._session = None  # built lazily; see _post

    @staticmethod
    def is_downloaded() -> bool:
        # Nothing to download. Saying True keeps the "fetch the weights first"
        # paths from trying to fetch weights that live on a server.
        return True

    def forward(self, image, mask, config: Config):
        # Not reachable: __call__ is overridden, so the padding and
        # HD-strategy machinery in the base class that would call this never
        # runs. Implemented only because the base class marks it abstract.
        raise NotImplementedError("RemoteInpainter overrides __call__")

    def __call__(self, image: np.ndarray, mask: np.ndarray, config: Config):
        """image: [H, W, C] RGB, mask: [H, W] with 255 = clean this. Returns BGR.

        The base class's `__call__` is bypassed wholesale, the way
        `DiffusionInpaintModel` does. Everything it provides — padding to a
        multiple of 8, choosing a torch or onnx session, the crop and resize HD
        strategies — describes how to run a model in this process. None of it
        applies when the model is on a machine we do not control, and the
        server is the right place to decide how to tile a large page.
        """
        if not self.endpoint:
            if self._endpoint_was_given:
                # Something was typed and it was not a URL. Saying "could not
                # reach the endpoint" here sends people to check their network,
                # which is the one place the fault is not.
                raise RemoteInpaintError(
                    "That does not look like an endpoint URL. Paste only the URL "
                    "— https://api.runpod.ai/v2/<ENDPOINT_ID> — into "
                    "Settings > Credentials > Cloud Cleaner, not the whole curl "
                    "command from the RunPod console. The API key goes in its "
                    "own box below."
                )
            raise RemoteInpaintError(
                "No cloud cleaner endpoint is configured. "
                "Set it in Settings > Credentials > Cloud Cleaner."
            )
        if mask is None or not np.any(mask):
            # Nothing marked. Paying a GPU to copy an image would be absurd.
            return image.copy()

        payload = {
            "input": {
                "image": _b64_jpeg(image),
                "mask": _b64_png(mask),
                # Passed through so the server can honour the same HD strategy
                # the local engines do, if it chooses to.
                "hd_strategy": str(getattr(config, "hd_strategy", "Original")),
            }
        }

        result = self._run_job(payload)
        cleaned = _decode_image(result.get("image"))

        if cleaned.shape[:2] != image.shape[:2]:
            raise RemoteInpaintError(
                f"The endpoint returned a {cleaned.shape[1]}x{cleaned.shape[0]} image "
                f"for a {image.shape[1]}x{image.shape[0]} page. The cleaned page has to "
                "come back the same size, or it cannot be pasted over the original."
            )
        return cleaned

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------

    def _run_job(self, payload: dict) -> dict:
        """Submit, wait, and return the job's `output` dict."""
        body = self._post(f"{self.endpoint}/runsync", payload)

        # /runsync still queues when every worker is busy: it answers with a job
        # id and a non-terminal status instead of the result. Polling from here
        # is what stops a busy endpoint looking like a broken one.
        job_id = body.get("id")
        status = (body.get("status") or "").upper()
        while status in ("IN_QUEUE", "IN_PROGRESS") and job_id:
            time.sleep(POLL_INTERVAL_SECONDS)
            body = self._get(f"{self.endpoint}/status/{job_id}")
            status = (body.get("status") or "").upper()

        if status and status not in ("COMPLETED", ""):
            self._cancel(job_id)
            raise RemoteInpaintError(
                f"The cloud cleaner job ended as {status}: {body.get('error') or 'no reason given'}"
            )

        # RunPod documents these as "execution details" without pinning the
        # field names, so they are read defensively: a missing timing means the
        # cost display stays hidden rather than showing a confident zero.
        self.last_execution_ms = _as_int(body.get("executionTime"))
        self.last_delay_ms = _as_int(body.get("delayTime"))

        output = body.get("output")
        if not isinstance(output, dict):
            raise RemoteInpaintError(
                "The endpoint completed without returning an image. "
                f"Got: {type(output).__name__}"
            )
        if output.get("error"):
            raise RemoteInpaintError(f"The cloud cleaner failed: {output['error']}")
        return output

    def _requests(self):
        # Imported lazily so this module stays importable in the headless gate
        # even in an environment without requests.
        import requests

        if self._session is None:
            self._session = requests.Session()
        return requests, self._session

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _unreachable(exc: Exception) -> "RemoteInpaintError":
        """Turn a transport exception into something safe to put on screen.

        `requests` embeds the full request URL in its exception messages, so
        interpolating one republishes whatever is in that URL. That is not
        hypothetical: a user pasted the RunPod console's whole curl snippet into
        the endpoint box, and the Bearer token inside it came back out in an
        error dialog and a traceback, which they then sent on. The URL is
        therefore never included, and what is included is redacted anyway —
        two separate things have to go wrong before a key escapes.

        The exception class name survives because it is the part that helps:
        `ConnectTimeout` and `SSLError` want different responses.
        """
        return RemoteInpaintError(
            f"Could not reach the cloud cleaner ({type(exc).__name__}). "
            "Check the endpoint URL in Settings > Credentials > Cloud Cleaner, "
            "and your connection."
        )

    def _post(self, url: str, payload: dict) -> dict:
        requests, session = self._requests()
        try:
            response = session.post(
                url, json=payload, headers=self._headers(), timeout=self.timeout
            )
        except requests.exceptions.RequestException as exc:
            logger.debug("cloud cleaner POST failed: %s", _redact(exc))
            raise self._unreachable(exc) from exc
        return _parse(response)

    def _get(self, url: str) -> dict:
        requests, session = self._requests()
        try:
            response = session.get(url, headers=self._headers(), timeout=self.timeout)
        except requests.exceptions.RequestException as exc:
            logger.debug("cloud cleaner GET failed: %s", _redact(exc))
            raise self._unreachable(exc) from exc
        return _parse(response)

    def _cancel(self, job_id: Optional[str]) -> None:
        """Stop a job we are no longer waiting for. Best effort, never fatal.

        A worker keeps running — and keeps billing — after we stop reading, so
        this is about money rather than tidiness. It is swallowed because
        failing to cancel must not replace the error that made us give up.
        """
        if not job_id or not self.endpoint:
            return
        try:
            _requests, session = self._requests()
            session.post(
                f"{self.endpoint}/cancel/{job_id}", headers=self._headers(), timeout=15
            )
        except Exception:
            logger.warning("Could not cancel cloud cleaner job %s; it may still bill", job_id)


# ----------------------------------------------------------------------
# helpers, kept module-level so they can be tested without a live endpoint
# ----------------------------------------------------------------------


# A bare URL, stopping at whitespace, quotes or a shell line-continuation, so
# one can be lifted out of a pasted curl command.
_URL_RE = re.compile(r"https?://[^\s'\"<>\\]+")

# `Bearer <token>` in any casing, and RunPod's own key shape, which is what
# turns up when someone pastes a console snippet.
_SECRET_RE = re.compile(r"(?i)\bbearer\s+\S+|\brpa_[A-Za-z0-9]+")


def _redact(text: Any) -> str:
    """Blank out anything credential-shaped before it is logged or shown."""
    return _SECRET_RE.sub("<redacted>", str(text))


def _normalise_endpoint(endpoint: Optional[str]) -> str:
    """Pull the endpoint's base URL out of whatever the user pasted.

    People paste whichever URL the console showed them, which is as often
    `.../runsync` as the bare endpoint. Appending `/runsync` to that produces a
    404 that reads like a broken deployment, so the suffix is stripped.

    They also paste the console's **entire curl command**, headers and all —
    there is a copy button for it, and it is the first thing on the page. That
    used to sail through unchecked and fail much later as "could not reach the
    cloud cleaner", which sends someone to look at their network rather than at
    the box they filled in. So the URL is extracted rather than assumed, and
    anything with no `http(s)` URL in it at all returns `""` for the caller to
    report properly.

    The API key sitting in that same pasted command is deliberately **not**
    harvested. It would work, and quietly storing a credential somebody typed
    into a *URL* field is worse than telling them which box it belongs in.
    """
    match = _URL_RE.search((endpoint or "").strip())
    if match is None:
        return ""
    url = match.group(0).rstrip("/")
    for suffix in ("/runsync", "/run", "/health", "/status", "/cancel"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url


def _b64_jpeg(image: np.ndarray) -> str:
    return base64.b64encode(imk.encode_image(image, ".jpg")).decode("utf-8")


def _b64_png(mask: np.ndarray) -> str:
    """PNG, not JPEG — the mask is binary and must survive the round trip exactly."""
    return base64.b64encode(imk.encode_image(mask, ".png")).decode("utf-8")


def _decode_image(encoded: Optional[str]) -> np.ndarray:
    if not encoded:
        raise RemoteInpaintError("The endpoint returned no image data.")
    try:
        return imk.decode_image(base64.b64decode(encoded))
    except RemoteInpaintError:
        raise
    except Exception as exc:
        raise RemoteInpaintError(f"The endpoint's image could not be decoded: {exc}") from exc


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse(response) -> dict:
    """Turn a response into the job dict, or raise with something readable."""
    if response.status_code == 401:
        raise RemoteInpaintError(
            "The cloud cleaner rejected the API key (401). Check it in "
            "Settings > Credentials > Cloud Cleaner.",
            status_code=401,
        )
    if response.status_code == 404:
        raise RemoteInpaintError(
            "The cloud cleaner endpoint was not found (404). Check the endpoint URL.",
            status_code=404,
        )
    if response.status_code == 429:
        raise RemoteInpaintError(
            "The cloud cleaner is rate limiting (429). Wait and try again — retrying "
            "automatically would spend money without asking.",
            status_code=429,
        )
    if response.status_code >= 400:
        raise RemoteInpaintError(
            f"The cloud cleaner returned {response.status_code}.",
            status_code=response.status_code,
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise RemoteInpaintError("The cloud cleaner's reply was not JSON.") from exc
    if not isinstance(body, dict):
        raise RemoteInpaintError("The cloud cleaner's reply was not a JSON object.")
    return body
