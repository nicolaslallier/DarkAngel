"""Entry point for the DarkAngel GUI frontend.

Run with `uv run darkangel-gui`; assumes the backend is already running
(`uv run darkangel` on http://127.0.0.1:8000).
"""

from PySide6.QtWidgets import QApplication

from .client import ApiClient
from .window import build_window


def main() -> None:
    app = QApplication.instance() or QApplication()
    window = build_window(ApiClient())
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
