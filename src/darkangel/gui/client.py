"""Frontend data client for the DarkAngel backend.

The GUI never talks FastAPI directly; it goes through :class:`ApiClient`, which
hides the HTTP details and the external-backend assumption (the backend is
launched separately via `uv run darkangel`).
"""

import os
from dataclasses import dataclass, field

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
_ENV_API_URL = "DARKANGEL_API"


class ApiError(RuntimeError):
    """Raised when the backend is unreachable or returns an unexpected status."""


@dataclass
class ApiClient:
    base_url: str = field(
        default_factory=lambda: os.environ.get(_ENV_API_URL, DEFAULT_BASE_URL)
    )
    timeout: float = 5.0
    transport: httpx.BaseTransport | None = field(
        default=None, repr=False, compare=False
    )

    def health(self) -> dict[str, str]:
        return self._get("/health")

    def hello(self) -> dict[str, str]:
        return self._get("/")

    def _get(self, path: str) -> dict[str, str]:
        with httpx.Client(
            base_url=self.base_url, timeout=self.timeout, transport=self.transport
        ) as client:
            try:
                response = client.get(path)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ApiError(f"{path}: {exc}") from exc
        return response.json()
