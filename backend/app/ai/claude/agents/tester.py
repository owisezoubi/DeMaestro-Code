"""TesterAgent — runs tests on generated code. Pure Python, no LLM."""
import ast
import json
import os
import re
import shutil
import subprocess
import tempfile
from difflib import get_close_matches
from pathlib import Path
from typing import Optional

import structlog

from app.config import settings
from app.models.generation_plan import GenerationPlan

log = structlog.get_logger("TesterAgent")

_BOOT_FAILURE_PATTERNS = [
    "Traceback (most recent call last):",
    "Error:",
    "Exception:",
    "sqlalchemy.exc.",
    "ImportError:",
    "ModuleNotFoundError:",
    "SyntaxError:",
    "AttributeError:",
    "TypeError:",
    "NameError:",
]


_FRONTEND_API_CALL_RE = re.compile(
    r'\b(?:api|axios)\.(?P<method>get|post|put|patch|delete)\s*\(\s*["\'](?P<path>/[^"\']+)["\']',
    re.IGNORECASE,
)


def _extract_backend_routes_from_openapi(schema: dict) -> set[tuple[str, str]]:
    """Pull (METHOD, /path) tuples from a fetched openapi.json.

    FastAPI's own route table — guaranteed to match what the running app serves.
    """
    routes: set[tuple[str, str]] = set()
    if not schema:
        return routes
    for path, methods in (schema.get("paths") or {}).items():
        for method, _spec in (methods or {}).items():
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                routes.add((method.upper(), path.rstrip("/") or "/"))
    return routes


def _stderr_has_exception(stderr: str) -> bool:
    return any(p in stderr for p in _BOOT_FAILURE_PATTERNS)

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

    def __init__(self) -> None:
        self._last_openapi_schema: Optional[dict] = None

    def run_tests(self, generated_files: dict[str, str], plan: GenerationPlan) -> dict:
        """Run full test suite (install / lint / typecheck / boot).

        Returns: { status, errors, logs, passed_checks }
        passed_checks values are "passed" | "failed" | "skipped".
        status is "success" when no check is "failed" (skipped is not a failure).
        """
        log.info("test.start", num_files=len(generated_files), stack=plan.technology_stack)

        if settings.mock_ai:
            result = self._test_mock(generated_files, plan)
        else:
            try:
                project_dir = self._write_files_to_temp(generated_files)
                result = self._test_real(project_dir, plan.technology_stack)
            except Exception as e:
                log.error("test.error", error=str(e))
                result = {
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

        # Contract check — uses the OpenAPI schema fetched from the running backend.
        # Skipped in mock mode and when boot failed (no schema captured).
        contract_status, contract_data = self._contract_check(
            generated_files, self._last_openapi_schema
        )
        if contract_status == "failed":
            for miss in contract_data:
                log.warning("contract_check.miss", miss=miss)
            result["errors"].extend(contract_data)
            result["passed_checks"]["contract"] = "failed"
            result["logs"]["contract"] = "; ".join(contract_data)
            result["status"] = "failed"
        else:
            result["passed_checks"]["contract"] = contract_status  # "passed" or "skipped"
            if contract_status == "skipped":
                result["logs"]["contract"] = contract_data

        return result

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

        try:
            install_status, install_log, venv_python = self._run_install(tmpdir, stack)
            passed_checks["install"] = install_status
            logs["install"] = install_log

            lint_status, lint_log = self._run_lint(tmpdir, stack)
            passed_checks["lint"] = lint_status
            logs["lint"] = lint_log

            typecheck_status, typecheck_log = self._run_typecheck(tmpdir, stack)
            passed_checks["typecheck"] = typecheck_status
            logs["typecheck"] = typecheck_log

            boot_status, boot_log = self._run_boot(tmpdir, stack, venv_python=venv_python)
            passed_checks["boot"] = boot_status
            logs["boot"] = boot_log

            build_status, build_log = self._run_frontend_build(tmpdir)
            passed_checks["frontend_build"] = build_status
            logs["frontend_build"] = build_log

            if passed_checks.get("frontend_build") == "passed":
                smoke_status, smoke_log = self._run_smoke(tmpdir)
                passed_checks["smoke"] = smoke_status
                logs["smoke"] = smoke_log

            # Only "failed" checks produce errors; "skipped" is not a failure.
            errors = [
                f"{check}: {logs[check]}"
                for check, chk_status in passed_checks.items()
                if chk_status == "failed"
            ]
            status = "success" if not errors else "failed"
            log.info("test.real.done", status=status, passed_checks=passed_checks)
            return {"status": status, "errors": errors, "logs": logs, "passed_checks": passed_checks}
        finally:
            venv_path = tmpdir / ".testenv"
            if venv_path.exists():
                shutil.rmtree(venv_path, ignore_errors=True)

    def _run_install(self, tmpdir: Path, stack: str) -> tuple[str, str, Optional[Path]]:
        """Returns (status, log_snippet, venv_python). status: 'passed' | 'failed' | 'skipped'."""
        if "python" in stack:
            backend_dir = _find_backend_dir(tmpdir)
            if backend_dir is None:
                log.warning("test_install.no_manifest", tmpdir=str(tmpdir))
                return "skipped", "No Python manifest found", None

            # Create an isolated venv inside tmpdir so we don't pollute DeMaestro's environment.
            venv_dir = tmpdir / ".testenv"
            venv_python: Optional[Path] = None
            try:
                subprocess.run(
                    ["python", "-m", "venv", str(venv_dir)],
                    capture_output=True, timeout=60, text=True, check=True,
                )
                venv_python = venv_dir / "bin" / "python"
                subprocess.run(
                    [str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "--quiet"],
                    capture_output=True, timeout=60, text=True,
                )
                py_cmd = [str(venv_python), "-m", "pip", "install", "-r", "requirements.txt", "--quiet"]
            except Exception as exc:
                log.warning("test_install.venv_creation_failed", error=str(exc))
                venv_python = None
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
                return "skipped", f"Tool '{py_cmd[0]}' not installed", None
            except subprocess.TimeoutExpired:
                log.warning("test_install.timeout")
                return "failed", "timeout", None

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
                            return "failed", f"{py_log}\n[frontend] {fe_log}", venv_python
                    except FileNotFoundError:
                        log.warning("test.tool_missing", tool="npm", check="install_frontend")
                    except subprocess.TimeoutExpired:
                        log.warning("test_install_frontend.timeout")

            return py_status, py_log, venv_python

        else:  # node-mongo
            frontend_dir = _find_frontend_dir(tmpdir)
            if frontend_dir is None:
                log.warning("test_install.no_manifest", tmpdir=str(tmpdir))
                return "skipped", "No Node.js manifest found", None
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
                return status, (result.stdout + result.stderr)[:500], None
            except FileNotFoundError:
                log.warning("test.tool_missing", tool=cmd[0], check="install")
                return "skipped", f"Tool '{cmd[0]}' not installed", None
            except subprocess.TimeoutExpired:
                log.warning("test_install.timeout")
                return "failed", "timeout", None

    def _run_frontend_build(self, tmpdir: Path) -> tuple[str, str]:
        """Run `npm run build`. Returns (status, log)."""
        frontend_dir = _find_frontend_dir(tmpdir)
        if frontend_dir is None:
            return ("skipped", "")
        try:
            result = subprocess.run(
                ["npm", "run", "build"],
                cwd=frontend_dir,
                capture_output=True,
                timeout=180,
                text=True,
            )
            status = "passed" if result.returncode == 0 else "failed"
            log_ = (result.stdout or "") + "\n" + (result.stderr or "")
            log.info(
                "test_frontend_build",
                status=status,
                returncode=result.returncode,
                stderr_tail=(result.stderr or "")[-3000:],
            )
            return (status, log_)
        except subprocess.TimeoutExpired:
            return ("failed", "frontend build timeout")
        except Exception as exc:
            log.warning("frontend_build.exception", error=str(exc))
            return ("skipped", str(exc))

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

    def _run_boot(self, tmpdir: Path, stack: str, venv_python: Optional[Path] = None) -> tuple[str, str]:
        if "python" in stack:
            backend_dir = _find_backend_dir(tmpdir)
            if backend_dir is None:
                log.warning("test_boot.no_backend", tmpdir=str(tmpdir))
                return "skipped", "No Python project root found"
            cwd = backend_dir
            if venv_python is not None and venv_python.exists():
                cmd = [str(venv_python), "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
            else:
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
                # Backend is running — grab the real route table from FastAPI before shutdown.
                # Retry up to 15 times to handle the race where FastAPI is still starting.
                openapi_schema = None
                for attempt in range(15):
                    try:
                        import urllib.request
                        with urllib.request.urlopen(
                            "http://localhost:8001/openapi.json", timeout=2,
                        ) as resp:
                            openapi_schema = json.loads(resp.read().decode("utf-8"))
                        log.info("test_boot.openapi_fetched",
                                 paths=len(openapi_schema.get("paths", {})),
                                 attempts=attempt + 1)
                        break
                    except Exception:
                        import time
                        time.sleep(1)
                if openapi_schema is None:
                    log.warning("test_boot.openapi_fetch_failed_after_retries")
                self._last_openapi_schema = openapi_schema

                proc.terminate()
                try:
                    out, err = proc.communicate(timeout=2)
                except Exception:
                    pass
                if _stderr_has_exception(err):
                    status = "failed"
                    output = f"boot exception in stderr: {err[:500]}"
                else:
                    status = "passed"
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

    def _run_smoke(self, tmpdir: Path) -> tuple[str, str]:
        backend_dir = _find_backend_dir(tmpdir)
        frontend_dir = _find_frontend_dir(tmpdir)
        if not backend_dir or not frontend_dir:
            return ("skipped", "no backend/frontend dir")

        dist_dir = frontend_dir / "dist"
        if not dist_dir.exists():
            return ("skipped", "no dist (build did not run)")

        # 1. Install playwright npm package (must come first).
        try:
            r = subprocess.run(
                ["npm", "install", "--no-save", "playwright"],
                cwd=frontend_dir, capture_output=True, timeout=180, text=True,
            )
            if r.returncode != 0:
                return ("skipped", f"playwright npm install failed: {r.stderr[-500:]}")
        except Exception as exc:
            return ("skipped", f"playwright install exception: {exc}")

        # 2. Install chromium browser (cached under ~/.cache/ms-playwright on
        #    subsequent runs, so this is slow only on the very first test).
        try:
            r = subprocess.run(
                ["npx", "--yes", "playwright", "install", "chromium"],
                cwd=frontend_dir, capture_output=True, timeout=180, text=True,
            )
            if r.returncode != 0:
                return ("skipped", f"chromium install failed: {r.stderr[-500:]}")
        except Exception as exc:
            return ("skipped", f"chromium install exception: {exc}")

        # 3. Write a defensive smoke script. require() is wrapped so a missing
        #    playwright never crashes the process silently.
        smoke_script = r'''
let chromium;
try {
  chromium = require('playwright').chromium;
} catch (e) {
  console.log(JSON.stringify({ ok: null, skipped: true, reason: 'playwright_require_failed', detail: e.message }));
  process.exit(0);
}
(async () => {
  const url = process.env.SMOKE_URL || 'http://localhost:5174';
  let browser;
  try {
    browser = await chromium.launch();
  } catch (e) {
    console.log(JSON.stringify({ ok: null, skipped: true, reason: 'chromium_launch_failed', detail: e.message }));
    process.exit(0);
  }
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()); });
  page.on('pageerror', e => errors.push('PageError: ' + e.message));
  let bodyLen = 0;
  try {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForTimeout(2000);
    bodyLen = (await page.evaluate(() => document.body.innerText)).trim().length;
  } catch (e) {
    errors.push('Navigation: ' + e.message);
  }
  await browser.close();
  const realErrors = errors.filter(e =>
    !/Failed to fetch|NetworkError|ERR_CONNECTION|net::ERR/.test(e)
  );
  console.log(JSON.stringify({
    ok: realErrors.length === 0 && bodyLen > 30,
    bodyLen,
    errors: realErrors,
  }));
})().catch(e => {
  console.log(JSON.stringify({ ok: null, skipped: true, reason: 'smoke_runtime_exception', detail: e.message }));
  process.exit(0);
});
'''
        smoke_path = tmpdir / "smoke.cjs"
        smoke_path.write_text(smoke_script)

        # 4. Boot `vite preview` on the dist folder.
        preview = subprocess.Popen(
            ["npx", "--yes", "vite", "preview", "--port", "5174", "--strictPort"],
            cwd=frontend_dir,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            import time; time.sleep(3)
            result = subprocess.run(
                ["node", str(smoke_path)],
                cwd=frontend_dir,
                env={**os.environ, "SMOKE_URL": "http://localhost:5174"},
                capture_output=True, timeout=60, text=True,
            )
            raw_out = (result.stdout or "") + "\n" + (result.stderr or "")
            # Find the LAST line that parses as JSON (the script may print
            # warnings before the result).
            parsed = None
            for line in reversed(raw_out.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        parsed = json.loads(line)
                        break
                    except Exception:
                        continue
            if parsed is None:
                log.warning("test_smoke.unparseable", out_tail=raw_out[-500:])
                return ("skipped", f"smoke output unparseable: {raw_out[-500:]}")
            if parsed.get("skipped"):
                reason = parsed.get("reason", "unknown")
                log.info("test_smoke.skipped", reason=reason)
                return ("skipped", reason)
            if parsed.get("ok"):
                log.info("test_smoke.passed", body_len=parsed.get("bodyLen", 0))
                return ("passed", json.dumps(parsed))
            log.warning(
                "test_smoke.failed",
                errors=parsed.get("errors", []),
                body_len=parsed.get("bodyLen", 0),
            )
            return ("failed", json.dumps(parsed))
        finally:
            preview.terminate()
            try:
                preview.wait(timeout=5)
            except Exception:
                preview.kill()

    # ── contract checks ───────────────────────────────────────────────────────

    def _extract_backend_routes(self, generated_files: dict[str, str]) -> dict:
        """Scan backend Python files; return paths where OAuth2PasswordRequestForm is used."""
        oauth2_paths: set[str] = set()
        for path, content in generated_files.items():
            if not path.endswith(".py") or "OAuth2PasswordRequestForm" not in content:
                continue
            for m in re.finditer(r'@(?:router|app)\.post\(["\']([^"\']+)["\']', content):
                oauth2_paths.add(m.group(1))
            if not oauth2_paths:
                oauth2_paths.add("__any_auth__")
        return {"oauth2_paths": oauth2_paths}

    def _extract_frontend_calls(self, generated_files: dict[str, str]) -> list[dict]:
        """Scan frontend files for POST calls to auth-shaped paths."""
        calls: list[dict] = []
        auth_path_re = re.compile(r'/(?:login|signin|register|signup)\b', re.IGNORECASE)
        for path, content in generated_files.items():
            if not any(path.endswith(ext) for ext in (".jsx", ".tsx", ".js", ".ts")):
                continue
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if "post(" not in line.lower() and "POST" not in line:
                    continue
                url_m = re.search(r'''[`'"](/[^`'"]+)[`'"]''', line)
                if not url_m:
                    continue
                post_path = url_m.group(1)
                if not auth_path_re.search(post_path):
                    continue
                ctx = "\n".join(lines[max(0, i - 5):min(len(lines), i + 10)])
                is_form = "URLSearchParams" in ctx or "FormData" in ctx
                calls.append({
                    "file": path,
                    "post_path": post_path,
                    "is_form_encoded": is_form,
                    "is_json": not is_form,
                })
        return calls

    def _extract_all_frontend_api_calls(self, generated_files: dict[str, str]) -> list[tuple[str, str]]:
        """Build deduplicated (METHOD, path) list from all frontend files (static string paths only)."""
        calls: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for path, content in generated_files.items():
            if not any(path.endswith(ext) for ext in (".jsx", ".tsx", ".js", ".ts")):
                continue
            if path.endswith("/api.js") or path.endswith("/api.ts"):
                continue
            for m in _FRONTEND_API_CALL_RE.finditer(content or ""):
                method = m.group("method").upper()
                route_path = m.group("path")
                key = (method, route_path)
                if key in seen:
                    continue
                seen.add(key)
                calls.append(key)
        return calls

    def _contract_check(
        self,
        generated_files: dict[str, str],
        openapi_schema: Optional[dict],
    ) -> tuple[str, list | str]:
        """Return (status, data).

        status is 'passed', 'failed', or 'skipped'.
        data is a list of CONTRACT MISS strings when failed, or a reason string when skipped.
        """
        if not openapi_schema:
            return ("skipped", "no openapi schema available (boot may have failed)")

        misses: list[str] = []

        # ── AUTH-SHAPE check ────────────────────────────────────────────────
        backend = self._extract_backend_routes(generated_files)
        frontend = self._extract_frontend_calls(generated_files)
        oauth2_paths = backend.get("oauth2_paths", set())
        if oauth2_paths and frontend:
            for call in frontend:
                if not call.get("is_json"):
                    continue
                post_path = call.get("post_path", "")
                path_matched = any(
                    post_path.endswith(op) or op == "__any_auth__"
                    for op in oauth2_paths
                )
                if path_matched:
                    misses.append(
                        f"CONTRACT MISS: AUTH-SHAPE POST {post_path}   "
                        "backend expects form-encoded username+password "
                        "(OAuth2PasswordRequestForm), frontend sends JSON. "
                        "suggestions: convert to URLSearchParams"
                    )

        # ── METHOD-MISMATCH check (OpenAPI ground-truth) ────────────────────
        backend_routes = _extract_backend_routes_from_openapi(openapi_schema)
        backend_paths_by_method: dict[str, set[str]] = {}
        for bmethod, bpath in backend_routes:
            backend_paths_by_method.setdefault(bmethod, set()).add(bpath)

        frontend_calls = self._extract_all_frontend_api_calls(generated_files)
        if backend_paths_by_method and frontend_calls:
            for method, path in frontend_calls:
                if method == "UNKNOWN":
                    all_paths = {p for s in backend_paths_by_method.values() for p in s}
                    if path in all_paths:
                        continue
                    sug = get_close_matches(path, list(all_paths), n=3, cutoff=0.6)
                    misses.append(f"CONTRACT MISS: {method} {path}   suggestions: {sug or '(none)'}")
                    continue

                cand_paths = backend_paths_by_method.get(method, set())
                if path in cand_paths:
                    continue

                methods_serving_this_path = sorted([
                    meth for meth, paths in backend_paths_by_method.items() if path in paths
                ])
                if methods_serving_this_path:
                    misses.append(
                        f"CONTRACT MISS: METHOD {method} {path}   "
                        f"backend serves this path with: {methods_serving_this_path}. "
                        f"suggestions: {methods_serving_this_path}"
                    )
                    continue

                all_paths = {p for s in backend_paths_by_method.values() for p in s}
                sug = get_close_matches(path, list(all_paths), n=3, cutoff=0.6)
                misses.append(f"CONTRACT MISS: {method} {path}   suggestions: {sug or '(none)'}")

        return ("failed", misses) if misses else ("passed", [])
