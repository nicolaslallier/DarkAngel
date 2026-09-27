import io
import json
import uuid
from contextlib import nullcontext

from fastapi.testclient import TestClient

from app import summary
from app.core.config import get_settings
from app.main import app
from tests.conftest import token

client = TestClient(app)


def auth():
    return {"Authorization": f"Bearer {token()}"}


def upload(name="notes.txt", data=b"hello", content_type="text/plain"):
    return client.post("/api/files", headers=auth(), files={"file": (name, data, content_type)})


def test_text_of_reads_utf8_text():
    assert summary.text_of("Bonjour à tous".encode()) == "Bonjour à tous"


def test_text_of_tolerates_a_character_split_by_the_head_cut():
    # "é" is two bytes; the cut keeps only the first.
    assert summary.text_of("café".encode()[:-1]) == "caf"


def test_text_of_refuses_binary():
    assert summary.text_of(b"%PDF-1.7\n\xe2\xe3\xcf\xd3\xff\xfe") is None
    assert summary.text_of(b"\x89PNG\r\n\x1a\n\x00\x00") is None
    assert summary.text_of(b"   \n") is None


def test_ask_ollama_drops_the_thinking(monkeypatch):
    monkeypatch.setattr(get_settings(), "ollama_url", "http://ollama:11434/")
    sent = {}

    def urlopen(request, timeout):
        sent["url"], sent["body"] = request.full_url, json.loads(request.data)
        reply = {"response": "<think>hmm</think>\n  A shopping list.  "}
        return nullcontext(io.BytesIO(json.dumps(reply).encode()))

    monkeypatch.setattr(summary.urllib.request, "urlopen", urlopen)

    assert summary.ask_ollama("list.txt", "eggs") == "A shopping list."
    assert sent["url"] == "http://ollama:11434/api/generate"
    assert sent["body"]["stream"] is False
    assert sent["body"]["prompt"].endswith("eggs")


class Recorder:
    def __init__(self, _db=None):
        Recorder.calls = []

    def set_summary(self, owner_sub, file_id, text):
        Recorder.calls.append((owner_sub, file_id, text))


def summarize_with(monkeypatch, head, ask):
    monkeypatch.setattr(summary, "session_factory", lambda: lambda: nullcontext())
    monkeypatch.setattr(summary, "FileRepository", Recorder)
    monkeypatch.setattr(summary, "ask_ollama", ask)
    file_id = uuid.uuid4()
    summary.summarize("user-1", file_id, "a.txt", head)
    return Recorder.calls, file_id


def test_summarize_stores_the_answer(monkeypatch):
    calls, file_id = summarize_with(monkeypatch, b"eggs", lambda _n, _t: "A list.")
    assert calls == [("user-1", file_id, "A list.")]


def test_summarize_stores_none_when_ollama_fails(monkeypatch):
    def down(_name, _text):
        raise OSError("connection refused")

    calls, file_id = summarize_with(monkeypatch, b"eggs", down)
    assert calls == [("user-1", file_id, None)]


def test_summarize_never_asks_about_binary(monkeypatch):
    def never(_name, _text):
        raise AssertionError("binary sent to Ollama")

    calls, file_id = summarize_with(monkeypatch, b"\x89PNG\x00", never)
    assert calls == [("user-1", file_id, None)]


def queued(monkeypatch):
    calls = []
    monkeypatch.setattr(summary, "summarize", lambda *args: calls.append(args))
    return calls


def test_upload_queues_a_summary_of_the_head(repo, store, monkeypatch):
    monkeypatch.setattr(get_settings(), "ollama_url", "http://ollama:11434")
    calls = queued(monkeypatch)

    response = upload(data=b"x" * (summary.HEAD_BYTES + 10))

    assert response.status_code == 201
    assert response.json()["summary"] is None  # it lands after the response
    [(sub, file_id, name, head)] = calls
    assert (sub, str(file_id), name) == ("user-1", response.json()["id"], "notes.txt")
    assert head == b"x" * summary.HEAD_BYTES


def test_a_new_version_is_summarized_again(repo, store, monkeypatch):
    monkeypatch.setattr(get_settings(), "ollama_url", "http://ollama:11434")
    calls = queued(monkeypatch)

    upload(data=b"one")
    upload(data=b"two")

    assert [head for *_, head in calls] == [b"one", b"two"]


def test_no_summary_without_an_ollama_url(repo, store, monkeypatch):
    monkeypatch.setattr(get_settings(), "ollama_url", "")
    calls = queued(monkeypatch)

    assert upload().status_code == 201
    assert calls == []
