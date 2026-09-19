from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

bearer = HTTPBearer(auto_error=False)


@lru_cache
def jwks_client() -> jwt.PyJWKClient:
    settings = get_settings()
    url = settings.auth_jwks_url or f"{settings.auth_issuer}/protocol/openid-connect/certs"
    # Caches the key set and refetches it when a token names an unknown `kid`, so
    # a Keycloak key rotation is picked up without a restart.
    return jwt.PyJWKClient(url, timeout=5)


def current_claims(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict[str, Any]:
    """Validate the Keycloak access token and return its claims (401 otherwise).

    Sync on purpose: FastAPI runs it in the threadpool, so the blocking JWKS
    fetch never stalls the event loop.
    """
    if credentials is None:
        raise _unauthorized("Not authenticated")

    settings = get_settings()
    try:
        key = jwks_client().get_signing_key_from_jwt(credentials.credentials).key
        return jwt.decode(
            credentials.credentials,
            key,
            algorithms=["RS256"],
            audience=settings.auth_audience,
            issuer=settings.auth_issuer,
            leeway=30,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as e:
        # PyJWKClientError (Keycloak unreachable, unknown kid) is a PyJWTError too.
        raise _unauthorized(f"Invalid token: {e}") from e


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"}
    )


Claims = Annotated[dict[str, Any], Depends(current_claims)]
