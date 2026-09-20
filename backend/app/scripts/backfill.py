"""One-shot: move legacy `<sub>/<name>` objects to `<sub>/<uuid>` and index them.

    make backfill ARGS=--dry-run     # list what would move, change nothing
    make backfill ARGS=--confirm     # do it

MIRROR THE BUCKET FIRST. This rewrites live data, and the server-side copy
starts a fresh version chain: MinIO versions predating the move do not follow
the object to its new key.

    mc mirror --preserve infra/darkangel-files ./darkangel-files-backup

Idempotent: a key already recorded in files.object_key is skipped, so a run
interrupted halfway can simply be repeated.
"""

import argparse
import uuid

from minio.commonconfig import CopySource
from sqlalchemy import select

from app.api.routes.files import minio_client
from app.core.config import get_settings
from app.core.db import session_factory
from app.models.files import File, FileVersion

MIRROR_WARNING = (
    "backfill rewrites live objects. Mirror the bucket first "
    "(`mc mirror --preserve infra/darkangel-files ./backup`), then pass confirm=True."
)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def backfill(*, confirm: bool = False, dry_run: bool = False) -> list[tuple[str, str]]:
    if not confirm:
        raise RuntimeError(MIRROR_WARNING)

    settings = get_settings()
    client = minio_client()
    moved: list[tuple[str, str]] = []

    with session_factory()() as session:
        known = set(session.scalars(select(File.object_key)).all())

        for obj in client.list_objects(settings.s3_bucket, recursive=True):
            old_key = obj.object_name
            owner_sub, _, name = old_key.partition("/")
            if not name or _is_uuid(name) or old_key in known:
                continue

            file_id = uuid.uuid4()
            new_key = f"{owner_sub}/{file_id}"
            moved.append((old_key, new_key))
            if dry_run:
                continue

            written = client.copy_object(
                settings.s3_bucket, new_key, CopySource(settings.s3_bucket, old_key)
            )
            stat = client.stat_object(settings.s3_bucket, new_key)
            row = File(
                id=file_id,
                owner_sub=owner_sub,
                folder_id=None,
                name=name,
                content_type=stat.content_type or "application/octet-stream",
                size_bytes=stat.size,
                object_key=new_key,
                status="ready",
            )
            version = FileVersion(
                id=uuid.uuid4(),
                file_id=file_id,
                version_no=1,
                s3_version_id=written.version_id or "",
                size_bytes=stat.size,
                content_type=row.content_type,
                created_by=owner_sub,
            )
            row.current_version_id = version.id
            session.add(row)
            session.add(version)
            session.commit()

            # Only after the row is committed: a crash here leaves a duplicate
            # object, which the next run skips, rather than a lost file.
            client.remove_object(settings.s3_bucket, old_key)

    return moved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="list the moves, change nothing")
    parser.add_argument("--confirm", action="store_true", help="acknowledge the mirror warning")
    args = parser.parse_args()

    moved = backfill(confirm=args.confirm or args.dry_run, dry_run=args.dry_run)
    for old_key, new_key in moved:
        print(f"{'would move' if args.dry_run else 'moved'} {old_key} -> {new_key}")
    print(f"{len(moved)} object(s)")


if __name__ == "__main__":
    main()
