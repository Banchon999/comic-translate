"""The one entry point for changing layers.

Object props (hide, lock, opacity, rename, order) are edits to the page, so they
go through the undo stack like any other edit. Group props (hiding "Editable
Text" across the whole document) are a way of looking at the document, like
zoom: they are saved with the project and mark it modified, but are not undo
steps.
"""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QObject, Signal

from app.ui.canvas.scene_registry import (
    find_by_id,
    get_item_layer,
    get_item_layer_props,
    iter_items,
    kind_of,
    object_id_of,
)
from app.ui.commands.layers import SetLayerPropsCommand, apply_object_layer
from core.layers import LayerGroup, LayerProps


class LayerController(QObject):
    # Emitted after any layer change, so views (the layers panel) can refresh.
    layers_changed = Signal()

    def __init__(self, main):
        super().__init__()
        self.main = main

    # --- queries ---------------------------------------------------------------

    @property
    def viewer(self):
        return self.main.image_viewer

    def document(self):
        return self.main.document_layers

    def object_props(self, object_id: str) -> LayerProps | None:
        item = find_by_id(self.viewer._scene, object_id, viewer=self.viewer)
        return get_item_layer_props(item) if item is not None else None

    def page_objects(self) -> list[tuple[LayerGroup, str, object]]:
        """(group, object_id, item) for every editable object on screen."""
        out = []
        for item in iter_items(self.viewer._scene, viewer=self.viewer):
            kind = kind_of(item)
            if kind is LayerGroup.RAW:
                continue
            oid = object_id_of(item)
            if oid:
                out.append((kind, oid, item))
        return out

    # --- object props (undoable) -------------------------------------------------

    def set_object_props(self, object_ids: Iterable[str], text: str = "Layer", **changes) -> bool:
        """Change some fields of several objects' props as one undo step.
        Returns False when nothing would change."""
        batch = []
        for oid in object_ids:
            item = find_by_id(self.viewer._scene, oid, viewer=self.viewer)
            if item is None:
                continue
            old = get_item_layer(item)
            props = LayerProps.from_dict(old)
            for key, value in changes.items():
                setattr(props, key, value)
            new = props.to_dict()
            if new != old:
                batch.append((oid, old, new))
        if not batch:
            return False
        cmd = SetLayerPropsCommand(self.main, batch, text)
        stack = self.main.undo_group.activeStack()
        if stack is not None:
            stack.push(cmd)
        else:
            cmd.redo()
        self.main.mark_project_dirty()
        self.layers_changed.emit()
        return True

    def reorder(self, ordered_ids: list[str]) -> bool:
        """Give objects of one group an explicit order, bottom to top."""
        batch = []
        for rank, oid in enumerate(ordered_ids, start=1):
            item = find_by_id(self.viewer._scene, oid, viewer=self.viewer)
            if item is None:
                continue
            old = get_item_layer(item)
            props = LayerProps.from_dict(old)
            props.z = float(rank)
            new = props.to_dict()
            if new != old:
                batch.append((oid, old, new))
        if not batch:
            return False
        cmd = SetLayerPropsCommand(self.main, batch, "Reorder layers")
        stack = self.main.undo_group.activeStack()
        if stack is not None:
            stack.push(cmd)
        else:
            cmd.redo()
        self.main.mark_project_dirty()
        self.layers_changed.emit()
        return True

    # --- group props (view state) --------------------------------------------------

    def set_group_props(self, group: LayerGroup, **changes) -> bool:
        props = self.document().group(group)
        before = (props.visible, props.locked, props.opacity)
        for key, value in changes.items():
            setattr(props, key, value)
        if (props.visible, props.locked, props.opacity) == before:
            return False
        self.viewer.refresh_layers()
        self.main.mark_project_dirty()
        self.layers_changed.emit()
        return True

    # Used by the project loader and tests: apply without an undo entry.
    def apply_directly(self, object_id: str, layer: dict | None) -> None:
        apply_object_layer(self.main, object_id, layer)
        self.viewer.refresh_layers()
        self.layers_changed.emit()
