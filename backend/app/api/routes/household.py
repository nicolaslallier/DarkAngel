from typing import Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.auth import Claims
from app.core.household import Owner, Reader
from app.repositories.households import (
    INVITATION_TTL,
    AlreadyMember,
    HouseholdRepo,
    InvitationInvalid,
)

router = APIRouter(prefix="/household", tags=["household"])


class MemberInfo(BaseModel):
    sub: str
    role: str
    display_name: str | None = None


class HouseholdInfo(BaseModel):
    id: str
    name: str
    role: str
    members: list[MemberInfo]


class HouseholdCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)


class InvitationCreate(BaseModel):
    role: Literal["member", "viewer"] = "member"


class InvitationInfo(BaseModel):
    token: str
    expires_in_days: int


class JoinBody(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class RolePatch(BaseModel):
    role: Literal["member", "viewer"]


def _member(m) -> MemberInfo:
    return MemberInfo(sub=m.sub, role=m.role, display_name=m.display_name)


def _display_name(claims) -> str | None:
    return claims.get("preferred_username") or claims.get("email") or None


def _info(repo: HouseholdRepo, household_id, role: str) -> HouseholdInfo:
    household = repo.get(household_id)
    return HouseholdInfo(
        id=str(household.id),
        name=household.name,
        role=role,
        members=[_member(m) for m in repo.members(household_id)],
    )


@router.get("", response_model=HouseholdInfo)
def get_household(ctx: Reader, repo: HouseholdRepo) -> HouseholdInfo:
    return _info(repo, ctx.household_id, ctx.role)


@router.post("", response_model=HouseholdInfo, status_code=status.HTTP_201_CREATED)
def create_household(claims: Claims, repo: HouseholdRepo, body: HouseholdCreate) -> HouseholdInfo:
    try:
        household = repo.create(claims["sub"], body.name, _display_name(claims))
    except AlreadyMember as e:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already in a household") from e
    return _info(repo, household.id, "owner")


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_household(ctx: Owner, repo: HouseholdRepo) -> Response:
    repo.delete(ctx.household_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/invitations", response_model=InvitationInfo, status_code=status.HTTP_201_CREATED)
def create_invitation(ctx: Owner, repo: HouseholdRepo, body: InvitationCreate) -> InvitationInfo:
    token = repo.invite(ctx.household_id, body.role, ctx.sub)
    return InvitationInfo(token=token, expires_in_days=INVITATION_TTL.days)


@router.post("/join", response_model=HouseholdInfo)
def join_household(claims: Claims, repo: HouseholdRepo, body: JoinBody) -> HouseholdInfo:
    try:
        member = repo.redeem(body.token, claims["sub"], _display_name(claims))
    except InvitationInvalid as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation invalid or expired") from e
    except AlreadyMember as e:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already in a household") from e
    return _info(repo, member.household_id, member.role)


@router.patch("/members/{sub}", response_model=MemberInfo)
def set_member_role(ctx: Owner, repo: HouseholdRepo, sub: str, body: RolePatch) -> MemberInfo:
    target = repo.membership(sub)
    if target is None or target.household_id != ctx.household_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such member")
    if target.role == "owner":
        raise HTTPException(status.HTTP_409_CONFLICT, "The owner's role cannot change")
    member = repo.set_role(ctx.household_id, sub, body.role)
    if member is None:  # left or moved between the check and the write
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such member")
    return _member(member)


@router.delete("/members/{sub}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(ctx: Owner, repo: HouseholdRepo, sub: str) -> Response:
    target = repo.membership(sub)
    if target is None or target.household_id != ctx.household_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such member")
    if target.role == "owner":
        raise HTTPException(status.HTTP_409_CONFLICT, "Delete the household instead")
    repo.remove(ctx.household_id, sub)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/leave", status_code=status.HTTP_204_NO_CONTENT)
def leave_household(ctx: Reader, repo: HouseholdRepo) -> Response:
    if ctx.role == "owner":
        raise HTTPException(status.HTTP_409_CONFLICT, "The owner must delete the household")
    repo.remove(ctx.household_id, ctx.sub)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
