import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.routes.files import _json_id, _live_folder, _validated_name
from app.core.auth import Claims
from app.models.files import Folder
from app.repositories.files import FileRepo, NameTaken
from app.repositories.folders import FolderRepo

router = APIRouter(prefix="/folders", tags=["folders"])


class FolderInfo(BaseModel):
    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None

    @classmethod
    def of(cls, row: Folder) -> "FolderInfo":
        return cls(id=row.id, name=row.name, parent_id=row.parent_id)


class FolderCreate(BaseModel):
    name: str
    parent_id: uuid.UUID | None = None


class FolderPatch(BaseModel):
    """Omitted = unchanged; an explicit `parent_id: null` = move to the root.
    `model_fields_set` is what tells those two apart."""

    name: str | None = None
    parent_id: uuid.UUID | None = None


class FolderNotEmpty(BaseModel):
    detail: str
    folders: int
    files: int


def _taken(name: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, f"A folder named {name} already exists here")


@router.get("", response_model=list[FolderInfo])
def list_folders(claims: Claims, folders: FolderRepo) -> list[FolderInfo]:
    return [FolderInfo.of(row) for row in folders.list(claims["sub"])]


@router.post("", response_model=FolderInfo, status_code=status.HTTP_201_CREATED)
def create_folder(
    claims: Claims, folders: FolderRepo, repo: FileRepo, body: FolderCreate
) -> FolderInfo:
    sub = claims["sub"]
    name = _validated_name(body.name)
    if body.parent_id is not None:
        _live_folder(folders, sub, body.parent_id)
    try:
        row = folders.create(sub, name=name, parent_id=body.parent_id)
    except NameTaken as e:
        raise _taken(name) from e
    detail = {"name": name, "parent_id": _json_id(body.parent_id)}
    repo.audit(sub, "folder_create", "folder", row.id, detail)
    return FolderInfo.of(row)


@router.patch("/{folder_id}", response_model=FolderInfo)
def update_folder(
    claims: Claims, folders: FolderRepo, repo: FileRepo, folder_id: uuid.UUID, body: FolderPatch
) -> FolderInfo:
    sub = claims["sub"]
    row = _live_folder(folders, sub, folder_id)
    before_name, before_parent = row.name, row.parent_id

    changes: dict[str, Any] = {}
    if body.name is not None and (name := _validated_name(body.name)) != row.name:
        changes["name"] = name
    if "parent_id" in body.model_fields_set and body.parent_id != row.parent_id:
        if body.parent_id is not None:
            _live_folder(folders, sub, body.parent_id)
            if folders.is_cycle(sub, row.id, body.parent_id):
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "A folder cannot move into itself or below itself"
                )
        changes["parent_id"] = body.parent_id

    if changes:
        try:
            folders.update(row, **changes)
        except NameTaken as e:
            raise _taken(changes.get("name", before_name)) from e
    if "name" in changes:
        detail = {"before": before_name, "after": changes["name"]}
        repo.audit(sub, "folder_rename", "folder", row.id, detail)
    if "parent_id" in changes:
        detail = {"before": _json_id(before_parent), "after": _json_id(changes["parent_id"])}
        repo.audit(sub, "folder_move", "folder", row.id, detail)
    return FolderInfo.of(row)


@router.delete(
    "/{folder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={409: {"model": FolderNotEmpty}},
)
def delete_folder(
    claims: Claims,
    folders: FolderRepo,
    repo: FileRepo,
    folder_id: uuid.UUID,
    recursive: bool = False,
) -> Response:
    """BR-7: soft, and a non-empty folder needs `?recursive=true`. The 409
    carries the subtree's counts so the SPA can ask before retrying."""
    sub = claims["sub"]
    row = _live_folder(folders, sub, folder_id)
    child_folders, child_files = folders.subtree_counts(sub, row.id)
    if (child_folders or child_files) and not recursive:
        body = FolderNotEmpty(
            detail=f"{row.name} is not empty ({child_folders} folders, {child_files} files)",
            folders=child_folders,
            files=child_files,
        )
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=body.model_dump())

    trashed_folders, trashed_files = folders.soft_delete_subtree(sub, row.id)
    detail = {"name": row.name, "folders": trashed_folders, "files": trashed_files}
    repo.audit(sub, "folder_delete", "folder", row.id, detail)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
