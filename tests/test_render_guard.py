"""A crash inside a Skia render must not repeat forever.

The self-test proves the Skia *runtime* works. It cannot prove every path the
renderer takes is safe, so a fault somewhere the probe does not reach would
crash the app on every launch, with no way out for the user — the failure mode
that actually shipped.

The first Skia render of a session leaves a marker and removes it on the way
out. Finding it at startup means the previous run went into a render and never
came back, which is the only trace a native fault leaves: there is no
exception, and no exit path to record one from.
"""

import pytest

from core import render_guard, skia_text, text_engine
from core.text_measure import set_measurer


@pytest.fixture(autouse=True)
def _isolated_marker(tmp_path, monkeypatch):
    """Point the marker at a temp directory and reset the cached verdict."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(render_guard, "_distrusted", None)
    original = skia_text._self_test_result
    yield
    skia_text._self_test_result = original
    monkeypatch.setattr(render_guard, "_distrusted", None)
    text_engine.set_engine(text_engine.QT)
    set_measurer(None)


def test_a_clean_session_leaves_no_marker():
    render_guard.mark_render_started()
    render_guard.mark_render_finished()
    assert not render_guard.previous_run_crashed_rendering()


def test_a_render_that_never_returned_is_detected():
    """A started-but-never-finished render is exactly what a native crash leaves."""
    render_guard.mark_render_started()
    assert render_guard.previous_run_crashed_rendering()


def test_the_verdict_is_cached_so_it_cannot_flip_mid_page():
    """The marker is removed early in the session; the answer must not change."""
    render_guard.mark_render_started()
    assert render_guard.previous_run_crashed_rendering()
    render_guard.mark_render_finished()
    assert render_guard.previous_run_crashed_rendering(), (
        "the verdict changed once this session's marker was cleared"
    )


@pytest.mark.skipif(skia_text.skia is None, reason="skia-python unavailable")
def test_a_crashed_previous_run_falls_back_to_qt(qapp):
    """The whole point: the next launch does not repeat the crash."""
    assert text_engine.default_engine() == text_engine.SKIA, (
        "precondition failed — Skia should be the default on a healthy install"
    )

    render_guard.mark_render_started()          # as if the last run died here
    render_guard._distrusted = None             # a fresh process reads it anew

    assert text_engine.default_engine() == text_engine.QT


@pytest.mark.skipif(skia_text.skia is None, reason="skia-python unavailable")
def test_asking_for_skia_by_name_overrides_an_old_marker(qapp):
    """One crash must not disable the fast engine permanently.

    Otherwise the checkbox would read "on" while Qt quietly did the drawing,
    and the user would have no way to retry.
    """
    render_guard.mark_render_started()
    render_guard._distrusted = None
    assert text_engine.default_engine() == text_engine.QT

    text_engine.set_engine(text_engine.SKIA)

    assert text_engine.engine() == text_engine.SKIA
    render_guard._distrusted = None
    assert not render_guard.previous_run_crashed_rendering(), (
        "the marker survived an explicit request for Skia"
    )


def test_a_broken_runtime_still_wins_over_a_clean_marker(qapp):
    """The two guards are independent; a failed self-test is decisive on its own."""
    skia_text._self_test_result = "pretend the native runtime is broken"
    render_guard.mark_render_finished()
    assert text_engine.default_engine() == text_engine.QT


def test_marker_failures_are_never_fatal(monkeypatch):
    """Losing the guard must not stop the app; it is a safety net, not a feature."""
    monkeypatch.setattr(render_guard, "_marker_path", lambda: None)
    render_guard.mark_render_started()
    render_guard.mark_render_finished()
    monkeypatch.setattr(render_guard, "_distrusted", None)
    assert not render_guard.previous_run_crashed_rendering()


# ---------------------------------------------------------------------------
# Overlapping renders.
#
# Renders do overlap in this app: export runs `ImageSaveRenderer` on a
# `QThreadPool` worker while the canvas paints on the GUI thread. A guard that
# tracks "the first render" with a bare flag removes the marker as soon as
# whichever finishes first comes back — so a crash in the one still running
# leaves nothing behind, and the next launch walks into it again. That is the
# case the guard exists for, so it is the case it must not drop.
# ---------------------------------------------------------------------------


def test_an_overlapping_render_keeps_the_marker_until_both_return(tmp_path, monkeypatch):
    """The marker must outlive the *last* guarded render, not the first."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from app.ui.canvas import skia_paint

    monkeypatch.setattr(skia_paint, "_first_render_done", False)
    monkeypatch.setattr(skia_paint, "_guarded_renders", 0)

    marker = tmp_path / "ComicTranslate" / render_guard.MARKER_NAME

    assert skia_paint._enter_guarded_render()          # GUI thread starts
    assert skia_paint._enter_guarded_render()          # worker starts too
    assert marker.exists()

    skia_paint._leave_guarded_render()                 # GUI thread finishes
    assert marker.exists(), (
        "the marker was cleared while another render was still in Skia — a "
        "crash in that one would leave no trace for the next launch"
    )

    skia_paint._leave_guarded_render()                 # worker finishes
    assert not marker.exists()


def test_later_renders_are_not_guarded_again(tmp_path, monkeypatch):
    """Once a render has come back intact, the marker is not retaken."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from app.ui.canvas import skia_paint

    monkeypatch.setattr(skia_paint, "_first_render_done", False)
    monkeypatch.setattr(skia_paint, "_guarded_renders", 0)

    assert skia_paint._enter_guarded_render()
    skia_paint._leave_guarded_render()

    assert not skia_paint._enter_guarded_render(), (
        "a later render was guarded again, costing two disk writes per paint"
    )
