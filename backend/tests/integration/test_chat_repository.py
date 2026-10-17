"""Integration — the chat repository's SQL, against a real PostgreSQL."""

import uuid

import pytest

from app.repositories.chat import ChatRepository


@pytest.fixture
def chat(db):
    return ChatRepository(db)


def test_list_is_owner_scoped_and_newest_first(chat):
    first = chat.create("user-1", "first")
    second = chat.create("user-1", "second")
    chat.create("user-2", "theirs")
    chat.add_message("user-1", first.id, role="user", content="bump")

    assert [c.title for c in chat.list("user-1")] == ["first", "second"]
    assert [c.title for c in chat.list("user-2")] == ["theirs"]
    assert chat.get("user-2", second.id) is None


def test_search_matches_title_or_message_content_case_insensitively(chat):
    convo = chat.create("user-1", "Groceries")
    chat.add_message("user-1", convo.id, role="user", content="Heat PUMP")

    assert [c.id for c in chat.list("user-1", "pump")] == [convo.id]
    assert [c.id for c in chat.list("user-1", "GROCER")] == [convo.id]
    assert chat.list("user-1", "nope") == []
    assert chat.list("user-2", "pump") == []


def test_soft_deleted_conversation_is_gone_from_list_and_get(chat):
    convo = chat.create("user-1", "x")

    assert chat.soft_delete("user-2", convo.id) is False
    assert chat.soft_delete("user-1", convo.id) is True
    assert chat.list("user-1") == []
    assert chat.get("user-1", convo.id) is None
    assert chat.soft_delete("user-1", convo.id) is False


def test_rename(chat):
    convo = chat.create("user-1", "old")

    assert chat.rename("user-2", convo.id, "hax") is None
    assert chat.rename("user-1", convo.id, "new").title == "new"


def test_add_message_bumps_updated_at(chat):
    convo = chat.create("user-1", "x")
    before = convo.updated_at

    chat.add_message("user-1", convo.id, role="user", content="hi")

    assert chat.get("user-1", convo.id).updated_at > before


def test_truncate_after_deletes_only_later_messages(chat):
    convo = chat.create("user-1", "x")
    ids = [chat.add_message("user-1", convo.id, role="user", content=str(i)).id for i in range(3)]

    chat.truncate_after("user-1", convo.id, ids[0])

    assert [m.id for m in chat.messages("user-1", convo.id)] == ids[:1]


def test_other_owner_cannot_read_or_update_messages(chat):
    convo = chat.create("user-1", "x")
    msg = chat.add_message("user-1", convo.id, role="user", content="secret")

    assert chat.messages("user-2", convo.id) == []
    chat.update_message("user-2", msg.id, content="pwned")
    chat.delete_message("user-2", msg.id)

    assert [m.content for m in chat.messages("user-1", convo.id)] == ["secret"]


def test_update_message_sets_fields(chat):
    convo = chat.create("user-1", "x")
    msg = chat.add_message("user-1", convo.id, role="assistant", content="", status="streaming")

    chat.update_message("user-1", msg.id, content="done", status="complete", completion_tokens=3)

    (row,) = chat.messages("user-1", convo.id)
    assert (row.content, row.status, row.completion_tokens) == ("done", "complete", 3)


def test_stop_streaming_marks_streaming_messages_stopped(chat):
    convo = chat.create("user-1", "x")
    chat.add_message("user-1", convo.id, role="assistant", content="", status="streaming")
    chat.add_message("user-1", convo.id, role="user", content="hi")

    assert chat.stop_streaming() == 1
    assert [m.status for m in chat.messages("user-1", convo.id)] == ["stopped", "complete"]


def test_add_message_refuses_another_owners_conversation(chat):
    convo = chat.create("user-1", "x")
    gone = chat.create("user-1", "gone")
    chat.soft_delete("user-1", gone.id)

    with pytest.raises(LookupError):
        chat.add_message("user-2", convo.id, role="user", content="intruder")
    with pytest.raises(LookupError):
        chat.add_message("user-1", gone.id, role="user", content="late")

    assert chat.messages("user-1", convo.id) == []


def test_truncate_after_unknown_or_foreign_pivot_deletes_nothing(chat):
    mine = chat.create("user-1", "x")
    other = chat.create("user-1", "y")
    kept = [chat.add_message("user-1", mine.id, role="user", content=str(i)).id for i in range(2)]
    foreign = chat.add_message("user-1", other.id, role="user", content="p").id
    chat.add_message("user-1", other.id, role="user", content="q")

    chat.truncate_after("user-1", mine.id, uuid.uuid4())
    chat.truncate_after("user-1", mine.id, foreign)
    chat.truncate_after("user-2", mine.id, kept[0])

    assert [m.id for m in chat.messages("user-1", mine.id)] == kept
    assert len(chat.messages("user-1", other.id)) == 2
