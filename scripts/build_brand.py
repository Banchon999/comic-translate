"""Render the Toon Studio brand assets into resources/icons/.

The mark is a speech bubble with two eyes, in the theme's accent. From that
one drawing this writes the window/app icon (icon.ico for Windows and Linux,
icon.icns for macOS, icon.png) and the splash image, so the brand changes in
one place:

    python scripts/build_brand.py

Needs PySide6 (rendering, fonts) and Pillow (ico/icns containers) — both are
already app dependencies. The fonts come from resources/fonts/ui, so the
splash's wordmark is set in the same face the app uses for headings.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from core import theme_tokens as tt  # noqa: E402

ICONS = REPO / "resources" / "icons"
FONT_DIR = REPO / "resources" / "fonts" / "ui"

ACCENT = tt.DARK["accent"]
INK = tt.DARK["on_accent"]


def mark_svg(accent: str = ACCENT, ink: str = INK) -> str:
    """The bubble mark on a 24-unit grid."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
        f'<path fill="{accent}" d="M3.5 6.5a3 3 0 0 1 3-3h11a3 3 0 0 1 3 3v8a3 3 0 0 1-3 3h-6.2l-4.3 3.6v-3.6h-.5a3 3 0 0 1-3-3z"/>'
        f'<ellipse fill="{ink}" cx="9.5" cy="10.4" rx="1.25" ry="1.6"/>'
        f'<ellipse fill="{ink}" cx="14.5" cy="10.4" rx="1.25" ry="1.6"/>'
        "</svg>\n"
    )


def _qt():
    from PySide6 import QtCore, QtGui, QtSvg, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["build_brand", "-platform", "offscreen"])
    return app, QtCore, QtGui, QtSvg


def render_icon(size: int):
    """The app icon: the mark on a rounded panel-coloured tile, as a QImage."""
    _, QtCore, QtGui, QtSvg = _qt()
    image = QtGui.QImage(size, size, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.setBrush(QtGui.QColor(tt.DARK["panel"]))
    radius = size * 0.22
    painter.drawRoundedRect(QtCore.QRectF(0, 0, size, size), radius, radius)
    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(mark_svg().encode()))
    inset = size * 0.12
    renderer.render(painter, QtCore.QRectF(inset, inset + size * 0.02, size - 2 * inset, size - 2 * inset))
    painter.end()
    return image


def _to_pil(qimage):
    from PIL import Image
    from PySide6 import QtCore

    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    qimage.save(buffer, "PNG")
    return Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGBA")


def render_splash(width: int = 1200, height: int = 675):
    """The splash: mark, wordmark and tagline on the ground colour."""
    _, QtCore, QtGui, QtSvg = _qt()
    for name in tt.UI_FONT_FILES:
        QtGui.QFontDatabase.addApplicationFont(str(FONT_DIR / name))
    image = QtGui.QImage(width, height, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtGui.QColor(tt.DARK["ground"]))
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)

    mark = height * 0.36
    left = width * 0.12
    top = (height - mark) / 2 - height * 0.02
    QtSvg.QSvgRenderer(QtCore.QByteArray(mark_svg().encode())).render(
        painter, QtCore.QRectF(left, top, mark, mark)
    )

    text_left = left + mark + width * 0.04
    display = QtGui.QFont(tt.DISPLAY_FONT_FAMILY)
    display.setPixelSize(int(height * 0.19))
    display.setWeight(QtGui.QFont.Weight.ExtraBold)
    display.setLetterSpacing(QtGui.QFont.SpacingType.PercentageSpacing, 97)
    painter.setFont(display)
    painter.setPen(QtGui.QColor(tt.DARK["text_1"]))
    metrics = QtGui.QFontMetrics(display)
    baseline = height / 2 + metrics.capHeight() * 0.35
    painter.drawText(QtCore.QPointF(text_left, baseline), tt.BRAND_NAME)

    body = QtGui.QFont(tt.UI_FONT_FAMILY)
    body.setPixelSize(int(height * 0.052))
    painter.setFont(body)
    painter.setPen(QtGui.QColor(tt.DARK["text_2"]))
    painter.drawText(
        QtCore.QPointF(text_left + height * 0.008, baseline + height * 0.11),
        "สตูดิโอแปลการ์ตูน มังงะ มังฮวา และเว็บตูน",
    )
    painter.end()
    return image


def main() -> int:
    ICONS.mkdir(parents=True, exist_ok=True)
    (ICONS / "toon-mark.svg").write_text(mark_svg(), encoding="utf-8")

    big = _to_pil(render_icon(1024))
    big.resize((256, 256), resample=3).save(ICONS / "icon.png")
    # Small sizes are drawn at their own size rather than downscaled from 1024,
    # so the eyes stay crisp at 16 px.
    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = {s: _to_pil(render_icon(s)) for s in ico_sizes}
    frames[256].save(ICONS / "icon.ico", sizes=[(s, s) for s in ico_sizes], append_images=[frames[s] for s in ico_sizes[:-1]])
    big.save(ICONS / "icon.icns")

    _to_pil(render_splash()).save(ICONS / "splash.png", optimize=True)
    print("wrote", ", ".join(p.name for p in sorted(ICONS.glob("*")) if p.name.startswith(("icon", "splash", "toon"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
