"""Unit — /api/chat: conversations, streamed replies, reattach and stop."""

import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from tests.conftest import WAIT, sse_events, token, wait_until


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def send(client, cid="new", content="Hi there", sub="user-1", **extra):
    body = {"content": content, "model": "m", **extra}
    return client.post(f"/api/chat/conversations/{cid}/messages", json=body, headers=auth(sub))


def in_background(fn, *args, **kwargs):
    return ThreadPoolExecutor(1).submit(fn, *args, **kwargs)


def seeded(chat, *pairs):
    """A conversation holding complete (role, content) messages, in order."""
    convo = chat.repo.create("user-1", "Seeded")
    for role, content in pairs:
        chat.repo.add_message("user-1", convo.id, role=role, content=content, model="m")
    return convo


def gated_send(chat, cid="new"):
    """Starts a send whose reply pauses after "Hel"; returns (future, conversation id)."""
    chat.llm.events = [("content", "Hel"), WAIT, ("content", "lo")]
    future = in_background(send, chat.client, cid)
    wait_until(chat.llm.waiting.is_set)
    (running,) = chat.runs.running("user-1")
    return future, running


def release(chat, future):
    chat.client.portal.call(chat.llm.gate.set)
    return sse_events(future.result(timeout=5).text)


def test_unconfigured_chat_is_503(chat_repo, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_url", "")
    client = TestClient(app)
    assert client.get("/api/chat/models", headers=auth()).status_code == 503
    assert send(client).status_code == 503
    cid = chat_repo.create("user-1", "c").id
    assert client.get("/api/chat/conversations", headers=auth()).status_code == 503
    assert client.post(f"/api/chat/conversations/{cid}/stop", headers=auth()).status_code == 503


def test_send_to_new_creates_conversation_with_fallback_title_and_streams(chat):
    chat.llm.title = ""  # no generated title: the fallback stays
    response = send(chat.client, content="  What   is\nthe weather? ")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    (convo,) = chat.repo.conversations
    assert convo.title == "What is the weather?"
    start = sse_events(response.text)[0][1]
    assert start["conversation"] == {"id": str(convo.id), "title": convo.title}
    user, assistant = chat.repo.rows
    assert start["user_message"]["id"] == str(user.id)
    assert start["user_message"]["role"] == "user"
    assert start["assistant_message_id"] == str(assistant.id)
    assert (assistant.content, assistant.status, assistant.model) == ("Hello", "complete", "m")


def test_send_stream_has_start_deltas_title_done_in_order(chat):
    chat.llm.events = [("reasoning", "Hmm"), ("content", "Hel"), ("content", "lo")]
    events = sse_events(send(chat.client).text)

    assert [e for e, _ in events] == ["start", "reasoning", "content", "content", "title", "done"]
    assert events[4][1] == {"title": "Smart title"}
    message = events[5][1]["message"]
    assert set(message) == set(events[0][1]["user_message"])
    assert (message["content"], message["reasoning"], message["status"]) == (
        "Hello",
        "Hmm",
        "complete",
    )
    assert chat.repo.conversations[0].title == "Smart title"
    model, context = chat.llm.streams[0]
    assert model == "m" and context[0]["role"] == "system"
    assert context[1:] == [{"role": "user", "content": "Hi there"}]


def test_concurrent_send_is_409(chat):
    future, cid = gated_send(chat)

    second = send(chat.client, cid)
    regenerate = chat.client.post(
        f"/api/chat/conversations/{cid}/regenerate", json={"model": "m"}, headers=auth()
    )

    assert second.status_code == 409
    assert second.json()["detail"] == "A reply is already running"
    assert regenerate.status_code == 409
    assert release(chat, future)[-1][1]["message"]["status"] == "complete"


def test_edit_replaces_text_and_drops_later_messages(chat):
    convo = seeded(chat, ("user", "one"), ("assistant", "A1"), ("user", "two"), ("assistant", "A2"))
    first = chat.repo.rows[0]

    events = sse_events(send(chat.client, convo.id, "one, edited", replace_from=str(first.id)).text)

    assert events[0][1]["user_message"]["id"] == str(first.id)
    assert events[0][1]["user_message"]["content"] == "one, edited"
    assert [(m.role, m.content) for m in chat.repo.rows] == [
        ("user", "one, edited"),
        ("assistant", "Hello"),
    ]
    assert chat.llm.streams[0][1][1:] == [{"role": "user", "content": "one, edited"}]
    assert convo.title == "Seeded"  # not a first reply: no title


def test_edit_of_an_assistant_or_unknown_message_is_404(chat):
    convo = seeded(chat, ("user", "one"), ("assistant", "A1"))

    assert send(chat.client, convo.id, replace_from=str(chat.repo.rows[1].id)).status_code == 404
    assert send(chat.client, convo.id, replace_from=str(uuid.uuid4())).status_code == 404
    assert len(chat.repo.rows) == 2
    assert send(chat.client, convo.id).status_code == 200  # reservation was released


def test_regenerate_replaces_last_assistant_message(chat):
    convo = seeded(chat, ("user", "one"), ("assistant", "old answer"))
    old = chat.repo.rows[1]

    response = chat.client.post(
        f"/api/chat/conversations/{convo.id}/regenerate", json={"model": "m"}, headers=auth()
    )

    events = sse_events(response.text)
    assert events[0][1]["user_message"] is None
    assert [(m.role, m.content) for m in chat.repo.rows] == [
        ("user", "one"),
        ("assistant", "Hello"),
    ]
    assert old not in chat.repo.rows
    assert chat.llm.streams[0][1][1:] == [{"role": "user", "content": "one"}]


def test_regenerate_after_error_message_retries(chat):
    convo = seeded(chat, ("user", "one"))
    chat.repo.add_message("user-1", convo.id, role="assistant", content="", status="error")

    response = chat.client.post(
        f"/api/chat/conversations/{convo.id}/regenerate", json={"model": "m"}, headers=auth()
    )

    assert sse_events(response.text)[-1][1]["message"]["status"] == "complete"
    assert [(m.role, m.status) for m in chat.repo.rows] == [
        ("user", "complete"),
        ("assistant", "complete"),
    ]


def test_regenerate_without_a_user_message_is_409(chat):
    convo = seeded(chat)
    response = chat.client.post(
        f"/api/chat/conversations/{convo.id}/regenerate", json={"model": "m"}, headers=auth()
    )
    assert response.status_code == 409


def test_reattach_gets_snapshot_then_done(chat):
    future, cid = gated_send(chat)

    reattach = in_background(
        chat.client.get, f"/api/chat/conversations/{cid}/stream", headers=auth()
    )
    wait_until(lambda: len(chat.runs._runs[cid].queues) == 2)
    sent = release(chat, future)
    events = sse_events(reattach.result(timeout=5).text)

    assert events[0] == ("snapshot", {"reasoning": "", "content": "Hel"})
    assert events[1] == ("content", {"text": "lo"})
    assert events[-1] == sent[-1]
    assert events[-1][1]["message"]["content"] == "Hello"


def test_reattach_without_run_is_204(chat):
    convo = seeded(chat)
    response = chat.client.get(f"/api/chat/conversations/{convo.id}/stream", headers=auth())
    assert response.status_code == 204


def test_stop_saves_stopped(chat):
    future, cid = gated_send(chat)

    response = chat.client.post(f"/api/chat/conversations/{cid}/stop", headers=auth())

    assert response.status_code == 204
    done = sse_events(future.result(timeout=5).text)[-1][1]["message"]
    assert (done["status"], done["content"]) == ("stopped", "Hel")
    assert chat.repo.rows[-1].status == "stopped"
    # Nothing running: still 204.
    assert (
        chat.client.post(f"/api/chat/conversations/{cid}/stop", headers=auth()).status_code == 204
    )


def test_list_marks_streaming_conversations(chat):
    idle = seeded(chat)
    future, cid = gated_send(chat)

    listed = chat.client.get("/api/chat/conversations", headers=auth()).json()
    opened = chat.client.get(f"/api/chat/conversations/{cid}", headers=auth()).json()

    assert [(c["id"], c["streaming"]) for c in listed] == [(str(cid), True), (str(idle.id), False)]
    assert set(listed[0]) == {"id", "title", "updated_at", "streaming"}
    assert opened["streaming"] is True
    assert [m["status"] for m in opened["messages"]] == ["complete", "streaming"]
    release(chat, future)


def test_search_param_is_passed_through(chat):
    seeded(chat, ("user", "about bananas"))
    chat.repo.create("user-1", "Other")

    listed = chat.client.get("/api/chat/conversations?q=BANANA", headers=auth()).json()

    assert [c["title"] for c in listed] == ["Seeded"]


def test_create_and_get_conversation(chat):
    created = chat.client.post("/api/chat/conversations", json={}, headers=auth())

    assert created.status_code == 201
    body = created.json()
    assert (body["title"], body["streaming"], body["messages"]) == ("New chat", False, [])
    got = chat.client.get(f"/api/chat/conversations/{body['id']}", headers=auth()).json()
    assert got == body


def test_rename_validates_length(chat):
    convo = seeded(chat)
    url = f"/api/chat/conversations/{convo.id}"

    assert chat.client.patch(url, json={"title": "   "}, headers=auth()).status_code == 422
    assert chat.client.patch(url, json={"title": "x" * 201}, headers=auth()).status_code == 422
    response = chat.client.patch(url, json={"title": "  Trip plans "}, headers=auth())

    assert response.status_code == 200
    assert response.json()["title"] == "Trip plans"
    assert response.json()["streaming"] is False
    assert convo.title == "Trip plans"


def test_delete_is_204_and_hides_conversation(chat):
    convo = seeded(chat)
    url = f"/api/chat/conversations/{convo.id}"

    assert chat.client.delete(url, headers=auth()).status_code == 204
    assert chat.client.get(url, headers=auth()).status_code == 404
    assert chat.client.get("/api/chat/conversations", headers=auth()).json() == []


def test_delete_stops_a_running_reply(chat):
    future, cid = gated_send(chat)

    assert chat.client.delete(f"/api/chat/conversations/{cid}", headers=auth()).status_code == 204

    assert sse_events(future.result(timeout=5).text)[-1][1]["message"]["status"] == "stopped"


def test_invalid_id_is_404(chat):
    for url in ("/api/chat/conversations/nope", "/api/chat/conversations/new"):
        assert chat.client.get(url, headers=auth()).status_code == 404
    regenerate = chat.client.post(
        "/api/chat/conversations/new/regenerate", json={"model": "m"}, headers=auth()
    )
    assert regenerate.status_code == 404
    assert send(chat.client, "nope").status_code == 404


def test_content_length_is_validated(chat):
    assert send(chat.client, content="").status_code == 422
    assert send(chat.client, content="x" * 100_001).status_code == 422
    assert chat.repo.conversations == []


def test_models_lists_loaded_state(chat):
    response = chat.client.get("/api/chat/models", headers=auth())

    assert response.json() == [
        {"id": "m", "loaded": True, "context_length": 32768},
        {"id": "cloud", "loaded": None, "context_length": 8000},
    ]


def test_stream_response_disables_nginx_buffering(chat):
    assert send(chat.client).headers["x-accel-buffering"] == "no"


def test_routes_require_a_token(chat):
    assert chat.client.get("/api/chat/conversations").status_code == 401


def test_send_to_a_conversation_deleted_meanwhile_is_404(chat):
    convo = seeded(chat, ("user", "one"), ("assistant", "A1"))
    add_message = chat.repo.add_message

    def deleted_first(sub, cid, **fields):
        chat.repo.soft_delete(sub, cid)
        return add_message(sub, cid, **fields)

    chat.repo.add_message = deleted_first

    assert send(chat.client, convo.id).status_code == 404
    assert chat.runs._runs == {}  # the reservation was released
