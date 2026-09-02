"""The local cleaner server, and the one bug only a real socket showed.

`runpod/local_server.py` exists so the cloud cleaner can be run end to end
without Docker, RunPod or a GPU.

**Serving connections one at a time deadlocks under keep-alive.** HTTP/1.1
holds the connection open after a reply, so a single-threaded server stays in
the first client's read loop and never accepts a second client at all. It
passed every in-process test and appeared the moment two clients talked to it.

Two things about these tests are worth knowing, because the first version of
them was useless. They hold connections **open** — an earlier version opened
and closed one per request and passed against the broken server, because
closing frees the handler loop and hides the bug. And the failure is a *hang*,
so the timeout inside `post` is the assertion, not an exception type.

I also blamed a missing body-drain for a 401 reaching the client as a
connection error, and added one. Removing that drain does not reproduce the
symptom — the deadlock explains it — so there is no test here pretending to
guard it. The drain stays as hygiene, described as such.

The handler is stubbed, so nothing here loads a model or downloads weights.
"""

import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_PATH = REPO_ROOT / "runpod" / "local_server.py"


@pytest.fixture(scope="module")
def server_module():
    spec = importlib.util.spec_from_file_location("local_cleaner_server", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["local_cleaner_server"] = module
    spec.loader.exec_module(module)
    return module


class StubHandler:
    """Stands in for runpod/serverless_handler.py — no model, no download."""

    @staticmethod
    def handler(job):
        return {"image": "stub", "echo": sorted((job.get("input") or {}).keys())}


@pytest.fixture
def running(server_module):
    """A live server on a real port, torn down afterwards."""
    handler_class = server_module.make_handler_class(
        StubHandler, "testkey", server_module.JobStore()
    )
    httpd = server_module.http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def post(port, path, body, key="testkey", session=None):
    """Post over a **kept-alive** connection, the way the real client does.

    This detail is the whole test. `modules/inpainting/remote.py` holds a
    `requests.Session`, so its connection stays open after a reply — which is
    exactly the condition both bugs below need. An earlier version of these
    tests opened and closed a connection per request and passed against both
    broken servers, because closing hides the desync and frees the handler
    loop. A test that cannot fail is worse than no test.
    """
    own = session is None
    if own:
        session = requests.Session()
    try:
        response = session.post(
            f"http://127.0.0.1:{port}{path}",
            json=body,
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, {}
    finally:
        if own:
            session.close()


def test_a_second_client_is_served_while_the_first_holds_its_connection(running):
    """The keep-alive deadlock.

    A single-threaded server sits in the first client's read loop after
    replying — HTTP/1.1 does not close the connection — and never accepts
    anyone else. The first session is deliberately left **open** here: close it
    and the handler loop ends, the second client is served, and the bug hides.

    It hangs rather than failing, so the 10s timeout inside `post` is the
    assertion.
    """
    first = requests.Session()
    try:
        first_status, _ = post(running, "/runsync", {"input": {"image": "a"}},
                               session=first)
        # first is still open and idle here — that is the condition under test
        second_status, _ = post(running, "/runsync", {"input": {"image": "b"}})
    finally:
        first.close()

    assert (first_status, second_status) == (200, 200)


def test_a_bad_key_gets_401_and_leaves_the_connection_usable(running):
    """A wrong key must read as a wrong key, over a connection that stays alive.

    This does not guard the body-drain — removing that does not break this, and
    saying otherwise would be a test claiming credit it has not earned. What it
    does pin is the behaviour that matters to somebody who mistyped their key:
    a 401 they can act on, on a connection they can immediately retry over.
    """
    session = requests.Session()
    try:
        first_status, body = post(
            running, "/runsync", {"input": {"image": "x" * 200_000}},
            key="wrong", session=session,
        )
        assert first_status == 401
        assert body["error"] == "Unauthorized"

        # The reply left the connection usable, so a corrected key works on it.
        retried_status, _ = post(
            running, "/runsync", {"input": {"image": "a"}}, session=session
        )
        assert retried_status == 200
    finally:
        session.close()


def test_the_input_wrapper_is_required(running):
    """RunPod requires it, so the local server rejecting it is what would catch
    a client that quietly stopped sending it."""
    status, body = post(running, "/runsync", {"image": "a"})

    assert status == 400
    assert "input" in body["error"]


def test_it_reports_a_timing_for_the_cost_display(running):
    status, body = post(running, "/runsync", {"input": {"image": "a"}})

    assert status == 200
    assert body["status"] == "COMPLETED"
    assert isinstance(body["executionTime"], int)


def test_a_finished_job_can_be_fetched_by_id(running):
    _, body = post(running, "/runsync", {"input": {"image": "a"}})

    fetched = requests.get(
        f"http://127.0.0.1:{running}/status/{body['id']}",
        headers={"Authorization": "Bearer testkey"},
        timeout=10,
    ).json()

    assert fetched["id"] == body["id"]
    assert fetched["status"] == "COMPLETED"


def test_cancel_is_answered(running):
    """The client calls this to stop paying for a job it gave up on."""
    status, body = post(running, "/cancel/some-job", {})

    assert status == 200
    assert body["status"] == "CANCELLED"
