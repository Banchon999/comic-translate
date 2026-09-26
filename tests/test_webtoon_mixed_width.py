"""Webtoon strips whose pages are not all the same width.

The layout centres each page horizontally on the strip, so a page narrower
(or wider) than the strip sits at x = (strip_width - page_width) / 2 in the
scene. Two things used to ignore that:

* the layout manager's page<->scene conversion, which detection uses to place
  its boxes, left the x offset out ("simplified for now"), so boxes on a narrow
  page landed shifted towards the left edge of the strip;
* the combined visible-area image stacked page crops into an array sized to the
  first page's width, so two differently sized pages on screen together raised
  a numpy broadcast error instead of producing an image.

A strip made entirely of equal-width pages has offset 0 everywhere, which is
why neither showed up on ordinary webtoons.
"""

from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QPointF, QRectF


@pytest.fixture
def layout(qapp):
    from PySide6.QtWidgets import QGraphicsScene
    from app.ui.canvas.webtoons.webtoon_layout_manager import WebtoonLayoutManager
    from app.ui.canvas.webtoons.coordinate_converter import CoordinateConverter

    viewer = SimpleNamespace(_scene=QGraphicsScene())
    lm = WebtoonLayoutManager(viewer)
    # Strip 1000 wide; page 0 is 1000 wide, page 1 only 600 (offset 200).
    lm.webtoon_width = 1000
    lm.image_positions = [0.0, 2000.0]
    lm.image_heights = [2000.0, 1500.0]
    lm.page_bottoms = [2000.0, 3500.0]
    loader = SimpleNamespace(image_data={
        0: np.zeros((2000, 1000, 3), np.uint8),
        1: np.zeros((1500, 600, 3), np.uint8),
    })
    lm.image_loader = loader
    lm.coordinate_converter = CoordinateConverter(lm, loader)
    return lm


def test_page_to_scene_applies_the_centring_offset(layout):
    scene = layout.page_to_scene_coordinates(1, QPointF(10, 20))
    assert (scene.x(), scene.y()) == (210, 2020)


def test_scene_to_page_removes_the_centring_offset(layout):
    idx, local = layout.scene_to_page_coordinates(QPointF(210, 2020))
    assert idx == 1
    assert (local.x(), local.y()) == (10, 20)


def test_full_width_page_is_unchanged(layout):
    scene = layout.page_to_scene_coordinates(0, QPointF(10, 20))
    assert (scene.x(), scene.y()) == (10, 20)


def test_combined_visible_image_tolerates_mixed_widths(qapp):
    """Both pages visible at once: the stack must not raise, and every page's
    pixels must start at combined x = 0 (page-local), since detection reads the
    combined x straight back as a page-local x."""
    from app.ui.canvas.webtoons.image_loader import LazyImageLoader

    wide = np.full((100, 1000, 3), 10, np.uint8)
    narrow = np.full((100, 600, 3), 200, np.uint8)
    lm = SimpleNamespace(
        image_positions=[0.0, 100.0],
        image_heights=[100.0, 100.0],
        get_pages_for_scene_bounds=lambda rect: {0, 1},
    )
    viewport_rect = QRectF(0, 0, 1000, 200)
    viewer = SimpleNamespace(
        mapToScene=lambda r: SimpleNamespace(boundingRect=lambda: viewport_rect),
        viewport=lambda: SimpleNamespace(rect=lambda: None),
    )
    loader = LazyImageLoader.__new__(LazyImageLoader)
    loader.layout_manager = lm
    loader.viewer = viewer
    loader.get_image_data = {0: wide, 1: narrow}.get

    combined, mappings = loader.get_visible_area_image(include_patches=False)

    assert combined.shape[:2] == (200, 1000)
    assert (combined[100:, :600] == 200).all()      # narrow page, left-aligned
    assert (combined[100:, 600:] == 0).all()        # padding, not stale data
    assert [m["page_index"] for m in mappings] == [0, 1]
