import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints
from starlette.concurrency import run_in_threadpool

from app.chat.llm import LlmClient, LlmError, llm_client
from app.chat.prompt import build_context, fallback_title
from app.chat.runs import Busy, Runs, chat_runs, sse
from app.core.auth import Claims
from app.core.clock import Now
from app.models.chat import Conversation, Message
from app.repositories.chat import ChatRepo, ChatRepository

router = APIRouter(prefix="/chat", tags=["chat"])

Llm = Annotated[LlmClient, Depends(llm_client)]
RunRegistry = Annotated[Runs, Depends(chat_runs)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

_DEFAULT_CONTEXT = 32768


class MessageOut(BaseModel):
    """Same keys as the `done` event's message."""

    id: uuid.UUID
    role: str
    content: str
    reasoning: str | None
    model: str | None
    status: str
    error: str | None
    created_at: datetime

    @classmethod
    def of(cls, row: Message) -> "MessageOut":
        return cls.model_validate(row, from_attributes=True)


class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: str
    updated_at: datetime
    streaming: bool


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    streaming: bool
    messages: list[MessageOut]


class ModelOut(BaseModel):
    id: str
    loaded: bool | None
    context_length: int


class ConversationCreate(BaseModel):
    title: Title = "New chat"


class ConversationPatch(BaseModel):
    title: Title


class SendBody(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    model: str
    replace_from: uuid.UUID | None = None


class RegenerateBody(BaseModel):
    model: str


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")


async def _owned(repo: ChatRepository, sub: str, conversation_id: str) -> Conversation:
    """The caller's live conversation, else 404. Every route that touches a run calls
    this first: the run registry itself is not owner-scoped."""
    try:
        cid = uuid.UUID(conversation_id)
    except ValueError:
        raise _not_found() from None
    row = await run_in_threadpool(repo.get, sub, cid)
    if row is None:
        raise _not_found()
    return row


@router.get("/models", response_model=list[ModelOut])
async def list_models(claims: Claims, llm: Llm) -> list[ModelOut]:
    try:
        models = await llm.models()
    except LlmError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    return [ModelOut(id=m.id, loaded=m.loaded, context_length=m.context_length) for m in models]


# The routes below that read `runs` are async: the registry belongs to the event loop.
@router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    claims: Claims, repo: ChatRepo, runs: RunRegistry, q: str | None = None
) -> list[ConversationSummary]:
    sub = claims["sub"]
    rows = await run_in_threadpool(repo.list, sub, q)
    running = runs.running(sub)
    return [
        ConversationSummary(
            id=c.id, title=c.title, updated_at=c.updated_at, streaming=c.id in running
        )
        for c in rows
    ]


@router.post("/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(
    claims: Claims, repo: ChatRepo, body: ConversationCreate
) -> ConversationOut:
    row = repo.create(claims["sub"], body.title)
    return ConversationOut(id=row.id, title=row.title, streaming=False, messages=[])


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    claims: Claims, repo: ChatRepo, runs: RunRegistry, conversation_id: str
) -> ConversationOut:
    sub = claims["sub"]
    row = await _owned(repo, sub, conversation_id)
    messages = await run_in_threadpool(repo.messages, sub, row.id)
    return ConversationOut(
        id=row.id,
        title=row.title,
        streaming=row.id in runs.running(sub),
        messages=[MessageOut.of(m) for m in messages],
    )


@router.patch("/conversations/{conversation_id}", response_model=ConversationSummary)
async def rename_conversation(
    claims: Claims, repo: ChatRepo, runs: RunRegistry, conversation_id: str, body: ConversationPatch
) -> ConversationSummary:
    sub = claims["sub"]
    row = await _owned(repo, sub, conversation_id)
    streaming = row.id in runs.running(sub)

    def rename() -> ConversationSummary:
        # In the thread too: reading `updated_at` back after the commit is a query.
        repo.rename(sub, row.id, body.title)
        return ConversationSummary(
            id=row.id, title=row.title, updated_at=row.updated_at, streaming=streaming
        )

    return await run_in_threadpool(rename)


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    claims: Claims, repo: ChatRepo, runs: RunRegistry, conversation_id: str
) -> Response:
    sub = claims["sub"]
    row = await _owned(repo, sub, conversation_id)
    runs.stop(sub, row.id)
    await run_in_threadpool(repo.soft_delete, sub, row.id)
    return Response(status_code=204)


async def _reply(
    sub: str,
    convo: Conversation,
    repo: ChatRepository,
    runs: Runs,
    llm: LlmClient,
    model: str,
    now: datetime,
    prepare: Callable[[list[Message]], tuple[MessageOut | None, str | None]],
) -> StreamingResponse:
    """Reserve the conversation, let `prepare(history)` write the user side (it returns
    the user message and the title source), add the streaming assistant row, start the
    run and stream it. Any failure before the run starts releases the reservation."""
    try:
        runs.reserve(convo.id)
    except Busy:
        raise HTTPException(status.HTTP_409_CONFLICT, "A reply is already running") from None
    try:
        history = await run_in_threadpool(repo.messages, sub, convo.id)
        user_message, title_from = await run_in_threadpool(prepare, list(history))
        history = await run_in_threadpool(repo.messages, sub, convo.id)
        assistant = await run_in_threadpool(
            repo.add_message,
            sub,
            convo.id,
            role="assistant",
            content="",
            status="streaming",
            model=model,
        )
        try:
            listed = {m.id: m.context_length for m in await llm.models()}
        except LlmError:
            listed = {}  # the run itself will report the model being unreachable
        context = build_context(history, listed.get(model, _DEFAULT_CONTEXT), now)
        runs.start(
            owner_sub=sub,
            conversation_id=convo.id,
            message_id=assistant.id,
            created_at=assistant.created_at,
            llm=llm,
            model=model,
            context=context,
            title_from=title_from,
        )
    except BaseException:
        runs.release(convo.id)
        raise
    # No await since start(): the subscriber cannot miss an event.
    events = runs.subscribe(sub, convo.id, snapshot=False)
    start = {
        "conversation": {"id": str(convo.id), "title": convo.title},
        "user_message": user_message and user_message.model_dump(mode="json"),
        "assistant_message_id": str(assistant.id),
    }
    return _event_stream(events, sse("start", start))


def _event_stream(events: AsyncIterator[tuple[str, dict]], first: str = "") -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        # A client leaving only unsubscribes (aclosing); the run keeps going.
        async with aclosing(events):
            if first:
                yield first
            async for event, data in events:
                yield sse(event, data)

    # X-Accel-Buffering: nginx would otherwise hold the deltas back.
    headers = {"X-Accel-Buffering": "no"}
    return StreamingResponse(body(), media_type="text/event-stream", headers=headers)


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    claims: Claims,
    repo: ChatRepo,
    runs: RunRegistry,
    llm: Llm,
    now: Now,
    conversation_id: str,
    body: SendBody,
) -> StreamingResponse:
    sub = claims["sub"]
    if conversation_id == "new":
        if body.replace_from is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
        convo = await run_in_threadpool(repo.create, sub, fallback_title(body.content))
    else:
        convo = await _owned(repo, sub, conversation_id)

    def prepare(history: list[Message]) -> tuple[MessageOut, str | None]:
        title_from = None if history else body.content
        if body.replace_from is None:
            row = repo.add_message(sub, convo.id, role="user", content=body.content)
            return MessageOut.of(row), title_from
        edited = next((m for m in history if m.id == body.replace_from and m.role == "user"), None)
        if edited is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
        out = MessageOut.of(edited).model_copy(update={"content": body.content})
        repo.update_message(sub, edited.id, content=body.content)
        # The session's identity map would otherwise hand the old text to the prompt.
        edited.content = body.content
        repo.truncate_after(sub, convo.id, edited.id)
        return out, title_from

    return await _reply(sub, convo, repo, runs, llm, body.model, now, prepare)


@router.post("/conversations/{conversation_id}/regenerate")
async def regenerate(
    claims: Claims,
    repo: ChatRepo,
    runs: RunRegistry,
    llm: Llm,
    now: Now,
    conversation_id: str,
    body: RegenerateBody,
) -> StreamingResponse:
    sub = claims["sub"]
    convo = await _owned(repo, sub, conversation_id)

    def prepare(history: list[Message]) -> tuple[None, None]:
        if history and history[-1].role == "assistant":
            repo.delete_message(sub, history.pop().id)
        if not any(m.role == "user" for m in history):
            raise HTTPException(status.HTTP_409_CONFLICT, "Nothing to regenerate")
        return None, None

    return await _reply(sub, convo, repo, runs, llm, body.model, now, prepare)


@router.get("/conversations/{conversation_id}/stream")
async def reattach(
    claims: Claims, repo: ChatRepo, runs: RunRegistry, conversation_id: str
) -> Response:
    sub = claims["sub"]
    row = await _owned(repo, sub, conversation_id)
    events = runs.subscribe(sub, row.id, snapshot=True)
    if events is None:
        return Response(status_code=204)
    return _event_stream(events)


@router.post("/conversations/{conversation_id}/stop", status_code=204)
async def stop(claims: Claims, repo: ChatRepo, runs: RunRegistry, conversation_id: str) -> Response:
    sub = claims["sub"]
    row = await _owned(repo, sub, conversation_id)
    runs.stop(sub, row.id)
    return Response(status_code=204)
