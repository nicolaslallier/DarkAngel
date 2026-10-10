# Grafana-like metrics panels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Grafana-style history graphs (host, containers, Portainer) with a range picker and auto-refresh to the home dashboard.

**Architecture:** A fixed server-side PromQL catalog; `GET /api/metrics/range` proxies Prometheus `query_range` (host/container panels) or reads `infra_status`/`backup_status` history (Portainer panels) and returns one series format. The SPA renders panels from `GET /api/metrics/panels` with uPlot.

**Tech Stack:** FastAPI, httpx (already a dependency), SQLAlchemy; Vue 3, Pinia, uPlot (new), vitest.

**Spec:** `docs/superpowers/specs/2026-10-10-grafana-like-metrics-design.md`

## Global Constraints

- Prometheus at `http://prometheus:9090`, no auth; exporters: node-exporter, cAdvisor.
- The browser only talks to `/api`; the client sends a panel id and a range, never PromQL.
- Ranges and steps: `1h`→15 s, `6h`→60 s, `24h`→300 s, `7d`→1800 s.
- Access via the `Reader` dependency (same as `/api/infra`); metrics are not scoped by household.
- Prometheus failure → 502 with a generic message; the Prometheus URL never reaches a client.
- Unknown `panel` or `range` → 422. Empty result → `series: []`, not an error.
- Backend: ruff, 100 columns, response models declared next to the route. Frontend: `<script setup lang="ts">`, imports via `@/`.
- Only new dependency: `uplot` (frontend). No new backend dependency.
- Tests never touch a real Prometheus. Do not commit unless the step says so; never commit to `main`.
- Tools: `cd backend && .venv/bin/python -m pytest -m unit`, `.venv/bin/python -m ruff check .`; `cd frontend && npm run test`, `npm run build`.

## Review Focus

- Prometheus down/timeout: one panel shows an error, the others still render (Task 2 route test, Task 3 store test).
- Prometheus returns `NaN`/`+Inf` samples (division by zero): they are dropped, not sent as invalid JSON (Task 1).
- A response for an old range arrives after the user switched range: it must not overwrite the new data (Task 3).
- A Portainer panel with no rows yet (fresh deploy): `series: []`, "No data" in the UI (Task 2, Task 4).
- Auto-refresh timer leaks after leaving the page, or fires while the tab is hidden (Task 3).

---

### Task 1: Backend metrics core (config, catalog, ranges, Prometheus client)

**Files:**
- Modify: `backend/app/core/config.py` (after `collector_interval_seconds`)
- Create: `backend/app/metrics/__init__.py` (empty), `backend/app/metrics/catalog.py`, `backend/app/metrics/ranges.py`, `backend/app/metrics/prometheus.py`
- Test: `backend/tests/unit/test_metrics_core.py`

**Interfaces:**
- Produces:
  - `catalog.Panel` (frozen dataclass: `id, title, row, unit, source, query=""`), `catalog.PANELS: tuple[Panel, ...]`, `catalog.get(panel_id) -> Panel | None`
  - `ranges.RangeId = Literal["1h","6h","24h","7d"]`, `ranges.RANGES: dict[str, tuple[timedelta, int]]`
  - `prometheus.PrometheusClient(base_url, timeout, transport=None).query_range(query, start, end, step) -> list[tuple[dict[str, str], list[tuple[float, float]]]]`, `prometheus.PrometheusError`, `prometheus.prometheus_client()` (FastAPI dependency), `prometheus.Prom` (`Annotated[PrometheusClient, Depends(...)]`)
  - Settings: `prometheus_url: str`, `prometheus_timeout_seconds: float`

- [ ] **Step 1: Write the failing tests**

```python
"""Unit — metrics core: catalog, ranges, and the Prometheus client over a fake transport."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.metrics import catalog
from app.metrics.prometheus import PrometheusClient, PrometheusError
from app.metrics.ranges import RANGES

END = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def test_panel_ids_are_unique_and_prom_panels_have_a_query():
    ids = [p.id for p in catalog.PANELS]
    assert len(ids) == len(set(ids))
    assert all(p.query for p in catalog.PANELS if p.source == "prom")
    assert catalog.get("host-cpu").row == "host"
    assert catalog.get("nope") is None


def test_ranges_and_steps_are_pinned():
    assert {k: (v[0], v[1]) for k, v in RANGES.items()} == {
        "1h": (timedelta(hours=1), 15),
        "6h": (timedelta(hours=6), 60),
        "24h": (timedelta(hours=24), 300),
        "7d": (timedelta(days=7), 1800),
    }


def client_for(handler) -> PrometheusClient:
    return PrometheusClient("http://prometheus:9090", 5, httpx.MockTransport(handler))


def matrix(*results):
    return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": list(results)}})


def test_query_range_sends_start_end_step_and_maps_the_matrix():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return matrix({"metric": {"name": "web"}, "values": [[1.0, "0.5"], [2.0, "1.5"]]})

    series = client_for(handler).query_range("up", END - timedelta(hours=1), END, 15)

    assert series == [({"name": "web"}, [(1.0, 0.5), (2.0, 1.5)])]
    assert seen["query"] == "up"
    assert seen["step"] == "15"
    assert float(seen["end"]) - float(seen["start"]) == 3600


def test_non_finite_samples_are_dropped():
    body = matrix({"metric": {}, "values": [[1.0, "NaN"], [2.0, "+Inf"], [3.0, "2"]]})

    (_, points), = client_for(lambda r: body).query_range("up", END, END, 15)

    assert points == [(3.0, 2.0)]


def test_empty_result_is_an_empty_list():
    assert client_for(lambda r: matrix()).query_range("up", END, END, 15) == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"status": "error", "error": "bad query"}),
        httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": []}}),
    ],
)
def test_bad_replies_raise_prometheus_error(response):
    with pytest.raises(PrometheusError):
        client_for(lambda r: response).query_range("up", END, END, 15)


def test_unreachable_prometheus_raises_without_leaking_the_url():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(PrometheusError) as err:
        client_for(refuse).query_range("up", END, END, 15)
    assert "prometheus" not in str(err.value).lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_metrics_core.py -q`
Expected: FAIL (`ModuleNotFoundError: app.metrics`).

- [ ] **Step 3: Implement**

`config.py`, after `collector_interval_seconds: int = 300`:

```python
    # The Prometheus the metrics panels read (node-exporter + cAdvisor). No auth.
    prometheus_url: str = "http://prometheus:9090"
    prometheus_timeout_seconds: float = 5
```

`backend/app/metrics/catalog.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Row = Literal["host", "containers", "portainer"]
Unit = Literal["percent", "bytes", "bytes_per_s", "count"]
Source = Literal["prom", "db"]

# Veth/bridge devices would double-count container traffic already seen on the NIC.
_NIC = 'device!~"lo|veth.*|docker.*|br-.*"'


@dataclass(frozen=True)
class Panel:
    id: str
    title: str
    row: Row
    unit: Unit
    source: Source
    query: str = ""


# The only PromQL this app ever runs: the client sends an id, never a query.
# ponytail: host panels aggregate over every node-exporter target; add a per-host
# split if a second host is ever scraped.
PANELS: tuple[Panel, ...] = (
    Panel("host-cpu", "CPU", "host", "percent", "prom",
          '100 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100'),
    Panel("host-memory", "Memory", "host", "percent", "prom",
          "100 * (1 - sum(node_memory_MemAvailable_bytes) / sum(node_memory_MemTotal_bytes))"),
    Panel("host-disk", "Disk (/)", "host", "percent", "prom",
          '100 * (1 - sum(node_filesystem_avail_bytes{mountpoint="/"})'
          ' / sum(node_filesystem_size_bytes{mountpoint="/"}))'),
    Panel("host-net-in", "Network in", "host", "bytes_per_s", "prom",
          f"sum(rate(node_network_receive_bytes_total{{{_NIC}}}[5m]))"),
    Panel("host-net-out", "Network out", "host", "bytes_per_s", "prom",
          f"sum(rate(node_network_transmit_bytes_total{{{_NIC}}}[5m]))"),
    Panel("containers-cpu", "Top containers by CPU", "containers", "percent", "prom",
          'topk(5, sum by (name) (rate(container_cpu_usage_seconds_total{name!=""}[5m])) * 100)'),
    Panel("containers-memory", "Top containers by memory", "containers", "bytes", "prom",
          'topk(5, max by (name) (container_memory_working_set_bytes{name!=""}))'),
    Panel("portainer-up", "Portainer reachable", "portainer", "count", "db"),
    Panel("portainer-stacks", "Stacks", "portainer", "count", "db"),
    Panel("portainer-backup-size", "Backup size", "portainer", "bytes", "db"),
)

_BY_ID = {p.id: p for p in PANELS}


def get(panel_id: str) -> Panel | None:
    return _BY_ID.get(panel_id)
```

`backend/app/metrics/ranges.py`:

```python
from datetime import timedelta
from typing import Literal

RangeId = Literal["1h", "6h", "24h", "7d"]

# (window, Prometheus step in seconds): about 240-340 points per graph.
RANGES: dict[str, tuple[timedelta, int]] = {
    "1h": (timedelta(hours=1), 15),
    "6h": (timedelta(hours=6), 60),
    "24h": (timedelta(hours=24), 300),
    "7d": (timedelta(days=7), 1800),
}
```

`backend/app/metrics/prometheus.py`:

```python
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
            return [
                (item["metric"], _points(item["values"])) for item in body["data"]["result"]
            ]
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
```

- [ ] **Step 4: Run to verify it passes, plus lint**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_metrics_core.py -q && .venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .`
Expected: PASS, no lint findings (run `ruff format .` if the format check fails).

- [ ] **Step 5: Commit** (only when the user has asked for commits; otherwise leave staged)

```bash
git add backend/app/core/config.py backend/app/metrics backend/tests/unit/test_metrics_core.py
git commit -m "feat: metrics catalog and Prometheus range client"
```

---

### Task 2: Metrics routes and DB-backed panels

**Files:**
- Modify: `backend/app/repositories/infra.py` (add two history methods), `backend/tests/conftest.py` (same methods on `FakeInfraRepository`, plus a `prometheus` fixture), `backend/app/api/router.py`, `backend/tests/regression/openapi.snapshot.json` (regenerated)
- Create: `backend/app/api/routes/metrics.py`
- Test: `backend/tests/unit/test_metrics_routes.py`, `backend/tests/regression/test_metrics_catalog.py`

**Interfaces:**
- Consumes: Task 1 (`catalog`, `RANGES`, `RangeId`, `Prom`, `PrometheusError`, `prometheus_client`), existing `Reader`, `InfraRepo`, `Now`.
- Produces: `InfraRepository.status_history(since) -> list[InfraStatus]` and `backup_history(since) -> list[BackupStatus]` (ordered by `checked_at` ascending, `checked_at >= since`); `GET /api/metrics/panels` → `list[{id,title,row,unit}]`; `GET /api/metrics/range?panel&range` → `{panel, range, series:[{label, points:[[ts,value],...]}]}`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/unit/test_metrics_routes.py`:

```python
"""Unit — /api/metrics: the catalog, Prometheus-backed panels, DB-backed panels."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.clock import now
from app.main import app
from app.metrics.prometheus import PrometheusError, prometheus_client
from tests.conftest import token

client = TestClient(app)
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def auth():
    return {"Authorization": f"Bearer {token(sub='user-1')}"}


class FakeProm:
    def __init__(self, result=None, error=False):
        self.result, self.error, self.calls = result or [], error, []

    def query_range(self, query, start, end, step):
        self.calls.append((query, start, end, step))
        if self.error:
            raise PrometheusError("metrics source unavailable")
        return self.result


@pytest.fixture
def prom():
    fake = FakeProm()
    app.dependency_overrides[prometheus_client] = lambda: fake
    app.dependency_overrides[now] = lambda: NOW
    yield fake
    app.dependency_overrides.pop(prometheus_client, None)
    app.dependency_overrides.pop(now, None)


def get(panel, range_="1h"):
    return client.get(f"/api/metrics/range?panel={panel}&range={range_}", headers=auth())


def test_requires_a_token():
    assert client.get("/api/metrics/panels").status_code == 401
    assert client.get("/api/metrics/range?panel=host-cpu&range=1h").status_code == 401


def test_panels_lists_the_catalog_without_promql(home):
    body = client.get("/api/metrics/panels", headers=auth()).json()

    assert body[0] == {"id": "host-cpu", "title": "CPU", "row": "host", "unit": "percent"}
    assert "query" not in body[0]
    assert {p["row"] for p in body} == {"host", "containers", "portainer"}


def test_prom_panel_runs_the_catalog_query_over_the_window(home, prom):
    prom.result = [({"name": "web"}, [(1.0, 2.0)])]

    body = get("containers-cpu", "6h").json()

    assert body == {
        "panel": "containers-cpu",
        "range": "6h",
        "series": [{"label": "web", "points": [[1.0, 2.0]]}],
    }
    query, start, end, step = prom.calls[0]
    assert "container_cpu_usage_seconds_total" in query
    assert (end - start, step) == (timedelta(hours=6), 60)
    assert end == NOW


def test_unlabelled_series_takes_the_panel_title(home, prom):
    prom.result = [({}, [(1.0, 2.0)])]
    assert get("host-cpu").json()["series"][0]["label"] == "CPU"


def test_empty_prometheus_result_is_not_an_error(home, prom):
    assert get("host-cpu").json()["series"] == []


def test_prometheus_down_is_a_generic_502(home, prom):
    prom.error = True

    response = get("host-cpu")

    assert response.status_code == 502
    assert "prometheus" not in response.text.lower()


@pytest.mark.parametrize("path", ["panel=nope&range=1h", "panel=host-cpu&range=2h"])
def test_unknown_panel_or_range_is_422(home, prom, path):
    assert client.get(f"/api/metrics/range?{path}", headers=auth()).status_code == 422
    assert prom.calls == []


def test_portainer_panels_read_the_infra_history(home, infra, prom):
    infra.add_status(instance="heaven", reachable=True, stacks=7, checked_at=NOW - timedelta(minutes=10))
    infra.add_status(instance="heaven", reachable=False, checked_at=NOW - timedelta(minutes=5))
    infra.add_status(instance="old", reachable=True, stacks=1, checked_at=NOW - timedelta(hours=3))
    infra.add_backup(instance="heaven", last_backup_at=NOW, size_bytes=900, object_key="k",
                     checked_at=NOW - timedelta(minutes=5))

    up = get("portainer-up").json()["series"]
    stacks = get("portainer-stacks").json()["series"]
    size = get("portainer-backup-size").json()["series"]

    ts = (NOW - timedelta(minutes=10)).timestamp()
    assert up == [{"label": "heaven", "points": [[ts, 1.0], [ts + 300, 0.0]]}]
    assert stacks == [{"label": "heaven", "points": [[ts, 7.0]]}]  # the unreachable row has none
    assert size == [{"label": "heaven", "points": [[ts + 300, 900.0]]}]
    assert prom.calls == []


def test_portainer_panel_with_no_rows_yet_is_empty(home, infra, prom):
    assert get("portainer-up").json()["series"] == []
```

`backend/tests/regression/test_metrics_catalog.py`:

```python
"""Regression — the panel catalog is pinned: a new or changed panel is a reviewed diff."""

from app.metrics.catalog import PANELS


def test_catalog_ids_in_order():
    assert [p.id for p in PANELS] == [
        "host-cpu", "host-memory", "host-disk", "host-net-in", "host-net-out",
        "containers-cpu", "containers-memory",
        "portainer-up", "portainer-stacks", "portainer-backup-size",
    ]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_metrics_routes.py tests/regression/test_metrics_catalog.py -q`
Expected: FAIL (404s / missing route).

- [ ] **Step 3: Implement**

`repositories/infra.py`, inside `InfraRepository` (before `prune`):

```python
    def status_history(self, since: datetime) -> list[InfraStatus]:
        statement = (
            select(InfraStatus).where(InfraStatus.checked_at >= since).order_by(InfraStatus.checked_at)
        )
        return list(self.db.scalars(statement))

    def backup_history(self, since: datetime) -> list[BackupStatus]:
        statement = (
            select(BackupStatus)
            .where(BackupStatus.checked_at >= since)
            .order_by(BackupStatus.checked_at)
        )
        return list(self.db.scalars(statement))
```

`tests/conftest.py`, inside `FakeInfraRepository` (before `prune`):

```python
    def status_history(self, since):
        return sorted((r for r in self.statuses if r.checked_at >= since), key=lambda r: r.checked_at)

    def backup_history(self, since):
        return sorted((r for r in self.backups if r.checked_at >= since), key=lambda r: r.checked_at)
```

`backend/app/api/routes/metrics.py`:

```python
from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.clock import Now
from app.core.household import Reader
from app.metrics import catalog
from app.metrics.prometheus import Prom, PrometheusError
from app.metrics.ranges import RANGES, RangeId
from app.repositories.infra import InfraRepo

router = APIRouter(prefix="/metrics", tags=["metrics"])


class PanelInfo(BaseModel):
    id: str
    title: str
    row: str
    unit: str


class Series(BaseModel):
    label: str
    points: list[tuple[float, float]]


class RangeResult(BaseModel):
    panel: str
    range: str
    series: list[Series]


@router.get("/panels", response_model=list[PanelInfo])
def panels(ctx: Reader) -> list[PanelInfo]:
    return [PanelInfo(id=p.id, title=p.title, row=p.row, unit=p.unit) for p in catalog.PANELS]


def _db_series(rows: list, value: Callable) -> list[Series]:
    """One series per instance; rows arrive oldest first and keep that order."""
    by_instance: dict[str, list[tuple[float, float]]] = {}
    for row in rows:
        v = value(row)
        if v is not None:
            by_instance.setdefault(row.instance, []).append((row.checked_at.timestamp(), float(v)))
    return [Series(label=name, points=pts) for name, pts in sorted(by_instance.items())]


@router.get("/range", response_model=RangeResult)
def metric_range(
    ctx: Reader, prom: Prom, repo: InfraRepo, now: Now, panel: str, range: RangeId
) -> RangeResult:
    spec = catalog.get(panel)
    if spec is None:
        raise HTTPException(422, "unknown panel")
    window, step = RANGES[range]
    start: datetime = now - window

    if spec.source == "prom":
        try:
            result = prom.query_range(spec.query, start, now, step)
        except PrometheusError:
            raise HTTPException(502, "metrics source unavailable") from None
        series = [Series(label=m.get("name") or spec.title, points=pts) for m, pts in result]
    elif panel == "portainer-up":
        series = _db_series(repo.status_history(start), lambda r: 1.0 if r.reachable else 0.0)
    elif panel == "portainer-stacks":
        series = _db_series(repo.status_history(start), lambda r: r.stacks)
    else:
        series = _db_series(repo.backup_history(start), lambda r: r.size_bytes)
    return RangeResult(panel=panel, range=range, series=series)
```

`api/router.py`: add `metrics` to the `from app.api.routes import ...` line and `api_router.include_router(metrics.router)` at the end.

- [ ] **Step 4: Run tests, regenerate the snapshot, run the whole gate**

```
cd backend && .venv/bin/python -m pytest tests/unit/test_metrics_routes.py tests/regression/test_metrics_catalog.py -q
make -C .. snapshot            # rewrites tests/regression/openapi.snapshot.json; review the diff: only /api/metrics/* additions
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format .
.venv/bin/python -m pytest -m "unit or regression" -q
```
Expected: all PASS. If `make snapshot` is unavailable, run `cd backend && .venv/bin/python -m tests.regression.test_openapi_contract`.

- [ ] **Step 5: Commit** (only when asked)

```bash
git add backend
git commit -m "feat: /api/metrics panels and range endpoints"
```

---

### Task 3: Frontend API module, alignment helper and store

**Files:**
- Create: `frontend/src/api/metrics.ts`, `frontend/src/metrics.ts`, `frontend/src/stores/metrics.ts`
- Test: `frontend/tests/unit/metrics-helpers.test.ts`, `frontend/tests/unit/stores-metrics.test.ts`

**Interfaces:**
- Produces:
  - `api/metrics.ts`: `type RangeId = '1h'|'6h'|'24h'|'7d'`; `interface PanelInfo {id,title,row:'host'|'containers'|'portainer',unit:'percent'|'bytes'|'bytes_per_s'|'count'}`; `interface Series {label:string; points:[number,number][]}`; `getPanels(): Promise<PanelInfo[]>`; `getRange(panel:string, range:RangeId): Promise<{panel:string;range:RangeId;series:Series[]}>`
  - `src/metrics.ts`: `align(series: Series[]): [number[], ...(number|null)[][]]` (union of timestamps ascending; `null` where a series has no sample); `formatValue(unit: PanelInfo['unit'], v: number): string`
  - `stores/metrics.ts`: `useMetricsStore()` exposing `panels`, `range`, `refreshSeconds`, `state` (record id → `{loading:boolean;error:string|null;series:Series[]}`), `rows` (computed: `{row, panels}[]` in host→containers→portainer order), `load()`, `setRange(r)`, `setRefresh(seconds)`, `stop()`.

- [ ] **Step 1: Write the failing tests**

`frontend/tests/unit/metrics-helpers.test.ts`:

```ts
import { expect, it } from 'vitest'

import { align, formatValue } from '@/metrics'

it('align merges timestamps and pads gaps with null', () => {
  const out = align([
    { label: 'a', points: [[1, 10], [3, 30]] },
    { label: 'b', points: [[2, 200], [3, 300]] },
  ])
  expect(out).toEqual([[1, 2, 3], [10, null, 30], [null, 200, 300]])
})

it('align of nothing is just an empty time axis', () => {
  expect(align([])).toEqual([[]])
})

it('formatValue renders each unit', () => {
  expect(formatValue('percent', 12.345)).toBe('12.3%')
  expect(formatValue('bytes', 2048)).toBe('2.0 KB')
  expect(formatValue('bytes_per_s', 2048)).toBe('2.0 KB/s')
  expect(formatValue('count', 7)).toBe('7')
})
```

`frontend/tests/unit/stores-metrics.test.ts`:

```ts
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { getPanels, getRange } from '@/api/metrics'
import { useMetricsStore } from '@/stores/metrics'

vi.mock('@/api/metrics', () => ({ getPanels: vi.fn(), getRange: vi.fn() }))

const PANELS = [
  { id: 'host-cpu', title: 'CPU', row: 'host', unit: 'percent' },
  { id: 'portainer-up', title: 'Up', row: 'portainer', unit: 'count' },
]
const result = (panel: string, range = '6h') => ({ panel, range, series: [{ label: panel, points: [[1, 2]] }] })

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.useFakeTimers()
  vi.mocked(getPanels).mockResolvedValue(PANELS as never)
  vi.mocked(getRange).mockImplementation(async (panel, range) => result(panel, range) as never)
})
afterEach(() => vi.useRealTimers())

it('load() fetches the catalog then every panel for the default range', async () => {
  const store = useMetricsStore()
  await store.load()

  expect(store.rows.map((r) => r.row)).toEqual(['host', 'portainer'])
  expect(getRange).toHaveBeenCalledWith('host-cpu', '6h')
  expect(store.state['host-cpu'].series).toHaveLength(1)
})

it('one failing panel does not touch the others', async () => {
  vi.mocked(getRange).mockImplementation(async (panel, range) => {
    if (panel === 'host-cpu') throw new Error('GET /metrics/range failed with 502')
    return result(panel, range) as never
  })
  const store = useMetricsStore()
  await store.load()

  expect(store.state['host-cpu'].error).toBe('GET /metrics/range failed with 502')
  expect(store.state['portainer-up'].error).toBeNull()
  expect(store.state['portainer-up'].series).toHaveLength(1)
})

it('setRange refetches every panel for the new range', async () => {
  const store = useMetricsStore()
  await store.load()
  vi.mocked(getRange).mockClear()

  store.setRange('24h')
  await vi.waitFor(() => expect(getRange).toHaveBeenCalledTimes(2))

  expect(getRange).toHaveBeenCalledWith('host-cpu', '24h')
})

it('a slow answer for an old range never overwrites the new range', async () => {
  let release!: (v: unknown) => void
  vi.mocked(getRange).mockImplementationOnce(() => new Promise((r) => (release = r)) as never)
  const store = useMetricsStore()
  const loading = store.load()
  await vi.waitFor(() => expect(release).toBeTypeOf('function'))

  store.setRange('1h')
  await vi.waitFor(() => expect(store.state['host-cpu'].series).toHaveLength(1))
  release({ panel: 'host-cpu', range: '6h', series: [{ label: 'stale', points: [[9, 9]] }] })
  await loading

  expect(store.state['host-cpu'].series[0].label).toBe('host-cpu')
})

it('auto-refresh refetches on the interval, skips hidden tabs and stops on stop()', async () => {
  const store = useMetricsStore()
  await store.load()
  vi.mocked(getRange).mockClear()

  store.setRefresh(30)
  await vi.advanceTimersByTimeAsync(30_000)
  expect(getRange).toHaveBeenCalledTimes(2)

  Object.defineProperty(document, 'hidden', { value: true, configurable: true })
  await vi.advanceTimersByTimeAsync(30_000)
  expect(getRange).toHaveBeenCalledTimes(2)
  Object.defineProperty(document, 'hidden', { value: false, configurable: true })

  store.stop()
  await vi.advanceTimersByTimeAsync(60_000)
  expect(getRange).toHaveBeenCalledTimes(2)
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run tests/unit/metrics-helpers.test.ts tests/unit/stores-metrics.test.ts`
Expected: FAIL (modules not found).

- [ ] **Step 3: Implement**

`frontend/src/api/metrics.ts`:

```ts
import { apiGet } from './client'

export type RangeId = '1h' | '6h' | '24h' | '7d'
export type Unit = 'percent' | 'bytes' | 'bytes_per_s' | 'count'

export interface PanelInfo {
  id: string
  title: string
  row: 'host' | 'containers' | 'portainer'
  unit: Unit
}

export interface Series {
  label: string
  points: [number, number][]
}

export interface RangeResult {
  panel: string
  range: RangeId
  series: Series[]
}

export function getPanels(): Promise<PanelInfo[]> {
  return apiGet<PanelInfo[]>('/metrics/panels')
}

export function getRange(panel: string, range: RangeId): Promise<RangeResult> {
  return apiGet<RangeResult>(`/metrics/range?panel=${encodeURIComponent(panel)}&range=${range}`)
}
```

`frontend/src/metrics.ts`:

```ts
import type { Series, Unit } from '@/api/metrics'
import { bytes } from '@/format'

/** uPlot wants one shared time axis: merge the timestamps, pad gaps with null. */
export function align(series: Series[]): [number[], ...(number | null)[][]] {
  const xs = [...new Set(series.flatMap((s) => s.points.map(([t]) => t)))].sort((a, b) => a - b)
  const rows = series.map((s) => {
    const byTime = new Map(s.points)
    return xs.map((t) => byTime.get(t) ?? null)
  })
  return [xs, ...rows]
}

export function formatValue(unit: Unit, v: number): string {
  if (unit === 'percent') return `${v.toFixed(1)}%`
  if (unit === 'bytes') return bytes(v)
  if (unit === 'bytes_per_s') return `${bytes(v)}/s`
  return String(v)
}
```

`frontend/src/stores/metrics.ts`:

```ts
import { defineStore } from 'pinia'
import { computed, reactive, ref } from 'vue'

import { getPanels, getRange, type PanelInfo, type RangeId, type Series } from '@/api/metrics'

const ROW_ORDER = ['host', 'containers', 'portainer'] as const

interface PanelState {
  loading: boolean
  error: string | null
  series: Series[]
}

export const useMetricsStore = defineStore('metrics', () => {
  const panels = ref<PanelInfo[]>([])
  const range = ref<RangeId>('6h')
  const refreshSeconds = ref(0)
  const state = reactive<Record<string, PanelState>>({})
  let timer: ReturnType<typeof setInterval> | undefined

  const rows = computed(() =>
    ROW_ORDER.map((row) => ({ row, panels: panels.value.filter((p) => p.row === row) })).filter(
      (r) => r.panels.length > 0,
    ),
  )

  async function loadPanel(id: string) {
    const asked = range.value
    const entry = (state[id] ??= { loading: false, error: null, series: [] })
    entry.loading = true
    try {
      const body = await getRange(id, asked)
      // The range moved on while this request was in flight: drop the answer.
      if (asked !== range.value) return
      entry.series = body.series
      entry.error = null
    } catch (e) {
      if (asked !== range.value) return
      entry.error = e instanceof Error ? e.message : String(e)
    } finally {
      if (asked === range.value) entry.loading = false
    }
  }

  function refresh() {
    return Promise.all(panels.value.map((p) => loadPanel(p.id)))
  }

  async function load() {
    try {
      panels.value = await getPanels()
    } catch {
      return // the section stays empty; the status cards above still work
    }
    await refresh()
  }

  function setRange(next: RangeId) {
    range.value = next
    void refresh()
  }

  function stop() {
    if (timer !== undefined) clearInterval(timer)
    timer = undefined
  }

  function setRefresh(seconds: number) {
    refreshSeconds.value = seconds
    stop()
    if (seconds > 0) {
      timer = setInterval(() => {
        if (!document.hidden) void refresh()
      }, seconds * 1000)
    }
  }

  return { panels, range, refreshSeconds, state, rows, load, setRange, setRefresh, stop }
})
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd frontend && npx vitest run tests/unit/metrics-helpers.test.ts tests/unit/stores-metrics.test.ts && npm run build`
Expected: PASS; `vue-tsc` clean.

- [ ] **Step 5: Commit** (only when asked)

```bash
git add frontend/src frontend/tests
git commit -m "feat: metrics api module and store"
```

---

### Task 4: uPlot panel component and dashboard wiring

**Files:**
- Modify: `frontend/package.json` + lockfile (via `npm install uplot`), `frontend/src/views/HomeView.vue`, `frontend/tests/unit/HomeView.test.ts`
- Create: `frontend/src/components/MetricPanel.vue`, `frontend/src/components/MetricsSection.vue`
- Test: `frontend/tests/unit/MetricPanel.test.ts`, `frontend/tests/unit/MetricsSection.test.ts`

**Interfaces:**
- Consumes: Task 3 (`align`, `formatValue`, `useMetricsStore`, `PanelInfo`, `Series`, `RangeId`).
- Produces: `<MetricPanel :panel="PanelInfo" :series="Series[]" :loading="boolean" :error="string|null" />`; `<MetricsSection />` (header with range picker `data-test="range"` buttons and refresh `<select data-test="refresh">`, then one block per row of panels; mounts `store.load()`, calls `store.stop()` on unmount).

- [ ] **Step 1: Install uPlot**

Run: `cd frontend && npm install uplot`
Expected: `package.json` gains `"uplot"` under dependencies (version resolved by npm; keep the caret range it writes).

- [ ] **Step 2: Write the failing tests**

`frontend/tests/unit/MetricPanel.test.ts`:

```ts
import { mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'

import MetricPanel from '@/components/MetricPanel.vue'

const ctor = vi.fn()
const destroy = vi.fn()
const setData = vi.fn()
vi.mock('uplot', () => ({
  default: class {
    constructor(...args: unknown[]) {
      ctor(...args)
    }
    destroy = destroy
    setData = setData
    setSize = vi.fn()
  },
}))
vi.mock('uplot/dist/uPlot.min.css', () => ({}))

const panel = { id: 'host-cpu', title: 'CPU', row: 'host', unit: 'percent' } as const
const series = [{ label: 'CPU', points: [[1, 2], [2, 3]] as [number, number][] }]

beforeEach(() => vi.clearAllMocks())

it('draws a graph when there is data', () => {
  const wrapper = mount(MetricPanel, { props: { panel, series, loading: false, error: null } })

  expect(wrapper.text()).toContain('CPU')
  expect(ctor).toHaveBeenCalledOnce()
  expect(wrapper.find('[data-test="no-data"]').exists()).toBe(false)
})

it('says "No data" for an empty series list and draws nothing', () => {
  const wrapper = mount(MetricPanel, { props: { panel, series: [], loading: false, error: null } })

  expect(wrapper.find('[data-test="no-data"]').exists()).toBe(true)
  expect(ctor).not.toHaveBeenCalled()
})

it('shows the error inline', () => {
  const wrapper = mount(MetricPanel, { props: { panel, series: [], loading: false, error: 'boom' } })

  expect(wrapper.find('[data-test="panel-error"]').text()).toBe('boom')
})

it('updates in place when data changes and destroys on unmount', async () => {
  const wrapper = mount(MetricPanel, { props: { panel, series, loading: false, error: null } })

  await wrapper.setProps({ series: [{ label: 'CPU', points: [[3, 4]] }] })
  expect(setData).toHaveBeenCalled()

  wrapper.unmount()
  expect(destroy).toHaveBeenCalled()
})
```

`frontend/tests/unit/MetricsSection.test.ts`:

```ts
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

import { getPanels, getRange } from '@/api/metrics'
import MetricsSection from '@/components/MetricsSection.vue'
import { useMetricsStore } from '@/stores/metrics'

vi.mock('@/api/metrics', () => ({ getPanels: vi.fn(), getRange: vi.fn() }))
vi.mock('@/components/MetricPanel.vue', () => ({
  default: { props: ['panel'], template: '<div data-test="panel">{{ panel.title }}</div>' },
}))

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(getPanels).mockResolvedValue([
    { id: 'host-cpu', title: 'CPU', row: 'host', unit: 'percent' },
    { id: 'portainer-up', title: 'Up', row: 'portainer', unit: 'count' },
  ])
  vi.mocked(getRange).mockResolvedValue({ panel: 'x', range: '6h', series: [] })
})

it('renders one block per row with a panel per catalog entry', async () => {
  const wrapper = mount(MetricsSection)
  await flushPromises()

  expect(wrapper.findAll('[data-test="panel"]').map((p) => p.text())).toEqual(['CPU', 'Up'])
  expect(wrapper.findAll('h3').map((h) => h.text())).toEqual(['Host', 'Portainer'])
})

it('the range buttons and refresh select drive the store', async () => {
  const wrapper = mount(MetricsSection)
  await flushPromises()
  const store = useMetricsStore()

  await wrapper.findAll('[data-test="range"]')[2].trigger('click') // 24h
  expect(store.range).toBe('24h')

  await wrapper.find('[data-test="refresh"]').setValue('30')
  expect(store.refreshSeconds).toBe(30)
})

it('stops the refresh timer when it unmounts', async () => {
  const wrapper = mount(MetricsSection)
  await flushPromises()
  const stop = vi.spyOn(useMetricsStore(), 'stop')

  wrapper.unmount()

  expect(stop).toHaveBeenCalled()
})
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd frontend && npx vitest run tests/unit/MetricPanel.test.ts tests/unit/MetricsSection.test.ts`
Expected: FAIL (components missing).

- [ ] **Step 4: Implement**

`frontend/src/components/MetricPanel.vue`:

```vue
<script setup lang="ts">
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { PanelInfo, Series } from '@/api/metrics'
import { align, formatValue } from '@/metrics'

const props = defineProps<{
  panel: PanelInfo
  series: Series[]
  loading: boolean
  error: string | null
}>()

const COLORS = ['#61afef', '#98c379', '#e5c07b', '#c678dd', '#56b6c2']

const host = ref<HTMLElement>()
let plot: uPlot | undefined
let observer: ResizeObserver | undefined

function build() {
  if (!host.value || props.series.length === 0) return
  const text = getComputedStyle(host.value).color
  plot = new uPlot(
    {
      width: host.value.clientWidth || 300,
      height: 160,
      series: [
        {},
        ...props.series.map((s, i) => ({
          label: s.label,
          stroke: COLORS[i % COLORS.length],
          width: 2,
          points: { show: false },
          value: (_: uPlot, v: number | null) => (v === null ? '—' : formatValue(props.panel.unit, v)),
        })),
      ],
      axes: [
        { stroke: text, grid: { stroke: 'rgba(128,128,128,.2)' } },
        {
          stroke: text,
          grid: { stroke: 'rgba(128,128,128,.2)' },
          size: 70,
          values: (_: uPlot, ticks: number[]) => ticks.map((t) => formatValue(props.panel.unit, t)),
        },
      ],
    },
    align(props.series),
    host.value,
  )
}

function destroy() {
  plot?.destroy()
  plot = undefined
}

onMounted(() => {
  build()
  if (typeof ResizeObserver !== 'undefined' && host.value) {
    observer = new ResizeObserver(() => {
      if (plot && host.value) plot.setSize({ width: host.value.clientWidth, height: 160 })
    })
    observer.observe(host.value)
  }
})

watch(
  () => props.series,
  (next, prev) => {
    const sameShape = plot && next.length === prev.length && next.every((s, i) => s.label === prev[i].label)
    if (sameShape) plot!.setData(align(next))
    else {
      destroy()
      build()
    }
  },
)

onBeforeUnmount(() => {
  observer?.disconnect()
  destroy()
})
</script>

<template>
  <article class="panel">
    <h4>{{ panel.title }}</h4>
    <p v-if="error" class="error" data-test="panel-error">{{ error }}</p>
    <p v-else-if="!series.length && !loading" data-test="no-data">No data</p>
    <p v-else-if="!series.length">Loading…</p>
    <div ref="host" class="plot"></div>
  </article>
</template>

<style scoped>
.panel {
  min-width: 0;
}
h4 {
  margin: 0 0 0.25rem;
}
.error {
  color: #e06c75;
}
</style>
```

Note for the implementer: the `watch` destroys and rebuilds when the series list changes shape (e.g. a container drops out of the top 5), because `host` must exist before `build()` can run on the next tick: keep `<div ref="host">` always rendered (it is, above), so no extra `nextTick` is needed.

`frontend/src/components/MetricsSection.vue`:

```vue
<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'

import type { RangeId } from '@/api/metrics'
import MetricPanel from '@/components/MetricPanel.vue'
import { useMetricsStore } from '@/stores/metrics'

const store = useMetricsStore()
const RANGES: RangeId[] = ['1h', '6h', '24h', '7d']
const REFRESH = [
  { label: 'Off', seconds: 0 },
  { label: '30 s', seconds: 30 },
  { label: '1 min', seconds: 60 },
]
const TITLES = { host: 'Host', containers: 'Containers', portainer: 'Portainer' }

onMounted(() => void store.load())
onBeforeUnmount(() => store.stop())
</script>

<template>
  <section class="metrics">
    <header>
      <h2>Metrics</h2>
      <div class="controls">
        <button
          v-for="r in RANGES"
          :key="r"
          data-test="range"
          :aria-pressed="store.range === r"
          :class="{ active: store.range === r }"
          @click="store.setRange(r)"
        >
          {{ r }}
        </button>
        <label>
          Refresh
          <select
            data-test="refresh"
            :value="store.refreshSeconds"
            @change="store.setRefresh(Number(($event.target as HTMLSelectElement).value))"
          >
            <option v-for="o in REFRESH" :key="o.seconds" :value="o.seconds">{{ o.label }}</option>
          </select>
        </label>
      </div>
    </header>

    <div v-for="r in store.rows" :key="r.row">
      <h3>{{ TITLES[r.row] }}</h3>
      <div class="grid">
        <MetricPanel
          v-for="p in r.panels"
          :key="p.id"
          :panel="p"
          :series="store.state[p.id]?.series ?? []"
          :loading="store.state[p.id]?.loading ?? true"
          :error="store.state[p.id]?.error ?? null"
        />
      </div>
    </div>
  </section>
</template>

<style scoped>
header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}
.controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.25rem;
}
button.active {
  font-weight: 700;
  text-decoration: underline;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(20rem, 1fr));
  gap: 1rem;
}
</style>
```

`HomeView.vue`: import `MetricsSection` and add `<MetricsSection />` as the last child of the `<section>`, rendered only when a household exists:

```vue
    <MetricsSection v-if="household.household" />
```

(placed right after the closing `</div>` of `.grid`; add `import MetricsSection from '@/components/MetricsSection.vue'`.)

`frontend/tests/unit/HomeView.test.ts`: it mounts the real HomeView, which would now call the metrics API. Add near the other `vi.mock` calls: `vi.mock('@/components/MetricsSection.vue', () => ({ default: { template: '<div data-test="metrics" />' } }))`, and one assertion that `[data-test="metrics"]` exists when a household is loaded and not when it is missing. Match the file's existing mocking style (read it first).

- [ ] **Step 5: Run the whole frontend gate**

Run: `cd frontend && npm run test && npm run build`
Expected: all tests PASS (186 existing + new), build clean, no Vue warnings. Then start the dev server (`npm run dev`) against the real backend and check by eye with the in-app browser: panels appear, the range buttons redraw, a deliberately wrong `DARKANGEL_PROMETHEUS_URL` turns host/container panels into inline errors while the Portainer row still draws.

- [ ] **Step 6: Commit** (only when asked)

```bash
git add frontend
git commit -m "feat: grafana-like metrics panels on the home dashboard"
```

---

### Task 5: Docs and deployment check

**Files:**
- Modify: `docs/dashboard.md`, `CLAUDE.md` (one line under Architecture), possibly `deploy/portainer-stack.yml`

- [ ] **Step 1: Verify the network**

Prometheus must be reachable as `prometheus` from `darkangel-api`. The API is on the external network `infra-net`. Run on the Docker host (or ask the user): `docker inspect <prometheus container> --format '{{json .NetworkSettings.Networks}}'`. If Prometheus is on `infra-net`, nothing changes in the stack. If not, report it to the user rather than guessing a network name.

- [ ] **Step 2: Document**

Append to `docs/dashboard.md` a section "Metrics panels": the three rows and their sources (node-exporter, cAdvisor, our DB), that `DARKANGEL_PROMETHEUS_URL` defaults to `http://prometheus:9090` (no stack variable needed), that a down Prometheus only blanks the host/container panels, that PromQL lives in `backend/app/metrics/catalog.py` (adding a panel = one entry there plus the pinned id list in `tests/regression/test_metrics_catalog.py`; the OpenAPI snapshot does not change), and the 30-day history limit of the Portainer row versus whatever Prometheus retains for the others.

Add to the `CLAUDE.md` Architecture bullet list, under `core/household.py`: one line, `api/routes/metrics.py` + `metrics/` — fixed PromQL catalog proxied to Prometheus, plus DB-backed Portainer history; the SPA draws it with uPlot (`components/MetricsSection.vue`).

- [ ] **Step 3: Final gate**

Run: `make verify` (or the individual backend and frontend commands from Global Constraints).
Expected: everything green.

- [ ] **Step 4: Commit** (only when asked)

```bash
git add docs CLAUDE.md deploy
git commit -m "docs: metrics panels"
```
