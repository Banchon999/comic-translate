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

---

## 2026-08-25 — PHASE 2: Skia canvas embedded in the PySide6 app

Skia now lays out **and** draws translated text inside the existing desktop
editor, selectable from `Settings > Tools > Draw text with Skia (experimental)`.
Qt remains the default.

### Pieces

| File | Role |
|---|---|
| `core/skia_render.py` | Qt-free rasteriser: spec in, RGBA array out |
| `core/text_engine.py` | The one switch — moves measurer **and** painter together |
| `app/ui/canvas/skia_paint.py` | Adapts a `TextBlockItem` to the spec, blits the result |
| `TextBlockItem.paint` | Takes the Skia path when the engine is Skia |

The item stays an ordinary `QGraphicsTextItem`. Selection, handles, editing,
undo, the layer panels and the project format are untouched — only the pixels
change. That is what kept the blast radius small enough to do safely.

### Features covered

Multi-stroke outline layers, gradient fill, drop shadow, kerning/letter
spacing, leading/line spacing, Thai diacritics, vertical CJK (columns
right-to-left). **Curved text stays on the Qt path** — the arc is baked into a
`QPainterPath` and has no Skia equivalent yet; `paint()` handles it before the
Skia branch is reached.

### Three bugs the visual check caught that the tests did not

Pixel counts and green tests said all three were fine. Rendering a sheet and
*looking at it* is what found them.

1. **Every line re-wrapped.** "Hello world" came out as "Hello" / "world".
   Laying a paragraph out at exactly its own measured width lets Skia's line
   breaker round against `LongestLine` and break the last word.
   `LAYOUT_SLACK_PX` fixes it.
2. **Letter-spaced text was clipped.** The surface was sized from a measurement
   that ignored letter spacing. Letter spacing moves glyphs, so it belongs in
   `TextStyle` — added there, applied by both measurers. `pyside_word_wrap`
   deliberately does *not* set it, so auto-fit behaves exactly as before.
3. **Line spacing applied to measurement but not painting.** A two-line block
   measured tall and drew tight at the top of its box. Horizontal text is now
   painted one line at a time, advancing by the spaced line height, because
   skia-python's `StrutStyle` cannot express a height multiplier.

A fourth was hidden by the design: `paint_item` swallowed every exception so a
bad block degrades instead of taking the editor down — which made a plain
`TypeError` (`int()` on a PySide6 enum, which is not an IntEnum) look exactly
like Skia working, while every render quietly came from Qt. It now logs once
per distinct reason, and `test_skia_engine_really_paints_differently` asserts
the two engines produce *different* pixels, so a silent fallback fails the
suite.

### Gates

- **Preview/export parity holds under both engines** — 13 cases × 2 engines,
  zero differing pixels.
- **Verified in the real app**, not just in tests: launched under `xvfb`,
  toggled the setting, and screenshotted a text block on an actual
  `ImageViewer`. Skia's output matches Qt's in position, line breaks, size and
  outline.
- **Graceful degradation verified** on an interpreter genuinely without
  skia-python: the engine list offers Qt only, the checkbox disables itself,
  and `set_engine("skia")` refuses with a clear reason rather than silently
  giving Qt.

### Memory

`MAX_SURFACE_PIXELS` (64 MP) refuses an oversized allocation rather than
attempting it, so handing a full webtoon strip to one surface raises
`SurfaceTooLarge` instead of an OOM kill. Text blocks are per-item surfaces and
nowhere near the cap. Long-strip *page* rasterisation still goes through the
existing chunked webtoon pipeline, unchanged.

pytest **420 passed**, ruff clean, headless gate 87/87 (hook, real no-Qt venv,
and `core/` on its own).

### Left for later, deliberately

- Curved text on the Qt path.
- Italic differs ~3.6% between engines (different faces resolved for a family).
- Vertical CJK metrics differ from Qt's by design; parity that matters
  (preview vs export) holds.
- Skia is opt-in. Making it the default is a decision for after it has seen
  real pages.

**Phase 3 (Flutter client) and Phase 4 (native hot spots) are out of scope and
were not started.**

### CI note on fonts

The GitHub runner carries **no Thai or CJK face**. Thai and Japanese strings
there fall back to a Latin one, so any script-specific assertion is trivially
true on CI and the numbers in the Phase 1 table above were measured on a
machine that does have those faces (Loma for Thai, WenQuanYi Zen Hei for CJK).

`tests/test_skia_measurer.py` detects this through
`FontMgr.matchFamilyStyleCharacter` and skips the affected test rather than
asserting a property of the runner's font set — which is what one of them did,
and it failed on CI while passing locally. The Qt/Skia agreement cases still
run everywhere: both engines fall back identically, so comparing them stays
meaningful even where the script does not render.

---

## 2026-08-25 — Pre-ship audit: five real defects found and Wave 1 fixed

The greenfield rebuild was cancelled; this branch is being closed out to a
shippable standard instead. A gap audit was run over the whole branch before
declaring it done, and it found **five correctness defects**, not just untested
paths. Two of them contradicted claims made in this very file.

### Corrections to what this document previously claimed

The Phase 2 entry above says selection, handles, **editing** and undo are
"untouched — only the pixels change", and lists **drop shadow** under "features
covered". Both were wrong:

- **Editing was broken under Skia.** The Skia branch in `TextBlockItem.paint`
  returned before `super().paint()`, and `super().paint()` is what draws Qt's
  caret and drag-selection band. Typing into a block with Skia on gave no
  cursor and no visible selection.
- **The drop shadow was drawn twice.** `apply_shadow()` attached a
  `QGraphicsDropShadowEffect` unconditionally while Skia was also baking a
  shadow into its raster.

Neither failed a test, and neither was caught by the preview/export parity
gate — parity compares Skia against Skia, so a defect present in both paths
passes. That is a real limit of that gate and worth remembering.

### The five defects

| # | Defect | Status |
|---|---|---|
| 1 | Caret and selection invisible while editing under Skia | **fixed** |
| 2 | Per-range rich text flattened to a single style | open |
| 3 | Per-range outlines lose their range and stroke the whole block | open |
| 4 | Drop shadow rendered twice | **fixed** |
| 5 | Text direction captured but never applied — RTL ignored | **fixed** |

Plus: curvature falls back to Qt (disclosed, but unguarded by any test), and
vertical CJK ignores letter spacing.

### Wave 1 — fixed and verified

- **Editing stays on the Qt path.** The Skia branch is now guarded by
  `not self.editing_mode`; a block flips back to Skia when focus leaves it.
- **Shadow.** `apply_shadow()` clears the Qt effect when Skia is painting, and
  `controller.apply_text_engine` re-applies it across every text item on an
  engine switch — without that, a page already on screen keeps whichever
  treatment it was built with.
- **RTL.** skia-python 144 exposes *no* direction setter: `ParagraphStyle` has
  only `setTextAlign`/`setStrutStyle`/`setTextStyle`, and there is no
  `TextDirection` enum at all. Verified by inspection, not assumed. The
  direction is therefore carried in the text itself, wrapped in Unicode
  directional isolates (`core/skia_text.apply_base_direction`), applied
  identically by the measurer and the renderer so they cannot diverge.
  Proven: rendering `"abc مرحبا"` LTR vs RTL now differs by 2,998 px under
  Skia. It differed by zero before — the string opens with a strong LTR
  character, so ICU's implicit guess could never have flipped it.
- **Shadow blur fidelity.** Skia's shadow measured 2.3–2.6× more diffuse than
  Qt's at the same setting, which reads as a muddy shadow washing over the
  glyph. The textbook `radius/2` sigma is wrong here because Qt's blur radius
  is not a standard deviation. `SHADOW_BLUR_TO_SIGMA = 0.15` was fitted by
  measuring soft-pixel counts against Qt at radii 4/10/20; it lands within 15%
  across that range.

`build_text_item_state()` gained the effect fields (`shadow_*`, `gradient_*`,
`letter_spacing`, `curvature`). Their absence is *why* the doubled shadow was
invisible: the test harness could not construct a block that had one.

pytest 420 passed, ruff clean.

**Next:** defects 2 and 3 (per-range rich text and per-range outlines), then
curved text on Skia, then the full visual matrix and a real-page run.
