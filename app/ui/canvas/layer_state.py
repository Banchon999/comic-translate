"""Where a scene item's own show/opacity lives, so two panels can both drive it.

The Layers popup in the header controls whole layers; the side panel controls
single items. Both end up calling `setVisible`/`setOpacity` on the same
`QGraphicsItem`, and whichever ran last used to win — hiding a layer and showing
it again silently un-hid every item the side panel had hidden, and any per-item
fade was reset to full the next time a layer toggle moved.

So an item's *own* state is kept here, on the item, and what Qt is told is the
combination of the two: visible only if both say so, opacity multiplied.
"""

from __future__ import annotations

#: Data roles on the scene item. 0 is taken — inpaint patches store their hash
#: there — so these start above it.
ITEM_VISIBLE_KEY = 10
ITEM_OPACITY_KEY = 11


def item_visible(item) -> bool:
    value = item.data(ITEM_VISIBLE_KEY)
    return True if value is None else bool(value)


def item_opacity(item) -> float:
    value = item.data(ITEM_OPACITY_KEY)
    return 1.0 if value is None else float(value)


def set_item_visible(item, visible: bool) -> None:
    item.setData(ITEM_VISIBLE_KEY, bool(visible))


def set_item_opacity(item, opacity: float) -> None:
    item.setData(ITEM_OPACITY_KEY, max(0.0, min(1.0, float(opacity))))


def apply_to(item, layer_visible: bool, layer_opacity: float) -> None:
    """Push the combined layer state and item state onto the item."""
    item.setVisible(bool(layer_visible) and item_visible(item))
    item.setOpacity(layer_opacity * item_opacity(item))
