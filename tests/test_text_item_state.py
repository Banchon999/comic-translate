"""The pipeline's state dict and the canvas's must stay interchangeable.

`core.text_style.build_text_item_state` is what the batch renderers now produce
instead of a `TextItemProperties`, and `TextItemProperties.from_dict` is what
reads it back. Nothing in the type system connects the two: a field renamed on
one side and not the other loses that property silently on every batch-rendered
page, which is exactly the kind of bug that only shows up as "the outline is
gone" three releases later.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from core.text_style import (
    OutlineType,
    TextItemState,
    build_text_item_state,
)
from app.ui.canvas.text.text_item_properties import TextItemProperties


def _state(**overrides):
    args = dict(
        text="Hello",
        font_family="Comic",
        font_size=24.0,
        text_color="#112233",
        alignment=Qt.AlignmentFlag.AlignHCenter,
        line_spacing=1.4,
        outline_color="#ffffff",
        outline_width=2.0,
        bold=True,
        italic=False,
        underline=False,
        position=(10, 20),
        rotation=5.0,
        scale=1.0,
        transform_origin=(3, 4),
        width=120.0,
        height=60.0,
        direction=Qt.LayoutDirection.LeftToRight,
        vertical=False,
        outline=True,
    )
    args.update(overrides)
    return build_text_item_state(**args)


def test_state_dict_has_exactly_the_keys_to_dict_produces():
    assert set(_state()) == set(TextItemProperties().to_dict())


def test_defaults_match_between_the_two_state_models():
    """A field the pipeline omits must default the same on both sides."""
    core_defaults = TextItemState().as_dict()
    qt_defaults = TextItemProperties().to_dict()
    mismatched = {
        key: (core_defaults[key], qt_defaults[key])
        for key in core_defaults
        # alignment and direction are Qt enums on the Qt side by default and
        # None on the core side, which from_dict handles; everything else has
        # to agree.
        if key not in ("alignment", "direction")
        and core_defaults[key] != qt_defaults[key]
    }
    assert not mismatched, f"defaults drifted: {mismatched}"


def test_from_dict_reads_pipeline_colours_given_as_strings(qapp):
    props = TextItemProperties.from_dict(_state())
    assert props.text_color == QColor("#112233")
    assert props.outline_color == QColor("#ffffff")


def test_from_dict_preserves_geometry_and_text(qapp):
    props = TextItemProperties.from_dict(_state())
    assert props.text == "Hello"
    assert props.font_family == "Comic"
    assert props.font_size == 24.0
    assert props.position == (10, 20)
    assert props.transform_origin == (3, 4)
    assert (props.width, props.height) == (120.0, 60.0)
    assert props.rotation == 5.0
    assert props.bold is True


def test_outline_produces_one_full_document_span():
    outlines = _state()["selection_outlines"]
    assert len(outlines) == 1
    only = outlines[0]
    assert (only.start, only.end) == (0, len("Hello"))
    assert only.type is OutlineType.Full_Document
    assert only.color == "#ffffff"


def test_no_outline_produces_no_spans():
    assert _state(outline=False)["selection_outlines"] == []


def test_outline_flag_survives_the_round_trip(qapp):
    assert TextItemProperties.from_dict(_state(outline=True)).outline is True
    assert TextItemProperties.from_dict(_state(outline=False)).outline is False


def test_outline_types_are_the_same_class_on_both_sides():
    """The Qt layer re-exports rather than redefining, so isinstance holds."""
    from app.ui.canvas.text_item import OutlineInfo as QtOutlineInfo
    from app.ui.canvas.text_item import OutlineType as QtOutlineType
    from core.text_style import OutlineInfo as CoreOutlineInfo

    assert QtOutlineInfo is CoreOutlineInfo
    assert QtOutlineType is OutlineType
    assert isinstance(_state()["selection_outlines"][0], QtOutlineInfo)
    assert QtOutlineType.Full_Document is OutlineType.Full_Document
