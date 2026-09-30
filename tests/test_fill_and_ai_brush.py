"""The fill bucket and the AI inpaint brush. Both end as ordinary patches
through the pixel-tool seam: one undo step, the raw image untouched, the
selection respected."""

import numpy as np
import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath


def _pump_until(qapp, condition, timeout_ms=20000):
    from PySide6.QtCore import QElapsedTimer

    timer = QElapsedTimer()
    timer.start()
    while not condition() and timer.elapsed() < timeout_ms:
        qapp.processEvents()
    return condition()


def page_image():
    """Grey page; a white box with dark lettering; a second white box apart
    from it; a one-pixel anti-aliased rim around the first box."""
    page = np.full((200, 300, 3), 200, np.uint8)
    page[49:151, 49:251] = 228          # the rim: between white and grey
    page[50:150, 50:250] = 255
    page[90:110, 100:200] = 10          # lettering
    page[170:190, 20:60] = 255          # another white area
    return page


@pytest.fixture
def window(qapp, tmp_path):
    from PIL import Image

    import controller as controller_mod

    win = controller_mod.ComicTranslate()
    path = str(tmp_path / "page.png")
    Image.fromarray(page_image()).save(path)
    win.image_ctrl.thread_load_images([path])
    assert _pump_until(qapp, lambda: win.image_viewer.hasPhoto() and win.undo_group.activeStack() is not None)
    dm = win.image_viewer.drawing_manager
    dm.paint_colour = QColor(0, 200, 0)
    dm.paint_opacity = 1.0
    dm.fill_tolerance = 32
    yield win
    win._skip_close_prompt = True
    win.close()


def composite(win):
    return win.image_viewer.get_image_array(include_patches=True)


class TestFill:
    def test_it_fills_the_region_and_leaves_the_lettering(self, window):
        before = composite(window).copy()
        stack = window.undo_group.activeStack()
        count = stack.count()
        assert window.image_viewer.drawing_manager.fill_at(QPointF(60, 60)) is not None
        after = composite(window)
        assert tuple(after[60, 60]) == (0, 200, 0)
        assert (after[95:105, 110:190] == 10).all(), "lettering inside is not a hole to fill"
        assert tuple(after[180, 40]) == (255, 255, 255), "a separate area stays"
        assert tuple(after[10, 10]) == (200, 200, 200)
        assert stack.count() == count + 1
        stack.undo()
        assert np.array_equal(composite(window), before)

    def test_the_anti_aliased_rim_is_blended_so_no_fringe_is_left(self, window):
        window.image_viewer.drawing_manager.fill_at(QPointF(60, 60))
        rim = tuple(int(v) for v in composite(window)[49, 120])
        assert rim != (228, 228, 228), "the old rim colour would show as a fringe"
        assert rim[1] > rim[0] and rim[1] > rim[2], "blended towards the fill colour"
        assert tuple(composite(window)[48, 120]) == (200, 200, 200)

    def test_a_hard_outline_is_not_eaten(self, qapp, tmp_path):
        """Growing the fill a pixel on every side ate a pixel of a bubble's
        black outline: on a hard edge the ring is the neighbour, not a rim."""
        from PIL import Image

        import controller as controller_mod

        page = np.full((120, 160, 3), 200, np.uint8)
        page[20:100, 20:140] = 0            # outline
        page[24:96, 24:136] = 255           # inside
        win = controller_mod.ComicTranslate()
        try:
            path = str(tmp_path / "hard.png")
            Image.fromarray(page).save(path)
            win.image_ctrl.thread_load_images([path])
            assert _pump_until(qapp, lambda: win.image_viewer.hasPhoto() and win.undo_group.activeStack())
            dm = win.image_viewer.drawing_manager
            dm.paint_colour, dm.paint_opacity = QColor(0, 200, 0), 1.0
            dm.fill_at(QPointF(60, 60))
            after = composite(win)
            assert tuple(after[60, 60]) == (0, 200, 0)
            assert (after[20:24, 20:140] == 0).all() and (after[96:100, 20:140] == 0).all()
        finally:
            win._skip_close_prompt = True
            win.close()

    def test_ctrl_fills_every_region_of_that_colour(self, window):
        window.image_viewer.drawing_manager.fill_at(QPointF(60, 60), all_regions=True)
        assert tuple(composite(window)[180, 40]) == (0, 200, 0)

    def test_tolerance_decides_what_counts_as_the_same_colour(self, window):
        dm = window.image_viewer.drawing_manager
        dm.fill_tolerance = 0
        dm.fill_at(QPointF(60, 60))
        assert tuple(composite(window)[60, 60]) == (0, 200, 0)
        assert tuple(composite(window)[48, 120]) == (200, 200, 200)

    def test_a_looser_tolerance_takes_close_colours_along(self, window):
        dm = window.image_viewer.drawing_manager
        dm.fill_tolerance = 40
        dm.fill_at(QPointF(10, 10))
        assert tuple(composite(window)[10, 10]) == (0, 200, 0)
        assert tuple(composite(window)[49, 120]) == (0, 200, 0), "the rim is 28 from grey: inside 40"
        assert tuple(composite(window)[180, 40]) == (255, 255, 255), "white is 55 from grey: outside"

    def test_a_selection_clips_the_fill(self, window):
        path = QPainterPath()
        path.addRect(QRectF(50, 50, 40, 40))
        window.image_viewer.selection.combine(path)
        window.image_viewer.drawing_manager.fill_at(QPointF(60, 60))
        after = composite(window)
        assert tuple(after[60, 60]) == (0, 200, 0)
        assert tuple(after[60, 120]) == (255, 255, 255)

    def test_half_opacity_mixes(self, window):
        window.image_viewer.drawing_manager.paint_opacity = 0.5
        window.image_viewer.drawing_manager.fill_at(QPointF(60, 60))
        assert tuple(composite(window)[60, 60]) == (128, 228, 128)

    def test_filling_with_the_same_colour_pushes_nothing(self, window):
        """The second white box has no anti-aliased rim to grow over, so
        filling it white changes nothing (the first box's rim would change)."""
        dm = window.image_viewer.drawing_manager
        dm.paint_colour = QColor(255, 255, 255)
        dm.fill_tolerance = 0
        stack = window.undo_group.activeStack()
        count = stack.count()
        assert dm.fill_at(QPointF(40, 180)) is None
        assert stack.count() == count

    def test_ctrl_click_with_the_tool_fills_every_region(self, window):
        from PySide6.QtCore import QEvent, QPoint
        from PySide6.QtGui import QMouseEvent

        viewer = window.image_viewer
        window.set_tool("fill")
        pos = viewer.mapFromScene(QPointF(60, 60))
        event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(pos), QPointF(viewer.mapToGlobal(QPoint(pos))),
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier)
        viewer.event_handler.handle_mouse_press(event)
        assert tuple(composite(window)[180, 40]) == (0, 200, 0)


def _fake_inpainter(monkeypatch, window):
    shapes = []

    def inpaint_image(image, mask, config, blk_list=None):
        shapes.append(image.shape[:2])
        out = image.copy()
        out[mask > 0] = (255, 255, 255)
        return out

    monkeypatch.setattr(window.pipeline.inpainting, "inpaint_image", inpaint_image)
    return shapes


def ai_stroke(win, points):
    dm = win.image_viewer.drawing_manager
    dm.paint_size = 12
    assert dm.pixel_press(QPointF(*points[0]), "aibrush")
    for point in points[1:]:
        dm.pixel_move(QPointF(*point))
    return dm.pixel_release()


class TestAIBrush:
    def test_the_model_sees_a_crop_and_only_the_stroke_changes(self, qapp, window, monkeypatch):
        shapes = _fake_inpainter(monkeypatch, window)
        before = composite(window).copy()
        stack = window.undo_group.activeStack()
        count = stack.count()
        ai_stroke(window, [(110, 100), (190, 100)])
        assert _pump_until(qapp, lambda: window.loading.isHidden())
        after = composite(window)
        assert shapes and shapes[0][0] < 200 and shapes[0][1] < 300, "a crop, not the page"
        assert (after[100, 115:185] == 255).all(), "the stroke is inpainted"
        changed = np.any(after != before, axis=2)
        ys, xs = np.nonzero(changed)
        assert ys.min() >= 93 and ys.max() <= 107 and xs.min() >= 103 and xs.max() <= 197
        assert stack.count() == count + 1
        stack.undo()
        assert np.array_equal(composite(window), before)

    def test_it_stays_inside_a_selection(self, qapp, window, monkeypatch):
        _fake_inpainter(monkeypatch, window)
        path = QPainterPath()
        path.addRect(QRectF(100, 80, 40, 40))
        window.image_viewer.selection.combine(path)
        before = composite(window).copy()
        ai_stroke(window, [(110, 100), (190, 100)])
        assert _pump_until(qapp, lambda: window.loading.isHidden())
        xs = np.nonzero(np.any(composite(window) != before, axis=2))[1]
        assert xs.max() <= 141
        assert not window.image_viewer.selection.is_empty()

    def test_the_preview_tints_and_is_not_a_layer(self, window):
        from app.ui.canvas.scene_registry import iter_items, kind_of

        dm = window.image_viewer.drawing_manager
        dm.paint_size = 12
        dm.pixel_press(QPointF(60, 60), "aibrush")
        preview = dm.pixel_session.preview
        assert kind_of(preview) is None
        assert preview not in list(iter_items(window.image_viewer._scene, viewer=window.image_viewer))
        red, green = preview._buffer[..., 0].max(), preview._buffer[60, 60, 1]
        assert red > green, "tinted red, not painted"
        window.set_tool("pan")
        assert preview.scene() is None and dm.pixel_session is None

    def test_other_pixel_tools_wait_while_it_runs(self, qapp, window, monkeypatch):
        """Its patches are cut from the page as it was at the press, padding
        included; paint laid down meanwhile would be covered when they land."""
        import threading

        from app.controllers import paint as paint_mod

        release = threading.Event()

        def slow(image, mask, config, blk_list=None):
            release.wait(10)
            out = image.copy()
            out[mask > 0] = (255, 255, 255)
            return out

        monkeypatch.setattr(window.pipeline.inpainting, "inpaint_image", slow)
        told = []
        monkeypatch.setattr(paint_mod.MMessage, "info", lambda text, parent=None, **k: told.append(text))
        ai_stroke(window, [(110, 100), (190, 100)])
        dm = window.image_viewer.drawing_manager
        assert dm.pixel_busy
        assert dm.pixel_press(QPointF(40, 40), "paint") is False
        assert dm.fill_at(QPointF(60, 60)) is None
        assert len(told) == 2
        release.set()
        assert _pump_until(qapp, lambda: window.loading.isHidden())
        assert not dm.pixel_busy
        assert dm.pixel_press(QPointF(40, 40), "paint") is True
        dm.pixel_cancel()

    def test_an_inpainter_failure_pushes_nothing(self, qapp, window, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("model missing")

        errors = []
        monkeypatch.setattr(window.pipeline.inpainting, "inpaint_image", boom)
        monkeypatch.setattr(window, "default_error_handler", lambda *a, **k: errors.append(a))
        stack = window.undo_group.activeStack()
        count = stack.count()
        ai_stroke(window, [(110, 100), (190, 100)])
        assert _pump_until(qapp, lambda: window.loading.isHidden() and errors)
        assert stack.count() == count
        assert not window.image_viewer.drawing_manager.pixel_busy, "a failure frees the tools too"


def test_inpaint_region_pads_the_crop_and_touches_nothing_else(qapp):
    """The pipeline half, without a window: only the crop reaches the model,
    only masked pixels come back."""
    from types import SimpleNamespace

    from pipeline.inpainting import InpaintingHandler

    handler = InpaintingHandler.__new__(InpaintingHandler)
    seen = []

    def inpaint_image(image, mask, config, blk_list=None):
        seen.append((image.shape[:2], blk_list))
        return np.full_like(image, 7)

    handler.inpaint_image = inpaint_image
    handler._denoise_cleaned = lambda mask, image: image
    handler.main_page = SimpleNamespace(settings_page=None, webtoon_mode=False)
    captured = {}
    handler.get_inpainted_patches = lambda mask, image, mappings=None, denoise=True: captured.update(
        mask=mask, image=image, denoise=denoise) or ["patch"]
    import pipeline.inpainting as mod

    original = mod.get_config
    mod.get_config = lambda settings: None
    try:
        image = np.full((400, 600, 3), 90, np.uint8)
        mask = np.zeros((400, 600), np.uint8)
        mask[200:210, 300:340] = 255
        assert handler.inpaint_region(image, mask) == ["patch"]
    finally:
        mod.get_config = original
    (shape, blocks), = seen
    assert shape == (10 + 64, 40 + 64) and blocks is None
    result = captured["image"]
    assert (result[200:210, 300:340] == 7).all()
    assert (result[:200] == 90).all() and (result[210:] == 90).all()
    assert captured["denoise"] is False


def test_fill_tolerance_persists(qapp):
    import controller as controller_mod
    from PySide6.QtCore import QSettings

    win = controller_mod.ComicTranslate()
    try:
        win.fill_tolerance_spin.setValue(12)
        assert win.image_viewer.drawing_manager.fill_tolerance == 12
        settings = QSettings("ComicLabs", "ComicTranslate")
        settings.beginGroup("paint")
        assert settings.value("fill_tolerance", type=int) == 12
        settings.endGroup()
        win.set_tool("fill")
        assert not win.fill_tolerance_spin.isHidden() and win.paint_hardness_slider.isHidden()
        win.set_tool("aibrush")
        assert win.fill_tolerance_spin.isHidden() and win.paint_colour_button.isHidden()
        assert not win.brush_options.isHidden()
    finally:
        win._skip_close_prompt = True
        win.close()


def test_in_webtoon_mode_a_fill_on_page_two_lands_on_page_two(qapp, tmp_path):
    from PIL import Image

    import controller as controller_mod

    win = controller_mod.ComicTranslate()
    try:
        win.resize(1200, 800)
        win.show()
        paths = []
        for index in range(2):
            page = np.full((300, 240, 3), 180, np.uint8)
            page[20:120, 20:220] = 255
            path = str(tmp_path / f"{index:03d}.png")
            Image.fromarray(page).save(path)
            paths.append(path)
        win.image_ctrl.thread_load_images(paths)
        assert _pump_until(qapp, lambda: win.image_viewer.hasPhoto() and len(win.image_files) == 2)
        win.show_main_page()
        win.webtoon_toggle.click()
        viewer = win.image_viewer
        assert _pump_until(qapp, lambda: viewer.webtoon_mode and len(viewer.webtoon_manager.loaded_pages) == 2)
        top = viewer.webtoon_manager.layout_manager.image_positions[1]
        viewer.centerOn(120, top + 60)
        qapp.processEvents()
        _, mappings = viewer.get_visible_area_image()
        page_two = next(m for m in mappings if m["page_index"] == 1)
        assert page_two["scene_y_end"] - top > 120, "page two's white box is not loaded"

        dm = viewer.drawing_manager
        dm.paint_colour, dm.paint_opacity = QColor(0, 200, 0), 1.0
        assert dm.fill_at(QPointF(100, top + 60)) is not None
        first, second = win.image_files
        assert win.image_patches.get(first, []) == []
        patches = win.image_patches.get(second, [])
        assert len(patches) == 1
        x, y, w, h = patches[0]["bbox"]
        assert y <= 20 and y + h >= 120
    finally:
        win._skip_close_prompt = True
        win.close()


class TestFillAlpha:
    def region(self):
        region = np.zeros((20, 20), bool)
        region[5:15, 5:15] = True
        return region

    def test_an_in_between_rim_pixel_is_half_covered(self):
        from modules.utils.flood_select import fill_alpha

        image = np.full((20, 20, 3), 200, np.uint8)
        image[4:16, 4:16] = 228
        image[5:15, 5:15] = 255
        alpha = fill_alpha(image, self.region())
        assert alpha[10, 10] == 1.0 and alpha[3, 10] == 0.0
        assert 0.4 < alpha[4, 10] < 0.6

    @pytest.mark.parametrize("edge", [0, 200])
    def test_a_hard_edge_is_not_covered(self, edge):
        from modules.utils.flood_select import fill_alpha

        image = np.full((20, 20, 3), 200, np.uint8)
        image[3:17, 3:17] = edge
        image[5:15, 5:15] = 255
        assert fill_alpha(image, self.region())[4, 10] == 0.0
