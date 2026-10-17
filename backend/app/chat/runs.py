from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractContextManager, aclosing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime

from app.chat.llm import LlmClient, LlmError
from app.core.db import session_factory
from app.repositories.chat import ChatRepository

log = logging.getLogger(__name__)

Store = Callable[[], AbstractContextManager[ChatRepository]]
Payload = tuple[str, dict]

_TITLE_PROMPT = (
    "Write a title of 6 words or fewer for a conversation that starts with the "
    "message below. Answer with the title only, without quotes.\n\n{text}"
)


class Busy(Exception):
    """A reply is already reserved or running for that conversation."""


@contextmanager
def default_store() -> Iterator[ChatRepository]:
    # The run outlives the request, so it opens its own sessions.
    with session_factory()() as session:
        yield ChatRepository(session)


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@dataclass
class _Run:
    owner_sub: str
    task: asyncio.Task | None = None
    queues: set[asyncio.Queue[Payload]] = field(default_factory=set)
    content: str = ""
    reasoning: str = ""
    stopped: bool = False
    # Only the stream and the title are cancelled: a cancel before the task's first
    # step would skip every handler, and one during the final save would lose it.
    cancellable: bool = False

    def publish(self, event: str, data: dict) -> None:
        for queue in self.queues:
            queue.put_nowait((event, data))


class Runs:
    """conversation id -> its reply run. `None` marks a reservation not yet started.
    Single event loop, so the sync methods are atomic."""

    def __init__(
        self, store: Store = default_store, flush_seconds: float = 2.0, title_timeout: float = 60.0
    ) -> None:
        self.store = store
        self.flush_seconds = flush_seconds
        self.title_timeout = title_timeout
        self._runs: dict[uuid.UUID, _Run | None] = {}

    def reserve(self, conversation_id: uuid.UUID) -> None:
        if conversation_id in self._runs:
            raise Busy(conversation_id)
        self._runs[conversation_id] = None

    def release(self, conversation_id: uuid.UUID) -> None:
        if conversation_id in self._runs and self._runs[conversation_id] is None:
            del self._runs[conversation_id]

    def start(
        self,
        *,
        owner_sub: str,
        conversation_id: uuid.UUID,
        message_id: uuid.UUID,
        created_at: datetime,
        llm: LlmClient,
        model: str,
        context: list[dict],
        title_from: str | None,
    ) -> None:
        run = self._runs[conversation_id] = _Run(owner_sub)
        run.task = asyncio.create_task(
            self._run(run, conversation_id, message_id, created_at, llm, model, context, title_from)
        )

    def running(self, owner_sub: str) -> set[uuid.UUID]:
        return {cid for cid, run in self._runs.items() if run and run.owner_sub == owner_sub}

    def _get(self, owner_sub: str, conversation_id: uuid.UUID) -> _Run | None:
        run = self._runs.get(conversation_id)
        return run if run and run.owner_sub == owner_sub else None

    def subscribe(
        self, owner_sub: str, conversation_id: uuid.UUID, *, snapshot: bool
    ) -> AsyncIterator[Payload] | None:
        run = self._get(owner_sub, conversation_id)
        if run is None:
            return None
        # Registered now, not on first iteration, so no event falls in between.
        queue: asyncio.Queue[Payload] = asyncio.Queue()
        run.queues.add(queue)
        first = ("snapshot", {"reasoning": run.reasoning, "content": run.content})

        async def events() -> AsyncIterator[Payload]:
            try:
                if snapshot:
                    yield first
                while True:
                    event = await queue.get()
                    yield event
                    if event[0] == "done":
                        return
            finally:
                run.queues.discard(queue)

        return events()

    def stop(self, owner_sub: str, conversation_id: uuid.UUID) -> bool:
        run = self._get(owner_sub, conversation_id)
        if run is None:
            return False
        if not run.stopped:
            run.stopped = True
            if run.cancellable and run.task:
                run.task.cancel()
        return True

    async def _db(self, method: str, *args, **kwargs) -> None:
        def call() -> None:
            with self.store() as repo:
                getattr(repo, method)(*args, **kwargs)

        await asyncio.to_thread(call)

    async def _run(
        self,
        run: _Run,
        conversation_id: uuid.UUID,
        message_id: uuid.UUID,
        created_at: datetime,
        llm: LlmClient,
        model: str,
        context: list[dict],
        title_from: str | None,
    ) -> None:
        owner = run.owner_sub
        status, error, usage = "stopped", None, {}
        if not run.stopped:
            run.cancellable = True
            try:
                usage = await self._stream(run, message_id, llm, model, context)
                status = "complete"
            except asyncio.CancelledError:
                asyncio.current_task().uncancel()  # swallowed on purpose: stop() asked
            except LlmError as exc:
                status, error = "error", str(exc)
            except Exception:
                log.exception("chat run failed")
                status, error = "error", "Something went wrong"
            finally:
                run.cancellable = False
        if error:
            run.publish("error", {"message": error})

        message = {
            "id": str(message_id),
            "role": "assistant",
            "content": run.content,
            "reasoning": run.reasoning or None,
            "model": model,
            "status": status,
            "error": error,
            "created_at": created_at.isoformat(),
        }
        try:
            await self._db(
                "update_message",
                owner,
                message_id,
                content=run.content,
                reasoning=run.reasoning or None,
                status=status,
                error=error,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
            )
        except Exception:
            # The row stays `streaming` until the startup sweep marks it `stopped`.
            log.exception("chat run final save failed")
        try:
            if status == "complete" and title_from and not run.stopped:
                run.cancellable = True
                try:
                    await self._title(run, conversation_id, llm, model, title_from)
                except asyncio.CancelledError:
                    # stop() during the title: keep the fallback. A rename already in
                    # its thread may still land, unpublished; the next list shows it.
                    asyncio.current_task().uncancel()
                finally:
                    run.cancellable = False
        finally:
            # No await between these two: a subscriber either gets `done` or finds no run.
            run.publish("done", {"message": message})
            del self._runs[conversation_id]

    async def _stream(
        self, run: _Run, message_id: uuid.UUID, llm: LlmClient, model: str, context: list[dict]
    ) -> dict:
        """Publishes deltas and flushes the text so far; returns the usage."""
        usage: dict = {}
        flushed = time.monotonic()
        async with aclosing(llm.stream(model, context)) as stream:
            async for kind, *rest in stream:
                if kind == "progress":
                    run.publish(kind, {"processed": rest[0], "total": rest[1]})
                elif kind == "usage":
                    usage = rest[0]
                else:
                    setattr(run, kind, getattr(run, kind) + rest[0])
                    run.publish(kind, {"text": rest[0]})
                    if time.monotonic() - flushed >= self.flush_seconds:
                        flushed = time.monotonic()
                        # Crash insurance only: the final save is authoritative. A flush
                        # cancelled by stop() may commit after it, harmlessly (same text).
                        try:
                            await self._db(
                                "update_message",
                                run.owner_sub,
                                message_id,
                                content=run.content,
                                reasoning=run.reasoning or None,
                            )
                        except Exception as exc:
                            log.warning("chat flush failed: %s", type(exc).__name__)
        return usage

    async def _title(
        self, run: _Run, conversation_id: uuid.UUID, llm: LlmClient, model: str, text: str
    ) -> None:
        prompt = [{"role": "user", "content": _TITLE_PROMPT.format(text=text)}]
        try:
            async with asyncio.timeout(self.title_timeout):
                answer = await llm.complete(model, prompt)
            lines = answer.strip().splitlines()
            title = lines[0].strip().strip("\"'").strip()[:80] if lines else ""
            if not title:
                return
            await self._db("rename", run.owner_sub, conversation_id, title)
        except Exception as exc:
            log.warning("chat title failed: %s", type(exc).__name__)
            return
        run.publish("title", {"title": title})


runs = Runs()


def chat_runs() -> Runs:
    return runs
