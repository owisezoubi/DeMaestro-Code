"""Generated models. User is imported from auth_models (scaffold)."""
# User is provided by the scaffold and must NOT be redefined here.
from app.auth_models import User  # noqa: F401 -- re-exported for `from app.models import User`

__all__ = ["User"]

# LLM appends other models below this line.
# Each model must import Base from app.database and define its own
# __tablename__. Foreign keys to users use String (UUID stored as str):
#   user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), ...)
