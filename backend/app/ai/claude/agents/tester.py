"""TesterAgent — runs tests on generated code. Pure Python, no LLM."""
import ast
import os
import subprocess
import tempfile

import structlog

from app.config import settings
from app.models.generation_plan import GenerationPlan

log = structlog.get_logger("TesterAgent")


class TesterAgent:
    """Runs install / lint / typecheck / boot checks on generated code.

    In mock mode: skips subprocess and Docker; uses in-process ast.parse for lint.
    In real mode: runs subprocess commands in a temp directory.
    """

    def run_tests(self, generated_files: dict[str, str], plan: GenerationPlan) -> dict:
        """Run full test suite (install / lint / typecheck / boot).

        Returns: { status, errors, logs, passed_checks }
        """
        log.info("test.start", num_files=len(generated_files), stack=plan.technology_stack)

        if settings.mock_ai:
            return self._test_mock(generated_files, plan)

        try:
            project_dir = self._write_files_to_temp(generated_files)
            return self._test_real(project_dir, plan.technology_stack)
        except Exception as e:
            log.error("test.error", error=str(e))
            return {
                "status": "error",
                "errors": [str(e)],
                "logs": {},
                "passed_checks": {"install": False, "lint": False, "typecheck": False, "boot": False},
            }

    # ── mock path ────────────────────────────────────────────────────────────

    def _test_mock(self, generated_files: dict[str, str], plan: GenerationPlan) -> dict:
        """In-process checks — no subprocesses, no Docker."""
        passed_checks: dict[str, bool] = {}
        logs: dict[str, str] = {}
        errors: list[str] = []

        # install: always passes (can't actually install in mock mode)
        passed_checks["install"] = True
        logs["install"] = "mock: skipped"

        # lint: use ast.parse for Python files, always pass for others
        lint_errors: list[str] = []
        for path, content in generated_files.items():
            if path.endswith(".py"):
                try:
                    ast.parse(content)
                except SyntaxError as exc:
                    lint_errors.append(f"{path}: SyntaxError at line {exc.lineno}: {exc.msg}")
        passed_checks["lint"] = not lint_errors
        logs["lint"] = "ok" if not lint_errors else "; ".join(lint_errors)
        errors.extend(lint_errors)

        # typecheck: always passes in mock mode
        passed_checks["typecheck"] = True
        logs["typecheck"] = "mock: skipped"

        # boot: always passes in mock mode
        passed_checks["boot"] = True
        logs["boot"] = "mock: skipped"

        status = "success" if all(passed_checks.values()) else "failed"
        log.info("test.mock.done", status=status, passed_checks=passed_checks)
        return {"status": status, "errors": errors, "logs": logs, "passed_checks": passed_checks}

    # ── real path ─────────────────────────────────────────────────────────────

    def _write_files_to_temp(self, generated_files: dict[str, str]) -> str:
        tmpdir = tempfile.mkdtemp(prefix="demaestro_test_")
        for file_path, content in generated_files.items():
            full_path = os.path.join(tmpdir, file_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w") as f:
                f.write(content)
        log.info("write_files_to_temp", tmpdir=tmpdir)
        return tmpdir

    def _test_real(self, project_dir: str, stack: str) -> dict:
        passed_checks: dict[str, bool] = {}
        logs: dict[str, str] = {}

        install_ok, install_log = self._run_install(project_dir, stack)
        passed_checks["install"] = install_ok
        logs["install"] = install_log

        lint_ok, lint_log = self._run_lint(project_dir, stack)
        passed_checks["lint"] = lint_ok
        logs["lint"] = lint_log

        typecheck_ok, typecheck_log = self._run_typecheck(project_dir, stack)
        passed_checks["typecheck"] = typecheck_ok
        logs["typecheck"] = typecheck_log

        boot_ok, boot_log = self._run_boot(project_dir, stack)
        passed_checks["boot"] = boot_ok
        logs["boot"] = boot_log

        errors = [
            f"{check}: {logs[check]}"
            for check, ok in passed_checks.items()
            if not ok
        ]
        status = "success" if not errors else "failed"
        log.info("test.real.done", status=status, passed_checks=passed_checks)
        return {"status": status, "errors": errors, "logs": logs, "passed_checks": passed_checks}

    def _run_install(self, project_dir: str, stack: str) -> tuple[bool, str]:
        try:
            if "python" in stack:
                cmd = ["python", "-m", "pip", "install", "-r", "requirements.txt"]
            else:
                cmd = ["npm", "install"]
            result = subprocess.run(cmd, cwd=project_dir, capture_output=True, timeout=120, text=True)
            passed = result.returncode == 0
            log.info("test_install", passed=passed, returncode=result.returncode)
            return passed, (result.stdout + result.stderr)[:500]
        except subprocess.TimeoutExpired:
            log.warning("test_install.timeout")
            return False, "timeout"

    def _run_lint(self, project_dir: str, stack: str) -> tuple[bool, str]:
        try:
            if "python" in stack:
                cmd = ["flake8", ".", "--max-line-length=120", "--ignore=E501,W503"]
            else:
                cmd = ["npx", "eslint", "src/", "--max-warnings=0"]
            result = subprocess.run(cmd, cwd=project_dir, capture_output=True, timeout=60, text=True)
            passed = result.returncode == 0
            log.info("test_lint", passed=passed)
            return passed, (result.stdout + result.stderr)[:500]
        except subprocess.TimeoutExpired:
            log.warning("test_lint.timeout")
            return False, "timeout"

    def _run_typecheck(self, project_dir: str, stack: str) -> tuple[bool, str]:
        try:
            if "python" in stack:
                cmd = ["mypy", ".", "--ignore-missing-imports"]
            else:
                cmd = ["npm", "run", "typecheck"]
            result = subprocess.run(cmd, cwd=project_dir, capture_output=True, timeout=60, text=True)
            passed = result.returncode == 0
            log.info("test_typecheck", passed=passed)
            return passed, (result.stdout + result.stderr)[:500]
        except subprocess.TimeoutExpired:
            log.warning("test_typecheck.timeout")
            return False, "timeout"

    def _run_boot(self, project_dir: str, stack: str) -> tuple[bool, str]:
        try:
            if "python" in stack:
                cmd = ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
            else:
                cmd = ["npm", "run", "dev"]
            proc = subprocess.Popen(
                cmd, cwd=project_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            try:
                proc.wait(timeout=5)
                passed = False  # exited early = crash
                output = "process exited early"
            except subprocess.TimeoutExpired:
                passed = True  # still running = boot ok
                proc.terminate()
                output = "booted ok"
            log.info("test_boot", passed=passed)
            return passed, output
        except Exception as exc:
            log.error("test_boot.error", error=str(exc))
            return False, str(exc)
