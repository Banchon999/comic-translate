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
