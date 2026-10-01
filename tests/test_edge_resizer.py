"""The frameless window's edge resizer watches the whole application, so it
must do so only while its window is on screen: installed for the window's
whole life, every closed window kept routing every event in the process
through Python, and a test run with thirty of them spent 30 s inside a single
processEvents() call."""

from PySide6 import QtCore, QtWidgets

from app.ui.main_window.frame import EdgeResizer


def _count_filter_calls(monkeypatch):
    calls = []
    original = EdgeResizer.eventFilter

    def counting(self, watched, event):
        calls.append(event.type())
        return original(self, watched, event)

    monkeypatch.setattr(EdgeResizer, "eventFilter", counting)
    return calls


def _post_and_deliver(qapp, count=20):
    target = QtCore.QObject()
    for _ in range(count):
        QtCore.QCoreApplication.postEvent(target, QtCore.QEvent(QtCore.QEvent.Type.User))
    qapp.processEvents()


def test_it_watches_the_application_only_while_its_window_is_shown(qapp, monkeypatch):
    window = QtWidgets.QMainWindow()
    resizer = EdgeResizer(window)
    calls = _count_filter_calls(monkeypatch)
    assert not resizer.attached
    _post_and_deliver(qapp)
    assert calls == [], "a window that was never shown taxes nothing"

    window.show()
    qapp.processEvents()
    assert resizer.attached
    calls.clear()
    _post_and_deliver(qapp)
    assert len(calls) >= 20, "shown, it sees every event (it needs the edges' mouse moves)"

    window.close()
    qapp.processEvents()
    assert not resizer.attached
    calls.clear()
    _post_and_deliver(qapp)
    assert calls == [], "a closed window stops taxing every event"

    window.show()
    qapp.processEvents()
    assert resizer.attached, "and starts again when shown again"
    window.close()


def test_a_window_already_visible_is_watched_at_once(qapp):
    window = QtWidgets.QMainWindow()
    window.show()
    qapp.processEvents()
    resizer = EdgeResizer(window)
    assert resizer.attached
    window.close()
    qapp.processEvents()
    assert not resizer.attached
