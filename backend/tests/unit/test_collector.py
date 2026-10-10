"""Unit — the collector, against a fake Portainer (httpx.MockTransport) and a fake bucket."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from app.collector import backups, main, portainer
from app.core.config import PortainerInstance, Settings
from tests.conftest import FakeInfraRepository

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
HEAVEN = PortainerInstance(name="HEAVEN", url="https://heaven.example", api_key="sekret-key")


def portainer_api(**overrides):
    routes = {
        "/api/system/status": {"Version": "2.21.0"},
        "/api/endpoints": [{"Id": 1}, {"Id": 2}],
        "/api/stacks": [{"Id": 1}, {"Id": 2}, {"Id": 3}],
    } | overrides

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.headers["X-API-KEY"] == "sekret-key"
        body = routes[request.url.path]
        if isinstance(body, httpx.Response):
            return body
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def test_slug_matches_the_backup_script():
    assert portainer.slug("MY_BOX2") == "my-box2"


def test_a_healthy_instance_reports_version_environments_and_stacks():
    reading = portainer.check(HEAVEN, portainer_api())

    assert reading == portainer.Reading(True, "2.21.0", 2, 3)


def test_a_bad_status_code_is_unreachable_and_the_key_stays_out_of_the_error():
    reading = portainer.check(HEAVEN, portainer_api(**{"/api/stacks": httpx.Response(401)}))

    assert reading.reachable is False
    assert "401" in reading.error
    assert "sekret-key" not in reading.error


def test_a_page_instead_of_json_is_unreachable():
    page = httpx.Response(200, text="<html>SSO login</html>")

    reading = portainer.check(HEAVEN, portainer_api(**{"/api/system/status": page}))

    assert reading.reachable is False


def test_an_object_where_a_list_is_expected_is_unreachable():
    reading = portainer.check(HEAVEN, portainer_api(**{"/api/endpoints": {"message": "nope"}}))

    assert reading.reachable is False


def test_a_connection_failure_is_unreachable():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    reading = portainer.check(HEAVEN, httpx.MockTransport(refuse))

    assert reading.reachable is False
    assert "ConnectError" in reading.error


class FakeBucket:
    def __init__(self, objects=(), fail=None):
        self.objects, self.fail, self.prefixes = list(objects), fail, []

    def list_objects(self, bucket, prefix=None, recursive=False):
        self.prefixes.append(prefix)
        if self.fail:
            raise self.fail
        return iter(self.objects)


def obj(key, when, size=100, is_dir=False):
    return SimpleNamespace(object_name=key, last_modified=when, size=size, is_dir=is_dir)


def test_newest_picks_the_latest_archive_under_the_instance_prefix():
    bucket = FakeBucket(
        [
            obj("heaven/20261008T000000Z.tar.gz.encrypted", NOW - timedelta(days=2), 90),
            obj("heaven/20261010T000000Z.tar.gz.encrypted", NOW - timedelta(hours=12), 120),
            obj("heaven/sub/", NOW, is_dir=True),
        ]
    )

    archive = backups.newest(bucket, "portainer-backups", "heaven")

    assert archive == backups.Archive(
        NOW - timedelta(hours=12), 120, "heaven/20261010T000000Z.tar.gz.encrypted"
    )
    assert bucket.prefixes == ["heaven/"]


def test_newest_is_none_for_an_empty_prefix():
    assert backups.newest(FakeBucket(), "portainer-backups", "heaven") is None


def run_once(repo, instances, bucket, monkeypatch, check):
    monkeypatch.setattr(portainer, "check", check)
    main.collect_once(repo, instances, bucket, "portainer-backups", now=NOW)


def test_collect_once_records_status_and_backup_per_instance(monkeypatch):
    repo = FakeInfraRepository()
    bucket = FakeBucket([obj("heaven/a.tar.gz.encrypted", NOW - timedelta(hours=3), 500)])

    run_once(
        repo, [HEAVEN], bucket, monkeypatch, lambda inst: portainer.Reading(True, "2.21.0", 2, 3)
    )

    (status,) = repo.statuses
    (backup,) = repo.backups
    assert (status.instance, status.reachable, status.version, status.checked_at) == (
        "heaven",
        True,
        "2.21.0",
        NOW,
    )
    assert (backup.instance, backup.size_bytes, backup.object_key) == (
        "heaven",
        500,
        "heaven/a.tar.gz.encrypted",
    )


def test_one_unreachable_instance_does_not_stop_the_next(monkeypatch):
    repo = FakeInfraRepository()
    infra = PortainerInstance(name="INFRA", url="https://infra.example", api_key="k")

    def check(inst):
        if inst.name == "HEAVEN":
            return portainer.Reading(False, error="ConnectError: refused")
        return portainer.Reading(True, "2.20.0", 1, 1)

    run_once(repo, [HEAVEN, infra], FakeBucket(), monkeypatch, check)

    assert [(s.instance, s.reachable) for s in repo.statuses] == [
        ("heaven", False),
        ("infra", True),
    ]


def test_an_unreadable_bucket_records_no_backup_and_still_records_the_status(monkeypatch):
    repo = FakeInfraRepository()
    bucket = FakeBucket(fail=RuntimeError("s3 down"))

    run_once(
        repo, [HEAVEN], bucket, monkeypatch, lambda inst: portainer.Reading(True, "2.21.0", 1, 1)
    )

    assert len(repo.statuses) == 1
    (backup,) = repo.backups
    assert backup.last_backup_at is None


def test_collect_once_prunes_rows_older_than_thirty_days(monkeypatch):
    repo = FakeInfraRepository()
    repo.add_status(instance="heaven", reachable=True, checked_at=NOW - timedelta(days=31))
    repo.add_backup(
        instance="heaven",
        last_backup_at=None,
        size_bytes=None,
        object_key=None,
        checked_at=NOW - timedelta(days=31),
    )

    run_once(repo, [], FakeBucket(), monkeypatch, lambda inst: None)

    assert repo.statuses == [] and repo.backups == []


BAD = PortainerInstance(name="BAD", url="http://[::1", api_key="sekret-key")


def test_a_malformed_url_is_unreachable_and_the_key_stays_out_of_the_error():
    reading = portainer.check(BAD)

    assert reading.reachable is False
    assert "sekret-key" not in reading.error


def test_a_malformed_url_does_not_stop_the_next_instance():
    repo = FakeInfraRepository()
    infra = PortainerInstance(name="INFRA", url="https://infra.example", api_key="k")
    real_check = portainer.check

    def check(inst):
        return real_check(inst, portainer_api_for_infra() if inst.name == "INFRA" else None)

    def portainer_api_for_infra():
        return httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"Version": "2"} if r.url.path.endswith("status") else []
            )
        )

    import pytest

    with pytest.MonkeyPatch.context() as mp:
        run_once(repo, [BAD, infra], FakeBucket(), mp, check)

    assert [(s.instance, s.reachable) for s in repo.statuses] == [("bad", False), ("infra", True)]


def test_the_api_key_stays_out_of_repr():
    instance = PortainerInstance(name="H", url="https://h", api_key="sekret-key")

    assert "sekret-key" not in repr(instance)


def test_an_invalid_instances_setting_does_not_leak_the_key(monkeypatch):
    monkeypatch.setenv("DARKANGEL_PORTAINER_INSTANCES", '[{"name":"H","api_key":"sekret-key"}]')

    with pytest.raises(ValidationError) as raised:
        Settings(_env_file=None)

    assert "sekret-key" not in str(raised.value)
