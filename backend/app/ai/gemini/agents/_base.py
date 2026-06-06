"""Abstract base class for all Gemini-backed agents."""
import json
import time
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path
from typing import Type, TypeVar

import google.generativeai as genai
import structlog
from pydantic import BaseModel

from app.config import settings

_PROMPT_DIR = Path(__file__).parent.parent / "prompts"

T = TypeVar("T", bound=BaseModel)


@lru_cache(maxsize=16)
def _load_prompt(name: str) -> str:
    path = _PROMPT_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


class GeminiAgent(ABC):
    agent_name: str = "GeminiAgent"
    prompt_name: str = ""
    model: str = ""
    temperature: float = 0.2

    def __init__(self) -> None:
        self._state: dict = {}
        self.log = structlog.get_logger(self.agent_name)

    @property
    def state(self) -> dict:
        return dict(self._state)

    def _set_state(self, **kwargs) -> None:
        self._state.update(kwargs)

    def _model_name(self) -> str:
        return self.model or settings.gemini_model

    def is_mock(self) -> bool:
        return settings.mock_ai

    def _load_prompt(self) -> str:
        if not self.prompt_name:
            raise ValueError(f"{self.__class__.__name__} must set prompt_name")
        return _load_prompt(self.prompt_name)

    def _call_gemini(
        self,
        system_prompt: str,
        user_content: str,
        *,
        response_model: Type[T],
        stage: str = "default",
    ) -> T:
        genai.configure(api_key=settings.gemini_api_key)
        model_name = self._model_name()
        gemini_model = genai.GenerativeModel(
            model_name=model_name,
            generation_config=genai.types.GenerationConfig(
                temperature=self.temperature,
                response_mime_type="application/json",
                max_output_tokens=32768,
            ),
            system_instruction=system_prompt,
        )
        start = time.time()
        try:
            response = gemini_model.generate_content(user_content)
            duration_ms = round((time.time() - start) * 1000)

            usage = getattr(response, "usage_metadata", None)
            tokens_in = getattr(usage, "prompt_token_count", None) if usage else None
            tokens_out = getattr(usage, "candidates_token_count", None) if usage else None

            self.log.info(
                "gemini.call",
                agent=self.agent_name,
                stage=stage,
                model=model_name,
                prompt_chars=len(system_prompt),
                response_chars=len(response.text),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                duration_ms=duration_ms,
            )

            finish_reason = None
            try:
                finish_reason = response.candidates[0].finish_reason.name
            except Exception:
                pass
            if finish_reason == "MAX_TOKENS":
                self.log.warning(
                    "gemini.truncated_at_max_tokens",
                    agent=self.agent_name,
                    stage=stage,
                    response_chars=len(response.text),
                )

            try:
                raw = json.loads(response.text)
            except json.JSONDecodeError:
                self.log.error(
                    "gemini.json_parse_error",
                    agent=self.agent_name,
                    stage=stage,
                    raw_preview=response.text[:500],
                )
                raise ValueError(f"Gemini returned non-JSON: {response.text[:200]}")

            try:
                return response_model.model_validate(raw)
            except Exception as exc:
                self.log.error(
                    "gemini.schema_error",
                    agent=self.agent_name,
                    stage=stage,
                    raw_preview=str(raw)[:500],
                    error=str(exc),
                )
                raise ValueError(f"Gemini returned invalid schema: {exc}") from exc

        except ValueError:
            raise
        except Exception as exc:
            self.log.error(
                "gemini.error",
                agent=self.agent_name,
                stage=stage,
                error=str(exc),
            )
            raise

    def _call_gemini_text(
        self,
        system_prompt: str,
        user_content: str,
        *,
        stage: str = "default",
    ) -> str:
        """Call Gemini and return the raw text response (no JSON parsing)."""
        genai.configure(api_key=settings.gemini_api_key)
        model_name = self._model_name()
        gemini_model = genai.GenerativeModel(
            model_name=model_name,
            generation_config=genai.types.GenerationConfig(
                temperature=self.temperature,
                max_output_tokens=32768,
            ),
            system_instruction=system_prompt,
        )
        start = time.time()
        try:
            response = gemini_model.generate_content(user_content)
            duration_ms = round((time.time() - start) * 1000)

            usage = getattr(response, "usage_metadata", None)
            tokens_in = getattr(usage, "prompt_token_count", None) if usage else None
            tokens_out = getattr(usage, "candidates_token_count", None) if usage else None

            self.log.info(
                "gemini.call",
                agent=self.agent_name,
                stage=stage,
                model=model_name,
                prompt_chars=len(system_prompt),
                response_chars=len(response.text),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                duration_ms=duration_ms,
            )

            finish_reason = None
            try:
                finish_reason = response.candidates[0].finish_reason.name
            except Exception:
                pass
            if finish_reason == "MAX_TOKENS":
                self.log.warning(
                    "gemini.truncated_at_max_tokens",
                    agent=self.agent_name,
                    stage=stage,
                    response_chars=len(response.text),
                )

            return response.text
        except Exception as exc:
            self.log.error(
                "gemini.error",
                agent=self.agent_name,
                stage=stage,
                error=str(exc),
            )
            raise

    @abstractmethod
    def process(self, *args, **kwargs):
        ...
