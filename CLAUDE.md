# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Comic Translate is a PySide6 (Qt) desktop GUI application that automatically translates comics/manga/manhwa/webtoons. The pipeline: detect speech bubbles & text → OCR → translate (LLM or traditional) → inpaint (clean) the original text → render translated text back onto the image. It also supports a browser extension and PSD/CBZ/PDF/EPUB import-export, but the desktop app (`comic.py`) is the primary surface developed here.

There is no test suite, linter config, or CI test job in this repo — verification is done by running the app manually and/or writing throwaway scripts against the relevant modules (see "Manual verification" below).

## Commands

Setup (uses `uv`, Python 3.12):
```bash
uv init --python 3.12
uv add -r requirements.txt --compile-bytecode
uv pip install onnxruntime-gpu   # optional, only if an NVIDIA GPU is available
```

Run the app:
```bash
uv run comic.py
```

Syntax/import check the whole tree (closest thing to a build check — there is no test suite):
```bash
python -m compileall -q modules app pipeline
```

Regenerate UI translations (see "Localization" below — never call `lrelease` directly):
```bash
python scripts/build_translations.py --update --compile th   # rescan sources, then compile Thai
python scripts/build_translations.py --compile               # recompile every language
```

Build a distributable (PyInstaller, mirrors `.github/workflows/build-*.yml`):
```bash
pyinstaller --noconfirm --clean --name ComicTranslate --add-data "resources:resources" comic.py   # Linux
pyinstaller --noconfirm --clean --windowed --name ComicTranslate --icon resources/icons/icon.ico --add-data "resources;resources" comic.py   # Windows (PowerShell `;` separator)
```

### Manual verification (no test suite exists)

For changes to a `modules/*` engine or utility, the practical way to verify is a standalone script run against a venv with the relevant deps installed (numpy/pillow/onnxruntime for detection/OCR/inpainting math; add pyside6-essentials only if the code path imports Qt). Importing `modules.utils` triggers `modules/utils/__init__.py` → `textblock.py` → `imkit`, which needs `mahotas`; importing anything under `app.ui` pulls in the full Qt stack. Prefer testing pure logic (e.g. `modules/utils/glossary.py`, `modules/rendering/render.py` wrap helpers, mask/box math) in isolation before wiring it into the Qt-dependent layers. For UI-affecting changes, actually launch `uv run comic.py` (or under `xvfb-run` in a headless environment) and exercise the feature — Qt widget wiring bugs do not show up in `compileall`.

## Architecture

### Layering

```
comic.py / controller.py (ComicTranslate, QMainWindow subclass)
  └─ app/ui/          Qt widgets: main window, canvas/graphics-scene editor, settings pages, dialogs
  └─ app/controllers/  Per-concern controllers the main window delegates to (image state, text editing,
                        projects/autosave, PSD import/export, manual workflow, batch report, shortcuts...)
  └─ pipeline/          Orchestrates modules/* into the end-to-end translate pipeline (see below)
       └─ modules/       Stateless-ish engines: detection, ocr, translation, inpainting, rendering, utils
```

`controller.py` defines `ComicTranslate(ComicTranslateUI)` — the QMainWindow. It owns `self.blk_list` (current page's `TextBlock`s), `self.image_files`, `self.image_states` (per-image dict: source/target lang, blk_list, viewer state, skip flag...), and instantiates `ComicTranslatePipeline` (`pipeline/main_pipeline.py`) which wires together the handler classes: `BlockDetectionHandler`, `OCRHandler`, `TranslationHandler`, `InpaintingHandler`, `SegmentationHandler`, `BatchProcessor`, `WebtoonBatchProcessor` — all sharing one `CacheManager`. `app/controllers/*` handle UI-adjacent concerns (text item editing, project save/load/autosave, PSD export, manual step-by-step workflow) and are attached to the main window as `self.xxx_ctrl`.

Two run modes share the same `modules/*` engines but have separate driving code: **Manual mode** (`app/controllers/manual_workflow.py` + the per-step buttons in `controller.py`: Detect → OCR → Translate → Segment → Clean → Render) and **Automatic/Batch mode** (`pipeline/batch_processor.py` for single images, `pipeline/webtoon_batch/` for long-strip webtoons which are chunked and stitched — see `chunk.py`/`flow.py`/`render.py`).

### Factory pattern for pluggable engines

Every pipeline stage that has multiple interchangeable implementations follows the same shape: an abstract base class, per-implementation subclasses, and a `Factory` class with a `create_engine(...)` classmethod plus an internal `_engines` cache keyed by a hash of the model/settings so switching engines mid-session doesn't require re-initializing unrelated ones.

- `modules/detection/factory.py` (`DetectionEngineFactory`) — `base.py` defines `DetectionEngine`; implementations include `rtdetr_v2_onnx.py`/`rtdetr_v2.py` (RT-DETR-v2, onnx/torch backends) and `bubble_seg_onnx.py` (YOLOv8-seg speech bubble detector, hybrid with RT-DETR text boxes).
- `modules/ocr/factory.py` (`OCRFactory`) — `base.py` defines `OCREngine`; implementations per-language/engine live in `manga_ocr/`, `pororo/`, `ppocr/`, plus API-based engines (`gpt_ocr.py`, `gemini_ocr.py`, `google_ocr.py`, `microsoft_ocr.py`, `easy_ocr.py`) and `user_ocr.py` for the hosted/managed backend.
- `modules/translation/factory.py` (`TranslationFactory`) — `base.py` defines `TranslationEngine`/`LLMTranslation`/`TraditionalTranslation`; `llm/base.py`'s `BaseLLMTranslation` is the shared LLM engine base (handles image encoding, system prompt via `PromptManager`, JSON-in/JSON-out translation contract). LLM engines (`llm/gpt.py`, `claude.py`, `gemini.py`, `deepseek.py`, `custom.py`, `openrouter.py`) subclass it; `openrouter.py` and `custom.py` both subclass `gpt.py`'s `GPTTranslation` since they're OpenAI-compatible APIs (note `GPTTranslation.MAX_TOKENS_PARAM` — OpenRouter needs `max_tokens` instead of `max_completion_tokens`). `deepl.py`, `microsoft.py`, `yandex.py` are non-LLM `TraditionalTranslation` engines. `user.py` (`UserTranslator`) is used instead of any of the above when the user is signed in to the managed/hosted account (checked via `app/account/auth/token_storage.get_token`).
- `modules/inpainting/` (`pipeline_config.py`'s `inpaint_map` dict, not a factory class) — `base.py` defines `InpaintModel`/`DiffusionInpaintModel`; implementations: `lama.py`, `aot.py`, `mi_gan.py`, and `smart_fill.py` (solid-fill cleaning: ranks progressively grown masks by border-colour uniformity and fills flat regions with the border's median colour, falling back to LaMa for non-uniform ones). The ranking itself lives in `mask_fitting.py`, ported from PanelCleaner.

### Text mask generation (what gets cleaned)

`modules/utils/image_utils.py`'s `build_block_mask_data` decides which pixels inside a detected box are lettering. Two sources, chosen by `Settings > Tools > Use the AI text segmentation model` and dispatched through `modules/utils/text_segmentation.py`:

- **Thresholding** (`modules/detection/utils/content.py`'s `detect_content_mask_in_bbox`) — Otsu plus connected-component filtering, per crop. Cheap and wrong whenever the lettering is not simply the darkest or lightest thing in the box.
- **Model** (default) — `modules/detection/text_seg_onnx.py` runs comic-text-detector's `seg` head over the whole page once (cached per page), and `modules/inpainting/text_mask_refine.py` uses that prediction to referee several candidate binarisations per box, keeping only components that improve agreement with it. This is the same algorithm PanelCleaner runs, ported to `imkit`; the file carries GPL-3.0 attribution.

Callers pass the page prediction down as `page_text_mask=` (see `pipeline/batch_processor.py`, `pipeline/webtoon_batch/chunk.py`, `pipeline/inpainting.py`). Interactive paths use `peek_page_mask()` instead, which returns the cached prediction or `None` and never runs inference — a brush stroke must not wait seconds for a model.

`imkit` is a partial `cv2` replacement and its semantics are not always OpenCV's; when porting cv2 code, check the specific function first (`erode`/`dilate` binarise their output, so grayscale morphology has to be reordered into a threshold plus binary morphology).

## Licensing

This project is distributed under **GPL-3.0-or-later** (`LICENSE`). It is a fork of the Apache-2.0 licensed [comic-translate](https://github.com/ogkalu2/comic-translate) (`LICENSE-Apache-2.0` retained), combined with GPLv3 code derived from [PanelCleaner](https://github.com/VoxelCubes/PanelCleaner) in `modules/inpainting/mask_fitting.py`. See `NOTICE.md`. Practical consequence when working here: **changes touching GPLv3-derived files cannot be contributed back to the Apache-2.0 upstream.** New files that copy or adapt PanelCleaner code must carry an attribution header like the one in `mask_fitting.py`.

When adding a new engine of any of these kinds: subclass the right base, register it in the factory/map, and add its display name to the relevant combo box list + `value_mappings` dict in `app/ui/settings/settings_ui.py` (translator/OCR/detector/inpainter selection is driven entirely by string name lookups threaded through `SettingsPage.get_tool_selection(...)`).

### Model downloads

All model weights are declared centrally in `modules/utils/download.py`: `ModelID` enum + `ModelDownloader.register(ModelSpec(...))` entries (url, files, sha256, save_dir under `models_base_dir` = `<user data dir>/models`). Engines call `ModelDownloader.get(ModelID.X)` / `.get_file_path(...)` / `.primary_path(...)` at `initialize()` time — downloads happen lazily on first use, not at startup (except whatever's in `mandatory_models`). When adding a model, register it here with a sha256 pin rather than downloading ad hoc.

### TextBlock — the shared unit of work

`modules/utils/textblock.py`'s `TextBlock` flows through the entire pipeline: detection produces `xyxy`/`bubble_xyxy`/`text_class` (`"text_bubble"` vs `"text_free"`), OCR fills `.text`, translation fills `.translation`, rendering reads both. `text_class` is the switch used throughout for bubble-specific behavior (e.g. per-type font selection in `modules/rendering/render.py`'s `font_family_for_block`, bubble-clipped inpainting masks in `modules/utils/image_utils.py`).

### Settings

`app/ui/settings/settings_page.py` (`SettingsPage`) is the single source of truth for all user-configurable state, backed by `QSettings("ComicLabs", "ComicTranslate")`. It composes per-tab widget classes from `app/ui/settings/*_page.py` (tools, credentials, LLMs, glossary, text rendering, project, export, shortcuts, account, about) via `SettingsPageUI` (`settings_ui.py`), which also owns the canonical lists of translator/OCR/detector/inpainter names and the localized-label ↔ internal-name `value_mappings`/`reverse_mappings`. Settings autosave ~1.5s after any change (debounced `QTimer` wired in `SettingsPage.__init__`) in addition to the save-on-close in `controller.py`'s `closeEvent`.

Glossary (`modules/utils/glossary.py`'s `GlossaryManager`) and translation prompts (`modules/utils/prompts.py`'s `PromptManager`) are separate JSON-file-backed stores (under the user data dir, not QSettings) with their own profile/preset systems — glossary is per-series-profile, prompts are per-style-preset (Manga/Manhwa/Webtoon/Comic/custom). Both are injected into the LLM system/user prompt via `SettingsPage.get_extra_context(...)` and `BaseLLMTranslation.get_system_prompt(...)` respectively, and both participate in translation cache keys (`pipeline/cache_manager.py`) so switching profile/preset invalidates stale cached translations.

`modules/utils/workspaces.py`'s `WorkspaceManager` is a third store of the same shape (one JSON per workspace under `<user data>/workspaces/`, plus a `config.json` naming the active one). A workspace bundles the per-series choices that otherwise have to be changed in four places at once: source folders, glossary profile, prompt preset, and language pair. `controller.py`'s `apply_workspace`/`capture_workspace_state` are the only places that read/write those UI widgets from a workspace; the picker lives at the top of the file tree panel.

### Pages sidebar

`app/ui/list_view.py`'s `PageListView` is the flat page strip and stays the ordering model — row index is the index into `main.image_files`. `app/ui/file_tree_panel.py`'s `FileTreePanel` shows the same pages grouped by source folder and is a pure view: `ImageController.refresh_file_tree()` rebuilds it from `image_files`/`image_states` and it does nothing while hidden. Its actions emit **file paths**; the flat list still emits base names, so every handler routes through `ImageController.resolve_page_paths()` (exact path wins, base name is the fallback) — two chapters of a series routinely share `002.png`.

### Editor canvas & layers

`app/ui/canvas/image_viewer.py`'s `ImageViewer` (a `QGraphicsView`) is the editing surface. Scene items are typed by role: `MoveableRectItem` (detection boxes), `TextBlockItem` (rendered translation text), `QGraphicsPathItem` (segmentation/brush strokes), and `QGraphicsPixmapItem` with `.setData(0, hash)` (inpaint patches, added via `app/ui/commands/base.py`'s `PatchCommandBase`). `ImageViewer.layer_visibility` (dict of `boxes`/`strokes`/`patches`/`text` → bool) plus `set_layer_visibility()`/`apply_layer_visibility()` drive the Layers toggle bar in the editor header; `app/ui/canvas/layer_panel.py`'s `LayerPanel` goes one level down, listing individual scene items with per-item show/lock/opacity (rebuilt from the scene on a debounced `QGraphicsScene.changed`, never mirrored into a second model) — every code path that creates one of these item types must apply the current visibility state (see call sites in `image_viewer.py`, `app/ui/commands/base.py`, `app/ui/commands/brush.py`, `app/ui/canvas/drawing_manager.py`). This is purely a display concern — `get_image_array(include_patches=True)` always composes patches for OCR/translation/inpainting regardless of what's toggled on screen.

Undo/redo for canvas edits goes through `QUndoCommand` subclasses in `app/ui/commands/` pushed onto `controller.py`'s `QUndoStack`.

### Caching

`pipeline/cache_manager.py`'s `CacheManager` caches OCR and translation results keyed by `(image_hash, model/translator_key, lang(s), device, ...extra)` — extra key components include the glossary/prompt fingerprint and `extra_context` hash, so results are invalidated exactly when something that could change them changes. Batch processing checks this cache before re-running OCR/translation (see `pipeline/batch_processor.py`'s `_apply_cached_ocr`).

### Webtoon (long-strip) mode

Long vertical-strip images are handled separately throughout: `app/ui/canvas/webtoons/` (viewer/scene management for lazy-loaded tall strips) and `pipeline/webtoon_batch/` (chunked batch detect/OCR/inpaint/render with seam-aware stitching so text isn't cut at chunk boundaries — see `chunk.py`'s stitched-context detection and `_shift_block_vertical`).

### Localization (UI language)

Catalogues live in `resources/translations/ct_<lang>.ts`, compiled to `compiled/ct_<lang>.qm` and loaded by `comic.py`'s `load_translation`. Adding a language means: a `ct_<lang>.ts`, its compiled `.qm`, an entry in `load_translation`'s display-name → code dict and `get_system_language`'s code → display-name dict, plus the display name in `SettingsPageUI.languages` and `value_mappings` (`app/ui/settings/settings_ui.py`). Comic source/target language names are separately mapped back to canonical English by `ComicTranslateUI.lang_mapping` (`app/ui/main_window/window.py`), so translating them is safe — but two languages must never translate to the same string or the dict collapses.

Always build the `.qm` through `scripts/build_translations.py`, not `lrelease`. PySide6 looks a string up under each class name in the object's MRO (so a string written in a mixin or base class is found from a subclass), but `lupdate` names a context after the expression at the call site — `self.main.tr(...)` is filed under the literal context `"self.main"`, which is not a class name and never matches. That silently strands ~60 strings in every language. The script appends a catch-all `@fallback` context to a temporary copy of the `.ts` — the committed `.ts` stays exactly as lupdate writes it — and `ContextFallbackTranslator` consults it when the real contexts miss. Per-context entries still win, so a word translated differently depending on where it appears keeps its specific translation.

A `QTranslator` subclass must return `None`, never `""`, for a miss. An empty string is a valid translation to Qt: it blanks the widget out *and* stops PySide6 walking up the MRO to the class that actually holds the string.

### Language/direction handling

`modules/utils/language_utils.py` centralizes language code mapping, RTL detection (Arabic/Hebrew/Persian), and no-space-language detection (`zh`/`ja`/`th` — affects both LLM text preprocessing and `modules/rendering/render.py`'s word-wrap algorithm, which has a dedicated Thai-aware path using cluster-safe breaks and optional `pythainlp` word segmentation, separate from the CJK "one character per line" fallback). Vertical text layout (CJK) is handled by `app/ui/canvas/text/vertical_layout.py`'s `VerticalTextDocumentLayout`.
