"""Tests for Claude-backed agents and GenerationOrchestrator.
No real API, Firestore, or Docker calls are made."""
import subprocess
from unittest.mock import MagicMock, patch

from app.ai.claude.agents.architect import ArchitectAgent
from app.ai.claude.agents.debugger import DebuggerAgent
from app.ai.claude.agents.deployer import DeployerAgent
from app.ai.claude.agents.generator import GeneratorAgent
from app.ai.claude.agents.tester import TesterAgent
from app.ai.claude.agents.verifier import VerifierAgent
from app.ai.gemini.agents.blueprint import APIRoute, BlueprintResponse, DatabaseTable, FrontendPage
from app.models.generation_plan import FileToGenerate, GenerationPlan
from app.models.project import ProjectMeta, ProjectStatus
from app.models.structured_requirements import (
    Entity,
    RequirementCategory,
    StructuredRequirements,
    UserRequirement,
)


# ── shared fixtures ───────────────────────────────────────────────────────────

def _make_sr() -> StructuredRequirements:
    return StructuredRequirements(
        app_name="WorkoutTracker",
        summary="A fitness tracking app.",
        entities=[Entity(name="Workout", description="A workout session", fields=["id", "type", "duration"])],
        user_requirements=[
            UserRequirement(
                id="UR-01",
                statement="A logged-in user can log a new workout session.",
                rationale="Core feature.",
                acceptance_criteria=["POST /workouts returns 201 with valid data"],
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
                name="Workouts",
                columns=[{"name": "id", "type": "uuid"}, {"name": "type", "type": "text"}],
                description="Stores workout records",
            )
        ],
        api_routes=[
            APIRoute(method="GET", path="/api/workouts", description="List workouts",
                     request_schema="None", response_schema="list"),
            APIRoute(method="POST", path="/api/workouts", description="Create workout",
                     request_schema="type, duration", response_schema="workout object"),
        ],
        frontend_pages=[
            FrontendPage(name="Dashboard", route="/dashboard", purpose="Overview",
                         key_components=["StatsCard"]),
        ],
        technology_stack_notes="React + FastAPI + PostgreSQL",
    )


def _make_project(blueprint: BlueprintResponse) -> ProjectMeta:
    return ProjectMeta(
        id="proj-test",
        name="WorkoutTracker",
        status=ProjectStatus.approved,
        blueprint=blueprint.model_dump(),
    )


def _make_plan() -> GenerationPlan:
    return GenerationPlan(
        technology_stack="python-postgres",
        files=[
            FileToGenerate(path="backend/app/models.py", description="DB models.",
                           depends_on=[], template="db_schema"),
            FileToGenerate(path="backend/app/routes/workouts.py", description="Workout routes.",
                           depends_on=["backend/app/models.py"], template="fastapi_route"),
        ],
        generation_order=["backend/app/models.py", "backend/app/routes/workouts.py"],
        notes="test plan",
    )


# ── ArchitectAgent ────────────────────────────────────────────────────────────

def test_architect_prompt_renders_without_format_error():
    """The architect prompt must not raise ValueError from unescaped braces in f-strings."""
    agent = ArchitectAgent()
    context = {
        "structured_requirements": _make_sr().model_dump(),
        "blueprint": _make_blueprint().model_dump(),
    }
    try:
        prompt = agent._build_architect_prompt(context)
    except ValueError as exc:
        if "Invalid format specifier" in str(exc):
            raise AssertionError(
                "f-string brace bug: JSON examples inside _build_architect_prompt "
                "are being parsed as Python format specifiers. "
                "Convert the static JSON block to a plain (non-f) string."
            ) from exc
        raise
    assert "CHECKLIST OUTPUT REQUIREMENT" in prompt, \
        "Checklist block is missing from the architect prompt"
    assert "model.User" in prompt, "JSON example for model item is missing"
    assert "route.POST.api.orders" in prompt, "JSON example for route item is missing"


def test_architect_outputs_valid_generation_plan(monkeypatch):
    """ArchitectAgent produces a valid GenerationPlan in mock mode."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    plan, checklist = ArchitectAgent().architect(_make_sr(), _make_blueprint())

    assert isinstance(plan, GenerationPlan)
    assert plan.technology_stack == "python-postgres"
    assert len(plan.files) > 0
    assert len(plan.generation_order) == len(plan.files)
    assert isinstance(checklist, list)


def test_architect_generation_order_matches_files(monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    plan, _ = ArchitectAgent().architect(_make_sr(), _make_blueprint())
    file_paths = {f.path for f in plan.files}
    for path in plan.generation_order:
        assert path in file_paths


def test_architect_includes_backend_and_frontend_files(monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    plan, _ = ArchitectAgent().architect(_make_sr(), _make_blueprint())
    paths = [f.path for f in plan.files]
    assert any("backend" in p or ".py" in p for p in paths)
    assert any("frontend" in p or ".jsx" in p or ".tsx" in p for p in paths)


# ── GeneratorAgent ────────────────────────────────────────────────────────────

def test_generator_respects_templates(monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    ftg = FileToGenerate(path="frontend/src/pages/Dashboard.jsx",
                         description="Dashboard.", depends_on=[], template="react_page")
    content = GeneratorAgent().generate_file(ftg, _make_plan(), _make_blueprint(), {})

    assert "useQuery" in content
    assert "useState" in content


def test_generator_fastapi_template_contains_router(monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    ftg = FileToGenerate(path="backend/app/routes/workouts.py",
                         description="Workout CRUD.", depends_on=[], template="fastapi_route")
    content = GeneratorAgent().generate_file(ftg, _make_plan(), _make_blueprint(), {})

    assert "APIRouter" in content
    assert "router" in content


def test_generator_none_template_returns_nonempty_content(monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    ftg = FileToGenerate(path="backend/app/config.py", description="App configuration.",
                         depends_on=[], template="none")
    content = GeneratorAgent().generate_file(ftg, _make_plan(), _make_blueprint(), {})
    assert len(content) > 0


def test_generator_includes_dependencies_in_prompt():
    dep_content = "class Workout(Base):\n    __tablename__ = 'workouts'"
    ftg = FileToGenerate(path="backend/app/routes/workouts.py",
                         description="Workout routes.",
                         depends_on=["backend/app/models.py"], template="fastapi_route")
    prompt = GeneratorAgent()._build_generate_prompt(
        ftg, _make_plan(), _make_blueprint(),
        {"backend/app/models.py": dep_content}, template=None,
    )
    assert dep_content in prompt
    assert "backend/app/models.py" in prompt


# ── TesterAgent ───────────────────────────────────────────────────────────────

def test_tester_mock_mode_all_checks_pass(monkeypatch):
    """TesterAgent in mock mode returns success when all .py files are valid."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    files = {
        "backend/app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
        "backend/app/models.py": "x = 1\n",
    }
    result = TesterAgent().run_tests(files, _make_plan())

    assert result["status"] == "success"
    assert result["passed_checks"]["install"] == "passed"
    assert result["passed_checks"]["lint"] == "passed"
    assert result["passed_checks"]["typecheck"] == "passed"
    assert result["passed_checks"]["boot"] == "passed"


def test_tester_mock_mode_four_checks_present(monkeypatch):
    """TesterAgent always returns all four check keys."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    result = TesterAgent().run_tests({"a.py": "x = 1\n"}, _make_plan())
    assert set(result["passed_checks"].keys()) == {"install", "lint", "typecheck", "boot"}


def test_tester_detects_syntax_errors(monkeypatch):
    """TesterAgent fails lint when a .py file has a SyntaxError."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    broken_py = "def broken(\n    pass\n"  # invalid: missing closing paren before body
    files = {"backend/app/routes/workouts.py": broken_py}
    result = TesterAgent().run_tests(files, _make_plan())

    assert result["status"] == "failed"
    assert result["passed_checks"]["lint"] == "failed"
    assert any("workouts.py" in e for e in result["errors"])


def test_tester_non_python_files_do_not_fail_lint(monkeypatch):
    """TesterAgent ignores non-.py files in lint check."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    files = {"frontend/src/App.jsx": "const x = <div>; not real jsx"}
    result = TesterAgent().run_tests(files, _make_plan())

    assert result["passed_checks"]["lint"] == "passed"


# ── DebuggerAgent ─────────────────────────────────────────────────────────────

def test_debugger_fixes_one_file_per_attempt(monkeypatch):
    """DebuggerAgent returns fixed_files with one entry and increments attempt count."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    test_results = {
        "status": "failed",
        "errors": ["backend/app/routes/workouts.py: SyntaxError at line 1: invalid syntax"],
        "logs": {"lint": "error"},
        "passed_checks": {"install": "passed", "lint": "failed", "typecheck": "passed", "boot": "passed"},
    }
    generated_files = {"backend/app/routes/workouts.py": "def broken("}

    result = DebuggerAgent().debug_and_fix(test_results, generated_files, _make_plan())

    assert result["status"] == "fixed"
    assert "backend/app/routes/workouts.py" in result["fixed_files"]
    assert result["attempt_counts"]["backend/app/routes/workouts.py"] == 1
    assert result["errors"] == []


def test_debugger_mock_fix_prepends_comment(monkeypatch):
    """Mock fix prepends a '# FIXED:' comment to the original content."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    test_results = {
        "status": "failed",
        "errors": ["backend/app/models.py: SyntaxError at line 3: unexpected EOF"],
        "logs": {},
        "passed_checks": {"install": "passed", "lint": "failed", "typecheck": "passed", "boot": "passed"},
    }
    original = "class Model:\n    pass\n"
    generated_files = {"backend/app/models.py": original}

    result = DebuggerAgent().debug_and_fix(test_results, generated_files, _make_plan())

    fixed = result["fixed_files"]["backend/app/models.py"]
    assert fixed.startswith("# FIXED:")
    assert original in fixed


def test_debugger_respects_3_attempt_cap(monkeypatch):
    """DebuggerAgent returns error when attempt_count >= 3 for the file."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    test_results = {
        "status": "failed",
        "errors": ["backend/app/routes/workouts.py: SyntaxError at line 1: invalid syntax"],
        "logs": {},
        "passed_checks": {"install": "passed", "lint": "failed", "typecheck": "passed", "boot": "passed"},
    }
    generated_files = {"backend/app/routes/workouts.py": "def broken("}
    # already at max attempts
    attempt_count = {"backend/app/routes/workouts.py": 3}

    result = DebuggerAgent().debug_and_fix(test_results, generated_files, _make_plan(), attempt_count)

    assert result["status"] == "error"
    assert any("Max" in e for e in result["errors"])


def test_debugger_no_errors_returns_success(monkeypatch):
    """DebuggerAgent returns success immediately if test_results has no errors."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    result = DebuggerAgent().debug_and_fix(
        {"status": "success", "errors": [], "logs": {}, "passed_checks": {}},
        {},
        _make_plan(),
    )
    assert result["status"] == "success"
    assert result["fixed_files"] == {}


# ── VerifierAgent ─────────────────────────────────────────────────────────────

def test_verifier_checks_routes_exist():
    """VerifierAgent flags missing API routes when no matching files are passed."""
    result = VerifierAgent().verify({}, _make_plan(), _make_blueprint())

    assert result["status"] == "fail"
    assert any("GET /api/workouts" in issue for issue in result["issues"])
    assert any("POST /api/workouts" in issue for issue in result["issues"])


def test_verifier_passes_when_all_routes_present():
    """VerifierAgent passes route check when decorators are present."""
    files = {
        "backend/app/routes/workouts.py": (
            "from fastapi import APIRouter\nrouter = APIRouter()\n"
            "@router.get('/api/workouts')\ndef list_workouts(): pass\n"
            "@router.post('/api/workouts')\ndef create_workout(): pass\n"
        ),
        "backend/app/models.py": "class Workouts:\n    pass\n",
    }
    result = VerifierAgent().verify(files, _make_plan(), _make_blueprint())
    route_issues = [i for i in result["issues"] if "Route" in i]
    assert route_issues == []


def test_verifier_checks_imports():
    """VerifierAgent flags route files missing a FastAPI import."""
    files = {
        "backend/app/routes/workouts.py": (
            "@router.get('/api/workouts')\n"
            "@router.post('/api/workouts')\n"
            "def handler(): pass\n"
        ),
        "backend/app/models.py": "class Workouts:\n    pass\n",
    }
    result = VerifierAgent().verify(files, _make_plan(), _make_blueprint())
    import_issues = [i for i in result["issues"] if "FastAPI" in i]
    assert len(import_issues) >= 1


def test_verifier_flags_missing_db_models():
    """VerifierAgent flags when no model file mentions a blueprint table."""
    files = {
        "backend/app/routes/workouts.py": (
            "from fastapi import APIRouter\nrouter = APIRouter()\n"
            "@router.get('/api/workouts')\n@router.post('/api/workouts')\n"
            "def h(): pass\n"
        ),
        "backend/app/models.py": "x = 1\n",  # no table name 'Workouts'
    }
    result = VerifierAgent().verify(files, _make_plan(), _make_blueprint())
    db_issues = [i for i in result["issues"] if "model" in i.lower() or "schema" in i.lower()]
    assert len(db_issues) >= 1


def test_verifier_skips_empty_init_files():
    """Empty __init__.py files in routes/ must not trigger a missing-FastAPI-import issue."""
    files = {
        "backend/app/__init__.py": "",
        "backend/app/routes/__init__.py": "",
        "backend/app/routes/workouts.py": (
            "from fastapi import APIRouter\nrouter = APIRouter()\n"
            "@router.get('/api/workouts')\n@router.post('/api/workouts')\ndef h(): pass\n"
        ),
        "backend/app/models.py": "class Workouts:\n    pass\n",
    }
    result = VerifierAgent().verify(files, _make_plan(), _make_blueprint())
    import_issues = [i for i in result["issues"] if "FastAPI" in i and "__init__" in i]
    assert import_issues == [], f"__init__.py must not trigger FastAPI import check: {import_issues}"


def test_verifier_skips_non_route_files():
    """Utility modules inside routes/ that have no route patterns must not require FastAPI import."""
    files = {
        "backend/app/routes/utils.py": (
            "def format_response(data):\n    return {'data': data, 'ok': True}\n" * 3
        ),
        "backend/app/routes/workouts.py": (
            "from fastapi import APIRouter\nrouter = APIRouter()\n"
            "@router.get('/api/workouts')\n@router.post('/api/workouts')\ndef h(): pass\n"
        ),
        "backend/app/models.py": "class Workouts:\n    pass\n",
    }
    result = VerifierAgent().verify(files, _make_plan(), _make_blueprint())
    import_issues = [i for i in result["issues"] if "FastAPI" in i and "utils" in i]
    assert import_issues == [], f"Non-route utility file must not require FastAPI import: {import_issues}"


def test_verifier_flags_route_file_missing_fastapi_import():
    """A route file that uses @router. decorators but has no FastAPI import must be flagged."""
    files = {
        "backend/app/routes/notes.py": (
            "router = APIRouter()\n"
            "@router.get('/notes')\ndef list_notes(): return []\n"
            "@router.post('/notes')\ndef create_note(): return {}\n"
        ),
        "backend/app/models.py": "class Workouts:\n    pass\n",
    }
    result = VerifierAgent().verify(files, _make_plan(), _make_blueprint())
    import_issues = [i for i in result["issues"] if "FastAPI" in i]
    assert len(import_issues) >= 1, "Route file using APIRouter without import must be flagged"


# ── DeployerAgent ─────────────────────────────────────────────────────────────

def test_deployer_mock_mode_returns_zip_url(monkeypatch):
    """DeployerAgent mock mode returns status=ready with a zip_url."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    files = {
        "backend/app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
        "frontend/src/App.jsx": "export default function App() { return null; }\n",
    }
    result = DeployerAgent().deploy("uid123", "proj-test", files, _make_plan())

    assert result["status"] == "ready"
    assert result["zip_url"] is not None
    assert "proj-test" in result["zip_url"]


# ── GenerationOrchestrator ────────────────────────────────────────────────────

def test_generation_orchestrator_runs_full_pipeline(monkeypatch):
    """Orchestrator run_generation (W5-P1) architect → generate → store."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    sr = _make_sr()
    blueprint = _make_blueprint()
    project = _make_project(blueprint)

    with (
        patch("app.services.firestore_service.get_project", return_value=project),
        patch("app.services.firestore_service.get_latest_structured_requirements", return_value=sr),
        patch("app.services.firestore_service.update_project") as mock_update,
        patch("app.services.firestore_service.set_project_status"),
    ):
        from app.pipeline.generation_orchestrator import GenerationOrchestrator

        result = GenerationOrchestrator().run_generation("uid123", "proj-test")

    assert result["status"] == "success"
    assert len(result["generated_files"]) > 0
    assert result["plan"] is not None
    assert result["errors"] == []

    mock_update.assert_called_once()
    payload = mock_update.call_args[0][2]
    assert "generated_files" in payload
    assert "generation_plan" in payload
    assert payload["status"] == ProjectStatus.generated


def test_generation_orchestrator_errors_on_missing_project(monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    with (
        patch("app.services.firestore_service.get_project", return_value=None),
        patch("app.services.firestore_service.set_project_status"),
    ):
        from app.pipeline.generation_orchestrator import GenerationOrchestrator

        result = GenerationOrchestrator().run_generation("uid123", "bad-proj")

    assert result["status"] == "error"
    assert len(result["errors"]) > 0


def test_generation_orchestrator_errors_on_missing_blueprint(monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    project_no_bp = ProjectMeta(id="proj-test", name="TestApp",
                                status=ProjectStatus.approved, blueprint=None)
    with (
        patch("app.services.firestore_service.get_project", return_value=project_no_bp),
        patch("app.services.firestore_service.get_latest_structured_requirements", return_value=_make_sr()),
        patch("app.services.firestore_service.set_project_status"),
    ):
        from app.pipeline.generation_orchestrator import GenerationOrchestrator

        result = GenerationOrchestrator().run_generation("uid123", "proj-test")

    assert result["status"] == "error"
    assert any("blueprint" in e.lower() for e in result["errors"])


def test_orchestrator_full_pipeline_architect_to_deploy(monkeypatch):
    """run_full_pipeline flows through all states in mock mode."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    sr = _make_sr()
    blueprint = _make_blueprint()
    project = _make_project(blueprint)

    # Build files that will pass the verifier
    generated_files_result = {
        "backend/app/models.py": "class Workouts:\n    pass\n",
        "backend/app/routes/workouts.py": (
            "from fastapi import APIRouter\nrouter = APIRouter()\n"
            "@router.get('/api/workouts')\n@router.post('/api/workouts')\n"
            "def h(): pass\n"
        ),
    }

    with (
        patch("app.services.firestore_service.get_project", return_value=project),
        patch("app.services.firestore_service.get_latest_structured_requirements", return_value=sr),
        patch("app.services.firestore_service.update_project") as mock_update,
        patch("app.services.firestore_service.set_project_status") as mock_set_status,
        # Stub architect and generator so we control the files
        patch(
            "app.ai.claude.agents.architect.ArchitectAgent.architect",
            return_value=(_make_plan(), []),
        ),
        patch(
            "app.ai.claude.agents.generator.GeneratorAgent.generate_file",
            side_effect=lambda ftg, *a, **kw: generated_files_result.get(ftg.path, "# mock\n"),
        ),
    ):
        from app.pipeline.generation_orchestrator import GenerationOrchestrator

        result = GenerationOrchestrator().run_full_pipeline("uid123", "proj-test")

    assert result["status"] == "success"
    assert result["zip_url"] is not None
    assert result["errors"] == []

    # Verify state transitions occurred (verifying is skipped in mock mode)
    set_statuses = [call.args[2] for call in mock_set_status.call_args_list]
    assert ProjectStatus.generating in set_statuses
    assert ProjectStatus.testing in set_statuses
    assert ProjectStatus.packaging in set_statuses

    # Final update should include current_stage = ready
    final_update = mock_update.call_args_list[-1].args[2]
    assert final_update["current_stage"] == "ready"


def test_orchestrator_full_pipeline_debug_loop_fires_on_test_failure(monkeypatch):
    """When tests fail once then pass, the debug loop runs exactly once."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    sr = _make_sr()
    blueprint = _make_blueprint()
    project = _make_project(blueprint)

    generated_files_result = {
        "backend/app/models.py": "class Workouts:\n    pass\n",
        "backend/app/routes/workouts.py": (
            "from fastapi import APIRouter\nrouter = APIRouter()\n"
            "@router.get('/api/workouts')\n@router.post('/api/workouts')\n"
            "def h(): pass\n"
        ),
    }

    # Tester fails on first call, passes on second
    fail_result = {
        "status": "failed",
        "errors": ["backend/app/models.py: SyntaxError at line 1: invalid syntax"],
        "logs": {"lint": "error"},
        "passed_checks": {"install": "passed", "lint": "failed", "typecheck": "passed", "boot": "passed"},
    }
    pass_result = {
        "status": "success",
        "errors": [],
        "logs": {"install": "ok", "lint": "ok", "typecheck": "ok", "boot": "ok"},
        "passed_checks": {"install": "passed", "lint": "passed", "typecheck": "passed", "boot": "passed"},
    }

    call_count = {"n": 0}

    def mock_test(files, plan):
        call_count["n"] += 1
        return fail_result if call_count["n"] == 1 else pass_result

    with (
        patch("app.services.firestore_service.get_project", return_value=project),
        patch("app.services.firestore_service.get_latest_structured_requirements", return_value=sr),
        patch("app.services.firestore_service.update_project"),
        patch("app.services.firestore_service.set_project_status"),
        patch("app.ai.claude.agents.architect.ArchitectAgent.architect", return_value=(_make_plan(), [])),
        patch(
            "app.ai.claude.agents.generator.GeneratorAgent.generate_file",
            side_effect=lambda ftg, *a, **kw: generated_files_result.get(ftg.path, "# mock\n"),
        ),
        patch.object(TesterAgent, "run_tests", side_effect=mock_test),
    ):
        from app.pipeline.generation_orchestrator import GenerationOrchestrator

        result = GenerationOrchestrator().run_full_pipeline("uid123", "proj-test")

    assert result["status"] == "success"
    assert call_count["n"] == 2  # tester called twice: fail then pass


def test_tester_runs_install_lint_typecheck_boot(monkeypatch):
    """TesterAgent real mode calls install/lint/typecheck as subprocess.run and boot as Popen."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", False)

    files = {"requirements.txt": "fastapi\n", "main.py": "x = 1\n"}

    mock_proc_result = MagicMock()
    mock_proc_result.returncode = 0
    mock_proc_result.stdout = "ok"
    mock_proc_result.stderr = ""

    mock_proc = MagicMock()
    # TimeoutExpired means the process is still running = boot success
    mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="uvicorn", timeout=5)
    mock_proc.terminate.return_value = None

    with (
        patch("subprocess.run", return_value=mock_proc_result) as mock_run,
        patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
    ):
        result = TesterAgent().run_tests(files, _make_plan())

    assert result["status"] == "success"
    assert result["passed_checks"]["install"] == "passed"
    assert result["passed_checks"]["lint"] == "passed"
    assert result["passed_checks"]["typecheck"] == "passed"
    assert result["passed_checks"]["boot"] == "passed"

    # venv_create + pip_upgrade + pip_install + ruff_autofix + flake8 + typecheck = 6 subprocess.run calls
    assert mock_run.call_count == 6
    # boot = 1 Popen call
    mock_popen.assert_called_once()

    # Verify the python-postgres commands
    run_cmds = [call.args[0] for call in mock_run.call_args_list]
    assert any("pip" in " ".join(c) for c in run_cmds)
    assert any("flake8" in " ".join(c) for c in run_cmds)
    assert any("mypy" in " ".join(c) for c in run_cmds)
    boot_cmd = mock_popen.call_args.args[0]
    assert "uvicorn" in " ".join(boot_cmd)


def test_full_pipeline_architect_to_deploy(monkeypatch):
    """run_full_pipeline flows architect → generate → test → verify → deploy in mock mode."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", True)

    sr = _make_sr()
    blueprint = _make_blueprint()
    project = _make_project(blueprint)

    generated_files_result = {
        "backend/app/models.py": "class Workouts:\n    pass\n",
        "backend/app/routes/workouts.py": (
            "from fastapi import APIRouter\nrouter = APIRouter()\n"
            "@router.get('/api/workouts')\n@router.post('/api/workouts')\n"
            "def h(): pass\n"
        ),
    }

    plan = GenerationPlan(
        technology_stack="python-postgres",
        files=[
            FileToGenerate(path="backend/app/models.py", description="DB models.", template="db_schema"),
            FileToGenerate(path="backend/app/routes/workouts.py", description="Routes.", template="fastapi_route"),
        ],
        generation_order=["backend/app/models.py", "backend/app/routes/workouts.py"],
        notes="",
    )

    with (
        patch("app.services.firestore_service.get_project", return_value=project),
        patch("app.services.firestore_service.get_latest_structured_requirements", return_value=sr),
        patch("app.services.firestore_service.update_project"),
        patch("app.services.firestore_service.set_project_status"),
        patch("app.ai.claude.agents.architect.ArchitectAgent.architect", return_value=(plan, [])),
        patch(
            "app.ai.claude.agents.generator.GeneratorAgent.generate_file",
            side_effect=lambda ftg, *a, **kw: generated_files_result.get(ftg.path, "# mock\n"),
        ),
    ):
        from app.pipeline.generation_orchestrator import GenerationOrchestrator

        result = GenerationOrchestrator().run_full_pipeline("uid123", "proj-test")

    assert result["status"] == "success"
    assert len(result["generated_files"]) >= 2
    assert "backend/app/models.py" in result["generated_files"]
    assert "backend/app/routes/workouts.py" in result["generated_files"]
    assert result["zip_url"] is not None
    assert result["errors"] == []
