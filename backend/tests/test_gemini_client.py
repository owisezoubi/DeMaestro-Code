"""Tests for the Gemini AI client (W3-A). No real API calls are made."""
import pytest

from app.ai.gemini.client import _load_prompt, apply_clarifications, structure_requirements
from app.models.structured_requirements import (
    AmbiguityFlag,
    ClarificationAnswer,
    Entity,
    RequirementCategory,
    StructuredRequirements,
    UserRequirement,
)


def _make_ur(uid: str = "UR-01") -> UserRequirement:
    return UserRequirement(
        id=uid,
        statement="A logged-in user can log in with valid credentials.",
        rationale="Authentication is required for all user-facing features.",
        acceptance_criteria=["POST /auth/login with valid credentials returns 200"],
        priority="must",
        category=RequirementCategory.functional,
    )


def test_structure_requirements_mock_returns_valid_model(monkeypatch):
    monkeypatch.setattr("app.ai.gemini.client.is_mock", lambda: True)
    result = structure_requirements("I want a todo app")
    assert isinstance(result, StructuredRequirements)
    assert len(result.entities) >= 1
    assert len(result.user_requirements) >= 1


def test_apply_clarifications_mock_bumps_version_and_clears_ambiguities(monkeypatch):
    monkeypatch.setattr("app.ai.gemini.client.is_mock", lambda: True)
    sr = StructuredRequirements(
        app_name="Test",
        summary="A test application for validating the clarification pipeline.",
        entities=[Entity(name="User", description="A user of the system", fields=["id", "name"])],
        user_requirements=[_make_ur()],
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
