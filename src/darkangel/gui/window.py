"""PySide6 main window for the DarkAngel frontend.

Each backend call runs on its own :class:`~PySide6.QtCore.QThread` so the UI
thread is never blocked. The window is built through :func:`build_window` so it
can be instantiated offscreen (CI / smoke tests) without a running event loop.

Guards:
- A single in-flight flag (``_busy``) ignores a second click while a request is
  pending, so the status pane stays coherent and no unbounded workers are
  created.
- In-flight workers are joined in :meth:`Dashboard.closeEvent`, so a window is
  never destroyed while a thread is still running.
"""

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .client import ApiClient, ApiError


class _RequestWorker(QThread):
    """Run one backend call off the UI thread and report via ``result_signal``.

    The signal carries ``(payload, error)`` so the main thread can render the
    outcome without touching httpx on its own.
    """

    result_signal = Signal(object, object)

    def __init__(self, client: ApiClient, method: str) -> None:
        super().__init__()
        self._client = client
        self._method = method

    def run(self) -> None:
        payload: object
        error: object
        try:
            payload = getattr(self._client, self._method)()
            error = None
        except ApiError as exc:
            payload = None
            error = str(exc)
        self.result_signal.emit(payload, error)


class Dashboard(QMainWindow):
    """The backend-connected main window.

    Exposes :attr:`response`, a ``(payload, error)`` signal emitted on the main
    thread whenever a request settles — useful both for the UI and for tests
    that need to synchronise without sleeping on a wall clock.
    """

    response = Signal(object, object)

    def __init__(self, client: ApiClient) -> None:
        super().__init__()
        self.setWindowTitle("DarkAngel")
        self._client = client
        self._busy = False
        self._workers: set[_RequestWorker] = set()

        central = QWidget()
        self.setCentralWidget(central)

        self._status = QTextEdit()
        self._status.setReadOnly(True)
        self._status.setPlaceholderText("Requis backend : uv run darkangel")

        layout = QVBoxLayout(central)
        for label, method in (("Health", "health"), ("Hello", "hello")):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, m=method: self._start(m))
            layout.addWidget(button)
        layout.addWidget(self._status)

    def _start(self, method: str) -> None:
        if self._busy:
            return
        self._busy = True
        self._status.append(f"> {method}...")
        worker = _RequestWorker(self._client, method)
        self._workers.add(worker)
        worker.result_signal.connect(lambda p, e: self._on_done(worker, p, e))
        worker.start()

    def _on_done(self, worker: _RequestWorker, payload: object, error: object) -> None:
        if error is not None:
            self._status.append(f"< {error}")
        else:
            self._status.append(f"< {payload}")
        # The worker thread has already finished emitting, so dropping the
        # reference and scheduling deletion is safe: the status pane was the
        # only thing still keeping this worker alive.
        self._workers.discard(worker)
        worker.deleteLater()
        self._busy = False
        self.response.emit(payload, error)

    def closeEvent(self, event: QCloseEvent) -> None:
        # Join any in-flight worker so its C++ thread is not destroyed while it
        # is still running, then let the window delete normally.
        for worker in self._workers:
            worker.wait()
            worker.deleteLater()
        self._workers.clear()
        self._busy = False
        super().closeEvent(event)


def build_window(client: ApiClient) -> Dashboard:
    """Build and return the connected main window (no event loop required)."""
    return Dashboard(client)
