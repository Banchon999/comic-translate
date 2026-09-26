"""Make scene items look and behave the way their layer props say.

``apply_layer_state`` turns an item's own props plus its group's document-wide
props into what Qt understands: visibility, opacity, whether it takes mouse
input, and its z-order within its group. ``ImageViewer.refresh_layers`` runs it
over every page item. It is called after anything that can add, remove or
change items (every undo-stack change, page loads, webtoon page loads, layer
edits), so no item creation site has to remember to apply layers itself — a
single scan of a few hundred items is cheap next to any of those events.

Locking is enforced where Qt routes input: a locked item accepts no mouse
buttons, so a click goes to whatever is underneath, and it is neither movable
nor selectable. It can therefore not be dragged, resized, rotated, edited or
deleted from the canvas.

Only presentation changes here. Processing (OCR, translation, inpainting)
reads page data, not what is visible, so hiding a layer never changes a result.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGraphicsItem

from core.layers import DocumentLayers, LayerGroup
from app.ui.canvas.scene_registry import get_item_layer_props, kind_of

# The z each group's items are created at today; an object's own order offsets
# it inside a window small enough never to cross into the next group.
GROUP_Z: dict[LayerGroup, float] = {
    LayerGroup.RAW: 0.0,
    LayerGroup.PATCHES: 0.5,
    LayerGroup.STROKES: 0.8,
    LayerGroup.BOXES: 1.0,
    LayerGroup.TEXT: 1.0,
}
Z_STEP = 1e-4
MAX_ORDER = 999  # keeps base + order * step inside the group's window

# Data slot recording that this module moved the item's z, so clearing the
# order puts it back at its group's base instead of leaving it offset.
_Z_OVERRIDE_KEY = 3

_MOVE_FLAGS = (
    QGraphicsItem.GraphicsItemFlag.ItemIsMovable,
    QGraphicsItem.GraphicsItemFlag.ItemIsSelectable,
)


def is_locked(item: QGraphicsItem, doc: DocumentLayers) -> bool:
    kind = kind_of(item)
    if kind is None:
        return False
    return doc.effective(kind, get_item_layer_props(item)).locked


def is_hidden(item: QGraphicsItem, doc: DocumentLayers) -> bool:
    kind = kind_of(item)
    if kind is None:
        return False
    return not doc.effective(kind, get_item_layer_props(item)).visible


def _deselect(item, viewer) -> None:
    if not getattr(item, "selected", False):
        return
    if hasattr(item, "handleDeselection"):
        item.handleDeselection()
    elif viewer is not None and hasattr(viewer, "deselect_rect"):
        viewer.deselect_rect(item)


def _apply_lock(item, kind: LayerGroup, locked: bool, viewer) -> None:
    if kind is LayerGroup.RAW:
        return  # the artwork is never interactive
    if locked:
        _deselect(item, viewer)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        if kind in (LayerGroup.TEXT, LayerGroup.BOXES):
            for flag in _MOVE_FLAGS:
                item.setFlag(flag, False)
    else:
        item.setAcceptedMouseButtons(Qt.MouseButton.AllButtons)
        if kind is LayerGroup.TEXT:
            # Text is movable except while its text is being edited.
            item.setFlag(_MOVE_FLAGS[0], not getattr(item, "editing_mode", False))
            item.setFlag(_MOVE_FLAGS[1], True)
        elif kind is LayerGroup.BOXES:
            item.setFlag(_MOVE_FLAGS[0], True)


def _apply_z(item, kind: LayerGroup, order: float) -> None:
    if order:
        order = max(-MAX_ORDER, min(MAX_ORDER, order))
        item.setZValue(GROUP_Z[kind] + order * Z_STEP)
        item.setData(_Z_OVERRIDE_KEY, True)
    elif item.data(_Z_OVERRIDE_KEY):
        item.setZValue(GROUP_Z[kind])
        item.setData(_Z_OVERRIDE_KEY, None)


def apply_layer_state(item: QGraphicsItem, doc: DocumentLayers, viewer=None) -> None:
    kind = kind_of(item)
    if kind is None:
        return
    props = get_item_layer_props(item)
    eff = doc.effective(kind, props)
    if not eff.visible:
        _deselect(item, viewer)  # a hidden item must not stay the edit target
    if item.isVisible() != eff.visible:
        item.setVisible(eff.visible)
    if item.opacity() != eff.opacity:
        item.setOpacity(eff.opacity)
    _apply_lock(item, kind, eff.locked, viewer)
    _apply_z(item, kind, props.z)
