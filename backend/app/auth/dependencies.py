"""FastAPI dependency that verifies a Firebase ID token from the request."""
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.auth.firebase_admin import verify_id_token
from app.models.user import AuthUser


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> AuthUser:
    """Extract and verify the Firebase ID token from the Authorization header.

    Frontend should send:  Authorization: Bearer <firebase-id-token>
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.removeprefix("Bearer ").strip()
    try:
        claims = verify_id_token(token)
    except Exception as exc:  # firebase raises various exception classes
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
