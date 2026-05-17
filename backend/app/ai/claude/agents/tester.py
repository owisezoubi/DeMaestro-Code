"""TesterAgent — runs tests on generated code. Pure Python, no LLM."""
import ast
import os
import subprocess
import tempfile

import structlog

from app.config import settings
from app.models.generation_plan import GenerationPlan

log = structlog.get_logger("TesterAgent")

# Statuses: "passed", "failed", "skipped"
# Skipped means the tool was absent; never enters the debug loop.


class TesterAgent:
    """Runs install / lint / typecheck / boot checks on generated code.

    In mock mode: skips subprocess and Docker; uses in-process ast.parse for lint.
    In real mode: runs subprocess commands in a temp directory.
    """

    def run_tests(self, generated_files: dict[str, str], plan: GenerationPlan) -> dict:
        """Run full test suite (install / lint / typecheck / boot).

        Returns: { status, errors, logs, passed_checks }
        passed_checks values are "passed" | "failed" | "skipped".
        status is "success" when no check is "failed" (skipped is not a failure).
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
                "passed_checks": {
                    "install": "failed",
                    "lint": "failed",
                    "typecheck": "failed",
                    "boot": "failed",
                },
            }

    # ── mock path ────────────────────────────────────────────────────────────

    def _test_mock(self, generated_files: dict[str, str], plan: GenerationPlan) -> dict:
        """In-process checks — no subprocesses, no Docker."""
        passed_checks: dict[str, str] = {}
        logs: dict[str, str] = {}
        errors: list[str] = []

        passed_checks["install"] = "passed"
        logs["install"] = "mock: skipped"

        lint_errors: list[str] = []
        for path, content in generated_files.items():
            if path.endswith(".py"):
                try:
                    ast.parse(content)
                except SyntaxError as exc:
                    lint_errors.append(f"{path}: SyntaxError at line {exc.lineno}: {exc.msg}")
        passed_checks["lint"] = "failed" if lint_errors else "passed"
        logs["lint"] = "ok" if not lint_errors else "; ".join(lint_errors)
        errors.extend(lint_errors)

        passed_checks["typecheck"] = "passed"
        logs["typecheck"] = "mock: skipped"

        passed_checks["boot"] = "passed"
        logs["boot"] = "mock: skipped"

        status = "success" if not errors else "failed"
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
        passed_checks: dict[str, str] = {}
        logs: dict[str, str] = {}

        install_status, install_log = self._run_install(project_dir, stack)
        passed_checks["install"] = install_status
        logs["install"] = install_log

        lint_status, lint_log = self._run_lint(project_dir, stack)
        passed_checks["lint"] = lint_status
        logs["lint"] = lint_log

        typecheck_status, typecheck_log = self._run_typecheck(project_dir, stack)
        passed_checks["typecheck"] = typecheck_status
        logs["typecheck"] = typecheck_log

        boot_status, boot_log = self._run_boot(project_dir, stack)
        passed_checks["boot"] = boot_status
        logs["boot"] = boot_log

        # Only "failed" checks produce errors; "skipped" is not a failure.
        errors = [
            f"{check}: {logs[check]}"
            for check, chk_status in passed_checks.items()
            if chk_status == "failed"
        ]
        status = "success" if not errors else "failed"
        log.info("test.real.done", status=status, passed_checks=passed_checks)
        return {"status": status, "errors": errors, "logs": logs, "passed_checks": passed_checks}

    def _run_install(self, project_dir: str, stack: str) -> tuple[str, str]:
        """Returns (status, log_snippet). status: 'passed' | 'failed' | 'skipped'."""
        if "python" in stack:
            cmd = ["python", "-m", "pip", "install", "-r", "requirements.txt"]
        else:
            cmd = ["npm", "install"]
        try:
            result = subprocess.run(
                cmd, cwd=project_dir, capture_output=True, timeout=120, text=True
            )
            status = "passed" if result.returncode == 0 else "failed"
            log.info(
                "test_install",
                status=status,
                returncode=result.returncode,
                stdout_tail=result.stdout[-2000:] if result.stdout else "",
                stderr_tail=result.stderr[-2000:] if result.stderr else "",
                cmd=" ".join(cmd),
            )
            return status, (result.stdout + result.stderr)[:500]
        except FileNotFoundError:
            log.warning("test.tool_missing", tool=cmd[0], check="install")
            return "skipped", f"Tool '{cmd[0]}' not installed"
        except subprocess.TimeoutExpired:
            log.warning("test_install.timeout")
            return "failed", "timeout"

    def _run_lint(self, project_dir: str, stack: str) -> tuple[str, str]:
        if "python" in stack:
            # Auto-fix cosmetic issues (trailing whitespace, unused imports) before evaluating.
            try:
                subprocess.run(
                    ["ruff", "check", "--fix", "--select", "W,F", "--exit-zero", "."],
                    cwd=project_dir, capture_output=True, text=True, timeout=30,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                log.warning("test.tool_missing", tool="ruff", check="lint_autofix")

            # Evaluate only on errors that actually break the app (syntax errors, undefined names).
            cmd = ["flake8", "--select=E9,F63,F7,F82", "--max-line-length=120", "."]
        else:
            cmd = ["npx", "eslint", "src/", "--max-warnings=0"]

        try:
            result = subprocess.run(
                cmd, cwd=project_dir, capture_output=True, timeout=60, text=True
            )
            status = "passed" if result.returncode == 0 else "failed"
            log.info(
                "test_lint",
                status=status,
                returncode=result.returncode,
                stdout_tail=result.stdout[-2000:] if result.stdout else "",
                stderr_tail=result.stderr[-2000:] if result.stderr else "",
                cmd=" ".join(cmd),
            )
            return status, (result.stdout + result.stderr)[:500]
        except FileNotFoundError:
            log.warning("test.tool_missing", tool=cmd[0], check="lint")
            return "skipped", f"Tool '{cmd[0]}' not installed"
        except subprocess.TimeoutExpired:
            log.warning("test_lint.timeout")
            return "failed", "timeout"

    def _run_typecheck(self, project_dir: str, stack: str) -> tuple[str, str]:
        if "python" in stack:
            cmd = ["mypy", ".", "--ignore-missing-imports"]
        else:
            cmd = ["npm", "run", "typecheck"]
        try:
            result = subprocess.run(
                cmd, cwd=project_dir, capture_output=True, timeout=60, text=True
            )
            status = "passed" if result.returncode == 0 else "failed"
            log.info(
                "test_typecheck",
                status=status,
                returncode=result.returncode,
                stdout_tail=result.stdout[-2000:] if result.stdout else "",
                stderr_tail=result.stderr[-2000:] if result.stderr else "",
                cmd=" ".join(cmd),
            )
            return status, (result.stdout + result.stderr)[:500]
        except FileNotFoundError:
            log.warning("test.tool_missing", tool=cmd[0], check="typecheck")
            return "skipped", f"Tool '{cmd[0]}' not installed"
        except subprocess.TimeoutExpired:
            log.warning("test_typecheck.timeout")
            return "failed", "timeout"

    def _run_boot(self, project_dir: str, stack: str) -> tuple[str, str]:
        if "python" in stack:
            cmd = ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
        else:
            cmd = ["npm", "run", "dev"]
        try:
            proc = subprocess.Popen(
                cmd, cwd=project_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            out, err = "", ""
            try:
                proc.wait(timeout=5)
                status = "failed"
                output = "process exited early"
                try:
                    out, err = proc.communicate(timeout=2)
                except Exception:
                    pass
            except subprocess.TimeoutExpired:
                status = "passed"
                proc.terminate()
                try:
                    out, err = proc.communicate(timeout=2)
                except Exception:
                    pass
                output = "booted ok"
            log.info(
                "test_boot",
                status=status,
                stdout_tail=out[-2000:] if out else "",
                stderr_tail=err[-2000:] if err else "",
                cmd=" ".join(cmd),
            )
            return status, output
        except FileNotFoundError:
            log.warning("test.tool_missing", tool=cmd[0], check="boot")
            return "skipped", f"Tool '{cmd[0]}' not installed"
        except Exception as exc:
            log.error("test_boot.error", error=str(exc))
            return "failed", str(exc)
