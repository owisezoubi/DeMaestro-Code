"""Application settings loaded from environment variables.

Pydantic-Settings reads from a .env file at the project root and
exposes a typed `settings` object that the rest of the app imports.
"""

import os
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
    firebase_service_account_b64: str = Field(default="")
    firebase_storage_bucket: str = Field(default="")

    # --- AI ---
    gemini_api_key: str = Field(default="")
    gemini_model: str = Field(default="gemini-2.5-pro")
    anthropic_api_key: str = Field(default="")
    claude_model: str = Field(default="claude-sonnet-4-6")
    mock_ai: bool = Field(default=False)

    # --- Deployment ---
    vercel_token: str = Field(default="")

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

# ── Per-agent model configuration ─────────────────────────────────────────────
# Verified against docs.anthropic.com/en/docs/about-claude/models on 2026-06-11:
#   claude-opus-4-8   — max output 128k tokens, $5/$25 per MTok in/out
#   claude-sonnet-4-6 — max output 64k tokens,  $3/$15 per MTok in/out
#
# Generator and Debugger use Opus 4.8 for stronger code gen / error reasoning.
# Architect / Modifier / Tester / Verifier stay on Sonnet 4.6 (cost).
# Override any agent at runtime — no code edits required:
#   export DEMAESTRO_MODEL_GENERATOR=claude-sonnet-4-6   # revert
#   export DEMAESTRO_MODEL_DEBUGGER=claude-sonnet-4-6
_AGENT_MODEL_DEFAULTS: dict[str, str] = {
    "architect": "claude-sonnet-4-6",
    "generator": "claude-opus-4-8",
    "debugger":  "claude-opus-4-8",
    "modifier":  "claude-sonnet-4-6",
    "tester":    "claude-sonnet-4-6",
    "verifier":  "claude-sonnet-4-6",
}

# Per-agent output token caps.
# Override: DEMAESTRO_MAX_TOKENS_<AGENT_NAME_UPPER>=<int>
_AGENT_MAX_TOKENS: dict[str, int] = {
    "architect": 16000,
    "generator": 16000,
    "debugger":  16000,
    "modifier":  16000,
    "tester":    8000,
    "verifier":  8000,
}


def get_agent_model(agent_name: str) -> str:
    """Return the Claude model string for a given agent.

    Override format: DEMAESTRO_MODEL_<AGENT_NAME_UPPER>
    Examples:
        DEMAESTRO_MODEL_GENERATOR=claude-opus-4-7
        DEMAESTRO_MODEL_DEBUGGER=claude-sonnet-4-6
    """
    env_key = f"DEMAESTRO_MODEL_{agent_name.upper()}"
    return (
        os.environ.get(env_key)
        or _AGENT_MODEL_DEFAULTS.get(agent_name)
        or settings.claude_model
    )


def get_agent_max_tokens(agent_name: str) -> int:
    """Return the max_tokens cap for a given agent's LLM calls.

    Override format: DEMAESTRO_MAX_TOKENS_<AGENT_NAME_UPPER>
    Example:
        DEMAESTRO_MAX_TOKENS_GENERATOR=64000
    """
    env_key = f"DEMAESTRO_MAX_TOKENS_{agent_name.upper()}"
    env_val = os.environ.get(env_key)
    if env_val:
        return int(env_val)
    return _AGENT_MAX_TOKENS.get(agent_name, 16000)


# ── Cost estimation ────────────────────────────────────────────────────────────
# Prices in USD per 1 million tokens. Verify against Anthropic's pricing page
# before relying on these for accounting — they change periodically.
# cache_write = 1.25× input rate; cache_read = 0.10× input rate.
PRICING_PER_MILLION_TOKENS: dict[str, dict[str, float]] = {
    "claude-haiku-4-5":          {"input": 1.00,  "output":  5.00,
                                  "cache_write": 1.25, "cache_read": 0.10},
    "claude-haiku-4-5-20251001": {"input": 1.00,  "output":  5.00,
                                  "cache_write": 1.25, "cache_read": 0.10},
    "claude-sonnet-4-6":         {"input": 3.00,  "output": 15.00,
                                  "cache_write": 3.75, "cache_read": 0.30},
    "claude-opus-4-7":           {"input": 5.00,  "output": 25.00,
                                  "cache_write": 6.25, "cache_read": 0.50},
    "claude-opus-4-8":           {"input": 5.00,  "output": 25.00,
                                  "cache_write": 6.25, "cache_read": 0.50},
}


def estimate_cost_usd(
    tokens_by_agent: dict[str, dict[str, int]],
    models_by_agent: dict[str, str],
) -> float:
    """Compute estimated generation cost from per-agent token counts.

    Expects usage dicts with keys: input_uncached, cache_write, cache_read, output.
    cache_write is billed at ~1.25× input; cache_read at ~0.10× input.
    """
    _fallback = PRICING_PER_MILLION_TOKENS["claude-sonnet-4-6"]
    total = 0.0
    for agent, usage in tokens_by_agent.items():
        model = models_by_agent.get(agent, "claude-sonnet-4-6")
        p = PRICING_PER_MILLION_TOKENS.get(model, _fallback)
        total += usage.get("input_uncached", 0) / 1_000_000 * p["input"]
        total += usage.get("cache_write",    0) / 1_000_000 * p["cache_write"]
        total += usage.get("cache_read",     0) / 1_000_000 * p["cache_read"]
        total += usage.get("output",         0) / 1_000_000 * p["output"]
    return round(total, 4)
