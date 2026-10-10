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
    Panel(
        "host-cpu",
        "CPU",
        "host",
        "percent",
        "prom",
        '100 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100',
    ),
    Panel(
        "host-memory",
        "Memory",
        "host",
        "percent",
        "prom",
        "100 * (1 - sum(node_memory_MemAvailable_bytes) / sum(node_memory_MemTotal_bytes))",
    ),
    Panel(
        "host-disk",
        "Disk (/)",
        "host",
        "percent",
        "prom",
        '100 * (1 - sum(node_filesystem_avail_bytes{mountpoint="/"})'
        ' / sum(node_filesystem_size_bytes{mountpoint="/"}))',
    ),
    Panel(
        "host-net-in",
        "Network in",
        "host",
        "bytes_per_s",
        "prom",
        f"sum(rate(node_network_receive_bytes_total{{{_NIC}}}[5m]))",
    ),
    Panel(
        "host-net-out",
        "Network out",
        "host",
        "bytes_per_s",
        "prom",
        f"sum(rate(node_network_transmit_bytes_total{{{_NIC}}}[5m]))",
    ),
    Panel(
        "containers-cpu",
        "Top containers by CPU",
        "containers",
        "percent",
        "prom",
        'topk(5, sum by (name) (rate(container_cpu_usage_seconds_total{name!=""}[5m])) * 100)',
    ),
    Panel(
        "containers-memory",
        "Top containers by memory",
        "containers",
        "bytes",
        "prom",
        'topk(5, max by (name) (container_memory_working_set_bytes{name!=""}))',
    ),
    Panel("portainer-up", "Portainer reachable", "portainer", "count", "db"),
    Panel("portainer-stacks", "Stacks", "portainer", "count", "db"),
    Panel("portainer-backup-size", "Backup size", "portainer", "bytes", "db"),
)

_BY_ID = {p.id: p for p in PANELS}


def get(panel_id: str) -> Panel | None:
    return _BY_ID.get(panel_id)
