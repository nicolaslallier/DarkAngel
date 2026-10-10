from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import Db
from app.models.households import Household, HouseholdInvitation, HouseholdMember

INVITATION_TTL = timedelta(days=7)


class AlreadyMember(Exception):
    """The person already belongs to a household (`household_members.sub` is
    the primary key, so the database decides, not a SELECT first)."""


class InvitationInvalid(Exception):
    """Unknown token, expired, or already used: indistinguishable on purpose."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class HouseholdRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def membership(self, sub: str) -> HouseholdMember | None:
        return self.db.get(HouseholdMember, sub)

    def get(self, household_id: uuid.UUID) -> Household | None:
        return self.db.get(Household, household_id)

    def members(self, household_id: uuid.UUID) -> Sequence[HouseholdMember]:
        statement = (
            select(HouseholdMember)
            .where(HouseholdMember.household_id == household_id)
            .order_by(HouseholdMember.joined_at, HouseholdMember.sub)
        )
        return self.db.scalars(statement).all()

    def create(self, sub: str, name: str) -> Household:
        household = Household(id=uuid.uuid4(), name=name)
        self.db.add(household)
        self.db.add(HouseholdMember(sub=sub, household_id=household.id, role="owner"))
        try:
            self.db.commit()
        except IntegrityError as e:
            self.db.rollback()
            raise AlreadyMember from e
        return household

    def set_role(self, household_id: uuid.UUID, sub: str, role: str) -> HouseholdMember | None:
        member = self.db.get(HouseholdMember, sub)
        if member is None or member.household_id != household_id:
            return None
        member.role = role
        self.db.commit()
        return member

    def remove(self, household_id: uuid.UUID, sub: str) -> bool:
        member = self.db.get(HouseholdMember, sub)
        if member is None or member.household_id != household_id:
            return False
        self.db.delete(member)
        self.db.commit()
        return True

    def delete(self, household_id: uuid.UUID) -> None:
        # Members, invitations and every ledger table go with it (ON DELETE CASCADE).
        self.db.execute(delete(Household).where(Household.id == household_id))
        self.db.commit()

    def invite(
        self,
        household_id: uuid.UUID,
        role: str,
        created_by: str,
        ttl: timedelta = INVITATION_TTL,
    ) -> str:
        token = secrets.token_urlsafe(32)
        self.db.add(
            HouseholdInvitation(
                id=uuid.uuid4(),
                household_id=household_id,
                role=role,
                token_hash=_hash(token),
                expires_at=datetime.now(UTC) + ttl,
                created_by=created_by,
            )
        )
        self.db.commit()
        return token

    def redeem(self, token: str, sub: str) -> HouseholdMember:
        """Claim the link and join, in one transaction: if the person is already
        in a household the claim is rolled back and the link stays usable."""
        claimed = self.db.execute(
            update(HouseholdInvitation)
            .where(
                HouseholdInvitation.token_hash == _hash(token),
                HouseholdInvitation.used_at.is_(None),
                HouseholdInvitation.expires_at > func.now(),
            )
            .values(used_at=func.now())
            .returning(HouseholdInvitation.household_id, HouseholdInvitation.role)
        ).one_or_none()
        if claimed is None:
            self.db.rollback()
            raise InvitationInvalid
        member = HouseholdMember(sub=sub, household_id=claimed.household_id, role=claimed.role)
        self.db.add(member)
        try:
            self.db.commit()
        except IntegrityError as e:
            self.db.rollback()
            raise AlreadyMember from e
        return member


def household_repository(db: Db) -> HouseholdRepository:
    return HouseholdRepository(db)


HouseholdRepo = Annotated[HouseholdRepository, Depends(household_repository)]
