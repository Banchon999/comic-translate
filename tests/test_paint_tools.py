"""Pixel tools: the paint brush, the restore eraser, pen pressure and the
eyedropper. Every stroke ends as inpaint patches, one undo step each, and never
touches the raw image."""

import numpy as np
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor

from core import paint


# -- maths ----------------------------------------------------------------------


class TestDab:
    def test_a_hard_dab_is_a_disc_of_the_radius(self):
        tip = paint.dab(5, 1.0)
        assert tip[5, 5] == 1.0
        assert tip[5, 1] == 1.0 and tip[5, 9] == 1.0     # 4 px from the centre: inside
        assert tip[5, 0] == pytest.approx(0.5)           # on the rim: anti-aliased
        assert tip[0, 0] == 0.0                          # the corner is outside

    def test_a_soft_dab_falls_off_monotonically(self):
        row = paint.dab(8, 0.0)[8, 8:]
        assert row[0] == 1.0 and row[-1] == 0.0
        assert np.all(np.diff(row) <= 1e-6)

    def test_a_tiny_brush_still_paints(self):
        coverage = np.zeros((5, 5), np.float32)
        paint.stamp(coverage, 2, 2, 0.1)
        assert coverage[2, 2] == 1.0

    def test_flow_builds_up_but_never_past_full(self):
        coverage = np.zeros((20, 20), np.float32)
        paint.stamp(coverage, 10, 10, 3, 1.0, 0.3)
        first = coverage[10, 10]
        for _ in range(20):
            paint.stamp(coverage, 10, 10, 3, 1.0, 0.3)
        assert first == pytest.approx(0.3)
        assert first < coverage[10, 10] <= 1.0

    def test_a_dab_off_the_image_touches_nothing(self):
        coverage = np.zeros((10, 10), np.float32)
        assert paint.stamp(coverage, -50, -50, 3) is None
        assert not coverage.any()

    def test_spacing_stays_even_across_segments(self):
        first, carry = paint.dab_positions((0, 0), (10, 0), 3.0)
        second, _ = paint.dab_positions((10, 0), (20, 0), 3.0, carry)
        xs = [x for x, _ in first + second]
        assert np.allclose(np.diff([0.0] + xs), 3.0)

    def test_sampling_averages_the_window(self):
        image = np.zeros((10, 10, 3), np.uint8)
        image[4:7, 4:7] = (30, 60, 90)
        image[5, 5] = (0, 0, 0)
        assert paint.sample(image, 5, 5, 3) == (27, 53, 80)
        assert paint.sample(image, 5, 5, 1) == (0, 0, 0)
        assert paint.sample(image, 99, 5) is None

    def test_pressure_scales_only_when_enabled(self):
        assert paint.pressure_scale(0.5, True) == 0.5
        assert paint.pressure_scale(0.5, False) == 1.0
        assert paint.pressure_scale(None, True) == 1.0
        assert paint.pressure_scale(0.0, True) == 0.05


# -- in the window ---------------------------------------------------------------


def _pump_until(qapp, condition, timeout_ms=20000):
    from PySide6.QtCore import QElapsedTimer

    timer = QElapsedTimer()
    timer.start()
    while not condition() and timer.elapsed() < timeout_ms:
        qapp.processEvents()
    return condition()


@pytest.fixture
def window(qapp, tmp_path):
    from PIL import Image

    import controller as controller_mod

    win = controller_mod.ComicTranslate()
    page = np.full((200, 300, 3), 200, np.uint8)
    page[100:140, 100:200] = 20          # "lettering" a clean will remove
    path = str(tmp_path / "page.png")
    Image.fromarray(page).save(path)
    win.image_ctrl.thread_load_images([path])
    assert _pump_until(qapp, lambda: win.image_viewer.hasPhoto() and win.undo_group.activeStack() is not None)
    dm = win.image_viewer.drawing_manager
    dm.paint_colour = QColor(255, 0, 0)
    dm.paint_size = 10
    dm.paint_hardness = 1.0
    dm.paint_opacity = 1.0
    dm.paint_pressure_size = False
    dm.paint_pressure_flow = False
    yield win
    win._skip_close_prompt = True
    win.close()


def composite(win):
    return win.image_viewer.get_image_array(include_patches=True)


def stroke(win, points, mode="paint", pressure=None):
    dm = win.image_viewer.drawing_manager
    assert dm.pixel_press(QPointF(*points[0]), mode, pressure)
    for point in points[1:]:
        dm.pixel_move(QPointF(*point), pressure)
    return dm.pixel_release()


class TestPaint:
    def test_a_stroke_is_one_undo_step_and_changes_only_what_it_crossed(self, window):
        before = composite(window).copy()
        stack = window.undo_group.activeStack()
        count = stack.count()
        stroke(window, [(40, 40), (160, 40)])
        after = composite(window)
        assert stack.count() == count + 1
        assert (after[40, 60:140] == (255, 0, 0)).all()
        changed = np.any(after != before, axis=2)
        ys, xs = np.nonzero(changed)
        assert ys.min() >= 34 and ys.max() <= 46 and xs.min() >= 34 and xs.max() <= 166
        stack.undo()
        assert np.array_equal(composite(window), before)

    def test_the_raw_image_is_never_touched(self, window):
        raw = window.image_viewer.get_image_array(include_patches=False).copy()
        stroke(window, [(40, 40), (160, 40)])
        assert np.array_equal(window.image_viewer.get_image_array(include_patches=False), raw)

    def test_a_selection_keeps_the_paint_inside_it(self, window):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainterPath

        path = QPainterPath()
        path.addRect(QRectF(80, 20, 40, 40))
        window.image_viewer.selection.combine(path)
        before = composite(window).copy()
        stroke(window, [(40, 40), (200, 40)])
        changed = np.any(composite(window) != before, axis=2)
        ys, xs = np.nonzero(changed)
        assert xs.min() >= 80 and xs.max() <= 121

    def test_half_opacity_mixes_evenly(self, window):
        window.image_viewer.drawing_manager.paint_opacity = 0.5
        stroke(window, [(40, 40), (160, 40)])
        assert tuple(composite(window)[40, 100]) == (228, 100, 100)

    def test_nothing_changed_means_no_undo_step(self, window):
        window.image_viewer.drawing_manager.paint_colour = QColor(200, 200, 200)  # the page colour
        stack = window.undo_group.activeStack()
        count = stack.count()
        assert stroke(window, [(40, 40), (160, 40)]) is None
        assert stack.count() == count

    def test_switching_tools_mid_stroke_drops_it(self, window):
        dm = window.image_viewer.drawing_manager
        before = composite(window).copy()
        dm.pixel_press(QPointF(40, 40), "paint")
        dm.pixel_move(QPointF(160, 40))
        preview = dm.pixel_session.preview
        assert preview.scene() is window.image_viewer._scene
        window.set_tool("pan")
        assert dm.pixel_session is None and preview.scene() is None
        assert dm.pixel_release() is None
        assert np.array_equal(composite(window), before)

    def test_the_preview_is_not_a_layer_or_a_stroke(self, window):
        from app.ui.canvas.scene_registry import iter_items, kind_of

        dm = window.image_viewer.drawing_manager
        dm.pixel_press(QPointF(40, 40), "paint")
        preview = dm.pixel_session.preview
        assert kind_of(preview) is None
        assert preview not in list(iter_items(window.image_viewer._scene, viewer=window.image_viewer))
        assert not window.image_viewer.has_drawn_elements()
        assert preview.shape().isEmpty()
        dm.pixel_release()
        assert preview.scene() is None

    def test_pressure_thins_the_line_when_enabled(self, window):
        dm = window.image_viewer.drawing_manager
        dm.paint_size = 20
        dm.paint_pressure_size = True
        before = composite(window).copy()
        stroke(window, [(40, 40), (160, 40)], pressure=0.5)
        rows = np.nonzero(np.any(composite(window)[:, 100] != before[:, 100], axis=1))[0]
        assert rows.max() - rows.min() + 1 <= 12, "half pressure, half the width"

    def test_pressure_is_ignored_when_disabled(self, window):
        dm = window.image_viewer.drawing_manager
        dm.paint_size = 20
        before = composite(window).copy()
        stroke(window, [(40, 40), (160, 40)], pressure=0.5)
        rows = np.nonzero(np.any(composite(window)[:, 100] != before[:, 100], axis=1))[0]
        assert rows.max() - rows.min() + 1 >= 19


class TestRestore:
    def test_restore_brings_back_the_original_under_a_clean(self, qapp, window, monkeypatch):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainterPath

        def inpaint_image(image, mask, config, blk_list=None):
            out = image.copy()
            out[mask > 0] = (200, 200, 200)
            return out

        monkeypatch.setattr(window.pipeline.inpainting, "inpaint_image", inpaint_image)
        path = QPainterPath()
        path.addRect(QRectF(95, 95, 110, 50))
        window.image_viewer.selection.combine(path)
        window.selection_ctrl.clean_selection()
        assert _pump_until(qapp, lambda: window.loading.isHidden())
        window.image_viewer.selection.deselect()
        assert (composite(window)[120, 150] == 200).all(), "the lettering was cleaned"

        stroke(window, [(110, 120), (140, 120)], mode="restore")
        after = composite(window)
        assert (after[120, 115:135] == 20).all(), "brushed back to the original"
        assert (after[120, 170:190] == 200).all(), "the rest stays cleaned"


class TestEyedropper:
    def test_it_samples_the_page_into_the_paint_colour(self, window):
        picked = []
        window.image_viewer.colour_picked.connect(picked.append)
        colour = window.image_viewer.drawing_manager.eyedrop(QPointF(150, 120))
        assert (colour.red(), colour.green(), colour.blue()) == (20, 20, 20)
        assert picked and picked[0].name() == "#141414"
        assert window.paint_colour_button.property("selected_color") == "#141414"

    def test_alt_click_while_painting_samples_instead_of_painting(self, window):
        from PySide6.QtCore import QEvent, QPoint
        from PySide6.QtGui import QMouseEvent

        viewer = window.image_viewer
        window.set_tool("paint")
        stack = window.undo_group.activeStack()
        count = stack.count()
        pos = viewer.mapFromScene(QPointF(150, 120))
        event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(pos), QPointF(viewer.mapToGlobal(QPoint(pos))),
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier)
        viewer.event_handler.handle_mouse_press(event)
        assert viewer.drawing_manager.paint_colour.name() == "#141414"
        assert viewer.drawing_manager.pixel_session is None
        assert stack.count() == count


def test_a_tablet_event_records_its_pressure(window):
    from PySide6.QtCore import QEvent, QPointF as P
    from PySide6.QtGui import QInputDevice, QPointingDevice, QTabletEvent

    device = QPointingDevice("stylus", 42, QInputDevice.DeviceType.Stylus, QPointingDevice.PointerType.Pen,
                             QInputDevice.Capability.Position | QInputDevice.Capability.Pressure, 1, 3)
    viewer = window.image_viewer

    def send(kind, pressure):
        event = QTabletEvent(kind, device, P(10, 10), P(10, 10), pressure, 0, 0, 0, 0, 0,
                             Qt.KeyboardModifier.NoModifier, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        viewer.event_handler.handle_viewport_event(event)
        return event

    event = send(QEvent.Type.TabletPress, 0.35)
    assert viewer.tablet_pressure == pytest.approx(0.35)
    assert not event.isAccepted(), "left for Qt to turn into a mouse event"
    send(QEvent.Type.TabletMove, 0.8)
    assert viewer.tablet_pressure == pytest.approx(0.8)
    send(QEvent.Type.TabletRelease, 0.0)
    assert viewer.tablet_pressure is None


def test_paint_settings_persist(qapp, tmp_path):
    import controller as controller_mod

    win = controller_mod.ComicTranslate()
    try:
        win.paint_opacity_spin.setValue(40)
        win.paint_hardness_slider.setValue(25)
        win.paint_pressure_flow_check.setChecked(True)
        dm = win.image_viewer.drawing_manager
        assert (dm.paint_opacity, dm.paint_hardness, dm.paint_pressure_flow) == (0.4, 0.25, True)
        from PySide6.QtCore import QSettings

        settings = QSettings("ComicLabs", "ComicTranslate")
        settings.beginGroup("paint")
        assert settings.value("opacity", type=int) == 40
        assert settings.value("hardness", type=int) == 25
        assert settings.value("pressure_flow", type=bool) is True
        settings.endGroup()
    finally:
        win._skip_close_prompt = True
        win.close()


def test_in_webtoon_mode_a_stroke_on_page_two_lands_on_page_two(qapp, tmp_path):
    from PIL import Image

    import controller as controller_mod

    win = controller_mod.ComicTranslate()
    try:
        win.resize(1200, 800)
        win.show()
        paths = []
        for index in range(2):
            path = str(tmp_path / f"{index:03d}.png")
            Image.fromarray(np.full((300, 240, 3), 180, np.uint8)).save(path)
            paths.append(path)
        win.image_ctrl.thread_load_images(paths)
        assert _pump_until(qapp, lambda: win.image_viewer.hasPhoto() and len(win.image_files) == 2)
        win.show_main_page()
        win.webtoon_toggle.click()
        viewer = win.image_viewer
        assert _pump_until(qapp, lambda: viewer.webtoon_mode and len(viewer.webtoon_manager.loaded_pages) == 2)
        top = viewer.webtoon_manager.layout_manager.image_positions[1]
        viewer.centerOn(120, top + 40)
        qapp.processEvents()
        _, mappings = viewer.get_visible_area_image()
        page_two = next(m for m in mappings if m["page_index"] == 1)
        y = page_two["scene_y_start"] + 15
        assert page_two["scene_y_end"] - y > 20

        dm = viewer.drawing_manager
        dm.paint_colour, dm.paint_size, dm.paint_hardness = QColor(255, 0, 0), 8, 1.0
        stroke(win, [(40, y), (120, y)])
        first, second = win.image_files
        assert win.image_patches.get(first, []) == []
        patches = win.image_patches.get(second, [])
        assert len(patches) == 1
        x, py, w, h = patches[0]["bbox"]
        assert py <= y - top <= py + h
    finally:
        win._skip_close_prompt = True
        win.close()
