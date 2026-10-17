"""Regression — chat ownership isolation, ROUTE layer (against FakeChatRepository).

`Runs.reserve`/`release` are keyed by conversation id alone, so the routes are the
only thing standing between user-2 and user-1's live reply: each must check the
caller owns the conversation BEFORE reserving, stopping or subscribing. A foreign
id is a 404 (never 403, never 409), and user-1's run is left untouched.
"""

from concurrent.futures import ThreadPoolExecutor

from tests.conftest import WAIT, sse_events, token, wait_until


def auth(sub):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def test_user_two_cannot_touch_user_ones_conversation_or_its_run(chat):
    client = chat.client
    chat.llm.events = [("content", "Hel"), WAIT, ("content", "lo")]
    body = {"content": "hi", "model": "m"}
    future = ThreadPoolExecutor(1).submit(
        client.post, "/api/chat/conversations/new/messages", json=body, headers=auth("user-1")
    )
    wait_until(chat.llm.waiting.is_set)
    (cid,) = chat.runs.running("user-1")
    url = f"/api/chat/conversations/{cid}"
    other = auth("user-2")

    assert client.get(url, headers=other).status_code == 404
    assert client.patch(url, json={"title": "mine"}, headers=other).status_code == 404
    assert client.delete(url, headers=other).status_code == 404
    assert client.post(f"{url}/messages", json=body, headers=other).status_code == 404
    assert client.post(f"{url}/regenerate", json={"model": "m"}, headers=other).status_code == 404
    assert client.get(f"{url}/stream", headers=other).status_code == 404
    assert client.post(f"{url}/stop", headers=other).status_code == 404
    assert client.get("/api/chat/conversations", headers=other).json() == []

    client.portal.call(chat.llm.gate.set)
    done = sse_events(future.result(timeout=5).text)[-1][1]["message"]
    assert (done["status"], done["content"]) == ("complete", "Hello")
    convo = chat.repo.conversations[0]
    assert convo.deleted_at is None and convo.title != "mine"
    assert len(chat.repo.rows) == 2
