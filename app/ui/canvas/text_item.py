from PySide6.QtWidgets import QGraphicsTextItem, QGraphicsItem, \
     QApplication, QWidget, QStyleOptionGraphicsItem, QGraphicsDropShadowEffect
from PySide6.QtGui import QFont, QCursor, QColor, QBrush, QPen, QFontMetricsF, \
     QLinearGradient, QGradient, QPainterPath, QTransform, \
     QTextCharFormat, QTextBlockFormat, QTextCursor, QPainter
from PySide6.QtCore import Qt, QRectF, Signal, QPointF
import math, copy
from dataclasses import dataclass
from enum import Enum
from . import handles
from .text.vertical_layout import VerticalTextDocumentLayout
from app.ui.qt_values import to_qt_color, to_qt_layout_direction
from core import text_engine
from app.ui.canvas import skia_paint
# Re-exported: these moved to core so the pipeline can build them without
# Qt. Same classes, so the project encoder and every existing holder of one
# are unaffected, and `from .text_item import OutlineInfo` still works.
from core.text_style import OutlineInfo, OutlineType  # noqa: F401
from modules.rendering.text_effects import arc_bulge, arc_placements, gradient_line


@dataclass
class TextBlockState:
    rect: tuple  
    rotation: float
    transform_origin: QPointF

    @classmethod
    def from_item(cls, item: QGraphicsTextItem):
        """Create TextBlockState from a TextBlockItem"""
        # The text's own rect, not the painted one: a curved item paints
        # outside its box, and this state is what a resize is undone back to.
        rect_of = getattr(item, 'text_rect', item.boundingRect)
        rect = QRectF(item.pos(), rect_of().size()).getCoords()
        return cls(
            rect=rect,
            rotation=item.rotation(),
            transform_origin=item.transformOriginPoint()
        )
    

class TextBlockItem(QGraphicsTextItem):
    text_changed = Signal(str)
    item_selected = Signal(object)
    item_deselected = Signal()
    text_highlighted = Signal(dict)
    change_undo = Signal(TextBlockState, TextBlockState)
    
    def __init__(self, 
             text = "", 
             font_family = "", 
             font_size = 20, 
             render_color = QColor(0, 0, 0), 
             alignment = Qt.AlignmentFlag.AlignCenter, 
             line_spacing = 1.2, 
             outline_color = QColor(255, 255, 255), 
             outline_width = 1,
             bold=False, 
             italic=False, 
             underline=False,
             direction=Qt.LayoutDirection.LeftToRight):

        super().__init__(text)
        self.text_color = render_color
        self.outline = True if outline_color else False
        self.outline_color = outline_color
        self.outline_width = outline_width
        self.bold = bold
        self.italic = italic
        self.underline = underline
        self.font_family = font_family
        self.font_size = font_size
        self.alignment = alignment
        self.line_spacing = line_spacing
        self.direction = direction

        self.layout = None
        self.vertical = False

        # Text effects
        self.letter_spacing = 0.0
        self.shadow_enabled = False
        self.shadow_color = QColor(0, 0, 0, 160)
        self.shadow_offset = (4.0, 4.0)
        self.shadow_blur = 0.0
        self.gradient_enabled = False
        self.gradient_color = QColor(255, 255, 255)
        self.gradient_angle = 90.0
        self.curvature = 0.0
        self._applying_gradient = False
        self._curved_bulge = None

        self.selected = False
        self.resizing = False
        self.resize_handle = None
        self.resize_start = None
        self.editing_mode = False
        self.last_selection = None 
        self._drag_selecting = False
        self._drag_select_anchor = None

        # Rotation properties
        self.rot_handle = None
        self.rotating = False
        self.last_rotation_angle = 0
        self.rotation_smoothing = 1.0  # rotation sensitivity
        self.center_scene_pos = None  

        self.old_state = None

        self.selection_outlines = []

        self.setAcceptHoverEvents(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setFlag(QGraphicsTextItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsTextItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.document().contentsChanged.connect(self._on_text_changed)
        self.setTransformOriginPoint(self.boundingRect().center())
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.setZValue(1)

        # Set the initial text direction
        self._apply_text_direction()

    def set_vertical(self, vertical: bool):
        doc = self.document()
        is_already_vertical = isinstance(doc.documentLayout(), VerticalTextDocumentLayout)

        if vertical == is_already_vertical:
            return

        self.vertical = vertical

        # Disconnect signals from the old layout if it's our custom one
        if is_already_vertical:
            old_layout = doc.documentLayout()
            if old_layout:
                try:
                    old_layout.size_enlarged.disconnect(self.on_document_enlarged)
                    old_layout.documentSizeChanged.disconnect(self.setCenterTransform)
                except (TypeError, RuntimeError): # Already disconnected
                    pass
        
        # Inform the graphics system that the geometry will change
        self.prepareGeometryChange()
        current_rect = self.text_rect()

        # Disable text interaction while changing layout
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        
        if doc.documentLayout():
            doc.documentLayout().blockSignals(True)

        if vertical:
            layout = VerticalTextDocumentLayout(
                document=doc,
                line_spacing=self.line_spacing,
            )
            self.layout = layout
            doc.setDocumentLayout(layout)
            
            # Connect signals for the new layout
            layout.size_enlarged.connect(self.on_document_enlarged)
            layout.documentSizeChanged.connect(self.setCenterTransform)
            
            # Initialize layout with the item's current size.
            # set_max_size enforces the dimensions, but a text item with no 
            # set text has negligible size, so this can collapse the layout.
            # Only uncomment if set_vertical runs after plain text is set.
            # layout.set_max_size(current_rect.width(), current_rect.height())
            # layout.update_layout()

        else:  # Switching back to horizontal
            self.layout = None
            doc.setDocumentLayout(None)  # Qt will restore the default layout.
            self.setTextWidth(current_rect.width())
        
        # After setting the new layout, update the item's state
        self.setCenterTransform()
        self.update()

    def setCenterTransform(self):
        center = self.boundingRect().center()
        self.setTransformOriginPoint(center)

    def on_document_enlarged(self):
        self.prepareGeometryChange()
        self.setCenterTransform()

    def _apply_text_direction(self):
        text_option = self.document().defaultTextOption()
        text_option.setTextDirection(to_qt_layout_direction(self.direction))
        self.document().setDefaultTextOption(text_option)

    def set_direction(self, direction):
        direction = to_qt_layout_direction(direction)
        if self.direction != direction:
            self.direction = direction
            self._apply_text_direction()
            self.update()

    def set_text(self, text, width):
        if self.is_html(text):
            self.setHtml(text)
            self.setTextWidth(width)
            self.set_outline(self.outline_color, self.outline_width)
        else:
            self.set_plain_text(text)

    def set_plain_text(self, text):
        self.setPlainText(text)
        self.apply_all_attributes()

    def is_html(self, text):
        import re
        if not isinstance(text, str):
            return False
        # Improved check for HTML tags (case-insensitive, includes common tags)
        return bool(re.search(r'<(div|span|p|br|html|body|style)[^>]*>', text, re.IGNORECASE))

    def set_font(self, font_family, font_size):
        if not self.textCursor().hasSelection():
            self.font_family = font_family
            self.font_size = font_size

        # Ensure minimum font size.
        font_size = max(1, font_size)

        # Fallback to application default font family if none provided
        effective_family = font_family.strip() if isinstance(font_family, str) and font_family.strip() else QApplication.font().family()
        font = self._with_letter_spacing(QFont(effective_family, font_size))
        self.update_text_format('font', font)

    def _with_letter_spacing(self, font: QFont) -> QFont:
        """Stamp the current letter spacing onto a font about to be applied.

        Spacing lives on the font, so it has to be re-applied every time the family
        or size changes or it would be dropped.
        """
        if self.letter_spacing:
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, self.letter_spacing)
        else:
            font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100.0)
        return font

    def set_letter_spacing(self, spacing: float):
        """Extra space between characters in points; 0 restores the font's own."""
        self.letter_spacing = float(spacing)
        self.apply_letter_spacing()

    def apply_letter_spacing(self):
        """Stamp the spacing onto every run without disturbing its own font.

        Merging a document-wide font would flatten per-selection families and sizes,
        so each fragment keeps its font and only gains the spacing.
        """
        doc = self.document()
        doc.setDefaultFont(self._with_letter_spacing(QFont(doc.defaultFont())))

        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        block = doc.firstBlock()
        while block.isValid():
            fragment_iter = block.begin()
            while not fragment_iter.atEnd():
                fragment = fragment_iter.fragment()
                if fragment.isValid():
                    char_format = QTextCharFormat()
                    char_format.setFont(
                        self._with_letter_spacing(fragment.charFormat().font())
                    )
                    cursor.setPosition(fragment.position())
                    cursor.setPosition(
                        fragment.position() + fragment.length(), QTextCursor.KeepAnchor
                    )
                    cursor.mergeCharFormat(char_format)
                fragment_iter += 1
            block = block.next()
        cursor.endEditBlock()

        # The vertical layout positions each glyph itself and tracks spacing separately.
        if self.vertical and self.layout:
            self.layout.set_letter_spacing(self.letter_spacing)

    def apply_effects(self):
        """Re-apply the effects that do not survive a plain attribute restore.

        Undo/redo swaps the item's __dict__ and reloads its HTML; the shadow lives on
        a graphics effect and Qt's HTML writer does not emit letter spacing, so both
        have to be put back explicitly.
        """
        self.apply_shadow()
        self.apply_letter_spacing()

    def set_shadow(self, enabled: bool, color: QColor = None, offset=None, blur: float = None):
        """Drop shadow behind the whole item, outline included.

        Uses a graphics effect rather than a hand-drawn offset copy so the blur is
        a real gaussian and so the shadow follows the item's own transform — and so
        it comes out of the save renderer identically, since that renders the same
        scene items.
        """
        self.shadow_enabled = bool(enabled)
        if color is not None:
            self.shadow_color = QColor(color)
        if offset is not None:
            self.shadow_offset = (float(offset[0]), float(offset[1]))
        if blur is not None:
            self.shadow_blur = max(0.0, float(blur))

        self.apply_shadow()

    def apply_shadow(self):
        if not self.shadow_enabled:
            self.setGraphicsEffect(None)
            return

        effect = QGraphicsDropShadowEffect()
        effect.setColor(self.shadow_color)
        effect.setOffset(*self.shadow_offset)
        effect.setBlurRadius(self.shadow_blur)
        self.setGraphicsEffect(effect)

    # ------------------------------------------------------------------
    # Gradient fill
    # ------------------------------------------------------------------

    def set_gradient(self, enabled: bool, color: QColor = None, angle: float = None):
        """Fade the fill from the item's text colour to a second one.

        The item's own colour is the starting one, so turning the gradient off
        leaves the text looking the way the colour button says it should.

        This is an item-wide fill: it replaces per-character colours while it is
        on, and turning it off restores the item's colour everywhere rather than
        whatever each run used to be. A gradient that stopped at a range
        boundary would restart on the next run anyway, which is not what anyone
        means by a gradient across a word.
        """
        was_enabled = self.gradient_enabled
        self.gradient_enabled = bool(enabled)
        if color is not None:
            self.gradient_color = QColor(color)
        if angle is not None:
            self.gradient_angle = float(angle)
        # Turning it off when it was never on must leave the document alone.
        # Applying a plain brush would flatten every per-character colour the
        # item has, and this runs for every text item the save renderer builds.
        if self.gradient_enabled or was_enabled:
            self.apply_gradient()

    def fill_brush(self) -> QBrush:
        """The brush the text is painted with, gradient or plain colour."""
        # An item restored from a project that predates the colour being saved
        # has no text_color, and Qt will not take None as a brush.
        base_color = QColor(self.text_color) if self.text_color else QColor(0, 0, 0)
        if not self.gradient_enabled:
            return QBrush(base_color)

        size = self.document().size()
        line = gradient_line(size.width(), size.height(), self.gradient_angle)
        gradient = QLinearGradient(*line)
        # Anchored to the laid-out document rather than to each glyph run:
        # Qt's ObjectBoundingMode restarts the sweep on every line, so a
        # two-line block would come out the same colour twice over.
        gradient.setCoordinateMode(QGradient.CoordinateMode.LogicalMode)
        gradient.setColorAt(0.0, base_color)
        gradient.setColorAt(1.0, QColor(self.gradient_color) if self.gradient_color else QColor(255, 255, 255))
        return QBrush(gradient)

    def apply_gradient(self):
        """Push the current fill onto the document, or take it back off.

        Curved text paints its own glyphs and reads `fill_brush()` directly, so
        there is nothing to push there.
        """
        if self.curvature:
            self.update()
            return

        # Merging a char format is a document edit, which comes back round as
        # contentsChanged; without this the re-apply would recurse.
        if self._applying_gradient:
            return
        self._applying_gradient = True
        try:
            cursor = QTextCursor(self.document())
            cursor.select(QTextCursor.SelectionType.Document)
            char_format = QTextCharFormat()
            char_format.setForeground(self.fill_brush())
            cursor.mergeCharFormat(char_format)
        finally:
            self._applying_gradient = False
        self.update()

    # ------------------------------------------------------------------
    # Curved text
    # ------------------------------------------------------------------

    def set_curvature(self, curvature: float):
        """Bend the baseline into an arc: 1.0 arches up, -1.0 sags, 0 is flat."""
        curvature = max(-1.0, min(1.0, float(curvature)))
        if curvature == self.curvature:
            return
        self.prepareGeometryChange()
        was_curved = bool(self.curvature)
        self.curvature = curvature
        self._curved_bulge = None
        # Coming back to flat has to put the document's own fill back, since
        # while curved the glyphs were painted by hand and the document was
        # never told what colour it is.
        if was_curved and not curvature:
            self.apply_gradient()
        self.update()

    def text_rect(self) -> QRectF:
        """The laid-out text's rectangle — the box the user sizes and drags.

        `boundingRect()` grows past this when the text is curved, because Qt
        repaints exactly that rectangle and an arch reaches above its line. Item
        geometry — handles, resizing, saved state — uses this one, so bending
        the text does not move the box it lives in.
        """
        return super().boundingRect()

    def boundingRect(self) -> QRectF:
        rect = super().boundingRect()
        if not getattr(self, 'curvature', 0.0):
            return rect
        bulge = self.curved_bulge()
        # Symmetric, so the centre stays where it was and the transform origin
        # and rotation pivot are unaffected.
        return rect.adjusted(0, -bulge, 0, bulge)

    def curved_bulge(self) -> float:
        if self._curved_bulge is None:
            self._curved_bulge = max(
                (arc_bulge(line.placements, line.metrics_height) for line in self.curved_lines()),
                default=0.0,
            )
        return self._curved_bulge

    def curved_font(self) -> QFont:
        """The font the curved glyphs are drawn in.

        The document's own default, so bending the text cannot change its size
        — the item's `font_size` attribute is only a copy of it and the two can
        be a step apart mid-resize. Bold, italic and underline are merged onto
        ranges rather than the default, so those come off the item.
        """
        font = QFont(self.document().defaultFont())
        font.setBold(self.bold)
        font.setItalic(self.italic)
        font.setUnderline(self.underline)
        return font

    @dataclass
    class CurvedLine:
        text: str
        advances: list
        placements: list
        origin: QPointF
        metrics_height: float

    def curved_lines(self) -> list:
        """One arc per line of text, stacked and centred in the text rect."""
        font = self.curved_font()
        metrics = QFontMetricsF(font)
        rect = self.text_rect()
        line_height = metrics.height() * float(self.line_spacing or 1.0)
        lines = self.toPlainText().split('\n')

        top = rect.center().y() - line_height * len(lines) / 2.0
        result = []
        for index, text in enumerate(lines):
            advances = [
                metrics.horizontalAdvance(character) + self.letter_spacing
                for character in text
            ]
            result.append(self.CurvedLine(
                text=text,
                advances=advances,
                placements=arc_placements(advances, self.curvature),
                origin=QPointF(
                    rect.center().x(),
                    top + line_height * index + metrics.ascent(),
                ),
                metrics_height=metrics.height(),
            ))
        return result

    def curved_path(self) -> QPainterPath:
        """Every curved glyph as one path, in the item's own coordinates.

        Turning the glyphs into outlines and baking each one's rotation into
        the path — rather than rotating the painter and drawing text — is what
        lets the whole thing be filled in a single pass. A gradient brush
        resolves against whatever transform the painter is under, so a glyph
        drawn under its own rotation samples the gradient at its own origin and
        every letter comes out the same colour.
        """
        path = QPainterPath()
        font = self.curved_font()
        for line in self.curved_lines():
            for character, advance, placement in zip(line.text, line.advances, line.placements):
                if not character.strip():
                    continue
                glyph = QPainterPath()
                glyph.addText(-advance / 2.0, 0.0, font, character)
                transform = QTransform()
                transform.translate(
                    line.origin.x() + placement.x,
                    line.origin.y() + placement.y,
                )
                transform.rotate(placement.angle)
                path.addPath(transform.map(glyph))
        return path

    def paint_curved(self, painter: QPainter):
        """Draw the text along its arc.

        Qt lays a document out on straight lines, so there is no document to
        draw through here. That costs the per-range character formats — a curved
        item is one font, one fill, one outline — which is what curved lettering
        is in practice.
        """
        path = self.curved_path()
        if path.isEmpty():
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if self.outline and self.outline_color and self.outline_width:
            # Stroked down the middle of the outline, so half of it lands
            # outside the glyph — the same reach as the width the flat path
            # displaces its copies by.
            pen = QPen(QColor(self.outline_color), float(self.outline_width) * 2.0)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.strokePath(path, pen)

        painter.fillPath(path, self.fill_brush())
        painter.restore()

    def set_font_size(self, font_size):
        font_size = max(1, font_size)
        if not self.textCursor().hasSelection():
            self.font_size = font_size
        self.update_text_format('size', font_size)

    def update_text_width(self):
        width = self.document().size().width()
        self.setTextWidth(width)

    def set_alignment(self, alignment):
        if not self.textCursor().hasSelection():
            self.alignment = alignment
        self.update_alignment(alignment)

    def update_alignment(self, alignment):
        cursor = self.textCursor()
        has_selection = cursor.hasSelection()
        block_format = cursor.blockFormat()
        block_format.setAlignment(alignment)

        if has_selection:
            cursor.beginEditBlock()
            start, end = cursor.selectionStart(), cursor.selectionEnd()
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            cursor.mergeBlockFormat(block_format)
            cursor.endEditBlock()
        else:
            doc = self.document()
            cursor = QTextCursor(doc)
            cursor.select(QTextCursor.SelectionType.Document)
            cursor.mergeBlockFormat(block_format)

        self.update()

    def update_text_format(self, attribute, value):
        cursor = self.textCursor()
        has_selection = cursor.hasSelection()

        format_operations = {
            'color': lambda cf, v: cf.setForeground(v),
            'font': lambda cf, v: cf.setFont(v),
            'size': lambda cf, v: cf.setFontPointSize(v),
            'bold': lambda cf, v: cf.setFontWeight(QFont.Bold if v else QFont.Normal),
            'italic': lambda cf, v: cf.setFontItalic(v),
            'underline': lambda cf, v: cf.setFontUnderline(v),
        }

        if attribute not in format_operations:
            print(f"Unsupported attribute: {attribute}")
            return

        char_format = QTextCharFormat()
        format_operations[attribute](char_format, value)

        # Only merge if we have a selection OR if it's not a color change on HTML.
        # This prevents clobbering inline span colors during project loading/global changes.
        is_global_html_color = not has_selection and attribute == 'color' and self.is_html(self.toHtml())
        
        if not is_global_html_color:
            if not has_selection:
                cursor.select(QTextCursor.SelectionType.Document)    
            cursor.mergeCharFormat(char_format)

        # Update the document's default format only if there is no selection
        if not has_selection:
            doc_format = self.document().defaultTextOption()
            if attribute == 'color':
                self.setDefaultTextColor(value)
            elif attribute == 'font':
                self.document().setDefaultFont(value)
            elif attribute == 'size':
                font = self.document().defaultFont()
                font.setPointSize(value)
                self.document().setDefaultFont(font)
            self.document().setDefaultTextOption(doc_format)
        
        # Clear the selection by moving the cursor to the end of the document
        cursor.clearSelection()
        cursor.movePosition(QTextCursor.End)

        self.setTextCursor(cursor)
        self.update()

    def set_line_spacing(self, spacing):
        self.line_spacing = spacing
        doc = self.document()
        cursor = QTextCursor(doc)
        cursor.select(QTextCursor.SelectionType.Document)
        block_format = QTextBlockFormat()
        spacing = spacing * 100
        spacing = float(spacing)
        block_format.setLineHeight(spacing, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor.mergeBlockFormat(block_format)

    def set_color(self, color):
        if not self.textCursor().hasSelection():
            self.text_color = color
        self.update_text_format('color', color)

    def update_outlines(self):
        """Update the selection outlines when text changes"""
        if self.outline:
            # Create an outline for the entire document
            doc = self.document()
            char_count = doc.characterCount()
            
            # Create an outline info for the entire document
            new_outline = OutlineInfo(  
                start = 0,
                end = max(0, char_count - 1),
                color = self.outline_color,  
                width = self.outline_width,
                type = OutlineType.Full_Document
            )
            
            # Remove any existing full document outline
            self.selection_outlines = [outline for outline in self.selection_outlines 
                                     if outline.type != OutlineType.Full_Document]
            # Add the new one
            self.selection_outlines.append(new_outline)
        else:
            # Remove only the full document outline
            self.selection_outlines = [outline for outline in self.selection_outlines 
                                     if outline.type != OutlineType.Full_Document]

        self.update() 

    def set_outline(self, outline_color, outline_width):
        # Initialize start and end variables
        start = 0
        end = 0

        if self.textCursor().hasSelection():
            # Store outline properties for the current selection
            start = self.textCursor().selectionStart()
            end = self.textCursor().selectionEnd()
        else:
            # Set global outline properties only when there's no selection
            self.outline = True if outline_color else False

            if self.outline:
                # enabling global outline: store color/width and target whole document
                self.outline_color = outline_color
                self.outline_width = outline_width

                char_count = self.document().characterCount()
                start = 0
                end = max(0, char_count - 1)

        # When disabling outlines (outline_color is falsy), remove the relevant outlines
        if not outline_color:
            if self.textCursor().hasSelection():
                # Remove any outlines that contain the current selection range
                self.selection_outlines = [
                    outline for outline in self.selection_outlines
                    if not (outline.start <= start and outline.end >= end)
                ]
            else:
                # No selection: remove only full-document outlines
                self.selection_outlines = [
                    outline for outline in self.selection_outlines
                    if outline.type != OutlineType.Full_Document
                ]
        else:
            # Adding/updating an outline for the selection or whole document
            type = OutlineType.Selection if self.textCursor().hasSelection() else OutlineType.Full_Document

            # Remove any existing outline for this exact selection range
            self.selection_outlines = [
                outline for outline in self.selection_outlines 
                if not (outline.start == start and outline.end == end)
            ]

            # Add new outline info
            self.selection_outlines.append(
                OutlineInfo(start, end, outline_color, outline_width, type)
            )
        
        self.update()

    def paint(   
        self, 
        painter: QPainter, 
        option: QStyleOptionGraphicsItem, 
        widget: QWidget = None
    ):

        if self.curvature:
            self.paint_curved(painter)
            if self.selected:
                handles.paint_handles(
                    painter, self.text_rect(), handles.item_view_scale(self, option, painter)
                )
            return

        # Skia draws the whole item in one pass — fill, outlines and shadow —
        # so it replaces everything below rather than layering with it. Curved
        # text is handled above and stays on the Qt path: the arc is baked into
        # a QPainterPath and has no Skia equivalent yet.
        if text_engine.paints_with_skia() and skia_paint.paint_item(painter, self):
            if self.selected:
                handles.paint_handles(
                    painter, self.text_rect(), handles.item_view_scale(self, option, painter)
                )
            return

        # Outline pass: draw the glyphs underneath, displaced around a circle, so
        # their union is the glyph dilated by the outline width. The fill is then
        # painted on top and stays intact.
        if self.selection_outlines:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            for width, doc in self._outline_documents():
                for dx, dy in self._outline_offsets(width):
                    painter.save()
                    painter.translate(dx, dy)
                    doc.drawContents(painter)
                    painter.restore()
            painter.restore()

        # Draw the normal text on top
        super().paint(painter, option, widget)

        if self.selected:
            handles.paint_handles(
                painter, self.text_rect(), handles.item_view_scale(self, option, painter)
            )

    # Enough displacements that the gap left between neighbours, width*(1-cos(pi/n)),
    # stays under 2% of the outline width — well below a pixel at any usable size.
    OUTLINE_SAMPLES = 16

    @classmethod
    def _outline_offsets(cls, width: float) -> list[tuple[float, float]]:
        step = 2 * math.pi / cls.OUTLINE_SAMPLES
        return [
            (width * math.cos(i * step), width * math.sin(i * step))
            for i in range(cls.OUTLINE_SAMPLES)
        ]

    def _outline_documents(self):
        """Yield (width, document) for each distinct outline width in use.

        Ranges sharing a width are drawn together since they are displaced by the
        same amount. Everything outside the ranges is made transparent so only the
        outlined text contributes. Drawing through a copy of the document — rather
        than re-laying the text out — keeps word wrap, per-range character formats
        and the vertical CJK layout intact.
        """
        # Widest first, so a narrower outline layered on top stays visible.
        widths = sorted({outline_info.width for outline_info in self.selection_outlines},
                        reverse=True)

        for width in widths:
            doc = self._clone_outline_document()

            cursor = QTextCursor(doc)
            cursor.select(QTextCursor.SelectionType.Document)
            hidden = QTextCharFormat()
            hidden.setForeground(QColor(0, 0, 0, 0))
            cursor.mergeCharFormat(hidden)

            for outline_info in self.selection_outlines:
                if outline_info.width != width:
                    continue
                fmt = QTextCharFormat()
                fmt.setForeground(to_qt_color(outline_info.color))
                cursor.setPosition(outline_info.start)
                cursor.setPosition(outline_info.end, QTextCursor.KeepAnchor)
                cursor.mergeCharFormat(fmt)

            yield width, doc

    def _clone_outline_document(self):
        source = self.document()
        doc = source.clone()
        doc.setDocumentMargin(source.documentMargin())
        doc.setDefaultFont(source.defaultFont())
        doc.setDefaultTextOption(source.defaultTextOption())
        doc.setPageSize(source.pageSize())
        doc.setTextWidth(source.textWidth())

        if self.vertical and self.layout:
            vertical_layout = VerticalTextDocumentLayout(
                document=doc,
                line_spacing=self.layout.line_spacing,
            )
            doc.setDocumentLayout(vertical_layout)
            vertical_layout.set_max_size(self.layout.max_width, self.layout.max_height)

        return doc

    def set_bold(self, state):
        if not self.textCursor().hasSelection():
            self.bold = state
        self.update_text_format('bold', state)

    def set_italic(self, state):
        if not self.textCursor().hasSelection():
            self.italic = state
        self.update_text_format('italic', state)

    def set_underline(self, state):
        if not self.textCursor().hasSelection():
            self.underline = state
        self.update_text_format('underline', state)

    def apply_all_attributes(self):
        self.set_font(self.font_family, self.font_size)
        self.set_color(self.text_color)
        self.set_outline(self.outline_color, self.outline_width)
        self.set_bold(self.bold)
        self.set_italic(self.italic)
        self.set_underline(self.underline)
        self.set_line_spacing(self.line_spacing)
        self.update_text_width()
        self.set_alignment(self.alignment)

    def mouseDoubleClickEvent(self, event):
        if not self.editing_mode:
            self.enter_editing_mode()
            if self.layout:
                hit = self.layout.hitTest(event.pos(), None)
                cursor = self.textCursor()
                cursor.setPosition(hit)
                self.setTextCursor(cursor)
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        # Handle single clicks in editing mode for vertical text
        if self.editing_mode and self.layout and event.button() == Qt.MouseButton.LeftButton:
            hit = self.layout.hitTest(event.pos(), None)
            cursor = self.textCursor()
            
            # Check if shift is pressed for selection
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._drag_select_anchor = cursor.anchor()
                cursor.setPosition(hit, QTextCursor.MoveMode.KeepAnchor)
            else:
                cursor.setPosition(hit)
                self._drag_select_anchor = hit
            
            self._drag_selecting = True
            self.setTextCursor(cursor)
            self.setFocus()
            event.accept()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event):

        if self.editing_mode and self.vertical:
            key = event.key()
            modifiers = event.modifiers()
            
            if key == Qt.Key.Key_Down:
                # Down arrow in vertical text = move to next character
                cursor = self.textCursor()
                move_mode = QTextCursor.MoveMode.KeepAnchor if (modifiers & Qt.KeyboardModifier.ShiftModifier) else QTextCursor.MoveMode.MoveAnchor
                cursor.movePosition(QTextCursor.MoveOperation.NextCharacter, move_mode)
                self.setTextCursor(cursor)
                event.accept()
                return
            elif key == Qt.Key.Key_Up:
                # Up arrow in vertical text = move to previous character
                cursor = self.textCursor()
                move_mode = QTextCursor.MoveMode.KeepAnchor if (modifiers & Qt.KeyboardModifier.ShiftModifier) else QTextCursor.MoveMode.MoveAnchor
                cursor.movePosition(QTextCursor.MoveOperation.PreviousCharacter, move_mode)
                self.setTextCursor(cursor)
                event.accept()
                return
            elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and not (
                modifiers & (
                    Qt.KeyboardModifier.ControlModifier
                    | Qt.KeyboardModifier.AltModifier
                    | Qt.KeyboardModifier.MetaModifier
                )
            ):
                # Left/Right arrow in vertical text = move between paragraphs (visual columns),
                # keeping the same in-block offset when possible.
                cursor = self.textCursor()
                move_mode = QTextCursor.MoveMode.KeepAnchor if (modifiers & Qt.KeyboardModifier.ShiftModifier) else QTextCursor.MoveMode.MoveAnchor

                # Prefer layout-aware movement (handles wrapped columns).
                if self.layout and hasattr(self.layout, "move_cursor_between_columns"):
                    column_delta = 1 if key == Qt.Key.Key_Left else -1
                    new_pos = self.layout.move_cursor_between_columns(cursor.position(), column_delta)
                    if new_pos is not None and new_pos != cursor.position():
                        cursor.setPosition(new_pos, move_mode)
                        self.setTextCursor(cursor)
                        event.accept()
                        return

                # Fallback: treat each QTextBlock as a vertical "line" and move between them.
                block = cursor.block()
                target_block = block.next() if key == Qt.Key.Key_Left else block.previous()
                if target_block.isValid():
                    offset_in_block = cursor.position() - block.position()
                    target_offset = min(offset_in_block, max(0, target_block.length() - 1))
                    new_pos = target_block.position() + target_offset
                    if new_pos != cursor.position():
                        cursor.setPosition(new_pos, move_mode)
                        self.setTextCursor(cursor)
                        event.accept()
                        return
            
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                # Use the current character format as the new block's char format so empty paragraphs
                # keep the same font metrics (Qt can otherwise fall back to a tiny default).
                # Currently only necessary for vertical text layouts.
                cursor = self.textCursor()
                inherited_char_format = QTextCharFormat(cursor.charFormat())
                inherited_block_format = cursor.blockFormat()
                inherited_block_char_format = QTextCharFormat(inherited_char_format)

                # Ensure we always carry a valid point size/font for layout metrics.
                if inherited_block_char_format.fontPointSize() <= 0:
                    inherited_block_char_format.setFontPointSize(max(1, float(self.font_size)))
                font = inherited_block_char_format.font()
                if font.pointSizeF() <= 0:
                    font = self.document().defaultFont()
                    if font.pointSizeF() <= 0:
                        font.setPointSizeF(max(1.0, float(self.font_size)))
                    inherited_block_char_format.setFont(font)

                cursor.beginEditBlock()
                if cursor.hasSelection():
                    cursor.removeSelectedText()

                # Create a new paragraph that keeps the current paragraph + char formatting.
                cursor.insertBlock(inherited_block_format, inherited_block_char_format)
                cursor.setCharFormat(inherited_char_format)

                cursor.endEditBlock()
                self.setTextCursor(cursor)
                event.accept()
                return
        
        # Default handling for all other cases
        super().keyPressEvent(event)

    def enter_editing_mode(self):
        self.editing_mode = True
        self.setCacheMode(QGraphicsItem.CacheMode.NoCache)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setFlag(QGraphicsTextItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setCursor(QCursor(Qt.CursorShape.IBeamCursor))
        self.setFocus()

    def exit_editing_mode(self):
        self.editing_mode = False
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setFlag(QGraphicsTextItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.clearFocus()

    def _on_text_changed(self):
        new_text = self.toPlainText()
        self._curved_bulge = None
        if self.curvature:
            self.prepareGeometryChange()
        self.text_changed.emit(new_text)
        self.update_outlines()
        if self.gradient_enabled:
            # The gradient spans the laid-out document, so it has to be
            # rebuilt whenever the text that document holds changes size.
            self.apply_gradient()

    def mouseMoveEvent(self, event):
        # Resize/rotate/move logic is now handled by EventHandler and QGraphicsView
        if self.editing_mode and self.layout and (event.buttons() & Qt.MouseButton.LeftButton) and self._drag_selecting:
            hit = self.layout.hitTest(event.pos(), None)
            anchor = self._drag_select_anchor
            if anchor is None:
                anchor = self.textCursor().anchor()

            cursor = self.textCursor()
            cursor.setPosition(anchor)
            cursor.setPosition(hit, QTextCursor.MoveMode.KeepAnchor)
            self.setTextCursor(cursor)
            event.accept()
            return

        if self.editing_mode:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.editing_mode and self.layout and event.button() == Qt.MouseButton.LeftButton:
            self._drag_selecting = False
            self._drag_select_anchor = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        super().contextMenuEvent(event)
        if self.editing_mode:
            self.enter_editing_mode()
    
    def handleDeselection(self):
        if self.selected:
            self.setSelected(False)
            self.selected = False
            self.item_deselected.emit()
            self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            if self.editing_mode:
                self.exit_editing_mode()
            self.update()

    def init_resize(self, scene_pos: QPointF):
        self.resizing = True
        self.resize_start = scene_pos

    def init_rotation(self, scene_pos):
        self.rotating = True
        center = self.boundingRect().center()
        self.center_scene_pos = self.mapToScene(center)
        self.last_rotation_angle = math.degrees(math.atan2(
            scene_pos.y() - self.center_scene_pos.y(),
            scene_pos.x() - self.center_scene_pos.x()
        ))

    def move_item(self, local_pos: QPointF, last_local_pos: QPointF):
        delta = self.mapToParent(local_pos) - self.mapToParent(last_local_pos)
        new_pos = self.pos() + delta
        
        # Calculate the bounding rect of the rotated rectangle in scene coordinates
        scene_rect = self.mapToScene(self.boundingRect())
        bounding_rect = scene_rect.boundingRect()
        
        # Get constraint bounds
        parent_rect = None
        
        # Check if we're in webtoon mode by looking for the lazy webtoon manager
        scene = self.scene()
        if scene and scene.views():
            parent_rect = scene.sceneRect()
        
        # Constrain the movement
        if bounding_rect.left() + delta.x() < parent_rect.left():
            new_pos.setX(self.pos().x() - (bounding_rect.left() - parent_rect.left()))
        elif bounding_rect.right() + delta.x() > parent_rect.right():
            new_pos.setX(self.pos().x() + parent_rect.right() - bounding_rect.right())
        
        if bounding_rect.top() + delta.y() < parent_rect.top():
            new_pos.setY(self.pos().y() - (bounding_rect.top() - parent_rect.top()))
        elif bounding_rect.bottom() + delta.y() > parent_rect.bottom():
            new_pos.setY(self.pos().y() + parent_rect.bottom() - bounding_rect.bottom())
        
        self.setPos(new_pos)

    def rotate_item(self, scene_pos):
        self.setTransformOriginPoint(self.boundingRect().center())
        current_angle = math.degrees(math.atan2(
            scene_pos.y() - self.center_scene_pos.y(),
            scene_pos.x() - self.center_scene_pos.x()
        ))
        
        angle_diff = current_angle - self.last_rotation_angle
        
        if angle_diff > 180:
            angle_diff -= 360
        elif angle_diff < -180:
            angle_diff += 360
        
        smoothed_angle = angle_diff / self.rotation_smoothing
        
        new_rotation = self.rotation() + smoothed_angle
        self.setRotation(new_rotation)
        self.last_rotation_angle = current_angle

    def resize_item(self, scene_pos: QPointF):
        if not self.resize_start:
            return

        # Calculate delta from start position in scene coordinates
        scene_start = self.resize_start
        scene_delta = scene_pos - scene_start

        # Counter-rotate the delta to align it with the item's unrotated coordinate system
        angle_rad = math.radians(-self.rotation())
        rotated_delta_x = scene_delta.x() * math.cos(angle_rad) - scene_delta.y() * math.sin(angle_rad)
        rotated_delta_y = scene_delta.x() * math.sin(angle_rad) + scene_delta.y() * math.cos(angle_rad)
        rotated_delta = QPointF(rotated_delta_x, rotated_delta_y)

        # Get the current rect and create a new one to modify
        rect = self.text_rect()
        new_rect = QRectF(rect)
        original_height = rect.height()

        # Apply the delta based on which handle is being dragged
        if self.resize_handle in ['left', 'top_left', 'bottom_left']:
            new_rect.setLeft(rect.left() + rotated_delta.x())
        if self.resize_handle in ['right', 'top_right', 'bottom_right']:
            new_rect.setRight(rect.right() + rotated_delta.x())
        if self.resize_handle in ['top', 'top_left', 'top_right']:
            new_rect.setTop(rect.top() + rotated_delta.y())
        if self.resize_handle in ['bottom', 'bottom_left', 'bottom_right']:
            new_rect.setBottom(rect.bottom() + rotated_delta.y())

        # Ensure minimum size
        min_size = 10
        if new_rect.width() < min_size:
            if 'left' in self.resize_handle: new_rect.setLeft(new_rect.right() - min_size)
            else: new_rect.setRight(new_rect.left() + min_size)
        if new_rect.height() < min_size:
            if 'top' in self.resize_handle: new_rect.setTop(new_rect.bottom() - min_size)
            else: new_rect.setBottom(new_rect.top() + min_size)

        # Determine constraint bounds
        constraint_rect = None
        scene = self.scene()
        
        if scene and scene.views():
            constraint_rect = scene.sceneRect()
        
        if constraint_rect:
            # Map the proposed new local rect to the scene to get its final footprint
            prospective_scene_rect = self.mapRectToScene(new_rect)

            # Check if the resize would push the item outside the constraint bounds
            if (prospective_scene_rect.left() < constraint_rect.left() or
                prospective_scene_rect.right() > constraint_rect.right() or
                prospective_scene_rect.top() < constraint_rect.top() or
                prospective_scene_rect.bottom() > constraint_rect.bottom()):
                return  # Abort the resize operation

        # Calculate the required shift in the parent's coordinate system.
        pos_delta = self.mapToParent(new_rect.topLeft()) - self.mapToParent(rect.topLeft())
        new_pos = self.pos() + pos_delta

        self.setPos(new_pos)

        if self.vertical:
            if self.layout:
                self.layout.set_max_size(new_rect.width(), new_rect.height())
        else: # Horizontal logic
            self.setTextWidth(new_rect.width())
            if original_height > 0:
                height_ratio = new_rect.height() / original_height
                if height_ratio > 0:
                    new_font_size = self.font_size * height_ratio
                    # Ensure minimum font size of 1pt.
                    if new_font_size >= 1:
                        self.font_size = new_font_size
                        self.set_font_size(new_font_size)
                    else:
                        # If font would become invalid, stop the resize.
                        return

        self.resize_start = scene_pos

    def on_selection_changed(self):
        cursor = self.textCursor()
        properties = self.get_selected_text_properties(cursor)
        if self.editing_mode:
            self.text_highlighted.emit(properties)

    def get_selected_text_properties(self, cursor: QTextCursor):
        if not cursor.hasSelection():
            return {
                'font_family': self.font_family,
                'font_size': self.font_size,
                'bold': False,
                'italic': False,
                'underline': False,
                'text_color': self.text_color.name(),
                'alignment': self.alignment,
                'outline': self.outline,
                'outline_color': self.outline_color.name() if self.outline_color else None,
                'outline_width': self.outline_width,
            }

        start = cursor.selectionStart()
        end = cursor.selectionEnd()

        # Find all selections that completely contain the current selection
        containing_outlines = [
            outline for outline in self.selection_outlines
            if outline.start <= start and outline.end >= end
        ]

        # Get outline properties from the last (most recent) containing selection
        outline_properties = None
        if containing_outlines:
            latest_outline = containing_outlines[-1]  # Get the last one from the list
            outline_properties = {
                'outline': True,
                'outline_color': to_qt_color(latest_outline.color).name(),
                'outline_width': latest_outline.width
            }
        else:
            outline_properties = {
                'outline': False,
                'outline_color': None,
                'outline_width': None
            }

        # Create a new cursor for traversing the selection
        format_cursor = QTextCursor(cursor)

        # Initialize properties with default values
        properties = {
            'font_family': set(),
            'font_size': set(),
            'bold': True,
            'italic': True,
            'underline': True,
            'text_color': set(),
            'alignment': None,
        }

        # Get initial block format for alignment
        format_cursor.setPosition(start)
        properties['alignment'] = format_cursor.blockFormat().alignment()

        # Iterate through the selection one character at a time
        for pos in range(start, end):
            format_cursor.setPosition(pos)
            format_cursor.setPosition(pos + 1, QTextCursor.KeepAnchor)
            char_format = format_cursor.charFormat()

            # Update properties
            properties['font_family'].add(char_format.font().family())
            properties['font_size'].add(char_format.fontPointSize())
            properties['bold'] &= char_format.font().bold()
            properties['italic'] &= char_format.font().italic()
            properties['underline'] &= char_format.font().underline()
            properties['text_color'].add(char_format.foreground().color().name())

        # Convert sets to single values if all elements are the same, otherwise set to None
        for key, value in properties.items():
            if isinstance(value, set):
                properties[key] = list(value)[0] if len(value) == 1 else None

        # Merge outline properties with other properties
        properties.update(outline_properties)

        return properties
    
    def __copy__(self):
        cls = self.__class__
        new_instance = cls(
            text=self.toHtml(),
            font_family=self.font_family,
            font_size=self.font_size,
            render_color=self.text_color,
            alignment=self.alignment,
            line_spacing=self.line_spacing,
            outline_color=self.outline_color,
            outline_width=self.outline_width,
            bold=self.bold,
            italic=self.italic,
            underline=self.underline
        )
        
        new_instance.set_text(self.toHtml(), self.text_rect().width())
        new_instance.setTransformOriginPoint(self.transformOriginPoint())
        new_instance.setPos(self.pos())
        new_instance.setRotation(self.rotation())
        new_instance.setScale(self.scale())
        new_instance.__dict__.update(copy.copy(self.__dict__))
        return new_instance

