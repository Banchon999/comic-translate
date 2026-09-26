"""Undoable edits to objects' own layer props (hide, lock, opacity, rename, order).

Each change is recorded as (object_id, old props, new props). Applying it sets
the props on the live scene item when the object is on screen, and on its
stored state otherwise — a webtoon page scrolled out of view, or a page the
user has since left — so undoing later still lands. Patches are also kept in
``image_patches``, their persistent store, which is what the project saves.
"""

from __future__ import annotations

from PySide6.QtGui import QUndoCommand

from app.ui.canvas.scene_registry import find_by_id, set_item_layer
from core.layers import layer_dict


def _update_state_lists(main, object_id: str, layer: dict | None) -> bool:
    """Write the props into every stored copy of the object. A page-spanning
    webtoon object is stored once per page it touches, all with one id."""
    found = False

    def _put(entry: dict) -> None:
        nonlocal found
        found = True
        if layer:
            entry["layer"] = dict(layer)
        else:
            entry.pop("layer", None)

    for plist in getattr(main, "image_patches", {}).values():
        for entry in plist:
            if entry.get("object_id") == object_id:
                _put(entry)
    for plist in getattr(main, "in_memory_patches", {}).values():
        for entry in plist:
            if entry.get("object_id") == object_id:
                _put(entry)

    states = getattr(main, "image_states", {})
    for state in states.values():
        if not isinstance(state, dict):
            continue
        viewer_state = state.get("viewer_state") or {}
        for key in ("rectangles", "text_items_state"):
            for entry in viewer_state.get(key) or []:
                if isinstance(entry, dict) and entry.get("object_id") == object_id:
                    _put(entry)
        for entry in state.get("brush_strokes") or []:
            if isinstance(entry, dict) and entry.get("object_id") == object_id:
                _put(entry)
    return found


def apply_object_layer(main, object_id: str, layer: dict | None) -> None:
    viewer = main.image_viewer
    item = find_by_id(viewer._scene, object_id, viewer=viewer)
    if item is not None:
        set_item_layer(item, layer)
    # Stored copies too: patches persist through image_patches, and an object
    # on an unloaded page exists only there.
    _update_state_lists(main, object_id, layer)


class SetLayerPropsCommand(QUndoCommand):
    def __init__(self, main, changes: list[tuple[str, dict | None, dict | None]], text: str = "Layer"):
        super().__init__(text)
        self.main = main
        self.changes = [(oid, layer_dict(old), layer_dict(new)) for oid, old, new in changes]

    def _apply(self, use_new: bool) -> None:
        for oid, old, new in self.changes:
            apply_object_layer(self.main, oid, new if use_new else old)
        self.main.image_viewer.refresh_layers()

    def redo(self):
        self._apply(True)

    def undo(self):
        self._apply(False)
