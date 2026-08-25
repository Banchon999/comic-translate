"""Which text engine measures *and* paints.

One switch for both, deliberately. The failure this whole programme exists to
avoid is measuring with one engine and drawing with another: the auto-fit picks
a point size and line breaks from one set of metrics, the canvas draws with
another, and the preview stops matching the export. Two independent settings
make that state reachable, so there is one.

**Skia is the default where it is installed**, and Qt everywhere else.
`set_engine` refuses Skia when skia-python is missing rather than silently
half-switching, so the fallback is explicit rather than accidental.

The default lives in `default_engine()` and is a pure string — nothing is
constructed to answer it. `core.text_measure._default_measurer` reads
`engine()` to pick the matching measurer, which is what keeps the pair
together: a module that imports only `text_measure` and never touches this one
still measures with whatever is going to paint.
"""

from __future__ import annotations

from typing import Literal

from core import render_guard, skia_text
from core.text_measure import set_measurer

QT = "qt"
SKIA = "skia"

EngineName = Literal["qt", "skia"]

def default_engine() -> str:
    """Skia where it is installed and trusted, Qt otherwise.

    Two conditions, because there are two ways Skia can be unusable and only
    one of them is visible in advance. `is_available()` runs a self-test, which
    proves the runtime works. `render_guard` covers what a self-test cannot:
    a fault somewhere the probe does not reach, which would otherwise crash the
    application on every launch with no way out.
    """
    if not skia_text.is_available():
        return QT
    if render_guard.previous_run_crashed_rendering():
        return QT
    return SKIA


_engine: str = default_engine()


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

    if name not in (QT, SKIA):
        raise ValueError(f"unknown text engine {name!r}; expected one of {(QT, SKIA)}")

    if name == SKIA:
        if not skia_text.is_available():
            raise RuntimeError(
                f"cannot select the Skia text engine: {skia_text.unavailable_reason()}"
            )
        # Asking for Skia by name is an explicit decision and outranks a
        # marker left by some earlier session — otherwise one crash would
        # disable the fast engine permanently, with the checkbox ticked and
        # Qt quietly doing the drawing.
        render_guard.clear()

    # Record the choice first, then clear the measurer so the next call to
    # `get_measurer` re-resolves against it. Installing one here instead would
    # mean two places deciding what measures, and they could disagree — which
    # is the one failure this module exists to prevent.
    _engine = name
    set_measurer(None)
