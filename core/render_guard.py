"""Do not start the run that killed the last one.

The Skia self-test proves the *runtime* works — a font manager builds, a
paragraph lays out, a surface rasterises. It cannot prove that every path the
renderer takes is safe, so a fault somewhere the probe does not reach would
still take the process down, and would do so again on the next launch, and the
next. A user whose machine happens to hit it has an application that crashes
forever with no way out.

So the first Skia render of each session leaves a marker on disk and removes it
when it returns. Finding that marker at startup means the previous run went
into a Skia render and never came back — which is the one thing a native fault
cannot tell us itself, because there is no exception and no exit path. That
session falls back to Qt.

Deliberately a plain file rather than QSettings: this has to be readable from
`core`, which stays Qt-free so the pipeline can run headlessly.

Two writes per session, not per render: the marker is only taken around the
*first* render, which is enough to catch a reproducible crash and costs nothing
on a page with forty text blocks.

The fallback is sticky for the session but not permanent — `clear()` lets the
user turn Skia back on and try again, so a one-off crash during a driver update
or an OOM does not disable the fast engine forever.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

MARKER_NAME = "skia-render-in-flight"

#: Set once a session has decided to distrust Skia, so the answer does not
#: change halfway through a page.
_distrusted: Optional[bool] = None


def _marker_path() -> Optional[str]:
    try:
        from modules.utils.paths import get_user_data_dir

        directory = get_user_data_dir()
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, MARKER_NAME)
    except Exception:
        return None


def previous_run_crashed_rendering() -> bool:
    """True when the last session entered a Skia render and never left it.

    Read once and cached: the marker is removed as soon as this session's first
    render completes, and the verdict must not flip mid-page.
    """
    global _distrusted
    if _distrusted is not None:
        return _distrusted

    path = _marker_path()
    _distrusted = bool(path and os.path.exists(path))
    if _distrusted:
        logger.warning(
            "the previous session did not return from a Skia render; using the "
            "Qt text engine for this session. Turn Skia back on in "
            "Settings > Tools once the cause is known."
        )
    return _distrusted


def mark_render_started() -> None:
    """Leave the marker. Called around the first Skia render of the session."""
    path = _marker_path()
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("a Skia render was in progress when this was written\n")
            handle.flush()
            # Forced to disk: the point is to survive a process that is about
            # to die without unwinding, so a buffered write would be lost.
            os.fsync(handle.fileno())
    except Exception:
        logger.debug("could not write the render guard marker", exc_info=True)


def mark_render_finished() -> None:
    """Remove the marker — this session got through a Skia render intact."""
    path = _marker_path()
    if not path:
        return
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except Exception:
        logger.debug("could not remove the render guard marker", exc_info=True)


def clear() -> None:
    """Forget the verdict and the marker, so Skia gets another chance.

    Called when the user turns Skia on themselves: an explicit request should
    not be silently overridden by a crash from some earlier session.
    """
    global _distrusted
    _distrusted = None
    mark_render_finished()
