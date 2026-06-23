"""Tests for DeployerAgent ZIP packaging and GenerationOrchestrator best-effort packaging."""
import io
import zipfile
from unittest.mock import MagicMock, patch

from app.ai.claude.agents.deployer import DeployerAgent
from app.models.project import ProjectStatus


def _make_files():
    return {
        "backend/app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
        "frontend/src/App.jsx": "export default function App() { return null; }\n",
        "README.md": "# My App\n",
    }


# ── DeployerAgent ─────────────────────────────────────────────────────────────

def test_deployer_mock_mode(monkeypatch):
    """Mock mode returns status=ready with a zip_url containing the project_id."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    result = DeployerAgent().deploy("uid1", "proj-abc", _make_files(), MagicMock())

    assert result["status"] == "ready"
    assert result["zip_url"] is not None
    assert "proj-abc" in result["zip_url"]


def test_deployer_packages_zip_and_returns_signed_url(monkeypatch):
    """Real mode: packages ZIP, uploads to storage, returns signed URL."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", False)

    fake_signed_url = "https://storage.example.com/proj-xyz.zip?token=abc"

    with (
        patch("app.ai.claude.agents.deployer.zip_service.package_project") as mock_zip,
        patch("app.ai.claude.agents.deployer.storage_service.upload_zip", return_value="users/uid1/projects/proj-xyz/exports/proj-xyz.zip") as mock_upload,
        patch("app.ai.claude.agents.deployer.storage_service.signed_download_url", return_value=fake_signed_url) as mock_sign,
        patch("app.ai.claude.agents.deployer.firestore_service.update_project") as mock_update,
    ):
        mock_zip.return_value = b"PK\x03\x04fakezipbytes"

        result = DeployerAgent().deploy("uid1", "proj-xyz", _make_files(), MagicMock())

    assert result["status"] == "ready"
    assert result["zip_url"] == fake_signed_url

    mock_zip.assert_called_once()
    mock_upload.assert_called_once_with("uid1", "proj-xyz", mock_zip.return_value, zip_filename="proj-xyz.zip")
    mock_sign.assert_called_once_with("users/uid1/projects/proj-xyz/exports/proj-xyz.zip", expires_in_days=7, download_filename="proj-xyz.zip")

    update_kwargs = mock_update.call_args.args[2]
    assert update_kwargs["zip_url"] == fake_signed_url
    assert update_kwargs["status"] == ProjectStatus.ready
    assert "packaged_at" in update_kwargs


def test_deployer_zip_contains_all_files(monkeypatch):
    """zip_service.package_project includes every generated file."""
    from app.services.zip_service import package_project

    files = _make_files()
    zip_bytes = package_project(files)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())

    for path in files:
        assert path in names, f"Missing {path} in ZIP"


# ── GenerationOrchestrator best-effort ────────────────────────────────────────

def test_orchestrator_packages_zip_even_when_tests_fail(monkeypatch):
    """When tests never pass, pipeline still packages ZIP and sets ready_with_warnings."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    from app.ai.gemini.agents.blueprint import APIRoute, BlueprintResponse, DatabaseTable, FrontendPage
    from app.models.generation_plan import FileToGenerate, GenerationPlan
    from app.models.project import ProjectMeta

    blueprint = BlueprintResponse(
        database_schema=[DatabaseTable(name="Items", columns=[], description="items")],
        api_routes=[APIRoute(method="GET", path="/api/items", description="list",
                             request_schema="None", response_schema="list")],
        frontend_pages=[FrontendPage(name="Home", route="/", purpose="main", key_components=[])],
        technology_stack_notes="React + FastAPI",
    )
    project = ProjectMeta(
        id="proj-fail",
        name="Fail Test",
        status=ProjectStatus.approved,
        blueprint=blueprint.model_dump(),
    )
    sr = MagicMock()
    sr.app_name = "FailTest"

    plan = GenerationPlan(
        technology_stack="python-postgres",
        files=[FileToGenerate(path="backend/app/main.py", description="main", template="fastapi_main")],
        generation_order=["backend/app/main.py"],
        notes="",
    )

    always_failing_test = {
        "status": "failure",
        "errors": ["syntax error"],
        "passed_checks": {"install": "failed"},
        "logs": {"install": "pip failed"},
    }
    debug_fixed = {
        "status": "fixed",
        "fixed_files": {"backend/app/main.py": "# fixed\n"},
        "attempt_counts": {},
        "errors": [],
    }

    updates = []

    with (
        patch("app.services.firestore_service.get_project", return_value=project),
        patch("app.services.firestore_service.get_latest_structured_requirements", return_value=sr),
        patch("app.services.firestore_service.update_project", side_effect=lambda u, p, d: updates.append(d)),
        patch("app.services.firestore_service.set_project_status"),
        patch("app.ai.claude.agents.architect.ArchitectAgent.architect", return_value=(plan, [])),
        patch("app.services.template_service.load_stack_templates", return_value={}),
        patch(
            "app.ai.claude.agents.generator.GeneratorAgent.generate_file",
            return_value="# generated\n",
        ),
        patch("app.ai.claude.agents.tester.TesterAgent.run_tests", return_value=always_failing_test),
        patch("app.ai.claude.agents.debugger.DebuggerAgent.debug_and_fix", return_value=debug_fixed),
    ):
        from app.pipeline.generation_orchestrator import GenerationOrchestrator

        result = GenerationOrchestrator().run_full_pipeline("uid1", "proj-fail")

    assert result["status"] == "success"
    assert result["zip_url"] is not None
    assert len(result["errors"]) > 0

    statuses_written = [d["status"] for d in updates if "status" in d]
    assert ProjectStatus.ready_with_warnings in statuses_written
