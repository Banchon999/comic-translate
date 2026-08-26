"""Open an exported PSD in a real editor and check it renders.

Everything in `test_psd_export.py` reads the file with a parsing library, and a
parsing library cannot tell you whether a page *draws*. The bug that made an
export open as an empty canvas — every layer written with fill opacity 0 — was
invisible to all of it: psd-tools returns the pixels regardless, so document
size, layer names, bounds, channel data and alpha all read as correct while the
page showed nothing.

This runs `scripts/check_psd_in_photopea.py`, which drives Photopea in a
headless Chromium and asks it what it actually renders.

Marked `photopea` and deselected by default: it needs a browser and reaches a
third-party website. Run it deliberately:

    pytest -m photopea
"""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from app.controllers import psd_exporter

pytestmark = [
    pytest.mark.photopea,
    pytest.mark.skipif(
        not psd_exporter.psd_support_available(),
        reason="PhotoshopAPI has no wheel for this platform",
    ),
    pytest.mark.skipif(
        importlib.util.find_spec("playwright") is None,
        reason="playwright is not installed (see requirements-dev.txt)",
    ),
]

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_psd_in_photopea.py"

WIDTH, HEIGHT = 320, 240


def _a_page():
    art = np.full((HEIGHT, WIDTH, 3), 225, dtype=np.uint8)
    art[60:140, 40:280] = (40, 70, 190)
    patch = np.full((40, 120, 3), 255, dtype=np.uint8)
    return psd_exporter.PsdPageData(
        file_path="001.png",
        rgb_image=art,
        viewer_state={"text_items_state": []},
        patches=[{"bbox": (40, 60, 120, 40), "image": patch}],
    )


def test_photopea_renders_the_exported_page(tmp_path, sandbox_dir):
    psd = Path(psd_exporter.export_psd_pages(str(sandbox_dir), [_a_page()], "bundle"))

    out = tmp_path / "report"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(psd), "--out", str(out)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )

    if result.returncode == 2:
        pytest.skip(f"the harness could not start: {result.stderr.strip()[:200]}")

    # Keep the artefacts findable when this fails — the screenshots are the
    # whole point of checking against a renderer rather than a parser.
    if result.returncode != 0 and out.exists():
        shutil.copytree(out, tmp_path / "failed-report", dirs_exist_ok=True)

    assert result.returncode == 0, (
        "Photopea did not render the exported PSD correctly:\n"
        + result.stdout
        + result.stderr
    )
