"""User-related Pydantic models."""
from typing import Optional

from pydantic import BaseModel, EmailStr


class AuthUser(BaseModel):
    """Authenticated user as returned by the get_current_user dependency."""
    uid: str
    email: Optional[EmailStr] = None
    email_verified: bool = False
    name: Optional[str] = None


class UserProfile(BaseModel):
    """User profile document stored in Firestore at users/{uid}."""
    uid: str
    email: Optional[EmailStr] = None
    display_name: Optional[str] = None
    created_at: Optional[str] = None
    last_login_at: Optional[str] = None
