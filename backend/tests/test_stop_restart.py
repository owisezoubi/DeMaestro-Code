"""Tests for stop/restart pipeline features and checklist visibility."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.main import app
from app.models.project import ProjectMeta, ProjectStatus
from app.models.user import AuthUser
from app.pipeline import generation_orchestrator as _orch


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mock_user() -> AuthUser:
    return AuthUser(uid="test-uid", email="test@example.com")


def _make_project(**kwargs) -> ProjectMeta:
    defaults = dict(
        id="proj-123",
        name="Test Project",
        status=ProjectStatus.stopped,
    )
    defaults.update(kwargs)
    return ProjectMeta(**defaults)


def _client():
    c = TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides[get_current_user] = lambda: _mock_user()
    return c


# ── Cancellation registry unit tests ─────────────────────────────────────────

def test_request_cancellation_sets_flag():
    _orch.clear_cancellation("proj-abc")
    _orch.request_cancellation("proj-abc")
    assert _orch.is_cancelled("proj-abc") is True
    _orch.clear_cancellation("proj-abc")


def test_is_cancelled_returns_false_after_clear():
    _orch.request_cancellation("proj-abc2")
    _orch.clear_cancellation("proj-abc2")
    assert _orch.is_cancelled("proj-abc2") is False


def test_is_active_true_after_mark():
    _orch._mark_inactive("proj-act1")
    _orch._mark_active("proj-act1")
    assert _orch.is_active("proj-act1") is True
    _orch._mark_inactive("proj-act1")


def test_is_active_false_after_mark_inactive():
    _orch._mark_active("proj-act2")
    _orch._mark_inactive("proj-act2")
    assert _orch.is_active("proj-act2") is False


# ── Stop endpoint tests ───────────────────────────────────────────────────────

def test_stop_endpoint_returns_cancellation_requested_when_active():
    project = _make_project(status=ProjectStatus.generating)
    with (
        patch("app.routes.generation.firestore_service.get_project", return_value=project),
        patch("app.pipeline.generation_orchestrator.is_active", return_value=True),
        patch("app.pipeline.generation_orchestrator.request_cancellation") as mock_req,
    ):
        c = _client()
        resp = c.post("/projects/proj-123/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancellation_requested"
        mock_req.assert_called_once_with("proj-123")


def test_stop_endpoint_returns_not_active_when_idle():
    project = _make_project(status=ProjectStatus.stopped)
    with (
        patch("app.routes.generation.firestore_service.get_project", return_value=project),
        patch("app.pipeline.generation_orchestrator.is_active", return_value=False),
    ):
        c = _client()
        resp = c.post("/projects/proj-123/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_active"


# ── Restart endpoint tests ────────────────────────────────────────────────────

def test_restart_from_scratch_409_when_active():
    project = _make_project(status=ProjectStatus.stopped)
    with (
        patch("app.routes.generation.firestore_service.get_project", return_value=project),
        patch("app.pipeline.generation_orchestrator.is_active", return_value=True),
    ):
        c = _client()
        resp = c.post("/projects/proj-123/restart-from-scratch")
        assert resp.status_code == 409
        assert "Stop the current run" in resp.json()["detail"]


def test_restart_from_scratch_starts_generation_when_stopped():
    project = _make_project(status=ProjectStatus.stopped)
    with (
        patch("app.routes.generation.firestore_service.get_project", return_value=project),
        patch("app.pipeline.generation_orchestrator.is_active", return_value=False),
        patch("app.pipeline.generation_orchestrator.clear_cancellation"),
        patch("app.routes.generation.firestore_service.reset_generation_state"),
        patch("app.routes.generation.firestore_service.set_project_status"),
        patch("app.routes.generation.GenerationOrchestrator") as MockOrch,
        patch("threading.Thread") as MockThread,
    ):
        mock_orch_inst = MagicMock()
        MockOrch.return_value = mock_orch_inst
        mock_thread_inst = MagicMock()
        MockThread.return_value = mock_thread_inst

        c = _client()
        resp = c.post("/projects/proj-123/restart-from-scratch")
        assert resp.status_code == 200
        assert resp.json()["status"] == "restarted"


# ── Checklist visibility test ─────────────────────────────────────────────────

def test_checklist_results_in_status_payload():
    checklist = [{"id": "m-1", "type": "model", "class_name": "User",
                  "table_name": "users", "columns": [{"name": "id", "type": "uuid"}]}]
    checklist_results = [{"item_id": "m-1", "status": "pass", "reason": ""}]
    project = _make_project(
        status=ProjectStatus.generating,
        checklist=checklist,
        checklist_results=checklist_results,
        generation_plan=None,
    )
    with (
        patch("app.routes.generation.firestore_service.get_project", return_value=project),
        patch("app.routes.generation.firestore_service.get_latest_structured_requirements", return_value=None),
    ):
        c = _client()
        resp = c.get("/projects/proj-123/generation-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["checklist"] == checklist
        assert data["checklist_results"] == checklist_results


# ── Pipeline cancellation integration test ───────────────────────────────────

def test_pipeline_sets_stopped_when_cancelled():
    """Verify that when is_cancelled returns True after architect, the pipeline
    sets status=stopped in Firestore and returns a cancelled dict."""
    from app.pipeline.generation_orchestrator import GenerationOrchestrator

    mock_plan = MagicMock()
    mock_plan.files = []
    mock_plan.generation_order = []
    mock_plan.model_dump.return_value = {}
    mock_plan.technology_stack = "python-postgres"
    mock_plan.extra_dependencies = []
    mock_plan.extra_frontend_dependencies = []
    mock_plan.notes = ""

    mock_checklist_item = MagicMock()
    mock_checklist_item.id = "r-1"

    mock_project = MagicMock()
    mock_project.blueprint = {
        "app_name": "Test",
        "database_schema": [],
        "api_routes": [],
        "frontend_pages": [],
        "technology_stack_notes": "",
    }
    mock_project.generation_plan = None
    mock_project.generated_files = None
    mock_project.status = ProjectStatus.approved

    mock_sr = MagicMock()
    mock_sr.app_name = "Test"
    mock_sr.auth_required = True
    mock_sr.user_requirements = []
    mock_sr.summary = ""

    update_calls = []

    def fake_update(uid, pid, fields):
        update_calls.append(fields)

    with (
        patch("app.pipeline.generation_orchestrator.firestore_service.get_project", return_value=mock_project),
        patch("app.pipeline.generation_orchestrator.firestore_service.get_latest_structured_requirements", return_value=mock_sr),
        patch("app.pipeline.generation_orchestrator.firestore_service.update_project", side_effect=fake_update),
        patch("app.pipeline.generation_orchestrator.firestore_service.set_project_status"),
        patch("app.pipeline.generation_orchestrator.template_service.load_stack_templates", return_value={}),
        patch("app.pipeline.generation_orchestrator.template_service.slugify", return_value="test"),
        patch.object(
            GenerationOrchestrator,
            "__init__",
            lambda self: None,
        ),
    ):
        orch = GenerationOrchestrator.__new__(GenerationOrchestrator)
        orch.architect = MagicMock()
        orch.architect.architect.return_value = (mock_plan, [mock_checklist_item])
        orch.generator = MagicMock()
        orch.tester = MagicMock()
        orch.debugger = MagicMock()
        orch.verifier = MagicMock()
        orch.deployer = MagicMock()

        # Inject a cancellation right after architect returns
        original_is_cancelled = _orch.is_cancelled

        call_count = [0]

        def cancel_after_architect(pid):
            call_count[0] += 1
            # Return True on the first call (post-architect checkpoint)
            return call_count[0] >= 1

        with patch("app.pipeline.generation_orchestrator.is_cancelled", side_effect=cancel_after_architect):
            result = orch.run_full_pipeline("uid-1", "proj-cancel")

    assert result["status"] == "cancelled"
    # Verify at least one update_project call set status=stopped
    stopped_updates = [c for c in update_calls if c.get("status") == ProjectStatus.stopped]
    assert len(stopped_updates) >= 1, f"Expected stopped status update, got: {update_calls}"
