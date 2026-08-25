"""Diagnostics have to survive a windowed build, where stderr goes nowhere.

`logging.basicConfig` writes to stderr and a PyInstaller `--windowed` build has
no console attached, so a crash left the user with nothing at all to report —
which is what happened when the first Skia-default build closed itself on
Render.

The load-bearing part is `faulthandler`: a native access violation inside Skia,
Qt or an ONNX runtime is not a Python exception, so nothing else here would
record it. Its output also names the loaded extension modules, which is what
identifies the faulting library.
"""

import logging
import subprocess
import sys
from pathlib import Path

from app import crash_log

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_install_writes_a_log_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    path = crash_log.install(logging.INFO)

    assert path is not None
    logging.getLogger("test").info("a line that must reach the file")

    for handler in logging.getLogger().handlers:
        handler.flush()
    assert "a line that must reach the file" in Path(path).read_text(encoding="utf-8")


def test_a_native_crash_is_recorded(tmp_path):
    """The reason this module exists: a segfault must leave a stack behind.

    Run in a child process because the crash is real — `ctypes.string_at(0)`
    dereferences a null pointer, which no `except` can catch.
    """
    home = tmp_path / "home"
    home.mkdir()
    # The path is derived in the parent rather than printed by the child:
    # stdout is buffered, and a segfault discards whatever has not been
    # flushed — which is the whole reason faulthandler writes unbuffered.
    log_file = (
        home / "ComicTranslate" / crash_log.LOG_DIR_NAME / crash_log.LOG_FILE_NAME
    )
    script = (
        "import logging, sys, ctypes\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from app import crash_log\n"
        "crash_log.install(logging.INFO)\n"
        "ctypes.string_at(0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        env={
            "PATH": __import__("os").environ.get("PATH", ""),
            "XDG_DATA_HOME": str(home),
            "HOME": str(home),
            "QT_QPA_PLATFORM": "offscreen",
        },
    )

    assert result.returncode != 0, "the process was supposed to crash"
    assert log_file.exists(), f"the crash log was never created at {log_file}"

    contents = log_file.read_text(encoding="utf-8", errors="replace")
    assert "Fatal Python error" in contents, (
        "faulthandler did not record the native crash — a frozen build would "
        f"again leave no trace. Log said:\n{contents[-2000:]}"
    )
    assert "Extension modules" in contents, (
        "the loaded-module list is missing, which is what names the faulting "
        "library in a native crash"
    )
