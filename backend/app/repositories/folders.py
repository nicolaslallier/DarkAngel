from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import Select, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import Db
from app.models.files import File, Folder
from app.repositories.files import NameTaken


class FolderRepository:
    """Owner-scoped folder queries. Same rule as FileRepository: `owner_sub`
    comes first, so forgetting it is a TypeError rather than a data leak."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def list(self, owner_sub: str) -> Sequence[Folder]:
        statement = (
            select(Folder)
            .where(Folder.owner_sub == owner_sub, Folder.deleted_at.is_(None))
            .order_by(func.lower(Folder.name), Folder.id)
        )
        return self.db.scalars(statement).all()

    def get(self, owner_sub: str, folder_id: uuid.UUID) -> Folder | None:
        statement = select(Folder).where(
            Folder.id == folder_id, Folder.owner_sub == owner_sub, Folder.deleted_at.is_(None)
        )
        return self.db.scalars(statement).one_or_none()

    def create(self, owner_sub: str, *, name: str, parent_id: uuid.UUID | None) -> Folder:
        row = Folder(id=uuid.uuid4(), owner_sub=owner_sub, name=name, parent_id=parent_id)
        self.db.add(row)
        self._commit()
        return row

    def update(self, folder: Folder, **changes: Any) -> Folder:
        for field, value in changes.items():
            setattr(folder, field, value)
        self._commit()
        return folder

    def _commit(self) -> None:
        try:
            self.db.commit()
        except IntegrityError as e:
            self.db.rollback()
            raise NameTaken from e

    def is_cycle(self, owner_sub: str, folder_id: uuid.UUID, new_parent_id: uuid.UUID) -> bool:
        """BR-5: True if `new_parent_id` is `folder_id` or lies below it. Walks
        *up* from the new parent -- a chain of ancestors is short, a subtree
        need not be."""
        ancestors = (
            select(Folder.id, Folder.parent_id)
            .where(Folder.id == new_parent_id, Folder.owner_sub == owner_sub)
            .cte("ancestors", recursive=True)
        )
        ancestors = ancestors.union(
            select(Folder.id, Folder.parent_id)
            .join(ancestors, Folder.id == ancestors.c.parent_id)
            .where(Folder.owner_sub == owner_sub)
        )
        hit = select(ancestors.c.id).where(ancestors.c.id == folder_id).exists()
        return bool(self.db.scalar(select(hit)))

    def _subtree(self, owner_sub: str, folder_id: uuid.UUID) -> Select:
        """The live folder and every live folder below it."""
        tree = (
            select(Folder.id)
            .where(
                Folder.id == folder_id,
                Folder.owner_sub == owner_sub,
                Folder.deleted_at.is_(None),
            )
            .cte("subtree", recursive=True)
        )
        tree = tree.union_all(
            select(Folder.id)
            .join(tree, Folder.parent_id == tree.c.id)
            .where(Folder.owner_sub == owner_sub, Folder.deleted_at.is_(None))
        )
        return select(tree.c.id)

    @staticmethod
    def _live_files_in(owner_sub: str, folder_ids: Sequence[uuid.UUID]) -> tuple:
        return (
            File.owner_sub == owner_sub,
            File.folder_id.in_(folder_ids),
            File.deleted_at.is_(None),
            File.status == "ready",
        )

    def subtree_counts(self, owner_sub: str, folder_id: uuid.UUID) -> tuple[int, int]:
        """(live folders below this one, live files anywhere in the subtree)."""
        ids = self.db.scalars(self._subtree(owner_sub, folder_id)).all()
        count = select(func.count()).select_from(File).where(*self._live_files_in(owner_sub, ids))
        return max(len(ids) - 1, 0), self.db.scalar(count) or 0

    def soft_delete_subtree(self, owner_sub: str, folder_id: uuid.UUID) -> tuple[int, int]:
        """BR-7: the folder, every live folder below it and every live file in
        them, in one transaction under one shared timestamp -- so Phase 3 can
        restore exactly this batch. Rows already trashed keep their own stamp."""
        now = datetime.now(UTC)
        ids = self.db.scalars(self._subtree(owner_sub, folder_id)).all()
        trashed = self.db.execute(
            update(File).where(*self._live_files_in(owner_sub, ids)).values(deleted_at=now)
        )
        self.db.execute(update(Folder).where(Folder.id.in_(ids)).values(deleted_at=now))
        self.db.commit()
        return max(len(ids) - 1, 0), trashed.rowcount


def folder_repository(db: Db) -> FolderRepository:
    return FolderRepository(db)


FolderRepo = Annotated[FolderRepository, Depends(folder_repository)]
