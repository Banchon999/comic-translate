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


def _a_layered_page():
    """A page the user edited in the Layers panel: one patch hidden, one at
    half opacity. Returned with the page ComicTranslate itself flattens from
    the same data, which is what Photopea's composite has to match."""
    from app.ui.canvas.save_renderer import ImageSaveRenderer

    art = np.full((HEIGHT, WIDTH, 3), 225, dtype=np.uint8)
    art[60:180, 40:280] = (40, 70, 190)
    black = np.zeros((60, 80, 3), dtype=np.uint8)
    patches = [
        {"bbox": (20, 20, 80, 60), "image": black, "hash": "a", "object_id": "A",
         "layer": {"visible": False, "name": "Cleaned SFX"}},
        {"bbox": (120, 90, 80, 60), "image": black, "hash": "b", "object_id": "B",
         "layer": {"opacity": 0.5, "name": "Half"}},
    ]
    page = psd_exporter.PsdPageData(
        file_path="003.png", rgb_image=art,
        viewer_state={"text_items_state": []}, patches=patches,
    )
    renderer = ImageSaveRenderer(art.copy())
    renderer.apply_patches(patches)
    renderer.add_state_to_image({"text_items_state": []})
    return page, renderer.render_to_image()


def test_photopea_keeps_hidden_layers_hidden_and_honours_opacity(tmp_path, sandbox_dir, qapp):
    """A hidden layer must arrive hidden, still carry its pixels (so it can be
    shown again), and stay out of the composite; opacity must blend.

    The composite is compared with ComicTranslate's own flattened render of the
    same page. That comparison is also what caught the harness itself making
    every layer visible again after soloing them, which drew the hidden patch
    into the composite and blamed the export for it.
    """
    from PIL import Image

    page, flat = _a_layered_page()
    psd = Path(psd_exporter.export_psd_pages(str(sandbox_dir), [page], "layers"))
    reference = tmp_path / "flat.png"
    Image.fromarray(flat).save(reference)

    out = tmp_path / "report-layers"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(psd), "--out", str(out),
         "--compare", str(reference), "--expect-hidden", "Cleaned SFX"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )

    if result.returncode == 2:
        pytest.skip(f"the harness could not start: {result.stderr.strip()[:200]}")

    if result.returncode != 0 and out.exists():
        shutil.copytree(out, tmp_path / "failed-report-layers", dirs_exist_ok=True)

    assert result.returncode == 0, (
        "Photopea did not render the layered PSD as ComicTranslate does:\n"
        + result.stdout + result.stderr
    )
    assert "layer arrives hidden as intended: 'Cleaned SFX'" in result.stdout
    assert "layer renders its own pixels: 'Cleaned SFX'" in result.stdout


def _a_page_with_text():
    art = np.full((HEIGHT, WIDTH, 3), 225, dtype=np.uint8)
    art[150:220, 40:280] = (40, 70, 190)
    return psd_exporter.PsdPageData(
        file_path="002.png",
        rgb_image=art,
        viewer_state={
            "text_items_state": [
                {
                    "text": "HELLO WORLD",
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
                }
            ]
        },
        patches=[],
    )


def test_photopea_renders_the_translated_text(tmp_path, sandbox_dir, qapp):
    """Translated text is on the page when the PSD opens in Photopea.

    It was not, for two independent reasons, both in how PhotoshopAPI writes a
    type layer. (1) No pixels: bounds (0,0,0,0) and no colour channels, while
    Photopea draws a type layer from its cached raster — so the page opened
    with the artwork and patches and none of the translation, the layer still
    listed and its text still readable. The exporter now writes the item's own
    render as that raster. (2) TySh declared at an odd length with its padding
    byte uncounted, which Photopea reads as the next block's signature: an
    "Error in PSD file: wrong signature" toast on every page with text, which
    the harness now fails on. The composite is compared with the app's own
    flattened render of the page, so the text is not merely present but where
    and how the app drew it.

    `qapp` is required, not incidental: `_apply_editor_style` measures line
    height with `QFontMetricsF`, and Qt **aborts the process** rather than
    raising when that is reached with no QApplication. Written without the
    fixture, this test killed the whole pytest run — no traceback, no failure,
    just SIGABRT. Every other PSD test passes `text_items_state: []`, so
    nothing in the suite had ever exported a text layer before this.
    """
    page = _a_page_with_text()
    psd = Path(psd_exporter.export_psd_pages(str(sandbox_dir), [page], "text"))
    reference = tmp_path / "flat.png"
    _save_flat(page, reference)

    out = tmp_path / "report-text"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(psd), "--out", str(out), "--compare", str(reference)],
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


def _save_flat(page, path):
    """ComicTranslate's own flattened render of a page, as a PNG."""
    from PIL import Image

    from app.ui.canvas.save_renderer import ImageSaveRenderer

    renderer = ImageSaveRenderer(page.rgb_image.copy(), page.document_layers)
    renderer.apply_patches(page.patches)
    renderer.add_state_to_image(page.viewer_state)
    Image.fromarray(renderer.render_to_image()).save(path)


def _text(text, x, y, **extra):
    state = {
        "text": text, "font_family": "Arial", "font_size": 22.0,
        "text_color": "#000000", "position": (x, y), "width": 200.0,
        "height": 40.0, "alignment": 1, "line_spacing": 1.0,
        "outline_color": "#FFFFFF", "outline_width": 0.0, "bold": False,
        "italic": False, "underline": False, "rotation": 0.0, "scale": 1.0,
        "transform_origin": (0.0, 0.0), "selection_outlines": [],
    }
    state.update(extra)
    return state


def test_photopea_draws_each_text_layer_in_its_own_place(tmp_path, sandbox_dir, qapp):
    """Several text layers, one reordered, one hidden, one at half opacity: each
    raster has to land on its own layer, the hidden one listed but not drawn,
    and the composite has to match the app's flattened render."""
    art = np.full((HEIGHT, WIDTH, 3), 225, dtype=np.uint8)
    art[150:220, 40:280] = (40, 70, 190)
    page = psd_exporter.PsdPageData(
        file_path="004.png", rgb_image=art,
        viewer_state={"text_items_state": [
            _text("FIRST", 20.0, 10.0),
            _text("SECOND", 20.0, 60.0, layer={"z": 3.0}),
            _text("HIDDEN", 20.0, 110.0, layer={"visible": False, "name": "Hidden note"}),
            _text("FADED", 60.0, 165.0, text_color="#FFFFFF", layer={"opacity": 0.5}),
        ]},
        patches=[],
    )
    psd = Path(psd_exporter.export_psd_pages(str(sandbox_dir), [page], "several"))
    reference = tmp_path / "flat.png"
    _save_flat(page, reference)

    out = tmp_path / "report-several"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(psd), "--out", str(out), "--compare", str(reference),
         "--expect-hidden", "Hidden note"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )

    if result.returncode == 2:
        pytest.skip(f"the harness could not start: {result.stderr.strip()[:200]}")

    if result.returncode != 0 and out.exists():
        shutil.copytree(out, tmp_path / "failed-report-several", dirs_exist_ok=True)

    assert result.returncode == 0, (
        "Photopea did not render the text layers as ComicTranslate does:\n" + result.stdout + result.stderr
    )
    for name in ("Text 1", "Text 2", "Hidden note", "Text 4"):
        assert f"layer renders its own pixels: {name!r}" in result.stdout, name
