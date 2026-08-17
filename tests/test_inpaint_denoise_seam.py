"""The denoiser is wired into the pipeline where the patches are cut.

`modules/inpainting/denoise.py` has its own tests; these are only about the
seam — that both patch-cutting paths pass through it, that the settings
checkbox actually turns it off, and that a failure inside it cannot take the
clean step down with it.
"""

import types

import numpy as np
import pytest

from pipeline.inpainting import InpaintingHandler


def page():
    rng = np.random.default_rng(0)
    flat = np.full((120, 200, 3), 180, dtype=np.uint8)
    return np.clip(
        flat.astype(np.int16) + rng.integers(-40, 40, flat.shape), 0, 255
    ).astype(np.uint8)


def mask():
    m = np.zeros((120, 200), dtype=np.uint8)
    m[50:70, 80:120] = 255
    return m


def handler(denoise=True, webtoon_mode=False):
    main = types.SimpleNamespace(
        webtoon_mode=webtoon_mode,
        settings_page=types.SimpleNamespace(get_denoise_cleaned_areas=lambda: denoise),
    )
    return InpaintingHandler(main)


class TestRegularPatches:
    def test_the_patch_it_returns_is_denoised(self):
        image = page()
        patches = handler()._get_regular_patches(mask(), image)
        assert len(patches) == 1
        x, y, w, h = patches[0]['bbox']
        assert patches[0]['image'].std() < image[y:y + h, x:x + w].std()

    def test_turning_the_setting_off_returns_the_raw_patch(self):
        image = page()
        patches = handler(denoise=False)._get_regular_patches(mask(), image)
        x, y, w, h = patches[0]['bbox']
        assert np.array_equal(patches[0]['image'], image[y:y + h, x:x + w])

    def test_the_source_image_is_not_modified_in_place(self):
        """It is the page the caller still holds; the patches are the output."""
        image = page()
        before = image.copy()
        handler()._get_regular_patches(mask(), image)
        assert np.array_equal(image, before)

    def test_an_empty_mask_still_yields_nothing(self):
        assert handler()._get_regular_patches(np.zeros((120, 200), np.uint8), page()) == []


class TestManualPath:
    """`get_inpainted_patches` is the one the per-step Clean button uses."""

    def test_it_denoises_too(self):
        image = page()
        patches = handler().get_inpainted_patches(mask(), image)
        assert len(patches) == 1
        x, y, w, h = patches[0]['bbox']
        assert patches[0]['image'].std() < image[y:y + h, x:x + w].std()

    def test_the_setting_reaches_it(self):
        image = page()
        patches = handler(denoise=False).get_inpainted_patches(mask(), image)
        x, y, w, h = patches[0]['bbox']
        assert np.array_equal(patches[0]['image'], image[y:y + h, x:x + w])


class TestItCannotBreakCleaning:
    def test_a_failure_inside_the_denoiser_is_swallowed(self, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("nope")

        monkeypatch.setattr("pipeline.inpainting.denoise_around_mask", boom)
        image = page()
        patches = handler()._get_regular_patches(mask(), image)
        x, y, w, h = patches[0]['bbox']
        assert np.array_equal(patches[0]['image'], image[y:y + h, x:x + w])

    @pytest.mark.parametrize("missing", ["settings_page", "getter"])
    def test_it_defaults_to_on_when_the_setting_is_not_there(self, missing):
        """A caller built without the settings page must not crash."""
        main = types.SimpleNamespace(webtoon_mode=False)
        if missing == "getter":
            main.settings_page = types.SimpleNamespace()
        image = page()
        patches = InpaintingHandler(main)._get_regular_patches(mask(), image)
        x, y, w, h = patches[0]['bbox']
        assert patches[0]['image'].std() < image[y:y + h, x:x + w].std()
