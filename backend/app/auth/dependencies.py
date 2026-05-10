"""FastAPI dependency that verifies a Firebase ID token from the request."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.firebase_admin import verify_id_token
from app.models.user import AuthUser

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    creds: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
) -> AuthUser:
    """Extract and verify the Firebase ID token from the Authorization header."""
    if not creds or creds.scheme.lower() != "bearer" or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = verify_id_token(creds.credentials)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Firebase token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return AuthUser(
        uid=claims["uid"],
        email=claims.get("email"),
        email_verified=claims.get("email_verified", False),
        name=claims.get("name"),
    )


CurrentUser = Annotated[AuthUser, Depends(get_current_user)]
