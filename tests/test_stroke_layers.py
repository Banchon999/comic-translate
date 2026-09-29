"""Stacked strokes on a text item, shadow opacity, and rich text on a curve.

A text item's outline is one stroke; lettering often stacks more (a white ring
inside a black one). Each layer sits outside everything before it, so its
reach is the outline width plus every earlier layer plus its own.
"""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QStyleOptionGraphicsItem

from core.text_style import StrokeLayer, stroke_layers_from, stroke_layers_payload, stroke_reaches

FONT = "DejaVu Sans"  # explicit, so the size under test is the size drawn


class TestHelpers:
    def test_layers_read_from_dicts_pairs_and_layers(self):
        layers = stroke_layers_from([
            {"color": "#ff000000", "width": 3},
            ("#ffffffff", 2.5),
            StrokeLayer("#ff00ff00", 1.0),
        ])
        assert layers == [
            StrokeLayer("#ff000000", 3.0),
            StrokeLayer("#ffffffff", 2.5),
            StrokeLayer("#ff00ff00", 1.0),
        ]

    def test_unreadable_or_empty_layers_are_dropped(self):
        assert stroke_layers_from([{"color": "#000", "width": 0}, {"width": 2}, ("x",), None]) == []
        assert stroke_layers_from(None) == []

    def test_a_qcolor_becomes_hex(self, qapp):
        (layer,) = stroke_layers_from([(QColor(10, 20, 30, 128), 2)])
        assert layer.color.lower() == "#800a141e"

    def test_reaches_accumulate_from_the_outline(self):
        assert stroke_reaches(2.0, [("#000", 3), ("#fff", 4)]) == [5.0, 9.0]
        assert stroke_reaches(0.0, []) == []

    def test_payload_is_plain_data(self):
        assert stroke_layers_payload([("#ff000000", 3)]) == [{"color": "#ff000000", "width": 3.0}]


@pytest.fixture
def item(qapp):
    from app.ui.canvas.text_item import TextBlockItem

    block = TextBlockItem(text="KABOOM", font_size=30, render_color=QColor(255, 0, 0),
                          outline_color=QColor(255, 255, 255), outline_width=2)
    block.set_font(FONT, 30)
    block.set_outline(QColor(255, 255, 255), 2)
    block.setTextWidth(320)
    yield block


def render(block, width=460, height=240, background=Qt.GlobalColor.gray):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(background)
    painter = QPainter(image)
    painter.translate(60, 60)
    block.paint(painter, QStyleOptionGraphicsItem(), None)
    painter.end()
    return image


def count(image, rgb, tolerance=40):
    found = 0
    for y in range(image.height()):
        for x in range(image.width()):
            c = QColor(image.pixel(x, y))
            if abs(c.red() - rgb[0]) + abs(c.green() - rgb[1]) + abs(c.blue() - rgb[2]) <= tolerance:
                found += 1
    return found


class TestItem:
    def test_layers_become_wider_whole_document_outlines(self, item):
        item.set_stroke_layers([("#ff000000", 3), ("#ff00ff00", 4)])
        widths = sorted(float(o.width) for o in item.effective_outlines())
        assert widths == [2.0, 5.0, 9.0]
        assert item.stroke_reach() == 9.0

    def test_without_layers_nothing_changes(self, item):
        assert item.effective_outlines() == item.selection_outlines
        assert item.boundingRect() == item.text_rect()

    def test_the_repaint_rect_grows_by_the_reach_and_the_box_does_not(self, item):
        box = item.text_rect()
        item.set_stroke_layers([("#ff000000", 6)])
        assert item.text_rect() == box
        assert item.boundingRect() == box.adjusted(-8, -8, 8, 8)
        assert item.boundingRect().center() == box.center()

    def test_the_layer_is_drawn_outside_the_outline(self, item):
        without = count(render(item), (0, 0, 0))
        item.set_stroke_layers([("#ff000000", 5)])
        drawn = render(item)
        assert count(drawn, (0, 0, 0)) > without + 500
        # The white outline is still there, between the fill and the black ring.
        assert count(drawn, (255, 255, 255)) > 300

    def test_a_thick_stroke_has_no_gaps(self, item):
        """Stroked from the glyph outlines, not faked with 16 offset copies:
        the ring must be solid, so the letters' counters fill in rather than
        showing grey specks between copies."""
        item.set_outline(None, 0)
        item.set_stroke_layers([("#ff000000", 14)])
        image = render(item)
        grey = QColor(Qt.GlobalColor.gray)
        box = item.document_glyph_path().boundingRect().translated(60, 60)
        inner = box.adjusted(4, 4, -4, -4)
        speckles = 0
        for y in range(int(inner.top()), int(inner.bottom())):
            for x in range(int(inner.left()), int(inner.right())):
                c = QColor(image.pixel(x, y))
                if abs(c.red() - grey.red()) < 8 and abs(c.green() - grey.green()) < 8:
                    speckles += 1
        assert speckles == 0

    def test_curved_text_draws_its_layers(self, item):
        item.set_curvature(0.5)
        without = count(render(item), (0, 0, 0))
        item.set_stroke_layers([("#ff000000", 5)])
        assert count(render(item), (0, 0, 0)) > without + 500


class TestRichTextOnACurve:
    def test_a_coloured_range_keeps_its_colour_when_bent(self, item):
        cursor = QTextCursor(item.document())
        cursor.setPosition(3)
        cursor.setPosition(6, QTextCursor.MoveMode.KeepAnchor)
        blue = QTextCharFormat()
        blue.setForeground(QColor(0, 0, 255))
        cursor.mergeCharFormat(blue)
        item.set_curvature(0.5)
        image = render(item)
        assert count(image, (255, 0, 0)) > 100
        assert count(image, (0, 0, 255)) > 100

    def test_a_bigger_range_stays_bigger_when_bent(self, item):
        def ink_width(block):
            image = render(block)
            xs = [x for y in range(image.height()) for x in range(image.width())
                  if QColor(image.pixel(x, y)).red() > 200 and QColor(image.pixel(x, y)).green() < 60]
            return max(xs) - min(xs)

        item.set_outline(None, 0)
        item.set_curvature(0.3)
        before = ink_width(item)
        cursor = QTextCursor(item.document())
        cursor.setPosition(0)
        cursor.setPosition(6, QTextCursor.MoveMode.KeepAnchor)
        big = QTextCharFormat()
        big.setFontPointSize(44)
        cursor.mergeCharFormat(big)
        item.update()
        assert ink_width(item) > before * 1.2


class TestSaveLoadUndo:
    def test_round_trip_through_the_saved_state(self, item):
        from app.ui.canvas.save_renderer import build_text_item
        from app.ui.canvas.text.text_item_properties import TextItemProperties

        item.set_stroke_layers([("#ff000000", 3), ("#ffffffff", 2)])
        state = TextItemProperties.from_text_item(item).to_dict()
        assert state["stroke_layers"] == [
            {"color": "#ff000000", "width": 3.0},
            {"color": "#ffffffff", "width": 2.0},
        ]
        rebuilt = build_text_item(state)
        assert rebuilt.stroke_layers == item.stroke_layers

    def test_an_item_without_layers_saves_as_before(self, item):
        from app.ui.canvas.text.text_item_properties import TextItemProperties

        assert "stroke_layers" not in TextItemProperties.from_text_item(item).to_dict()

    def test_undo_takes_the_layers_and_the_repaint_rect_back(self, qapp, item):
        from app.ui.canvas.image_viewer import ImageViewer
        from app.ui.commands.textformat import TextFormatCommand

        viewer = ImageViewer(None)
        viewer._scene.addItem(item)
        before = item.boundingRect()
        command = TextFormatCommand(viewer, item)
        item.set_stroke_layers([("#ff000000", 6)])
        command.finalize_new_state()
        command.redo()
        assert item.stroke_layers
        command.undo()
        assert item.stroke_layers == []
        assert item.boundingRect() == before
        command.redo()
        assert item.stroke_reach() == 8.0

    def test_skia_gets_the_layers_as_outlines(self, item):
        from app.ui.canvas.skia_paint import spec_for_item

        item.set_stroke_layers([("#ff000000", 3)])
        widths = sorted(layer.width for layer in spec_for_item(item).outlines)
        assert widths == [2.0, 5.0]


def test_the_shadow_opacity_control_sets_the_alpha(qapp):
    import controller as controller_mod
    from app.ui.canvas.text_item import TextBlockItem

    win = controller_mod.ComicTranslate()
    try:
        block = TextBlockItem(text="Hi", font_size=20, render_color=QColor(0, 0, 0))
        win.image_viewer._scene.addItem(block)
        win.curr_tblock_item = block
        win.shadow_checkbox.setChecked(True)
        win.shadow_opacity_dropdown.setCurrentText("50")
        win.text_ctrl.apply_shadow_settings()
        assert block.shadow_enabled
        assert block.shadow_color.alphaF() == pytest.approx(0.5, abs=0.01)
        win.text_ctrl.set_values_for_blk_item(block)
        assert win.shadow_opacity_dropdown.currentText() == "50"
        assert win.stroke_layers_button.text() == win.tr("+ Strokes")
        block.set_stroke_layers([("#ff000000", 2), ("#ffffffff", 2)])
        win.text_ctrl.set_values_for_blk_item(block)
        assert "2" in win.stroke_layers_button.text()
    finally:
        win._skip_close_prompt = True
        win.close()
