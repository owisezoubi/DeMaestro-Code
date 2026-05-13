"""Tests for the Gemini AI client module. No real API calls are made."""
from app.ai.gemini.agents import AnalystAgent
from app.ai.gemini.agents._base import _load_prompt
from app.models.structured_requirements import StructuredRequirements


def test_analyst_agent_mock_returns_valid_model(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)
    result = AnalystAgent().analyze("I want a todo app")
    assert isinstance(result, StructuredRequirements)
    assert len(result.entities) >= 1
    assert len(result.user_requirements) >= 1


def test_prompt_files_exist_and_nonempty():
    for name in ("analyst", "clarification", "validator"):
        content = _load_prompt(name)
        assert len(content) >= 200, f"Prompt '{name}' is too short ({len(content)} chars)"
