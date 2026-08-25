# Text canvas migration — running status

Working log for the migration described in `text-canvas-migration.md`. Append a
dated entry per logical step; read this file first when picking the work back
up. Newest entries at the bottom.

Scope for this run: **Phase 0 and Phase 1 and Phase 2 only.** Do not start
Phase 3 (Flutter client) or Phase 4 (native hot spots).

## Environment notes

The dev container starts with no Python dependencies and no Qt system
libraries. To get a working environment:

```bash
pip install --ignore-installed PyJWT -r requirements-dev.txt
apt-get update -q && apt-get install -y libegl1 libgl1 libxkbcommon0 \
    libdbus-1-3 libfontconfig1 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 \
    libxcb-shape0 libxcb-xinerama0 libxrender1 libxi6
```

`--ignore-installed PyJWT` is needed because the base image has a Debian-packaged
PyJWT with no RECORD file, which pip refuses to uninstall. Without the `libegl1`
group, every Qt test module fails collection with
`ImportError: libEGL.so.1: cannot open shared object file`.

Local interpreter is 3.11; CI runs 3.12 and 3.14.

## The Phase 0 gate

`python -c "import pipeline"` is **not** a usable gate: `pipeline/__init__.py`
is 19 bytes and the import succeeds today, while every module inside it needs
Qt. `scripts/check_headless.py` gates the real surface — every module under
`pipeline/` and `modules/`, imported in a child interpreter with a meta-path
hook that makes PySide6 unimportable.

The child process matters. An in-process blocker passes falsely: the module
walk has already imported Qt, so `from PySide6.QtCore import Qt` hits
`sys.modules` and succeeds. The first version of this script reported a clean
gate over 87 modules that had not been tested at all.

---

## 2026-08-25 — Baseline

- Environment built, `pytest` green at **319 passed**, `ruff check .` clean.
- Added `scripts/check_headless.py`.
- **Gate baseline: 67 of 87 modules fail** to import without Qt.

Qt/`app` coupling inventory in `pipeline/` and `modules/`:

| Site | Nature |
|---|---|
| `modules/utils/language_utils.py` | `Qt.LayoutDirection` return value |
| `modules/utils/image_utils.py` | `QColor` for colour comparison |
| `modules/utils/common_utils.py` | `QApplication`/`QProcess`, already function-local |
| `modules/utils/pipeline_config.py` | `QCoreApplication.translate`, `Messages`, `SettingsPage` |
| `modules/ocr/{factory,user_ocr,gemini_ocr}.py` | `SettingsPage`, `app.account.*` |
| `modules/translation/{factory,user}.py` | `SettingsPage`, `app.account.*` |
| `modules/rendering/render.py` | `QFont`/`QTextDocument`/`QApplication` + `VerticalTextDocumentLayout` — the Phase 1 seam |
| `pipeline/inpainting.py` | `QImage`/`QPainter`/`QPen`/`QBrush` mask rasterisation |
| `pipeline/block_detection.py` | `ReplaceDetectedBlocksCommand` (a `QUndoCommand`) |
| `pipeline/batch_processor.py` | `TextItemProperties`, `OutlineInfo/Type`, `QColor`, `Messages` |
| `pipeline/webtoon_batch/render.py` | `ImageSaveRenderer`, `TextItemProperties`, `QColor` |
| `pipeline/webtoon_batch/chunk.py` | `QCoreApplication.translate`, `Messages` |

**Next:** introduce `core/` (Qt-free enums, colour, i18n shim, notifier
protocol) and convert the leaf modules.

---

## 2026-08-25 — PHASE 0 GATE PASSED

`python scripts/check_headless.py` → **87 of 87 modules import without Qt**,
both under the import-hook blocker and in a real venv built from
`requirements.txt` with PySide6 stripped out. pytest **347 passed**, ruff clean.

### What moved, and why

| Was | Now | Note |
|---|---|---|
| `QColor` in `image_utils` | `core/color.py` | `get_smart_text_color` returns a hex string; the 4 call sites wrap it |
| `Qt.LayoutDirection` in `language_utils` | `core/enums.py` | `IntEnum` with Qt's values |
| `QCoreApplication.translate` in the pipeline | `core/i18n.py` | defers the Qt import to call time |
| `Messages.get_server_error_text` | `core/messages.py` | pure text builders; `Messages` delegates |
| `validate_ocr`/`validate_translator`/`font_selected` | `app/validation.py` | they open dialogs — UI, not pipeline |
| saved-stroke rasteriser | `app/ui/canvas/stroke_mask.py` | consumes `QPainterPath`; genuinely Qt data |
| `load_box_coords`, undo push | `app/controllers/rect_item.py` | drove the viewer and the undo stack |
| `QFont`/`QTextDocument` in `render.py` | `core/text_measure.py` + `app/ui/canvas/text/qt_measurer.py` | **the Phase 1 seam** |
| `TextItemProperties`/`OutlineInfo` in the pipeline | `core/text_style.py` | the pipeline only ever needed the state dict |
| `app.path_materialization` | `core/path_materialization.py` | project-blob half imported lazily |
| `token_storage.get_token` | `core/credentials.py` | provider seam; default asks the app, else None |

### Things worth knowing

- **`python -c "import pipeline"` is not the gate** and never was — see the note
  above. Use `scripts/check_headless.py`.
- **A latent bug was introduced and fixed inside Phase 0.** `core.enums.LayoutDirection`
  has Qt's integer values but is not Qt's type, and PySide6 rejects it with a
  `TypeError`. That would have crashed on selecting Arabic, Hebrew or Persian as
  the target language. `app/ui/qt_values.py` converts at the boundary and
  `tests/test_qt_values.py` pins it, including an assertion that Qt still
  rejects the raw enum.
- **The measurement seam is behaviour-preserving**, checked against the previous
  implementation over 256 combinations of text (incl. Japanese, Thai, Arabic),
  ROI, orientation, no-space-language and outline width: zero differences.
- **Import-clean is not the same as runtime-decoupled.** `pipeline/` still makes
  ~185 `main_page.*` accesses and holds Qt values it was handed (e.g.
  `button_to_alignment[...]` is a real `Qt.AlignmentFlag` that goes straight
  into the state dict). The gate says the pipeline can be *imported* and its
  pure logic tested headlessly; it does not yet say the handlers can *run*
  headlessly. That remains Phase 0 work if a sidecar is ever built.
- Three deliberate function-local imports remain, each where the callee is
  genuinely Qt data: `stroke_mask`, `ImageSaveRenderer` in the webtoon final
  render, and the project-blob branch of path materialization.

### CI

`.github/workflows/test.yml` gains a `headless` job that installs
`requirements.txt` minus PySide6, asserts Qt really is absent, and runs the
gate. `tests/test_headless_pipeline.py` runs the same check inside the normal
suite via the import hook, so regressions fail fast.

**Next:** Phase 1 — `skia-python` behind the `TextMeasurer` seam.

---

## 2026-08-25 — Phase 1: Skia measurer + parity harness

`core/skia_text.py` implements the seam with `skia-python` 144.0 (Skia m144),
Qt-free so a headless process can use it. Added to `requirements.txt` but
guarded: `is_available()` reports the import and the app keeps the Qt measurer
when it is missing, the way the optional OCR engines degrade.

### Two systematic offsets, each worth ~25-30%

A naive Skia measurer came out **28-31% narrow and 35-39% short** on every
string. Two causes, both corrected and both pinned by tests:

1. **Points vs pixels.** `font_size` means points everywhere here, because
   `QFont(family, size)` takes points. Skia's `setFontSize` takes pixels. At the
   96 DPI Qt reports, 24pt is 32px — a flat 25%.
2. **Document margin.** `QTextDocument` carries a 4px margin, so every Qt
   measurement in this codebase includes +8 on each axis.

With both applied, against the Qt measurer at 24pt:

| case | Δwidth | Δheight |
|---|---|---|
| Latin, 1 and 2 lines, caps, bold | **0.00px** | −2.2% |
| Thai | **0.00px** | 0.0% |
| Japanese | **0.00px** | +4.1% |
| Arabic | −1.00px | −2.2% |
| *italic* | *−7.00px (−3.6%)* | −2.2% |
| *vertical CJK* | *−5.2%* | *+17.2%* |

Width is exact for upright text in every script tested. Height carries Qt's
integer line rounding. **Italic is a genuine divergence** — the two resolve
different italic faces for the same family — and has its own tolerance so a
regression elsewhere still fails. **Vertical CJK diverges by design**: Qt uses
`VerticalTextDocumentLayout`, Skia has no vertical writing mode, and the two
stack glyphs by different rules.

A claim in the first draft was wrong and is corrected in the code: Thai does not
measure taller because a string carries stacked marks. `กาน` and `สวัสดีครับ`
get the identical box (59px at 24pt, against Latin's 46px). It is the *face*
that reserves the room, used or not — which is still exactly why the height is
read from the paragraph rather than assumed.

### The Phase 1 gate: preview/export parity

`tests/test_render_parity.py` renders the same state through
`ImageViewer.add_text_item` (what the canvas shows) and
`ImageSaveRenderer.add_state_to_image` (what every raster export is made from)
and compares pixels. **13 cases pass at zero differing pixels** — plain, bold,
italic, no/thick outline, rotated, scaled, right-aligned, wide spacing, Thai,
Japanese, Arabic RTL, vertical CJK, single character.

Two guard tests keep the harness honest: one asserts it *can* see a difference,
one asserts the export is not simply blank. Without those a parity test passes
by comparing nothing.

PNG, WebP and the PSD merged-image section all come from `render_to_image`, so
this covers all three raster formats; PSD *text layers* are separate and remain
covered by `test_psd_export.py`.

### Deliberately not switched over

The Skia measurer is **not** the default. Measuring with Skia while
`TextBlockItem` still paints with Qt is precisely the preview/export divergence
this whole programme exists to avoid. Phase 2 moves the painter, and the
measurer switches with it.

pytest **381 passed**, ruff clean, headless gate 87/87.

**Next:** Phase 2 — Skia-backed canvas item, then switch the measurer with it.
