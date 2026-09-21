import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from textwrap import shorten
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from minio.error import S3Error

from app.api.routes import files
from app.core import auth
from app.main import app
from app.models.files import File
from app.repositories.files import QuotaExceeded, file_repository

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
    # nine times over the same unreachable MinIO.
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
    # Autouse everywhere, integration included: only MinIO is real in this project.
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


class FakeMinio:
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
    """Swap MinIO for FakeMinio. Explicit, never autouse: the integration
    suite must keep talking to the real service."""
    fake = FakeMinio()
    monkeypatch.setattr(files, "minio_client", lambda: fake)
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

    def _live(self, owner_sub):
        return [
            r
            for r in self.rows
            if r.owner_sub == owner_sub and r.deleted_at is None and r.status == "ready"
        ]

    def list(self, owner_sub, *, limit=100, offset=0):
        return self._live(owner_sub)[offset : offset + limit]

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

    def sweep_pending(self, older_than_seconds=3600):
        return self.swept

    def audit(self, actor_sub, action, target_type, target_id, detail=None):
        self.audits.append((actor_sub, action, target_type, target_id, detail))


@pytest.fixture
def repo():
    """Swap the repository for the in-memory fake. Explicit, never autouse:
    the integration suite must keep talking to the real database."""
    fake = FakeFileRepository()
    app.dependency_overrides[file_repository] = lambda: fake
    yield fake
    app.dependency_overrides.pop(file_repository, None)
