"""Regression — the panel catalog is pinned: a new or changed panel is a reviewed diff."""

from app.metrics.catalog import PANELS


def test_catalog_ids_in_order():
    assert [p.id for p in PANELS] == [
        "host-cpu",
        "host-memory",
        "host-disk",
        "host-net-in",
        "host-net-out",
        "containers-cpu",
        "containers-memory",
        "portainer-up",
        "portainer-stacks",
        "portainer-backup-size",
    ]
