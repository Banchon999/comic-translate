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


# ---------------------------------------------------------------------------
# The layers and the merged image both have to carry pixels.
#
# The tests above check the document size, the group names and order, that a
# patch became a layer, and that the flattened preview is not black. None of
# that would notice every *layer* being empty — the preview is written
# separately by _write_flattened_preview, so a file can preview correctly over
# transparent layers. A user reported exactly that shape: three groups listed
# in Photopea, whole canvas transparent.
#
# The merged image matters as much as the layers and for the opposite reason:
# Photoshop re-composites from layers, and almost nothing else does. A file
# whose layers are fine still opens blank in Photopea, Krita, Preview and every
# web viewer if that section is wrong.
# ---------------------------------------------------------------------------


def _layer_named(psd, name):
    for layer in psd.descendants():
        if layer.name == name:
            return layer
    raise AssertionError(f"no layer named {name!r}")


def test_the_raw_image_layer_carries_the_page_pixels(exported):
    """The base art must be *in the layer*, not only in the preview."""
    psd, _ = exported
    raw = _layer_named(psd, "Raw Image")

    pixels = raw.numpy()
    assert pixels is not None, "the Raw Image layer has no pixel data at all"
    assert pixels.shape[:2] == (HEIGHT, WIDTH)
    assert pixels.size and pixels.max() > 0, (
        "the Raw Image layer is entirely zero — the page would open transparent "
        "in anything that composites from layers"
    )

    if pixels.shape[2] == 4:
        alpha = pixels[:, :, 3]
        assert alpha.max() > 0, (
            "the Raw Image layer is fully transparent — its alpha is zero "
            "everywhere, so nothing it contains is ever visible"
        )


def test_the_patch_layer_carries_pixels_too(exported):
    psd, _ = exported
    patch = _layer_named(psd, "Patch 1")

    pixels = patch.numpy()
    assert pixels is not None and pixels.size, "the patch layer has no pixel data"
    assert pixels.max() > 0, "the patch layer is entirely zero"


def test_the_merged_image_is_what_a_non_photoshop_viewer_will_show(exported):
    """Everything except Photoshop reads this section rather than the layers."""
    psd, _ = exported
    merged = psd.topil()

    assert merged is not None, (
        "the PSD has no merged image — every viewer but Photoshop shows nothing"
    )
    array = np.array(merged)
    assert array.shape[:2] == (HEIGHT, WIDTH)

    if array.ndim == 3 and array.shape[2] == 4:
        assert array[:, :, 3].max() > 0, (
            "the merged image is fully transparent — the file opens blank "
            "everywhere except Photoshop"
        )

    rgb = array[:, :, :3] if array.ndim == 3 else array
    assert rgb.max() > rgb.min(), (
        "the merged image is a single flat colour, not the page"
    )


def test_layer_pixels_use_a_compression_every_reader_implements(exported):
    """PhotoshopAPI defaults to ZipPrediction; almost nothing but Photoshop reads it.

    This is what made the export open as a blank canvas: the document size and
    every group and layer name parse fine, because those live in the layer
    *records*, and then each channel's pixels turn out to be Zip-compressed and
    a reader without Zip support recovers nothing from them. Photoshop itself
    writes RLE.

    Read straight out of the bytes — psd-tools decodes Zip perfectly well, so
    the assertions above stay green either way, which is exactly why they did
    not catch this.
    """
    from psd_tools.psd import PSD
    from psd_tools.constants import Compression

    _, path = exported
    with open(path, "rb") as handle:
        raw = PSD.read(handle)

    layer_info = raw.layer_and_mask_information.layer_info
    readable = {Compression.RAW, Compression.RLE}

    checked = 0
    for record, channels in zip(layer_info.layer_records, layer_info.channel_image_data):
        if record.right - record.left <= 0 or record.bottom - record.top <= 0:
            continue  # a group divider or a text layer: no pixels to compress
        checked += 1
        used = {channel.compression for channel in channels}
        assert used <= readable, (
            f"layer {record.name!r} stores its pixels as {used} — a reader "
            f"without Zip support decodes nothing and the layer appears empty"
        )

    assert checked >= 2, "expected the Raw Image and patch layers to have pixels"
