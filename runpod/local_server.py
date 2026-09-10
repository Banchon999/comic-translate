"""Run the cloud cleaner's worker on this machine, speaking RunPod's protocol.

The client in `modules/inpainting/remote.py` had never talked to a real server
over a real socket — only to a fake in the test suite, which proves only that it
agrees with itself. This is the smallest thing that makes the whole path real:
the same handler that runs in the RunPod image, behind the same HTTP contract,
on localhost.

Two uses:

* **Try the feature without Docker, RunPod or a GPU.** Point
  `Settings > Credentials > Cloud Cleaner` at `http://127.0.0.1:8000` and clean
  a page. It runs LaMa on this machine, so it is not fast — the point is that
  every byte crosses a socket exactly as it would in production.
* **Debug a real endpoint's problems locally.** If a page comes back wrong from
  RunPod, running it through here says whether the fault is in the handler or in
  the deployment.

    python runpod/local_server.py                    # no auth, port 8000
    python runpod/local_server.py --port 9000 --api-key secret

Standard library only. Connections are threaded but jobs are serialised behind
a lock, so it behaves like one serverless worker: one page at a time, but a
second client is still answered rather than left hanging.

Serving connections one at a time instead looks correct and is not. HTTP/1.1
keeps the connection alive after a reply, so a single-threaded server sits in
the first client's read loop and never accepts anyone else — the second client
times out, and the error it reports is "could not reach the server", which
sends you looking at your network instead of at this. Found by pointing two
clients at it, not by reading it.

This is a development tool. It has no TLS and binds to localhost; do not put it
on a network.
"""

from __future__ import annotations

import argparse
import http.server
import importlib.util
import json
import logging
import sys
import threading
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("local-cleaner")


def _load_handler():
    """Load the deployment handler by path.

    `runpod/` is copied into a Docker image rather than imported as a package,
    so there is no `__init__.py` to import through. Loading the real file is the
    whole point: a reimplementation here could pass while the shipped one fails.
    """
    spec = importlib.util.spec_from_file_location(
        "cleaner_handler", REPO_ROOT / "runpod" / "serverless_handler.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class JobStore:
    """Finished jobs, so /status/{id} has something to answer with."""

    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def put(self, job_id: str, body: dict) -> None:
        with self._lock:
            self._jobs[job_id] = body

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            self._jobs[job_id] = {"id": job_id, "status": "CANCELLED"}
            return self._jobs[job_id]


def make_handler_class(handler_module, api_key: str | None, jobs: JobStore):
    # One page at a time, like a single serverless worker. The threading above
    # is only so a second connection gets accepted; letting two jobs into the
    # model at once would not match what RunPod does with one worker.
    job_lock = threading.Lock()

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _reply(self, status: int, body: dict) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _drain(self) -> None:
            """Read the request body even when the reply ignores it.

            Hygiene, not a fix. Replying on a keep-alive connection while the
            request body is still unread leaves it in the socket for the next
            request to be parsed out of, and some clients handle that worse
            than others.

            Said plainly because I first blamed the missing drain for a 401
            arriving at the client as "could not reach the server". It was not:
            that was the single-threaded deadlock below, and removing this
            drain does not reproduce the symptom. Kept because it is correct,
            not because it was the cause.
            """
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)

        def _authorised(self) -> bool:
            """Mirror RunPod's 401 so the client's auth path is exercised too."""
            if not api_key:
                return True
            header = self.headers.get("Authorization", "")
            return header == f"Bearer {api_key}"

        def _run(self, job_input: dict) -> dict:
            job_id = str(uuid.uuid4())
            with job_lock:
                started = time.time()
                output = handler_module.handler({"input": job_input})
                elapsed_ms = int((time.time() - started) * 1000)

            body = {
                "id": job_id,
                "status": "COMPLETED",
                # Real numbers, so the client's cost display is driven by
                # something it actually measured rather than a constant.
                "executionTime": elapsed_ms,
                "delayTime": 0,
                "output": output,
            }
            jobs.put(job_id, body)
            logger.info("job %s finished in %d ms", job_id[:8], elapsed_ms)
            return body

        def do_POST(self):  # noqa: N802 - the stdlib spells it this way
            if not self._authorised():
                self._drain()
                self._reply(401, {"error": "Unauthorized"})
                return

            path = self.path.rstrip("/")

            if path.startswith("/cancel/"):
                self._drain()
                self._reply(200, jobs.cancel(path.rsplit("/", 1)[-1]))
                return

            if path not in ("/runsync", "/run"):
                self._drain()
                self._reply(404, {"error": f"no such operation: {path}"})
                return

            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._reply(400, {"error": "body was not JSON"})
                return

            if "input" not in payload:
                # RunPod requires the wrapper; rejecting it here is what would
                # catch a client that stopped sending it.
                self._reply(400, {"error": "missing the 'input' wrapper"})
                return

            self._reply(200, self._run(payload["input"]))

        def do_GET(self):  # noqa: N802
            if not self._authorised():
                self._reply(401, {"error": "Unauthorized"})
                return

            path = self.path.rstrip("/")
            if path == "/health":
                self._reply(200, {"status": "ok"})
                return
            if path.startswith("/status/"):
                job_id = path.rsplit("/", 1)[-1]
                body = jobs.get(job_id)
                self._reply(200, body or {"id": job_id, "status": "FAILED",
                                          "error": "unknown job"})
                return
            self._reply(404, {"error": f"no such operation: {path}"})

        def log_message(self, *args):
            pass  # the job log above is the useful one

    return Handler


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1",
                        help="localhost by default; this has no TLS and no real auth")
    parser.add_argument("--api-key", default=None,
                        help="when set, requests must carry 'Bearer <key>' as RunPod's do")
    args = parser.parse_args(argv)

    logger.info("loading the cleaning handler")
    handler_module = _load_handler()

    # Threaded, not TCPServer: see the module docstring. A single-threaded
    # server plus HTTP/1.1 keep-alive answers exactly one client, forever.
    server = http.server.ThreadingHTTPServer(
        (args.host, args.port),
        make_handler_class(handler_module, args.api_key, JobStore()),
    )

    url = f"http://{args.host}:{args.port}"
    logger.info("cloud cleaner listening on %s", url)
    logger.info("paste this as the Endpoint URL in Settings > Credentials > Cloud Cleaner")
    if args.api_key:
        logger.info("and the API key you passed on the command line")
    logger.info("the LaMa weights download on the first job, so that one is slow")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("stopping")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
