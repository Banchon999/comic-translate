"""Gradient fill and curved text on the canvas item.

The geometry has its own tests; these are about what the item does with it —
that a curved item still reports the box the user sized, that turning an
effect off puts things back, and that neither survives into a state it should
not.
"""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QTextCursor
from PySide6.QtWidgets import QStyleOptionGraphicsItem


@pytest.fixture
def item(qapp):
    from app.ui.canvas.text_item import TextBlockItem

    block = TextBlockItem(
        text="KABOOM",
        font_family="",
        font_size=30,
        render_color=QColor(255, 0, 0),
        outline_color=None,
    )
    block.setTextWidth(300)
    yield block


def rendered(block, width=400, height=300):
    """Paint the item onto a white page.

    The style option is a real one, not None — Qt dereferences it in
    QGraphicsTextItem's own paint and segfaults on a null.
    """
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    block.paint(painter, QStyleOptionGraphicsItem(), None)
    painter.end()
    return image


def ink(image):
    """Every non-white pixel, as (x, y, (r, g, b))."""
    found = []
    for y in range(image.height()):
        for x in range(image.width()):
            colour = QColor(image.pixel(x, y))
            rgb = (colour.red(), colour.green(), colour.blue())
            if rgb != (255, 255, 255):
                found.append((x, y, rgb))
    return found


class TestGradient:
    def test_it_is_off_to_begin_with(self, item):
        assert item.gradient_enabled is False
        assert item.fill_brush().color() == QColor(255, 0, 0)

    def test_enabling_it_gives_the_document_a_gradient_brush(self, item):
        item.set_gradient(True, QColor(0, 0, 255), 0)
        cursor = QTextCursor(item.document())
        cursor.setPosition(1)
        assert cursor.charFormat().foreground().gradient() is not None

    def test_it_runs_from_the_text_colour_to_the_second_one(self, item):
        item.set_gradient(True, QColor(0, 0, 255), 0)
        stops = item.fill_brush().gradient().stops()
        assert stops[0][1] == QColor(255, 0, 0)
        assert stops[-1][1] == QColor(0, 0, 255)

    def test_the_axis_follows_the_angle(self, item):
        def axis(angle):
            item.set_gradient(True, QColor(0, 0, 255), angle)
            # QBrush.gradient() borrows from the brush, so read it out while
            # the brush is still referenced rather than keeping the pointer.
            brush = item.fill_brush()
            gradient = brush.gradient()
            return gradient.start().toTuple(), gradient.finalStop().toTuple()

        (_, y1), (_, y2) = axis(0)
        assert y1 == y2, "a 0-degree gradient should not run downhill"
        (x1, _), (x2, _) = axis(90)
        assert x1 == x2, "a 90-degree gradient should not run sideways"

    def test_turning_it_off_restores_the_plain_colour(self, item):
        item.set_gradient(True, QColor(0, 0, 255), 0)
        item.set_gradient(False)
        cursor = QTextCursor(item.document())
        cursor.setPosition(1)
        assert cursor.charFormat().foreground().gradient() is None
        assert item.fill_brush().color() == QColor(255, 0, 0)

    def test_editing_the_text_does_not_recurse(self, item):
        """Re-applying the gradient is itself a document edit."""
        item.set_gradient(True, QColor(0, 0, 255), 90)
        item.setPlainText("A MUCH LONGER SOUND EFFECT INDEED")
        assert item.gradient_enabled

    def test_it_is_rebuilt_when_the_text_grows(self, item):
        item.set_gradient(True, QColor(0, 0, 255), 90)
        short = item.fill_brush().gradient().finalStop().y()
        item.setPlainText("one\ntwo\nthree\nfour\nfive")
        assert item.fill_brush().gradient().finalStop().y() > short

    def test_two_lines_are_not_the_same_colour_twice(self, item):
        """Qt's ObjectBoundingMode restarts the sweep on every glyph run."""
        item.setPlainText("AAA\nBBB")
        item.set_gradient(True, QColor(0, 0, 255), 90)
        pixels = ink(rendered(item))
        assert pixels, "nothing was drawn"
        top = min(y for _, y, _ in pixels)
        bottom = max(y for _, y, _ in pixels)
        first = [rgb for _, y, rgb in pixels if y < (top + bottom) / 2]
        second = [rgb for _, y, rgb in pixels if y > (top + bottom) / 2]
        # The first line should be redder than the second.
        assert sum(r - b for r, _, b in first) / len(first) > \
               sum(r - b for r, _, b in second) / len(second)


class TestCurvature:
    def test_it_is_flat_to_begin_with(self, item):
        assert item.curvature == 0.0
        assert item.boundingRect() == item.text_rect()

    def test_bending_grows_only_the_painted_rectangle(self, item):
        before = item.text_rect()
        item.set_curvature(0.6)
        assert item.text_rect() == before, "the box the user sized must not move"
        assert item.boundingRect().height() > before.height()

    def test_the_painted_rectangle_grows_symmetrically(self, item):
        """The transform origin and rotation pivot are its centre."""
        before = item.boundingRect().center()
        item.set_curvature(0.6)
        assert item.boundingRect().center() == before

    def test_a_sag_reaches_as_far_as_an_arch(self, item):
        item.set_curvature(0.6)
        arch = item.boundingRect()
        item.set_curvature(-0.6)
        assert item.boundingRect() == arch

    def test_going_back_to_flat_restores_the_rectangle(self, item):
        before = item.boundingRect()
        item.set_curvature(0.8)
        item.set_curvature(0.0)
        assert item.boundingRect() == before

    def test_curvature_is_clamped(self, item):
        item.set_curvature(4.0)
        assert item.curvature == 1.0
        item.set_curvature(-4.0)
        assert item.curvature == -1.0

    def test_the_glyphs_actually_move(self, item):
        flat = ink(rendered(item))
        item.set_curvature(0.8)
        curved = ink(rendered(item))
        assert flat and curved
        flat_rows = {y for _, y, _ in flat}
        curved_rows = {y for _, y, _ in curved}
        assert max(curved_rows) - min(curved_rows) > max(flat_rows) - min(flat_rows)

    def test_the_ends_fall_below_the_middle_on_an_arch(self, item):
        item.set_curvature(0.9)
        pixels = ink(rendered(item))
        assert pixels
        left, right = min(x for x, _, _ in pixels), max(x for x, _, _ in pixels)
        middle = (left + right) / 2

        def lowest(predicate):
            return max(y for x, y, _ in pixels if predicate(x))

        assert lowest(lambda x: x < left + 15) > lowest(lambda x: abs(x - middle) < 15)

    def test_a_curved_item_still_takes_a_gradient(self, item):
        item.set_curvature(0.5)
        item.set_gradient(True, QColor(0, 0, 255), 0)
        brush = item.fill_brush()
        assert brush.gradient() is not None
        assert ink(rendered(item)), "nothing was drawn"

    def test_the_gradient_sweeps_across_the_curved_text(self, item):
        """Each glyph is rotated, and a gradient brush resolves under whatever
        transform the painter is in. Rotating the painter per glyph would make
        every letter sample the gradient at its own origin and come out one
        flat colour."""
        item.set_curvature(0.5)
        item.set_gradient(True, QColor(0, 0, 255), 0)
        pixels = ink(rendered(item))
        assert pixels
        left = min(x for x, _, _ in pixels)
        right = max(x for x, _, _ in pixels)
        near_left = [rgb for x, _, rgb in pixels if x < left + 20]
        near_right = [rgb for x, _, rgb in pixels if x > right - 20]

        def redness(sample):
            return sum(r - b for r, _, b in sample) / len(sample)

        assert redness(near_left) > redness(near_right)

    def test_bending_does_not_change_the_text_size(self, item):
        """`font_size` is a copy of the document's default and the two can be a
        step apart, so the curved path must read the document."""
        flat = len(ink(rendered(item)))
        item.set_curvature(0.05)
        assert len(ink(rendered(item))) == pytest.approx(flat, rel=0.25)

    def test_an_outline_is_drawn_around_the_curve(self, item):
        without = len(ink(rendered(item)))
        item.set_outline(QColor(0, 0, 0), 3)
        item.set_curvature(0.5)
        assert len(ink(rendered(item))) > without

    def test_uncurving_puts_the_gradient_back_on_the_document(self, item):
        """While curved the glyphs are painted by hand and the document is not told."""
        item.set_curvature(0.5)
        item.set_gradient(True, QColor(0, 0, 255), 0)
        item.set_curvature(0.0)
        cursor = QTextCursor(item.document())
        cursor.setPosition(1)
        assert cursor.charFormat().foreground().gradient() is not None

    def test_the_bulge_is_recomputed_when_the_text_changes(self, item):
        item.set_curvature(0.8)
        before = item.boundingRect().height()
        item.setPlainText("A VERY MUCH LONGER SOUND EFFECT")
        assert item.boundingRect().height() != before

    def test_empty_text_does_not_blow_up(self, item):
        item.setPlainText("")
        item.set_curvature(0.8)
        assert item.boundingRect().isValid()
        rendered(item)


class TestSavedState:
    def test_the_effects_round_trip_through_properties(self, item):
        from app.ui.canvas.text.text_item_properties import TextItemProperties

        item.set_gradient(True, QColor(0, 0, 255), 135)
        item.set_curvature(0.4)
        restored = TextItemProperties.from_dict(TextItemProperties.from_text_item(item).to_dict())
        assert restored.gradient_enabled is True
        assert restored.gradient_color == QColor(0, 0, 255)
        assert restored.gradient_angle == 135
        assert restored.curvature == pytest.approx(0.4)

    def test_an_older_project_without_them_still_loads(self):
        from app.ui.canvas.text.text_item_properties import TextItemProperties

        props = TextItemProperties.from_dict({'text': 'hi'})
        assert props.gradient_enabled is False
        assert props.curvature == 0.0

    def test_the_saved_size_is_the_box_not_the_arc(self, item):
        from app.ui.canvas.text.text_item_properties import TextItemProperties

        flat = TextItemProperties.from_text_item(item).height
        item.set_curvature(0.9)
        assert TextItemProperties.from_text_item(item).height == flat
