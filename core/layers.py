"""The document-wide layer model (Qt-free).

A page is painted as five fixed **groups**, bottom to top: the raw image, the
inpaint patches, brush strokes, detection boxes, and the editable text. They
are the same groups the PSD export writes (boxes and strokes are working
layers and are not exported).

Every editable object inside a group is its own **layer**, with visibility,
lock, opacity, a display name and an order within its group. Those per-object
props live on the object's own state dict under one optional ``"layer"`` key,
next to its ``object_id`` — so they travel through undo snapshots, project
save/load and webtoon split/merge exactly the way the object does. A key is
written only when it differs from the default, so an untouched object (and an
untouched project) serialises exactly as it did before layers existed.

Group props (visible / locked / opacity) are document-wide: hiding "Editable
Text" hides it on every page. They live in ``DocumentLayers`` and are saved in
the project manifest.

Nothing here draws anything; applying these props to scene items is the
canvas's job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class LayerGroup(str, Enum):
    RAW = "raw"
    PATCHES = "patches"
    STROKES = "strokes"
    BOXES = "boxes"
    TEXT = "text"


# Bottom to top.
PAINT_ORDER: tuple[LayerGroup, ...] = (
    LayerGroup.RAW,
    LayerGroup.PATCHES,
    LayerGroup.STROKES,
    LayerGroup.BOXES,
    LayerGroup.TEXT,
)

# English source strings; the UI translates them. RAW / PATCHES / TEXT match
# the PSD group names the exporter writes.
DEFAULT_GROUP_NAMES: dict[LayerGroup, str] = {
    LayerGroup.RAW: "Raw Image",
    LayerGroup.PATCHES: "Inpaint Patches",
    LayerGroup.STROKES: "Brush Strokes",
    LayerGroup.BOXES: "Boxes",
    LayerGroup.TEXT: "Editable Text",
}

# Groups a flattened export (PNG/JPG/PDF/CBZ, PSD composite) can contain.
OUTPUT_GROUPS: frozenset[LayerGroup] = frozenset(
    {LayerGroup.RAW, LayerGroup.PATCHES, LayerGroup.TEXT}
)


def _clamp_opacity(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 1.0
    if v != v:  # NaN
        return 1.0
    return min(1.0, max(0.0, v))


@dataclass
class LayerProps:
    """One object's own layer props. Defaults mean "as if layers didn't exist"."""

    visible: bool = True
    locked: bool = False
    opacity: float = 1.0
    name: str = ""
    z: float = 0.0  # order within its group; higher paints on top

    def is_default(self) -> bool:
        return self == LayerProps()

    def to_dict(self) -> dict | None:
        """Only the non-default fields, or None when nothing differs."""
        out: dict[str, Any] = {}
        if not self.visible:
            out["visible"] = False
        if self.locked:
            out["locked"] = True
        if self.opacity != 1.0:
            out["opacity"] = self.opacity
        if self.name:
            out["name"] = self.name
        if self.z != 0.0:
            out["z"] = self.z
        return out or None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "LayerProps":
        """Tolerant of None, missing keys, bad values and unknown keys."""
        if not isinstance(data, Mapping):
            return cls()
        try:
            z = float(data.get("z", 0.0))
        except (TypeError, ValueError):
            z = 0.0
        return cls(
            visible=bool(data.get("visible", True)),
            locked=bool(data.get("locked", False)),
            opacity=_clamp_opacity(data.get("opacity", 1.0)),
            name=str(data.get("name", "") or ""),
            z=z if z == z else 0.0,
        )


def layer_dict(props: LayerProps | Mapping[str, Any] | None) -> dict | None:
    """Normalise to the stored form: a minimal dict, or None for defaults."""
    if isinstance(props, LayerProps):
        return props.to_dict()
    return LayerProps.from_dict(props).to_dict()


@dataclass
class GroupProps:
    visible: bool = True
    locked: bool = False
    opacity: float = 1.0

    def to_dict(self) -> dict | None:
        out: dict[str, Any] = {}
        if not self.visible:
            out["visible"] = False
        if self.locked:
            out["locked"] = True
        if self.opacity != 1.0:
            out["opacity"] = self.opacity
        return out or None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "GroupProps":
        if not isinstance(data, Mapping):
            return cls()
        return cls(
            visible=bool(data.get("visible", True)),
            locked=bool(data.get("locked", False)),
            opacity=_clamp_opacity(data.get("opacity", 1.0)),
        )


@dataclass(frozen=True)
class EffectiveLayer:
    """What an object actually gets once its group is taken into account."""

    visible: bool
    locked: bool
    opacity: float


@dataclass
class DocumentLayers:
    """Document-wide group props."""

    groups: dict[LayerGroup, GroupProps] = field(
        default_factory=lambda: {g: GroupProps() for g in LayerGroup}
    )

    def group(self, group: LayerGroup) -> GroupProps:
        return self.groups.setdefault(LayerGroup(group), GroupProps())

    def effective(
        self, group: LayerGroup, obj: LayerProps | Mapping[str, Any] | None = None
    ) -> EffectiveLayer:
        g = self.group(group)
        o = obj if isinstance(obj, LayerProps) else LayerProps.from_dict(obj)
        return EffectiveLayer(
            visible=g.visible and o.visible,
            locked=g.locked or o.locked,
            opacity=g.opacity * o.opacity,
        )

    def to_dict(self) -> dict:
        """Only groups that differ from the default; {} when none do."""
        out = {}
        for g, props in self.groups.items():
            d = props.to_dict()
            if d:
                out[g.value] = d
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "DocumentLayers":
        doc = cls()
        if isinstance(data, Mapping):
            for key, value in data.items():
                try:
                    group = LayerGroup(key)
                except ValueError:
                    continue  # a group this version does not know
                doc.groups[group] = GroupProps.from_dict(value)
        return doc


def is_locked_state(state: Mapping[str, Any] | None) -> bool:
    """Whether a serialised object (a state dict) is locked by its own props."""
    if not isinstance(state, Mapping):
        return False
    return LayerProps.from_dict(state.get("layer")).locked


def merge_preserving_locked(existing: list, fresh: list) -> list:
    """Replace a page's objects with freshly generated ones, except those the
    user locked.

    Batch processing rebuilds a page's text from scratch. A locked object is
    the user saying "leave this alone", so it is kept, and a fresh object with
    the same ``object_id`` (a re-render of the same block) is dropped rather
    than stacked on top of it.
    """
    locked = [s for s in existing or [] if is_locked_state(s)]
    if not locked:
        return list(fresh)
    locked_ids = {s.get("object_id") for s in locked if s.get("object_id")}
    kept_fresh = [s for s in fresh if not (s.get("object_id") and s.get("object_id") in locked_ids)]
    return locked + kept_fresh
