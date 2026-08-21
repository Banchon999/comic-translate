# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Comic Translate is a PySide6 (Qt) desktop GUI application that automatically translates comics/manga/manhwa/webtoons. The pipeline: detect speech bubbles & text → OCR → translate (LLM or traditional) → inpaint (clean) the original text → render translated text back onto the image. It also supports a browser extension and PSD/CBZ/PDF/EPUB import-export, but the desktop app (`comic.py`) is the primary surface developed here.

There is a test suite (`tests/`, pytest) and a linter (`ruff.toml`), both run by `.github/workflows/test.yml` on every push and pull request. They cover pure logic and Qt-free-ish widget behaviour; anything involving a model, a network call or how a page actually *looks* still has to be checked by running the app (see "Verification" below).

## Commands

Setup (uses `uv`, Python 3.12):
```bash
uv init --python 3.12
uv add -r requirements.txt --compile-bytecode
uv pip install onnxruntime-gpu   # optional, only if an NVIDIA GPU is available
uv pip install torch torchvision "transformers>=5"   # optional, enables the PaddleOCR-VL engine
```

Run the app:
```bash
uv run comic.py
```

Run the tests and the linter:
```bash
uv pip install -r requirements-dev.txt
pytest                  # conftest.py forces the offscreen Qt platform itself
ruff check .
```

Syntax/import check the whole tree, when you want something faster than the suite:
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

Every **build** workflow is `workflow_dispatch` only — nothing builds automatically on push or PR; `test.yml` is the one exception and is deliberately the opposite, since a gate only matters if it runs unasked. `build-windows-full.yml` is the same Windows build plus the PyTorch stack (`--collect-all torch torchvision transformers`), which is what makes PaddleOCR-VL reachable in a frozen bundle; it is separate because torch takes the download from a few hundred megabytes to several gigabytes, and its final step asserts the three packages actually landed in `dist/` rather than trusting `--collect-all`.

### Verification

`tests/` is where anything worth keeping goes. `conftest.py` does two things before Qt loads: forces the offscreen platform, and repoints `XDG_DATA_HOME`/`XDG_CONFIG_HOME`/`HOME` at a temp directory — without that a run reads and writes the real user's glossaries, settings and models, since `modules/utils/paths.py` resolves them through those variables. A session-scoped `qapp` fixture supplies the one QApplication a process is allowed.

`tests/test_psd_export.py` deliberately reads exported files back with **psd-tools** rather than PhotoshopAPI, which wrote them: asserting with the writer only proves it agrees with itself, and both PSD bugs found so far (a black merged-image section, and a frozen build dying before writing anything) were invisible that way. psd-tools is a dev dependency and cannot replace PhotoshopAPI for export — it creates pixel layers and groups but has no way to author an editable text layer.

The linter is scoped to rules that catch bugs (`E9`, `F`, `B`) rather than style. Its first run found a live one: `drawing_manager.py` called two functions it never imported, inside a broad `except Exception`, so the Segment tool silently produced a plain rectangle instead of a fitted mask in webtoon mode. `ruff.toml`'s `ignore` list is a reviewed backlog, not a verdict — each entry says what it is and how many sites.

Some things the suite cannot reach. For changes to a `modules/*` engine that runs a model, the practical way to verify is still a standalone script run against a venv with the relevant deps installed (numpy/pillow/onnxruntime for detection/OCR/inpainting math; add pyside6-essentials only if the code path imports Qt). Importing `modules.utils` triggers `modules/utils/__init__.py` → `textblock.py` → `imkit`, which needs `mahotas`; importing anything under `app.ui` pulls in the full Qt stack. Prefer testing pure logic (e.g. `modules/utils/glossary.py`, `modules/rendering/render.py` wrap helpers, mask/box math) in isolation before wiring it into the Qt-dependent layers. For UI-affecting changes, actually launch `uv run comic.py` (or under `xvfb-run` in a headless environment) and exercise the feature — Qt widget wiring bugs do not show up in `compileall`.

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
- `modules/ocr/factory.py` (`OCRFactory`) — `base.py` defines `OCREngine`; implementations per-language/engine live in `manga_ocr/`, `pororo/`, `ppocr/`, plus API-based engines (`gpt_ocr.py`, `gemini_ocr.py`, `google_ocr.py`, `microsoft_ocr.py`, `easy_ocr.py`) and `user_ocr.py` for the hosted/managed backend. Two engines are **optional** — `easy_ocr.py` (needs `easyocr`) and `paddle_vl.py` (needs `torch`, `torchvision` and `transformers>=5`). They are always listed but greyed out when their packages are missing, with a tooltip naming the install command: `SettingsPageUI.__init__` records what is missing in `unavailable_ocr_engines` using `importlib.util.find_spec` (never by importing the engine, which would drag the model stack into building the settings dialog), `ToolsPage._mark_unavailable_ocr_engines` disables those rows, and `SettingsPage.load_settings` falls back to Default rather than restoring a saved engine this machine cannot run.
  `openrouter_ocr.py` is a `GPTOCR` subclass rather than its own engine: OpenRouter speaks the OpenAI chat-completions protocol, so only three things differ — the endpoint, `MAX_TOKENS_PARAM` (OpenAI renamed it to `max_completion_tokens`, OpenRouter kept `max_tokens`), and where the model id comes from. It reads the api_key/model already stored for the OpenRouter *translator*, so one balance covers both, and it uses that id verbatim rather than through `MODEL_MAP` — a routing id like `google/gemini-2.5-flash-lite` is not one of this app's own model names. This is how a Gemini or GPT vision model is reached on OpenRouter credit without an account at the provider running it.
  `paddle_vl.py` is the odd one out architecturally: a 0.9B generative VLM that *writes out* what a crop says instead of decoding CTC logits, so it reads vertical CJK and stylised lettering with no special handling, at ~20s per block on a weak CPU. All 15 checkpoint files are sha256-pinned, and it loads through transformers' native `paddleocr_vl` support — passing `trust_remote_code=True` picks up the repo's own config class, which the built-in model class then rejects for a missing `text_config`.
- `modules/translation/factory.py` (`TranslationFactory`) — `base.py` defines `TranslationEngine`/`LLMTranslation`/`TraditionalTranslation`; `llm/base.py`'s `BaseLLMTranslation` is the shared LLM engine base (handles image encoding, system prompt via `PromptManager`, JSON-in/JSON-out translation contract). LLM engines (`llm/gpt.py`, `claude.py`, `gemini.py`, `deepseek.py`, `custom.py`, `openrouter.py`) subclass it; `openrouter.py` and `custom.py` both subclass `gpt.py`'s `GPTTranslation` since they're OpenAI-compatible APIs (note `GPTTranslation.MAX_TOKENS_PARAM` — OpenRouter needs `max_tokens` instead of `max_completion_tokens`). `deepl.py`, `microsoft.py`, `yandex.py` are non-LLM `TraditionalTranslation` engines. `user.py` (`UserTranslator`) is used instead of any of the above when the user is signed in to the managed/hosted account (checked via `app/account/auth/token_storage.get_token`).
- `modules/inpainting/` (`pipeline_config.py`'s `inpaint_map` dict, not a factory class) — `base.py` defines `InpaintModel`/`DiffusionInpaintModel`; implementations: `lama.py`, `aot.py`, `mi_gan.py`, and `smart_fill.py` (solid-fill cleaning: ranks progressively grown masks by border-colour uniformity and fills flat regions with the border's median colour, falling back to LaMa for non-uniform ones). The ranking itself lives in `mask_fitting.py`, ported from PanelCleaner.

`modules/inpainting/denoise.py` runs *after* whichever of those produced the cleaned page, at the one seam where patches get cut: `pipeline/inpainting.py`'s `_denoise_cleaned`, called by both `_get_regular_patches` (batch) and `get_inpainted_patches` (the manual Clean button, which also covers webtoon mode). It smooths a faded ring around the mask, because a low-quality JPEG carries ringing around every glyph and inpainting reconstructs the patch *from* those surroundings, leaving a halo where the text was. Structure follows PanelCleaner's denoiser (GPL attribution in the file header); the filter does not — PanelCleaner calls OpenCV's `fastNlMeansDenoising` and there is no OpenCV here, so it is a mahotas median instead.

Three things about it are load-bearing:

- **It works per mask region, not per page.** A page mask covers every bubble, so a single bounding box is very nearly the page — and a whole-page median costs 1.3s per page and 3s per webtoon strip, for a result that is then multiplied by a zero weight almost everywhere. `_mask_windows` cuts one padded window per region and merges overlapping ones (so a bubble's glyphs become one window, not forty). ~0.4s on a heavy page, and no longer scaling with page size.
- **Each window crops the *mask*, not the region.** A neighbouring bubble reaching into a window is denoised with it rather than clipped in half; windows are pairwise disjoint after merging, so nothing is written twice.
- **A region whose surroundings are a flat fill is skipped entirely**, bit for bit (`min_std`). This is what makes it safe to leave on by default: a clean PNG source loses nothing. `Settings > Tools > Tidy JPEG artefacts around cleaned text` turns it off, and a failure inside it is logged and swallowed — cleaning must not fail because tidying did.

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

A term's identity is `glossary.py`'s `term_key(source)` — NFC-normalised, stripped of zero-width and bidi marks, whitespace collapsed, case-folded — never the raw string. This is what `find`/`upsert`/`remove`/`keys` and `build_prompt`'s `match_only` filter all compare. Korean is why: Hangul has a composed and a decomposed encoding that render identically and compare unequal, so a raw-string glossary stores the same name twice and then fails to match either against OCR text using the other form. `deduplicate()` collapses a glossary saved before this existed (the "Merge Duplicates" button).

Glossary extraction has three entry points, all landing in `modules/utils/glossary_extractor.py`: the whole OCR log on demand, the current page on demand, and — with `auto_extract` on — each page as `OCRProcessor._log_ocr_texts` finishes it. That last one crosses a thread boundary, so it goes through `GlossaryPage.page_text_recognized` (a Qt signal, queued onto the GUI thread).

Two things govern how text reaches the model, and both exist for the same reason — a model given a handful of lines cannot tell a recurring character from a one-off shout:

- `extract_glossary_terms` **chunks rather than truncates**. `split_into_chunks` cuts at `CHUNK_LIMIT` (15k chars) on line boundaries and every chunk is sent. A `seen` set of `term_key`s spans the whole run: it seeds from the existing glossary, is fed back into each chunk's "do NOT repeat" list, and filters results — which is also what stops a model repeating itself *within* one response from producing duplicate entries. A chunk that fails is logged and skipped, never fatal.
- `GlossaryPage` **buffers pages** instead of extracting per page: `_queue_page` accumulates until `PAGE_BATCH_CHARS`, with `_page_flush_timer` (`PAGE_FLUSH_IDLE_MS`) catching the tail of a batch that stopped arriving. Explicit "Extract from This Page" passes `flush_now=True`. Only one extraction runs at a time — it is an LLM call, and one per page in parallel would rate-limit the account immediately.

`modules/utils/workspaces.py`'s `WorkspaceManager` is a third store of the same shape (one JSON per workspace under `<user data>/workspaces/`, plus a `config.json` naming the active one). A workspace bundles the per-series choices that otherwise have to be changed in four places at once: source folders, glossary profile, prompt preset, and language pair. `controller.py`'s `apply_workspace`/`capture_workspace_state` are the only places that read/write those UI widgets from a workspace; the picker lives at the top of the file tree panel.

### Pages sidebar

`app/ui/list_view.py`'s `PageListView` is the flat page strip and stays the ordering model — row index is the index into `main.image_files`. `app/ui/file_tree_panel.py`'s `FileTreePanel` shows the same pages grouped by source folder and is a pure view: `ImageController.refresh_file_tree()` rebuilds it from `image_files`/`image_states` and it does nothing while hidden. Its actions emit **file paths**; the flat list still emits base names, so every handler routes through `ImageController.resolve_page_paths()` (exact path wins, base name is the fallback) — two chapters of a series routinely share `002.png`.

Reopening a saved project materializes each page into its own `<temp>/unique_images/<id>/` directory, so a page's working path says nothing about the series layout. Both project loaders populate `main.path_originals` (working path → original path) and the tree groups and labels by that, while every path it emits stays the working one.

### Editor canvas & layers

`app/ui/canvas/image_viewer.py`'s `ImageViewer` (a `QGraphicsView`) is the editing surface. Scene items are typed by role — `ImageViewer._layer_of(item)` is the one place that classifies them: `self.photo` (the artwork), `MoveableRectItem` (detection boxes), `TextBlockItem` (rendered translation text), `QGraphicsPathItem` (segmentation/brush strokes), and `QGraphicsPixmapItem` with `.setData(0, hash)` (inpaint patches, added via `app/ui/commands/base.py`'s `PatchCommandBase`). All of them are added to the scene as **top-level items with no parent**; nothing is a child of `self.photo`, which is what lets the artwork be hidden without taking the boxes and text down with it.

The five layers split into `OUTPUT_LAYERS = ('text', 'patches', 'image')` — the three that make up the finished page, in stacking order, matching the three groups `app/controllers/psd_exporter.py` writes ("Editable Text" / "Inpaint Patches" / "Raw Image") — and `WORKING_LAYERS = ('boxes', 'strokes')`, which only exist while editing and never leave the canvas. `layer_visibility` covers all five; `layer_opacity` only the output three. `set_layer_visibility()` / `set_layer_opacity()` / `apply_layer_visibility()` drive them, and `app/ui/canvas/document_layers.py`'s `DocumentLayersPanel` is the UI, in a `Qt.Popup` frame hung off the Layers button in the editor header (built in `builders/workspace.py`, wired in `controller.py`'s `toggle_layers_popup`). Every code path that creates one of these item types must apply the current visibility state (see call sites in `image_viewer.py`, `app/ui/commands/base.py`, `app/ui/commands/brush.py`, `app/ui/canvas/drawing_manager.py`).

### Canvas tools

`viewer.current_tool` is a string (`box`, `brush`, `eraser`, `pan`, `wand`, `lasso`), set by `set_tool` and dispatched in `event_handler.py`. Buttons register themselves in `self.tool_buttons[name]` in `builders/workspace.py`, which is what makes them mutually exclusive — nothing else needs touching to add one.

Both selection tools end at `drawing_manager.add_region_stroke(path)`, which is why neither needed changes anywhere downstream.

The magic wand (`modules/utils/flood_select.py`, kept Qt-free so it can be tested as array maths) grows a region from the clicked pixel and hands back a mask. `drawing_manager.flood_fill_at` turns that into **an ordinary filled `QGraphicsPathItem` with a `BrushStrokeCommand`** — deliberately the same thing the brush produces, so mask generation, undo, both layer panels and project saving needed no changes at all.

Two decisions there are load-bearing and non-obvious:

- **Holes are closed in the mask, not by the path's fill rule.** `imk.find_contours` winds an enclosed gap opposite to its outer contour, so `WindingFill` leaves it empty. Clicking a bubble would then mask a ring *around* the lettering — the opposite of what cleaning it needs.
- **Only holes smaller than the region enclosing them are filled.** Every closed shape encloses something, so unbounded filling makes clicking a 4px bubble border select the whole bubble, and clicking a panel border select the whole panel.

The lasso is one tool told apart by what the mouse does: drag past `LASSO_DRAG_THRESHOLD` traces a loop freehand and commits on release, while clicks below it accumulate polygon vertices closed by double-click, Enter, or abandoned with Escape. The in-progress outline is a real scene item, so `has_drawn_elements` and `generate_mask_from_strokes` both skip `lasso_preview` explicitly — without that, an outline the user never finished still gets inpainted. `set_tool` cancels an unfinished outline when the user reaches for something else, and the viewer takes focus while the lasso is active so Enter and Escape reach `keyPressEvent` at all.

`app/ui/canvas/layer_panel.py`'s `LayerPanel` goes one level down, listing individual scene items with per-item show/lock/opacity (rebuilt from the scene on a debounced `QGraphicsScene.changed`, never mirrored into a second model). Both panels end up driving `setVisible`/`setOpacity` on the same items, so neither writes to Qt directly: an item's *own* state lives on the item under the data roles in `app/ui/canvas/layer_state.py`, and `apply_layer_visibility()` combines the two (visible only if both agree, opacity multiplied). Writing `item.setOpacity(...)` from a panel instead would be silently undone the next time any layer toggle moved.

All of this is purely a display concern — `get_image_array(include_patches=True)` always composes patches for OCR/translation/inpainting regardless of what's toggled on screen.

Undo/redo for canvas edits goes through `QUndoCommand` subclasses in `app/ui/commands/` pushed onto `controller.py`'s `QUndoStack`.

`app/ui/canvas/handles.py` owns the eight resize handles drawn on a selected `MoveableRectItem` or `TextBlockItem`. Both the drawn square and the (larger) grab area are sized in **screen** pixels and divided by `item_view_scale(item)`, so they stay the same size to the hand at any zoom — a fixed image-space margin is 5px at 25% zoom, which is exactly when boxes get dragged around. `paint_handles` draws them *inset* rather than centred on the border: they must stay inside `boundingRect()`, since that is the only region Qt repaints, and several call sites use `boundingRect()` for resize math and transform origins so it cannot be widened.

### Text effects: gradient fill and curved text

Both live on `TextBlockItem` (`app/ui/canvas/text_item.py`) next to the drop shadow, with the geometry split out Qt-free into `modules/rendering/text_effects.py` (`gradient_line`, `arc_placements`, `arc_bulge`). Adding either meant touching the same seven places the shadow already occupies: the item, `text/text_item_properties.py` (save/load), `image_viewer.py` and `save_renderer.py` (apply on create), `builders/workspace.py` (toolbar), `controllers/text.py` (widgets → item, item → widgets, plus `widgets_to_block`) and `controller.py` (signals).

- **The gradient is a document-wide char-format brush in `LogicalMode`, sized from `document().size()`.** Qt's `ObjectBoundingMode` anchors a text brush to each *glyph run*, so a two-line block gets the identical sweep twice instead of one across the whole thing. Because the axis is tied to the laid-out document, `_on_text_changed` has to rebuild it — and since merging a char format is itself a document edit, `_applying_gradient` guards the recursion. It is item-wide by design and replaces per-range colours while on.
- **Curved text paints outside its box, so `boundingRect()` grows and `text_rect()` does not.** `text_rect()` (the plain document rect) is what handles, resize math, `TextBlockState` and the saved width/height use; `boundingRect()` adds the arc's bulge symmetrically, purely so Qt repaints enough — symmetric because the transform origin and rotation pivot are its centre. Anything reading `boundingRect()` for *geometry* rather than for repaint is a bug.
- **Curved glyphs are baked into one `QPainterPath` and filled in a single pass.** Rotating the painter per glyph and calling `drawText` looks equivalent and is not: a gradient brush resolves against the painter's current transform, so every letter samples the gradient at its own origin and the whole word comes out one flat colour. The same path also gives the outline as a stroke instead of 16 displaced copies. The font comes from `document().defaultFont()`, never the item's `font_size` copy — the two are a step apart mid-resize, and reading the wrong one makes bending the text resize it.

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
