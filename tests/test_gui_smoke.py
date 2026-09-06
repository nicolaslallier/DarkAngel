import httpx
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from darkangel.api import health_payload
from darkangel.gui.client import ApiClient
from darkangel.gui.window import build_window

pytestmark = pytest.mark.usefixtures("qtbot")


class TestGuiSmoke:
    def test_window_builds_offscreen(self, qtbot) -> None:
        api = ApiClient(
            base_url="http://127.0.0.1:9",
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, json=health_payload())
            ),
        )
        window = build_window(api)
        qtbot.addWidget(window)
        assert window.windowTitle() == "DarkAngel"
        assert window.centralWidget() is not None

    def test_button_click_reaches_client(self, qtbot) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(200, json=health_payload())

        api = ApiClient(
            base_url="http://127.0.0.1:9", transport=httpx.MockTransport(handler)
        )
        window = build_window(api)
        qtbot.addWidget(window)
        window.show()
        window.activateWindow()

        buttons = window.findChildren(QPushButton)
        assert len(buttons) == 2
        qtbot.mouseClick(buttons[0], Qt.MouseButton.LeftButton)
        qtbot.wait(100)
        assert seen == ["/health"]
