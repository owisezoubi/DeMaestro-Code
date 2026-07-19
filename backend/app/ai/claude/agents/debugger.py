"""DebuggerAgent — uses Claude to fix test failures one file at a time."""
import ast
import difflib
import json
import os
import re
from typing import Optional

import structlog
from anthropic import Anthropic

from app.config import settings, get_agent_model, get_agent_max_tokens
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

# Paths that belong to installed libraries or tool caches — never user code.
_LIBRARY_SKIP_PREFIXES = (
    ".testenv/", "site-packages/", ".venv/", "/venv/", "<frozen", "lib/python",
    "node_modules/", "test_tools_cache/", "venv_cache/", "node_modules_cache/",
)

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


# ── Route-ordering fixer ─────────────────────────────────────────────────────

def _extract_router_decorator(dec) -> tuple | None:
    """Return (method, path_str) from an @router.<method>("<path>") AST node, or None."""
    if not isinstance(dec, ast.Call):
        return None
    func = dec.func
    if not (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "router"
    ):
        return None
    method = func.attr.lower()
    if method not in ("get", "post", "put", "patch", "delete"):
        return None
    if not dec.args:
        return None
    arg0 = dec.args[0]
    if not (isinstance(arg0, ast.Constant) and isinstance(arg0.value, str)):
        return None
    return method, arg0.value


def _route_path_shadows(param_path: str, literal_path: str) -> bool:
    """Return True when param_path declared first would shadow literal_path."""
    def _segs(p):
        return [s for s in p.strip("/").split("/") if s]
    def _is_param(s):
        return s.startswith("{") and s.endswith("}")
    ps, ls = _segs(param_path), _segs(literal_path)
    if len(ps) != len(ls):
        return False
    for p_seg, l_seg in zip(ps, ls):
        if _is_param(p_seg):
            continue
        if p_seg != l_seg:
            return False
    return any(_is_param(s) for s in ps)


def _reorder_routes_in_content(content: str) -> tuple[str, int]:
    """Move literal route blocks to be declared before their parametric
    same-method siblings. Uses a direct find-and-move algorithm so a
    literal can leapfrog any number of routes of other methods to land
    in front of a shadowing parametric route.

    Returns (new_content, swap_count).
    """
    import re as _re
    try:
        ast.parse(content)
    except SyntaxError:
        return content, 0

    lines = content.split("\n")
    total_swaps = 0
    max_iters = 20
    iter_count = 0

    while iter_count < max_iters:
        iter_count += 1
        try:
            tree = ast.parse("\n".join(lines))
        except SyntaxError:
            break

        route_blocks: list[tuple[str, str, int, int]] = []
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                info = _extract_router_decorator(dec)
                if info:
                    method, path_str = info
                    route_blocks.append((
                        method, path_str,
                        dec.lineno - 1,
                        node.end_lineno - 1,
                    ))
                    break

        # Find the FIRST shadowing pair: parametric route at i, same-method
        # literal route at j > i that would be shadowed.
        swap_pair: tuple[int, int] | None = None
        for i in range(len(route_blocks)):
            mi, pi, _, _ = route_blocks[i]
            if not _path_has_param(pi):
                continue
            for j in range(i + 1, len(route_blocks)):
                mj, pj, _, _ = route_blocks[j]
                if mj != mi:
                    continue
                if _path_has_param(pj):
                    continue
                if _route_path_shadows(pi, pj):
                    swap_pair = (i, j)
                    break
            if swap_pair:
                break

        if swap_pair is None:
            break

        i, j = swap_pair
        _, _, si, ei = route_blocks[i]
        _, _, sj, ej = route_blocks[j]

        # Extract block_j.
        block_j = lines[sj: ej + 1]

        # Find the blank-line separator immediately before block_j.
        k = sj - 1
        while k >= 0 and lines[k].strip() == "":
            k -= 1
        # k now points to the last non-blank line before block_j.

        # Remove block_j and its preceding blank separator.
        del lines[sj: ej + 1]
        del lines[k + 1: sj]

        # Insert block_j (with a blank-line separator) before block_i.
        # si is still valid — we only removed lines that were after si.
        insertion = block_j + ["", ""]
        for offset, line in enumerate(insertion):
            lines.insert(si + offset, line)

        total_swaps += 1

    result = "\n".join(lines)
    result = _re.sub(r"\n{4,}", "\n\n\n", result)
    return result, total_swaps


def _path_has_param(path: str) -> bool:
    """Return True if the path contains a {param} segment."""
    return "{" in path and "}" in path


def _fix_route_ordering(test_results: dict, generated_files: dict) -> dict:
    """Reorder route handlers so literal paths are declared before parametrized
    siblings.  Prevents FastAPI from catching /menu-items/categories with the
    /menu-items/{item_id} handler and returning a 422.

    Triggered when the tester flags a ROUTE-SHADOW violation, but also runs
    unconditionally so it catches problems the tester didn't see yet.
    Idempotent.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict = {}
    for path, content in generated_files.items():
        if not path.startswith("backend/app/routes/") or not path.endswith(".py"):
            continue
        if not content:
            continue
        new_content, swaps = _reorder_routes_in_content(content)
        if swaps > 0 and new_content != content:
            fixes[path] = new_content
            _log.info("fix_route_ordering.applied", file=path, swaps=swaps)
    return fixes


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

# Semantic preference: when multiple methods are served, prefer one that is
# functionally similar to the bad method (e.g. PUT -> PATCH for updates).
_METHOD_SIMILARITY: dict[str, list[str]] = {
    "PUT":    ["PATCH", "POST"],
    "PATCH":  ["PUT"],
    "POST":   ["PUT", "PATCH"],
    "GET":    [],
    "DELETE": [],
}

_ADMIN_PREFIX_MISS_RE = re.compile(
    r"CONTRACT MISS: ADMIN-PREFIX\s+(?P<method>\w+)\s+(?P<path>/api/[^\s(]+)\s+"
    r"\(called from (?P<source>[^)]+)\)"
    r"[^;]*?suggestions: change to (?P<corrected>/api/admin/\S+)",
    re.IGNORECASE,
)

# Quote-balanced URL pattern: (?P=q) backreference ensures the closing quote
# matches the opening one, preventing capture past the URL boundary (e.g. stray ;).
_ADMIN_URL_RE = re.compile(
    r"""(?P<q>["'])                       # opening quote (single or double)
        /api/(?P<resource>[^"'/\s;]+)     # first path segment (no quotes/slash/ws/;)
        (?P<rest>(?:/[^"'\s;]*)?)         # optional sub-path
        (?P=q)                            # MATCHING closing quote (backref)
    """,
    re.VERBOSE,
)


def _rewrite_admin_prefix(content: str, known_admin_resources: set) -> str:
    """Replace /api/<resource> URLs with /api/admin/<resource> for known admin resources."""
    def _rewrite(m):
        resource = m.group("resource")
        if resource in known_admin_resources:
            q = m.group("q")
            rest = m.group("rest")
            return f"{q}/api/admin/{resource}{rest}{q}"
        return m.group(0)

    return _ADMIN_URL_RE.sub(_rewrite, content)

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

# TypeScript-strip regexes.  Applied ONLY to non-import lines via
# _apply_ts_strip_to_content — never to `import`/`from`/re-export lines.
# Negative lookbehind (?<![*}]) prevents matching namespace-import `*` or `}`.
# Lookahead (?=\s*[,;)\]}\n]|$) ensures the cast ends at a statement boundary.
_AS_CONST_RE = re.compile(
    r"(?<![*}])\s+as\s+const\b(?=\s*[,;)\]}\n]|$)"
)
_AS_TYPE_RE = re.compile(
    r"(?<![*}])\s+as\s+(?:string|number|boolean|any|unknown|[A-Z]\w*(?:<[^>]*>)?)(?=\s*[,;)\]}\n]|$)"
)
_TYPED_PARAM_RE = re.compile(
    r'(\([^)]*?)(\w+)\s*:\s*(?:string|number|boolean|any|unknown|[A-Z]\w*(?:\[\])?)(\s*[,)])'
)
_BACKEND_TRAILING_ROUTE_RE = re.compile(
    r'@(\w+)\.(get|post|put|patch|delete)\(\s*["\']\/["\']'
)


# ── Social / OAuth auth strippers ────────────────────────────────────────────

# Import lines for known social-auth packages.
_SOCIAL_IMPORT_RE = re.compile(
    r"^import[^\n]+(?:@react-oauth|react-google-login|react-oauth-google|"
    r"GoogleLogin|FacebookLogin|GithubLogin|useGoogleLogin|useGithubLogin|"
    r"next-auth|firebase/auth|@auth/core|supabase/auth|"
    r"@microsoft/mgt|react-linkedin-login)[^\n]*\n",
    re.MULTILINE | re.IGNORECASE,
)

# Single-line social-auth <Button> / <button> elements.
_SOCIAL_BUTTON_RE = re.compile(
    r"[ \t]*<[Bb]utton\b[^\n>]*>[ \t]*(?:[^\n<]*)"
    r"(?:Google|GitHub|Github|Facebook|Apple|Microsoft|Twitter|"
    r"Sign in with|Log in with|Continue with|Login with)[ \t]*[^\n<]*"
    r"</[Bb]utton>[ \t]*\n?",
    re.IGNORECASE,
)

# "Or continue with" / "Or sign in with" divider lines.
_OR_WITH_LINE_RE = re.compile(
    r"[ \t]*(?:<[^>]+>)?[ \t]*[Oo]r\s+(?:continue|sign\s+in|log\s+in|login)\s+with\b"
    r"[^\n]*(?:</[^>]+>)?[ \t]*\n?",
    re.IGNORECASE,
)

# OAuth redirect links  (<a href="/auth/google/...">).
_OAUTH_HREF_RE = re.compile(
    r"[ \t]*<a\b[^>]*href=[\"'][^\"']*(?:/google|/github|/facebook|"
    r"/oauth|/social|/callback)[^\"']*[\"'][^>]*>.*?</a>[ \t]*\n?",
    re.IGNORECASE,
)

# Backend route decorators on OAuth paths.
_OAUTH_ROUTE_DECO_RE = re.compile(
    r"@\w+\.\w+\s*\(['\"][^'\"]*(?:/google|/github|/facebook|/apple|"
    r"/microsoft|/twitter|/oauth|/callback/|/social)[^'\"]*['\"]",
    re.IGNORECASE,
)

_LOGIN_REGISTER_NAMES = frozenset({
    "loginpage.jsx", "login.jsx", "registerpage.jsx", "register.jsx",
    "signinpage.jsx", "signin.jsx", "signuppage.jsx", "signup.jsx",
    "loginpage.tsx", "login.tsx", "registerpage.tsx", "register.tsx",
    "signinpage.tsx", "signin.tsx", "signuppage.tsx", "signup.tsx",
})

# Identifier -> (import_path, is_named, package_dep, dep_version)
# package_dep=None means the identifier comes from a local scaffold path.
_CANONICAL_IMPORTS: dict[str, tuple[str, bool, str | None, str | None]] = {
    # shadcn/ui primitives — local scaffold
    "Button":      ("@/components/ui/button", True, None, None),
    "Input":       ("@/components/ui/input", True, None, None),
    "Label":       ("@/components/ui/label", True, None, None),
    "Card":        ("@/components/ui/card", True, None, None),
    "CardHeader":  ("@/components/ui/card", True, None, None),
    "CardContent": ("@/components/ui/card", True, None, None),
    "CardTitle":   ("@/components/ui/card", True, None, None),
    "CardDescription": ("@/components/ui/card", True, None, None),
    "CardFooter":  ("@/components/ui/card", True, None, None),
    "Dialog":      ("@/components/ui/dialog", True, None, None),
    "DialogContent": ("@/components/ui/dialog", True, None, None),
    "DialogTrigger": ("@/components/ui/dialog", True, None, None),
    "DialogHeader": ("@/components/ui/dialog", True, None, None),
    "DialogTitle": ("@/components/ui/dialog", True, None, None),
    "DialogDescription": ("@/components/ui/dialog", True, None, None),
    "Select":      ("@/components/ui/select", True, None, None),
    "SelectTrigger": ("@/components/ui/select", True, None, None),
    "SelectContent": ("@/components/ui/select", True, None, None),
    "SelectItem":  ("@/components/ui/select", True, None, None),
    "SelectValue": ("@/components/ui/select", True, None, None),
    "EmptyState":  ("@/components/ui/empty-state", True, None, None),
    "Section":     ("@/components/ui/section", True, None, None),
    "Hero":        ("@/components/ui/hero", True, None, None),
    "FeatureCard": ("@/components/ui/feature-card", True, None, None),
    "Container":   ("@/components/ui/container", True, None, None),
    # Local utility
    "cn":          ("@/lib/utils", True, None, None),
    # Radix UI primitives
    "Slot":        ("@radix-ui/react-slot", True, "@radix-ui/react-slot", "^1.0.0"),
    # React Router
    "Link":        ("react-router-dom", True, "react-router-dom", "^6.0.0"),
    "NavLink":     ("react-router-dom", True, "react-router-dom", "^6.0.0"),
    "Navigate":    ("react-router-dom", True, "react-router-dom", "^6.0.0"),
    "Outlet":      ("react-router-dom", True, "react-router-dom", "^6.0.0"),
    "useNavigate": ("react-router-dom", True, "react-router-dom", "^6.0.0"),
    "useParams":   ("react-router-dom", True, "react-router-dom", "^6.0.0"),
    "useLocation": ("react-router-dom", True, "react-router-dom", "^6.0.0"),
    # TanStack Query
    "useQuery":    ("@tanstack/react-query", True, "@tanstack/react-query", "^5.0.0"),
    "useMutation": ("@tanstack/react-query", True, "@tanstack/react-query", "^5.0.0"),
    "useQueryClient": ("@tanstack/react-query", True, "@tanstack/react-query", "^5.0.0"),
    # Lucide icons
    "Plus":        ("lucide-react", True, "lucide-react", "^0.300.0"),
    "Trash":       ("lucide-react", True, "lucide-react", "^0.300.0"),
    "Trash2":      ("lucide-react", True, "lucide-react", "^0.300.0"),
    "Pencil":      ("lucide-react", True, "lucide-react", "^0.300.0"),
    "Edit":        ("lucide-react", True, "lucide-react", "^0.300.0"),
    "Edit2":       ("lucide-react", True, "lucide-react", "^0.300.0"),
    "X":           ("lucide-react", True, "lucide-react", "^0.300.0"),
    "Check":       ("lucide-react", True, "lucide-react", "^0.300.0"),
    "Search":      ("lucide-react", True, "lucide-react", "^0.300.0"),
    "Settings":    ("lucide-react", True, "lucide-react", "^0.300.0"),
    "User":        ("lucide-react", True, "lucide-react", "^0.300.0"),
    "Users":       ("lucide-react", True, "lucide-react", "^0.300.0"),
    "Home":        ("lucide-react", True, "lucide-react", "^0.300.0"),
    "Leaf":        ("lucide-react", True, "lucide-react", "^0.300.0"),
    "Heart":       ("lucide-react", True, "lucide-react", "^0.300.0"),
    "Calendar":    ("lucide-react", True, "lucide-react", "^0.300.0"),
    "Clock":       ("lucide-react", True, "lucide-react", "^0.300.0"),
    "Bell":        ("lucide-react", True, "lucide-react", "^0.300.0"),
    "Menu":        ("lucide-react", True, "lucide-react", "^0.300.0"),
    "ChevronRight": ("lucide-react", True, "lucide-react", "^0.300.0"),
    "ChevronLeft": ("lucide-react", True, "lucide-react", "^0.300.0"),
    "ChevronDown": ("lucide-react", True, "lucide-react", "^0.300.0"),
    "ChevronUp":   ("lucide-react", True, "lucide-react", "^0.300.0"),
    "ArrowLeft":   ("lucide-react", True, "lucide-react", "^0.300.0"),
    "ArrowRight":  ("lucide-react", True, "lucide-react", "^0.300.0"),
}

_BUILTIN_IDENTIFIERS = frozenset({
    "React", "Fragment", "console", "window", "document", "Math",
    "Date", "JSON", "Object", "Array", "Number", "String", "Boolean",
    "Promise", "Error", "Map", "Set", "Symbol", "RegExp",
    "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    "fetch", "alert", "prompt", "confirm", "navigator", "location",
    "localStorage", "sessionStorage",
})

_JSX_SCOPE_IMPORT_RE = re.compile(
    r"^import\s+(?:"
    r"(?P<default>[A-Za-z_$][\w$]*)"
    r"|"
    r"\{\s*(?P<named>[^}]+)\s*\}"
    r"|"
    r"\*\s+as\s+(?P<star>[A-Za-z_$][\w$]*)"
    r")(?:\s*,\s*\{\s*(?P<also_named>[^}]+)\s*\})?"
    r"\s+from\s+['\"][^'\"]+['\"]\s*;?\s*$",
    re.MULTILINE,
)


def _extract_imported_names(content: str) -> set[str]:
    """Return all identifiers brought into scope by import statements."""
    names: set[str] = set()
    for m in _JSX_SCOPE_IMPORT_RE.finditer(content):
        if m.group("default"):
            names.add(m.group("default"))
        if m.group("star"):
            names.add(m.group("star"))
        for grp in ("named", "also_named"):
            raw = m.group(grp)
            if not raw:
                continue
            for entry in raw.split(","):
                entry = entry.strip()
                if not entry:
                    continue
                if " as " in entry:
                    _, local = entry.split(" as ", 1)
                    names.add(local.strip())
                else:
                    names.add(entry.strip())
    return names


def _extract_local_declarations(content: str) -> set[str]:
    """Return identifiers declared at file scope (function, const, let, class)."""
    names: set[str] = set()
    for m in re.finditer(
        r"^(?:export\s+(?:default\s+)?)?"
        r"(?:function|class|const|let|var)\s+([A-Za-z_$][\w$]*)",
        content, re.MULTILINE,
    ):
        names.add(m.group(1))
    return names


def _extract_used_identifiers(content: str) -> set[str]:
    """Return identifiers used as JSX components or value references.
    Conservative: only PascalCase identifiers (likely components) and
    lowercase identifiers that appear in _CANONICAL_IMPORTS (hooks/utils).
    """
    names: set[str] = set()
    for m in re.finditer(r"<([A-Z][A-Za-z0-9_]*)\b", content):
        names.add(m.group(1))
    for m in re.finditer(r"\b([a-z][A-Za-z0-9_]*)\s*\(", content):
        ident = m.group(1)
        if ident in _CANONICAL_IMPORTS:
            names.add(ident)
    for name in _CANONICAL_IMPORTS:
        if name[0].isupper() and re.search(rf"\b{re.escape(name)}\b", content):
            names.add(name)
    return names


def _inject_imports_into_jsx(
    content: str, by_source: dict[str, list[str]],
) -> str:
    """Insert import statements for each {source: [names]} pair.

    Uses brace-balanced line walking to:
      - Skip past multi-line imports cleanly.
      - Merge with existing imports from the same source instead of
        adding a duplicate line.

    Returns the modified content. Idempotent.
    """
    lines = content.splitlines(keepends=True)

    # Walk through lines tracking when we are inside an import block
    # (single-line OR multi-line). End of the import block is the
    # first non-import, non-blank line.
    import_block_end = 0
    i = 0
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        if not (stripped.startswith("import ") or stripped.startswith("from ")):
            break
        # Found an import. Walk forward until braces are balanced.
        combined = lines[i]
        open_braces = combined.count("{") - combined.count("}")
        j = i + 1
        while open_braces > 0 and j < n:
            combined += lines[j]
            open_braces = combined.count("{") - combined.count("}")
            j += 1
        import_block_end = j
        i = j

    # Identify existing named imports per source in the import block.
    existing_block = "".join(lines[:import_block_end])
    existing_imports: dict[str, set[str]] = {}
    named_re = re.compile(
        r'import\s*\{([^}]+)\}\s*from\s*["\']([^"\']+)["\']',
        re.DOTALL,
    )
    for m in named_re.finditer(existing_block):
        raw = m.group(1)
        src = m.group(2)
        for entry in (e.strip() for e in raw.split(",")):
            if not entry:
                continue
            local = entry.split(" as ")[-1].strip()
            existing_imports.setdefault(src, set()).add(local)

    # For each requested source, compute new names only.
    # If the source already has imports, merge them in-place.
    # Otherwise, build a fresh import line for appending.
    new_block = "".join(lines[:import_block_end])
    append_lines: list[str] = []

    for src in sorted(by_source):
        requested = set(by_source[src])
        have = existing_imports.get(src, set())
        to_add = requested - have
        if not to_add:
            continue
        if have:
            # Preserve the raw existing names text (keeps aliases like
            # "Edit as Pencil") and only append the truly new names.
            new_block = re.sub(
                r'(import\s*\{)([^}]+)(\}\s*from\s*["\'])'
                + re.escape(src)
                + r'(["\'])',
                lambda m, _to_add=to_add, _src=src: (
                    f"{m.group(1)}"
                    f"{m.group(2).rstrip().rstrip(',')}, "
                    f"{', '.join(sorted(_to_add))} "
                    f"{m.group(3)}{_src}{m.group(4)}"
                ),
                new_block,
                count=1,
                flags=re.DOTALL,
            )
        else:
            append_lines.append(
                f'import {{ {", ".join(sorted(to_add))} }} from "{src}";\n'
            )

    tail = "".join(lines[import_block_end:])

    if append_lines:
        sep = "" if new_block.endswith("\n\n") else "\n"
        new_block = new_block + "".join(append_lines) + sep

    return new_block + tail


def _fix_unresolved_identifiers(
    test_results: dict, generated_files: dict,
) -> dict:
    """Scan JSX/TSX files for identifiers used but not imported.
    Auto-add canonical imports for whitelisted identifiers.
    Sync package.json with any external deps that became required.
    Unknown identifiers are logged but not fixed.
    """
    import json as _json
    _log = structlog.get_logger("debugger")
    fixes: dict[str, str] = {}

    pkg_deps_to_add: dict[str, str] = {}

    for path, content in list(generated_files.items()):
        if not path.startswith("frontend/src/"):
            continue
        if not re.search(r"\.(jsx|tsx)$", path):
            continue
        if not content:
            continue

        imported = _extract_imported_names(content)
        declared = _extract_local_declarations(content)
        in_scope = imported | declared | _BUILTIN_IDENTIFIERS

        used = _extract_used_identifiers(content)

        missing = [
            name for name in used
            if name in _CANONICAL_IMPORTS and name not in in_scope
        ]
        if not missing:
            continue

        by_source: dict[str, list[str]] = {}
        for name in sorted(set(missing)):
            src, named, pkg, ver = _CANONICAL_IMPORTS[name]
            by_source.setdefault(src, []).append(name)
            if pkg and ver:
                pkg_deps_to_add[pkg] = ver

        new_content = _inject_imports_into_jsx(content, by_source)
        if new_content != content:
            fixes[path] = new_content
            _log.info(
                "fix_unresolved_identifiers.added_imports",
                path=path, names=sorted(set(missing)),
            )

    pkg_path = "frontend/package.json"
    pkg_content = generated_files.get(pkg_path, "")
    if pkg_deps_to_add and pkg_content:
        try:
            pkg = _json.loads(pkg_content)
            deps = pkg.setdefault("dependencies", {})
            changed = False
            for dep, ver in pkg_deps_to_add.items():
                if dep not in deps:
                    deps[dep] = ver
                    changed = True
            if changed:
                fixes[pkg_path] = _json.dumps(pkg, indent=2) + "\n"
                _log.info(
                    "fix_unresolved_identifiers.synced_package_json",
                    added=list(pkg_deps_to_add.keys()),
                )
        except _json.JSONDecodeError:
            pass

    return fixes


def _fix_broken_query_fn(test_results: dict, generated_files: dict) -> dict:
    """Detect common queryFn anti-patterns and rewrite to canonical form.

    Handles:
      - queryFn: () => api.get("/x").then(r => r.data)  → strips .then(r => r.data)
      - queryFn body that calls api.X without returning  → logs warning

    Only touches .jsx/.tsx under frontend/src/.
    """
    import re as _re
    _log = structlog.get_logger("debugger")
    fixes: dict[str, str] = {}

    target_re = _re.compile(r"\.(jsx|tsx)$")

    # Pattern 1: api.get/post/etc(...).then(r => r.data) — api.js already returns data
    then_data_re = _re.compile(
        r'(api\.(get|post|put|patch|delete)\s*\([^)]+\))'
        r'\.then\(\s*[^)]*?\.data\s*\)',
    )

    # Pattern 2: missing return — queryFn body calls api.X but doesn't return it
    missing_return_re = _re.compile(
        r'queryFn\s*:\s*(?:async\s*)?\(\s*\)\s*=>\s*\{\s*'
        r'(?:[^}]*\n)*?\s*api\.(get|post|put|patch|delete)\s*\([^)]+\)\s*;?\s*\}',
        _re.MULTILINE,
    )

    for path, content in list(generated_files.items()):
        if not target_re.search(path):
            continue
        if not path.startswith("frontend/src/"):
            continue
        if not content or "useQuery" not in content:
            continue

        new_content = content
        changed = False

        new_attempt = then_data_re.sub(lambda m: m.group(1), new_content)
        if new_attempt != new_content:
            new_content = new_attempt
            changed = True
            _log.info("fix_broken_query_fn.stripped_then_data", path=path)

        if missing_return_re.search(new_content):
            _log.warning(
                "fix_broken_query_fn.missing_return_detected",
                path=path,
                hint="queryFn body calls api.X but doesn't return it — convert to () => api.get('/x')",
            )

        if changed:
            fixes[path] = new_content

    return fixes


def _strip_social_auth_ui(test_results: dict, generated_files: dict) -> dict:
    """Remove social/OAuth auth UI from LoginPage and RegisterPage.

    Strips:
      - Import lines for known social-auth packages
      - Single-line social-auth buttons (Google, GitHub, etc.)
      - "Or continue with" divider lines
      - OAuth redirect links

    Idempotent. Runs on LoginPage, RegisterPage, and path-matched login/register files.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict = {}

    for file_path, content in generated_files.items():
        if not content:
            continue
        basename = file_path.rsplit("/", 1)[-1].lower()
        is_target = (
            basename in _LOGIN_REGISTER_NAMES
            or any(kw in file_path.lower() for kw in ("/login", "/register", "/signin", "/signup"))
        )
        if not is_target:
            continue

        new = content
        changed = False

        for pat, label in [
            (_SOCIAL_IMPORT_RE, "import"),
            (_SOCIAL_BUTTON_RE, "button"),
            (_OR_WITH_LINE_RE, "or-divider"),
            (_OAUTH_HREF_RE, "oauth-link"),
        ]:
            new2 = pat.sub("", new)
            if new2 != new:
                new = new2
                changed = True
                _log.info("strip_social_auth_ui.removed", path=file_path, kind=label)

        # Collapse excess blank lines
        new = re.sub(r"\n{3,}", "\n\n", new)

        if changed and new != content:
            fixes[file_path] = new

    return fixes


def _strip_oauth_backend_routes(test_results: dict, generated_files: dict) -> dict:
    """Remove backend route files whose routes exclusively serve OAuth/social auth paths.

    When a file has only OAuth routes (e.g., /auth/google, /auth/callback/github),
    nulls out its content and removes its include_router call from main.py.
    Files with a mix of OAuth and legitimate routes are left untouched — the LLM
    debug cycle handles them.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict = {}

    main_path = "backend/app/main.py"
    main_content = fixes.get(main_path, generated_files.get(main_path, ""))

    for file_path, content in generated_files.items():
        if not file_path.startswith("backend/app/routes/") or not file_path.endswith(".py"):
            continue
        if not content or not _OAUTH_ROUTE_DECO_RE.search(content):
            continue

        total_routes = len(re.findall(r"^@\w+\.\w+\s*\(", content, re.MULTILINE))
        oauth_routes = len(_OAUTH_ROUTE_DECO_RE.findall(content))

        if oauth_routes == 0 or total_routes > oauth_routes:
            continue  # Mixed file — leave for LLM

        # All routes are OAuth — blank the file and remove its wiring from main.py
        stem = file_path.rsplit("/", 1)[-1].replace(".py", "")
        module = file_path.replace("backend/", "").replace("/", ".").replace(".py", "")

        fixes[file_path] = f'"""OAuth routes removed -- email+password only."""\n'
        _log.info("strip_oauth_backend_routes.removed_file", path=file_path)

        if main_content:
            new_main = re.sub(
                rf"^from {re.escape(module)} import[^\n]*\n",
                "",
                main_content,
                flags=re.MULTILINE,
            )
            new_main = re.sub(
                rf"^app\.include_router\(\s*{re.escape(stem)}[^\n]*\n",
                "",
                new_main,
                flags=re.MULTILINE,
            )
            if new_main != main_content:
                main_content = new_main
                fixes[main_path] = main_content
                _log.info("strip_oauth_backend_routes.removed_from_main", stem=stem)

    return fixes


def _fix_admin_prefix_missing(test_results: dict, generated_files: dict) -> dict:
    """Fix frontend API calls that are missing the /admin/ prefix.

    Triggered by CONTRACT MISS: ADMIN-PREFIX messages, which fire when an admin
    component calls /api/<resource> while the backend only serves
    /api/admin/<resource>.

    Locates the named source file and replaces the wrong path literal with the
    corrected /api/admin/<resource> path.  Idempotent.
    """
    contract_log = (test_results.get("logs", {}) or {}).get("contract", "") or ""
    advisory_log = (test_results.get("logs", {}) or {}).get("contract_advisory", "") or ""
    combined = contract_log + "\n" + advisory_log

    if "ADMIN-PREFIX" not in combined:
        return {}

    # Build the set of resource names that belong under /api/admin/ from the
    # contract log.  Strip any trailing punctuation that \S+ in the contract regex
    # may have over-captured (e.g. the stray ";" that triggered this bug).
    known_admin_resources: set[str] = set()
    source_files: list[str] = []
    for miss in _ADMIN_PREFIX_MISS_RE.finditer(combined):
        corrected = miss.group("corrected").rstrip("/;,.")
        m = re.match(r"/api/admin/([^/\s;,]+)", corrected)
        if m:
            known_admin_resources.add(m.group(1))
        source_files.append(miss.group("source").strip())

    if not known_admin_resources:
        return {}

    fixes: dict[str, str] = {}

    # Rewrite every frontend source file named in the contract misses.
    for source_basename in source_files:
        target_file: str | None = None
        for fp in generated_files:
            if fp.endswith(source_basename) or fp.rsplit("/", 1)[-1] == source_basename:
                target_file = fp
                break
        if target_file is None:
            continue

        content = fixes.get(target_file, generated_files.get(target_file, ""))
        if not content:
            continue

        new_content = _rewrite_admin_prefix(content, known_admin_resources)

        if new_content != content:
            fixes[target_file] = new_content
            structlog.get_logger("debugger").info(
                "fix_admin_prefix_missing.applied",
                file=target_file,
                admin_resources=sorted(known_admin_resources),
            )

    return fixes


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


_OAUTH2_PARAM_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?P<param>\w+)\s*:\s*OAuth2PasswordRequestForm\s*=\s*Depends\(\s*\)(?P<comma>,?)$"
)
_LOGIN_FN_RE = re.compile(
    r"(?:async\s+)?def\s+(?:login|signin|token)\s*\("
)
_REGISTER_FN_RE = re.compile(
    r"(?:async\s+)?def\s+(?:register|signup)\s*\("
)


def _fix_auth_body_shape(test_results: dict, generated_files: dict) -> dict:
    """When contract check flags AUTH-SHAPE, convert the BACKEND from
    OAuth2PasswordRequestForm to Pydantic LoginRequest/RegisterRequest.

    Converts EVERY function using OAuth2PasswordRequestForm in a single pass.
    Schema selection uses BOTH the route path (from @router decorator) AND
    the function name, so non-standard function names like `login_user` or
    `authenticate` are still caught.

    CRITICAL: if any OAuth2PasswordRequestForm reference survives, the fix
    is NOT recorded — "applied" is only logged when conversion is complete.
    """
    contract_log = (test_results.get("logs", {}) or {}).get("contract", "") or ""
    if "AUTH-SHAPE" not in contract_log:
        return {}

    _auth_log = structlog.get_logger("_fix_auth_body_shape")
    fixes = {}
    for path, content in generated_files.items():
        if not path.endswith(".py") or not path.startswith("backend/"):
            continue
        if "OAuth2PasswordRequestForm" not in (content or ""):
            continue

        new = content

        # (a) Inject Pydantic schema classes before the first route decorator.
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
            anchor = re.search(r"^\s*@(?:router|app)\.\w+\(", new, re.MULTILINE)
            if anchor:
                pos = anchor.start()
                if "class LoginRequest" not in new[:pos]:
                    new = new[:pos] + insert_block + new[pos:]
            else:
                if "class LoginRequest" not in new:
                    new = new.rstrip() + "\n\n\n" + insert_block

        # (b) Line-by-line rewrite of OAuth2 parameter declarations.
        #     Schema detection uses route PATH (from @router decorator) AND
        #     function name — so non-standard names like `authenticate` still work.
        #
        #     KEY BUG FIX: `decorator_set_schema` prevents the `def` line from
        #     resetting a schema already established by the preceding route decorator.
        #     Without this, `@router.post("/auth/login")` sets "LoginRequest" but
        #     the next `async def authenticate(` resets it to None because the
        #     function name doesn't match _LOGIN_FN_RE.
        _REGISTER_ROUTE_RE = re.compile(
            r"@(?:router|app)\.\w+\(['\"][^'\"]*(?:register|signup|create.?user)[^'\"]*['\"]"
        )
        _LOGIN_ROUTE_RE = re.compile(
            r"@(?:router|app)\.\w+\(['\"][^'\"]*(?:login|signin|token|auth)[^'\"]*['\"]"
        )

        lines = new.split("\n")
        current_schema: str | None = None
        decorator_set_schema: bool = False  # True after a route decorator sets the schema
        new_lines: list[str] = []
        changed_params: set[str] = set()

        for line in lines:
            stripped = line.strip()
            # Route decorator: set schema AND lock so the `def` line below keeps it.
            if _LOGIN_ROUTE_RE.search(stripped):
                current_schema = "LoginRequest"
                decorator_set_schema = True
            elif _REGISTER_ROUTE_RE.search(stripped):
                current_schema = "RegisterRequest"
                decorator_set_schema = True
            elif re.match(r"(?:async\s+)?def\s+\w+\s*\(", stripped):
                if decorator_set_schema:
                    # Schema was set by the preceding route decorator — keep it, unlock.
                    decorator_set_schema = False
                elif _LOGIN_FN_RE.search(stripped):
                    current_schema = "LoginRequest"
                elif _REGISTER_FN_RE.search(stripped):
                    current_schema = "RegisterRequest"
                else:
                    current_schema = None  # unrelated function — reset

            # Handle parameter on its OWN line (the common multi-line signature case)
            m = _OAUTH2_PARAM_LINE_RE.match(line)
            if m and current_schema:
                indent = m.group("indent")
                param = m.group("param")
                comma = m.group("comma")
                new_lines.append(f"{indent}payload: {current_schema}{comma}")
                changed_params.add(param)
                continue

            # Handle parameter INLINE with the function def
            # e.g.: `async def login(form: OAuth2PasswordRequestForm = Depends(), db=...)`
            if current_schema and "OAuth2PasswordRequestForm" in line:
                inline_m = re.search(
                    r"(\w+)\s*:\s*OAuth2PasswordRequestForm\s*=\s*Depends\(\)",
                    line,
                )
                if inline_m:
                    param = inline_m.group(1)
                    changed_params.add(param)
                    line = line.replace(
                        inline_m.group(0), f"payload: {current_schema}"
                    )

            new_lines.append(line)

        new = "\n".join(new_lines)

        # (c) Replace body references using the captured param names AND common aliases.
        for pname in changed_params | {"form", "form_data", "credentials", "data", "request"}:
            new = re.sub(rf'\b{re.escape(pname)}\.username\b', 'payload.email', new)
            new = re.sub(rf'\b{re.escape(pname)}\.password\b', 'payload.password', new)

        # (d) Drop the OAuth2 import.
        new = re.sub(
            r"^from\s+fastapi\.security\s+import\s+OAuth2PasswordRequestForm\s*$\n?",
            "",
            new,
            flags=re.MULTILINE,
        )
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

        # (e) CRITICAL: only claim "applied" when conversion is COMPLETE.
        #     If any OAuth2PasswordRequestForm reference survives, do NOT record
        #     the fix — the orchestrator must not log "applied" with remaining refs.
        if new == content:
            continue
        remaining = new.count("OAuth2PasswordRequestForm")
        if remaining > 0:
            _auth_log.error(
                "fix_auth_body_shape.conversion_failed",
                path=path,
                remaining_refs=remaining,
                reason="could_not_convert_all_endpoints",
            )
            # Leave the original file unchanged — next cycle will retry or
            # STRICT mode fires with a clear reason.
            continue

        fixes[path] = new
    return fixes


def _fix_method_mismatch(test_results: dict, generated_files: dict) -> dict:
    """Fix frontend calls using wrong HTTP method per CONTRACT MISS: METHOD log lines.

    Picks the most semantically similar method from the served list using
    _METHOD_SIMILARITY (e.g. PUT -> PATCH preferred over GET for updates).
    """
    contract_log = (test_results.get("logs", {}) or {}).get("contract", "") or ""
    if "CONTRACT MISS: METHOD" not in contract_log:
        return {}
    method_fixes: dict[str, str] = {}
    for m in _METHOD_MISS_RE.finditer(contract_log):
        bad_method = m.group("bad_method").upper()
        path = m.group("path")
        methods_raw = m.group("methods") or ""
        # Extract all method names from the served list (handles ['GET', 'PATCH']).
        served_methods = [
            mm.upper() for mm in re.findall(r"[A-Za-z]+", methods_raw)
            if mm.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE")
        ]
        if not served_methods:
            continue
        # Pick the most semantically similar method.
        preferred = _METHOD_SIMILARITY.get(bad_method, [])
        good_method = next(
            (p for p in preferred if p in served_methods),
            served_methods[0],
        )
        bad_lower = bad_method.lower()
        good_lower = good_method.lower()
        # Contract miss paths have the /api prefix (OpenAPI ground-truth), but
        # frontend api.X calls use relative paths without /api (the client
        # prepends it at runtime). Build patterns for both forms.
        rel_path = path[4:] if path.startswith("/api/") else path
        path_alts = "|".join(
            re.escape(p) for p in sorted({path, rel_path}, key=len, reverse=True)
        )
        pattern = re.compile(
            rf'(\b(?:api|axios))\.{bad_lower}\(\s*(["\'`])(?:{path_alts})\2'
        )
        for fp, content in list(generated_files.items()):
            if not fp.startswith("frontend/src/") or not content:
                continue
            working = method_fixes.get(fp) or content
            # Replace using whichever path form appears in the source.
            def _replace(m, _good=good_lower):
                matched_path = m.group(0)
                # Preserve the original path form used in the source.
                path_in_src = re.search(r'["\'`]([^"\'`]+)["\'`]', matched_path)
                p = path_in_src.group(1) if path_in_src else rel_path
                q = m.group(2)
                return f"{m.group(1)}.{_good}({q}{p}{q}"
            new_content, count = pattern.subn(_replace, working)
            if count > 0:
                method_fixes[fp] = new_content
    return method_fixes


def _apply_ts_strip(line: str) -> str:
    """Apply TS-stripping regexes to a single non-import line."""
    line = _AS_CONST_RE.sub("", line)
    line = _AS_TYPE_RE.sub("", line)
    line = _TYPED_PARAM_RE.sub(r'\1\2\3', line)
    return line


def _apply_ts_strip_to_content(content: str) -> str:
    """Strip TypeScript syntax line-by-line, skipping import/export-from lines.

    Skipping import lines prevents `import * as React from "react"` from
    being mangled into `import * from "react"` by the `as` regexes.
    """
    lines = content.split("\n")
    in_paren_import = 0
    new_lines = []
    for line in lines:
        stripped = line.lstrip()
        is_import_line = (
            stripped.startswith("import ")
            or stripped.startswith("from ")
            or (
                stripped.startswith("export ")
                and ("from " in stripped or stripped.startswith("export {"))
            )
        )
        if in_paren_import > 0 or is_import_line:
            in_paren_import += line.count("(") - line.count(")")
            in_paren_import = max(in_paren_import, 0)
            new_lines.append(line)
            continue
        new_lines.append(_apply_ts_strip(line))
    return "\n".join(new_lines)


def _strip_typescript_from_jsx(test_results: dict, generated_files: dict) -> dict:
    """Remove TypeScript syntax from .jsx files so esbuild does not reject them.

    Skips import/re-export lines so namespace imports like
    `import * as React from "react"` are never damaged.
    """
    fixes = {}
    for path, content in generated_files.items():
        if not path.endswith(".jsx") or not content:
            continue
        new = _apply_ts_strip_to_content(content)
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


# ── Missing /users/me endpoint fixer ────────────────────────────────────────

_USERS_ME_MISS_RE = re.compile(r'CONTRACT MISS[^\n]*GET[^\n]*/api/users/me\b', re.IGNORECASE)

# Matches frontend URL literals that call /users/me so they can be rewritten to /auth/me.
# (?P=q) ensures the closing quote balances the opening one.
_USERS_ME_RE = re.compile(
    r"""(?P<q>["'`])(?P<prefix>/?(?:api/)?)users/me(?P=q)""",
    re.VERBOSE,
)
_USERS_ME_ROUTE_RE = re.compile(
    r'@\w+\.get\(\s*["\'](?:/users)?/me["\']',
    re.IGNORECASE,
)
# Recognise common user-response schema names the project might use.
_USER_OUT_NAMES = ("UserOut", "UserRead", "UserResponse", "UserSchema", "UserPublic")
# Recognise auth-dependency function names the project might use.
_AUTH_DEP_NAMES = (
    "get_current_user", "get_current_active_user", "require_auth",
    "current_user", "get_user", "authenticate",
)


def _fix_missing_users_me(test_results: dict, generated_files: dict) -> dict:
    """Inject GET /users/me into the users/auth router when contract check flags it missing."""
    contract_log = (test_results.get("logs", {}) or {}).get("contract", "") or ""
    if not _USERS_ME_MISS_RE.search(contract_log):
        return {}

    # Find the best target file: prefer users.py, fall back to auth.py.
    candidate_paths = [
        "backend/app/routes/users.py",
        "backend/app/routes/auth.py",
        "backend/app/routes/user.py",
    ]
    target_path: str | None = None
    target_content: str | None = None
    for cp in candidate_paths:
        c = generated_files.get(cp)
        if c:
            target_path = cp
            target_content = c
            break
    if target_path is None or target_content is None:
        return {}

    # Idempotent: skip if the route already exists.
    if _USERS_ME_ROUTE_RE.search(target_content):
        return {}

    # Detect which UserOut schema the file already imports (or uses).
    response_model = "UserOut"
    for name in _USER_OUT_NAMES:
        if name in target_content:
            response_model = name
            break

    # Detect which auth-dependency function is used.
    auth_dep = "get_current_user"
    for name in _AUTH_DEP_NAMES:
        if re.search(rf'\bDepends\s*\(\s*{re.escape(name)}\s*\)', target_content):
            auth_dep = name
            break

    # Build the minimal route block.
    route_block = (
        f"\n\n@router.get(\"/users/me\", response_model={response_model})\n"
        f"def read_users_me(\n"
        f"    current_user: User = Depends({auth_dep}),\n"
        f") -> {response_model}:\n"
        f"    return current_user\n"
    )

    # Insert BEFORE the first @router. decorator so it appears near top of routes.
    first_route = re.search(r'^@router\.', target_content, re.MULTILINE)
    if first_route:
        pos = first_route.start()
        new_content = target_content[:pos] + route_block.lstrip("\n") + "\n" + target_content[pos:]
    else:
        new_content = target_content.rstrip() + route_block

    # Ensure User model is imported.
    if "from app.models" in new_content and "User" not in new_content.split("from app.models")[0]:
        new_content = re.sub(
            r'(from app\.models import )([^\n]+)',
            lambda m: m.group(0) if "User" in m.group(2) else m.group(1) + "User, " + m.group(2),
            new_content, count=1,
        )

    return {target_path: new_content}


def _fix_users_me_to_auth_me(test_results: dict, generated_files: dict) -> dict:
    """Rewrite frontend /users/me (or /api/users/me) URL literals to /auth/me.

    The scaffold auth routes expose the current user at GET /api/auth/me, not
    /api/users/me.  Idempotent — only rewrites files that still contain the
    wrong path.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict = {}
    for path, content in generated_files.items():
        if not path.endswith((".jsx", ".tsx", ".js", ".ts")):
            continue
        if "users/me" not in content:
            continue
        new = _USERS_ME_RE.sub(
            lambda m: f"{m.group('q')}{m.group('prefix')}auth/me{m.group('q')}",
            content,
        )
        if new != content:
            fixes[path] = new
            _log.info("fix_users_me_to_auth_me.applied", path=path)
    return fixes


# ── Auth dual-call and direct-authApi fixer ──────────────────────────────────

# Matches: import { authApi } from "@/lib/auth"
_AUTHAPI_IMPORT_RE = re.compile(
    r"^import\s+\{[^}]*\bauthApi\b[^}]*\}\s+from\s+[\"']@/lib/auth[\"'];?\s*$\n?",
    re.MULTILINE,
)
# Matches: [const result =] await authApi.login/register(args)
# Handles one level of nested parens in args (e.g. getEmail()).
_AUTHAPI_CALL_RE = re.compile(
    r"(?:(?:const|let|var)\s+(\w+)\s*=\s*)?await\s+authApi\.(login|register)\s*"
    r"\(([^()]*(?:\([^()]*\)[^()]*)*)\)\s*;?",
    re.MULTILINE,
)
# Matches: await login(tokenVar) or await login(result.access_token) —
# a single non-object arg (the dual-call follow-up pattern).
_DUAL_FOLLOW_TOKEN_RE = re.compile(
    r"await\s+(?:login|register)\s*\(\s*[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*\s*\)"
)
# Matches: await login(emailVar, passwordVar[, nameVar]) — positional args
_POSITIONAL_AUTH_CALL_RE = re.compile(
    r"(await\s+(?:login|register)\s*\()\s*"
    r"([a-zA-Z_]\w*)\s*,\s*([a-zA-Z_]\w*)(?:\s*,\s*([a-zA-Z_]\w*))?\s*(\))"
)


def _normalize_auth_args(raw: str) -> str:
    """Convert 'email, password' or '{email, password}' → '{ email, password }'."""
    s = raw.strip()
    if s.startswith("{"):
        return s  # already object form
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if not parts:
        return "{}"
    if len(parts) == 1:
        v = parts[0]
        return f"{{ email: {v}, password }}"
    elif len(parts) == 2:
        e, p = parts
        email_part = e if e == "email" else f"email: {e}"
        pass_part = p if p == "password" else f"password: {p}"
        return f"{{ {email_part}, {pass_part} }}"
    else:
        e, p, n = parts[0], parts[1], parts[2]
        email_part = e if e == "email" else f"email: {e}"
        pass_part = p if p == "password" else f"password: {p}"
        name_part = n if n == "name" else f"name: {n}"
        return f"{{ {email_part}, {pass_part}, {name_part} }}"


def _fix_dual_auth_call(test_results: dict, generated_files: dict) -> dict:
    """Fix three auth anti-patterns in page / component files.

    Pattern A (DUAL CALL): page calls authApi.login() then passes the token to
      useAuth().login() → 422 because a JWT is not valid credentials.
      Fix: remove the authApi call and rewrite the follow-up to use credentials.

    Pattern B (DIRECT authApi): page calls authApi.login() without useAuth.
      Fix: remove the authApi import, replace the call with useAuth().login().

    Pattern C (POSITIONAL): page calls login(email, password) positionally.
      Fix: rewrite to login({ email, password }).

    Regex patterns used:
      _AUTHAPI_IMPORT_RE  — import { authApi } from "@/lib/auth"
      _AUTHAPI_CALL_RE    — [const x =] await authApi.login/register(args)
      _DUAL_FOLLOW_TOKEN_RE — await login(singleTokenVar)
      _POSITIONAL_AUTH_CALL_RE — await login(emailVar, passwordVar)
    """
    _log = structlog.get_logger("debugger")
    fixes: dict = {}

    for path, content in generated_files.items():
        if not content:
            continue
        if not any(path.startswith(p) for p in ("frontend/src/pages/", "frontend/src/components/")):
            continue
        if not path.endswith((".jsx", ".tsx", ".js", ".ts")):
            continue

        new = content

        # ── Pattern C: positional args to useAuth login/register ──────────
        def _rewrite_positional(m):
            fn = m.group(1)     # "await login(" or "await register("
            arg1 = m.group(2)   # email variable name
            arg2 = m.group(3)   # password variable name
            arg3 = m.group(4)   # optional name variable
            close = m.group(5)
            e_part = arg1 if arg1 == "email" else f"email: {arg1}"
            p_part = arg2 if arg2 == "password" else f"password: {arg2}"
            if arg3:
                n_part = arg3 if arg3 == "name" else f"name: {arg3}"
                return f"{fn}{{ {e_part}, {p_part}, {n_part} }}{close}"
            return f"{fn}{{ {e_part}, {p_part} }}{close}"

        new, c_count = _POSITIONAL_AUTH_CALL_RE.subn(_rewrite_positional, new)
        if c_count > 0:
            _log.info("fix_dual_auth_call.pattern_c", path=path, count=c_count)

        # ── Patterns A & B: direct authApi usage ─────────────────────────
        if "authApi" not in new:
            if new != content:
                fixes[path] = new
            continue

        has_authapi_import = bool(_AUTHAPI_IMPORT_RE.search(new))
        authapi_calls = list(_AUTHAPI_CALL_RE.finditer(new))
        if not has_authapi_import or not authapi_calls:
            if new != content:
                fixes[path] = new
            continue

        has_useauth = "useAuth" in new

        # Replace each authApi.login/register call via substitution (no offset
        # tracking — positions shift after the import line is removed).
        def _rewrite_authapi(m):
            raw_args = (m.group(3) or "").strip()
            method = m.group(2)
            return f"await {method}({_normalize_auth_args(raw_args)})"

        new = _AUTHAPI_CALL_RE.sub(_rewrite_authapi, new)

        # Remove authApi import
        new = _AUTHAPI_IMPORT_RE.sub("", new)

        # Pattern A follow-up: remove `await login(someToken)` that immediately
        # followed the authApi call — that second call is now redundant because
        # the authApi call above was rewritten to login({...}).
        if has_useauth:
            # Only remove single-identifier or dotted-path args (token shape),
            # never object literals `{...}` or string literals.
            new = _DUAL_FOLLOW_TOKEN_RE.sub("", new)
            _log.info("fix_dual_auth_call.pattern_a", path=path)
        else:
            # Pattern B: authApi used without useAuth — add the import
            if 'from "@/contexts/AuthContext"' not in new and "from '@/contexts/AuthContext'" not in new:
                lines = new.split("\n")
                last_import = max(
                    (i for i, ln in enumerate(lines) if ln.strip().startswith("import ")),
                    default=-1,
                )
                insert_at = last_import + 1 if last_import >= 0 else 0
                lines.insert(insert_at, 'import { useAuth } from "@/contexts/AuthContext"')
                new = "\n".join(lines)
            _log.info("fix_dual_auth_call.pattern_b", path=path)

        # Collapse excess blank lines left by removals
        new = re.sub(r"\n{3,}", "\n\n", new)

        if new != content:
            fixes[path] = new

    return fixes


# ── Per-page chrome import fixer ─────────────────────────────────────────────

# Matches import lines that pull in Navbar/Footer/Sidebar/Header by name.
# Handles default imports (import Navbar from "...") and named ({Navbar}) forms.
_CHROME_IMPORT_RE = re.compile(
    r"^import\s+(?:\{[^}]*\b(?:Navbar|Footer|Sidebar|Header)\b[^}]*\}"
    r"|(?:Navbar|Footer|Sidebar|Header)\b)\s+from\s+[\"'][^\"']+[\"'].*$",
    re.MULTILINE,
)
# Matches self-closing or single-line paired chrome JSX tags.
_CHROME_JSX_RE = re.compile(
    r"[ \t]*<(?:Navbar|Footer|Sidebar|Header)\b(?:\s[^>]*)?\s*/?>(?:\s*</(?:Navbar|Footer|Sidebar|Header)>)?[ \t]*\n?",
)


def _fix_per_page_chrome_imports(test_results: dict, generated_files: dict) -> dict:
    """Remove Navbar/Footer/Sidebar imports and JSX usages from page components.

    Chrome elements (Navbar, Footer) belong in App.jsx's Layout wrapper, not in
    individual pages.  Per-page chrome causes duplicate navbars on Layout routes
    and missing navbars on routes that skipped the import.

    A) Import present + JSX present → remove both.
    B) Import present, no JSX → remove unused import.
    C) JSX only, no import → log warning, leave alone (may be a global reference).

    Idempotent: pages that already have no chrome are unchanged.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict = {}

    for path, content in generated_files.items():
        if not path.startswith("frontend/src/pages/") or not content:
            continue
        if not (path.endswith(".jsx") or path.endswith(".tsx")):
            continue

        # Fast pre-check before running regexes
        if not any(name in content for name in ("Navbar", "Footer", "Sidebar", "Header")):
            continue

        has_import = bool(_CHROME_IMPORT_RE.search(content))
        has_jsx = bool(_CHROME_JSX_RE.search(content))

        if not has_import and not has_jsx:
            continue

        # Case C: JSX without a matching import — suspicious, leave alone
        if has_jsx and not has_import:
            _log.warning("fix_per_page_chrome.jsx_without_import", path=path)
            continue

        new = content

        # Remove the import line(s)
        new = _CHROME_IMPORT_RE.sub("", new)

        # Remove the JSX usage (self-closing tags and single-line paired tags)
        if has_jsx:
            new = _CHROME_JSX_RE.sub("", new)

        # Collapse triple+ blank lines left by removals
        new = re.sub(r"\n{3,}", "\n\n", new)

        if new != content:
            fixes[path] = new
            _log.info(
                "fix_per_page_chrome.applied",
                path=path,
                removed_import=has_import,
                removed_jsx=has_jsx,
            )

    # App.jsx Outlet/Layout fix is handled by _fix_app_jsx_layout_pattern (runs earlier).
    return fixes


# ── Missing aggregate endpoint fixer ─────────────────────────────────────────

_AGGREGATE_MISS_RE = re.compile(
    r"CONTRACT MISS[^\n;]*GET[^\n;]*/api/(\w+)/data",
    re.IGNORECASE,
)
_MODEL_BASE_CLASS_RE = re.compile(
    r"^class\s+(\w+)\s*\(\s*Base\s*\)",
    re.MULTILINE,
)
_AGGREGATE_EXCLUDE_NAMES = frozenset({
    "User", "Token", "RefreshToken", "PasswordResetToken", "AuditLog", "ContactMessage",
})
_AGGREGATE_EXCLUDE_RE = re.compile(r"(?:Internal|Admin|Token|Session)", re.IGNORECASE)
_LAST_ROUTE_IMPORT_RE = re.compile(
    r"^from app\.routes\.\w+ import[^\n]*\n",
    re.MULTILINE,
)
_LAST_INCLUDE_ROUTER_RE = re.compile(
    r"^app\.include_router\([^\n]*\n",
    re.MULTILINE,
)


def _fix_missing_aggregate_endpoint(test_results: dict, generated_files: dict) -> dict:
    """Generate missing /api/<slug>/data aggregate endpoint and wire it into main.py.

    Triggered when contract_check reports a GET /api/<slug>/data miss (pattern
    r"CONTRACT MISS[^;]*GET[^;]*/api/(\\w+)/data"). Idempotent: skips when
    aggregate.py already exists with the correct path.
    """
    contract_log = (test_results.get("logs", {}) or {}).get("contract", "") or ""
    advisory_log = (test_results.get("logs", {}) or {}).get("contract_advisory", "") or ""
    combined_log = contract_log + "\n" + advisory_log

    match = _AGGREGATE_MISS_RE.search(combined_log)
    if not match:
        return {}

    slug = match.group(1)
    agg_path = "backend/app/routes/aggregate.py"

    # Idempotent: if aggregate.py already defines a router for this slug, do nothing.
    existing = generated_files.get(agg_path, "")
    if existing and (f'prefix="/{slug}"' in existing or f"prefix='/{slug}'" in existing):
        return {}

    # Scan backend/app/models.py for all Base-inheriting model names.
    models_content = generated_files.get("backend/app/models.py", "")
    if not models_content:
        return {}

    model_names = _MODEL_BASE_CLASS_RE.findall(models_content)
    public_models = [
        n for n in model_names
        if n not in _AGGREGATE_EXCLUDE_NAMES and not _AGGREGATE_EXCLUDE_RE.search(n)
    ]
    if not public_models:
        return {}

    model_imports = ", ".join(public_models)
    queries_lines = "\n".join(
        f'        "{n.lower()}s": [_to_dict(r) for r in db.query({n}).all()],'
        for n in public_models
    )
    agg_content = (
        '"""Aggregate read-only endpoint -- returns all public content in one request."""\n'
        "from fastapi import APIRouter, Depends\n"
        "from sqlalchemy.orm import Session\n"
        "from app.database import get_db\n"
        f"from app.models import {model_imports}\n\n"
        f'router = APIRouter(prefix="/{slug}", tags=["{slug}"])\n\n\n'
        "def _to_dict(obj):\n"
        "    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}\n\n\n"
        f'@router.get("/data")\n'
        f"def get_{slug}_data(db: Session = Depends(get_db)):\n"
        f"    return {{\n"
        f"{queries_lines}\n"
        "    }\n"
    )

    fixes: dict = {agg_path: agg_content}

    # Wire the router into main.py.
    main_content = generated_files.get("backend/app/main.py", "")
    if not main_content or "aggregate_router" in main_content:
        return fixes

    import_line = "from app.routes.aggregate import router as aggregate_router\n"
    include_line = 'app.include_router(aggregate_router, prefix="/api")\n'
    new_main = main_content

    # Insert import after the last `from app.routes.<x> import` line.
    route_imports = list(_LAST_ROUTE_IMPORT_RE.finditer(new_main))
    if route_imports:
        pos = route_imports[-1].end()
        new_main = new_main[:pos] + import_line + new_main[pos:]
    else:
        last_import_m = None
        for m in re.finditer(r"^(?:from|import) [^\n]+\n", new_main, re.MULTILINE):
            last_import_m = m
        if last_import_m:
            pos = last_import_m.end()
            new_main = new_main[:pos] + import_line + new_main[pos:]
        else:
            new_main = import_line + new_main

    # Insert include_router after the last `app.include_router(...)` call.
    includes = list(_LAST_INCLUDE_ROUTER_RE.finditer(new_main))
    if includes:
        pos = includes[-1].end()
        new_main = new_main[:pos] + include_line + new_main[pos:]
    else:
        new_main = new_main.rstrip() + "\n" + include_line

    fixes["backend/app/main.py"] = new_main
    return fixes


# ── Missing / mismatched admin endpoint fixers ───────────────────────────────

# Matches genuine 404s on admin paths — the negative lookahead (?!METHOD\s)
# excludes method-mismatch lines (those go to _fix_method_mismatch_admin).
_ADMIN_MISS_RE = re.compile(
    r"CONTRACT MISS:\s+(?!METHOD\s)(?P<method>GET|POST|PUT|PATCH|DELETE)\s+"
    r"(?P<path>/api/admin/[^\s;,]+)",
    re.IGNORECASE,
)

# Matches method-mismatch CONTRACT MISSes on admin paths.
_ADMIN_METHOD_MISS_RE = re.compile(
    r"CONTRACT MISS: METHOD\s+(?P<bad_method>GET|POST|PUT|PATCH|DELETE)\s+"
    r"(?P<path>/api/admin/[^\s;,]+)\s+"
    r"backend serves this path with:\s+\[(?P<methods>[^\]]+)\]",
    re.IGNORECASE,
)

# Auth-dependency function names that appear in admin route files.
_ADMIN_AUTH_DEP_NAMES = (
    "require_admin", "get_admin_user", "get_current_admin",
    "admin_required", "is_admin", "current_admin",
)


# ── Model verification helpers ────────────────────────────────────────────────

def _model_exists_in_file(content: str, name: str) -> bool:
    return bool(re.search(rf"^class\s+{re.escape(name)}\s*\(", content, re.MULTILINE))


def _closest_model_match(models_content: str, name: str) -> str | None:
    """Return the closest class name in models_content with Levenshtein distance ≤ 3."""
    candidates = re.findall(r"^class\s+(\w+)\s*\(", models_content, re.MULTILINE)
    best, best_d = None, 4
    for cand in candidates:
        d = _levenshtein(name, cand)
        if d < best_d:
            best_d, best = d, cand
    return best


def _resolve_model_name(
    requested: str,
    models_py_content: str,
    _log,
) -> str | None:
    """Return a real SQLAlchemy model class name from models.py that closely
    matches `requested`. Returns None if no good match exists -- caller
    should abort to LLM debug rather than write a handler that queries
    the wrong table.

    Strategy (in order):
      1. Exact match.
      2. Plural/singular convention (Orders → Order, Item → Items).
      3. Fuzzy match via difflib with similarity >= 0.80.
      4. Substring containment (Item in MenuItem).
    Only considers classes that inherit from Base (SQLAlchemy models).
    """
    try:
        tree = ast.parse(models_py_content)
    except SyntaxError:
        _log.warning("resolve_model_name.models_unparseable")
        return None

    available: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = ""
                if isinstance(base, ast.Name):
                    base_name = base.id
                elif isinstance(base, ast.Attribute):
                    base_name = base.attr
                if base_name == "Base" or "Base" in base_name:
                    available.append(node.name)
                    break

    if not available:
        _log.warning("resolve_model_name.no_models_in_file")
        return None

    # Exact match wins.
    if requested in available:
        return requested

    # Plural/singular convention.
    candidates = [requested]
    if requested.endswith("s"):
        candidates.append(requested[:-1])
    else:
        candidates.append(requested + "s")
    for c in candidates:
        if c in available:
            _log.info(
                "resolve_model_name.plural_match",
                requested=requested, matched=c,
            )
            return c

    # Strict fuzzy match: at least 0.80 similarity.
    matches = difflib.get_close_matches(
        requested, available, n=1, cutoff=0.80,
    )
    if matches:
        _log.info(
            "resolve_model_name.fuzzy_match",
            requested=requested, matched=matches[0],
            available=available,
        )
        return matches[0]

    # Last resort: substring containment (handles "Item" inside "MenuItem").
    lower_req = requested.lower()
    for name in available:
        if lower_req in name.lower() or name.lower() in lower_req:
            _log.info(
                "resolve_model_name.substring_match",
                requested=requested, matched=name,
                available=available,
            )
            return name

    _log.warning(
        "resolve_model_name.no_match",
        requested=requested, available=available,
        hint="auto-stub aborted; will fall through to LLM debug",
    )
    return None


def _build_model_stub(model_name: str) -> str:
    """Return a minimal Mapped[] model body to append to models.py."""
    # PascalCase → snake_case plural: VisitorCount → visitor_counts
    table = re.sub(r"(?<!^)(?=[A-Z])", "_", model_name).lower() + "s"
    return (
        f"\n\nclass {model_name}(Base):\n"
        f'    __tablename__ = "{table}"\n'
        f"    id: Mapped[int] = mapped_column(primary_key=True)\n"
        f"    user_id: Mapped[int | None] = mapped_column(\n"
        f'        ForeignKey("users.id"), nullable=True\n'
        f"    )\n"
        f"    created_at: Mapped[datetime] = mapped_column(\n"
        f"        DateTime, default=datetime.utcnow\n"
        f"    )\n"
        f"    updated_at: Mapped[datetime] = mapped_column(\n"
        f"        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow\n"
        f"    )\n"
    )


def _ensure_model_file_imports(content: str) -> str:
    """Inject datetime / SQLAlchemy symbols needed by auto-generated model stubs."""
    if "from datetime import datetime" not in content and "import datetime" not in content:
        content = "from datetime import datetime\n" + content

    # Ensure `from sqlalchemy import` exists with the needed symbols.
    sqla_m = re.search(r"(from sqlalchemy import\s+)([^\n]+)", content)
    if sqla_m:
        existing = sqla_m.group(2)
        to_add = [s for s in ("DateTime", "ForeignKey") if s not in existing]
        if to_add:
            content = content.replace(
                sqla_m.group(0),
                sqla_m.group(1) + existing.rstrip() + ", " + ", ".join(to_add),
                1,
            )
    else:
        # No `from sqlalchemy import` line — inject a minimal one.
        content = "from sqlalchemy import DateTime, ForeignKey\n" + content

    # Ensure Mapped / mapped_column are imported (needed by the Mapped[] stub).
    sqla_orm_m = re.search(r"(from sqlalchemy\.orm import\s+)([^\n]+)", content)
    if sqla_orm_m:
        existing = sqla_orm_m.group(2)
        to_add = [s for s in ("Mapped", "mapped_column") if s not in existing]
        if to_add:
            content = content.replace(
                sqla_orm_m.group(0),
                sqla_orm_m.group(1) + existing.rstrip() + ", " + ", ".join(to_add),
                1,
            )
    else:
        content = "from sqlalchemy.orm import Mapped, mapped_column\n" + content

    return content


# ── Route-file selection helpers ──────────────────────────────────────────────

def _target_admin_route_file(full_api_path: str, generated_files: dict) -> tuple[str, str, str]:
    """Return (file_path, decorator_path, router_prefix) for an /api/admin/* path.

    Selects the most-specific existing route file whose router prefix is a prefix
    of the target route.  Falls back to creating a new resource-specific file.

    The decorator_path is what goes in @router.METHOD("decorator_path") so that
        /api + router_prefix + decorator_path == full_api_path.

    CRITICAL: never returns a file whose prefix would produce the wrong URL.
    """
    route = re.sub(r"^/api", "", full_api_path.rstrip("/"))  # e.g., /admin/visitor-count
    parts = [p for p in route.split("/") if p]

    if not parts or parts[0] != "admin" or len(parts) < 2:
        return "backend/app/routes/admin.py", route, "/admin"

    resource = parts[1]                               # e.g., "visitor-count"
    resource_us = resource.replace("-", "_")          # e.g., "visitor_count"
    specific_file = f"backend/app/routes/admin_{resource_us}.py"
    default_prefix = f"/admin/{resource}"

    # Find the MOST-SPECIFIC matching file: longest prefix that route starts with.
    best_file = specific_file
    best_deco  = route[len(default_prefix):]
    best_prefix = default_prefix

    for fname, file_content in generated_files.items():
        if not fname.startswith("backend/app/routes/admin"):
            continue
        if not file_content:
            continue
        pm = re.search(r'APIRouter\([^)]*prefix=["\']([^"\']+)["\']', file_content)
        if not pm:
            continue
        prefix = pm.group(1).rstrip("/")
        if route.startswith(prefix) and len(prefix) > len(best_prefix):
            best_file   = fname
            best_deco   = route[len(prefix):]
            best_prefix = prefix

    return best_file, best_deco or "", best_prefix


def _create_admin_router_stub(
    file_path: str, router_prefix: str, admin_dep: str, model_name: str
) -> str:
    """Return content for a new admin router file with standard imports."""
    tag = router_prefix.strip("/").replace("/", "-")
    return (
        f'"""Auto-generated admin routes for {tag}."""\n'
        f"from fastapi import APIRouter, Depends, HTTPException\n"
        f"from sqlalchemy.orm import Session\n"
        f"from app.database import get_db\n"
        f"from app.auth import {admin_dep}\n"
        f"from app.models import {model_name}\n\n"
        f'router = APIRouter(prefix="{router_prefix}", tags=["admin"])\n'
    )


def _wire_admin_router_into_main(file_path: str, generated_files: dict) -> dict:
    """Inject import + include_router for file_path into backend/app/main.py."""
    main_path = "backend/app/main.py"
    main_content = generated_files.get(main_path, "")
    if not main_content:
        return {}

    # backend/app/routes/admin_visitor_count.py → app.routes.admin_visitor_count
    module = file_path.replace("backend/", "").replace("/", ".").replace(".py", "")
    stem = file_path.rsplit("/", 1)[-1].replace(".py", "")  # admin_visitor_count
    router_var = stem + "_router"                            # admin_visitor_count_router

    if router_var in main_content:
        return {}

    import_line = f"from {module} import router as {router_var}\n"
    include_line = f'app.include_router({router_var}, prefix="/api")\n'
    new_main = main_content

    route_imps = list(re.finditer(r"^from app\.routes\.\w+ import[^\n]*\n", new_main, re.MULTILINE))
    if route_imps:
        pos = route_imps[-1].end()
        new_main = new_main[:pos] + import_line + new_main[pos:]
    else:
        last_imp = None
        for m in re.finditer(r"^(?:from|import) [^\n]+\n", new_main, re.MULTILINE):
            last_imp = m
        pos = last_imp.end() if last_imp else 0
        new_main = new_main[:pos] + import_line + new_main[pos:]

    includes = list(re.finditer(r"^app\.include_router\([^\n]*\n", new_main, re.MULTILINE))
    if includes:
        pos = includes[-1].end()
        new_main = new_main[:pos] + include_line + new_main[pos:]
    else:
        new_main = new_main.rstrip() + "\n" + include_line

    return {main_path: new_main}


def _admin_path_to_model_name(router_path: str) -> str:
    """Derive PascalCase model name from the last non-param segment of a router path.

    Examples:
        /contact-messages/{id}  → ContactMessage
        /profile/resume         → Resume
        /items/{item_id}        → Item
    """
    parts = [p for p in router_path.split("/") if p and not p.startswith("{")]
    seg = parts[-1] if parts else "item"
    words = re.split(r"[-_]", seg)
    pascal = "".join(w.capitalize() for w in words if w)
    if pascal.endswith("ies") and len(pascal) > 4:
        return pascal[:-3] + "y"
    if pascal.endswith("sses") or pascal.endswith("xes"):
        return pascal[:-2]
    if pascal.endswith("s") and not pascal.endswith("ss") and len(pascal) > 3:
        return pascal[:-1]
    return pascal


_SYMBOL_TO_IMPORT: dict[str, str] = {
    "UploadFile":        "from fastapi import UploadFile, File",
    "File":              "from fastapi import UploadFile, File",
    "Form":              "from fastapi import Form",
    "BackgroundTasks":   "from fastapi import BackgroundTasks",
    "Response":          "from fastapi import Response",
    "Request":           "from fastapi import Request",
    "Body":              "from fastapi import Body",
    "Path":              "from fastapi import Path",
    "Query":             "from fastapi import Query",
    "StreamingResponse": "from fastapi.responses import StreamingResponse",
    "FileResponse":      "from fastapi.responses import FileResponse",
    "JSONResponse":      "from fastapi.responses import JSONResponse",
}


def _inject_missing_fastapi_imports(content: str) -> str:
    """Scan content for FastAPI symbols and inject missing import lines."""
    needed: list[str] = []
    for symbol, import_line in _SYMBOL_TO_IMPORT.items():
        if re.search(rf'\b{re.escape(symbol)}\b', content) and import_line not in content:
            if import_line not in needed:
                needed.append(import_line)
    if not needed:
        return content
    lines = content.split("\n")
    last_fastapi_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("from fastapi"):
            last_fastapi_idx = i
    insert_at = last_fastapi_idx + 1 if last_fastapi_idx >= 0 else 0
    for i, import_line in enumerate(needed):
        lines.insert(insert_at + i, import_line)
    return "\n".join(lines)


def _fix_duplicate_users_table(
    test_results: dict, generated_files: dict
) -> dict:
    """Remove any rogue `class User(Base)` block from models.py.

    Root cause: a debugger helper (e.g., _fix_missing_admin_endpoint) occasionally
    injects a `class User(Base)` stub into models.py when it can't resolve the
    model name, but `User` is already defined in auth_models.py (scaffold).  Both
    classes register `__tablename__ = "users"` with the same Base.metadata, which
    SQLAlchemy rejects at import time with:

        sqlalchemy.exc.InvalidRequestError:
            Table 'users' is already defined for this MetaData instance.

    Fix (idempotent):
      1. Strip any `class User(Base): ...` block from models.py.
      2. Ensure the canonical re-export line is present.
    """
    _log = structlog.get_logger("debugger")

    err_blob = " ".join(str(e) for e in (test_results.get("errors") or []))
    logs = test_results.get("logs") or {}
    log_blob = " ".join(logs.get(k, "") for k in ("boot", "typecheck", "install"))
    haystack = (err_blob + " " + log_blob).lower()

    triggered = (
        "table 'users' is already defined" in haystack
        or "table users is already defined" in haystack
        or "is already defined for this metadata" in haystack
    )
    if not triggered:
        return {}

    models_path = "backend/app/models.py"
    content = generated_files.get(models_path, "")
    if not content:
        return {}

    # Strip any `class User(...)` block — match from the class line through
    # the next top-level (non-indented) statement or end of file.
    lines = content.split("\n")
    new_lines: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.lstrip()
        if not skipping and re.match(r"class\s+User\s*\(.*\)\s*:", stripped):
            skipping = True
            _log.info(
                "fix_duplicate_users_table.removed_class",
                path=models_path,
                line_preview=line[:80],
            )
            continue
        if skipping:
            # A non-empty, non-indented line ends the class body.
            if line and not line[0].isspace():
                skipping = False
                new_lines.append(line)
            continue
        new_lines.append(line)

    new_content = "\n".join(new_lines)

    # Ensure the canonical re-export is present (idempotent).
    reexport = "from app.auth_models import User  # noqa: F401"
    if reexport not in new_content:
        new_content = reexport + "\n" + new_content

    if new_content == content:
        return {}

    _log.info("fix_duplicate_users_table.applied", path=models_path)
    return {models_path: new_content}


def _fix_missing_admin_endpoint(test_results: dict, generated_files: dict) -> dict:
    """Inject stub route handlers for /api/admin/* endpoints absent from the backend.

    Improvements:
      1. Verifies the referenced model exists in models.py; auto-creates a minimal
         Mapped[] stub if not; uses the closest existing name (Levenshtein ≤ 3) to
         avoid hallucinating a model that will never import correctly.
      2. Routes to the CORRECT file based on router-prefix matching, never injecting
         into a router whose prefix would produce the wrong URL.
      3. Creates new resource-specific router files when needed and wires them
         into main.py.

    Idempotent — skips routes whose method+path already exists in the target file.
    """
    contract_log = (test_results.get("logs", {}) or {}).get("contract", "") or ""
    advisory_log = (test_results.get("logs", {}) or {}).get("contract_advisory", "") or ""
    combined = contract_log + "\n" + advisory_log

    misses = list(_ADMIN_MISS_RE.finditer(combined))
    if not misses:
        return {}

    _log = structlog.get_logger("debugger")
    fixes: dict[str, str] = {}
    models_path = "backend/app/models.py"

    for miss in misses:
        method = miss.group("method").upper()
        full_path = miss.group("path").split()[0]

        # ── Part 2: select the correct target file ────────────────────────
        merged = {**generated_files, **fixes}
        target_path, decorator_path, router_prefix = _target_admin_route_file(full_path, merged)

        # ── Part 1: verify / auto-create model ───────────────────────────
        # Derive model name from the FULL admin path (decorator_path may be empty).
        full_admin_route = re.sub(r"^/api", "", full_path)
        model = _admin_path_to_model_name(full_admin_route)

        models_content = fixes.get(models_path, generated_files.get(models_path, ""))
        if models_content:
            resolved = _resolve_model_name(model, models_content, _log)
            if resolved is not None:
                if resolved != model:
                    _log.info(
                        "fix_missing_admin_endpoint.using_resolved_model",
                        requested=model, using=resolved,
                    )
                model = resolved
            else:
                # Models owned by the scaffold (auth_models.py) must never be
                # re-stubbed here. A second `class User(Base)` binding the same
                # __tablename__ = "users" crashes SQLAlchemy at import time with
                # "Table 'users' is already defined for this MetaData".
                _SCAFFOLD_MODEL_NAMES = frozenset({"User"})
                if model in _SCAFFOLD_MODEL_NAMES:
                    _log.info(
                        "fix_missing_admin_endpoint.skipped_scaffold_model",
                        model=model,
                        reason="scaffold owns this model — ensuring re-export instead of stubbing",
                    )
                    reexport = "from app.auth_models import User  # noqa: F401\n"
                    if reexport.strip() not in models_content:
                        fixes[models_path] = reexport + models_content
                        _log.info(
                            "fix_missing_admin_endpoint.re_exported_scaffold_user",
                            path=models_path,
                        )
                else:
                    updated_models = _ensure_model_file_imports(models_content)
                    updated_models = updated_models.rstrip() + _build_model_stub(model)
                    # Validate the stub: parse the file and confirm the class is
                    # at module level. A bad injection (indented under another
                    # class or inside an if-block) would produce a name that
                    # mypy can't find on the module.
                    try:
                        tree = ast.parse(updated_models)
                        # ast.walk doesn't give parent, so check directly on module body.
                        module_top_classes = {
                            node.name
                            for node in tree.body
                            if isinstance(node, ast.ClassDef)
                        }
                        if model not in module_top_classes:
                            _log.error(
                                "fix_missing_admin_endpoint.stub_injection_failed",
                                model=model,
                                reason="class not at module level after injection",
                                top_level_classes=sorted(module_top_classes),
                            )
                        else:
                            fixes[models_path] = updated_models
                            _log.info(
                                "fix_missing_admin_endpoint.created_model_stub",
                                model=model, path=models_path,
                            )
                    except SyntaxError as se:
                        _log.error(
                            "fix_missing_admin_endpoint.stub_syntax_error",
                            model=model, error=str(se),
                        )

        # ── Get / create target route file ────────────────────────────────
        existing_content = fixes.get(target_path, generated_files.get(target_path, ""))
        if not existing_content:
            # Create new router file with standard scaffolding.
            src = fixes.get("backend/app/routes/admin.py",
                            generated_files.get("backend/app/routes/admin.py", ""))
            admin_dep = "require_admin"
            for dep in _ADMIN_AUTH_DEP_NAMES:
                if re.search(rf'\bDepends\s*\(\s*{re.escape(dep)}\s*\)', src):
                    admin_dep = dep
                    break
            existing_content = _create_admin_router_stub(
                target_path, router_prefix, admin_dep, model
            )
            main_fixes = _wire_admin_router_into_main(
                target_path, {**generated_files, **fixes}
            )
            fixes.update(main_fixes)
            _log.info(
                "fix_missing_admin_endpoint.created_router_file",
                file=target_path, prefix=router_prefix,
            )
        else:
            admin_dep = "require_admin"
            for dep in _ADMIN_AUTH_DEP_NAMES:
                if re.search(rf'\bDepends\s*\(\s*{re.escape(dep)}\s*\)', existing_content):
                    admin_dep = dep
                    break

        # ── Normalise {id} in decorator_path ─────────────────────────────
        segs = [p for p in decorator_path.split("/") if p and not p.startswith("{")]
        if segs:
            resource_seg = segs[-1]
        else:
            # Fall back to the resource segment from the full path.
            _all = [p for p in full_admin_route.split("/") if p and not p.startswith("{")]
            resource_seg = _all[-1] if _all else "item"
        param_base = re.sub(r"[^a-z0-9]", "_", resource_seg.lower().rstrip("s"))
        norm_deco = re.sub(r"\{id\}", f"{{{param_base}_id}}", decorator_path)

        # ── Idempotency ───────────────────────────────────────────────────
        existing_re = re.compile(
            rf'@\w+\.{re.escape(method.lower())}\s*\(\s*["\']'
            + re.escape(norm_deco) + r'["\']',
            re.IGNORECASE,
        )
        if existing_re.search(existing_content):
            continue

        # ── Stub generation ───────────────────────────────────────────────
        path_params = re.findall(r"\{(\w+)\}", norm_deco)
        is_upload = any(kw in full_path.lower() for kw in (
            "resume", "upload", "file", "image", "avatar", "photo", "attachment",
        ))
        is_update = method in ("PUT", "PATCH") and bool(path_params)
        is_delete = method == "DELETE" and bool(path_params)

        _verb_map = {"GET": "get", "POST": "create", "PUT": "update",
                     "PATCH": "update", "DELETE": "delete"}
        verb = "upload" if is_upload else _verb_map.get(method, method.lower())
        name_parts = [
            re.sub(r"[^a-z0-9]", "_", p.lower())
            for p in norm_deco.split("/")
            if p and not p.startswith("{")
        ]
        if not name_parts:
            name_parts = [re.sub(r"[^a-z0-9]", "_", resource_seg.lower())]
        func_name = f"{verb}_{'_'.join(name_parts)}"
        deco_q = norm_deco  # may be "" for resource-root endpoints

        if is_upload:
            upload_dir = f"uploads/{resource_seg.lower().rstrip('s')}"
            url_key = f"{resource_seg.lower().rstrip('s')}_url"
            stub = (
                f'\n\n@router.{method.lower()}("{deco_q}")\n'
                f"async def {func_name}(\n"
                f"    file: UploadFile,\n"
                f"    db: Session = Depends(get_db),\n"
                f"    _: object = Depends({admin_dep}),\n"
                f"):\n"
                f"    import os\n"
                f'    os.makedirs("{upload_dir}", exist_ok=True)\n'
                f'    dest = f"{upload_dir}/{{file.filename}}"\n'
                f'    with open(dest, "wb") as _fh:\n'
                f"        _fh.write(await file.read())\n"
                f'    return {{"{url_key}": f"/{upload_dir}/{{file.filename}}"}}\n'
            )
        elif is_update:
            param_lines = "\n".join(f"    {p}: int," for p in path_params)
            pk = path_params[0]
            stub = (
                f'\n\n@router.{method.lower()}("{deco_q}")\n'
                f"def {func_name}(\n"
                f"{param_lines}\n"
                f"    payload: dict,\n"
                f"    db: Session = Depends(get_db),\n"
                f"    _: object = Depends({admin_dep}),\n"
                f"):\n"
                f"    obj = db.query({model}).filter({model}.id == {pk}).first()\n"
                f"    if not obj:\n"
                f'        raise HTTPException(status_code=404, detail="Not found")\n'
                f"    for k, v in payload.items():\n"
                f"        if hasattr(obj, k):\n"
                f"            setattr(obj, k, v)\n"
                f"    db.commit()\n"
                f"    db.refresh(obj)\n"
                f"    return obj\n"
            )
        elif is_delete:
            param_lines = "\n".join(f"    {p}: int," for p in path_params)
            pk = path_params[0]
            stub = (
                f'\n\n@router.{method.lower()}("{deco_q}")\n'
                f"def {func_name}(\n"
                f"{param_lines}\n"
                f"    db: Session = Depends(get_db),\n"
                f"    _: object = Depends({admin_dep}),\n"
                f"):\n"
                f"    obj = db.query({model}).filter({model}.id == {pk}).first()\n"
                f"    if not obj:\n"
                f'        raise HTTPException(status_code=404, detail="Not found")\n'
                f"    db.delete(obj)\n"
                f"    db.commit()\n"
                f'    return {{"ok": True}}\n'
            )
        elif method == "POST":
            stub = (
                f'\n\n@router.post("{deco_q}")\n'
                f"def {func_name}(\n"
                f"    payload: dict,\n"
                f"    db: Session = Depends(get_db),\n"
                f"    _: object = Depends({admin_dep}),\n"
                f"):\n"
                f"    obj = {model}(**payload)\n"
                f"    db.add(obj)\n"
                f"    db.commit()\n"
                f"    db.refresh(obj)\n"
                f"    return obj\n"
            )
        else:
            param_lines = "\n".join(f"    {p}: int," for p in path_params)
            if path_params:
                pk = path_params[0]
                body = (
                    f"    obj = db.query({model}).filter({model}.id == {pk}).first()\n"
                    f"    if not obj:\n"
                    f'        raise HTTPException(status_code=404, detail="Not found")\n'
                    f"    return obj\n"
                )
            else:
                body = f"    return db.query({model}).all()\n"
            stub = (
                f'\n\n@router.{method.lower()}("{deco_q}")\n'
                f"def {func_name}(\n"
                + (f"{param_lines}\n" if param_lines else "")
                + f"    db: Session = Depends(get_db),\n"
                  f"    _: object = Depends({admin_dep}),\n"
                  f"):\n"
                + body
            )

        existing_content = existing_content.rstrip() + stub

        # Ensure imports in the target file.
        if "HTTPException" not in existing_content:
            existing_content = re.sub(
                r"(from fastapi import )([^\n]+)",
                lambda m: m.group(1) + "HTTPException, " + m.group(2),
                existing_content, count=1,
            )
        existing_content = _inject_missing_fastapi_imports(existing_content)
        m_imp = re.search(r"(from app\.models import\s+)([^\n]+)", existing_content)
        if m_imp and model not in m_imp.group(2):
            existing_content = existing_content.replace(
                m_imp.group(0),
                m_imp.group(1) + m_imp.group(2).rstrip() + f", {model}",
                1,
            )

        try:
            ast.parse(existing_content)
        except SyntaxError as exc:
            _log.error(
                "fix_missing_admin_endpoint.syntax_error_skipping",
                file=target_path, error=str(exc),
            )
            continue

        fixes[target_path] = existing_content

    if not fixes:
        return {}

    _log.info(
        "fix_missing_admin_endpoint.applied",
        files=sorted(fixes),
        misses=[m.group("path") for m in misses],
    )
    return fixes


def _fix_method_mismatch_admin(test_results: dict, generated_files: dict) -> dict:
    """Prepend a missing HTTP-method decorator on admin backend route handlers.

    When the frontend calls PUT /api/admin/X but the backend only registers PATCH,
    add @router.put("...") directly before the existing @router.patch("...") so
    FastAPI accepts both verbs on the same function.  Idempotent.
    """
    contract_log = (test_results.get("logs", {}) or {}).get("contract", "") or ""
    if "CONTRACT MISS: METHOD" not in contract_log:
        return {}

    fixes: dict[str, str] = {}

    for miss in _ADMIN_METHOD_MISS_RE.finditer(contract_log):
        bad_method = miss.group("bad_method").upper()
        full_path = miss.group("path").split()[0]
        methods_raw = miss.group("methods")

        available = re.findall(r"['\"](\w+)['\"]", methods_raw)
        if not available:
            available = re.findall(r"\b(GET|POST|PUT|PATCH|DELETE)\b",
                                   methods_raw, re.IGNORECASE)
        if not available:
            continue
        good_method = available[0].upper()

        router_path = re.sub(r"^/api/admin", "", full_path, flags=re.IGNORECASE).rstrip("/")
        if not router_path:
            continue

        # Normalise {id}.
        segs = [p for p in router_path.split("/") if p and not p.startswith("{")]
        if segs:
            resource_seg = segs[-1]
            param_base = re.sub(r"[^a-z0-9]", "_", resource_seg.lower().rstrip("s"))
            router_path = re.sub(r"\{id\}", f"{{{param_base}_id}}", router_path)

        for candidate in ["backend/app/routes/admin.py"]:
            content = fixes.get(candidate) or generated_files.get(candidate, "")
            if not content:
                continue

            # Idempotency: requested method decorator already present.
            bad_deco_re = re.compile(
                rf'@\w+\.{re.escape(bad_method.lower())}\s*\(\s*["\']'
                + re.escape(router_path) + r'["\']',
                re.IGNORECASE,
            )
            if bad_deco_re.search(content):
                break

            # Find the existing good_method decorator.
            good_deco_re = re.compile(
                rf'(@\w+\.{re.escape(good_method.lower())}\s*\(\s*["\']'
                + re.escape(router_path) + r'["\'][^)]*\))',
                re.IGNORECASE,
            )
            m = good_deco_re.search(content)
            if not m:
                # Try with generic {id} variant in case normalisation differs.
                alt_path = re.sub(r"\{[^}]+_id\}", "{id}", router_path)
                alt_re = re.compile(
                    rf'(@\w+\.{re.escape(good_method.lower())}\s*\(\s*["\']'
                    + re.escape(alt_path) + r'["\'][^)]*\))',
                    re.IGNORECASE,
                )
                m = alt_re.search(content)
                if not m:
                    break

            # Prepend the new decorator immediately before the existing one.
            new_deco = f'@router.{bad_method.lower()}("{router_path}")\n'
            fixes[candidate] = content[:m.start()] + new_deco + content[m.start():]
            structlog.get_logger("debugger").info(
                "fix_method_mismatch_admin.applied",
                path=router_path, added=bad_method, existing=good_method,
            )
            break

    return fixes


# Patterns that produce invisible text (same-tone foreground and background).
_INVISIBLE_TEXT_COMBOS = [
    # (bg_pattern, bad_text_class, replacement_text_class)
    (re.compile(r'\bbg-white\b'),      re.compile(r'\btext-white\b'),        'text-text-default'),
    (re.compile(r'\bbg-gray-50\b'),    re.compile(r'\btext-gray-50\b'),      'text-text-default'),
    (re.compile(r'\bbg-slate-50\b'),   re.compile(r'\btext-slate-50\b'),     'text-text-default'),
    (re.compile(r'\bbg-slate-100\b'),  re.compile(r'\btext-slate-100\b'),    'text-text-default'),
    (re.compile(r'\bbg-surface-page\b'), re.compile(r'\btext-white\b'),      'text-text-default'),
    (re.compile(r'\bbg-surface-panel\b'), re.compile(r'\btext-white\b'),     'text-text-default'),
]

_CLASS_ATTR_RE = re.compile(r'className="([^"]*)"')


def _fix_invisible_text(test_results: dict, generated_files: dict) -> dict:
    """Scan .jsx/.tsx files for same-tone fg/bg combos that produce invisible text.
    Replaces the offending text class with text-text-default."""
    fixes: dict = {}
    for path, content in generated_files.items():
        if not path.startswith("frontend/src/") or not content:
            continue
        if not (path.endswith(".jsx") or path.endswith(".tsx")):
            continue
        new = content
        changed = False
        for m in _CLASS_ATTR_RE.finditer(content):
            classes = m.group(1)
            original_classes = classes
            for bg_re, text_re, replacement in _INVISIBLE_TEXT_COMBOS:
                if bg_re.search(classes) and text_re.search(classes):
                    classes = text_re.sub(replacement, classes)
            if classes != original_classes:
                changed = True
                new = new.replace(m.group(0), f'className="{classes}"', 1)
        if changed:
            fixes[path] = new
    return fixes


# ── React namespace-import fixer ─────────────────────────────────────────────

_BROKEN_REACT_IMPORT_RE = re.compile(
    r'^\s*import\s*\*\s*from\s*["\']react["\']\s*;?\s*$',
    re.MULTILINE,
)


# Matches a spread expression whose ternary is missing an else-branch:
#   ...(someVar ? SOME_ARRAY)   or   ...(someVar ? someArray),
# capturing the truncated ternary as group 1.
_INCOMPLETE_TERNARY_RE = re.compile(
    r'\.\.\.\((\w[\w\s]*\?\s*\w[\w.]*)\s*\)',
)


def _fix_incomplete_ternary_in_spread(test_results: dict, generated_files: dict) -> dict:
    """Fix spread expressions that contain a ternary with no else-branch.

    Pattern:  ...(cond ? VALUE)   →   ...(cond ? VALUE : [])
    This is a JavaScript syntax error and causes esbuild to fail.  The
    most common source is the generator partially modifying a Navbar's
    nav-link composition array.

    Idempotent: ternaries that already have ` : ...` are not matched.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict = {}
    for path, content in generated_files.items():
        if not path.endswith((".jsx", ".tsx", ".js", ".ts")):
            continue
        if "..." not in content or "?" not in content:
            continue

        new_content, count = _INCOMPLETE_TERNARY_RE.subn(
            r'...(\1 : [])',
            content,
        )
        if count and new_content != content:
            fixes[path] = new_content
            _log.info(
                "fix_incomplete_ternary_in_spread.applied",
                path=path, count=count,
            )
    return fixes


def _fix_react_namespace_import(test_results: dict, generated_files: dict) -> dict:
    """Replace every malformed `import * from "react"` with the valid
    `import * as React from "react"` across all .jsx/.tsx files in one pass."""
    fixes: dict = {}
    for path, content in generated_files.items():
        if not (path.endswith(".jsx") or path.endswith(".tsx")):
            continue
        if not content or not _BROKEN_REACT_IMPORT_RE.search(content):
            continue
        new = _BROKEN_REACT_IMPORT_RE.sub('import * as React from "react"', content)
        if new != content:
            fixes[path] = new
    return fixes


# ── forwardRef wrapping ───────────────────────────────────────────────────────

# Layout/design primitives that render children but never need a DOM ref.
# Wrapping them in forwardRef produces malformed code (double-close braces).
_FORWARD_REF_EXCLUDED_FILES: frozenset = frozenset({
    "frontend/src/components/ui/section.jsx",
    "frontend/src/components/ui/container.jsx",
    "frontend/src/components/ui/hero.jsx",
    "frontend/src/components/ui/feature-card.jsx",
    "frontend/src/components/ui/empty-state.jsx",
})

_FORWARD_REF_EXCLUDED_EXPORTS: frozenset = frozenset({
    # Page-level / layout symbols — no interactive DOM element needing a ref.
    "Section", "SectionHeader", "Container", "Hero",
    "FeatureCard", "FeatureCardImage", "FeatureCardBody",
    "EmptyState", "BareLayout", "Layout",
})


def _brackets_balance(s: str) -> bool:
    """Return True if every opening bracket has a matching close.

    Skips characters inside string literals so that brace-heavy
    template/JSX strings don't produce false negatives.
    """
    depth = {"(": 0, "[": 0, "{": 0}
    pairs = {")": "(", "]": "[", "}": "{"}
    in_string = False
    string_char: str | None = None
    i = 0
    while i < len(s):
        c = s[i]
        if in_string:
            if c == "\\":
                i += 2  # skip escaped character
                continue
            if c == string_char:
                in_string = False
        elif c in ("'", '"', "`"):
            in_string = True
            string_char = c
        elif c in depth:
            depth[c] += 1
        elif c in pairs:
            depth[pairs[c]] -= 1
            if depth[pairs[c]] < 0:
                return False
        i += 1
    return all(v == 0 for v in depth.values())


# Matches bare function-component declarations that are NOT already forwardRef-wrapped.
# Group 1 = component name (must start with uppercase).
_BARE_FN_DECL_RE = re.compile(
    r'^export\s+function\s+([A-Z]\w*)\s*\(|'       # export function Name(
    r'^const\s+([A-Z]\w*)\s*=\s*'                  # const Name =
    r'(?:React\.memo\s*\()?'                        # optional React.memo(
    r'\(\s*\{[^}]*\}|\(\s*props\s*\)'              # destructured or (props)
    r'\s*(?:=>|\))',                                # arrow or closing paren
    re.MULTILINE,
)
# Detects root JSX opening tag to inject ref= on.
_ROOT_JSX_TAG_RE = re.compile(r'return\s*\(\s*<([a-z][A-Za-z0-9.]*|[A-Z][A-Za-z0-9.]*)')
_ROOT_JSX_SELF_RE = re.compile(r'return\s*<([a-z][A-Za-z0-9.]*|[A-Z][A-Za-z0-9.]*)')


def _fix_missing_forward_ref(test_results: dict, generated_files: dict) -> dict:
    """Wrap bare function components in frontend/src/components/ui/ with React.forwardRef.

    Only transforms uppercase-named components whose bodies return JSX.
    Skips files that already contain React.forwardRef anywhere near an export.
    Skips layout/design-primitive files that don't need a DOM ref.
    Idempotent: re-running on an already-fixed file is a no-op.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict = {}
    for path, content in generated_files.items():
        if not path.startswith("frontend/src/components/ui/"):
            continue
        if not (path.endswith(".jsx") or path.endswith(".tsx")):
            continue
        if not content:
            continue

        # Skip design-primitive files — wrapping them produces malformed code.
        if path in _FORWARD_REF_EXCLUDED_FILES:
            continue

        # If forwardRef already appears in the file, treat it as done.
        if "React.forwardRef" in content or "forwardRef(" in content:
            continue

        new_content = content
        wrapped_names: list[str] = []

        # Detect any existing React import so we don't double-inject.
        # Matches: `import * as React from "react"`, `import React from "react"`,
        # `import * from "react"` (broken — _fix_react_namespace_import handles it),
        # and named `import { useState } from "react"`.
        has_react_star = bool(re.search(
            r'import\s+(?:\*\s+(?:as\s+React\s+)?|\bReact\b)\s*(?:,\s*\{[^}]*\})?\s*from\s*["\']react["\']',
            new_content,
        ))
        has_react_named = bool(re.search(
            r'import\s+\{[^}]*\}\s+from\s+["\']react["\']', new_content
        ))

        # Find all top-level function components via simple line-by-line scan.
        lines = new_content.splitlines()
        i = 0
        output_lines = []
        while i < len(lines):
            line = lines[i]

            # Pattern A: `export function Name(` at module top-level
            ma = re.match(r'^(export\s+)?function\s+([A-Z]\w*)\s*\(([^)]*)\)\s*\{?', line)
            if ma and ma.group(2):
                name = ma.group(2)
                if name in _FORWARD_REF_EXCLUDED_EXPORTS:
                    output_lines.append(line)
                    i += 1
                    continue
                params = ma.group(3).strip()
                # Collect the full function body (brace-balanced).
                body_lines = []
                depth = line.count('{') - line.count('}')
                if depth > 0 or '{' in line:
                    j = i + 1
                    while j < len(lines) and depth > 0:
                        body_lines.append(lines[j])
                        depth += lines[j].count('{') - lines[j].count('}')
                        j += 1
                else:
                    j = i + 1

                full_body = '\n'.join(body_lines).strip()
                # Only wrap if body returns JSX.
                if re.search(r'return\s*[\(\n]\s*<', full_body) or re.search(r'return\s*<', full_body):
                    # Build forwardRef wrapper.
                    # Add ref to root JSX element if not already there.
                    wrapped_body = _inject_ref_into_body(full_body)
                    # Params: keep original, add ref
                    param_decl = params if params else "props"
                    output_lines.append(f"const {name} = React.forwardRef(({param_decl}, ref) => {{")
                    for bl in wrapped_body.splitlines():
                        output_lines.append(bl)
                    output_lines.append("})")
                    output_lines.append(f'{name}.displayName = "{name}"')
                    output_lines.append("")
                    wrapped_names.append(name)
                    i = j
                    continue

            # Pattern B: `const Name = ({...}) =>` or `const Name = (props) =>`
            mb = re.match(
                r'^(export\s+)?const\s+([A-Z]\w*)\s*=\s*'
                r'(?:React\.memo\s*\()?\s*'
                r'(\([^)]*\))\s*=>',
                line
            )
            if mb and mb.group(2):
                name = mb.group(2)
                if name in _FORWARD_REF_EXCLUDED_EXPORTS:
                    output_lines.append(line)
                    i += 1
                    continue
                params = mb.group(3).strip().lstrip('(').rstrip(')')
                # Collect the arrow function body.
                rest_of_line = line[mb.end():].strip()
                if rest_of_line.startswith('('):
                    # Multi-line arrow: `({...}) => (\n  <JSX>\n)`
                    body_lines = [rest_of_line]
                    depth = rest_of_line.count('(') - rest_of_line.count(')')
                    j = i + 1
                    while j < len(lines) and depth > 0:
                        body_lines.append(lines[j])
                        depth += lines[j].count('(') - lines[j].count(')')
                        j += 1
                elif rest_of_line.startswith('{'):
                    # Block body arrow
                    body_lines = [rest_of_line]
                    depth = rest_of_line.count('{') - rest_of_line.count('}')
                    j = i + 1
                    while j < len(lines) and depth > 0:
                        body_lines.append(lines[j])
                        depth += lines[j].count('{') - lines[j].count('}')
                        j += 1
                else:
                    body_lines = [rest_of_line]
                    j = i + 1

                full_body = '\n'.join(body_lines).strip()
                if re.search(r'<[A-Za-z]', full_body):
                    # It returns JSX — wrap it.
                    wrapped_body = _inject_ref_into_jsx_arrow(full_body)
                    output_lines.append(f"const {name} = React.forwardRef(({params}, ref) => (")
                    for bl in wrapped_body.strip('()').splitlines():
                        output_lines.append(bl)
                    output_lines.append("))")
                    output_lines.append(f'{name}.displayName = "{name}"')
                    output_lines.append("")
                    wrapped_names.append(name)
                    i = j
                    continue

            output_lines.append(line)
            i += 1

        if not wrapped_names:
            continue

        new_content = '\n'.join(output_lines)

        # Inject React star import if missing.
        if not has_react_star:
            if has_react_named:
                # Replace named import with star import.
                new_content = re.sub(
                    r'import\s+\{[^}]*\}\s+from\s+["\']react["\']',
                    'import * as React from "react"',
                    new_content,
                    count=1,
                )
            else:
                new_content = 'import * as React from "react"\n' + new_content

        # Safety guard: if the wrapping produced unbalanced brackets, abort.
        if not _brackets_balance(new_content):
            _log.error(
                "fix_missing_forward_ref.unbalanced_brackets_skipping",
                file=path, wrapped=wrapped_names,
            )
            continue

        fixes[path] = new_content
    return fixes


def _inject_ref_into_body(body: str) -> str:
    """Add ref={ref} to the root JSX tag inside a function body string."""
    def _add_ref(m):
        tag = m.group(0)
        if 'ref=' in tag:
            return tag
        # Insert ref={ref} just after the tag name.
        return re.sub(r'(<\w[\w.]*)', r'\1 ref={ref}', tag, count=1)

    return re.sub(r'<[a-z]\w*|<[A-Z]\w*(?:\.[A-Z]\w*)*', _add_ref, body, count=1)


def _inject_ref_into_jsx_arrow(body: str) -> str:
    """Add ref={ref} to the root JSX element in an arrow-function expression body."""
    def _add_ref(m):
        tag = m.group(0)
        if 'ref=' in tag:
            return tag
        return re.sub(r'(<\w[\w.]*)', r'\1 ref={ref}', tag, count=1)

    return re.sub(r'<[a-z]\w*|<[A-Z]\w*(?:\.[A-Z]\w*)*', _add_ref, body, count=1)


# ── Scaffold hook constants (for stub-generator awareness) ───────────────────

_SCAFFOLDED_HOOK_NAMES: frozenset[str] = frozenset({
    "useIntersectionObserver",
    "useMediaQuery",
    "useDebounce",
    "useLocalStorage",
})

# Import paths that map directly to scaffold hook files in the templates dir.
_SCAFFOLD_HOOK_IMPORTS: frozenset[str] = frozenset({
    "@/hooks/useIntersectionObserver",
    "@/hooks/useMediaQuery",
    "@/hooks/useDebounce",
    "@/hooks/useLocalStorage",
    "@/hooks/index",
    "@/hooks",
})

# Path to the scaffold hooks directory (relative to this file in agents/).
_HOOKS_TEMPLATE_DIR: str = os.path.normpath(os.path.join(
    os.path.dirname(__file__),
    "..", "stack_templates", "python-postgres", "frontend", "src", "hooks",
))

# Pattern to detect require() calls in frontend files.
_REQUIRE_LINE_RE = re.compile(
    r"^\s*const\s+(\{[^}]+\}|\w+)\s*=\s*require\s*\(\s*[\"']([^\"']+)[\"']\s*\)\s*;?\s*$"
)


def _fix_hook_inline_definitions(test_results: dict, generated_files: dict) -> dict:
    """Fix two classes of LLM mistakes in frontend source files.

    A) Inline definition of a scaffolded hook (useIntersectionObserver,
       useMediaQuery, useDebounce, useLocalStorage) inside a component file:
       removes the function/const block and adds an import from @/hooks/<Name>.

    B) require() call in a Vite ESM file — converts to an ES module import
       statement and moves it to the top of the import block.

    Idempotent: files that already import from @/hooks/ are left untouched.
    """
    fixes: dict = {}

    for path, content in generated_files.items():
        if not path.startswith("frontend/src/") or not content:
            continue
        if not (path.endswith(".jsx") or path.endswith(".tsx") or
                path.endswith(".js") or path.endswith(".ts")):
            continue

        # Don't touch the hook files themselves — their definitions are intentional.
        stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if stem in _SCAFFOLDED_HOOK_NAMES:
            continue

        lines = content.split("\n")
        out_lines: list[str] = []
        new_imports: list[str] = []
        changed = False
        i = 0

        while i < len(lines):
            ln = lines[i]
            stripped = ln.lstrip()

            # ── A: Inline scaffold-hook definition ──────────────────────────
            hook_match: str | None = None
            for hook_name in _SCAFFOLDED_HOOK_NAMES:
                if hook_name not in ln:
                    continue
                # Skip if already importing from the scaffold
                if (f'from "@/hooks/{hook_name}"' in content or
                        f"from '@/hooks/{hook_name}'" in content):
                    continue
                pat = re.compile(
                    rf"^(?:export\s+)?(?:function\s+{re.escape(hook_name)}\b"
                    rf"|const\s+{re.escape(hook_name)}\s*=\s*(?:function\s*)?\()",
                )
                if pat.match(stripped):
                    hook_match = hook_name
                    break

            if hook_match:
                # Consume the function body using brace-balance tracking.
                depth = ln.count("{") - ln.count("}")
                j = i + 1
                while j < len(lines) and depth > 0:
                    depth += lines[j].count("{") - lines[j].count("}")
                    j += 1
                i = j
                stmt = f'import {{ {hook_match} }} from "@/hooks/{hook_match}"'
                if stmt not in new_imports:
                    new_imports.append(stmt)
                changed = True
                continue

            # ── B: require() → ESM import ────────────────────────────────────
            req_m = _REQUIRE_LINE_RE.match(ln)
            if req_m:
                binding = req_m.group(1).strip()
                mod = req_m.group(2)
                stmt = f'import {binding} from "{mod}"'
                new_imports.append(stmt)
                changed = True
                i += 1
                continue

            out_lines.append(ln)
            i += 1

        if not changed:
            continue

        # Insert new imports after the last existing `import ...` line.
        last_import_idx = -1
        for idx, ln in enumerate(out_lines):
            if ln.strip().startswith("import "):
                last_import_idx = idx

        insert_at = last_import_idx + 1 if last_import_idx >= 0 else 0
        joined = "\n".join(out_lines)
        for stmt in reversed(new_imports):
            if stmt not in joined:  # rough dedup
                out_lines.insert(insert_at, stmt)

        new_content = "\n".join(out_lines)
        if new_content != content:
            fixes[path] = new_content
            structlog.get_logger("debugger").info(
                "fix_hook_inline_definitions.applied",
                path=path,
                hooks_imported=[s for s in new_imports if "@/hooks/" in s],
                require_converted=sum(1 for s in new_imports if "require" not in s and "@/hooks/" not in s),
            )

    return fixes


# Suffixes/prefixes to try when an import resolves to a missing file.
# E.g., "@/pages/Menu" → look for MenuPage.jsx, MenuView.jsx, etc.
_PAGE_VARIANT_SUFFIXES = ("Page", "View", "Screen", "Container")


def _find_page_variant(import_path: str, generated_files: dict) -> str | None:
    """Return an alternative import path if a file with a variant suffix/prefix exists.

    Handles two mismatch directions:
      "@/pages/Menu"      → "frontend/src/pages/MenuPage.jsx" exists → "@/pages/MenuPage"
      "@/pages/MenuPage"  → "frontend/src/pages/Menu.jsx"     exists → "@/pages/Menu"

    Returns the corrected @/... import path, or None if no variant found.
    """
    base_path = _component_path_for(import_path)        # e.g. frontend/src/pages/Menu.jsx
    dir_part = base_path.rsplit("/", 1)[0]              # frontend/src/pages
    stem = base_path.rsplit("/", 1)[-1]                 # Menu.jsx
    stem_no_ext = stem[:-4] if stem.endswith(".jsx") else stem  # Menu

    candidates: list[str] = []

    # Direction 1: stem has no suffix → try adding Page/View/Screen/Container
    stripped = stem_no_ext
    for suf in _PAGE_VARIANT_SUFFIXES:
        if not stem_no_ext.endswith(suf):
            candidates.append(f"{dir_part}/{stem_no_ext}{suf}.jsx")

    # Direction 2: stem already ends with a suffix → try stripping it
    for suf in _PAGE_VARIANT_SUFFIXES:
        if stem_no_ext.endswith(suf) and len(stem_no_ext) > len(suf):
            stripped_stem = stem_no_ext[: -len(suf)]
            candidates.append(f"{dir_part}/{stripped_stem}.jsx")

    # Direction 3: pages/<Name>/index.jsx
    candidates.append(f"{dir_part}/{stem_no_ext}/index.jsx")

    for candidate_path in candidates:
        if candidate_path in generated_files:
            # Convert back to @/ import path
            return "@/" + candidate_path.replace("frontend/src/", "").replace(".jsx", "")
    return None


def _auto_stub_missing_imports(test_results: dict, generated_files: dict) -> dict:
    """Stub all missing @/... imports found by scanning generated files and build logs.

    Before creating a stub, checks whether a file with a variant suffix/prefix
    (e.g., MenuPage.jsx for a missing Menu.jsx) already exists.  When found,
    REWRITES the import in the calling file to point at the real page instead of
    creating a stub that would shadow it.

    Scaffold hook imports (@/hooks/useX) are handled specially: instead of
    generating a generic stub, the actual template file is copied from the
    stack_templates directory.  This guarantees the correct hook signature even
    when the template file is somehow absent from generated_files.
    """
    _log = structlog.get_logger("debugger")
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
        import_path = entry["path"]
        from_file = entry.get("from_file", "")
        target_rel = _component_path_for(import_path)
        if target_rel in generated_files or target_rel in fixed:
            continue

        # ── Variant-name lookup: prefer real page over stub ───────────────
        real_path = _find_page_variant(import_path, {**generated_files, **fixed})
        if real_path is not None:
            # Rewrite the import in every file that uses the wrong path.
            for fp, content in list(generated_files.items()):
                if import_path not in content:
                    continue
                old_import_re = re.compile(
                    rf'(from\s+["\']){re.escape(import_path)}(["\'])',
                )
                new_content, count = old_import_re.subn(
                    rf'\g<1>{real_path}\2', content
                )
                if count:
                    fixed[fp] = new_content
                    _log.info(
                        "fix_stub_overshadowing.repaired",
                        from_path=import_path,
                        to_path=real_path,
                        caller=fp,
                    )
            continue  # Do NOT create a stub

        # Scaffold hooks: copy the real file from the template directory
        # instead of generating a stub with the wrong signature.
        if import_path in _SCAFFOLD_HOOK_IMPORTS:
            hook_name = import_path.split("/")[-1]  # e.g. "useIntersectionObserver"
            js_target = f"frontend/src/hooks/{hook_name}.js"
            if js_target in generated_files or js_target in fixed:
                continue
            template_file = os.path.join(_HOOKS_TEMPLATE_DIR, f"{hook_name}.js")
            try:
                with open(template_file) as fh:
                    fixed[js_target] = fh.read()
                _log.info(
                    "auto_stub.scaffold_hook_copied",
                    import_path=import_path, target=js_target,
                )
            except OSError:
                # Template file unexpectedly missing — fall back to a generic stub
                named, has_default = _named_imports_for(import_path, generated_files)
                fixed[target_rel] = _build_stub(import_path, named, has_default)
            continue

        named, has_default = _named_imports_for(import_path, generated_files)
        fixed[target_rel] = _build_stub(import_path, named, has_default)
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


def _kebab_to_pascal(name: str) -> str:
    """Convert kebab-case or snake_case filename stem to PascalCase JS identifier.

    Examples:
        dropdown-menu → DropdownMenu
        scroll-area   → ScrollArea
        alert-dialog  → AlertDialog
        button        → Button
        scroll_area   → ScrollArea
    """
    for ext in (".jsx", ".tsx", ".js", ".ts"):
        if name.endswith(ext):
            name = name[:-len(ext)]
    parts = re.split(r"[-_]", name)
    return "".join(part[:1].upper() + part[1:] for part in parts if part)


# shadcn/ui multi-component primitives: filename stem → subcomponent names.
# When generating a stub for one of these files, emit passthrough re-exports so
# every subcomponent import the LLM wrote also resolves.
_SHADCN_SUBCOMPONENTS: dict[str, list[str]] = {
    "dropdown-menu": [
        "DropdownMenuTrigger", "DropdownMenuContent", "DropdownMenuItem",
        "DropdownMenuLabel", "DropdownMenuSeparator", "DropdownMenuGroup",
        "DropdownMenuPortal", "DropdownMenuSub", "DropdownMenuSubTrigger",
        "DropdownMenuSubContent", "DropdownMenuRadioGroup",
        "DropdownMenuCheckboxItem", "DropdownMenuRadioItem",
        "DropdownMenuShortcut",
    ],
    "dialog": [
        "DialogTrigger", "DialogContent", "DialogHeader", "DialogFooter",
        "DialogTitle", "DialogDescription", "DialogClose",
        "DialogPortal", "DialogOverlay",
    ],
    "select": [
        "SelectTrigger", "SelectContent", "SelectItem", "SelectValue",
        "SelectGroup", "SelectLabel", "SelectSeparator",
        "SelectScrollUpButton", "SelectScrollDownButton",
    ],
    "sheet": [
        "SheetTrigger", "SheetContent", "SheetHeader", "SheetFooter",
        "SheetTitle", "SheetDescription", "SheetClose",
        "SheetPortal", "SheetOverlay",
    ],
    "tabs": ["TabsList", "TabsTrigger", "TabsContent"],
    "popover": ["PopoverTrigger", "PopoverContent", "PopoverAnchor"],
    "tooltip": ["TooltipProvider", "TooltipTrigger", "TooltipContent"],
    "alert-dialog": [
        "AlertDialogTrigger", "AlertDialogContent", "AlertDialogHeader",
        "AlertDialogFooter", "AlertDialogTitle", "AlertDialogDescription",
        "AlertDialogAction", "AlertDialogCancel",
    ],
    "command": [
        "CommandInput", "CommandList", "CommandEmpty", "CommandGroup",
        "CommandItem", "CommandSeparator", "CommandShortcut",
    ],
    "accordion": ["AccordionItem", "AccordionTrigger", "AccordionContent"],
    "navigation-menu": [
        "NavigationMenuList", "NavigationMenuItem", "NavigationMenuTrigger",
        "NavigationMenuContent", "NavigationMenuLink",
        "NavigationMenuIndicator", "NavigationMenuViewport",
    ],
    "context-menu": [
        "ContextMenuTrigger", "ContextMenuContent", "ContextMenuItem",
        "ContextMenuCheckboxItem", "ContextMenuRadioItem",
        "ContextMenuLabel", "ContextMenuSeparator",
        "ContextMenuGroup", "ContextMenuPortal", "ContextMenuSub",
        "ContextMenuSubTrigger", "ContextMenuSubContent",
        "ContextMenuRadioGroup", "ContextMenuShortcut",
    ],
    "form": [
        "FormItem", "FormLabel", "FormControl",
        "FormDescription", "FormMessage", "FormField",
    ],
    "table": [
        "TableHeader", "TableBody", "TableFooter", "TableRow",
        "TableHead", "TableCell", "TableCaption",
    ],
    "menubar": [
        "MenubarMenu", "MenubarTrigger", "MenubarContent",
        "MenubarItem", "MenubarSeparator", "MenubarLabel",
        "MenubarCheckboxItem", "MenubarRadioGroup", "MenubarRadioItem",
        "MenubarPortal", "MenubarSubContent", "MenubarSubTrigger",
        "MenubarGroup", "MenubarSub", "MenubarShortcut",
    ],
    "resizable": [
        "ResizablePanel", "ResizablePanelGroup", "ResizableHandle",
    ],
    "breadcrumb": [
        "BreadcrumbList", "BreadcrumbItem", "BreadcrumbLink",
        "BreadcrumbPage", "BreadcrumbSeparator", "BreadcrumbEllipsis",
    ],
    "pagination": [
        "PaginationContent", "PaginationItem", "PaginationLink",
        "PaginationPrevious", "PaginationNext", "PaginationEllipsis",
    ],
    "carousel": [
        "CarouselContent", "CarouselItem",
        "CarouselPrevious", "CarouselNext",
    ],
}


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

    # Derive component name using kebab-to-PascalCase so filenames like
    # "dropdown-menu" → "DropdownMenu" (not the invalid "Dropdown-menu").
    raw_name = import_path.rstrip("/").split("/")[-1]
    for ext in (".jsx", ".js", ".tsx", ".ts"):
        if raw_name.endswith(ext):
            raw_name = raw_name[:-len(ext)]
            break
    component_name = _kebab_to_pascal(raw_name) if raw_name else "Stub"

    # Collect all names to export: the primary component + any from the import
    # statement + shadcn subcomponents for known multi-component primitives.
    subcomponents = _SHADCN_SUBCOMPONENTS.get(raw_name, [])

    lines = [
        "// AUTO-GENERATED STUB — original component was missing from the",
        "// generation output. Replace with a real implementation.",
        'import * as React from "react"',
        "",
        f"const {component_name} = React.forwardRef(function {component_name}(",
        f"  {{ children, className, asChild, ...rest }}, ref",
        ") {",
        "  return React.createElement(",
        '    "div",',
        "    { ref, className, ...rest },",
        "    children,",
        "  )",
        "})",
        f'{component_name}.displayName = "{component_name}"',
        "",
    ]

    # Passthrough aliases for subcomponents (and any explicitly named imports)
    all_extra = sorted(
        set(subcomponents) | {n for n in named if n != component_name}
    )
    for name in all_extra:
        lines.append(f"const {name} = {component_name}")

    # Export block
    all_exports = [component_name] + all_extra
    lines.append("")
    lines.append("export {")
    for exp in all_exports:
        lines.append(f"  {exp},")
    lines.append("}")
    lines.append(f"export default {component_name}")

    return "\n".join(lines) + "\n"


_UPLOAD_MOUNT_SENTINEL = "# === DEMAESTRO_UPLOAD_MOUNT_BEGIN ==="
_UPLOAD_MOUNT_BLOCK = """\
# === DEMAESTRO_UPLOAD_MOUNT_BEGIN ===
try:
    import os as _os_uploads
    _os_uploads.makedirs("uploads", exist_ok=True)
    from fastapi.staticfiles import StaticFiles as _SF_uploads
    app.mount("/uploads", _SF_uploads(directory="uploads"), name="uploads")
except Exception as _e_uploads:
    print(f"[startup] WARNING: upload mount skipped: {_e_uploads}", flush=True)
# === DEMAESTRO_UPLOAD_MOUNT_END ===
"""


def _fix_upload_static_mount(test_results: dict, generated_files: dict) -> dict:
    """Inject a self-contained StaticFiles mount for /uploads when any generated
    route uses UploadFile.

    Design:
    - Self-contained try/except block — never inserted inside an existing
      try body, so it cannot produce 'expected except or finally' SyntaxErrors.
    - Sentinel-guarded: idempotent across multiple cycles.
    - Validated with ast.parse before persisting — if the injection would
      produce invalid Python, the change is discarded.
    """
    _log = structlog.get_logger("debugger")
    has_upload = any(
        path.startswith("backend/") and path.endswith(".py") and (content or "") and (
            "UploadFile" in content or "uploads/" in content
        )
        for path, content in generated_files.items()
    )
    if not has_upload:
        return {}

    main_path = "backend/app/main.py"
    main_content = generated_files.get(main_path)
    if not main_content:
        return {}

    # Idempotent: skip if already injected (either sentinel or old-style mount).
    if _UPLOAD_MOUNT_SENTINEL in main_content or 'app.mount("/uploads"' in main_content:
        return {}

    # Insert BEFORE the route-includes marker if present; otherwise append.
    marker_primary = "# === ROUTE INCLUDES BELOW THIS LINE"
    marker_legacy  = "# Project-specific route includes"
    new_main = main_content

    if marker_primary in new_main:
        idx = new_main.index(marker_primary)
        new_main = new_main[:idx] + _UPLOAD_MOUNT_BLOCK + "\n" + new_main[idx:]
    elif marker_legacy in new_main:
        idx = new_main.index(marker_legacy)
        new_main = new_main[:idx] + _UPLOAD_MOUNT_BLOCK + "\n" + new_main[idx:]
    else:
        # No marker — append at the end of the file.
        new_main = new_main.rstrip() + "\n\n" + _UPLOAD_MOUNT_BLOCK

    if new_main == main_content:
        return {}

    # Validate: ensure the result is valid Python before persisting.
    try:
        ast.parse(new_main)
    except SyntaxError as e:
        _log.error(
            "upload_static_mount.would_break_file",
            line=e.lineno, msg=str(e),
        )
        return {}  # do NOT persist broken code

    return {main_path: new_main}


def _fix_duplicate_app_definition(test_results: dict, generated_files: dict) -> dict:
    """Remove `from app import app` (or similar) when main.py also defines
    `app = FastAPI(...)`.  The LLM occasionally emits both, which mypy
    flags as 'Name app already defined (by an import)'."""
    path = "backend/app/main.py"
    content = generated_files.get(path)
    if not content:
        return {}

    has_import = bool(re.search(
        r'^\s*from\s+app(?:\.main)?\s+import\s+(?:[^,\n]+,\s*)?app\b',
        content, re.MULTILINE,
    )) or bool(re.search(r'^\s*import\s+app\b', content, re.MULTILINE))
    has_def = bool(re.search(r'^\s*app\s*=\s*FastAPI\(', content, re.MULTILINE))

    if not (has_import and has_def):
        return {}

    new = re.sub(
        r'^\s*from\s+app(?:\.main)?\s+import\s+app\s*$\n?',
        '', content, flags=re.MULTILINE,
    )
    new = re.sub(
        r'(from\s+app(?:\.main)?\s+import\s+[^,\n]*),\s*app\b',
        r'\1', new,
    )
    new = re.sub(r'^\s*import\s+app\s*$\n?', '', new, flags=re.MULTILINE)
    if new != content:
        return {path: new}
    return {}


# ── LLM output validation ─────────────────────────────────────────────────────

# ── Duplicate top-level declaration detector ─────────────────────────────────

def _detect_duplicate_top_level_decls(content: str) -> list:
    """Return a list of top-level function/const names declared more than once.

    Handles export / export default prefixes so that both
    'function Layout(' and 'export default function Layout(' are
    recognised as the same name.
    """
    from collections import Counter
    func_names = re.findall(
        r"^(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+(\w+)\s*\(",
        content, re.MULTILINE,
    )
    const_names = re.findall(
        r"^(?:export\s+(?:default\s+)?)?const\s+(\w+)\s*=",
        content, re.MULTILINE,
    )
    counts = Counter(func_names + const_names)
    return [name for name, count in counts.items() if count > 1]


# ── Unwrapped context-provider fixer ────────────────────────────────────────

# Matches lucide-react import statements to extract the imported icon names
_LUCIDE_IMPORT_RE = re.compile(
    r"import\s*\{([^}]+)\}\s*from\s*['\"]lucide-react['\"]",
)

# Matches uppercase identifier rendered as JSX text child: >{SomeName}<
_ICON_AS_CHILD_RE = re.compile(
    r">\s*\{\s*([A-Z][a-zA-Z0-9]*)\s*\}\s*<",
)

# Matches object.icon rendered as JSX child: >{item.icon}< or >{nav.icon}<
_ICON_PROP_AS_CHILD_RE = re.compile(
    r">\s*\{(\w+)\.icon\}\s*<",
)


def _fix_component_rendered_as_child(
    test_results: dict, generated_files: dict,
) -> dict:
    """Fix icon/component references rendered as JSX text children (React error #31).

    Only rewrites identifiers actually imported from lucide-react in the file,
    avoiding false positives. Also fixes {obj.icon} patterns with an IIFE wrapper.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict[str, str] = {}

    for path, content in generated_files.items():
        if not (path.startswith("frontend/src/") and path.endswith((".jsx", ".tsx"))):
            continue
        if not content:
            continue

        # Collect names actually imported from lucide-react in this file
        lucide_imports: set[str] = set()
        for m in _LUCIDE_IMPORT_RE.finditer(content):
            for raw in m.group(1).split(","):
                name = raw.strip()
                if " as " in name:
                    name = name.split(" as ")[-1].strip()
                if name:
                    lucide_imports.add(name)

        new_content = content

        # Fix {LucideIcon} rendered as text child → <LucideIcon />
        if lucide_imports:
            _captured = lucide_imports

            def _rewrite_icon_child(m: re.Match, _icons: set = _captured) -> str:
                name = m.group(1)
                if name in _icons:
                    return f"><{name} /><"
                return m.group(0)

            new_content = _ICON_AS_CHILD_RE.sub(_rewrite_icon_child, new_content)

        # Fix {item.icon} / {obj.icon} as text child → IIFE wrapper
        def _rewrite_prop_icon(m: re.Match) -> str:
            obj = m.group(1)
            return f">{{(() => {{ const __C = {obj}.icon; return __C ? <__C /> : null; }})()}}<"

        new_content = _ICON_PROP_AS_CHILD_RE.sub(_rewrite_prop_icon, new_content)

        if new_content != content:
            fixes[path] = new_content
            _log.info("fix_component_rendered_as_child.applied", path=path)

    return fixes


def _enforce_app_name_in_frontend(
    test_results: dict, generated_files: dict,
) -> dict:
    """Rewrite invented app names in frontend files back to blueprint app_name.

    The LLM sometimes substitutes a different name in the Navbar brand, hero h1,
    and HTML title.  This fixer reads blueprint.app_name from the generator
    context stored in generated_files["__meta__"] (if present) and rewrites
    occurrences of common placeholder names.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict[str, str] = {}

    meta = generated_files.get("__meta__") or {}
    app_name: str = (meta.get("app_name") or "").strip()
    invented_names: list[str] = meta.get("invented_names") or []

    if not app_name or not invented_names:
        return fixes

    for path, content in generated_files.items():
        if not isinstance(content, str):
            continue
        if not (path.startswith("frontend/src/") and path.endswith((".jsx", ".tsx", ".js", ".html"))):
            continue
        if not content:
            continue

        new_content = content
        for bad_name in invented_names:
            if bad_name and bad_name != app_name:
                new_content = new_content.replace(bad_name, app_name)

        if new_content != content:
            fixes[path] = new_content
            _log.info("enforce_app_name.rewrote", path=path, correct=app_name)

    return fixes


def _fix_query_double_unwrap(
    test_results: dict, generated_files: dict,
) -> dict:
    """Rewrite the two LLM anti-patterns that produce 'data is undefined' in
    TanStack useQuery hooks.

    Pattern A — Axios direct with .then unwrap:
        queryFn: () => api.get('/x').then((r) => r.data)

    Pattern B — Async with intermediate variable:
        const r = await api.get('/x'); return r.data

    Fix: the api helper already returns the parsed body directly. Drop the extra
    unwrap layer.

    Only touches files that already import useQuery from @tanstack/react-query.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict = {}

    for fp, content in generated_files.items():
        if not (fp.startswith("frontend/src/") and fp.endswith((".jsx", ".tsx"))):
            continue
        if "useQuery" not in content or "@tanstack/react-query" not in content:
            continue

        new_content = content

        # Pattern A — .then((r) => r.data) appended to api.get(...)
        new_content = re.sub(
            r"(api\.get\(\s*['\"][^'\"]+['\"]\s*\))"
            r"\.then\(\s*\([^)]*\)\s*=>\s*[a-zA-Z_$][\w$]*\.data\s*\)",
            r"\1",
            new_content,
        )

        # Pattern B — const r = await api.get(...); return r.data
        new_content = re.sub(
            r"const\s+([a-zA-Z_$][\w$]*)\s*=\s*await\s+(api\.get\(\s*['\"][^'\"]+['\"]\s*\))\s*;\s*return\s+\1\.data",
            r"return await \2",
            new_content,
        )

        if new_content != content:
            fixes[fp] = new_content
            _log.info("fix_query_double_unwrap.rewrote", file=fp)

    return fixes


def _fix_shadcn_button_aschild(
    test_results: dict, generated_files: dict,
) -> dict:
    """Ensure the Button component destructures asChild and uses Slot.

    If button.jsx renders a bare <button> without destructuring asChild, any
    caller passing asChild=true leaks the prop to the DOM element, triggering
    "React does not recognize the `asChild` prop" in the console.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict[str, str] = {}

    target = "frontend/src/components/ui/button.jsx"
    content = generated_files.get(target)
    if not content:
        return fixes

    # Already canonical: has asChild destructure, asChild=false default, and Slot.
    if (
        "asChild" in content
        and re.search(r"asChild\s*=\s*false", content)
        and "Slot" in content
    ):
        return fixes

    # Only patch when file has a bare <button but no asChild handling yet.
    if "asChild" in content or not re.search(r"<button\b", content):
        return fixes

    new_content = content

    # Ensure Slot import exists.
    if "Slot" not in new_content:
        new_content = re.sub(
            r'(import\s+\*?\s*React[^\n]*\n)',
            r'\1import { Slot } from "@radix-ui/react-slot"\n',
            new_content, count=1,
        )

    # Inject asChild into the first destructured param block that contains className.
    new_content = re.sub(
        r"\(\{\s*([^}]*?)\bclassName\b",
        r"({ asChild = false, \1className",
        new_content, count=1,
    )

    # Replace the first <button return with a Comp = asChild ? Slot : "button" pattern.
    new_content = re.sub(
        r"(\s+)return\s*\(\s*\n?\s*<button",
        r'\1const Comp = asChild ? Slot : "button";\n'
        r'\1return (\n\1  <Comp',
        new_content, count=1,
    )
    new_content = re.sub(r"</button>", "</Comp>", new_content)

    if new_content != content:
        fixes[target] = new_content
        _log.info("fix_shadcn_button_aschild.upgraded_to_slot", path=target)

    return fixes


_PROVIDER_IMPORT_RE = re.compile(
    r'import\s+[{]?\s*(?P<name>\w+Provider)\s*[}]?\s+from\s+["\'][^"\']+["\']',
)
# Skip well-known scaffold providers that are already handled elsewhere.
_PROVIDER_SKIP = frozenset({"QueryClientProvider", "AuthProvider"})


def _fix_unwrapped_context_providers(test_results: dict, generated_files: dict) -> dict:
    """Detect Provider imports in App.jsx that are never used as JSX wrappers.

    Two cases:
      A) Provider IS used by some hook elsewhere → surgically wrap <BrowserRouter>
         with <Provider>…</Provider>.  NEVER regenerates the file or touches
         Layout/BareLayout declarations.
      B) Provider is imported but no component uses the hook → remove dead import.

    Surgical: only inserts two tags around the existing <BrowserRouter>.
    Idempotent.
    """
    _log = structlog.get_logger("debugger")
    app_path = "frontend/src/App.jsx"
    content = generated_files.get(app_path, "")
    if not content:
        return {}

    imported_providers: list[str] = [
        m.group("name")
        for m in _PROVIDER_IMPORT_RE.finditer(content)
        if m.group("name") not in _PROVIDER_SKIP
    ]
    if not imported_providers:
        return {}

    new_app = content

    for provider in imported_providers:
        # Already used as a JSX element?
        if re.search(rf'<{re.escape(provider)}[\s>/]', new_app):
            continue

        hook_name = f"use{provider[:-len('Provider')]}"

        hook_used = any(
            hook_name in (fc or "")
            for fp, fc in generated_files.items()
            if fp.startswith("frontend/src/") and fp.endswith((".jsx", ".tsx", ".js", ".ts"))
            and fp != app_path
        )

        if not hook_used:
            # Dead import — remove it cleanly.
            new_app = re.sub(
                rf'^import\s+[{{]?\s*{re.escape(provider)}\s*[}}]?\s+from\s+["\'][^"\']+["\'];?\s*\n',
                "",
                new_app,
                flags=re.MULTILINE,
            )
            _log.info("fix_unwrapped_context_providers.removed_dead_import",
                      provider=provider, hook=hook_name)
            continue

        # ── Surgical wrap: ONLY insert two tags around <BrowserRouter> ──────
        # NEVER regenerate the file; NEVER add function declarations.
        br_open = re.search(r"<BrowserRouter\b[^>]*>", new_app)
        br_close_pos = new_app.rfind("</BrowserRouter>")
        if not br_open or br_close_pos == -1:
            _log.warning("fix_unwrapped_context_providers.no_browser_router_found",
                         provider=provider, file=app_path)
            continue

        open_pos = br_open.start()
        close_end = br_close_pos + len("</BrowserRouter>")

        # Detect indentation of the <BrowserRouter> line for pretty output.
        line_start = new_app.rfind("\n", 0, open_pos) + 1
        indent = new_app[line_start:open_pos]

        new_app = (
            new_app[:open_pos]
            + f"<{provider}>\n{indent}"
            + new_app[open_pos:close_end]
            + f"\n{indent}</{provider}>"
            + new_app[close_end:]
        )
        _log.info("fix_unwrapped_context_providers.injected",
                  provider=provider, hook=hook_name, path=app_path)

    if new_app != content:
        return {app_path: new_app}
    return {}


# ── App.jsx Layout/BareLayout/Outlet fixer ───────────────────────────────────

_APP_ROUTE_LINE_RE = re.compile(
    r'<Route\b[^\n]*?\bpath=["\']([^"\']+)["\']'
)
_AUTH_ROUTE_SLUGS = frozenset({
    "login", "register", "signin", "signup",
    "forgot-password", "reset-password", "auth", "verify",
})


def _app_jsx_has_layout(content: str) -> tuple[bool, bool]:
    """Single source of truth for Layout/BareLayout detection.

    Returns (has_layout, has_bare_layout).  Anchors to start-of-line and
    handles all common declaration forms:
      function Layout(       export function Layout(
      const Layout =         export const Layout =
      export default function Layout(
    """
    has_layout = bool(re.search(
        r"^(?:export\s+(?:default\s+)?)?(?:function\s+Layout\s*\(|const\s+Layout\s*=)",
        content, re.MULTILINE,
    ))
    has_bare = bool(re.search(
        r"^(?:export\s+(?:default\s+)?)?(?:function\s+BareLayout\s*\(|const\s+BareLayout\s*=)",
        content, re.MULTILINE,
    ))
    return has_layout, has_bare


def _needs_layout_injection(content: str) -> bool:
    """Return True only when App.jsx has no Layout function, no BareLayout function,
    and no Outlet reference anywhere — all three absent means the scaffold pattern
    is completely missing and safe to inject."""
    has_layout_func, has_barelayout_func = _app_jsx_has_layout(content)
    has_outlet = bool(re.search(r"\bOutlet\b", content))
    return not (has_layout_func or has_barelayout_func or has_outlet)


_LAYOUT_DECL_RE = re.compile(
    r"^(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+(Layout|BareLayout)\s*\(",
    re.MULTILINE,
)


def _dedup_layout_decls(content: str) -> str:
    """Remove duplicate Layout/BareLayout function declarations, keeping only
    the first occurrence of each.  Uses brace-depth counting to find the full
    function body so it removes the declaration plus its entire body block.
    """
    seen: set[str] = set()
    ranges_to_delete: list[tuple[int, int]] = []
    for m in _LAYOUT_DECL_RE.finditer(content):
        name = m.group(1)
        if name in seen:
            open_brace = content.find("{", m.end())
            if open_brace == -1:
                continue
            depth = 0
            i = open_brace
            while i < len(content):
                if content[i] == "{":
                    depth += 1
                elif content[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            if depth != 0:
                continue
            end = i + 1
            # Consume trailing blank lines for a clean deletion
            while end < len(content) and content[end] == "\n":
                end += 1
            ranges_to_delete.append((m.start(), end))
        else:
            seen.add(name)
    if not ranges_to_delete:
        return content
    ranges_to_delete.sort(reverse=True)
    new_content = content
    for start, end in ranges_to_delete:
        new_content = new_content[:start] + new_content[end:]
    return new_content


# ── Layout import/inline conflict resolver ────────────────────────────────────
# The LLM sometimes generates BOTH an import (`import { Layout } from '...'`)
# AND an inline `function Layout()` declaration. Every dedup helper looks only
# for duplicate function declarations, so none of them fire -- but esbuild
# treats both the import and the inline function as declarations of the same
# symbol and raises "Layout has already been declared".
# This helper removes the import, keeping the inline function.

_IMPORT_LAYOUT_NAMED_RE = re.compile(
    r"""^import\s*\{([^}]*)\}\s*from\s*["'][^"']+["']\s*;?\s*$""",
    re.MULTILINE,
)
_IMPORT_LAYOUT_DEFAULT_RE = re.compile(
    r"""^import\s+(Layout|BareLayout)\s+from\s*["'][^"']+["']\s*;?\s*$""",
    re.MULTILINE,
)
_INLINE_LAYOUT_FUNC_RE = re.compile(
    r"^(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+(Layout|BareLayout)\s*\(",
    re.MULTILINE,
)


def _resolve_layout_import_inline_conflict(content: str) -> str:
    """Remove import lines that conflict with inline function declarations.

    If App.jsx has BOTH an import that brings Layout/BareLayout into scope AND
    an inline function declaration of the same name, remove the import.
    The inline declaration wins because it contains the actual JSX scaffold.

    Handles:
      import { Layout } from '...'            -> strip Layout from named list
      import { Layout, X, Y } from '...'      -> remove Layout, keep X, Y
      import Layout from '...'                -> remove entire line
      import BareLayout from '...'            -> remove entire line

    Idempotent: if no conflict exists, returns content unchanged.
    """
    inline_names = {m.group(1) for m in _INLINE_LAYOUT_FUNC_RE.finditer(content)}
    if not inline_names:
        return content

    changed = False
    new_content = content

    def _drop_default(m):
        nonlocal changed
        if m.group(1) in inline_names:
            changed = True
            return ""
        return m.group(0)

    new_content = _IMPORT_LAYOUT_DEFAULT_RE.sub(_drop_default, new_content)

    def _filter_named(m):
        nonlocal changed
        full = m.group(0)
        names_block = m.group(1)
        names = [n.strip() for n in names_block.split(",") if n.strip()]
        kept = []
        dropped = False
        for entry in names:
            parts = re.split(r"\s+as\s+", entry)
            local_name = parts[-1].strip()
            if local_name in inline_names:
                dropped = True
                continue
            kept.append(entry)
        if not dropped:
            return full
        changed = True
        if not kept:
            return ""
        rebuilt = "{ " + ", ".join(kept) + " }"
        return re.sub(r"\{[^}]*\}", rebuilt, full, count=1)

    new_content = _IMPORT_LAYOUT_NAMED_RE.sub(_filter_named, new_content)

    if not changed:
        return content

    new_content = re.sub(r"\n{3,}", "\n\n", new_content)
    return new_content


def _fix_layout_import_inline_conflict(
    test_results: dict, generated_files: dict,
) -> dict:
    """Per-cycle fixer: remove import lines that conflict with inline Layout/BareLayout."""
    _log = structlog.get_logger("debugger")
    fixes: dict[str, str] = {}
    app_path = "frontend/src/App.jsx"
    content = generated_files.get(app_path, "")
    if not content:
        return fixes
    try:
        new_content = _resolve_layout_import_inline_conflict(content)
        if new_content != content:
            fixes[app_path] = new_content
            _log.info(
                "fix_layout_import_inline_conflict.removed_import",
                path=app_path,
            )
    except Exception as exc:
        _log.warning("fix_layout_import_inline_conflict.error", error=str(exc))
    return fixes


_HARD_DEDUP_LOG = structlog.get_logger("debugger")


def _hard_dedup_jsx_files(files: dict) -> list:
    """Remove duplicate top-level Layout, BareLayout, and App function declarations.

    Mutates `files` in-place. Returns a list of paths that were changed.
    Keeps the FIRST occurrence of each name; deletes subsequent duplicates by
    walking brace depth from the opening brace of the duplicate declaration.

    This is wired as a DIRECT CALL after the post-loop rollback sweep so it
    cannot be undone by that sweep. It is safe to call multiple times (idempotent).
    """
    changed = []
    try:
        for path, content in list(files.items()):
            if not re.search(r"\.(jsx|tsx)$", path):
                continue
            if not content:
                continue
            new = content
            for name in ("Layout", "BareLayout", "App"):
                pattern = re.compile(
                    r"^(?:export\s+default\s+|export\s+)?"
                    rf"function\s+{name}\s*\([^)]*\)\s*\{{",
                    re.MULTILINE,
                )
                matches = list(pattern.finditer(new))
                if len(matches) <= 1:
                    continue
                for m in reversed(matches[1:]):
                    start = m.start()
                    depth = 0
                    i = m.end() - 1  # at the opening brace '{' (included in the match)
                    end = None
                    while i < len(new):
                        ch = new[i]
                        if ch == "{":
                            depth += 1
                        elif ch == "}":
                            depth -= 1
                            if depth == 0:
                                end = i + 1
                                break
                        i += 1
                    if end is None:
                        continue
                    while end < len(new) and new[end] in " \t":
                        end += 1
                    if end < len(new) and new[end] == "\n":
                        end += 1
                    while end < len(new) and new[end] == "\n":
                        end += 1
                    new = new[:start] + new[end:]
                    _HARD_DEDUP_LOG.info("hard_dedup_jsx.removed", file=path, name=name)
            if new != content:
                files[path] = new
                changed.append(path)
    except Exception as exc:
        _HARD_DEDUP_LOG.error("hard_dedup_jsx_files.crashed", error=str(exc))
    return changed


def _dedup_jsx_layout_decls_fixer(
    test_results: dict, generated_files: dict
) -> dict:
    """Standalone fixer: remove duplicate Layout/BareLayout declarations from
    every JSX/TSX file in generated_files.

    Wraps _dedup_layout_decls so it runs as a pre-LLM-fixers step on all JSX
    files, not only App.jsx. This catches cases where multiple helpers injected
    Layout/BareLayout in the same cycle, producing a build error like
    'Layout has already been declared'.

    Idempotent: files with exactly one (or zero) declarations are unchanged.
    Never raises -- exceptions are caught and swallowed.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict[str, str] = {}
    try:
        for path, content in generated_files.items():
            if not path.endswith((".jsx", ".tsx")) or not content:
                continue
            new_content = _dedup_layout_decls(content)
            if new_content != content:
                fixes[path] = new_content
                _log.info(
                    "dedup_jsx_layout_decls.removed",
                    path=path,
                )
    except Exception as exc:
        _log.warning("dedup_jsx_layout_decls_fixer.error", error=str(exc))
    return fixes


def _fix_app_jsx_layout_pattern(test_results: dict, generated_files: dict) -> dict:
    """Rewrite App.jsx to use the Layout/BareLayout/Outlet scaffold pattern.

    Idempotent -- skips when Layout function, BareLayout function, and Outlet
    reference are all already present.  Preserves existing import statements
    and Route entries.  Auth paths go into BareLayout; everything else into Layout.

    Also removes duplicate Layout/BareLayout declarations when detected --
    these arise when two helpers both inject the scaffold in the same cycle.
    """
    _log = structlog.get_logger("debugger")
    app_path = "frontend/src/App.jsx"
    content = generated_files.get(app_path, "")
    if not content:
        return {}

    # Hard guard: if BOTH Layout and BareLayout already exist, never inject.
    # _hard_dedup_jsx_files (wired as a direct call after this helper) will
    # handle any duplicates. This guard prevents us from creating duplicates
    # in the first place.
    _has_layout, _has_bare = _app_jsx_has_layout(content)
    if _has_layout and _has_bare:
        _log.info("fix_app_jsx_layout_pattern.already_present", path=app_path)
        return {}

    if not _needs_layout_injection(content):
        # Before declaring "already present", check for DUPLICATE declarations.
        # Two helpers injecting Layout in the same cycle causes a
        # "Layout has already been declared" build error.  Fix it here because
        # this helper is guaranteed to run every cycle (its log fires reliably).
        from collections import Counter
        counts = Counter(m.group(1) for m in _LAYOUT_DECL_RE.finditer(content))
        if any(c > 1 for c in counts.values()):
            new_content = _dedup_layout_decls(content)
            if new_content != content:
                _log.info(
                    "fix_app_jsx_layout_pattern.deduped",
                    path=app_path,
                    before_counts=dict(counts),
                )
                return {app_path: new_content}
        _log.info("fix_app_jsx_layout_pattern.already_present", path=app_path)
        return {}

    lines = content.splitlines()

    # Collect all existing import lines.
    import_lines = [ln for ln in lines if ln.lstrip().startswith("import ")]

    # Build set of already-imported names to avoid duplicate import statements.
    _already_imported: set[str] = set()
    for _il in import_lines:
        _already_imported.update(re.findall(r"\b([A-Z][A-Za-z0-9]*)\b", _il))

    # Ensure react-router-dom import includes Outlet (and BrowserRouter/Routes/Route).
    _rrd_needed = {"BrowserRouter", "Routes", "Route", "Outlet"}
    _rrd_missing = _rrd_needed - _already_imported
    has_rrd = any("react-router-dom" in ln for ln in import_lines)
    if not has_rrd:
        import_lines.insert(
            0, 'import { BrowserRouter, Routes, Route, Outlet } from "react-router-dom"'
        )
    elif _rrd_missing:
        # Merge missing names into existing react-router-dom import line.
        import_lines = [
            re.sub(
                r"(\{)([^}]+)(\}\s*from\s*['\"]react-router-dom['\"])",
                lambda m: m.group(1) + ", ".join(sorted(_rrd_missing)) + ", " + m.group(2) + m.group(3),
                ln,
            ) if "react-router-dom" in ln else ln
            for ln in import_lines
        ]

    # Ensure QueryClient / QueryClientProvider import (skip if either already present).
    if "QueryClient" not in _already_imported and "QueryClientProvider" not in _already_imported:
        import_lines.append(
            'import { QueryClient, QueryClientProvider } from "@tanstack/react-query"'
        )

    # Ensure AuthProvider import.
    if "AuthProvider" not in _already_imported:
        import_lines.append(
            'import { AuthProvider } from "@/contexts/AuthContext"'
        )

    # Collect existing Route entries (single-line self-closing).
    layout_routes, bare_routes = [], []
    seen: set[str] = set()
    for ln in lines:
        m = _APP_ROUTE_LINE_RE.search(ln)
        if not m:
            continue
        path_val = m.group(1)
        if path_val in seen:
            continue
        seen.add(path_val)
        slug = path_val.strip("/").split("/")[0]
        tag = "              " + ln.strip()
        if slug in _AUTH_ROUTE_SLUGS:
            bare_routes.append(tag)
        else:
            layout_routes.append(tag)

    layout_body = (
        "\n".join(layout_routes)
        if layout_routes else "              {/* add routes here */}"
    )
    bare_body = (
        "\n".join(bare_routes)
        if bare_routes else "              {/* auth routes */}"
    )
    imports_block = "\n".join(import_lines)

    new_app = (
        imports_block + "\n\n"
        "const queryClient = new QueryClient()\n\n"
        "function Layout() {\n"
        "  return (\n"
        '    <div className="min-h-screen flex flex-col bg-surface-page text-text-default">\n'
        "      <Outlet />\n"
        "    </div>\n"
        "  )\n"
        "}\n\n"
        "function BareLayout() {\n"
        "  return (\n"
        '    <div className="min-h-screen bg-surface-page text-text-default">\n'
        "      <Outlet />\n"
        "    </div>\n"
        "  )\n"
        "}\n\n"
        "function App() {\n"
        "  return (\n"
        "    <QueryClientProvider client={queryClient}>\n"
        "      <AuthProvider>\n"
        "        <BrowserRouter>\n"
        "          <Routes>\n"
        "            <Route element={<Layout />}>\n"
        + layout_body + "\n"
        "            </Route>\n"
        "            <Route element={<BareLayout />}>\n"
        + bare_body + "\n"
        "            </Route>\n"
        "          </Routes>\n"
        "        </BrowserRouter>\n"
        "      </AuthProvider>\n"
        "    </QueryClientProvider>\n"
        "  )\n"
        "}\n\n"
        "export default App\n"
    )

    return {app_path: new_app}


# ── LLM output validation ──────────────────────────────────────────────────────

_PROSE_STARTS = (
    "looking at", "the issue is", "to fix this", "i'll fix",
    "i will fix", "here's the fix", "here is the", "the fix is",
    "based on the error", "but i'm told", "i need to", "let me",
    "first,", "the problem", "the error", "to address",
    "to resolve", "we need to", "we should", "this is a",
    "analyzing the",
)


def _strip_code_fences(content: str) -> str:
    """Remove markdown ``` fences from LLM output if present."""
    stripped = content.strip()
    if not stripped.startswith("```"):
        return stripped
    m = re.match(r"^```(?:\w+)?\s*\n(.*?)\n```\s*$", stripped, re.DOTALL)
    return m.group(1).strip() if m else stripped


def _validate_fix_response(content: str, lang: str) -> tuple[bool, str]:
    """Return (is_valid, reason). Rejects prose/analysis instead of code."""
    import ast as _ast
    stripped = _strip_code_fences(content)
    if not stripped:
        return False, "empty"
    first_line = stripped.split("\n", 1)[0].lower().strip()
    for prefix in _PROSE_STARTS:
        if first_line.startswith(prefix):
            return False, f"prose_start:{prefix}"
    if lang == "python":
        try:
            _ast.parse(stripped)
        except SyntaxError as exc:
            return False, f"syntax_error:{exc.msg}"
        has_code = any(
            token in stripped
            for token in ("def ", "class ", "import ", "from ",
                          "@router", "@app", "router =", "app =")
        )
        if not has_code:
            return False, "no_code_constructs"
    elif lang == "jsx":
        has_code = any(
            token in stripped
            for token in ("import ", "export ", "function ", "const ",
                          "return (", "<")
        )
        if not has_code:
            return False, "no_jsx_constructs"
    return True, "ok"


# ── Legacy Column() → Mapped[] converter ──────────────────────────────────────

_LEGACY_COL_RE = re.compile(
    # Handles: plain, typed (email: str = Column(...)), and multi-line Column() calls
    r"^(\s*)(\w+)(?:\s*:\s*[\w\[\],\| ]+)?\s*=\s*Column\((.*?)\)\s*$",
    re.MULTILINE | re.DOTALL,
)
_SQLA_TYPE_MAP = {
    "Integer": "int", "BigInteger": "int", "SmallInteger": "int",
    "String": "str", "Text": "str", "Unicode": "str", "UnicodeText": "str",
    "Boolean": "bool",
    "DateTime": "datetime", "Date": "date",
    "Float": "float", "Numeric": "Decimal",
    "JSON": "dict", "JSONB": "dict",
}


def _fix_legacy_column_to_mapped(test_results: dict, generated_files: dict) -> dict:
    """Convert `email = Column(String)` declarations to `email: Mapped[str] = mapped_column(String)`.

    Eliminates ~200 'Unexpected keyword argument' mypy errors that fire when the
    sqlalchemy[mypy] plugin encounters the legacy Column() form instead of mapped_column().
    """
    fixes: dict[str, str] = {}
    for path, content in generated_files.items():
        if not path.endswith(".py") or not content:
            continue
        if "Column(" not in content or "class " not in content:
            continue
        if "(Base)" not in content and ", Base)" not in content:
            continue

        def _replace(m):
            indent, name, args = m.group(1), m.group(2), m.group(3)
            # Strip newlines/extra whitespace when reading the first type arg
            # (multi-line Column() declarations have newlines in `args`)
            first_arg = args.split(",")[0].strip().strip("\n").strip()
            if name == "id" or (
                "primary_key=True" in args
                and first_arg in ("Integer", "BigInteger", "SmallInteger")
            ):
                py_type = "int"
            else:
                py_type = _SQLA_TYPE_MAP.get(first_arg, "Any")
            return f"{indent}{name}: Mapped[{py_type}] = mapped_column({args})"

        new, count = _LEGACY_COL_RE.subn(_replace, content)
        if not count:
            continue

        # Ensure Mapped and mapped_column are imported
        if "from sqlalchemy.orm import" in new:
            def _add_orm_imports(m):
                existing = m.group(1)
                additions = [
                    sym for sym in ("Mapped", "mapped_column")
                    if sym not in existing
                ]
                if not additions:
                    return m.group(0)
                return f"from sqlalchemy.orm import {existing.rstrip()}, {', '.join(additions)}"
            new = re.sub(
                r"from sqlalchemy\.orm import ([^\n]+)",
                _add_orm_imports, new, count=1,
            )
        else:
            new = re.sub(
                r"(from sqlalchemy[^\n]*\n)",
                r"\1from sqlalchemy.orm import Mapped, mapped_column\n",
                new, count=1,
            )

        if new != content:
            fixes[path] = new
    return fixes


# ── Cross-file name mismatch fixer ───────────────────────────────────────────

_TOP_LEVEL_CLASS_RE = re.compile(r"^class\s+(\w+)\b", re.MULTILINE)
_IMPORT_FROM_RE = re.compile(
    r"^from\s+([\w.]+)\s+import\s+(.+)$",
    re.MULTILINE,
)


def _levenshtein(a: str, b: str) -> int:
    """Simple edit distance. Returns 99 for strings differing by >3 chars."""
    if abs(len(a) - len(b)) > 3:
        return 99
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, n + 1):
            temp = dp[j]
            dp[j] = prev if a[i - 1] == b[j - 1] else 1 + min(prev, dp[j], dp[j - 1])
            prev = temp
    return dp[n]


def _restore_user_model_in_auth_routes(
    test_results: dict, generated_files: dict
) -> dict:
    """If auth_routes.py imports or uses `Order` where User is expected,
    restore User. Defensive fixer for cases where a previous cycle's rename
    helper corrupted the auth scaffold.

    Detection heuristics:
      1. The scaffold's auth_routes.py hashes payload.password and creates a
         new user.  `Order(email=..., password_hash=...)` means Order has no
         password_hash column — that's the corrupted state.
      2. `db.query(Order).filter(Order.id == ...)` inside auth-related files
         means get_current_user was overwritten.

    Idempotent: no-op if auth_routes.py already imports User correctly.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict = {}

    auth_related = [
        "backend/app/routes/auth_routes.py",
        "backend/app/auth.py",
    ]

    for fp in auth_related:
        content = generated_files.get(fp)
        if not content:
            continue

        signals = [
            "Order(email=" in content,
            "Order.password_hash" in content,
            "db.query(Order).filter(Order.id" in content and "get_current_user" in content,
            "from app.models import Order" in content and "hash_password" in content,
        ]
        if not any(signals):
            continue

        new_content = re.sub(r"\bOrder\b", "User", content)

        new_content = re.sub(
            r"from\s+app\.models\s+import\s+([^\n]+)",
            lambda m: (
                "from app.models import "
                + ", ".join(n.strip() for n in m.group(1).split(",")
                            if n.strip() != "Order")
                + (", User" if "User" not in m.group(1) else "")
            ),
            new_content,
        )

        if new_content != content:
            fixes[fp] = new_content
            _log.info(
                "restore_user_model_in_auth_routes.applied",
                file=fp,
            )

    return fixes


def _fix_cross_file_name_mismatch(test_results: dict, generated_files: dict) -> dict:
    """Rewrite cross-file identifier mismatches (LLM emits `MenuItem`
    in routes but `MenuItems` in models, etc.)

    CRITICAL SAFETY: never rename scaffold-owned identifiers.  The
    scaffold guarantees these names exist and any 'rename' would
    destroy the auth flow (register 503s, /me fails, JWT lookup
    breaks — all confirmed in production incidents).
    """
    _log = structlog.get_logger("debugger")

    # ── Scaffold identifiers this helper must NEVER touch ────────────
    _SCAFFOLD_PROTECTED = frozenset({
        # Auth model (from auth_models.py)
        "User",
        # Core imports the scaffold ships
        "Base",
        # Auth utility functions
        "hash_password", "verify_password", "create_access_token",
        "get_current_user", "require_admin",
        "get_password_hash", "verify_pwd", "create_token",
        # Auth schemas ONLY when they're from the scaffold
        "LoginRequest", "RegisterRequest",
    })

    # Build module export map: file path -> set of PascalCase top-level names
    module_exports: dict[str, set[str]] = {}
    for path, content in generated_files.items():
        if not path.endswith(".py") or not content:
            continue
        exports: set[str] = set()
        for m in _TOP_LEVEL_CLASS_RE.finditer(content):
            exports.add(m.group(1))
        module_exports[path] = exports

    if not module_exports:
        return {}

    def _path_for_module(mod: str) -> str | None:
        candidate = "backend/" + mod.replace(".", "/") + ".py"
        return candidate if candidate in generated_files else None

    fixes: dict[str, str] = {}
    for path, content in generated_files.items():
        if not path.endswith(".py") or not content:
            continue
        new = content
        for m in _IMPORT_FROM_RE.finditer(content):
            module_str, names_str = m.group(1), m.group(2)
            target_path = _path_for_module(module_str)
            if target_path is None:
                continue
            target_exports = module_exports.get(target_path, set())
            if not target_exports:
                continue
            import_names = [
                n.strip().split(" as ")[0].strip()
                for n in names_str.split(",")
            ]
            for imp_name in import_names:
                if not imp_name or not imp_name[0].isupper() or imp_name in target_exports:
                    continue
                # Find the closest matching exported name (distance ≤ 3 to catch
                # common LLM typos like FavoredPlant vs FavoritePlant)
                best_match, best_dist = None, 99
                for export in target_exports:
                    d = _levenshtein(imp_name, export)
                    if d <= 3 and d < best_dist:
                        best_dist, best_match = d, export
                if best_match:
                    # Guard: never rename a scaffold-owned identifier away
                    if imp_name in _SCAFFOLD_PROTECTED:
                        _log.warning(
                            "fix_cross_file_name_mismatch.blocked_scaffold_rename",
                            source=path,
                            would_rename_from=imp_name,
                            would_rename_to=best_match,
                            reason=(
                                "Scaffold owns this identifier. Renaming would break the "
                                "auth flow. Skip and let the agentic LLM decide what to fix "
                                "instead."
                            ),
                        )
                        continue

                    # Guard: never rename INTO a scaffold identifier from a non-scaffold source
                    if best_match in _SCAFFOLD_PROTECTED and imp_name not in _SCAFFOLD_PROTECTED:
                        _log.warning(
                            "fix_cross_file_name_mismatch.blocked_scaffold_collision",
                            source=path,
                            from_name=imp_name,
                            to_name=best_match,
                        )
                        continue

                    _log.info(
                        "debugger.fix_cross_file_name_mismatch.applied",
                        source=path, fixed_name=imp_name, to=best_match,
                    )
                    # Rewrite the import line
                    old_line = m.group(0)
                    new_line = old_line.replace(imp_name, best_match)
                    new = new.replace(old_line, new_line, 1)
                    # Rewrite all identifier references in the file body
                    new = re.sub(rf'\b{re.escape(imp_name)}\b', best_match, new)
        if new != content:
            fixes[path] = new
    return fixes


# ── Duplicate Operation ID fixer ──────────────────────────────────────────────

_ROUTE_DECORATOR_LINE_RE = re.compile(
    r"^\s*@\w+\.(get|post|put|patch|delete)\s*\(",
    re.MULTILINE,
)
_FUNC_DEF_RE = re.compile(
    r"^\s*(?:async\s+)?def\s+(\w+)\s*\(",
    re.MULTILINE,
)


def _fix_duplicate_operation_ids(test_results: dict, generated_files: dict) -> dict:
    """Rename duplicate route-handler function names across backend route files.

    FastAPI requires unique operation IDs; duplicate function names trigger a
    'Duplicate Operation ID' warning at startup that confuses clients.
    """
    route_files = {
        p: c for p, c in generated_files.items()
        if p.startswith("backend/app/routes/") and p.endswith(".py")
    }
    if not route_files:
        return {}

    # name -> list of file paths that define a route handler with that name
    name_to_files: dict[str, list[str]] = {}
    for path, content in route_files.items():
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if not _ROUTE_DECORATOR_LINE_RE.match(line):
                continue
            for j in range(i + 1, min(i + 6, len(lines))):
                fm = re.match(r"\s*(?:async\s+)?def\s+(\w+)\s*\(", lines[j])
                if fm:
                    name_to_files.setdefault(fm.group(1), []).append(path)
                    break

    duplicates = {n: paths for n, paths in name_to_files.items() if len(paths) > 1}
    if not duplicates:
        return {}

    fixes: dict[str, str] = {}
    for dup_name, paths in duplicates.items():
        # Keep the first occurrence; rename subsequent ones with a module suffix
        for path in paths[1:]:
            module_suffix = path.rsplit("/", 1)[-1].replace(".py", "")
            new_name = f"{dup_name}_{module_suffix}"
            base = fixes.get(path, generated_files.get(path, ""))
            updated = re.sub(
                rf'^(\s*(?:async\s+)?def\s+){re.escape(dup_name)}(\s*\()',
                rf'\g<1>{new_name}\2',
                base,
                flags=re.MULTILINE,
            )
            if updated != base:
                fixes[path] = updated

    return fixes


# ── Undefined Icon in .map() fixer ───────────────────────────────────────────

_CAPITAL_ICON_JSX_RE = re.compile(r'<Icon[\s/>{]')
_ICON_IN_SCOPE_RE = re.compile(
    r'^(?:import\s+(?:\{[^}]*\b|\b)Icon\b|(?:const|let)\s+Icon\s*=)',
    re.MULTILINE,
)
_MAP_BLOCK_BODY_PARAM_RE = re.compile(r'\.map\s*\(\s*\((\w+)\)\s*=>\s*\{')
_MAP_DESTRUCT_RE = re.compile(
    r'(\.map\s*\(\s*\(\s*\{)([^}]+)(\}\s*\))\s*=>'
)
_ICON_ARRAY_PROP_RE = re.compile(r'\bicon\s*:\s*\w+')


def _fix_undefined_icon_in_map(test_results: dict, generated_files: dict) -> dict:
    """Fix undefined <Icon> component inside .map() callbacks.

    Detects the recurring LLM bug where an items array has `icon:` properties
    but the map callback references `<Icon>` as if it were in scope.

    Handles two cases:
      A) Block-body map: .map((item) => { ... <Icon> ... })
         → injects `const Icon = item.icon` at the start of the block
      B) Destructured map: .map(({ label, path }) => ...) with <Icon> in body
         → adds `icon: Icon` to the destructure params
    """
    _log = structlog.get_logger("debugger")
    fixes: dict = {}

    for path, content in generated_files.items():
        if not (path.endswith(".jsx") or path.endswith(".tsx")):
            continue
        if not content:
            continue
        if not _CAPITAL_ICON_JSX_RE.search(content):
            continue
        # Skip if Icon is already imported or declared at module level
        if _ICON_IN_SCOPE_RE.search(content):
            continue
        # Require that the file has an items array with icon: properties
        if not _ICON_ARRAY_PROP_RE.search(content):
            continue

        new = content
        changed = False

        # Pass A: block-body maps — inject `const Icon = param.icon`
        lines = new.split("\n")
        out: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            bm = _MAP_BLOCK_BODY_PARAM_RE.search(line)
            if bm:
                param = bm.group(1)
                # Look ahead up to 30 lines for <Icon without const Icon =
                end = min(i + 30, len(lines))
                window = "\n".join(lines[i:end])
                if _CAPITAL_ICON_JSX_RE.search(window) and "const Icon =" not in window:
                    # Find the indentation of the first code line inside the block
                    j = i + 1
                    inner_indent = "    "
                    while j < len(lines) and not lines[j].strip():
                        j += 1
                    if j < len(lines):
                        inner_indent = re.match(r"^(\s*)", lines[j]).group(1)
                    out.append(line)
                    i += 1
                    # Preserve any blank lines right after the opening brace
                    while i < len(lines) and not lines[i].strip():
                        out.append(lines[i])
                        i += 1
                    out.append(f"{inner_indent}const Icon = {param}.icon")
                    changed = True
                    continue
            out.append(line)
            i += 1

        if changed:
            new = "\n".join(out)

        # Pass B: destructured maps — add icon: Icon to the destructure
        if _CAPITAL_ICON_JSX_RE.search(new) and "const Icon =" not in new:
            def _add_icon_to_destruct(m: re.Match) -> str:
                props = m.group(2)
                if "icon" in props.lower():
                    return m.group(0)  # already has icon
                return f"{m.group(1)}icon: Icon, {props.lstrip()}{m.group(3)} =>"

            new2, c = _MAP_DESTRUCT_RE.subn(_add_icon_to_destruct, new)
            if c > 0:
                new = new2
                changed = True

        if changed and new != content:
            _log.info("fix_undefined_icon_in_map.applied", path=path)
            fixes[path] = new

    return fixes


# ── Routes config consistency fixer ─────────────────────────────────────────


def _run_helper_with_safety(
    helper_fn, test_results: dict, generated_files: dict
) -> dict:
    """Run helper_fn and roll back any file whose output has unbalanced brackets
    (broken JSX) or a Python SyntaxError.

    Safety net for structural helpers that rewrite multiple files at once — a
    damaged file is excluded from the returned fixes so the original stays in
    place.
    """
    _log = structlog.get_logger("debugger")
    try:
        fixes = helper_fn(test_results, generated_files)
    except Exception as exc:
        _log.warning(
            "_run_helper_with_safety.exception",
            helper=getattr(helper_fn, "__name__", str(helper_fn)),
            error=str(exc),
        )
        return {}
    if not fixes:
        return {}
    safe: dict[str, str] = {}
    for path, content in fixes.items():
        if path.endswith(".py"):
            try:
                ast.parse(content)
                safe[path] = content
            except SyntaxError as exc:
                _log.warning(
                    "_run_helper_with_safety.rollback",
                    helper=getattr(helper_fn, "__name__", str(helper_fn)),
                    path=path, error=str(exc),
                )
        elif path.endswith((".jsx", ".tsx", ".js", ".ts")):
            if not _brackets_balance(content):
                _log.warning(
                    "_run_helper_with_safety.rollback",
                    helper=getattr(helper_fn, "__name__", str(helper_fn)),
                    path=path, error="unbalanced_brackets",
                )
            elif path == "frontend/src/App.jsx":
                # Extra guard: App.jsx must never gain duplicate top-level
                # declarations (e.g. two `function Layout()` from two helpers
                # both injecting the scaffold in the same cycle).
                dups = _detect_duplicate_top_level_decls(content)
                if dups:
                    _log.error(
                        "_run_helper_with_safety.rollback_app_jsx_duplicates",
                        helper=getattr(helper_fn, "__name__", str(helper_fn)),
                        path=path, duplicates=dups,
                    )
                    # Exclude from safe — original is preserved
                else:
                    safe[path] = content
            else:
                safe[path] = content
        else:
            safe[path] = content
    return safe


_ROUTE_LABEL_OVERRIDES: dict[str, str] = {
    "loginpage": "Sign in",    "login": "Sign in",
    "signinpage": "Sign in",   "signin": "Sign in",
    "registerpage": "Sign up", "register": "Sign up",
    "signuppage": "Sign up",   "signup": "Sign up",
    "homepage": "Home",        "home": "Home",
    "indexpage": "Home",       "landingpage": "Home",   "landing": "Home",
    "aboutpage": "About",      "about": "About",
    "contactpage": "Contact",  "contact": "Contact",
    "menupage": "Menu",        "menu": "Menu",
    "cartpage": "Cart",        "cart": "Cart",
    "checkoutpage": "Checkout","checkout": "Checkout",
    "orderspage": "Orders",    "orders": "Orders",
    "profilepage": "Profile",  "profile": "Profile",
    "settingspage": "Settings","settings": "Settings",
    "dashboardpage": "Dashboard", "dashboard": "Dashboard",
    "adminpage": "Admin",      "admin": "Admin",
}


def _infer_route_requires(path: str) -> str | None:
    """Infer the ``requires`` field for a route path.

    Heuristic table:
      /login /register /signin /signup /forgot*  → "guest"
      /admin  /admin/*                           → "admin"
      /account /profile /orders /me /settings   → "auth"
      everything else                            → None  (public)
    """
    seg = path.strip("/").split("/")[0].lower()
    if seg in ("login", "register", "signin", "signup") or seg.startswith("forgot"):
        return "guest"
    if seg == "admin" or path.startswith("/admin/"):
        return "admin"
    if seg in ("account", "profile", "orders", "me", "settings"):
        return "auth"
    return None


def _infer_route_label(comp: str) -> str:
    """Derive a human-readable nav label from a React component name."""
    lower = comp.lower()
    if lower in _ROUTE_LABEL_OVERRIDES:
        return _ROUTE_LABEL_OVERRIDES[lower]
    name = re.sub(r"(Page|Screen|View|Container)$", "", comp)
    words = re.findall(r"[A-Z][a-z0-9]*", name)
    return " ".join(words) if words else name


def _build_routes_js_content(entries: list[dict]) -> str:
    """Emit a complete routes.js with ROUTES array + filtered nav exports.

    Every entry gets an explicit ``show_in_nav`` field (defaults to
    ``_infer_show_in_nav(path)`` when not provided).  The filter exports
    use ``r.show_in_nav && ...`` so Navbar links respect the flag.
    """
    lines = ["export const ROUTES = ["]
    for entry in entries:
        path_val = entry["path"]
        label = entry.get("label") or path_val.strip("/").capitalize() or "Home"
        requires = entry.get("requires")
        # Default show_in_nav from path heuristics when not explicitly set
        show_in_nav = entry.get("show_in_nav")
        if show_in_nav is None:
            show_in_nav = _infer_show_in_nav(path_val)
            if requires == "admin":
                show_in_nav = False
        req_str = "null" if requires is None else f'"{requires}"'
        parts = [
            f'    path: "{path_val}"',
            f'    label: "{label}"',
            f'    requires: {req_str}',
            f'    show_in_nav: {str(show_in_nav).lower()}',
        ]
        lines.append("  {")
        for part in parts:
            lines.append(part + ",")
        lines.append("  },")
    lines += [
        "]",
        "",
        'export const PUBLIC_NAV = ROUTES.filter(r => r.show_in_nav && r.requires === null)',
        'export const GUEST_NAV = ROUTES.filter(r => r.show_in_nav && r.requires === "guest")',
        'export const AUTH_NAV = ROUTES.filter(r => r.show_in_nav && r.requires === "auth")',
        'export const ADMIN_NAV = ROUTES.filter(r => r.show_in_nav && r.requires === "admin")',
        "",
    ]
    return "\n".join(lines)


def _rebuild_navbar_scaffold(content: str) -> str:
    """Rewrite a Navbar that uses hardcoded <Link> tags to the auth-aware
    route-map scaffold pattern.

    Preserves: outer nav/header tag + className, brand logo link, other imports.
    Replaces: hardcoded navigation links with links.map(...) iteration.
    Adds: @/lib/routes and @/contexts/AuthContext imports.
    """
    # Preserve imports we don't own
    keep_imports: list[str] = []
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped.startswith("import "):
            continue
        if "@/lib/routes" in stripped or "@/contexts/AuthContext" in stripped:
            continue
        keep_imports.append(line)

    has_link_import = any(
        "Link" in ln and "react-router-dom" in ln for ln in keep_imports
    )
    if not has_link_import:
        keep_imports.insert(0, 'import { Link } from "react-router-dom"')

    # Outer nav tag + className
    nav_tag_m = re.search(r'<(nav|header)\b[^>]*className="([^"]*)"', content)
    if nav_tag_m:
        nav_tag, nav_class = nav_tag_m.group(1), nav_tag_m.group(2)
    else:
        nav_tag = "nav"
        nav_class = "bg-white shadow-sm px-4 py-3 flex items-center justify-between"

    # Brand/logo link (to="/")
    brand_m = re.search(
        r'<Link\s+[^>]*to\s*=\s*["\'][/]["\'][^>]*>([\s\S]*?)</Link>',
        content,
    )
    brand_inner = brand_m.group(1).strip() if brand_m else "Brand"
    brand_class_m = re.search(
        r'<Link\s+[^>]*to\s*=\s*["\'][/]["\']\s+className="([^"]+)"', content,
    )
    brand_class = brand_class_m.group(1) if brand_class_m else "font-bold text-xl"

    # Logout function name
    logout_m = re.search(
        r'const\s+\{[^}]*\b(logout|signOut|signout)\b', content, re.IGNORECASE
    )
    logout_fn = logout_m.group(1) if logout_m else "logout"

    imports_block = "\n".join(keep_imports)
    return (
        imports_block + "\n"
        'import { PUBLIC_NAV, GUEST_NAV, AUTH_NAV, ADMIN_NAV } from "@/lib/routes"\n'
        'import { useAuth } from "@/contexts/AuthContext"\n'
        "\n"
        "export default function Navbar() {\n"
        f"  const {{ user, {logout_fn} }} = useAuth()\n"
        '  const isAdmin = user?.role === "admin"\n'
        "  const links = [\n"
        "    ...PUBLIC_NAV,\n"
        "    ...(user ? AUTH_NAV : GUEST_NAV),\n"
        "    ...(isAdmin ? ADMIN_NAV : []),\n"
        "  ]\n"
        "\n"
        "  return (\n"
        f'    <{nav_tag} className="{nav_class}">\n'
        f'      <Link to="/" className="{brand_class}">{brand_inner}</Link>\n'
        '      <div className="flex items-center gap-4">\n'
        "        {links.map(route => (\n"
        '          <Link key={route.path} to={route.path} className="hover:opacity-80">\n'
        "            {route.label}\n"
        "          </Link>\n"
        "        ))}\n"
        "        {user && (\n"
        f"          <button onClick={{{logout_fn}}} className=\"text-sm hover:opacity-80\">\n"
        "            Sign out\n"
        "          </button>\n"
        "        )}\n"
        "      </div>\n"
        f"    </{nav_tag}>\n"
        "  )\n"
        "}\n"
    )


def _infer_show_in_nav(path: str) -> bool:
    """True if this route should appear in the navbar by default.

    Hidden: wildcard/catch-all, admin pages, :id / {id} detail routes,
    sub-action paths (new/edit/create/add), and OAuth callback pages.
    """
    # Wildcard catch-all is the NotFound page — never shown in nav and would
    # also trigger a ping-pong with _strip_notfound_from_navbar.
    if path.strip() in ("*", "/*"):
        return False
    segs = [s for s in path.strip("/").split("/") if s]
    if not segs:
        return True  # root "/"
    if any(s.startswith(":") or s.startswith("{") for s in segs):
        return False  # parametrised detail page
    if segs[0] == "admin":
        return False  # admin pages hidden from public nav
    if len(segs) > 1 and segs[-1] in ("new", "edit", "create", "add", "delete", "callback"):
        return False  # sub-action page
    if "callback" in segs:
        return False
    return True


# Auth slugs that belong in BareLayout (no Navbar)
_BARE_LAYOUT_SLUGS: frozenset[str] = frozenset({
    "login", "register", "signin", "signup",
    "forgot-password", "reset-password", "auth", "verify",
})

_LAYOUT_FN_BLOCK = """\
function Layout() {
  return (
    <div className="min-h-screen flex flex-col bg-surface-page text-text-default">
      <Navbar />
      <main className="flex-1"><Outlet /></main>
      <Footer />
    </div>
  )
}

function BareLayout() {
  return (
    <div className="min-h-screen bg-surface-page text-text-default">
      <Outlet />
    </div>
  )
}

"""


def _collect_and_wrap_routes(content: str) -> str | None:
    """Find top-level <Route path=...> elements inside <Routes>, classify them
    into Layout vs BareLayout groups, and return the updated content.

    Returns None when no top-level routes are found (nothing to do).
    Never touches Layout/BareLayout function declarations.
    """
    routes_m = re.search(r'(<Routes>)([\s\S]*?)(</Routes>)', content)
    if not routes_m:
        return None
    routes_inner = routes_m.group(2)
    # Use .* (not [^>]*) so element={<Comp />} inside the tag doesn't stop match
    top_level = re.findall(
        r'^\s*<Route\s.*\bpath=["\'][^"\']+["\'].*/>[ \t]*$',
        routes_inner, re.MULTILINE,
    )
    if not top_level:
        return None
    layout_routes: list[str] = []
    bare_routes: list[str] = []
    for line in top_level:
        pm = re.search(r'path=["\']([^"\']+)["\']', line)
        if not pm:
            continue
        slug = pm.group(1).strip("/").split("/")[0]
        if slug in _BARE_LAYOUT_SLUGS:
            bare_routes.append("              " + line.strip())
        else:
            layout_routes.append("              " + line.strip())
    new_inner = "\n"
    if layout_routes:
        new_inner += (
            "            <Route element={<Layout />}>\n"
            + "\n".join(layout_routes) + "\n"
            + "            </Route>\n"
        )
    if bare_routes:
        new_inner += (
            "            <Route element={<BareLayout />}>\n"
            + "\n".join(bare_routes) + "\n"
            + "            </Route>\n"
        )
    new_inner += "          "
    return (
        content[: routes_m.start(2)]
        + new_inner
        + content[routes_m.end(2):]
    )


def ensure_layout_groups(content: str) -> str:
    """Move top-level <Route path=...> elements into Layout/BareLayout groups.

    Layout/BareLayout function injection is the EXCLUSIVE responsibility of
    ``_fix_app_jsx_layout_pattern``.  This helper only manages the <Route>
    wrapper grouping inside an already-present <Routes> block.

    Cases handled:
      A. Both functions AND ``<Route element={<Layout />}>`` exist → noop.
      B. Both functions exist, routes are top-level (no wrapper) →
         wrap routes in Layout/BareLayout groups; never touch declarations.
      C. Layout/BareLayout functions are absent → noop; log a warning.
         ``_fix_app_jsx_layout_pattern`` must run first to create them.
      D. Partial state → noop (log warning, skip to avoid corruption).
    """
    _log = structlog.get_logger("debugger")
    has_layout, has_bare = _app_jsx_has_layout(content)
    has_wrap = bool(re.search(r'<Route\s+element=\{<Layout\s*/>\}\s*>', content))

    # Case A: fully configured — nothing to do
    if has_layout and has_bare and has_wrap:
        return content

    # Case B: functions present, wrapper missing → wrap only, never inject
    if has_layout and has_bare and not has_wrap:
        wrapped = _collect_and_wrap_routes(content)
        return wrapped if wrapped is not None else content

    # Cases C & D: functions absent or partial → skip; _fix_app_jsx_layout_pattern
    # is responsible for injecting them and runs before this helper.
    _log.warning(
        "ensure_layout_groups.no_layout_functions_skipping",
        has_layout=has_layout, has_bare_layout=has_bare,
        reason="fix_app_jsx_layout_pattern must run first",
    )
    return content


# Fields that must be correct for each nav-critical path.
# Values are JS literals (strings already include quotes for string values).
_NAV_ROUTE_CORRECTIONS: dict[str, dict[str, str]] = {
    "/login":           {"requires": '"guest"', "show_in_nav": "true",  "label": '"Sign in"'},
    "/register":        {"requires": '"guest"', "show_in_nav": "true",  "label": '"Sign up"'},
    "/forgot-password": {"requires": '"guest"', "show_in_nav": "true"},
    "/reset-password":  {"requires": '"guest"', "show_in_nav": "true"},
    "/dashboard":       {"requires": '"auth"',  "show_in_nav": "true"},
    "/profile":         {"requires": '"auth"',  "show_in_nav": "true"},
    "/orders":          {"requires": '"auth"',  "show_in_nav": "true"},
}


def _ensure_guest_routes_visible(routes_js: str) -> str:
    """Correct show_in_nav and requires fields for guest/auth nav-critical routes.

    Guest routes (/login, /register, /forgot-password, /reset-password):
        requires → "guest", show_in_nav → true, label corrected if present.
    Auth routes (/dashboard, /profile, /orders):
        requires → "auth", show_in_nav → true.

    Uses brace-depth scanning so path order inside the entry doesn't matter.
    Idempotent: entries already correct are untouched.
    """
    new = routes_js
    for path, corrections in _NAV_ROUTE_CORRECTIONS.items():
        pm = re.search(r'path\s*:\s*["\']' + re.escape(path) + r'["\']', new)
        if not pm:
            continue
        # Locate the surrounding { } block via brace-depth scan
        open_brace = new.rfind("{", 0, pm.start())
        if open_brace == -1:
            continue
        depth = 0
        i = open_brace
        while i < len(new):
            if new[i] == "{":
                depth += 1
            elif new[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        if depth != 0:
            continue
        close_brace = i
        entry = new[open_brace : close_brace + 1]
        updated = entry

        for field, value in corrections.items():
            if field == "label":
                # Only rename when a label field is already present
                if "label:" not in updated:
                    continue
                updated = re.sub(r'label\s*:\s*"[^"]*"', f"label: {value}", updated)
            elif re.search(rf'\b{re.escape(field)}\s*:', updated):
                # Field present — update its value
                if field == "requires":
                    updated = re.sub(
                        r'requires\s*:\s*(?:null|"[^"]*")',
                        f"requires: {value}", updated,
                    )
                elif field == "show_in_nav":
                    updated = re.sub(
                        r'show_in_nav\s*:\s*(?:true|false)',
                        f"show_in_nav: {value}", updated,
                    )
            else:
                # Field absent — insert before closing }
                close_pos = updated.rfind("}")
                before = updated[:close_pos].rstrip()
                if not before.endswith(","):
                    before += ","
                # Detect indent from existing fields (default to 4 spaces)
                indent = "    "
                for ln in updated.split("\n")[1:]:
                    if ln.strip():
                        m_indent = re.match(r"^(\s+)", ln)
                        if m_indent:
                            indent = m_indent.group(1)
                        break
                is_multiline = "\n" in before
                if is_multiline:
                    close_indent = indent[: max(0, len(indent) - 2)]
                    updated = before + f"\n{indent}{field}: {value},\n{close_indent}}}"
                else:
                    updated = before + f" {field}: {value} }}"

        if updated != entry:
            new = new[:open_brace] + updated + new[close_brace + 1:]

    return new


def _fix_routes_js_show_in_nav(routes_js: str) -> str:
    """If routes.js has show_in_nav fields but NO entry has show_in_nav: true,
    promote eligible routes (non-admin, non-detail) to show_in_nav: true.

    Idempotent: returns unchanged when at least one true entry exists.
    """
    true_count = len(re.findall(r'\bshow_in_nav\s*:\s*true\b', routes_js))
    false_count = len(re.findall(r'\bshow_in_nav\s*:\s*false\b', routes_js))
    if true_count > 0 or false_count == 0:
        return routes_js  # already has visible entries, or no explicit flags

    def _maybe_flip(m: re.Match) -> str:
        entry = m.group(0)
        pm = re.search(r'path\s*:\s*["\']([^"\']+)["\']', entry)
        if pm and _infer_show_in_nav(pm.group(1)):
            return entry.replace('show_in_nav: false', 'show_in_nav: true')
        return entry

    return re.sub(r'\{[^{}]+\}', _maybe_flip, routes_js)


def _infer_page_component_for_path(path_str: str, generated_files: dict) -> str | None:
    """Map a route path to a page component name by scanning generated files.

    Priority order:
      1. Exact match on candidate names in generated_files.
      2. Loose match: any page file whose stripped name equals the first segment.
    """
    if path_str == "/":
        candidates = ["HomePage", "DashboardPage", "MenuPage", "TasksPage", "LandingPage", "IndexPage"]
    else:
        segs = [
            s for s in path_str.strip("/").split("/")
            if s and not s.startswith(":") and not s.startswith("{")
        ]
        if not segs:
            return None
        base_pascal = "".join(w.capitalize() for w in re.split(r"[-_]", segs[0]))
        full_pascal = "".join(
            "".join(w.capitalize() for w in re.split(r"[-_]", s)) for s in segs
        )
        candidates = [full_pascal + "Page", base_pascal + "Page"]
        has_param = any(
            s.startswith(":") or s.startswith("{") for s in path_str.split("/")
        )
        has_new = any(s in ("new", "create", "add") for s in segs)
        if has_new:
            rsc = "".join(w.capitalize() for w in re.split(r"[-_]", segs[0].rstrip("s")))
            candidates += [f"Create{rsc}Page", f"New{rsc}Page", f"Add{rsc}Page"]
        if has_param:
            rsc = "".join(w.capitalize() for w in re.split(r"[-_]", segs[0].rstrip("s")))
            candidates += [f"{rsc}DetailPage", f"{rsc}Page"]

    for c in candidates:
        if f"frontend/src/pages/{c}.jsx" in generated_files:
            return c

    # Loose fallback: match by first path segment
    if path_str != "/":
        seg_lower = path_str.strip("/").split("/")[0].lower().replace("-", "").replace("_", "")
        for fp in generated_files:
            if not fp.startswith("frontend/src/pages/") or not fp.endswith(".jsx"):
                continue
            stem = fp.rsplit("/", 1)[-1][:-4]  # e.g. "MenuPage"
            stem_base = re.sub(r"(page|screen|view)$", "", stem.lower())
            if stem_base == seg_lower or stem.lower() == seg_lower:
                return stem
    return None


def _insert_route_in_app_jsx_group(
    content: str, group_name: str, route_lines: list[str]
) -> str:
    """Insert route lines inside the <Route element={<GroupName />}> block.

    Falls back to inserting before </Routes> when no matching layout group exists.
    """
    pattern = (
        rf'(<Route\s+element=\{{<{re.escape(group_name)}\s*/>\}}>)'
        rf'([\s\S]*?)'
        rf'(</Route>)'
    )
    m = re.search(pattern, content)
    if not m:
        insert_at = content.rfind("</Routes>")
        if insert_at == -1:
            return content
        return content[:insert_at] + "\n" + "\n".join(route_lines) + "\n      " + content[insert_at:]
    inner = m.group(2).rstrip() + "\n" + "\n".join(route_lines) + "\n      "
    return content[: m.start(2)] + inner + content[m.end(2):]


def _fix_routes_config_consistency(test_results: dict, generated_files: dict) -> dict:
    """Reconcile App.jsx route declarations with routes.js ROUTES array and Navbar.jsx.

    BIDIRECTIONAL reconciliation:
      A. routes.js → App.jsx: when a routes.js entry has no matching <Route> in
         App.jsx but a page file exists, mount it and add the import.
      B. App.jsx → routes.js: when a <Route> has no routes.js entry, add one with
         heuristic requires/label.
    Also:
      4. Ensure routes.js has PUBLIC_NAV / GUEST_NAV / AUTH_NAV / ADMIN_NAV exports.
      5. If Navbar.jsx uses hardcoded <Link> tags, rewrite to the scaffold pattern.

    Idempotent: returns {} when all three files already agree.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict[str, str] = {}

    app_jsx = generated_files.get("frontend/src/App.jsx", "")
    if not app_jsx:
        return {}

    # ── 0. Ensure Layout/BareLayout grouping structure ────────────────────────
    app_jsx_ensured = ensure_layout_groups(app_jsx)
    if app_jsx_ensured != app_jsx:
        fixes["frontend/src/App.jsx"] = app_jsx_ensured
        app_jsx = app_jsx_ensured
        _log.info("fix_routes_config.layout_groups_restored")

    # ── 1. Parse App.jsx routes (line-by-line — generated routes are single-line) ──
    app_route_entries: list[tuple[str, str]] = []   # (path, component_name)
    app_paths: set[str] = set()

    for line in app_jsx.split("\n"):
        if "<Route" not in line:
            continue
        path_m = re.search(r'path=["\']([^"\']+)["\']', line)
        elem_m = re.search(r'element=\{<(\w+)', line)
        if path_m and elem_m:
            pv, comp = path_m.group(1), elem_m.group(1)
            if pv not in app_paths:
                app_paths.add(pv)
                app_route_entries.append((pv, comp))

    if not app_route_entries:
        return {}

    # ── 2. Parse routes.js ────────────────────────────────────────────────────
    routes_js = generated_files.get("frontend/src/lib/routes.js", "")
    routes_js_paths: set[str] = set()
    routes_js_entries: list[dict] = []

    if routes_js:
        body_m = re.search(
            r'export\s+const\s+ROUTES\s*=\s*\[([\s\S]*?)\]', routes_js
        )
        if body_m:
            for obj_m in re.finditer(r'\{([^{}]+)\}', body_m.group(1)):
                obj_txt = obj_m.group(1)
                p = re.search(r'path\s*:\s*["\']([^"\']+)["\']', obj_txt)
                l = re.search(r'label\s*:\s*["\']([^"\']+)["\']', obj_txt)
                r = re.search(
                    r'requires\s*:\s*(null|["\']([^"\']*)["\'])', obj_txt
                )
                if not p:
                    continue
                pv = p.group(1)
                routes_js_paths.add(pv)
                req = None if (not r or r.group(1) == "null") else r.group(2)
                # Parse show_in_nav if present
                sn_m = re.search(r'show_in_nav\s*:\s*(true|false)', obj_txt)
                sn = (sn_m.group(1) == "true") if sn_m else None
                routes_js_entries.append({
                    "path": pv,
                    "label": l.group(1) if l else None,
                    "requires": req,
                    "show_in_nav": sn,
                })

    # ── 3. Mount routes.js entries that are missing from App.jsx ─────────────
    # (Bidirectional fix: routes.js → App.jsx direction)
    app_jsx_working = app_jsx  # accumulates changes to App.jsx

    for entry in routes_js_entries:
        if entry["path"] in app_paths:
            continue
        path_val = entry["path"]
        requires = entry.get("requires")

        page_comp = _infer_page_component_for_path(path_val, generated_files)
        if not page_comp:
            _log.warning(
                "fix_routes_config.no_page_for_orphan",
                path=path_val,
                reason="in routes.js but not in App.jsx — no matching page file found",
            )
            continue

        # Add import if missing
        if f'"@/pages/{page_comp}"' not in app_jsx_working and f"'@/pages/{page_comp}'" not in app_jsx_working:
            import_line = f'import {page_comp} from "@/pages/{page_comp}"'
            import_end = max(
                (m.end() for m in re.finditer(r"^import\s+[^\n]+$", app_jsx_working, re.MULTILINE)),
                default=0,
            )
            app_jsx_working = (
                app_jsx_working[:import_end]
                + "\n" + import_line
                + app_jsx_working[import_end:]
            )

        # Decide layout group: guest auth routes go in BareLayout
        use_bare = requires == "guest" or path_val in (
            "/login", "/register", "/forgot-password", "/reset-password",
        )
        group = "BareLayout" if use_bare else "Layout"
        route_jsx = f'        <Route path="{path_val}" element={{<{page_comp} />}} />'
        app_jsx_working = _insert_route_in_app_jsx_group(
            app_jsx_working, group, [route_jsx]
        )
        app_paths.add(path_val)  # prevent duplicate mounts on next iteration
        _log.info(
            "fix_routes_config.mounted_missing_route",
            path=path_val, component=page_comp, group=group,
        )

    if app_jsx_working != app_jsx:
        fixes["frontend/src/App.jsx"] = app_jsx_working
        # Use the updated content for step 4 so we don't re-add newly mounted paths
        app_jsx = app_jsx_working

    # ── 4. Find paths in App.jsx missing from routes.js ───────────────────────
    missing: list[tuple[str, str]] = [
        (pv, comp)
        for pv, comp in app_route_entries
        if pv not in routes_js_paths
    ]

    new_routes_js = routes_js  # may be updated below

    if missing:
        new_entry_strs: list[str] = []
        for pv, comp in missing:
            if pv.strip() in ("*", "/*"):
                continue  # catch-all never belongs in routes.js config
            requires = _infer_route_requires(pv)
            label = _infer_route_label(comp)
            show_in_nav = _infer_show_in_nav(pv) and requires != "admin"
            req_str = "null" if requires is None else f'"{requires}"'
            parts = [
                f'    path: "{pv}"',
                f'    label: "{label}"',
                f'    requires: {req_str}',
                f'    show_in_nav: {str(show_in_nav).lower()}',
            ]
            new_entry_strs.append("  {\n" + ",\n".join(parts) + ",\n  }")
            _log.info(
                "fix_routes_config.added",
                path=pv, component=comp, label=label, requires=requires,
                show_in_nav=show_in_nav,
            )

        if routes_js:
            arr_m = re.search(
                r'(export\s+const\s+ROUTES\s*=\s*\[)([\s\S]*?)(\])',
                routes_js,
            )
            if arr_m:
                body = arr_m.group(2).rstrip()
                if body and not body.endswith(","):
                    body += ","
                new_body = body + "\n" + ",\n".join(new_entry_strs) + ",\n"
                new_routes_js = (
                    routes_js[: arr_m.start(2)]
                    + new_body
                    + routes_js[arr_m.end(2):]
                )
            else:
                all_entries = [
                    {
                        "path": pv,
                        "label": _infer_route_label(c),
                        "requires": _infer_route_requires(pv),
                        "show_in_nav": False if _infer_route_requires(pv) == "admin" else None,
                    }
                    for pv, c in app_route_entries
                ]
                new_routes_js = _build_routes_js_content(all_entries)
        else:
            all_entries = [
                {
                    "path": pv,
                    "label": _infer_route_label(c),
                    "requires": _infer_route_requires(pv),
                    "show_in_nav": False if _infer_route_requires(pv) == "admin" else None,
                }
                for pv, c in app_route_entries
            ]
            new_routes_js = _build_routes_js_content(all_entries)

    # Ensure nav-filter exports exist (use show_in_nav && for proper filtering)
    if new_routes_js and "PUBLIC_NAV" not in new_routes_js:
        new_routes_js = new_routes_js.rstrip() + "\n\n" + "\n".join([
            'export const PUBLIC_NAV = ROUTES.filter(r => r.show_in_nav && r.requires === null)',
            'export const GUEST_NAV = ROUTES.filter(r => r.show_in_nav && r.requires === "guest")',
            'export const AUTH_NAV = ROUTES.filter(r => r.show_in_nav && r.requires === "auth")',
            'export const ADMIN_NAV = ROUTES.filter(r => r.show_in_nav && r.requires === "admin")',
            "",
        ])

    # ── 6. Audit show_in_nav visibility ──────────────────────────────────────
    # If routes.js has show_in_nav fields but NONE are true, the Navbar renders
    # empty even though the app has pages.  Promote safe defaults.
    if new_routes_js and "show_in_nav" in new_routes_js:
        audited = _fix_routes_js_show_in_nav(new_routes_js)
        if audited != new_routes_js:
            new_routes_js = audited
            _log.info(
                "fix_routes_config.visibility_promoted",
                reason="all show_in_nav were false — promoted eligible routes to true",
            )

    # ── 7. Ensure guest/auth nav-critical routes have correct fields ──────────
    # Login/Register must have requires:"guest" + show_in_nav:true so they
    # appear in GUEST_NAV for logged-out visitors.  Dashboard/Profile/Orders
    # must have requires:"auth" so they appear in AUTH_NAV when logged in.
    if new_routes_js:
        corrected = _ensure_guest_routes_visible(new_routes_js)
        if corrected != new_routes_js:
            new_routes_js = corrected
            _log.info(
                "fix_routes_config.guest_routes_corrected",
                reason="corrected requires/show_in_nav on guest or auth nav routes",
            )

    if new_routes_js != routes_js:
        fixes["frontend/src/lib/routes.js"] = new_routes_js

    # ── 5. Fix Navbar.jsx if hardcoded ────────────────────────────────────────
    navbar = generated_files.get("frontend/src/components/Navbar.jsx", "")
    if navbar:
        uses_scaffold = bool(re.search(
            r'(?:PUBLIC_NAV|GUEST_NAV|AUTH_NAV|ADMIN_NAV)', navbar,
        ))
        # Any <Link to="/..."> with a path longer than "/" is a hardcoded nav link
        has_hardcoded = bool(re.search(
            r'<Link\s+[^>]*to\s*=\s*["\'][^"\']{2,}["\']', navbar,
        ))
        if has_hardcoded and not uses_scaffold:
            new_navbar = _rebuild_navbar_scaffold(navbar)
            if new_navbar != navbar:
                fixes["frontend/src/components/Navbar.jsx"] = new_navbar
                _log.info(
                    "fix_routes_config.navbar_rewritten",
                    reason="replaced hardcoded Link tags with auth-aware .map iteration",
                )
        elif uses_scaffold:
            # Already uses map pattern — ensure required imports are present
            new_navbar = navbar
            changed = False
            for imp_line, marker in [
                (
                    'import { PUBLIC_NAV, GUEST_NAV, AUTH_NAV, ADMIN_NAV } from "@/lib/routes"',
                    "@/lib/routes",
                ),
                (
                    'import { useAuth } from "@/contexts/AuthContext"',
                    "@/contexts/AuthContext",
                ),
            ]:
                if marker not in new_navbar:
                    lines = new_navbar.split("\n")
                    last_import = max(
                        (i for i, ln in enumerate(lines) if ln.strip().startswith("import ")),
                        default=-1,
                    )
                    lines.insert(last_import + 1, imp_line)
                    new_navbar = "\n".join(lines)
                    changed = True
            if changed and new_navbar != navbar:
                fixes["frontend/src/components/Navbar.jsx"] = new_navbar
                _log.info(
                    "fix_routes_config.navbar_imports_fixed",
                    reason="added missing imports for scaffold map pattern",
                )

    return fixes


def _fix_routes_config_consistency_safe(
    test_results: dict, generated_files: dict
) -> dict:
    return _run_helper_with_safety(
        _fix_routes_config_consistency, test_results, generated_files
    )


def _find_page_file(generated_files: dict, component_name: str) -> str | None:
    """Locate the page file whose default export matches component_name."""
    import re
    candidates = [
        f"frontend/src/pages/{component_name}.jsx",
        f"frontend/src/pages/{component_name}.tsx",
    ]
    for c in candidates:
        if c in generated_files:
            return c
    pat = re.compile(
        rf"export\s+default\s+{re.escape(component_name)}\b"
        rf"|export\s+default\s+function\s+{re.escape(component_name)}\b",
    )
    for path, content in generated_files.items():
        if not path.startswith("frontend/src/pages/"):
            continue
        if not path.endswith((".jsx", ".tsx")):
            continue
        if pat.search(content):
            return path
    return None


def _fix_use_params_name_mismatch(
    test_results: dict, generated_files: dict,
) -> dict:
    """Detect <Route path="/x/:paramName" element={<Page />} /> and verify
    the matching page component destructures the same key from useParams().
    If the destructured key differs, rewrite the page's destructuring to
    match the route param name. Route is the source of truth.
    """
    import re
    _log = structlog.get_logger("debugger")
    fixes: dict[str, str] = {}

    app_path = "frontend/src/App.jsx"
    app_content = generated_files.get(app_path, "")
    if not app_content:
        return fixes

    route_re = re.compile(
        r'<Route\s+[^>]*path\s*=\s*["\']([^"\']+)["\'][^>]*'
        r'element\s*=\s*\{\s*<\s*(\w+)\s*/?\s*>\s*\}',
    )
    route_re_alt = re.compile(
        r'<Route\s+[^>]*element\s*=\s*\{\s*<\s*(\w+)\s*/?\s*>\s*\}'
        r'[^>]*path\s*=\s*["\']([^"\']+)["\']',
    )

    route_map: list[tuple[str, str]] = []
    for m in route_re.finditer(app_content):
        route_map.append((m.group(1), m.group(2)))
    for m in route_re_alt.finditer(app_content):
        route_map.append((m.group(2), m.group(1)))

    param_re = re.compile(r":([a-zA-Z_]\w*)")

    for path, component in route_map:
        route_params = param_re.findall(path)
        if not route_params:
            continue
        page_file = _find_page_file(generated_files, component)
        if not page_file:
            continue
        content = generated_files.get(page_file, "")
        if not content:
            continue

        destruct_re = re.compile(
            r"const\s*\{\s*([^}]+?)\s*\}\s*=\s*useParams\s*\(\s*\)",
        )
        destruct_match = destruct_re.search(content)
        if not destruct_match:
            continue

        inside = destruct_match.group(1)
        existing_keys = set()
        existing_locals: dict[str, str] = {}
        for entry in [e.strip() for e in inside.split(",") if e.strip()]:
            if ":" in entry:
                key, local = [p.strip() for p in entry.split(":", 1)]
            else:
                key = local = entry.strip()
            existing_keys.add(key)
            existing_locals[key] = local

        missing = [p for p in route_params if p not in existing_keys]
        if not missing:
            continue

        # Use dict insertion order (not set order) so positional mapping is stable
        wrong_keys = [k for k in existing_locals if k not in route_params]
        consumed_wrong: set[str] = set()

        new_destructs = []
        for k in route_params:
            if wrong_keys:
                wk = wrong_keys.pop(0)
                local = existing_locals.get(wk, wk)
                new_destructs.append(f"{k}: {local}")
                consumed_wrong.add(wk)
            else:
                new_destructs.append(k)
        for k, local in existing_locals.items():
            if k in route_params or k in consumed_wrong:
                continue
            entry = f"{k}: {local}" if k != local else k
            if entry not in new_destructs:
                new_destructs.append(entry)

        new_inside = ", ".join(new_destructs)
        new_content = (
            content[:destruct_match.start()]
            + f"const {{ {new_inside} }} = useParams()"
            + content[destruct_match.end():]
        )

        if new_content != content:
            fixes[page_file] = new_content
            _log.info(
                "fix_use_params_name_mismatch.applied",
                page=page_file,
                route=path,
                route_params=route_params,
                page_had=list(existing_keys),
            )
    return fixes


def _fix_bad_module_imports(
    test_results: dict, generated_files: dict,
) -> dict:
    """Detect `from app.X import Y` where app.X doesn't exist as a module.
    If Y is a known model class (defined in app.models), rewrite the import
    to `from app.models import Y`.
    """
    import ast
    import re
    _log = structlog.get_logger("debugger")
    fixes: dict[str, str] = {}

    file_modules: set[str] = set()
    known_models: set[str] = set()
    for path, content in generated_files.items():
        if path.startswith("backend/app/") and path.endswith(".py"):
            parts = path[len("backend/"):].rsplit(".py", 1)[0].split("/")
            file_modules.add(".".join(parts))
        if path == "backend/app/models.py" and content:
            try:
                tree = ast.parse(content)
                for node in tree.body:
                    if isinstance(node, ast.ClassDef):
                        known_models.add(node.name)
            except SyntaxError:
                pass

    _KNOWN_OK = {
        "app.database", "app.models", "app.schemas", "app.auth",
        "app.seed", "app.main",
    }

    _bad_import_re = re.compile(
        r"^from\s+(app\.[\w\.]+)\s+import\s+([^\n]+)$",
        re.MULTILINE,
    )

    for path, content in list(generated_files.items()):
        if not path.startswith("backend/app/"):
            continue
        if not path.endswith(".py") or not content:
            continue
        new_content = content
        changed = False

        for m in list(_bad_import_re.finditer(content)):
            mod = m.group(1)
            names_str = m.group(2).strip().rstrip(",")
            if mod in _KNOWN_OK or mod in file_modules:
                continue
            names = [n.strip() for n in names_str.split(",") if n.strip()]
            model_names = [n for n in names if n in known_models]
            other_names = [n for n in names if n not in known_models]
            if not model_names:
                continue
            replacement_lines = [
                f"from app.models import {', '.join(model_names)}"
            ]
            if other_names:
                replacement_lines.append(
                    f"# TODO: unresolved import from {mod}: {other_names}"
                )
            replacement = "\n".join(replacement_lines)
            new_content = new_content.replace(m.group(0), replacement, 1)
            changed = True
            _log.info(
                "fix_bad_module_imports.rewrote",
                file=path, was=mod, now="app.models",
                names=model_names,
            )

        if changed:
            fixes[path] = new_content
    return fixes


def _strip_function_definition(content: str, func_name: str) -> str:
    """Remove a top-level function definition by name, including its
    decorators and body, via AST-based line range deletion.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return content
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != func_name:
            continue
        lines = content.split("\n")
        start = node.lineno - 1
        for d in node.decorator_list:
            start = min(start, d.lineno - 1)
        end = node.end_lineno  # 1-indexed exclusive
        del lines[start:end]
        # Trim consecutive blank lines left at the removal point.
        while start < len(lines) and lines[start].strip() == "":
            if start > 0 and lines[start - 1].strip() == "":
                del lines[start]
                continue
            break
        return "\n".join(lines)
    return content


def _replace_jwt_encode_calls(content: str) -> str:
    """Replace each `jwt.encode(...)` expression with
    `create_access_token(user.id)` using paren-balancing.
    """
    result = []
    i = 0
    n = len(content)
    while i < n:
        idx = content.find("jwt.encode(", i)
        if idx == -1:
            result.append(content[i:])
            break
        result.append(content[i:idx])
        depth = 0
        j = idx + len("jwt.encode")
        while j < n:
            ch = content[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        result.append("create_access_token(user.id)")
        i = j
    return "".join(result)


def _fix_auth_scaffold_integrity(
    test_results: dict, generated_files: dict,
) -> dict:
    """Verify auth_routes.py defers token generation to the scaffold's
    app.auth.create_access_token. Catches three bypass patterns:
      1. Direct jwt.encode call inline
      2. Local `def create_access_token(...)` redefinition
      3. Local SECRET_KEY reassignment that differs from scaffold

    Idempotent: returns {} if the file already uses only the scaffold.
    """
    import re
    _log = structlog.get_logger("debugger")
    fixes: dict[str, str] = {}

    path = "backend/app/routes/auth_routes.py"
    content = generated_files.get(path, "")
    if not content:
        return fixes

    uses_jwt_encode = bool(re.search(r"\bjwt\.encode\s*\(", content))
    has_local_def = bool(re.compile(
        r"^def\s+create_access_token\s*\(", re.MULTILINE,
    ).search(content))
    has_local_secret = bool(re.compile(
        r"^SECRET_KEY\s*=\s*[\"']", re.MULTILINE,
    ).search(content))

    if not (uses_jwt_encode or has_local_def or has_local_secret):
        return fixes

    new_content = content

    # Ensure scaffold import is present.
    if "from app.auth import" in new_content:
        if "create_access_token" not in new_content.split(
            "from app.auth import", 1
        )[1].split("\n", 1)[0]:
            new_content = re.sub(
                r"(from app\.auth import )([^\n]+)",
                lambda m: (
                    m.group(0)
                    if "create_access_token" in m.group(2)
                    else f"{m.group(1)}{m.group(2).rstrip(', ')}, create_access_token"
                ),
                new_content, count=1,
            )
    else:
        new_content = re.sub(
            r"(\n(?:from\s+[\w\.]+\s+import[^\n]*|import\s+\w[^\n]*))",
            r"\1\nfrom app.auth import create_access_token",
            new_content, count=1,
        )

    # Strip local create_access_token redefinition.
    if has_local_def:
        new_content = _strip_function_definition(
            new_content, "create_access_token",
        )

    # Strip local SECRET_KEY.
    new_content = re.sub(
        r"^SECRET_KEY\s*=\s*[\"'][^\"']*[\"']\s*\n",
        "",
        new_content,
        flags=re.MULTILINE,
    )

    # Replace remaining jwt.encode calls.
    new_content = _replace_jwt_encode_calls(new_content)

    if new_content != content:
        fixes[path] = new_content
        _log.info(
            "fix_auth_scaffold_integrity.normalized",
            path=path,
            had_jwt_encode=uses_jwt_encode,
            had_local_def=has_local_def,
            had_local_secret=has_local_secret,
        )
    return fixes


def _fix_auth_scaffold_int_sub(
    test_results: dict, generated_files: dict,
) -> dict:
    """If backend/app/auth.py has the old `int(payload.get('sub'))`
    pattern, replace with the tolerant version that handles both int
    and UUID sub claims.
    """
    import re
    _log = structlog.get_logger("debugger")
    fixes: dict[str, str] = {}
    path = "backend/app/auth.py"
    content = generated_files.get(path, "")
    if not content:
        return fixes
    if 'int(payload.get("sub"))' not in content:
        return fixes
    new_content = content.replace(
        'user_id = int(payload.get("sub"))',
        'user_id_raw = payload.get("sub")\n'
        '        if user_id_raw is None:\n'
        '            raise ValueError("sub claim missing")\n'
        '        try:\n'
        '            user_id = int(user_id_raw)\n'
        '        except (ValueError, TypeError):\n'
        '            user_id = user_id_raw',
    )
    if new_content != content:
        fixes[path] = new_content
        _log.info("fix_auth_scaffold_int_sub.applied", path=path)
    return fixes


# ── Missing main.py recovery ─────────────────────────────────────────────────

_MAIN_PY_TEMPLATE_PATH: str = os.path.normpath(os.path.join(
    os.path.dirname(__file__),
    "..", "stack_templates", "python-postgres",
    "backend", "app", "main.py",
))

_MAIN_PY_SCAFFOLD_FALLBACK = """\
\"\"\"FastAPI app entry point.\"\"\"
import os
import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

log = structlog.get_logger()
_cors_origins = [
    o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()
]
_allow_localhost = os.environ.get("ALLOW_LOCALHOST_CORS", "true").lower() == "true"
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=(
        r"https?://(localhost|127\\.0\\.0\\.1)(:\\d+)?" if _allow_localhost else None
    ),
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}

from app.routes.auth_routes import router as auth_router
app.include_router(auth_router, prefix="/api")

# Project-specific route includes -- LLM appends here.
"""


_LIFESPAN_BLOCK = '''\
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(_app):
    try:
        from app.database import Base, engine
        Base.metadata.create_all(bind=engine)
        print("[startup] Tables ready.", flush=True)
    except Exception as _e:
        print(f"[startup] WARNING: Could not create tables: {_e}", flush=True)
    try:
        from app.seed import seed_demo_data
        from app.database import SessionLocal
        import inspect as _inspect
        _db = SessionLocal()
        try:
            if _inspect.signature(seed_demo_data).parameters:
                seed_demo_data(_db)
            else:
                seed_demo_data()
        finally:
            _db.close()
    except ImportError:
        pass
    except Exception as _e:
        print(f"[startup] WARNING: Could not seed demo data: {_e}", flush=True)
    yield

'''

# Sentinel string checked by _ensure_tolerant_lifespan to determine
# whether the lifespan block is already the tolerant inspect-based form.
_TOLERANT_LIFESPAN_SENTINEL = "_inspect.signature(seed_demo_data).parameters"


def _fix_missing_lifespan_in_main(
    test_results: dict, generated_files: dict
) -> dict:
    """Inject a lifespan handler into backend/app/main.py when one is absent.

    Without the lifespan handler, `bash start.sh` boots but the SQLite database
    is empty — the first login hits "no such table: users".

    Triggered when:
      - backend/app/main.py exists AND
      - backend/app/models.py (or any models file) exists AND
      - main.py contains no reference to Base.metadata.create_all

    Restores the scaffold lifespan block and rewires FastAPI(lifespan=lifespan).
    Idempotent: noop when create_all is already present.
    """
    _log = structlog.get_logger("debugger")
    main_path = "backend/app/main.py"
    content = generated_files.get(main_path, "")
    if not content:
        return {}

    # Already has table-creation logic → nothing to do
    if "create_all" in content or "lifespan" in content:
        return {}

    # Only inject when there are models to create tables for
    has_models = any(
        p.startswith("backend/app/") and "model" in p and p.endswith(".py")
        for p in generated_files
    )
    if not has_models:
        return {}

    # Inject the lifespan block before the FastAPI() instantiation
    app_decl_m = re.search(r"^app\s*=\s*FastAPI\s*\(", content, re.MULTILINE)
    if not app_decl_m:
        return {}

    insert_at = app_decl_m.start()

    # Ensure asynccontextmanager isn't double-imported
    new_content = content
    if "asynccontextmanager" not in new_content:
        new_content = _LIFESPAN_BLOCK + new_content
        insert_at += len(_LIFESPAN_BLOCK)
    else:
        new_content = new_content[:insert_at] + _LIFESPAN_BLOCK + new_content[insert_at:]
        insert_at += len(_LIFESPAN_BLOCK)

    # Add lifespan=lifespan to the FastAPI() call
    new_content = re.sub(
        r"(app\s*=\s*FastAPI\s*\()(\s*)",
        r"\1lifespan=lifespan, \2",
        new_content,
        count=1,
    )

    if new_content == content:
        return {}

    _log.info("_fix_missing_lifespan_in_main.applied", path=main_path)
    return {main_path: new_content}


_SEED_NO_PARAM_RE = re.compile(
    r"def\s+seed_demo_data\s*\(\s*\)\s*(?:->\s*[^:]+)?\s*:",
)
_SEED_TYPED_DB_RE = re.compile(
    r"def\s+seed_demo_data\s*\(\s*db\s*(?::\s*[^,)=\n]+)?\s*\)\s*(?:->\s*[^:]+)?\s*:",
)


def _normalize_seed_signature(test_results: dict, generated_files: dict) -> dict:
    """Ensure seed_demo_data accepts db as an optional parameter.

    Fixes two patterns:
      def seed_demo_data():          → def seed_demo_data(db=None):
      def seed_demo_data(db: Session): → def seed_demo_data(db=None):

    Also injects a `if db is None: db = SessionLocal()` guard after the
    signature so the function works when called with no arguments (from
    tests) or with a db session (from the lifespan handler).

    Idempotent — skips files that already have `def seed_demo_data(db=None)`.
    """
    _log = structlog.get_logger("debugger")
    seed_path = "backend/app/seed.py"
    content = generated_files.get(seed_path, "")
    if not content:
        return {}
    if "def seed_demo_data(db=None)" in content:
        return {}

    new = content
    # Pattern A: no parameters at all
    new = _SEED_NO_PARAM_RE.sub("def seed_demo_data(db=None):", new)
    # Pattern B: typed db parameter → make it optional
    new = _SEED_TYPED_DB_RE.sub("def seed_demo_data(db=None):", new)

    # Inject the None-guard after the signature when it's not already there
    if "def seed_demo_data(db=None)" in new and "if db is None" not in new:
        new = new.replace(
            "def seed_demo_data(db=None):",
            "def seed_demo_data(db=None):\n"
            "    if db is None:\n"
            "        from app.database import SessionLocal\n"
            "        db = SessionLocal()",
            1,
        )

    if new == content:
        return {}
    _log.info("normalize_seed_signature.applied", path=seed_path)
    return {seed_path: new}


def _ensure_tolerant_lifespan(test_results: dict, generated_files: dict) -> dict:
    """Repair main.py's lifespan block to use inspect-based tolerant seed call.

    The scaffold's lifespan already uses `inspect.signature(seed_demo_data).parameters`
    to handle both seed_demo_data(db) and seed_demo_data() without knowing which
    signature the LLM generated.

    When the LLM overwrites main.py and replaces the lifespan with a non-tolerant
    form (calling seed_demo_data(_db) directly), boot fails if seed.py declares
    no parameters: "seed_demo_data() takes 0 positional arguments but 1 was given".

    This fixer detects the non-tolerant form and replaces the seed call block
    with the inspect-based tolerant version. Idempotent.
    """
    _log = structlog.get_logger("debugger")
    main_path = "backend/app/main.py"
    content = generated_files.get(main_path, "")
    if not content:
        return {}

    # Already tolerant — nothing to do.
    if _TOLERANT_LIFESPAN_SENTINEL in content:
        return {}

    # Only act if there is a lifespan block that calls seed_demo_data
    if "seed_demo_data" not in content or "lifespan" not in content:
        return {}

    # Replace the seed call section inside the lifespan with the tolerant form.
    # Pattern: any try/except block that imports and calls seed_demo_data.
    _INTOLERANT_SEED_RE = re.compile(
        r"try:\s*\n"
        r"(?:[ \t]+[^\n]*\n)*?"        # any lines (from app.seed import, from db import, etc.)
        r"[ \t]+(?:from app\.seed import seed_demo_data[^\n]*\n)"
        r"(?:[ \t]+[^\n]*\n)*?"        # more setup lines
        r"[ \t]+seed_demo_data\([^)]*\)\n"
        r"(?:[ \t]+[^\n]*\n)*?"        # more lines (finally, db.close, etc.)
        r"(?:[ \t]*except[^\n]*\n[ \t]*[^\n]*\n)+",
        re.MULTILINE,
    )

    _TOLERANT_SEED_REPLACEMENT = (
        "try:\n"
        "        from app.seed import seed_demo_data\n"
        "        from app.database import SessionLocal\n"
        "        import inspect as _inspect\n"
        "        _db = SessionLocal()\n"
        "        try:\n"
        "            if _inspect.signature(seed_demo_data).parameters:\n"
        "                seed_demo_data(_db)\n"
        "            else:\n"
        "                seed_demo_data()\n"
        "        finally:\n"
        "            _db.close()\n"
        "    except ImportError:\n"
        "        pass\n"
        "    except Exception as _e:\n"
        "        print(f\"[startup] WARNING: Could not seed demo data: {_e}\", flush=True)\n"
    )

    new = _INTOLERANT_SEED_RE.sub(_TOLERANT_SEED_REPLACEMENT, content, count=1)
    if new == content:
        return {}

    _log.info("ensure_tolerant_lifespan.applied", path=main_path)
    return {main_path: new}


def _normalize_router_export(test_results: dict, generated_files: dict) -> dict:
    """Ensure every backend/app/routes/*.py exposes its APIRouter as `router`.

    The LLM occasionally names the router after the module (e.g. `menu_items_router`
    or `orders_router`).  main.py always imports via:
        from app.routes.X import router as X_router
    so any name other than `router` causes an ImportError at boot.

    Transforms:
      menu_items_router = APIRouter(...)  →  router = APIRouter(...)
      @menu_items_router.get("/")         →  @router.get("/")

    Idempotent -- files that already use `router` are unchanged.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict = {}
    for path, content in generated_files.items():
        if not path.startswith("backend/app/routes/") or not path.endswith(".py"):
            continue
        if path.endswith("__init__.py") or not content:
            continue

        # Already canonical -- nothing to do.
        if re.search(r"^router\s*=\s*APIRouter\s*\(", content, re.MULTILINE):
            continue

        # Find whichever variable was assigned to APIRouter(...).
        m = re.search(
            r"^([a-zA-Z_]\w*)\s*=\s*APIRouter\s*\(",
            content, re.MULTILINE,
        )
        if not m:
            _log.warning("normalize_router_export.no_apirouter_found", path=path)
            continue

        old_name = m.group(1)
        if old_name == "router":
            continue  # already canonical (shouldn't reach here, but safe)

        new = content
        # Rename the APIRouter assignment.
        new = re.sub(
            rf"^{re.escape(old_name)}(\s*=\s*APIRouter\s*\()",
            r"router\1", new, count=1, flags=re.MULTILINE,
        )
        # Rewrite decorators: @<old>.method(...) → @router.method(...)
        new = re.sub(
            rf"@{re.escape(old_name)}(\.\w+\s*\()",
            r"@router\1", new,
        )
        # Rewrite any remaining bare `<old_name>.something` references.
        new = re.sub(
            rf"\b{re.escape(old_name)}(\s*\.\s*\w+)",
            r"router\1", new,
        )

        if new != content:
            fixes[path] = new
            _log.info(
                "normalize_router_export.renamed",
                path=path, old=old_name, new="router",
            )
    return fixes


def _normalize_route_files(test_results: dict, generated_files: dict) -> dict:
    """Strip /api from route file APIRouter prefix declarations and handler decorators.

    Canonical convention: route files declare prefix WITHOUT /api.
    /api is main.py's sole responsibility via app.include_router(..., prefix="/api").

    Transforms:
      APIRouter(prefix="/api/admin")  →  APIRouter(prefix="/admin")
      @router.get("/api/orders/{id}") →  @router.get("/orders/{id}")

    Idempotent — files that already comply are unchanged.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict = {}
    for path, content in generated_files.items():
        if not path.startswith("backend/app/routes/") or not path.endswith(".py"):
            continue
        if path.endswith("__init__.py") or not content:
            continue
        new = content
        # Strip /api from prefix= declarations (handles single and double quotes).
        new = re.sub(
            r"(prefix\s*=\s*[\"'])/api(?=[\"']|/)",
            r"\1",
            new,
        )
        # Strip /api from route handler decorator paths.
        new = re.sub(
            r'(@router\.(?:get|post|put|patch|delete)\s*\(\s*["\'])/api(?=/|["\'])',
            r"\1",
            new,
        )
        if new != content:
            fixes[path] = new
            _log.info("normalize_route_files.applied", path=path)
    return fixes


def _normalize_main_includes(test_results: dict, generated_files: dict) -> dict:
    """Normalize app.include_router() calls in main.py.

    Fixes:
      1. Doubled prefix: prefix="/api/api/..." → prefix="/api/..."
      2. Bare include_router(var) with no prefix → adds prefix="/api"
         (only for routers imported from app.routes.*)
      3. Deduplicates include_router calls for the same router variable

    Runs every cycle, before _fix_missing_route_includes, so that helper
    can unconditionally emit prefix="/api" without risk of doubling.
    """
    _log = structlog.get_logger("debugger")
    main_path = "backend/app/main.py"
    content = generated_files.get(main_path, "")
    if not content:
        return {}
    new = content

    # 1. Fix doubled /api/api → /api
    new = re.sub(
        r'(app\.include_router\([^)]*prefix\s*=\s*["\'])/api/api(?=/|["\'])',
        r"\1/api",
        new,
    )

    # 2. Add prefix="/api" to bare include_router(var) calls whose var
    #    is imported from app.routes.*
    route_router_vars: set[str] = set()
    for m in re.finditer(
        r"^from\s+app\.routes\.\w+\s+import\s+router\s+as\s+(\w+)",
        new, re.MULTILINE,
    ):
        route_router_vars.add(m.group(1))

    def _add_api_prefix(match: re.Match) -> str:
        full = match.group(0)
        if "prefix" in full:
            return full
        var = match.group(1).strip()
        if var in route_router_vars:
            return f'app.include_router({var}, prefix="/api")'
        return full

    new = re.sub(
        r"app\.include_router\(\s*([a-zA-Z_]\w*)\s*\)",
        _add_api_prefix,
        new,
    )

    # 3. Deduplicate include_router lines: keep first occurrence per router var.
    lines = new.splitlines(keepends=True)
    seen_routers: set[str] = set()
    kept: list[str] = []
    for ln in lines:
        m = re.match(r"\s*app\.include_router\(\s*([a-zA-Z_]\w*)", ln)
        if m:
            name = m.group(1)
            if name in seen_routers:
                continue
            seen_routers.add(name)
        kept.append(ln)
    new = "".join(kept)

    if new == content:
        return {}
    _log.info("normalize_main_includes.applied")
    return {main_path: new}


def _fix_missing_route_includes(
    test_results: dict, generated_files: dict
) -> dict:
    """Ensure every backend/app/routes/*.py module is wired into main.py.

    Catches the common LLM mistake of writing route files but forgetting to
    call app.include_router() in main.py.  Skips auth_routes.py (scaffold-
    managed) and any file whose router is already imported.  Idempotent.

    Safe to always emit prefix="/api" because _normalize_route_files (which
    runs first) has already stripped any /api from the route file's own
    APIRouter prefix declaration — so no doubling is possible.
    """
    _log = structlog.get_logger("debugger")
    main_path = "backend/app/main.py"
    main_content = generated_files.get(main_path, "")
    if not main_content:
        return {}

    # Collect route modules that need wiring
    additions: list[str] = []
    wired_mods: list[str] = []
    for path in sorted(generated_files):
        if not path.startswith("backend/app/routes/") or not path.endswith(".py"):
            continue
        mod = path.rsplit("/", 1)[-1][:-3]  # e.g. "menu_routes"
        if mod in ("__init__", "auth_routes"):
            continue
        content = generated_files.get(path, "")
        if not content or "router" not in content:
            continue
        if f"from app.routes.{mod} import router" in main_content:
            continue
        alias = re.sub(r"_routes$", "", mod).replace("-", "_") + "_router"
        # Always emit prefix="/api" — normalization guarantees route files
        # no longer carry /api in their own prefix.
        # Wrap in try/except so a broken route file never silently prevents
        # main.py from loading. Print to stderr so the boot log captures it.
        additions.append(
            f"try:\n"
            f"    from app.routes.{mod} import router as {alias}\n"
            f'    app.include_router({alias}, prefix="/api")\n'
            f"except Exception as _e:\n"
            f"    import sys\n"
            f'    print(f"[startup] FAILED to load app.routes.{mod}: '
            f'{{type(_e).__name__}}: {{_e}}", file=sys.stderr, flush=True)'
        )
        wired_mods.append(mod)

    if not additions:
        return {}

    block = "\n\n".join(additions)
    # Prefer the scaffold's explicit marker; fall back to the legacy one.
    # IMPORTANT: match the FULL marker line (including any suffix text like
    # " — LLM appends, never replaces ===") so the suffix is NOT stranded
    # after the injected block.  A prefix-only replace would leave ` — ...`
    # dangling after the block's last expression, producing a SyntaxError.
    marker_primary = "# === ROUTE INCLUDES BELOW THIS LINE"
    marker_legacy_1 = "# Project-specific route includes -- generator appends here"
    marker_legacy_2 = "# Project-specific route includes"
    m_primary = re.search(re.escape(marker_primary) + r"[^\n]*\n", main_content)
    if m_primary:
        pos = m_primary.end()
        new_main = main_content[:pos] + "\n" + block + "\n" + main_content[pos:]
    elif marker_legacy_1 in main_content:
        new_main = main_content.replace(
            marker_legacy_1, marker_legacy_1 + "\n\n" + block, 1
        )
    elif marker_legacy_2 in main_content:
        m_leg = re.search(re.escape(marker_legacy_2) + r"[^\n]*\n", main_content)
        if m_leg:
            pos = m_leg.end()
            new_main = main_content[:pos] + "\n" + block + "\n" + main_content[pos:]
        else:
            new_main = main_content.rstrip() + "\n\n" + block + "\n"
    else:
        new_main = main_content.rstrip() + "\n\n" + block + "\n"

    if new_main == main_content:
        return {}

    _log.info(
        "fix_missing_route_includes.applied",
        added=wired_mods,
        count=len(additions),
    )
    return {main_path: new_main}


_PASSWORD_ENDPOINT_MISS_RE = re.compile(
    r"CONTRACT MISS[^\n]*POST[^\n]*/api/auth/me/password\b",
    re.IGNORECASE,
)
_PASSWORD_HANDLER_RE = re.compile(
    r"(?:old_password|new_password)",
    re.IGNORECASE,
)
_PASSWORD_PATH_RE = re.compile(
    r'@\w+\.\w+\s*\([^)]*["\'][^"\']*password[^"\']*["\']',
    re.IGNORECASE,
)


def _fix_password_change_alias(test_results: dict, generated_files: dict) -> dict:
    """Inject POST /api/auth/me/password alias into auth_routes.py when the
    contract flags it missing but a password-change handler exists elsewhere.

    Heuristics for detecting a password-change handler:
      - function body references old_password AND new_password, OR
      - route decorator path ends with /password

    If found in a non-auth module, appends a thin alias to auth_routes.py that
    delegates to the existing handler. If no handler exists anywhere, does nothing.
    Idempotent.
    """
    contract_log = (test_results.get("logs", {}) or {}).get("contract", "") or ""
    advisory_log = (test_results.get("logs", {}) or {}).get("contract_advisory", "") or ""
    combined = contract_log + "\n" + advisory_log

    if not _PASSWORD_ENDPOINT_MISS_RE.search(combined):
        return {}

    auth_routes_path = "backend/app/routes/auth_routes.py"
    auth_content = generated_files.get(auth_routes_path, "")
    if not auth_content:
        return {}

    # Idempotent: if /me/password already exists in auth_routes, do nothing.
    if "/me/password" in auth_content:
        return {}

    # Scan all route files for a password-change handler.
    handler_module: str | None = None
    handler_fn: str | None = None

    for file_path, content in generated_files.items():
        if not file_path.startswith("backend/app/routes/") or not file_path.endswith(".py"):
            continue
        if not content:
            continue
        # Skip auth_routes itself — we know /me/password isn't there.
        if file_path == auth_routes_path:
            continue
        # Heuristic: function that uses old_password+new_password OR a route ending in /password.
        has_pw_body = bool(_PASSWORD_HANDLER_RE.search(content))
        has_pw_path = bool(_PASSWORD_PATH_RE.search(content))
        if not (has_pw_body or has_pw_path):
            continue

        # Extract the function name nearest a password path decorator or pw body.
        fn_match = None
        if has_pw_path:
            for m in _PASSWORD_PATH_RE.finditer(content):
                after = content[m.end():]
                fn_m = re.search(r"(?:async\s+)?def\s+(\w+)\s*\(", after)
                if fn_m:
                    fn_match = fn_m.group(1)
                    break
        if fn_match is None and has_pw_body:
            fn_m = re.search(r"(?:async\s+)?def\s+(\w+)\s*\(", content)
            if fn_m:
                fn_match = fn_m.group(1)

        if fn_match:
            # Convert path to dotted module: backend/app/routes/profile.py → app.routes.profile
            handler_module = (
                file_path.replace("backend/", "").replace("/", ".").replace(".py", "")
            )
            handler_fn = fn_match
            break

    if handler_module is None or handler_fn is None:
        return {}

    _log = structlog.get_logger("debugger")

    alias_block = (
        f"\n\n# Password-change alias -- canonical path expected by the contract.\n"
        f"from pydantic import BaseModel\n\n\n"
        f"class PasswordChangeRequest(BaseModel):\n"
        f"    old_password: str\n"
        f"    new_password: str\n\n\n"
        f"@router.post(\"/me/password\")\n"
        f"def change_my_password(\n"
        f"    payload: PasswordChangeRequest,\n"
        f"    db: Session = Depends(get_db),\n"
        f"    current_user: User = Depends(get_current_user),\n"
        f"):\n"
        f"    from {handler_module} import {handler_fn} as _orig\n"
        f"    return _orig(payload, db, current_user)\n"
    )

    # Ensure Session is imported in auth_routes (it almost always is, but be safe).
    new_auth = auth_content.rstrip() + alias_block

    _log.info(
        "fix_password_change_alias.applied",
        source_module=handler_module,
        source_fn=handler_fn,
    )
    return {auth_routes_path: new_auth}


def _fix_missing_main_py(test_results: dict, generated_files: dict) -> dict:
    """Regenerate backend/app/main.py when the LLM forgot to create it.

    Triggered by boot error 'No module named app.main' OR when main.py is
    simply absent from generated_files.  Reads the scaffold template if
    available; falls back to a hardcoded minimal version.  Appends an
    include_router call for every backend route module found in the project.
    """
    _log = structlog.get_logger("debugger")
    main_path = "backend/app/main.py"

    # Trigger only when main.py is absent or empty
    if generated_files.get(main_path):
        return {}

    boot_log = (test_results.get("logs", {}) or {}).get("boot", "") or ""
    errors = " ".join(str(e) for e in (test_results.get("errors") or []))
    combined = boot_log + " " + errors

    # Require either explicit error OR that the file is truly missing
    is_missing_error = (
        "No module named 'app.main'" in combined
        or "No module named 'app'" in combined
        or "backend_import_error" in combined
        or main_path not in generated_files
    )
    if not is_missing_error:
        return {}

    # Load scaffold template
    try:
        with open(_MAIN_PY_TEMPLATE_PATH) as fh:
            scaffold = fh.read()
    except OSError:
        scaffold = _MAIN_PY_SCAFFOLD_FALLBACK

    # Append include_router for every discovered route module
    route_includes: list[str] = []
    seen_vars: set[str] = set()
    for path in sorted(generated_files):
        if not path.startswith("backend/app/routes/") or not path.endswith(".py"):
            continue
        if path.endswith("auth_routes.py") or path.endswith("__init__.py"):
            continue
        content = generated_files.get(path, "")
        if not content or "router" not in content:
            continue
        module = path.replace("backend/", "").replace("/", ".").replace(".py", "")
        stem = path.rsplit("/", 1)[-1].replace(".py", "")
        var = stem + "_router"
        if var in seen_vars:
            continue
        seen_vars.add(var)
        route_includes.append(
            f"from {module} import router as {var}\n"
            f'app.include_router({var}, prefix="/api")'
        )

    new_main = scaffold.rstrip()
    if route_includes:
        new_main += "\n\n" + "\n".join(route_includes) + "\n"

    _log.info(
        "_fix_missing_main_py.applied",
        num_routes=len(route_includes),
    )
    return {main_path: new_main}


# ── Orphan-navigate rewriter ──────────────────────────────────────────────────


def _fix_orphan_navigates_to_existing_routes(
    test_results: dict, generated_files: dict
) -> dict:
    """Rewrite navigate('/X') and <Link to="/X"> calls that point to unmounted
    routes, redirecting them to the best-match mounted route.

    Key case: the LLM writes navigate('/menu') but the architect mounted
    MenuPage at '/' (the homepage IS the menu).  This rewrites the target
    to '/' so navigation keeps working.

    Algorithm:
      1. Build path→component map from App.jsx.
      2. For each orphan target path from route_link_violations, find the best
         existing route whose component name contains the orphan slug.
      3. Rewrite all navigate('TARGET') and <Link to="TARGET"> in the affected
         file to point at the found route instead.

    Only rewrites when a confident match is found.  Leaves ambiguous cases for
    the orphan-remover helper to clean up.
    """
    _log = structlog.get_logger("debugger")

    violations = test_results.get("route_link_violations", []) or []
    if not violations:
        return {}

    app_jsx = generated_files.get("frontend/src/App.jsx", "")
    if not app_jsx:
        return {}

    # Build path → component map from App.jsx
    route_map: dict[str, str] = {}
    for line in app_jsx.split("\n"):
        pm = re.search(r'path=["\']([^"\']+)["\']', line)
        em = re.search(r'element=\{<(\w+)', line)
        if pm and em:
            route_map[pm.group(1)] = em.group(1)

    mounted_paths = set(route_map)

    def _best_match(target: str) -> str | None:
        """Return an existing mounted path that best represents `target`."""
        if target in mounted_paths:
            return None  # already mounted — not orphan
        slug = target.strip("/").split("/")[0].lower().replace("-", "").replace("_", "")
        if not slug:
            return None
        # Priority 1: non-root path whose first segment matches the slug
        for path in mounted_paths:
            if path == "/":
                continue
            seg = path.strip("/").split("/")[0].lower().replace("-", "")
            if seg == slug or slug in seg or seg in slug:
                return path
        # Priority 2: component name at any route contains the slug
        for path, comp in route_map.items():
            comp_lower = comp.lower()
            # Strip common suffixes: MenuPage → menu, HomeScreen → home
            comp_base = re.sub(r"(page|screen|view|component)$", "", comp_lower)
            if comp_base == slug or slug in comp_base or comp_base in slug:
                return path
        return None

    fixes: dict[str, str] = {}

    # Group violations by file
    by_file: dict[str, list[str]] = {}
    for v in violations:
        if v.get("kind") not in ("Link", "navigate"):
            continue
        target = v.get("target", "")
        if not target or target in mounted_paths:
            continue
        fp = v.get("file", "")
        if not fp:
            continue
        by_file.setdefault(fp, [])
        if target not in by_file[fp]:
            by_file[fp].append(target)

    for fp, targets in by_file.items():
        content = generated_files.get(fp, "")
        if not content:
            continue
        new_content = content
        rewrote: list[tuple[str, str]] = []
        for target in targets:
            replacement = _best_match(target)
            if not replacement:
                continue
            escaped = re.escape(target)
            # Rewrite navigate('TARGET') / navigate("TARGET")
            new_content = re.sub(
                rf"navigate\s*\(\s*(['\"]){escaped}\1\s*\)",
                lambda m, r=replacement: f"navigate('{r}')",
                new_content,
            )
            # Rewrite <Link to="TARGET" ...> and <Link to='TARGET' ...>
            new_content = re.sub(
                rf'(<Link\s+[^>]*to\s*=\s*["\']){escaped}(["\'])',
                rf"\g<1>{replacement}\2",
                new_content,
            )
            if new_content != content:
                rewrote.append((target, replacement))
        if new_content != content:
            fixes[fp] = new_content
            _log.info(
                "_fix_orphan_navigates_to_existing_routes.applied",
                file=fp,
                rewrites=[(t, r) for t, r in rewrote],
            )

    return fixes


def _fix_remove_orphan_navbar_links(
    test_results: dict, generated_files: dict
) -> dict:
    """Remove <Link> elements from Navbar that point to routes not mounted in
    App.jsx AND for which no page file exists.

    Called after _fix_routes_config_consistency so the routes helper has
    already had a chance to mount any route that DOES have a page file.
    Only truly dead links — no route, no page — are pruned here.

    Idempotent: if no orphan links remain, returns {}.
    """
    _log = structlog.get_logger("debugger")

    violations = test_results.get("route_link_violations", []) or []
    nav_violations = [
        v for v in violations
        if "Nav" in v.get("file", "") and v.get("kind") == "Link"
    ]
    if not nav_violations:
        return {}

    app_jsx = generated_files.get("frontend/src/App.jsx", "")
    mounted_paths: set[str] = set(re.findall(
        r'<Route\s+[^>]*path\s*=\s*["\']([^"\']+)["\']',
        app_jsx,
    ))

    fixes: dict[str, str] = {}

    for v in nav_violations:
        target = v["target"]
        # Already mounted — not orphan
        if target in mounted_paths:
            continue

        # A page file exists → the routes helper should mount it; leave alone
        target_slug = target.strip("/").split("/")[0]
        page_exists = any(
            fp.startswith("frontend/src/pages/") and (
                fp.endswith(f"/{target_slug.title()}Page.jsx") or
                fp.endswith(f"/{target_slug.capitalize()}Page.jsx") or
                fp.endswith(f"/{target_slug.title()}.jsx") or
                fp.endswith(f"/{target_slug.capitalize()}.jsx")
            )
            for fp in generated_files
        )
        if page_exists:
            continue  # route_config will handle it

        # Truly orphan — no route, no page file — remove the Link element
        nav_path = v["file"]
        content = fixes.get(nav_path, generated_files.get(nav_path, ""))
        if not content:
            continue

        # Match <Link to="TARGET" ...>...</Link> including multi-line content
        pattern = (
            r'\s*<Link\s+[^>]*to\s*=\s*["\']'
            + re.escape(target)
            + r'["\'][^>]*>.*?</Link>'
        )
        new_content = re.sub(pattern, "", content, flags=re.DOTALL)
        # Clean up any <li></li> wrappers left empty after the removal
        new_content = re.sub(r'<li>\s*</li>', "", new_content)
        # Collapse excess blank lines
        new_content = re.sub(r"\n{3,}", "\n\n", new_content)

        if new_content != content:
            fixes[nav_path] = new_content
            _log.info(
                "fix_remove_orphan_navbar_links.removed",
                file=nav_path, target=target,
            )

    return fixes


def _stub_missing_pages_for_orphan_links(
    test_results: dict, generated_files: dict
) -> dict:
    """For every ORPHAN-LINK <Link to='/X'> whose target /X has no
    matching <Route> in App.jsx AND no matching page component on disk,
    create a minimal placeholder page file so the Link is not dead.

    Why this exists:
      The LLM frequently writes <Link to='/projects'> in DashboardPage
      without generating ProjectsPage.jsx OR mounting the route.  Without
      a page file there is nothing for _fix_mount_missing_routes_for_existing_pages
      to mount, so the orphan-link error never clears.  Generating a small
      'Coming soon' stub is a graceful degradation that lets the app ship
      and the user iterate later.

    Strategy:
      1. Read route_link_violations from test_results.
      2. Skip targets that ARE already mounted in App.jsx OR already
         have a matching page file (handled by the existing helper).
      3. For each remaining slug, generate a stub page at
         frontend/src/pages/<Slug>Page.jsx with a centered placeholder.
      4. Run AFTER any existing helper that creates page files, but BEFORE
         _fix_mount_missing_routes_for_existing_pages so the route-mounter
         can then pick the stub up and mount it.
    """
    _log = structlog.get_logger("debugger")

    violations = test_results.get("route_link_violations", []) or []
    if not violations:
        return {}

    app_jsx = generated_files.get("frontend/src/App.jsx", "")
    if not app_jsx:
        return {}

    mounted_paths = set(re.findall(
        r'<Route\s+[^>]*path\s*=\s*["\']([^"\']+)["\']',
        app_jsx,
    ))

    existing_page_files = {
        fp.split("/")[-1]
        for fp in generated_files
        if fp.startswith("frontend/src/pages/")
        and fp.endswith((".jsx", ".tsx"))
    }

    fixes: dict = {}
    seen_slugs: set = set()

    for v in violations:
        if v.get("kind") not in ("Link", "navigate"):
            continue
        target = (v.get("target") or "").strip()
        if not target.startswith("/"):
            continue
        if target in mounted_paths:
            continue

        slug = target.strip("/").split("/")[0]
        if not slug or slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        # If the existing helper can mount it, skip (we don't stub on top of real pages).
        candidates = {
            f"{slug.title()}Page.jsx",
            f"{slug.capitalize()}Page.jsx",
            f"{slug.title()}.jsx",
            f"{slug.capitalize()}.jsx",
        }
        if candidates & existing_page_files:
            continue

        # Build the stub component
        comp_name = f"{slug.title().replace('-', '').replace('_', '')}Page"
        stub_path = f"frontend/src/pages/{comp_name}.jsx"
        if stub_path in fixes or stub_path in generated_files:
            continue

        pretty_title = slug.replace('-', ' ').replace('_', ' ').title()
        stub = (
            f'import {{ Construction }} from "lucide-react"\n\n'
            f'export default function {comp_name}() {{\n'
            f'  return (\n'
            f'    <section className="max-w-3xl mx-auto px-4 py-16 text-center">\n'
            f'      <div className="inline-flex items-center justify-center w-16 h-16\n'
            f'                      rounded-2xl bg-amber-100 text-amber-600 mb-4">\n'
            f'        <Construction className="w-8 h-8" />\n'
            f'      </div>\n'
            f'      <h1 className="text-3xl font-bold mb-2">{pretty_title}</h1>\n'
            f'      <p className="text-slate-500">\n'
            f'        This page is coming soon. Linked from elsewhere in the app\n'
            f'        but has not been built out yet.\n'
            f'      </p>\n'
            f'    </section>\n'
            f'  )\n'
            f'}}\n'
        )
        fixes[stub_path] = stub
        _log.info(
            "stub_missing_pages_for_orphan_links.created",
            target=target,
            page=stub_path,
        )

    return fixes


def _fix_mount_missing_routes_for_existing_pages(
    test_results: dict, generated_files: dict
) -> dict:
    """For every ORPHAN-LINK (<Link to='/X'>) whose target /X has no matching
    <Route> in App.jsx, look for a matching page component in
    frontend/src/pages/.  If found, INJECT the missing route + import into
    App.jsx instead of rewriting the link or deleting it.

    Why this exists:
      * `_fix_orphan_navigates_to_existing_routes` only REWRITES the link
        to an existing route.
      * `_fix_routes_config_consistency` only mounts routes that are listed
        in routes.js.
      * Neither handles the common LLM bug where DashboardPage renders
        `<Link to='/projects'>` but the LLM forgot to add the
        `<Route path='/projects'>` to App.jsx, even though
        `ProjectsPage.jsx` already exists in pages/.

    Strategy:
      1. Read every ORPHAN-LINK target from test_results["route_link_violations"].
      2. Skip targets that ARE already mounted in App.jsx.
      3. For each remaining target, scan frontend/src/pages/ for a page file
         whose slug matches the target ('/projects' → ProjectsPage.jsx,
         Projects.jsx, ProjectsList.jsx, etc.).
      4. If found, insert a new `<Route path="/X" element={<PageComp />} />`
         line + the corresponding import. Wrap in RequireAuth IF auth scaffold
         exists AND a peer route uses RequireAuth (proxy for "this app
         requires login").

    Returns {path: new_content} for App.jsx when injections happened.
    Idempotent — safe to run every cycle.
    """
    _log = structlog.get_logger("debugger")

    violations = test_results.get("route_link_violations", []) or []
    if not violations:
        return {}

    app_jsx_path = "frontend/src/App.jsx"
    app_jsx = generated_files.get(app_jsx_path, "")
    if not app_jsx:
        return {}

    # Existing mounted paths
    mounted_paths: set[str] = set(re.findall(
        r'<Route\s+[^>]*path\s*=\s*["\']([^"\']+)["\']',
        app_jsx,
    ))

    # Collect orphan link targets (skip non-Link kinds and external/anchor)
    orphan_targets: list[str] = []
    seen: set[str] = set()
    for v in violations:
        if v.get("kind") not in ("Link", "navigate"):
            continue
        target = (v.get("target") or "").strip()
        if not target or not target.startswith("/"):
            continue
        if target in mounted_paths or target in seen:
            continue
        seen.add(target)
        orphan_targets.append(target)

    if not orphan_targets:
        return {}

    # Index existing page files for quick lookup
    page_files = {
        fp.split("/")[-1]: fp
        for fp in generated_files
        if fp.startswith("frontend/src/pages/")
        and fp.endswith((".jsx", ".tsx"))
    }

    def _find_page_for(target: str) -> tuple[str, str] | None:
        """Return (component_name, page_filename) for a target path, or None."""
        slug = target.strip("/").split("/")[0]
        if not slug:
            return None
        # Normalise candidates: CamelCase, slug-as-is, PascalPage forms.
        bare = re.sub(r"[-_]", "", slug).lower()
        candidates = [
            f"{slug.title()}Page.jsx",
            f"{slug.capitalize()}Page.jsx",
            f"{slug.title()}.jsx",
            f"{slug.capitalize()}.jsx",
        ]
        for cand in candidates:
            if cand in page_files:
                comp = cand[:-4]  # strip .jsx
                return (comp, page_files[cand])
        # Fuzzy: scan for a file whose basename (without .jsx/Page) matches.
        for filename in page_files:
            base = filename[:-4]
            base_norm = re.sub(r"(Page|Screen|View)$", "", base).lower()
            if base_norm == bare:
                return (base, page_files[filename])
        return None

    # Detect auth scaffold + whether peer routes use RequireAuth
    auth_scaffold_present = (
        "RequireAuth" in app_jsx
        or "frontend/src/components/RequireAuth.jsx" in generated_files
    )
    has_require_auth_routes = "<RequireAuth>" in app_jsx

    additions: list[tuple[str, str, str]] = []  # (path, component, page_filename)
    for target in orphan_targets:
        found = _find_page_for(target)
        if not found:
            continue
        additions.append((target, found[0], found[1]))

    if not additions:
        return {}

    new_app = app_jsx

    # 1. Inject imports for any component not already imported.
    for _path, comp, _filename in additions:
        if re.search(rf'\bimport\s+{comp}\b', new_app):
            continue
        import_line = f'import {comp} from "@/pages/{comp}"'
        # Insert after the last existing import line.
        last_import = None
        for m in re.finditer(r"^import\s+[^\n]+$", new_app, re.MULTILINE):
            last_import = m
        if last_import:
            new_app = (
                new_app[: last_import.end()]
                + "\n" + import_line
                + new_app[last_import.end():]
            )
        else:
            new_app = import_line + "\n" + new_app

    # 2. Inject <Route> entries before the closing </Routes>.
    # Try to insert just before </Routes> so they land in the same Router scope.
    routes_close = re.search(r"</Routes>", new_app)
    if not routes_close:
        # No <Routes> wrapper — abort to avoid breaking the file.
        _log.warning("fix_mount_missing_routes.no_routes_tag")
        return {}

    insert_at = routes_close.start()
    indent = "        "  # match common 2-deep nesting; harmless if it differs

    new_route_lines: list[str] = []
    for path_val, comp, _filename in additions:
        if auth_scaffold_present and has_require_auth_routes:
            line = (
                f'{indent}<Route path="{path_val}" element='
                f'{{<RequireAuth><{comp} /></RequireAuth>}} />\n'
            )
        else:
            line = f'{indent}<Route path="{path_val}" element={{<{comp} />}} />\n'
        new_route_lines.append(line)
        _log.info(
            "fix_mount_missing_routes.injected",
            path=path_val, component=comp,
        )

    new_app = new_app[:insert_at] + "".join(new_route_lines) + new_app[insert_at:]

    if new_app == app_jsx:
        return {}

    return {app_jsx_path: new_app}


# ── Duplicate top-level declaration cleaner ──────────────────────────────────

_DEDUP_FUNC_RE = re.compile(
    r"^(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+(\w+)\s*\(",
    re.MULTILINE,
)
_DEDUP_CONST_RE = re.compile(r"^const\s+(\w+)\s*=", re.MULTILINE)


def _fix_dedup_top_level_declarations(
    test_results: dict, generated_files: dict
) -> dict:
    """Remove duplicate top-level function/const declarations in .jsx/.tsx/.js files.

    Keeps the FIRST occurrence; removes the entire block of every subsequent
    duplicate.  Targets the recurring 'Layout has already been declared' bug
    class but works for any duplicate top-level binding.

    Idempotent: a file with no duplicates is returned unchanged.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict[str, str] = {}

    for path, content in generated_files.items():
        if not path.endswith((".jsx", ".tsx", ".js")):
            continue
        if not content:
            continue

        seen: set[str] = set()
        ranges_to_delete: list[tuple[int, int, str]] = []

        # ── function declarations ─────────────────────────────────────────────
        for m in _DEDUP_FUNC_RE.finditer(content):
            name = m.group(1)
            decl_start = m.start()
            open_brace = content.find("{", m.end())
            if open_brace == -1:
                seen.add(name)
                continue
            depth = 0
            i = open_brace
            while i < len(content):
                if content[i] == "{":
                    depth += 1
                elif content[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            if depth != 0:
                seen.add(name)
                continue
            decl_end = i + 1
            # Consume trailing blank lines for clean deletion
            while decl_end < len(content) and content[decl_end] == "\n":
                decl_end += 1
            if name in seen:
                ranges_to_delete.append((decl_start, decl_end, name))
            else:
                seen.add(name)

        # ── const declarations that duplicate an already-seen name ────────────
        for m in _DEDUP_CONST_RE.finditer(content):
            name = m.group(1)
            start = m.start()
            # Skip if this range is already scheduled for deletion (nested inside
            # a function body that's being removed).
            if any(s <= start < e for s, e, _ in ranges_to_delete):
                continue
            if name not in seen:
                seen.add(name)
                continue
            # Find end: depth-track brackets; stop at bare newline when depth==0
            i = m.end()
            depth = 0
            while i < len(content):
                c = content[i]
                if c in "({[":
                    depth += 1
                elif c in ")}]":
                    depth -= 1
                elif c == "\n" and depth == 0:
                    i += 1
                    break
                i += 1
            # Consume trailing blank lines
            while i < len(content) and content[i] == "\n":
                i += 1
            ranges_to_delete.append((start, i, name))

        if not ranges_to_delete:
            continue

        # Apply deletions from end to start so earlier offsets stay valid
        ranges_to_delete.sort(key=lambda x: x[0], reverse=True)
        new_content = content
        removed_names: list[str] = []
        for start, end, name in ranges_to_delete:
            new_content = new_content[:start] + new_content[end:]
            removed_names.append(name)

        # Collapse excess blank lines left by the removals
        new_content = re.sub(r"\n{3,}", "\n\n", new_content)

        if new_content != content:
            fixes[path] = new_content
            _log.info(
                "fix_dedup_top_level_declarations.applied",
                path=path,
                removed=sorted(set(removed_names)),
            )

    return fixes


_NON_ASCII_REPLACEMENTS: dict[str, str] = {
    "—": "--",    # em-dash
    "–": "-",     # en-dash
    "“": '"',     # left double-quote
    "”": '"',     # right double-quote
    "‘": "'",     # left single-quote
    "’": "'",     # right single-quote
    "…": "...",   # ellipsis
    " ": " ",     # non-breaking space
    "─": "-",     # box-drawing horizontal light
    "━": "-",     # box-drawing horizontal heavy
    "│": "|",     # box-drawing vertical
}


def _ascii_safe_python(content: str) -> str:
    """Replace smart-Unicode characters with ASCII equivalents.

    Em-dashes, smart quotes, and box-drawing chars are fine in comments and
    docstrings when read by Python — BUT they cause SyntaxError when they
    land in code position (e.g., injected as a suffix on an expression line).
    Normalizing all of them to ASCII is safe: docstrings still read correctly,
    and we eliminate the entire class of injection-position Unicode bugs.
    """
    for bad, good in _NON_ASCII_REPLACEMENTS.items():
        content = content.replace(bad, good)
    return content


def _normalize_python_ascii(generated_files: dict) -> list[str]:
    """Sweep every .py file in generated_files and replace non-ASCII punctuation.

    Runs BEFORE _validate_or_rollback so any em-dash injected by a helper is
    neutralized before the validator sees it. Idempotent.
    """
    _log = structlog.get_logger("debugger")
    changed: list[str] = []
    for path, content in list(generated_files.items()):
        if not path.endswith(".py") or not content:
            continue
        new = _ascii_safe_python(content)
        if new != content:
            chars_replaced = sum(content.count(c) for c in _NON_ASCII_REPLACEMENTS)
            generated_files[path] = new
            changed.append(path)
            _log.info(
                "normalize_python_ascii.applied",
                path=path,
                chars_replaced=chars_replaced,
            )
    return changed


# ── Contract miss pattern for the auto-stub helper ───────────────────────────
_GENERAL_MISS_RE = re.compile(
    r"CONTRACT MISS:\s+"
    r"(?!METHOD\b)(?!ADMIN-PREFIX\b)(?!AUTH-SCAFFOLD\b)(?!AUTH-SHAPE\b)(?!DOUBLED-PREFIX\b)"
    r"(?P<method>GET|POST|PUT|PATCH|DELETE)\s+(?P<path>/api/[^\s;,]+)",
    re.IGNORECASE,
)


def _stub_router_prefix(target_file: str) -> str:
    """Derive the router's own prefix (without /api) from the route file path."""
    name = target_file.split("/")[-1][:-3]
    if name == "auth_routes":
        return "/auth"
    if name.startswith("admin_"):
        resource = name[6:].replace("_", "-")
        return f"/admin/{resource}"
    resource = re.sub(r"_routes$", "", name).replace("_", "-")
    return f"/{resource}"


def _stub_handler_path(target_file: str, probe: str) -> str:
    """Strip the router prefix from probe to get the decorator path."""
    prefix = _stub_router_prefix(target_file)
    if probe.startswith(prefix):
        rel = probe[len(prefix):]
        return rel if rel else ""
    return probe


def _stub_func_name(method: str, probe: str) -> str:
    """Derive a unique function name from method + path."""
    parts = [p for p in probe.strip("/").split("/") if p and not p.startswith("{")]
    base = "_".join(parts).replace("-", "_") or "root"
    action = {
        "get": "get" if "{" in probe else "list",
        "post": "create",
        "put": "update",
        "patch": "patch",
        "delete": "delete",
    }.get(method, method)
    return f"{action}_{base}"


def _generate_handler_stub(
    method: str,
    handler_path: str,
    func_name: str,
    endpoint: dict,
) -> str:
    """Generate a working FastAPI handler stub that queries the DB.

    Stubs use lazy model imports inside the function body so they never
    fail to parse even when the model name doesn't match exactly.
    """
    path_params = re.findall(r"\{([^}]+)\}", handler_path)
    pp_sig = "".join(f"    {p}: int,\n" for p in path_params)

    needs_auth = endpoint.get("auth", True)
    auth_dep = "    current_user: User = Depends(get_current_user),\n" if needs_auth else ""
    body_dep = "    payload: dict = None,\n" if method in ("post", "put", "patch") else ""

    # Derive model name from the first non-param path segment.
    segs = [s for s in handler_path.strip("/").split("/") if s and not s.startswith("{")]
    resource_raw = segs[0] if segs else "item"
    resource_pascal = resource_raw.replace("-", "_").title().replace("_", "")
    model_name = resource_pascal[:-1] if resource_pascal.endswith("s") else resource_pascal

    pp = path_params[0] if path_params else "item_id"

    if method == "get" and not path_params:
        body = (
            f"    try:\n"
            f"        from app.models import {model_name}\n"
            f"        items = db.query({model_name}).all()\n"
            f"        return [_serialize(i) for i in items]\n"
            f"    except Exception:\n"
            f"        return []\n"
        )
    elif method == "get":
        body = (
            f"    try:\n"
            f"        from app.models import {model_name}\n"
            f"        item = db.query({model_name}).filter({model_name}.id == {pp}).first()\n"
            f"        if not item:\n"
            f'            raise HTTPException(status_code=404, detail="Not found")\n'
            f"        return _serialize(item)\n"
            f"    except HTTPException:\n"
            f"        raise\n"
            f"    except Exception:\n"
            f'        raise HTTPException(status_code=404, detail="Not found")\n'
        )
    elif method == "post":
        body = (
            f"    try:\n"
            f"        from app.models import {model_name}\n"
            f"        data = (payload or {{}})\n"
            f"        if hasattr(data, 'model_dump'):\n"
            f"            data = data.model_dump()\n"
            f"        item = {model_name}(**data)\n"
            f"        db.add(item); db.commit(); db.refresh(item)\n"
            f"        return _serialize(item)\n"
            f"    except Exception as e:\n"
            f"        db.rollback()\n"
            f'        raise HTTPException(status_code=400, detail=str(e))\n'
        )
    elif method in ("put", "patch"):
        body = (
            f"    try:\n"
            f"        from app.models import {model_name}\n"
            f"        item = db.query({model_name}).filter({model_name}.id == {pp}).first()\n"
            f"        if not item:\n"
            f'            raise HTTPException(status_code=404, detail="Not found")\n'
            f"        data = (payload or {{}})\n"
            f"        if hasattr(data, 'model_dump'):\n"
            f"            data = data.model_dump()\n"
            f"        for k, v in data.items():\n"
            f"            if hasattr(item, k):\n"
            f"                setattr(item, k, v)\n"
            f"        db.commit(); db.refresh(item)\n"
            f"        return _serialize(item)\n"
            f"    except HTTPException:\n"
            f"        raise\n"
            f"    except Exception as e:\n"
            f"        db.rollback()\n"
            f'        raise HTTPException(status_code=400, detail=str(e))\n'
        )
    elif method == "delete":
        body = (
            f"    from fastapi import Response\n"
            f"    try:\n"
            f"        from app.models import {model_name}\n"
            f"        item = db.query({model_name}).filter({model_name}.id == {pp}).first()\n"
            f"        if item is None:\n"
            f"            return Response(status_code=204)\n"
            f"        db.delete(item)\n"
            f"        db.commit()\n"
            f"        return Response(status_code=204)\n"
            f"    except Exception as e:\n"
            f"        db.rollback()\n"
            f'        raise HTTPException(status_code=400, detail=str(e))\n'
        )
    else:
        body = '    return {"ok": True}\n'

    deco_path = handler_path if handler_path else "/"
    return (
        f'@router.{method}("{deco_path}")\n'
        f"def {func_name}(\n"
        f"{pp_sig}"
        f"{body_dep}"
        f"{auth_dep}"
        f"    db: Session = Depends(get_db),\n"
        f"):\n"
        f"{body}"
    )


_SERIALIZE_HELPER = (
    "def _serialize(obj):\n"
    "    if obj is None:\n"
    "        return None\n"
    "    if hasattr(obj, '__dict__'):\n"
    "        return {k: v for k, v in obj.__dict__.items() if not k.startswith('_')}\n"
    "    return obj\n"
)


_REACHABILITY_MISS_RE = re.compile(
    r"\b(?P<method>GET|POST|PUT|PATCH|DELETE)\s+"
    r"(?P<path>/api/[^\s;,]+)\s+(?:->|→)\s+route does not exist",
)


def _ensure_required_imports(content: str) -> str:
    """Walk the file's AST, detect references to known scaffold
    helpers (get_current_user, hash_password, etc.), and inject
    `from app.X import Y` imports if missing.

    Returns the file with any required imports added at the top
    of the import block. Idempotent -- running twice produces
    the same output.

    If the file fails to parse, return unchanged (different
    fixer will handle syntax errors).
    """
    import ast as _ast

    try:
        tree = _ast.parse(content)
    except SyntaxError:
        return content

    # Map of known scaffold names -> (module, symbol).
    KNOWN_SCAFFOLD_NAMES = {
        "get_current_user":    ("app.auth", "get_current_user"),
        "require_admin":       ("app.auth", "require_admin"),
        "hash_password":       ("app.auth", "hash_password"),
        "verify_password":     ("app.auth", "verify_password"),
        "create_access_token": ("app.auth", "create_access_token"),
        "get_db":              ("app.database", "get_db"),
        "SessionLocal":        ("app.database", "SessionLocal"),
        "Base":                ("app.database", "Base"),
        "engine":              ("app.database", "engine"),
        "Depends":             ("fastapi", "Depends"),
        "HTTPException":       ("fastapi", "HTTPException"),
        "APIRouter":           ("fastapi", "APIRouter"),
        "status":              ("fastapi", "status"),
        "Session":             ("sqlalchemy.orm", "Session"),
        "Response":            ("fastapi", "Response"),
    }

    # Collect all Name nodes that are referenced (loaded, not stored).
    referenced: set = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Name) and isinstance(node.ctx, _ast.Load):
            referenced.add(node.id)
        # Also catch attribute access like `status.HTTP_200_OK` --
        # the base name `status` counts as a reference.
        elif isinstance(node, _ast.Attribute):
            base = node.value
            while isinstance(base, _ast.Attribute):
                base = base.value
            if isinstance(base, _ast.Name):
                referenced.add(base.id)

    # Collect names already brought into scope by imports + local defs.
    in_scope: set = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.ImportFrom):
            for alias in node.names:
                in_scope.add(alias.asname or alias.name)
        elif isinstance(node, _ast.Import):
            for alias in node.names:
                in_scope.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                                _ast.ClassDef)):
            in_scope.add(node.name)
        elif isinstance(node, _ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, _ast.Name):
                    in_scope.add(tgt.id)

    # For each referenced name that is missing AND known to us,
    # group by source module.
    to_add: dict = {}
    for name in sorted(referenced):
        if name in in_scope:
            continue
        if name not in KNOWN_SCAFFOLD_NAMES:
            continue
        module, symbol = KNOWN_SCAFFOLD_NAMES[name]
        to_add.setdefault(module, []).append(symbol)

    if not to_add:
        return content

    # Build new import lines.
    new_imports = []
    for module in sorted(to_add):
        names = sorted(set(to_add[module]))
        new_imports.append(f"from {module} import {', '.join(names)}")

    # Insert AFTER the last existing import (preserves any
    # module-level comments and docstring).
    lines = content.splitlines(keepends=True)
    last_import_idx = -1
    for i, ln in enumerate(lines):
        stripped = ln.lstrip()
        if stripped.startswith("from ") or stripped.startswith("import "):
            last_import_idx = i
    insert_at = last_import_idx + 1 if last_import_idx >= 0 else 0
    new_block = "".join(line + "\n" for line in new_imports)
    lines.insert(insert_at, new_block)
    new_content = "".join(lines)

    # Final safety: re-parse to confirm we didn't break anything.
    try:
        _ast.parse(new_content)
    except SyntaxError:
        return content
    return new_content


def _auto_stub_missing_contract_endpoints(
    test_results: dict, generated_files: dict
) -> dict:
    """For every CONTRACT MISS or reachability 404, synthesize a working handler
    stub and add it to the appropriate route file.

    Sources parsed:
      - contract / contract_advisory logs: "CONTRACT MISS: METHOD /path ..."
      - reachability log: "METHOD /path -> route does not exist"

    This is the safety net: after the generator + targeted retry still leave gaps,
    the stub ensures the endpoint exists and returns DB-queried data rather than a 404.
    Idempotent -- files that already have the handler are unchanged.
    """
    _log = structlog.get_logger("debugger")
    contract_log = (test_results.get("logs", {}) or {}).get("contract", "") or ""
    advisory_log = (test_results.get("logs", {}) or {}).get("contract_advisory", "") or ""
    combined = contract_log + "\n" + advisory_log

    # Also parse reachability failures (different log format).
    # Only stub "route does not exist" -- 405/500 failures are handled by other helpers.
    reachability_log = (test_results.get("logs", {}) or {}).get("reachability", "") or ""

    if "CONTRACT MISS" not in combined and "route does not exist" not in reachability_log:
        return {}

    missing_eps = []
    for m in _GENERAL_MISS_RE.finditer(combined):
        method = m.group("method").upper()
        path = m.group("path").split()[0].rstrip(";,")
        if not path.startswith("/api/"):
            continue
        missing_eps.append({"method": method, "path": path, "auth": True})

    for m in _REACHABILITY_MISS_RE.finditer(reachability_log):
        method = m.group("method").upper()
        path = m.group("path").rstrip(";,")
        if not path.startswith("/api/"):
            continue
        missing_eps.append({"method": method, "path": path, "auth": True})

    if not missing_eps:
        return {}

    # Deduplicate by (method, path).
    seen: set[tuple] = set()
    unique_eps = []
    for ep in missing_eps:
        k = (ep["method"], ep["path"])
        if k not in seen:
            seen.add(k)
            unique_eps.append(ep)

    fixes: dict[str, str] = {}
    _ROUTE_FILE_RE = re.compile(
        r"^/api/(.+)$"
    )

    for ep in unique_eps:
        method = ep["method"].lower()
        path = ep["path"]
        m_path = _ROUTE_FILE_RE.match(path)
        if not m_path:
            continue
        rest = m_path.group(1)
        # Determine target file (mirrors _route_file_for_endpoint logic).
        if rest.startswith("auth/") or rest == "auth":
            target = "backend/app/routes/auth_routes.py"
        elif rest.startswith("admin/"):
            tail = rest[6:].split("/")[0].replace("-", "_")
            target = f"backend/app/routes/admin_{tail}.py"
        else:
            tail = rest.split("/")[0].replace("-", "_")
            target = f"backend/app/routes/{tail}.py"

        probe = "/" + rest
        handler_path = _stub_handler_path(target, probe)
        func_name = _stub_func_name(method, probe)
        stub = _generate_handler_stub(method, handler_path, func_name, ep)

        # Get current content (may have been modified by an earlier stub this cycle).
        current = fixes.get(target, generated_files.get(target, ""))

        if not current:
            # Create a new route file with scaffold.
            prefix = _stub_router_prefix(target)
            tag = target.split("/")[-1][:-3].replace("_routes", "").replace("_", "-")
            # Resolve the actual model name from models.py instead of defaulting to User.
            _models_py = generated_files.get("backend/app/models.py", "")
            _requested_model = _admin_path_to_model_name("/" + rest)
            _model_import = (
                _resolve_model_name(_requested_model, _models_py, _log)
                if _models_py else None
            ) or "User"
            current = (
                "from fastapi import APIRouter, Depends, HTTPException\n"
                "from sqlalchemy.orm import Session\n"
                "from app.database import get_db\n"
                "from app.auth import get_current_user\n"
                f"from app.models import {_model_import}\n\n"
                f'router = APIRouter(prefix="{prefix}", tags=["{tag}"])\n\n'
            )
            _log.info("auto_stub.created_router_file", file=target, prefix=prefix)

        # Inject _serialize helper once per file.
        if "def _serialize(" not in current:
            current = current.rstrip() + "\n\n\n" + _SERIALIZE_HELPER

        # Append the stub if the decorator isn't already present.
        deco_line = f'@router.{method}("{handler_path if handler_path else "/"}")'
        if deco_line not in current:
            candidate = current.rstrip() + "\n\n\n" + stub + "\n"
            candidate = _ensure_required_imports(candidate)
            # Validate before persisting — a malformed stub template must not
            # be written to generated_files and cause a boot failure next cycle.
            if target.endswith(".py"):
                try:
                    ast.parse(candidate)
                except SyntaxError as _syn:
                    _log.error(
                        "auto_stub.syntax_check_failed",
                        file=target,
                        endpoint=f"{ep['method']} {ep['path']}",
                        error=str(_syn)[:200],
                        preview=candidate[:400],
                    )
                    continue
            current = candidate
            fixes[target] = current
            _log.info(
                "auto_stub.added_endpoint",
                file=target,
                endpoint=f"{ep['method']} {ep['path']}",
            )
        elif target in fixes or target not in generated_files:
            fixes[target] = current

    return fixes


def _ensure_crud_completeness(
    test_results: dict, generated_files: dict
) -> dict:
    """For each admin_<resource>.py that has GET list + POST create but is
    missing PUT update or DELETE, add stubs for the missing verbs.

    Admin UIs always need full CRUD; partial implementations leave dead buttons.
    Only fires when BOTH list (GET "") and create (POST "") are present.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict[str, str] = {}

    for path, content in generated_files.items():
        if not path.startswith("backend/app/routes/admin_"):
            continue
        if not path.endswith(".py") or not content:
            continue

        has: dict[str, bool] = {}
        for verb in ("get", "post", "put", "patch", "delete"):
            has[verb] = bool(re.search(
                rf'@router\.{verb}\s*\(\s*["\']',
                content,
            ))

        # Only auto-complete when list + create both exist.
        if not (has["get"] and has["post"]):
            continue

        resource_slug = path.split("/")[-1][:-3][6:]  # admin_orders → orders
        resource_tag = resource_slug.replace("_", "-")
        additions: list[str] = []

        if not has["put"] and not has["patch"]:
            additions.append(_generate_handler_stub(
                method="put",
                handler_path=f"/{{{resource_slug}_id}}",
                func_name=f"update_{resource_slug}",
                endpoint={
                    "method": "PUT",
                    "path": f"/api/admin/{resource_tag}/{{{resource_slug}_id}}",
                    "auth": True,
                },
            ))
        if not has["delete"]:
            additions.append(_generate_handler_stub(
                method="delete",
                handler_path=f"/{{{resource_slug}_id}}",
                func_name=f"delete_{resource_slug}",
                endpoint={
                    "method": "DELETE",
                    "path": f"/api/admin/{resource_tag}/{{{resource_slug}_id}}",
                    "auth": True,
                },
            ))

        if additions:
            new_content = content
            if "def _serialize(" not in new_content:
                new_content = new_content.rstrip() + "\n\n\n" + _SERIALIZE_HELPER
            new_content = new_content.rstrip() + "\n\n\n" + "\n\n\n".join(additions) + "\n"
            fixes[path] = new_content
            _log.info(
                "crud_completeness.added",
                file=path,
                added=[f"{'put' if 'put' in a else 'delete'} {resource_tag}" for a in additions],
            )

    return fixes


# ── FE->BE canonical field map (one direction only, no oscillation) ──────────
# Keys are backend field names that are known to differ from what the frontend
# sends. Values are the canonical frontend names. Never add A->B and B->A in
# the same dict -- that causes oscillation between fix cycles.
_RENAME_EQUIVALENCES: dict[str, str] = {
    "old_password": "current_password",
    "full_name": "name",
    "username": "email",
}


def _auto_fix_fe_be_shape_mismatches(
    test_results: dict, generated_files: dict
) -> dict:
    """Rewrite Pydantic model fields to match their known rename equivalents.

    When the fe_be_contract log shows a mismatch, this helper finds Pydantic
    classes that use the 'wrong' field name and renames them to what the
    frontend sends.  Frontend is the source of truth; backend is mutable.

    Only fires for (field, equivalent) pairs where both names are commonly
    confused (e.g. old_password vs current_password).  Idempotent.
    """
    _log = structlog.get_logger("debugger")
    fe_be_log = (test_results.get("logs", {}) or {}).get("fe_be_contract", "") or ""

    # Extract any model names mentioned in the fe_be log (if logged at warning level).
    # Fallback: scan every backend .py file for equivalences unconditionally.
    fixes: dict[str, str] = {}

    for path, content in generated_files.items():
        if not path.endswith(".py") or not content:
            continue
        if not path.startswith("backend/"):
            continue

        # Determine all needed renames by examining the ORIGINAL content.
        # This prevents bidirectional oscillation (old_password→current_password
        # and then current_password→old_password reversing it in the same pass).
        needed: list[tuple[str, str]] = []
        for be_field, fe_field in _RENAME_EQUIVALENCES.items():
            be_in_class = bool(re.search(
                rf"^\s{{4,}}{re.escape(be_field)}\s*:", content, re.MULTILINE
            ))
            fe_in_class = bool(re.search(
                rf"^\s{{4,}}{re.escape(fe_field)}\s*:", content, re.MULTILINE
            ))
            if be_in_class and not fe_in_class:
                needed.append((be_field, fe_field))

        if not needed:
            continue

        # Apply all renames in a single pass on the accumulated string.
        new = content
        for be_field, fe_field in needed:
            new = re.sub(
                rf"(^\s{{4,}}){re.escape(be_field)}(\s*:)",
                rf"\1{fe_field}\2",
                new, flags=re.MULTILINE,
            )
            new = re.sub(rf"\b{re.escape(be_field)}\b", fe_field, new)

        if new != content:
            fixes[path] = new
            _log.info("fe_be_contract.auto_renamed", file=path, renames=needed)

    return fixes


# ── SQLAlchemy / mypy noise suppressor ───────────────────────────────────────

def _add_to_import_list(import_text: str, new_name: str) -> str:
    """Insert new_name into a comma-separated import list, deduplicating."""
    names = sorted({n.strip() for n in import_text.split(",") if n.strip()} | {new_name})
    return ", ".join(names)


def _normalize_sqlalchemy_models(
    test_results: dict, generated_files: dict
) -> dict:
    """Suppress mypy call-arg noise from SQLAlchemy 2.0 Mapped[] models.

    SQLAlchemy 2.0 Mapped[] columns produce 100+ 'Unexpected keyword argument'
    mypy errors because mypy cannot infer the implicit __init__ signature without
    MappedAsDataclass.  The safest fix is to add a mypy.ini that suppresses
    call-arg errors globally (real call-arg bugs are still caught at runtime by
    the boot test).

    Idempotent -- skips when mypy.ini already disables call-arg, or when the
    models file doesn't use Mapped[].
    """
    _log = structlog.get_logger("debugger")
    models_path = "backend/app/models.py"
    models_content = generated_files.get(models_path, "")

    # Only needed when using SQLAlchemy 2.0 Mapped[] style.
    if "Mapped[" not in models_content:
        return {}

    mypy_ini_path = "backend/mypy.ini"
    existing = generated_files.get(mypy_ini_path, "")

    # Already configured.
    if "disable_error_code" in existing and "call-arg" in existing:
        return {}

    new_ini = (
        "[mypy]\n"
        "ignore_missing_imports = True\n"
        "disable_error_code = call-arg\n"
    )
    if existing and existing.strip():
        # Merge into existing mypy.ini rather than overwriting.
        if "[mypy]" in existing:
            new_ini = existing.rstrip() + "\ndisable_error_code = call-arg\n"
        else:
            new_ini = existing.rstrip() + "\n\n" + new_ini

    _log.info("normalize_sqlalchemy_models.added_mypy_ini", path=mypy_ini_path)
    return {mypy_ini_path: new_ini}


def _normalize_accent_css_var(
    test_results: dict, generated_files: dict
) -> dict:
    """Rewrite any --color-primary / --primary / --brand override in
    frontend/src/index.css to --accent, and coerce hex/rgb() values to
    'R G B' triplets so Tailwind alpha syntax works.

    Idempotent.
    """
    css_path = "frontend/src/index.css"
    css = generated_files.get(css_path, "")
    if not css:
        return {}

    synonyms = ("--color-primary", "--primary", "--brand",
                "--brand-color", "--theme-color", "--accent-color")
    changed = False
    new_css = css

    # 1) Synonym → --accent
    for syn in synonyms:
        if syn in new_css:
            new_css = re.sub(
                rf"{re.escape(syn)}\s*:\s*([^;]+);",
                r"--accent: \1;",
                new_css,
            )
            changed = True

    # 2) Coerce hex / rgb() inside any --accent declaration
    def nonlocal_changed_flag():
        nonlocal changed
        changed = True

    def _coerce(match: re.Match) -> str:
        var_name = match.group(1)
        val = match.group(2).strip()
        # Already a triplet?
        if re.fullmatch(r"\d{1,3}\s+\d{1,3}\s+\d{1,3}", val):
            return match.group(0)
        # Hex
        m = re.fullmatch(r"#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", val)
        if m:
            h = m.group(1)
            if len(h) == 3:
                h = "".join(c * 2 for c in h)
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            nonlocal_changed_flag()
            return f"{var_name}: {r} {g} {b};"
        # rgb(...)
        m = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)[^)]*\)", val)
        if m:
            nonlocal_changed_flag()
            return f"{var_name}: {int(m.group(1))} {int(m.group(2))} {int(m.group(3))};"
        return match.group(0)

    new_css = re.sub(
        r"(--accent(?:-fg)?)\s*:\s*([^;]+);",
        _coerce,
        new_css,
    )

    if not changed or new_css == css:
        return {}
    return {css_path: new_css}


def _strip_notfound_from_navbar(
    test_results: dict, generated_files: dict
) -> dict:
    """Remove any routes.js entry whose path is '*' or whose component
    is NotFoundPage, AND any hardcoded <Link to='*'> / <Link to='/404'>
    in Navbar.jsx. The catch-all should never be a navbar item.

    Idempotent.
    """
    fixes: dict = {}

    routes_path = "frontend/src/lib/routes.js"
    routes_js = generated_files.get(routes_path, "")
    if routes_js:
        original = routes_js
        # Strip object literals that mention path: "*" OR a NotFound* label/icon
        routes_js = re.sub(
            r"\{\s*[^{}]*?path\s*:\s*['\"]\*['\"][^{}]*?\}\s*,?",
            "",
            routes_js,
        )
        routes_js = re.sub(
            r"\{\s*[^{}]*?label\s*:\s*['\"](?:Not\s*Found|404)['\"][^{}]*?\}\s*,?",
            "",
            routes_js,
        )
        # Clean up double commas / trailing commas from the array.
        routes_js = re.sub(r",\s*,", ",", routes_js)
        routes_js = re.sub(r"\[\s*,", "[", routes_js)
        routes_js = re.sub(r",\s*\]", "]", routes_js)
        if routes_js != original:
            fixes[routes_path] = routes_js

    nav_path = "frontend/src/components/Navbar.jsx"
    navbar = generated_files.get(nav_path, "")
    if navbar:
        original = navbar
        # Strip <Link to="*"> ... </Link> and <Link to="/404"> variants.
        navbar = re.sub(
            r'<Link[^>]*to\s*=\s*["\'](?:\*|/404|/not-found)["\'][^>]*>.*?</Link>',
            "",
            navbar,
            flags=re.DOTALL,
        )
        navbar = re.sub(r"\n{3,}", "\n\n", navbar)
        if navbar != original:
            fixes[nav_path] = navbar

    return fixes


def _normalize_user_fk_types(
    test_results: dict, generated_files: dict
) -> dict:
    """Coerce any FK column referencing users.id to use String (VARCHAR).

    The scaffold's User.id is a plain String column (UUID hex stored as text).
    On Postgres, an FK whose column type is Integer/UUID/anything-other-than-
    String triggers:

        foreign key constraint cannot be implemented
        Key columns are of incompatible types: X and character varying.

    SQLite is permissive about this and lets it slide, so the failure only
    surfaces in production on Neon. This helper rewrites FK columns to use
    String when the referenced column is users.id.

    Patterns rewritten (covers SQLAlchemy 1.x Column and 2.0 mapped_column):
      Column(Integer, ForeignKey("users.id"))          -> Column(String, ForeignKey("users.id"))
      Column(UUID, ForeignKey("users.id"))             -> Column(String, ForeignKey("users.id"))
      Column(GUID(), ForeignKey("users.id"))           -> Column(String, ForeignKey("users.id"))
      mapped_column(Integer, ForeignKey("users.id"))   -> mapped_column(String, ForeignKey("users.id"))
      Mapped[int] = mapped_column(..., FK("users.id")) -> Mapped[str] = mapped_column(String, FK(...))

    Idempotent — does nothing when columns already use String.
    """
    _log = structlog.get_logger("debugger")
    out: dict = {}

    # Wrong types we know about. Order matters — GUID() must be matched before
    # we'd try to match bare GUID.
    wrong_type_pattern = re.compile(
        r"\b(?:Integer|BigInteger|SmallInteger|UUID|GUID\(\)|PG_UUID|"
        r"PG_UUID\(as_uuid=False\)|PG_UUID\(as_uuid=True\)|UUID\(as_uuid=False\)|"
        r"UUID\(as_uuid=True\))\b"
    )

    fk_users_id = re.compile(
        r"""(Column|mapped_column)\s*\(\s*([^,)]+?)\s*,\s*ForeignKey\(\s*["']users\.id["']""",
        re.MULTILINE,
    )

    for path, content in list(generated_files.items()):
        # Only touch python model files.
        if not path.endswith(".py"):
            continue
        if "ForeignKey" not in content or "users.id" not in content:
            continue
        # Don't touch the scaffold itself.
        if path.endswith("auth_models.py"):
            continue

        new_content = content
        changed = False

        # Pass 1: rewrite the type slot inside Column(...) / mapped_column(...)
        def _swap(m: re.Match) -> str:
            nonlocal changed
            ctor, type_expr = m.group(1), m.group(2).strip()
            if type_expr == "String":
                return m.group(0)  # already correct
            if not wrong_type_pattern.search(type_expr):
                # Type is something else (Text, custom) — leave it alone.
                return m.group(0)
            changed = True
            return f'{ctor}(String, ForeignKey("users.id"'

        new_content = fk_users_id.sub(_swap, new_content)

        # Pass 2: fix Mapped[int] annotations that pair with the rewritten FK.
        # Heuristic: lines that contain mapped_column(String, ForeignKey("users.id"))
        # but were typed Mapped[int] / Mapped[uuid.UUID].
        new_lines = []
        for line in new_content.split("\n"):
            if (
                'ForeignKey("users.id")' in line
                and "mapped_column(String" in line
                and ("Mapped[int]" in line or "Mapped[uuid.UUID]" in line)
            ):
                line = line.replace("Mapped[int]", "Mapped[str]").replace(
                    "Mapped[uuid.UUID]", "Mapped[str]"
                )
                changed = True
            new_lines.append(line)
        new_content = "\n".join(new_lines)

        if changed and new_content != content:
            out[path] = new_content
            _log.info(
                "normalize_user_fk_types.rewrote",
                path=path,
                before_sample=content[: min(len(content), 500)][-200:],
            )

    return out


def _fix_missing_post_auth_navigate(
    test_results: dict, generated_files: dict
) -> dict:
    """Ensure LoginPage / RegisterPage navigate after a successful auth call.

    The bug we are preventing: the LLM generates a LoginPage that
    correctly calls `await login({email, password})` (which sets the
    AuthContext user and stores the JWT), but then forgets to call
    `navigate('/dashboard')` afterwards. The user authenticates
    successfully but the page never redirects, so they sit on /login
    indefinitely with a populated session.

    What we do:
      1. Find every frontend page file that calls `await login(...)` or
         `await register(...)` from useAuth.
      2. Detect whether a navigate(...) call follows the await on the
         success path.
      3. If not, inject `navigate("/dashboard", { replace: true })`
         (LoginPage → /dashboard) or the auth landing route resolved
         from App.jsx route declarations.
      4. Ensure `useNavigate` is imported and `const navigate = useNavigate()`
         is declared.

    Idempotent — runs every cycle and is a no-op once the file looks
    correct.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict = {}

    # Resolve the preferred post-auth target by inspecting App.jsx.
    app_jsx = generated_files.get("frontend/src/App.jsx", "")
    mounted = set(re.findall(
        r'<Route\s+[^>]*path\s*=\s*["\']([^"\']+)["\']', app_jsx
    ))

    def _login_target() -> str:
        for cand in ("/dashboard", "/home", "/app", "/account", "/profile"):
            if cand in mounted:
                return cand
        # Pick the first non-auth, non-public route as a last resort.
        for p in mounted:
            if p in ("/", "/login", "/register", "/forgot-password", "*"):
                continue
            return p
        return "/"

    target = _login_target()

    # Pages we care about: LoginPage, RegisterPage, plus any file whose
    # body invokes await login({...}) / await register({...}) from useAuth.
    candidate_paths: list[str] = []
    for fp, content in generated_files.items():
        if not (fp.startswith("frontend/src/pages/") and fp.endswith((".jsx", ".tsx"))):
            continue
        base = fp.split("/")[-1]
        is_named_auth_page = base in (
            "LoginPage.jsx", "RegisterPage.jsx",
            "Login.jsx", "Register.jsx",
            "SignupPage.jsx", "SignUpPage.jsx",
        )
        calls_auth = bool(
            re.search(r"await\s+(?:login|register)\s*\(", content)
            and "useAuth" in content
        )
        if is_named_auth_page or calls_auth:
            candidate_paths.append(fp)

    if not candidate_paths:
        return {}

    for fp in candidate_paths:
        content = generated_files[fp]
        new_content = content

        # If there's already a navigate() call AFTER an await login/register
        # in the same function body, skip. Heuristic: look for the call
        # followed within ~400 chars by `navigate(` or `window.location`.
        already_navigates = bool(re.search(
            r"await\s+(?:login|register)\s*\([^)]*\)\s*;?\s*"
            r"(?:\}?\s*catch[^{]*\{[^}]*\}\s*)?"
            r".{0,400}?(?:navigate\s*\(|window\.location)",
            new_content,
            flags=re.DOTALL,
        ))
        if already_navigates:
            continue

        # Inject navigate("/dashboard", { replace: true }) after the await.
        # We only mutate the first matching await per file to avoid
        # double-injecting in pages that handle both login and register.
        injection_done = False

        def _inject(m: re.Match) -> str:
            nonlocal injection_done
            if injection_done:
                return m.group(0)
            injection_done = True
            base_call = m.group(0).rstrip(";").rstrip()
            return (
                f'{base_call};\n      '
                f'navigate("{target}", {{ replace: true }});'
            )

        new_content = re.sub(
            r"await\s+(?:login|register)\s*\([^)]*\)\s*;?",
            _inject,
            new_content,
            count=1,
        )

        if not injection_done:
            continue

        # Ensure useNavigate is imported from react-router-dom.
        if "useNavigate" not in new_content:
            # Try to extend an existing react-router-dom import.
            rrd_import = re.search(
                r'import\s*\{\s*([^}]*?)\}\s*from\s*["\']react-router-dom["\']',
                new_content,
            )
            if rrd_import:
                names = [n.strip() for n in rrd_import.group(1).split(",") if n.strip()]
                if "useNavigate" not in names:
                    names.append("useNavigate")
                    new_import = (
                        'import { '
                        + ", ".join(sorted(set(names)))
                        + ' } from "react-router-dom"'
                    )
                    new_content = (
                        new_content[: rrd_import.start()]
                        + new_import
                        + new_content[rrd_import.end():]
                    )
            else:
                # Add a fresh import line near the other imports.
                new_content = (
                    'import { useNavigate } from "react-router-dom"\n'
                    + new_content
                )

        # Ensure `const navigate = useNavigate()` is declared somewhere
        # before the await call. Inject right after the default export
        # function signature or after the useAuth() call.
        if "useNavigate()" not in new_content:
            # Prefer placing it next to the useAuth() destructure.
            ua = re.search(
                r"(const\s*\{[^}]*\}\s*=\s*useAuth\s*\(\s*\)\s*;?)",
                new_content,
            )
            if ua:
                new_content = new_content.replace(
                    ua.group(1),
                    ua.group(1) + "\n  const navigate = useNavigate();",
                    1,
                )
            else:
                # Place after the first `function ...() {` line.
                func = re.search(
                    r"(?:export\s+default\s+)?function\s+\w+\s*\([^)]*\)\s*\{",
                    new_content,
                )
                if func:
                    new_content = (
                        new_content[: func.end()]
                        + "\n  const navigate = useNavigate();"
                        + new_content[func.end():]
                    )

        if new_content != content:
            fixes[fp] = new_content
            _log.info(
                "fix_missing_post_auth_navigate.injected",
                file=fp,
                target=target,
            )

    return fixes


def _pin_bcrypt_for_vercel(
    test_results: dict, generated_files: dict
) -> dict:
    """Ensure api/requirements.txt pins bcrypt==4.0.1 alongside passlib.

    bcrypt 4.1+ removed the __about__.__version__ attribute that
    passlib reads at backend init, so passlib's CryptContext crashes on
    the first hash() call with:

        AttributeError: module 'bcrypt' has no attribute '__about__'

    Local tests use backend/requirements.txt (already pinned), but the
    Vercel runtime installs api/requirements.txt — which the LLM may
    generate without the pin. This helper enforces the pin in BOTH
    requirements files so the deployed app's /api/auth/register works.

    Idempotent — only edits when the pin is missing or unconstrained.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict = {}

    for req_path in (
        "api/requirements.txt",
        "backend/requirements.txt",
    ):
        content = generated_files.get(req_path)
        if content is None:
            continue
        lines = content.splitlines()
        has_passlib = False
        has_bcrypt = False
        new_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("passlib"):
                has_passlib = True
                # Normalise any >=1.7.4 or unpinned to ==1.7.4 to match.
                if "==1.7.4" not in stripped:
                    line = "passlib[bcrypt]==1.7.4"
            if stripped.startswith("bcrypt"):
                has_bcrypt = True
                if "==4.0.1" not in stripped:
                    line = "bcrypt==4.0.1"
            new_lines.append(line)

        if not has_passlib:
            continue  # not an auth project; nothing to pin

        if not has_bcrypt:
            # Insert bcrypt pin right after passlib for readability.
            inserted = False
            tmp: list[str] = []
            for line in new_lines:
                tmp.append(line)
                if not inserted and line.strip().startswith("passlib"):
                    tmp.append("bcrypt==4.0.1")
                    inserted = True
            new_lines = tmp
            if not inserted:
                new_lines.append("bcrypt==4.0.1")

        new_content = "\n".join(new_lines)
        if not new_content.endswith("\n"):
            new_content += "\n"

        if new_content != content:
            fixes[req_path] = new_content
            _log.info("pin_bcrypt_for_vercel.applied", file=req_path)

    return fixes


def _is_python_parseable(content: str) -> bool:
    """Return True when content is valid Python that ast.parse accepts."""
    try:
        ast.parse(content)
        return True
    except SyntaxError:
        return False


def _validate_or_rollback(
    files_before: dict,
    files_after: dict,
    all_fixes: dict,
    log,
) -> tuple[dict, dict]:
    """Validate every file mutated this cycle. Roll back files that don't parse.

    - Python files: ast.parse must succeed.
    - JS/JSX/TS/TSX files: brace and paren counts must balance.

    Newly created files (not present in files_before) are accepted without
    validation — they have no baseline to roll back to.

    Special case for Python: if BOTH the post-cycle content AND the pre-cycle
    content fail to parse, rolling back accomplishes nothing -- the file is
    broken in both states and we would loop forever.  In that situation we keep
    the post-cycle content (so the LLM debug pass sees the latest attempt) and
    log "escalated_to_llm_regen" instead of "rolled_back_broken_file".

    Returns (validated_files, validated_fixes) -- both dicts with broken
    mutations removed (or escalated).
    """
    validated_files = dict(files_after)
    validated_fixes = dict(all_fixes)

    for path, content in files_after.items():
        if not content:
            continue
        if path not in files_before:
            continue  # newly created file -- no rollback target
        if content == files_before[path]:
            continue  # unchanged

        ok = True
        err_msg = ""

        if path.endswith(".py"):
            try:
                ast.parse(content)
            except SyntaxError as e:
                ok = False
                err_msg = f"{e.msg} at line {e.lineno}"

        elif re.search(r"\.(jsx|tsx|js|ts)$", path):
            # Strip string literals to avoid counting braces inside strings.
            stripped = re.sub(r'`[^`]*`|"[^"]*"|\'[^\']*\'', '""', content)
            if stripped.count("{") != stripped.count("}"):
                ok = False
                err_msg = "unbalanced braces"
            elif stripped.count("(") != stripped.count(")"):
                ok = False
                err_msg = "unbalanced parens"

        if not ok:
            # For Python files, check whether the pre-cycle baseline is also broken.
            # If it is, reverting would not help -- it would restore an equally
            # broken file and cause an infinite rollback loop next cycle.
            if path.endswith(".py") and not _is_python_parseable(files_before[path]):
                log.error(
                    "debugger.escalated_to_llm_regen",
                    path=path,
                    reason=err_msg,
                    hint=(
                        "Both post-cycle and pre-cycle content fail ast.parse. "
                        "Rollback would not help. Keeping post-cycle content so "
                        "the LLM debug pass can attempt a full regeneration."
                    ),
                )
                # Keep post-cycle content; remove from fixes so the cycle is
                # not reported as "fixed" (lets the LLM debug branch run).
                validated_fixes.pop(path, None)
            else:
                log.error(
                    "debugger.rolled_back_broken_file",
                    path=path,
                    reason=err_msg,
                    hint=(
                        "A helper produced invalid syntax. Reverting to pre-cycle "
                        "content. Check the most recent helper that logged for this path."
                    ),
                )
                validated_files[path] = files_before[path]
                validated_fixes.pop(path, None)

                # If a route file is rolled back, also revert any same-cycle
                # models.py edit — _fix_missing_admin_endpoint emits both in one
                # pass, and leaving a stale `class User(Base)` in models.py after
                # the route is reverted creates a duplicate-table crash next boot.
                _MODELS_PATH = "backend/app/models.py"
                if (
                    path.startswith("backend/app/routes/")
                    and _MODELS_PATH in all_fixes
                    and _MODELS_PATH in files_before
                ):
                    log.info(
                        "debugger.rolled_back_paired_file",
                        path=_MODELS_PATH,
                        reason=f"paired with route rollback of {path}",
                    )
                    validated_files[_MODELS_PATH] = files_before[_MODELS_PATH]
                    validated_fixes.pop(_MODELS_PATH, None)

    # Universal import guardrail: heal any Python file mutated this cycle
    # that is missing scaffold imports (get_current_user, get_db, etc.).
    # Runs after rollback so we only touch files that passed validation.
    for path in list(validated_files):
        if not path.endswith(".py"):
            continue
        content = validated_files[path]
        if content == files_before.get(path):
            continue  # unchanged -- skip
        healed = _ensure_required_imports(content)
        if healed != content:
            validated_files[path] = healed
            if path in validated_fixes:
                validated_fixes[path] = healed
            log.info(
                "validate.imports_auto_added",
                path=path,
            )

    return validated_files, validated_fixes


# ── Frontend structural fixers ────────────────────────────────────────────────

def _add_import_after_last(content: str, import_line: str) -> str:
    """Insert import_line after the last existing import statement."""
    matches = list(re.finditer(r"^import\s+[^\n]+$", content, re.MULTILINE))
    if matches:
        end = matches[-1].end()
        return content[:end] + "\n" + import_line + content[end:]
    return import_line + "\n" + content


def _dedup_default_imports(
    test_results: dict, generated_files: dict,
) -> dict:
    """For each .jsx/.tsx file, detect duplicate default imports of the same
    local name (e.g., two `import AdminMessagesPage from "..."` lines that
    differ only in source path). Keep the FIRST occurrence, drop the rest.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict[str, str] = {}

    _default_import_re = re.compile(
        r'^import\s+([A-Za-z_$][\w$]*)\s+from\s+["\']([^"\']+)["\']\s*;?\s*$',
        re.MULTILINE,
    )

    for path, content in list(generated_files.items()):
        if not re.search(r"\.(jsx|tsx)$", path):
            continue
        if not content:
            continue

        seen_names: dict[str, tuple[int, int]] = {}
        to_remove: list[tuple[int, int]] = []
        for m in _default_import_re.finditer(content):
            name = m.group(1)
            span = (m.start(), m.end())
            if name in seen_names:
                to_remove.append(span)
            else:
                seen_names[name] = span

        if not to_remove:
            continue

        new_content = content
        for start, end in sorted(to_remove, reverse=True):
            # Absorb trailing newline to avoid leaving a blank line.
            if end < len(new_content) and new_content[end] == "\n":
                end += 1
            new_content = new_content[:start] + new_content[end:]

        if new_content != content:
            fixes[path] = new_content
            _log.info(
                "dedup_default_imports.applied",
                path=path,
                removed_count=len(to_remove),
            )
    return fixes


def _fix_require_auth_wrapping(
    test_results: dict, generated_files: dict,
) -> dict:
    """For each protected route in App.jsx, ensure it is wrapped in
    <RequireAuth>. Skips when auth is disabled for the project.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict[str, str] = {}

    app_path = "frontend/src/App.jsx"
    app_content = generated_files.get(app_path, "")
    if not app_content:
        return fixes

    # Skip entirely for AUTH_DISABLED projects.
    if "AUTH_DISABLED" in app_content:
        return fixes
    if "AuthContext" not in app_content and "useAuth" not in app_content:
        return fixes  # no auth in scope

    checklist = test_results.get("checklist") or []
    protected_pages = [
        item for item in checklist
        if getattr(item, "type", None) == "page"
        and getattr(item, "requires_auth", False)
    ]
    if not protected_pages:
        protected_routes = {
            "/dashboard", "/profile", "/my-plants", "/my-orders",
            "/admin", "/admin/orders", "/admin/menu",
            "/notifications", "/cart", "/checkout",
        }
    else:
        protected_routes = {
            getattr(p, "route", "") for p in protected_pages
        }

    new_content = app_content
    changed = False

    for route in protected_routes:
        if not route:
            continue
        pat = re.compile(
            rf'(<Route\s+path=["\']'
            + re.escape(route)
            + r'["\']\s+element=\{)<(\w+)(\s*/?>)\s*\}',
        )

        def _wrap(m, _changed=None):
            nonlocal changed
            comp = m.group(2)
            changed = True
            return (
                f"{m.group(1)}<RequireAuth><{comp} />"
                f"</RequireAuth>{'}'}"
            )

        new_content = pat.sub(_wrap, new_content)

    if not changed:
        return fixes

    if "RequireAuth" in new_content and 'from "@/components/RequireAuth"' not in new_content:
        new_content = _add_import_after_last(
            new_content,
            'import RequireAuth from "@/components/RequireAuth";',
        )

    if new_content != app_content:
        fixes[app_path] = new_content
        _log.info("fix_require_auth_wrapping.applied",
                  routes=sorted(protected_routes))
    return fixes


def _fix_landing_route(
    test_results: dict, generated_files: dict,
) -> dict:
    """Ensure App.jsx has a `/` route appropriate to the app's
    landing_strategy. If missing, inject based on strategy.
    """
    _log = structlog.get_logger("debugger")
    fixes: dict[str, str] = {}

    app_path = "frontend/src/App.jsx"
    app_content = generated_files.get(app_path, "")
    if not app_content:
        return fixes

    blueprint = test_results.get("blueprint") or {}
    strategy = blueprint.get("landing_strategy") or "auth_gate"

    has_root_route = bool(re.search(
        r'<Route\s+path=["\']/["\']\s+element=', app_content,
    ))
    if has_root_route:
        return fixes

    routes_open = app_content.find("<Routes>")
    if routes_open == -1:
        return fixes

    insert_at = routes_open + len("<Routes>")
    indent = "      "

    if strategy == "auth_gate":
        if "AuthGate" not in app_content:
            app_content = _add_import_after_last(
                app_content,
                'import AuthGate from "@/components/AuthGate";',
            )
            insert_at = app_content.find("<Routes>") + len("<Routes>")
        new_route = f'\n{indent}<Route path="/" element={{<AuthGate />}} />'
    else:
        # public_home or public_landing_with_login.
        # Use <HomePage /> if it's imported in App.jsx OR exists as a file.
        home_present = (
            "HomePage" in app_content
            or "frontend/src/pages/HomePage.jsx" in generated_files
        )
        target = "<HomePage />" if home_present else '<Navigate to="/home" replace />'
        new_route = f'\n{indent}<Route path="/" element={{{target}}} />'

    new_content = app_content[:insert_at] + new_route + app_content[insert_at:]

    if new_content != app_content:
        fixes[app_path] = new_content
        _log.info("fix_landing_route.injected", strategy=strategy, path=app_path)
    return fixes


def _ensure_public_home_page(
    test_results: dict, generated_files: dict,
) -> dict:
    """For public strategies, ensure HomePage.jsx exists. Stub a minimal
    one if missing -- the LLM should produce a real one, but a stub
    prevents white-screening.
    """
    _log = structlog.get_logger("debugger")
    blueprint = test_results.get("blueprint") or {}
    strategy = blueprint.get("landing_strategy", "auth_gate")
    if strategy == "auth_gate":
        return {}
    page_path = "frontend/src/pages/HomePage.jsx"
    if page_path in generated_files and generated_files[page_path]:
        return {}
    stub = (
        "export default function HomePage() {\n"
        "  return (\n"
        "    <div className=\"max-w-4xl mx-auto px-6 py-16\">\n"
        "      <h1 className=\"text-4xl font-bold text-text-default mb-4\">\n"
        "        Welcome\n"
        "      </h1>\n"
        "      <p className=\"text-text-muted\">\n"
        "        This is your home page. Add your hero, features,\n"
        "        and CTAs here.\n"
        "      </p>\n"
        "    </div>\n"
        "  );\n"
        "}\n"
    )
    _log.info("ensure_public_home_page.stubbed", path=page_path)
    return {page_path: stub}


def _fix_smoke_503_scaffold_broken(
    test_results: dict, generated_files: dict
) -> dict:
    """When smoke fails with 5xx on /api/auth/register, apply the top three
    diagnostics for a broken auth scaffold:

      1. Duplicate User class in models.py + auth_models.py
      2. Missing or unpinned bcrypt in requirements.txt
      3. models.py missing the `from app.auth_models import User` re-export

    Fires before the LLM so we fix known patterns cheaply.
    """
    _log = structlog.get_logger("debugger")

    passed = test_results.get("passed_checks") or {}
    if passed.get("smoke") not in ("failed", "advisory_failed"):
        return {}

    errors_blob = " ".join(str(e) for e in (test_results.get("errors") or []))
    smoke_log = (test_results.get("logs") or {}).get("smoke", "")
    haystack = (errors_blob + " " + smoke_log).lower()

    is_duplicate_user = (
        "table 'users' is already defined" in haystack
        or "already defined for this metadata" in haystack
    )
    is_bcrypt_issue = (
        "bcrypt" in haystack
        or "passlib" in haystack
        or "__about__" in haystack
    )
    is_scaffold_5xx = (
        "returned 5" in haystack
        or "auth scaffold broken" in haystack
        or "503" in haystack
    )

    fixes: dict = {}

    if is_duplicate_user or is_scaffold_5xx:
        fixes.update(_fix_duplicate_users_table(test_results, generated_files))

    if is_bcrypt_issue or is_scaffold_5xx:
        fixes.update(_pin_bcrypt_for_vercel(test_results, generated_files))

    if is_scaffold_5xx:
        models_path = "backend/app/models.py"
        models_content = generated_files.get(models_path, "")
        if (
            models_content
            and "from app.auth_models import User" not in models_content
            and "class User" in models_content
        ):
            new_content = "from app.auth_models import User  # noqa: F401\n" + models_content
            fixes[models_path] = new_content
            _log.info("fix_smoke_503.re_exported_user", path=models_path)

    if fixes:
        _log.info(
            "fix_smoke_503_scaffold_broken.applied",
            duplicate_user=is_duplicate_user,
            bcrypt_issue=is_bcrypt_issue,
            scaffold_5xx=is_scaffold_5xx,
            files=list(fixes.keys()),
        )

    return fixes


class DebuggerAgent:
    """Parses test errors and uses Claude to fix them.

    Incremental: fixes one file per call. Hard cap of 3 attempts per file.
    In mock mode: prepends a comment fix without calling Claude.
    """

    MAX_ATTEMPTS_PER_FILE = 3

    # Ordered list of (name, fixer_fn) for all deterministic pre-LLM fixers.
    # FIX ORDER MATTERS — see debug_and_fix docstring for constraints.
    _PRE_LLM_FIXERS: list = [
        # ── Phase -1: smoke 5xx triage (fires first — cheaply unblocks auth) ─
        ("fix_smoke_503_scaffold_broken", _fix_smoke_503_scaffold_broken),

        # ── Phase 0: structural dedup (must run before any content changes) ──
        # Cleans up duplicate top-level declarations left by a prior cycle's bad
        # fix (e.g. two Layout() in App.jsx) so subsequent helpers operate on a
        # structurally sound file.
        ("fix_dedup_top_level_declarations", _fix_dedup_top_level_declarations),

        # ── Phase 1: backend normalization sweeps (run unconditionally, every
        # cycle, BEFORE any contract-driven or LLM-driven fix) ──────────────
        # Order within this phase matters:
        #  1a. route files — strip /api from prefixes/decorators
        #  1b. seed.py     — make signature tolerant (db=None)
        #  1c. main.py     — repair lifespan to tolerant inspect-based form
        #  1d. main.py     — fix /api/api, add missing /api prefix, dedup includes
        #  1e. main.py     — wire any route files that are still missing
        ("normalize_route_files", _normalize_route_files),
        ("normalize_router_export", _normalize_router_export),
        ("normalize_seed_signature", _normalize_seed_signature),
        ("ensure_tolerant_lifespan", _ensure_tolerant_lifespan),
        ("normalize_main_includes", _normalize_main_includes),
        ("fix_missing_route_includes", _fix_missing_route_includes),
        ("auto_stub_missing_contract_endpoints", _auto_stub_missing_contract_endpoints),
        ("ensure_crud_completeness", _ensure_crud_completeness),

        # ── Phase 2: frontend structural fixes ───────────────────────────────
        ("strip_social_auth_ui", _strip_social_auth_ui),
        ("strip_oauth_backend_routes", _strip_oauth_backend_routes),
        ("fix_undefined_icon_in_map", _fix_undefined_icon_in_map),
        ("fix_incomplete_ternary_in_spread", _fix_incomplete_ternary_in_spread),
        ("strip_typescript_from_jsx", _strip_typescript_from_jsx),
        ("fix_react_namespace_import", _fix_react_namespace_import),
        ("fix_users_me_to_auth_me", _fix_users_me_to_auth_me),
        ("fix_dual_auth_call", _fix_dual_auth_call),
        ("fix_hook_inline_definitions", _fix_hook_inline_definitions),
        ("fix_unwrapped_context_providers", _fix_unwrapped_context_providers),
        ("fix_component_rendered_as_child", _fix_component_rendered_as_child),
        ("enforce_app_name_in_frontend", _enforce_app_name_in_frontend),
        ("fix_query_double_unwrap", _fix_query_double_unwrap),
        ("fix_shadcn_button_aschild", _fix_shadcn_button_aschild),
        ("fix_layout_import_inline_conflict", _fix_layout_import_inline_conflict),
        ("fix_app_jsx_layout_pattern", _fix_app_jsx_layout_pattern),
        ("fix_routes_config_consistency", _fix_routes_config_consistency_safe),
        ("fix_use_params_name_mismatch", _fix_use_params_name_mismatch),
        # Create stub page files for orphan Links targeting non-existent pages
        # so the next helper can mount them.
        ("stub_missing_pages_for_orphan_links", _stub_missing_pages_for_orphan_links),
        # Runs BEFORE the orphan rewriter so we mount existing pages first,
        # then the rewriter only redirects what's truly missing on disk.
        ("fix_mount_missing_routes_for_existing_pages", _fix_mount_missing_routes_for_existing_pages),
        ("fix_orphan_navigates_to_existing_routes", _fix_orphan_navigates_to_existing_routes),
        ("fix_remove_orphan_navbar_links", _fix_remove_orphan_navbar_links),
        ("fix_per_page_chrome_imports", _fix_per_page_chrome_imports),
        ("fix_missing_forward_ref", _fix_missing_forward_ref),
        ("auto_stub_missing_imports", _auto_stub_missing_imports),
        ("fix_unresolved_identifiers", _fix_unresolved_identifiers),
        ("fix_broken_query_fn", _fix_broken_query_fn),
        ("dedup_default_imports", _dedup_default_imports),
        ("fix_require_auth_wrapping", _fix_require_auth_wrapping),
        ("fix_landing_route", _fix_landing_route),
        ("ensure_public_home_page", _ensure_public_home_page),

        # ── Phase 3: backend structural fixes ────────────────────────────────
        ("fix_bad_module_imports", _fix_bad_module_imports),
        ("fix_auth_scaffold_integrity", _fix_auth_scaffold_integrity),
        ("fix_auth_scaffold_int_sub", _fix_auth_scaffold_int_sub),
        ("fix_route_ordering", _fix_route_ordering),
        ("fix_missing_lifespan_in_main", _fix_missing_lifespan_in_main),
        ("fix_missing_main_py", _fix_missing_main_py),
        ("add_missing_python_packages", _add_missing_python_packages),
        ("fix_legacy_column_to_mapped", _fix_legacy_column_to_mapped),
        ("restore_user_model_in_auth_routes", _restore_user_model_in_auth_routes),
        ("fix_cross_file_name_mismatch", _fix_cross_file_name_mismatch),
        ("fix_duplicate_operation_ids", _fix_duplicate_operation_ids),
        ("fix_duplicate_app_definition", _fix_duplicate_app_definition),
        ("fix_upload_static_mount", _fix_upload_static_mount),
        ("fix_auth_body_shape", _fix_auth_body_shape),
        ("fix_missing_users_me", _fix_missing_users_me),
        ("fix_missing_aggregate_endpoint", _fix_missing_aggregate_endpoint),
        ("fix_duplicate_users_table", _fix_duplicate_users_table),
        ("fix_missing_admin_endpoint", _fix_missing_admin_endpoint),
        ("auto_fix_fe_be_shape_mismatches", _auto_fix_fe_be_shape_mismatches),
        ("normalize_sqlalchemy_models", _normalize_sqlalchemy_models),
        ("normalize_user_fk_types", _normalize_user_fk_types),
        ("pin_bcrypt_for_vercel", _pin_bcrypt_for_vercel),
        ("fix_missing_post_auth_navigate", _fix_missing_post_auth_navigate),
        ("normalize_accent_css_var", _normalize_accent_css_var),
        ("strip_notfound_from_navbar", _strip_notfound_from_navbar),

        # ── Phase 4: contract-driven path/method fixes ───────────────────────
        ("fix_password_change_alias", _fix_password_change_alias),
        ("fix_method_mismatch_admin", _fix_method_mismatch_admin),
        ("fix_admin_prefix_missing", _fix_admin_prefix_missing),
        ("fix_api_prefix_in_main", _fix_api_prefix_in_main),
        ("fix_method_mismatch", _fix_method_mismatch),
        ("normalize_trailing_slashes", _normalize_trailing_slashes),
        ("fix_sqlalchemy_unmapped", _fix_sqlalchemy_unmapped),
        ("fix_body_forbidden_status", _fix_body_forbidden_status),
        ("auto_use_api_client", _auto_use_api_client),
        ("fix_invisible_text", _fix_invisible_text),

        # ── Phase 5: final dedup sweep (must be last before validate) ─────────
        # Removes duplicate top-level Layout/BareLayout/App declarations that
        # any earlier helper may have introduced. Runs after _fix_app_jsx_layout_pattern
        # so injection and dedup happen in the correct order.
        ("dedup_jsx_layout_decls", _dedup_jsx_layout_decls_fixer),
    ]

    def __init__(self) -> None:
        self.model = get_agent_model("debugger")
        self.max_tokens = get_agent_max_tokens("debugger")
        self.log = structlog.get_logger("DebuggerAgent")
        self.client = Anthropic(api_key=settings.anthropic_api_key)
        self.token_counter: dict[str, int] = {
            "input": 0, "output": 0, "calls": 0,
            "cache_write": 0, "cache_read": 0,
        }
        # Set DEMAESTRO_LLM_DEBUG=false to skip Claude calls during debug cycles.
        import os as _os
        self._llm_debug_enabled: bool = (
            _os.environ.get("DEMAESTRO_LLM_DEBUG", "true").lower() == "true"
        )
        self.validator_rejections: int = 0
        self._blind_fix_consecutive_misses: int = 0

    def _track_tokens(self, response) -> None:
        """Update token counters from a Claude API response."""
        if not response or not getattr(response, "usage", None):
            self.token_counter["calls"] = self.token_counter.get("calls", 0) + 1
            return
        usage = response.usage
        self.token_counter["input"] = (
            self.token_counter.get("input", 0)
            + (getattr(usage, "input_tokens", 0) or 0)
        )
        self.token_counter["output"] = (
            self.token_counter.get("output", 0)
            + (getattr(usage, "output_tokens", 0) or 0)
        )
        self.token_counter["cache_write"] = (
            self.token_counter.get("cache_write", 0)
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
        )
        self.token_counter["cache_read"] = (
            self.token_counter.get("cache_read", 0)
            + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        )
        self.token_counter["calls"] = self.token_counter.get("calls", 0) + 1

    def run_algorithmic_fixes(
        self,
        files: dict,
        test_results: dict | None = None,
    ) -> list:
        """Run ONLY the deterministic helpers — no Claude calls.

        Modifies `files` in-place.  Returns list of changed paths (deduped,
        order-preserving).  Safe to call without a running test cycle — pass
        test_results=None or supply results from a prior run to activate the
        contract-log–driven fixers.
        """
        tr = test_results or {"errors": [], "logs": {}, "passed_checks": {}}
        changed: dict[str, str] = {}
        working = dict(files)

        for fixer_name, fixer in type(self)._PRE_LLM_FIXERS:
            try:
                new_fixes = fixer(tr, working)
            except Exception as exc:
                self.log.warning(
                    f"run_algorithmic_fixes.{fixer_name}.exception", error=str(exc)
                )
                new_fixes = {}
            if new_fixes:
                for path, content in new_fixes.items():
                    changed[path] = content
                    working[path] = content

        files.update(changed)
        return list(changed.keys())

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
        # FIX ORDER MATTERS:
        # - TS strip MUST come before the namespace-import fixer so any damage
        #   it does (even with the import-safe logic) is immediately repaired.
        # - Auth body shape MUST come before /api/users/me injection because
        #   /users/me needs the same auth dependency the shape fix creates.
        all_fixes: dict[str, str] = {}
        # Snapshot before any fixer runs — used for duplicate-declaration rollback.
        _pre_fixer_snapshot: dict[str, str] = dict(generated_files)
        _pre_llm_fixers = type(self)._PRE_LLM_FIXERS
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

        # ── Sanity sweep: catch anything the individual fixers still missed ──────
        _broken_react_re = re.compile(r'import\s*\*\s*from\s*["\']react["\']')
        for _path, _content in list(generated_files.items()):
            if not _path.endswith((".jsx", ".tsx")):
                continue
            if _broken_react_re.search(_content):
                _fixed = _broken_react_re.sub('import * as React from "react"', _content)
                generated_files = {**generated_files, _path: _fixed}
                all_fixes[_path] = _fixed
                self.log.error("debugger.sanity_sweep.repaired_react_import", file=_path)

        for _path, _content in generated_files.items():
            if _path.endswith(".py") and "OAuth2PasswordRequestForm" in _content:
                self.log.error(
                    "debugger.sanity_sweep.auth_shape_unconverted",
                    file=_path,
                    count=_content.count("OAuth2PasswordRequestForm"),
                )

        for _path, _content in generated_files.items():
            if not _path.endswith(".py") or not _content:
                continue
            if "(Base)" not in _content and ", Base)" not in _content:
                continue
            _remaining = re.findall(r"^\s*\w+\s*=\s*Column\(", _content, re.MULTILINE)
            if _remaining:
                self.log.warning(
                    "debugger.sanity_sweep.unconverted_columns",
                    file=_path, count=len(_remaining),
                )

        # Duplicate top-level declaration sweep: roll back any JS/JSX file where
        # a fixer introduced a duplicate function or const name.
        for _path, _content in list(generated_files.items()):
            if not _path.endswith((".jsx", ".tsx", ".js")):
                continue
            if _path not in all_fixes:
                continue  # not touched this cycle — skip
            _dups = _detect_duplicate_top_level_decls(_content)
            if _dups:
                self.log.error(
                    "debug.post_fix.duplicate_declarations",
                    path=_path, symbols=_dups,
                )
                if _path in _pre_fixer_snapshot:
                    generated_files = {**generated_files, _path: _pre_fixer_snapshot[_path]}
                    all_fixes.pop(_path, None)

        # ── Hard dedup of JSX Layout/BareLayout/App declarations ─────────────
        # Runs AFTER the post-loop rollback sweep so it cannot be undone by
        # that sweep. The _dedup_jsx_layout_decls_fixer in _PRE_LLM_FIXERS is
        # a first pass; this direct call is the guaranteed safety net.
        try:
            _dedup_changed = _hard_dedup_jsx_files(generated_files)
            if _dedup_changed:
                self.log.info(
                    "debugger.hard_dedup_jsx_files.applied",
                    files=_dedup_changed,
                )
                for _dp in _dedup_changed:
                    all_fixes[_dp] = generated_files[_dp]
        except Exception as _dedup_exc:
            self.log.error("hard_dedup_jsx_files.crashed", error=str(_dedup_exc))

        # ── ASCII normalization sweep (before parse validation) ───────────────
        # Replace em-dashes and other non-ASCII punctuation in every .py file.
        # Must run BEFORE _validate_or_rollback so a helper-injected em-dash
        # is cleaned up before the validator would erroneously roll back the file.
        ascii_changed = _normalize_python_ascii(generated_files)
        for path in ascii_changed:
            if path in all_fixes or path in _pre_fixer_snapshot:
                all_fixes[path] = generated_files[path]

        # ── Universal parse-validation rollback ───────────────────────────────
        # ANY helper that produces a syntactically broken file gets rolled back
        # so the next cycle starts from a parseable baseline rather than an
        # unrecoverable broken state.
        generated_files, all_fixes = _validate_or_rollback(
            _pre_fixer_snapshot, generated_files, all_fixes, self.log
        )

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
                    # Skip the blind-fix path if it has returned nothing useful for 2+ cycles.
                    blind_misses = getattr(self, "_blind_fix_consecutive_misses", 0)
                    if blind_misses >= 2:
                        self.log.info(
                            "debug.blind_fix.skipped_consecutive_misses",
                            misses=blind_misses,
                        )
                    else:
                        self.log.info(
                            "debug.attempting_blind_fix",
                            error_preview=full_error_text[:100],
                        )
                        blind = self._blind_fix_attempt(full_error_text, generated_files)
                        if blind and blind.get("file_path") and blind["file_path"] in generated_files:
                            fp = blind["file_path"]
                            new_counts = dict(attempt_count)
                            new_counts[fp] = new_counts.get(fp, 0) + 1
                            self.log.info("debug.blind_fix.applied", file=fp)
                            self._blind_fix_consecutive_misses = 0
                            return {
                                "status": "fixed",
                                "fixed_files": {fp: blind["fixed_content"]},
                                "attempt_counts": new_counts,
                                "errors": [],
                            }
                        else:
                            self._blind_fix_consecutive_misses = blind_misses + 1
                            self.log.info(
                                "debug.blind_fix.no_result",
                                consecutive_misses=self._blind_fix_consecutive_misses,
                            )
                self.log.warning("debug.cannot_identify_file", error=full_error_text[:200])

                # ── Force agentic LLM fallback when REAL checks are failing ────
                # Reachability / smoke / contract / fe_be_contract failures point
                # at runtime crashes (500s) or shape mismatches that algorithmic
                # helpers can't pinpoint to a single file.  Don't give up here —
                # send the whole project to the LLM with a focus hint and let it
                # patch.  This is the difference between "tests_failed_recoverable"
                # and a shippable app.
                passed_checks = test_results.get("passed_checks") or {}
                FAILED_STATES = {"failed", "advisory_failed"}
                reachability_failed = passed_checks.get("reachability") in FAILED_STATES
                smoke_failed = passed_checks.get("smoke") in FAILED_STATES
                contract_failed = passed_checks.get("contract") in FAILED_STATES
                febe_failed = passed_checks.get("fe_be_contract") in FAILED_STATES

                # Build a list of error strings related to those failed checks.
                err_blob = list(test_results.get("errors") or [])
                err_lower_blob = " ".join(str(e).lower() for e in err_blob)
                has_500 = "server error: 500" in err_lower_blob
                has_reach_hint = "reachability:" in err_lower_blob or has_500
                has_smoke_hint = "smoke" in err_lower_blob and "fail" in err_lower_blob
                # smoke 5xx: log says "returned 5xx" / "scaffold broken" — no "fail" word
                has_smoke_5xx = smoke_failed and (
                    "returned 5" in err_lower_blob
                    or "scaffold broken" in err_lower_blob
                    or "internal server error" in err_lower_blob
                )
                has_contract_hint = "contract miss" in err_lower_blob

                # Pull file paths out of any stack traces in the error strings.
                trace_files = self._extract_files_from_500_traces(test_results)
                has_backend_trace = bool(trace_files)

                should_force_agentic = (
                    self._llm_debug_enabled
                    and (
                        (reachability_failed and has_reach_hint)
                        or (smoke_failed and has_smoke_hint)
                        or has_smoke_5xx
                        or (contract_failed and has_contract_hint)
                        or (febe_failed and ("fe_be" in err_lower_blob or "shape" in err_lower_blob))
                        or has_backend_trace  # trace alone is sufficient signal
                    )
                )

                if should_force_agentic:
                    # Build a focus hint tailored to whichever check is failing.
                    trace_focus = ""
                    if trace_files:
                        trace_focus = (
                            "STACK-TRACE FILES (top of trace first — fix here):\n  - "
                            + "\n  - ".join(trace_files[:5])
                            + "\n\n"
                        )

                    focus_parts: list[str] = []
                    if reachability_failed and has_reach_hint:
                        focus_parts.append(
                            "REACHABILITY: One or more endpoints return HTTP 500 "
                            "on first request. Read the relevant route handlers "
                            "and the models they query. The most common root "
                            "cause is a query against a singleton/owner row that "
                            "the seed never created — the handler does "
                            "`obj = db.query(X).first()` and then `obj.attr` "
                            "without checking for None. Patch the handlers to "
                            "handle the empty/missing case: return [] for list "
                            "endpoints, 404 for singletons, or auto-create the "
                            "singleton row when it makes sense. Also check the "
                            "seed file — if a singleton 'owner'/'profile'/"
                            "'settings' row is expected, seed it. Do NOT delete "
                            "the routes."
                        )
                    if smoke_failed and (has_smoke_hint or has_smoke_5xx):
                        focus_parts.append(
                            "SMOKE: The auth smoke test (register → login → /me) "
                            "failed. Inspect auth_routes.py and the User model. "
                            "Common causes: (a) duplicate User class — models.py "
                            "redefines User instead of importing from app.auth_models; "
                            "(b) bcrypt/passlib version mismatch — pin bcrypt>=4.0.1 "
                            "in requirements.txt; (c) password hash field name mismatch; "
                            "(d) missing 'role' default; (e) JWT secret env var name "
                            "mismatch; (f) response shape missing required fields. "
                            "The smoke_log in test_results contains the full response "
                            "body and backend stderr — read it first."
                        )
                    if contract_failed and has_contract_hint:
                        focus_parts.append(
                            "CONTRACT: Generated API does not match the "
                            "blueprint. Add the missing routes or align "
                            "paths/methods to the contract."
                        )
                    if febe_failed:
                        focus_parts.append(
                            "FE↔BE: Frontend calls and backend response shapes "
                            "diverge. Align Pydantic response_model fields with "
                            "what the frontend reads (or vice versa)."
                        )
                    focus_prefix = (
                        trace_focus
                        + "FOCUS — agentic fallback engaged:\n\n"
                        + "\n\n".join(focus_parts)
                        + "\n\n"
                    )

                    self.log.info(
                        "debug.agentic_forced",
                        reachability=reachability_failed,
                        smoke=smoke_failed,
                        contract=contract_failed,
                        fe_be=febe_failed,
                        has_trace=has_backend_trace,
                        trace_files=trace_files[:5],
                        num_errors=len(err_blob),
                    )
                    augmented_results = dict(test_results)
                    augmented_results["errors"] = [focus_prefix] + err_blob
                    try:
                        agentic_out = self._agentic_holistic_fix(
                            test_results=augmented_results,
                            generated_files=generated_files,
                        )
                    except Exception as _agentic_exc:
                        self.log.warning(
                            "debug.agentic_forced.exception",
                            error=str(_agentic_exc)[:300],
                        )
                        agentic_out = None
                    if agentic_out:
                        self.log.info(
                            "debug.agentic_forced.fixed",
                            num_files=len(agentic_out),
                        )
                        return {
                            "status": "fixed",
                            "fixed_files": agentic_out,
                            "attempt_counts": attempt_count,
                            "errors": [],
                        }
                    self.log.warning("debug.agentic_forced.no_fixes")

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
                    # External paths (node_modules, esbuild internals, etc.) are
                    # already filtered by _LIBRARY_SKIP_PREFIXES in
                    # _extract_files_from_error; any remaining misses are debug-level.
                    self.log.debug("debug_and_fix.file_not_in_generated", file=file_to_fix)
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
                elif not self._llm_debug_enabled:
                    self.log.info("debug.llm_disabled.skipped_file", file=file_to_fix)
                    continue
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
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            if hasattr(response, "usage"):
                self.token_counter["input"] += response.usage.input_tokens
                self.token_counter["output"] += response.usage.output_tokens
            self.token_counter["calls"] += 1
            text = response.content[0].text.strip()
            text = re.sub(r"^```\w*\n", "", text)
            text = re.sub(r"\n```$", "", text)
        except Exception as exc:
            self.log.warning("_blind_fix_attempt.api_error", error=str(exc))
            return None

        if not text:
            self.log.warning("_blind_fix_attempt.empty_response")
            return None

        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            self.log.warning(
                "_blind_fix_attempt.non_json_response",
                error=str(exc),
                response_preview=text[:200],
            )
            return None

        if result.get("file_path"):
            self.log.info("_blind_fix_attempt.success", file=result["file_path"])
            return result
        return None

    def _build_stable_debugger_system_prompt(self) -> str:
        """STABLE — must be byte-identical across all file repairs in a single
        debug cycle. Cached via cache_control on the system block.

        Contains: output rules (no prose, no fences, valid Python/JSX only),
        validation contract, Python-specific column rules.

        Does NOT contain: current file path, current file content, the specific
        errors to fix — those go in the user message.
        """
        # WARNING: changing this method's output bytes invalidates the
        # prompt cache. Any per-call variation MUST go in the user message.
        return (
            "You are a code repair tool. You receive ONE source file and a list "
            "of errors. You output ONLY the corrected source file content.\n\n"
            "STRICT OUTPUT RULES:\n"
            "- Output ONLY the corrected file content. Nothing else.\n"
            "- NO explanation. NO analysis. NO 'Looking at the errors...' prose.\n"
            "- NO markdown code fences (no ```python, no ```).\n"
            "- NO preamble. NO postamble. NO summary of changes.\n"
            "- If you cannot fix the file with confidence, output the file "
            "UNCHANGED. NEVER output partial code or commentary.\n"
            "- The first character of your response must be the first character "
            "of the corrected file (typically an import, comment, or definition).\n"
            "- The last character of your response must be the last character of "
            "the corrected file.\n\n"
            "If you violate any of these rules, the file will be corrupted and "
            "the build will break. The output goes DIRECTLY to disk with no "
            "post-processing.\n\n"
            "PYTHON-SPECIFIC RULES (for .py files):\n"
            "- If you see ANY `= Column(` in the file, you MUST convert it to "
            "Mapped[T] = mapped_column() form. Example:\n"
            "    BEFORE: email = Column(String, unique=True)\n"
            "    AFTER:  email: Mapped[str] = mapped_column(String, unique=True)\n"
            "  Add `from sqlalchemy.orm import Mapped, mapped_column` if missing.\n"
            "- NEVER re-introduce `= Column(` in any line you write."
        )

    def _build_variable_debugger_user_message(
        self, file_path: str, content: str, error_msg: str,
    ) -> str:
        """VARIABLE — per-file. Not cached."""
        user_msg = (
            f"File path: {file_path}\n\n"
            f"Errors to fix:\n{error_msg}\n\n"
            f"Current file content:\n{content}\n\n"
            "Output the corrected file content now (NO prose, NO fences):"
        )
        if any(content.rstrip().endswith(end) for end in (">", "/>", '">')) and \
                not content.rstrip().endswith(("/>", "}", ");")):
            user_msg += (
                "\n\nIMPORTANT: the existing file is truncated mid-element. Your "
                "fix MUST output the COMPLETE file with all opened tags closed. "
                "Do not preserve the truncated ending — finish it properly."
            )
        return user_msg

    def _fix_with_claude(
        self,
        file_path: str,
        original_content: str,
        error_msg: str,
        test_results: dict,
    ) -> str:
        """Call Claude to produce a minimal fix for the failing file.

        Validates each attempt before accepting it.  Prose / analysis responses
        are rejected and retried up to 3 times.  If all attempts produce invalid
        output, returns the ORIGINAL content unchanged so the file is never
        corrupted by an LLM response.
        """
        is_python = file_path.endswith(".py")
        is_jsx = file_path.endswith((".jsx", ".tsx", ".js", ".ts"))

        system_text = self._build_stable_debugger_system_prompt()
        user_text = self._build_variable_debugger_user_message(
            file_path, original_content, error_msg,
        )
        system_block = [
            {
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},
            }
        ]

        for attempt in range(3):
            # Attempt 0: use the configured debugger model.
            # Attempts 1-2: escalate to Opus 4.8 if validation fails.
            # Override: DEMAESTRO_MODEL_DEBUGGER_ESCALATION
            if attempt == 0:
                call_model = self.model
            else:
                call_model = os.environ.get(
                    "DEMAESTRO_MODEL_DEBUGGER_ESCALATION", "claude-opus-4-8"
                )
            try:
                with self.client.messages.stream(
                    model=call_model,
                    max_tokens=self.max_tokens,
                    system=system_block,
                    messages=[{"role": "user", "content": user_text}],
                ) as stream:
                    response = stream.get_final_message()
                if hasattr(response, "usage"):
                    self.token_counter["input"] += response.usage.input_tokens
                    self.token_counter["output"] += response.usage.output_tokens
                    self.token_counter["cache_write"] += getattr(response.usage, "cache_creation_input_tokens", 0) or 0
                    self.token_counter["cache_read"] += getattr(response.usage, "cache_read_input_tokens", 0) or 0
                self.token_counter["calls"] += 1
                fixed = response.content[0].text
            except Exception as exc:
                self.log.warning(
                    "_fix_with_claude.api_error",
                    file=file_path, attempt=attempt + 1, model=call_model, error=str(exc),
                )
                continue

            if is_python:
                ok, reason = _validate_fix_response(fixed, "python")
            elif is_jsx:
                ok, reason = _validate_fix_response(fixed, "jsx")
            else:
                ok, reason = True, "non_code_file"

            if ok:
                fixed = _strip_code_fences(fixed)

                # ── App.jsx content-preservation guards ───────────────────────
                if file_path == "frontend/src/App.jsx":
                    old_route_count = len(re.findall(r"<Route\s+path", original_content))
                    new_route_count = len(re.findall(r"<Route\s+path", fixed))
                    if new_route_count < old_route_count:
                        self.log.error(
                            "_fix_with_claude.rejected_lost_routes",
                            file=file_path,
                            before=old_route_count,
                            after=new_route_count,
                        )
                        continue
                    if len(fixed) < len(original_content) * 0.7:
                        self.log.error(
                            "_fix_with_claude.rejected_too_small",
                            file=file_path,
                            before_bytes=len(original_content),
                            after_bytes=len(fixed),
                        )
                        continue
                    # Layout/BareLayout must survive the fix
                    _layout_re = re.compile(r"\b(?:function|const)\s+Layout\b")
                    _barelayout_re = re.compile(r"\b(?:function|const)\s+BareLayout\b")
                    if _layout_re.search(original_content) and not _layout_re.search(fixed):
                        self.log.error(
                            "_fix_with_claude.rejected_lost_layout",
                            file=file_path,
                        )
                        continue
                    if "<Layout" in original_content and "<Layout" not in fixed:
                        self.log.error(
                            "_fix_with_claude.rejected_lost_layout_wrap",
                            file=file_path,
                        )
                        continue
                    if _barelayout_re.search(original_content) and not _barelayout_re.search(fixed):
                        self.log.error(
                            "_fix_with_claude.rejected_lost_barelayout",
                            file=file_path,
                        )
                        continue
                    if "<BareLayout" in original_content and "<BareLayout" not in fixed:
                        self.log.error(
                            "_fix_with_claude.rejected_lost_barelayout_wrap",
                            file=file_path,
                        )
                        continue

                self.log.info(
                    "_fix_with_claude.done",
                    file=file_path, attempt=attempt + 1, model=call_model,
                    content_length=len(fixed),
                )
                return fixed
            else:
                self.validator_rejections += 1
                self.log.warning(
                    "_fix_with_claude.rejected",
                    file=file_path, attempt=attempt + 1, model=call_model,
                    reason=reason, response_preview=fixed[:200],
                )

        # All 3 attempts produced prose / invalid code. Leave the original file
        # intact so subsequent deterministic fixers still have a valid baseline.
        self.log.error(
            "_fix_with_claude.gave_up",
            file=file_path,
            reason="all_attempts_produced_invalid_output",
        )
        return original_content

    @staticmethod
    def _extract_files_from_500_traces(test_results: dict) -> list[str]:
        """Parse error strings from test_results for stack-trace file paths.

        Vercel/Python tracebacks contain lines like:
            File "/var/task/backend/app/routes/tasks.py", line 80, in list_tasks
            File "backend/app/routes/owner.py", line 46, ...
        Also catches reachability error bodies that include the same lines
        when the global exception handler echoes them.

        Returns a deduplicated list of project-relative paths (e.g.
        "backend/app/routes/tasks.py") in priority order: route files first,
        then models, then everything else.
        """
        import re as _re
        errors = test_results.get("errors") or []
        paths: list[str] = []
        seen: set = set()

        trace_re = _re.compile(
            r'File\s+"(?:/var/task/)?([^"]+\.py)",\s*line\s*\d+',
        )
        for e in errors:
            if not isinstance(e, str):
                continue
            for m in trace_re.finditer(e):
                p = m.group(1).lstrip("./").lstrip("/")
                if "/site-packages/" in p or "/usr/lib/" in p:
                    continue
                if not p.startswith("backend"):
                    p = "backend/" + p
                if p in seen:
                    continue
                seen.add(p)
                paths.append(p)

        def _priority(path: str) -> int:
            if "/routes/" in path:
                return 0
            if "models.py" in path:
                return 1
            if "schemas" in path or "seed" in path:
                return 2
            return 3

        paths.sort(key=_priority)
        return paths

    def _agentic_holistic_fix(
        self,
        test_results: dict,
        generated_files: dict,
        max_attempts: int = 2,
        _prompt_prefix: str = "",
    ) -> dict[str, str]:
        """Last-resort agentic debug: send all errors + relevant file
        contents to Claude, receive a {path: new_content} patch map.
        Validates every patch before accepting it.
        Returns dict of {path: new_content} for accepted patches.
        """
        import json as _json
        import re as _re_h

        errors = "\n".join(test_results.get("errors") or [])
        logs = test_results.get("logs") or {}
        logs_text = "\n\n".join(
            f"=== {name} ===\n{(text or '')[:3000]}"
            for name, text in sorted(logs.items())
            if text
        )

        # Identify files referenced in errors.
        mentioned_files: set[str] = set()
        for path in generated_files:
            if path in errors or path in logs_text:
                mentioned_files.add(path)
            basename = path.split("/")[-1]
            if basename and basename in errors:
                mentioned_files.add(path)

        # Always include critical files for cross-file context.
        for critical in [
            "backend/app/models.py",
            "backend/app/routes/auth_routes.py",
            "backend/app/main.py",
            "backend/app/seed.py",
            "frontend/src/App.jsx",
        ]:
            if critical in generated_files:
                mentioned_files.add(critical)

        context_paths = sorted(mentioned_files)[:15]
        file_inventory = "\n".join(
            f"  - {p}" for p in sorted(generated_files)[:80]
        )
        relevant_dump = "\n\n".join(
            f"--- FILE: {p} ---\n{generated_files[p][:6000]}"
            for p in context_paths
        )

        prompt = (
            _prompt_prefix
            + "You are debugging a generated full-stack web app. Tests "
            "are failing after algorithmic fixers + targeted regen "
            "have already run. Remaining failures need cross-file "
            "judgment or rewrites.\n\n"
            f"TEST ERRORS:\n{errors[:4000]}\n\n"
            f"TEST LOGS:\n{logs_text[:6000]}\n\n"
            f"ALL FILE PATHS (truncated to 80):\n{file_inventory}\n\n"
            f"RELEVANT FILE CONTENTS:\n{relevant_dump}\n\n"
            "Return a JSON object:\n"
            "{\n"
            '  "analysis": "one-paragraph diagnosis",\n'
            '  "fixes": {\n'
            '    "backend/app/routes/foo.py": "full new file content",\n'
            '    "frontend/src/pages/Bar.jsx": "full new file content"\n'
            "  }\n"
            "}\n\n"
            "Common bug categories (apply the standard fix pattern when matched):\n\n"
            "1. SQLAlchemy 'Table X already defined for this MetaData':\n"
            "   A model class is defined or imported in two places. Find the\n"
            "   duplicate — usually a models.py AND a route file both define\n"
            "   the same class. Remove the duplicate; keep the definition in\n"
            "   models.py and have route files use `from app.models import X`.\n\n"
            "2. ModuleNotFoundError / ImportError for app.X:\n"
            "   A file imports `from app.X import Y` but app/X.py doesn't\n"
            "   exist. Either create the file with the expected exports, OR\n"
            "   change the import to point at the correct existing module\n"
            "   (most commonly app.auth, app.database, or app.models).\n\n"
            "3. Frontend orphan navigate/Link to a route not in App.jsx:\n"
            "   Either add the missing <Route> to App.jsx OR rewrite the\n"
            "   navigate/Link target to an existing route. If the missing\n"
            "   page genuinely needs to exist, create a basic page file with\n"
            "   a default export and wire the route in App.jsx.\n\n"
            "4. Pydantic 422 on register/login:\n"
            "   Backend Pydantic model field names don't match what the\n"
            "   frontend sends. Align field names (e.g., add AliasChoices\n"
            "   for 'email'/'username', 'name'/'full_name').\n\n"
            "5. Auth scaffold incompatibility:\n"
            "   The scaffold's auth_routes.py uses User(email=...,\n"
            "   password_hash=..., name=..., role=...). If the LLM redefined\n"
            "   User with different fields, the register endpoint 500s. Fix:\n"
            "   in models.py, ensure User imports from app.auth_models (do NOT\n"
            "   redefine), or update the User class to include expected fields.\n\n"
            "Rules:\n"
            "- Return FULL contents of any changed file, not a diff.\n"
            "- Do NOT modify scaffold files (auth.py, auth_models.py, "
            "auth_routes.py, database.py, main.py) unless an error "
            "PROVES the scaffold is wrong.\n"
            "- Match existing code style.\n"
            "- Python files must parse; JSX braces must balance.\n"
            "- If a file is correct, omit it.\n"
            "- Return ONLY the JSON object. No markdown fences.\n"
        )

        for attempt in range(1, max_attempts + 1):
            self.log.info(
                "agentic_holistic_fix.attempt",
                attempt=attempt,
                num_files_in_context=len(context_paths),
            )
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=16000,
                    messages=[{"role": "user", "content": prompt}],
                )
                self._track_tokens(response)
                raw = response.content[0].text.strip()
                raw = _re_h.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
                parsed = _json.loads(raw)
                analysis = parsed.get("analysis", "")
                fixes = parsed.get("fixes") or {}
                self.log.info(
                    "agentic_holistic_fix.parsed",
                    analysis=analysis[:300],
                    files_proposed=len(fixes),
                    paths=list(fixes.keys()),
                )

                validated: dict[str, str] = {}
                for path, content in fixes.items():
                    if not isinstance(content, str):
                        self.log.warning("agentic_holistic_fix.non_string", path=path)
                        continue
                    if path.endswith(".py"):
                        try:
                            ast.parse(content)
                        except SyntaxError as e:
                            self.log.warning(
                                "agentic_holistic_fix.py_parse_failed",
                                path=path, error=str(e),
                            )
                            continue
                    if _re_h.search(r"\.(jsx|tsx)$", path):
                        if content.count("{") != content.count("}"):
                            self.log.warning(
                                "agentic_holistic_fix.jsx_unbalanced",
                                path=path,
                            )
                            continue
                    validated[path] = content

                if validated:
                    self.log.info(
                        "agentic_holistic_fix.success",
                        patched=list(validated.keys()),
                    )
                    return validated

                self.log.warning("agentic_holistic_fix.no_valid_patches", attempt=attempt)

            except _json.JSONDecodeError as e:
                self.log.warning(
                    "agentic_holistic_fix.json_decode_failed",
                    attempt=attempt, error=str(e),
                    raw_preview=(raw[:300] if "raw" in dir() else ""),
                )
            except Exception as e:
                self.log.error("agentic_holistic_fix.exception", attempt=attempt, error=str(e))

        return {}

    def force_agentic_holistic_fix(
        self,
        test_results: dict,
        generated_files: dict,
        ping_pong_files: "set[str] | None" = None,
        failing_checks: "set[str] | None" = None,
    ) -> dict[str, str]:
        """Agentic fix variant for ping-pong situations.

        Prepends a prompt hint that steers the LLM away from the
        oscillating files so it searches for the real root cause instead
        of re-patching the same config entries.
        """
        if not self._llm_debug_enabled:
            self.log.info("force_agentic_holistic_fix.skipped", reason="llm_debug_disabled")
            return {}

        prefix_lines: list[str] = []
        if ping_pong_files:
            prefix_lines.append(
                "ALERT: The following files were patched repeatedly over "
                "the last several debug cycles but the same checks keep "
                "failing. They are almost certainly NOT the root cause. "
                "Do NOT modify them unless an error message directly "
                "points at a bug inside that exact file.\n"
                f"PING-PONG FILES: {', '.join(sorted(ping_pong_files))}\n"
            )
        if failing_checks:
            prefix_lines.append(
                f"STILL FAILING CHECKS: {', '.join(sorted(failing_checks))}\n"
                "Identify the root-cause file for these failures and fix "
                "it directly rather than adjusting config or routing files.\n"
            )

        prompt_prefix = "\n".join(prefix_lines) + ("\n\n" if prefix_lines else "")
        self.log.info(
            "force_agentic_holistic_fix.trigger",
            ping_pong_files=sorted(ping_pong_files or []),
            failing_checks=sorted(failing_checks or []),
        )
        return self._agentic_holistic_fix(
            test_results=test_results,
            generated_files=generated_files,
            _prompt_prefix=prompt_prefix,
        )
