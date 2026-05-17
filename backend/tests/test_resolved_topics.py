"""Tests for resolved-topic deduplication: helper function and coordinator integration."""
from unittest.mock import patch

from app.ai.gemini.agents.coordinator import RequirementsCoordinator, _is_resolved_topic
from app.models.structured_requirements import (
    AmbiguityFlag,
    Entity,
    RequirementCategory,
    StructuredRequirements,
    UserRequirement,
)


def _make_sr(ambiguities: list[AmbiguityFlag] | None = None, version: int = 1) -> StructuredRequirements:
    return StructuredRequirements(
        app_name="TestApp",
        summary="A test app.",
        entities=[Entity(name="User", description="A user", fields=["id"])],
        user_requirements=[
            UserRequirement(
                id="UR-01",
                statement="User can log in.",
                rationale="Auth required.",
                acceptance_criteria=["200 on valid login"],
                priority="must",
                category=RequirementCategory.functional,
            )
        ],
        ambiguities=ambiguities or [],
        version=version,
    )


# ── unit tests for _is_resolved_topic ────────────────────────────────────────


def test_is_resolved_topic_exact_match():
    assert _is_resolved_topic("auth.method", ["auth.method"])


def test_is_resolved_topic_prefix_match():
    """auth.method resolved → auth.providers filtered via shared first segment."""
    assert _is_resolved_topic("auth.providers", ["auth.method"])


def test_is_resolved_topic_no_false_positive():
    """notifications resolved should NOT filter an unrelated entities field."""
    assert not _is_resolved_topic("entities.User.fields", ["notifications"])


def test_is_resolved_topic_strips_array_indices():
    """Array indices in field_path are stripped before comparison."""
    assert _is_resolved_topic("entities[0].fields", ["entities.fields"])


# ── integration tests via RequirementsCoordinator ────────────────────────────


def test_resolved_topic_filters_duplicate_ambiguity():
    """Coordinator drops a re-introduced ambiguity on an already-resolved field_path."""
    current = _make_sr(
        ambiguities=[
            AmbiguityFlag(
                id="AMB-01",
                field_path="auth.method",
                reason="Auth method unclear",
                suggested_options=["email/password", "OAuth"],
            )
        ],
        version=1,
    )
    # Imagine validator re-introduces the exact same topic after the user answered.
    updated_with_dupe = _make_sr(
        ambiguities=[
            AmbiguityFlag(
                id="AMB-02",
                field_path="auth.method",
                reason="Still unclear",
                suggested_options=["email/password"],
            )
        ],
        version=2,
    )

    with (
        patch(
            "app.ai.gemini.agents.clarification.ClarificationAgent.apply_answer",
            return_value=updated_with_dupe,
        ),
        patch(
            "app.ai.gemini.agents.validator.ValidatorAgent.validate",
            return_value=updated_with_dupe,
        ),
        patch(
            "app.ai.gemini.agents.completeness.CompletenessAgent.validate",
            return_value=updated_with_dupe,
        ),
    ):
        coordinator = RequirementsCoordinator()
        result = coordinator.apply_clarification(
            current, "AMB-01", "email/password", resolved_topics=["auth.method"]
        )

    assert all(a.field_path != "auth.method" for a in result.ambiguities), (
        "Duplicate auth.method ambiguity should have been filtered"
    )


def test_resolved_topic_prefix_match():
    """auth.method resolved → auth.providers is also filtered via first-segment prefix."""
    current = _make_sr(version=1)
    updated_with_auth_providers = _make_sr(
        ambiguities=[
            AmbiguityFlag(
                id="AMB-10",
                field_path="auth.providers",
                reason="Which OAuth providers?",
                suggested_options=["Google", "GitHub"],
            )
        ],
        version=2,
    )

    with (
        patch(
            "app.ai.gemini.agents.clarification.ClarificationAgent.apply_answer",
            return_value=updated_with_auth_providers,
        ),
        patch(
            "app.ai.gemini.agents.validator.ValidatorAgent.validate",
            return_value=updated_with_auth_providers,
        ),
        patch(
            "app.ai.gemini.agents.completeness.CompletenessAgent.validate",
            return_value=updated_with_auth_providers,
        ),
    ):
        coordinator = RequirementsCoordinator()
        result = coordinator.apply_clarification(
            current, "AMB-01", "some answer", resolved_topics=["auth.method"]
        )

    assert all(a.field_path != "auth.providers" for a in result.ambiguities), (
        "auth.providers should have been filtered because 'auth' prefix was already resolved"
    )
