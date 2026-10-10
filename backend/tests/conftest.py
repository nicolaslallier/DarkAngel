import secrets
import time
import uuid
from collections import Counter
from datetime import UTC, date, datetime
from textwrap import shorten
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from minio.error import S3Error

from app.api.routes import files
from app.core import auth
from app.core.clock import today
from app.main import app
from app.models.files import File, Folder
from app.models.households import Household, HouseholdMember
from app.models.infra import BackupStatus, InfraStatus
from app.models.providers import Invoice, InvoiceTax, Provider, Service
from app.repositories.files import NameTaken, QuotaExceeded, file_repository
from app.repositories.folders import folder_repository
from app.repositories.households import (
    INVITATION_TTL,
    AlreadyMember,
    InvitationInvalid,
    household_repository,
)
from app.repositories.infra import infra_repository
from app.repositories.invoices import invoice_repository
from app.repositories.providers import provider_repository

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ISSUER = "https://keycloak.famillelallier.net/realms/ea"

MARKERS = ("unit", "integration", "regression")


def pytest_collection_modifyitems(items):
    # The directory a test lives in is its suite, so no test needs a decorator.
    for item in items:
        suite = item.path.parent.name
        if suite not in MARKERS:
            raise pytest.UsageError(
                f"{item.path} is not in tests/{{unit,integration,regression}}/, "
                "so no suite would run it."
            )
        item.add_marker(suite)


def _tests(count: int) -> str:
    return f"{count:>3} test{'s' if count > 1 else ''}"


def pytest_terminal_summary(terminalreporter):
    """Name the modules this pass ran, and everything it did not run.

    pytest's own last line says "5 passed, 23 deselected" and stops there: a
    run narrowed by `-m` looks identical whether it skipped one suite or two.
    """
    tr = terminalreporter

    ran = Counter(
        report.nodeid.split("::")[0]
        for reports in tr.stats.values()
        for report in reports
        # Deselected Items and warnings share this dict and have no `.when`.
        if getattr(report, "when", None) == "call"
    )
    if ran:
        tr.write_sep("-", "modules run")
        for module, count in sorted(ran.items()):
            tr.write_line(f"  {module:<52}{_tests(count)}")

    # A skip is one line per reason, not per test: the integration suite skips
    # nine times over the same unreachable object store.
    skipped = Counter(
        report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
        for report in tr.stats.get("skipped", [])
    )
    if skipped:
        tr.write_sep("-", "skipped at runtime")
        for reason, count in skipped.most_common():
            tr.write_line(f"  {_tests(count)}  {shorten(reason, 96, placeholder=' …')}")

    left = Counter(item.path.parent.name for item in tr.stats.get("deselected", []))
    if left:
        tr.write_sep("-", "deselected by -m (not run in this pass)")
        for suite, count in sorted(left.items()):
            tr.write_line(f"  {suite:<52}{_tests(count)}   -> make test-{suite}")


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    # Stands in for Keycloak's JWKS endpoint: every token verifies against KEY.
    # Autouse everywhere, integration included: only object storage is real in this project.
    signing_key = SimpleNamespace(key=KEY.public_key())
    fake = SimpleNamespace(get_signing_key_from_jwt=lambda _token: signing_key)
    monkeypatch.setattr(auth, "jwks_client", lambda: fake)


def token(**overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": "darkangel-api",
        "sub": "user-1",
        "iat": now,
        "exp": now + 300,
        "preferred_username": "nicolas",
        "realm_access": {"roles": ["ea-editor"]},
    } | overrides
    return jwt.encode(claims, KEY, algorithm="RS256")


class FakeS3:
    """The slice of minio.Minio the files routes use, over a dict."""

    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put_object(self, _bucket, key, data, length, content_type, part_size=None):
        self.objects[key] = (data.read(), content_type)
        # The real client returns an ObjectWriteResult; only version_id is read.
        return SimpleNamespace(version_id=f"v-{len(self.objects)}")

    def get_object(self, _bucket, key):
        if key not in self.objects:
            raise S3Error(None, "NoSuchKey", "missing", key, "", "")
        data, content_type = self.objects[key]
        return SimpleNamespace(
            headers={"Content-Type": content_type, "Content-Length": str(len(data))},
            stream=lambda _size: iter([data]),
            close=lambda: None,
            release_conn=lambda: None,
        )

    def remove_object(self, _bucket, key):
        self.objects.pop(key, None)


@pytest.fixture
def store(monkeypatch):
    """Swap object storage for FakeS3. Explicit, never autouse: the integration
    suite must keep talking to the real service."""
    fake = FakeS3()
    monkeypatch.setattr(files, "s3_client", lambda: fake)
    return fake


class FakeFileRepository:
    """The slice of FileRepository the routes use, over a list.

    It stores real `File` model objects: unattached to a session they are just
    data holders, so the fake cannot drift from the real column names.
    """

    def __init__(self):
        self.rows: list[File] = []
        self.versions: dict[uuid.UUID, int] = {}
        self.audits: list[tuple] = []
        self.swept: list[str] = []
        self.folders = FakeFolderRepository(self)

    def _live(self, owner_sub):
        return [
            r
            for r in self.rows
            if r.owner_sub == owner_sub and r.deleted_at is None and r.status == "ready"
        ]

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

    def get(self, owner_sub, file_id):
        return next((r for r in self._live(owner_sub) if r.id == file_id), None)

    def find_by_name(self, owner_sub, name, folder_id):
        return next(
            (
                r
                for r in self._live(owner_sub)
                if r.name.lower() == name.lower() and r.folder_id == folder_id
            ),
            None,
        )

    def used_bytes(self, owner_sub):
        # No deleted_at filter, matching FileRepository.used_bytes: BR-9 says
        # trashed files keep counting until purged.
        return sum(r.size_bytes for r in self.rows if r.owner_sub == owner_sub)

    def reserve(self, owner_sub, *, name, folder_id, size_bytes, content_type, quota_bytes):
        used = self.used_bytes(owner_sub)
        if used + size_bytes > quota_bytes:
            raise QuotaExceeded(used, quota_bytes, size_bytes)
        file_id = uuid.uuid4()
        row = File(
            id=file_id,
            owner_sub=owner_sub,
            folder_id=folder_id,
            name=name,
            content_type=content_type,
            size_bytes=size_bytes,
            object_key=f"{owner_sub}/{file_id}",
            status="pending",
            tags=[],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.rows.append(row)
        return row

    def finalize(self, file, *, s3_version_id, actor_sub):
        file.status = "ready"
        file.current_version_id = uuid.uuid4()
        self.versions[file.id] = 1
        return file

    def add_version(self, file, *, s3_version_id, size_bytes, content_type, actor_sub):
        self.versions[file.id] = self.versions.get(file.id, 1) + 1
        file.size_bytes = size_bytes
        file.content_type = content_type
        file.updated_at = datetime.now(UTC)
        return file

    def abandon(self, file):
        self.rows.remove(file)

    def soft_delete(self, file):
        file.deleted_at = datetime.now(UTC)

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

    def sweep_pending(self, older_than_seconds=3600):
        return self.swept

    def audit(self, actor_sub, action, target_type, target_id, detail=None):
        self.audits.append((actor_sub, action, target_type, target_id, detail))


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

    def add_member(self, household_id, sub, role, display_name=None):
        member = HouseholdMember(
            sub=sub, household_id=household_id, role=role, display_name=display_name
        )
        self._members[sub] = member
        return member

    def create(self, sub, name, display_name=None):
        if sub in self._members:
            raise AlreadyMember
        household = Household(id=uuid.uuid4(), name=name)
        self.households[household.id] = household
        self.add_member(household.id, sub, "owner", display_name)
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

    def redeem(self, token, sub, display_name=None):
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
        return self.add_member(invitation["household_id"], sub, invitation["role"], display_name)


class FakeProviderRepository:
    """ProviderRepository over lists. `invoices` is wired by the ledger fixture
    once the invoice fake exists (Task 5); `invoices_for_service` is the
    stand-in until then."""

    def __init__(self):
        self.providers: list[Provider] = []
        self.service_rows: list[Service] = []
        self.invoices = None

    def list_providers(self, household_id):
        rows = [p for p in self.providers if p.household_id == household_id]
        return sorted(rows, key=lambda p: (p.name.lower(), p.id))

    def get_provider(self, household_id, provider_id):
        return next(
            (p for p in self.providers if p.household_id == household_id and p.id == provider_id),
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
            (s for s in self.service_rows if s.household_id == household_id and s.id == service_id),
            None,
        )

    def create_service(self, household_id, provider_id, **fields):
        row = Service(id=uuid.uuid4(), household_id=household_id, provider_id=provider_id, **fields)
        self.service_rows.append(row)
        return row

    def update_service(self, service, **changes):
        for field, value in changes.items():
            setattr(service, field, value)
        return service

    def delete_service(self, service):
        self.service_rows.remove(service)

    def service_has_invoices(self, service_id):
        return any(i.service_id == service_id for i in self.invoices.rows)


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
        if invoice.status not in ("queued", "extracting"):
            return False
        for field, value in (fields or {}).items():
            setattr(invoice, field, value)
        invoice.status = status
        invoice.extraction = extraction if extraction is not None else invoice.extraction
        invoice.error = error
        return True

    def reset_stuck(self):
        stuck = [r for r in self.rows if r.status in ("queued", "extracting")]
        for row in stuck:
            row.status, row.error = "failed", "Interrupted by a restart"
        return len(stuck)


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


class FakeInfraRepository:
    """The slice of InfraRepository the routes and the collector use, over lists.

    Real model objects, like the other fakes, so column names cannot drift."""

    def __init__(self):
        self.statuses: list[InfraStatus] = []
        self.backups: list[BackupStatus] = []

    def add_status(
        self,
        *,
        instance,
        reachable,
        version=None,
        environments=None,
        stacks=None,
        error=None,
        checked_at=None,
    ):
        row = InfraStatus(
            instance=instance,
            reachable=reachable,
            version=version,
            environments=environments,
            stacks=stacks,
            error=error,
            checked_at=checked_at or datetime.now(UTC),
        )
        self.statuses.append(row)
        return row

    def add_backup(self, *, instance, last_backup_at, size_bytes, object_key, checked_at=None):
        row = BackupStatus(
            instance=instance,
            last_backup_at=last_backup_at,
            size_bytes=size_bytes,
            object_key=object_key,
            checked_at=checked_at or datetime.now(UTC),
        )
        self.backups.append(row)
        return row

    @staticmethod
    def _latest(rows):
        newest = {}
        for row in rows:
            if row.instance not in newest or row.checked_at >= newest[row.instance].checked_at:
                newest[row.instance] = row
        return [newest[name] for name in sorted(newest)]

    def latest_statuses(self):
        return self._latest(self.statuses)

    def latest_backups(self):
        return self._latest(self.backups)

    def prune(self, before):
        count = len(self.statuses) + len(self.backups)
        self.statuses = [r for r in self.statuses if r.checked_at >= before]
        self.backups = [r for r in self.backups if r.checked_at >= before]
        return count - len(self.statuses) - len(self.backups)


@pytest.fixture
def infra():
    """Swap the infra repository for an in-memory fake. Explicit, never autouse."""
    fake = FakeInfraRepository()
    app.dependency_overrides[infra_repository] = lambda: fake
    yield fake
    app.dependency_overrides.pop(infra_repository, None)
