"""Auth routes. Shipped as static scaffold — not LLM-generated.
Standardized across all projects.

Routes exposed (all mounted under /api by main.py):
  POST /api/auth/register  — create account, return JWT
  POST /api/auth/login     — verify credentials, return JWT
  GET  /api/auth/me        — return current user profile
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field, AliasChoices
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr = Field(
        validation_alias=AliasChoices("email", "username"),
    )
    password: str
    name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr = Field(
        validation_alias=AliasChoices("email", "username"),
    )
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int | str
    email: EmailStr
    name: str | None = None
    role: str = "user"

    class Config:
        from_attributes = True


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    try:
        existing = db.query(User).filter(User.email == payload.email).first()
    except Exception as e:
        import structlog as _sl
        _sl.get_logger().warning("register.db_query_failed", error=f"{type(e).__name__}: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable — please retry",
        )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )
    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        name=payload.name or payload.email.split("@")[0],
        role="user",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenResponse(access_token=create_access_token(user.id))


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    try:
        user = db.query(User).filter(User.email == payload.email).first()
    except Exception as e:
        import structlog as _sl
        _sl.get_logger().warning("login.db_query_failed", error=f"{type(e).__name__}: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    try:
        valid = verify_password(payload.password, user.password_hash)
    except Exception:
        valid = False
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    return current_user
