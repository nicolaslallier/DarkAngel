import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.core.auth import Claims
from app.repositories.households import HouseholdRepo


@dataclass(frozen=True)
class HouseholdContext:
    sub: str
    household_id: uuid.UUID
    role: str


def household_context(claims: Claims, repo: HouseholdRepo) -> HouseholdContext:
    """The caller's household and role, or 409 `no_household` so the SPA can
    offer to create or join one."""
    member = repo.membership(claims["sub"])
    if member is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no_household")
    return HouseholdContext(member.sub, member.household_id, member.role)


Reader = Annotated[HouseholdContext, Depends(household_context)]


def _writer(ctx: Reader) -> HouseholdContext:
    if ctx.role == "viewer":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Read-only access")
    return ctx


def _owner(ctx: Reader) -> HouseholdContext:
    if ctx.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can do this")
    return ctx


Writer = Annotated[HouseholdContext, Depends(_writer)]
Owner = Annotated[HouseholdContext, Depends(_owner)]
