from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import Select, delete, func, or_, select, tuple_, update
from sqlalchemy.orm import Session

from app.core.db import Db
from app.models.chat import Conversation, Message


class ChatRepository:
    """Owner-scoped chat queries. `owner_sub` comes first, like the other
    repositories; messages are scoped by joining their conversation."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def list(self, owner_sub: str, q: str | None = None) -> Sequence[Conversation]:
        statement = select(Conversation).where(
            Conversation.owner_sub == owner_sub, Conversation.deleted_at.is_(None)
        )
        if q:
            in_messages = (
                select(Message.id)
                .where(
                    Message.conversation_id == Conversation.id,
                    Message.content.icontains(q, autoescape=True),
                )
                .exists()
            )
            statement = statement.where(
                or_(Conversation.title.icontains(q, autoescape=True), in_messages)
            )
        statement = statement.order_by(Conversation.updated_at.desc(), Conversation.id)
        return self.db.scalars(statement).all()

    def get(self, owner_sub: str, conversation_id: uuid.UUID) -> Conversation | None:
        statement = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.owner_sub == owner_sub,
            Conversation.deleted_at.is_(None),
        )
        return self.db.scalars(statement).one_or_none()

    def create(self, owner_sub: str, title: str) -> Conversation:
        row = Conversation(id=uuid.uuid4(), owner_sub=owner_sub, title=title)
        self.db.add(row)
        self.db.commit()
        return row

    def rename(self, owner_sub: str, conversation_id: uuid.UUID, title: str) -> Conversation | None:
        row = self.get(owner_sub, conversation_id)
        if row is not None:
            row.title = title
            self.db.commit()
        return row

    def soft_delete(self, owner_sub: str, conversation_id: uuid.UUID) -> bool:
        row = self.get(owner_sub, conversation_id)
        if row is None:
            return False
        row.deleted_at = datetime.now(UTC)
        self.db.commit()
        return True

    def messages(self, owner_sub: str, conversation_id: uuid.UUID) -> Sequence[Message]:
        statement = (
            select(Message)
            .where(Message.conversation_id.in_(self._owned(owner_sub, conversation_id)))
            .order_by(Message.created_at, Message.id)
        )
        return self.db.scalars(statement).all()

    def add_message(
        self,
        owner_sub: str,
        conversation_id: uuid.UUID,
        *,
        role: str,
        content: str,
        status: str = "complete",
        model: str | None = None,
    ) -> Message:
        """Raises LookupError, inserting nothing, unless the conversation is the
        caller's and live."""
        if self.get(owner_sub, conversation_id) is None:
            raise LookupError(conversation_id)
        row = Message(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            role=role,
            content=content,
            status=status,
            model=model,
        )
        self.db.add(row)
        self.db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id, Conversation.owner_sub == owner_sub)
            .values(updated_at=func.clock_timestamp())
        )
        self.db.commit()
        return row

    def update_message(self, owner_sub: str, message_id: uuid.UUID, **fields: Any) -> None:
        self.db.execute(
            update(Message)
            .where(Message.id == message_id, Message.conversation_id.in_(self._owned(owner_sub)))
            .values(**fields)
        )
        self.db.commit()

    def truncate_after(
        self, owner_sub: str, conversation_id: uuid.UUID, message_id: uuid.UUID
    ) -> None:
        """Hard-delete every message created strictly after `message_id`."""
        pivot = select(Message.created_at, Message.id).where(
            Message.id == message_id,
            Message.conversation_id.in_(self._owned(owner_sub, conversation_id)),
        )
        found = self.db.execute(pivot).one_or_none()
        if found is None:
            return
        pivot_at, pivot_id = found
        self.db.execute(
            delete(Message).where(
                Message.conversation_id.in_(self._owned(owner_sub, conversation_id)),
                tuple_(Message.created_at, Message.id) > tuple_(pivot_at, pivot_id),
            )
        )
        self.db.commit()

    def delete_message(self, owner_sub: str, message_id: uuid.UUID) -> None:
        self.db.execute(
            delete(Message).where(
                Message.id == message_id, Message.conversation_id.in_(self._owned(owner_sub))
            )
        )
        self.db.commit()

    def stop_streaming(self) -> int:
        """Startup only, not owner-scoped: a reply cannot still be streaming
        in a process that just booted."""
        result = self.db.execute(
            update(Message).where(Message.status == "streaming").values(status="stopped")
        )
        self.db.commit()
        return result.rowcount

    @staticmethod
    def _owned(owner_sub: str, conversation_id: uuid.UUID | None = None) -> Select:
        statement = select(Conversation.id).where(Conversation.owner_sub == owner_sub)
        if conversation_id is not None:
            statement = statement.where(Conversation.id == conversation_id)
        return statement


def chat_repository(db: Db) -> ChatRepository:
    return ChatRepository(db)


ChatRepo = Annotated[ChatRepository, Depends(chat_repository)]
