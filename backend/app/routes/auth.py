"""Auth-related API routes.

Most authentication is handled directly by Firebase on the frontend.
The backend only needs to verify tokens (via the dependency) and return
user-context info.
"""
from datetime import datetime, timezone

from fastapi import APIRouter

from app.auth.dependencies import CurrentUser
from app.services.firestore_service import upsert_user_profile

router = APIRouter()


@router.get("/me")
async def me(user: CurrentUser):
    """Return the authenticated user's profile and ensure their Firestore
    document exists. Called by the frontend right after login."""
    upsert_user_profile(
        uid=user.uid,
        email=user.email,
        last_login_at=datetime.now(timezone.utc),
    )
    return {
        "uid": user.uid,
        "email": user.email,
        "name": user.name,
        "email_verified": user.email_verified,
    }
