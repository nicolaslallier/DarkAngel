from collections.abc import Iterator
from datetime import datetime
from functools import lru_cache
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from minio import Minio
from minio.error import S3Error
from pydantic import BaseModel

from app.core.auth import Claims
from app.core.config import get_settings

router = APIRouter(prefix="/files", tags=["files"])


class FileInfo(BaseModel):
    name: str
    size: int
    modified: datetime | None


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


@router.get("", response_model=list[FileInfo])
def list_files(claims: Claims) -> list[FileInfo]:
    prefix = _prefix(claims)
    objects = minio_client().list_objects(get_settings().s3_bucket, prefix=prefix)
    return [
        FileInfo(name=o.object_name.removeprefix(prefix), size=o.size, modified=o.last_modified)
        for o in objects
    ]


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


@router.get("/{name}")
def download_file(claims: Claims, name: str) -> StreamingResponse:
    try:
        obj = minio_client().get_object(get_settings().s3_bucket, _key(claims, name))
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
        media_type=obj.headers.get("Content-Type", "application/octet-stream"),
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "Content-Length": obj.headers["Content-Length"],
        },
    )


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(claims: Claims, name: str) -> Response:
    minio_client().remove_object(get_settings().s3_bucket, _key(claims, name))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
