# Files Phase 2 (Organise and Find) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A user can put their files in nested folders, rename / describe / tag / move them, and find them again by search, tag filter and sort, all from the SPA.

**Architecture:** No schema change: `folders`, `files.folder_id`, `files.description` and `files.tags` already exist (migration `0001`). A new `FolderRepository` owns folder SQL (recursive CTEs for cycles and subtrees); `FileRepository.list()` grows filters and a new `update()`. Name uniqueness is decided by the existing partial unique indexes, never by check-then-insert: both repositories turn `IntegrityError` into one shared `NameTaken` exception and the routes map it to `409`. The SPA keeps one `/files` route; `?folder`, `?q` and `?tag` live in the URL, and one native `<dialog>` edits files and folders.

**Tech Stack:** FastAPI (sync handlers), SQLAlchemy 2.0, PostgreSQL 16, pytest; Vue 3 `<script setup lang="ts">`, Pinia, vue-router, vitest + @vue/test-utils (jsdom).

**Spec:** `docs/superpowers/specs/2026-09-26-files-phase-2-design.md` (source of truth). Background: `docs/files-feature.md` §5, §6 (BR-2..BR-7, BR-13), §12 Phase 2 Gherkin. Read `docs/testing.md` before adding any test.

## Global Constraints

- **No new dependency** (backend or frontend) and **no Alembic migration**. The schema already covers Phase 2.
- The backend virtualenv is `backend/.venv`. Always call tools as `.venv/bin/python -m <tool>` from `backend/`.
- ruff, line length **100**, rules `["E", "F", "I", "UP", "B"]`. B008 flags `Form(...)` in a default, so multipart fields use `Annotated[..., Form()] = None`. (`Query(...)` defaults are exempt and already used.)
- Backend tests live in `backend/tests/{unit,integration,regression}/`. **The directory decides the pytest marker.** No test carries a decorator. A test in `tests/unit/` never touches a service.
- Integration tests never request the `repo` or `store` fixtures (they fail on sight there).
- Response shapes are pydantic models declared next to their route.
- Every route handler is a plain `def`. Every route takes `Claims`. Every query filters on `owner_sub = claims["sub"]`. A missing, foreign or trashed id is a `404` (never `403`).
- In `PATCH` bodies, an omitted field is unchanged and an explicit `null` for `folder_id` / `parent_id` means root (`model_fields_set` tells them apart).
- No MinIO call from any `PATCH` or folder route.
- Each mutation writes one `audit_log` row per action: `folder_create`, `folder_rename`, `folder_move`, `folder_delete`, `rename`, `move`, `retag` (retag also covers description). UUIDs in `detail` are stored as strings (`detail` is JSONB).
- Frontend: `<script setup lang="ts">`, imports via `@/` (same-directory `./client` imports inside `src/api/` stay as they are). `tsconfig.app.json` has `erasableSyntaxOnly`, so no TypeScript parameter properties; `tests/**/*.ts` is type-checked by `npm run build`.
- The fake repositories in `backend/tests/conftest.py` must behave like the real SQL for every case the integration suite pins (the Phase 1 fake-drift lesson): same ordering, same case-insensitive name clash, same search semantics.
- The API contract is pinned by `backend/tests/regression/openapi.snapshot.json`. Every task that changes a route regenerates it with `make snapshot` so the regression suite stays green.
- Git: work on a dedicated branch off `origin/main`. **Commit steps run only if the user has asked for commits in this session** (CLAUDE.md: never commit as a side effect). Commit messages end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Review Focus

- **A stale `?folder=` id** (the folder was deleted in another tab) must give `404` from `GET /api/files?folder_id=…`, not an empty 200 that hides the problem. Pinned in Task 3 (`test_listing_an_unknown_or_foreign_folder_is_a_404`).
- **A blank search** (`?q=%20%20`, a cleared search box) must be the plain folder listing, not a `422` and not "search everything". Pinned in Task 3 (`test_a_blank_search_is_the_plain_folder_listing`).
- **A case-only rename** (`notes.txt` → `Notes.txt`, `work` → `Work`) must succeed: the unique index must not clash a row with itself, and the fakes must exclude self. Pinned in Task 1, 2 and 4.
- **An empty or no-op `PATCH`** (`{}`, or a folder "moved" to its current parent) must be `200` with no audit row, not a `409` or a spurious `folder_move`. Pinned in Task 2 and 4.
- **`%` and `_` typed into search** must match literally. Pinned against real SQL in Task 3 (`test_search_treats_like_wildcards_literally`).

## File Structure

**Created**

| File | Responsibility |
|---|---|
| `backend/app/repositories/folders.py` | Owner-scoped folder SQL: list/get/create/update, cycle CTE, subtree counts, subtree soft-delete; `FolderRepo` dependency |
| `backend/app/api/routes/folders.py` | `/api/folders` routes and their pydantic models |
| `backend/tests/unit/test_folders.py` | Folder route logic against the fake |
| `backend/tests/integration/test_folder_repository.py` | Folder SQL against real PostgreSQL |
| `backend/tests/integration/test_phase2_acceptance.py` | The five Phase 2 Gherkin scenarios |
| `frontend/src/api/folders.ts` | `Folder` type and the four folder calls |
| `frontend/src/components/EditDialog.vue` | Native `<dialog>` for file edit, folder edit, folder create |
| `frontend/tests/unit/api-files-folders.test.ts` | Request shapes of `api/files.ts` + `api/folders.ts` |
| `frontend/tests/unit/EditDialog.test.ts` | Dialog behaviour |

**Modified**

| File | Change |
|---|---|
| `backend/app/repositories/files.py` | `NameTaken`, `Sort`/`Order`, filtered `list()`, `update()` |
| `backend/app/api/routes/files.py` | `FileInfo` fields, `_live_folder`, `_json_id`, list filters, upload `folder_id`, `PATCH` |
| `backend/app/api/router.py` | Include the folders router |
| `backend/.coveragerc.ci` | Omit `app/repositories/folders.py` (SQL-only, like `files.py`) |
| `backend/tests/conftest.py` | `FakeFolderRepository`; fake `list()` filters; fake `update()`; `repo` fixture overrides both repos |
| `backend/tests/unit/test_files.py` | Listing filters, `PATCH`, upload into a folder |
| `backend/tests/integration/test_repository.py` | Listing filters, `update()` |
| `backend/tests/regression/test_r1_cross_owner_isolation.py` | Every new route vs a foreign id |
| `backend/tests/regression/openapi.snapshot.json` | Regenerated by `make snapshot` |
| `frontend/src/api/client.ts` | `ApiError` (status + parsed body + server detail); optional `Content-Type` |
| `frontend/src/api/files.ts` | `HomeFile` fields, `listFiles(params)`, `uploadFile(file, folderId)`, `updateFile` |
| `frontend/src/stores/files.ts` | `folders`, `pathOf`, `hasMore`, `loadMore`, `removeFolder`, params-aware `load` |
| `frontend/src/views/FilesView.vue` | Breadcrumb, search, tag chips, sort headers, folders, dialog, load more |
| `frontend/tests/unit/client.test.ts`, `stores-files.test.ts`, `FilesView.test.ts`, `frontend/tests/regression/pr12-download-blob-url.test.ts` | Updated for the new shapes |
| `docs/testing.md`, `CLAUDE.md` | Coverage omission, folders route, test counts |

---

### Task 1: The folder repository

**Files:**
- Modify: `backend/app/repositories/files.py:1-20` (imports, `NameTaken`)
- Create: `backend/app/repositories/folders.py`
- Modify: `backend/.coveragerc.ci`
- Test: `backend/tests/integration/test_folder_repository.py`

**Interfaces:**
- Consumes: `app.models.files.Folder`, `File`; `app.core.db.Db`; `FileRepository.reserve/finalize/soft_delete` (tests only).
- Produces:
  - `app.repositories.files.NameTaken(Exception)` — raised when a unique sibling-name index rejects a write.
  - `FolderRepository(db)` with `list(owner_sub) -> Sequence[Folder]` (ordered `lower(name), id`), `get(owner_sub, folder_id) -> Folder | None` (live only), `create(owner_sub, *, name, parent_id) -> Folder`, `update(folder, **changes) -> Folder`, `is_cycle(owner_sub, folder_id, new_parent_id) -> bool`, `subtree_counts(owner_sub, folder_id) -> tuple[int, int]` (live descendant folders excluding itself, live ready files in the subtree), `soft_delete_subtree(owner_sub, folder_id) -> tuple[int, int]` (same counts, of what it trashed).
  - `folder_repository(db) -> FolderRepository`, `FolderRepo = Annotated[FolderRepository, Depends(folder_repository)]`.

- [ ] **Step 1: Write the failing integration tests**

Create `backend/tests/integration/test_folder_repository.py`:

```python
"""Integration — the folder repository's SQL, against a real PostgreSQL."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.repositories.files import FileRepository, NameTaken
from app.repositories.folders import FolderRepository

OLD = datetime(2020, 1, 1, tzinfo=UTC)


@pytest.fixture
def folders(db):
    return FolderRepository(db)


@pytest.fixture
def files(db):
    return FileRepository(db)


def ready_file(files, name="a.txt", folder_id=None, owner="user-1"):
    row = files.reserve(
        owner,
        name=name,
        folder_id=folder_id,
        size_bytes=1,
        content_type="text/plain",
        quota_bytes=10**9,
    )
    return files.finalize(row, s3_version_id="v", actor_sub=owner)


def test_list_returns_the_owners_live_folders_by_name(folders):
    folders.create("user-1", name="b", parent_id=None)
    folders.create("user-1", name="A", parent_id=None)
    folders.create("user-2", name="theirs", parent_id=None)

    assert [f.name for f in folders.list("user-1")] == ["A", "b"]


def test_a_root_sibling_clash_is_refused_by_the_index_whatever_the_case(folders):
    folders.create("user-1", name="work", parent_id=None)

    with pytest.raises(NameTaken):
        folders.create("user-1", name="WORK", parent_id=None)

    # The session survived the rollback and is usable again.
    folders.create("user-1", name="home", parent_id=None)
    assert [f.name for f in folders.list("user-1")] == ["home", "work"]


def test_the_same_name_is_fine_under_another_parent_or_owner(folders):
    parent = folders.create("user-1", name="A", parent_id=None)
    folders.create("user-1", name="work", parent_id=None)

    folders.create("user-1", name="work", parent_id=parent.id)
    folders.create("user-2", name="work", parent_id=None)


def test_a_trashed_folder_frees_its_name(folders):
    first = folders.create("user-1", name="work", parent_id=None)
    folders.soft_delete_subtree("user-1", first.id)

    second = folders.create("user-1", name="work", parent_id=None)

    assert folders.get("user-1", second.id) is not None
    assert folders.get("user-1", first.id) is None


def test_rename_into_a_clash_raises_and_keeps_the_old_name(folders):
    folders.create("user-1", name="a", parent_id=None)
    b = folders.create("user-1", name="b", parent_id=None)

    with pytest.raises(NameTaken):
        folders.update(b, name="A")

    assert folders.get("user-1", b.id).name == "b"


def test_a_case_only_rename_is_not_a_clash_with_itself(folders):
    row = folders.create("user-1", name="work", parent_id=None)

    folders.update(row, name="Work")

    assert folders.get("user-1", row.id).name == "Work"


def test_is_cycle_sees_the_folder_itself_and_every_descendant(folders):
    a = folders.create("user-1", name="A", parent_id=None)
    b = folders.create("user-1", name="B", parent_id=a.id)
    c = folders.create("user-1", name="C", parent_id=b.id)
    other = folders.create("user-1", name="Other", parent_id=None)

    assert folders.is_cycle("user-1", a.id, a.id)
    assert folders.is_cycle("user-1", a.id, c.id)
    assert not folders.is_cycle("user-1", c.id, a.id)
    assert not folders.is_cycle("user-1", b.id, other.id)


def test_subtree_counts_only_live_descendants(folders, files):
    a = folders.create("user-1", name="A", parent_id=None)
    b = folders.create("user-1", name="B", parent_id=a.id)
    ready_file(files, "one.txt", a.id)
    ready_file(files, "two.txt", b.id)
    files.soft_delete(ready_file(files, "gone.txt", b.id))

    assert folders.subtree_counts("user-1", a.id) == (1, 2)
    assert folders.subtree_counts("user-1", b.id) == (0, 1)


def test_soft_delete_subtree_shares_one_stamp_and_spares_pre_trashed_rows(folders, files, db):
    a = folders.create("user-1", name="A", parent_id=None)
    b = folders.create("user-1", name="B", parent_id=a.id)
    keep = folders.create("user-1", name="Keep", parent_id=None)
    ready_file(files, "one.txt", a.id)
    ready_file(files, "two.txt", b.id)
    old = ready_file(files, "old.txt", b.id)
    db.execute(text("UPDATE files SET deleted_at = :at WHERE id = :id"), {"at": OLD, "id": old.id})
    db.commit()

    assert folders.soft_delete_subtree("user-1", a.id) == (1, 2)

    stamps = db.execute(
        text(
            "SELECT deleted_at FROM folders WHERE id IN (:a, :b) "
            "UNION ALL SELECT deleted_at FROM files WHERE name IN ('one.txt', 'two.txt')"
        ),
        {"a": a.id, "b": b.id},
    ).scalars()
    assert len(set(stamps)) == 1
    old_stamp = db.execute(text("SELECT deleted_at FROM files WHERE id = :id"), {"id": old.id})
    assert old_stamp.scalar_one() == OLD
    assert [f.name for f in folders.list("user-1")] == ["Keep"]
    assert folders.get("user-1", keep.id) is not None


def test_folder_queries_never_cross_owners(folders, files):
    a = folders.create("user-1", name="A", parent_id=None)
    ready_file(files, "one.txt", a.id)

    assert folders.get("user-2", a.id) is None
    assert folders.list("user-2") == []
    assert folders.subtree_counts("user-2", a.id) == (0, 0)
    assert folders.soft_delete_subtree("user-2", a.id) == (0, 0)
    assert folders.get("user-1", a.id) is not None


def test_get_of_an_unknown_id_is_none(folders):
    assert folders.get("user-1", uuid.uuid4()) is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `make services-test-up && cd backend && .venv/bin/python -m pytest tests/integration/test_folder_repository.py -v`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'app.repositories.folders'` (and `ImportError` for `NameTaken`).

- [ ] **Step 3: Add `NameTaken` to the file repository**

In `backend/app/repositories/files.py`, after `QuotaExceeded`, add:

```python
class NameTaken(Exception):
    """A live sibling already has this name. Raised from the unique index
    (`uq_folders_sibling_name`, `uq_files_folder_name`), never from a SELECT
    first: a check-then-insert would let two racing requests both pass."""
```

- [ ] **Step 4: Write the folder repository**

Create `backend/app/repositories/folders.py`:

```python
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
```

- [ ] **Step 5: Keep the unit-job coverage gate honest**

`app/repositories/folders.py` is SQL only, exactly like `app/repositories/files.py`: the service-less `backend-unit` job cannot reach it. Replace `backend/.coveragerc.ci` with:

```ini
# Coverage config for the `backend-unit` CI job ONLY, which runs
# `-m "unit or regression"` with no services. The modules below are
# exercised only against a real PostgreSQL, so that run cannot reach them --
# the fake repositories substitute for them rather than exercising them. They
# are gated instead by the backend-integration job:
# tests/integration/test_repository.py, test_folder_repository.py and
# test_backfill.py.
#
# `make coverage-backend` does NOT use this file: it appends all three suites
# and holds every module to COVERAGE_MIN.
[run]
omit =
    app/repositories/files.py
    app/repositories/folders.py
    app/scripts/backfill.py
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest -m integration tests/integration/test_folder_repository.py -v`
Expected: 11 passed.

Then: `cd backend && .venv/bin/python -m pytest -m integration && .venv/bin/python -m ruff format --check . && .venv/bin/python -m ruff check .`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add backend/app/repositories/files.py backend/app/repositories/folders.py \
        backend/.coveragerc.ci backend/tests/integration/test_folder_repository.py
git commit -m "feat(files): folder repository with cycle and subtree CTEs"
```

---

### Task 2: The folder routes

**Files:**
- Modify: `backend/app/api/routes/files.py:1-20,79-92` (imports; `_live_folder`, `_json_id` next to `_validated_name`)
- Create: `backend/app/api/routes/folders.py`
- Modify: `backend/app/api/router.py`
- Modify: `backend/tests/conftest.py:13-17,137-232` (`FakeFolderRepository`, `repo` fixture)
- Test: `backend/tests/unit/test_folders.py`
- Regenerate: `backend/tests/regression/openapi.snapshot.json`

**Interfaces:**
- Consumes: everything Task 1 produces; `FileRepo.audit(actor_sub, action, target_type, target_id, detail)` (existing — folder routes write their audit rows through it, so there is one audit writer).
- Produces:
  - `app.api.routes.files._live_folder(folders, owner_sub, folder_id) -> Folder` (raises `404 "No such folder"`), `_json_id(value: uuid.UUID | None) -> str | None`.
  - `GET/POST /api/folders`, `PATCH/DELETE /api/folders/{folder_id}` (see spec API table). `FolderInfo {id, name, parent_id}`, `FolderNotEmpty {detail, folders, files}`.
  - Tests: `FakeFolderRepository` reachable as `repo.folders`; the `repo` fixture overrides both `file_repository` and `folder_repository`.

- [ ] **Step 1: Write the failing unit tests**

Create `backend/tests/unit/test_folders.py`:

```python
"""Unit — the folder routes, against FakeFolderRepository (`repo.folders`)."""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def create(name="Invoices", parent_id=None, sub="user-1"):
    return client.post(
        "/api/folders", headers=auth(sub), json={"name": name, "parent_id": parent_id}
    )


def patch(folder_id, body, sub="user-1"):
    return client.patch(f"/api/folders/{folder_id}", headers=auth(sub), json=body)


def listed(sub="user-1"):
    return client.get("/api/folders", headers=auth(sub)).json()


def seed_file(repo, folder_id, name="a.txt", sub="user-1"):
    row = repo.reserve(
        sub,
        name=name,
        folder_id=uuid.UUID(folder_id),
        size_bytes=1,
        content_type="text/plain",
        quota_bytes=10**9,
    )
    return repo.finalize(row, s3_version_id="v1", actor_sub=sub)


def test_create_returns_201_with_the_folder_and_audits_it(repo):
    response = create()

    assert response.status_code == 201
    body = response.json()
    assert (body["name"], body["parent_id"]) == ("Invoices", None)
    actor, action, target_type, target_id, detail = repo.audits[-1]
    assert (actor, action, target_type) == ("user-1", "folder_create", "folder")
    assert str(target_id) == body["id"]
    assert detail == {"name": "Invoices", "parent_id": None}


def test_create_trims_and_validates_the_name(repo):
    assert create("  Bills  ").json()["name"] == "Bills"
    assert create("   ").status_code == 422
    assert create("..").status_code == 422


def test_a_sibling_clash_is_a_409_whatever_the_case(repo):
    create("work")

    response = create("WORK")

    assert response.status_code == 409
    assert response.json()["detail"] == "A folder named WORK already exists here"


def test_the_same_name_is_fine_under_another_parent(repo):
    parent = create("A").json()["id"]
    create("work")

    assert create("work", parent_id=parent).status_code == 201


def test_create_under_an_unknown_parent_is_a_404(repo):
    assert create(parent_id=str(uuid.uuid4())).status_code == 404


def test_list_returns_only_the_callers_live_folders(repo):
    mine = create("Mine").json()
    create("Theirs", sub="user-2")

    assert listed() == [mine]


def test_rename_is_audited_with_before_and_after(repo):
    folder_id = create("Invoices").json()["id"]

    response = patch(folder_id, {"name": "Bills"})

    assert response.status_code == 200
    assert response.json()["name"] == "Bills"
    assert repo.audits[-1][1:] == (
        "folder_rename",
        "folder",
        uuid.UUID(folder_id),
        {"before": "Invoices", "after": "Bills"},
    )


def test_rename_into_a_sibling_name_is_a_409(repo):
    create("A")
    folder_id = create("B").json()["id"]

    assert patch(folder_id, {"name": "a"}).status_code == 409
    assert sorted(f["name"] for f in listed()) == ["A", "B"]


def test_a_case_only_rename_is_not_a_clash_with_itself(repo):
    folder_id = create("work").json()["id"]

    response = patch(folder_id, {"name": "Work"})

    assert response.status_code == 200
    assert response.json()["name"] == "Work"


def test_omitted_parent_is_unchanged_and_explicit_null_is_the_root(repo):
    parent = create("A").json()["id"]
    folder_id = create("B", parent_id=parent).json()["id"]

    assert patch(folder_id, {"name": "C"}).json()["parent_id"] == parent

    moved = patch(folder_id, {"parent_id": None})

    assert moved.json()["parent_id"] is None
    assert repo.audits[-1][1] == "folder_move"
    assert repo.audits[-1][4] == {"before": parent, "after": None}


def test_an_empty_or_no_op_patch_is_200_and_audits_nothing(repo):
    parent = create("A").json()["id"]
    folder_id = create("B", parent_id=parent).json()["id"]
    audits = len(repo.audits)

    assert patch(folder_id, {}).status_code == 200
    assert patch(folder_id, {"parent_id": parent, "name": "B"}).status_code == 200
    assert len(repo.audits) == audits


def test_a_folder_cannot_move_into_itself_or_below_itself(repo):
    a = create("A").json()["id"]
    b = create("B", parent_id=a).json()["id"]

    assert patch(a, {"parent_id": b}).status_code == 409
    assert patch(a, {"parent_id": a}).status_code == 409
    assert next(f for f in listed() if f["id"] == a)["parent_id"] is None


def test_move_under_an_unknown_parent_is_a_404(repo):
    folder_id = create().json()["id"]

    assert patch(folder_id, {"parent_id": str(uuid.uuid4())}).status_code == 404


def test_delete_an_empty_folder(repo):
    folder_id = create("A").json()["id"]

    assert client.delete(f"/api/folders/{folder_id}", headers=auth()).status_code == 204
    assert listed() == []
    assert repo.audits[-1][1] == "folder_delete"
    assert repo.audits[-1][4] == {"name": "A", "folders": 0, "files": 0}


def test_a_non_empty_folder_needs_recursive(repo):
    a = create("A").json()["id"]
    b = create("B", parent_id=a).json()["id"]
    seed_file(repo, b)

    response = client.delete(f"/api/folders/{a}", headers=auth())

    assert response.status_code == 409
    assert response.json() == {
        "detail": "A is not empty (1 folders, 1 files)",
        "folders": 1,
        "files": 1,
    }
    assert len(listed()) == 2


def test_recursive_delete_trashes_the_whole_subtree(repo):
    a = create("A").json()["id"]
    b = create("B", parent_id=a).json()["id"]
    inside = seed_file(repo, b)

    response = client.delete(f"/api/folders/{a}?recursive=true", headers=auth())

    assert response.status_code == 204
    assert listed() == []
    assert inside.deleted_at is not None
    assert repo.audits[-1][4] == {"name": "A", "folders": 1, "files": 1}


def test_unknown_or_foreign_folders_are_404(repo):
    folder_id = create().json()["id"]
    unknown = uuid.uuid4()

    assert patch(folder_id, {"name": "x"}, sub="user-2").status_code == 404
    assert patch(unknown, {"name": "x"}).status_code == 404
    assert client.delete(f"/api/folders/{folder_id}", headers=auth("user-2")).status_code == 404
    assert client.delete(f"/api/folders/{unknown}", headers=auth()).status_code == 404


def test_the_folder_routes_need_a_token(repo):
    assert client.get("/api/folders").status_code == 401
    assert client.post("/api/folders", json={"name": "A"}).status_code == 401
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_folders.py -v`
Expected: FAIL — every route returns `404 Not Found` (the router does not exist yet).

- [ ] **Step 3: Add the fake folder repository and wire the `repo` fixture**

In `backend/tests/conftest.py`, change the imports:

```python
from app.api.routes import files
from app.core import auth
from app.main import app
from app.models.files import File, Folder
from app.repositories.files import NameTaken, QuotaExceeded, file_repository
from app.repositories.folders import folder_repository
```

In `FakeFileRepository.__init__`, add as the last line:

```python
        self.folders = FakeFolderRepository(self)
```

After `FakeFileRepository` (before the `repo` fixture), add:

```python
class FakeFolderRepository:
    """The slice of FolderRepository the routes use, over a list. Mirrors the
    real SQL for every case tests/integration/test_folder_repository.py pins:
    case-insensitive sibling names excluding self, live-only subtrees, one
    shared deleted_at per recursive delete."""

    def __init__(self, files: FakeFileRepository):
        self.files = files
        self.rows: list[Folder] = []

    def _live(self, owner_sub):
        return [r for r in self.rows if r.owner_sub == owner_sub and r.deleted_at is None]

    def _check_free(self, owner_sub, name, parent_id, itself=None):
        for r in self._live(owner_sub):
            if r is not itself and r.parent_id == parent_id and r.name.lower() == name.lower():
                raise NameTaken

    def list(self, owner_sub):
        return sorted(self._live(owner_sub), key=lambda r: (r.name.lower(), r.id))

    def get(self, owner_sub, folder_id):
        return next((r for r in self._live(owner_sub) if r.id == folder_id), None)

    def create(self, owner_sub, *, name, parent_id):
        self._check_free(owner_sub, name, parent_id)
        row = Folder(id=uuid.uuid4(), owner_sub=owner_sub, name=name, parent_id=parent_id)
        self.rows.append(row)
        return row

    def update(self, folder, **changes):
        name = changes.get("name", folder.name)
        parent_id = changes.get("parent_id", folder.parent_id)
        self._check_free(folder.owner_sub, name, parent_id, itself=folder)
        for field, value in changes.items():
            setattr(folder, field, value)
        return folder

    def is_cycle(self, owner_sub, folder_id, new_parent_id):
        current = self.get(owner_sub, new_parent_id)
        while current is not None:
            if current.id == folder_id:
                return True
            current = self.get(owner_sub, current.parent_id)
        return False

    def _subtree(self, owner_sub, folder_id):
        if self.get(owner_sub, folder_id) is None:
            return []
        ids = [folder_id]
        for parent in ids:
            ids += [r.id for r in self._live(owner_sub) if r.parent_id == parent]
        return ids

    def _files_in(self, owner_sub, ids):
        return [f for f in self.files._live(owner_sub) if f.folder_id in ids]

    def subtree_counts(self, owner_sub, folder_id):
        ids = self._subtree(owner_sub, folder_id)
        return max(len(ids) - 1, 0), len(self._files_in(owner_sub, ids))

    def soft_delete_subtree(self, owner_sub, folder_id):
        now = datetime.now(UTC)
        ids = self._subtree(owner_sub, folder_id)
        trashed = self._files_in(owner_sub, ids)
        for row in [*trashed, *(r for r in self.rows if r.id in ids)]:
            row.deleted_at = now
        return max(len(ids) - 1, 0), len(trashed)
```

Replace the `repo` fixture with:

```python
@pytest.fixture
def repo():
    """Swap both repositories for the in-memory fakes; the folder fake is
    `repo.folders`. Explicit, never autouse: the integration suite must keep
    talking to the real database."""
    fake = FakeFileRepository()
    app.dependency_overrides[file_repository] = lambda: fake
    app.dependency_overrides[folder_repository] = lambda: fake.folders
    yield fake
    app.dependency_overrides.pop(file_repository, None)
    app.dependency_overrides.pop(folder_repository, None)
```

- [ ] **Step 4: Add the shared helpers to the files routes**

In `backend/app/api/routes/files.py`, extend the imports:

```python
from app.core.auth import Claims
from app.core.config import get_settings
from app.models.files import File, Folder
from app.repositories.files import FileRepo, QuotaExceeded
from app.repositories.folders import FolderRepository
```

Directly after `_validated_name` add:

```python
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
```

- [ ] **Step 5: Write the folder routes**

Create `backend/app/api/routes/folders.py`:

```python
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
```

- [ ] **Step 6: Mount the router**

Replace `backend/app/api/router.py` with:

```python
from fastapi import APIRouter

from app.api.routes import files, folders, health, me

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
# Protected routes take the `Claims` dependency (app.core.auth); health stays public.
api_router.include_router(me.router)
api_router.include_router(files.router)
api_router.include_router(folders.router)
```

- [ ] **Step 7: Run the unit tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_folders.py -v`
Expected: 18 passed.

- [ ] **Step 8: Regenerate the OpenAPI snapshot and run everything service-less**

Run: `make snapshot && git diff --stat backend/tests/regression/openapi.snapshot.json`
Expected: the diff adds `/api/folders` and `/api/folders/{folder_id}` and the `FolderInfo`, `FolderCreate`, `FolderPatch`, `FolderNotEmpty` schemas, and nothing else.

Run: `cd backend && .venv/bin/python -m pytest -m unit && .venv/bin/python -m pytest -m regression && .venv/bin/python -m ruff format --check . && .venv/bin/python -m ruff check .`
Expected: all green.

- [ ] **Step 9: Commit**

```bash
git add backend/app/api/routes/files.py backend/app/api/routes/folders.py \
        backend/app/api/router.py backend/tests/conftest.py backend/tests/unit/test_folders.py \
        backend/tests/regression/openapi.snapshot.json
git commit -m "feat(files): folder CRUD, move and recursive delete routes"
```

---

### Task 3: File listing — folder scope, search, tag filter, sort

**Files:**
- Modify: `backend/app/repositories/files.py:1-13,40-52` (`Sort`, `Order`, `_contains`, `list()`)
- Modify: `backend/app/api/routes/files.py:24-39,65-76` (`FileInfo`, `list_files`)
- Modify: `backend/tests/conftest.py` (`FakeFileRepository.list`)
- Test: `backend/tests/integration/test_repository.py`, `backend/tests/unit/test_files.py`
- Regenerate: `backend/tests/regression/openapi.snapshot.json`

**Interfaces:**
- Consumes: `FolderRepository.create` (tests), `_live_folder`, `FolderRepo` (Task 1–2).
- Produces:
  - `app.repositories.files.Sort = Literal["name", "size", "updated_at"]`, `Order = Literal["asc", "desc"]`.
  - `FileRepository.list(owner_sub, *, folder_id=None, q=None, tag=None, sort="updated_at", order="desc", limit=100, offset=0) -> Sequence[File]`. `q`/`tag` already trimmed (tag lowercased) by the route; either one being set drops the folder scope.
  - `FileInfo` gains `folder_id: uuid.UUID | None`, `description: str | None`, `tags: list[str]`.
  - `GET /api/files?folder_id&q&tag&sort&order&limit&offset`.

- [ ] **Step 1: Write the failing integration tests**

Append to `backend/tests/integration/test_repository.py` (add `from app.repositories.folders import FolderRepository` to the imports):

```python
def ready(repository, db, name, owner="user-1", folder_id=None, description=None, tags=()):
    row = repository.reserve(
        owner,
        name=name,
        folder_id=folder_id,
        size_bytes=len(name),
        content_type="text/plain",
        quota_bytes=10**9,
    )
    repository.finalize(row, s3_version_id="v", actor_sub=owner)
    row.description = description
    row.tags = list(tags)
    db.commit()
    return row


def names(rows):
    return [row.name for row in rows]


def test_list_is_scoped_to_one_folder(repository, db):
    folder = FolderRepository(db).create("user-1", name="A", parent_id=None)
    ready(repository, db, "root.txt")
    ready(repository, db, "inside.txt", folder_id=folder.id)

    assert names(repository.list("user-1")) == ["root.txt"]
    assert names(repository.list("user-1", folder_id=folder.id)) == ["inside.txt"]


def test_search_matches_name_description_and_tags_across_folders(repository, db):
    folder = FolderRepository(db).create("user-1", name="A", parent_id=None)
    ready(repository, db, "Tax 2026.pdf")
    ready(repository, db, "scan.pdf", folder_id=folder.id, description="Tax return")
    ready(repository, db, "r.pdf", tags=["tax"])
    ready(repository, db, "other.txt")
    ready(repository, db, "tax.txt", owner="user-2")

    found = repository.list("user-1", q="TAX")

    assert sorted(names(found)) == ["Tax 2026.pdf", "r.pdf", "scan.pdf"]


def test_search_treats_like_wildcards_literally(repository, db):
    for name in ("100%.txt", "1000.txt", "a_b.txt", "axb.txt"):
        ready(repository, db, name)

    assert names(repository.list("user-1", q="100%")) == ["100%.txt"]
    assert names(repository.list("user-1", q="a_b")) == ["a_b.txt"]


def test_tag_filter_is_containment_and_combines_with_q(repository, db):
    ready(repository, db, "a.txt", tags=["tax", "2026"])
    ready(repository, db, "b.txt", tags=["taxes"])
    ready(repository, db, "c.txt", tags=["tax"], description="receipt")

    assert sorted(names(repository.list("user-1", tag="tax"))) == ["a.txt", "c.txt"]
    assert names(repository.list("user-1", tag="tax", q="receipt")) == ["c.txt"]


def test_sort_is_case_insensitive_and_paging_is_stable_on_ties(repository, db):
    for name in ("b.txt", "A.txt", "c.txt", "d.txt"):
        ready(repository, db, name)
    db.execute(text("UPDATE files SET updated_at = '2026-01-01T00:00:00+00:00'"))
    db.commit()

    by_name = repository.list("user-1", sort="name", order="asc")
    first = repository.list("user-1", limit=2, offset=0)
    second = repository.list("user-1", limit=2, offset=2)

    assert names(by_name) == ["A.txt", "b.txt", "c.txt", "d.txt"]
    assert len({row.id for row in [*first, *second]}) == 4
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest -m integration tests/integration/test_repository.py -v`
Expected: the new tests FAIL with `TypeError: FileRepository.list() got an unexpected keyword argument 'folder_id'` (and `'q'`, `'tag'`, `'sort'`); the old ones pass.

- [ ] **Step 3: Implement the filtered listing**

In `backend/app/repositories/files.py`, change the imports to:

```python
from typing import Annotated, Any, Literal

from fastapi import Depends
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
```

Below the imports add:

```python
Sort = Literal["name", "size", "updated_at"]
Order = Literal["asc", "desc"]

SORT_KEYS = {"name": func.lower(File.name), "size": File.size_bytes, "updated_at": File.updated_at}


def _contains(q: str) -> str:
    """An ILIKE pattern matching `q` literally: its own % and _ are not wildcards."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
```

Replace `list()` with:

```python
    def list(
        self,
        owner_sub: str,
        *,
        folder_id: uuid.UUID | None = None,
        q: str | None = None,
        tag: str | None = None,
        sort: Sort = "updated_at",
        order: Order = "desc",
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[File]:
        statement = select(File).where(
            File.owner_sub == owner_sub,
            File.deleted_at.is_(None),
            File.status == "ready",
        )
        if q is None and tag is None:
            in_folder = File.folder_id.is_(None) if folder_id is None else File.folder_id == folder_id
            statement = statement.where(in_folder)
        if q is not None:
            # ponytail: ILIKE scans the owner's rows; add pg_trgm GIN indexes on
            # name/description if per-user file counts reach the tens of thousands.
            pattern = _contains(q)
            tag_value = func.unnest(File.tags).column_valued("t")
            statement = statement.where(
                or_(
                    File.name.ilike(pattern, escape="\\"),
                    File.description.ilike(pattern, escape="\\"),
                    select(tag_value).where(tag_value.ilike(pattern, escape="\\")).exists(),
                )
            )
        if tag is not None:
            statement = statement.where(File.tags.contains([tag]))  # @>, served by ix_files_tags
        key = SORT_KEYS[sort]
        # id breaks ties, so a page boundary never splits or repeats equal keys.
        ordering = (key.asc(), File.id.asc()) if order == "asc" else (key.desc(), File.id.desc())
        statement = statement.order_by(*ordering).limit(limit).offset(offset)
        return self.db.scalars(statement).all()
```

- [ ] **Step 4: Run the integration tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest -m integration tests/integration/test_repository.py -v`
Expected: all pass (14 old + 5 new).

- [ ] **Step 5: Write the failing unit tests**

In `backend/tests/unit/test_files.py`, replace `seed` with:

```python
def seed(
    repo,
    name="a.txt",
    sub="user-1",
    size=5,
    content_type="text/plain",
    folder_id=None,
    description=None,
    tags=(),
):
    """A ready file straight in the fake, so read tests do not depend on upload."""
    row = repo.reserve(
        sub,
        name=name,
        folder_id=folder_id,
        size_bytes=size,
        content_type=content_type,
        quota_bytes=10**9,
    )
    repo.finalize(row, s3_version_id="v1", actor_sub=sub)
    row.description = description
    row.tags = list(tags)
    return row
```

In `test_list_returns_the_owners_ready_files`, the expected dict becomes:

```python
    assert listed == [
        {
            "id": str(row.id),
            "name": "bail été.txt",
            "size": 5,
            "content_type": "text/plain",
            "folder_id": None,
            "description": None,
            "tags": [],
        }
    ]
```

Append:

```python
def names(response):
    return [f["name"] for f in response.json()]


def test_list_shows_the_root_by_default(repo):
    folder = repo.folders.create("user-1", name="A", parent_id=None)
    seed(repo, name="root.txt")
    seed(repo, name="inside.txt", folder_id=folder.id)

    assert names(client.get("/api/files", headers=auth())) == ["root.txt"]


def test_list_shows_one_folder_when_asked(repo):
    folder = repo.folders.create("user-1", name="A", parent_id=None)
    seed(repo, name="root.txt")
    inside = seed(repo, name="inside.txt", folder_id=folder.id)

    response = client.get("/api/files", headers=auth(), params={"folder_id": str(folder.id)})

    assert names(response) == ["inside.txt"]
    assert response.json()[0]["folder_id"] == str(inside.folder_id)


def test_listing_an_unknown_or_foreign_folder_is_a_404(repo):
    theirs = repo.folders.create("user-2", name="A", parent_id=None)

    for folder_id in (theirs.id, uuid.uuid4()):
        response = client.get("/api/files", headers=auth(), params={"folder_id": str(folder_id)})
        assert response.status_code == 404


def test_search_ignores_the_folder_and_matches_name_description_and_tags(repo):
    folder = repo.folders.create("user-1", name="A", parent_id=None)
    seed(repo, name="Tax 2026.pdf")
    seed(repo, name="scan.pdf", folder_id=folder.id, description="tax return")
    seed(repo, name="r.pdf", tags=["tax"])
    seed(repo, name="other.txt")

    response = client.get(
        "/api/files", headers=auth(), params={"q": "TAX", "folder_id": str(folder.id)}
    )

    assert sorted(names(response)) == ["Tax 2026.pdf", "r.pdf", "scan.pdf"]


def test_the_tag_filter_is_trimmed_lowercased_and_exact(repo):
    seed(repo, name="a.txt", tags=["tax"])
    seed(repo, name="b.txt", tags=["taxes"])

    assert names(client.get("/api/files", headers=auth(), params={"tag": " TAX "})) == ["a.txt"]


def test_q_and_tag_together_must_both_match(repo):
    seed(repo, name="a.txt", tags=["tax"])
    seed(repo, name="receipt.txt", tags=["tax"])
    seed(repo, name="receipt-2.txt")

    response = client.get("/api/files", headers=auth(), params={"q": "receipt", "tag": "tax"})

    assert names(response) == ["receipt.txt"]


def test_a_blank_search_is_the_plain_folder_listing(repo):
    folder = repo.folders.create("user-1", name="A", parent_id=None)
    seed(repo, name="root.txt")
    seed(repo, name="inside.txt", folder_id=folder.id)

    response = client.get("/api/files", headers=auth(), params={"q": "   ", "tag": ""})

    assert response.status_code == 200
    assert names(response) == ["root.txt"]


def test_an_overlong_search_is_rejected(repo):
    assert client.get("/api/files", headers=auth(), params={"q": "x" * 201}).status_code == 422


def test_sort_by_name_ascending_ignores_case(repo):
    for name in ("b.txt", "A.txt", "c.txt"):
        seed(repo, name=name)

    response = client.get("/api/files", headers=auth(), params={"sort": "name", "order": "asc"})

    assert names(response) == ["A.txt", "b.txt", "c.txt"]


def test_sort_by_size_descending(repo):
    seed(repo, name="small.txt", size=1)
    seed(repo, name="big.txt", size=9)

    response = client.get("/api/files", headers=auth(), params={"sort": "size", "order": "desc"})

    assert names(response) == ["big.txt", "small.txt"]


def test_an_unknown_sort_key_is_rejected(repo):
    assert client.get("/api/files", headers=auth(), params={"sort": "owner"}).status_code == 422
```

- [ ] **Step 6: Run them to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_files.py -v`
Expected: FAIL — `FileInfo` has no `folder_id`/`description`/`tags`, and the new filter tests return the unfiltered list (`TypeError` on `list()` kwargs once the route passes them).

- [ ] **Step 7: Mirror the filters in the fake**

In `backend/tests/conftest.py`, replace `FakeFileRepository.list` with:

```python
    SORT_KEYS = {
        "name": lambda r: r.name.lower(),
        "size": lambda r: r.size_bytes,
        "updated_at": lambda r: r.updated_at,
    }

    def list(
        self,
        owner_sub,
        *,
        folder_id=None,
        q=None,
        tag=None,
        sort="updated_at",
        order="desc",
        limit=100,
        offset=0,
    ):
        # Mirrors FileRepository.list: q/tag drop the folder scope; q is a
        # literal, case-insensitive substring of name, description or any tag;
        # tag is exact containment; id breaks ties in the sort's direction.
        rows = self._live(owner_sub)
        if q is None and tag is None:
            rows = [r for r in rows if r.folder_id == folder_id]
        if q is not None:
            needle = q.lower()
            rows = [
                r
                for r in rows
                if needle in r.name.lower()
                or needle in (r.description or "").lower()
                or any(needle in t.lower() for t in r.tags)
            ]
        if tag is not None:
            rows = [r for r in rows if tag in r.tags]
        key = self.SORT_KEYS[sort]
        rows = sorted(rows, key=lambda r: (key(r), r.id), reverse=order == "desc")
        return rows[offset : offset + limit]
```

- [ ] **Step 8: Extend `FileInfo` and the list route**

In `backend/app/api/routes/files.py`, change the imports:

```python
from app.repositories.files import FileRepo, Order, QuotaExceeded, Sort
from app.repositories.folders import FolderRepo, FolderRepository
```

Replace `FileInfo` with:

```python
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
```

Replace `list_files` with:

```python
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
```

(`_live_folder` is defined further down the module; that is fine at call time.)

- [ ] **Step 9: Run the unit tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest -m unit -v`
Expected: all pass.

- [ ] **Step 10: Regenerate the snapshot, then run every backend suite**

Run: `make snapshot && git diff --stat backend/tests/regression/openapi.snapshot.json`
Expected: `FileInfo` gains three properties; `GET /api/files` gains `folder_id`, `q`, `tag`, `sort`, `order`.

Run: `cd backend && .venv/bin/python -m pytest -m regression && .venv/bin/python -m pytest -m integration && .venv/bin/python -m ruff format --check . && .venv/bin/python -m ruff check .`
Expected: all green (`test_files_minio.py` compares names only, so the new `FileInfo` fields do not break it).

- [ ] **Step 11: Commit**

```bash
git add backend/app/repositories/files.py backend/app/api/routes/files.py backend/tests/conftest.py \
        backend/tests/unit/test_files.py backend/tests/integration/test_repository.py \
        backend/tests/regression/openapi.snapshot.json
git commit -m "feat(files): folder-scoped listing, search, tag filter and sort"
```

---

### Task 4: `PATCH /api/files/{id}` and upload into a folder

**Files:**
- Modify: `backend/app/repositories/files.py` (`update()`)
- Modify: `backend/app/api/routes/files.py:1-20,105-156` (imports, `FilePatch`, `_validated_tags`, `update_file`, `upload_file`)
- Modify: `backend/tests/conftest.py` (`FakeFileRepository.update`)
- Test: `backend/tests/unit/test_files.py`, `backend/tests/integration/test_repository.py`
- Regenerate: `backend/tests/regression/openapi.snapshot.json`

**Interfaces:**
- Consumes: `NameTaken`, `_live_folder`, `_json_id`, `_validated_name`, `FolderRepo`.
- Produces:
  - `FileRepository.update(file, **changes) -> File`; raises `NameTaken` when `uq_files_folder_name` rejects it (session rolled back, row reloads its old values).
  - `PATCH /api/files/{file_id}` with body `FilePatch {name?, description?, tags?, folder_id?}` → `200 FileInfo`; `404` / `409` / `422`.
  - `POST /api/files` accepts multipart field `folder_id`.

- [ ] **Step 1: Write the failing integration tests**

Append to `backend/tests/integration/test_repository.py` (add `NameTaken` to the `app.repositories.files` import):

```python
def test_update_renames_moves_describes_and_retags(repository, db):
    folder = FolderRepository(db).create("user-1", name="A", parent_id=None)
    row = ready(repository, db, "a.txt")

    repository.update(row, name="b.txt", folder_id=folder.id, description="d", tags=["x"])

    fresh = db.execute(text("SELECT name, folder_id, description, tags FROM files")).one()
    assert tuple(fresh) == ("b.txt", folder.id, "d", ["x"])


def test_update_into_a_clash_raises_name_taken_and_keeps_the_row(repository, db):
    ready(repository, db, "a.txt")
    row = ready(repository, db, "b.txt")

    with pytest.raises(NameTaken):
        repository.update(row, name="A.TXT")

    assert repository.get("user-1", row.id).name == "b.txt"


def test_a_case_only_file_rename_is_not_a_clash(repository, db):
    row = ready(repository, db, "notes.txt")

    repository.update(row, name="Notes.txt")

    assert repository.get("user-1", row.id).name == "Notes.txt"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest -m integration tests/integration/test_repository.py -v`
Expected: the three new tests FAIL with `AttributeError: 'FileRepository' object has no attribute 'update'`.

- [ ] **Step 3: Implement `update()`**

In `backend/app/repositories/files.py`, add `from sqlalchemy.exc import IntegrityError` to the imports, and after `soft_delete`:

```python
    def update(self, file: File, **changes: Any) -> File:
        """Rename / move / describe / retag. No bytes move: the object key is
        the id, not the name or the folder."""
        for field, value in changes.items():
            setattr(file, field, value)
        try:
            self.db.commit()
        except IntegrityError as e:
            self.db.rollback()
            raise NameTaken from e
        return file
```

- [ ] **Step 4: Run the integration tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest -m integration tests/integration/test_repository.py -v`
Expected: all pass.

- [ ] **Step 5: Write the failing unit tests**

Append to `backend/tests/unit/test_files.py` (add `import pytest` and `from app.api.routes import files as files_routes` to the imports):

```python
def patch(file_id, body, sub="user-1"):
    return client.patch(f"/api/files/{file_id}", headers=auth(sub), json=body)


def test_rename_is_audited_and_touches_no_object(repo, monkeypatch):
    monkeypatch.setattr(files_routes, "minio_client", lambda: pytest.fail("PATCH touched MinIO"))
    row = seed(repo, name="a.txt")

    response = patch(row.id, {"name": " b.txt "})

    assert response.status_code == 200
    assert response.json()["name"] == "b.txt"
    assert repo.audits[-1][1:] == ("rename", "file", row.id, {"before": "a.txt", "after": "b.txt"})


def test_rename_validates_the_name(repo):
    row = seed(repo)

    assert patch(row.id, {"name": ".."}).status_code == 422


def test_rename_into_a_clash_in_the_same_folder_is_a_409(repo):
    seed(repo, name="a.txt")
    row = seed(repo, name="b.txt")

    response = patch(row.id, {"name": "A.TXT"})

    assert response.status_code == 409
    assert response.json()["detail"] == "A file named A.TXT already exists here"
    assert row.name == "b.txt"


def test_a_case_only_rename_is_not_a_clash_with_itself(repo):
    row = seed(repo, name="notes.txt")

    assert patch(row.id, {"name": "Notes.txt"}).json()["name"] == "Notes.txt"


def test_the_same_name_is_fine_in_another_folder(repo):
    folder = repo.folders.create("user-1", name="A", parent_id=None)
    seed(repo, name="a.txt")
    row = seed(repo, name="a.txt", folder_id=folder.id)

    assert patch(row.id, {"folder_id": None}).status_code == 409
    assert patch(row.id, {"name": "b.txt", "folder_id": None}).status_code == 200


def test_an_empty_description_is_stored_as_null(repo):
    row = seed(repo, description="old")

    response = patch(row.id, {"description": ""})

    assert response.json()["description"] is None
    assert repo.audits[-1][1] == "retag"
    assert repo.audits[-1][4] == {
        "before": {"description": "old", "tags": []},
        "after": {"description": None, "tags": []},
    }


def test_an_overlong_description_is_a_422(repo):
    row = seed(repo)

    assert patch(row.id, {"description": "x" * 2001}).status_code == 422


def test_tags_are_trimmed_lowercased_and_deduplicated_in_order(repo):
    row = seed(repo)

    response = patch(row.id, {"tags": [" Tax ", "2026", "tax", "", "  "]})

    assert response.json()["tags"] == ["tax", "2026"]


def test_too_many_or_too_long_tags_are_a_422(repo):
    row = seed(repo)

    assert patch(row.id, {"tags": [f"t{i}" for i in range(21)]}).status_code == 422
    assert patch(row.id, {"tags": ["x" * 51]}).status_code == 422
    assert patch(row.id, {"tags": ["x" * 50]}).status_code == 200


def test_folder_id_omitted_is_unchanged_and_null_is_the_root(repo):
    folder = repo.folders.create("user-1", name="A", parent_id=None)
    row = seed(repo)

    moved = patch(row.id, {"folder_id": str(folder.id)})
    assert moved.json()["folder_id"] == str(folder.id)
    assert repo.audits[-1][1:] == (
        "move",
        "file",
        row.id,
        {"before": None, "after": str(folder.id)},
    )

    assert patch(row.id, {"name": "b.txt"}).json()["folder_id"] == str(folder.id)
    assert patch(row.id, {"folder_id": None}).json()["folder_id"] is None


def test_moving_into_an_unknown_folder_is_a_404(repo):
    row = seed(repo)

    assert patch(row.id, {"folder_id": str(uuid.uuid4())}).status_code == 404
    assert row.folder_id is None


def test_one_patch_writes_one_audit_row_per_action(repo):
    folder = repo.folders.create("user-1", name="A", parent_id=None)
    row = seed(repo)

    patch(row.id, {"name": "b.txt", "folder_id": str(folder.id), "tags": ["x"]})

    assert [a[1] for a in repo.audits] == ["rename", "move", "retag"]


def test_an_empty_patch_is_200_and_audits_nothing(repo):
    row = seed(repo)

    assert patch(row.id, {}).status_code == 200
    assert patch(row.id, {"name": "a.txt", "description": None, "tags": []}).status_code == 200
    assert repo.audits == []


def test_patch_of_an_unknown_or_foreign_file_is_a_404(repo):
    row = seed(repo, sub="user-1")

    assert patch(row.id, {"name": "x.txt"}, sub="user-2").status_code == 404
    assert patch(uuid.uuid4(), {"name": "x.txt"}).status_code == 404
    assert row.name == "a.txt"


def test_patch_needs_a_token(repo):
    row = seed(repo)

    assert client.patch(f"/api/files/{row.id}", json={"name": "b.txt"}).status_code == 401


def upload_into(folder_id, name="a.txt", data=b"hello", sub="user-1"):
    return client.post(
        "/api/files",
        headers=auth(sub),
        data={"folder_id": str(folder_id)},
        files={"file": (name, data, "text/plain")},
    )


def test_upload_into_a_folder(repo, store):
    folder = repo.folders.create("user-1", name="A", parent_id=None)

    response = upload_into(folder.id)

    assert response.status_code == 201
    assert response.json()["folder_id"] == str(folder.id)


def test_upload_into_an_unknown_or_foreign_folder_is_a_404(repo, store):
    theirs = repo.folders.create("user-2", name="A", parent_id=None)

    assert upload_into(theirs.id).status_code == 404
    assert upload_into(uuid.uuid4()).status_code == 404
    assert repo.rows == []
    assert store.objects == {}


def test_br2_versions_per_folder(repo, store):
    folder = repo.folders.create("user-1", name="A", parent_id=None)
    at_root = upload(name="notes.txt").json()

    first = upload_into(folder.id, name="notes.txt").json()
    second = upload_into(folder.id, name="notes.txt", data=b"two")

    assert first["id"] != at_root["id"]
    assert second.status_code == 200
    assert second.json()["id"] == first["id"]
```

- [ ] **Step 6: Run them to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_files.py -v`
Expected: FAIL — `PATCH` returns `405 Method Not Allowed`; uploads ignore `folder_id` (`folder_id` is `None` in the response, the 404 tests get `201`).

- [ ] **Step 7: Mirror `update()` in the fake**

In `backend/tests/conftest.py`, add to `FakeFileRepository` (after `soft_delete`):

```python
    def update(self, file, **changes):
        # uq_files_folder_name: case-insensitive, live ready rows, excluding self.
        name = changes.get("name", file.name)
        folder_id = changes.get("folder_id", file.folder_id)
        for r in self._live(file.owner_sub):
            if r is not file and r.folder_id == folder_id and r.name.lower() == name.lower():
                raise NameTaken
        for field, value in changes.items():
            setattr(file, field, value)
        file.updated_at = datetime.now(UTC)
        return file
```

- [ ] **Step 8: Implement `PATCH` and upload into a folder**

In `backend/app/api/routes/files.py`, change the imports:

```python
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
```

Replace the head of `upload_file` (signature through the `find_by_name` call) and its `reserve` call's `folder_id`:

```python
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
```

…keep the extension, size and content-type checks as they are, then:

```python
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
```

After `get_file`, add:

```python
class FilePatch(BaseModel):
    """Omitted = unchanged; an explicit `folder_id: null` = move to the root."""

    name: str | None = None
    description: str | None = Field(None, max_length=2000)
    tags: list[str] | None = None
    folder_id: uuid.UUID | None = None


def _validated_tags(raw: list[str]) -> list[str]:
    """Trimmed, lowercased, empties and repeats dropped, submission order kept."""
    tags: list[str] = []
    for tag in (t.strip().lower() for t in raw):
        if tag and tag not in tags:
            tags.append(tag)
    if len(tags) > 20 or any(len(tag) > 50 for tag in tags):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "At most 20 tags of at most 50 characters"
        )
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
    if "description" in body.model_fields_set and (body.description or None) != row.description:
        changes["description"] = body.description or None
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
```

- [ ] **Step 9: Run the unit tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest -m unit -v`
Expected: all pass.

- [ ] **Step 10: Regenerate the snapshot, then run every backend suite**

Run: `make snapshot && git diff --stat backend/tests/regression/openapi.snapshot.json`
Expected: adds `PATCH /api/files/{file_id}`, `FilePatch`, and the `folder_id` field of the upload body schema.

Run: `cd backend && .venv/bin/python -m pytest -m regression && .venv/bin/python -m pytest -m integration && .venv/bin/python -m ruff format --check . && .venv/bin/python -m ruff check .`
Expected: all green.

- [ ] **Step 11: Commit**

```bash
git add backend/app/repositories/files.py backend/app/api/routes/files.py backend/tests/conftest.py \
        backend/tests/unit/test_files.py backend/tests/integration/test_repository.py \
        backend/tests/regression/openapi.snapshot.json
git commit -m "feat(files): PATCH rename/describe/retag/move and upload into a folder"
```

---

### Task 5: Ownership regression for every new route, and the docs

**Files:**
- Modify: `backend/tests/regression/test_r1_cross_owner_isolation.py`
- Modify: `docs/testing.md:11-13,72-80,242-262`, `CLAUDE.md:52-54`

**Interfaces:**
- Consumes: every route from Tasks 2–4; `repo.rows`, `repo.folders.rows`.
- Produces: nothing new — pins R-1 for Phase 2.

- [ ] **Step 1: Write the regression tests**

In `backend/tests/regression/test_r1_cross_owner_isolation.py`, extend the "Covers" list in the module docstring with:

```
- folders: list, rename/move, delete (plain and recursive), and listing files
  in a foreign folder are all 404 or empty, and nothing changes
- a foreign folder cannot be a parent, a move target or an upload target
- PATCH on a foreign file is a 404 and changes nothing
- search (q and tag) never returns a foreign file
```

and append:

```python
def folder(name="Invoices", parent_id=None, sub="user-1"):
    return client.post(
        "/api/folders", headers=auth(sub), json={"name": name, "parent_id": parent_id}
    )


def test_user_two_cannot_see_or_touch_user_ones_folder(repo, store):
    created = folder().json()
    folder_id = created["id"]

    assert client.get("/api/folders", headers=auth("user-2")).json() == []
    rename = client.patch(f"/api/folders/{folder_id}", headers=auth("user-2"), json={"name": "x"})
    assert rename.status_code == 404
    for query in ("", "?recursive=true"):
        response = client.delete(f"/api/folders/{folder_id}{query}", headers=auth("user-2"))
        assert response.status_code == 404
    listing = client.get("/api/files", headers=auth("user-2"), params={"folder_id": folder_id})
    assert listing.status_code == 404
    assert client.get("/api/folders", headers=auth("user-1")).json() == [created]


def test_user_two_cannot_use_user_ones_folder_as_a_target(repo, store):
    target = folder().json()["id"]
    own_folder = folder(name="Mine", sub="user-2").json()["id"]
    own_file = upload(sub="user-2").json()["id"]

    assert folder(name="x", parent_id=target, sub="user-2").status_code == 404
    move_folder = client.patch(
        f"/api/folders/{own_folder}", headers=auth("user-2"), json={"parent_id": target}
    )
    assert move_folder.status_code == 404
    move_file = client.patch(
        f"/api/files/{own_file}", headers=auth("user-2"), json={"folder_id": target}
    )
    assert move_file.status_code == 404
    into = client.post(
        "/api/files",
        headers=auth("user-2"),
        data={"folder_id": target},
        files={"file": ("b.txt", b"x", "text/plain")},
    )
    assert into.status_code == 404
    assert {r.folder_id for r in repo.rows} == {None}
    assert [f.parent_id for f in repo.folders.rows] == [None, None]


def test_user_two_cannot_patch_user_ones_file(repo, store):
    created = upload(sub="user-1").json()

    response = client.patch(
        f"/api/files/{created['id']}",
        headers=auth("user-2"),
        json={"name": "stolen.txt", "tags": ["x"], "folder_id": None},
    )

    assert response.status_code == 404
    assert client.get(f"/api/files/{created['id']}", headers=auth()).json() == created
    assert [a[1] for a in repo.audits] == ["upload"]


def test_search_never_returns_user_ones_files(repo, store):
    created = upload(name="tax.txt", sub="user-1").json()
    client.patch(f"/api/files/{created['id']}", headers=auth(), json={"tags": ["tax"]})

    for params in ({"q": "tax"}, {"tag": "tax"}):
        assert client.get("/api/files", headers=auth("user-2"), params=params).json() == []
```

- [ ] **Step 2: Run them and watch one fail on purpose**

Run: `cd backend && .venv/bin/python -m pytest tests/regression/test_r1_cross_owner_isolation.py -v`
Expected: 9 passed.

Then prove they can fail (docs/testing.md: "reintroduce the bug and watch it fail"): in `update_file` in `backend/app/api/routes/files.py`, temporarily change `sub = claims["sub"]` to `sub = "user-1"`, run the file again, see `test_user_two_cannot_patch_user_ones_file` FAIL, then revert the line.

- [ ] **Step 3: Update the docs**

In `CLAUDE.md`, replace the `api/routes/files.py` bullet with:

```markdown
- `api/routes/files.py` — home files: metadata in PostgreSQL (`repositories/files.py`),
  bytes in the Infra MinIO (bucket `darkangel-files`, provisioned by `make minio`) under
  `<sub>/<file uuid>`. `api/routes/folders.py` + `repositories/folders.py` hold the
  folder tree. Every query filters on the caller's `sub`. Tests swap `minio_client()`
  and both repositories for in-memory fakes (`tests/conftest.py`).
```

In `docs/testing.md`, §Coverage: replace "which omits `app/repositories/files.py` and `app/scripts/backfill.py`. Both are exercised" with "which omits `app/repositories/files.py`, `app/repositories/folders.py` and `app/scripts/backfill.py`. All three are exercised", and "`FakeFileRepository` *substitutes*" with "the fake repositories *substitute*". Then refresh every test count in the file (the table at the top, the "modules run" sample, the frontend total, and "14 tests in `tests/integration/test_repository.py`") from the summary lines of:

Run: `make test-unit; make test-regression; make test-integration; cd frontend && npm run test`

- [ ] **Step 4: Verify**

Run: `cd backend && .venv/bin/python -m pytest -m unit && .venv/bin/python -m pytest -m regression && .venv/bin/python -m ruff check .`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/regression/test_r1_cross_owner_isolation.py docs/testing.md CLAUDE.md
git commit -m "test(files): pin R-1 ownership for folders, PATCH and search"
```

---

### Task 6: Frontend API — `ApiError`, files and folders calls

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/files.ts`
- Create: `frontend/src/api/folders.ts`
- Modify: `frontend/src/stores/files.ts:27` (one line: stop `map` passing its index as `folderId`)
- Modify: `frontend/tests/unit/FilesView.test.ts`, `frontend/tests/unit/stores-files.test.ts`, `frontend/tests/regression/pr12-download-blob-url.test.ts` (fixtures gain the new `HomeFile` fields so `vue-tsc` passes)
- Test: `frontend/tests/unit/client.test.ts`, `frontend/tests/unit/api-files-folders.test.ts`

**Interfaces:**
- Produces:
  - `class ApiError extends Error { status: number; body: unknown }` — thrown by `apiRequest` for any non-OK response. `message` is the server's `detail` when it is a string, the joined `msg`s when it is a pydantic error list, else `"<METHOD> <path> failed with <status>"`.
  - `apiRequest(method, path, body?, contentType?)`.
  - `HomeFile` gains `folder_id: string | null`, `description: string | null`, `tags: string[]`. `type Sort = 'name' | 'size' | 'updated_at'`, `interface ListParams { folder_id?; q?; tag?; sort?: Sort; order?: 'asc' | 'desc'; limit?; offset? }`, `interface FilePatch { name?; description?: string | null; tags?: string[]; folder_id?: string | null }`.
  - `listFiles(params?: ListParams)`, `uploadFile(file, folderId?: string | null)`, `updateFile(id, patch): Promise<HomeFile>`.
  - `interface Folder { id: string; name: string; parent_id: string | null }`, `listFolders()`, `createFolder({ name, parent_id })`, `updateFolder(id, { name?, parent_id? })`, `deleteFolder(id, recursive: boolean)`.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/tests/unit/client.test.ts` (change the import to `import { ApiError, apiGet, apiRequest } from '@/api/client'`):

```ts
describe('ApiError', () => {
  it('carries the status, the parsed body, and the server detail as its message', async () => {
    const body = { detail: 'A folder named work already exists here' }
    fetchMock.mockResolvedValue(response(409, body))

    const error = await apiRequest('POST', '/folders').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(409)
    expect((error as ApiError).body).toEqual(body)
    expect((error as ApiError).message).toBe('A folder named work already exists here')
  })

  it('joins a pydantic validation error list', async () => {
    fetchMock.mockResolvedValue(response(422, { detail: [{ msg: 'too long' }, { msg: 'bad' }] }))

    await expect(apiRequest('PATCH', '/files/1')).rejects.toThrow('too long; bad')
  })

  it('falls back to method, path and status when the body is not JSON', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => {
        throw new SyntaxError('not json')
      },
    } as unknown as Response)

    await expect(apiRequest('GET', '/files')).rejects.toThrow('GET /files failed with 502')
  })
})

it('sends a Content-Type only when one is given', async () => {
  await apiRequest('PATCH', '/files/1', '{}', 'application/json')

  expect(fetchMock.mock.calls[0][1].headers).toEqual({ 'Content-Type': 'application/json' })
})
```

Create `frontend/tests/unit/api-files-folders.test.ts`:

```ts
import { beforeEach, expect, it, vi } from 'vitest'

import { apiGet, apiRequest } from '@/api/client'
import { listFiles, updateFile, uploadFile } from '@/api/files'
import { createFolder, deleteFolder, listFolders, updateFolder } from '@/api/folders'

vi.mock('@/api/client', () => ({
  apiGet: vi.fn(async () => []),
  apiRequest: vi.fn(async () => ({ json: async () => ({}) })),
}))

const JSON_TYPE = 'application/json'

beforeEach(() => {
  vi.clearAllMocks()
})

it('listFiles with no params asks for the root', async () => {
  await listFiles()

  expect(apiGet).toHaveBeenCalledWith('/files')
})

it('listFiles puts only the params that are set in the query string', async () => {
  await listFiles({ folder_id: 'f1', q: 'tax', tag: undefined, sort: 'name', limit: 100, offset: 0 })

  expect(apiGet).toHaveBeenCalledWith('/files?folder_id=f1&q=tax&sort=name&limit=100&offset=0')
})

it('uploadFile sends folder_id only when there is one', async () => {
  await uploadFile(new File(['x'], 'a.txt'), 'f1')
  await uploadFile(new File(['x'], 'b.txt'))

  const [first, second] = vi.mocked(apiRequest).mock.calls.map((call) => call[2] as FormData)
  expect(first.get('folder_id')).toBe('f1')
  expect(second.has('folder_id')).toBe(false)
})

it('updateFile PATCHes JSON and keeps an explicit null folder', async () => {
  await updateFile('id1', { folder_id: null })

  expect(apiRequest).toHaveBeenCalledWith('PATCH', '/files/id1', '{"folder_id":null}', JSON_TYPE)
})

it('talks to /folders for list, create, update and delete', async () => {
  await listFolders()
  await createFolder({ name: 'A', parent_id: null })
  await updateFolder('f1', { name: 'B' })
  await deleteFolder('f1', false)
  await deleteFolder('f1', true)

  expect(apiGet).toHaveBeenCalledWith('/folders')
  expect(vi.mocked(apiRequest).mock.calls).toEqual([
    ['POST', '/folders', '{"name":"A","parent_id":null}', JSON_TYPE],
    ['PATCH', '/folders/f1', '{"name":"B"}', JSON_TYPE],
    ['DELETE', '/folders/f1'],
    ['DELETE', '/folders/f1?recursive=true'],
  ])
})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npm run test -- tests/unit/client.test.ts tests/unit/api-files-folders.test.ts`
Expected: FAIL — `ApiError` is not exported, `@/api/folders` does not resolve.

- [ ] **Step 3: Implement the client changes**

Replace `frontend/src/api/client.ts` with:

```ts
import { accessToken } from '@/auth'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

/** A non-OK response. `body` is the parsed JSON (e.g. a folder delete's
 *  `{detail, folders, files}`), or null when there was none. */
export class ApiError extends Error {
  status: number
  body: unknown

  constructor(message: string, status: number, body: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

function messageOf(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((d: { msg?: string }) => d.msg).join('; ')
  return fallback
}

export async function apiRequest(
  method: string,
  path: string,
  body?: BodyInit,
  contentType?: string,
): Promise<Response> {
  const token = await accessToken()
  const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {}
  if (contentType) headers['Content-Type'] = contentType
  const response = await fetch(`${BASE_URL}${path}`, { method, headers, body })

  if (!response.ok) {
    let payload: unknown = null
    try {
      payload = await response.json()
    } catch {
      // Not JSON (a proxy error page, an empty body): keep the generic message.
    }
    const fallback = `${method} ${path} failed with ${response.status}`
    throw new ApiError(messageOf(payload, fallback), response.status, payload)
  }

  return response
}

export async function apiGet<T>(path: string): Promise<T> {
  return (await (await apiRequest('GET', path)).json()) as T
}
```

- [ ] **Step 4: Implement the files and folders modules**

Replace `frontend/src/api/files.ts` with:

```ts
import { apiGet, apiRequest } from './client'

export interface HomeFile {
  id: string
  name: string
  size: number
  content_type: string
  modified: string | null
  folder_id: string | null
  description: string | null
  tags: string[]
}

export type Sort = 'name' | 'size' | 'updated_at'

/** No folder_id = the root. q or tag = search every folder instead. */
export interface ListParams {
  folder_id?: string
  q?: string
  tag?: string
  sort?: Sort
  order?: 'asc' | 'desc'
  limit?: number
  offset?: number
}

/** Omitted = unchanged; `folder_id: null` = move to the root. */
export interface FilePatch {
  name?: string
  description?: string | null
  tags?: string[]
  folder_id?: string | null
}

export function listFiles(params: ListParams = {}): Promise<HomeFile[]> {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) query.set(key, String(value))
  }
  const search = query.toString()
  return apiGet<HomeFile[]>(search ? `/files?${search}` : '/files')
}

export async function uploadFile(file: File, folderId: string | null = null): Promise<HomeFile> {
  const form = new FormData()
  form.append('file', file)
  if (folderId) form.append('folder_id', folderId)
  return (await apiRequest('POST', '/files', form)).json()
}

export async function updateFile(id: string, patch: FilePatch): Promise<HomeFile> {
  return (await apiRequest('PATCH', `/files/${id}`, JSON.stringify(patch), 'application/json')).json()
}

export async function downloadFile(id: string): Promise<Blob> {
  return (await apiRequest('GET', `/files/${id}/content`)).blob()
}

export async function deleteFile(id: string): Promise<void> {
  await apiRequest('DELETE', `/files/${id}`)
}
```

Create `frontend/src/api/folders.ts`:

```ts
import { apiGet, apiRequest } from './client'

export interface Folder {
  id: string
  name: string
  parent_id: string | null
}

const JSON_TYPE = 'application/json'

export function listFolders(): Promise<Folder[]> {
  return apiGet<Folder[]>('/folders')
}

export async function createFolder(folder: { name: string; parent_id: string | null }): Promise<Folder> {
  return (await apiRequest('POST', '/folders', JSON.stringify(folder), JSON_TYPE)).json()
}

/** Omitted = unchanged; `parent_id: null` = move to the root. */
export async function updateFolder(
  id: string,
  patch: { name?: string; parent_id?: string | null },
): Promise<Folder> {
  return (await apiRequest('PATCH', `/folders/${id}`, JSON.stringify(patch), JSON_TYPE)).json()
}

/** A non-empty folder without `recursive` rejects with an ApiError 409 whose
 *  body is `{detail, folders, files}`. */
export async function deleteFolder(id: string, recursive: boolean): Promise<void> {
  await apiRequest('DELETE', recursive ? `/folders/${id}?recursive=true` : `/folders/${id}`)
}
```

- [ ] **Step 5: Keep the store and the existing tests type-correct**

`picked.map(uploadFile)` would now pass `map`'s index as `folderId`. In `frontend/src/stores/files.ts`, change line 27 to:

```ts
  const upload = (picked: File[]) => run(() => Promise.all(picked.map((file) => uploadFile(file))))
```

In `frontend/tests/unit/FilesView.test.ts` (three fixtures), `frontend/tests/unit/stores-files.test.ts` (`aFile`) and `frontend/tests/regression/pr12-download-blob-url.test.ts` (`file` and the `listFiles` mock), add to every file object literal:

```ts
      folder_id: null,
      description: null,
      tags: [],
```

- [ ] **Step 6: Run the tests and the type-check**

Run: `cd frontend && npm run test && npm run build`
Expected: all tests pass; `vue-tsc` reports no error; build succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api frontend/src/stores/files.ts frontend/tests
git commit -m "feat(spa): ApiError, folder API, file list params and PATCH"
```

---

### Task 7: The files store — folders, paths, pages, folder delete

**Files:**
- Modify: `frontend/src/stores/files.ts` (rewrite)
- Test: `frontend/tests/unit/stores-files.test.ts` (rewrite)

**Interfaces:**
- Consumes: Task 6's API functions, `ApiError`, `PAGE_SIZE` below.
- Produces (store `useFilesStore`):
  - state: `files: HomeFile[]`, `folders: Folder[]`, `hasMore: boolean`, `error: string | null`, `loading: boolean`
  - `load(params?: ListParams)` — remembers params, loads the folder list and the first page.
  - `loadMore()` — appends the next page with the remembered params.
  - `pathOf(id: string | null): Folder[]` — root-first chain ending at `id`; `[]` for the root or an unknown id.
  - `upload(picked: File[], folderId?: string | null)`, `remove(id)`, `removeFolder(folder: Folder)` — each reloads afterwards.
  - `export const PAGE_SIZE = 100`.

- [ ] **Step 1: Write the failing tests**

Replace `frontend/tests/unit/stores-files.test.ts` with:

```ts
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'
import { deleteFile, listFiles, uploadFile, type HomeFile } from '@/api/files'
import { deleteFolder, listFolders } from '@/api/folders'
import { PAGE_SIZE, useFilesStore } from '@/stores/files'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/files', () => ({
  listFiles: vi.fn(async () => []),
  uploadFile: vi.fn(async () => {}),
  deleteFile: vi.fn(async () => {}),
}))
vi.mock('@/api/folders', () => ({
  listFolders: vi.fn(async () => []),
  deleteFolder: vi.fn(async () => {}),
}))

const aFile: HomeFile = {
  id: '11111111-1111-1111-1111-111111111111',
  name: 'a.txt',
  size: 5,
  content_type: 'text/plain',
  modified: null,
  folder_id: null,
  description: null,
  tags: [],
}
const A = { id: 'a', name: 'A', parent_id: null }
const B = { id: 'b', name: 'B', parent_id: 'a' }
const FIRST_PAGE = { limit: PAGE_SIZE, offset: 0 }

beforeEach(() => {
  setActivePinia(createPinia())
  vi.resetAllMocks()
  vi.mocked(listFiles).mockResolvedValue([aFile])
  vi.mocked(listFolders).mockResolvedValue([A, B])
  vi.stubGlobal('confirm', vi.fn(() => true))
})

it('load() fills files and folders and clears loading', async () => {
  const store = useFilesStore()

  const pending = store.load()
  expect(store.loading).toBe(true)
  await pending

  expect(store.files).toEqual([aFile])
  expect(store.folders).toEqual([A, B])
  expect(store.error).toBeNull()
  expect(store.loading).toBe(false)
})

it('load(params) asks for the first page with those params', async () => {
  await useFilesStore().load({ folder_id: 'a', sort: 'name' })

  expect(listFiles).toHaveBeenCalledWith({ folder_id: 'a', sort: 'name', ...FIRST_PAGE })
})

it('offers more only after a full page, and loadMore() appends the next one', async () => {
  const page = Array.from({ length: PAGE_SIZE }, (_, i) => ({ ...aFile, id: String(i) }))
  vi.mocked(listFiles).mockResolvedValueOnce(page).mockResolvedValueOnce([aFile])
  const store = useFilesStore()

  await store.load({ q: 'tax' })
  expect(store.hasMore).toBe(true)

  await store.loadMore()

  expect(listFiles).toHaveBeenLastCalledWith({ q: 'tax', limit: PAGE_SIZE, offset: PAGE_SIZE })
  expect(store.files).toHaveLength(PAGE_SIZE + 1)
  expect(store.hasMore).toBe(false)
})

it('pathOf walks parent_id up to the root', async () => {
  const store = useFilesStore()
  await store.load()

  expect(store.pathOf('b')).toEqual([A, B])
  expect(store.pathOf(null)).toEqual([])
  expect(store.pathOf('gone')).toEqual([])
})

it('upload() sends every picked file into the folder, then reloads', async () => {
  const one = new File(['a'], 'one.txt')
  const two = new File(['b'], 'two.txt')

  await useFilesStore().upload([one, two], 'a')

  expect(vi.mocked(uploadFile).mock.calls).toEqual([
    [one, 'a'],
    [two, 'a'],
  ])
  expect(listFiles).toHaveBeenCalledTimes(1)
})

it('remove() deletes by id, then reloads', async () => {
  await useFilesStore().remove(aFile.id)

  expect(deleteFile).toHaveBeenCalledWith(aFile.id)
  expect(listFiles).toHaveBeenCalledTimes(1)
})

it('removeFolder() deletes an empty folder without a second ask', async () => {
  await useFilesStore().removeFolder(A)

  expect(deleteFolder).toHaveBeenCalledWith('a', false)
  expect(confirm).not.toHaveBeenCalled()
  expect(listFolders).toHaveBeenCalledTimes(1)
})

it('removeFolder() confirms a 409 with its counts, then deletes recursively', async () => {
  vi.mocked(deleteFolder).mockRejectedValueOnce(
    new ApiError('A is not empty', 409, { detail: 'A is not empty', folders: 1, files: 2 }),
  )

  await useFilesStore().removeFolder(A)

  expect(confirm).toHaveBeenCalledWith('Delete A and its 1 folders / 2 files?')
  expect(vi.mocked(deleteFolder).mock.calls).toEqual([
    ['a', false],
    ['a', true],
  ])
})

it('removeFolder() stops when the confirmation is dismissed', async () => {
  vi.mocked(deleteFolder).mockRejectedValueOnce(
    new ApiError('A is not empty', 409, { detail: 'A is not empty', folders: 0, files: 1 }),
  )
  vi.stubGlobal('confirm', vi.fn(() => false))
  const store = useFilesStore()

  await store.removeFolder(A)

  expect(deleteFolder).toHaveBeenCalledTimes(1)
  expect(store.error).toBeNull()
})

it('removeFolder() reports any other failure', async () => {
  vi.mocked(deleteFolder).mockRejectedValueOnce(new ApiError('No such folder', 404, null))
  const store = useFilesStore()

  await store.removeFolder(A)

  expect(store.error).toBe('No such folder')
  expect(confirm).not.toHaveBeenCalled()
})

it('captures the message of a failed action and stops loading', async () => {
  vi.mocked(deleteFile).mockRejectedValue(new Error('DELETE /files/a.txt failed with 404'))
  const store = useFilesStore()

  await store.remove(aFile.id)

  expect(store.error).toBe('DELETE /files/a.txt failed with 404')
  expect(store.loading).toBe(false)
})

it('clears a previous error when the next action succeeds', async () => {
  const store = useFilesStore()
  store.error = 'stale'

  await store.load()

  expect(store.error).toBeNull()
})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npm run test -- tests/unit/stores-files.test.ts`
Expected: FAIL — `PAGE_SIZE` is not exported; `folders`, `pathOf`, `loadMore`, `removeFolder` are undefined.

- [ ] **Step 3: Rewrite the store**

Replace `frontend/src/stores/files.ts` with:

```ts
import { defineStore } from 'pinia'
import { ref } from 'vue'

import { ApiError } from '@/api/client'
import { deleteFile, listFiles, uploadFile, type HomeFile, type ListParams } from '@/api/files'
import { deleteFolder, listFolders, type Folder } from '@/api/folders'

export const PAGE_SIZE = 100

export const useFilesStore = defineStore('files', () => {
  const files = ref<HomeFile[]>([])
  // Flat: the whole tree is small, and pathOf() derives every path from it.
  const folders = ref<Folder[]>([])
  const hasMore = ref(false)
  const error = ref<string | null>(null)
  const loading = ref(false)
  let params: ListParams = {}

  async function run(action: () => Promise<unknown>) {
    loading.value = true
    error.value = null

    try {
      await action()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  // No optimistic updates: every mutation reloads the folders and the first page.
  async function refresh() {
    const [tree, page] = await Promise.all([
      listFolders(),
      listFiles({ ...params, limit: PAGE_SIZE, offset: 0 }),
    ])
    folders.value = tree
    files.value = page
    hasMore.value = page.length === PAGE_SIZE
  }

  /** Root first, ending at `id`. Empty for the root or an unknown id. */
  function pathOf(id: string | null): Folder[] {
    const path: Folder[] = []
    let folder = folders.value.find((f) => f.id === id)
    while (folder) {
      path.unshift(folder)
      const parent = folder.parent_id
      folder = folders.value.find((f) => f.id === parent)
    }
    return path
  }

  function load(next: ListParams = {}) {
    params = next
    return run(refresh)
  }

  const loadMore = () =>
    run(async () => {
      const page = await listFiles({ ...params, limit: PAGE_SIZE, offset: files.value.length })
      files.value = [...files.value, ...page]
      hasMore.value = page.length === PAGE_SIZE
    })

  const upload = (picked: File[], folderId: string | null = null) =>
    run(async () => {
      await Promise.all(picked.map((file) => uploadFile(file, folderId)))
      await refresh()
    })

  const remove = (id: string) =>
    run(async () => {
      await deleteFile(id)
      await refresh()
    })

  // BR-7: try the plain delete; a 409 names the subtree, and only then ask.
  const removeFolder = (folder: Folder) =>
    run(async () => {
      try {
        await deleteFolder(folder.id, false)
      } catch (e) {
        if (!(e instanceof ApiError) || e.status !== 409) throw e
        const counts = e.body as { folders: number; files: number }
        const question = `Delete ${folder.name} and its ${counts.folders} folders / ${counts.files} files?`
        if (!confirm(question)) return
        await deleteFolder(folder.id, true)
      }
      await refresh()
    })

  return {
    files,
    folders,
    hasMore,
    error,
    loading,
    pathOf,
    load,
    loadMore,
    upload,
    remove,
    removeFolder,
  }
})
```

- [ ] **Step 4: Keep the view tests off the real OIDC client**

The store now imports `@/api/client`, which imports `@/auth` and builds a real `UserManager`. Add, next to the other `vi.mock` calls, in `frontend/tests/unit/FilesView.test.ts` and `frontend/tests/regression/pr12-download-blob-url.test.ts`:

```ts
vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
```

- [ ] **Step 5: Run the tests and the type-check**

Run: `cd frontend && npm run test && npm run build`
Expected: all pass. (`FilesView.test.ts` still passes: the view calls `store.load()` with no argument and `store.upload(files)`, both still valid.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/stores/files.ts frontend/tests/unit/stores-files.test.ts \
        frontend/tests/unit/FilesView.test.ts frontend/tests/regression/pr12-download-blob-url.test.ts
git commit -m "feat(spa): store folders, paths, paging and the folder delete flow"
```

---

### Task 8: `EditDialog`

**Files:**
- Create: `frontend/src/components/EditDialog.vue`
- Test: `frontend/tests/unit/EditDialog.test.ts`

**Interfaces:**
- Consumes: `updateFile`, `createFolder`, `updateFolder`, `ApiError` messages (Task 6); `useFilesStore().folders` and `pathOf` (Task 7).
- Produces: `<EditDialog :file? :folder? :parent-id? @saved @close />`. Exactly one mode: `file` set = edit file; `folder` set = edit folder; neither = create a folder under `parentId` (default root). Emits `saved` after one successful `PATCH`/`POST`; emits `close` when the native dialog closes (Cancel or Escape). Errors are shown inside the dialog (`role="alert"`) and it stays open.

- [ ] **Step 1: Write the failing tests**

Create `frontend/tests/unit/EditDialog.test.ts`:

```ts
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'
import { updateFile, type HomeFile } from '@/api/files'
import { createFolder, updateFolder, type Folder } from '@/api/folders'
import EditDialog from '@/components/EditDialog.vue'
import { useFilesStore } from '@/stores/files'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/files', () => ({
  updateFile: vi.fn(async () => ({})),
  listFiles: vi.fn(async () => []),
  uploadFile: vi.fn(),
  deleteFile: vi.fn(),
}))
vi.mock('@/api/folders', () => ({
  createFolder: vi.fn(async () => ({})),
  updateFolder: vi.fn(async () => ({})),
  listFolders: vi.fn(async () => []),
  deleteFolder: vi.fn(),
}))

// jsdom has <dialog> but implements neither showModal() nor close().
HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
  this.setAttribute('open', '')
}
HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
  this.removeAttribute('open')
  this.dispatchEvent(new Event('close'))
}

const A: Folder = { id: 'a', name: 'A', parent_id: null }
const B: Folder = { id: 'b', name: 'B', parent_id: 'a' }
const C: Folder = { id: 'c', name: 'C', parent_id: null }
const file: HomeFile = {
  id: 'f1',
  name: 'a.txt',
  size: 1,
  content_type: 'text/plain',
  modified: null,
  folder_id: 'a',
  description: null,
  tags: ['tax', '2026'],
}

function render(props: { file?: HomeFile; folder?: Folder; parentId?: string | null }) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useFilesStore().folders = [A, B, C]
  return mount(EditDialog, { props, global: { plugins: [pinia] } })
}

const options = (wrapper: ReturnType<typeof render>) =>
  wrapper.findAll('select option').map((o) => o.text())

beforeEach(() => {
  vi.clearAllMocks()
})

it('opens as a modal when mounted', () => {
  const wrapper = render({ file })

  expect(wrapper.get('dialog').attributes('open')).toBeDefined()
})

it('prefills a file and saves it with one PATCH', async () => {
  const wrapper = render({ file })
  expect((wrapper.get('input[name="tags"]').element as HTMLInputElement).value).toBe('tax, 2026')
  expect(options(wrapper)).toEqual(['Home', 'A', 'A › B', 'C'])

  await wrapper.get('input[name="name"]').setValue('b.txt')
  await wrapper.get('input[name="tags"]').setValue('Tax, , receipts ')
  await wrapper.get('select').setValue('c')
  await wrapper.get('form').trigger('submit')
  await flushPromises()

  expect(updateFile).toHaveBeenCalledWith('f1', {
    name: 'b.txt',
    description: '',
    tags: ['Tax', 'receipts'],
    folder_id: 'c',
  })
  expect(wrapper.emitted('saved')).toHaveLength(1)
})

it('shows a 409 inside the dialog and stays open', async () => {
  vi.mocked(updateFile).mockRejectedValue(
    new ApiError('A file named b.txt already exists here', 409, null),
  )
  const wrapper = render({ file })

  await wrapper.get('form').trigger('submit')
  await flushPromises()

  expect(wrapper.get('[role="alert"]').text()).toBe('A file named b.txt already exists here')
  expect(wrapper.emitted('saved')).toBeUndefined()
  expect(wrapper.get('dialog').attributes('open')).toBeDefined()
})

it('offers a folder every parent except itself and its descendants', async () => {
  const wrapper = render({ folder: A })

  expect(options(wrapper)).toEqual(['Home', 'C'])

  await wrapper.get('input[name="name"]').setValue('Bills')
  await wrapper.get('form').trigger('submit')
  await flushPromises()

  expect(updateFolder).toHaveBeenCalledWith('a', { name: 'Bills', parent_id: null })
})

it('creates a folder inside the current one', async () => {
  const wrapper = render({ parentId: 'a' })

  await wrapper.get('input[name="name"]').setValue('New')
  await wrapper.get('form').trigger('submit')
  await flushPromises()

  expect(createFolder).toHaveBeenCalledWith({ name: 'New', parent_id: 'a' })
  expect(wrapper.find('textarea').exists()).toBe(false)
})

it('emits close on Cancel', async () => {
  const wrapper = render({ file })

  await wrapper.findAll('button').find((b) => b.text() === 'Cancel')!.trigger('click')

  expect(wrapper.emitted('close')).toHaveLength(1)
})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npm run test -- tests/unit/EditDialog.test.ts`
Expected: FAIL — `Failed to resolve import "@/components/EditDialog.vue"`.

- [ ] **Step 3: Write the component**

Create `frontend/src/components/EditDialog.vue`:

```vue
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { updateFile, type HomeFile } from '@/api/files'
import { createFolder, updateFolder, type Folder } from '@/api/folders'
import { useFilesStore } from '@/stores/files'

// One of three modes: `file` = edit it, `folder` = edit it, neither = new folder in `parentId`.
const props = defineProps<{ file?: HomeFile; folder?: Folder; parentId?: string | null }>()
const emit = defineEmits<{ saved: []; close: [] }>()

const store = useFilesStore()
const dialog = ref<HTMLDialogElement>()
const name = ref(props.file?.name ?? props.folder?.name ?? '')
const description = ref(props.file?.description ?? '')
const tags = ref(props.file?.tags.join(', ') ?? '')
const target = ref<string | null>(
  props.file ? props.file.folder_id : props.folder ? props.folder.parent_id : (props.parentId ?? null),
)
const error = ref<string | null>(null)

const title = props.file ? 'Edit file' : props.folder ? 'Edit folder' : 'New folder'
const label = (id: string) => store.pathOf(id).map((f) => f.name).join(' › ')

// BR-5: a folder cannot move into itself or anywhere below it.
const choices = computed(() =>
  store.folders.filter((f) => !props.folder || !store.pathOf(f.id).some((a) => a.id === props.folder!.id)),
)

onMounted(() => dialog.value?.showModal())

async function save() {
  error.value = null
  try {
    if (props.file) {
      await updateFile(props.file.id, {
        name: name.value,
        description: description.value,
        tags: tags.value.split(',').map((t) => t.trim()).filter(Boolean),
        folder_id: target.value,
      })
    } else if (props.folder) {
      await updateFolder(props.folder.id, { name: name.value, parent_id: target.value })
    } else {
      await createFolder({ name: name.value, parent_id: target.value })
    }
    emit('saved')
  } catch (e) {
    // A 409 (name taken, cycle) or 422 is the user's to fix: keep the dialog open.
    error.value = e instanceof Error ? e.message : String(e)
  }
}
</script>

<template>
  <dialog ref="dialog" @close="emit('close')">
    <form @submit.prevent="save">
      <h2>{{ title }}</h2>
      <label>Name <input v-model="name" name="name" required maxlength="255" /></label>
      <template v-if="file">
        <label>Description <textarea v-model="description" maxlength="2000" /></label>
        <label>Tags <input v-model="tags" name="tags" placeholder="comma, separated" /></label>
      </template>
      <label>
        {{ file ? 'Folder' : 'Parent' }}
        <select v-model="target">
          <option :value="null">Home</option>
          <option v-for="f in choices" :key="f.id" :value="f.id">{{ label(f.id) }}</option>
        </select>
      </label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <div class="buttons">
        <button type="submit">Save</button>
        <button type="button" @click="dialog?.close()">Cancel</button>
      </div>
    </form>
  </dialog>
</template>

<style scoped>
form {
  display: grid;
  gap: 0.6rem;
  min-width: 20rem;
}

label {
  display: grid;
  gap: 0.2rem;
}

.buttons {
  display: flex;
  gap: 0.5rem;
}

.error {
  color: #e06c75;
}
</style>
```

- [ ] **Step 4: Run the tests and the type-check**

Run: `cd frontend && npm run test && npm run build`
Expected: all pass, no type error.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/EditDialog.vue frontend/tests/unit/EditDialog.test.ts
git commit -m "feat(spa): native dialog to edit files and create/edit folders"
```

---

### Task 9: `FilesView` — breadcrumb, folders, search, tags, sort, load more

**Files:**
- Modify: `frontend/src/views/FilesView.vue` (rewrite)
- Test: `frontend/tests/unit/FilesView.test.ts` (rewrite), `frontend/tests/regression/pr12-download-blob-url.test.ts` (mount with a router)

**Interfaces:**
- Consumes: the store (Task 7), `EditDialog` (Task 8), `downloadFile`, `Sort` (Task 6), `vue-router`'s `useRoute`/`useRouter`.
- Produces: the `/files` page. URL state: `?folder=<id>` (absent = root), `?q=`, `?tag=`. `sort`/`order` are local state.

- [ ] **Step 1: Write the failing tests**

Replace `frontend/tests/unit/FilesView.test.ts` with:

```ts
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { deleteFile, listFiles, uploadFile, type HomeFile } from '@/api/files'
import { deleteFolder, listFolders } from '@/api/folders'
import { PAGE_SIZE } from '@/stores/files'
import FilesView from '@/views/FilesView.vue'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/files', () => ({
  listFiles: vi.fn(async () => []),
  uploadFile: vi.fn(async () => {}),
  deleteFile: vi.fn(async () => {}),
  downloadFile: vi.fn(async () => new Blob(['hello'])),
  updateFile: vi.fn(async () => ({})),
}))
vi.mock('@/api/folders', () => ({
  listFolders: vi.fn(async () => []),
  deleteFolder: vi.fn(async () => {}),
  createFolder: vi.fn(async () => ({})),
  updateFolder: vi.fn(async () => ({})),
}))

HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
  this.setAttribute('open', '')
}

const A = { id: 'a', name: 'A', parent_id: null }
const B = { id: 'b', name: 'B', parent_id: 'a' }

function aFile(overrides: Partial<HomeFile> = {}): HomeFile {
  return {
    id: '11111111-1111-1111-1111-111111111111',
    name: 'a.txt',
    size: 5,
    content_type: 'text/plain',
    modified: null,
    folder_id: null,
    description: null,
    tags: [],
    ...overrides,
  }
}

async function render(path = '/files') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/files', component: FilesView }],
  })
  await router.push(path)
  const wrapper = mount(FilesView, { global: { plugins: [createPinia(), router] } })
  await flushPromises()
  return { wrapper, router }
}

const button = (wrapper: VueWrapper, text: string) =>
  wrapper.findAll('button').find((b) => b.text() === text)!

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listFiles).mockResolvedValue([])
  vi.mocked(listFolders).mockResolvedValue([])
  vi.stubGlobal('confirm', vi.fn(() => true))
})

afterEach(() => {
  vi.useRealTimers()
})

it('shows an empty state before anything is uploaded', async () => {
  const { wrapper } = await render()

  expect(wrapper.text()).toContain('No files yet.')
  expect(wrapper.find('table').exists()).toBe(false)
})

it('lists what the API returns, with a human-readable size', async () => {
  vi.mocked(listFiles).mockResolvedValue([aFile({ name: 'bail été.txt', size: 2048 })])

  const { wrapper } = await render()

  const cells = wrapper.findAll('tbody td').map((c) => c.text())
  expect(cells[0]).toBe('bail été.txt')
  expect(cells[2]).toBe('2.0 KB')
})

it('browses a folder: breadcrumb, then subfolders before files', async () => {
  vi.mocked(listFolders).mockResolvedValue([A, B])
  vi.mocked(listFiles).mockResolvedValue([aFile({ folder_id: 'a' })])

  const { wrapper } = await render('/files?folder=a')

  expect(listFiles).toHaveBeenCalledWith(expect.objectContaining({ folder_id: 'a', offset: 0 }))
  const crumbs = wrapper.get('nav[aria-label="Breadcrumb"]').text().replace(/\s+/g, ' ')
  expect(crumbs).toBe('Home › A')
  const rows = wrapper.findAll('tbody tr').map((r) => r.find('td').text())
  expect(rows).toEqual(['B/', 'a.txt'])
  expect(wrapper.find('th.location').exists()).toBe(false)
})

it('search mode hides folders and shows where each file lives', async () => {
  vi.mocked(listFolders).mockResolvedValue([A, B])
  vi.mocked(listFiles).mockResolvedValue([aFile({ folder_id: 'b' })])

  const { wrapper } = await render('/files?q=tax')

  expect(listFiles).toHaveBeenCalledWith(expect.objectContaining({ q: 'tax' }))
  expect(wrapper.findAll('tbody tr')).toHaveLength(1)
  expect(wrapper.get('th.location').exists()).toBe(true)
  expect(wrapper.get('td.location').text()).toBe('A › B')
})

it('writes the search box to ?q after a 300 ms pause', async () => {
  const { wrapper, router } = await render()

  vi.useFakeTimers()
  await wrapper.get('input[type="search"]').setValue('  tax ')
  vi.advanceTimersByTime(299)
  expect(router.currentRoute.value.query.q).toBeUndefined()
  vi.advanceTimersByTime(1)
  vi.useRealTimers()
  await flushPromises()

  expect(router.currentRoute.value.query.q).toBe('tax')
})

it('a tag chip filters by that tag, and the active tag can be removed', async () => {
  vi.mocked(listFiles).mockResolvedValue([aFile({ tags: ['tax'] })])
  const { wrapper, router } = await render()

  await button(wrapper, 'tax').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.query.tag).toBe('tax')
  expect(listFiles).toHaveBeenLastCalledWith(expect.objectContaining({ tag: 'tax' }))

  await button(wrapper, '#tax ×').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.query.tag).toBeUndefined()
})

it('sorts by a header, flipping the order on a second click', async () => {
  vi.mocked(listFiles).mockResolvedValue([aFile()])
  const { wrapper } = await render()

  await button(wrapper, 'Name').trigger('click')
  await flushPromises()
  expect(listFiles).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'name', order: 'asc' }))

  await button(wrapper, 'Name').trigger('click')
  await flushPromises()
  expect(listFiles).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'name', order: 'desc' }))
})

it('offers Load more after a full page', async () => {
  const page = Array.from({ length: PAGE_SIZE }, (_, i) => aFile({ id: String(i) }))
  vi.mocked(listFiles).mockResolvedValueOnce(page).mockResolvedValue([])
  const { wrapper } = await render()

  await button(wrapper, 'Load more').trigger('click')
  await flushPromises()

  expect(listFiles).toHaveBeenLastCalledWith(expect.objectContaining({ offset: PAGE_SIZE }))
})

it('uploads every picked file into the current folder and clears the input', async () => {
  vi.mocked(listFolders).mockResolvedValue([A])
  const { wrapper } = await render('/files?folder=a')

  const file = new File(['hello'], 'notes.txt', { type: 'text/plain' })
  const input = wrapper.get('input[type="file"]')
  // jsdom's FileList is read-only, so it is replaced outright.
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  await flushPromises()

  expect(vi.mocked(uploadFile).mock.calls).toEqual([[file, 'a']])
  expect((input.element as HTMLInputElement).value).toBe('')
})

it('asks before deleting a file, and deletes when confirmed', async () => {
  vi.mocked(listFiles).mockResolvedValue([aFile()])
  const { wrapper } = await render()

  await button(wrapper, 'Delete').trigger('click')
  await flushPromises()

  expect(confirm).toHaveBeenCalledWith('Delete a.txt?')
  expect(deleteFile).toHaveBeenCalledWith('11111111-1111-1111-1111-111111111111')
})

it('does not delete a file when the confirmation is dismissed', async () => {
  vi.mocked(listFiles).mockResolvedValue([aFile()])
  vi.stubGlobal('confirm', vi.fn(() => false))
  const { wrapper } = await render()

  await button(wrapper, 'Delete').trigger('click')
  await flushPromises()

  expect(deleteFile).not.toHaveBeenCalled()
})

it('asks before deleting a folder, and skips the delete when dismissed', async () => {
  vi.mocked(listFolders).mockResolvedValue([{ id: 'a', name: 'A', parent_id: null }])
  vi.stubGlobal('confirm', vi.fn(() => false))
  const { wrapper } = await render()

  await button(wrapper, 'Delete').trigger('click')
  await flushPromises()

  expect(confirm).toHaveBeenCalledWith('Delete A?')
  expect(deleteFolder).not.toHaveBeenCalled()
})

it('deletes a subfolder through the store', async () => {
  vi.mocked(listFolders).mockResolvedValue([A])
  const { wrapper } = await render()

  await button(wrapper, 'Delete').trigger('click')
  await flushPromises()

  expect(deleteFolder).toHaveBeenCalledWith('a', false)
})

it('New folder opens the dialog', async () => {
  const { wrapper } = await render()

  await button(wrapper, 'New folder').trigger('click')

  expect(wrapper.get('dialog').text()).toContain('New folder')
})

it('shows the store error in an alert', async () => {
  vi.mocked(listFiles).mockRejectedValue(new Error('GET /files failed with 502'))
  const { wrapper } = await render()

  expect(wrapper.get('[role="alert"]').text()).toBe('GET /files failed with 502')
})
```

In `frontend/tests/regression/pr12-download-blob-url.test.ts`:
- add `import { createMemoryHistory, createRouter } from 'vue-router'`;
- add `vi.mock('@/api/folders', () => ({ listFolders: vi.fn(async () => []), deleteFolder: vi.fn() }))` (the `@/auth` mock is already there from Task 7);
- add `updateFile: vi.fn()` to the `@/api/files` mock;
- replace both `mount(FilesView, { global: { plugins: [createPinia()] } })` calls with `await mountFiles()`, defined as:

```ts
async function mountFiles() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/files', component: FilesView }],
  })
  await router.push('/files')
  return mount(FilesView, { global: { plugins: [createPinia(), router] } })
}
```

- replace both `wrapper.findAll('tbody button')[0]` with `wrapper.findAll('tbody button').find((b) => b.text() === 'Download')!`.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npm run test -- tests/unit/FilesView.test.ts tests/regression/pr12-download-blob-url.test.ts`
Expected: FAIL — no breadcrumb, no search input, no "New folder"/"Load more" buttons; `listFiles` is called without params.

- [ ] **Step 3: Rewrite the view**

Replace `frontend/src/views/FilesView.vue` with:

```vue
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { downloadFile, type HomeFile, type Sort } from '@/api/files'
import type { Folder } from '@/api/folders'
import EditDialog from '@/components/EditDialog.vue'
import { useFilesStore } from '@/stores/files'

const store = useFilesStore()
const route = useRoute()
const router = useRouter()

// Where you are and what you searched live in the URL, so refresh, back and links work.
const param = (value: unknown) => (typeof value === 'string' && value ? value : undefined)
const folderId = computed(() => param(route.query.folder) ?? null)
const q = computed(() => param(route.query.q))
const tag = computed(() => param(route.query.tag))
const searching = computed(() => Boolean(q.value || tag.value))

// How you look at it is a preference, not a location: local state, not the URL.
const sort = ref<Sort>('updated_at')
const order = ref<'asc' | 'desc'>('desc')

function reload() {
  return store.load({
    folder_id: folderId.value ?? undefined,
    q: q.value,
    tag: tag.value,
    sort: sort.value,
    order: order.value,
  })
}
watch([folderId, q, tag], reload, { immediate: true })

function setQuery(patch: Record<string, string | undefined>) {
  return router.push({ query: { ...route.query, ...patch } })
}

const searchText = ref(q.value ?? '')
watch(q, (value) => (searchText.value = value ?? ''))
let debounce: ReturnType<typeof setTimeout> | undefined
function onSearch() {
  clearTimeout(debounce)
  debounce = setTimeout(() => setQuery({ q: searchText.value.trim() || undefined }), 300)
}

function sortBy(key: Sort) {
  if (sort.value === key) {
    order.value = order.value === 'asc' ? 'desc' : 'asc'
  } else {
    sort.value = key
    order.value = key === 'name' ? 'asc' : 'desc'
  }
  reload()
}

const breadcrumb = computed(() => store.pathOf(folderId.value))
const subfolders = computed(() =>
  searching.value ? [] : store.folders.filter((f) => f.parent_id === folderId.value),
)
const location = (file: HomeFile) =>
  store.pathOf(file.folder_id).map((f) => f.name).join(' › ') || 'Home'
const at = (id: string | null) => ({ query: id ? { folder: id } : {} })

// null = closed; {} = a new folder inside the current one.
const editing = ref<{ file?: HomeFile; folder?: Folder } | null>(null)
function onSaved() {
  editing.value = null
  reload()
}

async function onPick(event: Event) {
  const input = event.target as HTMLInputElement
  await store.upload([...(input.files ?? [])], folderId.value)
  input.value = ''
}

// The API wants a bearer token, so a plain <a href> cannot fetch the file:
// fetch it, then hand the blob to a throwaway link.
async function download(file: HomeFile) {
  try {
    const url = URL.createObjectURL(await downloadFile(file.id))
    const link = Object.assign(document.createElement('a'), { href: url, download: file.name })
    link.click()
    setTimeout(() => URL.revokeObjectURL(url))
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
  }
}

function remove(file: HomeFile) {
  if (confirm(`Delete ${file.name}?`)) store.remove(file.id)
}

// Same one-line ask as a file; a non-empty folder gets a second, counted one from the store.
function removeFolder(folder: Folder) {
  if (confirm(`Delete ${folder.name}?`)) store.removeFolder(folder)
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`
}
</script>

<template>
  <section>
    <h1>Files</h1>

    <nav aria-label="Breadcrumb" class="breadcrumb">
      <RouterLink :to="at(null)">Home</RouterLink>
      <template v-for="folder in breadcrumb" :key="folder.id">
        › <RouterLink :to="at(folder.id)">{{ folder.name }}</RouterLink>
      </template>
    </nav>

    <div class="toolbar">
      <input
        v-model="searchText"
        type="search"
        placeholder="Search name, description, tags"
        aria-label="Search"
        @input="onSearch"
      />
      <button v-if="tag" type="button" class="chip" @click="setQuery({ tag: undefined })">
        #{{ tag }} ×
      </button>
      <button type="button" @click="editing = {}">New folder</button>
      <label>
        Upload
        <input type="file" multiple :disabled="store.loading" @change="onPick" />
      </label>
    </div>

    <p v-if="store.error" class="error" role="alert">{{ store.error }}</p>
    <p v-if="store.loading">Working…</p>
    <p v-else-if="!store.files.length && !subfolders.length">
      {{ searching ? 'No matches.' : 'No files yet.' }}
    </p>

    <table v-if="store.files.length || subfolders.length">
      <thead>
        <tr>
          <th><button type="button" class="sort" @click="sortBy('name')">Name</button></th>
          <th>Tags</th>
          <th><button type="button" class="sort" @click="sortBy('size')">Size</button></th>
          <th><button type="button" class="sort" @click="sortBy('updated_at')">Modified</button></th>
          <th v-if="searching" class="location">Location</th>
          <th><span class="visually-hidden">Actions</span></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="folder in subfolders" :key="folder.id">
          <td><RouterLink :to="at(folder.id)">{{ folder.name }}/</RouterLink></td>
          <td></td>
          <td></td>
          <td></td>
          <td class="actions">
            <button type="button" @click="editing = { folder }">Edit</button>
            <button type="button" @click="removeFolder(folder)">Delete</button>
          </td>
        </tr>
        <tr v-for="file in store.files" :key="file.id">
          <td>{{ file.name }}</td>
          <td>
            <button
              v-for="t in file.tags"
              :key="t"
              type="button"
              class="chip"
              @click="setQuery({ tag: t })"
            >{{ t }}</button>
          </td>
          <td>{{ formatSize(file.size) }}</td>
          <td>{{ file.modified ? new Date(file.modified).toLocaleString() : '' }}</td>
          <td v-if="searching" class="location">
            <RouterLink :to="at(file.folder_id)">{{ location(file) }}</RouterLink>
          </td>
          <td class="actions">
            <button type="button" @click="download(file)">Download</button>
            <button type="button" @click="editing = { file }">Edit</button>
            <button type="button" @click="remove(file)">Delete</button>
          </td>
        </tr>
      </tbody>
    </table>

    <button v-if="store.hasMore" type="button" :disabled="store.loading" @click="store.loadMore()">
      Load more
    </button>

    <EditDialog
      v-if="editing"
      :file="editing.file"
      :folder="editing.folder"
      :parent-id="folderId"
      @saved="onSaved"
      @close="editing = null"
    />
  </section>
</template>

<style scoped>
table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 1rem;
}

th,
td {
  padding: 0.4rem;
  text-align: left;
  border-bottom: 1px solid #444;
}

.toolbar,
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: center;
}

.breadcrumb {
  margin-bottom: 0.75rem;
}

.chip {
  border-radius: 1rem;
  padding: 0.1rem 0.6rem;
  margin-right: 0.25rem;
}

.sort {
  background: none;
  border: none;
  font: inherit;
  font-weight: bold;
  cursor: pointer;
  padding: 0;
}

.error {
  color: #e06c75;
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
</style>
```

- [ ] **Step 4: Run the tests and the type-check**

Run: `cd frontend && npm run test && npm run build`
Expected: all pass; `vue-tsc` clean; build succeeds.

- [ ] **Step 5: Check it in the browser**

Run `make dev-backend` and `make dev-frontend` (two terminals), open `http://localhost:5173/files`, and walk the flow: create folder `A`, open it, create `B`, upload a file into `B`, tag it `tax`, search `TAX` from Home (Location shows `A › B`), click the tag chip, rename `A` into a clash (the dialog shows the 409 and stays open), delete `A` (confirm names 1 folder / 1 file). Use the `run` skill if one is set up for this project.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/FilesView.vue frontend/tests/unit/FilesView.test.ts \
        frontend/tests/regression/pr12-download-blob-url.test.ts
git commit -m "feat(spa): folders, breadcrumb, search, tags, sort and load more"
```

---

### Task 10: The five Phase 2 acceptance scenarios

**Files:**
- Create: `backend/tests/integration/test_phase2_acceptance.py`

**Interfaces:**
- Consumes: every backend route; the `db` and `minio_bucket` fixtures of `backend/tests/integration/conftest.py`.
- Produces: one test per Gherkin scenario in `docs/files-feature.md` §12 Phase 2, against real PostgreSQL and real MinIO.

- [ ] **Step 1: Write the acceptance tests**

Create `backend/tests/integration/test_phase2_acceptance.py`:

```python
"""Acceptance — the five Phase 2 scenarios of docs/files-feature.md §12, one
test each, through the real routes, a real PostgreSQL and a real MinIO."""

import pytest
from fastapi.testclient import TestClient

from app.api.routes import files
from app.main import app
from tests.conftest import token

client = TestClient(app)


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def folder(name, parent_id=None, sub="user-1"):
    response = client.post(
        "/api/folders", headers=auth(sub), json={"name": name, "parent_id": parent_id}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload(name, folder_id=None, sub="user-1"):
    response = client.post(
        "/api/files",
        headers=auth(sub),
        data={"folder_id": folder_id} if folder_id else {},
        files={"file": (name, b"hello", "text/plain")},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_renaming_a_folder_reads_and_writes_no_object(db, minio_bucket, monkeypatch):
    # Given a folder "Invoices" containing a file
    invoices = folder("Invoices")
    upload("march.pdf", invoices)
    monkeypatch.setattr(files, "minio_client", lambda: pytest.fail("the rename touched MinIO"))

    # When the user renames the folder to "Bills"
    response = client.patch(f"/api/folders/{invoices}", headers=auth(), json={"name": "Bills"})

    # Then the response is 200 and no MinIO object has been read or written
    assert response.status_code == 200
    assert response.json()["name"] == "Bills"


def test_moving_a_folder_into_its_child_is_refused(db, minio_bucket):
    # Given a folder tree A > B
    a = folder("A")
    b = folder("B", parent_id=a)
    before = client.get("/api/folders", headers=auth()).json()

    # When the user moves A into B
    response = client.patch(f"/api/folders/{a}", headers=auth(), json={"parent_id": b})

    # Then the response is 409 and the tree is unchanged
    assert response.status_code == 409
    assert client.get("/api/folders", headers=auth()).json() == before


def test_a_second_root_folder_named_work_is_refused(db, minio_bucket):
    # Given a root folder named "work" exists
    folder("work")

    # When the user creates another root folder named "work"
    response = client.post("/api/folders", headers=auth(), json={"name": "work"})

    # Then the response is 409
    assert response.status_code == 409


def test_search_finds_a_tag_and_a_description_and_no_one_elses_file(db, minio_bucket):
    # Given a file tagged "tax" and another described "tax return"
    tagged = upload("a.txt")
    described = upload("b.txt")
    upload("c.txt")
    upload("tax.txt", sub="user-2")
    client.patch(f"/api/files/{tagged}", headers=auth(), json={"tags": ["tax"]})
    client.patch(f"/api/files/{described}", headers=auth(), json={"description": "tax return"})

    # When the user searches "TAX"
    found = client.get("/api/files", headers=auth(), params={"q": "TAX"}).json()

    # Then both are returned, and no other user's files are
    assert sorted(f["id"] for f in found) == sorted([tagged, described])


def test_deleting_a_non_empty_folder_names_the_child_count(db, minio_bucket):
    # Given a folder containing two files
    box = folder("Box")
    upload("one.txt", box)
    upload("two.txt", box)

    # When the user deletes it without ?recursive=true
    response = client.delete(f"/api/folders/{box}", headers=auth())

    # Then the response is 409 naming the child count
    assert response.status_code == 409
    assert (response.json()["folders"], response.json()["files"]) == (0, 2)
```

- [ ] **Step 2: Run them**

Run: `make services-test-up && cd backend && .venv/bin/python -m pytest -m integration tests/integration/test_phase2_acceptance.py -v`
Expected: 5 passed. (They are written after the code, so they pass first time; to trust them, temporarily comment out the `is_cycle` check in `update_folder`, see `test_moving_a_folder_into_its_child_is_refused` FAIL, and restore it with `git checkout backend/app/api/routes/folders.py`.)

- [ ] **Step 3: Full verification — everything CI runs**

Run: `make verify` (or, raw: `cd backend && .venv/bin/python -m ruff format --check . && .venv/bin/python -m ruff check . && .venv/bin/python -m pytest -m unit && .venv/bin/python -m pytest -m regression && .venv/bin/python -m pytest -m integration`, then `make coverage` and `cd frontend && npm run test && npm run build`)
Expected: all green; the backend coverage gate (≥ 80) holds for both `make coverage-backend` and the CI-style `-m "unit or regression" --cov-config=.coveragerc.ci` run.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/integration/test_phase2_acceptance.py
git commit -m "test(files): Phase 2 acceptance scenarios against real services"
```

---

## Out of scope for this plan

- Trash view, undelete, version history (Phase 3); drag & drop, progress, preview, quota indicator (Phase 4).
- Folder tree sidebar; multi-select move or delete; `pg_trgm` / full-text search (the `ponytail:` note on the search query names the upgrade path).
