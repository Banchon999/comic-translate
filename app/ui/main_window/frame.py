from PySide6 import QtCore, QtWidgets

from ..title_bar import RESIZE_MARGIN

_EDGE_CURSORS: dict = {
    frozenset({QtCore.Qt.Edge.LeftEdge}): QtCore.Qt.CursorShape.SizeHorCursor,
    frozenset({QtCore.Qt.Edge.RightEdge}): QtCore.Qt.CursorShape.SizeHorCursor,
    frozenset({QtCore.Qt.Edge.TopEdge}): QtCore.Qt.CursorShape.SizeVerCursor,
    frozenset({QtCore.Qt.Edge.BottomEdge}): QtCore.Qt.CursorShape.SizeVerCursor,
    frozenset({QtCore.Qt.Edge.TopEdge, QtCore.Qt.Edge.LeftEdge}): QtCore.Qt.CursorShape.SizeFDiagCursor,
    frozenset({QtCore.Qt.Edge.BottomEdge, QtCore.Qt.Edge.RightEdge}): QtCore.Qt.CursorShape.SizeFDiagCursor,
    frozenset({QtCore.Qt.Edge.TopEdge, QtCore.Qt.Edge.RightEdge}): QtCore.Qt.CursorShape.SizeBDiagCursor,
    frozenset({QtCore.Qt.Edge.BottomEdge, QtCore.Qt.Edge.LeftEdge}): QtCore.Qt.CursorShape.SizeBDiagCursor,
}


def _edges_at(win: QtWidgets.QMainWindow, gpos: QtCore.QPoint, margin: int = RESIZE_MARGIN):
    """Return a Qt.Edges flag for whichever window edges *gpos* is within *margin* pixels of."""
    geo = win.geometry()
    x = gpos.x() - geo.x()
    y = gpos.y() - geo.y()
    w = geo.width()
    h = geo.height()

    edges = QtCore.Qt.Edge(0)
    if x <= margin:
        edges |= QtCore.Qt.Edge.LeftEdge
    if x >= w - margin:
        edges |= QtCore.Qt.Edge.RightEdge
    if y <= margin:
        edges |= QtCore.Qt.Edge.TopEdge
    if y >= h - margin:
        edges |= QtCore.Qt.Edge.BottomEdge
    return edges


class _VisibilityWatcher(QtCore.QObject):
    """Tells the resizer when its window is shown or hidden. A filter on the
    window alone, so it sees only the window's own events."""

    def __init__(self, resizer: "EdgeResizer", window: QtWidgets.QMainWindow) -> None:
        super().__init__(resizer)
        self._resizer = resizer
        window.installEventFilter(self)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:  # noqa: N802
        etype = event.type()
        if etype == QtCore.QEvent.Type.Show:
            self._resizer.attach()
        elif etype in (QtCore.QEvent.Type.Hide, QtCore.QEvent.Type.Close):
            self._resizer.detach()
        return False


class EdgeResizer(QtCore.QObject):
    """Event filter that provides edge resize cursors and startSystemResize for frameless windows.

    It watches the whole application — the cursor has to change over child
    widgets at the window's edge, and they, not the window, get those mouse
    events. That routes *every* event in the process through this Python
    method, each one wrapped for it, so the filter is installed only while the
    window is visible. Installed for the window's whole life, a closed window
    kept taxing every event until it was collected: thirty of them in the test
    suite made one ``processEvents()`` call take 30 s.
    """

    MARGIN = RESIZE_MARGIN

    def __init__(self, window: QtWidgets.QMainWindow) -> None:
        super().__init__(window)
        self._win = window
        self.attached = False
        self._watcher = _VisibilityWatcher(self, window)
        if window.isVisible():
            self.attach()

    def attach(self) -> None:
        if not self.attached:
            QtWidgets.QApplication.instance().installEventFilter(self)
            self.attached = True

    def detach(self) -> None:
        if self.attached:
            QtWidgets.QApplication.instance().removeEventFilter(self)
            self.attached = False

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:  # noqa: N802
        etype = event.type()
        if etype not in (QtCore.QEvent.Type.MouseMove, QtCore.QEvent.Type.MouseButtonPress):
            return False

        win = self._win

        if isinstance(watched, QtWidgets.QWidget) and watched.window() is not win:
            return False

        if win.isMaximized() or win.isFullScreen():
            if etype == QtCore.QEvent.Type.MouseMove:
                win.unsetCursor()
            return False

        gpos = event.globalPosition().toPoint()
        geo = win.geometry()
        m = self.MARGIN
        if not geo.adjusted(-m, -m, m, m).contains(gpos):
            if etype == QtCore.QEvent.Type.MouseMove:
                win.unsetCursor()
            return False

        edges = _edges_at(win, gpos, m)

        if etype == QtCore.QEvent.Type.MouseMove:
            key = frozenset(e for e in QtCore.Qt.Edge if e & edges)
            cursor_shape = _EDGE_CURSORS.get(key)
            if cursor_shape is not None:
                win.setCursor(cursor_shape)
            else:
                win.unsetCursor()
            return False

        if event.button() == QtCore.Qt.MouseButton.LeftButton and edges:
            handle = win.windowHandle()
            if handle:
                handle.startSystemResize(edges)
            return True

        return False
