"""A searchable picker for OpenRouter's model catalogue.

Presents itself to the settings code as a line edit — `text()`, `setText()`
and `textChanged` — so it drops into the credentials page where the old plain
input was, and a model id that is not in the catalogue can still be typed.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from modules.utils.openrouter_models import (
    OpenRouterModel,
    cache_age_seconds,
    get_models,
    load_cached_models,
)

from ..dayu_widgets.check_box import MCheckBox
from ..dayu_widgets.label import MLabel
from ..dayu_widgets.push_button import MPushButton
from .utils import set_label_width


class _ModelFetcher(QtCore.QThread):
    """Fetches the catalogue off the UI thread."""

    finished_with_models = QtCore.Signal(list)
    failed = QtCore.Signal(str)

    def __init__(self, api_key: str, force_refresh: bool, parent=None):
        super().__init__(parent)
        self._api_key = api_key
        self._force_refresh = force_refresh

    def run(self):
        try:
            models = get_models(self._api_key, force_refresh=self._force_refresh)
        except Exception as exc:  # network, DNS, TLS, malformed payload
            self.failed.emit(str(exc))
            return
        self.finished_with_models.emit(models)


class _ModelItemDelegate(QtWidgets.QStyledItemDelegate):
    """Two-line rows: the model id, then its size and price.

    The id alone is what gets typed and stored, so it stays the display role
    and the details ride along in a separate role — that keeps completion
    matching on the id rather than on a price the user never types.
    """

    DETAIL_ROLE = QtCore.Qt.ItemDataRole.UserRole + 1
    PADDING_X = 8
    PADDING_Y = 4
    GAP = 2

    def paint(self, painter: QtGui.QPainter, option, index):
        option = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(option, index)

        style = option.widget.style() if option.widget else QtWidgets.QApplication.style()
        # Draw the row's background/selection, but not its text — we lay that out.
        option.text = ""
        style.drawControl(QtWidgets.QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget)

        model_id = index.data(QtCore.Qt.ItemDataRole.DisplayRole) or ""
        detail = index.data(self.DETAIL_ROLE) or ""

        is_selected = bool(option.state & QtWidgets.QStyle.StateFlag.State_Selected)
        role = (
            QtGui.QPalette.ColorRole.HighlightedText
            if is_selected
            else QtGui.QPalette.ColorRole.Text
        )
        primary_color = option.palette.color(role)
        secondary_color = QtGui.QColor(primary_color)
        secondary_color.setAlphaF(0.62)

        id_font = QtGui.QFont(option.font)
        detail_font = QtGui.QFont(option.font)
        detail_font.setPointSizeF(max(6.0, option.font.pointSizeF() - 1.0))

        rect = option.rect.adjusted(self.PADDING_X, self.PADDING_Y, -self.PADDING_X, -self.PADDING_Y)
        id_height = QtGui.QFontMetrics(id_font).height()

        painter.save()
        painter.setFont(id_font)
        painter.setPen(primary_color)
        painter.drawText(
            QtCore.QRect(rect.left(), rect.top(), rect.width(), id_height),
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
            model_id,
        )
        if detail:
            painter.setFont(detail_font)
            painter.setPen(secondary_color)
            painter.drawText(
                QtCore.QRect(
                    rect.left(),
                    rect.top() + id_height + self.GAP,
                    rect.width(),
                    rect.height() - id_height - self.GAP,
                ),
                QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
                detail,
            )
        painter.restore()

    def sizeHint(self, option, index) -> QtCore.QSize:
        detail_font = QtGui.QFont(option.font)
        detail_font.setPointSizeF(max(6.0, option.font.pointSizeF() - 1.0))
        height = (
            QtGui.QFontMetrics(option.font).height()
            + QtGui.QFontMetrics(detail_font).height()
            + self.GAP
            + 2 * self.PADDING_Y
        )
        return QtCore.QSize(option.rect.width(), height)


class OpenRouterModelSelector(QtWidgets.QWidget):
    """Model field for OpenRouter: type an id, or pick one from the catalogue."""

    textChanged = QtCore.Signal(str)

    INPUT_WIDTH = 400

    def __init__(self, parent=None):
        super().__init__(parent)

        self._models: list[OpenRouterModel] = []
        self._fetcher: _ModelFetcher | None = None
        self._api_key_source = None
        self._catalogue_loaded = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)

        prefix = MLabel(self.tr("Model")).border()
        set_label_width(prefix)
        prefix.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        row.addWidget(prefix)

        self.combo = QtWidgets.QComboBox()
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        # setFixedWidth on the label pins max == min, so this is its final width
        # even though the row has not been laid out yet. Together the two add up
        # to the same 400px the plain credential inputs use.
        self.combo.setFixedWidth(max(160, self.INPUT_WIDTH - prefix.maximumWidth() - row.spacing()))
        self.combo.lineEdit().setPlaceholderText("e.g. openai/gpt-4o, anthropic/claude-sonnet-4.5")
        self.combo.setView(QtWidgets.QListView())
        self.combo.view().setItemDelegate(_ModelItemDelegate(self.combo))
        # Without this the popup is capped at 10 rows and scrolls a 400-item list.
        self.combo.setMaxVisibleItems(14)
        self.combo.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        row.addWidget(self.combo)
        row.addStretch(1)
        layout.addLayout(row)

        completer = QtWidgets.QCompleter(self.combo.model(), self.combo)
        completer.setCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(QtCore.Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QtWidgets.QCompleter.CompletionMode.PopupCompletion)
        self.combo.setCompleter(completer)

        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(8)

        self.vision_only_checkbox = MCheckBox(self.tr("Vision models only"))
        self.vision_only_checkbox.setToolTip(self.tr(
            "Only list models that accept images, which are the ones that can use\n"
            "the page image when \"Provide Image as Input to AI\" is on."
        ))
        self.vision_only_checkbox.toggled.connect(self._repopulate)
        controls.addWidget(self.vision_only_checkbox)

        self.refresh_button = MPushButton(self.tr("Refresh list")).small()
        self.refresh_button.clicked.connect(self.refresh)
        controls.addWidget(self.refresh_button)

        self.status_label = MLabel("").secondary()
        controls.addWidget(self.status_label)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.combo.currentTextChanged.connect(self._on_text_changed)
        self.combo.lineEdit().textEdited.connect(self._on_text_changed)

    # Line-edit interface, so the settings page can treat this like MLineEdit.

    def text(self) -> str:
        return self.combo.currentText().strip()

    def setText(self, value: str) -> None:
        value = (value or "").strip()
        if self.combo.currentText() == value:
            return
        index = self.combo.findText(value, QtCore.Qt.MatchFlag.MatchFixedString)
        if index >= 0:
            self.combo.setCurrentIndex(index)
        else:
            self.combo.setEditText(value)

    def _on_text_changed(self, _text: str = "") -> None:
        self.textChanged.emit(self.text())
        self._update_status()

    # Catalogue

    def set_api_key_source(self, widget) -> None:
        """Where to read the key from when refreshing.

        The catalogue endpoint is public, but sending the key reflects the
        account's own access, so the picker reads whatever the neighbouring
        API key field currently holds rather than a stale saved copy.
        """
        self._api_key_source = widget

    def _api_key(self) -> str:
        if self._api_key_source is None:
            return ""
        try:
            return self._api_key_source.text().strip()
        except Exception:
            return ""

    def ensure_catalogue_loaded(self) -> None:
        """Populate from cache, and fetch when the cache is missing or stale.

        Called when the credentials page is first shown rather than at
        construction, so app startup never waits on the network.
        """
        if self._catalogue_loaded:
            return
        self._catalogue_loaded = True

        cached = load_cached_models()
        if cached:
            self._set_models(cached)
        self._start_fetch(force_refresh=False)

    def refresh(self) -> None:
        self._catalogue_loaded = True
        self._start_fetch(force_refresh=True)

    def _start_fetch(self, force_refresh: bool) -> None:
        if self._fetcher is not None:
            try:
                if self._fetcher.isRunning():
                    return
            except RuntimeError:
                # The previous fetcher finished and deleted its C++ side.
                self._fetcher = None

        self.refresh_button.setEnabled(False)
        self.status_label.setText(self.tr("Loading models…"))

        fetcher = _ModelFetcher(self._api_key(), force_refresh, self)
        fetcher.finished_with_models.connect(self._on_models_fetched)
        fetcher.failed.connect(self._on_fetch_failed)
        fetcher.finished.connect(lambda: self.refresh_button.setEnabled(True))
        # Parenting alone would keep every past fetcher alive for the app's lifetime.
        fetcher.finished.connect(fetcher.deleteLater)
        self._fetcher = fetcher
        fetcher.start()

    def _on_models_fetched(self, models: list) -> None:
        if models:
            self._set_models(models)
        self._update_status()

    def _on_fetch_failed(self, message: str) -> None:
        if self._models:
            self.status_label.setText(self.tr("Offline — showing the cached list."))
        else:
            self.status_label.setText(self.tr("Could not reach OpenRouter: {0}").format(message))

    def _set_models(self, models: list[OpenRouterModel]) -> None:
        self._models = models
        self._repopulate()

    def _repopulate(self) -> None:
        current = self.text()
        vision_only = self.vision_only_checkbox.isChecked()

        self.combo.blockSignals(True)
        self.combo.clear()
        for model in self._models:
            if vision_only and not model.supports_images:
                continue
            self.combo.addItem(model.id)
            index = self.combo.count() - 1
            self.combo.setItemData(index, model.describe(), _ModelItemDelegate.DETAIL_ROLE)
            self.combo.setItemData(
                index,
                f"{model.name}\n{model.describe()}",
                QtCore.Qt.ItemDataRole.ToolTipRole,
            )
        self.combo.blockSignals(False)

        # clear() empties the edit; put back what the user had chosen, whether or
        # not the current filter still lists it.
        self.setText(current)
        self._update_status()

    def _update_status(self) -> None:
        if not self._models:
            return

        listed = self.combo.count()
        current = self.text()
        known = {model.id for model in self._models}

        if current and current not in known:
            self.status_label.setText(self.tr("{0} models · custom id").format(listed))
            self.status_label.setToolTip(
                self.tr("\"{0}\" is not in the catalogue. It will still be sent as typed.").format(current)
            )
            return

        self.status_label.setToolTip("")
        age = cache_age_seconds()
        if age is None:
            self.status_label.setText(self.tr("{0} models").format(listed))
        elif age < 3600:
            self.status_label.setText(self.tr("{0} models · updated just now").format(listed))
        else:
            self.status_label.setText(
                self.tr("{0} models · updated {1}h ago").format(listed, int(age // 3600))
            )
