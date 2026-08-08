from dataclasses import dataclass, field
from typing import Optional, List, Any
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt
from app.ui.canvas.text_item import OutlineType

@dataclass
class TextItemProperties:
    """Dataclass for TextBlockItem properties to reduce duplication in construction"""
    text: str = ""
    font_family: str = ""
    font_size: float = 20
    text_color: QColor = None
    alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignCenter
    line_spacing: float = 1.2
    outline_color: Optional[QColor] = None
    outline_width: float = 1
    outline: bool = False
    bold: bool = False
    italic: bool = False
    underline: bool = False
    direction: Qt.LayoutDirection = Qt.LayoutDirection.LeftToRight
    
    # Position and transformation properties
    position: tuple = (0, 0)  # (x, y)
    rotation: float = 0
    scale: float = 1.0
    transform_origin: Optional[tuple] = None  # (x, y)
    
    # Layout properties
    width: Optional[float] = None
    height: Optional[float] = None
    vertical: bool = False
    
    # Text effects
    letter_spacing: float = 0.0
    shadow_enabled: bool = False
    shadow_color: Optional[QColor] = None
    shadow_offset: tuple = (4.0, 4.0)
    shadow_blur: float = 0.0

    # Advanced properties
    selection_outlines: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> 'TextItemProperties':
        """Create TextItemProperties from dictionary state"""
        props = cls()
        
        # Basic text properties
        props.text = data.get('text', '')
        props.font_family = data.get('font_family', '')
        props.font_size = data.get('font_size', 20)
        props.line_spacing = data.get('line_spacing', 1.2)
        props.bold = data.get('bold', False)
        props.italic = data.get('italic', False)
        props.underline = data.get('underline', False)
        
        # Color properties
        if 'text_color' in data:
            if isinstance(data['text_color'], QColor):
                props.text_color = data['text_color']
            elif data['text_color'] is not None:
                props.text_color = QColor(data['text_color'])
        
        if 'outline_color' in data:
            if isinstance(data['outline_color'], QColor):
                props.outline_color = data['outline_color']
            elif data['outline_color']:
                props.outline_color = QColor(data['outline_color'])
                
        props.outline_width = data.get('outline_width', 1)
        if 'outline' in data:
            props.outline = bool(data.get('outline', False))
        else:
            props.outline = _has_full_document_outline(data.get('selection_outlines', []))
        
        # Alignment
        if 'alignment' in data:
            if isinstance(data['alignment'], int):
                props.alignment = Qt.AlignmentFlag(data['alignment'])
            else:
                props.alignment = data['alignment']
                
        # Direction – stored as Qt.LayoutDirection enum but may arrive as a plain
        # integer after JSON round-trips (RightToLeft=1, LeftToRight=0).
        if 'direction' in data:
            dir_val = data['direction']
            if isinstance(dir_val, int):
                try:
                    props.direction = Qt.LayoutDirection(dir_val)
                except (ValueError, KeyError):
                    props.direction = Qt.LayoutDirection.LeftToRight
            else:
                props.direction = dir_val
            
        # Position and transformation
        props.position = data.get('position', (0, 0))
        props.rotation = data.get('rotation', 0)
        props.scale = data.get('scale', 1.0)
        props.transform_origin = data.get('transform_origin')
        
        # Layout
        props.width = data.get('width')
        props.height = data.get('height')
        props.vertical = data.get('vertical', False)
        
        # Text effects
        props.letter_spacing = data.get('letter_spacing', 0.0)
        props.shadow_enabled = bool(data.get('shadow_enabled', False))
        if 'shadow_color' in data:
            if isinstance(data['shadow_color'], QColor):
                props.shadow_color = data['shadow_color']
            elif data['shadow_color']:
                props.shadow_color = QColor(data['shadow_color'])
        shadow_offset = data.get('shadow_offset')
        if shadow_offset:
            props.shadow_offset = (float(shadow_offset[0]), float(shadow_offset[1]))
        props.shadow_blur = float(data.get('shadow_blur', 0.0))

        # Advanced
        props.selection_outlines = data.get('selection_outlines', [])

        return props
    
    @classmethod
    def from_text_item(cls, item) -> 'TextItemProperties':
        """Create TextItemProperties from an existing TextBlockItem"""
        props = cls()
        
        # Basic text properties
        props.text = item.toHtml()
        props.font_family = item.font_family
        props.font_size = item.font_size
        props.text_color = item.text_color
        props.alignment = item.alignment
        props.line_spacing = item.line_spacing
        props.outline_color = item.outline_color
        props.outline_width = item.outline_width
        props.outline = bool(getattr(item, 'outline', False))
        props.bold = item.bold
        props.italic = item.italic
        props.underline = item.underline
        props.direction = item.direction
        
        # Position and transformation
        props.position = (item.pos().x(), item.pos().y())
        props.rotation = item.rotation()
        props.scale = item.scale()
        if hasattr(item, 'transformOriginPoint'):
            origin = item.transformOriginPoint()
            props.transform_origin = (origin.x(), origin.y())
        
        # Layout properties
        props.width = item.boundingRect().width()
        props.height = item.boundingRect().height()
        props.vertical = getattr(item, 'vertical', False)
        
        # Text effects
        props.letter_spacing = getattr(item, 'letter_spacing', 0.0)
        props.shadow_enabled = bool(getattr(item, 'shadow_enabled', False))
        props.shadow_color = getattr(item, 'shadow_color', None)
        props.shadow_offset = getattr(item, 'shadow_offset', (4.0, 4.0))
        props.shadow_blur = getattr(item, 'shadow_blur', 0.0)

        # Advanced properties
        props.selection_outlines = getattr(item, 'selection_outlines', []).copy()

        return props
    
    def to_dict(self) -> dict:
        """Convert TextItemProperties to dictionary"""
        return {
            'text': self.text,
            'font_family': self.font_family,
            'font_size': self.font_size,
            'text_color': self.text_color,
            'alignment': self.alignment,
            'line_spacing': self.line_spacing,
            'outline_color': self.outline_color,
            'outline_width': self.outline_width,
            'outline': self.outline,
            'bold': self.bold,
            'italic': self.italic,
            'underline': self.underline,
            'direction': self.direction,
            'position': self.position,
            'rotation': self.rotation,
            'scale': self.scale,
            'transform_origin': self.transform_origin,
            'width': self.width,
            'height': self.height,
            'vertical': self.vertical,
            'letter_spacing': self.letter_spacing,
            'shadow_enabled': self.shadow_enabled,
            'shadow_color': self.shadow_color,
            'shadow_offset': self.shadow_offset,
            'shadow_blur': self.shadow_blur,
            'selection_outlines': self.selection_outlines,
        }


def _has_full_document_outline(selection_outlines: list) -> bool:
    for outline in selection_outlines or []:
        outline_type = outline.get('type') if isinstance(outline, dict) else getattr(outline, 'type', None)
        if outline_type == OutlineType.Full_Document:
            return True
        if isinstance(outline_type, str) and outline_type.lower() == "full_document":
            return True
    return False
