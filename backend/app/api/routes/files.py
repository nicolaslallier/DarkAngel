import os
import uuid
from collections.abc import Iterator
from contextlib import suppress
from datetime import datetime
from functools import lru_cache
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from minio import Minio
from minio.error import S3Error
from pydantic import BaseModel, Field

from app.core.auth import Claims
from app.core.config import get_settings
from app.models.files import File, Folder
from app.repositories.files import FileRepo, NameTaken, Order, QuotaExceeded, Sort
from app.repositories.folders import FolderRepo, FolderRepository

router = APIRouter(prefix="/files", tags=["files"])


class FileInfo(BaseModel):
    id: uuid.UUID
    name: str
    size: int
    content_type: str
    modified: datetime | None
    folder_id: uuid.UUID | None
    description: str | None
    tags: list[str]

    @classmethod
    def of(cls, row: File) -> "FileInfo":
        return cls(
            id=row.id,
            name=row.name,
            size=row.size_bytes,
            content_type=row.content_type,
            modified=row.updated_at,
            folder_id=row.folder_id,
            description=row.description,
            tags=row.tags,
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
        # Exception, not S3Error: an unreachable MinIO raises urllib3's
        # MaxRetryError, which is no relation to S3Error. The sweep is
        # opportunistic cleanup riding along on a listing that needs no object
        # storage at all, so it must never be what fails that listing (§12).
        # ponytail: sweep_pending commits the row DELETEs before these removes,
        # so an outage here orphans the bytes; deleting the object first would close it.
        with suppress(Exception):
            minio_client().remove_object(bucket, key)


@router.get("", response_model=list[FileInfo])
def list_files(
    claims: Claims,
    repo: FileRepo,
    folders: FolderRepo,
    folder_id: uuid.UUID | None = None,
    q: str | None = Query(None, max_length=200),
    tag: str | None = Query(None, max_length=50),
    sort: Sort = "updated_at",
    order: Order = "desc",
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[FileInfo]:
    """Without q/tag: one folder (absent = root). With either: every live file
    of the caller's, wherever it is -- the SPA shows a Location column then."""
    sub = claims["sub"]
    # There is no scheduler in this stack, so the sweep rides along here. It is
    # a single indexed DELETE over a table that is almost always empty.
    # ponytail: inline sweep; move to a cron if list latency ever suffers
    _sweep(repo)
    # A cleared search box sends `?q=`: that is "no search", not an error.
    q = (q or "").strip() or None
    tag = (tag or "").strip().lower() or None
    if q is None and tag is None and folder_id is not None:
        _live_folder(folders, sub, folder_id)
    rows = repo.list(
        sub, folder_id=folder_id, q=q, tag=tag, sort=sort, order=order, limit=limit, offset=offset
    )
    return [FileInfo.of(row) for row in rows]


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


def _live_folder(folders: FolderRepository, owner_sub: str, folder_id: uuid.UUID) -> Folder:
    """A live folder of the caller's, or 404 -- missing, foreign and trashed
    look the same from outside (files-feature.md §5)."""
    row = folders.get(owner_sub, folder_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such folder")
    return row


def _json_id(value: uuid.UUID | None) -> str | None:
    """audit_log.detail is JSONB, which cannot hold a UUID object."""
    return str(value) if value else None


def _measure(stream) -> int:
    """Exact size of an already-buffered upload. Starlette has read the whole
    body before the handler runs, so this is authoritative -- the
    Content-Length check below is only there to reject the obvious early."""
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    return size


@router.post("", response_model=FileInfo, responses={201: {"model": FileInfo}})
def upload_file(
    claims: Claims,
    repo: FileRepo,
    folders: FolderRepo,
    file: UploadFile,
    response: Response,
    folder_id: Annotated[uuid.UUID | None, Form()] = None,
) -> FileInfo:
    settings = get_settings()
    name = _validated_name(file.filename or "")
    if folder_id is not None:
        _live_folder(folders, claims["sub"], folder_id)

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

    existing = repo.find_by_name(claims["sub"], name, folder_id)
    if existing is not None:
        return _append_version(claims, repo, existing, file, size, content_type)

    try:
        row = repo.reserve(
            claims["sub"],
            name=name,
            folder_id=folder_id,
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


class FilePatch(BaseModel):
    """Omitted = unchanged; an explicit `folder_id: null` = move to the root."""

    name: str | None = None
    description: str | None = Field(None, max_length=2000)
    tags: list[str] | None = Field(None, max_length=200)
    folder_id: uuid.UUID | None = None


_TAGS_LIMIT_MESSAGE = "At most 20 tags of at most 50 characters"


def _validated_tags(raw: list[str]) -> list[str]:
    """Trimmed, lowercased, empties and repeats dropped, submission order kept."""
    tags: list[str] = []
    seen: set[str] = set()
    for tag in (t.strip().lower() for t in raw):
        if not tag:
            continue
        if len(tag) > 50:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, _TAGS_LIMIT_MESSAGE)
        if tag in seen:
            continue
        if len(tags) >= 20:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, _TAGS_LIMIT_MESSAGE)
        seen.add(tag)
        tags.append(tag)
    return tags


@router.patch("/{file_id}", response_model=FileInfo)
def update_file(
    claims: Claims, repo: FileRepo, folders: FolderRepo, file_id: uuid.UUID, body: FilePatch
) -> FileInfo:
    """Rename, describe, retag and move in one call. Metadata only: no MinIO
    call, because the object key is the id."""
    sub = claims["sub"]
    row = repo.get(sub, file_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such file")
    before = {"name": row.name, "description": row.description, "tags": list(row.tags)}
    before_folder = row.folder_id

    changes: dict[str, Any] = {}
    if body.name is not None and (name := _validated_name(body.name)) != row.name:
        changes["name"] = name
    description = body.description if (body.description or "").strip() else None
    if "description" in body.model_fields_set and description != row.description:
        changes["description"] = description
    if body.tags is not None and (tags := _validated_tags(body.tags)) != row.tags:
        changes["tags"] = tags
    if "folder_id" in body.model_fields_set and body.folder_id != row.folder_id:
        if body.folder_id is not None:
            _live_folder(folders, sub, body.folder_id)
        changes["folder_id"] = body.folder_id

    if changes:
        try:
            repo.update(row, **changes)
        except NameTaken as e:
            name = changes.get("name", before["name"])
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"A file named {name} already exists here"
            ) from e
    if "name" in changes:
        repo.audit(sub, "rename", "file", row.id, {"before": before["name"], "after": row.name})
    if "folder_id" in changes:
        detail = {"before": _json_id(before_folder), "after": _json_id(row.folder_id)}
        repo.audit(sub, "move", "file", row.id, detail)
    if "description" in changes or "tags" in changes:
        after = {"description": row.description, "tags": list(row.tags)}
        detail = {
            "before": {"description": before["description"], "tags": before["tags"]},
            "after": after,
        }
        repo.audit(sub, "retag", "file", row.id, detail)
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
