"""Shared setup for the suite.

Two things have to happen before anything else imports Qt or the app:

- Qt must be told there is no display, or constructing a QApplication aborts
  the whole run on a headless machine.
- The XDG directories must point somewhere disposable. The app resolves its
  glossaries, settings and downloaded models through them (see
  modules/utils/paths.py), so without this a test run would read — and write —
  the real user's data.
"""

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
