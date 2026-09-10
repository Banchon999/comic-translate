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


def _a_page_with_text():
    art = np.full((HEIGHT, WIDTH, 3), 225, dtype=np.uint8)
    art[150:220, 40:280] = (40, 70, 190)
    return psd_exporter.PsdPageData(
        file_path="002.png",
        rgb_image=art,
        viewer_state={
            "text_items_state": [
                {
                    "text": "<p>HELLO WORLD</p>",
                    "font_family": "Arial",
                    "font_size": 24.0,
                    "text_color": "#000000",
                    "position": (40.0, 30.0),
                    "width": 240.0,
                    "height": 40.0,
                    "alignment": 1,
                    "line_spacing": 1.0,
                    "outline_color": "#FFFFFF",
                    "outline_width": 0.0,
                    "bold": False,
                    "italic": False,
                    "underline": False,
                    "rotation": 0.0,
                    "scale": 1.0,
                    "transform_origin": (0.0, 0.0),
                    "selection_outlines": [],
                    "direction": "ltr",
                }
            ]
        },
        patches=[],
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "PhotoshopAPI writes type layers with bounds (0,0,0,0) and no colour "
        "channels — confirmed by psd-tools on the written file, and by "
        "TextLayer_8bit having no way to supply pixel data at all. Photopea "
        "composites a type layer from its cached raster, so it draws nothing "
        "on load while still reporting the layer present and editable; "
        "writing the same string back through Photopea's own API forces a "
        "re-layout and the glyphs appear (bounds become (46,34,220,53), 1047 "
        "dark pixels). Not a parsing failure: the EngineData does also carry "
        "two empty containers written without whitespace ('<<>>' and '[]') "
        "that psd-tools refuses to tokenise, but byte-patching those to "
        "'<< >>' and '[ ]' leaves Photopea's composite bit-for-bit identical. "
        "Remove this xfail with the fix."
    ),
)
def test_photopea_renders_the_translated_text(tmp_path, sandbox_dir, qapp):
    """The half of the blank-export bug that is still open.

    Fill opacity 0 made every layer invisible and is fixed. Text layers are
    still invisible in Photopea, for an unrelated reason, and a user without
    Photoshop is exactly the user this export is for — so the page they open
    has the artwork and the cleaned patches but none of the translation.

    `qapp` is required, not incidental: `_apply_editor_style` measures line
    height with `QFontMetricsF`, and Qt **aborts the process** rather than
    raising when that is reached with no QApplication. Written without the
    fixture, this test killed the whole pytest run — no traceback, no failure,
    just SIGABRT. Every other PSD test passes `text_items_state: []`, so
    nothing in the suite had ever exported a text layer before this.
    """
    psd = Path(psd_exporter.export_psd_pages(str(sandbox_dir), [_a_page_with_text()], "text"))

    out = tmp_path / "report-text"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(psd), "--out", str(out)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )

    if result.returncode == 2:
        pytest.skip(f"the harness could not start: {result.stderr.strip()[:200]}")

    if result.returncode != 0 and out.exists():
        shutil.copytree(out, tmp_path / "failed-report-text", dirs_exist_ok=True)

    assert result.returncode == 0, (
        "Photopea did not render the exported text:\n" + result.stdout + result.stderr
    )
