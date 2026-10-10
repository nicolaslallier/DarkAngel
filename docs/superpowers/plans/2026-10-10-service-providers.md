# Service Providers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A household of DarkAngel users can track its service providers (Hydro, Bell...): provider and service records, invoices read from uploaded PDFs by Ollama and validated by a human, upcoming due dates, and a cost history with overspend alerts.

**Architecture:** A `household_id` column on every new table plus a FastAPI dependency (`Reader` / `Writer` / `Owner`) that turns the caller's token into a household and a role replaces the per-`sub` filter for these resources. Invoice PDFs are ordinary rows of the existing `files` table (owned by the uploader); an invoice references the file and streams it through its own household-scoped endpoint. PDF reading runs in a `BackgroundTask` modelled on `app/summary.py` and ends in a `to_validate` queue. Alerts, "paid" state and upcoming lists are computed at read time from pure functions in `app/costs.py`.

**Tech Stack:** FastAPI (sync handlers), SQLAlchemy 2.0, Alembic, PostgreSQL 16, pytest, pypdf (already installed), Ollama over HTTP; Vue 3 `<script setup lang="ts">`, Pinia, vue-router, vitest + @vue/test-utils.

**Spec:** `docs/superpowers/specs/2026-10-10-service-providers-design.md` (source of truth). Read `docs/testing.md` before adding any test. Corrections to the spec found while planning are listed in "Spec deltas" below.

## Spec deltas (this plan wins where it differs)

1. **`services.name` is added** (e.g. "Internet", "Mobile"). The spec lists `category` but a household needs a label for two services of the same category.
2. **`GET /api/invoices/{id}/pdf` is added.** The spec keeps the PDF in the uploader's Files, which other members cannot read through `/api/files`; the preview in the review queue needs a household-scoped stream.
3. **Restart sweep resets `queued` and `extracting`** to `failed` (the spec says only `extracting`): a queued background task is lost on restart just like a running one.
4. **No Ollama configured** (`DARKANGEL_OLLAMA_URL` empty): an uploaded invoice goes straight to `to_validate` with an empty form instead of `queued`.
5. **Manual entry route** `POST /api/invoices/manual` creates a validated invoice with no PDF (the spec's decision table promises manual entry).

## Global Constraints

- **No new dependency** on either side. The cost chart is inline SVG.
- The backend virtualenv is `backend/.venv`. Call tools as `.venv/bin/python -m <tool>` from `backend/`.
- ruff, line length **100**, rules `["E", "F", "I", "UP", "B"]`. B008 flags `Form(...)` / `File(...)` in a default: use `Annotated[..., Form()] = None`. CI runs `make format-check`: the code blocks below are not hand-wrapped, so every lint step runs `ruff format .` first (`make format` does the same plus import fixes).
- Tests live in `backend/tests/{unit,integration,regression}/` and `frontend/tests/{unit,regression}/`; **the directory decides the marker**, no decorators. Unit tests touch no service. Integration tests never request the `repo`, `store` or `ledger` fixtures.
- Response shapes are pydantic models declared next to their route. Route handlers are plain `def`.
- Every new route takes `Reader`, `Writer` or `Owner` (from `app/core/household.py`), except `POST /api/household` and `POST /api/household/join`, which take `Claims`. Every repository query filters on `household_id` and takes it as the first argument after `self`. A missing or foreign id is a `404`, never `403`.
- Roles: `owner` (composition of the household, invitations, deletion), `member` (providers, services, invoices), `viewer` (reads). Reads are open to all three; writes need `member` or `owner`.
- No household yet → `409` with detail `no_household`.
- Money is `NUMERIC(12,2)` and Python `Decimal`; pydantic serializes `Decimal` as a JSON **string**, so frontend money fields are `string`. Currency is CAD, with no currency column.
- Invitation tokens: `secrets.token_urlsafe(32)`; only the SHA-256 hex digest is stored; links last 7 days (`INVITATION_TTL`) and are single use.
- Invoice statuses are exactly `queued`, `extracting`, `to_validate`, `validated`, `failed`. `total` and `due_on` are required to reach `validated`.
- Alert rule: reference = mean of the previous 6 validated totals of the service when there are at least 3, else the service's `expected_monthly_cost`; flagged when `total > reference * (1 + threshold_pct / 100)` (strict). `alert_threshold_pct` defaults to 20.
- The "paid" state of an invoice is `paid_at is not None`, or the service has `auto_pay` and `due_on <= today`; it is computed at read time, never stored for auto-pay.
- Frontend: `<script setup lang="ts">`, imports via `@/` (same-directory `./client` imports inside `src/api/` stay), `erasableSyntaxOnly` is on (no TypeScript parameter properties), `tests/**/*.ts` is type-checked by `npm run build`. `src/api/client.ts` stays the only place `fetch` is called.
- The API contract is pinned by `backend/tests/regression/openapi.snapshot.json`: every task that changes a route runs `make snapshot` and commits the diff with it.
- Git: work stays on branch `claude/service-providers-feature-4fcaf1`. **Commit steps run only if the user has asked for commits in this session** (CLAUDE.md: never commit as a side effect; never push to `main`). Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

- **Redeeming an invitation while already in a household** must fail with `409` *without burning the link*: the rightful invitee can still use it afterwards. Pinned in Task 1 (`test_redeeming_as_an_existing_member_keeps_the_link_usable`).
- **A `viewer` calling any write route** gets `403` on every family (household, providers/services, invoices), not just the one that was tested first. Pinned in Tasks 2, 3 and 5.
- **A member of another household** reading, editing or deleting a provider, service or invoice gets `404`, and cannot stream another household's PDF. Pinned in Task 3 (`test_r2_cross_household_isolation.py`) and Task 5.
- **Garbage from the model** (not JSON, wrong types, `"1 240,50 $"` for an amount, a scanned PDF with no text) must end in `failed` or in cleaned fields, and the invoice must still be validatable by hand. Pinned in Task 6.
- **A duplicate invoice number on one service**, and a **bill exactly at the alert threshold** (not flagged), and a service with **fewer than 3 invoices** (compared with the expected cost). Pinned in Tasks 4 and 5.

## File Structure

**Created (backend)**

| File | Responsibility |
|---|---|
| `backend/app/models/households.py` | `Household`, `HouseholdMember`, `HouseholdInvitation` |
| `backend/app/models/providers.py` | `Provider`, `Service`, `Invoice`, `InvoiceTax` |
| `backend/migrations/versions/0003_households.py` | households tables |
| `backend/migrations/versions/0004_providers_services_invoices.py` | provider, service, invoice tables |
| `backend/app/repositories/households.py` | `HouseholdRepository`, `HouseholdRepo`, `AlreadyMember`, `InvitationInvalid` |
| `backend/app/repositories/providers.py` | `ProviderRepository` (providers + services), `ProviderRepo` |
| `backend/app/repositories/invoices.py` | `InvoiceRepository`, `InvoiceRepo` |
| `backend/app/core/household.py` | `HouseholdContext`, `Reader`, `Writer`, `Owner` |
| `backend/app/core/clock.py` | `today()` dependency (overridable in tests) |
| `backend/app/costs.py` | pure rules: paid state, alert reference, flags, monthly series |
| `backend/app/invoice_extraction.py` | PDF text to fields: prompt, cleaning, matching, background job |
| `backend/app/api/routes/household.py` | `/api/household...` |
| `backend/app/api/routes/providers.py` | `/api/providers...`, `/api/services...` |
| `backend/app/api/routes/invoices.py` | `/api/invoices...` |
| `backend/app/api/routes/costs.py` | `/api/upcoming`, `/api/services/{id}/costs`, `/api/costs/monthly` |

**Modified (backend):** `app/models/__init__.py`, `app/api/router.py`, `app/main.py` (restart sweep), `app/summary.py` (`as_json`), `tests/conftest.py` (fakes + fixtures), `tests/integration/conftest.py` (TRUNCATE list), `tests/regression/openapi.snapshot.json`.

**Created (frontend):** `src/format.ts`, `src/api/{household,providers,invoices,costs}.ts`, `src/stores/{household,providers,invoices}.ts`, `src/components/{CostChart,ServiceForm}.vue`, `src/views/{HouseholdView,JoinView,ProvidersView,ProviderView,ServiceView,ReviewView}.vue`. **Modified:** `src/api/client.ts` (`apiSend`), `src/router/index.ts`, `src/App.vue`.

---

### Task 1: Households data layer

**Files:**
- Create: `backend/app/models/households.py`, `backend/migrations/versions/0003_households.py`, `backend/app/repositories/households.py`
- Modify: `backend/app/models/__init__.py`, `backend/tests/integration/conftest.py:~165` (TRUNCATE list)
- Test: `backend/tests/integration/test_household_repository.py`

**Interfaces:**
- Produces (`app/repositories/households.py`):
  - `INVITATION_TTL = timedelta(days=7)`; `class AlreadyMember(Exception)`; `class InvitationInvalid(Exception)`
  - `HouseholdRepository(db)`: `membership(sub) -> HouseholdMember | None`; `get(household_id) -> Household | None`; `members(household_id) -> Sequence[HouseholdMember]`; `create(sub, name) -> Household` (raises `AlreadyMember`); `set_role(household_id, sub, role) -> HouseholdMember | None`; `remove(household_id, sub) -> bool`; `delete(household_id) -> None`; `invite(household_id, role, created_by, ttl=INVITATION_TTL) -> str` (the raw token); `redeem(token, sub) -> HouseholdMember` (raises `InvitationInvalid`, `AlreadyMember`)
  - `household_repository(db: Db) -> HouseholdRepository`; `HouseholdRepo = Annotated[HouseholdRepository, Depends(household_repository)]`

- [ ] **Step 1: Write the failing integration tests**

Create `backend/tests/integration/test_household_repository.py`:

```python
"""Integration — HouseholdRepository against real PostgreSQL."""

from datetime import timedelta

import pytest
from sqlalchemy import text

from app.repositories.households import AlreadyMember, HouseholdRepository, InvitationInvalid


@pytest.fixture
def households(db):
    return HouseholdRepository(db)


def test_create_makes_the_creator_the_owner(households):
    household = households.create("alice", "Maison")

    member = households.membership("alice")

    assert (member.household_id, member.role) == (household.id, "owner")


def test_a_person_belongs_to_one_household(households):
    households.create("alice", "Maison")

    with pytest.raises(AlreadyMember):
        households.create("alice", "Chalet")


def test_an_invitation_is_single_use(households):
    household = households.create("alice", "Maison")
    token = households.invite(household.id, "member", "alice")

    member = households.redeem(token, "bob")

    assert (member.household_id, member.role) == (household.id, "member")
    with pytest.raises(InvitationInvalid):
        households.redeem(token, "carol")


def test_an_expired_invitation_is_refused(households):
    household = households.create("alice", "Maison")
    token = households.invite(household.id, "member", "alice", ttl=timedelta(seconds=-1))

    with pytest.raises(InvitationInvalid):
        households.redeem(token, "bob")


def test_an_unknown_token_is_refused(households):
    with pytest.raises(InvitationInvalid):
        households.redeem("nope", "bob")


def test_redeeming_as_an_existing_member_keeps_the_link_usable(households):
    household = households.create("alice", "Maison")
    households.create("dave", "Chalet")
    token = households.invite(household.id, "viewer", "alice")

    with pytest.raises(AlreadyMember):
        households.redeem(token, "dave")

    assert households.redeem(token, "bob").role == "viewer"


def test_the_database_keeps_only_the_hash(households, db):
    household = households.create("alice", "Maison")
    token = households.invite(household.id, "member", "alice")

    stored = db.execute(text("SELECT token_hash FROM household_invitations")).scalars().all()

    assert len(stored) == 1 and len(stored[0]) == 64 and token not in stored[0]


def test_roles_and_removal_are_scoped_to_the_household(households):
    mine = households.create("alice", "Maison")
    theirs = households.create("dave", "Chalet")
    households.redeem(households.invite(mine.id, "member", "alice"), "bob")

    assert households.set_role(theirs.id, "bob", "viewer") is None
    assert households.remove(theirs.id, "bob") is False
    assert households.set_role(mine.id, "bob", "viewer").role == "viewer"
    assert households.remove(mine.id, "bob") is True
    assert households.membership("bob") is None


def test_deleting_a_household_removes_its_members_and_invitations(households, db):
    household = households.create("alice", "Maison")
    households.invite(household.id, "member", "alice")

    households.delete(household.id)

    assert households.membership("alice") is None
    assert db.execute(text("SELECT count(*) FROM household_invitations")).scalar() == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/test_household_repository.py -v`
Expected: collection ERROR `ModuleNotFoundError: app.repositories.households` (or a skip if Postgres is down: run `make services-test-up` first).

- [ ] **Step 3: Write the models**

Create `backend/app/models/households.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.files import Base


class Household(Base):
    __tablename__ = "households"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HouseholdMember(Base):
    __tablename__ = "household_members"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'member', 'viewer')", name="ck_household_members_role"),
    )

    # The primary key is the person: one household each (spec §4).
    sub: Mapped[str] = mapped_column(Text, primary_key=True)
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("households.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HouseholdInvitation(Base):
    __tablename__ = "household_invitations"
    __table_args__ = (
        CheckConstraint("role IN ('member', 'viewer')", name="ck_household_invitations_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    # SHA-256 hex of the token in the link; the token itself is never stored.
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

Replace `backend/app/models/__init__.py` with:

```python
from app.models.files import AuditLog, Base, File, FileVersion, Folder
from app.models.households import Household, HouseholdInvitation, HouseholdMember

__all__ = [
    "AuditLog",
    "Base",
    "File",
    "FileVersion",
    "Folder",
    "Household",
    "HouseholdInvitation",
    "HouseholdMember",
]
```

(Task 3 adds the provider models to this list.) `migrations/env.py` imports `Base` from `app.models.files`; the new modules register their tables on the same `Base` only once imported, and the migration is hand-written, so `env.py` needs no change.

- [ ] **Step 4: Write the migration**

Create `backend/migrations/versions/0003_households.py`:

```python
"""households, household_members, household_invitations.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "households",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "household_members",
        sa.Column("sub", sa.Text(), primary_key=True),
        sa.Column(
            "household_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("households.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("role IN ('owner', 'member', 'viewer')", name="ck_household_members_role"),
    )
    op.create_index("ix_household_members_household_id", "household_members", ["household_id"])
    op.create_table(
        "household_invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "household_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("households.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("role IN ('member', 'viewer')", name="ck_household_invitations_role"),
    )


def downgrade() -> None:
    op.drop_table("household_invitations")
    op.drop_index("ix_household_members_household_id", table_name="household_members")
    op.drop_table("household_members")
    op.drop_table("households")
```

- [ ] **Step 5: Write the repository**

Create `backend/app/repositories/households.py`:

```python
from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import Db
from app.models.households import Household, HouseholdInvitation, HouseholdMember

INVITATION_TTL = timedelta(days=7)


class AlreadyMember(Exception):
    """The person already belongs to a household (`household_members.sub` is
    the primary key, so the database decides, not a SELECT first)."""


class InvitationInvalid(Exception):
    """Unknown token, expired, or already used: indistinguishable on purpose."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class HouseholdRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def membership(self, sub: str) -> HouseholdMember | None:
        return self.db.get(HouseholdMember, sub)

    def get(self, household_id: uuid.UUID) -> Household | None:
        return self.db.get(Household, household_id)

    def members(self, household_id: uuid.UUID) -> Sequence[HouseholdMember]:
        statement = (
            select(HouseholdMember)
            .where(HouseholdMember.household_id == household_id)
            .order_by(HouseholdMember.joined_at, HouseholdMember.sub)
        )
        return self.db.scalars(statement).all()

    def create(self, sub: str, name: str) -> Household:
        household = Household(id=uuid.uuid4(), name=name)
        self.db.add(household)
        self.db.add(HouseholdMember(sub=sub, household_id=household.id, role="owner"))
        try:
            self.db.commit()
        except IntegrityError as e:
            self.db.rollback()
            raise AlreadyMember from e
        return household

    def set_role(
        self, household_id: uuid.UUID, sub: str, role: str
    ) -> HouseholdMember | None:
        member = self.db.get(HouseholdMember, sub)
        if member is None or member.household_id != household_id:
            return None
        member.role = role
        self.db.commit()
        return member

    def remove(self, household_id: uuid.UUID, sub: str) -> bool:
        member = self.db.get(HouseholdMember, sub)
        if member is None or member.household_id != household_id:
            return False
        self.db.delete(member)
        self.db.commit()
        return True

    def delete(self, household_id: uuid.UUID) -> None:
        # Members, invitations and every ledger table go with it (ON DELETE CASCADE).
        self.db.execute(delete(Household).where(Household.id == household_id))
        self.db.commit()

    def invite(
        self,
        household_id: uuid.UUID,
        role: str,
        created_by: str,
        ttl: timedelta = INVITATION_TTL,
    ) -> str:
        token = secrets.token_urlsafe(32)
        self.db.add(
            HouseholdInvitation(
                id=uuid.uuid4(),
                household_id=household_id,
                role=role,
                token_hash=_hash(token),
                expires_at=datetime.now(UTC) + ttl,
                created_by=created_by,
            )
        )
        self.db.commit()
        return token

    def redeem(self, token: str, sub: str) -> HouseholdMember:
        """Claim the link and join, in one transaction: if the person is already
        in a household the claim is rolled back and the link stays usable."""
        claimed = self.db.execute(
            update(HouseholdInvitation)
            .where(
                HouseholdInvitation.token_hash == _hash(token),
                HouseholdInvitation.used_at.is_(None),
                HouseholdInvitation.expires_at > func.now(),
            )
            .values(used_at=func.now())
            .returning(HouseholdInvitation.household_id, HouseholdInvitation.role)
        ).one_or_none()
        if claimed is None:
            self.db.rollback()
            raise InvitationInvalid
        member = HouseholdMember(sub=sub, household_id=claimed.household_id, role=claimed.role)
        self.db.add(member)
        try:
            self.db.commit()
        except IntegrityError as e:
            self.db.rollback()
            raise AlreadyMember from e
        return member


def household_repository(db: Db) -> HouseholdRepository:
    return HouseholdRepository(db)


HouseholdRepo = Annotated[HouseholdRepository, Depends(household_repository)]
```

- [ ] **Step 6: Make the TRUNCATE cover the new tables**

In `backend/tests/integration/conftest.py`, in the `db` fixture change the statement to:

```python
        session.execute(
            sa_text(
                "TRUNCATE audit_log, file_versions, files, folders, households "
                "RESTART IDENTITY CASCADE"
            )
        )
```

(`CASCADE` reaches every table with a foreign key to `households` or `files`, including the provider and invoice tables added in Task 3.)

- [ ] **Step 7: Run the tests to verify they pass**

Run: `make services-test-up && cd backend && .venv/bin/python -m pytest tests/integration/test_household_repository.py tests/integration/test_schema.py -v && .venv/bin/python -m ruff format . && .venv/bin/python -m ruff check .`
Expected: all PASS, ruff clean.

- [ ] **Step 8: Commit (only if the user has asked for commits)**

```bash
git add backend/app/models backend/migrations/versions/0003_households.py backend/app/repositories/households.py backend/tests/integration
git commit -m "feat: households data layer"
```

---

### Task 2: Household context and routes

**Files:**
- Create: `backend/app/core/household.py`, `backend/app/api/routes/household.py`
- Modify: `backend/app/api/router.py`, `backend/tests/conftest.py`, `backend/tests/regression/openapi.snapshot.json`
- Test: `backend/tests/unit/test_household.py`

**Interfaces:**
- Consumes: `HouseholdRepo`, `AlreadyMember`, `InvitationInvalid`, `INVITATION_TTL` (Task 1); `Claims` (`app/core/auth.py`).
- Produces:
  - `app/core/household.py`: `@dataclass(frozen=True) HouseholdContext(sub: str, household_id: uuid.UUID, role: str)`; `household_context(claims, repo) -> HouseholdContext` (409 `no_household`); aliases `Reader`, `Writer` (403 for `viewer`), `Owner` (403 unless `owner`).
  - Routes under `/api/household`: `GET ""`, `POST ""`, `DELETE ""`, `POST /invitations`, `POST /join`, `PATCH /members/{sub}`, `DELETE /members/{sub}`, `POST /leave`.
  - Test fakes in `tests/conftest.py`: `FakeHouseholdRepository` (`add_member(household_id, sub, role)`), fixtures `ledger` (a `SimpleNamespace` with `.households`, `.providers`, `.invoices`) and `home` (user-1 is `owner` of a household; `home.id` is its id, `home.ledger` the ledger), `clock` (fixes today to 2026-10-10 via the `today` dependency from Task 5's `app/core/clock.py`, created here).

- [ ] **Step 1: Create the clock dependency**

Create `backend/app/core/clock.py`:

```python
from datetime import date
from typing import Annotated

from fastapi import Depends


def today() -> date:
    """A dependency so tests can pin the date instead of patching `datetime`."""
    return date.today()


Today = Annotated[date, Depends(today)]
```

- [ ] **Step 2: Add the fakes and fixtures to `tests/conftest.py`**

Add these imports at the top (merge with the existing import block, keep ruff's isort order):

```python
import secrets
from datetime import date

from app.core.clock import today
from app.models.households import Household, HouseholdMember
from app.repositories.households import (
    INVITATION_TTL,
    AlreadyMember,
    InvitationInvalid,
    household_repository,
)
```

Append at the end of the file:

```python
class FakeHouseholdRepository:
    """HouseholdRepository over dicts. `add_member` is test-only sugar."""

    def __init__(self):
        self.households: dict[uuid.UUID, Household] = {}
        self._members: dict[str, HouseholdMember] = {}
        self.tokens: dict[str, dict] = {}

    def membership(self, sub):
        return self._members.get(sub)

    def get(self, household_id):
        return self.households.get(household_id)

    def members(self, household_id):
        return [m for m in self._members.values() if m.household_id == household_id]

    def add_member(self, household_id, sub, role):
        member = HouseholdMember(sub=sub, household_id=household_id, role=role)
        self._members[sub] = member
        return member

    def create(self, sub, name):
        if sub in self._members:
            raise AlreadyMember
        household = Household(id=uuid.uuid4(), name=name)
        self.households[household.id] = household
        self.add_member(household.id, sub, "owner")
        return household

    def set_role(self, household_id, sub, role):
        member = self._members.get(sub)
        if member is None or member.household_id != household_id:
            return None
        member.role = role
        return member

    def remove(self, household_id, sub):
        member = self._members.get(sub)
        if member is None or member.household_id != household_id:
            return False
        del self._members[sub]
        return True

    def delete(self, household_id):
        self.households.pop(household_id, None)
        for sub in [s for s, m in self._members.items() if m.household_id == household_id]:
            del self._members[sub]

    def invite(self, household_id, role, created_by, ttl=INVITATION_TTL):
        token = secrets.token_urlsafe(8)
        self.tokens[token] = {
            "household_id": household_id,
            "role": role,
            "expires_at": datetime.now(UTC) + ttl,
            "used": False,
        }
        return token

    def redeem(self, token, sub):
        invitation = self.tokens.get(token)
        if (
            invitation is None
            or invitation["used"]
            or invitation["expires_at"] <= datetime.now(UTC)
        ):
            raise InvitationInvalid
        if sub in self._members:
            raise AlreadyMember
        invitation["used"] = True
        return self.add_member(invitation["household_id"], sub, invitation["role"])


@pytest.fixture
def ledger():
    """Swap the household repository (and, from Tasks 3 and 5, the provider and
    invoice repositories) for in-memory fakes. Explicit, never autouse."""
    fake = SimpleNamespace(households=FakeHouseholdRepository())
    app.dependency_overrides[household_repository] = lambda: fake.households
    yield fake
    app.dependency_overrides.pop(household_repository, None)


@pytest.fixture
def home(ledger):
    """user-1 owns a household called Maison."""
    household = ledger.households.create("user-1", "Maison")
    return SimpleNamespace(id=household.id, ledger=ledger)


@pytest.fixture
def clock():
    """Today is 2026-10-10 for the length of the test."""
    app.dependency_overrides[today] = lambda: date(2026, 10, 10)
    yield date(2026, 10, 10)
    app.dependency_overrides.pop(today, None)
```

- [ ] **Step 3: Write the failing unit tests**

Create `backend/tests/unit/test_household.py`:

```python
"""Unit — the household routes, against FakeHouseholdRepository."""

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def invite(role="member", sub="user-1"):
    return client.post("/api/household/invitations", headers=auth(sub), json={"role": role})


def test_a_person_without_a_household_gets_409(ledger):
    response = client.get("/api/household", headers=auth("newcomer"))

    assert response.status_code == 409
    assert response.json()["detail"] == "no_household"


def test_create_makes_the_caller_the_owner(ledger):
    response = client.post("/api/household", headers=auth("alice"), json={"name": "Maison"})

    assert response.status_code == 201
    body = response.json()
    assert (body["name"], body["role"]) == ("Maison", "owner")
    assert body["members"] == [{"sub": "alice", "role": "owner"}]


def test_a_second_create_is_a_409(home):
    response = client.post("/api/household", headers=auth(), json={"name": "Chalet"})

    assert response.status_code == 409


def test_get_lists_the_members_and_the_callers_role(home):
    home.ledger.households.add_member(home.id, "user-2", "viewer")

    body = client.get("/api/household", headers=auth("user-2")).json()

    assert body["role"] == "viewer"
    assert {m["sub"] for m in body["members"]} == {"user-1", "user-2"}


def test_an_invitation_lets_someone_join_with_the_offered_role(home):
    link = invite("viewer").json()
    assert link["expires_in_days"] == 7

    joined = client.post("/api/household/join", headers=auth("bob"), json={"token": link["token"]})

    assert joined.status_code == 200
    assert joined.json()["role"] == "viewer"


def test_a_token_works_once(home):
    link = invite().json()
    client.post("/api/household/join", headers=auth("bob"), json={"token": link["token"]})

    again = client.post("/api/household/join", headers=auth("carol"), json={"token": link["token"]})

    assert again.status_code == 404


def test_joining_while_already_in_a_household_is_409_and_keeps_the_link(home):
    home.ledger.households.create("dave", "Chalet")
    link = invite().json()

    refused = client.post("/api/household/join", headers=auth("dave"), json={"token": link["token"]})
    ok = client.post("/api/household/join", headers=auth("bob"), json={"token": link["token"]})

    assert (refused.status_code, ok.status_code) == (409, 200)


def test_only_the_owner_manages_the_household(home):
    households = home.ledger.households
    households.add_member(home.id, "member", "member")
    households.add_member(home.id, "viewer", "viewer")

    for sub in ("member", "viewer"):
        assert invite(sub=sub).status_code == 403
        assert client.delete("/api/household", headers=auth(sub)).status_code == 403
        assert (
            client.patch(
                "/api/household/members/viewer", headers=auth(sub), json={"role": "member"}
            ).status_code
            == 403
        )
        assert client.delete("/api/household/members/viewer", headers=auth(sub)).status_code == 403


def test_the_owner_changes_a_role_and_removes_a_member(home):
    home.ledger.households.add_member(home.id, "bob", "member")

    changed = client.patch("/api/household/members/bob", headers=auth(), json={"role": "viewer"})
    removed = client.delete("/api/household/members/bob", headers=auth())

    assert (changed.status_code, changed.json()) == (200, {"sub": "bob", "role": "viewer"})
    assert removed.status_code == 204
    assert client.get("/api/household", headers=auth("bob")).status_code == 409


def test_the_owner_cannot_be_demoted_or_removed(home):
    assert (
        client.patch("/api/household/members/user-1", headers=auth(), json={"role": "viewer"}).status_code
        == 409
    )
    assert client.delete("/api/household/members/user-1", headers=auth()).status_code == 409


def test_an_unknown_member_is_a_404(home):
    assert client.delete("/api/household/members/ghost", headers=auth()).status_code == 404
    assert (
        client.patch("/api/household/members/ghost", headers=auth(), json={"role": "member"}).status_code
        == 404
    )


def test_a_member_can_leave_but_the_owner_cannot(home):
    home.ledger.households.add_member(home.id, "bob", "member")

    left = client.post("/api/household/leave", headers=auth("bob"))
    stuck = client.post("/api/household/leave", headers=auth())

    assert (left.status_code, stuck.status_code) == (204, 409)


def test_the_owner_deletes_the_household(home):
    home.ledger.households.add_member(home.id, "bob", "member")

    assert client.delete("/api/household", headers=auth()).status_code == 204
    assert client.get("/api/household", headers=auth("bob")).status_code == 409
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_household.py -v`
Expected: FAIL (404 on every route; routes don't exist).

- [ ] **Step 5: Write the context dependency**

Create `backend/app/core/household.py`:

```python
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.core.auth import Claims
from app.repositories.households import HouseholdRepo


@dataclass(frozen=True)
class HouseholdContext:
    sub: str
    household_id: uuid.UUID
    role: str


def household_context(claims: Claims, repo: HouseholdRepo) -> HouseholdContext:
    """The caller's household and role, or 409 `no_household` so the SPA can
    offer to create or join one."""
    member = repo.membership(claims["sub"])
    if member is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no_household")
    return HouseholdContext(member.sub, member.household_id, member.role)


Reader = Annotated[HouseholdContext, Depends(household_context)]


def _writer(ctx: Reader) -> HouseholdContext:
    if ctx.role == "viewer":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Read-only access")
    return ctx


def _owner(ctx: Reader) -> HouseholdContext:
    if ctx.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can do this")
    return ctx


Writer = Annotated[HouseholdContext, Depends(_writer)]
Owner = Annotated[HouseholdContext, Depends(_owner)]
```

- [ ] **Step 6: Write the routes**

Create `backend/app/api/routes/household.py`:

```python
from typing import Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.core.auth import Claims
from app.core.household import Owner, Reader
from app.repositories.households import (
    INVITATION_TTL,
    AlreadyMember,
    HouseholdRepo,
    InvitationInvalid,
)

router = APIRouter(prefix="/household", tags=["household"])


class MemberInfo(BaseModel):
    sub: str
    role: str


class HouseholdInfo(BaseModel):
    id: str
    name: str
    role: str
    members: list[MemberInfo]


class HouseholdCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class InvitationCreate(BaseModel):
    role: Literal["member", "viewer"] = "member"


class InvitationInfo(BaseModel):
    token: str
    expires_in_days: int


class JoinBody(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class RolePatch(BaseModel):
    role: Literal["member", "viewer"]


def _info(repo: HouseholdRepo, household_id, role: str) -> HouseholdInfo:
    household = repo.get(household_id)
    return HouseholdInfo(
        id=str(household.id),
        name=household.name,
        role=role,
        members=[MemberInfo(sub=m.sub, role=m.role) for m in repo.members(household_id)],
    )


@router.get("", response_model=HouseholdInfo)
def get_household(ctx: Reader, repo: HouseholdRepo) -> HouseholdInfo:
    return _info(repo, ctx.household_id, ctx.role)


@router.post("", response_model=HouseholdInfo, status_code=status.HTTP_201_CREATED)
def create_household(claims: Claims, repo: HouseholdRepo, body: HouseholdCreate) -> HouseholdInfo:
    try:
        household = repo.create(claims["sub"], body.name.strip())
    except AlreadyMember as e:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already in a household") from e
    return _info(repo, household.id, "owner")


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_household(ctx: Owner, repo: HouseholdRepo) -> Response:
    repo.delete(ctx.household_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/invitations", response_model=InvitationInfo, status_code=status.HTTP_201_CREATED)
def create_invitation(ctx: Owner, repo: HouseholdRepo, body: InvitationCreate) -> InvitationInfo:
    token = repo.invite(ctx.household_id, body.role, ctx.sub)
    return InvitationInfo(token=token, expires_in_days=INVITATION_TTL.days)


@router.post("/join", response_model=HouseholdInfo)
def join_household(claims: Claims, repo: HouseholdRepo, body: JoinBody) -> HouseholdInfo:
    try:
        member = repo.redeem(body.token, claims["sub"])
    except InvitationInvalid as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation invalid or expired") from e
    except AlreadyMember as e:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already in a household") from e
    return _info(repo, member.household_id, member.role)


@router.patch("/members/{sub}", response_model=MemberInfo)
def set_member_role(ctx: Owner, repo: HouseholdRepo, sub: str, body: RolePatch) -> MemberInfo:
    target = repo.membership(sub)
    if target is None or target.household_id != ctx.household_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such member")
    if target.role == "owner":
        raise HTTPException(status.HTTP_409_CONFLICT, "The owner's role cannot change")
    member = repo.set_role(ctx.household_id, sub, body.role)
    return MemberInfo(sub=member.sub, role=member.role)


@router.delete("/members/{sub}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(ctx: Owner, repo: HouseholdRepo, sub: str) -> Response:
    target = repo.membership(sub)
    if target is None or target.household_id != ctx.household_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such member")
    if target.role == "owner":
        raise HTTPException(status.HTTP_409_CONFLICT, "Delete the household instead")
    repo.remove(ctx.household_id, sub)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/leave", status_code=status.HTTP_204_NO_CONTENT)
def leave_household(ctx: Reader, repo: HouseholdRepo) -> Response:
    if ctx.role == "owner":
        raise HTTPException(status.HTTP_409_CONFLICT, "The owner must delete the household")
    repo.remove(ctx.household_id, ctx.sub)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

Edit `backend/app/api/router.py`: add `household` to the import (`from app.api.routes import files, folders, health, household, me`) and `api_router.include_router(household.router)` after the folders line.

- [ ] **Step 7: Run the tests, regenerate the snapshot, lint**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_household.py -v && cd .. && make snapshot && cd backend && .venv/bin/python -m pytest -m "unit or regression" -q && .venv/bin/python -m ruff format . && .venv/bin/python -m ruff check .`
Expected: all PASS, ruff clean, snapshot diff shows only the new `/api/household...` paths.

- [ ] **Step 8: Commit (only if the user has asked for commits)**

```bash
git add backend/app/core backend/app/api backend/tests
git commit -m "feat: household routes and role dependencies"
```

---

### Task 3: Providers, services and the invoice schema

**Files:**
- Create: `backend/app/models/providers.py`, `backend/migrations/versions/0004_providers_services_invoices.py`, `backend/app/repositories/providers.py`, `backend/app/api/routes/providers.py`
- Modify: `backend/app/models/__init__.py`, `backend/app/api/router.py`, `backend/tests/conftest.py`, `backend/tests/regression/openapi.snapshot.json`
- Test: `backend/tests/unit/test_providers.py`, `backend/tests/regression/test_r2_cross_household_isolation.py`, `backend/tests/integration/test_ledger_repository.py`

**Interfaces:**
- Consumes: `Reader`, `Writer` (Task 2); `ledger`/`home` fixtures.
- Produces:
  - Models `Provider(id, household_id, name, website, phone, email, notes)`, `Service(id, provider_id, household_id, name, category, account_number, contract_start, contract_end, renewal_reminder_days, expected_monthly_cost, auto_pay, alert_threshold_pct, archived)`, `Invoice(id, household_id, service_id, file_id, uploaded_by, status, total, due_on, issued_on, period_start, period_end, invoice_number, consumption_qty, consumption_unit, paid_at, extraction, error, created_at)`, `InvoiceTax(id, invoice_id, name, amount)`.
  - `ProviderRepository(db)`: `list_providers(household_id)`, `get_provider(household_id, provider_id)`, `create_provider(household_id, **fields)`, `update_provider(provider, **changes)`, `delete_provider(provider)`, `services(household_id, provider_id=None, include_archived=False)`, `get_service(household_id, service_id)`, `create_service(household_id, provider_id, **fields)`, `update_service(service, **changes)`, `delete_service(service)`, `service_has_invoices(service_id) -> bool`; `provider_repository(db)`, `ProviderRepo`.
  - Routes: `GET/POST /api/providers`, `GET/PATCH/DELETE /api/providers/{id}`, `POST /api/providers/{id}/services`, `GET/PATCH/DELETE /api/services/{id}`. Response `ProviderInfo` = `{id, name, website, phone, email, notes, services: ServiceInfo[]}`; `ServiceInfo` = `{id, provider_id, name, category, account_number, contract_start, contract_end, renewal_reminder_days, expected_monthly_cost (str|null), auto_pay, alert_threshold_pct, archived}`.
  - Fake: `FakeProviderRepository` (attribute `invoices` is wired by the `ledger` fixture in Task 5; until then `service_has_invoices` returns `False` when it is `None`), `ledger.providers`.

- [ ] **Step 1: Write the models**

Create `backend/app/models/providers.py`:

```python
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.files import Base

INVOICE_STATUSES = ("queued", "extracting", "to_validate", "validated", "failed")


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("households.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Service(Base):
    __tablename__ = "services"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("providers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalised from the provider so every query can filter on it directly.
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("households.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    account_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    contract_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    contract_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    renewal_reminder_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_monthly_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    auto_pay: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    alert_threshold_pct: Mapped[int] = mapped_column(Integer, nullable=False, server_default="20")
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'extracting', 'to_validate', 'validated', 'failed')",
            name="ck_invoices_status",
        ),
        # NULLs are distinct in PostgreSQL, so unvalidated rows never clash.
        UniqueConstraint("service_id", "invoice_number", name="uq_invoices_service_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("households.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Empty until the invoice is validated (or preselected at upload).
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("services.id"), nullable=True, index=True
    )
    # The PDF is an ordinary file owned by `uploaded_by`; if it is deleted the
    # reference is cleared and the invoice keeps its numbers.
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_by: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    issued_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    consumption_qty: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    consumption_unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    paid_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    # What the model said and which provider / service it matched (Task 6).
    extraction: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class InvoiceTax(Base):
    __tablename__ = "invoice_taxes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
```

Update `backend/app/models/__init__.py` to also import and export `Invoice`, `InvoiceTax`, `Provider`, `Service` from `app.models.providers` (keep `__all__` sorted).

- [ ] **Step 2: Write the migration**

Create `backend/migrations/versions/0004_providers_services_invoices.py`:

```python
"""providers, services, invoices, invoice_taxes.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "providers",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "household_id",
            UUID,
            sa.ForeignKey("households.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("website", sa.Text()),
        sa.Column("phone", sa.Text()),
        sa.Column("email", sa.Text()),
        sa.Column("notes", sa.Text()),
        _created_at(),
    )
    op.create_index("ix_providers_household_id", "providers", ["household_id"])

    op.create_table(
        "services",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "provider_id", UUID, sa.ForeignKey("providers.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "household_id",
            UUID,
            sa.ForeignKey("households.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("account_number", sa.Text()),
        sa.Column("contract_start", sa.Date()),
        sa.Column("contract_end", sa.Date()),
        sa.Column("renewal_reminder_days", sa.Integer()),
        sa.Column("expected_monthly_cost", sa.Numeric(12, 2)),
        sa.Column("auto_pay", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("alert_threshold_pct", sa.Integer(), server_default="20", nullable=False),
        sa.Column("archived", sa.Boolean(), server_default="false", nullable=False),
        _created_at(),
    )
    op.create_index("ix_services_provider_id", "services", ["provider_id"])
    op.create_index("ix_services_household_id", "services", ["household_id"])

    op.create_table(
        "invoices",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "household_id",
            UUID,
            sa.ForeignKey("households.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("service_id", UUID, sa.ForeignKey("services.id")),
        sa.Column("file_id", UUID, sa.ForeignKey("files.id", ondelete="SET NULL")),
        sa.Column("uploaded_by", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("total", sa.Numeric(12, 2)),
        sa.Column("due_on", sa.Date()),
        sa.Column("issued_on", sa.Date()),
        sa.Column("period_start", sa.Date()),
        sa.Column("period_end", sa.Date()),
        sa.Column("invoice_number", sa.Text()),
        sa.Column("consumption_qty", sa.Numeric(14, 3)),
        sa.Column("consumption_unit", sa.Text()),
        sa.Column("paid_at", sa.Date()),
        sa.Column("extraction", postgresql.JSONB()),
        sa.Column("error", sa.Text()),
        _created_at(),
        sa.CheckConstraint(
            "status IN ('queued', 'extracting', 'to_validate', 'validated', 'failed')",
            name="ck_invoices_status",
        ),
        sa.UniqueConstraint("service_id", "invoice_number", name="uq_invoices_service_number"),
    )
    op.create_index("ix_invoices_household_id", "invoices", ["household_id"])
    op.create_index("ix_invoices_service_id", "invoices", ["service_id"])

    op.create_table(
        "invoice_taxes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "invoice_id", UUID, sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
    )
    op.create_index("ix_invoice_taxes_invoice_id", "invoice_taxes", ["invoice_id"])


def downgrade() -> None:
    op.drop_table("invoice_taxes")
    op.drop_table("invoices")
    op.drop_table("services")
    op.drop_table("providers")
```

- [ ] **Step 3: Write the failing integration test for the repository**

Create `backend/tests/integration/test_ledger_repository.py`:

```python
"""Integration — ProviderRepository against real PostgreSQL, and the schema's cascades."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.providers import Invoice
from app.repositories.households import HouseholdRepository
from app.repositories.providers import ProviderRepository


@pytest.fixture
def mine(db):
    return HouseholdRepository(db).create("alice", "Maison").id


@pytest.fixture
def theirs(db):
    return HouseholdRepository(db).create("dave", "Chalet").id


@pytest.fixture
def providers(db):
    return ProviderRepository(db)


def make_service(providers, household_id, provider_id, **overrides):
    fields = {
        "name": "Internet",
        "category": "internet",
        "account_number": None,
        "contract_start": None,
        "contract_end": None,
        "renewal_reminder_days": None,
        "expected_monthly_cost": Decimal("79.99"),
        "auto_pay": False,
        "alert_threshold_pct": 20,
        "archived": False,
    } | overrides
    return providers.create_service(household_id, provider_id, **fields)


def test_providers_are_scoped_to_the_household(providers, mine, theirs):
    bell = providers.create_provider(
        mine, name="Bell", website=None, phone=None, email=None, notes=None
    )

    assert providers.get_provider(mine, bell.id).name == "Bell"
    assert providers.get_provider(theirs, bell.id) is None
    assert providers.list_providers(theirs) == []


def test_services_hide_archived_ones_unless_asked(providers, mine):
    bell = providers.create_provider(
        mine, name="Bell", website=None, phone=None, email=None, notes=None
    )
    make_service(providers, mine, bell.id)
    make_service(providers, mine, bell.id, name="Mobile", archived=True)

    assert [s.name for s in providers.services(mine)] == ["Internet"]
    assert len(providers.services(mine, include_archived=True)) == 2


def test_a_service_with_an_invoice_reports_it_and_cannot_be_deleted(providers, mine, db):
    bell = providers.create_provider(
        mine, name="Bell", website=None, phone=None, email=None, notes=None
    )
    service = make_service(providers, mine, bell.id)
    db.add(
        Invoice(
            id=uuid.uuid4(),
            household_id=mine,
            service_id=service.id,
            uploaded_by="alice",
            status="validated",
        )
    )
    db.commit()

    assert providers.service_has_invoices(service.id) is True
    with pytest.raises(IntegrityError):
        providers.delete_service(service)


def test_deleting_a_household_removes_everything_under_it(providers, mine, db):
    bell = providers.create_provider(
        mine, name="Bell", website=None, phone=None, email=None, notes=None
    )
    service = make_service(providers, mine, bell.id)
    db.add(
        Invoice(
            id=uuid.uuid4(),
            household_id=mine,
            service_id=service.id,
            uploaded_by="alice",
            status="validated",
        )
    )
    db.commit()

    HouseholdRepository(db).delete(mine)

    for table in ("providers", "services", "invoices"):
        assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0


def test_the_database_refuses_an_unknown_invoice_status(mine, db):
    db.add(Invoice(id=uuid.uuid4(), household_id=mine, uploaded_by="alice", status="bogus"))

    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
```

- [ ] **Step 4: Write the failing unit and regression tests**

Create `backend/tests/unit/test_providers.py`:

```python
"""Unit — the provider and service routes, against FakeProviderRepository."""

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)

PROVIDER = {"name": "Bell", "website": "https://bell.ca", "phone": None, "email": None, "notes": None}
SERVICE = {"name": "Internet", "category": "internet", "auto_pay": True, "expected_monthly_cost": "79.99"}


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def make_provider(body=None, sub="user-1"):
    return client.post("/api/providers", headers=auth(sub), json=body or PROVIDER)


def make_service(provider_id, body=None, sub="user-1"):
    return client.post(
        f"/api/providers/{provider_id}/services", headers=auth(sub), json=body or SERVICE
    )


def test_create_and_read_a_provider_with_its_services(home):
    provider = make_provider().json()
    make_service(provider["id"])

    listed = client.get("/api/providers", headers=auth()).json()

    assert [p["name"] for p in listed] == ["Bell"]
    assert [s["name"] for s in listed[0]["services"]] == ["Internet"]
    assert listed[0]["services"][0]["alert_threshold_pct"] == 20
    assert listed[0]["services"][0]["expected_monthly_cost"] == "79.99"


def test_providers_are_listed_by_name_ignoring_case(home):
    for name in ("hydro", "Bell", "Vidéotron"):
        make_provider({**PROVIDER, "name": name})

    names = [p["name"] for p in client.get("/api/providers", headers=auth()).json()]

    assert names == ["Bell", "hydro", "Vidéotron"]


def test_a_blank_name_is_refused(home):
    assert make_provider({**PROVIDER, "name": ""}).status_code == 422


def test_patch_changes_only_what_is_sent(home):
    provider = make_provider().json()

    body = client.patch(
        f"/api/providers/{provider['id']}", headers=auth(), json={"phone": "1 888 310-2355"}
    ).json()

    assert (body["name"], body["phone"], body["website"]) == ("Bell", "1 888 310-2355", "https://bell.ca")


def test_patch_cannot_blank_the_name(home):
    provider = make_provider().json()

    response = client.patch(f"/api/providers/{provider['id']}", headers=auth(), json={"name": None})

    assert response.status_code == 422


def test_a_provider_with_services_cannot_be_deleted(home):
    provider = make_provider().json()
    service = make_service(provider["id"]).json()

    assert client.delete(f"/api/providers/{provider['id']}", headers=auth()).status_code == 409
    assert client.delete(f"/api/services/{service['id']}", headers=auth()).status_code == 204
    assert client.delete(f"/api/providers/{provider['id']}", headers=auth()).status_code == 204


def test_the_contract_cannot_end_before_it_starts(home):
    provider = make_provider().json()

    response = make_service(
        provider["id"], {**SERVICE, "contract_start": "2026-05-01", "contract_end": "2026-01-01"}
    )

    assert response.status_code == 422


def test_patching_the_end_date_is_checked_against_the_stored_start(home):
    provider = make_provider().json()
    service = make_service(provider["id"], {**SERVICE, "contract_start": "2026-05-01"}).json()

    response = client.patch(
        f"/api/services/{service['id']}", headers=auth(), json={"contract_end": "2026-01-01"}
    )

    assert response.status_code == 422


def test_a_service_is_archived_with_a_patch(home):
    provider = make_provider().json()
    service = make_service(provider["id"]).json()

    client.patch(f"/api/services/{service['id']}", headers=auth(), json={"archived": True})

    listed = client.get(f"/api/providers/{provider['id']}", headers=auth()).json()
    assert listed["services"][0]["archived"] is True


def test_a_service_with_invoices_is_not_deleted_but_archived(home):
    provider = make_provider().json()
    service = make_service(provider["id"]).json()
    home.ledger.providers.invoices_for_service[uuid_of(service["id"])] = True

    assert client.delete(f"/api/services/{service['id']}", headers=auth()).status_code == 409


def test_a_viewer_reads_but_never_writes(home):
    home.ledger.households.add_member(home.id, "viewer", "viewer")
    provider = make_provider().json()
    service = make_service(provider["id"]).json()

    assert client.get("/api/providers", headers=auth("viewer")).status_code == 200
    assert client.get(f"/api/services/{service['id']}", headers=auth("viewer")).status_code == 200
    writes = [
        client.post("/api/providers", headers=auth("viewer"), json=PROVIDER),
        client.patch(f"/api/providers/{provider['id']}", headers=auth("viewer"), json={"notes": "x"}),
        client.delete(f"/api/providers/{provider['id']}", headers=auth("viewer")),
        client.post(f"/api/providers/{provider['id']}/services", headers=auth("viewer"), json=SERVICE),
        client.patch(f"/api/services/{service['id']}", headers=auth("viewer"), json={"archived": True}),
        client.delete(f"/api/services/{service['id']}", headers=auth("viewer")),
    ]
    assert [r.status_code for r in writes] == [403] * 6


def test_a_member_of_the_household_sees_what_the_owner_created(home):
    home.ledger.households.add_member(home.id, "user-2", "member")
    make_provider()

    assert len(client.get("/api/providers", headers=auth("user-2")).json()) == 1


def uuid_of(value):
    import uuid

    return uuid.UUID(value)
```

Create `backend/tests/regression/test_r2_cross_household_isolation.py`:

```python
"""Regression R-2 — one household never sees, edits or deletes another's data.

Every ledger row carries a household_id and every repository call takes it.
A foreign id must look exactly like a missing one: 404, never 403.
"""

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)


def auth(sub):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def test_a_foreign_provider_or_service_is_a_404(ledger):
    ledger.households.create("alice", "Maison")
    ledger.households.create("dave", "Chalet")
    provider = client.post(
        "/api/providers", headers=auth("alice"), json={"name": "Bell"}
    ).json()
    service = client.post(
        f"/api/providers/{provider['id']}/services",
        headers=auth("alice"),
        json={"name": "Internet", "category": "internet"},
    ).json()

    attempts = [
        client.get(f"/api/providers/{provider['id']}", headers=auth("dave")),
        client.patch(f"/api/providers/{provider['id']}", headers=auth("dave"), json={"name": "X"}),
        client.delete(f"/api/providers/{provider['id']}", headers=auth("dave")),
        client.post(
            f"/api/providers/{provider['id']}/services",
            headers=auth("dave"),
            json={"name": "Hack", "category": "x"},
        ),
        client.get(f"/api/services/{service['id']}", headers=auth("dave")),
        client.patch(f"/api/services/{service['id']}", headers=auth("dave"), json={"archived": True}),
        client.delete(f"/api/services/{service['id']}", headers=auth("dave")),
    ]

    assert [r.status_code for r in attempts] == [404] * 7
    assert client.get("/api/providers", headers=auth("dave")).json() == []
```

Note: the `ledger` fixture here gains `providers` in Step 5; in `test_a_service_with_invoices_is_not_deleted_but_archived` the fake exposes `invoices_for_service` (a dict) until Task 5 wires the real `FakeInvoiceRepository` — Task 5 Step 2 replaces that attribute and updates this one test accordingly.

- [ ] **Step 5: Add `FakeProviderRepository` and wire it in the `ledger` fixture**

In `backend/tests/conftest.py` add imports `from app.models.providers import Provider, Service` and `from app.repositories.providers import provider_repository`, then add before the `ledger` fixture:

```python
class FakeProviderRepository:
    """ProviderRepository over lists. `invoices` is wired by the ledger fixture
    once the invoice fake exists (Task 5); `invoices_for_service` is the
    stand-in until then."""

    def __init__(self):
        self.providers: list[Provider] = []
        self.service_rows: list[Service] = []
        self.invoices = None
        self.invoices_for_service: dict[uuid.UUID, bool] = {}

    def list_providers(self, household_id):
        rows = [p for p in self.providers if p.household_id == household_id]
        return sorted(rows, key=lambda p: (p.name.lower(), p.id))

    def get_provider(self, household_id, provider_id):
        return next(
            (
                p
                for p in self.providers
                if p.household_id == household_id and p.id == provider_id
            ),
            None,
        )

    def create_provider(self, household_id, **fields):
        row = Provider(id=uuid.uuid4(), household_id=household_id, **fields)
        self.providers.append(row)
        return row

    def update_provider(self, provider, **changes):
        for field, value in changes.items():
            setattr(provider, field, value)
        return provider

    def delete_provider(self, provider):
        self.providers.remove(provider)

    def services(self, household_id, provider_id=None, include_archived=False):
        return [
            s
            for s in self.service_rows
            if s.household_id == household_id
            and (provider_id is None or s.provider_id == provider_id)
            and (include_archived or not s.archived)
        ]

    def get_service(self, household_id, service_id):
        return next(
            (
                s
                for s in self.service_rows
                if s.household_id == household_id and s.id == service_id
            ),
            None,
        )

    def create_service(self, household_id, provider_id, **fields):
        row = Service(
            id=uuid.uuid4(), household_id=household_id, provider_id=provider_id, **fields
        )
        self.service_rows.append(row)
        return row

    def update_service(self, service, **changes):
        for field, value in changes.items():
            setattr(service, field, value)
        return service

    def delete_service(self, service):
        self.service_rows.remove(service)

    def service_has_invoices(self, service_id):
        if self.invoices is not None:
            return any(i.service_id == service_id for i in self.invoices.rows)
        return self.invoices_for_service.get(service_id, False)
```

Replace the `ledger` fixture with:

```python
@pytest.fixture
def ledger():
    """Swap the household and provider repositories (and, from Task 5, the
    invoice repository) for in-memory fakes. Explicit, never autouse."""
    fake = SimpleNamespace(
        households=FakeHouseholdRepository(), providers=FakeProviderRepository()
    )
    app.dependency_overrides[household_repository] = lambda: fake.households
    app.dependency_overrides[provider_repository] = lambda: fake.providers
    yield fake
    app.dependency_overrides.pop(household_repository, None)
    app.dependency_overrides.pop(provider_repository, None)
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_providers.py tests/regression/test_r2_cross_household_isolation.py tests/integration/test_ledger_repository.py -v`
Expected: FAIL (`ModuleNotFoundError: app.repositories.providers`).

- [ ] **Step 7: Write the repository**

Create `backend/app/repositories/providers.py`:

```python
from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import Db
from app.models.providers import Invoice, Provider, Service


class ProviderRepository:
    """Household-scoped provider and service queries. `household_id` always
    comes first, like `owner_sub` in FileRepository, so omitting it is a
    TypeError rather than a leak."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def list_providers(self, household_id: uuid.UUID) -> Sequence[Provider]:
        statement = (
            select(Provider)
            .where(Provider.household_id == household_id)
            .order_by(func.lower(Provider.name), Provider.id)
        )
        return self.db.scalars(statement).all()

    def get_provider(self, household_id: uuid.UUID, provider_id: uuid.UUID) -> Provider | None:
        statement = select(Provider).where(
            Provider.id == provider_id, Provider.household_id == household_id
        )
        return self.db.scalars(statement).one_or_none()

    def create_provider(self, household_id: uuid.UUID, **fields: Any) -> Provider:
        row = Provider(id=uuid.uuid4(), household_id=household_id, **fields)
        self.db.add(row)
        self.db.commit()
        return row

    def update_provider(self, provider: Provider, **changes: Any) -> Provider:
        for field, value in changes.items():
            setattr(provider, field, value)
        self.db.commit()
        return provider

    def delete_provider(self, provider: Provider) -> None:
        self.db.delete(provider)
        self.db.commit()

    def services(
        self,
        household_id: uuid.UUID,
        provider_id: uuid.UUID | None = None,
        include_archived: bool = False,
    ) -> Sequence[Service]:
        statement = select(Service).where(Service.household_id == household_id)
        if provider_id is not None:
            statement = statement.where(Service.provider_id == provider_id)
        if not include_archived:
            statement = statement.where(Service.archived.is_(False))
        return self.db.scalars(statement.order_by(func.lower(Service.name), Service.id)).all()

    def get_service(self, household_id: uuid.UUID, service_id: uuid.UUID) -> Service | None:
        statement = select(Service).where(
            Service.id == service_id, Service.household_id == household_id
        )
        return self.db.scalars(statement).one_or_none()

    def create_service(
        self, household_id: uuid.UUID, provider_id: uuid.UUID, **fields: Any
    ) -> Service:
        row = Service(
            id=uuid.uuid4(), household_id=household_id, provider_id=provider_id, **fields
        )
        self.db.add(row)
        self.db.commit()
        return row

    def update_service(self, service: Service, **changes: Any) -> Service:
        for field, value in changes.items():
            setattr(service, field, value)
        self.db.commit()
        return service

    def delete_service(self, service: Service) -> None:
        # invoices.service_id has no cascade: a service with invoices refuses
        # to go (IntegrityError). The route checks service_has_invoices first.
        self.db.delete(service)
        self.db.commit()

    def service_has_invoices(self, service_id: uuid.UUID) -> bool:
        statement = select(Invoice.id).where(Invoice.service_id == service_id).limit(1)
        return self.db.scalar(statement) is not None


def provider_repository(db: Db) -> ProviderRepository:
    return ProviderRepository(db)


ProviderRepo = Annotated[ProviderRepository, Depends(provider_repository)]
```

- [ ] **Step 8: Write the routes**

Create `backend/app/api/routes/providers.py`:

```python
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.household import Reader, Writer
from app.models.providers import Service
from app.repositories.providers import ProviderRepo

router = APIRouter(tags=["providers"])


class ServiceInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider_id: uuid.UUID
    name: str
    category: str
    account_number: str | None
    contract_start: date | None
    contract_end: date | None
    renewal_reminder_days: int | None
    expected_monthly_cost: Decimal | None
    auto_pay: bool
    alert_threshold_pct: int
    archived: bool


class ProviderInfo(BaseModel):
    id: uuid.UUID
    name: str
    website: str | None
    phone: str | None
    email: str | None
    notes: str | None
    services: list[ServiceInfo]


class ProviderIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    website: str | None = Field(None, max_length=500)
    phone: str | None = Field(None, max_length=50)
    email: str | None = Field(None, max_length=254)
    notes: str | None = Field(None, max_length=4000)


class ProviderPatch(BaseModel):
    """Omitted = unchanged."""

    name: str | None = Field(None, min_length=1, max_length=200)
    website: str | None = Field(None, max_length=500)
    phone: str | None = Field(None, max_length=50)
    email: str | None = Field(None, max_length=254)
    notes: str | None = Field(None, max_length=4000)


class ServiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    account_number: str | None = Field(None, max_length=100)
    contract_start: date | None = None
    contract_end: date | None = None
    renewal_reminder_days: int | None = Field(None, ge=0, le=365)
    expected_monthly_cost: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    auto_pay: bool = False
    alert_threshold_pct: int = Field(20, ge=0, le=1000)


class ServicePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    category: str | None = Field(None, min_length=1, max_length=100)
    account_number: str | None = Field(None, max_length=100)
    contract_start: date | None = None
    contract_end: date | None = None
    renewal_reminder_days: int | None = Field(None, ge=0, le=365)
    expected_monthly_cost: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    auto_pay: bool | None = None
    alert_threshold_pct: int | None = Field(None, ge=0, le=1000)
    archived: bool | None = None


def _check_dates(start: date | None, end: date | None) -> None:
    if start is not None and end is not None and end < start:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "The contract cannot end before it starts"
        )


def _not_blank(changes: dict, *fields: str) -> None:
    """An explicit null on a required column is a mistake, not 'unchanged'."""
    for field in fields:
        if field in changes and changes[field] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{field} is required")


def _provider(repo, ctx, provider_id):
    row = repo.get_provider(ctx.household_id, provider_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such provider")
    return row


def _service(repo, ctx, service_id):
    row = repo.get_service(ctx.household_id, service_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such service")
    return row


def _provider_info(repo: ProviderRepo, ctx, provider) -> ProviderInfo:
    services = repo.services(ctx.household_id, provider.id, include_archived=True)
    return ProviderInfo(
        id=provider.id,
        name=provider.name,
        website=provider.website,
        phone=provider.phone,
        email=provider.email,
        notes=provider.notes,
        services=[ServiceInfo.model_validate(s) for s in services],
    )


@router.get("/providers", response_model=list[ProviderInfo])
def list_providers(ctx: Reader, repo: ProviderRepo) -> list[ProviderInfo]:
    return [_provider_info(repo, ctx, p) for p in repo.list_providers(ctx.household_id)]


@router.post("/providers", response_model=ProviderInfo, status_code=status.HTTP_201_CREATED)
def create_provider(ctx: Writer, repo: ProviderRepo, body: ProviderIn) -> ProviderInfo:
    row = repo.create_provider(ctx.household_id, **body.model_dump())
    return _provider_info(repo, ctx, row)


@router.get("/providers/{provider_id}", response_model=ProviderInfo)
def get_provider(ctx: Reader, repo: ProviderRepo, provider_id: uuid.UUID) -> ProviderInfo:
    return _provider_info(repo, ctx, _provider(repo, ctx, provider_id))


@router.patch("/providers/{provider_id}", response_model=ProviderInfo)
def update_provider(
    ctx: Writer, repo: ProviderRepo, provider_id: uuid.UUID, body: ProviderPatch
) -> ProviderInfo:
    row = _provider(repo, ctx, provider_id)
    changes = body.model_dump(exclude_unset=True)
    _not_blank(changes, "name")
    if changes:
        repo.update_provider(row, **changes)
    return _provider_info(repo, ctx, row)


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider(ctx: Writer, repo: ProviderRepo, provider_id: uuid.UUID) -> Response:
    row = _provider(repo, ctx, provider_id)
    if repo.services(ctx.household_id, provider_id, include_archived=True):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Delete or archive its services first"
        )
    repo.delete_provider(row)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/providers/{provider_id}/services",
    response_model=ServiceInfo,
    status_code=status.HTTP_201_CREATED,
)
def create_service(
    ctx: Writer, repo: ProviderRepo, provider_id: uuid.UUID, body: ServiceIn
) -> ServiceInfo:
    _provider(repo, ctx, provider_id)
    _check_dates(body.contract_start, body.contract_end)
    row = repo.create_service(
        ctx.household_id, provider_id, archived=False, **body.model_dump()
    )
    return ServiceInfo.model_validate(row)


@router.get("/services/{service_id}", response_model=ServiceInfo)
def get_service(ctx: Reader, repo: ProviderRepo, service_id: uuid.UUID) -> ServiceInfo:
    return ServiceInfo.model_validate(_service(repo, ctx, service_id))


@router.patch("/services/{service_id}", response_model=ServiceInfo)
def update_service(
    ctx: Writer, repo: ProviderRepo, service_id: uuid.UUID, body: ServicePatch
) -> ServiceInfo:
    row: Service = _service(repo, ctx, service_id)
    changes = body.model_dump(exclude_unset=True)
    _not_blank(changes, "name", "category", "auto_pay", "alert_threshold_pct", "archived")
    _check_dates(
        changes.get("contract_start", row.contract_start),
        changes.get("contract_end", row.contract_end),
    )
    if changes:
        repo.update_service(row, **changes)
    return ServiceInfo.model_validate(row)


@router.delete("/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(ctx: Writer, repo: ProviderRepo, service_id: uuid.UUID) -> Response:
    row = _service(repo, ctx, service_id)
    if repo.service_has_invoices(row.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "It has invoices: archive it instead")
    repo.delete_service(row)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

Edit `backend/app/api/router.py`: import `providers` and add `api_router.include_router(providers.router)`.

- [ ] **Step 9: Run everything, regenerate the snapshot, lint**

Run: `make services-test-up && cd backend && .venv/bin/python -m pytest -q && cd .. && make snapshot && cd backend && .venv/bin/python -m pytest -m "unit or regression" -q && .venv/bin/python -m ruff format . && .venv/bin/python -m ruff check .`
Expected: all PASS (including the three suites), ruff clean. If the line-length check flags the long test dicts, wrap them (ruff `E501` is enforced).

- [ ] **Step 10: Commit (only if the user has asked for commits)**

```bash
git add backend
git commit -m "feat: providers, services and the invoice schema"
```

---

### Task 4: Cost rules (pure functions)

**Files:**
- Create: `backend/app/costs.py`
- Test: `backend/tests/unit/test_costs.py`

**Interfaces:**
- Produces (`app/costs.py`):
  - `is_paid(paid_at: date | None, auto_pay: bool, due_on: date | None, today: date) -> bool`
  - `reference(previous: Sequence[Decimal], expected: Decimal | None) -> Decimal | None` (`previous` oldest first)
  - `is_flagged(total: Decimal, ref: Decimal | None, threshold_pct: int) -> bool`
  - `@dataclass(frozen=True) Point(invoice_id: uuid.UUID, when: date, total: Decimal, reference: Decimal | None, flagged: bool)`
  - `series(invoices: Iterable[tuple[uuid.UUID, date, Decimal]], expected: Decimal | None, threshold_pct: int) -> list[Point]` (any input order; output oldest first)
  - `month_of(when: date) -> str` (`"2026-10"`), `monthly_totals(rows: Iterable[tuple[date, Decimal]]) -> list[tuple[str, Decimal]]` (sorted by month)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_costs.py`:

```python
"""Unit — the pure cost rules: paid state, alert reference, flags, monthly totals."""

import uuid
from datetime import date
from decimal import Decimal as D

from app import costs

TODAY = date(2026, 10, 10)


def test_an_invoice_with_a_payment_date_is_paid():
    assert costs.is_paid(date(2026, 10, 1), False, date(2026, 11, 1), TODAY) is True


def test_an_auto_pay_invoice_is_paid_from_its_due_date():
    assert costs.is_paid(None, True, date(2026, 10, 10), TODAY) is True
    assert costs.is_paid(None, True, date(2026, 10, 11), TODAY) is False


def test_a_manual_invoice_is_unpaid_until_marked():
    assert costs.is_paid(None, False, date(2026, 1, 1), TODAY) is False


def test_an_invoice_with_no_due_date_is_never_auto_paid():
    assert costs.is_paid(None, True, None, TODAY) is False


def test_with_fewer_than_three_invoices_the_reference_is_the_expected_cost():
    assert costs.reference([D("100"), D("120")], D("90")) == D("90")
    assert costs.reference([], None) is None


def test_with_three_invoices_the_reference_is_their_mean():
    assert costs.reference([D("100"), D("110"), D("120")], D("90")) == D("110")


def test_only_the_last_six_invoices_count():
    history = [D("1000")] + [D("100")] * 6

    assert costs.reference(history, None) == D("100")


def test_a_bill_exactly_at_the_threshold_is_not_flagged():
    assert costs.is_flagged(D("120.00"), D("100"), 20) is False
    assert costs.is_flagged(D("120.01"), D("100"), 20) is True


def test_nothing_is_flagged_without_a_reference():
    assert costs.is_flagged(D("999"), None, 20) is False


def test_series_compares_each_invoice_with_the_ones_before_it():
    ids = [uuid.UUID(int=i) for i in range(1, 6)]
    rows = [
        (ids[3], date(2026, 4, 1), D("100")),
        (ids[0], date(2026, 1, 1), D("100")),
        (ids[2], date(2026, 3, 1), D("100")),
        (ids[4], date(2026, 5, 1), D("200")),
        (ids[1], date(2026, 2, 1), D("100")),
    ]

    points = costs.series(rows, expected=D("50"), threshold_pct=20)

    assert [p.invoice_id for p in points] == ids
    # The first three are compared with the expected cost (50), the next with their mean.
    assert [p.flagged for p in points] == [True, True, True, False, True]
    assert points[3].reference == D("100.00")


def test_monthly_totals_group_by_month_and_sort():
    rows = [
        (date(2026, 9, 28), D("10.50")),
        (date(2026, 9, 1), D("20")),
        (date(2026, 8, 15), D("5")),
    ]

    assert costs.monthly_totals(rows) == [("2026-08", D("5")), ("2026-09", D("30.50"))]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_costs.py -v`
Expected: FAIL (`ImportError: cannot import name 'costs'`).

- [ ] **Step 3: Write the implementation**

Create `backend/app/costs.py`:

```python
"""Cost rules, kept pure so they are tested without a database.

Nothing here is stored: the paid state of an auto-pay invoice and every alert
are computed at read time, so correcting a service's `auto_pay`, expected cost
or threshold corrects the whole history at once.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

WINDOW = 6  # invoices averaged for the reference
MIN_HISTORY = 3  # fewer than this and the expected cost is the reference
CENTS = Decimal("0.01")


def is_paid(paid_at: date | None, auto_pay: bool, due_on: date | None, today: date) -> bool:
    if paid_at is not None:
        return True
    return auto_pay and due_on is not None and due_on <= today


def reference(previous: Sequence[Decimal], expected: Decimal | None) -> Decimal | None:
    """`previous` is oldest first."""
    window = previous[-WINDOW:]
    if len(window) >= MIN_HISTORY:
        return (sum(window) / len(window)).quantize(CENTS)
    return expected


def is_flagged(total: Decimal, ref: Decimal | None, threshold_pct: int) -> bool:
    return ref is not None and total > ref * (1 + Decimal(threshold_pct) / 100)


@dataclass(frozen=True)
class Point:
    invoice_id: uuid.UUID
    when: date
    total: Decimal
    reference: Decimal | None
    flagged: bool


def series(
    invoices: Iterable[tuple[uuid.UUID, date, Decimal]],
    expected: Decimal | None,
    threshold_pct: int,
) -> list[Point]:
    ordered = sorted(invoices, key=lambda row: (row[1], str(row[0])))
    points: list[Point] = []
    for index, (invoice_id, when, total) in enumerate(ordered):
        ref = reference([row[2] for row in ordered[:index]], expected)
        points.append(Point(invoice_id, when, total, ref, is_flagged(total, ref, threshold_pct)))
    return points


def month_of(when: date) -> str:
    return f"{when.year:04d}-{when.month:02d}"


def monthly_totals(rows: Iterable[tuple[date, Decimal]]) -> list[tuple[str, Decimal]]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for when, total in rows:
        totals[month_of(when)] += total
    return sorted(totals.items())
```

- [ ] **Step 4: Run the tests and lint**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_costs.py -v && .venv/bin/python -m ruff format . && .venv/bin/python -m ruff check .`
Expected: PASS, clean.

- [ ] **Step 5: Commit (only if the user has asked for commits)**

```bash
git add backend/app/costs.py backend/tests/unit/test_costs.py
git commit -m "feat: pure cost rules for paid state and alerts"
```

---

### Task 5: Invoices: repository, upload, validation, PDF stream

**Files:**
- Create: `backend/app/repositories/invoices.py`, `backend/app/api/routes/invoices.py`
- Modify: `backend/app/api/router.py`, `backend/tests/conftest.py`, `backend/tests/regression/openapi.snapshot.json`, `backend/tests/unit/test_providers.py` (one test, see Step 2)
- Test: `backend/tests/unit/test_invoices.py`, `backend/tests/integration/test_invoice_repository.py`

**Interfaces:**
- Consumes: `Writer`, `Reader` (Task 2); `ProviderRepo` (Task 3); `costs.is_paid` (Task 4); `Today` (Task 2); from `app/api/routes/files.py`: `s3_client`, `_validated_name`, `_measure`, `_unversioned`; `FileRepo`, `QuotaExceeded`.
- Produces:
  - `InvoiceRepository(db)`: `create(household_id, *, uploaded_by, file_id, service_id, status, extraction=None, taxes=(), **fields) -> Invoice`; `get(household_id, invoice_id)`; `by_id(invoice_id)` (internal, unscoped, for the background job); `list(household_id, *, status=None, service_id=None)`; `taxes_by_invoice(invoice_ids) -> dict[UUID, list[InvoiceTax]]`; `find_duplicate(service_id, invoice_number, exclude_id=None) -> Invoice | None`; `validate(invoice, *, service_id, fields: dict, taxes: list[dict]) -> Invoice`; `set_paid(invoice, paid_at: date | None)`; `delete(invoice)`; `validated(household_id, service_id=None) -> list[Invoice]`; `set_status(invoice, status)`; `finish(invoice, status, *, extraction=None, error=None, fields=None)`; `reset_stuck() -> int`; `invoice_repository(db)`, `InvoiceRepo`.
  - Routes: `POST /api/invoices` (multipart `files`, optional `service_id`, `provider_id`), `POST /api/invoices/manual`, `GET /api/invoices`, `GET /api/invoices/{id}`, `GET /api/invoices/{id}/pdf`, `POST /api/invoices/{id}/validate`, `POST /api/invoices/{id}/paid`, `DELETE /api/invoices/{id}`.
  - `InvoiceInfo` = `{id, status, service_id, has_pdf, total, due_on, issued_on, period_start, period_end, invoice_number, consumption_qty, consumption_unit, paid_at, paid, error, extraction, taxes: [{name, amount}]}`; `InvoiceFields` = `{service_id, total, due_on, issued_on, period_start, period_end, invoice_number, consumption_qty, consumption_unit, taxes}`.
  - Task 6 calls `invoice_extraction.extract(invoice_id)` as a background task; this task imports it lazily so the route works before Task 6 exists: the upload only queues it when `get_settings().ollama_url` is set.
  - Fake: `FakeInvoiceRepository` (attribute `rows`), `ledger.invoices`.

- [ ] **Step 1: Write the integration test for the repository**

Create `backend/tests/integration/test_invoice_repository.py`:

```python
"""Integration — InvoiceRepository against real PostgreSQL."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.repositories.households import HouseholdRepository
from app.repositories.invoices import InvoiceRepository
from app.repositories.providers import ProviderRepository


@pytest.fixture
def setup(db):
    household = HouseholdRepository(db).create("alice", "Maison").id
    providers = ProviderRepository(db)
    bell = providers.create_provider(
        household, name="Bell", website=None, phone=None, email=None, notes=None
    )
    service = providers.create_service(
        household,
        bell.id,
        name="Internet",
        category="internet",
        account_number=None,
        contract_start=None,
        contract_end=None,
        renewal_reminder_days=None,
        expected_monthly_cost=None,
        auto_pay=False,
        alert_threshold_pct=20,
        archived=False,
    )
    return household, service.id, InvoiceRepository(db)


def make(invoices, household, service_id, status="validated", **fields):
    return invoices.create(
        household, uploaded_by="alice", file_id=None, service_id=service_id, status=status, **fields
    )


def test_create_with_taxes_and_read_them_back(setup):
    household, service_id, invoices = setup

    row = make(
        invoices,
        household,
        service_id,
        total=Decimal("114.98"),
        due_on=date(2026, 11, 1),
        taxes=[{"name": "TPS", "amount": Decimal("5.00")}, {"name": "TVQ", "amount": Decimal("9.98")}],
    )

    taxes = invoices.taxes_by_invoice([row.id])[row.id]
    assert sorted((t.name, t.amount) for t in taxes) == [
        ("TPS", Decimal("5.00")),
        ("TVQ", Decimal("9.98")),
    ]


def test_lists_are_scoped_and_filtered(setup, db):
    household, service_id, invoices = setup
    other = HouseholdRepository(db).create("dave", "Chalet").id
    make(invoices, household, service_id, due_on=date(2026, 11, 1), total=Decimal("1"))
    make(invoices, household, None, status="to_validate")

    assert len(invoices.list(household)) == 2
    assert [i.status for i in invoices.list(household, status="to_validate")] == ["to_validate"]
    assert len(invoices.list(household, service_id=service_id)) == 1
    assert invoices.list(other) == []


def test_find_duplicate_matches_service_and_number(setup):
    household, service_id, invoices = setup
    first = make(invoices, household, service_id, invoice_number="A-1", total=Decimal("1"))

    assert invoices.find_duplicate(service_id, "A-1").id == first.id
    assert invoices.find_duplicate(service_id, "A-1", exclude_id=first.id) is None
    assert invoices.find_duplicate(service_id, "A-2") is None


def test_validate_fills_the_fields_replaces_taxes_and_sets_the_status(setup):
    household, service_id, invoices = setup
    pending = make(invoices, household, None, status="to_validate")

    invoices.validate(
        pending,
        service_id=service_id,
        fields={"total": Decimal("50.00"), "due_on": date(2026, 11, 1)},
        taxes=[{"name": "TPS", "amount": Decimal("2.50")}],
    )

    assert (pending.status, pending.service_id, pending.total) == (
        "validated",
        service_id,
        Decimal("50.00"),
    )
    assert [t.name for t in invoices.taxes_by_invoice([pending.id])[pending.id]] == ["TPS"]


def test_set_paid_and_unpaid(setup):
    household, service_id, invoices = setup
    row = make(invoices, household, service_id, total=Decimal("1"), due_on=date(2026, 11, 1))

    invoices.set_paid(row, date(2026, 10, 10))
    assert row.paid_at == date(2026, 10, 10)
    invoices.set_paid(row, None)
    assert row.paid_at is None


def test_validated_is_ordered_oldest_first_and_filterable(setup):
    household, service_id, invoices = setup
    late = make(invoices, household, service_id, total=Decimal("2"), issued_on=date(2026, 5, 1))
    early = make(invoices, household, service_id, total=Decimal("1"), issued_on=date(2026, 1, 1))
    make(invoices, household, None, status="to_validate")

    assert [i.id for i in invoices.validated(household)] == [early.id, late.id]
    assert [i.id for i in invoices.validated(household, service_id)] == [early.id, late.id]


def test_finish_stores_the_result(setup):
    household, service_id, invoices = setup
    row = make(invoices, household, None, status="extracting")

    invoices.finish(
        row, "to_validate", extraction={"raw": {}}, fields={"total": Decimal("9.99")}
    )

    assert (row.status, row.total, row.extraction) == ("to_validate", Decimal("9.99"), {"raw": {}})
    assert invoices.by_id(row.id).status == "to_validate"
    assert invoices.by_id(uuid.uuid4()) is None


def test_reset_stuck_fails_queued_and_extracting_only(setup, db):
    household, service_id, invoices = setup
    queued = make(invoices, household, None, status="queued")
    extracting = make(invoices, household, None, status="extracting")
    waiting = make(invoices, household, None, status="to_validate")

    count = invoices.reset_stuck()
    db.expire_all()

    assert count == 2
    assert (queued.status, extracting.status, waiting.status) == ("failed", "failed", "to_validate")
    assert queued.error


def test_delete_removes_the_invoice_and_its_taxes(setup):
    household, service_id, invoices = setup
    row = make(
        invoices,
        household,
        service_id,
        total=Decimal("1"),
        taxes=[{"name": "TPS", "amount": Decimal("1")}],
    )

    invoices.delete(row)

    assert invoices.get(household, row.id) is None
    assert invoices.taxes_by_invoice([row.id]) == {}
```

- [ ] **Step 2: Add `FakeInvoiceRepository`; wire it into the `ledger` fixture**

In `backend/tests/conftest.py` add imports `from app.models.providers import Invoice, InvoiceTax` (merge with the existing `Provider, Service` import) and `from app.repositories.invoices import invoice_repository`, then add before the `ledger` fixture:

```python
class FakeInvoiceRepository:
    """InvoiceRepository over a list. Mirrors the SQL for the cases the
    integration suite pins: scoped lists ordered by due date (undated last),
    duplicates by (service, number), validated ordered oldest first."""

    def __init__(self):
        self.rows: list[Invoice] = []
        self.tax_rows: dict[uuid.UUID, list[InvoiceTax]] = {}

    def create(
        self,
        household_id,
        *,
        uploaded_by,
        file_id,
        service_id,
        status,
        extraction=None,
        taxes=(),
        **fields,
    ):
        row = Invoice(
            id=uuid.uuid4(),
            household_id=household_id,
            uploaded_by=uploaded_by,
            file_id=file_id,
            service_id=service_id,
            status=status,
            extraction=extraction,
            error=None,
            paid_at=None,
            **fields,
        )
        self.rows.append(row)
        self._set_taxes(row, taxes)
        return row

    def _set_taxes(self, row, taxes):
        self.tax_rows.pop(row.id, None)
        if taxes:
            self.tax_rows[row.id] = [
                InvoiceTax(id=uuid.uuid4(), invoice_id=row.id, **t) for t in taxes
            ]

    def get(self, household_id, invoice_id):
        return next(
            (r for r in self.rows if r.household_id == household_id and r.id == invoice_id),
            None,
        )

    def by_id(self, invoice_id):
        return next((r for r in self.rows if r.id == invoice_id), None)

    def list(self, household_id, *, status=None, service_id=None):
        rows = [
            r
            for r in self.rows
            if r.household_id == household_id
            and (status is None or r.status == status)
            and (service_id is None or r.service_id == service_id)
        ]
        # Stable: ties keep creation order, like ORDER BY due_on NULLS LAST, created_at.
        return sorted(rows, key=lambda r: (r.due_on is None, r.due_on or date.min))

    def taxes_by_invoice(self, invoice_ids):
        return {i: self.tax_rows[i] for i in invoice_ids if i in self.tax_rows}

    def find_duplicate(self, service_id, invoice_number, exclude_id=None):
        return next(
            (
                r
                for r in self.rows
                if r.service_id == service_id
                and r.invoice_number == invoice_number
                and r.id != exclude_id
            ),
            None,
        )

    def validate(self, invoice, *, service_id, fields, taxes):
        for field, value in fields.items():
            setattr(invoice, field, value)
        invoice.service_id = service_id
        invoice.status = "validated"
        invoice.error = None
        self._set_taxes(invoice, taxes)
        return invoice

    def set_paid(self, invoice, paid_at):
        invoice.paid_at = paid_at

    def delete(self, invoice):
        self.rows.remove(invoice)
        self.tax_rows.pop(invoice.id, None)

    def validated(self, household_id, service_id=None):
        rows = [
            r
            for r in self.rows
            if r.household_id == household_id
            and r.status == "validated"
            and (service_id is None or r.service_id == service_id)
        ]
        return sorted(rows, key=lambda r: (r.issued_on or r.due_on, str(r.id)))

    def set_status(self, invoice, status):
        invoice.status = status

    def finish(self, invoice, status, *, extraction=None, error=None, fields=None):
        for field, value in (fields or {}).items():
            setattr(invoice, field, value)
        invoice.status = status
        invoice.extraction = extraction if extraction is not None else invoice.extraction
        invoice.error = error

    def reset_stuck(self):
        stuck = [r for r in self.rows if r.status in ("queued", "extracting")]
        for row in stuck:
            row.status, row.error = "failed", "Interrupted by a restart"
        return len(stuck)
```

Replace the `ledger` fixture with:

```python
@pytest.fixture
def ledger():
    """Swap the household, provider and invoice repositories for in-memory
    fakes. Explicit, never autouse."""
    fake = SimpleNamespace(
        households=FakeHouseholdRepository(),
        providers=FakeProviderRepository(),
        invoices=FakeInvoiceRepository(),
    )
    fake.providers.invoices = fake.invoices
    app.dependency_overrides[household_repository] = lambda: fake.households
    app.dependency_overrides[provider_repository] = lambda: fake.providers
    app.dependency_overrides[invoice_repository] = lambda: fake.invoices
    yield fake
    for dependency in (household_repository, provider_repository, invoice_repository):
        app.dependency_overrides.pop(dependency, None)
```

Remove `invoices_for_service` from `FakeProviderRepository` and make `service_has_invoices` read `self.invoices.rows` unconditionally (`return any(i.service_id == service_id for i in self.invoices.rows)`). In `tests/unit/test_providers.py` rewrite `test_a_service_with_invoices_is_not_deleted_but_archived` to create the invoice through the fake and delete the helper `uuid_of`:

```python
def test_a_service_with_invoices_is_not_deleted_but_archived(home):
    provider = make_provider().json()
    service = make_service(provider["id"]).json()
    home.ledger.invoices.create(
        home.id,
        uploaded_by="user-1",
        file_id=None,
        service_id=uuid.UUID(service["id"]),
        status="validated",
    )

    assert client.delete(f"/api/services/{service['id']}", headers=auth()).status_code == 409
```

(add `import uuid` at the top of that file).

- [ ] **Step 3: Write the failing unit tests for the routes**

Create `backend/tests/unit/test_invoices.py`:

```python
"""Unit — the invoice routes, against the ledger fakes, FakeS3 and FakeFileRepository."""

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)

PDF = b"%PDF-1.4 fake"


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def service_for(home, **overrides):
    """A provider with one service, straight in the fake."""
    providers = home.ledger.providers
    provider = providers.create_provider(
        home.id, name="Bell", website=None, phone=None, email=None, notes=None
    )
    fields = {
        "name": "Internet",
        "category": "internet",
        "account_number": None,
        "contract_start": None,
        "contract_end": None,
        "renewal_reminder_days": None,
        "expected_monthly_cost": None,
        "auto_pay": False,
        "alert_threshold_pct": 20,
        "archived": False,
    } | overrides
    return providers.create_service(home.id, provider.id, **fields)


def upload(names=("facture.pdf",), sub="user-1", **form):
    files = [("files", (name, PDF, "application/pdf")) for name in names]
    return client.post("/api/invoices", headers=auth(sub), files=files, data=form)


def fields(service_id, **overrides):
    return {
        "service_id": str(service_id),
        "total": "114.98",
        "due_on": "2026-11-01",
        "issued_on": "2026-10-01",
        "invoice_number": "A-1",
        "consumption_qty": "1240",
        "consumption_unit": "kWh",
        "taxes": [{"name": "TPS", "amount": "5.00"}, {"name": "TVQ", "amount": "9.98"}],
    } | overrides


def test_uploading_a_pdf_stores_it_as_a_file_and_waits_for_validation(home, repo, store):
    response = upload()

    assert response.status_code == 201
    [invoice] = response.json()
    # No Ollama configured in the unit suite: straight to the validation queue.
    assert (invoice["status"], invoice["has_pdf"], invoice["service_id"]) == (
        "to_validate",
        True,
        None,
    )
    [stored] = repo.rows
    assert (stored.name, stored.owner_sub, stored.content_type) == (
        "facture.pdf",
        "user-1",
        "application/pdf",
    )
    assert store.objects[stored.object_key][0] == PDF


def test_several_pdfs_make_several_invoices_and_a_repeated_name_gets_a_suffix(home, repo, store):
    response = upload(("a.pdf", "a.pdf"))

    assert len(response.json()) == 2
    assert len({row.name for row in repo.rows}) == 2


def test_only_pdfs_are_accepted_and_nothing_is_stored_when_one_is_refused(home, repo, store):
    response = upload(("a.pdf", "b.txt"))

    assert response.status_code == 415
    assert repo.rows == [] and store.objects == {}


def test_a_preselected_service_is_kept(home, repo, store):
    service = service_for(home)

    [invoice] = upload(service_id=str(service.id)).json()

    assert invoice["service_id"] == str(service.id)


def test_a_foreign_preselection_is_a_404_and_stores_nothing(home, repo, store):
    other = home.ledger.households.create("dave", "Chalet")
    foreign = service_for(type("H", (), {"id": other.id, "ledger": home.ledger}))

    response = upload(service_id=str(foreign.id))

    assert response.status_code == 404
    assert repo.rows == []


def test_validating_fills_the_invoice_and_its_taxes(home, repo, store):
    service = service_for(home)
    [pending] = upload().json()

    response = client.post(
        f"/api/invoices/{pending['id']}/validate", headers=auth(), json=fields(service.id)
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["total"], body["service_id"]) == (
        "validated",
        "114.98",
        str(service.id),
    )
    assert [t["name"] for t in body["taxes"]] == ["TPS", "TVQ"]


def test_a_duplicate_number_on_the_same_service_is_a_409_naming_the_existing_invoice(
    home, repo, store
):
    service = service_for(home)
    first = client.post("/api/invoices/manual", headers=auth(), json=fields(service.id)).json()
    [pending] = upload().json()

    response = client.post(
        f"/api/invoices/{pending['id']}/validate", headers=auth(), json=fields(service.id)
    )

    assert response.status_code == 409
    assert response.json()["detail"]["existing_id"] == first["id"]


def test_the_same_number_on_another_service_is_fine(home, repo, store):
    one, two = service_for(home), service_for(home, name="Mobile")
    client.post("/api/invoices/manual", headers=auth(), json=fields(one.id))

    response = client.post("/api/invoices/manual", headers=auth(), json=fields(two.id))

    assert response.status_code == 201


def test_a_validated_invoice_cannot_be_validated_again(home):
    service = service_for(home)
    done = client.post("/api/invoices/manual", headers=auth(), json=fields(service.id)).json()

    response = client.post(
        f"/api/invoices/{done['id']}/validate", headers=auth(), json=fields(service.id)
    )

    assert response.status_code == 409


def test_total_and_due_date_are_required_to_validate(home):
    service = service_for(home)
    body = fields(service.id)
    del body["due_on"]

    assert client.post("/api/invoices/manual", headers=auth(), json=body).status_code == 422


def test_manual_entry_creates_a_validated_invoice_without_a_pdf(home):
    service = service_for(home)

    response = client.post("/api/invoices/manual", headers=auth(), json=fields(service.id))

    assert response.status_code == 201
    assert (response.json()["status"], response.json()["has_pdf"]) == ("validated", False)


def test_marking_paid_and_unpaid(home, clock):
    service = service_for(home)
    invoice = client.post("/api/invoices/manual", headers=auth(), json=fields(service.id)).json()
    assert invoice["paid"] is False

    paid = client.post(
        f"/api/invoices/{invoice['id']}/paid", headers=auth(), json={"paid": True}
    ).json()
    unpaid = client.post(
        f"/api/invoices/{invoice['id']}/paid", headers=auth(), json={"paid": False}
    ).json()

    assert (paid["paid"], paid["paid_at"]) == (True, "2026-10-10")
    assert (unpaid["paid"], unpaid["paid_at"]) == (False, None)


def test_an_auto_pay_service_makes_its_overdue_invoices_paid(home, clock):
    service = service_for(home, auto_pay=True)

    due = client.post(
        "/api/invoices/manual", headers=auth(), json=fields(service.id, due_on="2026-10-09")
    ).json()
    future = client.post(
        "/api/invoices/manual",
        headers=auth(),
        json=fields(service.id, due_on="2026-11-09", invoice_number="A-2"),
    ).json()

    assert (due["paid"], due["paid_at"]) == (True, None)
    assert future["paid"] is False


def test_the_list_filters_by_status_service_and_unpaid(home, repo, store, clock):
    service = service_for(home)
    upload()
    client.post("/api/invoices/manual", headers=auth(), json=fields(service.id))
    paid = client.post(
        "/api/invoices/manual", headers=auth(), json=fields(service.id, invoice_number="A-2")
    ).json()
    client.post(f"/api/invoices/{paid['id']}/paid", headers=auth(), json={"paid": True})

    def ids(**params):
        return {i["id"] for i in client.get("/api/invoices", headers=auth(), params=params).json()}

    assert len(ids()) == 3
    assert len(ids(status="to_validate")) == 1
    assert len(ids(service_id=str(service.id))) == 2
    assert len(ids(unpaid="true")) == 1


def test_a_viewer_can_read_and_stream_the_pdf_but_never_write(home, repo, store):
    home.ledger.households.add_member(home.id, "viewer", "viewer")
    service = service_for(home)
    [pending] = upload().json()

    assert client.get("/api/invoices", headers=auth("viewer")).status_code == 200
    assert client.get(f"/api/invoices/{pending['id']}", headers=auth("viewer")).status_code == 200
    assert client.get(f"/api/invoices/{pending['id']}/pdf", headers=auth("viewer")).content == PDF
    writes = [
        upload(sub="viewer"),
        client.post("/api/invoices/manual", headers=auth("viewer"), json=fields(service.id)),
        client.post(
            f"/api/invoices/{pending['id']}/validate", headers=auth("viewer"), json=fields(service.id)
        ),
        client.post(
            f"/api/invoices/{pending['id']}/paid", headers=auth("viewer"), json={"paid": True}
        ),
        client.delete(f"/api/invoices/{pending['id']}", headers=auth("viewer")),
    ]
    assert [r.status_code for r in writes] == [403] * 5


def test_another_member_streams_the_pdf_the_uploader_owns(home, repo, store):
    home.ledger.households.add_member(home.id, "user-2", "member")
    [pending] = upload().json()

    response = client.get(f"/api/invoices/{pending['id']}/pdf", headers=auth("user-2"))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content == PDF


def test_a_deleted_pdf_is_a_404_on_the_stream_but_the_invoice_survives(home, repo, store):
    [pending] = upload().json()
    repo.soft_delete(repo.rows[0])

    assert client.get(f"/api/invoices/{pending['id']}/pdf", headers=auth()).status_code == 404
    assert client.get(f"/api/invoices/{pending['id']}", headers=auth()).status_code == 200


def test_another_household_sees_nothing_of_an_invoice(home, repo, store):
    home.ledger.households.create("dave", "Chalet")
    [pending] = upload().json()

    responses = [
        client.get(f"/api/invoices/{pending['id']}", headers=auth("dave")),
        client.get(f"/api/invoices/{pending['id']}/pdf", headers=auth("dave")),
        client.delete(f"/api/invoices/{pending['id']}", headers=auth("dave")),
    ]

    assert [r.status_code for r in responses] == [404] * 3
    assert client.get("/api/invoices", headers=auth("dave")).json() == []


def test_deleting_an_invoice_leaves_the_file(home, repo, store):
    [pending] = upload().json()

    assert client.delete(f"/api/invoices/{pending['id']}", headers=auth()).status_code == 204
    assert client.get("/api/invoices", headers=auth()).json() == []
    assert len(repo.rows) == 1
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_invoices.py tests/integration/test_invoice_repository.py -v`
Expected: FAIL (`ModuleNotFoundError: app.repositories.invoices`).

- [ ] **Step 5: Write the repository**

Create `backend/app/repositories/invoices.py`:

```python
from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.db import Db
from app.models.providers import Invoice, InvoiceTax


class InvoiceRepository:
    """Household-scoped invoice queries. Two methods are deliberately unscoped
    and only the background job and the startup sweep use them: `by_id`
    (the job already holds an invoice id from a scoped request) and
    `reset_stuck` (a janitor, like FileRepository.sweep_pending)."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        household_id: uuid.UUID,
        *,
        uploaded_by: str,
        file_id: uuid.UUID | None,
        service_id: uuid.UUID | None,
        status: str,
        extraction: dict | None = None,
        taxes: Sequence[dict] = (),
        **fields: Any,
    ) -> Invoice:
        row = Invoice(
            id=uuid.uuid4(),
            household_id=household_id,
            uploaded_by=uploaded_by,
            file_id=file_id,
            service_id=service_id,
            status=status,
            extraction=extraction,
            **fields,
        )
        self.db.add(row)
        self.db.add_all(
            InvoiceTax(id=uuid.uuid4(), invoice_id=row.id, **tax) for tax in taxes
        )
        self.db.commit()
        return row

    def get(self, household_id: uuid.UUID, invoice_id: uuid.UUID) -> Invoice | None:
        statement = select(Invoice).where(
            Invoice.id == invoice_id, Invoice.household_id == household_id
        )
        return self.db.scalars(statement).one_or_none()

    def by_id(self, invoice_id: uuid.UUID) -> Invoice | None:
        return self.db.get(Invoice, invoice_id)

    def list(
        self,
        household_id: uuid.UUID,
        *,
        status: str | None = None,
        service_id: uuid.UUID | None = None,
    ) -> Sequence[Invoice]:
        statement = select(Invoice).where(Invoice.household_id == household_id)
        if status is not None:
            statement = statement.where(Invoice.status == status)
        if service_id is not None:
            statement = statement.where(Invoice.service_id == service_id)
        statement = statement.order_by(
            Invoice.due_on.asc().nulls_last(), Invoice.created_at, Invoice.id
        )
        return self.db.scalars(statement).all()

    def taxes_by_invoice(self, invoice_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[InvoiceTax]]:
        found: dict[uuid.UUID, list[InvoiceTax]] = defaultdict(list)
        if invoice_ids:
            statement = (
                select(InvoiceTax)
                .where(InvoiceTax.invoice_id.in_(invoice_ids))
                .order_by(InvoiceTax.name, InvoiceTax.id)
            )
            for tax in self.db.scalars(statement):
                found[tax.invoice_id].append(tax)
        return dict(found)

    def find_duplicate(
        self,
        service_id: uuid.UUID,
        invoice_number: str,
        exclude_id: uuid.UUID | None = None,
    ) -> Invoice | None:
        statement = select(Invoice).where(
            Invoice.service_id == service_id, Invoice.invoice_number == invoice_number
        )
        if exclude_id is not None:
            statement = statement.where(Invoice.id != exclude_id)
        return self.db.scalars(statement.limit(1)).one_or_none()

    def validate(
        self,
        invoice: Invoice,
        *,
        service_id: uuid.UUID,
        fields: dict[str, Any],
        taxes: Sequence[dict],
    ) -> Invoice:
        for field, value in fields.items():
            setattr(invoice, field, value)
        invoice.service_id = service_id
        invoice.status = "validated"
        invoice.error = None
        self.db.execute(delete(InvoiceTax).where(InvoiceTax.invoice_id == invoice.id))
        self.db.add_all(
            InvoiceTax(id=uuid.uuid4(), invoice_id=invoice.id, **tax) for tax in taxes
        )
        self.db.commit()
        return invoice

    def set_paid(self, invoice: Invoice, paid_at: date | None) -> None:
        invoice.paid_at = paid_at
        self.db.commit()

    def delete(self, invoice: Invoice) -> None:
        self.db.delete(invoice)
        self.db.commit()

    def validated(
        self, household_id: uuid.UUID, service_id: uuid.UUID | None = None
    ) -> Sequence[Invoice]:
        """Oldest first, by issue date (due date when the issue date is unknown)."""
        statement = select(Invoice).where(
            Invoice.household_id == household_id, Invoice.status == "validated"
        )
        if service_id is not None:
            statement = statement.where(Invoice.service_id == service_id)
        statement = statement.order_by(func.coalesce(Invoice.issued_on, Invoice.due_on), Invoice.id)
        return self.db.scalars(statement).all()

    def set_status(self, invoice: Invoice, status: str) -> None:
        invoice.status = status
        self.db.commit()

    def finish(
        self,
        invoice: Invoice,
        status: str,
        *,
        extraction: dict | None = None,
        error: str | None = None,
        fields: dict[str, Any] | None = None,
    ) -> None:
        for field, value in (fields or {}).items():
            setattr(invoice, field, value)
        invoice.status = status
        if extraction is not None:
            invoice.extraction = extraction
        invoice.error = error
        self.db.commit()

    def reset_stuck(self) -> int:
        """Background tasks die with the process: whatever was queued or
        running at a restart will never finish."""
        result = self.db.execute(
            update(Invoice)
            .where(Invoice.status.in_(("queued", "extracting")))
            .values(status="failed", error="Interrupted by a restart")
        )
        self.db.commit()
        return result.rowcount


def invoice_repository(db: Db) -> InvoiceRepository:
    return InvoiceRepository(db)


InvoiceRepo = Annotated[InvoiceRepository, Depends(invoice_repository)]
```

- [ ] **Step 6: Write the routes**

Create `backend/app/api/routes/invoices.py`:

```python
import logging
import uuid
from collections.abc import Iterator
from contextlib import suppress
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from minio.error import S3Error
from pydantic import BaseModel, Field

from app import costs
from app.api.routes.files import _measure, _unversioned, _validated_name, s3_client
from app.core.clock import Today
from app.core.config import get_settings
from app.core.household import Reader, Writer
from app.models.files import File
from app.models.providers import Invoice
from app.repositories.files import FileRepo, QuotaExceeded
from app.repositories.invoices import InvoiceRepo
from app.repositories.providers import ProviderRepo

log = logging.getLogger(__name__)

router = APIRouter(prefix="/invoices", tags=["invoices"])

Status = Literal["queued", "extracting", "to_validate", "validated", "failed"]


class TaxLine(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    amount: Decimal = Field(ge=0, max_digits=12, decimal_places=2)


class InvoiceFields(BaseModel):
    """What a person confirms: the same shape validates a queued invoice and
    creates a manual one. Total and due date are what the reminders and the
    chart need, so they are required."""

    service_id: uuid.UUID
    total: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    due_on: date
    issued_on: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    invoice_number: str | None = Field(None, max_length=100)
    consumption_qty: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=3)
    consumption_unit: str | None = Field(None, max_length=30)
    taxes: list[TaxLine] = Field(default_factory=list, max_length=20)


class InvoiceInfo(BaseModel):
    id: uuid.UUID
    status: Status
    service_id: uuid.UUID | None
    has_pdf: bool
    total: Decimal | None
    due_on: date | None
    issued_on: date | None
    period_start: date | None
    period_end: date | None
    invoice_number: str | None
    consumption_qty: Decimal | None
    consumption_unit: str | None
    paid_at: date | None
    paid: bool
    error: str | None
    extraction: dict | None
    taxes: list[TaxLine]

    @classmethod
    def of(cls, row: Invoice, service, taxes, today: date) -> "InvoiceInfo":
        paid = row.status == "validated" and costs.is_paid(
            row.paid_at, bool(service and service.auto_pay), row.due_on, today
        )
        return cls(
            id=row.id,
            status=row.status,
            service_id=row.service_id,
            has_pdf=row.file_id is not None,
            total=row.total,
            due_on=row.due_on,
            issued_on=row.issued_on,
            period_start=row.period_start,
            period_end=row.period_end,
            invoice_number=row.invoice_number,
            consumption_qty=row.consumption_qty,
            consumption_unit=row.consumption_unit,
            paid_at=row.paid_at,
            paid=paid,
            error=row.error,
            extraction=row.extraction,
            taxes=[TaxLine(name=t.name, amount=t.amount) for t in taxes],
        )


class PaidBody(BaseModel):
    paid: bool
    paid_at: date | None = None


def _services(providers: ProviderRepo, ctx) -> dict:
    # ponytail: one query for the whole household; a household has dozens of services
    return {s.id: s for s in providers.services(ctx.household_id, include_archived=True)}


def _infos(ctx, invoices: InvoiceRepo, providers: ProviderRepo, rows, today: date):
    services = _services(providers, ctx)
    taxes = invoices.taxes_by_invoice([r.id for r in rows])
    return [
        InvoiceInfo.of(r, services.get(r.service_id), taxes.get(r.id, []), today) for r in rows
    ]


def _invoice(invoices: InvoiceRepo, ctx, invoice_id: uuid.UUID) -> Invoice:
    row = invoices.get(ctx.household_id, invoice_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such invoice")
    return row


def _own_service(providers: ProviderRepo, ctx, service_id: uuid.UUID):
    row = providers.get_service(ctx.household_id, service_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such service")
    return row


def _reject_duplicate(invoices: InvoiceRepo, service_id, number, exclude_id=None) -> None:
    # ponytail: check-then-write; two racing validations of one number would
    # hit uq_invoices_service_number and surface as a 500, which a household
    # of a few people will not see. Catch IntegrityError if it ever happens.
    if number and (existing := invoices.find_duplicate(service_id, number, exclude_id)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "message": "An invoice with this number already exists for this service",
                "existing_id": str(existing.id),
            },
        )


def _store_pdf(sub: str, files: FileRepo, upload: UploadFile) -> File:
    """An invoice PDF is a normal file of the uploader's: same quota, same
    storage, same audit trail as the Files page."""
    settings = get_settings()
    name = _validated_name(upload.filename or "")
    size = _measure(upload.file)
    if size > settings.max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File is {size} bytes; the limit is {settings.max_upload_bytes}",
        )
    if files.find_by_name(sub, name, None) is not None:
        # A same-name upload to the Files page would become a new *version*;
        # two invoices must stay two files.
        name = f"{name[:-4]}-{uuid.uuid4().hex[:6]}.pdf"
    try:
        row = files.reserve(
            sub,
            name=name,
            folder_id=None,
            size_bytes=size,
            content_type="application/pdf",
            quota_bytes=settings.user_quota_bytes,
        )
    except QuotaExceeded as e:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Quota exceeded: {e.used} of {e.limit} bytes used, {e.needed} more needed",
        ) from e
    try:
        written = s3_client().put_object(
            settings.s3_bucket,
            row.object_key,
            upload.file,
            length=size,
            content_type="application/pdf",
        )
    except S3Error as e:
        files.abandon(row)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Storage is unavailable") from e
    if not written.version_id:
        files.abandon(row)
        with suppress(Exception):
            s3_client().remove_object(settings.s3_bucket, row.object_key)
        raise _unversioned()
    files.finalize(row, s3_version_id=written.version_id, actor_sub=sub)
    files.audit(sub, "upload", "file", row.id, {"name": name, "size": size})
    return row


@router.post("", response_model=list[InvoiceInfo], status_code=status.HTTP_201_CREATED)
def upload_invoices(
    ctx: Writer,
    invoices: InvoiceRepo,
    providers: ProviderRepo,
    files_repo: FileRepo,
    today: Today,
    background: BackgroundTasks,
    files: list[UploadFile],
    service_id: Annotated[uuid.UUID | None, Form()] = None,
    provider_id: Annotated[uuid.UUID | None, Form()] = None,
) -> list[InvoiceInfo]:
    """One invoice per PDF. A preselected service (or provider, when the drop
    comes from a provider's page) narrows what the AI may propose."""
    for upload in files:
        if not (upload.filename or "").lower().endswith(".pdf"):
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                f"Only PDF invoices are accepted: {upload.filename}",
            )
    if service_id is not None:
        _own_service(providers, ctx, service_id)
    elif provider_id is not None and providers.get_provider(ctx.household_id, provider_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such provider")

    use_ai = bool(get_settings().ollama_url)
    created: list[Invoice] = []
    for upload in files:
        file_row = _store_pdf(ctx.sub, files_repo, upload)
        row = invoices.create(
            ctx.household_id,
            uploaded_by=ctx.sub,
            file_id=file_row.id,
            service_id=service_id,
            status="queued" if use_ai else "to_validate",
            extraction={"provider_id": str(provider_id)} if provider_id else None,
        )
        created.append(row)
        if use_ai:
            from app import invoice_extraction

            background.add_task(invoice_extraction.extract, row.id)
    return _infos(ctx, invoices, providers, created, today)


@router.post("/manual", response_model=InvoiceInfo, status_code=status.HTTP_201_CREATED)
def create_manual_invoice(
    ctx: Writer, invoices: InvoiceRepo, providers: ProviderRepo, today: Today, body: InvoiceFields
) -> InvoiceInfo:
    service = _own_service(providers, ctx, body.service_id)
    _reject_duplicate(invoices, service.id, body.invoice_number)
    fields = body.model_dump(exclude={"service_id", "taxes"})
    row = invoices.create(
        ctx.household_id,
        uploaded_by=ctx.sub,
        file_id=None,
        service_id=service.id,
        status="validated",
        taxes=[t.model_dump() for t in body.taxes],
        **fields,
    )
    return _infos(ctx, invoices, providers, [row], today)[0]


@router.get("", response_model=list[InvoiceInfo])
def list_invoices(
    ctx: Reader,
    invoices: InvoiceRepo,
    providers: ProviderRepo,
    today: Today,
    status: Status | None = None,
    service_id: uuid.UUID | None = None,
    unpaid: bool = False,
) -> list[InvoiceInfo]:
    rows = invoices.list(ctx.household_id, status=status, service_id=service_id)
    infos = _infos(ctx, invoices, providers, rows, today)
    if unpaid:
        infos = [i for i in infos if i.status == "validated" and not i.paid]
    return infos


@router.get("/{invoice_id}", response_model=InvoiceInfo)
def get_invoice(
    ctx: Reader, invoices: InvoiceRepo, providers: ProviderRepo, today: Today, invoice_id: uuid.UUID
) -> InvoiceInfo:
    row = _invoice(invoices, ctx, invoice_id)
    return _infos(ctx, invoices, providers, [row], today)[0]


@router.get("/{invoice_id}/pdf")
def invoice_pdf(
    ctx: Reader, invoices: InvoiceRepo, files: FileRepo, invoice_id: uuid.UUID
) -> StreamingResponse:
    """The PDF lives in the uploader's files, which other members cannot read
    through /api/files; this endpoint is the household's way to it."""
    row = _invoice(invoices, ctx, invoice_id)
    file_row = files.get(row.uploaded_by, row.file_id) if row.file_id else None
    if file_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The PDF is missing")
    try:
        obj = s3_client().get_object(get_settings().s3_bucket, file_row.object_key)
    except S3Error as e:
        if e.code == "NoSuchKey":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "The PDF is missing") from e
        raise

    def chunks() -> Iterator[bytes]:
        try:
            yield from obj.stream(64 * 1024)
        finally:
            obj.close()
            obj.release_conn()

    return StreamingResponse(
        chunks(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline",
            "Content-Length": obj.headers["Content-Length"],
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )


@router.post("/{invoice_id}/validate", response_model=InvoiceInfo)
def validate_invoice(
    ctx: Writer,
    invoices: InvoiceRepo,
    providers: ProviderRepo,
    today: Today,
    invoice_id: uuid.UUID,
    body: InvoiceFields,
) -> InvoiceInfo:
    row = _invoice(invoices, ctx, invoice_id)
    if row.status not in ("to_validate", "failed"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This invoice is not awaiting validation")
    service = _own_service(providers, ctx, body.service_id)
    _reject_duplicate(invoices, service.id, body.invoice_number, exclude_id=row.id)
    invoices.validate(
        row,
        service_id=service.id,
        fields=body.model_dump(exclude={"service_id", "taxes"}),
        taxes=[t.model_dump() for t in body.taxes],
    )
    return _infos(ctx, invoices, providers, [row], today)[0]


@router.post("/{invoice_id}/paid", response_model=InvoiceInfo)
def set_paid(
    ctx: Writer,
    invoices: InvoiceRepo,
    providers: ProviderRepo,
    today: Today,
    invoice_id: uuid.UUID,
    body: PaidBody,
) -> InvoiceInfo:
    row = _invoice(invoices, ctx, invoice_id)
    if row.status != "validated":
        raise HTTPException(status.HTTP_409_CONFLICT, "Validate the invoice first")
    invoices.set_paid(row, (body.paid_at or today) if body.paid else None)
    return _infos(ctx, invoices, providers, [row], today)[0]


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_invoice(ctx: Writer, invoices: InvoiceRepo, invoice_id: uuid.UUID) -> Response:
    """The invoice goes; its PDF stays in the uploader's Files."""
    invoices.delete(_invoice(invoices, ctx, invoice_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

Edit `backend/app/api/router.py`: import `invoices` and add `api_router.include_router(invoices.router)`.

- [ ] **Step 7: Run everything, regenerate the snapshot, lint**

Run: `cd backend && .venv/bin/python -m pytest -q && cd .. && make snapshot && cd backend && .venv/bin/python -m pytest -m "unit or regression" -q && .venv/bin/python -m ruff format . && .venv/bin/python -m ruff check .`
Expected: all PASS, ruff clean. If a test fails on `FakeFileRepository` lacking a method the route calls (`find_by_name`, `reserve`, `finalize`, `abandon`, `audit`, `get` all exist), fix the route, not the fake.

- [ ] **Step 8: Commit (only if the user has asked for commits)**

```bash
git add backend
git commit -m "feat: invoices with PDF upload, validation and payment state"
```

---

### Task 6: Reading invoices with Ollama

**Files:**
- Create: `backend/app/invoice_extraction.py`
- Modify: `backend/app/summary.py` (`ask_ollama`), `backend/app/main.py` (restart sweep)
- Test: `backend/tests/unit/test_invoice_extraction.py`, `backend/tests/unit/test_main_lifespan.py`, `backend/tests/integration/test_invoice_extraction.py`

**Interfaces:**
- Consumes: `summary._read`, `summary.pdf_text`, `summary.MAX_READ_BYTES`, `summary.ask_ollama`; `InvoiceRepository` (`by_id`, `set_status`, `finish`, `reset_stuck`), `ProviderRepository` (`list_providers`, `services`), `session_factory`.
- Produces (`app/invoice_extraction.py`):
  - `clean(raw: dict) -> dict` returning `{"provider", "service", "total", "issued_on", "due_on", "period_start", "period_end", "invoice_number", "consumption_qty", "consumption_unit", "taxes"}` with `Decimal`/`date`/`str`/`None` values and `taxes: list[{"name": str, "amount": Decimal}]`
  - `normalize(text: str) -> str`
  - `build(raw, providers, services, preselect_provider_id=None, preselect_service_id=None) -> tuple[dict, dict]`: `(fields, extraction)`; `providers` = `[(id, name)]`, `services` = `[(id, provider_id, name, category)]`; `fields` holds only non-null invoice columns; `extraction = {"raw": {...json-safe...}, "candidates": {"provider": {"id","name"}|None, "service": {...}|None, "new_provider": str|None, "new_service": str|None}, "provider_id": str|None}`
  - `extract(invoice_id: uuid.UUID) -> None` (background job; never raises)
- `summary.ask_ollama(prompt, images=(), *, as_json=False)`: unchanged for existing callers; `as_json=True` sends `"format": "json"` and does not truncate.
- `app.main` calls `InvoiceRepository.reset_stuck()` at startup through `reset_interrupted()`.

- [ ] **Step 1: Write the failing unit tests**

Create `backend/tests/unit/test_invoice_extraction.py`:

```python
"""Unit — cleaning the model's answer and matching it with the household's providers."""

import uuid
from datetime import date
from decimal import Decimal as D

import pytest

from app import invoice_extraction as ie

HYDRO, BELL = uuid.UUID(int=1), uuid.UUID(int=2)
ELEC, NET, MOBILE = uuid.UUID(int=11), uuid.UUID(int=12), uuid.UUID(int=13)
PROVIDERS = [(HYDRO, "Hydro-Québec"), (BELL, "Bell")]
SERVICES = [
    (ELEC, HYDRO, "Électricité", "electricity"),
    (NET, BELL, "Internet", "internet"),
    (MOBILE, BELL, "Mobile", "phone"),
]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (12.5, D("12.5")),
        ("1 240,50 $", D("1240.50")),
        ("1,240.50", D("1240.50")),
        ("114.98", D("114.98")),
        ("", None),
        ("n/a", None),
        (None, None),
        (True, None),
        (float("nan"), None),
        ([1], None),
    ],
)
def test_money_is_read_leniently_and_garbage_becomes_none(raw, expected):
    assert ie._money(raw) == expected


def test_dates_must_be_iso_and_anything_else_is_dropped():
    assert ie._date("2026-10-01") == date(2026, 10, 1)
    assert ie._date("2026-10-01T00:00:00") == date(2026, 10, 1)
    assert ie._date("1er octobre 2026") is None
    assert ie._date("2026-13-40") is None
    assert ie._date(20261001) is None


def test_clean_keeps_good_fields_and_drops_bad_ones():
    cleaned = ie.clean(
        {
            "provider": "  Bell  ",
            "service": "Internet",
            "total": "114,98 $",
            "due_on": "pas une date",
            "issued_on": "2026-10-01",
            "invoice_number": "A-1",
            "consumption_qty": "1 240",
            "consumption_unit": "kWh",
            "taxes": [
                {"name": "TPS", "amount": "5.00"},
                {"name": "bad", "amount": "x"},
                "not a dict",
                {"amount": 9.98},
            ],
        }
    )

    assert cleaned["provider"] == "Bell"
    assert cleaned["total"] == D("114.98")
    assert cleaned["due_on"] is None
    assert cleaned["issued_on"] == date(2026, 10, 1)
    assert cleaned["consumption_qty"] == D("1240")
    assert cleaned["taxes"] == [
        {"name": "TPS", "amount": D("5.00")},
        {"name": "Taxe", "amount": D("9.98")},
    ]


def test_clean_survives_a_non_dict_taxes_and_missing_keys():
    assert ie.clean({"taxes": "none"})["taxes"] == []
    assert ie.clean({})["total"] is None


def test_normalize_drops_accents_case_and_punctuation():
    assert ie.normalize("  Hydro-Québec  ") == "hydro quebec"


def test_build_matches_provider_and_service_by_name():
    fields, extraction = ie.build(
        {"provider": "BELL CANADA", "service": "internet", "total": 100, "due_on": "2026-11-01"},
        PROVIDERS,
        SERVICES,
    )

    assert fields == {"total": D("100"), "due_on": date(2026, 11, 1)}
    candidates = extraction["candidates"]
    assert candidates["provider"] == {"id": str(BELL), "name": "Bell"}
    assert candidates["service"] == {"id": str(NET), "name": "Internet"}
    assert (candidates["new_provider"], candidates["new_service"]) == (None, None)


def test_an_unknown_provider_is_proposed_as_new():
    _, extraction = ie.build({"provider": "Vidéotron", "service": "Internet"}, PROVIDERS, SERVICES)

    candidates = extraction["candidates"]
    assert candidates["provider"] is None and candidates["service"] is None
    assert (candidates["new_provider"], candidates["new_service"]) == ("Vidéotron", "Internet")


def test_a_lone_service_is_proposed_when_the_model_names_none():
    _, extraction = ie.build({"provider": "Hydro-Québec"}, PROVIDERS, SERVICES)

    assert extraction["candidates"]["service"]["id"] == str(ELEC)


def test_an_ambiguous_service_is_left_for_the_person_to_pick():
    _, extraction = ie.build({"provider": "Bell"}, PROVIDERS, SERVICES)

    assert extraction["candidates"]["service"] is None


def test_a_preselected_provider_limits_the_matching():
    _, extraction = ie.build(
        {"provider": "Hydro-Québec", "service": "Internet"},
        PROVIDERS,
        SERVICES,
        preselect_provider_id=BELL,
    )

    assert extraction["candidates"]["provider"]["id"] == str(BELL)
    assert extraction["candidates"]["service"]["id"] == str(NET)
    assert extraction["provider_id"] == str(BELL)


def test_a_preselected_service_wins_over_the_model():
    _, extraction = ie.build(
        {"provider": "Hydro-Québec", "service": "Électricité"},
        PROVIDERS,
        SERVICES,
        preselect_service_id=MOBILE,
    )

    candidates = extraction["candidates"]
    assert candidates["provider"]["id"] == str(BELL)
    assert candidates["service"]["id"] == str(MOBILE)


def test_the_raw_part_is_json_safe():
    import json

    _, extraction = ie.build(
        {"total": "10", "due_on": "2026-11-01", "taxes": [{"name": "TPS", "amount": "1"}]},
        PROVIDERS,
        SERVICES,
    )

    json.dumps(extraction)
    assert extraction["raw"]["total"] == "10"
    assert extraction["raw"]["taxes"] == [{"name": "TPS", "amount": "1"}]
```

Create `backend/tests/unit/test_main_lifespan.py`:

```python
"""Unit — the restart sweep runs at startup and never blocks it."""

from fastapi.testclient import TestClient

from app import main


def test_startup_runs_the_sweep(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "reset_interrupted", lambda: calls.append(1))

    with TestClient(main.create_app()):
        pass

    assert calls == [1]


def test_a_database_outage_does_not_stop_the_app(monkeypatch):
    def boom():
        raise RuntimeError("database down")

    monkeypatch.setattr(main, "reset_interrupted", boom)

    with TestClient(main.create_app()) as client:
        assert client.get("/api/health").status_code == 200
```

- [ ] **Step 2: Write the failing integration test for the background job**

Create `backend/tests/integration/test_invoice_extraction.py`:

```python
"""Integration — extract() end to end against real PostgreSQL, with the PDF read and
the model faked (Ollama and a real PDF are not what is under test)."""

import json
import uuid
from datetime import UTC, datetime

import pytest

from app import invoice_extraction as ie
from app.models.files import File
from app.repositories.files import FileRepository
from app.repositories.households import HouseholdRepository
from app.repositories.invoices import InvoiceRepository
from app.repositories.providers import ProviderRepository


@pytest.fixture
def queued(db):
    household = HouseholdRepository(db).create("alice", "Maison").id
    providers = ProviderRepository(db)
    bell = providers.create_provider(
        household, name="Bell", website=None, phone=None, email=None, notes=None
    )
    providers.create_service(
        household,
        bell.id,
        name="Internet",
        category="internet",
        account_number=None,
        contract_start=None,
        contract_end=None,
        renewal_reminder_days=None,
        expected_monthly_cost=None,
        auto_pay=False,
        alert_threshold_pct=20,
        archived=False,
    )
    files = FileRepository(db)
    pdf = files.reserve(
        "alice",
        name="facture.pdf",
        folder_id=None,
        size_bytes=10,
        content_type="application/pdf",
        quota_bytes=10**9,
    )
    files.finalize(pdf, s3_version_id="v1", actor_sub="alice")
    invoice = InvoiceRepository(db).create(
        household, uploaded_by="alice", file_id=pdf.id, service_id=None, status="queued"
    )
    return invoice.id


def reread(db, invoice_id):
    db.expire_all()
    return InvoiceRepository(db).by_id(invoice_id)


def test_a_readable_invoice_lands_in_the_validation_queue(queued, db, monkeypatch):
    monkeypatch.setattr(ie, "_pdf_text", lambda key, size: "Bell Internet 114,98 $")
    monkeypatch.setattr(
        ie,
        "_ask",
        lambda text: {
            "provider": "Bell",
            "service": "Internet",
            "total": "114,98 $",
            "due_on": "2026-11-01",
            "invoice_number": "A-1",
            "taxes": [{"name": "TPS", "amount": "5.00"}],
        },
    )

    ie.extract(queued)

    row = reread(db, queued)
    assert (row.status, str(row.total), row.invoice_number) == ("to_validate", "114.98", "A-1")
    assert row.extraction["candidates"]["provider"]["name"] == "Bell"
    assert row.extraction["raw"]["taxes"] == [{"name": "TPS", "amount": "5.00"}]
    json.dumps(row.extraction)


@pytest.mark.parametrize(
    ("text", "ask", "message"),
    [
        ("", lambda t: {}, "no readable text"),
        ("some text", lambda t: (_ for _ in ()).throw(ValueError("not json")), "not json"),
    ],
)
def test_an_unreadable_invoice_fails_with_a_message_and_can_still_be_filled_by_hand(
    queued, db, monkeypatch, text, ask, message
):
    monkeypatch.setattr(ie, "_pdf_text", lambda key, size: text)
    monkeypatch.setattr(ie, "_ask", ask)

    ie.extract(queued)

    row = reread(db, queued)
    assert row.status == "failed"
    assert message in row.error


def test_a_missing_pdf_fails_cleanly(queued, db, monkeypatch):
    db.get(File, reread(db, queued).file_id).deleted_at = datetime.now(UTC)
    db.commit()

    ie.extract(queued)

    assert reread(db, queued).status == "failed"


def test_an_unknown_invoice_id_is_ignored(db):
    ie.extract(uuid.uuid4())
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_invoice_extraction.py tests/unit/test_main_lifespan.py tests/integration/test_invoice_extraction.py -v`
Expected: FAIL (`ImportError: cannot import name 'invoice_extraction'`, and `main` has no `reset_interrupted`).

- [ ] **Step 4: Teach `ask_ollama` to ask for JSON**

In `backend/app/summary.py`, replace `ask_ollama` with:

```python
def ask_ollama(prompt: str, images: Sequence[bytes] = (), *, as_json: bool = False) -> str:
    """`as_json` asks Ollama for a JSON object (`format: json`) and keeps the
    whole answer: truncating it to MAX_SUMMARY would cut the JSON in half."""
    settings = get_settings()
    body = {"model": settings.ollama_model, "prompt": prompt, "stream": False}
    if as_json:
        body["format"] = "json"
    if images:
        body["images"] = [base64.b64encode(image).decode() for image in images]
    request = urllib.request.Request(
        f"{settings.ollama_url.rstrip('/')}/api/generate",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    # A big model on a Mac takes its time; this only ever holds a background task.
    with urllib.request.urlopen(request, timeout=600) as response:
        reply = json.load(response)["response"]
    answer = _THINKING.sub("", reply).strip()
    return answer if as_json else answer[:MAX_SUMMARY]
```

(The existing summary tests monkeypatch `ask_ollama` with two positional parameters and `summarize` still calls `ask_ollama(*found)`, so nothing else changes.)

- [ ] **Step 5: Write the extraction module**

Create `backend/app/invoice_extraction.py`:

```python
"""Reading an uploaded invoice PDF with Ollama, after the response is sent.

Best effort, like app.summary: whatever goes wrong the invoice is still there,
marked `failed`, and the person fills it in by hand. The model's answer is never
trusted: every field is cleaned on its own, a bad one is dropped, and nothing
becomes a validated invoice without a human pressing the button.
"""

import json
import logging
import re
import unicodedata
import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app import summary
from app.core.db import session_factory
from app.models.files import File
from app.repositories.invoices import InvoiceRepository
from app.repositories.providers import ProviderRepository

log = logging.getLogger(__name__)

PROMPT = (
    "You read a household service invoice (utility, telecom, insurance...). "
    "Reply with ONE JSON object and nothing else, using exactly these keys, "
    "with null for anything the invoice does not state: "
    '"provider" (the company), "service" (what is billed, e.g. Internet or Electricity), '
    '"total" (the amount due, a number), "issued_on", "due_on", "period_start", '
    '"period_end" (dates as YYYY-MM-DD), "invoice_number", "consumption_qty" '
    '(a number), "consumption_unit" (e.g. kWh, GB), and "taxes" '
    '(a list of {"name", "amount"}). The invoice text follows.\n\n'
)

Provider = tuple[uuid.UUID, str]
ServiceRow = tuple[uuid.UUID, uuid.UUID, str, str]  # id, provider_id, name, category


# --- cleaning ---------------------------------------------------------------


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip()[:limit] or None


def _money(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = Decimal(str(value))
    elif isinstance(value, str):
        # Drops spaces, non-breaking spaces and currency signs; a lone comma is
        # a decimal comma, a comma next to a point is a thousands separator.
        text = re.sub(r"[^\d,.\-]", "", value)
        text = text.replace(",", "") if "," in text and "." in text else text.replace(",", ".")
        try:
            result = Decimal(text)
        except InvalidOperation:
            return None
    else:
        return None
    return result if result.is_finite() else None


def _date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def clean(raw: dict) -> dict:
    taxes = []
    for line in raw.get("taxes") if isinstance(raw.get("taxes"), list) else []:
        if not isinstance(line, dict) or (amount := _money(line.get("amount"))) is None:
            continue
        taxes.append({"name": _text(line.get("name"), 100) or "Taxe", "amount": amount})
    return {
        "provider": _text(raw.get("provider"), 200),
        "service": _text(raw.get("service"), 200),
        "total": _money(raw.get("total")),
        "issued_on": _date(raw.get("issued_on")),
        "due_on": _date(raw.get("due_on")),
        "period_start": _date(raw.get("period_start")),
        "period_end": _date(raw.get("period_end")),
        "invoice_number": _text(raw.get("invoice_number"), 100),
        "consumption_qty": _money(raw.get("consumption_qty")),
        "consumption_unit": _text(raw.get("consumption_unit"), 30),
        "taxes": taxes[:20],
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, (date, Decimal)):
        return value.isoformat() if isinstance(value, date) else str(value)
    return value


# --- matching ---------------------------------------------------------------


def normalize(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", plain).strip()


def _match(name: str | None, rows: Sequence[tuple[uuid.UUID, str]]):
    """The one row whose name is, or contains, or is contained in `name`."""
    wanted = normalize(name or "")
    if not wanted:
        return None
    exact = [r for r in rows if normalize(r[1]) == wanted]
    if len(exact) == 1:
        return exact[0]
    loose = [
        r
        for r in rows
        if len(normalize(r[1])) >= 3 and (normalize(r[1]) in wanted or wanted in normalize(r[1]))
    ]
    return loose[0] if len(loose) == 1 else None


def build(
    raw: dict,
    providers: Sequence[Provider],
    services: Sequence[ServiceRow],
    preselect_provider_id: uuid.UUID | None = None,
    preselect_service_id: uuid.UUID | None = None,
) -> tuple[dict, dict]:
    data = clean(raw)
    chosen_service = next((s for s in services if s[0] == preselect_service_id), None)
    if chosen_service is not None:
        provider = next((p for p in providers if p[0] == chosen_service[1]), None)
    elif preselect_provider_id is not None:
        provider = next((p for p in providers if p[0] == preselect_provider_id), None)
    else:
        provider = _match(data["provider"], providers)

    pool = [s for s in services if provider is not None and s[1] == provider[0]]
    if chosen_service is None:
        found = _match(data["service"], [(s[0], s[2]) for s in pool]) or _match(
            data["service"], [(s[0], s[3]) for s in pool]
        )
        chosen_service = next((s for s in pool if found and s[0] == found[0]), None)
        if chosen_service is None and len(pool) == 1 and not data["service"]:
            chosen_service = pool[0]

    taxes = data.pop("taxes")
    provider_name, service_name = data.pop("provider"), data.pop("service")
    fields = {k: v for k, v in data.items() if v is not None}
    extraction = {
        "raw": _jsonable({**data, "provider": provider_name, "service": service_name, "taxes": taxes}),
        "provider_id": str(preselect_provider_id) if preselect_provider_id else None,
        "candidates": {
            "provider": {"id": str(provider[0]), "name": provider[1]} if provider else None,
            "service": (
                {"id": str(chosen_service[0]), "name": chosen_service[2]}
                if chosen_service
                else None
            ),
            "new_provider": provider_name if provider is None else None,
            "new_service": service_name if chosen_service is None and service_name else None,
        },
    }
    return fields, extraction


# --- the background job -------------------------------------------------------


def _pdf_text(object_key: str, size: int) -> str:
    if size > summary.MAX_READ_BYTES:
        raise ValueError("the PDF is too big to read")
    return summary.pdf_text(summary._read(object_key, summary.MAX_READ_BYTES))


def _ask(text: str) -> dict:
    answer = json.loads(summary.ask_ollama(PROMPT + text[: summary.TEXT_CHARS], as_json=True))
    if not isinstance(answer, dict):
        raise ValueError("the model did not answer with a JSON object")
    return answer


def extract(invoice_id: uuid.UUID) -> None:
    """Runs as a BackgroundTask: the request's session is closed by now, so
    this opens its own. Never raises."""
    try:
        with session_factory()() as db:
            invoices = InvoiceRepository(db)
            invoice = invoices.by_id(invoice_id)
            if invoice is None:
                return
            invoices.set_status(invoice, "extracting")
            try:
                file_row = db.get(File, invoice.file_id) if invoice.file_id else None
                if file_row is None or file_row.deleted_at is not None:
                    raise ValueError("the PDF is missing")
                text = _pdf_text(file_row.object_key, file_row.size_bytes)
                if not text.strip():
                    raise ValueError("no readable text: a scanned PDF?")
                raw = _ask(text)

                catalog = ProviderRepository(db)
                providers = [(p.id, p.name) for p in catalog.list_providers(invoice.household_id)]
                services = [
                    (s.id, s.provider_id, s.name, s.category)
                    for s in catalog.services(invoice.household_id)
                ]
                preselect = (invoice.extraction or {}).get("provider_id")
                fields, extraction = build(
                    raw,
                    providers,
                    services,
                    preselect_provider_id=uuid.UUID(preselect) if preselect else None,
                    preselect_service_id=invoice.service_id,
                )
                invoices.finish(invoice, "to_validate", extraction=extraction, fields=fields)
            except Exception as e:
                log.exception("reading invoice %s failed", invoice_id)
                db.rollback()
                invoices.finish(invoice, "failed", error=str(e)[:300] or type(e).__name__)
    except Exception:
        log.exception("could not record the outcome for invoice %s", invoice_id)
```

- [ ] **Step 6: Add the restart sweep to `main.py`**

Replace `backend/app/main.py` with:

```python
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.db import session_factory
from app.repositories.invoices import InvoiceRepository

log = logging.getLogger(__name__)


def reset_interrupted() -> None:
    """Background reads die with the process: whatever was queued or running at
    the last stop will never finish, so it must not sit in 'extracting' forever."""
    with session_factory()() as db:
        InvoiceRepository(db).reset_stuck()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        reset_interrupted()
    except Exception:
        # The database may simply not be up yet; the API must still start.
        log.exception("could not reset interrupted invoice reads")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name, version=settings.version, debug=settings.debug, lifespan=lifespan
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)
    return app


app = create_app()
```

- [ ] **Step 7: Run everything and lint**

Run: `cd backend && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff format . && .venv/bin/python -m ruff check .`
Expected: all PASS, ruff clean. The first `test_money` parameter `float("nan")` relies on `Decimal.is_finite()`; if `[1]` (a list) reaches the `else` branch it returns `None` as intended.

- [ ] **Step 8: Commit (only if the user has asked for commits)**

```bash
git add backend
git commit -m "feat: read invoices with Ollama and sweep interrupted reads at startup"
```

---

### Task 7: Upcoming dues and cost history routes

**Files:**
- Create: `backend/app/api/routes/costs.py`
- Modify: `backend/app/api/router.py`, `backend/tests/regression/openapi.snapshot.json`
- Test: `backend/tests/unit/test_costs_routes.py`

**Interfaces:**
- Consumes: `Reader`, `ProviderRepo`, `InvoiceRepo`, `Today`, `costs.series`, `costs.is_paid`, `costs.monthly_totals`, `costs.month_of`.
- Produces:
  - `GET /api/upcoming` → `{"invoices": [{invoice_id, service_id, provider_name, service_name, total, due_on, overdue}], "renewals": [{service_id, provider_name, service_name, contract_end, days_left}]}`. Invoices: validated and not paid, sorted by `due_on` ascending (overdue first by construction). Renewals: non-archived services with `contract_end` and `renewal_reminder_days` where `0 <= days_left <= renewal_reminder_days`, sorted by `contract_end`.
  - `GET /api/services/{service_id}/costs` → `{"expected_monthly_cost": str|null, "threshold_pct": int, "points": [{invoice_id, month, total, reference, flagged}]}`. A point's date is `issued_on or due_on`.
  - `GET /api/costs/monthly` → `{"months": [{"month": "2026-09", "total": str}]}` over all validated invoices of the household.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_costs_routes.py`:

```python
"""Unit — upcoming dues, per-service cost history and monthly totals."""

from datetime import date
from decimal import Decimal as D

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token
from tests.unit.test_invoices import service_for

client = TestClient(app)


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def invoice(home, service, total, due, issued=None, **overrides):
    return home.ledger.invoices.create(
        home.id,
        uploaded_by="user-1",
        file_id=None,
        service_id=service.id,
        status="validated",
        total=D(total),
        due_on=due,
        issued_on=issued,
        **overrides,
    )


def test_upcoming_lists_unpaid_invoices_by_due_date_and_flags_the_overdue(home, clock):
    service = service_for(home)
    invoice(home, service, "10.00", date(2026, 11, 1))
    invoice(home, service, "20.00", date(2026, 10, 1))
    paid = invoice(home, service, "30.00", date(2026, 9, 1))
    paid.paid_at = date(2026, 9, 2)

    body = client.get("/api/upcoming", headers=auth()).json()

    assert [(i["total"], i["overdue"]) for i in body["invoices"]] == [("20.00", True), ("10.00", False)]
    assert body["invoices"][0]["provider_name"] == "Bell"
    assert body["invoices"][0]["service_name"] == "Internet"


def test_upcoming_skips_auto_pay_invoices_already_past_due(home, clock):
    service = service_for(home, auto_pay=True)
    invoice(home, service, "10.00", date(2026, 10, 1))
    invoice(home, service, "20.00", date(2026, 11, 1))

    body = client.get("/api/upcoming", headers=auth()).json()

    assert [i["total"] for i in body["invoices"]] == ["20.00"]


def test_upcoming_ignores_invoices_still_awaiting_validation(home, clock):
    service = service_for(home)
    home.ledger.invoices.create(
        home.id, uploaded_by="user-1", file_id=None, service_id=service.id, status="to_validate"
    )

    assert client.get("/api/upcoming", headers=auth()).json()["invoices"] == []


def test_renewals_appear_inside_their_reminder_window_only(home, clock):
    service_for(home, name="Soon", contract_end=date(2026, 10, 25), renewal_reminder_days=30)
    service_for(home, name="Far", contract_end=date(2026, 12, 31), renewal_reminder_days=30)
    service_for(home, name="Over", contract_end=date(2026, 10, 1), renewal_reminder_days=30)
    service_for(home, name="NoReminder", contract_end=date(2026, 10, 12))
    service_for(
        home, name="Archived", contract_end=date(2026, 10, 12), renewal_reminder_days=30, archived=True
    )

    renewals = client.get("/api/upcoming", headers=auth()).json()["renewals"]

    assert [(r["service_name"], r["days_left"]) for r in renewals] == [("Soon", 15)]


def test_the_cost_history_flags_an_overspend_against_the_mean_of_the_previous_invoices(home):
    service = service_for(home, expected_monthly_cost=D("50.00"))
    for month, total in enumerate(("100", "100", "100", "200"), start=1):
        invoice(home, service, total, date(2026, month, 28), issued=date(2026, month, 1))

    body = client.get(f"/api/services/{service.id}/costs", headers=auth()).json()

    assert (body["expected_monthly_cost"], body["threshold_pct"]) == ("50.00", 20)
    assert [p["month"] for p in body["points"]] == ["2026-01", "2026-02", "2026-03", "2026-04"]
    # Three invoices of history make the mean (100) the reference for the fourth.
    assert [p["flagged"] for p in body["points"]] == [True, True, True, True]
    assert body["points"][3]["reference"] == "100.00"


def test_a_bill_at_the_threshold_is_not_flagged(home):
    service = service_for(home)
    for month, total in enumerate(("100", "100", "100", "120"), start=1):
        invoice(home, service, total, date(2026, month, 28), issued=date(2026, month, 1))

    flags = [p["flagged"] for p in client.get(f"/api/services/{service.id}/costs", headers=auth()).json()["points"]]

    assert flags[-1] is False


def test_the_threshold_is_the_services_own(home):
    service = service_for(home, alert_threshold_pct=5)
    for month, total in enumerate(("100", "100", "100", "110"), start=1):
        invoice(home, service, total, date(2026, month, 28), issued=date(2026, month, 1))

    points = client.get(f"/api/services/{service.id}/costs", headers=auth()).json()["points"]

    assert points[-1]["flagged"] is True


def test_costs_of_a_foreign_service_are_a_404(home):
    other = home.ledger.households.create("dave", "Chalet")
    foreign = service_for(type("H", (), {"id": other.id, "ledger": home.ledger}))

    assert client.get(f"/api/services/{foreign.id}/costs", headers=auth()).status_code == 404


def test_monthly_totals_sum_every_service(home):
    one, two = service_for(home), service_for(home, name="Mobile")
    invoice(home, one, "10.50", date(2026, 9, 28), issued=date(2026, 9, 1))
    invoice(home, two, "20", date(2026, 9, 30), issued=date(2026, 9, 5))
    invoice(home, one, "5", date(2026, 8, 28))

    months = client.get("/api/costs/monthly", headers=auth()).json()["months"]

    assert months == [{"month": "2026-08", "total": "5.00"}, {"month": "2026-09", "total": "30.50"}]


def test_a_viewer_can_read_all_three(home, clock):
    home.ledger.households.add_member(home.id, "viewer", "viewer")
    service = service_for(home)

    for path in ("/api/upcoming", f"/api/services/{service.id}/costs", "/api/costs/monthly"):
        assert client.get(path, headers=auth("viewer")).status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_costs_routes.py -v`
Expected: FAIL (404 on every route).

- [ ] **Step 3: Write the routes**

Create `backend/app/api/routes/costs.py`:

```python
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app import costs
from app.core.clock import Today
from app.core.household import Reader
from app.repositories.invoices import InvoiceRepo
from app.repositories.providers import ProviderRepo

router = APIRouter(tags=["costs"])

CENTS = Decimal("0.01")


class UpcomingInvoice(BaseModel):
    invoice_id: uuid.UUID
    service_id: uuid.UUID
    provider_name: str
    service_name: str
    total: Decimal
    due_on: date
    overdue: bool


class Renewal(BaseModel):
    service_id: uuid.UUID
    provider_name: str
    service_name: str
    contract_end: date
    days_left: int


class Upcoming(BaseModel):
    invoices: list[UpcomingInvoice]
    renewals: list[Renewal]


class CostPoint(BaseModel):
    invoice_id: uuid.UUID
    month: str
    total: Decimal
    reference: Decimal | None
    flagged: bool


class ServiceCosts(BaseModel):
    expected_monthly_cost: Decimal | None
    threshold_pct: int
    points: list[CostPoint]


class MonthTotal(BaseModel):
    month: str
    total: Decimal


class MonthlyCosts(BaseModel):
    months: list[MonthTotal]


@router.get("/upcoming", response_model=Upcoming)
def upcoming(
    ctx: Reader, providers: ProviderRepo, invoices: InvoiceRepo, today: Today
) -> Upcoming:
    names = {p.id: p.name for p in providers.list_providers(ctx.household_id)}
    services = {s.id: s for s in providers.services(ctx.household_id, include_archived=True)}

    due = []
    for row in invoices.validated(ctx.household_id):
        service = services.get(row.service_id)
        if service is None or row.due_on is None or row.total is None:
            continue
        if costs.is_paid(row.paid_at, service.auto_pay, row.due_on, today):
            continue
        due.append(
            UpcomingInvoice(
                invoice_id=row.id,
                service_id=service.id,
                provider_name=names[service.provider_id],
                service_name=service.name,
                total=row.total,
                due_on=row.due_on,
                overdue=row.due_on < today,
            )
        )
    due.sort(key=lambda i: (i.due_on, str(i.invoice_id)))

    renewals = []
    for service in services.values():
        if service.archived or service.contract_end is None:
            continue
        if service.renewal_reminder_days is None:
            continue
        days_left = (service.contract_end - today).days
        if 0 <= days_left <= service.renewal_reminder_days:
            renewals.append(
                Renewal(
                    service_id=service.id,
                    provider_name=names[service.provider_id],
                    service_name=service.name,
                    contract_end=service.contract_end,
                    days_left=days_left,
                )
            )
    renewals.sort(key=lambda r: (r.contract_end, str(r.service_id)))
    return Upcoming(invoices=due, renewals=renewals)


@router.get("/services/{service_id}/costs", response_model=ServiceCosts)
def service_costs(
    ctx: Reader, providers: ProviderRepo, invoices: InvoiceRepo, service_id: uuid.UUID
) -> ServiceCosts:
    service = providers.get_service(ctx.household_id, service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such service")
    rows = [
        (r.id, r.issued_on or r.due_on, r.total)
        for r in invoices.validated(ctx.household_id, service_id)
        if r.total is not None and (r.issued_on or r.due_on) is not None
    ]
    points = costs.series(rows, service.expected_monthly_cost, service.alert_threshold_pct)
    return ServiceCosts(
        expected_monthly_cost=service.expected_monthly_cost,
        threshold_pct=service.alert_threshold_pct,
        points=[
            CostPoint(
                invoice_id=p.invoice_id,
                month=costs.month_of(p.when),
                total=p.total,
                reference=p.reference,
                flagged=p.flagged,
            )
            for p in points
        ],
    )


@router.get("/costs/monthly", response_model=MonthlyCosts)
def monthly_costs(ctx: Reader, invoices: InvoiceRepo) -> MonthlyCosts:
    rows = [
        (r.issued_on or r.due_on, r.total)
        for r in invoices.validated(ctx.household_id)
        if r.total is not None and (r.issued_on or r.due_on) is not None
    ]
    return MonthlyCosts(
        months=[
            MonthTotal(month=month, total=total.quantize(CENTS))
            for month, total in costs.monthly_totals(rows)
        ]
    )
```

Edit `backend/app/api/router.py`: import `costs as cost_routes` (`from app.api.routes import costs as cost_routes, ...`, keeping ruff's import order) and `api_router.include_router(cost_routes.router)`.

Note on the first alert test: `expected_monthly_cost=50` with `100, 100, 100`: the first three compare to the expected cost (50 × 1.2 = 60 < 100, so flagged), and the fourth (200) compares to the mean 100.00 (limit 120, flagged). The test asserts exactly that.

- [ ] **Step 4: Run everything, regenerate the snapshot, lint**

Run: `cd backend && .venv/bin/python -m pytest -q && cd .. && make snapshot && cd backend && .venv/bin/python -m pytest -m "unit or regression" -q && .venv/bin/python -m ruff format . && .venv/bin/python -m ruff check .`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit (only if the user has asked for commits)**

```bash
git add backend
git commit -m "feat: upcoming dues and cost history routes"
```

---

> **Frontend language:** the existing SPA is in English (`Home`, `Files`, `Sign out`), so the copy below is English and lives only in templates; a French pass later is a find-and-replace. Money fields are strings end to end (see Global Constraints) and are only converted with `Number()` for display.

### Task 8: Frontend API modules and stores

**Files:**
- Create: `frontend/src/format.ts`, `frontend/src/api/household.ts`, `frontend/src/api/providers.ts`, `frontend/src/api/invoices.ts`, `frontend/src/api/costs.ts`, `frontend/src/stores/household.ts`, `frontend/src/stores/providers.ts`, `frontend/src/stores/invoices.ts`
- Modify: `frontend/src/api/client.ts` (add `apiSend`)
- Test: `frontend/tests/unit/format.test.ts`, `frontend/tests/unit/api-ledger.test.ts`, `frontend/tests/unit/stores-ledger.test.ts`

**Interfaces:**
- Produces:
  - `format.ts`: `money(value: string | number | null | undefined): string` (CAD, `'—'` for empty); `blankToNull(value: string | number | null | undefined): string | null` (trimmed text, `null` when empty).
  - `client.ts`: `apiSend<T>(method, path, body?: unknown): Promise<T>` (JSON in, JSON out).
  - `api/household.ts`: `type Role = 'owner' | 'member' | 'viewer'`; `Member {sub, role}`; `Household {id, name, role, members}`; `Invitation {token, expires_in_days}`; `getHousehold()`, `createHousehold(name)`, `deleteHousehold()`, `createInvitation(role: 'member' | 'viewer')`, `joinHousehold(token)`, `setMemberRole(sub, role)`, `removeMember(sub)`, `leaveHousehold()`.
  - `api/providers.ts`: `Service`, `Provider` (with `services: Service[]`), `ProviderInput`, `ServiceInput`; `listProviders()`, `getProvider(id)`, `createProvider(input)`, `updateProvider(id, patch)`, `deleteProvider(id)`, `createService(providerId, input)`, `getService(id)`, `updateService(id, patch)`, `deleteService(id)`.
  - `api/invoices.ts`: `InvoiceStatus`, `Tax {name, amount}`, `Candidates`, `Invoice`, `InvoiceFields`, `UploadTarget {service_id?, provider_id?}`; `uploadInvoices(files, target?)`, `listInvoices(params?)`, `getInvoice(id)`, `validateInvoice(id, fields)`, `createManualInvoice(fields)`, `setPaid(id, paid)`, `deleteInvoice(id)`, `invoicePdf(id): Promise<Blob>`.
  - `api/costs.ts`: `Upcoming`, `ServiceCosts`, `MonthlyCosts`; `getUpcoming()`, `getServiceCosts(serviceId)`, `getMonthlyCosts()`.
  - Stores: `useHouseholdStore` (`household`, `loaded`, `loading`, `error`, `canWrite`, `isOwner`, `load`, `create`, `join`, `remove`, `leave`, `invite`, `setRole`, `removeMember`); `useProvidersStore` (`providers`, `error`, `loading`, `serviceOptions: {id, label}[]`, `load`, `createProvider`, `updateProvider`, `deleteProvider`, `createService`, `updateService`, `deleteService`); `useInvoicesStore` (`invoices`, `upcoming`, `monthly`, `error`, `duplicateOf`, `loading`, `toReview`, `loadAll`, `loadDashboard`, `upload`, `validate`, `createManual`, `markPaid`, `remove`). Every store action resolves to its result, or `undefined` / `false` after recording `error`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/tests/unit/format.test.ts`:

```ts
import { expect, it } from 'vitest'

import { blankToNull, money } from '@/format'

it('money formats a decimal string as Canadian dollars', () => {
  expect(money('114.98').replace(/\s/g, ' ')).toContain('114,98')
  expect(money('114.98')).toContain('$')
})

it('money shows a dash for nothing', () => {
  expect(money(null)).toBe('—')
  expect(money('')).toBe('—')
  expect(money(undefined)).toBe('—')
})

it('blankToNull trims and turns empty into null, for text and numbers alike', () => {
  expect(blankToNull('  Bell ')).toBe('Bell')
  expect(blankToNull('   ')).toBeNull()
  expect(blankToNull('')).toBeNull()
  expect(blankToNull(null)).toBeNull()
  expect(blankToNull(12.5)).toBe('12.5')
})
```

Create `frontend/tests/unit/api-ledger.test.ts`:

```ts
import { beforeEach, expect, it, vi } from 'vitest'

import { apiGet, apiRequest, apiSend } from '@/api/client'
import { getUpcoming, getServiceCosts } from '@/api/costs'
import { createInvitation, deleteHousehold, setMemberRole } from '@/api/household'
import {
  createManualInvoice,
  invoicePdf,
  listInvoices,
  setPaid,
  uploadInvoices,
  validateInvoice,
} from '@/api/invoices'
import { createService, updateProvider } from '@/api/providers'

vi.mock('@/api/client', () => ({
  apiGet: vi.fn(async () => ({})),
  apiRequest: vi.fn(async () => ({ json: async () => ({}), blob: async () => new Blob(['pdf']) })),
  apiSend: vi.fn(async () => ({})),
}))

beforeEach(() => {
  vi.clearAllMocks()
})

it('household calls send JSON and encode the member sub', async () => {
  await createInvitation('viewer')
  await setMemberRole('a/b', 'member')
  await deleteHousehold()

  expect(apiSend).toHaveBeenCalledWith('POST', '/household/invitations', { role: 'viewer' })
  expect(apiSend).toHaveBeenCalledWith('PATCH', '/household/members/a%2Fb', { role: 'member' })
  expect(apiRequest).toHaveBeenCalledWith('DELETE', '/household')
})

it('provider calls PATCH and POST JSON', async () => {
  await updateProvider('p1', { phone: '1' })
  await createService('p1', {
    name: 'Internet',
    category: 'internet',
    account_number: null,
    contract_start: null,
    contract_end: null,
    renewal_reminder_days: null,
    expected_monthly_cost: '79.99',
    auto_pay: true,
    alert_threshold_pct: 20,
  })

  expect(apiSend).toHaveBeenCalledWith('PATCH', '/providers/p1', { phone: '1' })
  expect(apiSend).toHaveBeenCalledWith(
    'POST',
    '/providers/p1/services',
    expect.objectContaining({ name: 'Internet', auto_pay: true }),
  )
})

it('uploadInvoices sends every file and only the targets that are set', async () => {
  const a = new File(['a'], 'a.pdf')
  const b = new File(['b'], 'b.pdf')

  await uploadInvoices([a, b], { service_id: 's1' })
  await uploadInvoices([a])

  const [first, second] = vi.mocked(apiRequest).mock.calls.map((call) => call[2] as FormData)
  expect(first.getAll('files')).toHaveLength(2)
  expect(first.get('service_id')).toBe('s1')
  expect(first.has('provider_id')).toBe(false)
  expect(second.has('service_id')).toBe(false)
  expect(vi.mocked(apiRequest).mock.calls[0].slice(0, 2)).toEqual(['POST', '/invoices'])
})

it('listInvoices puts only the params that are set in the query string', async () => {
  await listInvoices({ status: 'to_validate', service_id: undefined, unpaid: true })
  await listInvoices()

  expect(apiGet).toHaveBeenNthCalledWith(1, '/invoices?status=to_validate&unpaid=true')
  expect(apiGet).toHaveBeenNthCalledWith(2, '/invoices')
})

it('validate, manual entry and paid state post JSON', async () => {
  const fields = { service_id: 's1', total: '10.00', due_on: '2026-11-01', taxes: [] }
  await validateInvoice('i1', { ...fields, issued_on: null, period_start: null, period_end: null,
    invoice_number: null, consumption_qty: null, consumption_unit: null })
  await createManualInvoice({ ...fields, issued_on: null, period_start: null, period_end: null,
    invoice_number: null, consumption_qty: null, consumption_unit: null })
  await setPaid('i1', true)

  expect(apiSend).toHaveBeenCalledWith('POST', '/invoices/i1/validate', expect.any(Object))
  expect(apiSend).toHaveBeenCalledWith('POST', '/invoices/manual', expect.any(Object))
  expect(apiSend).toHaveBeenCalledWith('POST', '/invoices/i1/paid', { paid: true })
})

it('invoicePdf downloads the bytes through the authenticated client', async () => {
  const blob = await invoicePdf('i1')

  expect(apiRequest).toHaveBeenCalledWith('GET', '/invoices/i1/pdf')
  expect(blob).toBeInstanceOf(Blob)
})

it('cost calls hit the three read endpoints', async () => {
  await getUpcoming()
  await getServiceCosts('s1')

  expect(apiGet).toHaveBeenCalledWith('/upcoming')
  expect(apiGet).toHaveBeenCalledWith('/services/s1/costs')
})
```

Create `frontend/tests/unit/stores-ledger.test.ts`:

```ts
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'
import { getHousehold, joinHousehold } from '@/api/household'
import { listInvoices, validateInvoice, type Invoice } from '@/api/invoices'
import { listProviders } from '@/api/providers'
import { useHouseholdStore } from '@/stores/household'
import { useInvoicesStore } from '@/stores/invoices'
import { useProvidersStore } from '@/stores/providers'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/household', () => ({
  getHousehold: vi.fn(),
  createHousehold: vi.fn(),
  deleteHousehold: vi.fn(),
  createInvitation: vi.fn(),
  joinHousehold: vi.fn(),
  setMemberRole: vi.fn(),
  removeMember: vi.fn(),
  leaveHousehold: vi.fn(),
}))
vi.mock('@/api/providers', () => ({
  listProviders: vi.fn(),
  createProvider: vi.fn(),
  updateProvider: vi.fn(),
  deleteProvider: vi.fn(),
  createService: vi.fn(),
  updateService: vi.fn(),
  deleteService: vi.fn(),
}))
vi.mock('@/api/invoices', () => ({
  listInvoices: vi.fn(async () => []),
  uploadInvoices: vi.fn(),
  validateInvoice: vi.fn(),
  createManualInvoice: vi.fn(),
  setPaid: vi.fn(),
  deleteInvoice: vi.fn(),
}))
vi.mock('@/api/costs', () => ({
  getUpcoming: vi.fn(async () => ({ invoices: [], renewals: [] })),
  getMonthlyCosts: vi.fn(async () => ({ months: [] })),
}))

const home = { id: 'h1', name: 'Maison', role: 'owner' as const, members: [] }

function invoice(overrides: Partial<Invoice>): Invoice {
  return {
    id: 'i1', status: 'to_validate', service_id: null, has_pdf: true, total: null, due_on: null,
    issued_on: null, period_start: null, period_end: null, invoice_number: null,
    consumption_qty: null, consumption_unit: null, paid_at: null, paid: false, error: null,
    extraction: null, taxes: [], ...overrides,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

it('a person with no household is not an error', async () => {
  vi.mocked(getHousehold).mockRejectedValue(new ApiError('no_household', 409, null))
  const store = useHouseholdStore()

  await store.load()

  expect(store.household).toBeNull()
  expect(store.error).toBeNull()
  expect(store.loaded).toBe(true)
})

it('any other failure is recorded', async () => {
  vi.mocked(getHousehold).mockRejectedValue(new ApiError('boom', 500, null))
  const store = useHouseholdStore()

  await store.load()

  expect(store.error).toBe('boom')
})

it('roles decide who can write', async () => {
  vi.mocked(getHousehold).mockResolvedValue({ ...home, role: 'viewer' })
  const store = useHouseholdStore()

  await store.load()

  expect(store.canWrite).toBe(false)
  expect(store.isOwner).toBe(false)
})

it('joining stores the household it lands in', async () => {
  vi.mocked(joinHousehold).mockResolvedValue({ ...home, role: 'member' })
  const store = useHouseholdStore()

  await store.join('tok')

  expect(store.household?.role).toBe('member')
})

it('serviceOptions lists live services as "Provider — Service"', async () => {
  const service = (id: string, name: string, archived = false) => ({
    id, provider_id: 'p1', name, category: 'x', account_number: null, contract_start: null,
    contract_end: null, renewal_reminder_days: null, expected_monthly_cost: null, auto_pay: false,
    alert_threshold_pct: 20, archived,
  })
  vi.mocked(listProviders).mockResolvedValue([
    { id: 'p1', name: 'Bell', website: null, phone: null, email: null, notes: null,
      services: [service('s1', 'Internet'), service('s2', 'Old', true)] },
  ])
  const store = useProvidersStore()

  await store.load()

  expect(store.serviceOptions).toEqual([{ id: 's1', label: 'Bell — Internet' }])
})

it('toReview keeps everything that is not validated yet', async () => {
  vi.mocked(listInvoices).mockResolvedValue([
    invoice({ id: 'a', status: 'to_validate' }),
    invoice({ id: 'b', status: 'validated' }),
    invoice({ id: 'c', status: 'failed' }),
  ])
  const store = useInvoicesStore()

  await store.loadAll()

  expect(store.toReview.map((i) => i.id)).toEqual(['a', 'c'])
})

it('validating replaces the invoice in the list', async () => {
  vi.mocked(listInvoices).mockResolvedValue([invoice({ id: 'a' })])
  vi.mocked(validateInvoice).mockResolvedValue(invoice({ id: 'a', status: 'validated' }))
  const store = useInvoicesStore()
  await store.loadAll()

  await store.validate('a', {} as never)

  expect(store.toReview).toEqual([])
  expect(store.invoices[0].status).toBe('validated')
})

it('a duplicate is reported with the id of the invoice it duplicates', async () => {
  vi.mocked(listInvoices).mockResolvedValue([invoice({ id: 'a' })])
  vi.mocked(validateInvoice).mockRejectedValue(
    new ApiError('Conflict', 409, { detail: { message: 'Duplicate', existing_id: 'other' } }),
  )
  const store = useInvoicesStore()
  await store.loadAll()

  const result = await store.validate('a', {} as never)

  expect(result).toBeUndefined()
  expect(store.duplicateOf).toBe('other')
  expect(store.toReview).toHaveLength(1)
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npm run test -- tests/unit/format.test.ts tests/unit/api-ledger.test.ts tests/unit/stores-ledger.test.ts`
Expected: FAIL (cannot resolve `@/format`, `@/api/household`, ...).

- [ ] **Step 3: Add `apiSend` and `format.ts`**

Append to `frontend/src/api/client.ts`:

```ts
/** JSON in, JSON out. A DELETE or a 204 has no body: use apiRequest for those. */
export async function apiSend<T>(method: string, path: string, body?: unknown): Promise<T> {
  const json = body === undefined ? undefined : JSON.stringify(body)
  const response = await apiRequest(method, path, json, json === undefined ? undefined : 'application/json')
  return (await response.json()) as T
}
```

Create `frontend/src/format.ts`:

```ts
const currency = new Intl.NumberFormat('fr-CA', { style: 'currency', currency: 'CAD' })

/** Money arrives as a decimal string (the API never sends floats for amounts). */
export function money(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  return currency.format(Number(value))
}

/** Trimmed text, or null when empty: what the API wants for an optional field. */
export function blankToNull(value: string | number | null | undefined): string | null {
  const text = String(value ?? '').trim()
  return text === '' ? null : text
}
```

- [ ] **Step 4: Write the API modules**

Create `frontend/src/api/household.ts`:

```ts
import { apiGet, apiRequest, apiSend } from './client'

export type Role = 'owner' | 'member' | 'viewer'

export interface Member {
  sub: string
  role: Role
}

export interface Household {
  id: string
  name: string
  role: Role
  members: Member[]
}

export interface Invitation {
  token: string
  expires_in_days: number
}

export function getHousehold(): Promise<Household> {
  return apiGet<Household>('/household')
}

export function createHousehold(name: string): Promise<Household> {
  return apiSend<Household>('POST', '/household', { name })
}

export async function deleteHousehold(): Promise<void> {
  await apiRequest('DELETE', '/household')
}

export function createInvitation(role: 'member' | 'viewer'): Promise<Invitation> {
  return apiSend<Invitation>('POST', '/household/invitations', { role })
}

export function joinHousehold(token: string): Promise<Household> {
  return apiSend<Household>('POST', '/household/join', { token })
}

export function setMemberRole(sub: string, role: 'member' | 'viewer'): Promise<Member> {
  return apiSend<Member>('PATCH', `/household/members/${encodeURIComponent(sub)}`, { role })
}

export async function removeMember(sub: string): Promise<void> {
  await apiRequest('DELETE', `/household/members/${encodeURIComponent(sub)}`)
}

export async function leaveHousehold(): Promise<void> {
  await apiRequest('POST', '/household/leave')
}
```

Create `frontend/src/api/providers.ts`:

```ts
import { apiGet, apiRequest, apiSend } from './client'

export interface Service {
  id: string
  provider_id: string
  name: string
  category: string
  account_number: string | null
  contract_start: string | null
  contract_end: string | null
  renewal_reminder_days: number | null
  expected_monthly_cost: string | null
  auto_pay: boolean
  alert_threshold_pct: number
  archived: boolean
}

export interface Provider {
  id: string
  name: string
  website: string | null
  phone: string | null
  email: string | null
  notes: string | null
  services: Service[]
}

export type ProviderInput = Pick<Provider, 'name' | 'website' | 'phone' | 'email' | 'notes'>
export type ServiceInput = Omit<Service, 'id' | 'provider_id' | 'archived'>

export function listProviders(): Promise<Provider[]> {
  return apiGet<Provider[]>('/providers')
}

export function getProvider(id: string): Promise<Provider> {
  return apiGet<Provider>(`/providers/${id}`)
}

export function createProvider(input: ProviderInput): Promise<Provider> {
  return apiSend<Provider>('POST', '/providers', input)
}

export function updateProvider(id: string, patch: Partial<ProviderInput>): Promise<Provider> {
  return apiSend<Provider>('PATCH', `/providers/${id}`, patch)
}

export async function deleteProvider(id: string): Promise<void> {
  await apiRequest('DELETE', `/providers/${id}`)
}

export function createService(providerId: string, input: ServiceInput): Promise<Service> {
  return apiSend<Service>('POST', `/providers/${providerId}/services`, input)
}

export function getService(id: string): Promise<Service> {
  return apiGet<Service>(`/services/${id}`)
}

export function updateService(
  id: string,
  patch: Partial<ServiceInput> & { archived?: boolean },
): Promise<Service> {
  return apiSend<Service>('PATCH', `/services/${id}`, patch)
}

/** A service with invoices answers 409: archive it instead. */
export async function deleteService(id: string): Promise<void> {
  await apiRequest('DELETE', `/services/${id}`)
}
```

Create `frontend/src/api/invoices.ts`:

```ts
import { apiGet, apiRequest, apiSend } from './client'

export type InvoiceStatus = 'queued' | 'extracting' | 'to_validate' | 'validated' | 'failed'

export interface Tax {
  name: string
  amount: string
}

export interface Candidates {
  provider: { id: string; name: string } | null
  service: { id: string; name: string } | null
  new_provider: string | null
  new_service: string | null
}

export interface Invoice {
  id: string
  status: InvoiceStatus
  service_id: string | null
  has_pdf: boolean
  total: string | null
  due_on: string | null
  issued_on: string | null
  period_start: string | null
  period_end: string | null
  invoice_number: string | null
  consumption_qty: string | null
  consumption_unit: string | null
  paid_at: string | null
  paid: boolean
  error: string | null
  extraction: {
    raw?: { taxes?: Tax[] }
    candidates?: Candidates
    provider_id?: string | null
  } | null
  taxes: Tax[]
}

export interface InvoiceFields {
  service_id: string
  total: string
  due_on: string
  issued_on: string | null
  period_start: string | null
  period_end: string | null
  invoice_number: string | null
  consumption_qty: string | null
  consumption_unit: string | null
  taxes: Tax[]
}

export interface UploadTarget {
  service_id?: string
  provider_id?: string
}

export async function uploadInvoices(files: File[], target: UploadTarget = {}): Promise<Invoice[]> {
  const form = new FormData()
  for (const file of files) form.append('files', file)
  if (target.service_id) form.append('service_id', target.service_id)
  if (target.provider_id) form.append('provider_id', target.provider_id)
  return (await apiRequest('POST', '/invoices', form)).json()
}

export function listInvoices(
  params: { status?: InvoiceStatus; service_id?: string; unpaid?: boolean } = {},
): Promise<Invoice[]> {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) query.set(key, String(value))
  }
  const text = query.toString()
  return apiGet<Invoice[]>(text ? `/invoices?${text}` : '/invoices')
}

export function getInvoice(id: string): Promise<Invoice> {
  return apiGet<Invoice>(`/invoices/${id}`)
}

export function validateInvoice(id: string, fields: InvoiceFields): Promise<Invoice> {
  return apiSend<Invoice>('POST', `/invoices/${id}/validate`, fields)
}

export function createManualInvoice(fields: InvoiceFields): Promise<Invoice> {
  return apiSend<Invoice>('POST', '/invoices/manual', fields)
}

export function setPaid(id: string, paid: boolean): Promise<Invoice> {
  return apiSend<Invoice>('POST', `/invoices/${id}/paid`, { paid })
}

export async function deleteInvoice(id: string): Promise<void> {
  await apiRequest('DELETE', `/invoices/${id}`)
}

/** The PDF goes through the authenticated client: an <iframe src> to the API
 *  would carry no bearer token. The caller turns the blob into an object URL. */
export async function invoicePdf(id: string): Promise<Blob> {
  return (await apiRequest('GET', `/invoices/${id}/pdf`)).blob()
}
```

Create `frontend/src/api/costs.ts`:

```ts
import { apiGet } from './client'

export interface UpcomingInvoice {
  invoice_id: string
  service_id: string
  provider_name: string
  service_name: string
  total: string
  due_on: string
  overdue: boolean
}

export interface Renewal {
  service_id: string
  provider_name: string
  service_name: string
  contract_end: string
  days_left: number
}

export interface Upcoming {
  invoices: UpcomingInvoice[]
  renewals: Renewal[]
}

export interface CostPoint {
  invoice_id: string
  month: string
  total: string
  reference: string | null
  flagged: boolean
}

export interface ServiceCosts {
  expected_monthly_cost: string | null
  threshold_pct: number
  points: CostPoint[]
}

export interface MonthlyCosts {
  months: { month: string; total: string }[]
}

export function getUpcoming(): Promise<Upcoming> {
  return apiGet<Upcoming>('/upcoming')
}

export function getServiceCosts(serviceId: string): Promise<ServiceCosts> {
  return apiGet<ServiceCosts>(`/services/${serviceId}/costs`)
}

export function getMonthlyCosts(): Promise<MonthlyCosts> {
  return apiGet<MonthlyCosts>('/costs/monthly')
}
```

- [ ] **Step 5: Write the stores**

Create `frontend/src/stores/household.ts`:

```ts
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { ApiError } from '@/api/client'
import {
  createHousehold,
  createInvitation,
  deleteHousehold,
  getHousehold,
  joinHousehold,
  leaveHousehold,
  removeMember,
  setMemberRole,
  type Household,
  type Invitation,
} from '@/api/household'

export const useHouseholdStore = defineStore('household', () => {
  const household = ref<Household | null>(null)
  // False until the first load() settles: "no household" is only true after that.
  const loaded = ref(false)
  const error = ref<string | null>(null)
  const loading = ref(false)

  const canWrite = computed(() => household.value !== null && household.value.role !== 'viewer')
  const isOwner = computed(() => household.value?.role === 'owner')

  async function run<T>(action: () => Promise<T>): Promise<T | undefined> {
    loading.value = true
    error.value = null
    try {
      return await action()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      return undefined
    } finally {
      loading.value = false
    }
  }

  async function load() {
    await run(async () => {
      try {
        household.value = await getHousehold()
      } catch (e) {
        // 409 no_household: the person simply has none yet.
        if (e instanceof ApiError && e.status === 409) household.value = null
        else throw e
      }
      loaded.value = true
    })
  }

  const reload = () => run(async () => void (household.value = await getHousehold()))

  async function create(name: string) {
    return run(async () => void (household.value = await createHousehold(name)))
  }

  async function join(token: string) {
    return run(async () => void (household.value = await joinHousehold(token)))
  }

  async function remove() {
    return run(async () => {
      await deleteHousehold()
      household.value = null
    })
  }

  async function leave() {
    return run(async () => {
      await leaveHousehold()
      household.value = null
    })
  }

  async function invite(role: 'member' | 'viewer'): Promise<Invitation | undefined> {
    return run(() => createInvitation(role))
  }

  async function setRole(sub: string, role: 'member' | 'viewer') {
    await run(() => setMemberRole(sub, role))
    await reload()
  }

  async function removeFromHousehold(sub: string) {
    await run(() => removeMember(sub))
    await reload()
  }

  return {
    household, loaded, error, loading, canWrite, isOwner,
    load, create, join, remove, leave, invite, setRole, removeMember: removeFromHousehold,
  }
})
```

Create `frontend/src/stores/providers.ts`:

```ts
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import {
  createProvider,
  createService,
  deleteProvider,
  deleteService,
  listProviders,
  updateProvider,
  updateService,
  type Provider,
  type ProviderInput,
  type ServiceInput,
} from '@/api/providers'

export const useProvidersStore = defineStore('providers', () => {
  const providers = ref<Provider[]>([])
  const error = ref<string | null>(null)
  const loading = ref(false)

  // For a <select>: live services only, labelled with their provider.
  const serviceOptions = computed(() =>
    providers.value.flatMap((p) =>
      p.services.filter((s) => !s.archived).map((s) => ({ id: s.id, label: `${p.name} — ${s.name}` })),
    ),
  )

  async function run<T>(action: () => Promise<T>): Promise<T | undefined> {
    loading.value = true
    error.value = null
    try {
      return await action()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      return undefined
    } finally {
      loading.value = false
    }
  }

  // No optimistic updates: every mutation reloads the list.
  async function mutate<T>(action: () => Promise<T>): Promise<T | undefined> {
    const result = await run(async () => {
      const value = await action()
      providers.value = await listProviders()
      return value
    })
    return result
  }

  const load = () => run(async () => void (providers.value = await listProviders()))
  const create = (input: ProviderInput) => mutate(() => createProvider(input))
  const update = (id: string, patch: Partial<ProviderInput>) => mutate(() => updateProvider(id, patch))
  const remove = (id: string) => mutate(() => deleteProvider(id))
  const addService = (providerId: string, input: ServiceInput) =>
    mutate(() => createService(providerId, input))
  const changeService = (id: string, patch: Partial<ServiceInput> & { archived?: boolean }) =>
    mutate(() => updateService(id, patch))
  const removeService = (id: string) => mutate(() => deleteService(id))

  return {
    providers, error, loading, serviceOptions,
    load,
    createProvider: create,
    updateProvider: update,
    deleteProvider: remove,
    createService: addService,
    updateService: changeService,
    deleteService: removeService,
  }
})
```

Create `frontend/src/stores/invoices.ts`:

```ts
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { ApiError } from '@/api/client'
import { getMonthlyCosts, getUpcoming, type MonthlyCosts, type Upcoming } from '@/api/costs'
import {
  createManualInvoice,
  deleteInvoice,
  listInvoices,
  setPaid,
  uploadInvoices,
  validateInvoice,
  type Invoice,
  type InvoiceFields,
  type UploadTarget,
} from '@/api/invoices'

export const useInvoicesStore = defineStore('invoices', () => {
  const invoices = ref<Invoice[]>([])
  const upcoming = ref<Upcoming>({ invoices: [], renewals: [] })
  const monthly = ref<MonthlyCosts['months']>([])
  const error = ref<string | null>(null)
  // Set by a 409 on validation: the id of the invoice that already has this number.
  const duplicateOf = ref<string | null>(null)
  const loading = ref(false)

  // Everything that still needs a person: waiting, reading, readable or failed.
  const toReview = computed(() => invoices.value.filter((i) => i.status !== 'validated'))

  async function run<T>(action: () => Promise<T>): Promise<T | undefined> {
    loading.value = true
    error.value = null
    duplicateOf.value = null
    try {
      return await action()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      if (e instanceof ApiError && e.status === 409) {
        const detail = (e.body as { detail?: { existing_id?: string } } | null)?.detail
        duplicateOf.value = detail?.existing_id ?? null
      }
      return undefined
    } finally {
      loading.value = false
    }
  }

  function replace(updated: Invoice) {
    const index = invoices.value.findIndex((i) => i.id === updated.id)
    if (index === -1) invoices.value = [updated, ...invoices.value]
    else invoices.value.splice(index, 1, updated)
  }

  const loadAll = () => run(async () => void (invoices.value = await listInvoices()))

  const loadDashboard = () =>
    run(async () => {
      const [all, soon, months] = await Promise.all([listInvoices(), getUpcoming(), getMonthlyCosts()])
      invoices.value = all
      upcoming.value = soon
      monthly.value = months.months
    })

  const upload = (files: File[], target?: UploadTarget) =>
    run(async () => {
      const created = await uploadInvoices(files, target)
      invoices.value = [...created, ...invoices.value]
      return created
    })

  const validate = (id: string, fields: InvoiceFields) =>
    run(async () => {
      const updated = await validateInvoice(id, fields)
      replace(updated)
      return updated
    })

  const createManual = (fields: InvoiceFields) =>
    run(async () => {
      const created = await createManualInvoice(fields)
      replace(created)
      return created
    })

  const markPaid = (id: string, paid: boolean) =>
    run(async () => {
      const updated = await setPaid(id, paid)
      replace(updated)
      return updated
    })

  const remove = (id: string) =>
    run(async () => {
      await deleteInvoice(id)
      invoices.value = invoices.value.filter((i) => i.id !== id)
      return true
    })

  return {
    invoices, upcoming, monthly, error, duplicateOf, loading, toReview,
    loadAll, loadDashboard, upload, validate, createManual, markPaid, remove,
  }
})
```

- [ ] **Step 6: Run the tests and the type-check**

Run: `cd frontend && npm run test -- tests/unit/format.test.ts tests/unit/api-ledger.test.ts tests/unit/stores-ledger.test.ts && npm exec -- vue-tsc --build --force`
Expected: PASS, type-check clean. (`money('114.98')` formats with a non-breaking space in `fr-CA`; the test normalises whitespace.)

- [ ] **Step 7: Commit (only if the user has asked for commits)**

```bash
git add frontend
git commit -m "feat: frontend API modules and stores for households, providers and invoices"
```

---

### Task 9: Household views, routes and navigation

**Files:**
- Create: `frontend/src/views/HouseholdView.vue`, `frontend/src/views/JoinView.vue`
- Modify: `frontend/src/router/index.ts`, `frontend/src/App.vue`
- Test: `frontend/tests/unit/HouseholdView.test.ts`, `frontend/tests/unit/JoinView.test.ts`, `frontend/tests/unit/router-ledger.test.ts`

**Interfaces:**
- Consumes: `useHouseholdStore` (Task 8).
- Produces: routes `/household` (`household`), `/household/join` (`household-join`), and the four ledger routes registered here so later tasks only add the views: `/providers` (`providers`), `/providers/:id` (`provider`), `/services/:id` (`service`), `/invoices/review` (`invoice-review`). The ledger views are lazy-loaded, so the router resolves before they exist; Tasks 10 and 11 create the files (the build fails until then, so Task 9 verifies with `vitest` only and the full `npm run build` runs from Task 11).

- [ ] **Step 1: Write the failing tests**

Create `frontend/tests/unit/HouseholdView.test.ts`:

```ts
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'
import { createHousehold, createInvitation, getHousehold } from '@/api/household'
import HouseholdView from '@/views/HouseholdView.vue'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/household', () => ({
  getHousehold: vi.fn(),
  createHousehold: vi.fn(),
  deleteHousehold: vi.fn(async () => {}),
  createInvitation: vi.fn(),
  joinHousehold: vi.fn(),
  setMemberRole: vi.fn(async () => ({})),
  removeMember: vi.fn(async () => {}),
  leaveHousehold: vi.fn(async () => {}),
}))

const owned = {
  id: 'h1',
  name: 'Maison',
  role: 'owner' as const,
  members: [
    { sub: 'alice', role: 'owner' as const },
    { sub: 'bob', role: 'member' as const },
  ],
}

async function render() {
  const wrapper = mount(HouseholdView, { global: { plugins: [createPinia()] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
})

it('offers to create a household when the person has none', async () => {
  vi.mocked(getHousehold).mockRejectedValue(new ApiError('no_household', 409, null))
  vi.mocked(createHousehold).mockResolvedValue(owned)
  const wrapper = await render()

  await wrapper.find('input[name="household-name"]').setValue('Maison')
  await wrapper.find('form').trigger('submit')
  await flushPromises()

  expect(createHousehold).toHaveBeenCalledWith('Maison')
  expect(wrapper.text()).toContain('bob')
})

it('lets the owner generate an invitation link', async () => {
  vi.mocked(getHousehold).mockResolvedValue(owned)
  vi.mocked(createInvitation).mockResolvedValue({ token: 'abc 123', expires_in_days: 7 })
  const wrapper = await render()

  await wrapper.find('select[name="invite-role"]').setValue('viewer')
  await wrapper.find('button[data-test="invite"]').trigger('click')
  await flushPromises()

  expect(createInvitation).toHaveBeenCalledWith('viewer')
  const link = (wrapper.find('input[data-test="invite-link"]').element as HTMLInputElement).value
  expect(link).toContain('/household/join?token=abc%20123')
})

it('hides the owner controls from a member', async () => {
  vi.mocked(getHousehold).mockResolvedValue({ ...owned, role: 'member' })
  const wrapper = await render()

  expect(wrapper.find('button[data-test="invite"]').exists()).toBe(false)
  expect(wrapper.find('button[data-test="delete-household"]').exists()).toBe(false)
  expect(wrapper.find('button[data-test="leave"]').exists()).toBe(true)
})
```

Create `frontend/tests/unit/JoinView.test.ts`:

```ts
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { joinHousehold } from '@/api/household'
import JoinView from '@/views/JoinView.vue'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/household', () => ({ joinHousehold: vi.fn() }))

async function render(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/household/join', component: JoinView },
      { path: '/providers', component: { template: '<p>providers</p>' } },
    ],
  })
  await router.push(path)
  const wrapper = mount(JoinView, { global: { plugins: [createPinia(), router] } })
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  vi.clearAllMocks()
})

it('joins with the token in the link and goes to the providers', async () => {
  vi.mocked(joinHousehold).mockResolvedValue({ id: 'h', name: 'M', role: 'viewer', members: [] })

  const { router } = await render('/household/join?token=tok123')

  expect(joinHousehold).toHaveBeenCalledWith('tok123')
  expect(router.currentRoute.value.path).toBe('/providers')
})

it('says so when the link is invalid or expired', async () => {
  vi.mocked(joinHousehold).mockRejectedValue(new Error('Invitation invalid or expired'))

  const { wrapper, router } = await render('/household/join?token=old')

  expect(wrapper.text()).toContain('Invitation invalid or expired')
  expect(router.currentRoute.value.path).toBe('/household/join')
})

it('does not call the API for a link with no token', async () => {
  const { wrapper } = await render('/household/join')

  expect(joinHousehold).not.toHaveBeenCalled()
  expect(wrapper.text()).toContain('no invitation')
})
```

Create `frontend/tests/unit/router-ledger.test.ts`:

```ts
import { expect, it, vi } from 'vitest'

import { router } from '@/router'

vi.mock('@/auth', () => ({
  accessToken: vi.fn(async () => 'a-token'),
  signIn: vi.fn(async () => {}),
  completeSignIn: vi.fn(async () => '/'),
  signOut: vi.fn(async () => {}),
}))

it.each([
  ['/household', 'household'],
  ['/household/join', 'household-join'],
  ['/providers', 'providers'],
  ['/providers/abc', 'provider'],
  ['/services/abc', 'service'],
  ['/invoices/review', 'invoice-review'],
])('%s resolves to %s', (path, name) => {
  expect(router.resolve(path).name).toBe(name)
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npm run test -- tests/unit/HouseholdView.test.ts tests/unit/JoinView.test.ts tests/unit/router-ledger.test.ts`
Expected: FAIL (views and routes missing).

- [ ] **Step 3: Add the routes and the navigation**

In `frontend/src/router/index.ts`, add these routes after the `/files` entry:

```ts
    { path: '/providers', name: 'providers', component: () => import('@/views/ProvidersView.vue') },
    {
      path: '/providers/:id',
      name: 'provider',
      component: () => import('@/views/ProviderView.vue'),
    },
    { path: '/services/:id', name: 'service', component: () => import('@/views/ServiceView.vue') },
    {
      path: '/invoices/review',
      name: 'invoice-review',
      component: () => import('@/views/ReviewView.vue'),
    },
    { path: '/household', name: 'household', component: () => import('@/views/HouseholdView.vue') },
    {
      path: '/household/join',
      name: 'household-join',
      component: () => import('@/views/JoinView.vue'),
    },
```

In `frontend/src/App.vue`, add after the Files link:

```vue
      <RouterLink to="/providers">Providers</RouterLink>
      <RouterLink to="/household">Household</RouterLink>
```

- [ ] **Step 4: Write the views**

Create `frontend/src/views/HouseholdView.vue`:

```vue
<script setup lang="ts">
import { onMounted, ref } from 'vue'

import type { Role } from '@/api/household'
import { useHouseholdStore } from '@/stores/household'

const store = useHouseholdStore()
const name = ref('')
const inviteRole = ref<'member' | 'viewer'>('member')
const link = ref('')

const LABELS: Record<Role, string> = { owner: 'Owner', member: 'Member', viewer: 'Read-only' }

onMounted(() => store.load())

async function create() {
  if (name.value.trim()) await store.create(name.value.trim())
}

async function invite() {
  const invitation = await store.invite(inviteRole.value)
  if (invitation) {
    link.value = `${location.origin}/household/join?token=${encodeURIComponent(invitation.token)}`
  }
}

async function destroy() {
  if (confirm('Delete the household with every provider, service and invoice?')) await store.remove()
}

async function leave() {
  if (confirm('Leave this household?')) await store.leave()
}
</script>

<template>
  <section>
    <h1>Household</h1>
    <p v-if="store.error" class="error">{{ store.error }}</p>

    <form v-if="store.loaded && !store.household" @submit.prevent="create">
      <p>
        You are not in a household yet. Create one, or open the invitation link someone sent you.
      </p>
      <input v-model="name" name="household-name" placeholder="Household name" maxlength="100" />
      <button type="submit">Create household</button>
    </form>

    <template v-if="store.household">
      <h2>{{ store.household.name }}</h2>
      <ul>
        <li v-for="member in store.household.members" :key="member.sub">
          <code>{{ member.sub }}</code>
          <template v-if="store.isOwner && member.role !== 'owner'">
            <select
              :value="member.role"
              @change="
                store.setRole(member.sub, ($event.target as HTMLSelectElement).value as 'member' | 'viewer')
              "
            >
              <option value="member">Member</option>
              <option value="viewer">Read-only</option>
            </select>
            <button type="button" @click="store.removeMember(member.sub)">Remove</button>
          </template>
          <span v-else>{{ LABELS[member.role] }}</span>
        </li>
      </ul>

      <div v-if="store.isOwner">
        <h3>Invite someone</h3>
        <select v-model="inviteRole" name="invite-role">
          <option value="member">Member</option>
          <option value="viewer">Read-only</option>
        </select>
        <button type="button" data-test="invite" @click="invite">Create invitation link</button>
        <p v-if="link">
          Send this link yourself; it works once and expires in 7 days.
          <input data-test="invite-link" :value="link" readonly @focus="($event.target as HTMLInputElement).select()" />
        </p>
        <button type="button" data-test="delete-household" @click="destroy">Delete household</button>
      </div>
      <button v-else type="button" data-test="leave" @click="leave">Leave household</button>
    </template>
  </section>
</template>

<style scoped>
.error {
  color: #e06c75;
}

input[readonly] {
  width: 100%;
}
</style>
```

Create `frontend/src/views/JoinView.vue`:

```vue
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useHouseholdStore } from '@/stores/household'

const route = useRoute()
const router = useRouter()
const store = useHouseholdStore()
const missing = ref(false)

onMounted(async () => {
  const token = String(route.query.token ?? '')
  if (!token) {
    missing.value = true
    return
  }
  await store.join(token)
  if (!store.error) await router.replace('/providers')
})
</script>

<template>
  <section>
    <h1>Join a household</h1>
    <p v-if="missing" class="error">This link has no invitation token.</p>
    <p v-else-if="store.error" class="error">{{ store.error }}</p>
    <p v-else>Joining…</p>
  </section>
</template>

<style scoped>
.error {
  color: #e06c75;
}
</style>
```

- [ ] **Step 5: Run the tests**

Run: `cd frontend && npm run test -- tests/unit/HouseholdView.test.ts tests/unit/JoinView.test.ts tests/unit/router-ledger.test.ts`
Expected: PASS. (The router test only resolves paths; the lazy `import()` of the ledger views is not triggered, so their absence does not matter yet.)

- [ ] **Step 6: Commit (only if the user has asked for commits)**

```bash
git add frontend
git commit -m "feat: household page, invitation link and ledger routes"
```

---

### Task 10: Provider, service and spending views

**Files:**
- Create: `frontend/src/components/CostChart.vue`, `frontend/src/components/ServiceForm.vue`, `frontend/src/views/ProvidersView.vue`, `frontend/src/views/ProviderView.vue`, `frontend/src/views/ServiceView.vue`
- Test: `frontend/tests/unit/CostChart.test.ts`, `frontend/tests/unit/ServiceForm.test.ts`, `frontend/tests/unit/ProvidersView.test.ts`, `frontend/tests/unit/ServiceView.test.ts`

**Interfaces:**
- Consumes: stores and API modules from Task 8; `money`, `blankToNull` from `@/format`.
- Produces:
  - `CostChart.vue`: prop `points: { label: string; value: number; flagged?: boolean }[]`; renders an `<svg>` with one `<circle>` per point (class `flagged` when flagged), or the text "Not enough data for a chart." for fewer than 2 points.
  - `ServiceForm.vue`: props `initial?: Partial<Service>`, `submitLabel: string`; emits `submit` with a `ServiceInput`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/tests/unit/CostChart.test.ts`:

```ts
import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'

import CostChart from '@/components/CostChart.vue'

it('asks for more data below two points', () => {
  const wrapper = mount(CostChart, { props: { points: [{ label: '2026-01', value: 10 }] } })

  expect(wrapper.find('svg').exists()).toBe(false)
  expect(wrapper.text()).toContain('Not enough data')
})

it('draws one dot per point and marks the flagged ones', () => {
  const wrapper = mount(CostChart, {
    props: {
      points: [
        { label: '2026-01', value: 100 },
        { label: '2026-02', value: 100 },
        { label: '2026-03', value: 250, flagged: true },
      ],
    },
  })

  const dots = wrapper.findAll('circle')
  expect(dots).toHaveLength(3)
  expect(dots.map((d) => d.classes('flagged'))).toEqual([false, false, true])
})

it('keeps the highest point inside the drawing', () => {
  const wrapper = mount(CostChart, {
    props: { points: [{ label: 'a', value: 0 }, { label: 'b', value: 500 }] },
  })

  const ys = wrapper.findAll('circle').map((d) => Number(d.attributes('cy')))
  expect(Math.min(...ys)).toBeGreaterThanOrEqual(0)
  expect(Math.max(...ys)).toBeLessThanOrEqual(100)
})
```

Create `frontend/tests/unit/ServiceForm.test.ts`:

```ts
import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'

import ServiceForm from '@/components/ServiceForm.vue'

it('emits a payload with blanks turned into nulls and numbers parsed', async () => {
  const wrapper = mount(ServiceForm, { props: { submitLabel: 'Add service' } })

  await wrapper.find('input[name="name"]').setValue('Internet')
  await wrapper.find('input[name="category"]').setValue('internet')
  await wrapper.find('input[name="expected_monthly_cost"]').setValue('79.99')
  await wrapper.find('input[name="auto_pay"]').setValue(true)
  await wrapper.find('form').trigger('submit')

  expect(wrapper.emitted('submit')![0][0]).toEqual({
    name: 'Internet',
    category: 'internet',
    account_number: null,
    contract_start: null,
    contract_end: null,
    renewal_reminder_days: null,
    expected_monthly_cost: '79.99',
    auto_pay: true,
    alert_threshold_pct: 20,
  })
})

it('starts from the service it is given', async () => {
  const wrapper = mount(ServiceForm, {
    props: {
      submitLabel: 'Save',
      initial: {
        name: 'Mobile', category: 'phone', alert_threshold_pct: 10, renewal_reminder_days: 30,
        contract_end: '2027-01-31',
      },
    },
  })

  expect((wrapper.find('input[name="name"]').element as HTMLInputElement).value).toBe('Mobile')
  await wrapper.find('form').trigger('submit')
  expect(wrapper.emitted('submit')![0][0]).toMatchObject({
    alert_threshold_pct: 10,
    renewal_reminder_days: 30,
    contract_end: '2027-01-31',
  })
})

it('does not emit without a name and a category', async () => {
  const wrapper = mount(ServiceForm, { props: { submitLabel: 'Add service' } })

  await wrapper.find('form').trigger('submit')

  expect(wrapper.emitted('submit')).toBeUndefined()
})
```

Create `frontend/tests/unit/ProvidersView.test.ts`:

```ts
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { getMonthlyCosts, getUpcoming } from '@/api/costs'
import { getHousehold } from '@/api/household'
import { listInvoices, uploadInvoices, type Invoice } from '@/api/invoices'
import { listProviders } from '@/api/providers'
import ProvidersView from '@/views/ProvidersView.vue'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/household', () => ({ getHousehold: vi.fn() }))
vi.mock('@/api/providers', () => ({ listProviders: vi.fn(), createProvider: vi.fn() }))
vi.mock('@/api/invoices', () => ({ listInvoices: vi.fn(), uploadInvoices: vi.fn() }))
vi.mock('@/api/costs', () => ({ getUpcoming: vi.fn(), getMonthlyCosts: vi.fn() }))

const home = { id: 'h', name: 'Maison', role: 'owner' as const, members: [] }

function pending(id: string): Invoice {
  return {
    id, status: 'to_validate', service_id: null, has_pdf: true, total: null, due_on: null,
    issued_on: null, period_start: null, period_end: null, invoice_number: null,
    consumption_qty: null, consumption_unit: null, paid_at: null, paid: false, error: null,
    extraction: null, taxes: [],
  }
}

async function render() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: ProvidersView }, { path: '/:rest(.*)', component: { template: '<p/>' } }],
  })
  await router.push('/')
  const wrapper = mount(ProvidersView, { global: { plugins: [createPinia(), router] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getHousehold).mockResolvedValue(home)
  vi.mocked(listProviders).mockResolvedValue([
    {
      id: 'p1', name: 'Bell', website: null, phone: null, email: null, notes: null,
      services: [
        { id: 's1', provider_id: 'p1', name: 'Internet', category: 'internet', account_number: null,
          contract_start: null, contract_end: null, renewal_reminder_days: null,
          expected_monthly_cost: null, auto_pay: false, alert_threshold_pct: 20, archived: false },
      ],
    },
  ])
  vi.mocked(listInvoices).mockResolvedValue([pending('a'), pending('b')])
  vi.mocked(getMonthlyCosts).mockResolvedValue({ months: [] })
  vi.mocked(getUpcoming).mockResolvedValue({
    invoices: [
      { invoice_id: 'i1', service_id: 's1', provider_name: 'Bell', service_name: 'Internet',
        total: '79.99', due_on: '2026-10-01', overdue: true },
      { invoice_id: 'i2', service_id: 's1', provider_name: 'Bell', service_name: 'Internet',
        total: '79.99', due_on: '2026-11-01', overdue: false },
    ],
    renewals: [
      { service_id: 's1', provider_name: 'Bell', service_name: 'Internet',
        contract_end: '2026-10-25', days_left: 15 },
    ],
  })
})

it('points a person without a household to the household page', async () => {
  vi.mocked(getHousehold).mockRejectedValue(new ApiError('no_household', 409, null))

  const wrapper = await render()

  expect(wrapper.find('a[href="/household"]').exists()).toBe(true)
  expect(listProviders).not.toHaveBeenCalled()
})

it('lists the providers with a link to each service', async () => {
  const wrapper = await render()

  expect(wrapper.find('a[href="/providers/p1"]').text()).toBe('Bell')
  expect(wrapper.find('a[href="/services/s1"]').text()).toBe('Internet')
})

it('shows the upcoming dues with the overdue ones marked, and the renewals', async () => {
  const wrapper = await render()

  const dues = wrapper.findAll('[data-test="due"]')
  expect(dues.map((d) => d.classes('overdue'))).toEqual([true, false])
  expect(wrapper.find('[data-test="renewal"]').text()).toContain('15 days')
})

it('counts the invoices waiting for review and links to the queue', async () => {
  const wrapper = await render()

  const link = wrapper.find('a[href="/invoices/review"]')
  expect(link.text()).toContain('2 invoices to review')
})

it('uploads the chosen PDFs', async () => {
  vi.mocked(uploadInvoices).mockResolvedValue([pending('c')])
  const wrapper = await render()
  const input = wrapper.find('input[type="file"]')
  const file = new File(['%PDF'], 'facture.pdf', { type: 'application/pdf' })

  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  await flushPromises()

  expect(uploadInvoices).toHaveBeenCalledWith([file], undefined)
})

it('hides the upload for a read-only member', async () => {
  vi.mocked(getHousehold).mockResolvedValue({ ...home, role: 'viewer' })

  const wrapper = await render()

  expect(wrapper.find('input[type="file"]').exists()).toBe(false)
})
```

Create `frontend/tests/unit/ServiceView.test.ts`:

```ts
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { getServiceCosts } from '@/api/costs'
import { getHousehold } from '@/api/household'
import { createManualInvoice, listInvoices, setPaid, type Invoice } from '@/api/invoices'
import { getService, listProviders } from '@/api/providers'
import ServiceView from '@/views/ServiceView.vue'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/household', () => ({ getHousehold: vi.fn() }))
vi.mock('@/api/providers', () => ({
  getService: vi.fn(),
  listProviders: vi.fn(async () => []),
  updateService: vi.fn(),
  deleteService: vi.fn(),
}))
vi.mock('@/api/invoices', () => ({
  listInvoices: vi.fn(),
  createManualInvoice: vi.fn(),
  setPaid: vi.fn(),
  deleteInvoice: vi.fn(),
  uploadInvoices: vi.fn(),
}))
vi.mock('@/api/costs', () => ({ getServiceCosts: vi.fn() }))

const service = {
  id: 's1', provider_id: 'p1', name: 'Internet', category: 'internet', account_number: null,
  contract_start: null, contract_end: null, renewal_reminder_days: null,
  expected_monthly_cost: '79.99', auto_pay: false, alert_threshold_pct: 20, archived: false,
}

function invoice(id: string, overrides: Partial<Invoice> = {}): Invoice {
  return {
    id, status: 'validated', service_id: 's1', has_pdf: false, total: '100.00', due_on: '2026-11-01',
    issued_on: '2026-10-01', period_start: null, period_end: null, invoice_number: id,
    consumption_qty: null, consumption_unit: null, paid_at: null, paid: false, error: null,
    extraction: null, taxes: [], ...overrides,
  }
}

async function render() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/services/:id', component: ServiceView }],
  })
  await router.push('/services/s1')
  const wrapper = mount(ServiceView, { global: { plugins: [createPinia(), router] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getHousehold).mockResolvedValue({ id: 'h', name: 'M', role: 'member', members: [] })
  vi.mocked(getService).mockResolvedValue(service)
  vi.mocked(listInvoices).mockResolvedValue([invoice('A-1'), invoice('A-2', { paid: true })])
  vi.mocked(getServiceCosts).mockResolvedValue({
    expected_monthly_cost: '79.99',
    threshold_pct: 20,
    points: [
      { invoice_id: 'A-1', month: '2026-09', total: '100.00', reference: '79.99', flagged: true },
      { invoice_id: 'A-2', month: '2026-10', total: '80.00', reference: '90.00', flagged: false },
    ],
  })
})

it('lists the invoices of the service and marks the ones above the alert threshold', async () => {
  const wrapper = await render()

  const rows = wrapper.findAll('tr[data-test="invoice"]')
  expect(rows).toHaveLength(2)
  expect(rows[0].find('[data-test="flag"]').exists()).toBe(true)
  expect(rows[1].find('[data-test="flag"]').exists()).toBe(false)
})

it('marks an unpaid invoice paid', async () => {
  vi.mocked(setPaid).mockResolvedValue(invoice('A-1', { paid: true }))
  const wrapper = await render()

  await wrapper.find('button[data-test="toggle-paid"]').trigger('click')
  await flushPromises()

  expect(setPaid).toHaveBeenCalledWith('A-1', true)
})

it('adds an invoice by hand for this service', async () => {
  vi.mocked(createManualInvoice).mockResolvedValue(invoice('A-3'))
  const wrapper = await render()

  await wrapper.find('input[name="manual-total"]').setValue('55.10')
  await wrapper.find('input[name="manual-due"]').setValue('2026-12-01')
  await wrapper.find('form[data-test="manual"]').trigger('submit')
  await flushPromises()

  expect(createManualInvoice).toHaveBeenCalledWith(
    expect.objectContaining({ service_id: 's1', total: '55.1', due_on: '2026-12-01', taxes: [] }),
  )
})

it('hides every write control from a read-only member', async () => {
  vi.mocked(getHousehold).mockResolvedValue({ id: 'h', name: 'M', role: 'viewer', members: [] })

  const wrapper = await render()

  expect(wrapper.find('form[data-test="manual"]').exists()).toBe(false)
  expect(wrapper.find('button[data-test="toggle-paid"]').exists()).toBe(false)
  expect(wrapper.find('input[type="file"]').exists()).toBe(false)
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npm run test -- tests/unit/CostChart.test.ts tests/unit/ServiceForm.test.ts tests/unit/ProvidersView.test.ts tests/unit/ServiceView.test.ts`
Expected: FAIL (components and views do not exist).

- [ ] **Step 3: Write the components**

Create `frontend/src/components/CostChart.vue`:

```vue
<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ points: { label: string; value: number; flagged?: boolean }[] }>()

const WIDTH = 300
const HEIGHT = 100
const PAD = 8

// Always drawn from zero so a small rise does not look like a spike.
const dots = computed(() => {
  const max = Math.max(...props.points.map((p) => p.value), 1)
  const step = props.points.length > 1 ? (WIDTH - 2 * PAD) / (props.points.length - 1) : 0
  return props.points.map((p, i) => ({
    ...p,
    x: PAD + i * step,
    y: HEIGHT - PAD - (p.value / max) * (HEIGHT - 2 * PAD),
  }))
})

const line = computed(() => dots.value.map((d) => `${d.x},${d.y}`).join(' '))
</script>

<template>
  <p v-if="points.length < 2" class="empty">Not enough data for a chart.</p>
  <svg v-else :viewBox="`0 0 ${WIDTH} ${HEIGHT}`" role="img" aria-label="Cost history">
    <polyline :points="line" fill="none" stroke="currentColor" stroke-width="2" />
    <circle
      v-for="d in dots"
      :key="d.label"
      :cx="d.x"
      :cy="d.y"
      r="3.5"
      :class="{ flagged: d.flagged }"
    >
      <title>{{ d.label }}: {{ d.value }}</title>
    </circle>
  </svg>
</template>

<style scoped>
svg {
  width: 100%;
  max-width: 40rem;
}

circle {
  fill: currentColor;
}

circle.flagged {
  fill: #e06c75;
}
</style>
```

Create `frontend/src/components/ServiceForm.vue`:

```vue
<script setup lang="ts">
import { reactive, watch } from 'vue'

import type { Service, ServiceInput } from '@/api/providers'
import { blankToNull } from '@/format'

const props = defineProps<{ initial?: Partial<Service>; submitLabel: string }>()
const emit = defineEmits<{ submit: [input: ServiceInput] }>()

const CATEGORIES = ['electricity', 'internet', 'mobile', 'tv', 'water', 'gas', 'insurance', 'other']

const form = reactive({
  name: '',
  category: '',
  account_number: '',
  contract_start: '',
  contract_end: '',
  renewal_reminder_days: '' as string | number,
  expected_monthly_cost: '' as string | number,
  auto_pay: false,
  alert_threshold_pct: 20 as string | number,
})

function fill(service: Partial<Service> = {}) {
  form.name = service.name ?? ''
  form.category = service.category ?? ''
  form.account_number = service.account_number ?? ''
  form.contract_start = service.contract_start ?? ''
  form.contract_end = service.contract_end ?? ''
  form.renewal_reminder_days = service.renewal_reminder_days ?? ''
  form.expected_monthly_cost = service.expected_monthly_cost ?? ''
  form.auto_pay = service.auto_pay ?? false
  form.alert_threshold_pct = service.alert_threshold_pct ?? 20
}

watch(() => props.initial, fill, { immediate: true })

function submit() {
  if (!form.name.trim() || !form.category.trim()) return
  const reminder = blankToNull(form.renewal_reminder_days)
  emit('submit', {
    name: form.name.trim(),
    category: form.category.trim(),
    account_number: blankToNull(form.account_number),
    contract_start: blankToNull(form.contract_start),
    contract_end: blankToNull(form.contract_end),
    renewal_reminder_days: reminder === null ? null : Number(reminder),
    expected_monthly_cost: blankToNull(form.expected_monthly_cost),
    auto_pay: form.auto_pay,
    alert_threshold_pct: Number(form.alert_threshold_pct),
  })
}
</script>

<template>
  <form @submit.prevent="submit">
    <label>Name <input v-model="form.name" name="name" required maxlength="200" /></label>
    <label>
      Category
      <input v-model="form.category" name="category" list="service-categories" required />
      <datalist id="service-categories">
        <option v-for="c in CATEGORIES" :key="c" :value="c" />
      </datalist>
    </label>
    <label>Account number <input v-model="form.account_number" name="account_number" /></label>
    <label>Contract start <input v-model="form.contract_start" name="contract_start" type="date" /></label>
    <label>Contract end <input v-model="form.contract_end" name="contract_end" type="date" /></label>
    <label>
      Remind me of the renewal (days before)
      <input v-model="form.renewal_reminder_days" name="renewal_reminder_days" type="number" min="0" max="365" />
    </label>
    <label>
      Expected monthly cost
      <input v-model="form.expected_monthly_cost" name="expected_monthly_cost" type="number" min="0" step="0.01" />
    </label>
    <label>
      Alert when a bill is above the usual by (%)
      <input v-model="form.alert_threshold_pct" name="alert_threshold_pct" type="number" min="0" max="1000" />
    </label>
    <label>
      <input v-model="form.auto_pay" name="auto_pay" type="checkbox" />
      Paid automatically (pre-authorised debit)
    </label>
    <button type="submit">{{ submitLabel }}</button>
  </form>
</template>

<style scoped>
form {
  display: grid;
  gap: 0.5rem;
  max-width: 28rem;
}

label {
  display: grid;
  gap: 0.2rem;
}
</style>
```

- [ ] **Step 4: Write the providers home**

Create `frontend/src/views/ProvidersView.vue`:

```vue
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import CostChart from '@/components/CostChart.vue'
import { money } from '@/format'
import { useHouseholdStore } from '@/stores/household'
import { useInvoicesStore } from '@/stores/invoices'
import { useProvidersStore } from '@/stores/providers'

const household = useHouseholdStore()
const providers = useProvidersStore()
const invoices = useInvoicesStore()
const newName = ref('')

const chart = computed(() =>
  invoices.monthly.map((m) => ({ label: m.month, value: Number(m.total) })),
)

onMounted(async () => {
  await household.load()
  if (household.household) await Promise.all([providers.load(), invoices.loadDashboard()])
})

async function addProvider() {
  const name = newName.value.trim()
  if (!name) return
  const created = await providers.createProvider({
    name, website: null, phone: null, email: null, notes: null,
  })
  if (created) newName.value = ''
}

async function onFiles(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files?.length) await invoices.upload(Array.from(input.files), undefined)
  input.value = ''
}
</script>

<template>
  <section>
    <h1>Providers</h1>

    <p v-if="household.loaded && !household.household">
      <RouterLink to="/household">Set up your household</RouterLink> to start tracking providers.
    </p>

    <template v-else-if="household.household">
      <p v-if="providers.error || invoices.error" class="error">
        {{ providers.error ?? invoices.error }}
      </p>

      <p v-if="invoices.toReview.length">
        <RouterLink to="/invoices/review">
          {{ invoices.toReview.length }} invoice{{ invoices.toReview.length > 1 ? 's' : '' }} to review
        </RouterLink>
      </p>

      <label v-if="household.canWrite">
        Add invoice PDFs
        <input type="file" accept="application/pdf,.pdf" multiple @change="onFiles" />
      </label>

      <h2>Upcoming</h2>
      <p v-if="!invoices.upcoming.invoices.length && !invoices.upcoming.renewals.length">
        Nothing due.
      </p>
      <ul>
        <li
          v-for="due in invoices.upcoming.invoices"
          :key="due.invoice_id"
          data-test="due"
          :class="{ overdue: due.overdue }"
        >
          <RouterLink :to="`/services/${due.service_id}`">
            {{ due.provider_name }} — {{ due.service_name }}
          </RouterLink>
          {{ money(due.total) }} due {{ due.due_on }}<template v-if="due.overdue"> (overdue)</template>
        </li>
        <li v-for="r in invoices.upcoming.renewals" :key="r.service_id" data-test="renewal">
          <RouterLink :to="`/services/${r.service_id}`">
            {{ r.provider_name }} — {{ r.service_name }}
          </RouterLink>
          contract ends {{ r.contract_end }} ({{ r.days_left }} days)
        </li>
      </ul>

      <h2>Household spending by month</h2>
      <CostChart :points="chart" />

      <h2>All providers</h2>
      <ul>
        <li v-for="p in providers.providers" :key="p.id">
          <RouterLink :to="`/providers/${p.id}`">{{ p.name }}</RouterLink>
          <ul>
            <li v-for="s in p.services.filter((x) => !x.archived)" :key="s.id">
              <RouterLink :to="`/services/${s.id}`">{{ s.name }}</RouterLink>
            </li>
          </ul>
        </li>
      </ul>

      <form v-if="household.canWrite" @submit.prevent="addProvider">
        <input v-model="newName" placeholder="New provider (e.g. Hydro-Québec)" maxlength="200" />
        <button type="submit">Add provider</button>
      </form>
    </template>
  </section>
</template>

<style scoped>
.error {
  color: #e06c75;
}

.overdue {
  color: #e06c75;
  font-weight: 600;
}
</style>
```

- [ ] **Step 5: Write the provider page**

Create `frontend/src/views/ProviderView.vue`:

```vue
<script setup lang="ts">
import { computed, onMounted, reactive, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import type { ServiceInput } from '@/api/providers'
import { blankToNull } from '@/format'
import { useHouseholdStore } from '@/stores/household'
import { useInvoicesStore } from '@/stores/invoices'
import { useProvidersStore } from '@/stores/providers'
import ServiceForm from '@/components/ServiceForm.vue'

const route = useRoute()
const router = useRouter()
const household = useHouseholdStore()
const providers = useProvidersStore()
const invoices = useInvoicesStore()

const id = computed(() => String(route.params.id))
const provider = computed(() => providers.providers.find((p) => p.id === id.value) ?? null)
const form = reactive({ name: '', website: '', phone: '', email: '', notes: '' })

watch(
  provider,
  (p) => {
    form.name = p?.name ?? ''
    form.website = p?.website ?? ''
    form.phone = p?.phone ?? ''
    form.email = p?.email ?? ''
    form.notes = p?.notes ?? ''
  },
  { immediate: true },
)

onMounted(async () => {
  await household.load()
  if (household.household) await providers.load()
})

async function save() {
  if (!form.name.trim()) return
  await providers.updateProvider(id.value, {
    name: form.name.trim(),
    website: blankToNull(form.website),
    phone: blankToNull(form.phone),
    email: blankToNull(form.email),
    notes: blankToNull(form.notes),
  })
}

async function addService(input: ServiceInput) {
  await providers.createService(id.value, input)
}

async function remove() {
  if (!confirm('Delete this provider?')) return
  await providers.deleteProvider(id.value)
  if (!providers.error) await router.replace('/providers')
}

async function onFiles(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files?.length) await invoices.upload(Array.from(input.files), { provider_id: id.value })
  input.value = ''
}
</script>

<template>
  <section>
    <p><RouterLink to="/providers">← Providers</RouterLink></p>
    <p v-if="providers.error" class="error">{{ providers.error }}</p>
    <p v-if="!provider && !providers.loading">No such provider.</p>

    <template v-if="provider">
      <h1>{{ provider.name }}</h1>

      <form v-if="household.canWrite" @submit.prevent="save">
        <label>Name <input v-model="form.name" required maxlength="200" /></label>
        <label>Website <input v-model="form.website" maxlength="500" /></label>
        <label>Phone <input v-model="form.phone" maxlength="50" /></label>
        <label>Email <input v-model="form.email" maxlength="254" /></label>
        <label>Notes <textarea v-model="form.notes" maxlength="4000" /></label>
        <button type="submit">Save</button>
      </form>
      <dl v-else>
        <dt>Website</dt><dd>{{ provider.website ?? '—' }}</dd>
        <dt>Phone</dt><dd>{{ provider.phone ?? '—' }}</dd>
        <dt>Email</dt><dd>{{ provider.email ?? '—' }}</dd>
        <dt>Notes</dt><dd>{{ provider.notes ?? '—' }}</dd>
      </dl>

      <h2>Services</h2>
      <ul>
        <li v-for="s in provider.services" :key="s.id">
          <RouterLink :to="`/services/${s.id}`">{{ s.name }}</RouterLink>
          <small v-if="s.archived"> (archived)</small>
        </li>
      </ul>

      <template v-if="household.canWrite">
        <label>
          Add invoice PDFs for this provider
          <input type="file" accept="application/pdf,.pdf" multiple @change="onFiles" />
        </label>
        <h3>Add a service</h3>
        <ServiceForm submit-label="Add service" @submit="addService" />
        <p>
          <button type="button" :disabled="provider.services.length > 0" @click="remove">
            Delete provider
          </button>
          <small v-if="provider.services.length"> Remove or archive its services first.</small>
        </p>
      </template>
    </template>
  </section>
</template>

<style scoped>
.error {
  color: #e06c75;
}

form {
  display: grid;
  gap: 0.5rem;
  max-width: 28rem;
}

label {
  display: grid;
  gap: 0.2rem;
}
</style>
```

- [ ] **Step 6: Write the service page**

Create `frontend/src/views/ServiceView.vue`:

```vue
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getServiceCosts, type ServiceCosts } from '@/api/costs'
import { listInvoices, type Invoice } from '@/api/invoices'
import { getService, type Service, type ServiceInput } from '@/api/providers'
import CostChart from '@/components/CostChart.vue'
import ServiceForm from '@/components/ServiceForm.vue'
import { blankToNull, money } from '@/format'
import { useHouseholdStore } from '@/stores/household'
import { useInvoicesStore } from '@/stores/invoices'
import { useProvidersStore } from '@/stores/providers'

const route = useRoute()
const router = useRouter()
const household = useHouseholdStore()
const providers = useProvidersStore()
const invoices = useInvoicesStore()

const id = computed(() => String(route.params.id))
const service = ref<Service | null>(null)
const costs = ref<ServiceCosts | null>(null)
const rows = ref<Invoice[]>([])
const manual = reactive({ total: '' as string | number, due_on: '', issued_on: '', invoice_number: '' })
const missing = ref(false)

const flagged = computed(() => new Set(costs.value?.points.filter((p) => p.flagged).map((p) => p.invoice_id)))
const chart = computed(() =>
  (costs.value?.points ?? []).map((p) => ({ label: p.month, value: Number(p.total), flagged: p.flagged })),
)

async function refresh() {
  ;[rows.value, costs.value] = await Promise.all([
    listInvoices({ service_id: id.value }),
    getServiceCosts(id.value),
  ])
}

onMounted(async () => {
  await household.load()
  if (!household.household) return
  try {
    service.value = await getService(id.value)
    await Promise.all([refresh(), providers.load()])
  } catch {
    missing.value = true
  }
})

async function save(input: ServiceInput) {
  const updated = await providers.updateService(id.value, input)
  if (updated) service.value = updated
}

async function archive() {
  const updated = await providers.updateService(id.value, { archived: !service.value?.archived })
  if (updated) service.value = updated
}

async function remove() {
  if (!confirm('Delete this service?')) return
  await providers.deleteService(id.value)
  if (!providers.error) await router.replace('/providers')
}

async function addManual() {
  const total = String(manual.total).trim()
  if (!total || !manual.due_on) return
  const created = await invoices.createManual({
    service_id: id.value,
    total,
    due_on: manual.due_on,
    issued_on: blankToNull(manual.issued_on),
    period_start: null,
    period_end: null,
    invoice_number: blankToNull(manual.invoice_number),
    consumption_qty: null,
    consumption_unit: null,
    taxes: [],
  })
  if (created) {
    Object.assign(manual, { total: '', due_on: '', issued_on: '', invoice_number: '' })
    await refresh()
  }
}

async function togglePaid(invoice: Invoice) {
  if (await invoices.markPaid(invoice.id, !invoice.paid)) await refresh()
}

async function removeInvoice(invoice: Invoice) {
  if (confirm('Delete this invoice? Its PDF stays in Files.') && (await invoices.remove(invoice.id))) {
    await refresh()
  }
}

async function onFiles(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files?.length) await invoices.upload(Array.from(input.files), { service_id: id.value })
  input.value = ''
}
</script>

<template>
  <section>
    <p><RouterLink to="/providers">← Providers</RouterLink></p>
    <p v-if="missing" class="error">No such service.</p>
    <p v-if="invoices.error || providers.error" class="error">
      {{ invoices.error ?? providers.error }}
    </p>

    <template v-if="service">
      <h1>{{ service.name }}<small v-if="service.archived"> (archived)</small></h1>
      <p>
        <RouterLink :to="`/providers/${service.provider_id}`">Provider</RouterLink> ·
        {{ service.category }}
        <template v-if="service.auto_pay"> · paid automatically</template>
      </p>

      <h2>Cost history</h2>
      <CostChart :points="chart" />
      <p v-if="costs">
        Expected {{ money(costs.expected_monthly_cost) }} a month; alert above usual by
        {{ costs.threshold_pct }}%.
      </p>

      <h2>Invoices</h2>
      <table v-if="rows.length">
        <thead>
          <tr><th>Number</th><th>Issued</th><th>Due</th><th>Total</th><th>Status</th><th /></tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.id" data-test="invoice">
            <td>{{ row.invoice_number ?? '—' }}</td>
            <td>{{ row.issued_on ?? '—' }}</td>
            <td>{{ row.due_on ?? '—' }}</td>
            <td>
              {{ money(row.total) }}
              <span v-if="flagged.has(row.id)" data-test="flag" class="flag" title="Above the usual">▲</span>
            </td>
            <td>{{ row.status === 'validated' ? (row.paid ? 'Paid' : 'Unpaid') : row.status }}</td>
            <td v-if="household.canWrite">
              <button
                v-if="row.status === 'validated'"
                type="button"
                data-test="toggle-paid"
                @click="togglePaid(row)"
              >
                {{ row.paid ? 'Mark unpaid' : 'Mark paid' }}
              </button>
              <button type="button" @click="removeInvoice(row)">Delete</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else>No invoices yet.</p>

      <template v-if="household.canWrite">
        <label>
          Add invoice PDFs for this service
          <input type="file" accept="application/pdf,.pdf" multiple @change="onFiles" />
        </label>

        <h3>Add an invoice by hand</h3>
        <form data-test="manual" @submit.prevent="addManual">
          <input v-model="manual.total" name="manual-total" type="number" step="0.01" min="0" placeholder="Total" required />
          <input v-model="manual.due_on" name="manual-due" type="date" required />
          <input v-model="manual.issued_on" name="manual-issued" type="date" />
          <input v-model="manual.invoice_number" name="manual-number" placeholder="Invoice number" />
          <button type="submit">Add invoice</button>
        </form>

        <h2>Settings</h2>
        <ServiceForm :initial="service" submit-label="Save" @submit="save" />
        <p>
          <button type="button" @click="archive">{{ service.archived ? 'Unarchive' : 'Archive' }}</button>
          <button type="button" @click="remove">Delete</button>
          <small> A service with invoices cannot be deleted: archive it.</small>
        </p>
      </template>
    </template>
  </section>
</template>

<style scoped>
.error {
  color: #e06c75;
}

.flag {
  color: #e06c75;
}
</style>
```

- [ ] **Step 7: Run the tests and the type-check**

Run: `cd frontend && npm run test -- tests/unit/CostChart.test.ts tests/unit/ServiceForm.test.ts tests/unit/ProvidersView.test.ts tests/unit/ServiceView.test.ts`
Expected: PASS. (`vue-tsc` still fails on the missing `ReviewView.vue` lazy import until Task 11; do not chase that error here.)

- [ ] **Step 8: Commit (only if the user has asked for commits)**

```bash
git add frontend
git commit -m "feat: provider, service and spending views with cost chart"
```

---

### Task 11: The review queue

**Files:**
- Create: `frontend/src/views/ReviewView.vue`
- Test: `frontend/tests/unit/ReviewView.test.ts`

**Interfaces:**
- Consumes: `useInvoicesStore` (`toReview`, `loadAll`, `validate`, `remove`, `duplicateOf`, `error`), `useProvidersStore` (`serviceOptions`, `load`), `useHouseholdStore` (`canWrite`), `invoicePdf` (Task 8).
- Produces: route `/invoices/review` view.

- [ ] **Step 1: Write the failing tests**

Create `frontend/tests/unit/ReviewView.test.ts`:

```ts
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { getHousehold } from '@/api/household'
import { deleteInvoice, invoicePdf, listInvoices, validateInvoice, type Invoice } from '@/api/invoices'
import { listProviders } from '@/api/providers'
import ReviewView from '@/views/ReviewView.vue'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/household', () => ({ getHousehold: vi.fn() }))
vi.mock('@/api/providers', () => ({ listProviders: vi.fn() }))
vi.mock('@/api/invoices', () => ({
  listInvoices: vi.fn(),
  validateInvoice: vi.fn(),
  deleteInvoice: vi.fn(async () => {}),
  invoicePdf: vi.fn(),
}))

const readable: Invoice = {
  id: 'i1', status: 'to_validate', service_id: null, has_pdf: true, total: '114.98',
  due_on: '2026-11-01', issued_on: '2026-10-01', period_start: null, period_end: null,
  invoice_number: 'A-1', consumption_qty: '1240', consumption_unit: 'kWh', paid_at: null,
  paid: false, error: null, taxes: [],
  extraction: {
    raw: { taxes: [{ name: 'TPS', amount: '5.00' }] },
    candidates: {
      provider: { id: 'p1', name: 'Bell' },
      service: { id: 's1', name: 'Internet' },
      new_provider: null,
      new_service: null,
    },
  },
}

const failed: Invoice = {
  ...readable, id: 'i2', status: 'failed', total: null, due_on: null, issued_on: null,
  invoice_number: null, consumption_qty: null, consumption_unit: null, extraction: null,
  error: 'no readable text: a scanned PDF?',
}

async function render() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: ReviewView }, { path: '/:rest(.*)', component: { template: '<p/>' } }],
  })
  await router.push('/')
  const wrapper = mount(ReviewView, { global: { plugins: [createPinia(), router] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  URL.createObjectURL = vi.fn(() => 'blob:pdf')
  URL.revokeObjectURL = vi.fn()
  vi.mocked(getHousehold).mockResolvedValue({ id: 'h', name: 'M', role: 'member', members: [] })
  vi.mocked(invoicePdf).mockResolvedValue(new Blob(['%PDF']))
  vi.mocked(listProviders).mockResolvedValue([
    {
      id: 'p1', name: 'Bell', website: null, phone: null, email: null, notes: null,
      services: [
        { id: 's1', provider_id: 'p1', name: 'Internet', category: 'internet', account_number: null,
          contract_start: null, contract_end: null, renewal_reminder_days: null,
          expected_monthly_cost: null, auto_pay: false, alert_threshold_pct: 20, archived: false },
      ],
    },
  ])
  vi.mocked(listInvoices).mockResolvedValue([readable, failed])
})

it('opens the first invoice with the form filled from what the model read', async () => {
  const wrapper = await render()

  const value = (name: string) =>
    (wrapper.find(`[name="${name}"]`).element as HTMLInputElement | HTMLSelectElement).value
  expect(value('service_id')).toBe('s1')
  expect(value('total')).toBe('114.98')
  expect(value('due_on')).toBe('2026-11-01')
  expect(value('invoice_number')).toBe('A-1')
  expect(wrapper.findAll('[data-test="tax"]')).toHaveLength(1)
  expect(wrapper.find('iframe').attributes('src')).toBe('blob:pdf')
})

it('validates with the corrected values', async () => {
  vi.mocked(validateInvoice).mockResolvedValue({ ...readable, status: 'validated', service_id: 's1' })
  const wrapper = await render()

  await wrapper.find('input[name="total"]').setValue('120.00')
  await wrapper.find('form').trigger('submit')
  await flushPromises()

  // A type="number" input is cast to a number by v-model, so '120.00' becomes 120.
  expect(validateInvoice).toHaveBeenCalledWith('i1', {
    service_id: 's1',
    total: '120',
    due_on: '2026-11-01',
    issued_on: '2026-10-01',
    period_start: null,
    period_end: null,
    invoice_number: 'A-1',
    consumption_qty: '1240',
    consumption_unit: 'kWh',
    taxes: [{ name: 'TPS', amount: '5.00' }],
  })
})

it('moves on to the next invoice after validating one', async () => {
  vi.mocked(validateInvoice).mockResolvedValue({ ...readable, status: 'validated', service_id: 's1' })
  const wrapper = await render()

  await wrapper.find('form').trigger('submit')
  await flushPromises()

  expect(wrapper.text()).toContain('no readable text')
})

it('lets a failed invoice be filled in by hand', async () => {
  const wrapper = await render()
  await wrapper.findAll('[data-test="queue-item"]')[1].trigger('click')
  await flushPromises()

  expect(wrapper.text()).toContain('no readable text')
  expect((wrapper.find('input[name="total"]').element as HTMLInputElement).value).toBe('')
  expect(wrapper.find('form').exists()).toBe(true)
})

it('reports a duplicate number with the id it duplicates', async () => {
  vi.mocked(validateInvoice).mockRejectedValue(
    new ApiError('Conflict', 409, { detail: { message: 'Duplicate', existing_id: 'old-1' } }),
  )
  const wrapper = await render()

  await wrapper.find('form').trigger('submit')
  await flushPromises()

  expect(wrapper.find('[data-test="duplicate"]').text()).toContain('old-1')
})

it('suggests creating a provider the model found but the household lacks', async () => {
  vi.mocked(listInvoices).mockResolvedValue([
    {
      ...readable,
      extraction: {
        raw: {},
        candidates: { provider: null, service: null, new_provider: 'Vidéotron', new_service: 'Internet' },
      },
    },
  ])

  const wrapper = await render()

  expect(wrapper.find('[data-test="new-provider"]').text()).toContain('Vidéotron')
})

it('shows a message when the PDF is gone', async () => {
  vi.mocked(invoicePdf).mockRejectedValue(new ApiError('The PDF is missing', 404, null))

  const wrapper = await render()

  expect(wrapper.find('iframe').exists()).toBe(false)
  expect(wrapper.text()).toContain('PDF missing')
})

it('deletes an invoice from the queue', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  const wrapper = await render()

  await wrapper.find('button[data-test="delete"]').trigger('click')
  await flushPromises()

  expect(deleteInvoice).toHaveBeenCalledWith('i1')
})

it('is read-only for a viewer', async () => {
  vi.mocked(getHousehold).mockResolvedValue({ id: 'h', name: 'M', role: 'viewer', members: [] })

  const wrapper = await render()

  expect(wrapper.find('button[type="submit"]').exists()).toBe(false)
  expect(wrapper.find('button[data-test="delete"]').exists()).toBe(false)
})

it('says so when nothing is waiting', async () => {
  vi.mocked(listInvoices).mockResolvedValue([])

  const wrapper = await render()

  expect(wrapper.text()).toContain('Nothing to review')
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npm run test -- tests/unit/ReviewView.test.ts`
Expected: FAIL (`ReviewView.vue` missing).

- [ ] **Step 3: Write the view**

Create `frontend/src/views/ReviewView.vue`:

```vue
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import { invoicePdf, type Invoice, type InvoiceStatus } from '@/api/invoices'
import { blankToNull } from '@/format'
import { useHouseholdStore } from '@/stores/household'
import { useInvoicesStore } from '@/stores/invoices'
import { useProvidersStore } from '@/stores/providers'

const household = useHouseholdStore()
const providers = useProvidersStore()
const invoices = useInvoicesStore()

const selectedId = ref<string | null>(null)
const pdfUrl = ref<string | null>(null)
const pdfMissing = ref(false)
const form = reactive({
  service_id: '',
  total: '' as string | number,
  due_on: '',
  issued_on: '',
  period_start: '',
  period_end: '',
  invoice_number: '',
  consumption_qty: '' as string | number,
  consumption_unit: '',
  taxes: [] as { name: string; amount: string | number }[],
})

const current = computed(() => invoices.toReview.find((i) => i.id === selectedId.value) ?? null)
const reading = computed(() => current.value?.status === 'queued' || current.value?.status === 'extracting')
const candidates = computed(() => current.value?.extraction?.candidates ?? null)

function fill(invoice: Invoice | null) {
  const raw = invoice?.extraction?.raw
  form.service_id = invoice?.service_id ?? invoice?.extraction?.candidates?.service?.id ?? ''
  form.total = invoice?.total ?? ''
  form.due_on = invoice?.due_on ?? ''
  form.issued_on = invoice?.issued_on ?? ''
  form.period_start = invoice?.period_start ?? ''
  form.period_end = invoice?.period_end ?? ''
  form.invoice_number = invoice?.invoice_number ?? ''
  form.consumption_qty = invoice?.consumption_qty ?? ''
  form.consumption_unit = invoice?.consumption_unit ?? ''
  form.taxes = (raw?.taxes ?? []).map((t) => ({ name: t.name, amount: t.amount }))
}

async function showPdf(invoice: Invoice | null) {
  if (pdfUrl.value) URL.revokeObjectURL(pdfUrl.value)
  pdfUrl.value = null
  pdfMissing.value = false
  if (!invoice) return
  if (!invoice.has_pdf) {
    pdfMissing.value = true
    return
  }
  try {
    pdfUrl.value = URL.createObjectURL(await invoicePdf(invoice.id))
  } catch {
    pdfMissing.value = true
  }
}

// Only a change of selection refills the form: reloading the list must not
// wipe what the person is typing.
watch(selectedId, () => {
  fill(current.value)
  showPdf(current.value)
})

onMounted(async () => {
  await household.load()
  if (!household.household) return
  await Promise.all([providers.load(), invoices.loadAll()])
  selectedId.value = invoices.toReview[0]?.id ?? null
})

onBeforeUnmount(() => {
  if (pdfUrl.value) URL.revokeObjectURL(pdfUrl.value)
})

function selectNext() {
  selectedId.value = invoices.toReview[0]?.id ?? null
}

async function validate() {
  if (!current.value) return
  const taxes = form.taxes
    .filter((t) => t.name.trim() && blankToNull(t.amount) !== null)
    .map((t) => ({ name: t.name.trim(), amount: String(t.amount).trim() }))
  const done = await invoices.validate(current.value.id, {
    service_id: form.service_id,
    total: String(form.total).trim(),
    due_on: form.due_on,
    issued_on: blankToNull(form.issued_on),
    period_start: blankToNull(form.period_start),
    period_end: blankToNull(form.period_end),
    invoice_number: blankToNull(form.invoice_number),
    consumption_qty: blankToNull(form.consumption_qty),
    consumption_unit: blankToNull(form.consumption_unit),
    taxes,
  })
  if (done) selectNext()
}

async function remove() {
  if (!current.value || !confirm('Delete this invoice? Its PDF stays in Files.')) return
  if (await invoices.remove(current.value.id)) selectNext()
}

function label(invoice: Invoice): string {
  const states: Partial<Record<InvoiceStatus, string>> = {
    queued: 'waiting',
    extracting: 'reading…',
    failed: 'could not read',
  }
  return `${invoice.invoice_number ?? 'Invoice'} — ${states[invoice.status] ?? 'to review'}`
}
</script>

<template>
  <section>
    <h1>Invoices to review</h1>
    <p v-if="invoices.error && !invoices.duplicateOf" class="error">{{ invoices.error }}</p>
    <p v-if="household.loaded && !household.household">
      <RouterLink to="/household">Set up your household</RouterLink> first.
    </p>

    <p v-else-if="household.household && !invoices.toReview.length && !invoices.loading">
      Nothing to review. <RouterLink to="/providers">Back to providers</RouterLink>
    </p>

    <div v-if="invoices.toReview.length" class="layout">
      <ul class="queue">
        <li v-for="i in invoices.toReview" :key="i.id">
          <button
            type="button"
            data-test="queue-item"
            :class="{ active: i.id === selectedId }"
            @click="selectedId = i.id"
          >
            {{ label(i) }}
          </button>
        </li>
      </ul>

      <div v-if="current">
        <p v-if="reading">
          Still being read.
          <button type="button" @click="invoices.loadAll()">Refresh</button>
        </p>
        <p v-else-if="current.status === 'failed'" class="error">
          Could not read this invoice ({{ current.error }}). Fill it in by hand.
        </p>

        <p v-if="candidates?.new_provider" data-test="new-provider">
          This looks like a provider you do not have yet: <strong>{{ candidates.new_provider }}</strong>
          <template v-if="candidates.new_service"> ({{ candidates.new_service }})</template>.
          <RouterLink to="/providers">Create it</RouterLink>, then reload this page.
        </p>

        <p v-if="pdfMissing" data-test="pdf-missing">PDF missing: the file was deleted.</p>
        <iframe v-else-if="pdfUrl" :src="pdfUrl" title="Invoice PDF" />

        <p v-if="invoices.duplicateOf" data-test="duplicate" class="error">
          This number is already recorded for that service (invoice {{ invoices.duplicateOf }}).
        </p>

        <form v-if="household.canWrite" @submit.prevent="validate">
          <label>
            Service
            <select v-model="form.service_id" name="service_id" required>
              <option value="" disabled>Choose a service</option>
              <option v-for="o in providers.serviceOptions" :key="o.id" :value="o.id">{{ o.label }}</option>
            </select>
          </label>
          <label>Total <input v-model="form.total" name="total" type="number" step="0.01" min="0" required /></label>
          <label>Due <input v-model="form.due_on" name="due_on" type="date" required /></label>
          <label>Issued <input v-model="form.issued_on" name="issued_on" type="date" /></label>
          <label>Period start <input v-model="form.period_start" name="period_start" type="date" /></label>
          <label>Period end <input v-model="form.period_end" name="period_end" type="date" /></label>
          <label>Invoice number <input v-model="form.invoice_number" name="invoice_number" /></label>
          <label>
            Consumption
            <input v-model="form.consumption_qty" name="consumption_qty" type="number" step="0.001" min="0" />
            <input v-model="form.consumption_unit" name="consumption_unit" placeholder="kWh, GB…" />
          </label>

          <fieldset>
            <legend>Taxes</legend>
            <div v-for="(tax, index) in form.taxes" :key="index" data-test="tax">
              <input v-model="tax.name" placeholder="Name" />
              <input v-model="tax.amount" type="number" step="0.01" min="0" placeholder="Amount" />
              <button type="button" @click="form.taxes.splice(index, 1)">Remove</button>
            </div>
            <button type="button" @click="form.taxes.push({ name: '', amount: '' })">Add a tax</button>
          </fieldset>

          <button type="submit" :disabled="reading">Validate</button>
          <button type="button" data-test="delete" @click="remove">Delete</button>
        </form>
      </div>
    </div>
  </section>
</template>

<style scoped>
.error {
  color: #e06c75;
}

.layout {
  display: grid;
  grid-template-columns: minmax(12rem, 16rem) 1fr;
  gap: 1rem;
}

.queue {
  list-style: none;
  padding: 0;
}

.queue button.active {
  font-weight: 600;
}

iframe {
  width: 100%;
  height: 24rem;
  border: 1px solid #8884;
}

form {
  display: grid;
  gap: 0.5rem;
  max-width: 28rem;
}

label {
  display: grid;
  gap: 0.2rem;
}
</style>
```

Notes for the implementer: the `watch(selectedId, …)` refills the form only when the selection changes, so the order in `onMounted` matters (the list loads first, then `selectedId` is set). The form is wrapped in `v-if="household.canWrite"`, so a viewer sees the PDF and the queue but no controls; the `Validate` and `Delete` buttons live inside that `<form>`.

- [ ] **Step 4: Run the tests, then the whole frontend gate**

Run: `cd frontend && npm run test && npm run build`
Expected: every test PASSES; `npm run build` (vue-tsc + vite) now succeeds because all lazy-loaded views exist. If `vue-tsc` flags `$event.target` casts in `HouseholdView.vue`, replace the inline cast with a small handler function in the script block.

- [ ] **Step 5: Commit (only if the user has asked for commits)**

```bash
git add frontend
git commit -m "feat: review queue for invoices read by the model"
```

---

### Task 12: Documentation and the full gate

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `docs/superpowers/specs/2026-10-10-service-providers-design.md` (apply the "Spec deltas"), `docs/testing.md` (test counts, only if the file states exact counts that are now wrong)

- [ ] **Step 1: Document the feature in the README**

Add a `## Service providers` section after `## Files` in `README.md` with: what it does in three sentences (household, providers/services, invoices read from PDFs by Ollama and validated by hand, upcoming dues, cost alerts); the roles (`owner`, `member`, `viewer`); that invoice PDFs are ordinary files of the uploader's; that reading needs `DARKANGEL_OLLAMA_URL` (already documented under Files) and that without it uploaded invoices go straight to the review queue to be filled by hand; and that email/push reminders are not part of this feature (see `docs/infra-feature-request-notifications.md`).

- [ ] **Step 2: Update the architecture notes in `CLAUDE.md`**

In the `Backend` list add one bullet: `core/household.py` — the `Reader` / `Writer` / `Owner` dependencies resolve the caller's household and role; routes for providers, services and invoices take one of them instead of `Claims`, and every repository query filters on `household_id`. `api/routes/household.py`, `providers.py`, `invoices.py`, `costs.py` and `invoice_extraction.py` (Ollama reads invoice PDFs; mirrors `summary.py`). In the `Frontend` list add: `stores/{household,providers,invoices}.ts` and the `views/{Providers,Provider,Service,Review,Household,Join}View.vue` pages.

- [ ] **Step 3: Apply the spec deltas to the spec**

In `docs/superpowers/specs/2026-10-10-service-providers-design.md`: add `name` to the `services` bullet in §5; add `GET /api/invoices/{id}/pdf` and `POST /api/invoices/manual` to §6; change §7 point 5 and §11 to "queued or extracting"; add one sentence to §7 saying that without `DARKANGEL_OLLAMA_URL` an uploaded invoice goes straight to `to_validate`; change the status line to `approuvé`.

- [ ] **Step 4: Run the full local gate**

Run: `make format && make verify`
Expected: format, lint, type-check, backend unit + regression + integration (start `make services-test-up` first), frontend tests and build, and the coverage gate (`--cov-fail-under`) all pass. If coverage of `app/invoice_extraction.py` or `app/api/routes/invoices.py` is below the gate, add the missing branch tests rather than lowering the gate.

- [ ] **Step 5: Smoke-test the real thing once**

With the backend (`uvicorn`) and the frontend (`npm run dev`) running against a local Postgres, SeaweedFS and Ollama: create a household, add the provider "Bell" with a service "Internet", drop one real invoice PDF on the Providers page, open the review queue, check the form is prefilled and the PDF shows, validate it, and confirm it appears in the service's history and in "Upcoming". Report what the model got wrong; that feeds the prompt in `invoice_extraction.PROMPT`.

- [ ] **Step 6: Commit (only if the user has asked for commits)**

```bash
git add README.md CLAUDE.md docs
git commit -m "docs: service providers feature"
```

---

## Self-review (done while writing)

- **Spec coverage:** households, roles, invitation links, expiry and single use (§4) → Tasks 1, 2, 9. Data model (§5) → Tasks 1, 3. Routes (§6) → Tasks 2, 3, 5, 7. Reading by Ollama, matching, restart sweep (§7) → Task 6 (+ Task 5 upload). Alerts and "À venir" (§8) → Tasks 4, 7. Interface (§9) → Tasks 8 to 11. Tests (§10) → every task. Out of scope items (email/push, sharing PDFs, retry button for a failed read) are not planned.
- **Placeholders:** none; every step carries its code or exact command.
- **Type consistency:** repository method names, `InvoiceFields` / `InvoiceInfo`, store action names and the `extraction` JSON shape (`raw`, `candidates`, `provider_id`) are used identically in the fakes, the routes, the extraction module and the frontend types.
- **Known soft spots to watch when executing:** (1) tests import `service_for` from `tests.unit.test_invoices` in `test_costs_routes.py`; if that import ever causes a collection problem, move the helper into `tests/conftest.py`. (2) `ruff format` will reflow the long test lines; commit the formatted result. (3) the Vue inline casts in templates (`$event.target as …`) may need to move into script handlers if `vue-tsc` objects.
