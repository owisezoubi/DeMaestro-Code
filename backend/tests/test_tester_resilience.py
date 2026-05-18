"""Tests for TesterAgent/DebuggerAgent resilience and directory discovery."""
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.ai.claude.agents.debugger import DebuggerAgent
from app.ai.claude.agents.tester import TesterAgent, _find_backend_dir, _find_frontend_dir
from app.models.generation_plan import FileToGenerate, GenerationPlan

_FAKE_TMPDIR = Path("/tmp/fake_demaestro_test")


def _make_plan(stack: str = "python-sqlite") -> GenerationPlan:
    return GenerationPlan(
        technology_stack=stack,
        files=[FileToGenerate(path="main.py", description="Main app", template="# main")],
        generation_order=["main.py"],
        notes="",
    )


# ── directory discovery helpers ───────────────────────────────────────────────


def test_find_backend_dir_returns_backend_subdirectory(tmp_path):
    """_find_backend_dir prefers ./backend/ over root when both could match."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "requirements.txt").write_text("fastapi\n")
    assert _find_backend_dir(tmp_path) == tmp_path / "backend"


def test_find_backend_dir_returns_root_when_manifest_at_root(tmp_path):
    """_find_backend_dir falls back to root when requirements.txt is there."""
    (tmp_path / "requirements.txt").write_text("fastapi\n")
    assert _find_backend_dir(tmp_path) == tmp_path


def test_find_backend_dir_accepts_pyproject_toml(tmp_path):
    """_find_backend_dir recognises pyproject.toml as a valid manifest."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "pyproject.toml").write_text("[tool.poetry]\n")
    assert _find_backend_dir(tmp_path) == tmp_path / "backend"


def test_find_backend_dir_returns_none_when_no_manifest(tmp_path):
    """_find_backend_dir returns None when no Python manifest exists anywhere."""
    assert _find_backend_dir(tmp_path) is None


def test_find_frontend_dir_returns_frontend_subdirectory(tmp_path):
    """_find_frontend_dir prefers ./frontend/ when package.json lives there."""
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text("{}")
    assert _find_frontend_dir(tmp_path) == tmp_path / "frontend"


def test_find_frontend_dir_returns_root_when_package_at_root(tmp_path):
    """_find_frontend_dir falls back to root when package.json is at root."""
    (tmp_path / "package.json").write_text("{}")
    assert _find_frontend_dir(tmp_path) == tmp_path


def test_find_frontend_dir_returns_none_when_no_package(tmp_path):
    """_find_frontend_dir returns None when no package.json exists anywhere."""
    assert _find_frontend_dir(tmp_path) is None


# ── install directory discovery integration ───────────────────────────────────


def test_install_uses_discovered_backend_dir(tmp_path, monkeypatch):
    """_run_install calls pip with cwd=backend_dir, not cwd=tmpdir."""
    monkeypatch.setattr("app.config.settings.mock_ai", False)

    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    (backend_dir / "requirements.txt").write_text("fastapi\n")

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "Successfully installed"
    mock_result.stderr = ""

    with (
        patch("subprocess.run", return_value=mock_result) as mock_run,
        patch("subprocess.Popen", side_effect=FileNotFoundError),
    ):
        TesterAgent()._test_real(str(tmp_path), "python-sqlite")

    pip_calls = [c for c in mock_run.call_args_list if "pip" in " ".join(c.args[0])]
    assert len(pip_calls) == 1, "Expected exactly one pip install call"
    assert pip_calls[0].kwargs["cwd"] == backend_dir, (
        f"pip should run from {backend_dir}, got {pip_calls[0].kwargs['cwd']}"
    )


# ── missing-tool resilience ───────────────────────────────────────────────────


def test_missing_tool_returns_skipped_not_failed(monkeypatch):
    """When all subprocess tools are missing, every check is 'skipped', never 'failed'."""
    monkeypatch.setattr("app.config.settings.mock_ai", False)

    with (
        patch("subprocess.run", side_effect=FileNotFoundError("No such file")),
        patch("subprocess.Popen", side_effect=FileNotFoundError("No such file")),
        patch("app.ai.claude.agents.tester.TesterAgent._write_files_to_temp",
              return_value=str(_FAKE_TMPDIR)),
        patch("app.ai.claude.agents.tester._find_backend_dir", return_value=_FAKE_TMPDIR),
        patch("app.ai.claude.agents.tester._find_frontend_dir", return_value=None),
    ):
        result = TesterAgent().run_tests({"main.py": "print('hello')"}, _make_plan())

    for check, status in result["passed_checks"].items():
        assert status != "failed", f"Check '{check}' should be 'skipped', not 'failed'"

    assert result["status"] == "success"


def test_debugger_skips_infrastructure_errors():
    """DebuggerAgent returns 'skipped' without calling Claude when all errors are infrastructure."""
    test_results = {
        "status": "failed",
        "errors": [
            "install: No such file or directory: 'pip'",
            "lint: command not found: flake8",
        ],
        "logs": {},
        "passed_checks": {"install": "failed", "lint": "failed"},
    }

    with patch("anthropic.Anthropic") as mock_anthropic:
        result = DebuggerAgent().debug_and_fix(test_results, {"main.py": "x=1"}, _make_plan())

    mock_anthropic.assert_not_called()
    assert result["status"] == "skipped"
    assert result["fixed_files"] == {}
    assert result["errors"] == []


# ── infrastructure-error pattern recognition ─────────────────────────────────


def test_debugger_recognizes_wheel_build_failure_as_infrastructure():
    """Wheel-build errors are infrastructure — Debugger returns 'skipped', never calls Claude."""
    test_results = {
        "status": "failed",
        "errors": [
            "Failed building wheel for psycopg2-binary",
            "ERROR: Failed building wheel for psycopg2-binary",
        ],
        "logs": {},
        "passed_checks": {"install": "failed"},
    }

    with patch("anthropic.Anthropic") as mock_anthropic:
        result = DebuggerAgent().debug_and_fix(test_results, {"main.py": "x=1"}, _make_plan())

    mock_anthropic.assert_not_called()
    assert result["status"] == "skipped"
    assert result["fixed_files"] == {}


def test_debugger_recognizes_pip_dependency_resolution_failure_as_infrastructure():
    """pip dependency resolution failures are infrastructure — Debugger skips without calling Claude."""
    for error_text in [
        "ERESOLVE unable to resolve dependency tree",
        "ERROR: Could not find a version that satisfies the requirement flask==99.0.0",
        "ERROR: No matching distribution found for flask==99.0.0",
        "npm ERR! code ERESOLVE",
    ]:
        test_results = {
            "status": "failed",
            "errors": [error_text],
            "logs": {},
            "passed_checks": {"install": "failed"},
        }

        with patch("anthropic.Anthropic") as mock_anthropic:
            result = DebuggerAgent().debug_and_fix(test_results, {"main.py": "x=1"}, _make_plan())

        mock_anthropic.assert_not_called()
        assert result["status"] == "skipped", (
            f"Expected 'skipped' for error {error_text!r}, got {result['status']!r}"
        )


# ── multi-file extraction ─────────────────────────────────────────────────────


def test_debugger_extracts_multiple_files_from_lint_output():
    """_extract_files_from_error returns all unique files from flake8-style multi-line output."""
    lint_output = (
        "lint: ./backend/app/auth.py:225:16: W292 no newline at end of file\n"
        "./backend/app/database.py:56:42: W292 no newline at end of file\n"
        "./backend/app/main.py:1:1: F401 'asyncio' imported but unused\n"
        "./backend/app/database.py:78:1: E302 expected 2 blank lines\n"
    )
    files = DebuggerAgent()._extract_files_from_error(lint_output)

    assert "backend/app/auth.py" in files
    assert "backend/app/database.py" in files
    assert "backend/app/main.py" in files
    # database.py appears twice in the output but must be deduplicated.
    assert files.count("backend/app/database.py") == 1


def test_debugger_extracts_empty_list_for_non_file_errors():
    """_extract_files_from_error returns [] when the error has no file paths."""
    assert DebuggerAgent()._extract_files_from_error("command not found: pip") == []


# ── lint flag scope ───────────────────────────────────────────────────────────


def test_lint_passes_when_only_warnings(monkeypatch):
    """With --select=E9,F63,F7,F82, W292/F401 warnings don't fail lint."""
    monkeypatch.setattr("app.config.settings.mock_ai", False)

    def _mock_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    with (
        patch("subprocess.run", side_effect=_mock_run),
        patch("subprocess.Popen", side_effect=FileNotFoundError("No such file")),
        patch("app.ai.claude.agents.tester.TesterAgent._write_files_to_temp",
              return_value=str(_FAKE_TMPDIR)),
        patch("app.ai.claude.agents.tester._find_backend_dir", return_value=_FAKE_TMPDIR),
        patch("app.ai.claude.agents.tester._find_frontend_dir", return_value=None),
    ):
        result = TesterAgent().run_tests(
            {"main.py": "import os\nx = 1"}, _make_plan()
        )

    assert result["passed_checks"]["lint"] == "passed", (
        "W-class and F401 warnings must not fail lint with --select=E9,F63,F7,F82"
    )
