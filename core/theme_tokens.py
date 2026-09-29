"""Toon Studio design tokens — the one place the app's colours are defined.

Qt-free on purpose: the dayu theme, the hand-styled widgets (home screen,
title bar, panels) and the tests all read the same values from here, so a
colour changed once changes everywhere, and a contrast rule can be checked
without a display.

Two palettes, one shape. ``palette(dark)`` returns the one in use; every
key exists in both, which ``tests/test_theme_tokens.py`` enforces.
"""

from __future__ import annotations

# Surfaces run dark to light: ground (behind the page) < panel (chrome) <
# raised (inputs, cards) < hover. Lines separate them. Text has three levels.
# One accent, used only for what is selected or primary; ``on_accent`` is the
# text drawn on top of it (dark ink — white on this pink is 3:1 and fails).
DARK = {
    "ground": "#0F1014",
    "panel": "#16181E",
    "raised": "#1F222A",
    "header": "#22252D",
    "hover": "#262A33",
    "pressed": "#2E323C",
    "line": "#2B2F39",
    "line_strong": "#3A3F4B",
    "text_1": "#EEEFF3",
    "text_2": "#A9ADB9",
    "text_3": "#8A8F9C",
    "disabled": "#5E6370",
    "accent": "#FF5C8A",
    "on_accent": "#22060F",
    "ok": "#3CCF8E",
    "warn": "#F2B13D",
    "info": "#6CB6FF",
    "danger": "#FF7A66",
    "toast": "#2E323C",
    "on_toast": "#EEEFF3",
}

LIGHT = {
    "ground": "#EEF0F4",
    "panel": "#FFFFFF",
    "raised": "#F6F7F9",
    "header": "#F0F1F4",
    "hover": "#E9EBEF",
    "pressed": "#DFE2E8",
    "line": "#DCDFE6",
    "line_strong": "#C3C7D0",
    "text_1": "#16181E",
    "text_2": "#4A4F5B",
    "text_3": "#626775",
    "disabled": "#A4A8B4",
    # A deeper pink than dark mode's: the dark-mode accent is 2.9:1 on white.
    "accent": "#CC2A5E",
    "on_accent": "#FFFFFF",
    "ok": "#17895A",
    "warn": "#A8650F",
    "info": "#2563C9",
    "danger": "#C8392A",
    "toast": "#16181E",
    "on_toast": "#EEEFF3",
}

RADIUS = {"small": 4, "base": 6, "large": 10, "card": 14}

# The UI typeface, bundled under resources/fonts/ui (SIL OFL 1.1). Covers Thai
# and Latin; Qt falls back per script for CJK and Hangul.
UI_FONT_FAMILY = "IBM Plex Sans Thai"
UI_FONT_FILES = (
    "IBMPlexSansThai-Regular.ttf",
    "IBMPlexSansThai-Medium.ttf",
    "IBMPlexSansThai-SemiBold.ttf",
    "IBMPlexSansThai-Bold.ttf",
)

# Pairs that must stay readable: (foreground, background, minimum ratio).
# 4.5:1 is WCAG AA for body text; 3:1 for large text and UI shapes.
CONTRAST_RULES = (
    ("text_1", "panel", 4.5), ("text_1", "raised", 4.5), ("text_1", "ground", 4.5),
    ("text_2", "panel", 4.5), ("text_2", "raised", 4.5), ("text_2", "ground", 4.5),
    ("text_3", "panel", 4.5), ("text_3", "raised", 4.5), ("text_3", "ground", 4.5),
    ("accent", "panel", 4.5), ("accent", "raised", 4.5),
    ("on_accent", "accent", 4.5),
    ("on_toast", "toast", 4.5),
    ("danger", "panel", 4.5), ("warn", "panel", 4.5), ("info", "panel", 4.5),
    ("ok", "panel", 3.0),
)


def palette(dark: bool = True) -> dict:
    """The token table for the current mode (a copy; callers may not edit it)."""
    return dict(DARK if dark else LIGHT)


def rgba(hex_color: str, alpha: float) -> str:
    """A QSS/CSS ``rgba()`` string for a token at the given opacity."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha:g})"


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    channels = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: str, bg: str) -> float:
    """WCAG contrast ratio between two hex colours."""
    a, b = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)
