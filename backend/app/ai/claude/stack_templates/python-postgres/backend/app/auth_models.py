"""User model -- shipped as scaffold. Standardized across all projects so
auth.py and auth_routes.py can rely on its shape. The LLM may add OTHER
models in models.py but must NOT redefine User there.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.types import TypeDecorator, CHAR

from app.database import Base


class GUID(TypeDecorator):
    """Platform-independent UUID. Uses Postgres UUID where available,
    falls back to CHAR(36) on SQLite."""
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=False))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(value) if isinstance(value, uuid.UUID) else str(value)

    def process_result_value(self, value, dialect):
        return None if value is None else str(value)


class User(Base):
    __tablename__ = "users"
    id = Column(GUID(), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    name = Column(String, nullable=True)
    role = Column(String, nullable=False, default="user")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
