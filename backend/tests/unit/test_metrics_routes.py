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
    infra.add_status(
        instance="heaven", reachable=True, stacks=7, checked_at=NOW - timedelta(minutes=10)
    )
    infra.add_status(instance="heaven", reachable=False, checked_at=NOW - timedelta(minutes=5))
    infra.add_status(instance="old", reachable=True, stacks=1, checked_at=NOW - timedelta(hours=3))
    infra.add_backup(
        instance="heaven",
        last_backup_at=NOW,
        size_bytes=900,
        object_key="k",
        checked_at=NOW - timedelta(minutes=5),
    )

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


def test_db_panel_without_a_handler_is_a_500_not_other_data(home, infra, prom, monkeypatch):
    from app.metrics import catalog

    orphan = catalog.Panel("zz-db", "Z", "portainer", "count", "db")
    monkeypatch.setattr(catalog, "get", lambda panel_id: orphan)

    assert get("zz-db").status_code == 500
