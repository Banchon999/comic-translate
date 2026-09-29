"""The bundled UI typeface (core/theme_tokens.UI_FONT_FAMILY).

Loaded once, right after the QApplication exists and before any widget is
styled. A missing or unreadable file is logged and skipped: the theme's
font-family list continues with the platform faces, so the app still draws
Thai — just not in the house face.
"""

from __future__ import annotations

import logging
import os

from PySide6.QtGui import QFontDatabase

from core import theme_tokens

logger = logging.getLogger(__name__)

# app/ui -> app -> repository root (also the bundle root in a frozen build,
# where resources/ is added next to app/).
UI_FONT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "resources", "fonts", "ui")
)

_loaded: list[str] | None = None


def load_ui_fonts(font_dir: str = UI_FONT_DIR) -> list[str]:
    """Register the UI font files with Qt; returns the families that loaded."""
    global _loaded
    if _loaded is not None and font_dir == UI_FONT_DIR:
        return _loaded
    families: list[str] = []
    for name in theme_tokens.UI_FONT_FILES:
        path = os.path.join(font_dir, name)
        if not os.path.isfile(path):
            logger.warning("UI font missing: %s", path)
            continue
        font_id = QFontDatabase.addApplicationFont(path)
        if font_id == -1:
            logger.warning("UI font could not be loaded: %s", path)
            continue
        for family in QFontDatabase.applicationFontFamilies(font_id):
            if family not in families:
                families.append(family)
    if font_dir == UI_FONT_DIR:
        _loaded = families
    return families
