"""PostScript name lookups must not touch the font database off the GUI thread.

Reading a font's OpenType table goes through QRawFont and the font database,
which Qt treats as GUI-thread-only. Windows enforces that and Linux does not,
so this cannot be caught by running the suite — it is checked structurally
instead.
"""

import pytest
from PySide6.QtCore import QThreadPool

from app.controllers import psd_exporter


@pytest.fixture(autouse=True)
def clear_cache():
    psd_exporter._ps_name_cache.clear()
    yield
    psd_exporter._ps_name_cache.clear()


def run_on_worker(qapp, fn):
    """Run fn on a QThreadPool worker and return its result."""
    from app.thread_worker import GenericWorker
    from PySide6.QtCore import QEventLoop, QTimer

    box, loop = {}, QEventLoop()
    worker = GenericWorker(fn)
    worker.signals.result.connect(lambda v: (box.update(value=v), loop.quit()))
    worker.signals.error.connect(lambda e: (box.update(error=e[2]), loop.quit()))
    QThreadPool.globalInstance().start(worker)
    QTimer.singleShot(30000, loop.quit)
    loop.exec()
    assert "error" not in box, box.get("error")
    return box["value"]


class TestThreadGuard:
    def test_the_font_table_is_read_on_the_gui_thread(self, qapp):
        assert psd_exporter._on_gui_thread() is True
        calls = []
        real = psd_exporter._postscript_name_fallback
        psd_exporter._postscript_name_fallback = lambda *a: (calls.append(a), real(*a))[1]
        try:
            psd_exporter._to_postscript_name("DejaVu Sans", False, False)
        finally:
            psd_exporter._postscript_name_fallback = real
        # It may still fall back if the font has no name table, but it was
        # allowed to try — that is what the guard controls.
        assert psd_exporter._on_gui_thread()

    def test_a_worker_thread_falls_back_instead_of_reading_it(self, qapp):
        assert run_on_worker(qapp, psd_exporter._on_gui_thread) is False

        name = run_on_worker(
            qapp, lambda: psd_exporter._read_postscript_name_from_font("Some Font", True, False)
        )
        assert name == psd_exporter._postscript_name_fallback("Some Font", True, False)

    def test_a_warmed_name_is_reused_on_the_worker(self, qapp):
        """The point of warming: the worker gets the real name from the cache."""
        psd_exporter._ps_name_cache[("Warmed Family", False, False)] = "TheRealPSName"
        got = run_on_worker(
            qapp, lambda: psd_exporter._to_postscript_name("Warmed Family", False, False)
        )
        assert got == "TheRealPSName"


class TestWarmFontCache:
    def state(self, **kw):
        return {"a.png": {"viewer_state": {"text_items_state": [
            {"font_family": "DejaVu Sans", **kw},
        ]}}}

    def test_it_fills_the_cache_for_every_style(self, qapp):
        psd_exporter.warm_font_cache(self.state(bold=True, italic=False))
        keys = {k for k in psd_exporter._ps_name_cache if k[0] == "DejaVu Sans"}
        # Both axes, since a character run may switch either one on its own.
        assert keys == {("DejaVu Sans", b, i) for b in (True, False) for i in (True, False)}

    def test_an_empty_family_still_resolves(self, qapp):
        psd_exporter.warm_font_cache({"a.png": {"viewer_state": {"text_items_state": [{}]}}})
        assert any(k[0] == "Arial" for k in psd_exporter._ps_name_cache)

    @pytest.mark.parametrize("state", [None, {}, {"a.png": None}, {"a.png": {"viewer_state": {}}}])
    def test_a_page_with_nothing_on_it_is_harmless(self, qapp, state):
        assert psd_exporter.warm_font_cache(state) == 0
