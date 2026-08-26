"""The Phase 0 gate, as a test.

`scripts/check_headless.py` imports every module under `pipeline/` and
`modules/` in a child interpreter with PySide6 blocked. Running it here means a
change that reintroduces a Qt import into the pipeline fails the suite rather
than being noticed months later by whoever next tries to run it headless.

It is one test rather than one per module because the work is a single
subprocess; the failure output names every module that broke.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE = REPO_ROOT / "scripts" / "check_headless.py"


def test_pipeline_and_modules_import_without_qt():
    proc = subprocess.run(
        [sys.executable, str(GATE)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, (
        "pipeline/ or modules/ gained a Qt dependency:\n\n"
        f"{proc.stderr}\n{proc.stdout}"
    )


def test_core_does_not_import_qt_at_module_scope():
    """`core/` sits underneath both layers, so it is held to the same rule.

    Deferred imports inside a function are allowed and used deliberately —
    core.i18n reaches for QCoreApplication at call time — so this checks the
    module scope only, by importing with Qt blocked.
    """
    proc = subprocess.run(
        [sys.executable, str(GATE), "core"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"core/ gained a Qt import:\n\n{proc.stderr}"
