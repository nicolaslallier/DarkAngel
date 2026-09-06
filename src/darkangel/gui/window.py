"""PySide6 main window for the DarkAngel frontend.

All backend calls run on a :class:`QThread` so the UI thread is never blocked.
The window is constructed lazily through :func:`build_window` so it can be
instantiated offscreen (CI / smoke tests) without an active QApplication.
"""

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .client import ApiClient, ApiError


class _RequestWorker(QThread):
    finished_signal = Signal(object, object)

    def __init__(self, client: ApiClient, method: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self._method = method

    def run(self) -> None:
        try:
            result = getattr(self._client, self._method)()
            self.finished_signal.emit(result, None)
        except ApiError as exc:
            self.finished_signal.emit(None, str(exc))


def build_window(client: ApiClient) -> QMainWindow:
    window = QMainWindow()
    window.setWindowTitle("DarkAngel")

    central = QWidget()
    window.setCentralWidget(central)

    layout = QVBoxLayout(central)
    status = QTextEdit()
    status.setReadOnly(True)
    status.setPlaceholderText("Requis backend : uv run darkangel")

    for label, method in (("Health", "health"), ("Hello", "hello")):
        button = QPushButton(label)
        button.clicked.connect(lambda _checked=False, m=method: _start(client, status, m))
        layout.addWidget(button)

    layout.addWidget(status)
    return window


def _start(client: ApiClient, status: QTextEdit, method: str) -> None:
    status.append(f"> {method}...")
    worker = _RequestWorker(client, method, parent=None)
    worker.finished_signal.connect(lambda payload, error: _on_done(status, worker, payload, error))
    worker.start()


def _on_done(status: QTextEdit, worker: _RequestWorker, payload: object, error: object) -> None:
    if error is not None:
        status.append(f"< {error}")
        return
    status.append(f"< {payload}")
    worker.deleteLater()
