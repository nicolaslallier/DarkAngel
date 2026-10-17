"""Unit — the restart sweep runs at startup and never blocks it."""

from contextlib import nullcontext
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import main
from tests.conftest import FakeChatRepository


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


def test_lifespan_marks_streaming_messages_stopped(monkeypatch):
    chats = FakeChatRepository()
    convo = chats.create("user-1", "c")
    msg = chats.add_message("user-1", convo.id, role="assistant", content="", status="streaming")

    def broken_invoices(db):
        raise RuntimeError("invoice sweep down")

    monkeypatch.setattr(
        main, "session_factory", lambda: lambda: nullcontext(SimpleNamespace(rollback=lambda: None))
    )
    monkeypatch.setattr(main, "InvoiceRepository", broken_invoices)
    monkeypatch.setattr(main, "ChatRepository", lambda db: chats)

    with TestClient(main.create_app()):
        pass

    assert msg.status == "stopped"  # the invoice sweep failing did not skip it
