"""Tests for TesterAgent and DebuggerAgent resilience to missing tools / infrastructure errors."""
from unittest.mock import patch

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
