"""Tests for the Gemini AI client (W3-A). No real API calls are made."""
import pytest

from app.ai.gemini.client import _load_prompt, apply_clarifications, structure_requirements
from app.models.structured_requirements import (
    AmbiguityFlag,
    ClarificationAnswer,
    Entity,
    FeatureRequirement,
    StructuredRequirements,
)


def test_structure_requirements_mock_returns_valid_model(monkeypatch):
    monkeypatch.setattr("app.ai.gemini.client.is_mock", lambda: True)
    result = structure_requirements("I want a todo app")
    assert isinstance(result, StructuredRequirements)
    assert len(result.entities) >= 1
    assert len(result.features) >= 1


def test_apply_clarifications_mock_bumps_version_and_clears_ambiguities(monkeypatch):
    monkeypatch.setattr("app.ai.gemini.client.is_mock", lambda: True)
    sr = StructuredRequirements(
        app_name="Test",
        summary="A test application for validating the clarification pipeline.",
        entities=[Entity(name="User", description="A user of the system", fields=["id", "name"])],
        features=[FeatureRequirement(id="FR-01", description="User can log in", priority="must")],
        ambiguities=[
            AmbiguityFlag(
                id="AMB-01",
                field_path="auth.method",
                reason="Authentication method not specified",
            )
        ],
    )
    result = apply_clarifications(
        sr, [ClarificationAnswer(question_id="AMB-01", answer="email/password")]
    )
    assert result.version == 2
    assert result.ambiguities == []


def test_prompt_files_exist_and_nonempty():
    for name in ("structure_requirements", "apply_clarifications"):
        content = _load_prompt(name)
        assert len(content) >= 200, f"Prompt '{name}' is too short ({len(content)} chars)"
