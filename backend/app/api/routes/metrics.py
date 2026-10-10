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
