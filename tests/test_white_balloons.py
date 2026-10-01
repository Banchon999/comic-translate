"""Clean White Balloons: every plain white speech bubble on the page loses its
lettering in one step, painted in the bubble's own colour, with no model.
Bubbles that are not plain white are skipped and reported, never smeared."""

import numpy as np
import pytest
from PySide6.QtCore import QPointF

from core import balloons


@pytest.fixture(autouse=True)
def no_segmentation_model(monkeypatch):
    """The command asks the text-segmentation model for a better lettering
    mask; in a test's empty home that is a download. Thresholding it is."""
    from modules.utils import text_segmentation

    monkeypatch.setattr(text_segmentation, "segment_page", lambda image, settings: None)


def _pump_until(qapp, condition, timeout_ms=20000):
    from PySide6.QtCore import QElapsedTimer

    timer = QElapsedTimer()
    timer.start()
    while not condition() and timer.elapsed() < timeout_ms:
        qapp.processEvents()
    return condition()


def _ellipse(shape, cy, cx, ry, rx):
    yy, xx = np.mgrid[:shape[0], :shape[1]]
    return ((yy - cy) / ry) ** 2 + ((xx - cx) / rx) ** 2 <= 1.0


def page_image():
    """Two bubbles on a grey page: a white one and a screentoned one, each
    with a dark outline and a bar of dark 'lettering'."""
    page = np.full((260, 420, 3), 170, np.uint8)
    for cx, tone in ((105, False), (315, True)):
        outer = _ellipse(page.shape, 130, cx, 90, 90)
        inner = _ellipse(page.shape, 130, cx, 85, 85)
        page[outer] = 20                      # the outline
        page[inner] = 250
        if tone:
            dots = np.zeros(page.shape[:2], bool)
            dots[::4, ::4] = True
            page[inner & dots] = 150
        page[118:142, cx - 45:cx + 45] = 15   # the lettering
    return page


def blocks():
    from modules.utils.textblock import TextBlock

    out = []
    for cx in (105, 315):
        out.append(TextBlock(
            text_bbox=np.array([cx - 50, 112, cx + 50, 148]),
            bubble_bbox=np.array([cx - 90, 40, cx + 90, 220]),
            text_class="text_bubble",
        ))
    return out


# -- the decision ----------------------------------------------------------------

class TestPlan:
    def bubble(self):
        crop = np.full((80, 120, 3), 250, np.uint8)
        bubble = _ellipse(crop.shape, 40, 60, 36, 56)
        crop[~bubble] = 120
        crop[35:45, 30:90] = 15
        text = np.zeros((80, 120), bool)
        text[33:47, 28:92] = True
        return crop, text, bubble

    def test_a_white_bubble_is_filled_with_its_own_colour(self):
        crop, text, bubble = self.bubble()
        crop[bubble & ~text] = (252, 249, 240)          # cream still counts
        plan = balloons.plan_balloon(crop, text, bubble)
        assert plan.ok and plan.colour == (252, 249, 240)
        out = balloons.fill_balloon(crop, text, bubble, plan.colour)
        assert (out[text & bubble] == (252, 249, 240)).all()
        assert np.array_equal(out[~bubble], crop[~bubble]), "nothing outside the bubble moves"

    def test_screentone_is_refused_as_textured(self):
        crop, text, bubble = self.bubble()
        crop[::4, ::4] = np.where(bubble[::4, ::4, None], 150, crop[::4, ::4])
        assert balloons.plan_balloon(crop, text, bubble).reason == balloons.TEXTURED

    def test_a_grey_bubble_is_refused_as_not_white(self):
        crop, text, bubble = self.bubble()
        crop[bubble & ~text] = 160
        assert balloons.plan_balloon(crop, text, bubble).reason == balloons.NOT_WHITE

    def test_lettering_filling_the_bubble_leaves_too_little_to_judge(self):
        crop, _, bubble = self.bubble()
        text = bubble.copy()
        assert balloons.plan_balloon(crop, text, bubble).reason == balloons.TOO_LITTLE

    def test_no_lettering_inside_the_bubble_is_nothing_to_do(self):
        crop, text, bubble = self.bubble()
        assert balloons.plan_balloon(crop, np.zeros_like(text), bubble).reason == balloons.NO_TEXT

    def test_an_outline_inside_the_bubble_mask_is_neither_texture_nor_painted(self):
        """A segmented bubble includes its own line: about as many dark pixels
        as screentone has, but joined to the mask's edge rather than enclosed.
        Found on a real detection, where both white bubbles were refused."""
        crop, text, _ = self.bubble()
        bubble = _ellipse(crop.shape, 40, 60, 38, 58)          # reaches over the line
        line = bubble & ~_ellipse(crop.shape, 40, 60, 34, 54)
        crop[line] = 10
        plan = balloons.plan_balloon(crop, text, bubble)
        assert plan.ok, plan.reason
        text_near_line = text.copy()
        text_near_line[38:42, 3:30] = True                     # lettering running into the line
        out = balloons.fill_balloon(crop, text_near_line, bubble, plan.colour, protect=plan.protect)
        assert np.array_equal(out[line & ~text_near_line], crop[line & ~text_near_line]), "the line is kept"

    def test_jpeg_noise_on_white_is_still_flat(self):
        crop, text, bubble = self.bubble()
        rng = np.random.default_rng(3)
        noise = rng.integers(-4, 5, crop.shape)
        crop = np.clip(crop.astype(int) + np.where(bubble[..., None] & ~text[..., None], noise, 0), 0, 255).astype(np.uint8)
        assert balloons.plan_balloon(crop, text, bubble).ok


# -- the pipeline pass --------------------------------------------------------------

def test_the_pipeline_cleans_the_white_bubble_and_skips_the_textured_one(qapp):
    import controller as controller_mod

    win = controller_mod.ComicTranslate()
    try:
        page = page_image()
        patches, cleaned, skipped = win.pipeline.inpainting.clean_white_balloons(page, blocks())
        assert cleaned == 1 and skipped == [balloons.TEXTURED]
        assert len(patches) >= 1
        result = page.copy()
        for patch in patches:
            x, y, w, h = patch["bbox"]
            result[y:y + h, x:x + w] = patch["image"][..., :3]
        assert (result[120:140, 70:140] > 240).all(), "the lettering is gone"
        outline = _ellipse(page.shape, 130, 105, 90, 90) & ~_ellipse(page.shape, 130, 105, 85, 85)
        assert np.array_equal(result[outline], page[outline]), "the outline is exactly as drawn"
        assert np.array_equal(result[:, 210:], page[:, 210:]), "the screentoned bubble is untouched"
    finally:
        win._skip_close_prompt = True
        win.close()


# -- the command ----------------------------------------------------------------------

@pytest.fixture
def window(qapp, tmp_path):
    from PIL import Image

    import controller as controller_mod

    win = controller_mod.ComicTranslate()
    path = str(tmp_path / "page.png")
    Image.fromarray(page_image()).save(path)
    win.image_ctrl.thread_load_images([path])
    assert _pump_until(qapp, lambda: win.image_viewer.hasPhoto() and win.undo_group.activeStack() is not None)
    yield win
    win._skip_close_prompt = True
    win.close()


def composite(win):
    return win.image_viewer.get_image_array(include_patches=True)[..., :3]


def _capture_toasts(monkeypatch):
    from app.controllers import paint as paint_mod

    told = []
    monkeypatch.setattr(paint_mod.MMessage, "info", lambda text, parent=None, **k: told.append(("info", text)))
    monkeypatch.setattr(paint_mod.MMessage, "warning", lambda text, parent=None, **k: told.append(("warning", text)))
    return told


class TestCommand:
    def test_one_click_cleans_the_white_bubble_in_one_undo_step(self, qapp, window, monkeypatch):
        told = _capture_toasts(monkeypatch)
        window.blk_list = blocks()
        before = composite(window).copy()
        stack = window.undo_group.activeStack()
        count = stack.count()
        window.clean_balloons_button.click()
        assert _pump_until(qapp, lambda: window.loading.isHidden() and told)
        after = composite(window)
        assert (after[120:140, 70:140] > 240).all()
        assert np.array_equal(after[:, 210:], before[:, 210:])
        assert stack.count() == count + 1
        assert stack.text(stack.index() - 1) == "Clean White Balloons"
        assert "1" in told[-1][1] and told[-1][0] == "info"
        stack.undo()
        assert np.array_equal(composite(window), before)
        assert not window.image_viewer.drawing_manager.pixel_busy

    def test_with_no_detection_it_detects_first(self, qapp, window, monkeypatch):
        told = _capture_toasts(monkeypatch)
        ran = []

        def detect(load_rects=True):
            ran.append(True)
            return blocks(), load_rects, None

        monkeypatch.setattr(window.pipeline, "detect_blocks", detect)
        window.blk_list = []
        window.clean_balloons_button.click()
        assert _pump_until(qapp, lambda: window.loading.isHidden() and told)
        assert ran, "Detect ran first"
        assert (composite(window)[120:140, 70:140] > 240).all()

    def test_when_detection_finds_no_bubbles_it_says_so(self, qapp, window, monkeypatch):
        told = _capture_toasts(monkeypatch)
        monkeypatch.setattr(window.pipeline, "detect_blocks", lambda load_rects=True: ([], load_rects, None))
        window.blk_list = []
        window.clean_balloons_button.click()
        assert _pump_until(qapp, lambda: window.loading.isHidden() and told)
        assert "No speech bubbles" in told[-1][1]
        assert not any(window.image_patches.values()), "nothing painted"

    def test_only_textured_bubbles_warn_and_push_nothing(self, qapp, window, monkeypatch):
        told = _capture_toasts(monkeypatch)
        window.blk_list = blocks()[1:]
        stack = window.undo_group.activeStack()
        count = stack.count()
        window.clean_balloons_button.click()
        assert _pump_until(qapp, lambda: window.loading.isHidden() and told)
        assert told[-1][0] == "warning" and "Skipped" in told[-1][1]
        assert stack.count() == count

    def test_it_waits_for_a_running_cleaning(self, window, monkeypatch):
        told = _capture_toasts(monkeypatch)
        window.blk_list = blocks()
        window.image_viewer.drawing_manager.pixel_busy = True
        try:
            window.paint_ctrl.clean_white_balloons()
            assert told and "Wait" in told[-1][1]
            assert window.loading.isHidden()
        finally:
            window.image_viewer.drawing_manager.pixel_busy = False

    def test_blocks_are_not_changed_by_it(self, qapp, window, monkeypatch):
        _capture_toasts(monkeypatch)
        window.blk_list = blocks()
        boxes = [list(blk.xyxy) for blk in window.blk_list]
        window.clean_balloons_button.click()
        assert _pump_until(qapp, lambda: window.loading.isHidden())
        assert [list(blk.xyxy) for blk in window.blk_list] == boxes


def test_in_webtoon_mode_it_cleans_the_loaded_page_in_its_own_coordinates(qapp, tmp_path, monkeypatch):
    from PIL import Image

    import controller as controller_mod
    from modules.utils.textblock import TextBlock

    _capture_toasts(monkeypatch)
    win = controller_mod.ComicTranslate()
    try:
        win.resize(1200, 800)
        win.show()
        paths = []
        for index in range(2):
            path = str(tmp_path / f"{index:03d}.png")
            Image.fromarray(page_image()).save(path)
            paths.append(path)
        win.image_ctrl.thread_load_images(paths)
        assert _pump_until(qapp, lambda: win.image_viewer.hasPhoto() and len(win.image_files) == 2)
        win.show_main_page()
        win.webtoon_toggle.click()
        viewer = win.image_viewer
        assert _pump_until(qapp, lambda: viewer.webtoon_mode and len(viewer.webtoon_manager.loaded_pages) == 2)
        layout = viewer.webtoon_manager.layout_manager
        assert _pump_until(qapp, lambda: viewer.sceneRect().bottom() >= layout.total_height - 1)
        top = layout.image_positions[1]
        viewer.resetTransform()
        viewer.centerOn(210, top + 130)
        qapp.processEvents()
        # The white bubble of page two, in scene coordinates.
        x0 = viewer.page_to_scene_xy(1, 0, 0)[0]
        win.blk_list = [TextBlock(
            text_bbox=np.array([x0 + 55, top + 112, x0 + 155, top + 148]),
            bubble_bbox=np.array([x0 + 15, top + 40, x0 + 195, top + 220]),
            text_class="text_bubble",
        )]
        win.clean_balloons_button.click()
        assert _pump_until(qapp, lambda: win.loading.isHidden() and win.image_patches.get(win.image_files[1]))
        first, second = win.image_files
        assert win.image_patches.get(first, []) == []
        x, y, w, h = win.image_patches[second][0]["bbox"]
        assert 100 <= y <= 125 and x < 100, "in page two's own coordinates"
        assert list(win.blk_list[0].xyxy)[1] == top + 112, "the block is back in scene coordinates"
    finally:
        win._skip_close_prompt = True
        win.close()
