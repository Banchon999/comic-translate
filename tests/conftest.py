"""Shared setup for the suite.

Two things have to happen before anything else imports Qt or the app:

- Qt must be told there is no display, or constructing a QApplication aborts
  the whole run on a headless machine.
- The XDG directories must point somewhere disposable. The app resolves its
  glossaries, settings and downloaded models through them (see
  modules/utils/paths.py), so without this a test run would read — and write —
  the real user's data.
"""

import gc
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SANDBOX = tempfile.mkdtemp(prefix="comic-translate-tests-")
os.environ["XDG_DATA_HOME"] = os.path.join(_SANDBOX, "data")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_SANDBOX, "config")
os.environ["XDG_CACHE_HOME"] = os.path.join(_SANDBOX, "cache")
os.environ["HOME"] = os.path.join(_SANDBOX, "home")
for _key in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "HOME"):
    Path(os.environ[_key]).mkdir(parents=True, exist_ok=True)

# No D-Bus secret service under a test runner. The failing backend is what makes
# token storage fall back to QSettings; the null one would swallow writes.
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole run — Qt allows only one per process."""
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.fixture
def sandbox_dir():
    """A fresh empty directory for a test that writes files."""
    with tempfile.TemporaryDirectory() as path:
        yield Path(path)


@pytest.fixture(autouse=True)
def _collect_qt_cycles_on_the_gui_thread():
    """Collect reference cycles after every test, here, on the GUI thread.

    Many Qt objects a test builds sit in Python reference cycles (an
    ImageViewer and its managers hold each other), so nothing frees them when
    the test ends — the cyclic garbage collector does, later, on whichever
    thread happens to trigger it. When that is a worker thread, the C++
    destructor runs there too, and a QObject destroyed off the thread that
    started its timers cannot stop them: the timer stays registered on the GUI
    thread and fires into freed memory at the next processEvents(). That is how
    the suite used to die with SIGSEGV in QTimerInfoList::activateTimers:
    test_render_parity built ImageViewers, a worker thread's garbage
    collection destroyed them, and test_type_text_tool's first processEvents()
    fired their scene's index timer. Traced in gdb: ~QGraphicsView on a
    non-GUI thread, reached from _PyObject_ClearManagedDict. Whether it
    happened depended only on when collection ran, so adding or removing any
    one test file moved it.
    """
    yield
    gc.collect()
