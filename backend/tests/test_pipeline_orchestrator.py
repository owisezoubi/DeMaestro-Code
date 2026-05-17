"""Tests for the pipeline orchestrator (W3-B). No real Firebase or Gemini calls."""
import time
from datetime import datetime, timezone
from unittest.mock import patch

from app.models.project import ProjectStatus
from app.models.raw_input import RawInputDoc, RawInputType
from app.models.structured_requirements import (
    AmbiguityFlag,
    Entity,
    RequirementCategory,
    StructuredRequirements,
    UserRequirement,
)
from app.pipeline.orchestrator import apply_clarification_in_background, kick_off_structuring


def _make_ur(uid: str = "UR-01") -> UserRequirement:
    return UserRequirement(
        id=uid,
        statement="A logged-in user can log in with valid credentials.",
        rationale="Authentication is required for all user-facing features.",
        acceptance_criteria=["POST /auth/login with valid credentials returns 200"],
        priority="must",
        category=RequirementCategory.functional,
    )


def _make_sr(ambiguities: list[AmbiguityFlag] | None = None, version: int = 1) -> StructuredRequirements:
    return StructuredRequirements(
        app_name="TestApp",
        summary="A test application.",
        entities=[Entity(name="User", description="A user", fields=["id", "email"])],
        user_requirements=[_make_ur()],
        ambiguities=ambiguities or [],
        version=version,
    )


def test_kick_off_structuring_in_mock_mode_does_not_crash(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)

    fake_input = RawInputDoc(
        id="abc123000000",
        type=RawInputType.text,
        content="I want a todo app",
        char_count=17,
        timestamp=datetime.now(timezone.utc),
    )

    with (
        patch("app.services.firestore_service.list_raw_inputs", return_value=[fake_input]),
        patch("app.services.firestore_service.set_project_status") as mock_set_status,
        patch("app.services.firestore_service.add_structured_requirements") as mock_add_sr,
    ):
        kick_off_structuring("uid123", "proj456")
        time.sleep(0.5)

        statuses = [call.args[2] for call in mock_set_status.call_args_list]
        assert statuses[0] == ProjectStatus.structuring
        assert statuses[1] in (ProjectStatus.clarifying, ProjectStatus.awaiting_approval)

        mock_add_sr.assert_called_once()


def test_force_finish_at_round_3_clears_ambiguities():
    current_sr = _make_sr(
        ambiguities=[
            AmbiguityFlag(
                id="AMB-01",
                field_path="auth.method",
                reason="Auth method unclear",
                suggested_options=["email/password", "Google OAuth"],
            ),
            AmbiguityFlag(
                id="AMB-02",
                field_path="entities.User.fields",
                reason="Missing user fields",
                suggested_options=["name + role", "name only"],
            ),
        ],
        version=3,
    )

    with (
        patch("app.services.firestore_service.get_latest_structured_requirements", return_value=current_sr),
        patch("app.services.firestore_service.increment_clarification_round", return_value=3),
        patch("app.services.firestore_service.add_structured_requirements") as mock_add_sr,
        patch("app.services.firestore_service.add_clarification_turn"),
        patch("app.services.firestore_service.set_project_status") as mock_set_status,
        patch("app.ai.gemini.agents.coordinator.RequirementsCoordinator.apply_clarification") as mock_coordinator,
    ):
        apply_clarification_in_background("uid123", "proj456", "AMB-01", "Auth method?", "email/password")
        time.sleep(0.5)

        mock_coordinator.assert_not_called()

        mock_add_sr.assert_called_once()
        saved_sr = mock_add_sr.call_args[0][2]
        assert saved_sr.ambiguities == []

        statuses = [call.args[2] for call in mock_set_status.call_args_list]
        assert ProjectStatus.awaiting_approval in statuses


def test_round_1_trims_to_max_2_ambiguities():
    current_sr = _make_sr(version=1)

    gemini_result = _make_sr(
        ambiguities=[
            AmbiguityFlag(id="AMB-01", field_path="auth.method", reason="Unclear", suggested_options=["email/password", "OAuth"]),
            AmbiguityFlag(id="AMB-02", field_path="entities.Post.fields", reason="Missing fields", suggested_options=["title+body", "title only"]),
            AmbiguityFlag(id="AMB-03", field_path="user_requirements.roles", reason="Role count unclear", suggested_options=["single role", "admin + user"]),
            AmbiguityFlag(id="AMB-04", field_path="entities.Comment.fields", reason="Comment fields missing", suggested_options=["body only", "body + author"]),
        ],
        version=2,
    )

    with (
        patch("app.services.firestore_service.get_latest_structured_requirements", return_value=current_sr),
        patch("app.services.firestore_service.increment_clarification_round", return_value=1),
        patch("app.services.firestore_service.get_resolved_topics", return_value=[]),
        patch("app.services.firestore_service.add_resolved_topic"),
        patch("app.services.firestore_service.add_structured_requirements") as mock_add_sr,
        patch("app.services.firestore_service.add_clarification_turn"),
        patch("app.services.firestore_service.set_project_status"),
        patch("app.ai.gemini.agents.coordinator.RequirementsCoordinator.apply_clarification", return_value=gemini_result),
    ):
        apply_clarification_in_background("uid123", "proj456", "AMB-01", "Auth method?", "email/password")
        time.sleep(0.5)

        mock_add_sr.assert_called_once()
        saved_sr = mock_add_sr.call_args[0][2]
        assert len(saved_sr.ambiguities) == 2
        assert saved_sr.ambiguities[0].id == "AMB-01"
        assert saved_sr.ambiguities[1].id == "AMB-02"


def test_orchestrator_generates_summary_blueprint_when_clarifications_complete():
    """When the last clarification is answered, summary+blueprint should be written to the project doc."""
    current_sr = _make_sr(
        ambiguities=[
            AmbiguityFlag(
                id="AMB-01",
                field_path="auth.method",
                reason="Auth method unclear",
                suggested_options=["email/password", "Google OAuth"],
            )
        ],
        version=1,
    )
    # Coordinator returns SR with no remaining ambiguities → clarifications complete.
    resolved_sr = _make_sr(ambiguities=[], version=2)

    with (
        patch("app.services.firestore_service.get_latest_structured_requirements", return_value=current_sr),
        patch("app.services.firestore_service.increment_clarification_round", return_value=1),
        patch("app.services.firestore_service.get_resolved_topics", return_value=[]),
        patch("app.services.firestore_service.add_resolved_topic"),
        patch("app.services.firestore_service.add_structured_requirements"),
        patch("app.services.firestore_service.add_clarification_turn"),
        patch("app.services.firestore_service.set_project_status"),
        patch("app.ai.gemini.agents.coordinator.RequirementsCoordinator.apply_clarification", return_value=resolved_sr),
        # _generate_and_store_summary_blueprint will fetch SR again, then call coordinator.
        patch("app.services.firestore_service.update_project") as mock_update_project,
        patch("app.ai.gemini.agents.coordinator.RequirementsCoordinator.generate_summary", return_value="Mock summary"),
        patch("app.ai.gemini.agents.coordinator.RequirementsCoordinator.generate_blueprint") as mock_bp,
    ):
        from app.ai.gemini.agents.blueprint import BlueprintResponse, DatabaseTable, APIRoute, FrontendPage
        mock_bp.return_value = BlueprintResponse(
            database_schema=[DatabaseTable(name="Users", columns=[{"name": "id", "type": "uuid"}], description="Users")],
            api_routes=[APIRoute(method="GET", path="/api/users", description="List users", request_schema="None", response_schema="list")],
            frontend_pages=[FrontendPage(name="Dashboard", route="/", purpose="Main page", key_components=[])],
            technology_stack_notes="",
        )

        apply_clarification_in_background("uid123", "proj456", "AMB-01", "Auth method?", "email/password")
        time.sleep(0.5)

        mock_update_project.assert_called_once()
        update_args = mock_update_project.call_args[0][2]
        assert "summary" in update_args
        assert "blueprint" in update_args
        assert update_args["summary"] == "Mock summary"
