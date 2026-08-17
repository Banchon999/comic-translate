"""The exported PSD is read back with a different library than wrote it.

PhotoshopAPI writes the file; psd-tools parses it. Asserting with the same code
that produced the bytes would only prove the writer agrees with itself — and
the two bugs this covers were both invisible that way: the merged image section
came out black, and a frozen build died before writing anything at all.
"""

import numpy as np
import pytest

from app.controllers import psd_exporter

psd_tools = pytest.importorskip("psd_tools", reason="psd-tools is a dev dependency")

pytestmark = pytest.mark.skipif(
    not psd_exporter.psd_support_available(),
    reason="PhotoshopAPI has no wheel for this platform",
)


WIDTH, HEIGHT = 180, 120


def a_page():
    """A page with a black bar of 'lettering' and a patch covering half of it."""
    art = np.full((HEIGHT, WIDTH, 3), 210, dtype=np.uint8)
    art[40:70, 20:160] = 0

    patch = np.full((30, 70, 3), 210, dtype=np.uint8)
    return psd_exporter.PsdPageData(
        file_path="001.png",
        rgb_image=art,
        viewer_state={"text_items_state": []},
        patches=[{"bbox": (20, 40, 70, 30), "image": patch}],
    )


@pytest.fixture
def exported(sandbox_dir):
    path = psd_exporter.export_psd_pages(str(sandbox_dir), [a_page()], "bundle")
    return psd_tools.PSDImage.open(path), path


def test_the_file_parses_at_all(exported):
    psd, _ = exported
    assert (psd.width, psd.height) == (WIDTH, HEIGHT)


def test_the_three_groups_are_present_in_stacking_order(exported):
    psd, _ = exported
    # psd-tools iterates bottom-up; the exporter's intended visual order is
    # Editable Text over Inpaint Patches over Raw Image.
    names = [layer.name for layer in psd]
    assert names == ["Raw Image", "Inpaint Patches", "Editable Text"]


def test_layer_names_have_no_trailing_terminator(exported):
    """PhotoshopAPI appends a NUL to every Unicode layer name."""
    psd, _ = exported
    for layer in psd.descendants():
        assert layer.name == layer.name.rstrip("\x00")
        assert "\x00" not in layer.name


def test_the_patch_became_a_layer_inside_its_group(exported):
    psd, _ = exported
    patches = next(layer for layer in psd if layer.name == "Inpaint Patches")
    assert len(list(patches)) == 1


def test_the_flattened_preview_is_the_page_and_not_a_black_rectangle(exported):
    """Photoshop shows this section; PhotoshopAPI leaves it black on its own."""
    psd, _ = exported
    preview = np.asarray(psd.topil().convert("RGB"))
    assert preview.shape == (HEIGHT, WIDTH, 3)
    assert preview.mean() > 32, "preview is (nearly) black — the merged image was not written"
    # The patch should have covered the left half of the black bar.
    covered = preview[45:65, 30:80].mean()
    uncovered = preview[45:65, 100:150].mean()
    assert covered > uncovered + 50


def test_a_page_with_no_patches_still_exports(sandbox_dir):
    page = a_page()
    page.patches = []
    path = psd_exporter.export_psd_pages(str(sandbox_dir), [page], "bundle")
    psd = psd_tools.PSDImage.open(path)
    assert [layer.name for layer in psd] == ["Raw Image", "Inpaint Patches", "Editable Text"]


def test_several_pages_land_in_the_output_folder(sandbox_dir):
    pages = [a_page(), a_page()]
    pages[1].file_path = "002.png"
    out = psd_exporter.export_psd_pages(str(sandbox_dir), pages, "bundle")
    written = sorted(p.name for p in sandbox_dir.glob("*.psd"))
    assert written == ["001.psd", "002.psd"]
    assert out == str(sandbox_dir)


def test_exporting_nothing_is_an_error(sandbox_dir):
    with pytest.raises(ValueError):
        psd_exporter.export_psd_pages(str(sandbox_dir), [], "bundle")


def test_the_numpy_shim_pybind11_needs_is_importable():
    """Guards the frozen-build crash: PhotoshopAPI imports this lazily.

    pybind11 <= 2.11.1 reaches for numpy.core.multiarray on its first array
    conversion. Nothing else imports it, so a bundler drops it and the first
    ImageLayer_8bit call dies with ModuleNotFoundError.
    """
    import sys

    assert "numpy.core.multiarray" in sys.modules
