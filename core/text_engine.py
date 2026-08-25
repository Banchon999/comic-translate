"""Which text engine measures *and* paints.

One switch for both, deliberately. The failure this whole programme exists to
avoid is measuring with one engine and drawing with another: the auto-fit picks
a point size and line breaks from one set of metrics, the canvas draws with
another, and the preview stops matching the export. Two independent settings
make that state reachable, so there is one.

`QT` is the default. `SKIA` is opt-in until the Skia painter has been through
enough real pages to be trusted — `set_engine` refuses it when skia-python is
missing rather than silently half-switching.
"""

from __future__ import annotations

from typing import Literal

from core import skia_text
from core.text_measure import set_measurer

QT = "qt"
SKIA = "skia"

EngineName = Literal["qt", "skia"]

_engine: str = QT


def engine() -> str:
    """The active engine name."""
    return _engine


def paints_with_skia() -> bool:
    """True when `TextBlockItem` should draw through the Skia renderer."""
    return _engine == SKIA


def available_engines() -> tuple[str, ...]:
    return (QT, SKIA) if skia_text.is_available() else (QT,)


def set_engine(name: str) -> None:
    """Switch measurement and painting together.

    Raises rather than falling back: a caller that asked for Skia and silently
    got Qt would be told the pages it renders are Skia's, which is worse than
    an error at the point of the request.
    """
    global _engine

    if name == QT:
        _engine = QT
        set_measurer(None)  # restores the lazy Qt default
        return

    if name != SKIA:
        raise ValueError(f"unknown text engine {name!r}; expected one of {(QT, SKIA)}")

    if not skia_text.is_available():
        raise RuntimeError(
            f"cannot select the Skia text engine: {skia_text.unavailable_reason()}"
        )

    set_measurer(skia_text.SkiaTextMeasurer())
    _engine = SKIA
