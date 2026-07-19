"""TesterAgent — runs tests on generated code. Pure Python, no LLM."""
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from difflib import get_close_matches
from pathlib import Path
from typing import Optional

import structlog

from app.config import settings
from app.models.generation_plan import GenerationPlan

log = structlog.get_logger("TesterAgent")


# ── Route-ordering shadow check ───────────────────────────────────────────────

_ROUTE_DECORATOR_RE = re.compile(
    r'@router\.(get|post|put|patch|delete)\s*\(\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def _path_segments(path: str) -> list:
    return [s for s in path.strip("/").split("/") if s]


def _is_param_seg(seg: str) -> bool:
    return seg.startswith("{") and seg.endswith("}")


def _path_shadows(param_path: str, literal_path: str) -> bool:
    """Return True if param_path declared before literal_path would shadow it."""
    ps = _path_segments(param_path)
    ls = _path_segments(literal_path)
    if len(ps) != len(ls):
        return False
    for p_seg, l_seg in zip(ps, ls):
        if _is_param_seg(p_seg):
            continue        # param matches anything — keep checking
        if p_seg != l_seg:
            return False    # literal mismatch — no shadow
    return any(_is_param_seg(s) for s in ps)  # only shadow when at least one param exists


# ── Persistent test-tools cache ───────────────────────────────────────────────
# Survives across cycles, runs, and server restarts.  Safe to delete manually.
_TEST_TOOLS_CACHE = os.path.expanduser("~/.demaestro/test_tools_cache")
_VENV_CACHE = os.path.expanduser("~/.demaestro/venv_cache")
_NODE_MODULES_CACHE = os.path.expanduser("~/.demaestro/node_modules_cache")
os.makedirs(_TEST_TOOLS_CACHE, exist_ok=True)
os.makedirs(_VENV_CACHE, exist_ok=True)
os.makedirs(_NODE_MODULES_CACHE, exist_ok=True)
# Playwright removed: smoke test uses direct HTTP (requests/urllib) — no browser.


def _get_or_create_venv(req_path: str) -> Optional[Path]:
    """Return a cached venv Python path keyed by requirements.txt content hash.

    Cache hit: returns in ~0 ms.
    Cache miss: creates a new venv, installs deps, then returns (~30-60 s first time).
    Returns None if the cache cannot be used (file missing, install failed, etc.).
    """
    try:
        req_text = open(req_path).read()
    except OSError:
        return None
    req_hash = hashlib.sha256(req_text.encode()).hexdigest()[:16]
    cache_dir = os.path.join(_VENV_CACHE, req_hash)
    venv_python = Path(cache_dir) / "bin" / "python"
    if venv_python.exists():
        log.info("test_install.venv_cache_hit", hash=req_hash)
        return venv_python
    log.info("test_install.venv_cache_miss", hash=req_hash)
    os.makedirs(cache_dir, exist_ok=True)
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", cache_dir],
            check=True, capture_output=True, text=True, timeout=60,
        )
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "--quiet"],
            capture_output=True, text=True, timeout=60,
        )
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "-r", req_path, "--quiet"],
            check=True, capture_output=True, text=True, timeout=300,
        )
        return venv_python
    except Exception as exc:
        log.warning("test_install.venv_cache_failed", hash=req_hash, error=str(exc))
        return None


def _link_cached_node_modules(pkg_path: str, frontend_dir: Path) -> bool:
    """Symlink a cached node_modules into frontend_dir keyed by package.json hash.

    Returns True when the cache was used (npm install should be skipped).
    Returns False when cache is empty or symlinking failed (do a normal npm install).
    """
    try:
        pkg_text = open(str(pkg_path)).read()
    except OSError:
        return False
    pkg_hash = hashlib.sha256(pkg_text.encode()).hexdigest()[:16]
    cache_dir = os.path.join(_NODE_MODULES_CACHE, pkg_hash)
    cached_modules = os.path.join(cache_dir, "node_modules")
    marker = os.path.join(cache_dir, ".installed")
    target = frontend_dir / "node_modules"

    if os.path.exists(marker) and os.path.isdir(cached_modules):
        try:
            if not target.exists():
                os.symlink(cached_modules, str(target))
            log.info("test_install_frontend.cache_hit", hash=pkg_hash)
            return True
        except Exception as exc:
            log.warning("test_install_frontend.symlink_failed", error=str(exc))
            return False

    # Cache miss — install into the cache dir and mark it
    log.info("test_install_frontend.cache_miss", hash=pkg_hash)
    os.makedirs(cache_dir, exist_ok=True)
    shutil.copy(str(pkg_path), os.path.join(cache_dir, "package.json"))
    lock_src = os.path.join(os.path.dirname(str(pkg_path)), "package-lock.json")
    if os.path.exists(lock_src):
        shutil.copy(lock_src, os.path.join(cache_dir, "package-lock.json"))
    try:
        r = subprocess.run(
            ["npm", "install", "--quiet"],
            cwd=cache_dir, capture_output=True, timeout=180, text=True,
        )
        if r.returncode == 0:
            open(marker, "w").write("ok")
            if not target.exists():
                os.symlink(cached_modules, str(target))
            log.info("test_install_frontend.cached", hash=pkg_hash)
            return True
    except Exception as exc:
        log.warning("test_install_frontend.cache_install_failed", error=str(exc))
    return False


# Playwright removed — smoke test replaced with direct HTTP checks (see _run_smoke).

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


def _normalize_frontend_path(url: str, from_api_client: bool) -> str:
    """If the URL came from an api.X call and doesn't already include
    /api, prepend it (matches api.js runtime behavior).

    api.get("/dashboard") -> /api/dashboard at runtime.
    fetch("/api/dashboard") -> /api/dashboard at runtime (already has it).
    """
    if not url.startswith("/"):
        return url
    if url.startswith("/api/") or url == "/api":
        return url
    if from_api_client:
        return "/api" + url
    return url


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


_CONTRACT_AGG_DATA_RE = re.compile(r"/API/\w+/DATA\b")


def _is_admin_component(source_file: str) -> bool:
    """True when source_file is an admin page or component.

    Matches:
      frontend/src/pages/admin/Dashboard.jsx  (path-based)
      frontend/src/components/admin/Table.jsx (path-based)
      frontend/src/pages/AdminDashboard.jsx   (name-based)
    """
    lower = source_file.lower()
    if "/pages/admin/" in lower or "/components/admin/" in lower:
        return True
    basename = source_file.rsplit("/", 1)[-1]
    return "admin" in basename.lower()


def _classify_contract_miss(miss: str) -> str:
    """Return 'real' (blocks ZIP) or 'advisory' (warning only) for a CONTRACT MISS string."""
    m = miss.upper()
    # Auth scaffold / body-shape issues always block.
    if "AUTH-SHAPE" in m or "AUTH-SCAFFOLD" in m:
        return "real"
    # Admin component calling a public path when an admin path exists.
    if "ADMIN-PREFIX" in m:
        return "real"
    # Evaluate path-based rules on the miss description only (before "suggestions:"
    # and "backend serves" clauses) so /api/admin paths in the suggestions list don't
    # accidentally trigger real classification for unrelated public-path misses.
    miss_desc = m.split("SUGGESTIONS:")[0].split("BACKEND SERVES")[0]
    # Admin and auth paths in the miss itself are always critical.
    if "/API/ADMIN/" in miss_desc or "/API/AUTH/" in miss_desc:
        return "real"
    # User-profile endpoint — page cannot function without it.
    if "/API/USERS/ME" in miss_desc:
        return "real"
    # Aggregate data endpoint — page renders empty without it.
    if _CONTRACT_AGG_DATA_RE.search(miss_desc):
        return "real"
    # Method mismatch where the FRONTEND uses a write verb blocks ZIP.
    # Match "METHOD PUT/POST/PATCH/DELETE" specifically (not verbs in the backend list).
    if re.search(r"\bMETHOD\s+(?:PUT|POST|PATCH|DELETE)\b", m):
        return "real"
    # Everything else: public read-only GET gaps degrade gracefully.
    return "advisory"


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


def _classify_reachability_status(
    status: int, method: str, body_text: str,
) -> tuple[str, str]:
    """Return (verdict, reason) for a probed endpoint response.

    FastAPI's own "no route matched" 404 returns exactly
    {"detail":"Not Found"}.  Any other 404 body means a handler ran
    and chose to return 404 (e.g. row not found) -- that is a PASS
    because the route exists.
    """
    import json as _json
    if 200 <= status < 300:
        return "PASS", "ok"
    if status in (401, 403):
        return "PASS", "auth-required (expected)"
    if status == 422 and method in ("POST", "PUT", "PATCH"):
        return "PASS", "route exists, validates body"
    if status == 404:
        # FastAPI's routing 404 always returns JSON {"detail":"Not Found"}.
        # An empty body or non-JSON body cannot be a handler response, so
        # treat both as the FastAPI default (route genuinely missing).
        is_default = True
        if body_text:
            try:
                parsed = _json.loads(body_text)
                if isinstance(parsed, dict) and parsed.get("detail") != "Not Found":
                    is_default = False
            except _json.JSONDecodeError:
                pass  # non-JSON -> FastAPI default
        if is_default:
            return "FAIL", "route does not exist"
        return "PASS", "route exists (handler returned 404)"
    if status == 405:
        return "FAIL", "wrong HTTP method"
    if status >= 500:
        return "FAIL", f"server error: {status}"
    return "PASS", f"non-fatal status: {status}"


# ── Brace-balanced body-key parsers ───────────────────────────────────────────

def _read_object_literal(s: str, start: int) -> str | None:
    """If s[start] is `{`, return the content between the matching braces
    (exclusive). Handles nested braces, strings, and escape sequences.
    Returns None when s[start] is not `{` or the object is unclosed.
    """
    if start >= len(s) or s[start] != "{":
        return None
    depth = 0
    in_str: str | None = None
    escaped = False
    for i in range(start, len(s)):
        ch = s[i]
        if escaped:
            escaped = False
            continue
        if in_str:
            if ch == "\\":
                escaped = True
            elif ch == in_str:
                in_str = None
            continue
        if ch in ('"', "'", "`"):
            in_str = ch
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start + 1 : i]
    return None


def _extract_body_field_keys(options_body: str) -> list[str]:
    """Given the inside of a fetch-options object, find the body field
    (body: JSON.stringify({...}) or body: {...}) and return the keys
    of the inner object.  Walks at brace depth 0, so `body:` is found
    whether it comes before or after `headers:`.
    """
    depth = 0
    i = 0
    in_str: str | None = None
    escaped = False
    body_start = -1
    n = len(options_body)
    while i < n:
        ch = options_body[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if in_str:
            if ch == "\\":
                escaped = True
            elif ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ('"', "'", "`"):
            in_str = ch
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif depth == 0 and options_body[i : i + 5] == "body:":
            body_start = i + 5
            break
        elif depth == 0 and options_body[i : i + 6] == "body :":
            body_start = i + 6
            break
        i += 1
    if body_start == -1:
        return []
    # Skip whitespace, optional JSON.stringify(.
    j = body_start
    while j < n and options_body[j] in " \t\n\r":
        j += 1
    _JSTRINGIFY = "JSON.stringify("          # 15 chars
    if options_body[j : j + 15] == _JSTRINGIFY:
        j += 15
        while j < n and options_body[j] in " \t\n\r":
            j += 1
    inner = _read_object_literal(options_body, j)
    if not inner:
        return []
    return _extract_top_level_keys(inner)


def _extract_top_level_keys(obj_body: str) -> list[str]:
    """Given the inside of an object literal, return its top-level keys.
    Handles shorthand (`{ email, password }`) and full form
    (`{ email: x, password: y }`).  Brace- and bracket-aware.
    """
    keys: list[str] = []
    depth = 0
    bracket_depth = 0
    in_str: str | None = None
    escaped = False
    entry_start = 0
    n = len(obj_body)

    def _capture(start: int, end: int) -> None:
        entry = obj_body[start:end].strip()
        if not entry:
            return
        if entry.startswith("..."):
            return  # spread -- ignore
        colon = entry.find(":")
        name = entry if colon == -1 else entry[:colon]
        name = name.strip()
        if re.match(r"^[A-Za-z_$][\w$]*$", name):
            keys.append(name)

    i = 0
    while i < n:
        ch = obj_body[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if in_str:
            if ch == "\\":
                escaped = True
            elif ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ('"', "'", "`"):
            in_str = ch
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "[":
            bracket_depth += 1
        elif ch == "]":
            bracket_depth -= 1
        elif ch == "," and depth == 0 and bracket_depth == 0:
            _capture(entry_start, i)
            entry_start = i + 1
        i += 1
    _capture(entry_start, n)
    return keys


def _extract_body_keys_balanced(content: str) -> list[dict]:
    """For each api.post/put/patch/delete or fetch(url, options) call in
    content, extract the URL, HTTP method, and body-object keys.

    Returns a list of dicts: [{url, method, body_keys, has_spread}].

    Uses a brace-balanced state machine so `body:` is found whether it
    appears before or after `headers:` inside the options object.
    """
    call_pattern = re.compile(
        r"(?:"
        r"\bfetch\s*\(\s*[`\"']([^`\"']+)[`\"']\s*,"
        r"|"
        r"\bapi\.(?P<method>post|put|patch|delete)\s*\(\s*"
        r"[`\"'](?P<api_url>[^`\"']+)[`\"']"
        r")",
    )

    results = []
    for m in call_pattern.finditer(content):
        url = m.group(1) or m.group("api_url")
        if not url:
            continue
        method = (m.group("method") or "POST").upper()

        # tail starts right after the URL (or after the comma for fetch).
        tail = content[m.end():]
        i = 0
        while i < len(tail) and tail[i] in " \t\n\r":
            i += 1

        if m.group("method"):
            # api.X(url, body) -- skip optional comma then read body literal.
            if i < len(tail) and tail[i] == ",":
                i += 1
                while i < len(tail) and tail[i] in " \t\n\r":
                    i += 1
            body_obj = _read_object_literal(tail, i)
            has_spread = bool(body_obj and re.search(r"\.\.\.[a-zA-Z_$]", body_obj))
            body_keys = _extract_top_level_keys(body_obj) if body_obj else []
        else:
            # fetch(url, options) -- read the options object, find body inside.
            options_obj = _read_object_literal(tail, i)
            inner_body = None
            if options_obj:
                # Locate the raw text after `body:` to detect spreads.
                j = options_obj.find("body:")
                inner_body = options_obj[j + 5:] if j != -1 else None
            has_spread = bool(inner_body and re.search(r"\.\.\.[a-zA-Z_$]", inner_body))
            body_keys = _extract_body_field_keys(options_obj) if options_obj else []

        results.append({
            "url": url,
            "method": method,
            "body_keys": sorted(set(body_keys)),
            "has_spread": has_spread,
        })
    return results


# ── agent ─────────────────────────────────────────────────────────────────────

class TesterAgent:
    """Runs install / lint / typecheck / boot checks on generated code.

    In mock mode: skips subprocess and Docker; uses in-process ast.parse for lint.
    In real mode: runs subprocess commands in the discovered backend/frontend dirs.
    """

    def __init__(self) -> None:
        self._last_openapi_schema: Optional[dict] = None
        self._last_boot_route_errors: str = ""

    def run_tests(self, generated_files: dict[str, str], plan: GenerationPlan) -> dict:
        """Run full test suite (install / lint / typecheck / boot).

        Returns: { status, errors, logs, passed_checks }
        passed_checks values are "passed" | "failed" | "skipped".
        status is "success" when no check is "failed" (skipped is not a failure).
        """
        log.info("test.start", num_files=len(generated_files), stack=plan.technology_stack)

        # Detect whether auth is enabled for this project so smoke/reachability
        # know whether to enforce auth endpoint tests.
        auth_enabled = "AUTH_DISABLED" not in (plan.notes or "")

        # ── Test 3: FE↔BE contract check (offline, no server) ───────────────
        # Run FIRST so cheap static failures surface immediately.
        fe_be_status, fe_be_log = self._run_fe_be_contract(generated_files, auth_enabled)

        if settings.mock_ai:
            result = self._test_mock(generated_files, plan)
        else:
            try:
                project_dir = self._write_files_to_temp(generated_files)
                result = self._test_real(
                    project_dir,
                    plan.technology_stack,
                    generated_files=generated_files,
                    auth_enabled=auth_enabled,
                )
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

        # Inject fe_be_contract result (runs regardless of mock/real).
        result["passed_checks"]["fe_be_contract"] = fe_be_status
        result["logs"]["fe_be_contract"] = fe_be_log
        if fe_be_status == "failed":
            result["errors"].append(f"fe_be_contract: {fe_be_log}")
            result["status"] = "failed"

        # ── Static route-ordering check (no server needed) ───────────────
        shadow_violations = self._check_route_ordering(generated_files)
        if shadow_violations:
            msgs = [
                f"ROUTE-SHADOW: {v['file']} — {v['shadowing_route']} declared before "
                f"{v['shadowed_route']}, will catch the path. Reorder."
                for v in shadow_violations
            ]
            result["errors"].extend(msgs)
            result["passed_checks"]["route_ordering"] = "failed"
            result["logs"]["route_ordering"] = "; ".join(msgs)
            result["status"] = "failed"
            for msg in msgs:
                log.warning("route_ordering.shadow", detail=msg)
        else:
            result["passed_checks"]["route_ordering"] = "passed"

        # ── Static route-link consistency check (no server needed) ─────────────
        link_violations = self._check_route_link_consistency(generated_files)
        if link_violations:
            log.warning(
                "test.route_link_consistency.violations",
                count=len(link_violations),
                sample=link_violations[:5],
            )
            result["route_link_violations"] = link_violations
            result.setdefault("real_failures", []).append("route_consistency")
            for v in link_violations:
                result["errors"].append(
                    f"ORPHAN-LINK: {v['file']} has {v['kind']} "
                    f"to='{v['target']}' — no matching <Route> in App.jsx"
                )
            result["passed_checks"]["route_link_consistency"] = "failed"
            result["logs"]["route_link_consistency"] = "; ".join(
                f"{v['file']}:{v['kind']}:{v['target']}" for v in link_violations
            )
            result["status"] = "failed"
        else:
            result["passed_checks"]["route_link_consistency"] = "passed"

        # ── Static navbar-visibility check (no server needed) ─────────────────
        navbar_violations = self._check_navbar_renders_links(generated_files)
        if navbar_violations:
            v = navbar_violations[0]
            msg = (
                f"NAVBAR-EMPTY: routes.js has {v['count_visible']} visible "
                f"links out of {v['count_total']} entries — {v['reason']}"
            )
            log.warning("test.navbar_renders_links.failed", detail=msg)
            result["errors"].append(msg)
            result["passed_checks"]["navbar_renders_links"] = "failed"
            result["logs"]["navbar_renders_links"] = msg
            result["status"] = "failed"
        else:
            result["passed_checks"]["navbar_renders_links"] = "passed"

        # Contract check — uses the OpenAPI schema fetched from the running backend.
        # Skipped in mock mode and when boot failed (no schema captured).
        contract_status, contract_data = self._contract_check(
            generated_files, self._last_openapi_schema, plan=plan
        )
        if contract_status == "failed":
            # Critical contract miss (AUTH-SHAPE, missing login/register) — blocks ZIP.
            for miss in contract_data:
                log.warning("contract_check.miss", miss=miss)
            result["errors"].extend(contract_data)
            result["passed_checks"]["contract"] = "failed"
            result["logs"]["contract"] = "; ".join(contract_data)
            result["status"] = "failed"
        elif contract_status == "advisory_failed":
            # Non-critical misses — log warnings but don't block ZIP.
            for miss in contract_data:
                log.warning("contract_check.advisory_miss", miss=miss)
            result["passed_checks"]["contract"] = "advisory_failed"
            result["logs"]["contract_advisory"] = "; ".join(contract_data)
            result["logs"]["contract"] = f"advisory: {len(contract_data)} non-critical miss(es)"
        else:
            result["passed_checks"]["contract"] = contract_status  # "passed" or "skipped"
            if contract_status == "skipped":
                result["logs"]["contract"] = contract_data

        # ── Advisory raw-fetch check (never blocks ZIP) ───────────────────────
        raw_fetch_status, raw_fetch_log = self._check_raw_fetch_usage(generated_files)
        result["passed_checks"]["raw_fetch_usage"] = raw_fetch_status
        result["logs"]["raw_fetch_usage"] = raw_fetch_log
        if raw_fetch_status == "advisory_failed":
            for offender in raw_fetch_log.split(": ", 1)[-1].split(", "):
                log.warning("raw_fetch.advisory", file=offender.strip())

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

    def _test_real(
        self,
        project_dir: str,
        stack: str,
        generated_files: Optional[dict] = None,
        auth_enabled: bool = True,
    ) -> dict:
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

            typecheck_status, typecheck_log = self._run_typecheck(
                tmpdir, stack, venv_python=venv_python
            )
            passed_checks["typecheck"] = typecheck_status
            logs["typecheck"] = typecheck_log

            boot_status, boot_log = self._run_boot(tmpdir, stack, venv_python=venv_python)
            passed_checks["boot"] = boot_status
            logs["boot"] = boot_log

            build_status, build_log = self._run_frontend_build(tmpdir)
            passed_checks["frontend_build"] = build_status
            logs["frontend_build"] = build_log

            if passed_checks.get("frontend_build") == "passed":
                # Run smoke + reachability together (shared server session).
                smoke_result = self._run_smoke(
                    tmpdir,
                    venv_python=venv_python,
                    auth_enabled=auth_enabled,
                    openapi_schema=self._last_openapi_schema,
                )
                passed_checks["smoke"] = smoke_result["smoke_status"]
                logs["smoke"] = smoke_result["smoke_log"]
                passed_checks["reachability"] = smoke_result["reachability_status"]
                logs["reachability"] = smoke_result["reachability_log"]

                # Browser smoke: serve built frontend, visit key routes via Playwright.
                frontend_dist = _find_frontend_dir(tmpdir)
                if frontend_dist is not None:
                    frontend_dist = frontend_dist / "dist"
                _default_routes = ["/", "/dashboard", "/login", "/register", "/menu", "/cart"]
                bs_status, bs_log = self._test_browser_smoke(
                    frontend_dist=frontend_dist or (tmpdir / "frontend" / "dist"),
                    backend_url=f"http://127.0.0.1:8002",
                    routes=_default_routes,
                )
                passed_checks["browser_smoke"] = bs_status
                logs["browser_smoke"] = bs_log

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
        """Returns (status, log_snippet, venv_python). status: 'passed' | 'failed' | 'skipped'.

        Uses a persistent venv cache keyed by requirements.txt hash so dep install
        is skipped on cache hits (~0 ms vs ~30 s for pip install).
        """
        if "python" in stack:
            backend_dir = _find_backend_dir(tmpdir)
            if backend_dir is None:
                log.warning("test_install.no_manifest", tmpdir=str(tmpdir))
                return "skipped", "No Python manifest found", None

            req_path = str(backend_dir / "requirements.txt")
            venv_python: Optional[Path] = None

            # Try the persistent venv cache first.
            if os.path.exists(req_path):
                venv_python = _get_or_create_venv(req_path)

            # Fall back to a per-cycle venv if cache is unavailable.
            if venv_python is None:
                venv_dir = tmpdir / ".testenv"
                try:
                    subprocess.run(
                        ["python", "-m", "venv", str(venv_dir)],
                        capture_output=True, timeout=60, text=True, check=True,
                    )
                    venv_python = venv_dir / "bin" / "python"
                    subprocess.run(
                        [str(venv_python), "-m", "pip", "install",
                         "--upgrade", "pip", "--quiet"],
                        capture_output=True, timeout=60, text=True,
                    )
                    py_cmd = [str(venv_python), "-m", "pip", "install",
                              "-r", "requirements.txt", "--quiet"]
                    py_result = subprocess.run(
                        py_cmd, cwd=backend_dir,
                        capture_output=True, timeout=180, text=True,
                    )
                    py_status = "passed" if py_result.returncode == 0 else "failed"
                    py_log = (py_result.stdout + py_result.stderr)[:500]
                    log.info(
                        "test_install",
                        status=py_status,
                        returncode=py_result.returncode,
                        cmd=" ".join(py_cmd),
                        stdout_tail=(py_result.stdout or "")[-2000:],
                        stderr_tail=(py_result.stderr or "")[-2000:],
                    )
                    if py_status == "failed":
                        return py_status, py_log, venv_python
                except Exception as exc:
                    log.warning("test_install.venv_creation_failed", error=str(exc))
                    return "failed", str(exc), None
            else:
                py_status = "passed"
                py_log = "venv cache hit"
                log.info("test_install", status=py_status, note="cache hit")

            # For full-stack projects (python-postgres), also install frontend deps.
            if stack == "python-postgres":
                frontend_dir = _find_frontend_dir(tmpdir)
                if frontend_dir is not None:
                    pkg_json = frontend_dir / "package.json"
                    # Try symlink from cache; fall back to regular npm install.
                    cached = _link_cached_node_modules(str(pkg_json), frontend_dir) \
                        if pkg_json.exists() else False
                    if not cached:
                        try:
                            npm_result = subprocess.run(
                                ["npm", "install"],
                                cwd=frontend_dir, capture_output=True,
                                timeout=180, text=True,
                            )
                            npm_status = "passed" if npm_result.returncode == 0 else "failed"
                            log.info(
                                "test_install_frontend",
                                status=npm_status,
                                returncode=npm_result.returncode,
                                stdout_tail=(npm_result.stdout or "")[-2000:],
                                stderr_tail=(npm_result.stderr or "")[-2000:],
                            )
                            if npm_status == "failed":
                                fe_log = (npm_result.stdout + npm_result.stderr)[:500]
                                return "failed", f"{py_log}\n[frontend] {fe_log}", venv_python
                        except FileNotFoundError:
                            log.warning("test.tool_missing", tool="npm",
                                        check="install_frontend")
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

    def _test_browser_smoke(
        self,
        frontend_dist: Path,
        backend_url: str,
        routes: list[str],
    ) -> tuple[str, str]:
        """Headless Chromium smoke test via Playwright.

        Serves the built frontend dist from a local HTTP server, visits each
        route, and fails if ANY page emits a JS pageerror or console.error.

        Catches: 'Objects are not valid as a React child', undefined prop
        errors, 'Cannot read properties of undefined', and any runtime crash
        the HTTP-only smoke test misses because those happen in the browser.

        Returns ("advisory_failed", reason) if Playwright is not installed or
        the dist directory doesn't exist — this never blocks deployment.
        """
        if not frontend_dist.exists():
            return ("skipped", "frontend dist not found")

        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
        except ImportError:
            return ("advisory_failed", "playwright not installed; run: pip install playwright && playwright install chromium")

        errors: list[dict] = []
        static_proc = None
        static_port = 5174  # distinct from Vite dev (5173) to avoid conflicts

        try:
            static_proc = subprocess.Popen(
                ["python", "-m", "http.server", str(static_port), "--directory", str(frontend_dist)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)  # wait for server to bind

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()

                page.on("pageerror", lambda exc: errors.append({
                    "route": page.url,
                    "kind": "pageerror",
                    "message": str(exc)[:500],
                }))
                page.on("console", lambda msg: (
                    errors.append({
                        "route": page.url,
                        "kind": "console.error",
                        "message": msg.text[:500],
                    }) if msg.type == "error" else None
                ))

                base = f"http://localhost:{static_port}"
                for route in routes:
                    try:
                        page.goto(f"{base}{route}", wait_until="networkidle", timeout=15000)
                        page.wait_for_timeout(500)
                    except Exception as exc:
                        errors.append({"route": route, "kind": "navigation", "message": str(exc)[:500]})

                browser.close()

        except Exception as exc:
            return ("advisory_failed", f"playwright crashed: {exc}")
        finally:
            if static_proc:
                static_proc.terminate()

        fatal = [e for e in errors if e["kind"] in ("pageerror", "console.error")]
        if fatal:
            sample = "; ".join(
                f"[{e['route']}] {e['message'][:120]}"
                for e in fatal[:5]
            )
            return ("failed", f"{len(fatal)} runtime JS errors across {len({e['route'] for e in fatal})} routes: {sample}")

        return ("passed", f"visited {len(routes)} routes, no runtime errors")

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

    def _run_typecheck(
        self, tmpdir: Path, stack: str, venv_python: Optional[Path] = None
    ) -> tuple[str, str]:
        if "python" in stack:
            if venv_python is None or not venv_python.exists():
                log.warning("test_typecheck.no_venv")
                return "skipped", "no venv available for mypy"

            # Install mypy + SQLAlchemy mypy plugin into the per-cycle venv.
            # sqlalchemy[mypy] resolves Column/Mapped attribute types so we get
            # ~0 ORM noise errors instead of ~50.
            try:
                subprocess.run(
                    [str(venv_python), "-m", "pip", "install", "--quiet",
                     "mypy", "sqlalchemy[mypy]"],
                    check=True, capture_output=True, text=True, timeout=120,
                )
            except Exception as exc:
                log.error("test_typecheck.mypy_install_failed", error=str(exc))
                return "skipped", f"mypy install failed: {exc}"

            # Write a mypy.ini that enables the SQLAlchemy plugin and disables
            # the error codes that fire as noise on correct LLM-generated code
            # (assignment variance, return-value from ORM queries, etc.).
            # Real bugs — attr-defined, name-defined, call-arg, misc — still fire.
            mypy_ini = """\
[mypy]
plugins = sqlalchemy.ext.mypy.plugin
ignore_missing_imports = True
no_strict_optional = True
allow_untyped_defs = True
check_untyped_defs = True
warn_return_any = False
warn_unused_ignores = False
show_error_codes = True
no_color_output = True
disable_error_code = no-redef, assignment, return-value, arg-type

[mypy-sqlalchemy.*]
ignore_missing_imports = True

[mypy-passlib.*]
ignore_missing_imports = True

[mypy-jose.*]
ignore_missing_imports = True
"""
            mypy_ini_path = os.path.join(str(tmpdir), "mypy.ini")
            with open(mypy_ini_path, "w") as _f:
                _f.write(mypy_ini)

            result = subprocess.run(
                [
                    str(venv_python), "-m", "mypy",
                    "--config-file", mypy_ini_path,
                    "backend/app",
                ],
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=120,
            )
            # typecheck is ADVISORY: failures log and surface to UI but never block ZIP.
            status = "passed" if result.returncode == 0 else "advisory_failed"
            output = (result.stdout or "") + (result.stderr or "")
            log.info(
                "test_typecheck",
                status=status,
                returncode=result.returncode,
                stdout_tail=(result.stdout or "")[-2000:],
            )
            return status, output[-2000:]

        else:
            # Node/TypeScript stack — use the project's own typecheck script.
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
                    stdout_tail=(result.stdout or "")[-2000:],
                    stderr_tail=(result.stderr or "")[-2000:],
                    cmd=" ".join(cmd),
                )
                return status, ((result.stdout or "") + (result.stderr or ""))[:500]
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
                # Pre-flight: verify app.main imports cleanly before starting uvicorn.
                # Uvicorn swallows import tracebacks; this captures them in full.
                try:
                    pre_check = subprocess.run(
                        [str(venv_python), "-c", "from app.main import app; print('IMPORT_OK')"],
                        cwd=cwd,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                except subprocess.TimeoutExpired:
                    log.error("test_boot.import_failed", stderr="import check timed out", stdout="")
                    log.info(
                        "test_boot",
                        status="failed",
                        stdout_tail="",
                        stderr_tail="import check timed out",
                        cmd="preflight: from app.main import app",
                    )
                    return (
                        "failed",
                        "backend_import_error: import timed out "
                        "(possible circular import or blocking module-level code)",
                    )
                except Exception as pre_exc:
                    log.warning("test_boot.preflight_error", error=str(pre_exc))
                else:
                    if pre_check.returncode != 0:
                        full_tb = (pre_check.stderr or "") + (pre_check.stdout or "")
                        log.error(
                            "test_boot.import_failed",
                            stderr=pre_check.stderr,
                            stdout=pre_check.stdout,
                        )
                        log.info(
                            "test_boot",
                            status="failed",
                            stdout_tail=(pre_check.stdout or "")[-2000:],
                            stderr_tail=(pre_check.stderr or "")[-2000:],
                            cmd="preflight: from app.main import app",
                        )
                        return "failed", f"backend_import_error:\n{full_tb[-2000:]}"
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
                # Surface route import failures even when the server booted.
                # The try/except in _fix_missing_route_includes prints these to stderr.
                failed_routes = re.findall(
                    r"\[startup\] FAILED to load (app\.routes\.\w+): ([^\n]+)",
                    err or "",
                )
                if failed_routes:
                    route_err_lines = [f"{m}: {e}" for m, e in failed_routes]
                    route_err_msg = "; ".join(route_err_lines)
                    log.error(
                        "boot.route_import_failed",
                        routes=[m for m, _ in failed_routes],
                        errors=route_err_lines,
                    )
                    # Store in the logs dict so the debugger can see the real exception.
                    self._last_boot_route_errors = route_err_msg
                    # Treat as boot failure — a router that can't load means its
                    # endpoints never appear in /openapi.json.
                    status = "failed"
                    output = f"route_import_failed: {route_err_msg}"
                else:
                    self._last_boot_route_errors = ""
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

    def _run_smoke(
        self,
        tmpdir: Path,
        venv_python: Optional[Path] = None,
        auth_enabled: bool = True,
        openapi_schema: Optional[dict] = None,
    ) -> dict:
        """HTTP-based smoke test: starts the backend and hits API endpoints directly.

        Strict auth checks (when auth_enabled):
          - POST /api/auth/register → 2xx or 409 (already exists) required
          - POST /api/auth/login with demo credentials → MUST return 200 + token
          - GET  /api/auth/me with Bearer token → MUST return 200

        Also runs reachability probes against all contract paths using the same
        live server, so we don't pay startup cost twice.

        Returns a dict with keys:
          smoke_status, smoke_log      — auth check result
          reachability_status, reachability_log — endpoint probe result
        """
        import urllib.request
        import urllib.error

        _INFRA_RESULT = lambda status, msg: {  # noqa: E731
            "smoke_status": status,
            "smoke_log": msg,
            "reachability_status": "skipped",
            "reachability_log": "skipped (smoke did not reach server)",
        }

        backend_dir = _find_backend_dir(tmpdir)
        if not backend_dir:
            return _INFRA_RESULT("skipped", "no backend dir")
        if venv_python is None or not venv_python.exists():
            return _INFRA_RESULT("skipped", "no venv available for smoke test")

        backend_port = 8002  # distinct from boot's 8001 to avoid port reuse race
        base = f"http://127.0.0.1:{backend_port}"
        backend_proc = None
        _stderr_tmp = None
        try:
            import tempfile as _tempfile
            _stderr_tmp = _tempfile.NamedTemporaryFile(
                mode="w", suffix=".smoke_stderr.txt", delete=False
            )
            backend_proc = subprocess.Popen(
                [str(venv_python), "-m", "uvicorn", "app.main:app",
                 "--host", "127.0.0.1", "--port", str(backend_port)],
                cwd=backend_dir,
                stdout=subprocess.DEVNULL,
                stderr=_stderr_tmp,
            )

            # Wait up to 15s for the backend to start
            openapi: dict = openapi_schema or {}
            for _ in range(15):
                try:
                    with urllib.request.urlopen(
                        f"{base}/openapi.json", timeout=2
                    ) as resp:
                        openapi = json.loads(resp.read())
                    break
                except Exception:
                    time.sleep(1)
            else:
                return _INFRA_RESULT(
                    "advisory_failed",
                    "backend did not start within 15s for smoke test",
                )

            paths = openapi.get("paths", {})

            # ── HTTP helpers ──────────────────────────────────────────────────

            def _post_json(url: str, payload: dict) -> tuple[int, dict]:
                data = json.dumps(payload).encode()
                req = urllib.request.Request(
                    url, data=data,
                    headers={"Content-Type": "application/json"},
                )
                try:
                    with urllib.request.urlopen(req, timeout=10) as r:
                        body = json.loads(r.read()) if r.read else {}
                        return r.status, body
                except urllib.error.HTTPError as e:
                    try:
                        body = json.loads(e.read())
                    except Exception:
                        body = {}
                    return e.code, body
                except Exception:
                    return 0, {}

            def _get(url: str, token: Optional[str] = None) -> tuple[int, dict]:
                req = urllib.request.Request(url)
                if token:
                    req.add_header("Authorization", f"Bearer {token}")
                try:
                    with urllib.request.urlopen(req, timeout=10) as r:
                        try:
                            body = json.loads(r.read())
                        except Exception:
                            body = {}
                        return r.status, body
                except urllib.error.HTTPError as e:
                    try:
                        body = json.loads(e.read())
                    except Exception:
                        body = {}
                    return e.code, body
                except Exception:
                    return 0, {}

            # ── Auth smoke checks ─────────────────────────────────────────────

            token: Optional[str] = None
            smoke_status = "passed"
            smoke_log = ""

            if auth_enabled:
                auth_paths = [p for p in paths if "login" in p or "register" in p]
                if not auth_paths:
                    # No auth endpoints at all — that's a hard contract failure.
                    smoke_status = "failed"
                    smoke_log = f"no auth endpoints in OpenAPI (got {list(paths)[:5]})"
                    log.error("smoke.no_auth_endpoints", paths=list(paths)[:10])
                else:
                    # Step 1: Register a fresh smoke user (201 or 409/422 are fine).
                    register_path = next((p for p in paths if "register" in p), None)
                    register_status = 0
                    if register_path:
                        register_status, register_body = _post_json(
                            f"{base}{register_path}",
                            {"email": f"smoke_{int(time.time())}@example.com",
                             "password": "smoke1234", "name": "Smoke Test"},
                        )
                        if register_status >= 500:
                            # Capture response body for diagnostics.
                            try:
                                response_body_str = str(register_body)[:800]
                            except Exception:
                                response_body_str = "<could not read body>"

                            # Drain available stderr from the uvicorn process.
                            subprocess_stderr = ""
                            try:
                                if _stderr_tmp is not None:
                                    _stderr_tmp.flush()
                                    import os as _os
                                    _ssize = _os.path.getsize(_stderr_tmp.name)
                                    with open(_stderr_tmp.name, "r", errors="replace") as _sf:
                                        _sf.seek(max(0, _ssize - 4000))
                                        subprocess_stderr = _sf.read()
                            except Exception:
                                pass

                            smoke_status = "failed"
                            smoke_log = (
                                f"register {register_path} returned {register_status} "
                                f"(server error — auth scaffold broken). "
                                f"Response body: {response_body_str}. "
                                f"Backend stderr tail: "
                                f"{subprocess_stderr[-1500:] if subprocess_stderr else '<none>'}"
                            )
                            log.error(
                                "smoke.register_server_error",
                                path=register_path,
                                status=register_status,
                                body_preview=response_body_str[:400],
                                stderr_tail=subprocess_stderr[-2000:],
                            )
                        else:
                            log.info("smoke.register_ok", status=register_status)

                    if smoke_status == "passed":
                        # Step 2: Login with seeded demo credentials — MUST return token.
                        login_path = next((p for p in paths if "login" in p), None)
                        if login_path:
                            login_status, login_body = _post_json(
                                f"{base}{login_path}",
                                {"email": "demo@example.com", "password": "demo1234"},
                            )
                            token = login_body.get("access_token") if isinstance(login_body, dict) else None
                            if login_status == 200 and token:
                                log.info("smoke.login_jwt_obtained")
                            else:
                                smoke_status = "failed"
                                smoke_log = (
                                    f"Login FAILED: {login_path} returned {login_status} "
                                    f"(body={str(login_body)[:200]}). "
                                    "demo@example.com/demo1234 seed data may be missing or "
                                    "the auth route has a bug."
                                )
                                log.error("smoke.login_strict_failed",
                                          register_status=register_status,
                                          login_status=login_status,
                                          login_body=str(login_body)[:200])

                    if smoke_status == "passed" and token:
                        # Step 3: GET /me WITH token — MUST return 200.
                        me_path = next((p for p in paths if p.endswith("/me")), None)
                        if me_path:
                            me_status, me_body = _get(f"{base}{me_path}", token=token)
                            if me_status == 200:
                                log.info("smoke.me_ok", path=me_path)
                            else:
                                smoke_status = "failed"
                                smoke_log = (
                                    f"GET {me_path} with valid Bearer token returned "
                                    f"{me_status} (expected 200). "
                                    f"Body: {str(me_body)[:200]}"
                                )
                                log.error("smoke.me_strict_failed",
                                          status=me_status, body=str(me_body)[:200])

                if smoke_status == "passed":
                    smoke_log = (
                        f"smoke passed: {len(paths)} endpoints, "
                        f"auth_endpoints={len(auth_paths)}, "
                        f"jwt={'yes' if token else 'no'}"
                    )
                    log.info("test_smoke.passed", summary=smoke_log)
            else:
                # Auth disabled — skip auth checks, just verify server responds.
                smoke_log = "smoke skipped: AUTH_DISABLED project"
                log.info("smoke.auth_disabled_skipped")

            # ── Reachability probes ───────────────────────────────────────────
            reach_status, reach_log = self._run_reachability(
                base_url=base,
                openapi_schema=openapi,
                auth_token=token,
            )

            return {
                "smoke_status": smoke_status,
                "smoke_log": smoke_log,
                "reachability_status": reach_status,
                "reachability_log": reach_log,
            }

        except Exception as exc:
            log.error("test_smoke.exception", error=str(exc))
            return {
                "smoke_status": "advisory_failed",
                "smoke_log": f"smoke exception: {exc}",
                "reachability_status": "skipped",
                "reachability_log": "skipped (smoke exception)",
            }
        finally:
            if backend_proc is not None:
                backend_proc.terminate()
                try:
                    backend_proc.wait(timeout=5)
                except Exception:
                    backend_proc.kill()
            if _stderr_tmp is not None:
                try:
                    _stderr_tmp.close()
                    import os as _os
                    _os.unlink(_stderr_tmp.name)
                except Exception:
                    pass

    def _run_reachability(
        self,
        base_url: str,
        openapi_schema: dict,
        auth_token: Optional[str],
    ) -> tuple[str, str]:
        """Probe every path in openapi_schema and classify the response.

        PASS verdicts: 2xx, 401/403 (auth-required), 422 on write verbs (body validates),
          handler-returned 404 (route exists but row not found).
        FAIL verdicts: FastAPI default 404 (route missing), 405 (wrong method), 5xx.

        Returns (status, log_summary).
        """
        import urllib.request
        import urllib.error

        paths = openapi_schema.get("paths", {}) if openapi_schema else {}
        if not paths:
            return "skipped", "no openapi paths to probe"

        results: list[dict] = []
        for path_template, methods_obj in paths.items():
            for method_lower in (methods_obj or {}):
                method = method_lower.upper()
                if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
                    continue
                # Substitute path params with "1" — server will accept the path shape.
                probe_path = re.sub(r"\{[^}]+\}", "1", path_template)
                url = f"{base_url}{probe_path}"
                headers: dict[str, str] = {"Content-Type": "application/json"}
                if auth_token:
                    headers["Authorization"] = f"Bearer {auth_token}"
                body_data = b"{}" if method in ("POST", "PUT", "PATCH") else None
                req = urllib.request.Request(url, data=body_data, headers=headers, method=method)
                status = None
                body_text = ""
                try:
                    with urllib.request.urlopen(req, timeout=5) as r:
                        status = r.status
                        try:
                            body_text = r.read(512).decode("utf-8", errors="replace")
                        except Exception:
                            body_text = ""
                except urllib.error.HTTPError as e:
                    status = e.code
                    try:
                        body_text = e.read(512).decode("utf-8", errors="replace")
                    except Exception:
                        body_text = ""
                except Exception as exc:
                    results.append({
                        "path": path_template, "method": method,
                        "verdict": "FAIL", "reason": f"exception: {exc}", "status": 0,
                    })
                    continue

                verdict, reason = _classify_reachability_status(status, method, body_text)

                results.append({
                    "path": path_template, "method": method,
                    "verdict": verdict, "reason": reason, "status": status,
                })

        failures = [r for r in results if r["verdict"] == "FAIL"]
        log.info("test_reachability.done", total=len(results), failures=len(failures))
        for f in failures:
            log.warning("reachability.fail", **f)

        if not results:
            return "skipped", "no endpoints to probe"
        if not failures:
            return "passed", f"all {len(results)} endpoints reachable"
        fail_summary = "; ".join(
            f"{r['method']} {r['path']} → {r['reason']}" for r in failures[:10]
        )
        return "failed", f"{len(failures)}/{len(results)} endpoints unreachable: {fail_summary}"

    def _run_fe_be_contract(
        self,
        generated_files: dict[str, str],
        auth_enabled: bool = True,
    ) -> tuple[str, str]:
        """Offline static check: compare frontend API call shapes to backend Pydantic models.

        Catches:
          - Missing body fields (frontend sends {email} but backend expects {email, name})
          - Extra body fields (usually harmless but may indicate wrong endpoint)
          - OAuth2PasswordRequestForm vs JSON LoginRequest mismatch (classic 422 bug)

        Controlled by DEMAESTRO_FE_BE_CONTRACT_STRICT env var (default "false" —
        check is advisory until the parser matures). Set to "true" to block ZIP.
        """
        strict = os.environ.get("DEMAESTRO_FE_BE_CONTRACT_STRICT", "false").lower() == "true"

        # ── 1. Extract frontend API calls (brace-balanced parser) ─────────────
        fe_calls: list[dict] = []

        for path, content in generated_files.items():
            if not path.startswith("frontend/src/"):
                continue
            if not re.search(r"\.(js|jsx|ts|tsx)$", path):
                continue
            for call in _extract_body_keys_balanced(content):
                fe_calls.append({
                    "file": path,
                    "method": call["method"],
                    "url": call["url"],
                    "body_keys": call["body_keys"],
                    "has_spread": call.get("has_spread", False),
                    "has_body_key": bool(call["body_keys"]),
                })

        # ── 2. Extract backend Pydantic models per route ──────────────────────
        backend_routes: list[dict] = []
        # [^"']* (zero or more) so @router.post("") with empty path is matched.
        _ROUTE_RE = re.compile(
            r'@router\.(?P<method>post|put|patch)\s*\(\s*["\'](?P<path>[^"\']*)["\'][^)]*\)\s*\n'
            r'(?:async\s+)?def\s+\w+\s*\((?P<params>[^)]*)\)',
            re.DOTALL | re.IGNORECASE,
        )
        _ROUTER_PREFIX_RE = re.compile(
            r'APIRouter\s*\([^)]*prefix\s*=\s*["\']([^"\']+)["\']',
        )
        _FIELD_RE = re.compile(r"^\s{4}(\w+)\s*:", re.MULTILINE)

        for path, content in generated_files.items():
            if not path.startswith("backend/app/routes/") or not path.endswith(".py"):
                continue
            # Extract the router's own prefix so decorator paths can be combined.
            router_prefix_m = _ROUTER_PREFIX_RE.search(content)
            router_prefix = router_prefix_m.group(1).rstrip("/") if router_prefix_m else ""

            for m in _ROUTE_RE.finditer(content):
                method = m.group("method").upper()
                deco_path = m.group("path")
                # Full path = router prefix + decorator path
                route_path = (router_prefix + ("/" if deco_path and not deco_path.startswith("/") else "") + deco_path).rstrip("/") or "/"
                params = m.group("params")
                # First PascalCase param without default is the body model.
                body_m = re.search(r"(\w+)\s*:\s*([A-Z]\w*)(?!\s*=)", params)
                if not body_m:
                    continue
                model_name = body_m.group(2)
                # Skip FastAPI built-ins that aren't Pydantic body models.
                if model_name in ("Session", "Depends", "HTTPException", "Request"):
                    continue
                # Find the model class definition anywhere in backend files.
                model_fields: list[str] = []
                for p2, c2 in generated_files.items():
                    if not p2.endswith(".py"):
                        continue
                    cm = re.search(
                        rf"class\s+{re.escape(model_name)}\s*\([^)]*BaseModel[^)]*\)\s*:"
                        rf"((?:\n[ \t]+[^\n]+)*)",
                        c2,
                    )
                    if cm:
                        model_fields = sorted(set(_FIELD_RE.findall(cm.group(1))))
                        break
                backend_routes.append({
                    "method": method, "path": route_path,
                    "model": model_name, "fields": model_fields,
                })

        # ── 3. Match and compare shapes ───────────────────────────────────────
        mismatches: list[dict] = []

        for call in fe_calls:
            # Normalize: strip /api prefix (backend declares without it).
            probe_url = re.sub(r"^/api", "", call["url"])
            # Normalize template params: /items/${id} → /items/{id}
            probe_url = re.sub(r"\$\{[^}]+\}", "{id}", probe_url).rstrip("/")

            for route in backend_routes:
                r_path = route["path"].rstrip("/")
                # Normalize backend path params: /items/{item_id} → /items/{id}
                r_norm = re.sub(r"\{[^}]+\}", "{id}", r_path)
                p_norm = re.sub(r"\{[^}]+\}", "{id}", probe_url)
                if r_norm != p_norm:
                    continue
                if route["method"] != call["method"]:
                    continue
                fe = set(call["body_keys"])
                be = set(route["fields"])
                # Filter out common non-field tokens (react variable refs etc.)
                be_significant = {
                    f for f in be
                    if not f.startswith("_") and f not in ("id", "created_at", "updated_at")
                }
                missing = be_significant - fe
                if missing:
                    # Downgrade: spread operator means we can't statically prove shape.
                    if call.get("has_spread"):
                        log.info(
                            "fe_be_contract.spread_downgrade",
                            url=call["url"], missing=sorted(missing),
                        )
                        break
                    # Downgrade: body_keys is empty AND the file has a `body:` key
                    # near the call site → parser likely missed the body argument.
                    if not fe and call.get("has_body_key"):
                        log.info(
                            "fe_be_contract.empty_body_downgrade",
                            url=call["url"], missing=sorted(missing),
                        )
                        break
                    mismatches.append({
                        "url": call["url"], "method": call["method"],
                        "file": call["file"], "model": route["model"],
                        "frontend_sends": sorted(fe),
                        "backend_expects": sorted(be_significant),
                        "missing_from_frontend": sorted(missing),
                    })
                break

        # ── 4. OAuth2PasswordRequestForm vs JSON special case ─────────────────
        # This is the most common 422 bug: backend uses form-encoded, frontend sends JSON.
        if auth_enabled:
            for p2, c2 in generated_files.items():
                if "auth_routes" not in p2 and "auth.py" not in p2:
                    continue
                if "OAuth2PasswordRequestForm" not in c2:
                    continue
                for call in fe_calls:
                    if "/auth/login" not in call["url"] and "/login" not in call["url"]:
                        continue
                    if "username" not in call["body_keys"]:
                        mismatches.append({
                            "url": call["url"],
                            "kind": "oauth2_form_vs_json",
                            "file": call["file"],
                            "fix": (
                                "Backend uses OAuth2PasswordRequestForm (expects form-encoded "
                                "'username'). Frontend sends JSON with fields "
                                f"{call['body_keys']}. Either change backend to accept "
                                "LoginRequest JSON body, or change frontend to URLSearchParams "
                                "with 'username' field."
                            ),
                        })

        log.info(
            "test_fe_be_contract.done",
            fe_calls=len(fe_calls),
            backend_routes=len(backend_routes),
            mismatches=len(mismatches),
        )
        for mm in mismatches:
            log.warning("fe_be_contract.mismatch", **mm)

        if not mismatches:
            return "passed", (
                f"fe_be_contract passed: {len(fe_calls)} frontend calls, "
                f"{len(backend_routes)} backend routes checked"
            )

        summary = "; ".join(
            f"{m.get('method','?')} {m.get('url','?')}: missing {m.get('missing_from_frontend', m.get('kind', '?'))}"
            for m in mismatches[:5]
        )
        status = "failed" if strict else "advisory_failed"
        return status, f"{len(mismatches)} shape mismatch(es): {summary}"

    # ── contract checks ───────────────────────────────────────────────────────

    def _extract_all_frontend_api_calls(self, generated_files: dict[str, str]) -> list[tuple[str, str]]:
        """Build deduplicated (METHOD, normalized-path) list from all frontend files.

        Paths from api.X() calls are normalized to include /api prefix so they
        can be compared directly to OpenAPI backend paths.
        """
        calls: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for path, content in generated_files.items():
            if not any(path.endswith(ext) for ext in (".jsx", ".tsx", ".js", ".ts")):
                continue
            if path.endswith("/api.js") or path.endswith("/api.ts"):
                continue
            for m in _FRONTEND_API_CALL_RE.finditer(content or ""):
                method = m.group("method").upper()
                # All _FRONTEND_API_CALL_RE matches are api.X / axios.X calls —
                # they go through the api client which prepends /api at runtime.
                route_path = _normalize_frontend_path(m.group("path"), from_api_client=True)
                key = (method, route_path)
                if key in seen:
                    continue
                seen.add(key)
                calls.append(key)
        return calls

    def _map_frontend_calls_to_source(
        self, generated_files: dict[str, str]
    ) -> dict[tuple[str, str], str]:
        """Return a (METHOD, normalized-path) → first-source-file mapping."""
        result: dict[tuple[str, str], str] = {}
        for file_path, content in generated_files.items():
            if not any(file_path.endswith(ext) for ext in (".jsx", ".tsx", ".js", ".ts")):
                continue
            if file_path.endswith("/api.js") or file_path.endswith("/api.ts"):
                continue
            for m in _FRONTEND_API_CALL_RE.finditer(content or ""):
                route_path = _normalize_frontend_path(m.group("path"), from_api_client=True)
                key = (m.group("method").upper(), route_path)
                result.setdefault(key, file_path)
        return result

    # Contract misses on these paths are REAL failures (always block ZIP).
    _CRITICAL_CONTRACT_ENDPOINTS = frozenset({
        ("POST", "/api/auth/login"),
        ("POST", "/api/auth/register"),
        ("GET", "/api/auth/me"),
    })
    # Scaffold auth check emits this tag; _is_critical matches it.
    _AUTH_SCAFFOLD_PATTERN = "AUTH-SCAFFOLD"

    def _check_route_ordering(self, files: dict) -> list:
        """Static check: find route files where a parametrized route is declared
        before a literal sibling and would shadow it (producing a 422).

        Returns a list of violation dicts (empty = no violations).
        No server required — pure source analysis.
        """
        violations = []
        for path, content in files.items():
            if not (path.startswith("backend/app/routes/") and path.endswith(".py")):
                continue
            decorators = _ROUTE_DECORATOR_RE.findall(content or "")
            for i, (mi, pi) in enumerate(decorators):
                for j in range(i + 1, len(decorators)):
                    mj, pj = decorators[j]
                    if mi.lower() != mj.lower():
                        continue
                    if _path_shadows(pi, pj):
                        violations.append({
                            "file": path,
                            "shadowing_route": f"{mi.upper()} {pi}",
                            "shadowed_route":  f"{mj.upper()} {pj}",
                            "decl_order": (i, j),
                        })
        return violations

    def _check_navbar_renders_links(self, files: dict) -> list[dict]:
        """Return violations when routes.js has zero show_in_nav:true entries.

        An empty navbar is a structural failure — the app ships with no
        navigation links, which breaks UX completely.  Classified as REAL
        (blocks ZIP) so the debugger's visibility-promotion fixer gets a
        chance to run.

        Returns a list of dicts with keys: check, count_visible, reason.
        Empty list means the check passed.
        """
        routes_js = files.get("frontend/src/lib/routes.js", "")
        if not routes_js:
            return []

        # Count entries with show_in_nav: true
        true_count = len(re.findall(r'\bshow_in_nav\s*:\s*true\b', routes_js))
        if true_count > 0:
            return []  # at least one visible link — pass

        # Zero visible entries.  Distinguish "no show_in_nav field at all"
        # from "all explicitly false" to give a useful error message.
        false_count = len(re.findall(r'\bshow_in_nav\s*:\s*false\b', routes_js))
        total_entries = len(re.findall(r'path\s*:\s*["\']', routes_js))

        if total_entries == 0:
            return []  # no ROUTES entries at all — routes.js itself is broken

        reason = (
            f"all {false_count} routes have show_in_nav: false"
            if false_count > 0
            else f"{total_entries} routes have no show_in_nav field and the nav "
                 "filter uses show_in_nav && — Navbar renders empty"
        )
        return [{
            "check": "navbar_renders_links",
            "count_visible": true_count,
            "count_total": total_entries,
            "reason": reason,
        }]

    def _check_route_link_consistency(self, files: dict) -> list[dict]:
        """Flag any <Link to='/X'> or navigate('/X') in frontend code where
        /X has no matching <Route path='/X' /> in App.jsx.

        Handles parameterised routes: /menu-items/:id and /menu-items/{id}
        both match a static link to /menu-items/42.
        """
        violations: list[dict] = []
        app_jsx = files.get("frontend/src/App.jsx", "")
        if not app_jsx:
            return violations

        route_paths = set(re.findall(
            r'<Route\s+[^>]*path\s*=\s*["\']([^"\']+)["\']',
            app_jsx,
        ))
        if not route_paths:
            return violations

        def _path_exists(target: str) -> bool:
            if target in route_paths:
                return True
            target_segs = target.strip("/").split("/")
            for route in route_paths:
                route_segs = route.strip("/").split("/")
                if len(route_segs) != len(target_segs):
                    continue
                ok = True
                for rs, ts in zip(route_segs, target_segs):
                    if rs.startswith(":") or rs.startswith("{"):
                        continue
                    if rs != ts:
                        ok = False
                        break
                if ok:
                    return True
            return False

        link_to_re = re.compile(
            r'<Link\s+[^>]*to\s*=\s*["\']([^"\']+)["\']'
        )
        navigate_re = re.compile(
            r'navigate\s*\(\s*["\']([^"\']+)["\']'
        )

        for path, content in files.items():
            if not (path.startswith("frontend/src/") and
                    path.endswith((".jsx", ".tsx"))):
                continue
            if path == "frontend/src/App.jsx":
                continue

            for match in link_to_re.finditer(content):
                target = match.group(1)
                if target.startswith(("#", "http", "mailto:", "tel:")):
                    continue
                if not target.startswith("/"):
                    continue
                if not _path_exists(target):
                    violations.append({
                        "file": path,
                        "kind": "Link",
                        "target": target,
                        "reason": "no matching <Route> in App.jsx",
                    })

            for match in navigate_re.finditer(content):
                target = match.group(1)
                if target.startswith("/") and not _path_exists(target):
                    violations.append({
                        "file": path,
                        "kind": "navigate",
                        "target": target,
                        "reason": "no matching <Route> in App.jsx",
                    })

        return violations

    def _contract_check(
        self,
        generated_files: dict[str, str],
        openapi_schema: Optional[dict],
        plan=None,
    ) -> tuple[str, list | str]:
        """Return (status, data).

        status is 'passed', 'failed' (critical miss), 'advisory_failed'
        (non-critical miss), or 'skipped'.
        data is a list of CONTRACT MISS strings, or a reason string when skipped.
        """
        if not openapi_schema:
            return ("skipped", "no openapi schema available (boot may have failed)")

        # ── Doubled /api prefix check ─────────────────────────────────────────
        # Any /api/api/... path is a normalization bug, not a real contract miss.
        # Flag it immediately so the debugger can identify and fix the root cause.
        doubled_paths = [
            p for p in (openapi_schema.get("paths") or {})
            if "/api/api/" in p
        ]
        if doubled_paths:
            log.error("contract_check.doubled_prefix", paths=doubled_paths)
            return ("failed", [
                f"CONTRACT MISS: DOUBLED-PREFIX {p} — "
                "route file declares APIRouter(prefix=\"/api/...\") AND main.py "
                "mounts it with prefix=\"/api\", producing a doubled path. "
                "Fix: strip /api from the route file's APIRouter prefix so "
                "only main.py contributes /api."
                for p in doubled_paths
            ])

        misses: list[str] = []

        # Determine whether auth is enabled for this project.  The orchestrator
        # embeds "AUTH_DISABLED" in plan.notes when the requirements explicitly
        # opted out of auth (e.g. a public-only cat gallery app).
        _plan_notes = (getattr(plan, "notes", None) or "") if plan else ""
        _auth_enabled = "AUTH_DISABLED" not in _plan_notes

        # ── Auth scaffold presence check ─────────────────────────────────────
        # The static auth scaffold guarantees these three endpoints in every app
        # that has auth enabled.  Skip entirely for public-only apps.
        backend_routes = _extract_backend_routes_from_openapi(openapi_schema)
        backend_paths_by_method: dict[str, set[str]] = {}
        for bmethod, bpath in backend_routes:
            backend_paths_by_method.setdefault(bmethod, set()).add(bpath)

        if not _auth_enabled:
            log.info(
                "contract_check.auth_skipped_by_blueprint",
                reason="AUTH_DISABLED in plan.notes — public-only app",
            )
        else:
            _REQUIRED_AUTH_ROUTES = [
                ("POST", "/api/auth/register"),
                ("POST", "/api/auth/login"),
                ("GET", "/api/auth/me"),
            ]
            for method, path in _REQUIRED_AUTH_ROUTES:
                if path not in backend_paths_by_method.get(method, set()):
                    misses.append(
                        f"CONTRACT MISS: AUTH-SCAFFOLD {method} {path} not in OpenAPI — "
                        "add `from app.routes.auth_routes import router as auth_router` "
                        "and `app.include_router(auth_router, prefix='/api')` to main.py"
                    )

        # ── METHOD-MISMATCH / ADMIN-PREFIX check (OpenAPI ground-truth) ───────
        # backend_routes and backend_paths_by_method already computed above.

        frontend_calls = self._extract_all_frontend_api_calls(generated_files)
        call_to_source = self._map_frontend_calls_to_source(generated_files)
        all_backend_paths = {p for s in backend_paths_by_method.values() for p in s}

        if backend_paths_by_method and frontend_calls:
            for method, path in frontend_calls:
                # ── ADMIN-PREFIX check ────────────────────────────────────
                # An admin component calling /api/<resource> when the backend
                # only serves /api/admin/<resource> → 404/405 in production.
                # Intercept BEFORE the generic miss logic and emit a targeted miss.
                if (
                    path.startswith("/api/")
                    and not path.startswith("/api/admin/")
                    and not path.startswith("/api/auth/")
                ):
                    _src = call_to_source.get((method, path), "")
                    if _src and _is_admin_component(_src):
                        _resource = path[len("/api/"):].split("/")[0]
                        _admin_base = f"/api/admin/{_resource}"
                        _admin_exists = any(
                            p == _admin_base or p.startswith(_admin_base + "/")
                            for p in all_backend_paths
                        )
                        if _admin_exists:
                            _src_name = _src.rsplit("/", 1)[-1]
                            misses.append(
                                f"CONTRACT MISS: ADMIN-PREFIX {method} {path} "
                                f"(called from {_src_name}) "
                                f"backend serves {_admin_base} — frontend missing "
                                f"/admin/ prefix. "
                                f"suggestions: change to {_admin_base}"
                            )
                            continue  # don't also emit the generic miss

                if method == "UNKNOWN":
                    if path in all_backend_paths:
                        continue
                    sug = get_close_matches(path, list(all_backend_paths), n=3, cutoff=0.6)
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

                sug = get_close_matches(path, list(all_backend_paths), n=3, cutoff=0.6)
                misses.append(f"CONTRACT MISS: {method} {path}   suggestions: {sug or '(none)'}")

        if not misses:
            return ("passed", [])
        # Classify each miss — real ones block ZIP, advisory ones log a warning only.
        real = [m for m in misses if _classify_contract_miss(m) == "real"]
        advisory = [m for m in misses if _classify_contract_miss(m) != "real"]
        if real:
            return ("failed", misses)         # any real miss blocks ZIP
        return ("advisory_failed", advisory)  # only minor gaps — advisory

    def _check_raw_fetch_usage(self, files: dict) -> tuple[str, str]:
        """Advisory check: flag frontend files that call fetch() directly
        instead of going through the api client at @/lib/api.js."""
        import re
        offenders = []
        for path, content in files.items():
            if not path.startswith("frontend/src/"):
                continue
            if path.endswith("/lib/api.js"):
                continue
            if not re.search(r"\.(jsx?|tsx?)$", path):
                continue
            if re.search(r"\bfetch\s*\(", content):
                offenders.append(path)
        if not offenders:
            return "passed", "no raw fetch calls"
        return (
            "advisory_failed",
            f"raw fetch in {len(offenders)} file(s): "
            + ", ".join(offenders[:5]),
        )
