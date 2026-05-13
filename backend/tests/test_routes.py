"""Tests for the approval route. No real Firebase or Gemini calls."""
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.ai.gemini.agents.blueprint import (
    APIRoute,
    BlueprintResponse,
    DatabaseTable,
    FrontendPage,
)
from app.auth.dependencies import get_current_user
from app.main import app
from app.models.project import ProjectMeta, ProjectStatus
from app.models.structured_requirements import (
    AmbiguityFlag,
    Entity,
    RequirementCategory,
    StructuredRequirements,
    UserRequirement,
)
from app.models.user import AuthUser


def _mock_user() -> AuthUser:
    return AuthUser(uid="test-uid", email="test@example.com")


def _make_sr() -> StructuredRequirements:
    return StructuredRequirements(
        app_name="TestApp",
        summary="A test application.",
        entities=[Entity(name="User", description="A user", fields=["id", "email"])],
        user_requirements=[
            UserRequirement(
                id="UR-01",
                statement="A logged-in user can log in with valid credentials.",
                rationale="Core feature.",
                acceptance_criteria=["POST /auth/login returns 200"],
                priority="must",
                category=RequirementCategory.functional,
            )
        ],
        ambiguities=[],
    )


def _make_blueprint() -> BlueprintResponse:
    return BlueprintResponse(
        database_schema=[
            DatabaseTable(
                name="Users",
                columns=[{"name": "id", "type": "uuid"}],
                description="User accounts",
            )
        ],
        api_routes=[
            APIRoute(
                method="GET",
                path="/api/users",
                description="List users",
                request_schema="None",
                response_schema="List of users",
            )
        ],
        frontend_pages=[
            FrontendPage(
                name="Dashboard",
                route="/dashboard",
                purpose="Main page",
                key_components=["Header"],
            )
        ],
        technology_stack_notes="React + FastAPI",
    )


@pytest.fixture(autouse=True)
def override_auth():
    app.dependency_overrides[get_current_user] = _mock_user
    yield
    app.dependency_overrides.clear()


client = TestClient(app)


def test_post_approve_updates_project_status_to_approved_and_stores_summary_blueprint():
    # summary + blueprint are pre-generated (by the orchestrator after clarifications complete).
    blueprint = _make_blueprint()
    project = ProjectMeta(
        id="proj123",
        name="TestApp",
        status=ProjectStatus.awaiting_approval,
        summary="# TestApp\n\nA test app.",
        blueprint=blueprint.model_dump(),
    )

    with (
        patch("app.services.firestore_service.get_project", return_value=project),
        patch("app.services.firestore_service.update_project") as mock_update,
    ):
        r = client.post("/projects/proj123/approve")

    assert r.status_code == 200
    data = r.json()
    assert data["project_id"] == "proj123"
    assert "approved_at" in data
    mock_update.assert_called_once()
    # Verify only status + approved_at are written (not re-generating summary/blueprint).
    update_fields = mock_update.call_args[0][2]
    assert update_fields["status"] == ProjectStatus.approved
    assert "summary" not in update_fields
    assert "blueprint" not in update_fields


def test_post_approve_requires_project_status_structured():
    project = ProjectMeta(id="proj123", name="TestApp", status=ProjectStatus.clarifying)

    with patch("app.services.firestore_service.get_project", return_value=project):
        r = client.post("/projects/proj123/approve")

    assert r.status_code == 400
    assert "clarifying" in r.json()["detail"]


def test_post_approve_rejects_if_summary_blueprint_not_yet_generated():
    # Project is in awaiting_approval but summary/blueprint haven't been written yet.
    project = ProjectMeta(id="proj123", name="TestApp", status=ProjectStatus.awaiting_approval)

    with patch("app.services.firestore_service.get_project", return_value=project):
        r = client.post("/projects/proj123/approve")

    assert r.status_code == 400
    assert "still being generated" in r.json()["detail"]


def test_post_approve_returns_isoformat_approved_at():
    blueprint = _make_blueprint()
    project = ProjectMeta(
        id="proj123",
        name="TestApp",
        status=ProjectStatus.awaiting_approval,
        summary="# Test",
        blueprint=blueprint.model_dump(),
    )

    with (
        patch("app.services.firestore_service.get_project", return_value=project),
        patch("app.services.firestore_service.update_project"),
    ):
        r = client.post("/projects/proj123/approve")

    assert r.status_code == 200
    approved_at_str = r.json()["approved_at"]
    # Must parse as a valid ISO 8601 datetime.
    dt = datetime.fromisoformat(approved_at_str)
    assert isinstance(dt, datetime)
