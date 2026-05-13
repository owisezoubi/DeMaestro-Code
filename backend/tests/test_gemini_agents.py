"""Tests for Gemini agents (W3.5-Patch 2). No real API calls are made."""
import pytest

from app.ai.gemini.agents import AnalystAgent
from app.models.structured_requirements import StructuredRequirements


def test_analyst_agent_in_mock_mode_returns_valid_requirements(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)
    result = AnalystAgent().analyze("any input")
    assert isinstance(result, StructuredRequirements)
    assert len(result.entities) >= 1
    assert len(result.user_requirements) >= 1
    assert len(result.ambiguities) >= 1


def test_analyst_agent_tracks_state(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)
    agent = AnalystAgent()
    input_text = "I want a todo app with user auth"
    agent.analyze(input_text)
    assert agent.state["raw_input_chars"] == len(input_text)
    assert agent.state["last_num_requirements"] > 0


def test_analyst_agent_loads_correct_prompt():
    agent = AnalystAgent()
    content = agent._load_prompt()
    assert "User-requirements engineering" in content
