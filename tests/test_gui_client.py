import httpx
import pytest

from darkangel.api import health_payload, hello_payload
from darkangel.gui.client import ApiClient, ApiError


class TestApiClientPaths:
    def test_health_uses_health_path(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(200, json=health_payload())

        api = ApiClient(base_url="http://127.0.0.1:9", transport=httpx.MockTransport(handler))
        assert api.health() == health_payload()
        assert seen == ["/health"]

    def test_hello_uses_root_path(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(200, json=hello_payload())

        api = ApiClient(base_url="http://127.0.0.1:9", transport=httpx.MockTransport(handler))
        assert api.hello() == hello_payload()
        assert seen == ["/"]


class TestApiClientErrors:
    def test_5xx_raises_apierror(self) -> None:
        api = ApiClient(
            base_url="http://127.0.0.1:9",
            transport=httpx.MockTransport(lambda req: httpx.Response(500, text="boom")),
        )
        with pytest.raises(ApiError):
            api.health()

    def test_404_raises_apierror(self) -> None:
        api = ApiClient(
            base_url="http://127.0.0.1:9",
            transport=httpx.MockTransport(lambda req: httpx.Response(404, json={"detail": "nope"})),
        )
        with pytest.raises(ApiError):
            api.hello()

    def test_connect_error_raises_apierror(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        api = ApiClient(base_url="http://127.0.0.1:9", transport=httpx.MockTransport(handler))
        with pytest.raises(ApiError):
            api.hello()


class TestApiClientDefaults:
    def test_default_base_url_is_local_backend(self) -> None:
        api = ApiClient()
        assert api.base_url == "http://127.0.0.1:8000"
