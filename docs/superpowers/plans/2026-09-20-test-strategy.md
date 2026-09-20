# Test Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give DarkAngel unit, integration and regression suites on both sides, wired into the Makefile and into three parallel GitHub Actions jobs.

**Architecture:** Backend tests split into `tests/{unit,integration,regression}/`, each directory auto-marked by a `pytest_collection_modifyitems` hook so no test carries a decorator. Unit and regression tests keep the in-memory `FakeMinio` double and the fake JWKS; integration tests drive the real FastAPI app over `TestClient` against a real MinIO in a throwaway bucket. The frontend gains Vitest with jsdom, mirroring the same `tests/{unit,regression}/` split.

**Tech Stack:** Python 3.11, FastAPI, pytest 8, pytest-cov, minio-py, PyJWT; Vue 3, Vite 8, Vitest, @vue/test-utils, jsdom, Pinia, vue-router.

**Spec:** `docs/superpowers/specs/2026-09-20-test-strategy-design.md`

## Global Constraints

- Backend tools are always called through `backend/.venv/bin/` — there is no global Python for this project.
- Backend line length is 100 columns; ruff `select = ["E", "F", "I", "UP", "B"]` must stay clean (`make lint-backend`).
- `backend/tests/` is a package (`__init__.py` present). **Every new test subdirectory needs its own `__init__.py`**, or imports of `tests.conftest` break.
- Frontend components are `<script setup lang="ts">`; imports use the `@/` alias, never relative paths that climb directories.
- Settings are read through the cached `get_settings()`; `Settings()` is never instantiated directly. Environment variables use the `DARKANGEL_` prefix.
- `get_settings()` and `app.api.routes.files.minio_client()` are both `@lru_cache`d — any test that changes their inputs must call `.cache_clear()` on both.
- `COVERAGE_MIN` is 80. Measured baseline today: **96%** from unit tests alone (uncovered: `files.py:26-27,76` and `auth.py:15-19`, all of which the integration suite exercises). The 80 gate is safe to enforce on unit+regression alone.
- `deploy.yml` is never modified. New jobs go only into `.github/workflows/ci.yml`, which runs on GitHub-hosted `ubuntu-latest`.
- Commit after every task.

## Deviation from the spec

The spec says `FakeMinio` moves to `tests/unit/`. It cannot: Task 2 moves
`test_rejects_unsafe_names` into `tests/regression/`, and that test needs the
same double. `FakeMinio` and the `store` fixture therefore live in the shared
`backend/tests/conftest.py`, with `store` **not** autouse, so the integration
suite simply never requests it and keeps talking to real MinIO. Everything
else follows the spec.

## Verified before writing

These were run against a throwaway copy of `backend/`, so the expected outputs
below are observed, not guessed:

- Current coverage is **96%** from unit tests alone.
- Pre-change, `pytest -m unit` reports `14 deselected`.
- The directory→marker hook in Task 1 turns that into `14 passed`.
- The conftest import block in Task 1 Step 4 is `ruff check` clean as written.
- The Task 3 snapshot cycle works end to end: `FileNotFoundError` before the
  snapshot exists, `wrote .../openapi.snapshot.json` on regeneration, `1 passed`
  after, and the intended failure message when a response model changes.

The frontend tasks (5–8) and the CI workflow (9) were not executed.

## File Structure

| File | Responsibility |
|---|---|
| `backend/tests/conftest.py` | Shared: directory→marker hook, `fake_jwks`, `token()`, `FakeMinio`, `store` fixture |
| `backend/tests/unit/{__init__,test_auth,test_files,test_health}.py` | Fast in-process tests, every service faked |
| `backend/tests/integration/{__init__,conftest}.py` | Throwaway-bucket fixture + reachability gate |
| `backend/tests/integration/test_files_minio.py` | The files API against real S3 |
| `backend/tests/regression/{__init__}.py` | Pinned bugs + API contract |
| `backend/tests/regression/test_openapi_contract.py` | Compares `app.openapi()` to the snapshot; `python -m` regenerates it |
| `backend/tests/regression/openapi.snapshot.json` | The pinned public API shape |
| `docker-compose.test.yml` | MinIO only, for running the integration suite locally |
| `frontend/tests/unit/*.test.ts` | Vitest unit/component tests |
| `frontend/tests/regression/*.test.ts` | Pinned frontend bugs |
| `docs/testing.md` | The testing guide the README links to |

---

### Task 1: Markers and the unit directory

Moves the three existing test files under `tests/unit/`, registers the three
markers, and makes the directory itself decide the marker. No test behaviour
changes: the same 14 tests must still pass.

**Files:**
- Modify: `backend/pyproject.toml` (`[tool.pytest.ini_options]`)
- Modify: `backend/tests/conftest.py`
- Create: `backend/tests/unit/__init__.py`
- Move: `backend/tests/test_auth.py` → `backend/tests/unit/test_auth.py`
- Move: `backend/tests/test_files.py` → `backend/tests/unit/test_files.py`
- Move: `backend/tests/test_health.py` → `backend/tests/unit/test_health.py`
- Modify: `Makefile` (`.PHONY`, new `test-unit`)

**Interfaces:**
- Consumes: nothing.
- Produces: `tests.conftest.token(**overrides) -> str`, `tests.conftest.FakeMinio`,
  the `store` pytest fixture (returns a `FakeMinio`), and the markers
  `unit` / `integration` / `regression`, applied from the test's parent
  directory name. Later tasks rely on all of these.

- [ ] **Step 1: Run the selection that does not work yet**

Run: `cd backend && .venv/bin/python -m pytest -m unit -q`

Expected: `no tests ran ... 14 deselected` — no test carries the `unit` mark, so
the selection is empty. That is the failure this task fixes.

- [ ] **Step 2: Register the markers in `backend/pyproject.toml`**

Replace the existing `[tool.pytest.ini_options]` block with:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--strict-markers"
markers = [
    "unit: fast and in-process; every external service is faked",
    "integration: needs a real MinIO (see docs/testing.md)",
    "regression: pins a fixed bug, or the public API contract",
]
```

- [ ] **Step 3: Create the unit package and move the three test files**

```bash
cd backend
mkdir -p tests/unit
touch tests/unit/__init__.py
git mv tests/test_auth.py tests/test_files.py tests/test_health.py tests/unit/
```

- [ ] **Step 4: Rewrite `backend/tests/conftest.py`**

The whole file, after the change:

```python
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from minio.error import S3Error

from app.api.routes import files
from app.core import auth

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ISSUER = "https://keycloak.famillelallier.net/realms/ea"

MARKERS = ("unit", "integration", "regression")


def pytest_collection_modifyitems(items):
    # The directory a test lives in is its suite, so no test needs a decorator.
    for item in items:
        suite = item.path.parent.name
        if suite in MARKERS:
            item.add_marker(suite)


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

    def put_object(self, _bucket, key, data, length, part_size, content_type):
        self.objects[key] = (data.read(), content_type)

    def list_objects(self, _bucket, prefix):
        return [
            SimpleNamespace(object_name=k, size=len(v[0]), last_modified=None)
            for k, v in self.objects.items()
            if k.startswith(prefix)
        ]

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
```

- [ ] **Step 5: Strip the moved pieces out of `backend/tests/unit/test_files.py`**

Delete the `FakeMinio` class and the local `store` fixture from that file, and
fix the imports. The head of the file becomes:

```python
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)
```

`store` is no longer autouse, so every test that touches MinIO has to ask for
it. Exactly one signature changes:

```python
def test_users_only_see_their_own_files(store):
```

`test_upload_list_download_delete(store)` and `test_rejects_unsafe_names(name, store)`
already take it. `test_files_need_a_token()` stays without it on purpose — those
requests are rejected at the auth layer, before MinIO is reached, which is what
that test is asserting.

- [ ] **Step 6: Run the unit suite and confirm the marker works**

Run: `cd backend && .venv/bin/python -m pytest -m unit -q`
Expected: `14 passed`, nothing deselected.

Run: `cd backend && .venv/bin/python -m pytest -m integration -q`
Expected: `14 deselected` — the marker exists and selects nothing yet.

Note: `--strict-markers` rejects an unregistered `@pytest.mark.x` **decorator**.
It does not validate a `-m` expression: `pytest -m nosuchmarker` deselects
everything and exits 5, it does not error. Registering the markers is still what
keeps a stray decorator from silently doing nothing.

- [ ] **Step 7: Add the `test-unit` target to the `Makefile`**

In the `.PHONY` block, change the `test test-backend coverage \` line to:

```make
        test test-backend test-unit test-integration test-regression test-frontend \
        snapshot coverage coverage-backend coverage-frontend \
```

Then, directly under the existing `test-backend` target, add:

```make
test-unit: $(PY) ## Backend unit tests (everything faked, no services needed)
	cd $(BACKEND) && .venv/bin/python -m pytest -m unit $(ARGS)
```

- [ ] **Step 8: Verify the target and the linter**

Run: `make test-unit`
Expected: `14 passed`

Run: `make lint-backend`
Expected: exit 0, no findings.

- [ ] **Step 9: Commit**

```bash
git add backend/pyproject.toml backend/tests Makefile
git commit -m "test: split the backend suite into marked directories"
```

---

### Task 2: The regression directory

Moves the three checks that pin fixed bugs out of the unit files into
`tests/regression/`. They are *moved*, not copied, so each check lives in
exactly one suite.

**Files:**
- Create: `backend/tests/regression/__init__.py`
- Create: `backend/tests/regression/test_pr12_unsafe_file_names.py`
- Create: `backend/tests/regression/test_pr10_token_rejection.py`
- Modify: `backend/tests/unit/test_files.py` (remove `test_rejects_unsafe_names`)
- Modify: `backend/tests/unit/test_auth.py` (remove `test_me_rejects_bad_tokens`, `test_health_stays_public`)
- Modify: `Makefile` (new `test-regression`)

**Interfaces:**
- Consumes: `tests.conftest.token`, the `store` fixture, the directory→marker hook (Task 1).
- Produces: the `tests/regression/` package that Task 3 adds its snapshot test to.

- [ ] **Step 1: Create the regression package**

```bash
cd backend && mkdir -p tests/regression && touch tests/regression/__init__.py
```

- [ ] **Step 2: Write `backend/tests/regression/test_pr12_unsafe_file_names.py`**

```python
"""Regression — PR #12, MinIO home files.

`_key()` builds an object key by concatenating the caller's `sub` with the
upload's filename. A name of `..`, a name holding a backslash, or a name long
enough to break the key had to be rejected before anything reached MinIO.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)


@pytest.mark.parametrize("name", ["", ".", "..", "a\\b", "a/b", "x" * 256])
def test_unsafe_names_are_rejected_before_minio(name, store):
    response = client.post(
        "/api/files",
        headers={"Authorization": f"Bearer {token()}"},
        files={"file": (name, b"hello", "text/plain")},
    )

    assert response.status_code == 422
    assert store.objects == {}
```

- [ ] **Step 3: Write `backend/tests/regression/test_pr10_token_rejection.py`**

```python
"""Regression — PR #10, Keycloak authentication against realm `ea`.

Every one of these token shapes must come back 401 with a `WWW-Authenticate`
header, and `/api/health` must stay reachable without a token at all, because
the container healthcheck calls it.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)


@pytest.mark.parametrize(
    "bearer",
    [
        None,
        token(aud="ea-api"),
        token(iss="https://evil.example/realms/ea"),
        token(exp=int(time.time()) - 3600),
        "not-a-jwt",
    ],
    ids=["missing", "wrong-audience", "wrong-issuer", "expired", "garbage"],
)
def test_me_rejects_bad_tokens(bearer):
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    response = client.get("/api/me", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_health_stays_public():
    assert client.get("/api/health").status_code == 200
```

- [ ] **Step 4: Delete the originals from the unit files**

From `backend/tests/unit/test_files.py`, delete this whole test (it now lives
in `test_pr12_unsafe_file_names.py`):

```python
@pytest.mark.parametrize("name", ["..", "a\\b", "x" * 256])
def test_rejects_unsafe_names(name, store):
    assert upload(name).status_code == 422
    assert store.objects == {}
```

`pytest` is then unused in that file — remove `import pytest` too.

From `backend/tests/unit/test_auth.py`, delete `test_me_rejects_bad_tokens`
(including its `@pytest.mark.parametrize` decorator) and `test_health_stays_public`.
`import time` and `import pytest` become unused there — remove both. What remains
is the imports, `client`, `get_me()` and `test_me_returns_the_caller`.

- [ ] **Step 5: Run both suites**

Run: `cd backend && .venv/bin/python -m pytest -m unit -q`
Expected: `5 passed` (`test_me_returns_the_caller`, the three files tests, `test_health_returns_ok`)

Run: `cd backend && .venv/bin/python -m pytest -m regression -q`
Expected: `12 passed` (6 unsafe names + 5 bad tokens + health public)

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: `17 passed` — one more than before, because the unsafe-name
parametrisation gained `""`, `"."` and `"a/b"`.

- [ ] **Step 6: Add the `test-regression` target to the `Makefile`**

Under `test-unit`:

```make
test-regression: $(PY) ## Backend regression tests (pinned bugs + API contract)
	cd $(BACKEND) && .venv/bin/python -m pytest -m regression $(ARGS)
```

- [ ] **Step 7: Verify**

Run: `make test-regression && make lint-backend`
Expected: `12 passed`, then ruff exits 0.

- [ ] **Step 8: Commit**

```bash
git add backend/tests Makefile
git commit -m "test: move the pinned-bug checks into a regression suite"
```

---

### Task 3: The OpenAPI contract snapshot

A regression test that fails when the public API shape changes without the
snapshot being regenerated. The same module writes the snapshot when run with
`python -m`, so the test and `make snapshot` can never disagree on formatting.

**Files:**
- Create: `backend/tests/regression/test_openapi_contract.py`
- Create: `backend/tests/regression/openapi.snapshot.json` (generated, then committed)
- Modify: `Makefile` (new `snapshot`)

**Interfaces:**
- Consumes: the `tests/regression/` package (Task 2).
- Produces: `make snapshot`, referenced by the failure message, by `docs/testing.md` (Task 10) and by the README (Task 10).

- [ ] **Step 1: Write the test module**

`backend/tests/regression/test_openapi_contract.py`:

```python
"""Regression — the public API shape is pinned.

An unintended change to a path, a status code or a response model shows up here
as a failing diff. An intended one is `make snapshot`, and the snapshot's diff
is then reviewed in the pull request.

Run as a module (`python -m tests.regression.test_openapi_contract`) to rewrite
the snapshot; `make snapshot` does exactly that.
"""

import json
from pathlib import Path

from app.main import app

SNAPSHOT = Path(__file__).with_name("openapi.snapshot.json")


def dump(schema: dict) -> str:
    # Sorted keys: FastAPI's dict ordering is an implementation detail, and
    # churn in it must not fail the build.
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def test_openapi_matches_the_snapshot():
    assert json.loads(SNAPSHOT.read_text()) == app.openapi(), (
        "The OpenAPI schema no longer matches tests/regression/openapi.snapshot.json. "
        "If the change is intended, run `make snapshot` and review the diff in the PR."
    )


if __name__ == "__main__":
    SNAPSHOT.write_text(dump(app.openapi()))
    print(f"wrote {SNAPSHOT}")
```

- [ ] **Step 2: Run the test to watch it fail**

Run: `cd backend && .venv/bin/python -m pytest tests/regression/test_openapi_contract.py -q`
Expected: FAIL — `FileNotFoundError: ... openapi.snapshot.json`

- [ ] **Step 3: Add the `snapshot` target to the `Makefile`**

Under `test-regression`:

```make
snapshot: $(PY) ## Rewrite the pinned OpenAPI snapshot after an intended API change
	cd $(BACKEND) && .venv/bin/python -m tests.regression.test_openapi_contract
```

- [ ] **Step 4: Generate the snapshot and re-run**

Run: `make snapshot`
Expected: `wrote .../backend/tests/regression/openapi.snapshot.json`

Run: `cd backend && .venv/bin/python -m pytest tests/regression/test_openapi_contract.py -q`
Expected: `1 passed`

- [ ] **Step 5: Prove the gate actually catches a change**

Temporarily edit `backend/app/api/routes/health.py` and add a field to
`HealthResponse`:

```python
class HealthResponse(BaseModel):
    status: str
    version: str
    canary: str = "x"
```

Run: `cd backend && .venv/bin/python -m pytest -m regression -q`
Expected: FAIL, with the "run `make snapshot`" message.

Now revert that edit:

```bash
git checkout backend/app/api/routes/health.py
```

Run: `cd backend && .venv/bin/python -m pytest -m regression -q`
Expected: `13 passed`

- [ ] **Step 6: Commit**

```bash
git add backend/tests/regression Makefile
git commit -m "test: pin the OpenAPI contract with a reviewed snapshot"
```

---

### Task 4: The integration suite against real MinIO

The real app over `TestClient`, against a real MinIO in a bucket created and
destroyed by the fixture. Auth stays faked — only the storage is real.

**Files:**
- Create: `backend/tests/integration/__init__.py`
- Create: `backend/tests/integration/conftest.py`
- Create: `backend/tests/integration/test_files_minio.py`
- Create: `docker-compose.test.yml`
- Modify: `Makefile` (new `test-integration`, `minio-test-up`, `minio-test-down`)

**Interfaces:**
- Consumes: `tests.conftest.token`, `fake_jwks` (autouse), the directory→marker hook (Task 1).
- Produces: a session-scoped autouse fixture `minio_bucket` yielding the bucket name (`str`), and `make test-integration`. Task 9 runs it in CI with `CI=true`.

- [ ] **Step 1: Write `docker-compose.test.yml`**

```yaml
# MinIO on its own, for the integration suite. Nothing else in this project
# needs a local service, so this file stays deliberately small.
#
#   make minio-test-up && make test-integration && make minio-test-down
#
# The credentials are the MinIO defaults and only ever exist on a developer's
# machine and on the CI runner: the real deployment's keys come from Portainer.
services:
  minio:
    image: minio/minio
    command: server /data
    ports:
      - "9000:9000"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 2s
      timeout: 3s
      retries: 20
```

`mc ready local` is MinIO's own documented healthcheck and ships in the image.
If `docker compose ... --wait` ever hangs because the image dropped `mc`, swap
the test for `["CMD-SHELL", "curl -fsS http://localhost:9000/minio/health/ready"]`
— the same endpoint the CI job polls.

- [ ] **Step 2: Write `backend/tests/integration/conftest.py`**

```python
import os
import uuid

import pytest

from app.api.routes import files
from app.core.config import get_settings

# Matches docker-compose.test.yml and the CI step. Exporting any of these before
# the run points the suite at another MinIO instead.
DEFAULTS = {
    "DARKANGEL_S3_ENDPOINT": "localhost:9000",
    "DARKANGEL_S3_ACCESS_KEY": "minioadmin",
    "DARKANGEL_S3_SECRET_KEY": "minioadmin",
    "DARKANGEL_S3_SECURE": "false",
}


@pytest.fixture(scope="session", autouse=True)
def minio_bucket():
    """Point the app at a throwaway bucket on a real MinIO, and clean it up.

    The app never creates its own bucket (`make minio` does, in production), so
    the fixture owns one for the length of the session.
    """
    for key, value in DEFAULTS.items():
        os.environ.setdefault(key, value)
    # Unique per run, so two sessions against one MinIO cannot collide.
    os.environ["DARKANGEL_S3_BUCKET"] = f"darkangel-test-{uuid.uuid4().hex[:12]}"

    # Both are lru_cached; without this they keep the settings read at import.
    get_settings.cache_clear()
    files.minio_client.cache_clear()

    settings = get_settings()
    client = files.minio_client()

    try:
        client.list_buckets()
    except Exception as e:
        reason = f"MinIO unreachable at {settings.s3_endpoint}: {e}"
        # Skipping is a local convenience. In CI a broken service must go red,
        # never green-by-skip.
        if os.environ.get("CI") == "true":
            pytest.fail(reason, pytrace=False)
        pytest.skip(reason)

    client.make_bucket(settings.s3_bucket)
    try:
        yield settings.s3_bucket
    finally:
        # Teardown runs even when a test failed mid-upload, so nothing leaks.
        for obj in client.list_objects(settings.s3_bucket, recursive=True):
            client.remove_object(settings.s3_bucket, obj.object_name)
        client.remove_bucket(settings.s3_bucket)
        get_settings.cache_clear()
        files.minio_client.cache_clear()
```

- [ ] **Step 3: Write `backend/tests/integration/test_files_minio.py`**

```python
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)

# `put_object` is called with part_size=10 MiB and length=-1, so anything past
# that goes up as a real multipart upload.
PART_SIZE = 10 * 1024 * 1024


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def upload(name, data=b"hello", sub="user-1", content_type="text/plain"):
    return client.post(
        "/api/files", headers=auth(sub), files={"file": (name, data, content_type)}
    )


def test_upload_list_download_delete_round_trip():
    assert upload("notes.txt", b"hello").status_code == 204

    listed = client.get("/api/files", headers=auth()).json()
    assert [f["name"] for f in listed] == ["notes.txt"]
    assert listed[0]["size"] == 5
    # Real S3 returns a real timestamp; the FakeMinio double returns None.
    assert listed[0]["modified"] is not None

    got = client.get("/api/files/notes.txt", headers=auth())
    assert got.status_code == 200
    assert got.content == b"hello"
    assert got.headers["content-type"] == "text/plain"
    assert got.headers["content-length"] == "5"

    assert client.delete("/api/files/notes.txt", headers=auth()).status_code == 204
    assert client.get("/api/files", headers=auth()).json() == []


def test_users_cannot_reach_each_others_files():
    assert upload("secret.txt", sub="user-1").status_code == 204

    assert client.get("/api/files", headers=auth("user-2")).json() == []
    assert client.get("/api/files/secret.txt", headers=auth("user-2")).status_code == 404

    client.delete("/api/files/secret.txt", headers=auth("user-1"))


@pytest.mark.parametrize("name", ["bail été.txt", "two words.txt", "日本語.txt"])
def test_names_with_unicode_and_spaces_round_trip(name):
    assert upload(name, b"hello").status_code == 204

    assert name in [f["name"] for f in client.get("/api/files", headers=auth()).json()]
    assert client.get(f"/api/files/{name}", headers=auth()).content == b"hello"

    assert client.delete(f"/api/files/{name}", headers=auth()).status_code == 204


def test_a_multipart_sized_upload_survives_the_round_trip():
    payload = b"x" * (PART_SIZE + 1024)

    assert upload("big.bin", payload, content_type="application/octet-stream").status_code == 204

    got = client.get("/api/files/big.bin", headers=auth())
    assert len(got.content) == len(payload)
    assert got.content == payload

    client.delete("/api/files/big.bin", headers=auth())


def test_reuploading_a_name_replaces_the_object():
    assert upload("dup.txt", b"first").status_code == 204
    assert upload("dup.txt", b"second-and-longer").status_code == 204

    listed = [f for f in client.get("/api/files", headers=auth()).json() if f["name"] == "dup.txt"]
    assert len(listed) == 1
    assert client.get("/api/files/dup.txt", headers=auth()).content == b"second-and-longer"

    client.delete("/api/files/dup.txt", headers=auth())


def test_downloading_a_missing_file_is_404():
    assert client.get("/api/files/nope.txt", headers=auth()).status_code == 404


def test_deleting_a_missing_file_succeeds():
    # S3 DELETE is idempotent: MinIO reports success for a key that is not there.
    assert client.delete("/api/files/nope.txt", headers=auth()).status_code == 204


def test_unauthenticated_requests_never_reach_minio():
    assert client.get("/api/files").status_code == 401
    assert client.post("/api/files", files={"file": ("a.txt", b"x")}).status_code == 401
    assert client.get("/api/files/a.txt").status_code == 401
    assert client.delete("/api/files/a.txt").status_code == 401
```

- [ ] **Step 4: Create the package file and run with MinIO down**

```bash
cd backend && touch tests/integration/__init__.py
```

Make sure nothing is listening on 9000, then run:

`cd backend && .venv/bin/python -m pytest -m integration -q`
Expected: `10 skipped`, with the reason `MinIO unreachable at localhost:9000: ...`

- [ ] **Step 5: Prove the CI switch goes red instead of skipping**

Run: `cd backend && CI=true .venv/bin/python -m pytest -m integration -q`
Expected: `10 errors`, each `Failed: MinIO unreachable at localhost:9000: ...`

- [ ] **Step 6: Add the Makefile targets**

Under `test-regression`:

```make
test-integration: $(PY) ## Backend integration tests (needs MinIO; `make minio-test-up`)
	cd $(BACKEND) && .venv/bin/python -m pytest -m integration $(ARGS)

minio-test-up: ## Start the MinIO the integration suite runs against
	docker compose -f docker-compose.test.yml up -d --wait

minio-test-down: ## Stop that MinIO and drop its data
	docker compose -f docker-compose.test.yml down -v
```

Add `minio-test-up minio-test-down` to the `.PHONY` list, on the line that
already holds `snapshot`.

- [ ] **Step 7: Run the suite for real**

```bash
make minio-test-up
make test-integration
```

Expected: `10 passed`. The multipart test is the slow one (~11 MiB through the
loopback); the whole run should still be a few seconds.

- [ ] **Step 8: Confirm the bucket was cleaned up**

Run: `docker compose -f docker-compose.test.yml exec minio ls /data`
Expected: no `darkangel-test-*` directory — the fixture removed it.

Then: `make minio-test-down`

- [ ] **Step 9: Verify the unit suite is unaffected**

Run: `make test-unit && make test-regression && make lint-backend`
Expected: `5 passed`, `13 passed`, ruff exits 0. The unit suite must still pass
with MinIO stopped — that is the point of the split.

- [ ] **Step 10: Commit**

```bash
git add backend/tests/integration docker-compose.test.yml Makefile
git commit -m "test: add an integration suite running against a real MinIO"
```

---

### Task 5: Vitest, and the first frontend test

Brings up the whole frontend test toolchain and proves it on `api/client.ts`,
the one module every other one goes through.

**Files:**
- Modify: `frontend/package.json` (devDependencies, `test` + `test:coverage` scripts)
- Modify: `frontend/vite.config.ts` (`test` block)
- Modify: `frontend/tsconfig.app.json` (include the tests)
- Create: `frontend/tests/unit/client.test.ts`
- Modify: `Makefile` (new `test-frontend`, `coverage-frontend`)

**Interfaces:**
- Consumes: nothing.
- Produces: `npm run test` / `npm run test:coverage` in `frontend/`, `make test-frontend`, and the `frontend/tests/{unit,regression}/` layout Tasks 6–8 write into. Test files are matched by `tests/**/*.test.ts`.

- [ ] **Step 1: Install the test dependencies**

```bash
cd frontend
npm install -D vitest @vue/test-utils jsdom @vitest/coverage-v8
```

`@vitest/coverage-v8` must end up on the **same version** as `vitest` — npm
resolves both to the current major, so check `package.json` afterwards and fix
the range by hand if they differ.

- [ ] **Step 2: Add the scripts to `frontend/package.json`**

The `scripts` block becomes:

```json
  "scripts": {
    "dev": "vite",
    "build": "vue-tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:coverage": "vitest run --coverage"
  },
```

- [ ] **Step 3: Add the `test` block to `frontend/vite.config.ts`**

Change the `defineConfig` import to come from `vitest/config` (it re-exports
Vite's own and adds the `test` types), and add the block. The head of the file
and the new block:

```ts
import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  test: {
    // Components and the api client both touch the DOM and fetch.
    environment: 'jsdom',
    include: ['tests/**/*.test.ts'],
    coverage: {
      // No threshold yet: the first CI run is the baseline, and docs/testing.md
      // says to raise it to that number and never below it.
      reporter: ['text', 'lcov'],
      include: ['src/**'],
    },
  },
  resolve: {
```

Everything from `resolve:` down is unchanged.

- [ ] **Step 4: Make `vue-tsc` type-check the tests**

In `frontend/tsconfig.app.json`, change the last line:

```json
  "include": ["src/**/*.ts", "src/**/*.tsx", "src/**/*.vue", "tests/**/*.ts"]
}
```

- [ ] **Step 5: Write the failing test — `frontend/tests/unit/client.test.ts`**

```ts
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiGet, apiRequest } from '@/api/client'
import { accessToken } from '@/auth'

// The client is the only module that calls fetch; auth is the only thing it
// needs from the rest of the app, so it is the only thing mocked.
vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))

const fetchMock = vi.fn()
vi.stubGlobal('fetch', fetchMock)

function response(status: number, body: unknown = {}) {
  return { ok: status < 400, status, json: async () => body } as unknown as Response
}

beforeEach(() => {
  fetchMock.mockReset()
  fetchMock.mockResolvedValue(response(200))
  vi.mocked(accessToken).mockResolvedValue(null)
})

describe('apiRequest', () => {
  it('prefixes the path with /api and attaches the bearer token', async () => {
    vi.mocked(accessToken).mockResolvedValue('a-token')

    await apiRequest('GET', '/files')

    expect(fetchMock).toHaveBeenCalledWith('/api/files', {
      method: 'GET',
      headers: { Authorization: 'Bearer a-token' },
      body: undefined,
    })
  })

  it('sends no Authorization header when there is no session', async () => {
    await apiRequest('GET', '/files')

    expect(fetchMock.mock.calls[0][1].headers).toEqual({})
  })

  it('passes the body straight through', async () => {
    const form = new FormData()

    await apiRequest('POST', '/files', form)

    expect(fetchMock.mock.calls[0][1].body).toBe(form)
  })

  it('throws with the method, path and status when the response is not ok', async () => {
    fetchMock.mockResolvedValue(response(500))

    await expect(apiRequest('DELETE', '/files/a.txt')).rejects.toThrow(
      'DELETE /files/a.txt failed with 500',
    )
  })
})

describe('apiGet', () => {
  it('returns the parsed JSON body', async () => {
    fetchMock.mockResolvedValue(response(200, [{ name: 'a.txt' }]))

    await expect(apiGet('/files')).resolves.toEqual([{ name: 'a.txt' }])
  })

  it('honours VITE_API_BASE_URL when it is set', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.example.test')
    // BASE_URL is read once, at module load, so the module has to be re-imported.
    vi.resetModules()
    const { apiGet: freshGet } = await import('@/api/client')

    await freshGet('/health')

    expect(fetchMock.mock.calls[0][0]).toBe('https://api.example.test/health')
    vi.unstubAllEnvs()
  })
})
```

- [ ] **Step 6: Run the suite**

Run: `cd frontend && npm run test`
Expected: 6 passed. If it fails with "Cannot find module '@/api/client'",
the `resolve.alias` block was lost while editing `vite.config.ts`; restore it.
If it fails with "environment jsdom not found", `jsdom` did not install.

- [ ] **Step 7: Confirm the type-check covers the tests**

Break a type on purpose — in `client.test.ts`, change
`await apiRequest('GET', '/files')` to `await apiRequest(1, '/files')`.

Run: `cd frontend && npm run build`
Expected: FAIL, `Argument of type 'number' is not assignable to parameter of type 'string'`.

Undo that edit, then run `npm run build` again.
Expected: the build succeeds.

- [ ] **Step 8: Add the Makefile targets**

Under `test-integration`:

```make
test-frontend: ## Frontend unit, component and regression tests (vitest)
	cd $(FRONTEND) && $(NPM) run test

coverage-frontend: ## Frontend coverage report (text + lcov)
	cd $(FRONTEND) && $(NPM) run test:coverage
```

- [ ] **Step 9: Verify**

Run: `make test-frontend`
Expected: 6 passed.

- [ ] **Step 10: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts \
        frontend/tsconfig.app.json frontend/tests Makefile
git commit -m "test: set up vitest and cover the api client"
```

---

### Task 6: Store tests

The three Pinia stores share one shape — `loading` / `error` / data — so these
tests pin the transitions, with the api modules mocked.

**Files:**
- Create: `frontend/tests/unit/stores-files.test.ts`
- Create: `frontend/tests/unit/stores-health.test.ts`
- Create: `frontend/tests/unit/stores-me.test.ts`

**Interfaces:**
- Consumes: the Vitest setup from Task 5.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write `frontend/tests/unit/stores-files.test.ts`**

```ts
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

import { deleteFile, listFiles, uploadFile } from '@/api/files'
import { useFilesStore } from '@/stores/files'

vi.mock('@/api/files', () => ({
  listFiles: vi.fn(async () => []),
  uploadFile: vi.fn(async () => {}),
  deleteFile: vi.fn(async () => {}),
}))

const aFile = { name: 'a.txt', size: 5, modified: null }

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(listFiles).mockResolvedValue([aFile])
})

it('load() fills the list and clears loading', async () => {
  const store = useFilesStore()

  const pending = store.load()
  expect(store.loading).toBe(true)
  await pending

  expect(store.files).toEqual([aFile])
  expect(store.error).toBeNull()
  expect(store.loading).toBe(false)
})

it('upload() sends every picked file, then reloads', async () => {
  const one = new File(['a'], 'one.txt')
  const two = new File(['b'], 'two.txt')

  await useFilesStore().upload([one, two])

  expect(uploadFile).toHaveBeenCalledTimes(2)
  expect(uploadFile).toHaveBeenCalledWith(one)
  expect(uploadFile).toHaveBeenCalledWith(two)
  expect(listFiles).toHaveBeenCalledTimes(1)
})

it('remove() deletes, then reloads', async () => {
  await useFilesStore().remove('a.txt')

  expect(deleteFile).toHaveBeenCalledWith('a.txt')
  expect(listFiles).toHaveBeenCalledTimes(1)
})

it('captures the message of a failed action and stops loading', async () => {
  vi.mocked(deleteFile).mockRejectedValue(new Error('DELETE /files/a.txt failed with 404'))
  const store = useFilesStore()

  await store.remove('a.txt')

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

- [ ] **Step 2: Write `frontend/tests/unit/stores-health.test.ts`**

```ts
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

import { fetchHealth } from '@/api/health'
import { useHealthStore } from '@/stores/health'

vi.mock('@/api/health', () => ({ fetchHealth: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

it('load() stores the health payload', async () => {
  vi.mocked(fetchHealth).mockResolvedValue({ status: 'ok', version: '0.1.0' })
  const store = useHealthStore()

  await store.load()

  expect(store.health).toEqual({ status: 'ok', version: '0.1.0' })
  expect(store.error).toBeNull()
  expect(store.loading).toBe(false)
})

it('load() records the error and leaves health untouched', async () => {
  vi.mocked(fetchHealth).mockRejectedValue(new Error('GET /health failed with 502'))
  const store = useHealthStore()

  await store.load()

  expect(store.health).toBeNull()
  expect(store.error).toBe('GET /health failed with 502')
  expect(store.loading).toBe(false)
})
```

- [ ] **Step 3: Write `frontend/tests/unit/stores-me.test.ts`**

```ts
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

import { fetchMe } from '@/api/me'
import { useMeStore } from '@/stores/me'

vi.mock('@/api/me', () => ({ fetchMe: vi.fn() }))

const me = { sub: 'user-1', username: 'nicolas', email: null, roles: ['ea-editor'] }

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

it('load() stores the caller', async () => {
  vi.mocked(fetchMe).mockResolvedValue(me)
  const store = useMeStore()

  await store.load()

  expect(store.me).toEqual(me)
  expect(store.error).toBeNull()
})

it('load() records a 401 as an error message', async () => {
  vi.mocked(fetchMe).mockRejectedValue(new Error('GET /me failed with 401'))
  const store = useMeStore()

  await store.load()

  expect(store.me).toBeNull()
  expect(store.error).toBe('GET /me failed with 401')
  expect(store.loading).toBe(false)
})
```

- [ ] **Step 4: Run them**

Run: `cd frontend && npm run test`
Expected: 15 passed (6 from Task 5 + 5 + 2 + 2).

- [ ] **Step 5: Type-check**

Run: `cd frontend && npm run build`
Expected: succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/tests
git commit -m "test: cover the files, health and me stores"
```

---

### Task 7: Auth and the router guard

`auth.ts` is the whole Keycloak contract in one file, and the router guard is
the only thing keeping an unauthenticated visitor off a page. Both are covered
with `oidc-client-ts` mocked — no network, no Keycloak.

**Files:**
- Create: `frontend/tests/unit/auth.test.ts`
- Create: `frontend/tests/unit/router.test.ts`

**Interfaces:**
- Consumes: the Vitest setup from Task 5.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write `frontend/tests/unit/auth.test.ts`**

```ts
import { UserManager } from 'oidc-client-ts'
import { beforeEach, expect, it, vi } from 'vitest'

import { accessToken, completeSignIn, signIn, signOut } from '@/auth'

// vi.hoisted, because vi.mock's factory is lifted above the imports.
const manager = vi.hoisted(() => ({
  getUser: vi.fn(),
  signinRedirect: vi.fn(async () => {}),
  signinRedirectCallback: vi.fn(),
  signoutRedirect: vi.fn(async () => {}),
}))

vi.mock('oidc-client-ts', () => ({
  // Returning an object from a constructor makes `new UserManager()` yield it.
  UserManager: vi.fn(() => manager),
  WebStorageStateStore: vi.fn(),
  InMemoryWebStorage: vi.fn(),
}))

beforeEach(() => {
  manager.getUser.mockReset()
  manager.signinRedirect.mockClear()
  manager.signinRedirectCallback.mockReset()
  manager.signoutRedirect.mockClear()
})

it('configures the darkangel-spa public client against realm ea', () => {
  expect(vi.mocked(UserManager).mock.calls[0][0]).toMatchObject({
    authority: 'https://keycloak.famillelallier.net/realms/ea',
    client_id: 'darkangel-spa',
    redirect_uri: 'http://localhost:3000/auth/callback',
    post_logout_redirect_uri: 'http://localhost:3000/',
    scope: 'openid profile email',
    automaticSilentRenew: true,
  })
})

it('returns the access token of a live session', async () => {
  manager.getUser.mockResolvedValue({ access_token: 'a-token', expired: false })

  await expect(accessToken()).resolves.toBe('a-token')
})

it('returns null once the token has expired', async () => {
  manager.getUser.mockResolvedValue({ access_token: 'a-token', expired: true })

  await expect(accessToken()).resolves.toBeNull()
})

it('returns null when there is no session at all', async () => {
  manager.getUser.mockResolvedValue(null)

  await expect(accessToken()).resolves.toBeNull()
})

it('carries the page to come back to through the redirect state', async () => {
  await signIn('/files')

  expect(manager.signinRedirect).toHaveBeenCalledWith({ state: '/files' })
})

it('resumes at the in-app path the state holds', async () => {
  manager.signinRedirectCallback.mockResolvedValue({ state: '/files' })

  await expect(completeSignIn()).resolves.toBe('/files')
})

it('falls back to / when the state is not an in-app path', async () => {
  // An open-redirect guard: anything that is not a local path goes home.
  manager.signinRedirectCallback.mockResolvedValue({ state: 'https://evil.example' })

  await expect(completeSignIn()).resolves.toBe('/')
})

it('falls back to / when there is no state', async () => {
  manager.signinRedirectCallback.mockResolvedValue({})

  await expect(completeSignIn()).resolves.toBe('/')
})

it('signs out through the manager', async () => {
  await signOut()

  expect(manager.signoutRedirect).toHaveBeenCalled()
})
```

- [ ] **Step 2: Run it**

Run: `cd frontend && npm run test tests/unit/auth.test.ts`
Expected: 9 passed.

If the first test fails on `redirect_uri`, print
`vi.mocked(UserManager).mock.calls[0][0]` — jsdom's default origin is
`http://localhost:3000`, and `import.meta.env.BASE_URL` is `/`, which is where
those two expected values come from.

- [ ] **Step 3: Write `frontend/tests/unit/router.test.ts`**

```ts
import { beforeEach, expect, it, vi } from 'vitest'

import { accessToken, signIn } from '@/auth'
import { router } from '@/router'

// Every export the router and its lazily-loaded views reach for.
vi.mock('@/auth', () => ({
  accessToken: vi.fn(async () => null),
  signIn: vi.fn(async () => {}),
  completeSignIn: vi.fn(async () => '/'),
  signOut: vi.fn(async () => {}),
}))

beforeEach(async () => {
  vi.clearAllMocks()
  vi.mocked(accessToken).mockResolvedValue('a-token')
  await router.push('/')
  vi.clearAllMocks()
})

it('sends an unauthenticated visitor to Keycloak instead of the page', async () => {
  vi.mocked(accessToken).mockResolvedValue(null)

  // The guard returns false, so the navigation resolves as a failure.
  await router.push('/files').catch(() => {})

  expect(signIn).toHaveBeenCalledWith('/files')
  expect(router.currentRoute.value.path).toBe('/')
})

it('keeps the query and hash in the path it will come back to', async () => {
  vi.mocked(accessToken).mockResolvedValue(null)

  await router.push('/files?sort=name').catch(() => {})

  expect(signIn).toHaveBeenCalledWith('/files?sort=name')
})

it('lets a signed-in visitor through', async () => {
  await router.push('/about')

  expect(signIn).not.toHaveBeenCalled()
  expect(router.currentRoute.value.path).toBe('/about')
})

it('lets the callback route through without a token', async () => {
  vi.mocked(accessToken).mockResolvedValue(null)

  await router.push('/auth/callback?code=abc')

  expect(signIn).not.toHaveBeenCalled()
  expect(router.currentRoute.value.path).toBe('/auth/callback')
})
```

- [ ] **Step 4: Run it**

Run: `cd frontend && npm run test tests/unit/router.test.ts`
Expected: 4 passed.

- [ ] **Step 5: Run the whole suite and type-check**

Run: `cd frontend && npm run test && npm run build`
Expected: 28 passed (15 + 9 + 4), then a clean build.

- [ ] **Step 6: Commit**

```bash
git add frontend/tests
git commit -m "test: cover the Keycloak client and the router guard"
```

---

### Task 8: FilesView, and the blob-URL regression

The one real component test, plus the frontend's first regression test: the
download blob URL must be revoked *after* the browser has taken the click, not
in the same tick.

**Files:**
- Create: `frontend/tests/unit/FilesView.test.ts`
- Create: `frontend/tests/regression/pr12-download-blob-url.test.ts`

**Interfaces:**
- Consumes: the Vitest setup from Task 5.
- Produces: the `frontend/tests/regression/` directory that `docs/testing.md` (Task 10) points new frontend regression tests at.

- [ ] **Step 1: Write `frontend/tests/unit/FilesView.test.ts`**

```ts
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

import { deleteFile, listFiles, uploadFile } from '@/api/files'
import FilesView from '@/views/FilesView.vue'

vi.mock('@/api/files', () => ({
  listFiles: vi.fn(async () => []),
  uploadFile: vi.fn(async () => {}),
  deleteFile: vi.fn(async () => {}),
  downloadFile: vi.fn(async () => new Blob(['hello'])),
}))

function render() {
  return mount(FilesView, { global: { plugins: [createPinia()] } })
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listFiles).mockResolvedValue([])
  vi.stubGlobal('confirm', vi.fn(() => true))
})

it('shows an empty state before anything is uploaded', async () => {
  const wrapper = render()
  await flushPromises()

  expect(wrapper.text()).toContain('No files yet.')
  expect(wrapper.find('table').exists()).toBe(false)
})

it('lists what the API returns, with a human-readable size', async () => {
  vi.mocked(listFiles).mockResolvedValue([
    { name: 'bail été.txt', size: 2048, modified: null },
  ])

  const wrapper = render()
  await flushPromises()

  const cells = wrapper.findAll('tbody td').map((c) => c.text())
  expect(cells[0]).toBe('bail été.txt')
  expect(cells[1]).toBe('2.0 KB')
})

it('uploads every picked file and clears the input', async () => {
  const wrapper = render()
  await flushPromises()

  const file = new File(['hello'], 'notes.txt', { type: 'text/plain' })
  const input = wrapper.get('input[type="file"]')
  // jsdom's FileList is read-only, so it is replaced outright.
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  await flushPromises()

  expect(uploadFile).toHaveBeenCalledWith(file)
  expect((input.element as HTMLInputElement).value).toBe('')
})

it('asks before deleting, and deletes when confirmed', async () => {
  vi.mocked(listFiles).mockResolvedValue([{ name: 'a.txt', size: 5, modified: null }])
  const wrapper = render()
  await flushPromises()

  await wrapper.findAll('tbody button')[1].trigger('click')
  await flushPromises()

  expect(confirm).toHaveBeenCalledWith('Delete a.txt?')
  expect(deleteFile).toHaveBeenCalledWith('a.txt')
})

it('does not delete when the confirmation is dismissed', async () => {
  vi.mocked(listFiles).mockResolvedValue([{ name: 'a.txt', size: 5, modified: null }])
  vi.stubGlobal('confirm', vi.fn(() => false))
  const wrapper = render()
  await flushPromises()

  await wrapper.findAll('tbody button')[1].trigger('click')
  await flushPromises()

  expect(deleteFile).not.toHaveBeenCalled()
})

it('shows the store error in an alert', async () => {
  vi.mocked(listFiles).mockRejectedValue(new Error('GET /files failed with 502'))
  const wrapper = render()
  await flushPromises()

  expect(wrapper.get('[role="alert"]').text()).toBe('GET /files failed with 502')
})
```

- [ ] **Step 2: Run it**

Run: `cd frontend && npm run test tests/unit/FilesView.test.ts`
Expected: 6 passed.

- [ ] **Step 3: Write `frontend/tests/regression/pr12-download-blob-url.test.ts`**

```ts
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { downloadFile } from '@/api/files'
import FilesView from '@/views/FilesView.vue'

/**
 * Regression — PR #12, MinIO home files.
 *
 * The API needs a bearer token, so a download is a fetch into a Blob handed to
 * a throwaway <a>. Revoking the object URL in the same tick as `link.click()`
 * killed the download before the browser had taken it; the fix defers the
 * revoke to the next macrotask. This pins that the revoke is still deferred.
 */

vi.mock('@/api/files', () => ({
  listFiles: vi.fn(async () => [{ name: 'a.txt', size: 5, modified: null }]),
  uploadFile: vi.fn(async () => {}),
  deleteFile: vi.fn(async () => {}),
  downloadFile: vi.fn(async () => new Blob(['hello'])),
}))

// jsdom implements neither, and a real <a download> click would try to navigate.
const click = vi.spyOn(HTMLAnchorElement.prototype, 'click')

beforeEach(() => {
  vi.clearAllMocks()
  click.mockImplementation(() => {})
  URL.createObjectURL = vi.fn(() => 'blob:darkangel/1')
  URL.revokeObjectURL = vi.fn()
})

afterEach(() => {
  vi.useRealTimers()
})

// flushPromises is a macrotask, so it would run the very timer under test.
// Draining microtasks by hand is the only way to look at the tick in between.
const microtasks = async () => {
  for (let i = 0; i < 10; i++) await Promise.resolve()
}

it('revokes the blob URL only after the click, never in the same tick', async () => {
  const wrapper = mount(FilesView, { global: { plugins: [createPinia()] } })
  await flushPromises()

  vi.useFakeTimers()
  await wrapper.findAll('tbody button')[0].trigger('click')
  await microtasks()

  expect(downloadFile).toHaveBeenCalledWith('a.txt')
  expect(URL.createObjectURL).toHaveBeenCalled()
  expect(click).toHaveBeenCalled()
  // The bug: this was already called by now, and the download never started.
  expect(URL.revokeObjectURL).not.toHaveBeenCalled()

  vi.runAllTimers()

  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:darkangel/1')
})

it('reports a failed download in the store error instead of throwing', async () => {
  vi.mocked(downloadFile).mockRejectedValue(new Error('GET /files/a.txt failed with 404'))
  const wrapper = mount(FilesView, { global: { plugins: [createPinia()] } })
  await flushPromises()

  await wrapper.findAll('tbody button')[0].trigger('click')
  await flushPromises()

  expect(wrapper.get('[role="alert"]').text()).toBe('GET /files/a.txt failed with 404')
})
```

- [ ] **Step 4: Prove the regression test would catch the bug**

Temporarily reintroduce it in `frontend/src/views/FilesView.vue` — replace

```ts
    setTimeout(() => URL.revokeObjectURL(url))
```

with

```ts
    URL.revokeObjectURL(url)
```

Run: `cd frontend && npm run test tests/regression/pr12-download-blob-url.test.ts`
Expected: FAIL on `expect(URL.revokeObjectURL).not.toHaveBeenCalled()`.

Restore the file:

```bash
git checkout frontend/src/views/FilesView.vue
```

Run the same command again.
Expected: 2 passed.

- [ ] **Step 5: Run everything and type-check**

Run: `cd frontend && npm run test && npm run build`
Expected: 36 passed (28 + 6 + 2), then a clean build.

- [ ] **Step 6: Commit**

```bash
git add frontend/tests
git commit -m "test: cover FilesView and pin the download blob-URL timing"
```

---

### Task 9: The aggregate targets and the three CI jobs

Wires every suite into `make test` / `make coverage` — which `verify` and `ci`
already depend on — and splits the workflow into three parallel jobs.

**Files:**
- Modify: `Makefile` (`test`, `coverage`, `coverage-backend`)
- Modify: `.github/workflows/ci.yml` (rewritten)

**Interfaces:**
- Consumes: `test-unit`, `test-integration`, `test-regression`, `test-frontend`, `coverage-frontend` (Tasks 1–8).
- Produces: the job names `backend-unit`, `backend-integration` and `frontend`, which acceptance criterion 4 checks for.

- [ ] **Step 1: Rewrite the `test` and `coverage` targets in the `Makefile`**

Replace the existing `test:` line and the whole `coverage:` target with:

```make
# One pytest process per suite on purpose: the integration fixture rewrites
# DARKANGEL_S3_* in the environment, which must not reach the other suites.
test: test-unit test-integration test-regression test-frontend ## Run every suite

coverage: coverage-backend coverage-frontend ## Coverage for both sides

coverage-backend: $(PY) ## Backend coverage over every suite, failing under COVERAGE_MIN%
	cd $(BACKEND) && .venv/bin/python -m pytest \
		--cov=app --cov-report=term-missing --cov-report=xml \
		--cov-fail-under=$(COVERAGE_MIN)
```

`test-backend` stays exactly as it is: it is the escape hatch for
`make test-backend ARGS="-k health"`, and the README documents it.

- [ ] **Step 2: Run the aggregate with MinIO down**

Run: `make test`
Expected: unit `5 passed`, integration `10 skipped`, regression `13 passed`,
frontend `36 passed`. This is acceptance criterion 1's second half.

- [ ] **Step 3: Run it with MinIO up**

```bash
make minio-test-up
make test
make minio-test-down
```

Expected: `10 passed` for integration this time; everything else unchanged.

- [ ] **Step 4: Check the coverage gate**

```bash
make minio-test-up
make coverage
make minio-test-down
```

Expected: the backend report prints a TOTAL at or above 80% (it is 96% today),
`backend/coverage.xml` exists, and the frontend prints its own table. This is
acceptance criterion 2.

- [ ] **Step 5: Rewrite `.github/workflows/ci.yml`**

The whole file:

```yaml
name: CI

on:
  push:
    branches: ["**"]
  pull_request:

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

env:
  # Kept in step with COVERAGE_MIN in the Makefile.
  COVERAGE_MIN: "80"

jobs:
  backend-unit:
    name: Backend unit + regression
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v5

      - name: Create virtualenv and install
        run: |
          uv venv --python 3.11
          uv pip install -e ".[dev]"

      - name: Lint
        run: .venv/bin/python -m ruff check .

      - name: Test
        run: |
          .venv/bin/python -m pytest -m "unit or regression" \
            --junitxml=reports/junit.xml \
            --cov=app --cov-report=term-missing --cov-report=xml \
            --cov-fail-under="$COVERAGE_MIN"

      - name: Summary
        if: always()
        run: |
          echo "### Backend unit + regression" >> "$GITHUB_STEP_SUMMARY"
          .venv/bin/python -m coverage report --format=markdown \
            >> "$GITHUB_STEP_SUMMARY" || true

      - name: Upload reports
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: backend-unit-reports
          path: |
            backend/reports/junit.xml
            backend/coverage.xml
          if-no-files-found: warn

  backend-integration:
    name: Backend integration (real MinIO)
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    env:
      # `CI` makes an unreachable MinIO fail the job instead of skipping it.
      CI: "true"
      DARKANGEL_S3_ENDPOINT: localhost:9000
      DARKANGEL_S3_ACCESS_KEY: minioadmin
      DARKANGEL_S3_SECRET_KEY: minioadmin
      DARKANGEL_S3_SECURE: "false"
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v5

      - name: Create virtualenv and install
        run: |
          uv venv --python 3.11
          uv pip install -e ".[dev]"

      - name: Start MinIO
        # A plain container, not a service container: those cannot pass the
        # `server /data` command the MinIO image needs.
        run: |
          docker run -d --name minio -p 9000:9000 \
            -e MINIO_ROOT_USER=minioadmin \
            -e MINIO_ROOT_PASSWORD=minioadmin \
            minio/minio server /data

      - name: Wait for MinIO
        run: |
          for _ in $(seq 1 30); do
            if curl -fsS http://localhost:9000/minio/health/ready >/dev/null; then
              echo "MinIO is ready"
              exit 0
            fi
            sleep 2
          done
          echo "MinIO never became ready" >&2
          docker logs minio >&2
          exit 1

      - name: Test
        run: .venv/bin/python -m pytest -m integration --junitxml=reports/junit.xml

      - name: Summary
        if: always()
        run: |
          echo "### Backend integration" >> "$GITHUB_STEP_SUMMARY"
          echo "Ran against MinIO at $DARKANGEL_S3_ENDPOINT." >> "$GITHUB_STEP_SUMMARY"

      - name: MinIO logs on failure
        if: failure()
        run: docker logs minio

      - name: Upload reports
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: backend-integration-reports
          path: backend/reports/junit.xml
          if-no-files-found: warn

  frontend:
    name: Frontend (vitest + vue-tsc + vite build)
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: frontend/package-lock.json

      - run: npm ci

      - name: Test
        run: |
          npm run test:coverage -- \
            --reporter=default --reporter=junit \
            --outputFile.junit=reports/junit.xml

      # `npm run build` is `vue-tsc -b && vite build`, so this type-checks too.
      - name: Build
        run: npm run build

      - name: Summary
        if: always()
        run: |
          echo "### Frontend" >> "$GITHUB_STEP_SUMMARY"
          echo "vitest + vue-tsc + vite build." >> "$GITHUB_STEP_SUMMARY"

      - name: Upload reports
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: frontend-reports
          path: |
            frontend/reports/junit.xml
            frontend/coverage/lcov.info
          if-no-files-found: warn
```

- [ ] **Step 6: Keep the generated reports out of git**

`.gitignore` already has `.coverage` and `coverage.xml` under its `# Python`
section. Add the JUnit and lcov output beneath the `# Node` section's `dist/`
line:

```gitignore
# Node
node_modules/
dist/
coverage/
reports/
```

`coverage/` and `reports/` are unanchored, so they match
`frontend/coverage/`, `frontend/reports/` and `backend/reports/` alike.

- [ ] **Step 7: Push and read the three jobs**

```bash
git add Makefile .github/workflows/ci.yml .gitignore
git commit -m "ci: run unit, integration and frontend suites as parallel jobs"
git push
```

Then: `gh run watch`

Expected: three jobs — `backend-unit`, `backend-integration`, `frontend` — all
green, each with an artifact and a step summary. This is acceptance criterion 4.

- [ ] **Step 8: Prove a broken MinIO fails instead of skipping**

On a throwaway branch, break the service on purpose — in `ci.yml`, change the
image line of the "Start MinIO" step to a port nothing listens on:

```yaml
            minio/minio server /data --address :9999
```

Push it and watch: `backend-integration` must go **red** with
`Failed: MinIO unreachable at localhost:9000`, never green-with-skips. This is
acceptance criterion 5.

Revert that change and delete the throwaway branch.

---

### Task 10: The testing guide

**Files:**
- Create: `docs/testing.md`
- Modify: `README.md` (the `## Checks` section)
- Modify: `CLAUDE.md` (the `## Commands` section)

**Interfaces:**
- Consumes: every target and directory from Tasks 1–9.
- Produces: nothing.

- [ ] **Step 1: Write `docs/testing.md`**

````markdown
# Testing

Three backend suites and two frontend ones. Which suite a test belongs to is
decided by the directory it sits in — there are no markers to remember and
none to forget.

## The layers

| Suite | Where | What is real | Runs in |
|---|---|---|---|
| Backend unit | `backend/tests/unit/` | Nothing outside the process. MinIO is `FakeMinio`, Keycloak is a fake JWKS. | `backend-unit` |
| Backend regression | `backend/tests/regression/` | Same as unit. Each file pins one fixed bug, or the API contract. | `backend-unit` |
| Backend integration | `backend/tests/integration/` | A real MinIO, in a throwaway bucket. Auth stays faked. | `backend-integration` |
| Frontend unit | `frontend/tests/unit/` | jsdom. `fetch` and `oidc-client-ts` are mocked. | `frontend` |
| Frontend regression | `frontend/tests/regression/` | Same as frontend unit. | `frontend` |

Keycloak is never real, not even in integration: the suite signs its own RS256
tokens against a key it generates, and `tests/conftest.py` swaps the JWKS client
out for one that returns that key. Only storage is real.

## Running them

```sh
make test              # every suite; integration skips if MinIO is down
make test-unit         # backend unit only, no services needed
make test-regression   # backend regression only
make test-integration  # backend integration; needs MinIO
make test-frontend     # vitest
make coverage          # both sides; the backend fails under 80%
```

Integration needs a MinIO on `localhost:9000`:

```sh
make minio-test-up     # docker-compose.test.yml, MinIO alone
make test-integration
make minio-test-down   # also drops its data
```

Narrow a run with `ARGS`:

```sh
make test-unit ARGS="-k token -v"
make test-backend ARGS="tests/regression/test_pr10_token_rejection.py"
cd frontend && npm run test tests/unit/router.test.ts
```

## How the markers work

`backend/tests/conftest.py` has a `pytest_collection_modifyitems` hook that
marks each test with the name of its parent directory. `pyproject.toml`
registers `unit`, `integration` and `regression` and turns on
`--strict-markers`, so an unregistered `@pytest.mark.x` decorator is an error
instead of a silent no-op. (`--strict-markers` does not check `-m` expressions:
a typo there just deselects everything and exits 5.)

Putting a test file in the right directory is the entire contract. A test in
`tests/unit/` must never touch the network or a service.

## The integration bucket

The app does not create its own bucket — in production `make minio` does. So
`tests/integration/conftest.py` creates one per session, named
`darkangel-test-<random>`, and removes every object and the bucket itself in
teardown, even after a failure.

It points the app at that bucket by setting `DARKANGEL_S3_*` in the environment
and then clearing the `lru_cache` on both `get_settings()` and
`minio_client()` — without that, the app keeps whatever it read at import.

Exporting any `DARKANGEL_S3_*` variable before the run targets a different
MinIO instead of the compose one.

**Unreachable MinIO skips locally and fails in CI.** The switch is the `CI`
environment variable, which GitHub Actions sets to `true`. A broken service
must never turn a CI run green by skipping.

## Writing a regression test

One file per fixed bug, named `test_<issue-or-pr>_<slug>.py` on the backend and
`<issue-or-pr>-<slug>.test.ts` on the frontend. The docstring or header comment
says which issue or PR, and what the original failure looked like — the point is
that someone reading the test in two years knows why it exists.

Move the check out of wherever it was; never leave a copy behind. Every check
lives in exactly one suite.

Before trusting a new regression test, reintroduce the bug and watch it fail.

## The OpenAPI snapshot

`tests/regression/test_openapi_contract.py` compares `app.openapi()` to
`tests/regression/openapi.snapshot.json`. Any change to a path, a status code or
a response model fails it.

When the change is intended:

```sh
make snapshot
```

That rewrites the snapshot, and the diff is then reviewed in the pull request —
which is the whole point: an API change is visible in review instead of silent.

## Debugging a failing CI job

Each job uploads its JUnit XML (and the backend its `coverage.xml`, the frontend
its `lcov.info`) as an artifact, and writes a short summary to the run page.

- **`backend-unit` red** — reproduce exactly: `make test-unit && make test-regression`.
  A coverage failure instead reads `Required test coverage of 80% not reached`.
- **`backend-integration` red with `MinIO unreachable`** — the service did not
  come up. The job prints `docker logs minio` on failure.
- **`backend-integration` red on a test** — reproduce with
  `make minio-test-up && make test-integration`.
- **`frontend` red in `vue-tsc`** — the tests are type-checked too
  (`tsconfig.app.json` includes `tests/**/*.ts`); `npm run build` reproduces it.

## Coverage

The backend gate is `COVERAGE_MIN`, 80, enforced by `make coverage` and by the
`backend-unit` job. The frontend reports coverage but has no threshold yet: set
one in `vite.config.ts` from the first measured baseline, and never below it.
````

- [ ] **Step 2: Update the `## Checks` section of `README.md`**

Replace the code block under `## Checks` with:

```sh
make test              # every suite: unit, integration, regression, frontend
make test-unit         # backend unit only (no services needed)
make test-integration  # backend integration (needs `make minio-test-up`)
make test-regression   # backend regression: pinned bugs + OpenAPI contract
make test-frontend     # vitest
make coverage          # both sides, backend fails under 80%
make snapshot          # rewrite the OpenAPI snapshot after an intended change
make lint              # ruff check + vue-tsc
make format            # apply ruff formatting
make format-check      # fail if the backend is unformatted
make build             # backend wheel + vue-tsc/vite build
```

Then add this line directly beneath that block:

```markdown
See [docs/testing.md](docs/testing.md) for what each suite is for, how the
directory decides the marker, and how to write a regression test.
```

- [ ] **Step 3: Update the `## Commands` section of `CLAUDE.md`**

In the raw-equivalents code block, replace the single pytest line with:

```sh
cd backend && .venv/bin/python -m pytest -m unit        # fast, nothing real
cd backend && .venv/bin/python -m pytest -m regression  # pinned bugs + contract
cd backend && .venv/bin/python -m pytest -m integration # needs a real MinIO
cd frontend && npm run test                             # vitest
```

And add, at the end of that section:

```markdown
Tests live in `backend/tests/{unit,integration,regression}/` and
`frontend/tests/{unit,regression}/`; the directory a test sits in decides its
pytest marker, so no test carries a decorator. `docs/testing.md` is the full
guide — read it before adding a test.
```

- [ ] **Step 4: Check the links resolve**

Run: `grep -n 'docs/testing.md' README.md CLAUDE.md && ls docs/testing.md`
Expected: both files reference it, and the file exists. This is acceptance
criterion 6.

- [ ] **Step 5: Full gate**

```bash
make minio-test-up
make verify
make minio-test-down
```

Expected: `verify: ok` — format-check, lint, all four suites, and both builds.

- [ ] **Step 6: Commit**

```bash
git add docs/testing.md README.md CLAUDE.md
git commit -m "docs: document the test layers and how to run them"
```

---

## Acceptance criteria check

Run these at the end, against the finished branch.

| # | Criterion | How to check |
|---|---|---|
| 1 | `make test` passes with MinIO up; integration skips without it | `make minio-test-up && make test && make minio-test-down && make test` |
| 2 | Backend ≥ 80%, frontend report produced | `make coverage` — backend TOTAL and `frontend/coverage/lcov.info` |
| 3 | An unsnapshotted API change fails | Task 3, Step 5 |
| 4 | Three separate passing jobs | `gh run view --json jobs` on the pushed branch |
| 5 | A broken MinIO fails, does not skip | Task 9, Step 8 |
| 6 | `docs/testing.md` exists, linked from the README | Task 10, Step 4 |
