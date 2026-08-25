"""Write diagnostics to a file, because a windowed build has nowhere else.

`logging.basicConfig` sends everything to stderr, and a PyInstaller
`--windowed` build has no console attached, so stderr is discarded. A frozen
app that dies therefore leaves the user with nothing at all to report — which
is exactly what happened with the first Skia-default build: the app closed the
moment Render was pressed and there was no log to look at.

Four sinks, because a crash can arrive through four different doors:

* `faulthandler` — the important one. A native access violation inside Skia,
  Qt or an ONNX runtime is *not* a Python exception and no `except` can catch
  it; the interpreter is already gone. `faulthandler` installs OS-level signal
  handlers that dump the Python stack of every thread as the process dies, and
  it names the C-level frame it died in. Without it a native fault leaves no
  trace whatsoever.
* `sys.excepthook` — an uncaught exception on the main thread.
* `threading.excepthook` — the same on a worker, which the pipeline uses
  heavily and which would otherwise only print to the dead stderr.
* A Qt message handler — Qt's own warnings (`qWarning`, failed plugin loads,
  QPainter misuse) never pass through Python's logging at all.

The log file is opened unbuffered and kept open for the process's lifetime:
`faulthandler` writes to the file descriptor from a signal handler, so it
cannot be given a handle that Python might close or flush lazily.
"""

from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import os
import sys
import threading
from datetime import datetime, timezone
from typing import Optional

#: Kept alive for the life of the process — faulthandler writes to this
#: descriptor from a signal handler, long after normal cleanup would have run.
_crash_stream = None

LOG_DIR_NAME = "logs"
LOG_FILE_NAME = "comic-translate.log"


def log_directory() -> str:
    """Where the log lives, created if need be.

    Under the user data directory rather than beside the executable: on Windows
    a program installed in Program Files cannot write next to itself.
    """
    from modules.utils.paths import get_user_data_dir

    directory = os.path.join(get_user_data_dir(), LOG_DIR_NAME)
    os.makedirs(directory, exist_ok=True)
    return directory


def log_path() -> str:
    return os.path.join(log_directory(), LOG_FILE_NAME)


def install(level: int = logging.INFO) -> Optional[str]:
    """Route logging, uncaught exceptions, Qt messages and native faults to a file.

    Returns the log's path, or None if even opening it failed — in which case
    the app still starts, because losing diagnostics must never be the thing
    that stops it running.
    """
    try:
        path = log_path()
    except Exception:
        logging.basicConfig(level=level)
        return None

    root = logging.getLogger()
    root.setLevel(level)

    try:
        # Rotating so a long-running install cannot fill the disk, and small
        # enough that a user can actually attach one to a bug report.
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
        ))
        root.addHandler(handler)
    except Exception:
        logging.basicConfig(level=level)
        return None

    # Keep stderr too: it costs nothing and a developer running from a terminal
    # still wants output there.
    if sys.stderr is not None:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
        root.addHandler(stream)

    _install_faulthandler(path)
    _install_excepthooks()
    _install_qt_handler()

    logging.getLogger(__name__).info(
        "session started %s | frozen=%s | python=%s | log=%s",
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        is_frozen(), sys.version.split()[0], path,
    )
    return path


def is_frozen() -> bool:
    """True inside a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")


def _install_faulthandler(path: str) -> None:
    global _crash_stream
    try:
        # Appended to and never closed: a signal handler cannot rely on the
        # normal file machinery still being intact when it runs.
        _crash_stream = open(path, "a", buffering=1, encoding="utf-8", errors="replace")
        _crash_stream.write(
            f"\n--- faulthandler armed {datetime.now(timezone.utc).isoformat()} ---\n"
        )
        faulthandler.enable(file=_crash_stream, all_threads=True)
    except Exception:
        logging.getLogger(__name__).warning(
            "could not arm faulthandler; a native crash will leave no trace",
            exc_info=True,
        )


def _install_excepthooks() -> None:
    logger = logging.getLogger("crash")

    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        logger.critical("uncaught exception", exc_info=(exc_type, exc, tb))
        try:
            previous(exc_type, exc, tb)
        except Exception:
            pass

    sys.excepthook = hook

    def thread_hook(args):
        logger.critical(
            "uncaught exception in thread %s",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = thread_hook


def _install_qt_handler() -> None:
    """Send Qt's own messages through logging.

    Qt writes to its own message stream, so a failed plugin load or a QPainter
    complaint never reaches Python's logging and is lost with stderr.
    """
    try:
        from PySide6 import QtCore
    except Exception:
        return

    levels = {
        QtCore.QtMsgType.QtDebugMsg: logging.DEBUG,
        QtCore.QtMsgType.QtInfoMsg: logging.INFO,
        QtCore.QtMsgType.QtWarningMsg: logging.WARNING,
        QtCore.QtMsgType.QtCriticalMsg: logging.ERROR,
        QtCore.QtMsgType.QtFatalMsg: logging.CRITICAL,
    }
    logger = logging.getLogger("qt")

    def handler(mode, context, message):
        logger.log(levels.get(mode, logging.INFO), "%s", message)

    try:
        QtCore.qInstallMessageHandler(handler)
    except Exception:
        pass
