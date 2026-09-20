import uuid
from contextlib import suppress
from datetime import datetime
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query, Response, UploadFile, status
from minio import Minio
from minio.error import S3Error
from pydantic import BaseModel

from app.core.auth import Claims
from app.core.config import get_settings
from app.models.files import File
from app.repositories.files import FileRepo

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


def _prefix(claims: Claims) -> str:
    # Each user owns the keys under their Keycloak `sub`: nobody lists or reads
    # another user's files.
    return f"{claims['sub']}/"


def _key(claims: Claims, name: str) -> str:
    # A path parameter never holds a `/`, but an upload's filename can.
    if name in ("", ".", "..") or "/" in name or "\\" in name or len(name) > 255:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid file name")
    return _prefix(claims) + name


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


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
def upload_file(claims: Claims, file: UploadFile) -> Response:
    # Same name overwrites; the bucket is versioned, so the old copy is kept.
    minio_client().put_object(
        get_settings().s3_bucket,
        _key(claims, file.filename or ""),
        file.file,
        length=-1,
        part_size=10 * 1024 * 1024,
        content_type=file.content_type or "application/octet-stream",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{file_id}", response_model=FileInfo)
def get_file(claims: Claims, repo: FileRepo, file_id: uuid.UUID) -> FileInfo:
    row = repo.get(claims["sub"], file_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such file")
    return FileInfo.of(row)
