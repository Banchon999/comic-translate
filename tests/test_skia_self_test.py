"""Skia has to prove it works before it is chosen, not while it is being used.

A frozen Windows build shipped with `skia*.pyd` present and closed itself the
instant Render was pressed, leaving no log. `import skia` had succeeded, so
`is_available()` returned True and Skia was selected as the default engine; the
fault came later, inside the native library.

That failure mode cannot be handled where it happens. A native access violation
is not a Python exception — `except Exception` never runs, the interpreter is
already gone — so the only defence is to rule it out *before* the engine is
chosen. `self_test()` does that by exercising what the render path really uses,
and in a frozen build it runs in a separate process so a fault takes the probe
down instead of the application.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from core import skia_text, text_engine
from core.text_measure import get_measurer, set_measurer

REPO_ROOT = Path(__file__).resolve().parent.parent

requires_skia = pytest.mark.skipif(
    skia_text.skia is None, reason="skia-python unavailable"
)


@pytest.fixture(autouse=True)
def _restore():
    """Never let a forced verdict leak into another test."""
    original = skia_text._self_test_result
    yield
    skia_text._self_test_result = original
    text_engine.set_engine(text_engine.QT)
    set_measurer(None)


@requires_skia
def test_a_healthy_install_passes_the_self_test():
    assert skia_text._run_self_test() is None


@requires_skia
def test_the_self_test_result_is_cached():
    """It builds and rasterises a paragraph — too expensive to repeat per call."""
    skia_text.self_test(force=True)
    first = skia_text._self_test_result
    skia_text.self_test()
    assert skia_text._self_test_result is first


def test_a_broken_runtime_is_not_reported_as_available():
    """The whole point: a runtime that imports but does not work is not available."""
    skia_text._self_test_result = "pretend the native runtime is broken"
    assert not skia_text.is_available()
    assert "broken" in skia_text.unavailable_reason()


def test_a_broken_runtime_falls_back_to_qt(qapp):
    """The engine, the engine list and the measurer must all agree on Qt."""
    skia_text._self_test_result = "pretend the native runtime is broken"

    assert text_engine.default_engine() == text_engine.QT
    assert text_engine.available_engines() == (text_engine.QT,)

    set_measurer(None)
    assert get_measurer().name == "qt", (
        "the measurer stayed on Skia while the painter fell back to Qt — "
        "measuring with one engine and drawing with another is the failure "
        "this seam exists to prevent"
    )


def test_a_broken_runtime_refuses_an_explicit_skia_request(qapp):
    """Selecting Skia by hand must fail loudly rather than half-switch."""
    skia_text._self_test_result = "pretend the native runtime is broken"
    with pytest.raises(RuntimeError, match="broken"):
        text_engine.set_engine(text_engine.SKIA)


@requires_skia
def test_the_self_test_flag_runs_the_probe_and_exits(qapp):
    """The entry point a frozen build re-invokes itself with.

    Run as a child process because that is exactly how it is used: the probe
    has to answer by exit code without starting the application, so a crash
    inside it is survivable.
    """
    result = subprocess.run(
        [sys.executable, "comic.py", skia_text.SELF_TEST_FLAG],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=REPO_ROOT,
        env={**__import__("os").environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert result.returncode == 0, (
        f"the self-test entry point failed: {result.stderr[-2000:]}"
    )


def test_the_probe_reports_a_failure_by_exit_code(monkeypatch):
    """A failing probe must exit non-zero, since that is all the parent sees."""
    import comic

    monkeypatch.setattr(skia_text, "_run_self_test", lambda: "something is wrong")
    assert comic._run_skia_self_test() == 1

    def explode():
        raise RuntimeError("the probe could not start")

    monkeypatch.setattr(skia_text, "_run_self_test", explode)
    assert comic._run_skia_self_test() == 2
