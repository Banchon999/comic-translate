"""Every name a module uses at runtime is actually importable from it.

`ruff --select F821` found two that were not: drawing_manager called
adjust_text_line_coordinates and detect_content_mask_in_bbox without importing
either. Both sat inside a broad `except Exception`, so instead of crashing, the
Segment tool quietly produced a plain rectangle instead of a fitted mask —
every time, in webtoon mode and whenever a box had no matching TextBlock.

A NameError that a bare except turns into a silent downgrade is exactly the
kind of thing no amount of clicking around finds.
"""

import pytest


@pytest.mark.parametrize(
    "module_path, names",
    [
        (
            "app.ui.canvas.drawing_manager",
            ["adjust_text_line_coordinates", "detect_content_mask_in_bbox",
             "build_block_mask_data", "peek_page_mask"],
        ),
        (
            "modules.utils.glossary_extractor",
            ["GlossaryEntry", "term_key", "split_into_chunks", "extract_glossary_terms"],
        ),
        (
            "app.ui.canvas.image_viewer",
            ["layer_state", "TextBlockItem", "MoveableRectItem"],
        ),
    ],
)
def test_module_resolves_the_names_it_uses(qapp, module_path, names):
    import importlib

    module = importlib.import_module(module_path)
    missing = [name for name in names if not hasattr(module, name)]
    assert not missing, f"{module_path} would raise NameError for: {missing}"


@pytest.mark.parametrize(
    "module_path",
    [
        "modules.utils.glossary",
        "modules.utils.glossary_extractor",
        "modules.utils.paths",
        "modules.utils.workspaces",
        "app.ui.canvas.handles",
        "app.ui.canvas.layer_state",
        "app.ui.canvas.document_layers",
        "app.ui.canvas.drawing_manager",
        "app.ui.canvas.image_viewer",
        "app.ui.settings.glossary_page",
        "app.controllers.psd_exporter",
        "modules.rendering.render",
        "pipeline.batch_processor",
    ],
)
def test_module_imports_cleanly(qapp, module_path):
    import importlib

    importlib.import_module(module_path)
