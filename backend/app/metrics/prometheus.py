from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Annotated

import httpx
from fastapi import Depends

from app.core.config import get_settings

log = logging.getLogger(__name__)

Points = list[tuple[float, float]]


class PrometheusError(Exception):
    """Any failure talking to Prometheus. The message never carries the URL."""


class PrometheusClient:
    def __init__(
        self, base_url: str, timeout: float, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.transport = transport

    def query_range(
        self, query: str, start: datetime, end: datetime, step: int
    ) -> list[tuple[dict[str, str], Points]]:
        params = {"query": query, "start": start.timestamp(), "end": end.timestamp(), "step": step}
        try:
            with httpx.Client(
                base_url=self.base_url, timeout=self.timeout, transport=self.transport
            ) as client:
                response = client.get("/api/v1/query_range", params=params)
                response.raise_for_status()
                body = response.json()
            if body["status"] != "success" or body["data"]["resultType"] != "matrix":
                raise ValueError("unexpected reply")
            return [(item["metric"], _points(item["values"])) for item in body["data"]["result"]]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            # Class name only: the exception text can hold the URL.
            log.warning("prometheus query failed: %s", type(exc).__name__)
            raise PrometheusError("metrics source unavailable") from None


def _points(values: list) -> Points:
    # Prometheus sends samples as strings and uses NaN/+Inf (division by zero):
    # JSON cannot carry those, so they are dropped.
    points = [(float(ts), float(v)) for ts, v in values]
    return [(ts, v) for ts, v in points if math.isfinite(v)]


def prometheus_client() -> PrometheusClient:
    settings = get_settings()
    return PrometheusClient(settings.prometheus_url, settings.prometheus_timeout_seconds)


Prom = Annotated[PrometheusClient, Depends(prometheus_client)]
