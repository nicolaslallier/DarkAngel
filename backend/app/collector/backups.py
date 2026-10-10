from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from minio import Minio


@dataclass(frozen=True)
class Archive:
    last_backup_at: datetime
    size_bytes: int
    object_key: str


def newest(client: Minio, bucket: str, instance: str) -> Archive | None:
    """The most recent object under `<instance>/`, or None when there is none."""
    objects = [
        o
        for o in client.list_objects(bucket, prefix=f"{instance}/", recursive=True)
        if not o.is_dir
    ]
    if not objects:
        return None
    latest = max(objects, key=lambda o: o.last_modified)
    return Archive(latest.last_modified, latest.size, latest.object_name)
