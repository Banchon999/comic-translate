"""Qt implementation of the text measurement seam.

Lifted verbatim from `pyside_word_wrap`'s inner `eval_metrics`, so the numbers
it returns are the ones the renderer has always used. Two deliberate
differences:

* The outline is no longer added here. It padded the measured box without
  changing the layout, which made it a caller concern; `render.py` adds it.
* The font family fallback is exposed separately, because Skia answers it from
  a different place and callers should not have to ask which engine is in play.

This is the *measurer*, not the painter. `TextBlockItem` still paints, and the
two must agree — see the note in `core/text_measure.py`.
"""

from __future__ import annotations

from PySide6.QtGui import (
    QFont,
    QTextBlockFormat,
    QTextCursor,
    QTextDocument,
    QTextOption,
)
from PySide6.QtWidgets import QApplication

from app.ui.qt_values import to_qt_alignment, to_qt_layout_direction
from core.text_measure import TextMeasurer, TextStyle

from .vertical_layout import VerticalTextDocumentLayout


class QtTextMeasurer(TextMeasurer):
    name = "qt"

    def resolve_font_family(self, font_family: str) -> str:
        if isinstance(font_family, str) and font_family.strip():
            return font_family.strip()
        return QApplication.font().family()

    def _font(self, style: TextStyle) -> QFont:
        font = QFont(self.resolve_font_family(style.font_family), style.font_size)
        font.setBold(style.bold)
        font.setItalic(style.italic)
        font.setUnderline(style.underline)
        return font

    def measure(self, text: str, style: TextStyle) -> tuple[float, float]:
        doc = QTextDocument()
        doc.setDefaultFont(self._font(style))
        doc.setPlainText(text)

        text_option = QTextOption()
        text_option.setTextDirection(to_qt_layout_direction(style.direction))
        doc.setDefaultTextOption(text_option)

        if style.vertical:
            layout = VerticalTextDocumentLayout(
                document=doc,
                line_spacing=style.line_spacing,
            )
            doc.setDocumentLayout(layout)
            layout.update_layout()
        else:
            cursor = QTextCursor(doc)
            cursor.select(QTextCursor.SelectionType.Document)
            block_format = QTextBlockFormat()
            block_format.setLineHeight(
                style.line_spacing * 100,
                QTextBlockFormat.LineHeightTypes.ProportionalHeight.value,
            )
            block_format.setAlignment(to_qt_alignment(style.alignment))
            cursor.mergeBlockFormat(block_format)

        size = doc.size()
        return size.width(), size.height()
