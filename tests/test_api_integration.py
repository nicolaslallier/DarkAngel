import threading
import time

import httpx
import uvicorn

from darkangel.api import VERSION
from darkangel.main import create_server


def _free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server_on_port(port: int) -> None:
    create_server(host="127.0.0.1", port=port)


def _start_server_threaded(port: int) -> threading.Thread:
    t = threading.Thread(target=_start_server_on_port, args=(port,), daemon=True)
    t.start()
    return t


def _wait_until_ready(client: httpx.Client, port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = client.get(f"http://127.0.0.1:{port}/health")
            if resp.status_code == 200:
                return
        except httpx.ConnectError as exc:
            last_err = exc
            time.sleep(0.1)
    raise AssertionError(f"server did not become ready: {last_err}")


class TestIntegration:
    def test_live_root_endpoint(self) -> None:
        port = _free_port()
        t = _start_server_threaded(port)
        try:
            with httpx.Client() as client:
                _wait_until_ready(client, port)
                resp = client.get(f"http://127.0.0.1:{port}/")
                assert resp.status_code == 200
                assert resp.json() == {"message": "Hello, World!"}
        finally:
            t.join(timeout=1)

    def test_live_health_endpoint(self) -> None:
        port = _free_port()
        t = _start_server_threaded(port)
        try:
            with httpx.Client() as client:
                _wait_until_ready(client, port)
                resp = client.get(f"http://127.0.0.1:{port}/health")
                assert resp.status_code == 200
                assert resp.json() == {"status": "ok"}
        finally:
            t.join(timeout=1)

    def test_live_unknown_route_404s(self) -> None:
        port = _free_port()
        t = _start_server_threaded(port)
        try:
            with httpx.Client() as client:
                _wait_until_ready(client, port)
                resp = client.get(f"http://127.0.0.1:{port}/does-not-exist")
                assert resp.status_code == 404
        finally:
            t.join(timeout=1)

    def test_live_openapi_schema_available(self) -> None:
        port = _free_port()
        t = _start_server_threaded(port)
        try:
            with httpx.Client() as client:
                _wait_until_ready(client, port)
                resp = client.get(f"http://127.0.0.1:{port}/openapi.json")
                assert resp.status_code == 200
                payload = resp.json()
                assert payload["info"]["version"] == VERSION
        finally:
            t.join(timeout=1)

