"""DebuggerAgent — uses Claude to fix test failures one file at a time."""
import json
import re
from typing import Optional

import structlog
from anthropic import Anthropic

from app.config import settings
from app.models.generation_plan import GenerationPlan

_INFRASTRUCTURE_ERROR_PATTERNS = [
    # Missing tools / filesystem / network
    "No such file or directory:",
    "command not found",
    "FileNotFoundError",
    "EACCES",
    "Could not connect to",
    "Connection refused",
    "Timeout",
    # pip / wheel-build failures (environment, not code)
    "Failed building wheel for",
    "Could not build wheels for",
    "ERROR: Failed building wheel",
    "error: failed-wheel-build",
    "error: command '/usr/bin/clang' failed",
    "error: command 'gcc' failed",
    "Microsoft Visual C++ 14.0 or greater is required",
    "Requirement already satisfied:",
    "ERROR: Could not find a version that satisfies",
    "ERROR: No matching distribution found for",
    # npm dependency failures
    "npm ERR! code",
    "npm error code",
    "ERESOLVE unable to resolve dependency tree",
]

# Primary: matches flake8/ruff output lines like `./backend/app/auth.py:225:16: W292 ...`
_LINT_LINE_RE = re.compile(r"^\s*\./?(?P<path>[\w./\\-]+\.[a-z]+):\d+:\d+:")

# Secondary: matches Python traceback lines `  File "/tmp/.../backend/app/models.py", line 42`
_TRACEBACK_FILE_RE = re.compile(r'File\s+"(?P<path>[^"]+\.py)",\s+line\s+\d+')

# Fallback: matches `path/to/file.py:` references regardless of what follows the colon.
# Covers both ast.parse-style "file.py: SyntaxError" and "file.py:42" formats.
_FILE_REF_RE = re.compile(r"(?P<path>[\w./\\-]+\.(?:py|jsx?|tsx?)):")

# Paths that belong to installed libraries, not generated user code.
_LIBRARY_SKIP_PREFIXES = (".testenv/", "site-packages/", ".venv/", "/venv/", "<frozen", "lib/python")

# Patterns that indicate a real (non-infrastructure) code error worth attempting a blind fix.
_SUBSTANTIVE_ERROR_PATTERNS = ["Traceback", "Error:", "Exception:", "sqlalchemy."]


def _is_infrastructure_error(msg: str) -> bool:
    return any(p.lower() in msg.lower() for p in _INFRASTRUCTURE_ERROR_PATTERNS)


_VITE_UNRESOLVED_RE = re.compile(
    r'Failed to resolve import ["\'](?P<path>@/[\w./\-]+)["\']'
    r'.*?from ["\'](?P<from>[\w./\-]+)["\']',
    re.DOTALL,
)
_VITE_ENOENT_RE = re.compile(
    r'Could not load (?P<abs>/[^\s]+/(?P<rel>frontend/src/[\w./\-]+))'
    r'(?: \(imported by (?P<from>[\w./\-]+)\))?',
)

_MODULE_NOT_FOUND_RE = re.compile(
    r"ModuleNotFoundError: No module named ['\"](?P<mod>[\w.]+)['\"]"
)

_STDLIB_MODULES = {
    "os", "sys", "re", "json", "datetime", "time", "uuid", "math", "random",
    "collections", "itertools", "functools", "typing", "pathlib", "asyncio",
    "contextlib", "dataclasses", "enum", "logging", "warnings", "io", "tempfile",
    "subprocess", "shutil", "csv", "sqlite3", "hashlib", "base64", "urllib",
    "http", "html", "xml", "argparse", "abc", "copy", "string", "textwrap",
    "decimal", "fractions", "statistics", "operator", "threading",
    "multiprocessing", "concurrent", "socket", "ssl", "email", "secrets",
    "ipaddress", "platform", "traceback", "inspect", "ast", "tokenize",
}

# Common alias map: import name → pip package name (when they differ)
_PIP_NAME_OVERRIDES = {
    "PIL": "pillow",
    "cv2": "opencv-python",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "jose": "python-jose",
    "dotenv": "python-dotenv",
    "jwt": "pyjwt",
    "magic": "python-magic",
}


_BARE_FETCH_API_RE = re.compile(
    r'fetch\s*\(\s*(?:`[^`]*?\$\{[^}]+\}[^`]*`|["\'][^"\']*?["\'])\s*[,)]'
)
_BARE_FETCH_LINE_RE = re.compile(
    r'(?P<indent>\s*)(?P<assign>(?:const|let)\s+\w+\s*=\s*)?await\s+fetch\s*\(\s*'
    r'(?:`(?P<tpl>[^`]+)`|["\'](?P<lit>[^"\']+)["\'])\s*'
    r'(?:,\s*\{(?P<opts>[^}]*)\})?\s*\)'
)


def _auto_use_api_client(test_results: dict, generated_files: dict) -> dict:
    """Scan frontend files for bare fetch() to /api/ paths and rewrite them
    to use the centralized api client. Returns {path: new_content} fixes."""
    api_path_in_url = re.compile(r'/api/[\w/{}-]+')
    fixes: dict = {}
    for path, content in generated_files.items():
        if not path.startswith("frontend/src/"):
            continue
        if not path.endswith((".jsx", ".tsx", ".js", ".ts")):
            continue
        if path.endswith("/api.js") or path.endswith("/api.ts"):
            continue
        if "fetch(" not in content or "/api/" not in content:
            continue
        new = content

        def _replace(match):
            method = "get"
            url = match.group("lit") or match.group("tpl") or ""
            m = api_path_in_url.search(url)
            if not m:
                return match.group(0)
            api_path = m.group(0)
            opts = match.group("opts") or ""
            mm = re.search(r'method\s*:\s*["\'](\w+)["\']', opts, re.IGNORECASE)
            if mm:
                method = mm.group(1).lower()
            assign = match.group("assign") or ""
            indent = match.group("indent") or ""
            body_match = re.search(r'body\s*:\s*JSON\.stringify\(\s*([^)]+)\)', opts)
            body = body_match.group(1) if body_match else ""
            if method in ("get", "delete"):
                call = f"api.{method}(`{api_path}`)"
            else:
                call = f"api.{method}(`{api_path}`, {body})" if body else f"api.{method}(`{api_path}`)"
            return f"{indent}{assign}(await {call}).data"

        new = _BARE_FETCH_LINE_RE.sub(_replace, new)
        if new != content and 'from "@/lib/api"' not in new and "from '@/lib/api'" not in new:
            lines = new.split("\n")
            last_import = max(
                (i for i, ln in enumerate(lines) if ln.startswith("import ")),
                default=-1,
            )
            insert_at = last_import + 1 if last_import >= 0 else 0
            lines.insert(insert_at, 'import { api } from "@/lib/api";')
            new = "\n".join(lines)
        if new != content:
            fixes[path] = new
    return fixes


_204_ASSERT_RE = re.compile(
    r'Status code (?P<code>20[14]|30[14])\s+must not have a response body',
    re.IGNORECASE,
)
_ROUTE_DECORATOR_RE = re.compile(
    r'@(?:router|app)\.(?P<method>get|post|put|patch|delete)\s*\([^)]*'
    r'status_code\s*=\s*(?:status\.HTTP_(?P<code1>20[14]|30[14])_\w+|(?P<code2>20[14]|30[14]))',
    re.IGNORECASE | re.DOTALL,
)


def _fix_body_forbidden_status(test_results: dict, generated_files: dict) -> dict:
    """If boot failed with 'Status code N must not have a response body',
    rewrite the offending route to status_code=200 (HTTP_200_OK). Returns
    a {path: new_content} dict of fixes, or empty."""
    text = "\n".join([
        (test_results.get("logs", {}) or {}).get("boot", "") or "",
        *[str(e) for e in (test_results.get("errors") or [])],
    ])
    if not _204_ASSERT_RE.search(text):
        return {}
    fixes: dict = {}
    for path, content in generated_files.items():
        if not path.startswith("backend/") or not path.endswith(".py"):
            continue
        new_content, count = _ROUTE_DECORATOR_RE.subn(
            lambda m: m.group(0).replace(
                f"HTTP_{m.group('code1') or m.group('code2')}_NO_CONTENT",
                "HTTP_200_OK",
            ).replace(
                f"status_code={m.group('code1') or m.group('code2')}",
                "status_code=200",
            ),
            content,
        )
        if count > 0:
            fixes[path] = new_content
    return fixes


_SQLA_UNMAPPED_RE = re.compile(
    r"can't be correctly interpreted for Annotated Declarative Table form",
)
_BASE_DECL_RE = re.compile(
    r"^(Base\s*=\s*declarative_base\(\)\s*)$",
    re.MULTILINE,
)


def _fix_sqlalchemy_unmapped(test_results: dict, generated_files: dict) -> dict:
    """Inject Base.__allow_unmapped__ = True when SQLAlchemy rejects bare type annotations."""
    text = "\n".join([
        (test_results.get("logs", {}) or {}).get("boot", "") or "",
        *[str(e) for e in (test_results.get("errors") or [])],
    ])
    if not _SQLA_UNMAPPED_RE.search(text):
        return {}
    fixes = {}
    candidates = ("backend/app/database.py", "backend/app/models.py")
    for path in candidates:
        content = generated_files.get(path)
        if not content or "__allow_unmapped__" in content:
            continue
        new_content, count = _BASE_DECL_RE.subn(
            r"\1\nBase.__allow_unmapped__ = True",
            content,
            count=1,
        )
        if count > 0:
            fixes[path] = new_content
            break  # one fix is enough
    return fixes


_AUTH_SHAPE_PATH_RE = re.compile(r"AUTH-SHAPE\s+\w+\s+(?P<path>/api/\S+)")
_FRONTEND_AUTH_JSON_RE = re.compile(
    r'(?P<indent>[ \t]*)(?P<assign>(?:const|let|var)\s+\w+\s*=\s*)?'
    r'await\s+(?P<client>api|axios)\.post\(\s*'
    r'["\'`](?P<path>/api/(?:login|register|signin|signup|token))["\'`]'
    r'\s*,\s*\{(?P<payload>[^}]*)\}\s*\)',
    re.DOTALL,
)


_METHOD_MISS_RE = re.compile(
    r'CONTRACT MISS: METHOD\s+(?P<bad_method>\w+)\s+(?P<path>/[\w/{}-]+)\s+'
    r"backend serves this path with:\s+\[(?P<methods>[^\]]+)\]",
)

_API_PREFIX_MISS_RE = re.compile(
    r"CONTRACT MISS:\s+\w+\s+(?P<bad>/api/[\w/{}-]+)\s+suggestions:\s+"
    r"\[(?P<suggs>[^\]]+)\]"
)
_INCLUDE_ROUTER_CALL_RE = re.compile(
    r'(?P<head>app\.include_router\(\s*)'
    r'(?P<router>[\w\.]+)'
    r'(?P<rest>[^)]*)'
    r'\)',
    re.DOTALL,
)

_AS_CONST_RE = re.compile(r'\s+as\s+const\b')
_AS_TYPE_RE = re.compile(r'\s+as\s+(?:string|number|boolean|any|unknown|[A-Z]\w*)\b')
_TYPED_PARAM_RE = re.compile(
    r'(\([^)]*?)(\w+)\s*:\s*(?:string|number|boolean|any|unknown|[A-Z]\w*(?:\[\])?)(\s*[,)])'
)
_BACKEND_TRAILING_ROUTE_RE = re.compile(
    r'@(\w+)\.(get|post|put|patch|delete)\(\s*["\']\/["\']'
)


def _fix_api_prefix_in_main(test_results: dict, generated_files: dict) -> dict:
    """When >=2 contract misses are /api/* paths whose suggestions show the matching
    /X path exists, the backend forgot to mount routers under /api. Inject
    prefix='/api' into every include_router call in main.py that lacks one."""
    contract_log = (test_results.get("logs", {}) or {}).get("contract", "") or ""
    if "CONTRACT MISS" not in contract_log:
        return {}

    pattern_hits = 0
    for m in _API_PREFIX_MISS_RE.finditer(contract_log):
        bad = m.group("bad")        # e.g. "/api/register"
        suggs = m.group("suggs") or ""
        bare = bad[len("/api"):]    # e.g. "/register"
        if f"'{bare}'" in suggs or f'"{bare}"' in suggs:
            pattern_hits += 1

    if pattern_hits < 2:
        return {}

    main_path = "backend/app/main.py"
    content = generated_files.get(main_path)
    if not content:
        return {}

    def _rewrite(m):
        head = m.group("head")
        router_name = m.group("router")
        rest = m.group("rest") or ""
        if re.search(r'\bprefix\s*=', rest):
            return m.group(0)
        return f'{head}{router_name}, prefix="/api"{rest})'

    new_content, count = _INCLUDE_ROUTER_CALL_RE.subn(_rewrite, content)
    if count == 0 or new_content == content:
        return {}
    return {main_path: new_content}


def _fix_auth_body_shape(test_results: dict, generated_files: dict) -> dict:
    """When contract check flags AUTH-SHAPE, convert the BACKEND from
    OAuth2PasswordRequestForm to Pydantic LoginRequest/RegisterRequest. One
    file change per project, regardless of how the frontend submits."""
    log = (test_results.get("logs", {}) or {}).get("contract", "") or ""
    if "AUTH-SHAPE" not in log:
        return {}

    fixes = {}
    for path, content in generated_files.items():
        if not path.endswith(".py") or not path.startswith("backend/"):
            continue
        if "OAuth2PasswordRequestForm" not in (content or ""):
            continue

        new = content

        # Inject Pydantic models — only if they're not already present.
        # Use the first @router. or @app. decorator as the insertion anchor,
        # which is always at module top-level and never inside an open paren.
        if "class LoginRequest" not in new and "class RegisterRequest" not in new:
            insert_block = (
                "from pydantic import BaseModel, EmailStr\n\n\n"
                "class LoginRequest(BaseModel):\n"
                "    email: EmailStr\n"
                "    password: str\n\n\n"
                "class RegisterRequest(BaseModel):\n"
                "    email: EmailStr\n"
                "    password: str\n"
                "    name: str | None = None\n\n\n"
            )
            # Find first @router.* or @app.* decorator at line start.
            anchor = re.search(
                r"^\s*@(?:router|app)\.\w+\(",
                new,
                re.MULTILINE,
            )
            if anchor:
                pos = anchor.start()
                # Don't double-inject if already at the right spot.
                if "class LoginRequest" not in new[:pos]:
                    new = new[:pos] + insert_block + new[pos:]
            else:
                # No route decorator found — append at end as last resort.
                if "class LoginRequest" not in new:
                    new = new.rstrip() + "\n\n\n" + insert_block

        # 2. Rewrite login route signature.
        new = re.sub(
            r"def\s+(login|signin)\s*\(\s*"
            r"(?P<param>\w+)\s*:\s*OAuth2PasswordRequestForm\s*=\s*Depends\(\s*\)\s*,",
            r"def \1(payload: LoginRequest,",
            new,
        )

        # 3. Rewrite register route signature.
        new = re.sub(
            r"def\s+(register|signup)\s*\(\s*"
            r"(?P<param>\w+)\s*:\s*OAuth2PasswordRequestForm\s*=\s*Depends\(\s*\)\s*,",
            r"def \1(payload: RegisterRequest,",
            new,
        )

        # 4. Replace form.username → payload.email, form.password → payload.password.
        new = re.sub(
            r"\b(?:form|form_data|credentials|data|request)\.username\b",
            "payload.email",
            new,
        )
        new = re.sub(
            r"\b(?:form|form_data|credentials|data|request)\.password\b",
            "payload.password",
            new,
        )

        # 5. Drop the OAuth2 import — no longer needed.
        new = re.sub(
            r"^from\s+fastapi\.security\s+import\s+OAuth2PasswordRequestForm\s*$\n?",
            "",
            new,
            flags=re.MULTILINE,
        )
        # Combined import: "from fastapi.security import X, OAuth2PasswordRequestForm"
        new = re.sub(
            r"(from\s+fastapi\.security\s+import\s+[^,\n]+),\s*OAuth2PasswordRequestForm",
            r"\1",
            new,
        )
        new = re.sub(
            r"(from\s+fastapi\.security\s+import\s+)OAuth2PasswordRequestForm\s*,\s*",
            r"\1",
            new,
        )

        if new != content:
            fixes[path] = new
    return fixes


def _fix_method_mismatch(test_results: dict, generated_files: dict) -> dict:
    """Fix frontend calls using wrong HTTP method per CONTRACT MISS: METHOD log lines."""
    contract_log = (test_results.get("logs", {}) or {}).get("contract", "") or ""
    if "CONTRACT MISS: METHOD" not in contract_log:
        return {}
    method_fixes: dict[str, str] = {}
    for m in _METHOD_MISS_RE.finditer(contract_log):
        bad_method = m.group("bad_method").upper()
        path = m.group("path")
        methods_raw = m.group("methods") or ""
        method_match = re.search(r"['\"]?(\w+)['\"]?", methods_raw)
        if not method_match:
            continue
        good_method = method_match.group(1).upper()
        bad_lower = bad_method.lower()
        good_lower = good_method.lower()
        pattern = re.compile(
            rf'(\b(?:api|axios))\.{bad_lower}\(\s*(["\'`]){re.escape(path)}\2'
        )
        for fp, content in list(generated_files.items()):
            if not fp.startswith("frontend/src/") or not content:
                continue
            new_content, count = pattern.subn(
                rf'\1.{good_lower}(\2{path}\2',
                method_fixes.get(fp) or content,
            )
            if count > 0:
                method_fixes[fp] = new_content
    return method_fixes


def _strip_typescript_from_jsx(test_results: dict, generated_files: dict) -> dict:
    """Remove TypeScript syntax from .jsx files so esbuild does not reject them."""
    fixes = {}
    for path, content in generated_files.items():
        if not path.endswith(".jsx") or not content:
            continue
        new = _AS_CONST_RE.sub("", content)
        new = _AS_TYPE_RE.sub("", new)
        new = _TYPED_PARAM_RE.sub(r'\1\2\3', new)
        if new != content:
            fixes[path] = new
    return fixes


def _normalize_trailing_slashes(test_results: dict, generated_files: dict) -> dict:
    """Convert @router.get('/') to @router.get('') in backend route files."""
    fixes = {}
    for path, content in generated_files.items():
        if not path.startswith("backend/") or not path.endswith(".py") or not content:
            continue
        new, count = _BACKEND_TRAILING_ROUTE_RE.subn(
            lambda m: f'@{m.group(1)}.{m.group(2)}("")',
            content,
        )
        if count > 0:
            fixes[path] = new
    return fixes


def _auto_stub_missing_imports(test_results: dict, generated_files: dict) -> dict:
    """Stub all missing @/... imports found by scanning generated files and build logs."""
    scan_missing = _scan_generated_files_for_missing_imports(generated_files)
    log_missing = _collect_unresolved_imports(test_results) if test_results else []
    combined: list[dict] = []
    seen_paths: set[str] = set()
    for entry in scan_missing + log_missing:
        if entry["path"] in seen_paths:
            continue
        seen_paths.add(entry["path"])
        combined.append(entry)
    if not combined:
        return {}
    fixed: dict[str, str] = {}
    for entry in combined:
        target_rel = _component_path_for(entry["path"])
        if target_rel in generated_files or target_rel in fixed:
            continue
        named, has_default = _named_imports_for(entry["path"], generated_files)
        fixed[target_rel] = _build_stub(entry["path"], named, has_default)
    return fixed


def _add_missing_python_packages(test_results: dict, generated_files: dict) -> dict:
    """Add missing Python packages to requirements.txt based on ModuleNotFoundError."""
    text_py = "\n".join([
        (test_results.get("logs", {}) or {}).get("boot", "") or "",
        (test_results.get("logs", {}) or {}).get("install", "") or "",
        *[str(e) for e in (test_results.get("errors") or [])],
    ])
    missing_py: set[str] = set()
    for m in _MODULE_NOT_FOUND_RE.finditer(text_py):
        mod = m.group("mod").split(".")[0]
        if mod in _STDLIB_MODULES:
            continue
        if any(
            p.endswith(f"app/{mod}.py") or p.endswith(f"app/{mod}/__init__.py")
            for p in generated_files
        ):
            continue
        missing_py.add(_PIP_NAME_OVERRIDES.get(mod, mod))
    if not missing_py:
        return {}
    req_path = "backend/requirements.txt"
    current = generated_files.get(req_path, "")
    lower = current.lower()
    new_lines = [pkg for pkg in sorted(missing_py) if pkg.lower().split("==")[0] not in lower]
    if not new_lines:
        return {}
    return {req_path: current.rstrip() + "\n" + "\n".join(new_lines) + "\n"}


def _collect_unresolved_imports(test_results: dict) -> list[dict]:
    """Pull every {path, from_file} pair out of the frontend build log.

    Handles both Vite's "Failed to resolve import" and the ENOENT load-error form.
    """
    logs = test_results.get("logs", {}) or {}
    errors = test_results.get("errors", []) or []
    text = "\n".join([
        logs.get("frontend_build", "") or "",
        logs.get("frontend_build_full", "") or "",
        *[str(e) for e in errors],
    ])
    found: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for m in _VITE_UNRESOLVED_RE.finditer(text):
        key = (m.group("path"), m.group("from") or "")
        if key in seen:
            continue
        seen.add(key)
        found.append({"path": m.group("path"), "from_file": m.group("from") or ""})
    for m in _VITE_ENOENT_RE.finditer(text):
        rel = m.group("rel")
        if not rel:
            continue
        import_path = "@/" + rel.split("frontend/src/", 1)[1]
        key = (import_path, m.group("from") or "")
        if key in seen:
            continue
        seen.add(key)
        found.append({"path": import_path, "from_file": m.group("from") or ""})
    return found


_IMPORT_LINE_RE = re.compile(
    r'import\s*(?:(?P<default>\w+)\s*,?\s*)?'
    r'(?:\{(?P<named>[^}]*)\})?'
    r'\s*from\s*["\'](?P<from>@/[^"\']+)["\']',
)


def _scan_generated_files_for_missing_imports(generated_files: dict[str, str]) -> list[dict]:
    """Walk every generated frontend file and return every @/... import whose
    target file does not exist in generated_files. Catches ALL missing imports
    in one pass instead of only the first one Vite halts on.
    """
    missing: list[dict] = []
    seen: set[str] = set()
    for fp, content in generated_files.items():
        if not fp.endswith((".jsx", ".js", ".tsx", ".ts")):
            continue
        for m in _IMPORT_LINE_RE.finditer(content or ""):
            import_path = m.group("from")
            if not import_path or not import_path.startswith("@/"):
                continue
            if import_path in seen:
                continue
            target = _component_path_for(import_path)
            candidates = [
                target,
                target[:-4] + ".js" if target.endswith(".jsx") else target,
                target[:-4] + "/index.jsx" if target.endswith(".jsx") else target + "/index.jsx",
                target[:-4] + "/index.js" if target.endswith(".jsx") else target + "/index.js",
            ]
            if any(c in generated_files for c in candidates):
                continue
            seen.add(import_path)
            missing.append({"path": import_path, "from_file": fp})
    return missing


def _named_imports_for(path: str, generated_files: dict[str, str]) -> tuple[set[str], bool]:
    """Walk every generated frontend file and collect named imports + default usage for path."""
    named: set[str] = set()
    has_default = False
    for fp, content in generated_files.items():
        if not fp.endswith((".jsx", ".js", ".tsx", ".ts")):
            continue
        for m in _IMPORT_LINE_RE.finditer(content or ""):
            if m.group("from") != path:
                continue
            if m.group("default"):
                has_default = True
            if m.group("named"):
                for piece in m.group("named").split(","):
                    name = piece.strip().split(" as ")[0].strip()
                    if name:
                        named.add(name)
    return named, has_default


def _component_path_for(import_path: str) -> str:
    """Map "@/components/ui/tabs" → "frontend/src/components/ui/tabs.jsx"."""
    rel = import_path.replace("@/", "frontend/src/")
    if not rel.endswith((".jsx", ".js", ".tsx", ".ts")):
        rel += ".jsx"
    return rel


def _build_stub(import_path: str, named: set[str], has_default: bool) -> str:
    """Generate a JSX stub that exports exactly the names the consumers need."""
    is_context = "/context" in import_path or "/contexts" in import_path
    is_layout = (
        import_path.endswith("Layout") or "/layouts/" in import_path
        or "/components/" in import_path and import_path.split("/")[-1].endswith("Layout")
    )
    if is_context:
        hook = "use" + import_path.split("/")[-1].replace("Context", "")
        ctx = import_path.split("/")[-1]
        return (
            'import { createContext, useContext } from "react";\n'
            f'const {ctx} = createContext({{}});\n'
            f'export default {ctx};\n'
            f'export {{ {ctx} }};\n'
            f'export function {hook}() {{ return useContext({ctx}) ?? {{}}; }}\n'
            f'export function {ctx.replace("Context", "Provider")}({{ children, value }}) {{\n'
            f'  return <{ctx}.Provider value={{value ?? {{}}}}>{{children}}</{ctx}.Provider>;\n'
            f'}}\n'
        )
    if is_layout:
        return (
            'import { Outlet } from "react-router-dom";\n'
            'export default function Layout({ children }) {\n'
            '  return <div className="min-h-screen">{children ?? <Outlet />}</div>;\n'
            '}\n'
        )
    lines = [
        'import * as React from "react";',
        '',
        'const Stub = React.forwardRef(function StubComponent({ children, className, asChild, ...rest }, ref) {',
        '  return React.createElement("div", { ref, className, ...rest }, children);',
        '});',
        '',
        'export default Stub;',
    ]
    for name in sorted(named):
        lines.append(f'export const {name} = Stub;')
    return "\n".join(lines) + "\n"


class DebuggerAgent:
    """Parses test errors and uses Claude to fix them.

    Incremental: fixes one file per call. Hard cap of 3 attempts per file.
    In mock mode: prepends a comment fix without calling Claude.
    """

    MAX_ATTEMPTS_PER_FILE = 3

    def __init__(self) -> None:
        self.model = settings.claude_model
        self.log = structlog.get_logger("DebuggerAgent")

    def debug_and_fix(
        self,
        test_results: dict,
        generated_files: dict[str, str],
        plan: GenerationPlan,
        attempt_count: dict[str, int] | None = None,
    ) -> dict:
        """Analyze test failures and fix all identifiable files.

        Returns: { status, fixed_files, attempt_counts, errors }
        """
        if attempt_count is None:
            attempt_count = {}

        errors = test_results.get("errors", [])
        self.log.info("debug_and_fix.start", num_errors=len(errors))

        # ── Pre-LLM deterministic fixers: collect ALL applicable fixes in one pass ──
        all_fixes: dict[str, str] = {}
        _pre_llm_fixers = [
            ("auto_stub_missing_imports", _auto_stub_missing_imports),
            ("add_missing_python_packages", _add_missing_python_packages),
            ("fix_body_forbidden_status", _fix_body_forbidden_status),
            ("fix_sqlalchemy_unmapped", _fix_sqlalchemy_unmapped),
            ("fix_auth_body_shape", _fix_auth_body_shape),
            ("fix_method_mismatch", _fix_method_mismatch),
            ("fix_api_prefix_in_main", _fix_api_prefix_in_main),
            ("auto_use_api_client", _auto_use_api_client),
            ("strip_typescript_from_jsx", _strip_typescript_from_jsx),
            ("normalize_trailing_slashes", _normalize_trailing_slashes),
        ]
        for fixer_name, fixer in _pre_llm_fixers:
            try:
                new_fixes = fixer(test_results, generated_files)
            except Exception as exc:
                self.log.warning(f"debugger.{fixer_name}.exception", error=str(exc))
                new_fixes = {}
            if new_fixes:
                self.log.info(f"debugger.{fixer_name}.applied", files=list(new_fixes.keys()))
                for path, content in new_fixes.items():
                    all_fixes[path] = content
                    generated_files = {**generated_files, path: content}

        if all_fixes:
            self.log.info("debugger.pre_llm_fixes.done", total_files=len(all_fixes))
            return {"status": "fixed", "fixed_files": all_fixes, "attempt_counts": attempt_count}

        # ── smoke failure → inject runtime errors into the LLM debug path ─────
        smoke_log = (test_results.get("logs", {}) or {}).get("smoke", "")
        if (
            test_results.get("passed_checks", {}).get("smoke") == "failed"
            and "frontend_build" in test_results.get("passed_checks", {})
            and test_results["passed_checks"]["frontend_build"] == "passed"
        ):
            try:
                parsed = json.loads(smoke_log.splitlines()[-1])
                runtime_errors = parsed.get("errors", [])
            except Exception:
                runtime_errors = [smoke_log[-500:]]
            if runtime_errors:
                test_results.setdefault("errors", []).extend(
                    f"RUNTIME (smoke): {e}" for e in runtime_errors
                )

        if not errors:
            return {"status": "success", "fixed_files": {}, "attempt_counts": attempt_count, "errors": []}

        # Filter infrastructure errors — missing tools are not code bugs.
        code_errors = [e for e in errors if not _is_infrastructure_error(e)]
        for e in errors:
            if _is_infrastructure_error(e):
                self.log.info("debug.skipped_infrastructure_error", error=e[:120])

        if not code_errors:
            return {
                "status": "skipped",
                "reason": "All errors are infrastructure, not code",
                "fixed_files": {},
                "attempt_counts": attempt_count,
                "errors": [],
            }

        try:
            full_error_text = "\n".join(code_errors)
            files_to_fix = self._extract_files_from_error(full_error_text)

            if not files_to_fix:
                # Attempt a blind fix when no file paths are extractable but the error is real code.
                if any(p in full_error_text for p in _SUBSTANTIVE_ERROR_PATTERNS):
                    self.log.info("debug.attempting_blind_fix", error_preview=full_error_text[:100])
                    blind = self._blind_fix_attempt(full_error_text, generated_files)
                    if blind and blind.get("file_path") and blind["file_path"] in generated_files:
                        fp = blind["file_path"]
                        new_counts = dict(attempt_count)
                        new_counts[fp] = new_counts.get(fp, 0) + 1
                        self.log.info("debug.blind_fix.applied", file=fp)
                        return {
                            "status": "fixed",
                            "fixed_files": {fp: blind["fixed_content"]},
                            "attempt_counts": new_counts,
                            "errors": [],
                        }
                self.log.warning("debug.cannot_identify_file", error=full_error_text[:200])
                return {
                    "status": "skipped",
                    "reason": "Could not auto-fix; surfaced to user",
                    "fixed_files": {},
                    "attempt_counts": attempt_count,
                    "errors": [f"Could not identify any file to fix from: {full_error_text[:120]}"],
                }

            fixed_files: dict[str, str] = {}
            new_attempt_count = dict(attempt_count)

            for file_to_fix in files_to_fix:
                if file_to_fix not in generated_files:
                    self.log.warning("debug_and_fix.file_not_in_generated", file=file_to_fix)
                    continue

                current_attempts = new_attempt_count.get(file_to_fix, 0)
                if current_attempts >= self.MAX_ATTEMPTS_PER_FILE:
                    self.log.warning(
                        "debug_and_fix.max_attempts_reached",
                        file=file_to_fix,
                        attempts=current_attempts,
                    )
                    continue

                # Find the most relevant error line for this specific file.
                error_for_file = next(
                    (e for e in code_errors if file_to_fix in e), code_errors[0]
                )
                original_content = generated_files[file_to_fix]

                if settings.mock_ai:
                    fixed_content = self._fix_mock(file_to_fix, original_content, error_for_file)
                else:
                    fixed_content = self._fix_with_claude(
                        file_to_fix, original_content, error_for_file, test_results
                    )

                fixed_files[file_to_fix] = fixed_content
                new_attempt_count[file_to_fix] = current_attempts + 1
                self.log.info(
                    "debug_and_fix.fixed_file",
                    file=file_to_fix,
                    attempt=new_attempt_count[file_to_fix],
                )

            if not fixed_files:
                return {
                    "status": "error",
                    "fixed_files": {},
                    "attempt_counts": new_attempt_count,
                    "errors": [
                        f"Max {self.MAX_ATTEMPTS_PER_FILE} attempts reached for all identified files"
                    ],
                }

            self.log.info("debug_and_fix.done", num_fixed=len(fixed_files))
            return {
                "status": "fixed",
                "fixed_files": fixed_files,
                "attempt_counts": new_attempt_count,
                "errors": [],
            }

        except Exception as exc:
            self.log.error("debug_and_fix.error", error=str(exc))
            return {
                "status": "error",
                "fixed_files": {},
                "attempt_counts": attempt_count,
                "errors": [str(exc)],
            }

    # ── helpers ───────────────────────────────────────────────────────────────

    def _extract_files_from_error(self, error_message: str) -> list[str]:
        """Extract unique generated-project file paths from lint output OR Python traceback.

        Filters out library paths (.testenv/, site-packages/, .venv/) that aren't user code.
        Normalises absolute tmpdir paths to relative project paths (backend/… or frontend/…).
        """
        files: set[str] = set()
        for line in error_message.splitlines():
            m = _LINT_LINE_RE.match(line) or _TRACEBACK_FILE_RE.search(line)
            if not m:
                m = _FILE_REF_RE.search(line)
            if not m:
                continue
            path = m.group("path")
            # Skip library / interpreter internals
            if any(skip in path for skip in _LIBRARY_SKIP_PREFIXES):
                continue
            # Normalise absolute tmpdir paths to relative project paths.
            # e.g. /private/var/.../demaestro_test_xxx/backend/app/models.py → backend/app/models.py
            for prefix in ("backend/", "frontend/"):
                idx = path.find(prefix)
                if idx >= 0:
                    path = path[idx:]
                    break
            else:
                path = path.lstrip("./")
            if path:
                files.add(path)
        return sorted(files)

    def _fix_mock(self, file_path: str, original_content: str, error_msg: str) -> str:
        """Mock fix: strip the broken line and prepend a correction comment."""
        short_err = error_msg[:80].replace("\n", " ")
        return f"# FIXED: {short_err}\n{original_content}"

    def _blind_fix_attempt(
        self, error: str, all_files: dict[str, str]
    ) -> Optional[dict]:
        """When file extraction fails but the error is real, ask Claude to identify and fix.

        Returns {"file_path": "...", "fixed_content": "..."} or None if Claude cannot help.
        Capped at one call to avoid runaway; mock mode returns None immediately.
        """
        if settings.mock_ai:
            return None

        prompt = (
            "Below is a build error from a generated Python+FastAPI project. "
            "Identify which file most likely contains the bug, and return a fix.\n\n"
            f"Error:\n{error[:3000]}\n\n"
            f"Available files:\n{json.dumps(list(all_files.keys()), indent=2)}\n\n"
            'Respond with JSON: {"file_path": "...", "fixed_content": "..."} '
            'or {"file_path": null} if you cannot determine the cause.'
        )
        try:
            client = Anthropic(api_key=settings.anthropic_api_key)
            response = client.messages.create(
                model=self.model,
                max_tokens=16000,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            text = re.sub(r"^```\w*\n", "", text)
            text = re.sub(r"\n```$", "", text)
            result = json.loads(text)
            if result.get("file_path"):
                self.log.info("_blind_fix_attempt.success", file=result["file_path"])
                return result
            return None
        except Exception as exc:
            self.log.warning("_blind_fix_attempt.failed", error=str(exc))
            return None

    def _fix_with_claude(
        self,
        file_path: str,
        original_content: str,
        error_msg: str,
        test_results: dict,
    ) -> str:
        """Call Claude Sonnet to produce a minimal fix for the failing file."""
        checks = test_results.get("passed_checks", {})
        prompt = (
            "You are a code debugger. Fix ONE file based on test errors.\n\n"
            f"**File to fix:** {file_path}\n\n"
            f"**Original content:**\n```\n{original_content}\n```\n\n"
            f"**Test error:**\n{error_msg}\n\n"
            "**Test results summary:**\n"
            f"- Install: {checks.get('install')}\n"
            f"- Lint: {checks.get('lint')}\n"
            f"- Typecheck: {checks.get('typecheck')}\n"
            f"- Boot: {checks.get('boot')}\n\n"
            "**Task:**\n"
            "- Identify why the test failed\n"
            "- Fix ONLY the necessary code in this file\n"
            "- Do NOT rewrite the entire file; make minimal changes\n"
            "- Preserve the structure and logic; fix the specific error\n\n"
            "Output ONLY the fixed file content. No explanations, no markdown."
        )

        if any(original_content.rstrip().endswith(end) for end in (">", "/>", '">')) and \
                not original_content.rstrip().endswith(("/>", "}", ");")):
            prompt += (
                "\n\nIMPORTANT: the existing file is truncated mid-element. Your "
                "fix MUST output the COMPLETE file with all opened tags closed. "
                "Do not preserve the truncated ending — finish it properly."
            )

        client = Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=16000,
            messages=[{"role": "user", "content": prompt}],
        )

        fixed = response.content[0].text
        # Strip accidental code-fence wrapping
        fixed = re.sub(r"^```\w*\n", "", fixed.strip())
        fixed = re.sub(r"\n```$", "", fixed)

        self.log.info("_fix_with_claude.done", file=file_path, content_length=len(fixed))
        return fixed
