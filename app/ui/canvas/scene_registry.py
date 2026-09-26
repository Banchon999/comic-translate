"""Which layer group a scene item belongs to, and where its layer props live.

This is the one place that decides an item's kind. The same class checks used
to be repeated wherever a caller needed them (patch = a pixmap item carrying a
hash, stroke = a bare path item, ...); callers should ask ``kind_of`` instead.

Lookups scan ``scene.items()`` rather than keeping an index. That is
deliberate: Qt's convenience adders (``scene.addPath``, ``addPixmap``) insert
items from C++ and never pass through a Python ``addItem`` override, so an index
maintained that way silently misses items. A page holds at most a few hundred
items, and a scan is cheap next to anything that would trigger one.

An item's own layer props (see ``core.layers.LayerProps``) are stored on the
item in data slot ``LAYER_KEY`` as the minimal dict form — None when default —
so every item type carries them the same way ``object_id`` is carried.
"""

from __future__ import annotations

from typing import Iterator, Mapping, Any

from PySide6.QtWidgets import QGraphicsItem, QGraphicsPathItem, QGraphicsPixmapItem

from core.layers import LayerGroup, LayerProps, layer_dict

# Data slots on QGraphicsItem: 0 = patch content hash (PatchCommandBase.HASH_KEY),
# 1 = object_id (commands.base.OBJECT_ID_KEY), 2 = layer props.
HASH_KEY = 0
OBJECT_ID_KEY = 1
LAYER_KEY = 2


def is_top_level(item: QGraphicsItem) -> bool:
    """Whether an item has no parent, without calling parentItem().

    In PySide6 (seen on 6.11.2), calling ``parentItem()`` on an item that has
    no parent hands ownership of the C++ item to its Python wrapper, so when
    that wrapper is next garbage-collected the item is deleted and drops out of
    the scene. Items from ``scene.addPath`` and patch pixmaps usually have no
    other live wrapper, so they vanish. ``topLevelItem()`` answers the same
    question without that side effect.
    """
    return item.topLevelItem() is item


def kind_of(item: QGraphicsItem) -> LayerGroup | None:
    """The layer group an item belongs to, or None for items that are not
    part of the page (handles, previews, selection outlines, ...)."""
    # Imported here: both modules import heavy canvas code that itself may
    # import this one.
    from app.ui.canvas.text_item import TextBlockItem
    from app.ui.canvas.rectangle import MoveableRectItem

    if isinstance(item, TextBlockItem):
        return LayerGroup.TEXT
    if isinstance(item, MoveableRectItem):
        return LayerGroup.BOXES
    if isinstance(item, QGraphicsPixmapItem):
        return LayerGroup.PATCHES if item.data(HASH_KEY) is not None else LayerGroup.RAW
    if type(item) is QGraphicsPathItem:
        return LayerGroup.STROKES
    return None


def object_id_of(item: QGraphicsItem) -> str:
    """Text items and boxes keep the id as an attribute; strokes and patches in
    data slot 1. Both are read so callers need not know which."""
    return getattr(item, "object_id", "") or item.data(OBJECT_ID_KEY) or ""


def _excluded(viewer) -> set:
    if viewer is None:
        return set()
    skip = set()
    preview = getattr(getattr(viewer, "drawing_manager", None), "lasso_preview", None)
    if preview is not None:
        skip.add(preview)
    return skip


def iter_items(scene, kind: LayerGroup | None = None, viewer=None) -> Iterator[QGraphicsItem]:
    """Page items of one kind (or of every kind), skipping non-page items and
    the in-progress lasso outline."""
    skip = _excluded(viewer)
    for item in scene.items():
        # topLevelItem(), never parentItem(): see is_top_level.
        if item in skip or not is_top_level(item):
            continue
        k = kind_of(item)
        if k is None or (kind is not None and k is not kind):
            continue
        yield item


def find_by_id(scene, object_id: str, viewer=None) -> QGraphicsItem | None:
    if not object_id:
        return None
    for item in iter_items(scene, viewer=viewer):
        if object_id_of(item) == object_id:
            return item
    return None


def get_item_layer(item: QGraphicsItem) -> dict | None:
    data = item.data(LAYER_KEY)
    return dict(data) if isinstance(data, Mapping) and data else None


def get_item_layer_props(item: QGraphicsItem) -> LayerProps:
    return LayerProps.from_dict(item.data(LAYER_KEY))


def set_item_layer(item: QGraphicsItem, props: LayerProps | Mapping[str, Any] | None) -> None:
    """Store an item's layer props (normalised; defaults store None)."""
    item.setData(LAYER_KEY, layer_dict(props))


def put_layer(target: dict, item: QGraphicsItem) -> dict:
    """Copy an item's layer props into a state dict being serialised, only when
    non-default, so untouched objects serialise exactly as before."""
    layer = get_item_layer(item)
    if layer:
        target["layer"] = layer
    else:
        target.pop("layer", None)
    return target
