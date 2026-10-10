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
    return httpx.Response(
        200, json={"status": "success", "data": {"resultType": "matrix", "result": list(results)}}
    )


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

    ((_, points),) = client_for(lambda r: body).query_range("up", END, END, 15)

    assert points == [(3.0, 2.0)]


def test_empty_result_is_an_empty_list():
    assert client_for(lambda r: matrix()).query_range("up", END, END, 15) == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"status": "error", "error": "bad query"}),
        httpx.Response(
            200, json={"status": "success", "data": {"resultType": "vector", "result": []}}
        ),
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
