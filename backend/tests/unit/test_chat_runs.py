import asyncio
from contextlib import nullcontext

import pytest

from app.chat.llm import LlmError
from app.chat.runs import Busy, Runs, sse
from tests.conftest import FakeChatRepository

WAIT = ("wait",)  # FakeLlm pauses here until `gate` is set


class FakeLlm:
    def __init__(self, events, *, error=None, title="New title"):
        self.events = events
        self.error = error
        self.title = title
        self.gate = asyncio.Event()
        self.waiting = asyncio.Event()
        self.completions = []

    async def stream(self, model, messages):
        for event in self.events:
            if event == WAIT:
                self.waiting.set()
                await self.gate.wait()
                continue
            yield event
        if self.error:
            raise self.error

    async def complete(self, model, messages):
        self.completions.append((model, messages))
        if self.title is WAIT:
            self.waiting.set()
            await self.gate.wait()
        if isinstance(self.title, Exception):
            raise self.title
        return self.title


def setup(**kwargs):
    repo = FakeChatRepository()
    convo = repo.create("alice", "New chat")
    msg = repo.add_message(
        "alice", convo.id, role="assistant", content="", status="streaming", model="m"
    )
    return Runs(store=lambda: nullcontext(repo), **kwargs), repo, convo, msg


def start(runs, convo, msg, llm, title_from=None):
    runs.reserve(convo.id)
    runs.start(
        owner_sub="alice",
        conversation_id=convo.id,
        message_id=msg.id,
        created_at=msg.created_at,
        llm=llm,
        model="m",
        context=[{"role": "user", "content": "hi"}],
        title_from=title_from,
    )
    return runs.subscribe("alice", convo.id, snapshot=False)


async def drain(events):
    return [e async for e in events]


def test_run_saves_complete_reply_and_publishes_done():
    async def body():
        runs, _repo, convo, msg = setup()
        usage = {"prompt_tokens": 5, "completion_tokens": 2}
        llm = FakeLlm(
            [
                ("progress", 1, 4),
                ("reasoning", "Think"),
                ("content", "Hel"),
                ("content", "lo"),
                ("usage", usage),
            ]
        )
        events = await drain(start(runs, convo, msg, llm))

        assert events[:4] == [
            ("progress", {"processed": 1, "total": 4}),
            ("reasoning", {"text": "Think"}),
            ("content", {"text": "Hel"}),
            ("content", {"text": "lo"}),
        ]
        kind, data = events[4]
        assert kind == "done"
        assert len(events) == 5
        assert data["message"] == {
            "id": str(msg.id),
            "role": "assistant",
            "content": "Hello",
            "reasoning": "Think",
            "model": "m",
            "status": "complete",
            "error": None,
            "created_at": msg.created_at.isoformat(),
        }
        assert (msg.content, msg.reasoning, msg.status) == ("Hello", "Think", "complete")
        assert (msg.prompt_tokens, msg.completion_tokens) == (5, 2)
        assert runs.running("alice") == set()
        assert runs.subscribe("alice", convo.id, snapshot=True) is None
        assert llm.completions == []  # not a first reply: no title

    asyncio.run(body())


def test_reasoning_only_reply_saves_complete_with_empty_content():
    async def body():
        runs, _repo, convo, msg = setup()
        events = await drain(start(runs, convo, msg, FakeLlm([("reasoning", "hmm")])))

        assert events[-1][1]["message"]["status"] == "complete"
        assert (msg.content, msg.reasoning, msg.status) == ("", "hmm", "complete")

    asyncio.run(body())


def test_dropped_subscriber_does_not_stop_the_run():
    async def body():
        runs, _repo, convo, msg = setup()
        llm = FakeLlm([("content", "a"), WAIT, ("content", "b")])
        first = start(runs, convo, msg, llm)
        assert await anext(first) == ("content", {"text": "a"})
        await first.aclose()  # the client went away
        assert not runs._runs[convo.id].queues

        later = runs.subscribe("alice", convo.id, snapshot=True)
        llm.gate.set()
        events = await drain(later)

        assert events[0] == ("snapshot", {"reasoning": "", "content": "a"})
        assert events[-1][1]["message"]["status"] == "complete"
        assert (msg.content, msg.status) == ("ab", "complete")

    asyncio.run(body())


def test_snapshot_subscriber_gets_text_so_far_then_live_deltas():
    async def body():
        runs, _repo, convo, msg = setup()
        llm = FakeLlm([("reasoning", "r"), ("content", "Hel"), WAIT, ("content", "lo")])
        first = start(runs, convo, msg, llm)
        await llm.waiting.wait()

        later = runs.subscribe("alice", convo.id, snapshot=True)
        llm.gate.set()
        events, _ = await asyncio.gather(drain(later), drain(first))

        assert events[:2] == [
            ("snapshot", {"reasoning": "r", "content": "Hel"}),
            ("content", {"text": "lo"}),
        ]
        assert events[2][0] == "done"
        assert len(events) == 3

    asyncio.run(body())


def test_stop_saves_stopped_and_publishes_done():
    async def body():
        runs, _repo, convo, msg = setup()
        events = start(runs, convo, msg, FakeLlm([("content", "par"), WAIT]))
        assert await anext(events) == ("content", {"text": "par"})

        assert runs.stop("alice", convo.id) is True
        rest = await drain(events)

        assert [kind for kind, _ in rest] == ["done"]
        assert rest[0][1]["message"]["status"] == "stopped"
        assert (msg.content, msg.status) == ("par", "stopped")
        assert runs.running("alice") == set()
        assert runs.stop("alice", convo.id) is False

    asyncio.run(body())


def test_llm_error_saves_error_status_and_safe_text():
    async def body():
        runs, _repo, convo, msg = setup()
        llm = FakeLlm([("content", "x")], error=LlmError("m is not loaded on the cluster"))
        events = await drain(start(runs, convo, msg, llm, title_from="hi"))

        assert [kind for kind, _ in events] == ["content", "error", "done"]
        assert events[1][1] == {"message": "m is not loaded on the cluster"}
        assert events[2][1]["message"]["error"] == "m is not loaded on the cluster"
        assert (msg.content, msg.status, msg.error) == (
            "x",
            "error",
            "m is not loaded on the cluster",
        )
        assert llm.completions == []  # no title for a failed reply

    asyncio.run(body())


def test_partial_text_is_flushed_while_streaming():
    async def body():
        runs, _repo, convo, msg = setup(flush_seconds=0)
        llm = FakeLlm([("reasoning", "r"), ("content", "a"), WAIT, ("content", "b")])
        events = start(runs, convo, msg, llm)
        await llm.waiting.wait()

        assert (msg.content, msg.reasoning, msg.status) == ("a", "r", "streaming")
        llm.gate.set()
        await drain(events)
        assert (msg.content, msg.status) == ("ab", "complete")

    asyncio.run(body())


async def settle(runs):
    """Waits for the detached title tasks."""
    await asyncio.gather(*runs._titles)


def test_first_reply_gets_model_title_after_done():
    async def body():
        runs, _repo, convo, msg = setup()
        llm = FakeLlm([("content", "Sure")], title='"Lisbon trip plan"\nmore text')
        events = await drain(start(runs, convo, msg, llm, title_from="Plan a trip to Lisbon"))

        assert [kind for kind, _ in events] == ["content", "done"]
        assert runs._runs == {}  # registry empty right after done
        await settle(runs)
        assert convo.title == "Lisbon trip plan"
        ((model, prompt),) = llm.completions
        assert model == "m"
        assert "Plan a trip to Lisbon" in prompt[-1]["content"]
        assert runs._titles == set()

    asyncio.run(body())


def test_title_failure_keeps_fallback():
    async def body():
        runs, _repo, convo, msg = setup()
        llm = FakeLlm([("content", "Sure")], title=LlmError("The model did not answer"))
        events = await drain(start(runs, convo, msg, llm, title_from="Plan a trip"))
        await settle(runs)

        assert [kind for kind, _ in events] == ["content", "done"]
        assert events[-1][1]["message"]["status"] == "complete"
        assert convo.title == "New chat"

    asyncio.run(body())


def test_hanging_title_does_not_delay_done_and_times_out():
    async def body():
        runs, _repo, convo, msg = setup(title_timeout=0.05)
        llm = FakeLlm([("content", "Sure")], title=WAIT)  # complete() never returns
        events = await drain(start(runs, convo, msg, llm, title_from="Plan a trip"))

        assert events[-1][1]["message"]["status"] == "complete"
        assert runs.running("alice") == set()
        assert len(runs._titles) == 1  # still pending
        await settle(runs)  # the timeout ends it silently
        assert (msg.status, convo.title) == ("complete", "New chat")

    asyncio.run(body())


def test_stop_after_done_does_not_affect_the_title():
    async def body():
        runs, _repo, convo, msg = setup()
        llm = FakeLlm([("content", "Sure")], title=WAIT)
        await drain(start(runs, convo, msg, llm, title_from="Plan a trip"))
        await llm.waiting.wait()

        assert runs.stop("alice", convo.id) is False  # the run is gone
        llm.title = "Trip"
        llm.gate.set()
        await settle(runs)
        assert convo.title == "Trip"

    asyncio.run(body())


def test_failed_periodic_flush_does_not_end_the_run():
    async def body():
        runs, repo, convo, msg = setup(flush_seconds=0)
        real, calls = repo.update_message, []

        def flaky(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError("db blip")
            real(*args, **kwargs)

        repo.update_message = flaky
        llm = FakeLlm([("content", "a"), ("content", "b")])
        events = await drain(start(runs, convo, msg, llm))

        assert [kind for kind, _ in events] == ["content", "content", "done"]
        assert (msg.content, msg.status, msg.error) == ("ab", "complete", None)
        assert len(calls) == 3  # two flushes, one final save

    asyncio.run(body())


def test_reserve_twice_raises_busy():
    runs, _repo, convo, _msg = setup()
    runs.reserve(convo.id)
    with pytest.raises(Busy):
        runs.reserve(convo.id)
    assert runs.running("alice") == set()  # reserved is not running yet
    runs.release(convo.id)
    runs.reserve(convo.id)


def test_other_owner_cannot_subscribe_or_stop():
    async def body():
        runs, _repo, convo, msg = setup()
        events = start(runs, convo, msg, FakeLlm([WAIT]))  # the task has not run yet

        assert runs.subscribe("bob", convo.id, snapshot=True) is None
        assert runs.stop("bob", convo.id) is False
        assert runs.running("bob") == set()
        assert runs.running("alice") == {convo.id}
        with pytest.raises(Busy):
            runs.reserve(convo.id)

        runs.stop("alice", convo.id)
        assert (await drain(events))[-1][1]["message"]["status"] == "stopped"

    asyncio.run(body())


def test_sse_frames_one_event():
    assert sse("content", {"text": "é"}) == 'event: content\ndata: {"text": "\\u00e9"}\n\n'
