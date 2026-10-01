"""The canvas selection: maths, tools, what it must never be mistaken for, and
cleaning only what it covers.

A selection is marching ants over the page. It is not a mask stroke, a layer
or anything saved — dozens of places treat any path item as a stroke, which is
why the overlay is not one, and several tests here pin that it stays out of
each of them.
"""

import numpy as np
import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainterPath, QUndoStack

from core import selection as ops


def square(size=60, lo=20, hi=40):
    mask = np.zeros((size, size), np.uint8)
    mask[lo:hi, lo:hi] = 255
    return mask


# -- core maths ------------------------------------------------------------------


class TestMaths:
    def test_grow_and_shrink_move_the_outline_by_the_radius(self):
        assert ops.bbox(ops.grow(square(), 3)) == (17, 17, 26, 26)
        assert ops.bbox(ops.shrink(square(), 3)) == (23, 23, 14, 14)
        assert ops.bbox(ops.grow(square(), 0)) == (20, 20, 20, 20)

    def test_shrink_does_not_eat_in_from_the_edge_of_the_crop(self):
        mask = np.zeros((20, 20), np.uint8)
        mask[:, :10] = 255  # touches the top, left and bottom of the array
        assert ops.bbox(ops.shrink(mask, 3)) == (0, 0, 7, 20)

    def test_smooth_drops_specks_and_spurs(self):
        mask = square()
        mask[29:31, 40:50] = 255   # a two-pixel spur
        mask[5, 5] = 255           # a speck
        out = ops.smooth(mask, 3)
        assert ops.bbox(out) == (20, 20, 20, 20)
        assert out[5, 5] == 0

    def test_invert(self):
        assert ops.invert(square()).sum() == (60 * 60 - 400) * 255

    def test_feather_is_exact_away_from_the_edge(self):
        alpha = ops.feather_alpha(square(), 4)
        assert alpha[30, 30] == 1.0          # well inside
        assert alpha[30, 25] == 1.0          # more than 4 px inside
        assert alpha[30, 10] == 0.0          # well outside
        assert alpha[30, 15] == 0.0          # more than 4 px outside
        assert 0.0 < alpha[30, 19] < alpha[30, 20] < 1.0

    def test_feather_rises_monotonically_across_the_edge(self):
        row = ops.feather_alpha(square(), 5)[30, 10:30]
        assert np.all(np.diff(row) >= 0)

    def test_no_feather_is_the_hard_mask(self):
        alpha = ops.feather_alpha(square(), 0)
        assert set(np.unique(alpha)) == {0.0, 1.0}

    def test_blend_keeps_the_original_where_alpha_is_zero(self):
        original = np.full((10, 10, 3), 40, np.uint8)
        edited = np.full((10, 10, 3), 240, np.uint8)
        alpha = np.zeros((10, 10), np.float32)
        alpha[:, 5:] = 1.0
        alpha[:, 4] = 0.5
        out = ops.blend(original, edited, alpha)
        assert (out[:, :4] == 40).all() and (out[:, 5:] == 240).all()
        assert (out[:, 4] == 140).all()
        assert out.dtype == np.uint8

    def test_bbox_of_nothing(self):
        assert ops.bbox(np.zeros((5, 5), np.uint8)) is None


# -- the canvas ------------------------------------------------------------------


@pytest.fixture
def viewer(qapp):
    from app.ui.canvas.image_viewer import ImageViewer

    view = ImageViewer(None)
    view.resize(400, 300)
    page = np.full((120, 200, 3), 128, dtype=np.uint8)
    page[20:70, 20:100] = 0        # bubble outline
    page[24:66, 24:96] = 255       # bubble interior
    page[40:50, 50:70] = 0         # lettering inside the bubble
    page[80:110, 130:190] = 255    # a second, separate white area
    view.display_image_array(page)
    stack = QUndoStack()
    view.command_emitted.connect(stack.push)
    view._test_stack = stack
    yield view
    view.close()


def selected(view):
    return view.selection.rasterise(0, 0, 200, 120) > 0


def path_items(view):
    from PySide6.QtWidgets import QGraphicsPathItem

    return [i for i in view._scene.items() if isinstance(i, QGraphicsPathItem)]


class TestRoundTrip:
    @pytest.mark.parametrize("shape", ["square", "disc", "hole", "diagonal"])
    def test_mask_to_path_to_mask_is_exact(self, qapp, shape):
        from app.ui.canvas.selection import path_from_mask, rasterise_path

        yy, xx = np.mgrid[:60, :60]
        masks = {
            "square": square(),
            "disc": (((xx - 30) ** 2 + (yy - 30) ** 2) < 15 ** 2).astype(np.uint8) * 255,
            "diagonal": (np.abs(xx - yy) < 4).astype(np.uint8) * 255,
        }
        hole = square()
        hole[27:33, 27:33] = 0
        masks["hole"] = hole
        mask = masks[shape]
        again = rasterise_path(path_from_mask(mask), 0, 0, 60, 60)
        assert np.array_equal(again > 0, mask > 0)

    def test_refining_twice_does_not_creep(self, viewer):
        """Each refine goes mask → path → mask; any bias would compound."""
        path = QPainterPath()
        path.addRect(QRectF(30, 30, 40, 30))
        viewer.selection.combine(path)
        before = selected(viewer).sum()
        viewer.selection.refine("grow", 2)
        viewer.selection.refine("shrink", 2)
        assert selected(viewer).sum() == before


class TestTools:
    def test_the_wand_makes_a_selection_not_a_stroke(self, viewer):
        viewer.drawing_manager.flood_fill_at(QPointF(40, 30))
        assert path_items(viewer) == []
        sel = selected(viewer)
        assert sel[30, 40] and sel[45, 60], "the bubble and its lettering are selected"
        assert not sel[5, 5] and not sel[90, 150]

    def test_shift_adds_and_alt_takes_away(self, viewer):
        viewer.drawing_manager.flood_fill_at(QPointF(40, 30))
        viewer.drawing_manager.flood_fill_at(QPointF(150, 90), modifiers=Qt.KeyboardModifier.ShiftModifier)
        sel = selected(viewer)
        assert sel[30, 40] and sel[90, 150]

        viewer.drawing_manager.flood_fill_at(QPointF(40, 30), modifiers=Qt.KeyboardModifier.AltModifier)
        sel = selected(viewer)
        assert not sel[30, 40] and sel[90, 150]

    def test_a_plain_click_replaces(self, viewer):
        viewer.drawing_manager.flood_fill_at(QPointF(40, 30))
        viewer.drawing_manager.flood_fill_at(QPointF(150, 90))
        sel = selected(viewer)
        assert not sel[30, 40] and sel[90, 150]

    def test_every_change_is_one_undo_step(self, viewer):
        stack = viewer._test_stack
        viewer.drawing_manager.flood_fill_at(QPointF(40, 30))
        viewer.drawing_manager.flood_fill_at(QPointF(150, 90), modifiers=Qt.KeyboardModifier.ShiftModifier)
        assert stack.count() == 2
        stack.undo()
        sel = selected(viewer)
        assert sel[30, 40] and not sel[90, 150]
        stack.undo()
        assert viewer.selection.is_empty()
        stack.redo()
        assert selected(viewer)[30, 40]

    def test_the_lasso_polygon_closes_into_a_selection(self, viewer):
        dm = viewer.drawing_manager
        for point in (QPointF(10, 10), QPointF(60, 10), QPointF(60, 60), QPointF(10, 60)):
            dm.lasso_press(point)
            dm.lasso_release(point)
        dm.lasso_close()
        sel = selected(viewer)
        assert sel[30, 30] and not sel[80, 80]
        assert path_items(viewer) == []

    def test_lasso_modifiers_are_read_when_the_outline_starts(self, viewer):
        viewer.drawing_manager.flood_fill_at(QPointF(150, 90))
        dm = viewer.drawing_manager
        points = (QPointF(10, 10), QPointF(60, 10), QPointF(60, 60), QPointF(10, 60))
        dm.lasso_press(points[0], Qt.KeyboardModifier.ShiftModifier)
        dm.lasso_release(points[0])
        for point in points[1:]:
            dm.lasso_press(point)  # no modifier held any more
            dm.lasso_release(point)
        dm.lasso_close()
        sel = selected(viewer)
        assert sel[30, 30] and sel[90, 150], "the polygon was added, not substituted"

    def test_the_marquee_selects_a_rectangle_and_a_click_deselects(self, viewer):
        dm = viewer.drawing_manager
        dm.marquee_press(QPointF(10, 10))
        dm.marquee_move(QPointF(50, 40))
        assert dm.marquee_preview is not None
        dm.marquee_release(QPointF(50, 40))
        assert dm.marquee_preview is None
        sel = selected(viewer)
        assert sel[20, 20] and not sel[45, 60]

        dm.marquee_press(QPointF(100, 100))
        dm.marquee_release(QPointF(101, 100))
        assert viewer.selection.is_empty()

    def test_the_marquee_is_clipped_to_the_page(self, viewer):
        dm = viewer.drawing_manager
        dm.marquee_press(QPointF(-50, -50))
        dm.marquee_release(QPointF(500, 500))
        rect = viewer.selection.path.boundingRect()
        assert rect.left() >= 0 and rect.top() >= 0
        assert rect.right() <= 200 and rect.bottom() <= 120

    def test_mask_mode_still_paints_a_stroke(self, viewer):
        viewer.drawing_manager.region_output = "mask"
        viewer.drawing_manager.flood_fill_at(QPointF(40, 30))
        assert len(path_items(viewer)) == 1
        assert viewer.selection.is_empty()


class TestActions:
    def test_select_all_and_invert(self, viewer):
        viewer.selection.select_all()
        assert selected(viewer).all()
        viewer.selection.deselect()
        viewer.drawing_manager.flood_fill_at(QPointF(40, 30))
        before = selected(viewer)
        viewer.selection.invert()
        after = selected(viewer)
        assert not after[30, 40] and after[5, 5]
        assert not (before & after).any()

    def test_refine_operations_are_undoable(self, viewer):
        path = QPainterPath()
        path.addRect(QRectF(40, 40, 40, 40))
        viewer.selection.combine(path)
        area = selected(viewer).sum()
        viewer.selection.refine("grow", 4)
        assert selected(viewer).sum() > area
        viewer.selection.refine("feather", 3)
        assert viewer.selection.feather == 3
        viewer._test_stack.undo()
        assert viewer.selection.feather == 0
        viewer._test_stack.undo()
        assert selected(viewer).sum() == area

    def test_the_feathered_alpha_reaches_past_the_image_edge(self, viewer):
        path = QPainterPath()
        path.addRect(QRectF(0, 0, 50, 50))
        viewer.selection.combine(path)
        viewer.selection.refine("feather", 4)
        alpha = viewer.selection.alpha(0, 0, 200, 120)
        assert alpha[0, 0] == 1.0, "a selection touching the edge is not faded at the edge"
        assert 0.0 < alpha[25, 50] < 1.0
        assert alpha[25, 60] == 0.0


class TestNotAStroke:
    def make_selection(self, viewer):
        viewer.drawing_manager.flood_fill_at(QPointF(40, 30))
        assert not viewer.selection.is_empty()

    def test_the_overlay_is_not_a_path_item(self, viewer):
        from PySide6.QtWidgets import QGraphicsPathItem

        self.make_selection(viewer)
        overlay = viewer.selection.overlay()
        assert overlay is not None and overlay.scene() is viewer._scene
        assert not isinstance(overlay, QGraphicsPathItem)

    def test_it_is_not_in_the_mask_or_the_saved_strokes(self, viewer):
        self.make_selection(viewer)
        assert not viewer.has_drawn_elements()
        assert viewer.get_mask_for_inpainting() is None
        assert viewer.drawing_manager.save_brush_strokes() == []

    def test_it_is_not_a_layer(self, viewer):
        from app.ui.canvas.scene_registry import iter_items, kind_of

        self.make_selection(viewer)
        overlay = viewer.selection.overlay()
        assert kind_of(overlay) is None
        assert overlay not in list(iter_items(viewer._scene, viewer=viewer))

    def test_it_never_takes_a_click(self, viewer):
        self.make_selection(viewer)
        view_pos = viewer.mapFromScene(QPointF(40, 30))
        assert viewer.event_handler._item_at(view_pos) is not viewer.selection.overlay()
        assert viewer.selection.overlay().shape().isEmpty()

    def test_loading_another_page_drops_it(self, viewer):
        self.make_selection(viewer)
        viewer.display_image_array(np.full((50, 50, 3), 10, np.uint8))
        assert viewer.selection.is_empty()
        assert viewer.selection.overlay() is None
        # and a new one works on the new page
        viewer.selection.select_all()
        assert viewer.selection.overlay().scene() is viewer._scene

    def test_the_ants_march(self, viewer):
        self.make_selection(viewer)
        overlay = viewer.selection.overlay()
        start = overlay._dash_offset
        viewer.selection._timer.timeout.emit()
        viewer.show()
        viewer.selection._tick()
        assert overlay._dash_offset != start
        viewer.selection.deselect()
        assert not viewer.selection._timer.isActive()


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
    # Textured artwork, so a selection on it has no flat background to keep
    # and is inpainted whole (the flat case has its own tests below).
    page = np.random.default_rng(7).integers(0, 200, (160, 240, 3), dtype=np.uint8)
    page[100:150, 150:230] = 255     # a white bubble...
    page[115:125, 165:215] = 20      # ...with lettering on it
    path = str(tmp_path / "page.png")
    Image.fromarray(page).save(path)
    win.image_ctrl.thread_load_images([path])
    assert _pump_until(qapp, lambda: win.image_viewer.hasPhoto() and win.undo_group.activeStack() is not None)
    yield win
    win._skip_close_prompt = True
    win.close()


def _fake_inpainter(monkeypatch, window, colour=(0, 255, 0)):
    calls = []

    def inpaint_image(image, mask, config, blk_list=None):
        calls.append((mask > 0).sum())
        out = image.copy()
        out[mask > 0] = colour
        return out

    monkeypatch.setattr(window.pipeline.inpainting, "inpaint_image", inpaint_image)
    return calls


def _select_rect(window, x, y, w, h):
    path = QPainterPath()
    path.addRect(QRectF(x, y, w, h))
    window.image_viewer.selection.combine(path)


class TestInTheWindow:
    def test_the_action_buttons_follow_the_selection(self, window):
        buttons = window.selection_action_buttons
        assert not any(b.isEnabled() for b in buttons)
        _select_rect(window, 10, 10, 30, 30)
        assert all(b.isEnabled() for b in buttons)
        window.deselect_button.click()
        assert not any(b.isEnabled() for b in buttons)

    def test_the_pick_into_switch_sets_the_output(self, window):
        dm = window.image_viewer.drawing_manager
        assert dm.region_output == "selection"
        window.region_to_mask_button.click()
        assert dm.region_output == "mask"
        window.region_to_selection_button.click()
        assert dm.region_output == "selection"

    def test_clean_selection_changes_only_what_is_selected(self, qapp, window, monkeypatch):
        calls = _fake_inpainter(monkeypatch, window)
        viewer = window.image_viewer
        # A mask stroke elsewhere must survive: Clean Selection is not Clean.
        viewer.drawing_manager.region_output = "mask"
        viewer.drawing_manager.flood_fill_at(QPointF(5, 150))
        strokes_before = len(viewer.drawing_manager.save_brush_strokes())
        assert strokes_before == 1

        _select_rect(window, 70, 45, 40, 20)
        before = viewer.get_image_array(include_patches=True).copy()
        assert window.selection_ctrl.clean_selection()
        assert _pump_until(qapp, lambda: window.loading.isHidden())
        after = viewer.get_image_array(include_patches=True)

        assert calls, "the inpainter never ran"
        inside = after[50:60, 75:105]
        assert (inside == (0, 255, 0)).all()
        changed = np.any(after != before, axis=2)
        ys, xs = np.nonzero(changed)
        assert xs.min() >= 70 and xs.max() <= 110 and ys.min() >= 45 and ys.max() <= 65
        assert len(viewer.drawing_manager.save_brush_strokes()) == strokes_before
        assert not viewer.selection.is_empty(), "the selection stays for the next action"

    def test_a_feathered_clean_blends_at_the_edge(self, qapp, window, monkeypatch):
        _fake_inpainter(monkeypatch, window, colour=(255, 255, 255))
        viewer = window.image_viewer
        _select_rect(window, 60, 40, 80, 40)
        viewer.selection.refine("feather", 4)
        before = viewer.get_image_array(include_patches=True).copy()
        window.selection_ctrl.clean_selection()
        assert _pump_until(qapp, lambda: window.loading.isHidden())
        after = viewer.get_image_array(include_patches=True)
        assert (after[60, 100] == 255).all(), "the middle is fully cleaned"
        edge = int(after[60, 60].astype(int).sum())
        assert int(before[60, 60].astype(int).sum()) < edge < 3 * 255, "the edge is a mix, not a hard cut"
        assert (after[60, 50] == before[60, 50]).all(), "beyond the feather nothing moved"

    def test_clean_selection_is_one_undo_step(self, qapp, window, monkeypatch):
        _fake_inpainter(monkeypatch, window)
        viewer = window.image_viewer
        _select_rect(window, 70, 45, 40, 20)
        before = viewer.get_image_array(include_patches=True).copy()
        stack = window.undo_group.activeStack()
        count = stack.count()
        window.selection_ctrl.clean_selection()
        assert _pump_until(qapp, lambda: window.loading.isHidden())
        assert stack.count() == count + 1
        stack.undo()
        assert np.array_equal(viewer.get_image_array(include_patches=True), before)

    def test_to_mask_makes_one_stroke_and_one_undo_step(self, window):
        viewer = window.image_viewer
        _select_rect(window, 70, 45, 40, 20)
        stack = window.undo_group.activeStack()
        count = stack.count()
        window.selection_to_mask_button.click()
        assert len(viewer.drawing_manager.save_brush_strokes()) == 1
        assert viewer.selection.is_empty()
        assert stack.count() == count + 1
        stack.undo()
        assert viewer.drawing_manager.save_brush_strokes() == []
        assert not viewer.selection.is_empty()

    def test_the_clean_step_cleans_a_selection_when_nothing_else_is_marked(self, qapp, window, monkeypatch):
        calls = _fake_inpainter(monkeypatch, window)
        _select_rect(window, 70, 45, 40, 20)
        window.manual_workflow_ctrl.inpaint_and_set()
        assert _pump_until(qapp, lambda: window.loading.isHidden())
        assert calls
        assert (window.image_viewer.get_image_array(include_patches=True)[55, 90] == (0, 255, 0)).all()

    def test_a_bubble_keeps_its_white_and_loses_its_lettering(self, qapp, window, monkeypatch):
        """The wand selects a bubble's white with its lettering; inpainting all
        of it smears the bubble. On an even background only the lettering is
        cleaned, painted in the background colour — no model needed."""
        calls = _fake_inpainter(monkeypatch, window)
        viewer = window.image_viewer
        _select_rect(window, 150, 100, 80, 50)
        before = viewer.get_image_array(include_patches=True).copy()
        window.selection_ctrl.clean_selection()
        assert _pump_until(qapp, lambda: window.loading.isHidden())
        after = viewer.get_image_array(include_patches=True)
        assert calls == [], "an even bubble does not need the inpainter"
        assert (after[100:150, 150:230] == 255).all(), "the lettering is gone and the white untouched"
        assert np.array_equal(after[:95], before[:95])

    def test_a_flat_selection_with_nothing_on_it_is_left_alone(self, qapp, window, monkeypatch):
        calls = _fake_inpainter(monkeypatch, window)
        viewer = window.image_viewer
        _select_rect(window, 152, 102, 60, 10)   # white only, above the lettering
        stack = window.undo_group.activeStack()
        count = stack.count()
        window.selection_ctrl.clean_selection()
        assert _pump_until(qapp, lambda: window.loading.isHidden())
        assert calls == [] and stack.count() == count

    def test_ctrl_a_while_typing_on_the_canvas_does_not_select_the_page(self, window):
        from app.ui.canvas.text_item import TextBlockItem

        window.show_main_page()
        item = TextBlockItem(text="HELLO")
        window.image_viewer._scene.addItem(item)
        item.editing_mode = True
        window.image_viewer._scene.setFocusItem(item)
        window.shortcut_ctrl._activate_shortcut("select_all")
        assert window.image_viewer.selection.is_empty()

        item.editing_mode = False
        window.image_viewer._scene.clearFocus()
        window.shortcut_ctrl._activate_shortcut("select_all")
        assert not window.image_viewer.selection.is_empty()
        window.shortcut_ctrl._activate_shortcut("invert_selection")
        assert window.image_viewer.selection.is_empty()


def test_in_webtoon_mode_a_selection_on_page_two_cleans_page_two(qapp, tmp_path, monkeypatch):
    """The path is in scene coordinates; the clean runs on the loaded slice and
    the patch is mapped back to the page it lies on, in that page's own
    coordinates."""
    from PIL import Image

    import controller as controller_mod

    win = controller_mod.ComicTranslate()
    try:
        win.resize(1200, 800)
        win.show()
        paths = []
        rng = np.random.default_rng(3)
        for index in range(2):
            path = str(tmp_path / f"{index:03d}.png")
            # Textured, so the selection is inpainted whole rather than judged
            # a flat area with nothing on it.
            Image.fromarray(rng.integers(0, 200, (300, 240, 3), dtype=np.uint8)).save(path)
            paths.append(path)
        win.image_ctrl.thread_load_images(paths)
        assert _pump_until(qapp, lambda: win.image_viewer.hasPhoto() and len(win.image_files) == 2)
        win.show_main_page()
        win.webtoon_toggle.click()
        viewer = win.image_viewer
        assert _pump_until(qapp, lambda: viewer.webtoon_mode and len(viewer.webtoon_manager.loaded_pages) == 2)

        top_of_page_two = viewer.webtoon_manager.layout_manager.image_positions[1]
        viewer.centerOn(120, top_of_page_two + 40)
        qapp.processEvents()
        _, mappings = viewer.get_visible_area_image()
        page_two = next(m for m in mappings if m["page_index"] == 1)
        y = page_two["scene_y_start"] + 5
        assert page_two["scene_y_end"] - y > 30, "page two is not loaded far enough to test"

        _fake_inpainter(monkeypatch, win)
        _select_rect(win, 50, y, 60, 25)
        assert win.selection_ctrl.clean_selection()
        assert _pump_until(qapp, lambda: win.loading.isHidden())

        first, second = win.image_files
        assert win.image_patches.get(first, []) == []
        patches = win.image_patches.get(second, [])
        assert len(patches) == 1
        x, py, w, h = patches[0]["bbox"]
        assert (x, w, h) == (50, 60, 25)
        assert py == int(y - top_of_page_two)
    finally:
        win._skip_close_prompt = True
        win.close()


class TestPlanClean:
    def page(self):
        page = np.full((60, 80, 3), 250, np.uint8)
        page[25:35, 20:60] = 10
        return page

    def test_a_flat_area_cleans_only_what_differs_from_it(self):
        alpha = np.zeros((60, 80), np.float32)
        alpha[10:50, 10:70] = 1.0
        plan = ops.plan_clean(self.page(), alpha)
        assert plan["mode"] == "solid" and plan["colour"] == (250, 250, 250)
        mask = plan["mask"] > 0
        assert mask[30, 40] and mask[24, 40], "the lettering, grown past its edge"
        assert not mask[15, 15], "the background is kept"
        assert not (mask & (alpha == 0)).any(), "never outside the selection"

    def test_a_noisy_background_is_inpainted_not_painted(self):
        rng = np.random.default_rng(1)
        page = (240 + rng.integers(-20, 15, (60, 80, 3))).clip(0, 255).astype(np.uint8)
        page[25:35, 20:60] = 10
        alpha = np.zeros((60, 80), np.float32)
        alpha[10:50, 10:70] = 1.0
        plan = ops.plan_clean(page, alpha)
        assert plan["mode"] == "inpaint"
        assert not (plan["mask"] > 0)[12, 12]

    def test_artwork_is_inpainted_whole(self):
        page = np.random.default_rng(2).integers(0, 255, (60, 80, 3), dtype=np.uint8)
        alpha = np.zeros((60, 80), np.float32)
        alpha[10:50, 10:70] = 1.0
        plan = ops.plan_clean(page, alpha)
        assert plan["mode"] == "inpaint"
        assert np.array_equal(plan["mask"] > 0, alpha > 0)

    def test_nothing_on_a_flat_area_means_nothing_to_do(self):
        alpha = np.zeros((60, 80), np.float32)
        alpha[2:20, 2:70] = 1.0
        assert ops.plan_clean(self.page(), alpha)["mode"] == "none"

    def test_lettering_that_fills_most_of_a_tight_selection_is_still_lettering(self):
        """A snug marquee around a bold SFX is mostly SFX. The background is
        the colour around it inside the selection, not the majority colour —
        judged by the majority, the SFX became the background and nothing was
        cleaned."""
        page = np.full((60, 120, 3), 200, np.uint8)
        page[20:40, 20:100] = 20          # 73% of the selection below
        alpha = np.zeros((60, 120), np.float32)
        alpha[15:45, 15:105] = 1.0
        plan = ops.plan_clean(page, alpha)
        assert plan["mode"] == "solid" and plan["colour"] == (200, 200, 200)
        assert (plan["mask"] > 0)[30, 60]

    def test_an_outline_the_selection_overlaps_is_not_lettering(self):
        """The wand grows its region over the anti-aliased edge, so a bubble
        selection includes a sliver of the outline. Painting that white would
        thin the bubble's line."""
        page = np.full((60, 80, 3), 250, np.uint8)
        page[:, :6] = 0                 # the outline, at the left of the selection
        page[25:35, 30:60] = 10         # lettering
        alpha = np.zeros((60, 80), np.float32)
        alpha[10:50, 3:70] = 1.0        # overlaps the outline by 3 px
        plan = ops.plan_clean(page, alpha)
        mask = plan["mask"] > 0
        assert mask[30, 40], "the lettering is still cleaned"
        assert not mask[:, :6].any(), "the outline is left alone"

    def test_lettering_running_off_the_page_is_still_cleaned(self):
        page = np.full((60, 80, 3), 250, np.uint8)
        page[25:35, 0:30] = 10          # cut off by the page edge
        alpha = np.zeros((60, 80), np.float32)
        alpha[10:50, 0:70] = 1.0
        assert (ops.plan_clean(page, alpha)["mask"] > 0)[30, 5]



def test_a_webtoon_page_above_changing_height_moves_the_selection_with_its_page(viewer):
    """A page loads taller than estimated and everything below it moves down;
    a selection is a path, not an item, so it has to be moved explicitly."""
    from types import SimpleNamespace

    from app.ui.canvas.webtoons.image_loader import LazyImageLoader

    path = QPainterPath()
    path.addRect(QRectF(20, 60, 40, 20))
    viewer.selection.combine(path)
    top = viewer.selection.path.boundingRect().top()

    loader = LazyImageLoader.__new__(LazyImageLoader)
    loader._scene = viewer._scene
    loader.viewer = viewer
    loader.image_items = {}
    loader.placeholder_items = {}
    loader.layout_manager = SimpleNamespace(image_positions=[0.0, 50.0 + 30])
    loader._adjust_scene_items_for_layout_change(0, 30)   # page 0 grew by 30
    assert viewer.selection.path.boundingRect().top() == top + 30
    assert viewer.selection.overlay()._path.boundingRect().top() == top + 30

    loader._adjust_scene_items_for_layout_change(1, 30)   # a page below it: no move
    assert viewer.selection.path.boundingRect().top() == top + 30
