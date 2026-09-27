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


# --- type layers carry their pixels -------------------------------------------------
#
# PhotoshopAPI writes a type layer with no channels and bounds (0,0,0,0).
# Photoshop re-runs its text engine on open, so that looks fine there; Photopea
# draws a type layer from its cached raster and so drew *nothing* for it, while
# listing the layer and reading its text back. The exporter now writes the
# item's own render into each type layer's record (_finish_type_layers).


def _text_state(text, x, y, layer=None, width=140.0, height=40.0):
    state = {
        "text": text, "font_family": "Arial", "font_size": 20.0,
        "text_color": "#101010", "position": (x, y), "width": width,
        "height": height, "alignment": 1, "line_spacing": 1.0,
        "outline_color": "#FFFFFF", "outline_width": 0.0, "bold": False,
        "italic": False, "underline": False, "rotation": 0.0, "scale": 1.0,
        "transform_origin": (0.0, 0.0), "selection_outlines": [],
    }
    if layer:
        state["layer"] = layer
    return state


def _text_page(states, width=WIDTH, height=HEIGHT, name="003.png"):
    art = np.full((height, width, 3), 210, dtype=np.uint8)
    return psd_exporter.PsdPageData(
        file_path=name, rgb_image=art,
        viewer_state={"text_items_state": states}, patches=[],
    )


def _type_layers(psd):
    return {layer.name: layer for layer in psd.descendants() if layer.kind == "type"}


def test_the_text_layer_carries_its_own_pixels(exported_with_text):
    """Still a type layer (editable text), now also with pixels to draw."""
    psd, _ = exported_with_text
    (layer,) = _type_layers(psd).values()
    assert layer.text.strip() == "HELLO WORLD"
    left, top, right, bottom = layer.bbox
    assert right > left and bottom > top, f"type layer has an empty box {layer.bbox}"
    pixels = layer.numpy()
    assert pixels is not None and pixels.shape[2] == 4
    assert (pixels[:, :, 3] > 0).sum() > 50, "type layer has no opaque pixels"
    # The fixture puts the box at (20, 20), 140 wide.
    assert 20 <= left and right <= 160 + 2, layer.bbox


def test_type_layer_pixels_are_what_the_flattened_page_shows(sandbox_dir, qapp):
    """The raster is the same render the flattened export draws, so a reader
    showing the raster shows what the app showed."""
    from app.ui.canvas.save_renderer import ImageSaveRenderer

    page = _text_page([_text_state("HELLO", 20.0, 30.0)])
    path = psd_exporter.export_psd_pages(str(sandbox_dir), [page], "flat")
    (layer,) = _type_layers(psd_tools.PSDImage.open(path)).values()
    left, top, right, bottom = layer.bbox
    rgba = layer.numpy()  # floats 0..1

    alpha = rgba[:, :, 3:4]
    art = page.rgb_image[top:bottom, left:right].astype(float) / 255.0
    composed = rgba[:, :, :3] * alpha + art * (1 - alpha)

    renderer = ImageSaveRenderer(page.rgb_image.copy())
    renderer.add_state_to_image(page.viewer_state)
    flat = renderer.render_to_image()[top:bottom, left:right].astype(float) / 255.0

    assert np.abs(composed - flat).mean() < 2 / 255, "the layer's pixels differ from the flattened page"


def test_each_text_layer_gets_its_own_pixels(sandbox_dir, qapp):
    """Records are matched to text items by order and confirmed by content: a
    raster on the wrong layer would draw one item's text where another is."""
    page = _text_page([
        _text_state("TOP", 10.0, 5.0),
        _text_state("BOTTOM", 10.0, 70.0, layer={"z": 5.0}),  # reordered to the top
    ])
    path = psd_exporter.export_psd_pages(str(sandbox_dir), [page], "two")
    layers = _type_layers(psd_tools.PSDImage.open(path))

    assert layers["Text 1"].text.strip() == "TOP"
    assert layers["Text 2"].text.strip() == "BOTTOM"
    assert layers["Text 1"].bbox[3] <= 60, layers["Text 1"].bbox
    assert layers["Text 2"].bbox[1] >= 65, layers["Text 2"].bbox


def test_a_hidden_text_layer_still_has_pixels_to_show(sandbox_dir, qapp):
    """Hidden in the Layers panel means a hidden PSD layer, which the user can
    show again in Photopea — so it needs its raster like any other."""
    page = _text_page([_text_state("SECRET", 20.0, 30.0, layer={"visible": False})])
    path = psd_exporter.export_psd_pages(str(sandbox_dir), [page], "hidden")
    (layer,) = _type_layers(psd_tools.PSDImage.open(path)).values()
    assert layer.visible is False
    assert (layer.numpy()[:, :, 3] > 0).sum() > 50


def _layer_record_blocks(path):
    """(layer name, key, declared length, bytes left in the record after it)
    for every tagged block in every layer record, read straight from the file
    rather than through the exporter's own parser."""
    data = open(path, "rb").read()
    offset = 26
    for _ in range(2):
        offset += 4 + struct.unpack(">I", data[offset:offset + 4])[0]
    position = offset + 8
    count = abs(struct.unpack(">h", data[position:position + 2])[0])
    position += 2
    found = []
    for _ in range(count):
        position += 16
        channels = struct.unpack(">H", data[position:position + 2])[0]
        position += 2 + 6 * channels + 12
        extra_len = struct.unpack(">I", data[position:position + 4])[0]
        position += 4
        end = position + extra_len
        cursor = position + 4 + struct.unpack(">I", data[position:position + 4])[0]
        cursor += 4 + struct.unpack(">I", data[cursor:cursor + 4])[0]
        name = data[cursor + 1:cursor + 1 + data[cursor]].decode("latin-1")
        cursor += (data[cursor] + 1 + 3) // 4 * 4
        while cursor + 12 <= end and data[cursor:cursor + 4] == b"8BIM":
            key = data[cursor + 4:cursor + 8]
            length = struct.unpack(">I", data[cursor + 8:cursor + 12])[0]
            cursor += 12 + length
            found.append((name, key, length, end - cursor))
        position = end
    return found


def test_no_tagged_block_leaves_padding_a_reader_takes_for_a_signature(sandbox_dir, qapp):
    """PhotoshopAPI declares TySh at its exact, odd, length and leaves the
    padding byte uncounted. Photopea then reads that byte as the next block's
    signature and tells the user "Error in PSD file: wrong signature".

    EngineData writes numbers as text, so the length's parity follows the
    content (font size 24 and 31 came out odd, 20 even, with the font name
    adding a machine-dependent constant) — hence a spread of sizes, so some
    are odd before the fix wherever this runs. Every block has to end exactly
    where the next begins, or at the end of its record."""
    states = [
        dict(_text_state(f"SIZE {size}", 10.0, 5.0 + 12 * i, height=30.0), font_size=float(size))
        for i, size in enumerate((20, 21, 22, 23, 24, 25, 31))
    ]
    path = psd_exporter.export_psd_pages(str(sandbox_dir), [_text_page(states, height=200)], "parity")
    blocks = _layer_record_blocks(path)
    assert any(key == b"TySh" for _, key, _, _ in blocks), "no TySh block found — layout not as expected"
    for name, key, length, left_over in blocks:
        assert length % 2 == 0, f"{name!r} {key!r} declares odd length {length}"
    # After the last block of each record nothing may remain.
    last = {}
    for name, key, length, left_over in blocks:
        last[name] = (key, left_over)
    for name, (key, left_over) in last.items():
        assert left_over == 0, f"{name!r} ends with {left_over} stray bytes after {key!r}"


def test_an_oversized_page_with_text_is_a_psb_whose_text_layer_has_pixels(sandbox_dir, qapp):
    """PSB widens the section and channel lengths to 8 bytes and the RLE row
    counts to 4; the rewrite has to follow."""
    page = _text_page([_text_state("WIDE", 29000.0, 10.0)], width=30010, height=60, name="wide.png")
    path = psd_exporter.export_psd_pages(str(sandbox_dir), [page], "wide")
    assert path.endswith(".psb") and _read_psd_version(path) == 2
    (layer,) = _type_layers(psd_tools.PSDImage.open(path)).values()
    left, top, right, bottom = layer.bbox
    assert right > left and 29000 <= left
    assert (layer.numpy()[:, :, 3] > 0).sum() > 50


def test_a_mismatch_writes_no_pixels_rather_than_wrong_ones(sandbox_dir, qapp):
    """If the file's type layers cannot be matched to the text items, no raster
    is written — the file stays as PhotoshopAPI wrote it, text still editable."""
    page = _text_page([_text_state("ALPHA", 20.0, 30.0)])
    path = psd_exporter.export_psd_pages(str(sandbox_dir), [page], "mismatch")
    # Re-run the finishing pass on a fresh write with a raster for other text.
    fake = np.zeros((5, 5, 4), dtype=np.uint8)
    fake[..., 3] = 255
    before = open(path, "rb").read()
    psd_exporter._finish_type_layers(path, [("SOMETHING ELSE", (0, 0, fake))])
    (layer,) = _type_layers(psd_tools.PSDImage.open(path)).values()
    assert layer.text.strip() == "ALPHA"
    assert layer.bbox != (0, 0, 5, 5)
    assert open(path, "rb").read() == before, "a mismatched pass changed the file"
