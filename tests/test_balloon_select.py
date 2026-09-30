"""The balloon tool: one click selects a speech bubble's inside, lettering
included, outline never — and it refuses rather than guesses when it cannot
tell where the bubble ends."""

import numpy as np
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QUndoStack

from modules.utils.flood_select import balloon_select


def bubble_page(gap=False, size=(400, 520)):
    """A screentone-ish page with one elliptical bubble (4 px outline) holding
    a line of lettering. With `gap`, the outline is broken on the right."""
    h, w = size
    page = np.full((h, w, 3), 150, np.uint8)
    page[::4, ::4] = 110
    yy, xx = np.mgrid[:h, :w]
    r = ((xx - 120) / 90.0) ** 2 + ((yy - 90) / 60.0) ** 2
    page[r <= 1.12] = 20                 # outline
    page[r <= 1.0] = 255                 # interior
    page[85:95, 80:160] = 15             # lettering
    if gap:
        page[80:100, 205:220] = 255      # break the outline on the right...
        page[80:100, 220:] = 255         # ...and let the white run off the page
    return page


def outline(page):
    return (page.max(axis=2) <= 25) & ~((np.arange(page.shape[0])[:, None] >= 85)
                                        & (np.arange(page.shape[0])[:, None] < 95)
                                        & (np.arange(page.shape[1])[None, :] >= 80)
                                        & (np.arange(page.shape[1])[None, :] < 160))


class TestBalloonMaths:
    def test_the_inside_and_the_lettering_but_not_the_outline(self):
        page = bubble_page()
        mask, reason = balloon_select(page, 120, 60)
        assert reason == "ok"
        sel = mask > 0
        assert sel[60, 120] and sel[90, 120], "interior and lettering"
        assert not (sel & outline(page)).any(), "the outline is never selected"
        assert not sel[5, 5]

    def test_a_gap_without_a_detected_box_is_refused(self):
        mask, reason = balloon_select(bubble_page(gap=True), 120, 60)
        assert (mask, reason) == (None, "leak")

    def test_a_detected_box_holds_the_flood_inside_the_bubble(self):
        page = bubble_page(gap=True)
        bound = (30, 30, 210, 150)
        mask, reason = balloon_select(page, 120, 60, bound=bound)
        assert reason == "ok"
        ys, xs = np.nonzero(mask)
        assert xs.max() < 210 + 5 and ys.max() < 150 + 5
        assert mask[90, 120] > 0

    def test_a_large_light_panel_is_not_a_bubble(self):
        """Seen in a screenshot: a light screentone panel covering 39% of the
        page was accepted as a bubble when the limit was 40%."""
        page = np.full((300, 300, 3), 245, np.uint8)
        page[::6, ::6] = 170          # dots
        page[:, :8] = 20              # gutter lines around a panel...
        page[:, -8:] = 20
        page[:8, :] = 20
        page[190:198, :] = 20         # ...that covers ~60% of the page
        assert balloon_select(page, 150, 100) == (None, "leak")

    def test_a_dark_click_is_refused(self):
        assert balloon_select(bubble_page(), 5, 5)[1] == "not-light"

    def test_a_click_outside_the_box_or_the_page(self):
        page = bubble_page()
        assert balloon_select(page, 120, 60, bound=(0, 0, 50, 50))[1] == "outside"
        assert balloon_select(page, 999, 60)[1] == "outside"


@pytest.fixture
def viewer(qapp):
    from app.ui.canvas.image_viewer import ImageViewer

    view = ImageViewer(None)
    view.resize(400, 300)
    view.display_image_array(bubble_page())
    stack = QUndoStack()
    view.command_emitted.connect(stack.push)
    view._test_stack = stack
    yield view
    view.close()


def selected(view):
    return view.selection.rasterise(0, 0, 520, 400) > 0


class TestOnTheCanvas:
    def test_a_click_selects_the_bubble(self, viewer):
        assert viewer.drawing_manager.balloon_at(QPointF(120, 60)) == "ok"
        sel = selected(viewer)
        assert sel[60, 120] and sel[90, 120]
        assert not (sel & outline(bubble_page())).any()

    def test_a_refused_click_leaves_the_selection_alone(self, viewer):
        viewer.drawing_manager.balloon_at(QPointF(120, 60))
        before = selected(viewer).copy()
        assert viewer.drawing_manager.balloon_at(QPointF(5, 5)) == "not-light"
        assert np.array_equal(selected(viewer), before)

    def test_alt_takes_a_bubble_away(self, viewer):
        viewer.drawing_manager.balloon_at(QPointF(120, 60))
        viewer.drawing_manager.balloon_at(QPointF(120, 60), Qt.KeyboardModifier.AltModifier)
        assert viewer.selection.is_empty()

    def test_mask_mode_paints_a_stroke(self, viewer):
        viewer.drawing_manager.region_output = "mask"
        viewer.drawing_manager.balloon_at(QPointF(120, 60))
        assert len(viewer.drawing_manager.save_brush_strokes()) == 1
        assert viewer.selection.is_empty()

    def test_the_webtoon_slice_offset_is_applied(self, viewer, monkeypatch):
        """In webtoon mode the image is the loaded slice, which starts lower
        down the scene; the seed goes in and the outline comes out shifted."""
        page = bubble_page()
        monkeypatch.setattr(viewer, "get_image_array", lambda **k: page)
        monkeypatch.setattr(viewer.drawing_manager, "_visible_area_offset", lambda: (0, 500))
        monkeypatch.setattr(viewer.selection, "page_area", lambda: __import__(
            "PySide6.QtCore", fromlist=["QRectF"]).QRectF(0, 500, 520, 400))
        assert viewer.drawing_manager.balloon_at(QPointF(120, 560)) == "ok"
        rect = viewer.selection.path.boundingRect()
        assert 500 < rect.top() < 560 < rect.bottom() < 700

    def test_the_tool_sets_a_crosshair(self, viewer):
        viewer.set_tool("balloon")
        assert viewer.cursor().shape() == Qt.CursorShape.CrossCursor


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
    path = str(tmp_path / "page.png")
    Image.fromarray(bubble_page(gap=True)).save(path)
    win.image_ctrl.thread_load_images([path])
    assert _pump_until(qapp, lambda: win.image_viewer.hasPhoto() and win.undo_group.activeStack() is not None)
    yield win
    win._skip_close_prompt = True
    win.close()


class TestInTheWindow:
    def test_a_detected_bubble_box_bounds_the_click(self, window):
        from modules.utils.textblock import TextBlock

        dm = window.image_viewer.drawing_manager
        assert dm.balloon_at(QPointF(120, 60)) == "leak", "no detection: the gap leaks"
        blk = TextBlock(text_bbox=np.array([80, 85, 160, 95]), bubble_bbox=np.array([30, 30, 210, 150]))
        blk.text_class = "text_bubble"
        window.blk_list = [blk]
        assert dm.balloon_at(QPointF(120, 60)) == "ok"
        rect = window.image_viewer.selection.path.boundingRect()
        assert rect.right() <= 215 and rect.bottom() <= 155

    def test_a_refusal_says_why(self, window, monkeypatch):
        from app.controllers import selection as selection_mod

        shown = []
        monkeypatch.setattr(selection_mod.MMessage, "info", lambda text, parent=None, **k: shown.append(text))
        window.image_viewer.balloon_refused.emit("leak")
        window.image_viewer.balloon_refused.emit("outside")   # nothing worth saying
        assert len(shown) == 1 and "Detect" in shown[0]

    def test_balloon_then_clean_leaves_a_white_bubble_and_its_outline(self, qapp, window):
        from modules.utils.textblock import TextBlock

        blk = TextBlock(text_bbox=np.array([80, 85, 160, 95]), bubble_bbox=np.array([30, 30, 210, 150]))
        blk.text_class = "text_bubble"
        window.blk_list = [blk]
        viewer = window.image_viewer
        before = viewer.get_image_array(include_patches=True).copy()
        viewer.drawing_manager.balloon_at(QPointF(120, 60))
        window.selection_ctrl.clean_selection()
        assert _pump_until(qapp, lambda: window.loading.isHidden())
        after = viewer.get_image_array(include_patches=True)
        assert (after[85:95, 80:160] == 255).all(), "the lettering is gone"
        ring = outline(before)
        assert np.array_equal(after[ring], before[ring]), "the outline is untouched"

    def test_modify_asks_for_pixels_and_is_undoable(self, window, monkeypatch):
        from PySide6.QtWidgets import QInputDialog

        viewer = window.image_viewer
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainterPath

        path = QPainterPath()
        path.addRect(QRectF(60, 50, 60, 40))
        viewer.selection.combine(path)
        area = viewer.selection.rasterise(0, 0, 520, 400).sum()
        assert all(b.isEnabled() for b in window.selection_action_buttons)

        monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: (5, True))
        window.grow_selection_action.trigger()
        assert viewer.selection.rasterise(0, 0, 520, 400).sum() > area

        monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: (9, False))
        stack = window.undo_group.activeStack()
        count = stack.count()
        window.shrink_selection_action.trigger()
        assert stack.count() == count, "cancelled: no step"

        monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: (4, True))
        window.feather_selection_action.trigger()
        assert viewer.selection.feather == 4
        stack.undo()
        assert viewer.selection.feather == 0
        stack.undo()
        assert viewer.selection.rasterise(0, 0, 520, 400).sum() == area

    def test_the_balloon_tool_shows_the_selection_options(self, window):
        window.set_tool("balloon")
        assert not window.selection_options.isHidden()


def test_a_failed_webtoon_conversion_leaves_the_blocks_as_they_were(window, monkeypatch):
    """filter_and_convert_visible_blocks edits blocks in place; if it raises
    halfway, the blocks it already converted must still be restored."""
    import pipeline.webtoon_utils as wu
    from modules.utils.textblock import TextBlock

    blk = TextBlock(text_bbox=np.array([80, 85, 160, 95]), bubble_bbox=np.array([30, 30, 210, 150]))
    window.blk_list = [blk]
    viewer = window.image_viewer

    def half_convert(main, pipeline, mappings, single_block=False):
        blk._original_xyxy = blk.xyxy.copy()
        blk._original_bubble_xyxy = blk.bubble_xyxy.copy()
        blk._mapping, blk._page_index = {}, 0
        blk.xyxy[:] = blk.xyxy + 999
        raise RuntimeError("boom")

    monkeypatch.setattr(wu, "filter_and_convert_visible_blocks", half_convert)
    monkeypatch.setattr(type(viewer), "webtoon_mode", property(lambda self: True))
    monkeypatch.setattr(viewer, "get_visible_area_image", lambda *a, **k: (None, [{"page_index": 0}]))
    assert viewer.drawing_manager._bubble_bound_at(120, 60) is None
    assert list(blk.xyxy) == [80, 85, 160, 95]
    assert not hasattr(blk, "_original_xyxy")
