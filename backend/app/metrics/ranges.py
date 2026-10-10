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
