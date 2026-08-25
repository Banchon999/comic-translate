"""Selecting the text engine.

One switch moves measurement and painting together. Two independent settings
would make "measure with Qt, draw with Skia" reachable, and that state is the
preview/export divergence the whole migration exists to avoid — so the tests
that matter here are the ones about the two never separating.
"""

import pytest

from core import text_engine
from core.text_measure import get_measurer, set_measurer

HAS_SKIA = text_engine.SKIA in text_engine.available_engines()
needs_skia = pytest.mark.skipif(not HAS_SKIA, reason="skia-python unavailable")


@pytest.fixture(autouse=True)
def restore_default_engine():
    yield
    text_engine.set_engine(text_engine.QT)
    set_measurer(None)


def test_qt_is_the_default(qapp):
    assert text_engine.engine() == text_engine.QT
    assert not text_engine.paints_with_skia()


def test_qt_is_always_available():
    assert text_engine.QT in text_engine.available_engines()


def test_an_unknown_engine_is_refused():
    with pytest.raises(ValueError):
        text_engine.set_engine("pango")


def test_selecting_qt_restores_the_qt_measurer(qapp):
    text_engine.set_engine(text_engine.QT)
    assert get_measurer().name == "qt"


@needs_skia
def test_selecting_skia_moves_both_measurer_and_painter(qapp):
    text_engine.set_engine(text_engine.SKIA)
    assert text_engine.engine() == text_engine.SKIA
    assert text_engine.paints_with_skia()
    assert get_measurer().name == "skia"


@needs_skia
def test_switching_back_moves_both_back(qapp):
    text_engine.set_engine(text_engine.SKIA)
    text_engine.set_engine(text_engine.QT)
    assert not text_engine.paints_with_skia()
    assert get_measurer().name == "qt"


def test_skia_is_refused_rather_than_silently_downgraded(monkeypatch):
    """A caller told it got Skia must actually have Skia.

    Falling back to Qt without saying so would mean the pages being rendered
    are not the ones the caller thinks they are.
    """
    monkeypatch.setattr(text_engine.skia_text, "is_available", lambda: False)
    monkeypatch.setattr(
        text_engine.skia_text, "unavailable_reason", lambda: "pretend it is missing"
    )
    with pytest.raises(RuntimeError, match="pretend it is missing"):
        text_engine.set_engine(text_engine.SKIA)
    assert text_engine.engine() == text_engine.QT


def test_available_engines_reflects_the_installation(monkeypatch):
    monkeypatch.setattr(text_engine.skia_text, "is_available", lambda: False)
    assert text_engine.available_engines() == (text_engine.QT,)
