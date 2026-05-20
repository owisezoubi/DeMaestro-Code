"""Tests for TesterAgent/DebuggerAgent resilience and directory discovery."""
import subprocess
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

    pip_install_calls = [
        c for c in mock_run.call_args_list
        if "requirements.txt" in " ".join(str(a) for a in c.args[0])
    ]
    assert len(pip_install_calls) == 1, "Expected exactly one pip install -r requirements.txt call"
    assert pip_install_calls[0].kwargs["cwd"] == backend_dir, (
        f"pip should run from {backend_dir}, got {pip_install_calls[0].kwargs['cwd']}"
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


# ── isolated venv ─────────────────────────────────────────────────────────────


def test_install_creates_isolated_venv(tmp_path):
    """_run_install creates a .testenv venv in tmpdir before installing requirements."""
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    (backend_dir / "requirements.txt").write_text("fastapi\n")

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        _, _, venv_python = TesterAgent()._run_install(tmp_path, "python-sqlite")

    venv_calls = [
        c for c in mock_run.call_args_list
        if "venv" in c.args[0]
    ]
    assert len(venv_calls) == 1, "Expected exactly one python -m venv call"
    assert str(tmp_path / ".testenv") in " ".join(str(a) for a in venv_calls[0].args[0])

    assert venv_python is not None
    assert str(venv_python) == str(tmp_path / ".testenv" / "bin" / "python")


def test_boot_uses_venv_python_not_system_python(tmp_path):
    """_run_boot uses the venv python binary when the venv path exists."""
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    (backend_dir / "requirements.txt").write_text("fastapi\n")

    venv_python = tmp_path / ".testenv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()

    proc_mock = MagicMock()
    proc_mock.wait.side_effect = subprocess.TimeoutExpired(cmd="uvicorn", timeout=5)
    proc_mock.communicate.return_value = ("", "")

    with patch("subprocess.Popen", return_value=proc_mock) as mock_popen:
        status, _ = TesterAgent()._run_boot(tmp_path, "python-sqlite", venv_python=venv_python)

    assert status == "passed"
    cmd = mock_popen.call_args.args[0]
    assert cmd[0] == str(venv_python), f"Expected venv python as first arg, got {cmd[0]}"
    assert "-m" in cmd
    assert "uvicorn" in cmd


def test_install_falls_back_to_system_pip_when_venv_creation_fails(tmp_path):
    """When venv creation fails, _run_install falls back to system pip and sets venv_python=None."""
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    (backend_dir / "requirements.txt").write_text("fastapi\n")

    def _selective_run(cmd, **kwargs):
        if "venv" in cmd:
            raise FileNotFoundError("python not found")
        r = MagicMock()
        r.returncode = 0
        r.stdout = "ok"
        r.stderr = ""
        return r

    with patch("subprocess.run", side_effect=_selective_run):
        status, _, venv_python = TesterAgent()._run_install(tmp_path, "python-sqlite")

    assert venv_python is None, "Fallback path must not set venv_python"
    assert status == "passed"


# ── boot stderr exception detection ──────────────────────────────────────────


def test_boot_fails_when_stderr_has_sqlalchemy_exception(tmp_path):
    """Boot is failed when stderr contains a SQLAlchemy exception even if the process is still running."""
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    (backend_dir / "requirements.txt").write_text("fastapi\n")

    proc_mock = MagicMock()
    proc_mock.wait.side_effect = subprocess.TimeoutExpired(cmd="uvicorn", timeout=5)
    proc_mock.communicate.return_value = (
        "",
        "sqlalchemy.exc.ArgumentError: Mapper could not assemble any primary key columns\n",
    )

    with patch("subprocess.Popen", return_value=proc_mock):
        status, output = TesterAgent()._run_boot(tmp_path, "python-sqlite")

    assert status == "failed", "SQLAlchemy exception in stderr must fail boot"
    assert "sqlalchemy" in output.lower() or "exception" in output.lower()


def test_boot_fails_when_stderr_has_traceback(tmp_path):
    """Boot is failed when stderr contains a Python traceback."""
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    (backend_dir / "requirements.txt").write_text("fastapi\n")

    proc_mock = MagicMock()
    proc_mock.wait.side_effect = subprocess.TimeoutExpired(cmd="uvicorn", timeout=5)
    proc_mock.communicate.return_value = (
        "",
        "Traceback (most recent call last):\n  File 'main.py', line 1\nImportError: No module named 'foo'\n",
    )

    with patch("subprocess.Popen", return_value=proc_mock):
        status, _ = TesterAgent()._run_boot(tmp_path, "python-sqlite")

    assert status == "failed", "Traceback in stderr must fail boot"


def test_boot_passes_with_clean_stderr(tmp_path):
    """Boot passes when stderr contains only gRPC infrastructure noise (not exception patterns)."""
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    (backend_dir / "requirements.txt").write_text("fastapi\n")

    proc_mock = MagicMock()
    proc_mock.wait.side_effect = subprocess.TimeoutExpired(cmd="uvicorn", timeout=5)
    proc_mock.communicate.return_value = (
        "INFO: Started server process [1234]\nINFO: Uvicorn running on http://0.0.0.0:8001\n",
        "FD from fork parent\nINFO: Application startup complete.\n",
    )

    with patch("subprocess.Popen", return_value=proc_mock):
        status, _ = TesterAgent()._run_boot(tmp_path, "python-sqlite")

    assert status == "passed", "gRPC/infra noise in stderr must not fail boot"


# ── debugger traceback file extraction ───────────────────────────────────────


def test_debugger_extracts_files_from_python_traceback():
    """_extract_files_from_error parses 'File "...", line N' traceback lines."""
    traceback = (
        'Traceback (most recent call last):\n'
        '  File "/private/var/folders/xx/demaestro_test_abc123/backend/app/models.py", line 42, in <module>\n'
        '    mapper_registry.configure()\n'
        'sqlalchemy.exc.ArgumentError: Mapper could not assemble any primary key columns\n'
    )
    files = DebuggerAgent()._extract_files_from_error(traceback)
    assert "backend/app/models.py" in files


def test_debugger_skips_library_paths_in_traceback():
    """_extract_files_from_error excludes .testenv/site-packages paths — those aren't user code."""
    traceback = (
        'Traceback (most recent call last):\n'
        '  File "/tmp/demaestro_test_xxx/.testenv/lib/python3.13/site-packages/sqlalchemy/orm/mapper.py", line 100\n'
        '    raise ArgumentError(...)\n'
        'sqlalchemy.exc.ArgumentError: bad mapper\n'
    )
    files = DebuggerAgent()._extract_files_from_error(traceback)
    assert files == [], f"Library paths must be excluded, got: {files}"


def test_debugger_blind_fix_fallback_when_no_file_extractable(monkeypatch):
    """When no file path is extractable but the error is substantive, _blind_fix_attempt is called."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "mock_ai", False)

    test_results = {
        "status": "failed",
        "errors": ["Error: Mapper could not assemble any primary key columns for table 'items'"],
        "logs": {},
        "passed_checks": {"boot": "failed"},
    }

    with patch.object(DebuggerAgent, "_blind_fix_attempt", return_value=None) as mock_blind:
        result = DebuggerAgent().debug_and_fix(
            test_results, {"backend/app/main.py": "x = 1\n"}, _make_plan()
        )

    mock_blind.assert_called_once()
    assert result["status"] == "skipped"
    assert "Could not auto-fix" in result.get("reason", "")
