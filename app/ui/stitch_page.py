"""The Stitch page: join webtoon slices and re-cut them where the page is empty.

The maths lives Qt-free in ``modules/utils/stitch.py``; this is the page
around it — a source list (files, a folder, or the open project's pages), the
settings, a preview of where the cuts fall, and the write. Loading, planning
and writing run on the task runner's worker; the only thing that crosses back
is this page's own ``_progress`` signal, so no QObject is ever created or
destroyed off the GUI thread.
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from core import theme_tokens
from modules.utils import stitch
from modules.utils.archives import is_image_file, natural_sort_key

from .dayu_widgets.progress_bar import MProgressBar
from .dayu_widgets.push_button import MPushButton
from .dayu_widgets.qt import MIcon
from .dayu_widgets.spin_box import MSpinBox

SETTINGS_GROUP = "stitch"
THUMB = QtCore.QSize(44, 60)
PREVIEW_PAGE_HEIGHT = 1200  # pixels per preview page; the canvas scales them to fit
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.bmp *.avif)"
SOURCE_ROLE = QtCore.Qt.ItemDataRole.UserRole


@dataclass(frozen=True)
class Source:
    """One slice: a file on disk, or a page of the open project."""

    path: str
    from_project: bool = False


def _read_source(item) -> np.ndarray:
    """Worker side: a path to read, or an array already resolved on the GUI thread."""
    if isinstance(item, np.ndarray):
        return item
    import imkit as imk

    from app.path_materialization import ensure_path_materialized

    ensure_path_materialized(item)
    return imk.read_image(item)


def _plan_job(items, settings: stitch.StitchSettings):
    slices = [_read_source(i) for i in items]
    stitch_plan = stitch.plan(slices, settings)
    pages, _scale = stitch.preview_pages(stitch_plan, PREVIEW_PAGE_HEIGHT)
    return stitch_plan, pages


class PreviewCanvas(QtWidgets.QWidget):
    """The output pages side by side, scaled to the visible height.

    Dashed lines mark where the *source* slices used to end, which is the
    point of the tool: a bubble that sat on one of those lines is now whole.
    A forced cut (no empty row nearby) is drawn as a bar under its page.
    """

    GAP = 18
    LABEL_H = 22

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmaps: list[QtGui.QPixmap] = []
        self._plan: stitch.StitchPlan | None = None
        self._colours = {"text": "#888888", "edge": "#888888", "warn": "#ffaa00", "line": "#444444"}
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)

    def set_pages(self, stitch_plan: stitch.StitchPlan | None, pages) -> None:
        self._plan = stitch_plan
        self._pixmaps = []
        for arr in pages or []:
            h, w = arr.shape[:2]
            image = QtGui.QImage(arr.data, w, h, 3 * w, QtGui.QImage.Format.Format_RGB888).copy()
            self._pixmaps.append(QtGui.QPixmap.fromImage(image))
        self._relayout()
        self.update()

    def has_pages(self) -> bool:
        return bool(self._pixmaps)

    def set_colours(self, **colours) -> None:
        self._colours.update(colours)
        self.update()

    def _scale(self) -> float:
        """Strip rows → canvas pixels: the tallest page fills the height."""
        if not self._plan or not self._plan.page_heights:
            return 1.0
        avail = max(60, self.height() - self.LABEL_H - 12)
        return avail / max(self._plan.page_heights)

    def _relayout(self):
        if not self._plan:
            self.setMinimumWidth(0)
            return
        scale = self._scale()
        width = self._plan.width * scale
        self.setMinimumWidth(int(len(self._pixmaps) * (width + self.GAP) + self.GAP))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def paintEvent(self, event):
        if not self._plan or not self._pixmaps:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        scale = self._scale()
        page_w = self._plan.width * scale
        edges = stitch.slice_edges(self._plan)
        forced = set(self._plan.forced)
        bounds = self._plan.bounds
        x = float(self.GAP)
        font = painter.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1))
        painter.setFont(font)
        for i, pix in enumerate(self._pixmaps):
            top, bottom = bounds[i], bounds[i + 1]
            rect = QtCore.QRectF(x, 6, page_w, (bottom - top) * scale)
            painter.drawPixmap(rect, pix, QtCore.QRectF(pix.rect()))
            painter.setPen(QtGui.QPen(QtGui.QColor(self._colours["line"]), 1))
            painter.drawRect(rect)
            pen = QtGui.QPen(QtGui.QColor(self._colours["edge"]), 1.5, QtCore.Qt.PenStyle.DashLine)
            painter.setPen(pen)
            for edge in edges:
                if top < edge < bottom:
                    y = rect.top() + (edge - top) * scale
                    painter.drawLine(QtCore.QPointF(rect.left() - 4, y), QtCore.QPointF(rect.right() + 4, y))
            if bottom in forced:
                painter.fillRect(QtCore.QRectF(rect.left(), rect.bottom() - 1, page_w, 4), QtGui.QColor(self._colours["warn"]))
            painter.setPen(QtGui.QColor(self._colours["text"]))
            label = f"{i + 1}  ·  {bottom - top}px"
            painter.drawText(QtCore.QRectF(x - self.GAP / 2, rect.bottom() + 4, page_w + self.GAP, self.LABEL_H),
                             QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignTop, label)
            x += page_w + self.GAP
        painter.end()


class StitchPage(QtWidgets.QWidget):
    """Join slices → one strip → pages of about ``target_height`` cut at empty rows."""

    open_pages = QtCore.Signal(list)      # written page paths, to open as a new project
    _progress = QtCore.Signal(int, int)   # emitted from the worker; queued onto the GUI thread

    def __init__(self, main=None, parent=None):
        super().__init__(parent)
        self.setObjectName("toonStitchPage")
        self.main = main
        self._plan: stitch.StitchPlan | None = None
        self._plan_key = None
        self._preview: list[np.ndarray] | None = None
        self._written: list[str] = []
        self._busy = False
        self._is_dark = True
        self._build()
        self._load_settings()
        self._progress.connect(self._on_progress)
        self._update_state()

    # ------------------------------------------------------------------ layout

    def _build(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 18)
        outer.setSpacing(14)

        self.heading = QtWidgets.QLabel(self.tr("Stitch"))
        self.heading.setObjectName("toonStitchHeading")
        self.subtitle = QtWidgets.QLabel(self.tr(
            "Join webtoon slices into one strip, then cut it into even pages at empty rows "
            "so no bubble is split across two pages."
        ))
        self.subtitle.setObjectName("toonStitchSubtitle")
        self.subtitle.setWordWrap(True)
        outer.addWidget(self.heading)
        outer.addWidget(self.subtitle)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(16)
        outer.addLayout(body, 1)

        body.addWidget(self._build_sources(), 0)
        body.addWidget(self._build_preview(), 1)
        body.addWidget(self._build_settings(), 0)

    def _card(self, title: str) -> tuple[QtWidgets.QFrame, QtWidgets.QVBoxLayout]:
        card = QtWidgets.QFrame()
        card.setObjectName("toonStitchCard")
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.setSpacing(10)
        label = QtWidgets.QLabel(title)
        label.setObjectName("toonSectionLabel")
        lay.addWidget(label)
        return card, lay

    def _build_sources(self):
        card, lay = self._card(self.tr("Slices"))
        card.setFixedWidth(300)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        self.add_files_button = MPushButton(self.tr("Add Images"))
        self.add_files_button.setIcon(MIcon("ion--image-outline.svg"))
        self.add_folder_button = MPushButton(self.tr("Add Folder"))
        self.add_folder_button.setIcon(MIcon("folder-open.svg"))
        row.addWidget(self.add_files_button)
        row.addWidget(self.add_folder_button)
        lay.addLayout(row)

        self.from_project_button = MPushButton(self.tr("Use Pages of the Open Project"))
        self.from_project_button.setIcon(MIcon("nav-project.svg"))
        lay.addWidget(self.from_project_button)

        self.source_list = QtWidgets.QListWidget()
        self.source_list.setObjectName("toonStitchSources")
        self.source_list.setIconSize(THUMB)
        self.source_list.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.InternalMove)
        self.source_list.setDefaultDropAction(QtCore.Qt.DropAction.MoveAction)
        self.source_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.source_list.setSpacing(2)
        lay.addWidget(self.source_list, 1)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        self.sort_button = MPushButton(self.tr("Sort by Name"))
        self.remove_button = MPushButton(self.tr("Remove"))
        self.clear_button = MPushButton(self.tr("Clear"))
        for b in (self.sort_button, self.remove_button, self.clear_button):
            b.small()
            row.addWidget(b)
        lay.addLayout(row)

        self.count_label = QtWidgets.QLabel()
        self.count_label.setObjectName("toonStitchMuted")
        lay.addWidget(self.count_label)

        self.add_files_button.clicked.connect(self._pick_files)
        self.add_folder_button.clicked.connect(self._pick_folder)
        self.from_project_button.clicked.connect(self.use_project_pages)
        self.sort_button.clicked.connect(self.sort_by_name)
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button.clicked.connect(self.clear_sources)
        self.source_list.model().rowsMoved.connect(self._sources_changed)
        self.source_list.itemSelectionChanged.connect(self._update_state)
        return card

    def _build_preview(self):
        card, lay = self._card(self.tr("Preview"))
        self.summary_label = QtWidgets.QLabel(self.tr("Add slices, then press Preview to see where the cuts fall."))
        self.summary_label.setObjectName("toonStitchMuted")
        self.summary_label.setWordWrap(True)
        lay.addWidget(self.summary_label)

        self.preview_canvas = PreviewCanvas()
        self.preview_scroll = QtWidgets.QScrollArea()
        self.preview_scroll.setObjectName("toonStitchPreview")
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview_scroll.setWidget(self.preview_canvas)
        self.preview_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        lay.addWidget(self.preview_scroll, 1)

        legend = QtWidgets.QHBoxLayout()
        self.legend_found = QtWidgets.QLabel(self.tr("- - - where the original slices ended"))
        self.legend_forced = QtWidgets.QLabel(self.tr("━ forced cut (no empty row nearby)"))
        legend.addWidget(self.legend_found)
        legend.addWidget(self.legend_forced)
        legend.addStretch(1)
        lay.addLayout(legend)
        return card

    def _build_settings(self):
        card, lay = self._card(self.tr("Pages"))
        card.setFixedWidth(300)
        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)

        self.height_spin = MSpinBox()
        self.height_spin.setRange(0, 60000)
        self.height_spin.setSingleStep(500)
        self.height_spin.setSuffix(" px")
        self.height_spin.setSpecialValueText(self.tr("Don't split"))
        self.height_spin.setToolTip(self.tr("About how tall each page should be. 0 keeps one long image."))
        form.addRow(self.tr("Page height"), self.height_spin)

        self.sensitivity_spin = MSpinBox()
        self.sensitivity_spin.setRange(0, 100)
        self.sensitivity_spin.setSuffix(" %")
        self.sensitivity_spin.setToolTip(self.tr(
            "How empty a row must be to cut there. 100 % accepts only perfectly flat rows; "
            "lower it for scans with grain or a faint texture."
        ))
        form.addRow(self.tr("Detection"), self.sensitivity_spin)

        self.step_spin = MSpinBox()
        self.step_spin.setRange(1, 100)
        self.step_spin.setSuffix(" px")
        self.step_spin.setToolTip(self.tr("Rows skipped between candidate cut rows."))
        form.addRow(self.tr("Scan step"), self.step_spin)

        self.margin_spin = MSpinBox()
        self.margin_spin.setRange(0, 500)
        self.margin_spin.setSuffix(" px")
        self.margin_spin.setToolTip(self.tr("Columns ignored at each edge, e.g. a frame line down the sides."))
        form.addRow(self.tr("Ignore edges"), self.margin_spin)

        self.width_combo = QtWidgets.QComboBox()
        self.width_combo.addItem(self.tr("Shrink to the narrowest"), "min")
        self.width_combo.addItem(self.tr("Enlarge to the widest"), "max")
        self.width_combo.addItem(self.tr("Keep pixels, pad the sides"), "none")
        form.addRow(self.tr("Different widths"), self.width_combo)

        self.format_combo = QtWidgets.QComboBox()
        self.format_combo.addItem("PNG", "png")
        self.format_combo.addItem("JPEG", "jpg")
        form.addRow(self.tr("Format"), self.format_combo)

        self.quality_spin = MSpinBox()
        self.quality_spin.setRange(50, 100)
        self.quality_spin.setSuffix(" %")
        form.addRow(self.tr("JPEG quality"), self.quality_spin)

        self.prefix_edit = QtWidgets.QLineEdit()
        self.prefix_edit.setPlaceholderText(self.tr("e.g. ch12_"))
        form.addRow(self.tr("Name prefix"), self.prefix_edit)
        lay.addLayout(form)

        out_label = QtWidgets.QLabel(self.tr("Save to"))
        lay.addWidget(out_label)
        row = QtWidgets.QHBoxLayout()
        self.output_edit = QtWidgets.QLineEdit()
        self.output_edit.setPlaceholderText(self.tr("Choose a folder"))
        self.output_button = MPushButton()
        self.output_button.setIcon(MIcon("folder-open.svg"))
        self.output_button.setToolTip(self.tr("Choose a folder"))
        row.addWidget(self.output_edit, 1)
        row.addWidget(self.output_button)
        lay.addLayout(row)

        lay.addStretch(1)
        self.progress = MProgressBar()
        self.progress.setVisible(False)
        lay.addWidget(self.progress)
        self.status_label = QtWidgets.QLabel()
        self.status_label.setObjectName("toonStitchMuted")
        self.status_label.setWordWrap(True)
        lay.addWidget(self.status_label)

        self.preview_button = MPushButton(self.tr("Preview"))
        self.stitch_button = MPushButton(self.tr("Stitch and Save")).primary()
        self.stitch_button.setIcon(MIcon("run.svg"))
        lay.addWidget(self.preview_button)
        lay.addWidget(self.stitch_button)

        row = QtWidgets.QHBoxLayout()
        self.open_folder_button = MPushButton(self.tr("Open Folder"))
        self.open_project_button = MPushButton(self.tr("Open as New Project"))
        row.addWidget(self.open_folder_button)
        row.addWidget(self.open_project_button)
        lay.addLayout(row)

        self.output_button.clicked.connect(self._pick_output)
        self.preview_button.clicked.connect(self.run_preview)
        self.stitch_button.clicked.connect(self.run_stitch)
        self.open_folder_button.clicked.connect(self._open_folder)
        self.open_project_button.clicked.connect(lambda: self.open_pages.emit(list(self._written)))
        for spin in (self.height_spin, self.sensitivity_spin, self.step_spin, self.margin_spin, self.quality_spin):
            spin.valueChanged.connect(self._settings_changed)
        self.width_combo.currentIndexChanged.connect(self._settings_changed)
        self.format_combo.currentIndexChanged.connect(self._settings_changed)
        self.prefix_edit.textChanged.connect(self._save_settings)
        self.output_edit.textChanged.connect(self._update_state)
        return card

    # ------------------------------------------------------------------ sources

    def sources(self) -> list[Source]:
        return [self.source_list.item(i).data(SOURCE_ROLE) for i in range(self.source_list.count())]

    def add_files(self, paths, from_project: bool = False) -> int:
        added = 0
        for path in paths:
            if not from_project and not is_image_file(path):
                continue
            item = QtWidgets.QListWidgetItem(os.path.basename(path))
            item.setData(SOURCE_ROLE, Source(path, from_project))
            item.setToolTip(path)
            item.setIcon(self._thumbnail(path))
            self.source_list.addItem(item)
            added += 1
        if added and not self.output_edit.text() and not from_project:
            self.output_edit.setText(os.path.join(os.path.dirname(paths[0]), "stitched"))
        self._sources_changed()
        return added

    def add_folder(self, folder: str) -> int:
        names = sorted((n for n in os.listdir(folder) if is_image_file(n)), key=natural_sort_key)
        return self.add_files([os.path.join(folder, n) for n in names])

    def use_project_pages(self) -> int:
        files = list(getattr(self.main, "image_files", None) or [])
        return self.add_files(files, from_project=True)

    def sort_by_name(self):
        items = [self.source_list.takeItem(0) for _ in range(self.source_list.count())]
        items.sort(key=lambda it: natural_sort_key(it.data(SOURCE_ROLE).path))
        for it in items:
            self.source_list.addItem(it)
        self._sources_changed()

    def remove_selected(self):
        for it in self.source_list.selectedItems():
            self.source_list.takeItem(self.source_list.row(it))
        self._sources_changed()

    def clear_sources(self):
        self.source_list.clear()
        self._sources_changed()

    def _thumbnail(self, path: str) -> QtGui.QIcon:
        reader = QtGui.QImageReader(path)
        size = reader.size()
        if size.isValid() and size.width() > 0:
            reader.setScaledSize(size.scaled(THUMB, QtCore.Qt.AspectRatioMode.KeepAspectRatio))
        image = reader.read()
        return QtGui.QIcon(QtGui.QPixmap.fromImage(image)) if not image.isNull() else QtGui.QIcon()

    def _resolve(self, source: Source):
        """GUI side: what the worker should read for a source.

        A project page counts as it stands in the editor — an unsaved edit in
        ``image_data``, else the latest history file — which only the GUI
        thread may look up.
        """
        if not source.from_project or self.main is None:
            return source.path
        data = getattr(self.main, "image_data", {}).get(source.path)
        if isinstance(data, np.ndarray):
            return data
        history = getattr(self.main, "image_history", {}).get(source.path)
        if history:
            index = getattr(self.main, "current_history_index", {}).get(source.path, len(history) - 1)
            return history[index]
        return source.path

    def _pick_files(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, self.tr("Add Images"), "", IMAGE_FILTER)
        if paths:
            self.add_files(sorted(paths, key=natural_sort_key))

    def _pick_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, self.tr("Add Folder"))
        if folder:
            self.add_folder(folder)

    def _pick_output(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, self.tr("Choose a folder"), self.output_edit.text())
        if folder:
            self.output_edit.setText(folder)

    def _open_folder(self):
        folder = self.output_edit.text().strip()
        if folder and os.path.isdir(folder):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(folder))

    # ------------------------------------------------------------------ settings

    def settings(self) -> stitch.StitchSettings:
        return stitch.StitchSettings(
            target_height=self.height_spin.value(),
            sensitivity=self.sensitivity_spin.value(),
            scan_step=self.step_spin.value(),
            margin=self.margin_spin.value(),
            width_mode=self.width_combo.currentData(),
            fmt=self.format_combo.currentData(),
            quality=self.quality_spin.value(),
        )

    def _qsettings(self) -> QtCore.QSettings:
        return QtCore.QSettings("ComicLabs", "ComicTranslate")

    def _load_settings(self):
        d = stitch.StitchSettings()
        s = self._qsettings()
        s.beginGroup(SETTINGS_GROUP)
        widgets = [self.height_spin, self.sensitivity_spin, self.step_spin, self.margin_spin,
                   self.width_combo, self.format_combo, self.quality_spin, self.prefix_edit]
        blockers = [QtCore.QSignalBlocker(w) for w in widgets]
        self.height_spin.setValue(s.value("target_height", d.target_height, type=int))
        self.sensitivity_spin.setValue(s.value("sensitivity", d.sensitivity, type=int))
        self.step_spin.setValue(s.value("scan_step", d.scan_step, type=int))
        self.margin_spin.setValue(s.value("margin", d.margin, type=int))
        self.quality_spin.setValue(s.value("quality", d.quality, type=int))
        for combo, key, default in ((self.width_combo, "width_mode", d.width_mode), (self.format_combo, "fmt", d.fmt)):
            index = combo.findData(s.value(key, default, type=str))
            combo.setCurrentIndex(max(0, index))
        self.prefix_edit.setText(s.value("prefix", "", type=str))
        s.endGroup()
        del blockers

    def _save_settings(self):
        st = self.settings()
        s = self._qsettings()
        s.beginGroup(SETTINGS_GROUP)
        for key in ("target_height", "sensitivity", "scan_step", "margin", "width_mode", "fmt", "quality"):
            s.setValue(key, getattr(st, key))
        s.setValue("prefix", self.prefix_edit.text())
        s.endGroup()

    def _settings_changed(self):
        self._save_settings()
        self._invalidate()

    def _sources_changed(self, *args):
        self._invalidate()

    def _invalidate(self):
        """Settings or sources changed: the preview no longer shows what would be written."""
        self._plan = None
        self._plan_key = None
        self._preview = None
        self.preview_canvas.set_pages(None, [])
        self._update_state()

    def _update_state(self):
        n = self.source_list.count()
        self.count_label.setText(self.tr("{0} slice(s)").format(n))
        has_project = bool(getattr(self.main, "image_files", None))
        self.from_project_button.setEnabled(has_project and not self._busy)
        for b in (self.add_files_button, self.add_folder_button, self.sort_button, self.clear_button):
            b.setEnabled(not self._busy)
        self.remove_button.setEnabled(bool(self.source_list.selectedItems()) and not self._busy)
        self.preview_button.setEnabled(n > 0 and not self._busy)
        self.stitch_button.setEnabled(n > 0 and not self._busy and bool(self.output_edit.text().strip()))
        self.quality_spin.setEnabled(self.format_combo.currentData() == "jpg")
        self.open_folder_button.setEnabled(bool(self._written))
        self.open_project_button.setEnabled(bool(self._written) and not self._busy)

    # ------------------------------------------------------------------ running

    def _key(self):
        return (tuple(self.sources()), self.settings())

    def _run(self, fn, on_result, *args):
        """On the task runner when there is a window, inline otherwise (tests)."""
        self._set_busy(True)

        def done(result):
            try:
                on_result(result)
            finally:
                self._set_busy(False)

        def failed(error):
            self._set_busy(False)
            exc = error[1] if isinstance(error, tuple) and len(error) > 1 else error
            self.status_label.setText(self.tr("Failed: {0}").format(exc))

        runner = getattr(self.main, "run_threaded", None)
        if runner is None:
            try:
                result = fn(*args)
            except Exception as exc:  # noqa: BLE001 — surfaced in the page, as the worker path does
                failed((type(exc), exc, None))
                return
            done(result)
            return
        runner(fn, done, failed, None, *args)

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.progress.setVisible(busy)
        if busy:
            self.progress.setRange(0, 0)
        self._update_state()

    def run_preview(self):
        if not self.source_list.count():
            return
        key = self._key()
        items = [self._resolve(s) for s in key[0]]
        self.status_label.setText(self.tr("Reading {0} slice(s)…").format(len(items)))

        def show(result):
            stitch_plan, pages = result
            self._plan, self._plan_key, self._preview = stitch_plan, key, pages
            self._show_plan()
            self.status_label.clear()

        self._run(_plan_job, show, items, key[1])

    def run_stitch(self):
        out_dir = self.output_edit.text().strip()
        if not self.source_list.count() or not out_dir:
            return
        key = self._key()
        settings = key[1]
        if settings.target_height == 0 and settings.fmt == "jpg":
            settings = dataclasses.replace(settings, fmt="png")  # JPEG cannot hold a long strip
        cached = self._plan if self._plan_key == key else None
        cached_preview = self._preview
        items = None if cached is not None else [self._resolve(s) for s in key[0]]
        prefix = self.prefix_edit.text().strip()
        progress = self._progress.emit

        def job():
            if cached is not None:
                stitch_plan, preview = cached, cached_preview
            else:
                stitch_plan, preview = _plan_job(items, key[1])
            paths = stitch.write_pages(stitch_plan, out_dir, settings, prefix, progress)
            return stitch_plan, preview, paths

        def finished(result):
            stitch_plan, preview, paths = result
            self._plan, self._plan_key, self._preview = stitch_plan, key, preview
            self._written = paths
            self._show_plan()
            self.status_label.setText(self.tr("Saved {0} page(s) to {1}").format(len(paths), out_dir))

        self.status_label.setText(self.tr("Stitching…"))
        self._run(job, finished)

    def _on_progress(self, done: int, total: int):
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.status_label.setText(self.tr("Writing page {0} of {1}…").format(done, total))

    # ------------------------------------------------------------------ preview

    def _show_plan(self):
        p = self._plan
        if p is None:
            return
        forced = len(p.forced)
        text = self.tr("{0} slice(s) → {1} page(s) · {2} × {3} px strip").format(
            len(p.slices), p.page_count, p.width, p.total_height
        )
        if forced:
            text += "  ·  " + self.tr("{0} forced cut(s)").format(forced)
        self.summary_label.setText(text)
        self._paint_preview()
        self._update_state()

    def _paint_preview(self):
        if self._plan is not None and self._preview is not None:
            self.preview_canvas.set_pages(self._plan, self._preview)

    # ------------------------------------------------------------------ theme

    def apply_theme(self, is_dark: bool):
        self._is_dark = is_dark
        t = theme_tokens.palette(dark=is_dark)
        self.setStyleSheet(f"""
            QWidget#toonStitchPage {{ background: {t['ground']}; }}
            QLabel#toonStitchHeading {{
                color: {t['text_1']}; font-family: "{theme_tokens.DISPLAY_FONT_FAMILY}";
                font-size: 26px; font-weight: 700; background: transparent;
            }}
            QLabel#toonStitchSubtitle, QLabel#toonStitchMuted {{ color: {t['text_2']}; background: transparent; }}
            QFrame#toonStitchCard {{
                background: {t['panel']}; border: 1px solid {t['line']}; border-radius: 12px;
            }}
            QFrame#toonStitchCard QLabel {{ background: transparent; }}
            QScrollArea#toonStitchPreview, QScrollArea#toonStitchPreview > QWidget > QWidget {{
                background: {t['ground']}; border-radius: 8px;
            }}
            QListWidget#toonStitchSources {{
                background: {t['ground']}; border: 1px solid {t['line']}; border-radius: 8px;
            }}
        """)
        self.legend_found.setStyleSheet(f"color: {t['text_2']}; background: transparent;")
        self.legend_forced.setStyleSheet(f"color: {t['warn']}; background: transparent;")
        self.preview_canvas.set_colours(text=t["text_2"], edge=t["accent"], warn=t["warn"], line=t["line"])

    def showEvent(self, event):
        super().showEvent(event)
        self._update_state()  # the open project may have changed while the page was hidden
