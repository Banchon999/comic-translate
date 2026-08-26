"""Qt-free values shared by the pipeline, the engines and the UI.

Nothing in this package may import PySide6, `app`, or anything that does.
`scripts/check_headless.py` enforces that for `pipeline/` and `modules/`;
`core/` is held to the same rule because it sits underneath both.

The Qt layer converts at its own boundary — `QColor(resolve_text_color(...))`
rather than passing a QColor down.
"""
