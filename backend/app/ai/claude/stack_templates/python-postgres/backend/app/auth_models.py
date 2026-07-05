"""User model -- shipped as scaffold. Standardized across all projects so
auth.py and auth_routes.py can rely on its shape. The LLM may add OTHER
models in models.py but must NOT redefine User there.

IMPORTANT: User.id is plain String (VARCHAR on Postgres, TEXT on SQLite)
populated with UUID strings. We deliberately do NOT use the Postgres native
UUID type, because the generator emits `Column(String, ForeignKey("users.id"))`
in dependent models — and a UUID-vs-VARCHAR FK mismatch is rejected by
Postgres ("incompatible types: character varying and uuid"). Keeping the
column type as String keeps every generated FK valid on both engines.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime

from app.database import Base


class User(Base):
    __tablename__ = "users"
    # String (VARCHAR) — NOT UUID. Stores uuid4 hex strings. See module docstring.
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    name = Column(String, nullable=True)
    role = Column(String, nullable=False, default="user")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
