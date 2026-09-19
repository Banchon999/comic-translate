"""The exported PSD is read back with a different library than wrote it.

PhotoshopAPI writes the file; psd-tools parses it. Asserting with the same code
that produced the bytes would only prove the writer agrees with itself — and
the two bugs this covers were both invisible that way: the merged image section
came out black, and a frozen build died before writing anything at all.
"""

import os
import struct

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


# --- oversized pages must be written as PSB, not an invalid version-1 PSD ----
#
# A webtoon long-strip is easily taller than 30,000 px. The PSD format stores
# its dimensions in fields that cannot express that, so PhotoshopAPI writes an
# out-of-spec version-1 file that older Photoshop refuses with "not compatible
# with this version". The fix is to write PSB (header version 2) instead. A
# tall, thin strip keeps the test's memory footprint small while still crossing
# the limit.

def _read_psd_version(path: str) -> int:
    with open(path, "rb") as handle:
        header = handle.read(6)
    assert header[:4] == b"8BPS", "not a PSD/PSB file"
    return struct.unpack(">H", header[4:6])[0]


def _a_tall_page(height: int, width: int = 100):
    art = np.full((height, width, 3), 210, dtype=np.uint8)
    # A dark band somewhere in the middle so the preview is provably not black.
    art[height // 2: height // 2 + 40, 10:90] = 0
    return psd_exporter.PsdPageData(
        file_path="strip.png",
        rgb_image=art,
        viewer_state={"text_items_state": []},
        patches=[],
    )


def test_a_normal_page_stays_a_version_1_psd(sandbox_dir):
    path = psd_exporter.export_psd_pages(str(sandbox_dir), [a_page()], "bundle")
    assert path.endswith(".psd")
    assert _read_psd_version(path) == 1


def test_an_oversized_page_is_written_as_a_version_2_psb(sandbox_dir):
    page = _a_tall_page(30001)
    path = psd_exporter.export_psd_pages(str(sandbox_dir), [page], "bundle")

    assert path.endswith(".psb")
    assert _read_psd_version(path) == 2
    # Parse it back with the independent reader: a valid PSB, right size, and
    # the three groups still present.
    psd = psd_tools.PSDImage.open(path)
    assert (psd.width, psd.height) == (100, 30001)
    assert [layer.name for layer in psd] == ["Raw Image", "Inpaint Patches", "Editable Text"]


def _packbits_decode(data: bytes, expected_len: int) -> tuple[bytes, int]:
    """Decode one PackBits row; return the bytes and how many were consumed.

    A deliberately independent implementation — the exporter's own encoder is
    not imported here, so this cannot agree with a bug by sharing its code.
    """
    out = bytearray()
    i = 0
    while len(out) < expected_len:
        n = data[i]
        i += 1
        if n < 128:
            count = n + 1
            out += data[i:i + count]
            i += count
        elif n > 128:
            count = 257 - n
            out += bytes([data[i]]) * count
            i += 1
        # n == 128 is a no-op
    return bytes(out), i


def _read_merged_preview(path: str) -> np.ndarray:
    """Walk a PSD/PSB to its merged-image section using the spec's section-length
    widths (8 bytes for PSB's layer-and-mask section) and decode the composite.

    psd-tools refuses to composite an image past its own 30,000 px cap, so it
    cannot verify an oversized PSB's preview. This reads it directly. If the
    exporter had walked the sections with PSD-width lengths on a PSB, the
    composite would sit at a different offset and this correctly-walked read
    would instead land on PhotoshopAPI's untouched (black) merged section.
    """
    with open(path, "rb") as handle:
        blob = handle.read()
    assert blob[:4] == b"8BPS"
    version = struct.unpack(">H", blob[4:6])[0]
    channels, height, width, _depth, _mode = struct.unpack(">H I I H H", blob[12:26])

    layer_fmt, layer_size = (">Q", 8) if version == 2 else (">I", 4)
    offset = 26
    for fmt, size in [(">I", 4), (">I", 4), (layer_fmt, layer_size)]:
        section_length = struct.unpack(fmt, blob[offset:offset + size])[0]
        offset += size + section_length

    compression = struct.unpack(">H", blob[offset:offset + 2])[0]
    offset += 2
    assert compression == 1, f"expected an RLE merged image, got compression {compression}"

    count_char = "I" if version == 2 else "H"
    count_size = 4 if version == 2 else 2
    n_counts = channels * height
    counts = struct.unpack(f">{n_counts}{count_char}", blob[offset:offset + n_counts * count_size])
    offset += n_counts * count_size

    planes = []
    for c in range(channels):
        plane = bytearray()
        for r in range(height):
            row_len = counts[c * height + r]
            row, _ = _packbits_decode(blob[offset:offset + row_len], width)
            plane += row
            offset += row_len
        planes.append(np.frombuffer(bytes(plane), dtype=np.uint8).reshape(height, width))
    return np.stack(planes[:3], axis=-1)


def test_the_psb_flattened_preview_is_not_a_black_rectangle(sandbox_dir):
    """The merged-image section is written after a version-aware walk over the
    file's sections — PSB's layer-and-mask length is 8 bytes, not 4. Get that
    wrong and the composite lands in the wrong place and reads back black."""
    page = _a_tall_page(30001)
    path = psd_exporter.export_psd_pages(str(sandbox_dir), [page], "bundle")
    preview = _read_merged_preview(path)
    assert preview.shape == (30001, 100, 3)
    assert preview.mean() > 32, "PSB preview is (nearly) black — merged image misplaced"
    # The dark band drawn across the middle of the strip must be present.
    band = preview[15020:15040, 10:90].mean()
    assert band < 64, "the page content is missing from the PSB preview"


def test_a_user_chosen_psd_name_is_switched_to_psb_when_oversized(sandbox_dir):
    """The single-file save path lets the user name the file. An oversized page
    overrides the .psd they picked, and the real .psb path is reported back so
    the caller can tell the user where it landed."""
    chosen = str(sandbox_dir / "my_export.psd")
    path = psd_exporter.export_psd_pages(
        str(sandbox_dir), [_a_tall_page(30001)], "bundle", single_file_path=chosen
    )
    assert path == str(sandbox_dir / "my_export.psb")
    assert os.path.isfile(path)
    assert not os.path.isfile(chosen)


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


def _additional_layer_blocks(path, key):
    """Every occurrence of one additional-layer-information block, as raw bytes.

    psd-tools does not surface fill opacity at all, and neither does
    PhotoshopAPI's own reader, so the only way to assert on it is to find the
    block in the file.
    """
    import struct

    data = open(path, "rb").read()
    found = []
    position = 0
    needle = b"8BIM" + key
    while True:
        position = data.find(needle, position)
        if position < 0:
            return found
        length = struct.unpack(">I", data[position + 8 : position + 12])[0]
        found.append(data[position + 12 : position + 12 + length])
        position += 12


def test_layers_are_not_written_with_zero_fill_opacity(exported):
    """The bug that made an exported page open as an empty canvas.

    PhotoshopAPI's `fill` defaults to 0.0 and it writes that into each layer's
    `iOpa` block. Every other thing a parser can check is correct — document
    size, group and layer names, bounds, pixels, alpha — and the page draws
    nothing at all. Photopea shows the right thumbnail next to each layer, an
    empty checkerboard canvas, and "Fill: 0%".

    Nothing that reads channel data notices, which is why every assertion
    above stayed green while the export was unusable.

    The two zeroes this tolerates are the hidden "</Layer group>" divider
    records Photoshop uses to close a group. They have no content to fill.
    """
    _, path = exported

    fills = [block[0] for block in _additional_layer_blocks(path, b"iOpa") if block]
    assert fills, "no iOpa blocks at all — the file layout is not what we think"

    content_fills = [value for value in fills if value != 0]
    assert content_fills, (
        f"every layer has fill opacity 0 ({fills}) — the page opens blank in "
        f"any renderer, however correct its pixels are"
    )
    assert all(value == 255 for value in content_fills), (
        f"a content layer is partly transparent by fill: {fills}"
    )

    # The dividers are the only layers allowed to be zero, and there is one per
    # group, so a third zero means a real layer slipped through.
    assert fills.count(0) <= 2, f"more zero-fill layers than group dividers: {fills}"


def test_layer_bounds_match_what_was_exported(exported):
    """A layer at the wrong rect renders in the wrong place, or nowhere."""
    from psd_tools.psd import PSD

    _, path = exported
    with open(path, "rb") as handle:
        raw = PSD.read(handle)

    by_name = {
        record.name: (record.left, record.top, record.right, record.bottom)
        for record in raw.layer_and_mask_information.layer_info.layer_records
    }

    assert by_name["Raw Image"] == (0, 0, WIDTH, HEIGHT)
    # a_page() puts the patch at (20, 40) and makes it 70x30.
    assert by_name["Patch 1"] == (20, 40, 90, 70)


def test_no_pixel_layer_is_fully_transparent(exported):
    """Alpha zeroed everywhere is invisible however right the colour data is."""
    psd, _ = exported
    for layer in psd.descendants():
        pixels = layer.numpy()
        if pixels is None or pixels.size == 0 or pixels.shape[2] < 4:
            continue
        assert pixels[:, :, 3].max() > 0, f"{layer.name!r} is fully transparent"


# Everything above exports a page with `text_items_state: []`, so until this
# test nothing in CI had ever written a *text* layer to disk — and the text
# path is the one that reaches PhotoshopAPI's type-layer code and Qt's font
# metrics. Writing one is not a small extra: `_apply_editor_style` measures
# line height with `QFontMetricsF`, which **aborts the process** rather than
# raising if it is reached without a QApplication, so a fault in here does not
# surface as a failing test but as a dead interpreter and a 0-byte file.
def a_page_with_text():
    art = np.full((HEIGHT, WIDTH, 3), 210, dtype=np.uint8)
    return psd_exporter.PsdPageData(
        file_path="002.png",
        rgb_image=art,
        viewer_state={
            "text_items_state": [
                {
                    "text": "<p>HELLO WORLD</p>",
                    "font_family": "Arial",
                    "font_size": 20.0,
                    "text_color": "#101010",
                    "position": (20.0, 20.0),
                    "width": 140.0,
                    "height": 30.0,
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


@pytest.fixture
def exported_with_text(sandbox_dir, qapp):
    path = psd_exporter.export_psd_pages(str(sandbox_dir), [a_page_with_text()], "text")
    return psd_tools.PSDImage.open(path), path


def test_a_page_with_text_survives_the_write(exported_with_text):
    """The guard against the export dying partway through.

    A native abort inside PhotoshopAPI or Qt leaves a file that exists and is
    empty, which is what a user reports as "the app closed itself and the PSD
    is 0 bytes". Reading the result back with a different library is the only
    way to tell that apart from a successful write.
    """
    psd, path = exported_with_text
    assert (psd.width, psd.height) == (WIDTH, HEIGHT)
    assert os.path.getsize(path) > 0


def test_the_text_layer_carries_the_string_it_was_given(exported_with_text):
    psd, _ = exported_with_text
    group = next(layer for layer in psd if layer.name == "Editable Text")
    text_layers = [layer for layer in group if layer.kind == "type"]

    assert len(text_layers) == 1
    assert text_layers[0].text.strip() == "HELLO WORLD"
