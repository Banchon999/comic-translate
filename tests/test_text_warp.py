"""Point warps on text: the offset maths, the item, save/load, the toolbar.

Arc stays the glyph-placing curve (`curvature`). The other styles move every
point of every glyph outline vertically by `warp_offset`, so per-range fonts
and colours survive and strokes are drawn from the warped union.
"""

import math

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QStyleOptionGraphicsItem

from modules.rendering.text_effects import WARP_STYLES, warp_extent, warp_offset


class TestMaths:
    @pytest.mark.parametrize("style", WARP_STYLES)
    def test_zero_bend_is_the_identity(self, style):
        assert all(warp_offset(style, 0.0, u / 10, v / 2) == 0.0 for u in range(11) for v in range(3))

    @pytest.mark.parametrize("style", WARP_STYLES)
    def test_bend_flips_the_direction(self, style):
        for u in (0.2, 0.37, 0.8):
            assert warp_offset(style, 0.7, u, 0.0) == pytest.approx(-warp_offset(style, -0.7, u, 0.0))

    def test_arch_lifts_the_middle_over_the_ends_and_stays_centred(self):
        middle, end = warp_offset("arch", 1.0, 0.5, 0.5), warp_offset("arch", 1.0, 0.0, 0.5)
        assert middle < 0 < end
        assert warp_offset("arch", 1.0, 0.5, 0.0) == warp_offset("arch", 1.0, 0.5, 1.0)
        mean = sum(warp_offset("arch", 1.0, u / 1000, 0.5) for u in range(1001)) / 1001
        assert mean == pytest.approx(0.0, abs=1e-3)

    def test_bulge_moves_top_and_bottom_apart(self):
        assert warp_offset("bulge", 1.0, 0.5, 0.0) < 0 < warp_offset("bulge", 1.0, 0.5, 1.0)
        assert warp_offset("bulge", 1.0, 0.5, 0.5) == pytest.approx(0.0)

    def test_flag_is_one_wave_and_wave_is_two(self):
        def crossings(style):
            values = [warp_offset(style, 1.0, u / 400, 0.0) for u in range(401)]
            return sum(1 for a, b in zip(values, values[1:]) if a * b < 0)
        assert crossings("flag") == 1
        assert crossings("wave") >= 3

    def test_rise_goes_from_low_to_high(self):
        assert warp_offset("rise", 1.0, 0.0, 0.5) > 0 > warp_offset("rise", 1.0, 1.0, 0.5)

    def test_extent_is_the_largest_shift(self):
        assert warp_extent("arch", 0.8) == pytest.approx(0.4 * (2 / math.pi), abs=1e-3)
        assert warp_extent("arch", 0.0) == 0.0
        assert warp_extent("nope", 1.0) == 0.0
        assert math.isclose(warp_extent("flag", -1.0), 0.3, abs_tol=1e-3)


@pytest.fixture
def item(qapp):
    from app.ui.canvas.text_item import TextBlockItem

    block = TextBlockItem(text="WARPED WORD", font_size=30, render_color=QColor(255, 0, 0))
    block.set_font("DejaVu Sans", 30)
    block.setTextWidth(320)
    yield block


def render(block):
    image = QImage(520, 320, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.translate(80, 110)
    block.paint(painter, QStyleOptionGraphicsItem(), None)
    painter.end()
    return image


def ink_rows(image):
    rows = []
    for y in range(image.height()):
        if any(QColor(image.pixel(x, y)).red() > 200 and QColor(image.pixel(x, y)).green() < 80
               for x in range(0, image.width(), 2)):
            rows.append(y)
    return rows


def count(image, rgb, tolerance=60):
    return sum(
        1 for y in range(image.height()) for x in range(image.width())
        if sum(abs(a - b) for a, b in zip(QColor(image.pixel(x, y)).getRgb()[:3], rgb)) <= tolerance
    )


class TestItem:
    def test_unknown_styles_and_zero_bend_mean_no_warp(self, item):
        item.set_warp("spiral", 0.5)
        assert item.warp_style == "" and not item.is_warped()
        item.set_warp("arch", 0.0)
        assert not item.is_warped()

    def test_the_repaint_rect_grows_and_the_box_does_not(self, item):
        box = item.text_rect()
        item.set_warp("arch", 0.8)
        assert item.text_rect() == box
        grown = item.boundingRect()
        assert grown.height() > box.height()
        assert grown.center().y() == pytest.approx(box.center().y())

    def test_an_arch_raises_the_middle_of_the_word(self, item):
        flat = ink_rows(render(item))
        item.set_warp("arch", 0.8)
        arched = ink_rows(render(item))
        assert min(arched) < min(flat) - 10
        assert max(arched) > max(flat) + 10  # the ends dip: it stays centred

    def test_a_coloured_range_keeps_its_colour(self, item):
        cursor = QTextCursor(item.document())
        cursor.setPosition(7)
        cursor.setPosition(11, QTextCursor.MoveMode.KeepAnchor)
        blue = QTextCharFormat()
        blue.setForeground(QColor(0, 0, 255))
        cursor.mergeCharFormat(blue)
        item.set_warp("wave", 0.6)
        image = render(item)
        assert count(image, (255, 0, 0)) > 150
        assert count(image, (0, 0, 255)) > 150

    def test_strokes_follow_the_warp(self, item):
        item.set_stroke_layers([("#ff000000", 4)])
        item.set_warp("flag", 0.6)
        assert count(render(item), (0, 0, 0)) > 600

    def test_editing_bypasses_the_warp_so_the_caret_shows(self, item, monkeypatch):
        """The caret and selection band are drawn by Qt's own flat paint."""
        calls = []
        monkeypatch.setattr(item, "paint_warped", lambda painter: calls.append(painter))
        item.set_warp("arch", 0.8)
        item.editing_mode = True
        render(item)
        assert calls == []
        item.editing_mode = False
        render(item)
        assert len(calls) == 1


class TestSaveLoad:
    def test_round_trip(self, item):
        from app.ui.canvas.save_renderer import build_text_item
        from app.ui.canvas.text.text_item_properties import TextItemProperties

        item.set_warp("rise", -0.4)
        state = TextItemProperties.from_text_item(item).to_dict()
        assert (state["warp_style"], state["warp_bend"]) == ("rise", -0.4)
        rebuilt = build_text_item(state)
        assert (rebuilt.warp_style, rebuilt.warp_bend) == ("rise", -0.4)

    def test_unwarped_text_saves_as_before(self, item):
        from app.ui.canvas.text.text_item_properties import TextItemProperties

        state = TextItemProperties.from_text_item(item).to_dict()
        assert "warp_style" not in state and "warp_bend" not in state


def test_the_toolbar_picks_a_style_and_arc_stays_the_curve(qapp):
    import controller as controller_mod
    from PySide6.QtGui import QUndoStack

    from app.ui.canvas.text_item import TextBlockItem

    win = controller_mod.ComicTranslate()
    try:
        win.undo_group.setActiveStack(QUndoStack(win.undo_group))
        block = TextBlockItem(text="Hi", font_size=20, render_color=QColor(0, 0, 0))
        win.image_viewer._scene.addItem(block)
        win.curr_tblock_item = block
        win.curvature_dropdown.setCurrentText("50")
        assert block.curvature == pytest.approx(0.5) and block.warp_style == ""
        win.warp_style_combo.setCurrentIndex(win.warp_style_combo.findData("bulge"))
        assert block.curvature == 0.0
        assert (block.warp_style, block.warp_bend) == ("bulge", pytest.approx(0.5))
        win.text_ctrl.set_values_for_blk_item(block)
        assert win.warp_style_combo.currentData() == "bulge"
        assert win.curvature_dropdown.currentText() == "50"
        win.undo_group.activeStack().undo()
        assert block.warp_style == "" and block.curvature == pytest.approx(0.5)
    finally:
        win._skip_close_prompt = True
        win.close()
