"""Tests for GenerationOrchestrator template seeding behaviour."""
from unittest.mock import MagicMock, call, patch

from app.ai.gemini.agents.blueprint import APIRoute, BlueprintResponse, DatabaseTable, FrontendPage
from app.models.generation_plan import FileToGenerate, GenerationPlan
from app.models.project import ProjectMeta, ProjectStatus
from app.models.structured_requirements import (
    AmbiguityFlag,
    Entity,
    RequirementCategory,
    StructuredRequirements,
    UserRequirement,
)

_FAKE_TEMPLATES = {
    "frontend/package.json": '{"name":"test-app"}',
    "backend/requirements.txt": "fastapi==0.115.0\n",
    "frontend/src/index.css": "@tailwind base;\n",
}


def _make_sr() -> StructuredRequirements:
    return StructuredRequirements(
        app_name="TestApp",
        summary="A test app.",
        entities=[Entity(name="Item", description="an item", fields=["id", "name"])],
        user_requirements=[
            UserRequirement(
                id="UR-01",
                statement="A user can manage items in the app.",
                rationale="Core feature.",
                acceptance_criteria=["GET /api/items returns 200"],
                priority="must",
                category=RequirementCategory.functional,
            )
        ],
        ambiguities=[],
    )


def _make_blueprint() -> BlueprintResponse:
    return BlueprintResponse(
        database_schema=[DatabaseTable(name="Items", columns=[], description="items")],
        api_routes=[APIRoute(method="GET", path="/api/items", description="list",
                             request_schema="None", response_schema="list")],
        frontend_pages=[FrontendPage(name="Home", route="/", purpose="main", key_components=[])],
        technology_stack_notes="React + FastAPI",
    )


def _make_plan(extra_files: list[FileToGenerate] | None = None) -> GenerationPlan:
    base = [
        FileToGenerate(path="backend/app/models.py", description="models", template="db_schema"),
        FileToGenerate(path="backend/app/main.py", description="main", template="none"),
    ]
    files = base + (extra_files or [])
    return GenerationPlan(
        technology_stack="python-postgres",
        files=files,
        generation_order=[f.path for f in files],
        notes="",
    )


def _make_project(blueprint: BlueprintResponse) -> ProjectMeta:
    return ProjectMeta(
        id="proj-scaffold",
        name="TestApp",
        status=ProjectStatus.approved,
        blueprint=blueprint.model_dump(),
    )


# ── Template seeding ──────────────────────────────────────────────────────────

def test_orchestrator_seeds_generated_files_with_templates_before_claude(monkeypatch):
    """Templates are in generated_files before any Claude generate_file call."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    sr = _make_sr()
    blueprint = _make_blueprint()
    project = _make_project(blueprint)
    plan = _make_plan()

    generated_snapshots: list[dict] = []

    def capturing_generate(file_to_gen, p, bp, previously_generated):
        generated_snapshots.append(dict(previously_generated))
        return f"# generated {file_to_gen.path}\n"

    with (
        patch("app.services.firestore_service.get_project", return_value=project),
        patch("app.services.firestore_service.get_latest_structured_requirements", return_value=sr),
        patch("app.services.firestore_service.update_project"),
        patch("app.services.firestore_service.set_project_status"),
        patch("app.ai.claude.agents.architect.ArchitectAgent.architect", return_value=(plan, [])),
        patch("app.services.template_service.load_stack_templates", return_value=_FAKE_TEMPLATES),
        patch(
            "app.ai.claude.agents.generator.GeneratorAgent.generate_file",
            side_effect=capturing_generate,
        ),
    ):
        from app.pipeline.generation_orchestrator import GenerationOrchestrator
        GenerationOrchestrator().run_full_pipeline("uid1", "proj-scaffold")

    assert len(generated_snapshots) > 0, "generate_file was never called"
    first_snapshot = generated_snapshots[0]

    for scaffold_path in _FAKE_TEMPLATES:
        assert scaffold_path in first_snapshot, (
            f"Template path {scaffold_path!r} was not in generated_files "
            "when the first Claude generate_file call was made"
        )


def test_orchestrator_template_wins_over_claude_collision(monkeypatch):
    """If the architect plans a scaffolding file, template content is kept; Claude is not called."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    sr = _make_sr()
    blueprint = _make_blueprint()
    project = _make_project(blueprint)

    colliding_file = FileToGenerate(
        path="frontend/package.json",
        description="package.json — should be skipped",
        template="none",
    )
    plan = _make_plan(extra_files=[colliding_file])

    claude_calls: list[str] = []

    def tracking_generate(file_to_gen, p, bp, prev):
        claude_calls.append(file_to_gen.path)
        return f"# claude: {file_to_gen.path}\n"

    with (
        patch("app.services.firestore_service.get_project", return_value=project),
        patch("app.services.firestore_service.get_latest_structured_requirements", return_value=sr),
        patch("app.services.firestore_service.update_project"),
        patch("app.services.firestore_service.set_project_status"),
        patch("app.ai.claude.agents.architect.ArchitectAgent.architect", return_value=(plan, [])),
        patch("app.services.template_service.load_stack_templates", return_value=_FAKE_TEMPLATES),
        patch(
            "app.ai.claude.agents.generator.GeneratorAgent.generate_file",
            side_effect=tracking_generate,
        ),
    ):
        from app.pipeline.generation_orchestrator import GenerationOrchestrator
        result = GenerationOrchestrator().run_full_pipeline("uid1", "proj-scaffold")

    assert "frontend/package.json" not in claude_calls, (
        "Claude was called for a scaffolding file — template should have won"
    )
    assert result["generated_files"]["frontend/package.json"] == _FAKE_TEMPLATES["frontend/package.json"]


def test_orchestrator_total_files_includes_scaffold_count(monkeypatch):
    """total_files reported to Firestore = scaffold count + app file count."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    sr = _make_sr()
    blueprint = _make_blueprint()
    project = _make_project(blueprint)
    plan = _make_plan()  # 2 app files

    firestore_updates: list[dict] = []

    with (
        patch("app.services.firestore_service.get_project", return_value=project),
        patch("app.services.firestore_service.get_latest_structured_requirements", return_value=sr),
        patch("app.services.firestore_service.update_project", side_effect=lambda u, p, d: firestore_updates.append(d)),
        patch("app.services.firestore_service.set_project_status"),
        patch("app.ai.claude.agents.architect.ArchitectAgent.architect", return_value=(plan, [])),
        patch("app.services.template_service.load_stack_templates", return_value=_FAKE_TEMPLATES),
        patch("app.ai.claude.agents.generator.GeneratorAgent.generate_file", return_value="# ok\n"),
    ):
        from app.pipeline.generation_orchestrator import GenerationOrchestrator
        GenerationOrchestrator().run_full_pipeline("uid1", "proj-scaffold")

    total_updates = [d for d in firestore_updates if "total_files" in d]
    assert total_updates, "total_files was never written to Firestore"

    expected_total = len(_FAKE_TEMPLATES) + len(plan.files)
    assert total_updates[0]["total_files"] == expected_total
    assert total_updates[0]["generated_count"] == len(_FAKE_TEMPLATES)


def test_orchestrator_install_failure_results_in_ready_with_warnings_not_failed(monkeypatch):
    """When install fails with an infrastructure error, pipeline delivers ready_with_warnings ZIP."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    sr = _make_sr()
    blueprint = _make_blueprint()
    project = _make_project(blueprint)
    plan = _make_plan()

    # Tester always fails with a wheel-build infra error
    failing_test = {
        "status": "failure",
        "errors": ["Failed building wheel for psycopg2-binary"],
        "passed_checks": {"install": "failed", "lint": "skipped", "typecheck": "skipped", "boot": "skipped"},
        "logs": {"install": "ERROR: Failed building wheel for psycopg2-binary"},
    }
    # Debugger classifies it as infrastructure → skipped
    infra_skip = {
        "status": "skipped",
        "reason": "All errors are infrastructure, not code",
        "fixed_files": {},
        "attempt_counts": {},
        "errors": [],
    }

    firestore_updates: list[dict] = []

    with (
        patch("app.services.firestore_service.get_project", return_value=project),
        patch("app.services.firestore_service.get_latest_structured_requirements", return_value=sr),
        patch("app.services.firestore_service.update_project", side_effect=lambda u, p, d: firestore_updates.append(d)),
        patch("app.services.firestore_service.set_project_status"),
        patch("app.ai.claude.agents.architect.ArchitectAgent.architect", return_value=(plan, [])),
        patch("app.services.template_service.load_stack_templates", return_value=_FAKE_TEMPLATES),
        patch("app.ai.claude.agents.generator.GeneratorAgent.generate_file", return_value="# ok\n"),
        patch("app.ai.claude.agents.tester.TesterAgent.run_tests", return_value=failing_test),
        patch("app.ai.claude.agents.debugger.DebuggerAgent.debug_and_fix", return_value=infra_skip),
    ):
        from app.pipeline.generation_orchestrator import GenerationOrchestrator
        result = GenerationOrchestrator().run_full_pipeline("uid1", "proj-infra")

    # Pipeline must succeed (not error) and return a ZIP
    assert result["status"] == "success", f"Expected success, got {result['status']}: {result['errors']}"
    assert result["zip_url"] is not None

    # Errors list must be non-empty (warning surfaced to caller)
    assert len(result["errors"]) > 0

    # Firestore must have been updated to ready_with_warnings (not failed)
    status_updates = [d["status"] for d in firestore_updates if "status" in d]
    assert ProjectStatus.ready_with_warnings in status_updates, (
        f"Expected ready_with_warnings in status updates, got: {status_updates}"
    )
    assert ProjectStatus.failed not in status_updates, (
        "Pipeline must NOT set failed for infrastructure errors"
    )


def test_orchestrator_test_failure_results_in_ready_with_warnings_with_zip(monkeypatch):
    """When tests fail and debug cannot fix, pipeline still packages a ZIP and sets ready_with_warnings."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    sr = _make_sr()
    blueprint = _make_blueprint()
    project = _make_project(blueprint)
    plan = _make_plan()

    failing_test = {
        "status": "failed",
        "errors": ["boot: Traceback (most recent call last): ..."],
        "passed_checks": {"install": "passed", "lint": "passed", "typecheck": "skipped", "boot": "failed"},
        "logs": {"boot": "Traceback (most recent call last): ..."},
    }
    # Debugger returns "error" — could not identify the file to fix.
    unfixable = {
        "status": "error",
        "fixed_files": {},
        "attempt_counts": {},
        "errors": ["Could not identify any file to fix"],
    }

    firestore_updates: list[dict] = []

    with (
        patch("app.services.firestore_service.get_project", return_value=project),
        patch("app.services.firestore_service.get_latest_structured_requirements", return_value=sr),
        patch("app.services.firestore_service.update_project", side_effect=lambda u, p, d: firestore_updates.append(d)),
        patch("app.services.firestore_service.set_project_status"),
        patch("app.ai.claude.agents.architect.ArchitectAgent.architect", return_value=(plan, [])),
        patch("app.services.template_service.load_stack_templates", return_value=_FAKE_TEMPLATES),
        patch("app.ai.claude.agents.generator.GeneratorAgent.generate_file", return_value="# ok\n"),
        patch("app.ai.claude.agents.tester.TesterAgent.run_tests", return_value=failing_test),
        patch("app.ai.claude.agents.debugger.DebuggerAgent.debug_and_fix", return_value=unfixable),
    ):
        from app.pipeline.generation_orchestrator import GenerationOrchestrator
        result = GenerationOrchestrator().run_full_pipeline("uid1", "proj-unfixable")

    # Pipeline must succeed and return a ZIP despite test failure
    assert result["status"] == "success", f"Expected success, got {result['status']}: {result['errors']}"
    assert result["zip_url"] is not None, "ZIP must be packaged even when tests fail"
    assert len(result["errors"]) > 0, "Warning must be surfaced to caller"

    status_updates = [d["status"] for d in firestore_updates if "status" in d]
    assert ProjectStatus.ready_with_warnings in status_updates, (
        f"Expected ready_with_warnings, got: {status_updates}"
    )
    assert ProjectStatus.failed not in status_updates, (
        "Test/debug failures must not mark the project as failed"
    )


def test_orchestrator_only_fails_on_catastrophic_errors(monkeypatch):
    """Generation step raising an exception is catastrophic — pipeline sets failed status."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    sr = _make_sr()
    blueprint = _make_blueprint()
    project = _make_project(blueprint)
    plan = _make_plan()

    firestore_updates: list[dict] = []

    with (
        patch("app.services.firestore_service.get_project", return_value=project),
        patch("app.services.firestore_service.get_latest_structured_requirements", return_value=sr),
        patch("app.services.firestore_service.update_project", side_effect=lambda u, p, d: firestore_updates.append(d)),
        patch("app.services.firestore_service.set_project_status"),
        patch("app.ai.claude.agents.architect.ArchitectAgent.architect", return_value=(plan, [])),
        patch("app.services.template_service.load_stack_templates", return_value=_FAKE_TEMPLATES),
        patch("app.ai.claude.agents.generator.GeneratorAgent.generate_file",
              side_effect=RuntimeError("LLM API unavailable")),
    ):
        from app.pipeline.generation_orchestrator import GenerationOrchestrator
        result = GenerationOrchestrator().run_full_pipeline("uid1", "proj-crash")

    assert result["status"] == "error", "Generation crash must result in error status"
    assert result["zip_url"] is None, "No ZIP should be delivered after a catastrophic crash"

    status_updates = [d["status"] for d in firestore_updates if "status" in d]
    assert ProjectStatus.failed in status_updates, (
        "Catastrophic generation failure must mark the project as failed"
    )
