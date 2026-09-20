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
