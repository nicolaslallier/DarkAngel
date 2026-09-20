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

# Every env var this fixture ever writes, so setup/restore stay in lockstep.
ENV_KEYS = (*DEFAULTS, "DARKANGEL_S3_BUCKET")


@pytest.fixture(scope="session", autouse=True)
def minio_bucket():
    """Point the app at a throwaway bucket on a real MinIO, and clean it up.

    The app never creates its own bucket (`make minio` does, in production), so
    the fixture owns one for the length of the session. Everything below the
    snapshot runs inside a `finally` so a skip or a CI-fail — both raised
    before the bucket even exists — still restore the environment and both
    lru_caches before this process exits.

    That restore fires at *session* teardown, after every test in this process
    has already run — it does NOT protect a unit/regression test sharing this
    same pytest process: such a test would still read settings pointed at this
    fixture's throwaway bucket, because the restore hasn't happened yet. Suite
    isolation instead comes from running each suite in its own pytest process
    (`make test-unit`, `make test-integration`, `make test-regression`, and
    `coverage-backend`'s three separate invocations) — never merge them into
    one `pytest` call.
    """
    prior_env = {key: os.environ.get(key) for key in ENV_KEYS}

    def restore():
        for key, value in prior_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()
        files.minio_client.cache_clear()

    try:
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
    finally:
        restore()
