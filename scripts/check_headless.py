"""Phase 0 gate: import the whole pipeline with Qt made unavailable.

`import pipeline` on its own proves nothing — `pipeline/__init__.py` is empty,
so the import succeeds even while every module inside it needs PySide6. This
walks the real surface instead: every module under `pipeline/` and `modules/`,
imported with a meta-path hook that makes PySide6 unimportable, so the check
holds in a normal development environment where Qt *is* installed.

    python scripts/check_headless.py            # gate the default packages
    python scripts/check_headless.py --list     # print what it would import
    python scripts/check_headless.py modules.ocr  # gate a subtree

Exit status is 0 only when every module imported cleanly.
"""

from __future__ import annotations

import argparse
import importlib
import json
import pkgutil
import subprocess
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The packages that must stand up without Qt. `app/` is deliberately absent:
# it is the Qt layer and is expected to need Qt.
GATED_PACKAGES = ("pipeline", "modules")

BLOCKED_ROOTS = ("PySide6", "shiboken6")


def iter_modules(package_names):
    """Yield every importable module name under the given packages."""
    for package_name in package_names:
        yield package_name
        package = importlib.import_module(package_name)
        prefix = package.__name__ + "."
        for _, name, _ in pkgutil.walk_packages(package.__path__, prefix):
            yield name


CHILD_SOURCE = """
import importlib, json, sys, traceback
sys.path.insert(0, {repo_root!r})
BLOCKED_ROOTS = {blocked!r}

class QtBlocker:
    def find_module(self, fullname, path=None):
        self.find_spec(fullname, path)
        return None

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in BLOCKED_ROOTS:
            raise ImportError(
                fullname + " is blocked by the headless gate "
                "(scripts/check_headless.py). A module under the gated "
                "packages must not import Qt."
            )
        return None

sys.meta_path.insert(0, QtBlocker())

names = json.loads(sys.stdin.read())
failures = []
for name in names:
    try:
        importlib.import_module(name)
    except BaseException:
        failures.append([name, traceback.format_exc()])
sys.stdout.write(json.dumps(failures))
"""


def run_imports_in_child(names):
    """Import `names` in a fresh interpreter with Qt blocked from the start."""
    source = CHILD_SOURCE.format(repo_root=str(REPO_ROOT), blocked=BLOCKED_ROOTS)
    proc = subprocess.run(
        [sys.executable, "-c", source],
        input=json.dumps(names),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"headless gate child exited {proc.returncode}:\n{proc.stderr}"
        )
    return [tuple(entry) for entry in json.loads(proc.stdout)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "packages", nargs="*", default=None,
        help=f"packages to gate (default: {' '.join(GATED_PACKAGES)})",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="print the module list without importing under the blocker",
    )
    args = parser.parse_args(argv)
    packages = args.packages or list(GATED_PACKAGES)

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    # Walk first, with Qt still available: pkgutil has to import each package
    # to read its __path__, and a package whose __init__ needs Qt would abort
    # the walk before it produced a single name to report on.
    names = sorted(set(iter_modules(packages)))

    if args.list:
        print("\n".join(names))
        return 0

    # The import phase runs in a child process. Doing it here would be wrong:
    # the walk above has already imported Qt and the gated packages, and a
    # blocker cannot un-cache them — `from PySide6.QtCore import Qt` would hit
    # sys.modules and succeed, passing the gate while nothing was tested.
    failures = run_imports_in_child(names)

    if failures:
        for name, tb in failures:
            print(f"\n=== {name} ===", file=sys.stderr)
            print(tb.rstrip(), file=sys.stderr)
        print(
            f"\nheadless gate FAILED: {len(failures)} of {len(names)} modules "
            f"could not be imported without Qt",
            file=sys.stderr,
        )
        return 1

    print(f"headless gate OK: {len(names)} modules imported without Qt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
