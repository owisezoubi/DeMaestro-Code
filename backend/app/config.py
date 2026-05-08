"""Application settings loaded from environment variables.

Pydantic-Settings reads from a .env file at the project root and
exposes a typed `settings` object that the rest of the app imports.
"""
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---
    env: str = Field(default="development")
    log_level: str = Field(default="INFO")
    cors_origins: str = Field(default="http://localhost:5173")

    # --- Firebase ---
    firebase_service_account_path: str = Field(
        default="secrets/firebase-service-account.json"
    )

    # --- AI ---
    gemini_api_key: str = Field(default="")
    gemini_model: str = Field(default="gemini-2.5-pro")
    anthropic_api_key: str = Field(default="")
    claude_model: str = Field(default="claude-sonnet-4-6")
    mock_ai: bool = Field(default=False)

    # --- Observability ---
    sentry_dsn: str = Field(default="")

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def firebase_service_account_full_path(self) -> Path:
        return Path(self.firebase_service_account_path).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached singleton accessor."""
    return Settings()


settings = get_settings()
