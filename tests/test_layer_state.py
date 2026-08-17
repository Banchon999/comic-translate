"""A layer's state and an item's own state combine; neither overwrites the other.

The Layers popup and the per-item panel both end up calling setVisible and
setOpacity on the same QGraphicsItem. Whichever ran last used to win, so hiding
a layer and showing it again silently un-hid every item the side panel had
hidden.
"""

import pytest
from PySide6 import QtWidgets

from app.ui.canvas import layer_state


@pytest.fixture
def item(qapp):
    scene = QtWidgets.QGraphicsScene()
    rect = QtWidgets.QGraphicsRectItem(0, 0, 10, 10)
    scene.addItem(rect)
    # Keep the scene alive for the life of the item.
    rect._scene_ref = scene
    return rect


def test_an_untouched_item_is_shown_and_opaque(item):
    layer_state.apply_to(item, True, 1.0)
    assert item.isVisible() and item.opacity() == 1.0


@pytest.mark.parametrize(
    "layer_visible, layer_opacity, item_shown, item_alpha, expect_visible, expect_opacity",
    [
        (True, 1.0, True, 1.0, True, 1.0),
        (True, 0.5, True, 1.0, True, 0.5),
        (True, 1.0, True, 0.4, True, 0.4),
        (True, 0.5, True, 0.4, True, 0.2),
        (False, 1.0, True, 1.0, False, 1.0),
        (True, 1.0, False, 1.0, False, 1.0),
        (False, 1.0, False, 1.0, False, 1.0),
    ],
)
def test_visibility_and_opacity_combine(
    item, layer_visible, layer_opacity, item_shown, item_alpha, expect_visible, expect_opacity
):
    layer_state.set_item_visible(item, item_shown)
    layer_state.set_item_opacity(item, item_alpha)
    layer_state.apply_to(item, layer_visible, layer_opacity)
    assert item.isVisible() is expect_visible
    assert item.opacity() == pytest.approx(expect_opacity)


def test_an_item_hidden_on_its_own_survives_its_layer_being_toggled(item):
    layer_state.set_item_visible(item, False)
    layer_state.apply_to(item, False, 1.0)
    layer_state.apply_to(item, True, 1.0)
    assert not item.isVisible()


def test_a_per_item_fade_survives_its_layer_being_toggled(item):
    layer_state.set_item_opacity(item, 0.3)
    layer_state.apply_to(item, False, 1.0)
    layer_state.apply_to(item, True, 1.0)
    assert item.opacity() == pytest.approx(0.3)


@pytest.mark.parametrize("value, expected", [(-1.0, 0.0), (0.0, 0.0), (0.5, 0.5), (1.0, 1.0), (5.0, 1.0)])
def test_item_opacity_is_clamped(item, value, expected):
    layer_state.set_item_opacity(item, value)
    assert layer_state.item_opacity(item) == pytest.approx(expected)


def test_the_data_keys_do_not_collide_with_the_patch_hash(item):
    """Inpaint patches store their hash under data role 0."""
    item.setData(0, "patch-hash")
    layer_state.set_item_visible(item, False)
    layer_state.set_item_opacity(item, 0.25)
    assert item.data(0) == "patch-hash"
    assert layer_state.item_visible(item) is False
    assert layer_state.item_opacity(item) == pytest.approx(0.25)
