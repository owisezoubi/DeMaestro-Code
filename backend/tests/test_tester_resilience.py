"""Tests for TesterAgent and DebuggerAgent resilience to missing tools / infrastructure errors."""
from unittest.mock import MagicMock, patch

from app.ai.claude.agents.debugger import DebuggerAgent
from app.ai.claude.agents.tester import TesterAgent
from app.models.generation_plan import FileToGenerate, GenerationPlan


def _make_plan(stack: str = "python-sqlite") -> GenerationPlan:
    return GenerationPlan(
        technology_stack=stack,
        files=[FileToGenerate(path="main.py", description="Main app", template="# main")],
        generation_order=["main.py"],
        notes="",
    )


def test_missing_tool_returns_skipped_not_failed(monkeypatch):
    """When all subprocess tools are missing, every check is 'skipped', never 'failed'."""
    monkeypatch.setattr("app.config.settings.mock_ai", False)

    generated_files = {"main.py": "print('hello')"}
    plan = _make_plan()

    with (
        patch("subprocess.run", side_effect=FileNotFoundError("No such file")),
        patch("subprocess.Popen", side_effect=FileNotFoundError("No such file")),
        patch(
            "app.ai.claude.agents.tester.TesterAgent._write_files_to_temp",
            return_value="/tmp/fake_demaestro_test",
        ),
    ):
        agent = TesterAgent()
        result = agent.run_tests(generated_files, plan)

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
    generated_files = {"main.py": "print('hello')"}
    plan = _make_plan()

    with patch("anthropic.Anthropic") as mock_anthropic:
        agent = DebuggerAgent()
        result = agent.debug_and_fix(test_results, generated_files, plan)

    mock_anthropic.assert_not_called()
    assert result["status"] == "skipped"
    assert result["fixed_files"] == {}
    assert result["errors"] == []


# ── multi-file extraction tests ───────────────────────────────────────────────


def test_debugger_extracts_multiple_files_from_lint_output():
    """_extract_files_from_error returns all unique files from flake8-style multi-line output."""
    agent = DebuggerAgent()
    lint_output = (
        "lint: ./backend/app/auth.py:225:16: W292 no newline at end of file\n"
        "./backend/app/database.py:56:42: W292 no newline at end of file\n"
        "./backend/app/main.py:1:1: F401 'asyncio' imported but unused\n"
        "./backend/app/database.py:78:1: E302 expected 2 blank lines\n"
    )
    files = agent._extract_files_from_error(lint_output)

    assert "backend/app/auth.py" in files
    assert "backend/app/database.py" in files
    assert "backend/app/main.py" in files
    # Duplicates are collapsed — database.py appears twice but should be listed once.
    assert files.count("backend/app/database.py") == 1


def test_debugger_extracts_empty_list_for_non_file_errors():
    """_extract_files_from_error returns [] for errors that reference no file paths."""
    agent = DebuggerAgent()
    error = "command not found: pip"
    files = agent._extract_files_from_error(error)
    assert files == []


def test_lint_passes_when_only_warnings(monkeypatch):
    """With --select=E9,F63,F7,F82, W292/F401 style warnings don't cause lint failure."""
    monkeypatch.setattr("app.config.settings.mock_ai", False)

    def _mock_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    with (
        patch("subprocess.run", side_effect=_mock_run),
        # boot not tested here — suppress it
        patch("subprocess.Popen", side_effect=FileNotFoundError("No such file")),
        patch(
            "app.ai.claude.agents.tester.TesterAgent._write_files_to_temp",
            return_value="/tmp/fake_demaestro_test",
        ),
    ):
        agent = TesterAgent()
        result = agent.run_tests({"main.py": "import os\nx = 1"}, _make_plan())

    assert result["passed_checks"]["lint"] == "passed", (
        "W-class and F401 warnings should not fail lint with --select=E9,F63,F7,F82"
    )
