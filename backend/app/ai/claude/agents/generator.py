"""GeneratorAgent — generates individual source files using Claude."""
import ast as _ast
import re

import structlog
from anthropic import Anthropic

from app.ai.gemini.agents.blueprint import BlueprintResponse
from app.config import settings, get_agent_model, get_agent_max_tokens
from app.models.generation_plan import FileToGenerate, GenerationPlan

_gen_log = structlog.get_logger("generator")


# ── Contract coverage helpers ─────────────────────────────────────────────────

def _endpoints_for_route_file(
    file_path: str, api_routes: list,
) -> list:
    """Return the api_routes entries whose URL belongs in this route file.

    Mapping rules:
      auth_routes.py         → /api/auth/*
      admin_<res>.py         → /api/admin/<res>/*
      <res>.py / <res>_routes.py → /api/<res>/*
    """
    if not file_path.startswith("backend/app/routes/") or not file_path.endswith(".py"):
        return []
    module = file_path.split("/")[-1][:-3]  # e.g. "orders", "admin_orders", "auth_routes"

    if module in ("__init__", "aggregate"):
        return []
    if module == "auth_routes":
        prefix = "/api/auth"
    elif module.startswith("admin_"):
        resource = module[6:].replace("_", "-")
        prefix = f"/api/admin/{resource}"
    else:
        resource = re.sub(r"_routes$", "", module).replace("_", "-")
        prefix = f"/api/{resource}"

    return [
        r for r in (api_routes or [])
        if r.get("path") == prefix or (r.get("path") or "").startswith(prefix + "/")
    ]


def _route_file_for_endpoint(ep: dict) -> str:
    """Return the canonical route file path that should own this endpoint."""
    path = ep.get("path") or ""
    m = re.match(r"^/api/(.+)$", path)
    if not m:
        return "backend/app/routes/misc_routes.py"
    rest = m.group(1)
    if rest.startswith("auth/") or rest == "auth":
        return "backend/app/routes/auth_routes.py"
    if rest.startswith("admin/"):
        tail = rest[6:].split("/")[0].replace("-", "_")
        return f"backend/app/routes/admin_{tail}.py"
    tail = rest.split("/")[0].replace("-", "_")
    return f"backend/app/routes/{tail}.py"


def _check_contract_coverage(api_routes: list, files: dict) -> list:
    """For each endpoint in api_routes, check that a matching @router decorator
    exists in the generated route files.

    Returns the list of missing endpoint dicts.
    """
    _ROUTER_PREFIX_RE = re.compile(
        r'APIRouter\s*\([^)]*prefix\s*=\s*["\']([^"\']*)["\']',
    )
    misses = []
    for ep in (api_routes or []):
        method = (ep.get("method") or "GET").lower()
        path = ep.get("path") or ""
        # Strip /api → the portion owned by the router + decorator together.
        probe = re.sub(r"^/api", "", path)
        found = False
        for fp, content in files.items():
            if not fp.startswith("backend/app/routes/") or not content:
                continue
            pm = _ROUTER_PREFIX_RE.search(content)
            router_prefix = pm.group(1) if pm else ""
            # Compute expected decorator path.
            if router_prefix and probe.startswith(router_prefix):
                deco_path = probe[len(router_prefix):] or ""
            else:
                deco_path = probe
            # Escape and allow {param} placeholders.
            pat = re.escape(deco_path).replace(r"\{[^}]+\}", r"\{[^}]+\}")
            pat = re.sub(r"\\{[^}]+\\}", r"[^\"']+", pat)
            if re.search(
                rf'@router\.{re.escape(method)}\s*\(\s*["\']{pat}["\']',
                content,
            ):
                found = True
                break
        if not found:
            misses.append(ep)
    return misses


# ── Truncation detection ──────────────────────────────────────────────────────

def _looks_truncated(content: str, path: str) -> bool:
    """Return True when the generated file appears to be cut off mid-output.

    More thorough than the existing last-char heuristic:
      - Python files: attempt ast.parse; EOF errors indicate truncation.
      - JS/JSX/TS/TSX: brace and paren balance check.
    """
    if not content or not content.strip():
        return True

    if path.endswith(".py"):
        try:
            _ast.parse(content)
            return False
        except SyntaxError as e:
            msg = str(e).lower()
            # "unexpected eof" or "was never closed" → truncation, not real bug.
            if "unexpected eof" in msg or "was never closed" in msg or "unterminated" in msg:
                return True
            return False  # real syntax error — don't retry on wrong assumption

    if path.endswith((".jsx", ".js", ".tsx", ".ts")):
        # Strip string literals to avoid counting braces inside strings.
        stripped = re.sub(r'`[^`]*`|"[^"]*"|\'[^\']*\'', '""', content)
        if stripped.count("{") != stripped.count("}"):
            return True
        if stripped.count("(") != stripped.count(")"):
            return True
        # Last non-blank line ending in an open JSX tag is a reliable truncation signal.
        last = content.rstrip().split("\n")[-1] if content.strip() else ""
        if re.search(r"<[A-Z]\w*[^/>]*$", last):
            return True

    return False

_SCAFFOLDING_PATHS: frozenset[str] = frozenset({
    "backend/requirements.txt",
    "backend/Dockerfile",
    "backend/app/__init__.py",
    "backend/app/routes/__init__.py",
    "frontend/package.json",
    "frontend/Dockerfile",
    "frontend/vite.config.js",
    "frontend/tailwind.config.js",
    "frontend/postcss.config.js",
    "frontend/index.html",
    "frontend/src/main.jsx",
    "frontend/src/index.css",
    "frontend/src/lib/utils.js",
    "frontend/src/components/ui/button.jsx",
    "frontend/src/components/ui/card.jsx",
    "frontend/src/components/ui/input.jsx",
    "frontend/src/components/ui/label.jsx",
    "frontend/src/components/ui/textarea.jsx",
    "frontend/src/components/ui/badge.jsx",
    "frontend/src/components/ui/alert.jsx",
    "frontend/src/components/ui/avatar.jsx",
    "frontend/src/components/ui/separator.jsx",
    "frontend/src/components/ui/scroll-area.jsx",
    "frontend/src/components/ui/skeleton.jsx",
    "frontend/src/components/ui/tooltip.jsx",
    "docker-compose.yml",
    ".env.example",
    "SETUP.md",
    "DATABASE.md",
    # Auth scaffold — identical boilerplate across every project, never LLM-generated.
    "backend/app/auth.py",
    "backend/app/auth_models.py",
    "backend/app/models.py",
    "backend/app/routes/auth_routes.py",
    "frontend/src/lib/auth.js",
    "frontend/src/contexts/AuthContext.jsx",
    # Common hook scaffold — canonical implementations, never LLM-generated.
    "frontend/src/hooks/useIntersectionObserver.js",
    "frontend/src/hooks/useMediaQuery.js",
    "frontend/src/hooks/useDebounce.js",
    "frontend/src/hooks/useLocalStorage.js",
    "frontend/src/hooks/index.js",
    # Footer scaffold — minimal default, never LLM-generated.
    "frontend/src/components/Footer.jsx",
    # Auth-gate scaffolds — canonical, never LLM-generated.
    "frontend/src/components/RequireAuth.jsx",
    "frontend/src/components/AuthGate.jsx",
    # API client scaffold — native fetch wrapper with auth and path normalization.
    "frontend/src/lib/api.js",
})


def _re_strip_fences(text: str) -> str:
    """Strip markdown code fences from LLM output."""
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    return text.strip()


class GeneratorAgent:
    def __init__(self) -> None:
        self.model = get_agent_model("generator")
        self.max_tokens = get_agent_max_tokens("generator")
        self.log = structlog.get_logger("GeneratorAgent")
        self.client = Anthropic(api_key=settings.anthropic_api_key)
        self.templates = self._load_templates()
        self.token_counter: dict[str, int] = {
            "input": 0, "output": 0, "calls": 0,
            "cache_write": 0, "cache_read": 0,
        }

    def _track_tokens(self, response) -> None:
        """Update token counter from a Claude API response."""
        if not response or not getattr(response, "usage", None):
            self.token_counter["calls"] += 1
            return
        usage = response.usage
        self.token_counter["input"]       += getattr(usage, "input_tokens", 0) or 0
        self.token_counter["output"]      += getattr(usage, "output_tokens", 0) or 0
        self.token_counter["cache_write"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.token_counter["cache_read"]  += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.token_counter["calls"] += 1

    def generate_file(
        self,
        file_to_gen: FileToGenerate,
        plan: GenerationPlan,
        blueprint: BlueprintResponse,
        previously_generated: dict[str, str],
        structured_requirements=None,
    ) -> str:
        """Generate a single file's content."""
        self.log.info("generate_file.start", path=file_to_gen.path)

        if file_to_gen.path in _SCAFFOLDING_PATHS:
            self.log.warning("generator.skip_scaffolding", path=file_to_gen.path)
            return ""

        if settings.mock_ai:
            content = self._build_mock_content(file_to_gen)
            self.log.info("generate_file.done.mock", path=file_to_gen.path, content_length=len(content))
            return content

        template = self.templates.get(file_to_gen.template) if file_to_gen.template != "none" else None

        # Split into stable (cached) system prompt + per-file user message.
        # The system prompt is identical across all files in a project, so the
        # cache fires on files 2-25 and cuts input costs ~20-40%.
        system_text = self._build_stable_system_prompt(plan, blueprint, structured_requirements)
        # Inject the contract checklist for route files so the generator knows
        # exactly which endpoints it must produce.
        # Wrapped in try/except so a bug here can never kill the whole pipeline.
        try:
            contract_endpoints = _endpoints_for_route_file(
                file_to_gen.path,
                blueprint.api_routes if blueprint else [],
            )
        except Exception as _ce:
            self.log.error(
                "generator.checklist_injection_failed",
                path=file_to_gen.path, error=str(_ce),
            )
            contract_endpoints = []
        user_text = self._build_variable_user_message(
            file_to_gen, previously_generated, template,
            contract_endpoints=contract_endpoints,
        )
        system_block = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]

        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_block,
            messages=[{"role": "user", "content": user_text}],
        ) as stream:
            response = stream.get_final_message()
        self._track_tokens(response)

        content = response.content[0].text
        # Detect mid-statement truncation via stop_reason, last-char heuristics,
        # and the more thorough _looks_truncated AST / brace-balance check.
        stop_reason = getattr(response, "stop_reason", None)
        stripped = content.rstrip()
        last_chars = stripped[-3:] if stripped else ""
        truncated_by_heuristic = (
            stop_reason == "max_tokens"
            or last_chars in (">", '"/>', "/>", '"',)
            or (stripped and stripped[-1] not in ("}", ")", ";", "`", "\n"))
        )
        # Strip markdown fences first so _looks_truncated sees real code.
        raw_for_check = re.sub(r"^```\w*\n", "", content.strip())
        raw_for_check = re.sub(r"\n```$", "", raw_for_check)
        truncated_by_ast = _looks_truncated(raw_for_check, file_to_gen.path)

        if truncated_by_heuristic or truncated_by_ast:
            self.log.warning(
                "generate_file.truncated_retry",
                path=file_to_gen.path,
                content_length=len(content),
                stop_reason=stop_reason,
                by_heuristic=truncated_by_heuristic,
                by_ast=truncated_by_ast,
                tail=stripped[-200:],
            )
            retry_user = user_text + (
                f"\n\n**Your previous attempt was TRUNCATED at the token limit. "
                f"Here is exactly what you produced — output the COMPLETE, FULL file "
                f"again, finishing all open tags / brackets / functions. Do NOT add "
                f"a preface. Output only the complete file code:**\n```\n{content[-1500:]}\n```\n"
            )
            with self.client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system_block,
                messages=[{"role": "user", "content": retry_user}],
            ) as stream:
                retry = stream.get_final_message()
            self._track_tokens(retry)
            content = retry.content[0].text
            self.log.info(
                "generate_file.retry_done",
                path=file_to_gen.path,
                new_length=len(content),
            )
        # Strip markdown code fences if Claude wrapped the output
        content = re.sub(r"^```\w*\n", "", content.strip())
        content = re.sub(r"\n```$", "", content)

        self.log.info("generate_file.done", path=file_to_gen.path, content_length=len(content))
        return content

    def _build_generate_prompt(
        self,
        file_to_gen: FileToGenerate,
        plan: GenerationPlan,
        blueprint: BlueprintResponse,
        previously_generated: dict[str, str],
        structured_requirements=None,
        template: str | None = None,
    ) -> str:
        deps_context = ""
        for dep_path in file_to_gen.depends_on:
            if dep_path in previously_generated:
                deps_context += f"\n\n**Dependency: {dep_path}**\n```\n{previously_generated[dep_path]}\n```"

        template_hint = (
            f"\n\n**Use this template as a starting point:**\n```\n{template}\n```" if template else ""
        )

        # Build the user requirements section
        requirements_section = ""
        if structured_requirements is not None:
            reqs_lines = []
            for r in structured_requirements.user_requirements:
                ac = "; ".join(r.acceptance_criteria or [])
                reqs_lines.append(
                    f"- [{r.priority.upper() if r.priority else 'SHOULD'}] {r.statement}"
                    + (f"  (acceptance: {ac})" if ac else "")
                )
            entities_lines = []
            for e in structured_requirements.entities:
                fields = ", ".join(e.fields or [])
                entities_lines.append(f"- {e.name}: {e.description}  (fields: {fields})")
            added = getattr(structured_requirements, "user_added_requirements", None) or []
            added_block = ""
            if added:
                added_block = (
                    "\n\n**Requirements the user added later (HIGH priority — they specifically asked):**\n"
                    + "\n".join(f"- {r}" for r in added)
                )
            requirements_section = f"""

**User requirements (apply these directly to THIS file when relevant):**
{chr(10).join(reqs_lines) if reqs_lines else "(none)"}

**Entities:**
{chr(10).join(entities_lines) if entities_lines else "(none)"}{added_block}

When writing this file, honor every requirement above that touches what the file
does — numeric values (tax rates, limits), enums (statuses, categories),
behaviors (sort orders, filters, notifications), and any explicit styling or
visual preferences. Do NOT silently substitute defaults for things the user
explicitly specified.
"""

        return f"""You are a code generator. Generate ONLY the file content, no explanations.

**File to generate:** {file_to_gen.path}
**Description:** {file_to_gen.description}
**Template:** {file_to_gen.template}

**Blueprint (for reference):**
- Database tables: {[t.name for t in blueprint.database_schema]}
- API routes: {[r.path for r in blueprint.api_routes]}
- Frontend pages: {[p.name for p in blueprint.frontend_pages]}

**Design brief / plan notes:**
{plan.notes or "(none)"}
{requirements_section}
**Dependencies (already generated):**
{deps_context if deps_context else "(none)"}
{template_hint}

**Rules:**
- NEVER generate these files — they are shipped as static auth scaffolds identical
  across every project: backend/app/auth.py, backend/app/routes/auth_routes.py,
  frontend/src/lib/auth.js, frontend/src/contexts/AuthContext.jsx.
  If you are asked to generate one of them, output an empty string.
- Use React 18, Vite, Tailwind, shadcn/ui for frontend
- Use FastAPI, Pydantic v2, SQLAlchemy for backend
- Include type hints and docstrings
- Follow the tech stack: {plan.technology_stack}
- Reference generated dependencies correctly
- Code must be production-ready
- BACKEND: you may import the pinned core packages (fastapi, uvicorn, sqlalchemy,
  pydantic, pydantic-settings, python-dotenv, python-jose, passlib, psycopg2-binary,
  python-multipart, alembic) AND any package the architect declared in
  extra_dependencies: {plan.extra_dependencies}. Do not import packages outside the
  union of those two lists. Standard library is always fine.
- FRONTEND: import only packages in the core package.json (react, react-dom,
  react-router-dom, @tanstack/react-query, axios, tailwind, the provided shadcn/ui
  components, lucide-react) PLUS any package the architect declared in
  extra_frontend_dependencies: {plan.extra_frontend_dependencies}. Do not import npm
  packages outside the union of those two lists.
- DEFENSIVE FRONTEND DATA — be strict about types, not just null:
  * useQuery destructures ALWAYS get a typed default:
        const {{ data: items = [], isLoading }} = useQuery(...)    // for lists
        const {{ data: order = null, isLoading }} = useQuery(...)  // for objects
  * Before calling .filter / .map / .reduce / .some / .find / .length on any
    value that came from useQuery, props, or storage, use:
        Array.isArray(value) ? value : []
    NOT just `(value ?? [])` — `??` does not protect against an object, string,
    or stale wrong shape.
  * When reading from localStorage / sessionStorage, ALWAYS wrap in try/catch
    AND validate the parsed shape before using it:
        const stored = (() => {{
          try {{
            const raw = JSON.parse(localStorage.getItem("KEY") ?? "[]")
            return Array.isArray(raw) ? raw : []     // for arrays
          }} catch {{ return [] }}
        }})()
    For object shapes:
        const stored = (() => {{
          try {{
            const raw = JSON.parse(localStorage.getItem("KEY") ?? "null")
            return (raw && typeof raw === "object" && !Array.isArray(raw)) ? raw : {{}}
          }} catch {{ return {{}} }}
        }})()
  * When the API may return a wrapper object like {{items: [...], total: ...}},
    read the array explicitly: `const list = Array.isArray(res?.items) ? res.items : []`.
  * At the TOP of any component that depends on fetched data, render a loading
    state while isLoading is true. Never assume async data is defined or correctly
    shaped on first render.
  * If a context provider (CartProvider, AuthProvider, etc.) initializes from
    localStorage, harden the read with the validators above — a stale payload
    must not crash the whole app on mount.
  This rule supersedes the previous, weaker version that used `(value ?? [])`.
  Example:
  // good:
  const {{ data: items = [], isLoading }} = useQuery({{...}})
  if (isLoading) return <Skeleton />
  const grouped = useMemo(() => CATEGORIES.map(c => ({{
    name: c, items: (Array.isArray(items) ? items : []).filter(i => i.category === c)
  }})), [items])
- USEQUERY PATTERN — STRICT:
  * For every protected API call, use this exact pattern:
        import api, {{ apiQuery }} from "@/lib/api";
        const {{ user }} = useAuth();
        const {{ data, isLoading }} = useQuery({{
          queryKey: ["dashboard"],
          queryFn: () => apiQuery("/dashboard"),
          enabled: !!user,
        }});
  * Use `apiQuery` (not bare `api.get`) so queryFn never returns undefined.
  * Always include `enabled: !!user` for protected endpoints.
  * Always destructure `data` and provide a sensible default in render:
        const items = data?.items ?? data ?? [];
  * NEVER write any of these broken patterns:
        queryFn: () => api.get("/x").then(r => r.data)   // api.js returns data directly — no .data
        queryFn: () => {{ api.get("/x"); }}               // missing return — always undefined
        queryFn: async () => {{ try {{ ... }} catch {{ }} }}  // swallowed error — always undefined
- REACT IMPORT — when a ui/ primitive uses React.forwardRef the ONLY valid
  namespace import is `import * as React from "react"` (note the `as React`
  — that part is required). NEVER write `import * from "react"` — that is
  a JavaScript syntax error that esbuild rejects at build time. If only
  named hooks are needed, prefer `import {{ useState, useEffect }} from "react"`
  instead of the namespace form.

- SHADCN IMPORT DISCIPLINE — only import and implement shadcn ui/ components the
  app actually uses. Do NOT speculatively import primitives the user never asked
  for (DropdownMenu, Command, AlertDialog, Accordion, NavigationMenu, etc.) unless
  the requirements explicitly need them. Every file you import from
  @/components/ui/ MUST have a full implementation generated in the same output.
  An import without a matching file causes a build failure. Prefer fewer, complete
  components over many partial ones.

- SHADCN PRIMITIVES — every file you generate under frontend/src/components/ui/
  that exports a primitive (Input, Card, CardHeader, CardTitle, CardDescription,
  CardContent, CardFooter, Button, Textarea, Label, Select, SelectTrigger,
  SelectValue, SelectContent, SelectItem, Checkbox, RadioGroup, RadioGroupItem,
  Switch, Slider, Avatar, AvatarImage, AvatarFallback, Badge, Dialog parts,
  Popover parts, Tabs parts, Tooltip parts, Sheet parts, DropdownMenu parts,
  Command parts, Form, FormItem, FormLabel, FormControl, FormDescription,
  FormMessage) MUST be defined with React.forwardRef forwarding the ref to the
  underlying DOM or Radix node:

      import * as React from "react"
      const Input = React.forwardRef(({{className, type, ...props}}, ref) => (
        <input ref={{ref}} type={{type}} className={{cn("...", className)}} {{...props}} />
      ))
      Input.displayName = "Input"
      export {{ Input }}

  Every forwardRef primitive MUST set displayName matching the exported name
  (Input, Button, Card, etc.) — required for React DevTools and the forwardRef
  compile check.

  Components wrapping a Radix primitive (Dialog, Popover, Select, etc.) must
  forward ref to the Radix element:
      const SelectTrigger = React.forwardRef(({{className, children, ...props}}, ref) => (
        <SelectPrimitive.Trigger ref={{ref}} className={{cn(...)}} {{...props}}>
          {{children}}
        </SelectPrimitive.Trigger>
      ))

  NEVER define a ui/ primitive as `function Name(props) {{ ... }}` or
  `const Name = (props) => ...` without forwardRef. The only allowed exception is
  a purely-presentational pass-through with no DOM child (e.g., a context provider
  wrapper that just returns children) — and even those must explain why in a comment.

- STYLING (CRITICAL — use these classes everywhere, never raw Tailwind color grabs):
  The scaffold's index.css defines CSS variables; tailwind.config.js exposes them
  as proper utility classes. Use ONLY these for neutral surfaces and accents:

    Surface                  → class
    Page background          → bg-surface-page
    Card / panel             → bg-surface-panel border border-surface-border
    Body text                → text-text-default   (NEVER bare text-slate-800/900)
    Secondary / muted text   → text-text-muted
    Primary button           → bg-accent text-accent-fg hover:bg-accent/90
    Accent link / icon       → text-accent

  Common component recipes:
    PAGE WRAPPER:
      ✅ <div className="bg-surface-page min-h-screen p-6">
    CARD / PANEL:
      ✅ <div className="bg-surface-panel border border-surface-border rounded-xl p-4">
    INPUTS:
      ✅ <input className="bg-surface-panel border border-surface-border text-text-default
                           rounded-md px-3 py-2 text-sm placeholder:text-text-muted" />
    NAV / SIDEBAR:
      ✅ <nav className="bg-surface-panel border-r border-surface-border w-64 h-screen p-4">
    PRIMARY BUTTON:
      ✅ <button className="bg-accent text-accent-fg hover:bg-accent/90 rounded-md px-4 py-2">
    BODY TEXT:
      ✅ <p className="text-text-default">...</p>
    MUTED TEXT:
      ✅ <p className="text-text-muted text-sm">...</p>

  ABSOLUTE BANS — these produce invisible or white-on-white text:
  - NEVER `bg-white text-white` or `bg-gray-50 text-gray-50` (invisible text).
  - NEVER `bg-slate-50 text-slate-50` or any same-tone fg/bg combo.
  - NEVER bare `text-slate-800` / `text-slate-900` — use text-text-default.
  - NEVER `dark:` Tailwind variants unless the architect plan explicitly opted
    into dark mode for this project.
  - NEVER hardcode `bg-blue-*`, `bg-emerald-*`, `bg-orange-*`, etc. for
    accent purposes — use bg-accent instead.
  - Every container must have a visible border OR a noticeably different
    background from its parent — never invisible containers.
  - If the user requirements specify a color palette, ONLY --accent is
    overridden. The surface and text variables always stay readable neutrals.
- DEFENSIVE ERROR RENDERING (CRITICAL): when displaying an error from useQuery,
  useMutation, a try/catch, or any axios/fetch failure, NEVER render the error
  object or response body directly. Always extract a string:
        const msg =
          typeof err === 'string' ? err
          : err?.response?.data?.detail?.[0]?.msg
            || (typeof err?.response?.data?.detail === 'string' ? err.response.data.detail : null)
            || err?.message
            || 'Something went wrong'
        return <p className="text-red-600">{{msg}}</p>
  Rendering an object (including {{detail: [...]}} or {{type, loc, msg, input}})
  crashes the entire React tree. The string extraction above handles strings,
  FastAPI Pydantic validation errors, plain Error instances, and generic objects.
- CURRENT-USER ENDPOINT (REQUIRED): backend/app/routes/users.py (or auth.py)
  MUST define:
      @router.get("/users/me", response_model=UserOut)
      def read_users_me(current_user: User = Depends(get_current_user)):
          return current_user
  Even if the architect blueprint does not list it explicitly, the generator
  MUST include this route. Every frontend AuthContext calls GET /api/users/me
  on mount to validate the token — the app will appear logged-out on reload
  without it.

- AUTH ENDPOINTS: auth routes always use Pydantic JSON (LoginRequest with
  `email` + `password`). Frontend always calls:
      await api.post("/api/login", {{ email, password }})
      await api.post("/api/register", {{ email, password, name }})
  Never use OAuth2PasswordRequestForm or form-encoded auth.

- CENTRALIZED API CLIENT (CRITICAL — bare fetch causes 403s on protected
  endpoints):
  Every API call from the frontend MUST go through the centralized axios client
  defined at `frontend/src/lib/api.js` (or wherever the plan names it). NEVER
  call `fetch()` directly for app endpoints. The centralized client carries
  the Authorization header automatically via interceptor; bare fetch does not.

  Pattern (correct):
      import {{ api }} from "@/lib/api";
      const {{ data }} = await api.get("/api/plants/mine");
      const created = await api.post("/api/plants", payload);
      await api.delete(`/api/plants/${{id}}`);

  Pattern (FORBIDDEN — token won't be sent):
      const res = await fetch(`${{API}}/api/plants/mine`);  // ❌ no auth header
      const data = await res.json();

  Only acceptable bare fetch is for explicitly public assets outside your API
  (CDN images, public weather APIs, etc.). All /api/ calls go through the
  centralized client. No exceptions.

- API CLIENT MUST EXIST: every frontend page or component that hits /api/ MUST
  import `api` from `@/lib/api`. This file is scaffold-provided (do NOT include
  it in your plan). It exports a pre-configured axios instance:
      import {{ api }} from "@/lib/api"
      const {{ data }} = await api.get("/api/tasks")
      const result = await api.post("/api/tasks", payload)
  In dev the Vite proxy forwards /api/* to the backend automatically — no
  baseURL or port configuration needed. In prod, VITE_API_URL is set to the
  deployed backend URL. Always use `api` for every /api/ call.

- FASTAPI STATUS-CODE DISCIPLINE (CRITICAL — boot fails if violated):
  * status_code=204 (HTTP_204_NO_CONTENT) means NO RESPONSE BODY. The function
    must NOT have a return type annotation that implies a body, MUST NOT use
    response_model=ModelSchema, and MUST return None or Response(status_code=204).
    Example of correct usage:
        @router.delete("/posts/{{post_id}}", status_code=status.HTTP_204_NO_CONTENT)
        def delete_post(post_id: int, db: Session = Depends(get_db)) -> None:
            ...
            return None
  * status_code=304 (NOT_MODIFIED) — same rule, no body.
  * For DELETE operations that you WANT to return a confirmation body, use
    status_code=200 with a {{detail: "deleted"}} response and a proper
    response_model.
  * Never combine status_code=204 with response_model=SomeModel — the FastAPI
    decorator raises an AssertionError at import time and the whole app fails
    to boot.
- FASTAPI ROUTE PATTERNS (avoid common mistakes):
  * Path parameters in the URL ({{post_id}}) MUST match the function arguments
    exactly (post_id: int).
  * Use Pydantic models for request bodies (POST/PUT/PATCH), not raw dict.
  * Always pass db: Session = Depends(get_db) when accessing the database.
  * For auth-gated routes, pass current_user via Depends(get_current_user)
    consistently everywhere.

- DO NOT add light/dark mode toggle unless the requirements explicitly
  request it. No ThemeContext.jsx, no ThemeProvider, no `dark:` Tailwind
  classes, no theme toggle button in the navbar — unless the architect
  blueprint explicitly states the project opted into dark mode. The scaffold
  ships ONE color mode (light). Keep it that way by default.

- ROUTE MOUNTING UNDER /api (CRITICAL):
  In backend/app/main.py, every `app.include_router(<router>, ...)` call MUST
  include `prefix="/api"` (or a more specific /api/<entity> prefix). The
  frontend calls all hit /api/*; without the prefix on the backend side, every
  request returns 404 or 405. Pattern:

      app.include_router(auth.router, prefix="/api", tags=["auth"])
      app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
      app.include_router(profile.router, prefix="/api/profile", tags=["profile"])

  If a router file ALREADY has its own prefix inside APIRouter(prefix="/tasks"),
  combine carefully — the final path is the concatenation. Pick ONE place to
  put the /api prefix (recommended: in main.py via include_router) so it's
  consistent across all routers.

- ROUTE PATH CONSISTENCY: never mix /api/X in some routers with /X in others.
  Whatever pattern you start the auth router with must be used by every other
  router in the same app.

- ROUTER EXPORT NAME — ABSOLUTE (wrong name causes ImportError at boot):
  Every route file in backend/app/routes/*.py MUST declare its APIRouter as the
  variable named `router`. No other name is acceptable.
    GOOD:  router = APIRouter(prefix="/orders", tags=["orders"])
    BAD:   orders_router = APIRouter(prefix="/orders")  # import fails
  main.py imports it with an alias to avoid name collision in that file:
    from app.routes.orders import router as orders_router
    app.include_router(orders_router, prefix="/api")
  The `as orders_router` alias only exists at the import site in main.py.
  The route file itself always declares `router = APIRouter(...)`.

- API PREFIX CONVENTION — ABSOLUTE (violation causes /api/api/... doubled paths):
  In route files (backend/app/routes/*.py), NEVER include /api in the APIRouter
  prefix or in handler decorator paths.
    GOOD:  router = APIRouter(prefix="/admin", tags=["admin"])
           @router.get("/orders")          # final path: /api/admin/orders
    BAD:   router = APIRouter(prefix="/api/admin")   # /api belongs in main.py
    BAD:   @router.get("/api/admin/orders")           # absolute paths in decorators
  In main.py, mount every project router with prefix="/api":
    GOOD:  app.include_router(admin_router, prefix="/api")
    BAD:   app.include_router(admin_router)          # missing /api — every call 404s
    BAD:   app.include_router(admin_router, prefix="/api/api")  # doubled prefix
  There is exactly ONE /api in any final URL. The route file contributes the
  resource segment (/admin, /orders, /profile); main.py contributes /api.

- PASSWORD CHANGE ENDPOINT (REQUIRED): the password-change endpoint MUST be
  exposed at POST /api/auth/me/password, even if the implementation lives in a
  profile module. Add an alias route in auth_routes.py when needed:
      @router.post("/me/password")
      def change_my_password(payload: PasswordChangeRequest, db=..., current_user=...):
          from app.routes.profile import change_password as _orig
          return _orig(payload, db, current_user)
  NEVER put the password-change handler only at /api/profile/password without also
  aliasing it at /api/auth/me/password — the contract always expects the auth path.

- CONTRACT IS A CHECKLIST, NOT A SUGGESTION:
  When you receive a "CONTRACT — YOU MUST PRODUCE EXACTLY THESE DECORATORS"
  block, treat every line as a hard requirement, not a guideline.
  * Count your @router.* decorators before closing the file. If you wrote N of
    M required, write the remaining (M - N) before finishing.
  * Match the URL string EXACTLY. /me and /my-orders are DIFFERENT paths.
    PATCH and PUT are DIFFERENT methods. The contract checker is case-sensitive.
  * Match the METHOD exactly. Do not substitute GET for POST or vice-versa.

- ROUTE FILE OWNERSHIP (endpoints live in exactly ONE file):
  * Auth endpoints (/api/auth/*) → auth_routes.py ONLY.
    POST /api/auth/me/password lives here, NOT in profile_routes.py.
  * Admin endpoints (/api/admin/<resource>/*) → admin_<resource>.py.
    Do NOT mix admin and public endpoints in the same file.
  * Public resource endpoints (/api/<resource>/*) → <resource>.py or
    <resource>_routes.py. One resource, one file.

- HANDLER QUALITY:
  * Every handler must query the DB or compute something real. The route must
    either return a Pydantic model from a DB query, update a row, or delete one.
  * `return []` or `pass` is only acceptable for DELETE 204 responses or
    endpoints that truly have no body. If you cannot implement a handler because
    the model fields are unknown, write `raise NotImplementedError` with a
    comment — this triggers a clear test failure rather than silent omission.
  * If a path has {{path_params}}, declare them in the function signature with
    type hints: `def get_order(order_id: int, ...)`.

- CONTRACT COMPLETENESS (CRITICAL — the single biggest source of debug cycles):
  The architect has already specified EVERY endpoint this app needs. Your job is
  to implement ALL of them, not a subset. Incomplete implementations cause contract
  failures that the debugger cannot fix (it cannot invent business logic).
  Rules:
  * Before finishing a route file, count the endpoints you have written against
    the blueprint's API routes for that resource. If you wrote 3 of 5, write the
    other 2 before moving on.
  * For resources with CRUD, all four operations (list, get, create, update/delete)
    MUST be present in the same router file unless the blueprint explicitly splits them.
  * The canonical password-change endpoint is POST /api/auth/me/password.
    Put it in auth_routes.py, NOT profile_routes.py.
  * The canonical "my orders" / "my items" endpoint is GET /api/<resource>/me.
    Do NOT use /my-orders, /mine, or /my-<resource> — these are different paths.
  * Admin endpoints (POST /api/admin/X, PUT /api/admin/X/{id}, DELETE ...) MUST
    be in admin_X.py or admin.py — never mixed into the public resource router.
  * If a route file would exceed ~200 lines for one resource, still implement all
    endpoints — do not truncate. Return the complete file.

- AGGREGATE ENDPOINTS — if the architect blueprint specifies aggregate_endpoints:
  {getattr(plan, 'aggregate_endpoints', []) or []}
  then you MUST generate backend/app/routes/aggregate.py that implements every
  aggregate endpoint listed. Each aggregate endpoint queries all relevant tables and
  returns a dict with one key per entity type (pluralised), where each value is a
  list of all rows serialised as dicts using `__table__.columns`.

  EXAMPLE for path=/api/portfolio/data, returns=[projects, education, experience, skills]:

      from fastapi import APIRouter, Depends
      from sqlalchemy.orm import Session
      from app.database import get_db
      from app.models import Project, Education, Experience, Skill

      router = APIRouter(prefix="/portfolio", tags=["portfolio"])

      def _to_dict(obj):
          return {{c.name: getattr(obj, c.name) for c in obj.__table__.columns}}

      @router.get("/data")
      def get_portfolio_data(db: Session = Depends(get_db)):
          return {{
              "projects":    [_to_dict(p) for p in db.query(Project).all()],
              "education":   [_to_dict(e) for e in db.query(Education).all()],
              "experience":  [_to_dict(x) for x in db.query(Experience).all()],
              "skills":      [_to_dict(s) for s in db.query(Skill).all()],
          }}

  Wire the router into main.py (AFTER the auth router include):
      from app.routes.aggregate import router as aggregate_router
      app.include_router(aggregate_router, prefix="/api")

  Per-entity CRUD routes remain MANDATORY even when an aggregate endpoint exists.
  The admin panel uses CRUD; the public page uses the aggregate.

- ROUTE FUNCTION NAMES — every route handler function MUST have a unique name
  across the ENTIRE backend. If two routers both need a "list users" endpoint,
  name them distinctly by resource: `list_users_admin` vs `list_users_public`.
  Duplicate function names trigger FastAPI's "Duplicate Operation ID" warning at
  startup AND break OpenAPI client generation. Never reuse function names like
  `list_items`, `create_item`, `get_item` across different router files — always
  prefix with the resource name: `list_plants`, `create_plant`, `get_plant`.

- "MY ITEMS" ENDPOINTS — for any resource the user can favorite, bookmark, save,
  or own personally, the backend MUST expose a list endpoint that returns the
  current user's items. Examples:
      GET /api/plants/favorites   → list current user's favorite plants
      GET /api/tasks/mine         → list current user's own tasks
      GET /api/users/me           → current user profile
      GET /api/posts/saved        → current user's saved posts
  NEVER expose ONLY a toggle endpoint (e.g. POST /api/plants/{id}/favorite)
  without a corresponding list endpoint. The list endpoint is what the dashboard
  needs to display "what I have".

- SQLALCHEMY 2.0 — MANDATORY Mapped[] for EVERY model (Column() causes 200+ mypy errors):
  Every column in every model MUST use Mapped[T] + mapped_column(). NO EXCEPTIONS.
  The legacy `email = Column(String)` form is FORBIDDEN — the sqlalchemy[mypy] plugin
  cannot infer constructor argument names from it, so mypy fires "Unexpected keyword
  argument 'email' for User" on EVERY model instantiation in the codebase.

  REQUIRED imports at the top of every models file:
      from datetime import datetime
      from sqlalchemy import String, Integer, DateTime, ForeignKey, Boolean, Text
      from sqlalchemy.orm import Mapped, mapped_column, relationship
      from app.database import Base

  REQUIRED form for EVERY column in EVERY model:
      class User(Base):
          __tablename__ = "users"
          id: Mapped[int] = mapped_column(primary_key=True)
          email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
          password_hash: Mapped[str] = mapped_column(String, nullable=False)
          name: Mapped[str | None] = mapped_column(String, nullable=True)
          role: Mapped[str] = mapped_column(String, default="user", nullable=False)
          created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
          updated_at: Mapped[datetime] = mapped_column(
              DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
          )
          tasks: Mapped[list["Task"]] = relationship(back_populates="user")

  USER MODEL — MANDATORY base fields (the scaffold auth.py depends on these exact names):
      id, email, password_hash, name, role, created_at, updated_at
  You MAY add project-specific fields (phone_number, address, preferences, etc.)
  but the base fields above are MANDATORY and must appear first. Do NOT rename them.
  Do NOT use `username` instead of `name` — the scaffold auth_routes.py writes `name`.

  NEVER write (causes 200+ cascade mypy errors):
      email = Column(String, unique=True)            # FORBIDDEN
      email: str = Column(String, unique=True)       # FORBIDDEN
      tasks = relationship("Task", ...)              # FORBIDDEN (missing Mapped[])

  ALWAYS write:
      email: Mapped[str] = mapped_column(String, unique=True)

- AUTH BODY = JSON ONLY (CRITICAL — 422 on every login otherwise):
  Auth routes (login + register) MUST use Pydantic BaseModel for the request
  body. NEVER use fastapi.security.OAuth2PasswordRequestForm — it requires
  form-encoded data while every modern frontend sends JSON. Required pattern:

      from pydantic import BaseModel, EmailStr
      class LoginRequest(BaseModel):
          email: EmailStr
          password: str
      class RegisterRequest(BaseModel):
          email: EmailStr
          password: str
          name: str | None = None

      @router.post("/login")
      def login(payload: LoginRequest, db: Session = Depends(get_db)):
          user = authenticate_user(db, payload.email, payload.password)
          if not user:
              raise HTTPException(401, "Invalid credentials")
          return {{"access_token": create_access_token(user.id),
                  "token_type": "bearer"}}

  Frontend:
      await api.post("/api/login", {{ email, password }})
      await api.post("/api/register", {{ email, password, name }})

  The frontend always sends JSON with `email`. Backend always reads
  Pydantic. Both sides match by construction. NO OAuth2PasswordRequestForm
  anywhere in generated code.

- NO TYPESCRIPT IN .JSX FILES (build fails if violated): files ending in .jsx
  must be PURE JavaScript. NEVER use TypeScript syntax: no `as const`, no
  `as Type`, no type annotations on parameters (`(x: string) =>`), no
  interfaces, no `: ReturnType`, no generics like `<T>`. If you need a
  readonly array, just use the array literal — JavaScript's `Object.freeze`
  if needed, never `as const`. esbuild rejects every .jsx file containing
  TypeScript syntax and the build fails.

- TRAILING SLASH CONSISTENCY: pick one — usually NO trailing slash on path
  literals. The backend FastAPI router decorators must match the frontend
  fetch paths exactly. Recommended: define backend routes WITHOUT a leading
  slash inside a router with a prefix that has NO trailing slash:
      router = APIRouter(prefix="/tasks", tags=["tasks"])
      @router.get("")     # /tasks
      @router.post("")    # /tasks
      @router.get("/{{id}}") # /tasks/{{id}}
  Frontend: api.get("/api/tasks"), api.post("/api/tasks", body). No trailing
  slashes anywhere. Trailing-slash mismatches cause 307 redirects that drop
  Authorization headers in some clients.

- LIST RESPONSE DEFENSIVE READS: when consuming a list endpoint, handle BOTH
  common backend shapes — bare array `[...]` OR wrapper `{{tasks: [...], total: N}}`:
      const {{ data }} = useQuery(...)
      const list = Array.isArray(data) ? data : (data?.tasks || data?.items || [])
  Then map `list` not `data`. This prevents "the page is empty despite the
  request succeeding."

- INVALIDATE QUERIES AFTER MUTATIONS (CRITICAL — UI looks broken otherwise):
  Every useMutation that changes server data must invalidate the queries that
  read that data:
      const qc = useQueryClient()
      const createTask = useMutation({{
        mutationFn: (payload) => api.post("/api/tasks", payload),
        onSuccess: () => {{
          qc.invalidateQueries({{ queryKey: ["tasks"] }})
          qc.invalidateQueries({{ queryKey: ["tasks-summary"] }})
        }},
      }})
  Without onSuccess invalidation, the dashboard reads stale cached data and
  the new task does not appear — even though the POST succeeded.

**File-specific requirements (follow whichever applies to {file_to_gen.path}):**
- backend/app/database.py: at the top do `from dotenv import load_dotenv; load_dotenv()`;
  read `raw_url = os.getenv("DATABASE_URL", "sqlite:///./app.db")`. Normalize Neon /
  Heroku "postgres://" URLs (SQLAlchemy 1.4+ requires "postgresql://"):
      if raw_url.startswith("postgres://"):
          raw_url = "postgresql://" + raw_url[len("postgres://"):]
  Then create the engine:
      if raw_url.startswith("sqlite"):
          engine = create_engine(raw_url, connect_args={{"check_same_thread": False}})
      else:
          engine = create_engine(raw_url, pool_pre_ping=True, pool_recycle=300)
  Define Base = declarative_base(), SessionLocal, a get_db() generator, and
  create_tables() calling Base.metadata.create_all(bind=engine). SYNC SQLAlchemy only.
- model files: `from app.database import Base` (do NOT create a new declarative_base);
  use only portable column types (Integer, String, Text, Boolean, DateTime, Float,
  ForeignKey) — no JSONB/ARRAY/server-side UUID defaults.
- backend/app/seed.py: expose seed_demo_data() that inserts REALISTIC, PLENTIFUL
  rows per entity in dependency order (parents before children). Use real-sounding
  domain content — NOT placeholder text like "Item 1" or "Test Data".
  MANDATORY MINIMUMS: for any model shown in a list/browse/menu/feed page, insert
  AT LEAST 15 rows (browse-heavy apps) or 10 rows (list apps) or 5 rows (admin).
  For MEDIA columns (image_url, avatar, cover_image), use picsum.photos URLs:
    https://picsum.photos/seed/<slug>/400/300
  For PRICE columns, use realistic ranges (restaurant: $6–$35, not $2 or $0).
  End with a `print(f"[seed] done — ...", flush=True)`. Idempotent (empty-table check).
  If auth exists, seed BOTH demo@example.com/demo1234 AND
  admin@example.com/admin1234 (both properly hashed).
  SEED FUNCTION CONTRACT — use this exact signature (db=None, not db: Session):
      def seed_demo_data(db=None):
          if db is None:
              from app.database import SessionLocal
              db = SessionLocal()
          if db.query(User).first():
              return  # already seeded
          # ... domain-relevant insertions ...
          db.commit()
  NEVER define seed_demo_data() with no parameters — the lifespan handler
  passes a db session; if the signature has no parameter, boot fails with
  "takes 0 positional arguments but 1 was given".
  The db=None default makes the function work from both the lifespan handler
  (which passes a session) and from tests (which may call it with no args).
  SEED IDEMPOTENCE — seed_demo_data MUST check before inserting:
      if db.query(User).first():
          return  # already seeded
  Without this, every restart doubles the seed rows.
- backend/app/main.py: SCAFFOLD — DO NOT REWRITE.
  MAIN.PY IS SCAFFOLD (CRITICAL): main.py is pre-seeded with CORS middleware,
  a tolerant lifespan handler, a /health endpoint, and the auth router include.
  NEVER rewrite main.py from scratch. ONLY APPEND your project-specific
  include_router calls below the marker:
      # === ROUTE INCLUDES BELOW THIS LINE — LLM appends, never replaces ===
  The lifespan handler uses inspect.signature to tolerate both seed signatures.
  NEVER remove or modify the lifespan block — removing it causes
  "no such table: users" on the very first login.
  NEVER add a second FastAPI() instantiation — there must be exactly one `app`.
  NEVER add allow_origins=["*"] — it breaks credentials mode and raises
  ValueError at startup.
  CORS SETUP — use the following pattern verbatim (no shortcuts):

      import os
      from fastapi.middleware.cors import CORSMiddleware

      # Production: explicit allowlist via env var (comma-separated URLs).
      _cors_origins = [
          origin.strip()
          for origin in os.environ.get("CORS_ORIGINS", "").split(",")
          if origin.strip()
      ]

      # Dev: allow any localhost / 127.0.0.1 port so Vite can auto-increment
      # freely. Set ALLOW_LOCALHOST_CORS=false in production for strict mode.
      _allow_localhost = (
          os.environ.get("ALLOW_LOCALHOST_CORS", "true").lower() == "true"
      )
      _localhost_regex = (
          r"https?://(localhost|127\\.0\\.0\\.1)(:\\d+)?"
          if _allow_localhost
          else None
      )

      app.add_middleware(
          CORSMiddleware,
          allow_origins=_cors_origins,
          allow_origin_regex=_localhost_regex,
          allow_credentials=True,
          allow_methods=["*"],
          allow_headers=["*"],
      )

  NEVER use allow_origins=["*"] — it is incompatible with allow_credentials=True
  and raises a ValueError at startup. NEVER hardcode localhost port numbers in
  allow_origins.
  CRITICAL: main.py MUST define `app = FastAPI(...)` exactly once and MUST NOT
  import `app` from anywhere (`from app import app` and `from app.main import app`
  are both forbidden here — other modules import from main.py, not the reverse).
  AUTH ROUTER (MANDATORY): main.py MUST include the scaffold auth router:
      from app.routes.auth_routes import router as auth_router
      app.include_router(auth_router, prefix="/api")
  Include this BEFORE any project-specific routers. The auth scaffold provides
  POST /api/auth/register, POST /api/auth/login, GET /api/auth/me automatically.
  Start main.py with this module docstring:
      \"\"\"FastAPI app entry point.
      Note: mypy may warn 'Module has no attribute X' on this file due to the
      `app` name shadowing the package name. These warnings are cosmetic —
      the runtime is unaffected. flake8 and runtime checks are authoritative.\"\"\"
  DO NOT regenerate backend/app/main.py from scratch — the file is pre-seeded
  with CORS middleware, health check, and the auth router include.  ONLY APPEND
  your project-specific include_router calls below the marker comment
  \"# Project-specific route includes\":
      from app.routes.menu_routes import router as menu_router
      app.include_router(menu_router, prefix=\"/api\")
  Never delete or replace the auth_router include or the health endpoint.
- LIST ENDPOINT RETURN TYPES — annotate handlers with the ORM type, not the schema:
  When a route returns an ORM query result but uses response_model for serialization,
  annotate the function return type with the ORM model list so mypy is consistent:
      @router.get("/tasks", response_model=list[TaskOut])
      def list_tasks(db: Session = Depends(get_db)) -> list[Task]:  # ORM type
          return db.query(Task).all()
  FastAPI's response_model serialization handles the Task → TaskOut conversion at
  runtime (via from_attributes=True). NEVER annotate the return type as `list[TaskOut]`
  for ORM queries — that makes mypy flag a type mismatch between the ORM result and
  the Pydantic schema.
- any frontend file that calls the API: import from `@/lib/api` and use the
  centralized `api` client — NEVER use bare fetch(). In dev, the Vite proxy
  forwards /api/* requests to the backend; no baseURL or port config needed.
- frontend/src/App.jsx MUST use the shared Layout/BareLayout pattern. Chrome
  (Navbar, Footer) lives ONLY in Layout, not in any page component. Required
  structure:

      import {{ BrowserRouter, Routes, Route, Outlet }} from "react-router-dom"
      import {{ QueryClient, QueryClientProvider }} from "@tanstack/react-query"
      import {{ AuthProvider }} from "@/contexts/AuthContext"
      import {{ Navbar }} from "@/components/Navbar"
      import {{ Footer }} from "@/components/Footer"
      // ↓ insert page imports here:
      import HomePage from "@/pages/HomePage"
      import LoginPage from "@/pages/LoginPage"
      // ...

      const queryClient = new QueryClient()

      /** Renders chrome (Navbar/Footer) around every main route. */
      function Layout() {{
        return (
          <div className="min-h-screen flex flex-col bg-surface-page text-text-default">
            <Navbar />
            <main className="flex-1"><Outlet /></main>
            <Footer />
          </div>
        )
      }}

      /** Auth/focused pages: no chrome, just the content. */
      function BareLayout() {{
        return (
          <div className="min-h-screen bg-surface-page text-text-default">
            <Outlet />
          </div>
        )
      }}

      function App() {{
        return (
          <QueryClientProvider client={{{{queryClient}}}}>
            <AuthProvider>
              <BrowserRouter>
                <Routes>
                  <Route element={{{{<Layout />}}}}>
                    <Route path="/" element={{{{<HomePage />}}}} />
                    {{/* other main pages go here */}}
                  </Route>
                  <Route element={{{{<BareLayout />}}}}>
                    <Route path="/login" element={{{{<LoginPage />}}}} />
                    <Route path="/register" element={{{{<RegisterPage />}}}} />
                  </Route>
                </Routes>
              </BrowserRouter>
            </AuthProvider>
          </QueryClientProvider>
        )
      }}

      export default App

  AuthContext.jsx is a scaffold file — do not redefine it. Import AuthProvider
  from "@/contexts/AuthContext" and use the `useAuth()` hook in components.
  Footer.jsx is a scaffold file — it ships automatically. Do NOT include it in
  the plan; the scaffold provides the default implementation.

- COMMON HOOKS — the scaffold ships these reusable hooks at @/hooks/:
      useIntersectionObserver — scroll-triggered animations / lazy reveals
      useMediaQuery           — responsive breakpoint checks
      useDebounce             — delayed value tracking (search inputs)
      useLocalStorage         — persistent local state
  NEVER define any of these inline in a component file. ALWAYS import from
  the scaffold:
      import {{ useIntersectionObserver }} from "@/hooks/useIntersectionObserver"
  Signature: const [ref, isVisible] = useIntersectionObserver()
  Attach `ref` to the JSX element you want to observe. `isVisible` flips
  true when the element scrolls into the viewport.
  The same applies to useMediaQuery, useDebounce, and useLocalStorage —
  import from "@/hooks/<HookName>", never redefine.

- NO COMMONJS — this is a Vite ESM bundle. NEVER use:
      const X = require("...")          // ReferenceError at runtime
      module.exports = { ... }          // silently broken in ESM
  ALWAYS use ES module syntax:
      import X from "..."
      import {{ a, b }} from "..."
      export default X
      export {{ a, b }}
  require() does not exist in the browser bundle and throws
  "ReferenceError: require is not defined" on the very first render.

- SINGLE-PAGE APPS — when the blueprint specifies an aggregate endpoint
  (aggregate_endpoints is non-empty), the public-facing page MUST call that
  endpoint via apiClient.get and destructure the result. Do NOT make multiple
  parallel requests to per-entity endpoints.

  CORRECT (one call, one place to fail):
      const {{ data }} = await api.get("/portfolio/data")
      const {{ projects, education, experience, skills }} = data

  WRONG (multiple round-trips, partial-failure states, 404s if any per-entity
  route is missing):
      const [projects, education] = await Promise.all([
          api.get("/projects"),
          api.get("/education"),
      ])

  The aggregate endpoint is faster (one network call) and avoids partial-failure
  states where some sections render and others are empty.

- AUTH SURFACE — LoginPage, RegisterPage, and any component doing login /
  register / logout MUST use the useAuth() hook exclusively:

      const {{ login, register, logout, user }} = useAuth()
      // login/register — single object arg, never positional:
      await login({{ email, password }})
      await register({{ email, password, name }})

  NEVER import `authApi` from "@/lib/auth" in a page or component — it is
  INTERNAL to AuthContext. The useAuth login/register functions handle the
  full flow (API call + token storage + header injection + /me fetch) in one
  atomic operation.

- NEVER write the dual-call pattern:
      // WRONG — second call passes a token, not credentials:
      const result = await authApi.login(email, password)
      await login(result.access_token)
  Use ONE call:
      await login({{ email, password }})
  The dual-call sends a JWT to AuthContext which then tries to log in with the
  token as a password → 422 every time.

- AUTH ARGS ARE ALWAYS OBJECTS: pass a single `{{ email, password, name? }}`
  object to login/register. Never pass positional args like `login(email, password)`.

- POST-AUTH NAVIGATION (MANDATORY) — LoginPage and RegisterPage MUST
  navigate to a real authenticated route immediately after a successful
  login/register. The useAuth login() and register() functions resolve
  to the user profile when they succeed and throw on failure, so use
  try/catch and navigate inside the try.

  CORRECT — LoginPage:
      import {{ useNavigate, Link }} from "react-router-dom"
      const navigate = useNavigate()
      const {{ login }} = useAuth()

      const onSubmit = async (e) => {{
        e.preventDefault()
        try {{
          await login({{ email, password }})
          navigate("/dashboard", {{ replace: true }})   // ← MANDATORY
        }} catch (err) {{
          setError(err?.message || "Login failed")
        }}
      }}

  WRONG — login succeeds but page never redirects (the bug we are
  preventing):
      const onSubmit = async (e) => {{
        await login({{ email, password }})
        // ← MISSING navigate(): user lands back on /login with
        //   a populated AuthContext but no visible feedback
      }}

  Target rules:
   * LoginPage / RegisterPage → navigate("/dashboard", {{ replace: true }})
     when /dashboard is mounted. Otherwise navigate to the first
     authenticated route from routes.js (the first ROUTES entry with
     requires === "auth"). Last resort: navigate("/").
   * LogoutPage / logout handler → navigate("/login", {{ replace: true }}).
   * Use {{ replace: true }} so the back button does not re-show the
     auth form after the user is logged in.

- SHARED LAYOUT — App.jsx ships with a Layout component that renders <Navbar />
  and <Footer /> ONCE around every main route via <Outlet />. Page components
  MUST NOT import or render <Navbar />, <Footer />, <Sidebar />, or other chrome
  elements themselves. Doing so produces duplicate navbars on routes that already
  have the Layout, and breaks the nav on any route that skips it.

  CORRECT — page returns only its content:
      export default function MenuPage() {{
        return (
          <section className="max-w-7xl mx-auto px-4 py-8">
            <h1>Menu</h1>
            ...
          </section>
        )
      }}

  WRONG — page embeds chrome (produces duplicate or inconsistent navbar):
      export default function MenuPage() {{
        return (
          <div>
            <Navbar />     {{/* ← FORBIDDEN — Layout already renders this */}}
            <section>...</section>
          </div>
        )
      }}

- ROUTE PLACEMENT — when generating App.jsx route entries:
  * Place MOST routes inside <Route element={{<Layout />}}> so they get chrome.
  * Place ONLY these inside <Route element={{<BareLayout />}}>:
      LoginPage, RegisterPage, ForgotPasswordPage, any 404/error page.
  * BareLayout gives those pages a clean centered-card look without a navbar.
  * Never put a page in BOTH groups. Never skip the Layout wrapper entirely.

- DYNAMIC ICONS in lists — when mapping over an array of items where each item
  has an icon component, render via the property directly or a capitalized alias.
  NEVER reference a bare `Icon` that is not in scope.

    // CORRECT — assign to capitalized const inside map body
    {{items.map((item) => {{
      const Icon = item.icon
      return <Icon className="h-5 w-5" />
    }})}}

    // CORRECT — destructure with rename (lowercase property → capitalized alias)
    {{items.map(( {{ icon: Icon, label, path }}) => (
      <Link to={{path}}><Icon className="h-5 w-5" />{{label}}</Link>
    ))}}

    // WRONG — Icon not in scope (the most common LLM bug)
    {{items.map((item) => <Icon />)}}

    // ALSO WRONG — lowercase component names crash React silently
    {{items.map((item) => <item.icon />)}}

  Always assign to a capitalized const inside the map body BEFORE rendering,
  OR destructure with a capitalized alias.  Never use a bare `<Icon>` tag unless
  Icon is actually imported or declared at the top of the file.

- ROUTES CONFIG — frontend/src/lib/routes.js is the single source of truth for
  navigation. When generating this file, include an entry for EVERY page from
  the blueprint with appropriate `show_in_nav` and `requires` values:
    - Public marketing/landing pages: requires: null
    - Login / Register:               requires: "guest"  (hide from logged-in users)
    - User dashboards / account:      requires: "auth"
    - Admin pages:                    requires: "admin"
  Export PUBLIC_NAV, GUEST_NAV, AUTH_NAV, ADMIN_NAV as filtered arrays. Example:

      import {{ Home, LogIn, UserPlus, LayoutDashboard }} from "lucide-react"
      export const ROUTES = [
        {{ path: "/",         label: "Home",      icon: Home,           show_in_nav: true,  requires: null }},
        {{ path: "/login",    label: "Login",     icon: LogIn,          show_in_nav: true,  requires: "guest" }},
        {{ path: "/register", label: "Register",  icon: UserPlus,       show_in_nav: true,  requires: "guest" }},
        {{ path: "/dashboard",label: "Dashboard", icon: LayoutDashboard,show_in_nav: true,  requires: "auth" }},
      ]
      export const PUBLIC_NAV = ROUTES.filter(r => r.show_in_nav && r.requires === null)
      export const GUEST_NAV  = ROUTES.filter(r => r.show_in_nav && r.requires === "guest")
      export const AUTH_NAV   = ROUTES.filter(r => r.show_in_nav && r.requires === "auth")
      export const ADMIN_NAV  = ROUTES.filter(r => r.show_in_nav && r.requires === "admin")

  Pages NOT in routes.js will NEVER appear in the nav.

- NAVBAR COMPONENT — frontend/src/components/Navbar.jsx MUST derive nav links
  from the routes config, NOT from a separate hand-written array that drifts:

      import {{ PUBLIC_NAV, GUEST_NAV, AUTH_NAV, ADMIN_NAV }} from "@/lib/routes"
      import {{ useAuth }} from "@/contexts/AuthContext"

      export function Navbar() {{
        const {{ user }} = useAuth()
        const isAdmin = user?.role === "admin"
        const links = [
          ...PUBLIC_NAV,
          ...(user ? AUTH_NAV : GUEST_NAV),
          ...(isAdmin ? ADMIN_NAV : []),
        ]
        return (
          <nav>
            {{links.map(( {{ path, label, icon }}) => {{
              const Icon = icon
              return (
                <Link to={{path}} key={{path}}>
                  {{Icon && <Icon className="h-4 w-4" />}} {{label}}
                </Link>
              )
            }})}}
          </nav>
        )
      }}

  App.jsx route mounting should also loop over ROUTES to avoid drift.

- CATCH-ALL ROUTE — App.jsx must include a `*` catch-all route that
  renders NotFoundPage (or redirects to "/"). The NotFoundPage component
  exists and is mounted, but it MUST NOT appear in routes.js, and the
  Navbar MUST NEVER show a link to it. Any routes.js entry whose path
  is "*" or whose component is "NotFoundPage" MUST have
  `show_in_nav: false`. Better: omit it from routes.js entirely.

- LINK-ROUTE INTEGRITY (mandatory) — EVERY <Link to="/X"> and
  navigate("/X") you emit in any page or component MUST have a matching
  <Route path="/X" element={{<...Page />}} /> in App.jsx AND a matching
  entry in frontend/src/lib/routes.js. Before you finish a page that
  references other routes, mentally re-check that those routes are mounted
  somewhere. A Link to an unmounted route is a real test failure
  (route_link_consistency / ORPHAN-LINK), not a stylistic issue. If the
  blueprint mentions /projects, /skills, /profile — there must be a
  ProjectsPage.jsx, SkillsPage.jsx, ProfilePage.jsx mounted in App.jsx
  AND listed in routes.js.

Output ONLY the file code. No markdown, no wrapper, no explanations."""

    # ── Cached-prompt helpers ─────────────────────────────────────────────────

    def _build_stable_system_prompt(
        self,
        plan: GenerationPlan,
        blueprint: BlueprintResponse,
        structured_requirements=None,
    ) -> str:
        """STABLE — must be byte-identical across all file generations in a
        single project run. Cached via cache_control on the system block.

        Contains: all Rules bullets, scaffold awareness, architectural patterns
        (Mapped[], useAuth, Layout pattern, hook imports, etc.), user
        requirements, and project blueprint.

        Does NOT contain: current file path, generated_count, cycle number, or
        anything that varies between calls.
        """
        # WARNING: changing this method's output bytes invalidates the
        # prompt cache. Any per-call variation MUST go in the user message.
        # Build user-requirements block (project-level — same for all files)
        requirements_section = ""
        if structured_requirements is not None:
            reqs_lines = []
            for r in structured_requirements.user_requirements:
                ac = "; ".join(r.acceptance_criteria or [])
                reqs_lines.append(
                    f"- [{r.priority.upper() if r.priority else 'SHOULD'}] {r.statement}"
                    + (f"  (acceptance: {ac})" if ac else "")
                )
            entities_lines = []
            for e in structured_requirements.entities:
                fields = ", ".join(e.fields or [])
                entities_lines.append(f"- {e.name}: {e.description}  (fields: {fields})")
            added = getattr(structured_requirements, "user_added_requirements", None) or []
            added_block = ""
            if added:
                added_block = (
                    "\n\n**Requirements the user added later (HIGH priority — they specifically asked):**\n"
                    + "\n".join(f"- {r}" for r in added)
                )
            requirements_section = f"""

**User requirements (apply these directly to the current file when relevant):**
{chr(10).join(reqs_lines) if reqs_lines else "(none)"}

**Entities:**
{chr(10).join(entities_lines) if entities_lines else "(none)"}{added_block}

When writing each file, honor every requirement above that touches what the file
does — numeric values (tax rates, limits), enums (statuses, categories),
behaviors (sort orders, filters, notifications), and any explicit styling or
visual preferences. Do NOT silently substitute defaults for things the user
explicitly specified.
"""

        _app_name = (getattr(structured_requirements, "app_name", None) or "").strip()

        return f"""You are a code generator. Generate ONE source file at a time, exactly as specified in the user message.

**Project blueprint:**
- App name:        {_app_name or "(see plan notes)"}
- Database tables: {[t.name for t in blueprint.database_schema]}
- API routes:      {[r.path for r in blueprint.api_routes]}
- Frontend pages:  {[p.name for p in blueprint.frontend_pages]}

**Design brief / architecture notes:**
{plan.notes or "(none)"}
{requirements_section}
**Rules:**
- NEVER generate these files — they are shipped as static auth scaffolds identical
  across every project: backend/app/auth.py, backend/app/routes/auth_routes.py,
  frontend/src/lib/auth.js, frontend/src/contexts/AuthContext.jsx.
  If you are asked to generate one of them, output an empty string.
- Use React 18, Vite, Tailwind, shadcn/ui for frontend
- Use FastAPI, Pydantic v2, SQLAlchemy for backend
- Include type hints and docstrings
- Follow the tech stack: {plan.technology_stack}
- Reference generated dependencies correctly
- Code must be production-ready
- BACKEND: you may import the pinned core packages (fastapi, uvicorn, sqlalchemy,
  pydantic, pydantic-settings, python-dotenv, python-jose, passlib, psycopg2-binary,
  python-multipart, alembic) AND any package the architect declared in
  extra_dependencies: {plan.extra_dependencies}. Do not import packages outside the
  union of those two lists. Standard library is always fine.
- FRONTEND: import only packages in the core package.json (react, react-dom,
  react-router-dom, @tanstack/react-query, axios, tailwind, the provided shadcn/ui
  components, lucide-react) PLUS any package the architect declared in
  extra_frontend_dependencies: {plan.extra_frontend_dependencies}. Do not import npm
  packages outside the union of those two lists.
- DEFENSIVE FRONTEND DATA — be strict about types, not just null:
  * useQuery destructures ALWAYS get a typed default:
        const {{ data: items = [], isLoading }} = useQuery(...)    // for lists
        const {{ data: order = null, isLoading }} = useQuery(...)  // for objects
  * Before calling .filter / .map / .reduce / .some / .find / .length on any
    value that came from useQuery, props, or storage, use:
        Array.isArray(value) ? value : []
    NOT just `(value ?? [])` — `??` does not protect against an object, string,
    or stale wrong shape.
  * When reading from localStorage / sessionStorage, ALWAYS wrap in try/catch
    AND validate the parsed shape before using it:
        const stored = (() => {{
          try {{
            const raw = JSON.parse(localStorage.getItem("KEY") ?? "[]")
            return Array.isArray(raw) ? raw : []     // for arrays
          }} catch {{ return [] }}
        }})()
    For object shapes:
        const stored = (() => {{
          try {{
            const raw = JSON.parse(localStorage.getItem("KEY") ?? "null")
            return (raw && typeof raw === "object" && !Array.isArray(raw)) ? raw : {{}}
          }} catch {{ return {{}} }}
        }})()
  * When the API may return a wrapper object like {{items: [...], total: ...}},
    read the array explicitly: `const list = Array.isArray(res?.items) ? res.items : []`.
  * At the TOP of any component that depends on fetched data, render a loading
    state while isLoading is true. Never assume async data is defined or correctly
    shaped on first render.
  * If a context provider (CartProvider, AuthProvider, etc.) initializes from
    localStorage, harden the read with the validators above — a stale payload
    must not crash the whole app on mount.
  This rule supersedes the previous, weaker version that used `(value ?? [])`.
  Example:
  // good:
  const {{ data: items = [], isLoading }} = useQuery({{...}})
  if (isLoading) return <Skeleton />
  const grouped = useMemo(() => CATEGORIES.map(c => ({{
    name: c, items: (Array.isArray(items) ? items : []).filter(i => i.category === c)
  }})), [items])
- USEQUERY PATTERN — STRICT:
  * For every protected API call, use this exact pattern:
        import api, {{ apiQuery }} from "@/lib/api";
        const {{ user }} = useAuth();
        const {{ data, isLoading }} = useQuery({{
          queryKey: ["dashboard"],
          queryFn: () => apiQuery("/dashboard"),
          enabled: !!user,
        }});
  * Use `apiQuery` (not bare `api.get`) so queryFn never returns undefined.
  * Always include `enabled: !!user` for protected endpoints.
  * Always destructure `data` and provide a sensible default in render:
        const items = data?.items ?? data ?? [];
  * NEVER write any of these broken patterns:
        queryFn: () => api.get("/x").then(r => r.data)   // api.js returns data directly — no .data
        queryFn: () => {{ api.get("/x"); }}               // missing return — always undefined
        queryFn: async () => {{ try {{ ... }} catch {{ }} }}  // swallowed error — always undefined
- REACT IMPORT — when a ui/ primitive uses React.forwardRef the ONLY valid
  namespace import is `import * as React from "react"` (note the `as React`
  — that part is required). NEVER write `import * from "react"` — that is
  a JavaScript syntax error that esbuild rejects at build time. If only
  named hooks are needed, prefer `import {{ useState, useEffect }} from "react"`
  instead of the namespace form.

- SHADCN IMPORT DISCIPLINE — only import and implement shadcn ui/ components the
  app actually uses. Do NOT speculatively import primitives the user never asked
  for (DropdownMenu, Command, AlertDialog, Accordion, NavigationMenu, etc.) unless
  the requirements explicitly need them. Every file you import from
  @/components/ui/ MUST have a full implementation generated in the same output.
  An import without a matching file causes a build failure. Prefer fewer, complete
  components over many partial ones.

- SHADCN PRIMITIVES — every file you generate under frontend/src/components/ui/
  that exports a primitive (Input, Card, CardHeader, CardTitle, CardDescription,
  CardContent, CardFooter, Button, Textarea, Label, Select, SelectTrigger,
  SelectValue, SelectContent, SelectItem, Checkbox, RadioGroup, RadioGroupItem,
  Switch, Slider, Avatar, AvatarImage, AvatarFallback, Badge, Dialog parts,
  Popover parts, Tabs parts, Tooltip parts, Sheet parts, DropdownMenu parts,
  Command parts, Form, FormItem, FormLabel, FormControl, FormDescription,
  FormMessage) MUST be defined with React.forwardRef forwarding the ref to the
  underlying DOM or Radix node:

      import * as React from "react"
      const Input = React.forwardRef(({{className, type, ...props}}, ref) => (
        <input ref={{ref}} type={{type}} className={{cn("...", className)}} {{...props}} />
      ))
      Input.displayName = "Input"
      export {{ Input }}

  Every forwardRef primitive MUST set displayName matching the exported name
  (Input, Button, Card, etc.) — required for React DevTools and the forwardRef
  compile check.

  Components wrapping a Radix primitive (Dialog, Popover, Select, etc.) must
  forward ref to the Radix element:
      const SelectTrigger = React.forwardRef(({{className, children, ...props}}, ref) => (
        <SelectPrimitive.Trigger ref={{ref}} className={{cn(...)}} {{...props}}>
          {{children}}
        </SelectPrimitive.Trigger>
      ))

  NEVER define a ui/ primitive as `function Name(props) {{ ... }}` or
  `const Name = (props) => ...` without forwardRef. The only allowed exception is
  a purely-presentational pass-through with no DOM child (e.g., a context provider
  wrapper that just returns children) — and even those must explain why in a comment.

- STYLING (CRITICAL — use these classes everywhere, never raw Tailwind color grabs):
  The scaffold's index.css defines CSS variables; tailwind.config.js exposes them
  as proper utility classes. Use ONLY these for neutral surfaces and accents:

    Surface                  → class
    Page background          → bg-surface-page
    Card / panel             → bg-surface-panel border border-surface-border
    Body text                → text-text-default   (NEVER bare text-slate-800/900)
    Secondary / muted text   → text-text-muted
    Primary button           → bg-accent text-accent-fg hover:bg-accent/90
    Accent link / icon       → text-accent

  Common component recipes:
    PAGE WRAPPER:
      ✅ <div className="bg-surface-page min-h-screen p-6">
    CARD / PANEL:
      ✅ <div className="bg-surface-panel border border-surface-border rounded-xl p-4">
    INPUTS:
      ✅ <input className="bg-surface-panel border border-surface-border text-text-default
                           rounded-md px-3 py-2 text-sm placeholder:text-text-muted" />
    NAV / SIDEBAR:
      ✅ <nav className="bg-surface-panel border-r border-surface-border w-64 h-screen p-4">
    PRIMARY BUTTON:
      ✅ <button className="bg-accent text-accent-fg hover:bg-accent/90 rounded-md px-4 py-2">
    BODY TEXT:
      ✅ <p className="text-text-default">...</p>
    MUTED TEXT:
      ✅ <p className="text-text-muted text-sm">...</p>

  ABSOLUTE BANS — these produce invisible or white-on-white text:
  - NEVER `bg-white text-white` or `bg-gray-50 text-gray-50` (invisible text).
  - NEVER `bg-slate-50 text-slate-50` or any same-tone fg/bg combo.
  - NEVER bare `text-slate-800` / `text-slate-900` — use text-text-default.
  - NEVER `dark:` Tailwind variants unless the architect plan explicitly opted
    into dark mode for this project.
  - NEVER hardcode `bg-blue-*`, `bg-emerald-*`, `bg-orange-*`, etc. for
    accent purposes — use bg-accent instead.
  - Every container must have a visible border OR a noticeably different
    background from its parent — never invisible containers.
  - If the user requirements specify a color palette, ONLY --accent is
    overridden. The surface and text variables always stay readable neutrals.
- DEFENSIVE ERROR RENDERING (CRITICAL): when displaying an error from useQuery,
  useMutation, a try/catch, or any axios/fetch failure, NEVER render the error
  object or response body directly. Always extract a string:
        const msg =
          typeof err === 'string' ? err
          : err?.response?.data?.detail?.[0]?.msg
            || (typeof err?.response?.data?.detail === 'string' ? err.response.data.detail : null)
            || err?.message
            || 'Something went wrong'
        return <p className="text-red-600">{{msg}}</p>
  Rendering an object (including {{detail: [...]}} or {{type, loc, msg, input}})
  crashes the entire React tree. The string extraction above handles strings,
  FastAPI Pydantic validation errors, plain Error instances, and generic objects.
- CURRENT-USER ENDPOINT — the scaffold auth exposes the current user at
  `GET /api/auth/me`, NOT `/api/users/me`. Frontend code fetching current user
  info MUST call `apiClient.get("/auth/me")` or use `useAuth().user`.
  NEVER call `/api/users/me` — that path does not exist on the backend.
  The auth router already handles: POST /api/auth/login, POST /api/auth/register,
  GET /api/auth/me. Do not duplicate these routes elsewhere.

- AUTH UTILITIES — the scaffold's app.auth module exposes:
      hash_password(password) → str
      verify_password(plain, hashed) → bool
      create_access_token(user_id) → str
      get_current_user  (FastAPI dependency)
      require_admin     (FastAPI dependency)
  Prefer these canonical names. If you import an alias (get_password_hash,
  verify_pwd, create_token) it will work, but the canonical names above are
  standard. NEVER define your own password-hashing logic — always import
  from app.auth.

- AUTH ENDPOINTS: auth routes always use Pydantic JSON (LoginRequest with
  `email` + `password`). Frontend always calls:
      await api.post("/api/login", {{ email, password }})
      await api.post("/api/register", {{ email, password, name }})
  Never use OAuth2PasswordRequestForm or form-encoded auth.

- CENTRALIZED API CLIENT (CRITICAL — bare fetch causes 403s on protected
  endpoints):
  Every API call from the frontend MUST go through the centralized axios client
  defined at `frontend/src/lib/api.js` (or wherever the plan names it). NEVER
  call `fetch()` directly for app endpoints. The centralized client carries
  the Authorization header automatically via interceptor; bare fetch does not.

  Pattern (correct):
      import {{ api }} from "@/lib/api";
      const {{ data }} = await api.get("/api/plants/mine");
      const created = await api.post("/api/plants", payload);
      await api.delete(`/api/plants/${{id}}`);

  Pattern (FORBIDDEN — token won't be sent):
      const res = await fetch(`${{API}}/api/plants/mine`);  // ❌ no auth header
      const data = await res.json();

  Only acceptable bare fetch is for explicitly public assets outside your API
  (CDN images, public weather APIs, etc.). All /api/ calls go through the
  centralized client. No exceptions.

- API CLIENT MUST EXIST: every frontend page or component that hits /api/ MUST
  import `api` from `@/lib/api`. This file is scaffold-provided (do NOT include
  it in your plan). It exports a pre-configured axios instance:
      import {{ api }} from "@/lib/api"
      const {{ data }} = await api.get("/api/tasks")
      const result = await api.post("/api/tasks", payload)
  In dev the Vite proxy forwards /api/* to the backend automatically — no
  baseURL or port configuration needed. In prod, VITE_API_URL is set to the
  deployed backend URL. Always use `api` for every /api/ call.

- FASTAPI STATUS-CODE DISCIPLINE (CRITICAL — boot fails if violated):
  * status_code=204 (HTTP_204_NO_CONTENT) means NO RESPONSE BODY. The function
    must NOT have a return type annotation that implies a body, MUST NOT use
    response_model=ModelSchema, and MUST return None or Response(status_code=204).
    Example of correct usage:
        @router.delete("/posts/{{post_id}}", status_code=status.HTTP_204_NO_CONTENT)
        def delete_post(post_id: int, db: Session = Depends(get_db)) -> None:
            ...
            return None
  * status_code=304 (NOT_MODIFIED) — same rule, no body.
  * For DELETE operations that you WANT to return a confirmation body, use
    status_code=200 with a {{detail: "deleted"}} response and a proper
    response_model.
  * Never combine status_code=204 with response_model=SomeModel — the FastAPI
    decorator raises an AssertionError at import time and the whole app fails
    to boot.
- FASTAPI ROUTE PATTERNS (avoid common mistakes):
  * Path parameters in the URL ({{post_id}}) MUST match the function arguments
    exactly (post_id: int).
  * Use Pydantic models for request bodies (POST/PUT/PATCH), not raw dict.
  * Always pass db: Session = Depends(get_db) when accessing the database.
  * For auth-gated routes, pass current_user via Depends(get_current_user)
    consistently everywhere.

- DO NOT add light/dark mode toggle unless the requirements explicitly
  request it. No ThemeContext.jsx, no ThemeProvider, no `dark:` Tailwind
  classes, no theme toggle button in the navbar — unless the architect
  blueprint explicitly states the project opted into dark mode. The scaffold
  ships ONE color mode (light). Keep it that way by default.

- ROUTE MOUNTING UNDER /api (CRITICAL):
  In backend/app/main.py, every `app.include_router(<router>, ...)` call MUST
  include `prefix="/api"` (or a more specific /api/<entity> prefix). The
  frontend calls all hit /api/*; without the prefix on the backend side, every
  request returns 404 or 405. Pattern:

      app.include_router(auth.router, prefix="/api", tags=["auth"])
      app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
      app.include_router(profile.router, prefix="/api/profile", tags=["profile"])

  If a router file ALREADY has its own prefix inside APIRouter(prefix="/tasks"),
  combine carefully — the final path is the concatenation. Pick ONE place to
  put the /api prefix (recommended: in main.py via include_router) so it's
  consistent across all routers.

- ROUTE PATH CONSISTENCY: never mix /api/X in some routers with /X in others.
  Whatever pattern you start the auth router with must be used by every other
  router in the same app.

- ROUTER EXPORT NAME — ABSOLUTE (wrong name causes ImportError at boot):
  Every route file in backend/app/routes/*.py MUST declare its APIRouter as the
  variable named `router`. No other name is acceptable.
    GOOD:  router = APIRouter(prefix="/orders", tags=["orders"])
    BAD:   orders_router = APIRouter(prefix="/orders")  # import fails
  main.py imports it with an alias to avoid name collision in that file:
    from app.routes.orders import router as orders_router
    app.include_router(orders_router, prefix="/api")
  The `as orders_router` alias only exists at the import site in main.py.
  The route file itself always declares `router = APIRouter(...)`.

- API PREFIX CONVENTION — ABSOLUTE (violation causes /api/api/... doubled paths):
  In route files (backend/app/routes/*.py), NEVER include /api in the APIRouter
  prefix or in handler decorator paths.
    GOOD:  router = APIRouter(prefix="/admin", tags=["admin"])
           @router.get("/orders")          # final path: /api/admin/orders
    BAD:   router = APIRouter(prefix="/api/admin")   # /api belongs in main.py
    BAD:   @router.get("/api/admin/orders")           # absolute paths in decorators
  In main.py, mount every project router with prefix="/api":
    GOOD:  app.include_router(admin_router, prefix="/api")
    BAD:   app.include_router(admin_router)          # missing /api — every call 404s
    BAD:   app.include_router(admin_router, prefix="/api/api")  # doubled prefix
  There is exactly ONE /api in any final URL. The route file contributes the
  resource segment (/admin, /orders, /profile); main.py contributes /api.

- PASSWORD CHANGE ENDPOINT (REQUIRED): the password-change endpoint MUST be
  exposed at POST /api/auth/me/password, even if the implementation lives in a
  profile module. Add an alias route in auth_routes.py when needed:
      @router.post("/me/password")
      def change_my_password(payload: PasswordChangeRequest, db=..., current_user=...):
          from app.routes.profile import change_password as _orig
          return _orig(payload, db, current_user)
  NEVER put the password-change handler only at /api/profile/password without also
  aliasing it at /api/auth/me/password — the contract always expects the auth path.

- CONTRACT IS A CHECKLIST, NOT A SUGGESTION:
  When you receive a "CONTRACT — YOU MUST PRODUCE EXACTLY THESE DECORATORS"
  block, treat every line as a hard requirement, not a guideline.
  * Count your @router.* decorators before closing the file. If you wrote N of
    M required, write the remaining (M - N) before finishing.
  * Match the URL string EXACTLY. /me and /my-orders are DIFFERENT paths.
    PATCH and PUT are DIFFERENT methods. The contract checker is case-sensitive.
  * Match the METHOD exactly. Do not substitute GET for POST or vice-versa.

- ROUTE FILE OWNERSHIP (endpoints live in exactly ONE file):
  * Auth endpoints (/api/auth/*) → auth_routes.py ONLY.
    POST /api/auth/me/password lives here, NOT in profile_routes.py.
  * Admin endpoints (/api/admin/<resource>/*) → admin_<resource>.py.
    Do NOT mix admin and public endpoints in the same file.
  * Public resource endpoints (/api/<resource>/*) → <resource>.py or
    <resource>_routes.py. One resource, one file.

- HANDLER QUALITY:
  * Every handler must query the DB or compute something real. The route must
    either return a Pydantic model from a DB query, update a row, or delete one.
  * `return []` or `pass` is only acceptable for DELETE 204 responses or
    endpoints that truly have no body. If you cannot implement a handler because
    the model fields are unknown, write `raise NotImplementedError` with a
    comment — this triggers a clear test failure rather than silent omission.
  * If a path has {{path_params}}, declare them in the function signature with
    type hints: `def get_order(order_id: int, ...)`.

- CONTRACT COMPLETENESS (CRITICAL — the single biggest source of debug cycles):
  The architect has already specified EVERY endpoint this app needs. Your job is
  to implement ALL of them, not a subset. Incomplete implementations cause contract
  failures that the debugger cannot fix (it cannot invent business logic).
  Rules:
  * Before finishing a route file, count the endpoints you have written against
    the blueprint's API routes for that resource. If you wrote 3 of 5, write the
    other 2 before moving on.
  * For resources with CRUD, all four operations (list, get, create, update/delete)
    MUST be present in the same router file unless the blueprint explicitly splits them.
  * The canonical password-change endpoint is POST /api/auth/me/password.
    Put it in auth_routes.py, NOT profile_routes.py.
  * The canonical "my orders" / "my items" endpoint is GET /api/<resource>/me.
    Do NOT use /my-orders, /mine, or /my-<resource> — these are different paths.
  * Admin endpoints (POST /api/admin/X, PUT /api/admin/X/{id}, DELETE ...) MUST
    be in admin_X.py or admin.py — never mixed into the public resource router.
  * If a route file would exceed ~200 lines for one resource, still implement all
    endpoints — do not truncate. Return the complete file.

- AGGREGATE ENDPOINTS — if the architect blueprint specifies aggregate_endpoints:
  {getattr(plan, 'aggregate_endpoints', []) or []}
  then you MUST generate backend/app/routes/aggregate.py that implements every
  aggregate endpoint listed. Each aggregate endpoint queries all relevant tables and
  returns a dict with one key per entity type (pluralised), where each value is a
  list of all rows serialised as dicts using `__table__.columns`.

  EXAMPLE for path=/api/portfolio/data, returns=[projects, education, experience, skills]:

      from fastapi import APIRouter, Depends
      from sqlalchemy.orm import Session
      from app.database import get_db
      from app.models import Project, Education, Experience, Skill

      router = APIRouter(prefix="/portfolio", tags=["portfolio"])

      def _to_dict(obj):
          return {{c.name: getattr(obj, c.name) for c in obj.__table__.columns}}

      @router.get("/data")
      def get_portfolio_data(db: Session = Depends(get_db)):
          return {{
              "projects":    [_to_dict(p) for p in db.query(Project).all()],
              "education":   [_to_dict(e) for e in db.query(Education).all()],
              "experience":  [_to_dict(x) for x in db.query(Experience).all()],
              "skills":      [_to_dict(s) for s in db.query(Skill).all()],
          }}

  Wire the router into main.py (AFTER the auth router include):
      from app.routes.aggregate import router as aggregate_router
      app.include_router(aggregate_router, prefix="/api")

  Per-entity CRUD routes remain MANDATORY even when an aggregate endpoint exists.
  The admin panel uses CRUD; the public page uses the aggregate.

- ROUTE FUNCTION NAMES — every route handler function MUST have a unique name
  across the ENTIRE backend. If two routers both need a "list users" endpoint,
  name them distinctly by resource: `list_users_admin` vs `list_users_public`.
  Duplicate function names trigger FastAPI's "Duplicate Operation ID" warning at
  startup AND break OpenAPI client generation. Never reuse function names like
  `list_items`, `create_item`, `get_item` across different router files — always
  prefix with the resource name: `list_plants`, `create_plant`, `get_plant`.

- "MY ITEMS" ENDPOINTS — for any resource the user can favorite, bookmark, save,
  or own personally, the backend MUST expose a list endpoint that returns the
  current user's items. Examples:
      GET /api/plants/favorites   → list current user's favorite plants
      GET /api/tasks/mine         → list current user's own tasks
      GET /api/users/me           → current user profile
      GET /api/posts/saved        → current user's saved posts
  NEVER expose ONLY a toggle endpoint (e.g. POST /api/plants/{{id}}/favorite)
  without a corresponding list endpoint. The list endpoint is what the dashboard
  needs to display "what I have".

- SQLALCHEMY 2.0 — MANDATORY Mapped[] for EVERY model (Column() causes 200+ mypy errors):
  Every column in every model MUST use Mapped[T] + mapped_column(). NO EXCEPTIONS.
  The legacy `email = Column(String)` form is FORBIDDEN — the sqlalchemy[mypy] plugin
  cannot infer constructor argument names from it, so mypy fires "Unexpected keyword
  argument 'email' for User" on EVERY model instantiation in the codebase.

  REQUIRED imports at the top of every models file:
      from datetime import datetime
      from sqlalchemy import String, Integer, DateTime, ForeignKey, Boolean, Text
      from sqlalchemy.orm import Mapped, mapped_column, relationship
      from app.database import Base

  REQUIRED form for EVERY column in EVERY model:
      class User(Base):
          __tablename__ = "users"
          id: Mapped[int] = mapped_column(primary_key=True)
          email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
          password_hash: Mapped[str] = mapped_column(String, nullable=False)
          name: Mapped[str | None] = mapped_column(String, nullable=True)
          role: Mapped[str] = mapped_column(String, default="user", nullable=False)
          created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
          updated_at: Mapped[datetime] = mapped_column(
              DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
          )
          tasks: Mapped[list["Task"]] = relationship(back_populates="user")

  USER MODEL — MANDATORY base fields (the scaffold auth.py depends on these exact names):
      id, email, password_hash, name, role, created_at, updated_at
  You MAY add project-specific fields (phone_number, address, preferences, etc.)
  but the base fields above are MANDATORY and must appear first. Do NOT rename them.
  Do NOT use `username` instead of `name` — the scaffold auth_routes.py writes `name`.

  NEVER write (causes 200+ cascade mypy errors):
      email = Column(String, unique=True)            # FORBIDDEN
      email: str = Column(String, unique=True)       # FORBIDDEN
      tasks = relationship("Task", ...)              # FORBIDDEN (missing Mapped[])

  ALWAYS write:
      email: Mapped[str] = mapped_column(String, unique=True)

- AUTH BODY = JSON ONLY (CRITICAL — 422 on every login otherwise):
  Auth routes (login + register) MUST use Pydantic BaseModel for the request
  body. NEVER use fastapi.security.OAuth2PasswordRequestForm — it requires
  form-encoded data while every modern frontend sends JSON. Required pattern:

      from pydantic import BaseModel, EmailStr
      class LoginRequest(BaseModel):
          email: EmailStr
          password: str
      class RegisterRequest(BaseModel):
          email: EmailStr
          password: str
          name: str | None = None

      @router.post("/login")
      def login(payload: LoginRequest, db: Session = Depends(get_db)):
          user = authenticate_user(db, payload.email, payload.password)
          if not user:
              raise HTTPException(401, "Invalid credentials")
          return {{"access_token": create_access_token(user.id),
                  "token_type": "bearer"}}

  Frontend:
      await api.post("/api/login", {{ email, password }})
      await api.post("/api/register", {{ email, password, name }})

  The frontend always sends JSON with `email`. Backend always reads
  Pydantic. Both sides match by construction. NO OAuth2PasswordRequestForm
  anywhere in generated code.

- NO TYPESCRIPT IN .JSX FILES (build fails if violated): files ending in .jsx
  must be PURE JavaScript. NEVER use TypeScript syntax: no `as const`, no
  `as Type`, no type annotations on parameters (`(x: string) =>`), no
  interfaces, no `: ReturnType`, no generics like `<T>`. If you need a
  readonly array, just use the array literal — JavaScript's `Object.freeze`
  if needed, never `as const`. esbuild rejects every .jsx file containing
  TypeScript syntax and the build fails.

- TRAILING SLASH CONSISTENCY: pick one — usually NO trailing slash on path
  literals. The backend FastAPI router decorators must match the frontend
  fetch paths exactly. Recommended: define backend routes WITHOUT a leading
  slash inside a router with a prefix that has NO trailing slash:
      router = APIRouter(prefix="/tasks", tags=["tasks"])
      @router.get("")     # /tasks
      @router.post("")    # /tasks
      @router.get("/{{id}}") # /tasks/{{id}}
  Frontend: api.get("/api/tasks"), api.post("/api/tasks", body). No trailing
  slashes anywhere. Trailing-slash mismatches cause 307 redirects that drop
  Authorization headers in some clients.

- LIST RESPONSE DEFENSIVE READS: when consuming a list endpoint, handle BOTH
  common backend shapes — bare array `[...]` OR wrapper `{{tasks: [...], total: N}}`:
      const {{ data }} = useQuery(...)
      const list = Array.isArray(data) ? data : (data?.tasks || data?.items || [])
  Then map `list` not `data`. This prevents "the page is empty despite the
  request succeeding."

- INVALIDATE QUERIES AFTER MUTATIONS (CRITICAL — UI looks broken otherwise):
  Every useMutation that changes server data must invalidate the queries that
  read that data:
      const qc = useQueryClient()
      const createTask = useMutation({{
        mutationFn: (payload) => api.post("/api/tasks", payload),
        onSuccess: () => {{
          qc.invalidateQueries({{ queryKey: ["tasks"] }})
          qc.invalidateQueries({{ queryKey: ["tasks-summary"] }})
        }},
      }})
  Without onSuccess invalidation, the dashboard reads stale cached data and
  the new task does not appear — even though the POST succeeded.

- DYNAMIC ICONS in lists — when mapping over an array of items where each item
  has an icon component, render via the property directly or a capitalized alias.
  NEVER reference a bare `Icon` that is not in scope.

    // CORRECT — assign to capitalized const inside map body
    {{items.map((item) => {{
      const Icon = item.icon
      return <Icon className="h-5 w-5" />
    }})}}

    // CORRECT — destructure with rename (lowercase property → capitalized alias)
    {{items.map(( {{ icon: Icon, label, path }}) => (
      <Link to={{path}}><Icon className="h-5 w-5" />{{label}}</Link>
    ))}}

    // WRONG — Icon not in scope (the most common LLM bug)
    {{items.map((item) => <Icon />)}}

    // ALSO WRONG — lowercase component names crash React silently
    {{items.map((item) => <item.icon />)}}

  Always assign to a capitalized const inside the map body BEFORE rendering,
  OR destructure with a capitalized alias.  Never use a bare `<Icon>` tag unless
  Icon is actually imported or declared at the top of the file.

- ICONS IN JSX (CRITICAL — this bug whitepages the entire deployed app) —
  Every lucide-react icon MUST be rendered as a JSX element with angle brackets.
  NEVER render it as a bare `{{Icon}}` expression inside JSX.

  CORRECT — always angle brackets:
      <ArrowRight />
      <ArrowRight className="w-4 h-4 text-accent" />
      <button><ArrowRight /> Continue</button>

  BROKEN — throws React error #31 at first render, whole page turns white:
      {{ArrowRight}}
      <span>{{ArrowRight}}</span>
      <button>{{ArrowRight}} Continue</button>
      <div>{{items[0].icon}}</div>

  When iterating over data that has an icon prop, ALWAYS destructure to a
  capitalized const inside the map body BEFORE rendering:

      // GOOD
      items.map((item) => {{
        const Icon = item.icon
        return <Icon className="w-4 h-4" />
      }})

      // BROKEN — {{item.icon}} inside JSX renders the component object as text
      items.map((item) => <div>{{item.icon}}</div>)

  Rule of thumb: if you write `{{X}}` in JSX, X must be a string, number, or
  already-rendered JSX — NEVER a component. If it's a component, wrap it: `<X />`.

- ROUTES CONFIG — frontend/src/lib/routes.js is the single source of truth for
  navigation. When generating this file, include an entry for EVERY page from
  the blueprint with appropriate `show_in_nav` and `requires` values:
    - Public marketing/landing pages: requires: null
    - Login / Register:               requires: "guest"  (hide from logged-in users)
    - User dashboards / account:      requires: "auth"
    - Admin pages:                    requires: "admin"
  Export PUBLIC_NAV, GUEST_NAV, AUTH_NAV, ADMIN_NAV as filtered arrays. Example:

      import {{ Home, LogIn, UserPlus, LayoutDashboard }} from "lucide-react"
      export const ROUTES = [
        {{ path: "/",         label: "Home",      icon: Home,           show_in_nav: true,  requires: null }},
        {{ path: "/login",    label: "Login",     icon: LogIn,          show_in_nav: true,  requires: "guest" }},
        {{ path: "/register", label: "Register",  icon: UserPlus,       show_in_nav: true,  requires: "guest" }},
        {{ path: "/dashboard",label: "Dashboard", icon: LayoutDashboard,show_in_nav: true,  requires: "auth" }},
      ]
      export const PUBLIC_NAV = ROUTES.filter(r => r.show_in_nav && r.requires === null)
      export const GUEST_NAV  = ROUTES.filter(r => r.show_in_nav && r.requires === "guest")
      export const AUTH_NAV   = ROUTES.filter(r => r.show_in_nav && r.requires === "auth")
      export const ADMIN_NAV  = ROUTES.filter(r => r.show_in_nav && r.requires === "admin")

  Pages NOT in routes.js will NEVER appear in the nav.

- NAVBAR COMPONENT — frontend/src/components/Navbar.jsx MUST derive nav links
  from the routes config, NOT from a separate hand-written array that drifts:

      import {{ PUBLIC_NAV, GUEST_NAV, AUTH_NAV, ADMIN_NAV }} from "@/lib/routes"
      import {{ useAuth }} from "@/contexts/AuthContext"

      export function Navbar() {{
        const {{ user }} = useAuth()
        const isAdmin = user?.role === "admin"
        const links = [
          ...PUBLIC_NAV,
          ...(user ? AUTH_NAV : GUEST_NAV),
          ...(isAdmin ? ADMIN_NAV : []),
        ]
        return (
          <nav>
            {{links.map(( {{ path, label, icon }}) => {{
              const Icon = icon
              return (
                <Link to={{path}} key={{path}}>
                  {{Icon && <Icon className="h-4 w-4" />}} {{label}}
                </Link>
              )
            }})}}
          </nav>
        )
      }}

  App.jsx route mounting should also loop over ROUTES to avoid drift.

- APP NAME — the blueprint contains an `app_name` field (also shown at the top
  of this prompt as "App name:"). You MUST use that exact name verbatim in:
    * The HTML <title> (index.html or vite.config.js title)
    * The Navbar brand/logo text
    * Any hero <h1> on the landing / home page
    * The README title (if generated)
  NEVER invent a different name, translate it, abbreviate it, or replace it with
  a generic placeholder like "MyApp" or "App". The app name is set by the user —
  it is not yours to change.

- NAVBAR VISIBILITY — every user-facing page MUST have `show_in_nav: true`
  in its routes.js entry UNLESS it is one of the following exceptions:
    * A detail page with an :id / {{id}} parameter  (/tasks/:id, /menu-items/:id)
    * A sub-action page  (/tasks/new, /admin/users/new, /items/edit)
    * An OAuth callback or redirect page  (/auth/callback, /verify)
  Pages that MUST be show_in_nav: true include:
    Home (/), Menu (/menu), Login (/login), Register (/register),
    Dashboard (/dashboard), Profile (/profile), Tasks list (/tasks),
    Orders (/orders), Cart (/cart), About (/about), Contact (/contact)
  A Navbar with zero visible links is a structural failure — every generated
  app must display at least one nav link to logged-out visitors.

- CATCH-ALL ROUTE — App.jsx must include a `*` catch-all route that
  renders NotFoundPage (or redirects to "/"). The NotFoundPage component
  exists and is mounted, but it MUST NOT appear in routes.js, and the
  Navbar MUST NEVER show a link to it. Any routes.js entry whose path
  is "*" or whose component is "NotFoundPage" MUST have
  `show_in_nav: false`. Better: omit it from routes.js entirely.

- AUTH FLAG — the plan notes may contain "AUTH_DISABLED" when the app is fully
  public. Check `plan.notes` for this token before generating any file.

  If AUTH_DISABLED is present in plan.notes:
    * Do NOT generate LoginPage.jsx, RegisterPage.jsx, ForgotPasswordPage.jsx,
      or AuthContext.jsx — these files must not exist.
    * Do NOT generate backend auth routes (backend/app/routes/auth*.py).
    * Do NOT include `from app.routes.auth_routes import` or
      `app.include_router(auth_router` in main.py.
    * Do NOT import or call useAuth(), AuthContext, or authApi anywhere.
    * Do NOT include role/permission checks anywhere.
    * Do NOT include /admin/ routes or admin pages.
    * Navbar.jsx must have NO "Sign In" / "Sign Up" / "Log out" / "My account"
      links. Only include public navigation links.
    * routes.js must export ONLY `PUBLIC_NAV` — no GUEST_NAV, AUTH_NAV, or
      ADMIN_NAV.
    * seed.py must NOT insert demo@example.com or admin@example.com.
    * SETUP.md / README should say the app is fully public and list NO
      JWT_SECRET_KEY as a required env var.
    * Every page is accessible to all visitors — no auth guards anywhere.

  If AUTH_DISABLED is NOT present (default — auth enabled):
    * Use the scaffolded auth as before. Generate LoginPage / RegisterPage
      using useAuth(). Wrap App.jsx in AuthProvider.

- LOGIN/REGISTER PAGES — the ONLY auth method is email + password.
  LoginPage.jsx / Login.jsx MUST contain ONLY:
    * email input  (`<input type="email" ...>`)
    * password input  (`<input type="password" ...>`)
    * submit button
    * link to RegisterPage / Register
    * optional "Forgot password?" link (only if the blueprint includes it)
  RegisterPage.jsx / Register.jsx MUST contain ONLY:
    * email input
    * password input
    * optional name input
    * submit button
    * link to LoginPage / Login

  Use `useAuth().login({{ email, password }})` and
  `useAuth().register({{ email, password, name }})` — the scaffolded
  AuthContext handles all JWT storage and user-state management.

  FORBIDDEN — never include any of:
    * "Log in with Google" / Google button / GoogleAuthProvider
    * Facebook / GitHub / Microsoft / Apple / Twitter sign-in buttons
    * "Send magic link" / passwordless flows
    * MFA / 2FA setup / TOTP input field
    * Phone number input for OTP
    * "Or continue with" dividers that lead to social auth
    * Any import from @react-oauth, react-google-login, next-auth, firebase/auth,
      supabase/auth, or any similar social-auth library

  If the requirements or blueprint somehow mention these, IGNORE them entirely.
  They were downgraded upstream and must not reach the generated code.

- ADMIN PAGE PATHS (CRITICAL — wrong path returns 405 or empty data):
  Any component under /pages/admin/ or /components/admin/ that calls the
  backend MUST use paths starting with /api/admin/<resource>.  The public
  endpoint at /api/<resource> exists for end-users (visitors submitting
  contact forms, reading public data) and typically exposes DIFFERENT methods
  (POST only, or GET with no auth). Admin pages NEVER call the public path.

  CORRECT (admin component):
      // In AdminDashboardPage.jsx or pages/admin/Dashboard.jsx
      const {{ data }} = useQuery({{
        queryFn: () => api.get("/api/admin/contact-messages"),
      }})
      const del = useMutation({{
        mutationFn: (id) => api.delete(`/api/admin/contact-messages/${{id}}`),
      }})

  WRONG — causes 405 (backend has POST /api/contact-messages, not GET):
      const {{ data }} = useQuery({{
        queryFn: () => api.get("/api/contact-messages"),   // ❌ public path
      }})

  Rule: if the file path contains /admin/ OR the component name contains
  "Admin", every /api/ call must include /api/admin/ in the path. No exceptions.

**File-specific requirements (apply the rule whose path pattern matches the file in the user message):**
- backend/app/database.py: at the top do `from dotenv import load_dotenv; load_dotenv()`;
  read `raw_url = os.getenv("DATABASE_URL", "sqlite:///./app.db")`. Normalize Neon /
  Heroku "postgres://" URLs (SQLAlchemy 1.4+ requires "postgresql://"):
      if raw_url.startswith("postgres://"):
          raw_url = "postgresql://" + raw_url[len("postgres://"):]
  Then create the engine:
      if raw_url.startswith("sqlite"):
          engine = create_engine(raw_url, connect_args={{"check_same_thread": False}})
      else:
          engine = create_engine(raw_url, pool_pre_ping=True, pool_recycle=300)
  Define Base = declarative_base(), SessionLocal, a get_db() generator, and
  create_tables() calling Base.metadata.create_all(bind=engine). SYNC SQLAlchemy only.
- model files: `from app.database import Base` (do NOT create a new declarative_base);
  use only portable column types (Integer, String, Text, Boolean, DateTime, Float,
  ForeignKey) — no JSONB/ARRAY/server-side UUID defaults.
- backend/app/seed.py: expose seed_demo_data() that inserts REALISTIC, PLENTIFUL
  rows per entity in dependency order (parents before children). Use real-sounding
  domain content — NOT placeholder text like "Item 1" or "Test Data".
  MANDATORY MINIMUMS: for any model shown in a list/browse/menu/feed page, insert
  AT LEAST 15 rows (browse-heavy apps) or 10 rows (list apps) or 5 rows (admin).
  For MEDIA columns (image_url, avatar, cover_image), use picsum.photos URLs:
    https://picsum.photos/seed/<slug>/400/300
  For PRICE columns, use realistic ranges (restaurant: $6–$35, not $2 or $0).
  End with a `print(f"[seed] done — ...", flush=True)`. Idempotent (empty-table check).
  If auth exists, seed BOTH demo@example.com/demo1234 AND
  admin@example.com/admin1234 (both properly hashed).
  SEED FUNCTION CONTRACT — use this exact signature (db=None, not db: Session):
      def seed_demo_data(db=None):
          if db is None:
              from app.database import SessionLocal
              db = SessionLocal()
          if db.query(User).first():
              return  # already seeded
          # ... domain-relevant insertions ...
          db.commit()
  NEVER define seed_demo_data() with no parameters — the lifespan handler
  passes a db session; if the signature has no parameter, boot fails with
  "takes 0 positional arguments but 1 was given".
  The db=None default makes the function work from both the lifespan handler
  (which passes a session) and from tests (which may call it with no args).
  SEED IDEMPOTENCE — seed_demo_data MUST check before inserting:
      if db.query(User).first():
          return  # already seeded
  Without this, every restart doubles the seed rows.
- backend/app/main.py: SCAFFOLD — DO NOT REWRITE.
  MAIN.PY IS SCAFFOLD (CRITICAL): main.py is pre-seeded with CORS middleware,
  a tolerant lifespan handler, a /health endpoint, and the auth router include.
  NEVER rewrite main.py from scratch. ONLY APPEND your project-specific
  include_router calls below the marker:
      # === ROUTE INCLUDES BELOW THIS LINE — LLM appends, never replaces ===
  The lifespan handler uses inspect.signature to tolerate both seed signatures.
  NEVER remove or modify the lifespan block — removing it causes
  "no such table: users" on the very first login.
  NEVER add a second FastAPI() instantiation — there must be exactly one `app`.
  NEVER add allow_origins=["*"] — it breaks credentials mode and raises
  ValueError at startup.
  CORS SETUP — use the following pattern verbatim (no shortcuts):

      import os
      from fastapi.middleware.cors import CORSMiddleware

      # Production: explicit allowlist via env var (comma-separated URLs).
      _cors_origins = [
          origin.strip()
          for origin in os.environ.get("CORS_ORIGINS", "").split(",")
          if origin.strip()
      ]

      # Dev: allow any localhost / 127.0.0.1 port so Vite can auto-increment
      # freely. Set ALLOW_LOCALHOST_CORS=false in production for strict mode.
      _allow_localhost = (
          os.environ.get("ALLOW_LOCALHOST_CORS", "true").lower() == "true"
      )
      _localhost_regex = (
          r"https?://(localhost|127\\.0\\.0\\.1)(:\\d+)?"
          if _allow_localhost
          else None
      )

      app.add_middleware(
          CORSMiddleware,
          allow_origins=_cors_origins,
          allow_origin_regex=_localhost_regex,
          allow_credentials=True,
          allow_methods=["*"],
          allow_headers=["*"],
      )

  NEVER use allow_origins=["*"] — it is incompatible with allow_credentials=True
  and raises a ValueError at startup. NEVER hardcode localhost port numbers in
  allow_origins.
  CRITICAL: main.py MUST define `app = FastAPI(...)` exactly once and MUST NOT
  import `app` from anywhere (`from app import app` and `from app.main import app`
  are both forbidden here — other modules import from main.py, not the reverse).
  AUTH ROUTER (MANDATORY): main.py MUST include the scaffold auth router:
      from app.routes.auth_routes import router as auth_router
      app.include_router(auth_router, prefix="/api")
  Include this BEFORE any project-specific routers. The auth scaffold provides
  POST /api/auth/register, POST /api/auth/login, GET /api/auth/me automatically.
  Start main.py with this module docstring:
      \"\"\"FastAPI app entry point.
      Note: mypy may warn 'Module has no attribute X' on this file due to the
      `app` name shadowing the package name. These warnings are cosmetic —
      the runtime is unaffected. flake8 and runtime checks are authoritative.\"\"\"
  DO NOT regenerate backend/app/main.py from scratch — the file is pre-seeded
  with CORS middleware, health check, and the auth router include.  ONLY APPEND
  your project-specific include_router calls below the marker comment
  \"# Project-specific route includes\":
      from app.routes.menu_routes import router as menu_router
      app.include_router(menu_router, prefix=\"/api\")
  Never delete or replace the auth_router include or the health endpoint.
- LIST ENDPOINT RETURN TYPES — annotate handlers with the ORM type, not the schema:
  When a route returns an ORM query result but uses response_model for serialization,
  annotate the function return type with the ORM model list so mypy is consistent:
      @router.get("/tasks", response_model=list[TaskOut])
      def list_tasks(db: Session = Depends(get_db)) -> list[Task]:  # ORM type
          return db.query(Task).all()
  FastAPI's response_model serialization handles the Task → TaskOut conversion at
  runtime (via from_attributes=True). NEVER annotate the return type as `list[TaskOut]`
  for ORM queries — that makes mypy flag a type mismatch between the ORM result and
  the Pydantic schema.
- any frontend file that calls the API: import from `@/lib/api` and use the
  centralized `api` client — NEVER use bare fetch(). In dev, the Vite proxy
  forwards /api/* requests to the backend; no baseURL or port config needed.
- frontend/src/App.jsx MUST use the shared Layout/BareLayout pattern. Chrome
  (Navbar, Footer) lives ONLY in Layout, not in any page component. Required
  structure:

      import {{ BrowserRouter, Routes, Route, Outlet }} from "react-router-dom"
      import {{ QueryClient, QueryClientProvider }} from "@tanstack/react-query"
      import {{ AuthProvider }} from "@/contexts/AuthContext"
      import {{ Navbar }} from "@/components/Navbar"
      import {{ Footer }} from "@/components/Footer"
      // ↓ insert page imports here:
      import HomePage from "@/pages/HomePage"
      import LoginPage from "@/pages/LoginPage"
      // ...

      const queryClient = new QueryClient()

      /** Renders chrome (Navbar/Footer) around every main route. */
      function Layout() {{
        return (
          <div className="min-h-screen flex flex-col bg-surface-page text-text-default">
            <Navbar />
            <main className="flex-1"><Outlet /></main>
            <Footer />
          </div>
        )
      }}

      /** Auth/focused pages: no chrome, just the content. */
      function BareLayout() {{
        return (
          <div className="min-h-screen bg-surface-page text-text-default">
            <Outlet />
          </div>
        )
      }}

      function App() {{
        return (
          <QueryClientProvider client={{queryClient}}>
            <AuthProvider>
              <BrowserRouter>
                <Routes>
                  <Route element={{<Layout />}}>
                    <Route path="/" element={{<HomePage />}} />
                    {{/* other main pages go here */}}
                  </Route>
                  <Route element={{<BareLayout />}}>
                    <Route path="/login" element={{<LoginPage />}} />
                    <Route path="/register" element={{<RegisterPage />}} />
                  </Route>
                </Routes>
              </BrowserRouter>
            </AuthProvider>
          </QueryClientProvider>
        )
      }}

      export default App

  AuthContext.jsx is a scaffold file — do not redefine it. Import AuthProvider
  from "@/contexts/AuthContext" and use the `useAuth()` hook in components.
  Footer.jsx is a scaffold file — it ships automatically. Do NOT include it in
  the plan; the scaffold provides the default implementation.

- COMMON HOOKS — the scaffold ships these reusable hooks at @/hooks/:
      useIntersectionObserver — scroll-triggered animations / lazy reveals
      useMediaQuery           — responsive breakpoint checks
      useDebounce             — delayed value tracking (search inputs)
      useLocalStorage         — persistent local state
  NEVER define any of these inline in a component file. ALWAYS import from
  the scaffold:
      import {{ useIntersectionObserver }} from "@/hooks/useIntersectionObserver"
  Signature: const [ref, isVisible] = useIntersectionObserver()
  Attach `ref` to the JSX element you want to observe. `isVisible` flips
  true when the element scrolls into the viewport.
  The same applies to useMediaQuery, useDebounce, and useLocalStorage —
  import from "@/hooks/<HookName>", never redefine.

- NO COMMONJS — this is a Vite ESM bundle. NEVER use:
      const X = require("...")          // ReferenceError at runtime
      module.exports = {{ ... }}          // silently broken in ESM
  ALWAYS use ES module syntax:
      import X from "..."
      import {{ a, b }} from "..."
      export default X
      export {{ a, b }}
  require() does not exist in the browser bundle and throws
  "ReferenceError: require is not defined" on the very first render.

- SINGLE-PAGE APPS — when the blueprint specifies an aggregate endpoint
  (aggregate_endpoints is non-empty), the public-facing page MUST call that
  endpoint via apiClient.get and destructure the result. Do NOT make multiple
  parallel requests to per-entity endpoints.

  CORRECT (one call, one place to fail):
      const {{ data }} = await api.get("/portfolio/data")
      const {{ projects, education, experience, skills }} = data

  WRONG (multiple round-trips, partial-failure states, 404s if any per-entity
  route is missing):
      const [projects, education] = await Promise.all([
          api.get("/projects"),
          api.get("/education"),
      ])

  The aggregate endpoint is faster (one network call) and avoids partial-failure
  states where some sections render and others are empty.

- AUTH SURFACE — LoginPage, RegisterPage, and any component doing login /
  register / logout MUST use the useAuth() hook exclusively:

      const {{ login, register, logout, user }} = useAuth()
      // login/register — single object arg, never positional:
      await login({{ email, password }})
      await register({{ email, password, name }})

  NEVER import `authApi` from "@/lib/auth" in a page or component — it is
  INTERNAL to AuthContext. The useAuth login/register functions handle the
  full flow (API call + token storage + header injection + /me fetch) in one
  atomic operation.

- NEVER write the dual-call pattern:
      // WRONG — second call passes a token, not credentials:
      const result = await authApi.login(email, password)
      await login(result.access_token)
  Use ONE call:
      await login({{ email, password }})
  The dual-call sends a JWT to AuthContext which then tries to log in with the
  token as a password → 422 every time.

- AUTH ARGS ARE ALWAYS OBJECTS: pass a single `{{ email, password, name? }}`
  object to login/register. Never pass positional args like `login(email, password)`.

- POST-AUTH NAVIGATION (MANDATORY) — LoginPage and RegisterPage MUST
  navigate to a real authenticated route immediately after a successful
  login/register. The useAuth login() and register() functions resolve
  to the user profile when they succeed and throw on failure, so use
  try/catch and navigate inside the try.

  CORRECT — LoginPage:
      import {{ useNavigate, Link }} from "react-router-dom"
      const navigate = useNavigate()
      const {{ login }} = useAuth()

      const onSubmit = async (e) => {{
        e.preventDefault()
        try {{
          await login({{ email, password }})
          navigate("/dashboard", {{ replace: true }})   // ← MANDATORY
        }} catch (err) {{
          setError(err?.message || "Login failed")
        }}
      }}

  WRONG — login succeeds but page never redirects (the bug we are
  preventing):
      const onSubmit = async (e) => {{
        await login({{ email, password }})
        // ← MISSING navigate(): user lands back on /login with
        //   a populated AuthContext but no visible feedback
      }}

  Target rules:
   * LoginPage / RegisterPage → navigate("/dashboard", {{ replace: true }})
     when /dashboard is mounted. Otherwise navigate to the first
     authenticated route from routes.js (the first ROUTES entry with
     requires === "auth"). Last resort: navigate("/").
   * LogoutPage / logout handler → navigate("/login", {{ replace: true }}).
   * Use {{ replace: true }} so the back button does not re-show the
     auth form after the user is logged in.

- SHARED LAYOUT — App.jsx ships with a Layout component that renders <Navbar />
  and <Footer /> ONCE around every main route via <Outlet />. Page components
  MUST NOT import or render <Navbar />, <Footer />, <Sidebar />, or other chrome
  elements themselves. Doing so produces duplicate navbars on routes that already
  have the Layout, and breaks the nav on any route that skips it.

  CORRECT — page returns only its content:
      export default function MenuPage() {{
        return (
          <section className="max-w-7xl mx-auto px-4 py-8">
            <h1>Menu</h1>
            ...
          </section>
        )
      }}

  WRONG — page embeds chrome (produces duplicate or inconsistent navbar):
      export default function MenuPage() {{
        return (
          <div>
            <Navbar />     {{/* ← FORBIDDEN — Layout already renders this */}}
            <section>...</section>
          </div>
        )
      }}

- ROUTE PLACEMENT — when generating App.jsx route entries:
  * Place MOST routes inside <Route element={{<Layout />}}> so they get chrome.
  * Place ONLY these inside <Route element={{<BareLayout />}}>:
      LoginPage, RegisterPage, ForgotPasswordPage, any 404/error page.
  * BareLayout gives those pages a clean centered-card look without a navbar.
  * Never put a page in BOTH groups. Never skip the Layout wrapper entirely.

- DESIGN BASELINE — every generated page MUST feel modern and polished.

  LAYOUT PRIMITIVES (scaffold-provided — import and use them):
  - <Section> for every page band. bg="panel" alternates sections visually.
      import {{ Section, SectionHeader }} from "@/components/ui/section"
  - <Container> for any centered content block.
      import {{ Container }} from "@/components/ui/container"
  - <Hero> for landing/intro sections.
      import {{ Hero }} from "@/components/ui/hero"
  - <FeatureCard> / <FeatureCardImage> / <FeatureCardBody> for ALL card-shaped UI.
      import {{ FeatureCard, FeatureCardImage, FeatureCardBody }} from "@/components/ui/feature-card"
  - <EmptyState> when a list may have zero items — NEVER render a blank div.
      import {{ EmptyState }} from "@/components/ui/empty-state"
  NEVER write raw `<div className="max-w-...">` containers. NEVER write raw
  `<section className="py-8">`. Use the primitives — they handle spacing.

  PLACEHOLDER IMAGES (always resolves, no API key):
      import {{ PLACEHOLDER, avatarUrl }} from "@/lib/placeholders"
      <img src={{PLACEHOLDER.landscape(1)}} alt="..." loading="eager" className="w-full rounded-2xl object-cover aspect-[4/3]" />
      <img src={{avatarUrl(user.name)}} alt={{user.name}} loading="lazy" className="w-10 h-10 rounded-full object-cover" />
  NEVER use placeholder.com, via.placeholder.com, or any URL that might 404.
  Every <img> tag MUST have: alt (descriptive), loading ("lazy" or "eager"),
  className with object-cover AND an explicit aspect ratio class.

  SPACING
  - Sections: py-12 to py-20 (the <Section> primitive handles this).
  - Heading groups: mb-8 to mb-12. Card grids: gap-6 to gap-8.
  - NEVER use py-2 or py-4 for full-page section padding — produces cramped UI.

  INTERACTIVITY
  - Every clickable element MUST have transition + hover state:
      transition-all duration-200 hover:... focus:outline-none focus:ring-2 focus:ring-accent
  - Cards: hover:-translate-y-1 hover:shadow-xl (use <FeatureCard href="...">)
  - Images inside cards: group-hover:scale-105 transition-transform duration-300

  TYPOGRAPHY
  - h1: text-4xl sm:text-5xl lg:text-6xl font-bold tracking-tight leading-[1.1]
  - h2: text-3xl sm:text-4xl font-bold tracking-tight
  - h3: text-xl sm:text-2xl font-semibold
  - Body: text-base text-text-default leading-relaxed
  - Muted: text-sm text-text-muted
  ALWAYS use text-text-default / text-text-muted, NEVER raw text-gray-XXX.

  BUTTONS
  - Primary: bg-accent text-accent-fg hover:bg-accent/90 rounded-lg px-5 py-2.5 font-medium transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-accent
  - Secondary: bg-surface-panel border border-surface-border text-text-default hover:bg-surface-page rounded-lg px-5 py-2.5 font-medium transition-colors duration-200
  - Ghost: text-text-default hover:bg-surface-panel rounded-lg px-4 py-2 font-medium

  GRIDS
  - Card grid: grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 lg:gap-8
  - Two-column: grid lg:grid-cols-2 gap-8 lg:gap-12 items-start
  - NEVER use single-column at all breakpoints.

  LOADING STATES — skeleton placeholders, NOT blank:
      <div className="animate-pulse bg-surface-border rounded h-48" />

  Reference implementation: frontend/src/pages/HomePage.example.jsx

- FASTAPI ROUTE ORDERING — when multiple routes share a path prefix, declare
  LITERAL paths BEFORE parametrized paths.  FastAPI matches in declaration
  order, so a parametrized /{{id}} declared first will catch literal sibling
  paths before their own handlers can run, producing a 422.

  CORRECT (literal first):
      @router.get("/menu-items/categories")    # literal — declare first
      def list_categories(): ...
      @router.get("/menu-items/{{item_id}}")   # param — declare last
      def get_menu_item(item_id: int): ...

  WRONG (produces 422 on /menu-items/categories):
      @router.get("/menu-items/{{item_id}}")   # ← catches "categories" too!
      def get_menu_item(item_id: int): ...
      @router.get("/menu-items/categories")   # ← unreachable
      def list_categories(): ...

  General rule: more-specific (literal) paths first, /{{param}} paths last,
  among all routes sharing the same prefix.  This applies to every resource
  (posts, items, orders, users, …) — not just menu items.

- SCAFFOLDED NAVBAR — the scaffold ships components/Navbar.jsx with a
  route-driven link composition pattern using spread ternaries:
      const links = [
        ...PUBLIC_NAV,
        ...(user ? AUTH_NAV : GUEST_NAV),
        ...(isAdmin ? ADMIN_NAV : []),
      ]
  EVERY ternary inside a spread MUST have BOTH a then-branch AND an
  else-branch. NEVER write:
      ...(user ? AUTH_NAV),       // ← SYNTAX ERROR — missing : else
      ...(user ? AUTH_NAV)        // ← SYNTAX ERROR — missing : else
  Valid forms only:
      ...(user ? AUTH_NAV : [])
      ...(user ? AUTH_NAV : GUEST_NAV)
  If you modify the Navbar, preserve the existing ternary structure with
  both branches intact.

- PAGE FILE NAMING — pick ONE convention and use it consistently in BOTH the
  file name AND every import path. The standard is <Name>Page.jsx for pages:
      File: MenuPage.jsx    Import: import MenuPage from "@/pages/MenuPage"
      File: HomePage.jsx    Import: import HomePage from "@/pages/HomePage"
  NEVER mix conventions:
      File: MenuPage.jsx, Import: import Menu from "@/pages/Menu"   ← BUG → white page
  The App.jsx route entry must use the exact same name as the file and the import:
      <Route path="/menu" element={{<MenuPage />}} />

- CONTEXT PROVIDERS — every Provider you import in App.jsx MUST wrap the route
  tree. NEVER import a Provider and forget to use it. If you don't intend to
  use it, don't import it. Correct order:
      <QueryClientProvider> → <AuthProvider> → app providers (Cart, Theme…)
        → <BrowserRouter> → <Routes>
  Example with CartProvider:
      <QueryClientProvider client={{queryClient}}>
        <AuthProvider>
          <CartProvider>
            <BrowserRouter>
              <Routes>...</Routes>
            </BrowserRouter>
          </CartProvider>
        </AuthProvider>
      </QueryClientProvider>
  An unused Provider import is always a bug — it will make hooks throw or
  return null, causing the whole page tree to render blank.

Output ONLY the file code. No markdown, no wrapper, no explanations.""" + """

NETWORK CALLS -- ABSOLUTE:
- Every backend request from frontend code MUST go through
  `api` from "@/lib/api". Never call window.fetch directly.
  Never construct your own Authorization header.
      GOOD: import api from "@/lib/api";
            const plants = await api.get("/plants");
            const order  = await api.post("/orders", payload);
      BAD:  const res = await fetch("/api/plants", { headers: {...} });
      BAD:  fetch(url, { headers: { Authorization: `Bearer ${token}` } });
- The api client handles: base URL, JSON serialization,
  Authorization header from localStorage, 401 token cleanup,
  error throwing on non-2xx.
- For login/register flows, use the helpers in @/lib/auth.js
  (they call api internally and persist tokens).

USER MODEL -- DO NOT REDEFINE:
- The User model is shipped as scaffold in app/auth_models.py.
  Do NOT define `class User(Base)` in models.py or anywhere else.
- models.py is also scaffold. It starts with:
      from app.auth_models import User
  Add OTHER models (Profile, Plant, Order, etc.) below that line.
  NEVER redefine User -- auth_routes.py depends on its exact shape.
- For models that need a user foreign key, reference users.id as a
  String column (UUID stored as string). DO NOT use Integer, UUID,
  or GUID -- the scaffold User.id is plain String (VARCHAR), and any
  other FK type will be rejected by Postgres at table-create time
  with "incompatible types: X and character varying".

      GOOD:
          user_id: Mapped[str] = mapped_column(
              String, ForeignKey("users.id"), nullable=False,
          )
          # or 1.x style:
          user_id = Column(String, ForeignKey("users.id"), nullable=False)

      BAD (breaks Neon/Postgres deploy):
          user_id = Column(Integer, ForeignKey("users.id"), ...)
          user_id = Column(UUID, ForeignKey("users.id"), ...)
          user_id = Column(GUID(), ForeignKey("users.id"), ...)

- The User model has exactly: id, email, password_hash, name, role,
  created_at. Do not add extra columns to User -- extend via
  a separate Profile model if needed.

- HANDLERS MUST HANDLE EMPTY/MISSING ROWS (MANDATORY) — every route
  handler MUST be safe when the database table is empty or the
  requested row does not exist. Reachability probes test every endpoint
  on a fresh DB with seed-only data; any handler that crashes on an
  empty/missing row fails the deploy.

  GOOD — singleton lookups guard with 404 or auto-create:
      @router.get("/profile")
      def get_profile(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
          profile = db.query(Profile).filter(Profile.user_id == user.id).first()
          if profile is None:
              # Either auto-create OR 404 — never raise AttributeError.
              profile = Profile(user_id=user.id)
              db.add(profile); db.commit(); db.refresh(profile)
          return profile

  GOOD — list endpoints return [] on empty:
      @router.get("/tasks", response_model=list[TaskOut])
      def list_tasks(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
          rows = db.query(Task).filter(Task.user_id == user.id).all()
          return rows   # empty list is valid JSON []

  GOOD — single-by-id returns 404 when missing:
      @router.get("/tasks/{{task_id}}")
      def get_task(task_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
          task = db.query(Task).filter(Task.id == task_id).first()
          if task is None:
              raise HTTPException(status_code=404, detail="Task not found")
          return task

  WRONG — any of these patterns causes a 500 on first request:
      owner = db.query(Owner).first()
      return owner.profile         # AttributeError when owner is None

      task = db.query(Task).filter(Task.id == task_id).first()
      return {{"title": task.title}} # AttributeError when missing

      return db.query(Task).all()[0]  # IndexError on empty table

  Rule of thumb: every .first() result must be checked for None
  BEFORE attribute access. Every [0] / [-1] index must be guarded by
  a len() check. Every .one() must be wrapped in try/except
  NoResultFound. List endpoints that return rows directly are fine —
  SQLAlchemy returns an empty list on no rows, which serialises to [].

PATH CONVENTION FOR api CLIENT:
- Pass paths RELATIVE to /api, without the /api prefix.
      GOOD: api.get("/dashboard")
      GOOD: api.post("/orders", payload)
      GOOD: api.get("/plants")
  The api client automatically prepends /api.
- Do NOT include /api in the path you pass to api.X.
      BAD:  api.get("/api/dashboard")    // becomes /api/api/dashboard -> 404
      BAD:  api.post("/api/orders", ...) // becomes /api/api/orders -> 404
- The api client tolerates both forms (it strips the duplicate
  /api prefix), but RELATIVE paths are the convention. Be consistent.

AUTH GATING (when auth is enabled in the blueprint):

- For any page that requires login, the route in App.jsx MUST wrap
  the page component in <RequireAuth>:
      GOOD: import RequireAuth from "@/components/RequireAuth";
            <Route path="/dashboard" element={
              <RequireAuth><DashboardPage /></RequireAuth>
            } />
      BAD:  <Route path="/dashboard" element={<DashboardPage />} />
  RequireAuth shows a loading splash while AuthContext resolves /me,
  then redirects unauthenticated users to /login (preserving the
  attempted path in state.from so login can bounce back).

- Public pages (login, register, landing/home for public-only apps)
  MUST NOT be wrapped in RequireAuth. Wrapping login creates an
  infinite redirect loop.

- For TanStack Query calls to protected endpoints, include
  enabled: !!user where user comes from useAuth():
      GOOD: const { user } = useAuth();
            const { data } = useQuery({
              queryKey: ["dashboard"],
              queryFn: () => api.get("/dashboard"),
              enabled: !!user,
            });
      BAD:  useQuery({
              queryKey: ["dashboard"],
              queryFn: () => api.get("/dashboard"),
            });
  Without enabled: !!user, the query fires before auth resolves,
  hits 401, and TanStack reports "Query data cannot be undefined".

- LoginPage MUST redirect to the attempted path after success:
      const location = useLocation();
      const navigate = useNavigate();
      const fromPath = location.state?.from?.pathname || "/dashboard";
      // ...
      await login({ email, password });
      navigate(fromPath, { replace: true });

AUTH GATING -- when blueprint has AUTH_DISABLED:
- Do NOT wrap any page in RequireAuth.
- Do NOT import RequireAuth.
- All pages are public; the app has no login/register/AuthContext.

LANDING ROUTE WIRING -- ABSOLUTE:

Read blueprint.landing_strategy. Wire App.jsx's `/` route:

Strategy "auth_gate":
  import AuthGate from "@/components/AuthGate";
  <Route path="/" element={<AuthGate />} />
  Do NOT wrap AuthGate in RequireAuth -- it handles its own auth check.
  The first protected page (usually /dashboard) is a separate route
  wrapped in RequireAuth.

Strategy "public_home":
  <Route path="/" element={<HomePage />} />
  HomePage renders the app's primary public content. Do NOT create
  LoginPage, RegisterPage, AuthContext, AuthGate, or RequireAuth.

Strategy "public_landing_with_login":
  <Route path="/" element={<HomePage />} />
  HomePage is public. Navbar shows Sign In / Sign Up when no user,
  Profile / Logout when user. Protected routes (e.g., /profile,
  /my-items) are wrapped in RequireAuth. Login redirects back.

In ALL strategies, ensure App.jsx has a `*` catch-all route that
renders a NotFoundPage or redirects to `/`. A white page on an
unknown URL is unacceptable.
"""

    def _build_variable_user_message(
        self,
        file_to_gen: FileToGenerate,
        previously_generated: dict[str, str],
        template: str | None = None,
        contract_endpoints: list | None = None,
    ) -> str:
        """VARIABLE — per-file, not cached.

        Contains only content that changes between files: the target path,
        description, template hint, already-generated dependencies, and
        (for route files) the contract checklist of required endpoints.
        """
        deps_context = ""
        for dep_path in file_to_gen.depends_on:
            if dep_path in previously_generated:
                deps_context += (
                    f"\n\n**Dependency: {dep_path}**\n```\n"
                    f"{previously_generated[dep_path]}\n```"
                )

        template_hint = (
            f"\n\n**Use this template as a starting point:**\n```\n{template}\n```"
            if template else ""
        )

        # Per-file contract checklist for route files.
        checklist_block = ""
        if contract_endpoints:
            # Compute the router's own prefix from the file path so we can
            # show the decorator-relative path (what the LLM actually writes).
            module = file_to_gen.path.split("/")[-1][:-3] if file_to_gen.path.endswith(".py") else ""
            if module == "auth_routes":
                router_prefix = "/auth"
            elif module.startswith("admin_"):
                router_prefix = "/admin/" + module[6:].replace("_", "-")
            else:
                router_prefix = "/" + re.sub(r"_routes$", "", module).replace("_", "-")

            lines = []
            for ep in contract_endpoints:
                method = (ep.get("method") or "GET").upper()
                full_path = ep.get("path") or ""
                # Strip /api + router prefix to show the decorator path.
                probe = re.sub(r"^/api", "", full_path)
                if probe.startswith(router_prefix):
                    deco = probe[len(router_prefix):] or ""
                else:
                    deco = probe
                summary = ep.get("description") or ep.get("summary") or ""
                line = f"  - @router.{method.lower()}(\"{deco}\")   # {full_path}"
                if summary:
                    line += f"  — {summary}"
                lines.append(line)

            checklist_block = (
                f"\n\n**CONTRACT — YOU MUST PRODUCE EXACTLY THESE DECORATORS IN THIS FILE:**\n"
                + "\n".join(lines)
                + f"\n\nRules:\n"
                f"- There are {len(contract_endpoints)} required endpoints. Before finishing the file,\n"
                f"  count your @router.* decorators and confirm you wrote all {len(contract_endpoints)}.\n"
                f"- Match the METHOD and PATH strings exactly as shown above.\n"
                f"- Each decorator path is relative to the router's prefix ({router_prefix}).\n"
                f"- Write REAL handlers: query the DB, return the Pydantic model, use Depends().\n"
                f"  `pass` or `return []` is only acceptable for DELETE 204 responses.\n"
            )

        return (
            f"**File to generate:** {file_to_gen.path}\n"
            f"**Description:** {file_to_gen.description}\n"
            f"**Template:** {file_to_gen.template}\n\n"
            f"**Dependencies (already generated):**\n"
            f"{deps_context if deps_context else '(none)'}"
            f"{template_hint}"
            f"{checklist_block}"
        )

    def _build_mock_content(self, file_to_gen: FileToGenerate) -> str:
        """Return a minimal mock file body for test/mock mode."""
        template = self.templates.get(file_to_gen.template)
        if template:
            return template.strip()
        ext = file_to_gen.path.rsplit(".", 1)[-1] if "." in file_to_gen.path else "txt"
        if ext in ("py",):
            return f'"""Mock generated: {file_to_gen.path}\n{file_to_gen.description}\n"""\n'
        if ext in ("jsx", "tsx", "js", "ts"):
            return (
                f"// Mock generated: {file_to_gen.path}\n"
                f"// {file_to_gen.description}\n"
                "export default function Mock() { return null; }\n"
            )
        return f"# Mock generated: {file_to_gen.path}\n# {file_to_gen.description}\n"

    def generate_single_item(
        self,
        item,
        current_files: dict,
        context: str = "",
    ) -> dict[str, str]:
        """Generate or fix code for ONE checklist item.

        Returns a dict of {file_path: new_content} for the affected file(s).
        Validates every Python file with ast.parse before returning.
        Never raises -- exceptions are caught and logged; returns {} on failure.
        """
        try:
            item_type = getattr(item, "type", None)
            if item_type == "model":
                return self._regen_model_item(item, current_files, context)
            elif item_type == "route":
                return self._regen_route_item(item, current_files, context)
            elif item_type == "page":
                return self._regen_page_item(item, current_files, context)
            elif item_type == "seed":
                return self._regen_seed_item(item, current_files, context)
            elif item_type == "style":
                return self._regen_style_item(item, current_files, context)
            else:
                self.log.warning("generate_single_item.unknown_type", item_id=getattr(item, "id", "?"), type=item_type)
                return {}
        except Exception as exc:
            self.log.warning(
                "generate_single_item.failed",
                item_id=getattr(item, "id", "?"),
                error=str(exc),
            )
            return {}

    def _regen_llm_call(self, prompt: str, file_path: str) -> str:
        """Single focused LLM call for one checklist item. Returns raw content."""
        import ast as _ast_mod
        response = self.client.messages.create(
            model=self.model,
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
        )
        self._track_tokens(response)
        raw = response.content[0].text.strip()
        # Strip markdown fences.
        raw = _re_strip_fences(raw)
        # Validate Python before returning.
        if file_path.endswith(".py"):
            try:
                _ast_mod.parse(raw)
            except SyntaxError as exc:
                self.log.warning(
                    "generate_single_item.invalid_python",
                    file=file_path,
                    error=str(exc),
                )
                raise
        return raw

    def _regen_model_item(self, item, current_files: dict, context: str) -> dict:
        target = "backend/app/models.py"
        existing = current_files.get(target, "")
        col_spec = "\n".join(
            f"  - {c['name']}: {c.get('type','str')}, nullable={c.get('nullable',True)}, unique={c.get('unique',False)}"
            for c in item.columns
        )
        prompt = (
            f"You are modifying {target}.\n"
            f"The checklist requires this model item:\n"
            f"  class_name: {item.class_name}\n"
            f"  table_name: {item.table_name}\n"
            f"  columns:\n{col_spec}\n\n"
            f"Current file contents:\n{existing}\n\n"
            "Produce the COMPLETE updated file with this model class correctly defined "
            "using SQLAlchemy Mapped[] declarations and the shared Base from app.database. "
            "Do not modify unrelated classes. Do not add anything not in the item spec. "
            "Return raw Python file content only, no markdown fences."
        )
        new_content = self._regen_llm_call(prompt, target)
        return {target: new_content}

    def _regen_route_item(self, item, current_files: dict, context: str) -> dict:
        ep = {"method": item.method, "path": item.path}
        target = _route_file_for_endpoint(ep)
        existing = current_files.get(target, "")
        method_lower = item.method.lower()
        # Compute the decorator sub-path relative to the router prefix.
        m = re.match(r"^/api(/[^/]+)?(.*)", item.path)
        decorator_path = (m.group(2) or "") if m else item.path
        if not decorator_path:
            decorator_path = "/"
        prompt = (
            f"You are modifying {target}.\n"
            "The checklist requires this route item:\n"
            f"  method: {item.method}\n"
            f"  path: {item.path}\n"
            f"  auth_required: {item.auth_required}\n"
            f"  summary: {item.summary}\n"
            f"  request_body: {item.request_body}\n"
            f"  response_shape: {item.response_shape}\n\n"
            f"Current file contents:\n{existing}\n\n"
            "Produce the COMPLETE updated file with this endpoint added. "
            f"The decorator should be @router.{method_lower}(\"{decorator_path}\"). "
            "Return real handler code matching request_body and response_shape. "
            "Do not modify unrelated handlers. "
            "Return raw Python file content only, no markdown fences."
        )
        new_content = self._regen_llm_call(prompt, target)
        return {target: new_content}

    def _regen_page_item(self, item, current_files: dict, context: str) -> dict:
        target = item.file_path
        existing = current_files.get(target, "")
        prompt = (
            f"You are creating or modifying {target}.\n"
            f"The checklist requires this page item:\n"
            f"  route: {item.route}\n"
            f"  requires_auth: {item.requires_auth}\n"
            f"  nav_label: {item.nav_label}\n\n"
            f"Current file contents:\n{existing}\n\n"
            "Produce the COMPLETE React page component with a default export. "
            "The page should match its route and purpose. "
            "Return raw JSX file content only, no markdown fences."
        )
        result: dict = {}
        new_content = self._regen_llm_call(prompt, target)
        result[target] = new_content

        # Also ensure the route appears in App.jsx and routes.js.
        for routing_file in ("frontend/src/App.jsx", "frontend/src/lib/routes.js"):
            rc = current_files.get(routing_file, "")
            if rc and item.route not in rc:
                pass  # Leave routing to the orchestrator -- don't partially fix here.

        return result

    def _regen_seed_item(self, item, current_files: dict, context: str) -> dict:
        target = "backend/app/seed.py"
        existing = current_files.get(target, "")
        sample_str = str(item.sample_data[:3]) if item.sample_data else "[]"
        count = max(item.count or 5, 5)  # enforce minimum of 5
        prompt = (
            f"You are modifying {target}.\n"
            f"The checklist requires a seed item:\n"
            f"  model: {item.model}\n"
            f"  count: {count}\n"
            f"  sample_data (first 3): {sample_str}\n\n"
            f"Current file contents:\n{existing}\n\n"
            f"Produce the COMPLETE updated seed.py that inserts EXACTLY {count} "
            f"realistic rows of {item.model} with data shaped like sample_data. "
            f"Use real domain-specific names and descriptions, NOT 'Item 1' or 'Test'. "
            f"For image/photo/avatar columns use picsum.photos/seed/<slug>/400/300 URLs. "
            f"For price/cost columns use realistic ranges (restaurant: $6–$35). "
            "Use the idempotent pattern (check count==0 before inserting). "
            "Do not remove other model seeds. "
            "Return raw Python file content only, no markdown fences."
        )
        new_content = self._regen_llm_call(prompt, target)
        return {target: new_content}

    def _regen_style_item(self, item, current_files: dict, context: str) -> dict:
        """Inject a CSS variable into frontend/src/index.css :root block.

        Defensive normalization:
          * Force css_var to --accent when the LLM emits a synonym; the
            scaffold only reads --accent for the accent color.
          * Coerce value to an "R G B" triplet when given hex (#RRGGBB or
            #RGB) or rgb(r,g,b). Tailwind composes alpha with
            rgb(var(--accent) / <alpha-value>) so the triplet form is required.
        """
        css_path = "frontend/src/index.css"
        existing = current_files.get(css_path, "")
        if not existing:
            return {}

        var_name = (item.css_var or "").strip()
        accent_synonyms = {
            "--color-primary", "--primary", "--brand",
            "--brand-color", "--theme-color", "--accent-color",
        }
        if var_name in accent_synonyms or not var_name:
            var_name = "--accent"

        raw_value = (item.value or "").strip()
        value = self._coerce_color_to_triplet(raw_value)

        # Already present? Replace the existing line in :root so we override prior value.
        pattern = rf"{re.escape(var_name)}\s*:\s*[^;]+;"
        if re.search(pattern, existing):
            new_css = re.sub(pattern, f"{var_name}: {value};", existing, count=1)
        elif ":root {" in existing:
            new_css = existing.replace(
                ":root {",
                f":root {{\n  {var_name}: {value};",
                1,
            )
        else:
            new_css = existing + f"\n:root {{\n  {var_name}: {value};\n}}\n"

        if new_css == existing:
            return {}
        return {css_path: new_css}

    @staticmethod
    def _coerce_color_to_triplet(value: str) -> str:
        """Return 'R G B' triplet. Accepts hex (#RRGGBB / #RGB), rgb(...) /
        rgba(...), or an existing 'R G B' string. Falls back to the input
        when format is unrecognised."""
        import re as _re
        v = value.strip().lower()

        # Already a triplet "R G B"?
        if _re.fullmatch(r"\d{1,3}\s+\d{1,3}\s+\d{1,3}", v):
            return v

        # hex: #RGB or #RRGGBB
        m = _re.fullmatch(r"#?([0-9a-f]{3}|[0-9a-f]{6})", v)
        if m:
            h = m.group(1)
            if len(h) == 3:
                h = "".join(c * 2 for c in h)
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return f"{r} {g} {b}"

        # rgb(r,g,b) / rgba(r,g,b,a)
        m = _re.fullmatch(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})", v)
        if m:
            return f"{int(m.group(1))} {int(m.group(2))} {int(m.group(3))}"

        # Unknown — return as-is and let downstream catch it.
        return value

    def _load_templates(self) -> dict[str, str]:
        return {
            "react_page": """
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

export default function PageName() {
  const [data, setData] = useState(null);

  const { data: items = [], isLoading } = useQuery({
    queryKey: ["items"],
    queryFn: async () => {
      const { data } = await api.get("/api/items");
      return data;
    },
  });

  if (isLoading) return <div>Loading...</div>;

  return (
    <div className="p-6">
      <h1 className="text-3xl font-bold mb-4">Page Title</h1>
      <Card className="p-4">
        {/* Content here */}
      </Card>
    </div>
  );
}
""",
            "react_component": """
import { Card } from "@/components/ui/card";

export default function ComponentName({ data }) {
  return (
    <Card className="p-4">
      <div>{data}</div>
    </Card>
  );
}
""",
            "fastapi_route": """
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.models import Model
from app.schemas import ModelSchema
from app.database import get_db

router = APIRouter(prefix="/api/items", tags=["items"])

@router.get("/")
def list_items(db: Session = Depends(get_db)):
    '''List all items.'''
    items = db.query(Model).all()
    return items

@router.post("/")
def create_item(item: ModelSchema, db: Session = Depends(get_db)):
    '''Create a new item.'''
    db_item = Model(**item.model_dump())
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item
""",
            "db_schema": """
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from app.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True)
    name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
""",
        }
