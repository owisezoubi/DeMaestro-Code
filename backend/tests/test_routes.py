"""Tests for the approval route. No real Firebase or Gemini calls."""
from datetime import datetime
from unittest.mock import MagicMock, patch

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
from app.routes.projects import _regenerate_with_changes


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


# ── request-changes tests ────────────────────────────────────────────────────


def test_post_request_changes_requires_deployed_status():
    project = ProjectMeta(id="proj123", name="TestApp", status=ProjectStatus.generating)

    with patch("app.services.firestore_service.get_project", return_value=project):
        r = client.post(
            "/projects/proj123/request-changes",
            json={"change_request": "Add dark mode"},
        )

    assert r.status_code == 400
    assert "generating" in r.json()["detail"]


def test_post_request_changes_stores_request_and_starts_regeneration():
    project = ProjectMeta(
        id="proj123",
        name="TestApp",
        status=ProjectStatus.ready,
        modification_round=1,
    )

    with (
        patch("app.services.firestore_service.get_project", return_value=project),
        patch("app.services.firestore_service.update_project") as mock_update,
        patch("threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = lambda: None
        r = client.post(
            "/projects/proj123/request-changes",
            json={"change_request": "Add dark mode"},
        )

    assert r.status_code == 200
    data = r.json()
    assert data["project_id"] == "proj123"
    assert "Regenerating" in data["message"]

    update_fields = mock_update.call_args[0][2]
    assert update_fields["status"] == ProjectStatus.modifying
    assert update_fields["last_change_request"] == "Add dark mode"
    assert update_fields["modification_round"] == 2

    mock_thread.assert_called_once()


def test_post_request_changes_missing_request_returns_400():
    project = ProjectMeta(id="proj123", name="TestApp", status=ProjectStatus.ready)

    with patch("app.services.firestore_service.get_project", return_value=project):
        r = client.post("/projects/proj123/request-changes", json={})

    assert r.status_code == 400
    assert "change_request" in r.json()["detail"]


def test_request_changes_endpoint_exists():
    """Endpoint is registered and reachable (not 404/405)."""
    project = ProjectMeta(id="proj123", name="TestApp", status=ProjectStatus.generating)

    with patch("app.services.firestore_service.get_project", return_value=project):
        r = client.post(
            "/projects/proj123/request-changes",
            json={"change_request": "anything"},
        )

    assert r.status_code != 404
    assert r.status_code != 405


def test_request_changes_stores_in_firestore():
    """update_project is called with the change_request and modifying status."""
    project = ProjectMeta(
        id="proj456",
        name="AnotherApp",
        status=ProjectStatus.ready,
        modification_round=None,
    )

    with (
        patch("app.services.firestore_service.get_project", return_value=project),
        patch("app.services.firestore_service.update_project") as mock_update,
        patch("threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = lambda: None
        r = client.post(
            "/projects/proj456/request-changes",
            json={"change_request": "Switch to dark theme"},
        )

    assert r.status_code == 200
    mock_update.assert_called_once()
    fields = mock_update.call_args[0][2]
    assert fields["last_change_request"] == "Switch to dark theme"
    assert fields["modification_round"] == 1
    assert fields["status"] == ProjectStatus.modifying


# ── _regenerate_with_changes unit tests ──────────────────────────────────────

def _make_regen_project() -> ProjectMeta:
    blueprint = _make_blueprint()
    return ProjectMeta(
        id="proj123",
        name="TestApp",
        status=ProjectStatus.modifying,
        modification_round=2,
        generated_files={"src/App.jsx": "const App = () => <div/>;"},
        blueprint=blueprint.model_dump(),
        generation_plan={
            "technology_stack": "python-sqlite",
            "files": [],
            "generation_order": [],
            "notes": "",
        },
    )


def _mock_agents(mock_debug_status="fixed"):
    """Return (debug_mock, verify_mock, deploy_mock) MagicMocks."""
    debug = MagicMock()
    debug.debug_and_fix.return_value = {
        "status": mock_debug_status,
        "fixed_files": {"src/App.jsx": "const App = () => <div>dark</div>;"},
        "attempt_counts": {},
        "errors": [] if mock_debug_status == "fixed" else ["could not apply change"],
    }

    verify = MagicMock()
    verify.verify.return_value = {"status": "pass", "issues": []}

    deploy = MagicMock()
    deploy.deploy.return_value = {
        "status": "ready",
        "zip_url": "https://storage.example.com/proj123.zip",
    }

    return debug, verify, deploy


def test_regenerate_with_changes_updates_files_and_redeploys():
    project = _make_regen_project()
    debug, verify, deploy = _mock_agents()

    with (
        patch("app.services.firestore_service.get_project", return_value=project),
        patch("app.services.firestore_service.update_project") as mock_update,
        patch("app.ai.claude.agents.debugger.DebuggerAgent", return_value=debug),
        patch("app.ai.claude.agents.verifier.VerifierAgent", return_value=verify),
        patch("app.ai.claude.agents.deployer.DeployerAgent", return_value=deploy),
    ):
        _regenerate_with_changes("test-uid", "proj123", "Add dark mode")

    # Verify the final update contains the merged files
    final_fields = mock_update.call_args_list[-1][0][2]
    assert final_fields["generated_files"]["src/App.jsx"] == "const App = () => <div>dark</div>;"
    assert final_fields["zip_url"] == "https://storage.example.com/proj123.zip"
    # Verify the deploy call received the merged file set
    deploy.deploy.assert_called_once()
    deployed_files = deploy.deploy.call_args[0][2]
    assert "src/App.jsx" in deployed_files


def test_regenerate_with_changes_sets_status_to_deployed_on_success():
    project = _make_regen_project()
    debug, verify, deploy = _mock_agents()

    with (
        patch("app.services.firestore_service.get_project", return_value=project),
        patch("app.services.firestore_service.update_project") as mock_update,
        patch("app.ai.claude.agents.debugger.DebuggerAgent", return_value=debug),
        patch("app.ai.claude.agents.verifier.VerifierAgent", return_value=verify),
        patch("app.ai.claude.agents.deployer.DeployerAgent", return_value=deploy),
    ):
        _regenerate_with_changes("test-uid", "proj123", "Add dark mode")

    calls = mock_update.call_args_list
    # First call sets regenerating; last call sets deployed
    assert calls[0][0][2] == {"status": ProjectStatus.regenerating}
    final_fields = calls[-1][0][2]
    assert final_fields["status"] == ProjectStatus.ready
    assert final_fields["modification_round_completed"] == 2
    assert final_fields["error_message"] is None


def test_regenerate_with_changes_sets_deployment_failed_on_error():
    project = _make_regen_project()
    debug, verify, deploy = _mock_agents(mock_debug_status="error")

    with (
        patch("app.services.firestore_service.get_project", return_value=project),
        patch("app.services.firestore_service.update_project") as mock_update,
        patch("app.ai.claude.agents.debugger.DebuggerAgent", return_value=debug),
        patch("app.ai.claude.agents.verifier.VerifierAgent", return_value=verify),
        patch("app.ai.claude.agents.deployer.DeployerAgent", return_value=deploy),
    ):
        _regenerate_with_changes("test-uid", "proj123", "Add dark mode")

    final_fields = mock_update.call_args_list[-1][0][2]
    assert final_fields["status"] == ProjectStatus.failed
    assert "Could not generate changes" in final_fields["error_message"]
