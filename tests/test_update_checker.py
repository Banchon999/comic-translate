"""The update check must never give a Qt object a life on another thread.

It used to move a Python QObject worker onto a QThread and, when the request
finished, destroy it there with deleteLater. Destroying a Python-derived
QObject calls back into PySide, which needs the GIL, while Qt holds one of its
pooled signal/slot mutexes — and the main thread, holding the GIL, could be
waiting on that same mutex to make a connection. Every window starts a check,
so a check finishing while a window was being built (two addresses hashing to
the same pooled mutex) froze the process for good: the suite hung once in
every ten to twenty runs, gdb-traced to exactly this, and the app could freeze at startup
the same way.

The request now runs on a plain Python thread that only emits the checker's
own signals, so nothing Qt is created or destroyed off the GUI thread.
"""

import threading

import pytest
from PySide6 import QtCore

from app import update_checker


class _Response:
    def __init__(self, tag):
        self._tag = tag

    def raise_for_status(self):
        pass

    def json(self):
        return {"tag_name": self._tag, "html_url": "https://example.invalid", "assets": []}


def _wait_for(predicate, qapp, timeout_ms=5000):
    deadline = QtCore.QDeadlineTimer(timeout_ms)
    while not predicate() and not deadline.hasExpired():
        qapp.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 20)
    return predicate()


@pytest.fixture
def no_moves(monkeypatch):
    """Record every QObject.moveToThread call made while the test runs."""
    moved = []
    original = QtCore.QObject.moveToThread

    def recording(self, thread):
        moved.append(type(self).__name__)
        return original(self, thread)

    monkeypatch.setattr(QtCore.QObject, "moveToThread", recording)
    return moved


def test_the_result_arrives_on_the_gui_thread_without_moving_any_qobject(qapp, monkeypatch, no_moves):
    monkeypatch.setattr(update_checker.requests, "get", lambda *a, **k: _Response("0.0.1"))
    checker = update_checker.UpdateChecker()
    seen = []
    checker.up_to_date.connect(lambda: seen.append(threading.current_thread() is threading.main_thread()))
    checker.error_occurred.connect(lambda msg: seen.append(f"error: {msg}"))

    checker.check_for_updates()

    assert _wait_for(lambda: seen, qapp), "the check never reported back"
    assert seen == [True], seen
    assert no_moves == [], f"a QObject was moved to another thread: {no_moves}"
    checker.shutdown()


def test_a_failed_request_reports_an_error_on_the_gui_thread(qapp, monkeypatch, no_moves):
    def boom(*args, **kwargs):
        raise OSError("no network")

    monkeypatch.setattr(update_checker.requests, "get", boom)
    checker = update_checker.UpdateChecker()
    errors = []
    checker.error_occurred.connect(
        lambda msg: errors.append((msg, threading.current_thread() is threading.main_thread()))
    )

    checker.check_for_updates()

    assert _wait_for(lambda: errors, qapp)
    assert errors == [("no network", True)]
    assert no_moves == []
    checker.shutdown()


def test_a_checker_deleted_mid_request_is_harmless(qapp, monkeypatch):
    """The window can close while the request is still out; the late result
    must not touch a deleted C++ object or raise on the worker thread."""
    release = threading.Event()
    finished = threading.Event()

    def slow(*args, **kwargs):
        release.wait(5)
        return _Response("0.0.1")

    monkeypatch.setattr(update_checker.requests, "get", slow)
    failures = []
    monkeypatch.setattr(threading, "excepthook", lambda args: failures.append(args.exc_value))

    checker = update_checker.UpdateChecker()
    checker.check_for_updates()
    worker = checker._thread
    checker.shutdown(timeout=0)
    import shiboken6

    shiboken6.delete(checker)
    release.set()
    worker.join(5)
    finished.set()

    assert not worker.is_alive()
    assert failures == []


# -- which release it asks, and what it downloads ------------------------------

def _published_assets():
    """The archive names release.yml publishes, as GitHub would list them."""
    import re
    from pathlib import Path

    release = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    names = sorted(set(re.findall(r"dist/(ToonStudio-[\w.-]+)", release)))
    assert names, "release.yml publishes nothing"
    return [{"name": n, "browser_download_url": f"https://dl.invalid/{n}"} for n in names]


@pytest.mark.parametrize("system, expected", [
    ("Windows", "ToonStudio-Windows-x86_64.zip"),
    ("Linux", "ToonStudio-Linux-x86_64.tar.gz"),
])
def test_every_os_finds_its_own_archive_in_a_release(system, expected):
    url = update_checker.choose_asset(_published_assets(), system)
    assert url == f"https://dl.invalid/{expected}"


def test_an_installer_wins_over_an_archive_and_macos_needs_one():
    assets = _published_assets() + [{"name": "ToonStudio-Setup.exe", "browser_download_url": "https://dl.invalid/setup"}]
    assert update_checker.choose_asset(assets, "Windows") == "https://dl.invalid/setup"
    # No DMG in the release: macOS is told there is nothing for it.
    assert update_checker.choose_asset(_published_assets(), "Darwin") is None


def test_it_asks_this_projects_releases_not_upstreams(qapp, monkeypatch):
    asked = []

    class Response(_Response):
        def json(self):
            return {"tag_name": "v999.0.0", "html_url": "https://example.invalid", "assets": _published_assets()}

    def get(url, **kwargs):
        asked.append(url)
        return Response("v999.0.0")

    monkeypatch.setattr(update_checker.requests, "get", get)
    monkeypatch.setattr(update_checker.platform, "system", lambda: "Linux")
    checker = update_checker.UpdateChecker()
    offered = []
    checker.update_available.connect(lambda v, notes, url: offered.append((v, url)))
    checker.error_occurred.connect(lambda msg: offered.append(("error", msg)))
    checker.check_for_updates()
    assert _wait_for(lambda: offered, qapp)
    assert asked == ["https://api.github.com/repos/Banchon999/comic-translate/releases/latest"]
    assert offered == [("999.0.0", "https://dl.invalid/ToonStudio-Linux-x86_64.tar.gz")]
    checker.shutdown()


@pytest.mark.parametrize("system, path, expected", [
    ("Windows", r"C:\Users\me\Downloads\ToonStudio-Windows-x86_64.zip", "explorer"),
    ("Linux", "/home/me/Downloads/ToonStudio-Linux-x86_64.tar.gz", "xdg-open"),
])
def test_a_downloaded_archive_is_shown_in_its_folder_never_run(monkeypatch, system, path, expected):
    launched = []
    monkeypatch.setattr(update_checker.platform, "system", lambda: system)
    monkeypatch.setattr(update_checker.subprocess, "Popen", lambda args, **k: launched.append(args))
    monkeypatch.setattr(update_checker.os, "startfile", lambda p: launched.append(["startfile", p]), raising=False)
    assert not update_checker.is_installer(path, system)
    update_checker.UpdateChecker().run_installer(path)
    assert len(launched) == 1 and launched[0][0] == expected, launched


def test_an_installer_is_still_run(monkeypatch):
    launched = []
    monkeypatch.setattr(update_checker.platform, "system", lambda: "Windows")
    monkeypatch.setattr(update_checker.os, "startfile", lambda p: launched.append(p), raising=False)
    assert update_checker.is_installer("setup.exe", "Windows")
    update_checker.UpdateChecker().run_installer("setup.exe")
    assert launched == ["setup.exe"]
