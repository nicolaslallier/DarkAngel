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
