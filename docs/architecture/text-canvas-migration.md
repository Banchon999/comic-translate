# Text canvas architecture: evaluation and migration roadmap

Status: proposal · Scope: the three options tabled for fixing the text canvas
bottleneck · Companion files: `proto/comictranslate/v1/*.proto`,
`ffi/comic_core.h`

## Recommendation

**Do not start with either Option 1 or Option 2. Start with the option the
table omits: keep PySide6 and put Skia behind the text canvas only.** Then,
if that proves insufficient, Option 1 — re-scoped from 1–2 months to 4–6, and
sequenced behind a decoupling phase that Option 2 would need anyway.

The reasoning is in three parts: the table's scoring does not survive contact
with this repository; the canvas is not where the coupling lives; and the one
premise nobody has tested is whether Skia actually fixes our text problems.

## 1. What the table gets wrong about this codebase

### "Pipeline Logic Reuse: 🟢 100% Code Reuse" is not true today

Option 1 is scored on the assumption that `pipeline/` is a headless library
that only needs a transport in front of it. Measured:

| Coupling | Count |
|---|---|
| `main_page.*` accesses inside `pipeline/` | **185**, across 25 distinct attributes |
| `pipeline/` modules importing from `app/` | 5 files, 11 import sites |
| `modules/` files importing PySide6 | 6, including `modules/rendering/render.py` |

`pipeline/` does not take inputs and return outputs. It reads `main_page.blk_list`,
`main_page.image_viewer`, `main_page.settings_page`, `main_page.image_states`,
`main_page.s_combo`; it imports `ReplaceDetectedBlocksCommand` (a `QUndoCommand`),
`TextBlockItem`, `TextItemProperties` and `ImageSaveRenderer`; it emits results by
mutating the window and pushing onto its undo stack. Serving that over gRPC
requires extracting a Qt-free core first. That extraction is the bulk of
Option 1's real cost, and the table charges it nothing.

### Qt is the renderer of record, not just the canvas

Two facts make "swap the canvas" a much larger change than it sounds:

- `modules/rendering/render.py`'s `pyside_word_wrap` — the auto-fit that
  chooses each block's font size and line breaks — binary-searches over
  `QFont`/`QTextDocument` metrics. Layout is decided by Qt, in the pipeline,
  not in the UI.
- `app/ui/canvas/save_renderer.py`'s `ImageSaveRenderer` produces the
  **exported** page by rebuilding `TextBlockItem`s into an offscreen
  `QGraphicsScene` and rasterising it. The exported PNG is Qt output.

So a Skia canvas paired with a Qt shaper gives two different text engines for
the same page: same font, same size, different line breaks. **The preview
stops matching the export.** This risk is absent from the table and it is the
dominant technical risk in any option that moves the canvas without moving
measurement and rasterisation with it. `service.proto`'s `TextLayoutService`
exists to name the two coherent resolutions; there is no third.

### The project file format is Qt-serialised

Rich text is persisted as `QTextDocument.toHtml()` — see `TextBlockItem.is_html`,
`controllers/text.py`'s `_last_item_html`, and the `.ctpr` writer. That is a
Qt-version-specific HTML dialect no other engine parses, sitting inside every
saved project. Any non-Qt client needs either a parser for it or a format
migration.

There is good news here: `app/controllers/psd_exporter.py` already walks a
`QTextDocument` into explicit `TextStyleRun` / `ParagraphStyleRun` /
`OutlineStyleSpan` values, because PSD text layers need exactly that. **The
conversion away from Qt HTML is already half-written**; it is in the wrong
module. Promoting `_extract_text_runs` into the shared core is the single
highest-leverage refactor in this whole programme, and it pays off even if the
migration is cancelled.

### "Option 3: Pure C++ (Qt 6 + Skia)" is two different things

Qt 6 does not render text with Skia. `QPainter` uses Qt's own raster/RHI
engine over HarfBuzz plus FreeType/DirectWrite/CoreText. "Qt 6 + Skia"
therefore means *you* embed Skia in a Qt surface and own that integration —
which is a thing you can do **right now, from Python**, without rewriting
anything. That is the missing option.

### The effort columns are the wrong order of magnitude

78,300 lines of Python: `app/` 43,060 (of which `app/ui` 30,816),
`modules/` 25,094, `pipeline/` 4,545, `imkit/` 1,350, `tests/` 2,538. Option 2's
"Requires Full Port" covers `modules/` (25k lines, 11 OCR engines, 8
translation engines, ONNX orchestration, glossary and prompt stores) and
Option 3 adds `app/` on top. `runpod/README.md` already records the team's own
estimate for the UI alone — "rewriting the editor canvas for a browser would
mean rewriting the ~30,000 lines under `app/ui`" — and that number checks out.
Option 2 at 3–5 months and Option 3 at 6+ are not port estimates; they are
rewrite estimates with a port's budget.

## 2. Option 0 — Skia inside the existing app

All three tabled options score text canvas flexibility identically: 🟢 High
(Skia Native). The table is therefore saying the canvas outcome does not
distinguish them. If Skia is what unlocks the canvas, Skia can be had without
changing language, process model or UI toolkit:

- `skia-python` ships Skia m144 with cp312 wheels for manylinux x86_64/aarch64,
  macOS x86_64/arm64 and Windows amd64/arm64 (144.0.post2, March 2026) — the
  full matrix this project builds for. BSD-3-Clause.
- A `QGraphicsItem` subclass that paints a Skia surface into a `QImage` and
  blits it in `paint()` replaces `TextBlockItem`'s painting without touching
  its selection, handle, undo or layer wiring.
- `pyside_word_wrap` gains a `TextMeasurer` seam with two implementations —
  Qt today, Skia next — so measurement and painting can be moved to the same
  engine together, in one place, and compared against each other on a golden
  corpus before either is trusted.

This is weeks, not months. It answers the question the whole programme rests
on — *does Skia give us the text control we actually want?* — before anyone
commits to a transport, a language or a rewrite. If the answer is no, Options
1–3 were all going to fail for the same reason and we found out cheaply. If
the answer is yes, the `TextMeasurer` seam and the run-based text model are
precisely what Option 1 needs next.

Two specific things must be proven in this spike, because they are what a
naive Skia port loses:

- **Vertical CJK.** `app/ui/canvas/text/vertical_layout.py` is 932 lines of
  custom `QAbstractTextDocumentLayout`. Skia gives glyph runs, not a vertical
  writing mode; this becomes manual per-glyph placement.
- **Thai.** `render.py`'s cluster-safe break path plus optional `pythainlp`
  word segmentation has no equivalent in a stock shaper.

## 3. Phased roadmap

Each phase ends at a gate that is independently valuable, so the programme can
be stopped at any phase boundary without stranding work.

### Phase 0 — Headless core (4–6 weeks)

Break `pipeline/` → `app/`. Concretely:

- Introduce `core/` holding Qt-free values: `PageState`, `TextStyle` (from
  `TextItemProperties`, with `QColor` → hex and `Qt.AlignmentFlag` → enum),
  and the run-based rich-text model lifted out of `psd_exporter.py`.
- Replace the 185 `main_page.*` reads with an explicit `PipelineContext`
  passed in, and the undo-command writes with returned result objects.
- Replace Qt signals with a `ProgressSink` protocol; the Qt app supplies an
  adapter that re-emits the existing signals, so `controller.py` is unchanged.

**Gate:** CI job that uninstalls PySide6 and runs `python -c "import pipeline"`
plus the pipeline tests. Nothing else moves until that is green.

**Standalone value:** the pipeline becomes unit-testable without Qt, which the
existing suite cannot do today, and the hosted backend (`user_ocr.py`,
`user.py`, `runpod/`) gains a real server-side entry point.

### Phase 1 — One text engine (4–6 weeks, overlaps Phase 0)

Option 0 above. Ends with measurement, layout and rasterisation behind one
interface with a Qt implementation and a Skia implementation, golden-image
tests comparing them, and the canvas on whichever wins.

**Gate:** a corpus of pages renders byte-identically through the old path and
the new one, or the differences are reviewed and accepted. Vertical CJK and
Thai are in the corpus.

### Phase 2 — Sidecar transport (6–8 weeks)

Stand up `proto/comictranslate/v1/` against the Phase 0 core, over a Unix
domain socket / named pipe into a child process. Point the *existing Qt app*
at it first — same UI, pipeline out of process. This validates the contract,
the shared-memory page transfer and the streaming progress model against a
client we already trust.

**Gate:** the Qt app runs entirely through the sidecar, with the in-process
path still selectable by flag, and batch throughput within 10% of today's.

### Phase 3 — Flutter client (8–12 weeks)

Only now is a second client worth writing, and only against a contract two
implementations have already exercised. Ship behind a flag; the Qt app stays
shippable throughout.

Budget honestly for what is *not* the canvas: 11 settings pages (3,811 lines),
the layer and document-layer panels, webtoon lazy loading, project save/load,
PSD import/export, and 10 localisation catalogues whose `@fallback` machinery
(`scripts/build_translations.py`) is Qt-specific and does not carry over.

### Phase 4 — Native hot spots, if profiling asks for it (open-ended)

This is where Option 2 belongs: not as a big-bang port, but as `comic_core.h`
FFI-ising the specific kernels that profile hot — mask fitting, the denoise
windowing, flood select. Each is a few hundred lines of array maths with a
clean interface and no Qt.

**Do not port `imkit` to OpenCV as part of this.** `CLAUDE.md` records that
`imkit`'s semantics are deliberately not cv2's (`erode`/`dilate` binarise their
output), and a C++ port that reaches for OpenCV silently changes mask
behaviour everywhere.

## 4. The interface contract

Both designs are written out in full:

- **gRPC** — `proto/comictranslate/v1/{common,textblock,textstyle,service}.proto`
- **C FFI** — `ffi/comic_core.h`

Four decisions in there are worth surfacing here because they are easy to get
wrong and expensive to change later:

1. **Page rasters never travel as a `bytes` field.** A 800×20000 webtoon strip
   is ~48 MB against gRPC's 4 MB default. `ImageRef` is a oneof over shared
   memory (the intended path for a same-host sidecar), inline bytes (small
   crops only) and a file path (the fallback, and what the pipeline already
   materialises).
2. **Text offsets are UTF-16 code units.** Qt, Dart and PSD all index UTF-16;
   Python does not. `psd_exporter.py`'s `_u16_len` exists for this reason
   already. Any other choice adds a conversion at three of four boundaries.
3. **`TextBlock` gets an `id`.** It has no identity today — call sites match
   blocks by list position, which breaks the first time a client reorders or
   the server re-detects.
4. **`TextItem.width`/`height` are the laid-out text rect, never the painted
   bounds.** Curved text paints outside its box; `text_item.py` keeps
   `text_rect()` and `boundingRect()` apart precisely so that geometry readers
   do not pick up the arc bulge.

A socket is also the right transport over TCP loopback: the pipeline holds the
user's API keys and glossaries, and a loopback port is reachable by every other
process on the machine.

## 5. Licensing and dependency risk

### The repository is GPL-3.0-or-later, and that governs the split

Copyleft reaches the whole combined work because of PanelCleaner-derived
`mask_fitting.py` and comic-text-detector-derived `text_mask_refine.py` (see
`NOTICE.md`; `denoise.py`, `smart_fill.py` and `text_seg_onnx.py` carry related
notices).

- **Option 2 / FFI is unambiguously one program.** A Flutter UI linked to a
  GPLv3 core through FFI is a single work: the Dart code is GPLv3 too. There
  is no configuration of Option 2 that yields a permissively-licensed client.
- **Option 1 / gRPC is not the escape hatch it looks like.** The FSF's position
  is that separate processes talking over sockets are *usually* separate
  works — but that the intimacy of the communication matters, and exchanging
  complex internal data structures can make them one program. This contract
  passes `TextBlock` internals, style runs and rendering state. That is the
  intimate end of the spectrum. Anyone hoping a gRPC boundary permits a
  proprietary client should get counsel before building on it.
- **The clean answer is to license the Flutter client GPLv3 as well.** Then no
  part of this question needs answering, and nothing above is a constraint.
- **Porting `mask_fitting.py` to C++ produces a GPLv3 derivative of
  PanelCleaner.** The attribution header travels with the port. Same for any
  C++ rewrite of `text_mask_refine.py`.

### The Apache-2.0 boundary narrows as this proceeds

This is a fork of Apache-2.0 upstream comic-translate. Phases 0–3 rewrite
`pipeline/` and much of `app/` — the parts most likely to be shared upstream.
Not a legal blocker, but a strategic cost worth stating once: **the further
this programme goes, the less of the fork can ever flow back.**

### Dependencies — verified against PyPI metadata

| Package | Licence | Verdict |
|---|---|---|
| mahotas | MIT | Fine. (Was GPL before 1.4.0; we pin ≥1.4.18.) |
| psd-tools | MIT | Fine, and dev-only. |
| PhotoshopAPI | BSD-3-Clause | Fine — and it is a C++ library with Python bindings, so a native core keeps PSD export rather than losing it. |
| pyclipper | MIT | Fine. |
| shapely | BSD-3-Clause | Fine. |
| pythainlp | Apache-2.0 | Fine. |
| pypdfium2 | BSD-3-Clause / Apache-2.0 | Fine. |
| rarfile | ISC | Fine. |
| **py7zr** | **LGPL-2.1-or-later** | Fine **because of "or-later"** — upgradeable to LGPL-3, which is GPL-3 compatible. LGPL-2.1-*only* would not be. Do not let a pin drift to an only-variant fork. |
| PySide6-Essentials | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | Fine today. Note the direction of travel below. |
| Flutter / Skia / skia-python | BSD-3-Clause | Fine. |
| gRPC (Apache-2.0), protobuf (BSD-3) | — | Fine. |

Nothing in the dependency set blocks any option.

**One upside worth naming:** PyInstaller-frozen PySide6 carries an LGPL §4
obligation to permit relinking against a modified Qt. Moving the UI to
Flutter/Skia (BSD-3) removes that obligation rather than adding one. It is a
small argument *for* the migration, and the only licensing argument in its
favour.

**`imkit` is in-repo project code**, not a dependency — GPLv3 as part of the
combined work, resting on mahotas (MIT), Pillow and numpy. Its risk in this
migration is behavioural, not legal, and is covered under Phase 4.

## 6. What would change this recommendation

- **Phase 1 shows Skia does not fix the canvas.** Then the canvas problem is
  not a rasteriser problem and none of the three options address it. Re-open
  the diagnosis.
- **The bottleneck turns out to be interaction latency, not text fidelity.**
  Profile first: `QGraphicsScene` with hundreds of items, the debounced
  `scene.changed` rebuild in `layer_panel.py`, and webtoon lazy loading are all
  plausible culprits that no amount of Skia touches.
- **The real goal is a mobile or web client rather than a better canvas.**
  Then the canvas framing is a proxy, Option 1's process split becomes the
  point rather than a means, and the roadmap should be re-cut around it.

The last one is worth settling before Phase 2 starts, because it changes what
Phase 2 is for.
