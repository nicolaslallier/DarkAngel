import os
import uuid
from collections.abc import Iterator
from contextlib import suppress
from datetime import datetime
from functools import lru_cache
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from minio import Minio
from minio.error import S3Error
from pydantic import BaseModel

from app.core.auth import Claims
from app.core.config import get_settings
from app.models.files import File
from app.repositories.files import FileRepo, QuotaExceeded

router = APIRouter(prefix="/files", tags=["files"])


class FileInfo(BaseModel):
    id: uuid.UUID
    name: str
    size: int
    content_type: str
    modified: datetime | None

    @classmethod
    def of(cls, row: File) -> "FileInfo":
        return cls(
            id=row.id,
            name=row.name,
            size=row.size_bytes,
            content_type=row.content_type,
            modified=row.updated_at,
        )


@lru_cache
def minio_client() -> Minio:
    s = get_settings()
    return Minio(
        s.s3_endpoint, access_key=s.s3_access_key, secret_key=s.s3_secret_key, secure=s.s3_secure
    )


def _sweep(repo: FileRepo) -> None:
    """Drop reservations whose upload never finished, and the bytes they may
    have left behind."""
    bucket = get_settings().s3_bucket
    for key in repo.sweep_pending():
        with suppress(S3Error):
            minio_client().remove_object(bucket, key)


@router.get("", response_model=list[FileInfo])
def list_files(
    claims: Claims,
    repo: FileRepo,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[FileInfo]:
    # There is no scheduler in this stack, so the sweep rides along here. It is
    # a single indexed DELETE over a table that is almost always empty.
    # ponytail: inline sweep; move to a cron if list latency ever suffers
    _sweep(repo)
    return [FileInfo.of(row) for row in repo.list(claims["sub"], limit=limit, offset=offset)]


def _validated_name(raw: str) -> str:
    """Validate a *display* name.

    The object key is a UUID now, so a slash in the name can no longer escape
    the owner's prefix -- traversal is structurally impossible rather than
    merely filtered. What is left are display rules: something non-empty, no
    control characters, and short enough to show in a table.
    """
    name = raw.strip()
    if not name or name in (".", "..") or len(name) > 255:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid file name")
    if any(character < " " or character == "\x7f" for character in name):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid file name")
    return name


def _measure(stream) -> int:
    """Exact size of an already-buffered upload. Starlette has read the whole
    body before the handler runs, so this is authoritative -- the
    Content-Length check below is only there to reject the obvious early."""
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    return size


@router.post("", response_model=FileInfo, responses={201: {"model": FileInfo}})
def upload_file(claims: Claims, repo: FileRepo, file: UploadFile, response: Response) -> FileInfo:
    settings = get_settings()
    name = _validated_name(file.filename or "")

    if any(name.lower().endswith(suffix) for suffix in settings.denied_extensions):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"Files of this type are not accepted: {name}"
        )

    size = _measure(file.file)
    if size > settings.max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File is {size} bytes; the limit is {settings.max_upload_bytes}",
        )

    content_type = file.content_type or "application/octet-stream"

    existing = repo.find_by_name(claims["sub"], name, None)
    if existing is not None:
        return _append_version(claims, repo, existing, file, size, content_type)

    try:
        row = repo.reserve(
            claims["sub"],
            name=name,
            folder_id=None,
            size_bytes=size,
            content_type=content_type,
            quota_bytes=settings.user_quota_bytes,
        )
    except QuotaExceeded as e:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Quota exceeded: {e.used} of {e.limit} bytes used, {e.needed} more needed",
        ) from e

    try:
        written = minio_client().put_object(
            settings.s3_bucket, row.object_key, file.file, length=size, content_type=content_type
        )
    except S3Error as e:
        # The reservation is released immediately rather than waiting for the
        # sweeper, so a storage outage does not eat anyone's quota for an hour.
        repo.abandon(row)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Storage is unavailable") from e

    repo.finalize(row, s3_version_id=written.version_id or "", actor_sub=claims["sub"])
    repo.audit(claims["sub"], "upload", "file", row.id, {"name": name, "size": size})
    response.status_code = status.HTTP_201_CREATED
    return FileInfo.of(row)


def _append_version(
    claims: Claims, repo: FileRepo, existing: File, file: UploadFile, size: int, content_type: str
) -> FileInfo:
    """BR-2: a same-name upload into the same folder is a new version of that
    file, not a second file. This is what today's overwrite becomes once the
    bucket's versions are indexed."""
    settings = get_settings()

    # The delta, because the old version's bytes are about to stop counting.
    # This path has no `pending` reservation to hold a lock on -- the row is
    # already `ready` -- so the check below is advisory: a concurrent pair of
    # version uploads could briefly exceed the quota by one delta.
    # ponytail: version uploads check quota without the advisory lock; add
    # reserve_version if overshoot ever matters
    projected = repo.used_bytes(claims["sub"]) - existing.size_bytes + size
    if projected > settings.user_quota_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Quota exceeded: {projected} bytes needed, the limit is {settings.user_quota_bytes}",
        )

    try:
        written = minio_client().put_object(
            settings.s3_bucket,
            existing.object_key,
            file.file,
            length=size,
            content_type=content_type,
        )
    except S3Error as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Storage is unavailable") from e

    repo.add_version(
        existing,
        s3_version_id=written.version_id or "",
        size_bytes=size,
        content_type=content_type,
        actor_sub=claims["sub"],
    )
    repo.audit(claims["sub"], "upload", "file", existing.id, {"name": existing.name, "size": size})
    return FileInfo.of(existing)


@router.get("/{file_id}", response_model=FileInfo)
def get_file(claims: Claims, repo: FileRepo, file_id: uuid.UUID) -> FileInfo:
    row = repo.get(claims["sub"], file_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such file")
    return FileInfo.of(row)


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(claims: Claims, repo: FileRepo, file_id: uuid.UUID) -> Response:
    """Soft: the row is marked, the bytes stay. From the outside this is what
    today's delete already looks like -- the bucket is versioned, so even the
    old hard delete only ever wrote a delete marker. The Trash that makes the
    difference visible is Phase 3."""
    row = repo.get(claims["sub"], file_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such file")

    repo.soft_delete(row)
    repo.audit(claims["sub"], "delete", "file", row.id, {"name": row.name})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{file_id}/content")
def download_file(
    claims: Claims,
    repo: FileRepo,
    file_id: uuid.UUID,
    disposition: Literal["attachment", "inline"] = "attachment",
) -> StreamingResponse:
    row = repo.get(claims["sub"], file_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such file")

    settings = get_settings()
    # The SPA and the API share an origin, so anything rendered inline runs in
    # it. Only the allow-list is ever rendered, and only the allow-list keeps
    # its own content type -- everything else downloads as opaque bytes.
    renderable = row.content_type in settings.inline_content_types
    mode = "inline" if (disposition == "inline" and renderable) else "attachment"
    served_type = row.content_type if renderable else "application/octet-stream"

    try:
        obj = minio_client().get_object(settings.s3_bucket, row.object_key)
    except S3Error as e:
        if e.code == "NoSuchKey":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such file") from e
        raise

    def chunks() -> Iterator[bytes]:
        try:
            yield from obj.stream(64 * 1024)
        finally:
            obj.close()
            obj.release_conn()

    return StreamingResponse(
        chunks(),
        media_type=served_type,
        headers={
            # safe="" matters: a display name may hold a '/' now, and quote()
            # would otherwise leave it raw and split the header value.
            "Content-Disposition": f"{mode}; filename*=UTF-8''{quote(row.name, safe='')}",
            "Content-Length": obj.headers["Content-Length"],
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )
