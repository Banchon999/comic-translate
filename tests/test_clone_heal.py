"""The clone stamp and the healing brush. Both are pixel tools: a stroke ends
as ordinary patches (one undo step, the raw image untouched, the selection
respected) — what they add is *where* the paint comes from."""

import numpy as np
import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainterPath

from core import paint


def _pump_until(qapp, condition, timeout_ms=20000):
    from PySide6.QtCore import QElapsedTimer

    timer = QElapsedTimer()
    timer.start()
    while not condition() and timer.elapsed() < timeout_ms:
        qapp.processEvents()
    return condition()


def page_image():
    """Left: a grey texture around 100 (something worth cloning). Right: flat
    200 with a dark mark on it (something worth healing away)."""
    rng = np.random.default_rng(7)
    page = np.full((200, 300, 3), 200, np.uint8)
    texture = 100 + rng.integers(-12, 13, (200, 120, 1))
    page[:, :120] = np.repeat(texture, 3, axis=2).astype(np.uint8)
    page[90:110, 200:240] = 10
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
    dm.paint_size = 30
    dm.paint_hardness = 1.0
    dm.paint_opacity = 1.0
    dm.paint_pressure_size = dm.paint_pressure_flow = False
    dm.clone_aligned, dm.clone_lock = True, False
    yield win
    win._skip_close_prompt = True
    win.close()


def composite(win):
    return win.image_viewer.get_image_array(include_patches=True)


def stroke(win, mode, points):
    dm = win.image_viewer.drawing_manager
    if not dm.pixel_press(QPointF(*points[0]), mode):
        return None
    for point in points[1:]:
        dm.pixel_move(QPointF(*point))
    return dm.pixel_release()


def marker_of(win):
    marker = win.image_viewer.drawing_manager.clone_marker
    return marker if marker is not None and marker.scene() is not None else None


# -- maths ---------------------------------------------------------------------

class TestShifted:
    def test_it_samples_the_given_distance_away(self):
        image = np.arange(60, dtype=np.uint8).reshape(6, 10)
        out, valid = paint.shifted(image, 3, -2)
        assert out[2, 0] == image[0, 3] and out[5, 6] == image[3, 9]
        assert valid[2:, :7].all()
        assert not valid[:2].any() and not valid[:, 7:].any()
        assert (out[~valid] == 0).all()

    def test_a_shift_off_the_image_leaves_nothing_valid(self):
        out, valid = paint.shifted(np.ones((4, 4, 3), np.uint8), 10, 0)
        assert not valid.any() and not out.any()


class TestHeal:
    def region(self):
        region = np.zeros((120, 120), bool)
        region[45:75, 35:85] = True
        return region

    def test_the_source_detail_takes_the_destination_tone(self):
        rng = np.random.default_rng(0)
        dest = np.full((120, 120, 3), 180, np.uint8)
        dest[50:70, 40:80] = 20                                   # the mark
        texture = rng.integers(-10, 11, (120, 120, 1))
        source = np.repeat(100 + texture, 3, axis=2).astype(np.uint8)
        out = paint.heal(dest, source, self.region())
        inner = out[45:75, 35:85].astype(float)
        assert abs(inner.mean() - 180) < 2, "the tone comes from around the stroke"
        assert inner.min() > 150, "the mark inside the stroke does not bleed in"
        assert abs(inner.std() - source[45:75, 35:85].std()) < 1.5, "the texture comes from the source"
        assert np.array_equal(out[~self.region()], dest[~self.region()])

    def test_a_gradient_around_the_stroke_is_carried_through_it(self):
        dest = np.tile(np.linspace(50, 250, 120).astype(np.uint8)[None, :, None], (120, 1, 3))
        out = paint.heal(dest, np.full_like(dest, 128), self.region())
        assert np.abs(out.astype(int) - dest.astype(int)).max() <= 2

    def test_ring_pixels_with_no_source_do_not_pull_the_tone(self):
        dest = np.full((40, 40, 3), 180, np.uint8)
        source = np.full((40, 40, 3), 100, np.uint8)
        valid = np.ones((40, 40), bool)
        valid[:, :12] = False                  # sampled from beyond the page
        source[~valid] = 0
        region = np.zeros((40, 40), bool)
        region[10:30, 12:30] = True
        out = paint.heal(dest, source, region, valid=valid)
        assert abs(out[10:30, 12:30].astype(float).mean() - 180) < 2
        pinned = paint.heal(dest, source, region)
        assert pinned[20, 12].mean() > 200, "without it the zeros drag the edge"

    def test_no_region_changes_nothing(self):
        dest = np.full((10, 10, 3), 5, np.uint8)
        assert np.array_equal(paint.heal(dest, dest * 0, np.zeros((10, 10), bool)), dest)


# -- the clone stamp --------------------------------------------------------------

class TestClone:
    def test_without_a_source_it_says_how_to_set_one(self, window, monkeypatch):
        from app.controllers import paint as paint_mod

        told = []
        monkeypatch.setattr(paint_mod.MMessage, "info", lambda text, parent=None, **k: told.append(text))
        stack = window.undo_group.activeStack()
        count = stack.count()
        assert stroke(window, "clone", [(210, 100), (230, 100)]) is None
        assert len(told) == 1 and "Alt" in told[0]
        assert stack.count() == count

    def test_alt_click_sets_the_source_and_shows_where_it_is(self, window):
        from PySide6.QtCore import QEvent, QPoint
        from PySide6.QtGui import QMouseEvent

        from app.ui.canvas.scene_registry import iter_items, kind_of

        viewer = window.image_viewer
        window.set_tool("clone")
        pos = viewer.mapFromScene(QPointF(50, 100))
        event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(pos), QPointF(viewer.mapToGlobal(QPoint(pos))),
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier)
        viewer.event_handler.handle_mouse_press(event)
        dm = viewer.drawing_manager
        assert dm.clone_source is not None
        # Within one screen pixel (the unshown view is zoomed well out).
        slack = 1.0 / viewer.transform().m11() + 0.5
        assert abs(dm.clone_source.x() - 50) <= slack and abs(dm.clone_source.y() - 100) <= slack
        marker = marker_of(window)
        assert marker is not None and kind_of(marker) is None
        assert marker not in list(iter_items(viewer._scene, viewer=viewer)), "not a layer"
        assert dm.pixel_session is None, "an Alt+click paints nothing"

    def test_a_stroke_copies_from_the_source_exactly(self, window):
        before = composite(window).copy()
        stack = window.undo_group.activeStack()
        count = stack.count()
        window.image_viewer.drawing_manager.set_clone_source(QPointF(50, 100))
        assert stroke(window, "clone", [(205, 100), (235, 100)]) is not None
        after = composite(window)
        # The mark is covered by the texture 155 px to its left, pixel for pixel.
        assert np.array_equal(after[92:108, 205:236], before[92:108, 50:81])
        changed = np.any(after != before, axis=2)
        ys, xs = np.nonzero(changed)
        assert ys.min() >= 84 and ys.max() <= 116 and xs.min() >= 189 and xs.max() <= 251
        assert stack.count() == count + 1
        stack.undo()
        assert np.array_equal(composite(window), before)

    def test_aligned_keeps_the_offset_for_the_next_stroke(self, window):
        before = composite(window).copy()
        dm = window.image_viewer.drawing_manager
        dm.set_clone_source(QPointF(50, 100))
        stroke(window, "clone", [(205, 100)])
        stroke(window, "clone", [(205, 40)])        # same offset: samples (50, 40)
        assert np.array_equal(composite(window)[35:46, 200:211], before[35:46, 45:56])

    def test_not_aligned_every_stroke_starts_at_the_source_again(self, window):
        before = composite(window).copy()
        dm = window.image_viewer.drawing_manager
        dm.clone_aligned = False
        dm.set_clone_source(QPointF(50, 100))
        stroke(window, "clone", [(205, 100)])
        stroke(window, "clone", [(205, 40)])        # samples (50, 100) again
        assert np.array_equal(composite(window)[35:46, 200:211], before[95:106, 45:56])

    def test_a_locked_offset_survives_a_new_page_and_an_unlocked_one_does_not(self, window, monkeypatch):
        from app.controllers import paint as paint_mod

        monkeypatch.setattr(paint_mod.MMessage, "info", lambda *a, **k: None)
        viewer = window.image_viewer
        dm = viewer.drawing_manager
        dm.set_clone_source(QPointF(50, 100))
        stroke(window, "clone", [(205, 100)])
        viewer.display_image_array(page_image())
        assert dm.pixel_press(QPointF(205, 100), "clone") is False, "the source meant the old page"

        dm.clone_lock = True
        dm.set_clone_source(QPointF(50, 100))
        stroke(window, "clone", [(205, 100)])
        viewer.display_image_array(page_image())
        assert dm.pixel_press(QPointF(205, 40), "clone") is True
        assert dm.pixel_session.clone_offset == (-155.0, 0.0)
        dm.pixel_cancel()

    def test_there_is_nothing_to_clone_beyond_the_page(self, window):
        before = composite(window).copy()
        window.image_viewer.drawing_manager.set_clone_source(QPointF(5, 100))
        stroke(window, "clone", [(220, 100)])       # samples x from -10 to 20
        after = composite(window)
        assert np.array_equal(after[100, 215:235], before[100, 0:20])  # 235 is the rim
        assert np.array_equal(after[100, 205:215], before[100, 205:215]), "no source there"

    def test_a_selection_clips_it(self, window):
        path = QPainterPath()
        path.addRect(QRectF(200, 80, 20, 40))
        window.image_viewer.selection.combine(path)
        before = composite(window).copy()
        window.image_viewer.drawing_manager.set_clone_source(QPointF(50, 100))
        stroke(window, "clone", [(205, 100), (235, 100)])
        xs = np.nonzero(np.any(composite(window) != before, axis=2))[1]
        assert xs.min() >= 199 and xs.max() <= 221

    def test_the_marker_shows_only_with_a_clone_tool_in_hand(self, window):
        dm = window.image_viewer.drawing_manager
        window.set_tool("clone")
        dm.set_clone_source(QPointF(50, 100))
        assert marker_of(window) is not None
        window.set_tool("paint")
        assert marker_of(window) is None
        window.set_tool("heal")
        assert marker_of(window) is not None
        # While painting it follows the brush at the offset.
        dm.pixel_press(QPointF(205, 100), "heal")
        dm.pixel_move(QPointF(215, 110))
        assert marker_of(window).pos() == QPointF(60, 110)
        dm.pixel_cancel()

    def test_the_preview_is_the_clone_and_is_gone_after_release(self, window):
        from app.ui.canvas.scene_registry import kind_of

        dm = window.image_viewer.drawing_manager
        dm.set_clone_source(QPointF(50, 100))
        dm.pixel_press(QPointF(205, 100), "clone")
        preview = dm.pixel_session.preview
        assert kind_of(preview) is None
        before = page_image()
        assert np.array_equal(preview._buffer[100, 205, :3], before[100, 50])
        dm.pixel_release()
        assert preview.scene() is None


# -- the healing brush -------------------------------------------------------------

class TestHealingBrush:
    def test_it_removes_the_mark_in_the_tone_around_it(self, window):
        before = composite(window).copy()
        stack = window.undo_group.activeStack()
        count = stack.count()
        window.image_viewer.drawing_manager.set_clone_source(QPointF(50, 100))
        assert stroke(window, "heal", [(205, 100), (235, 100)]) is not None
        after = composite(window)
        core = after[94:107, 205:236].astype(float)
        assert core.min() > 150, "the mark is gone"
        assert abs(core.mean() - 200) < 4, "in the page's tone, not the source's 100"
        assert core.std() > 3, "with the source's texture, not a flat smear"
        assert stack.count() == count + 1
        assert np.array_equal(after[:, :120], before[:, :120]), "the source is untouched"

    def test_the_undo_label_names_the_tool(self, window):
        window.image_viewer.drawing_manager.set_clone_source(QPointF(50, 100))
        stroke(window, "heal", [(205, 100)])
        stack = window.undo_group.activeStack()
        assert stack.text(stack.index() - 1) == "Healing Brush"


def test_the_clone_options_persist_and_show_for_both_tools(qapp):
    import controller as controller_mod
    from PySide6.QtCore import QSettings

    win = controller_mod.ComicTranslate()
    try:
        dm = win.image_viewer.drawing_manager
        win.clone_lock_check.setChecked(True)
        win.clone_aligned_check.setChecked(False)
        assert dm.clone_lock is True and dm.clone_aligned is False
        settings = QSettings("ComicLabs", "ComicTranslate")
        settings.beginGroup("paint")
        assert settings.value("clone_lock", type=bool) is True
        assert settings.value("clone_aligned", type=bool) is False
        settings.endGroup()
        for tool in ("clone", "heal"):
            win.set_tool(tool)
            assert not win.clone_aligned_check.isHidden() and not win.clone_lock_check.isHidden()
            assert win.paint_colour_button.isHidden() and not win.brush_options.isHidden()
        win.set_tool("paint")
        assert win.clone_aligned_check.isHidden()
    finally:
        win.clone_lock_check.setChecked(False)
        win.clone_aligned_check.setChecked(True)
        win._skip_close_prompt = True
        win.close()


def test_turning_aligned_off_restarts_from_the_source(qapp):
    import controller as controller_mod

    win = controller_mod.ComicTranslate()
    try:
        dm = win.image_viewer.drawing_manager
        win.clone_aligned_check.setChecked(True)
        dm.clone_offset = (-10.0, 0.0)
        win.clone_aligned_check.setChecked(False)
        assert dm.clone_offset is None
    finally:
        win.clone_aligned_check.setChecked(True)
        win._skip_close_prompt = True
        win.close()


def test_in_webtoon_mode_a_clone_on_page_two_lands_on_page_two(qapp, tmp_path):
    from PIL import Image

    import controller as controller_mod

    win = controller_mod.ComicTranslate()
    try:
        win.resize(1200, 800)
        win.show()
        paths = []
        for index in range(2):
            path = str(tmp_path / f"{index:03d}.png")
            Image.fromarray(page_image()[:, :240]).save(path)
            paths.append(path)
        win.image_ctrl.thread_load_images(paths)
        assert _pump_until(qapp, lambda: win.image_viewer.hasPhoto() and len(win.image_files) == 2)
        win.show_main_page()
        win.webtoon_toggle.click()
        viewer = win.image_viewer
        assert _pump_until(qapp, lambda: viewer.webtoon_mode and len(viewer.webtoon_manager.loaded_pages) == 2)
        top = viewer.webtoon_manager.layout_manager.image_positions[1]
        # The view scrolls the whole strip only once the first page has
        # loaded and fitted it; until then its scene rect is still page one's.
        layout = viewer.webtoon_manager.layout_manager
        assert _pump_until(qapp, lambda: viewer.sceneRect().bottom() >= layout.total_height - 1)
        # Actual size, so enough of page two is on screen whatever the
        # window's height (the fit-to-width zoom depends on it).
        viewer.resetTransform()
        viewer.centerOn(120, top + 100)
        qapp.processEvents()

        dm = viewer.drawing_manager
        dm.paint_size, dm.paint_hardness, dm.paint_opacity = 20, 1.0, 1.0
        dm.set_clone_source(QPointF(50, top + 100))
        assert stroke(win, "clone", [(215, top + 100)]) is not None
        first, second = win.image_files
        assert win.image_patches.get(first, []) == []
        patches = win.image_patches.get(second, [])
        assert len(patches) == 1
        x, y, w, h = patches[0]["bbox"]
        assert 80 <= y <= 100 and x >= 190, "in page two's own coordinates"
    finally:
        win._skip_close_prompt = True
        win.close()
