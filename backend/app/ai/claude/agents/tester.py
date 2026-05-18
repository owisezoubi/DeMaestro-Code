"""TesterAgent — runs tests on generated code. Pure Python, no LLM."""
import ast
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import structlog

from app.config import settings
from app.models.generation_plan import GenerationPlan

log = structlog.get_logger("TesterAgent")

# Statuses: "passed", "failed", "skipped"
# Skipped means the tool was absent or a manifest was missing; never enters the debug loop.


# ── directory discovery ───────────────────────────────────────────────────────

def _find_backend_dir(tmpdir: Path) -> Optional[Path]:
    """Find the directory containing a Python project's manifest.

    Checks ./backend/ first, then root, then ./server/, ./api/.
    """
    candidates = [
        tmpdir / "backend",
        tmpdir,
        tmpdir / "server",
        tmpdir / "api",
    ]
    for c in candidates:
        if (c / "requirements.txt").exists() or (c / "pyproject.toml").exists():
            return c
    return None


def _find_frontend_dir(tmpdir: Path) -> Optional[Path]:
    """Find the directory containing a Node project's package.json."""
    candidates = [
        tmpdir / "frontend",
        tmpdir,
        tmpdir / "client",
        tmpdir / "web",
    ]
    for c in candidates:
        if (c / "package.json").exists():
            return c
    return None


# ── agent ─────────────────────────────────────────────────────────────────────

class TesterAgent:
    """Runs install / lint / typecheck / boot checks on generated code.

    In mock mode: skips subprocess and Docker; uses in-process ast.parse for lint.
    In real mode: runs subprocess commands in the discovered backend/frontend dirs.
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
        """Write all generated files to a temp directory preserving their relative paths."""
        tmpdir = tempfile.mkdtemp(prefix="demaestro_test_")
        for file_path, content in generated_files.items():
            full_path = os.path.join(tmpdir, file_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w") as f:
                f.write(content)
        log.info("write_files_to_temp", tmpdir=tmpdir)
        return tmpdir

    def _test_real(self, project_dir: str, stack: str) -> dict:
        tmpdir = Path(project_dir)
        passed_checks: dict[str, str] = {}
        logs: dict[str, str] = {}

        install_status, install_log = self._run_install(tmpdir, stack)
        passed_checks["install"] = install_status
        logs["install"] = install_log

        lint_status, lint_log = self._run_lint(tmpdir, stack)
        passed_checks["lint"] = lint_status
        logs["lint"] = lint_log

        typecheck_status, typecheck_log = self._run_typecheck(tmpdir, stack)
        passed_checks["typecheck"] = typecheck_status
        logs["typecheck"] = typecheck_log

        boot_status, boot_log = self._run_boot(tmpdir, stack)
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

    def _run_install(self, tmpdir: Path, stack: str) -> tuple[str, str]:
        """Returns (status, log_snippet). status: 'passed' | 'failed' | 'skipped'."""
        if "python" in stack:
            backend_dir = _find_backend_dir(tmpdir)
            if backend_dir is None:
                log.warning("test_install.no_manifest", tmpdir=str(tmpdir))
                return "skipped", "No Python manifest found"

            py_cmd = ["python", "-m", "pip", "install", "-r", "requirements.txt"]
            try:
                py_result = subprocess.run(
                    py_cmd, cwd=backend_dir, capture_output=True, timeout=120, text=True
                )
                py_status = "passed" if py_result.returncode == 0 else "failed"
                log.info(
                    "test_install",
                    status=py_status,
                    returncode=py_result.returncode,
                    backend_dir=str(backend_dir.relative_to(tmpdir)) if backend_dir != tmpdir else ".",
                    stdout_tail=py_result.stdout[-2000:] if py_result.stdout else "",
                    stderr_tail=py_result.stderr[-2000:] if py_result.stderr else "",
                    cmd=" ".join(py_cmd),
                )
                py_log = (py_result.stdout + py_result.stderr)[:500]
            except FileNotFoundError:
                log.warning("test.tool_missing", tool=py_cmd[0], check="install")
                return "skipped", f"Tool '{py_cmd[0]}' not installed"
            except subprocess.TimeoutExpired:
                log.warning("test_install.timeout")
                return "failed", "timeout"

            # For full-stack projects (python-postgres), also install frontend deps if present.
            if stack == "python-postgres":
                frontend_dir = _find_frontend_dir(tmpdir)
                if frontend_dir is not None:
                    npm_cmd = ["npm", "install"]
                    try:
                        npm_result = subprocess.run(
                            npm_cmd, cwd=frontend_dir, capture_output=True, timeout=120, text=True
                        )
                        npm_status = "passed" if npm_result.returncode == 0 else "failed"
                        log.info(
                            "test_install_frontend",
                            status=npm_status,
                            returncode=npm_result.returncode,
                            frontend_dir=str(frontend_dir.relative_to(tmpdir)) if frontend_dir != tmpdir else ".",
                            stdout_tail=npm_result.stdout[-2000:] if npm_result.stdout else "",
                            stderr_tail=npm_result.stderr[-2000:] if npm_result.stderr else "",
                        )
                        if npm_status == "failed":
                            fe_log = (npm_result.stdout + npm_result.stderr)[:500]
                            return "failed", f"{py_log}\n[frontend] {fe_log}"
                    except FileNotFoundError:
                        log.warning("test.tool_missing", tool="npm", check="install_frontend")
                    except subprocess.TimeoutExpired:
                        log.warning("test_install_frontend.timeout")

            return py_status, py_log

        else:  # node-mongo
            frontend_dir = _find_frontend_dir(tmpdir)
            if frontend_dir is None:
                log.warning("test_install.no_manifest", tmpdir=str(tmpdir))
                return "skipped", "No Node.js manifest found"
            cmd = ["npm", "install"]
            try:
                result = subprocess.run(
                    cmd, cwd=frontend_dir, capture_output=True, timeout=120, text=True
                )
                status = "passed" if result.returncode == 0 else "failed"
                log.info(
                    "test_install",
                    status=status,
                    returncode=result.returncode,
                    frontend_dir=str(frontend_dir.relative_to(tmpdir)) if frontend_dir != tmpdir else ".",
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

    def _run_lint(self, tmpdir: Path, stack: str) -> tuple[str, str]:
        if "python" in stack:
            backend_dir = _find_backend_dir(tmpdir)
            if backend_dir is None:
                return "skipped", "No Python project root found"

            # Scope lint to the backend subdirectory when it's a proper subdirectory.
            lint_path = "." if backend_dir == tmpdir else str(backend_dir.relative_to(tmpdir))

            # Auto-fix cosmetic issues before evaluating.
            try:
                subprocess.run(
                    ["ruff", "check", "--fix", "--select", "W,F", "--exit-zero", lint_path],
                    cwd=tmpdir, capture_output=True, text=True, timeout=30,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                log.warning("test.tool_missing", tool="ruff", check="lint_autofix")

            cmd = ["flake8", "--select=E9,F63,F7,F82", "--max-line-length=120", lint_path]
            cwd = tmpdir
        else:
            cmd = ["npx", "eslint", "src/", "--max-warnings=0"]
            frontend_dir = _find_frontend_dir(tmpdir)
            cwd = frontend_dir if frontend_dir is not None else tmpdir

        try:
            result = subprocess.run(
                cmd, cwd=cwd, capture_output=True, timeout=60, text=True
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

    def _run_typecheck(self, tmpdir: Path, stack: str) -> tuple[str, str]:
        if "python" in stack:
            backend_dir = _find_backend_dir(tmpdir)
            cwd = backend_dir if backend_dir is not None else tmpdir
            cmd = ["mypy", ".", "--ignore-missing-imports"]
        else:
            frontend_dir = _find_frontend_dir(tmpdir)
            cwd = frontend_dir if frontend_dir is not None else tmpdir
            cmd = ["npm", "run", "typecheck"]

        try:
            result = subprocess.run(
                cmd, cwd=cwd, capture_output=True, timeout=60, text=True
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

    def _run_boot(self, tmpdir: Path, stack: str) -> tuple[str, str]:
        if "python" in stack:
            backend_dir = _find_backend_dir(tmpdir)
            if backend_dir is None:
                log.warning("test_boot.no_backend", tmpdir=str(tmpdir))
                return "skipped", "No Python project root found"
            cwd = backend_dir
            cmd = ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
        else:
            frontend_dir = _find_frontend_dir(tmpdir)
            if frontend_dir is None:
                log.warning("test_boot.no_frontend", tmpdir=str(tmpdir))
                return "skipped", "No Node.js project root found"
            cwd = frontend_dir
            cmd = ["npm", "run", "dev"]

        try:
            proc = subprocess.Popen(
                cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
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
