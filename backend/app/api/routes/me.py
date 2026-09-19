from fastapi import APIRouter
from pydantic import BaseModel

from app.core.auth import Claims

router = APIRouter(tags=["auth"])


class MeResponse(BaseModel):
    sub: str
    username: str | None
    email: str | None
    roles: list[str]


@router.get("/me", response_model=MeResponse)
def me(claims: Claims) -> MeResponse:
    return MeResponse(
        sub=claims["sub"],
        username=claims.get("preferred_username"),
        email=claims.get("email"),
        roles=claims.get("realm_access", {}).get("roles", []),
    )
