"""Named text styles: the look of a text item, saved and re-applied (Qt-free).

A preset holds only the fields it sets; every field is optional and a None
leaves the item's own value alone. That lets one mechanism serve two uses:
applying a preset by hand (every field it has, font and size included), and a
per-text-class *default* preset that newly rendered text picks up — where only
the look is applied (`LOOK_FIELDS`), because the renderer already fitted the
text to its bubble in its own font and size, and a different face, weight or
spacing would overflow the box it was fitted to.

Built-ins are read-only; the user's own presets live in
``<user data>/text_presets.json`` beside the glossaries and prompts.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from typing import Optional

from core.text_style import stroke_layers_payload

from .paths import get_user_data_dir

PRESET_FILE = "text_presets.json"

#: What a default preset changes on freshly fitted text: colour and effects,
#: never anything that changes the text's size.
LOOK_FIELDS = (
    "color", "outline", "outline_color", "outline_width", "stroke_layers",
    "shadow_enabled", "shadow_color", "shadow_offset", "shadow_blur",
    "gradient_enabled", "gradient_color", "gradient_angle",
    "warp_style", "warp_bend", "curvature",
)


@dataclass
class TextPreset:
    name: str
    builtin: bool = False
    font_family: Optional[str] = None
    font_size: Optional[float] = None
    color: Optional[str] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    underline: Optional[bool] = None
    line_spacing: Optional[float] = None
    letter_spacing: Optional[float] = None
    outline: Optional[bool] = None
    outline_color: Optional[str] = None
    outline_width: Optional[float] = None
    stroke_layers: Optional[list] = None
    shadow_enabled: Optional[bool] = None
    shadow_color: Optional[str] = None
    shadow_offset: Optional[tuple] = None
    shadow_blur: Optional[float] = None
    gradient_enabled: Optional[bool] = None
    gradient_color: Optional[str] = None
    gradient_angle: Optional[float] = None
    warp_style: Optional[str] = None
    warp_bend: Optional[float] = None
    curvature: Optional[float] = None
    extra: dict = field(default_factory=dict)  # keys from a newer version, kept on save

    def values(self, only=None) -> dict:
        """The fields this preset sets, optionally limited to `only`."""
        out = {}
        for f in fields(self):
            if f.name in ("name", "builtin", "extra"):
                continue
            if only is not None and f.name not in only:
                continue
            value = getattr(self, f.name)
            if value is not None:
                out[f.name] = value
        return out

    def to_dict(self) -> dict:
        data = {key: value for key, value in asdict(self).items() if value is not None and key not in ("builtin", "extra")}
        if self.stroke_layers is not None:
            data["stroke_layers"] = stroke_layers_payload(self.stroke_layers)
        if self.shadow_offset is not None:
            data["shadow_offset"] = list(self.shadow_offset)
        data.update(self.extra)
        return data

    @classmethod
    def from_dict(cls, data: dict, builtin: bool = False) -> "TextPreset":
        known = {f.name for f in fields(cls)} - {"builtin", "extra"}
        kwargs = {key: data[key] for key in known if key in data}
        kwargs.setdefault("name", str(data.get("name", "")))
        if kwargs.get("shadow_offset") is not None:
            kwargs["shadow_offset"] = tuple(float(v) for v in kwargs["shadow_offset"])
        extra = {key: value for key, value in data.items() if key not in known}
        return cls(builtin=builtin, extra=extra, **kwargs)


BUILTIN_PRESETS = (
    TextPreset("Speech", True, color="#000000", bold=False, italic=False,
               outline=True, outline_color="#ffffff", outline_width=1.5, stroke_layers=[],
               shadow_enabled=False, gradient_enabled=False, warp_style="", warp_bend=0.0, curvature=0.0),
    TextPreset("Shout", True, color="#111111", bold=True, italic=False,
               outline=True, outline_color="#ffffff", outline_width=3.0,
               stroke_layers=[{"color": "#ff111111", "width": 3.0}],
               shadow_enabled=False, gradient_enabled=False, warp_style="bulge", warp_bend=0.35, curvature=0.0),
    TextPreset("Thought", True, color="#333333", bold=False, italic=True,
               outline=True, outline_color="#ffffff", outline_width=2.0, stroke_layers=[],
               shadow_enabled=False, gradient_enabled=False, warp_style="", warp_bend=0.0, curvature=0.0),
    TextPreset("Narration", True, color="#1a1a1a", bold=False, italic=False,
               outline=False, stroke_layers=[],
               shadow_enabled=False, gradient_enabled=False, warp_style="", warp_bend=0.0, curvature=0.0),
    TextPreset("SFX", True, color="#ff3b6b", bold=True, italic=False,
               outline=True, outline_color="#ffffff", outline_width=3.0,
               stroke_layers=[{"color": "#ff111111", "width": 5.0}, {"color": "#ffffd400", "width": 4.0}],
               shadow_enabled=True, shadow_color="#6e000000", shadow_offset=(6.0, 8.0), shadow_blur=4.0,
               gradient_enabled=False, warp_style="arch", warp_bend=0.4, curvature=0.0),
)


def apply_to_state(state: dict, preset: Optional[TextPreset], only=LOOK_FIELDS) -> dict:
    """A rendered text state with the preset's fields written over it.

    The state is a `build_text_item_state` dict (what the batch renderers
    produce and a project saves), so this is the whole of "a default preset
    for new text" on the pipeline side — no Qt, no item.
    """
    if preset is None:
        return state
    values = preset.values(only)
    for key, value in values.items():
        if key == "color":
            state["text_color"] = value
        elif key == "stroke_layers":
            if value:
                state["stroke_layers"] = stroke_layers_payload(value)
            else:
                state.pop("stroke_layers", None)
        elif key in ("warp_style", "warp_bend"):
            continue  # written together below
        elif key == "shadow_offset":
            state[key] = tuple(value)
        else:
            state[key] = value
    if "warp_style" in values or "warp_bend" in values:
        style, bend = values.get("warp_style", ""), float(values.get("warp_bend", 0.0) or 0.0)
        if style and bend:
            state["warp_style"], state["warp_bend"] = style, bend
        else:
            state.pop("warp_style", None)
            state.pop("warp_bend", None)
    if values.get("outline") is False:
        state["outline_color"] = None
    return state


class TextPresetStore:
    """Built-ins followed by the user's presets, by name."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(get_user_data_dir(), PRESET_FILE)
        self._user: list[TextPreset] = []
        self.load()

    def load(self) -> None:
        self._user = []
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return
        names = {preset.name for preset in BUILTIN_PRESETS}
        for entry in data.get("presets", []) if isinstance(data, dict) else []:
            if isinstance(entry, dict) and entry.get("name") and entry["name"] not in names:
                self._user.append(TextPreset.from_dict(entry))
                names.add(entry["name"])

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "presets": [p.to_dict() for p in self._user]}, handle,
                      ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def presets(self) -> list[TextPreset]:
        return list(BUILTIN_PRESETS) + list(self._user)

    def names(self) -> list[str]:
        return [preset.name for preset in self.presets()]

    def get(self, name: str) -> Optional[TextPreset]:
        return next((preset for preset in self.presets() if preset.name == name), None)

    def put(self, preset: TextPreset) -> TextPreset:
        """Add or replace a user preset. Built-in names are refused."""
        if any(p.name == preset.name for p in BUILTIN_PRESETS):
            raise ValueError(f"{preset.name!r} is a built-in style")
        if not preset.name.strip():
            raise ValueError("a style needs a name")
        preset.builtin = False
        self._user = [p for p in self._user if p.name != preset.name] + [preset]
        self.save()
        return preset

    def delete(self, name: str) -> bool:
        before = len(self._user)
        self._user = [p for p in self._user if p.name != name]
        if len(self._user) != before:
            self.save()
            return True
        return False

    def rename(self, old: str, new: str) -> None:
        preset = next((p for p in self._user if p.name == old), None)
        if preset is None:
            raise KeyError(old)
        if self.get(new) is not None and new != old:
            raise ValueError(f"{new!r} already exists")
        preset.name = new
        self.save()
