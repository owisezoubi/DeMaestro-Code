"""GeneratorAgent — generates individual source files using Claude."""
import re

import structlog
from anthropic import Anthropic

from app.ai.gemini.agents.blueprint import BlueprintResponse
from app.config import settings
from app.models.generation_plan import FileToGenerate, GenerationPlan

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
})


class GeneratorAgent:
    def __init__(self) -> None:
        self.model = settings.claude_model
        self.log = structlog.get_logger("GeneratorAgent")
        self.templates = self._load_templates()

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

        prompt = self._build_generate_prompt(
            file_to_gen, plan, blueprint, previously_generated,
            structured_requirements=structured_requirements, template=template,
        )

        client = Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=16000,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        )

        content = response.content[0].text
        # Detect mid-statement truncation. JSX should end with } or );
        stripped = content.rstrip()
        last_chars = stripped[-3:] if stripped else ""
        looks_truncated = (
            stop_reason := getattr(response, "stop_reason", None)
        ) == "max_tokens" or last_chars in (">", '"/>', "/>", '"',) or (
            stripped and stripped[-1] not in ("}", ")", ";", "`", "\n")
        )
        if looks_truncated:
            self.log.warning(
                "generate_file.truncated_retry",
                path=file_to_gen.path,
                content_length=len(content),
                stop_reason=stop_reason,
                tail=stripped[-200:],
            )
            continuation_prompt = prompt + (
                f"\n\n**Your previous attempt was TRUNCATED at the token limit. "
                f"Here is exactly what you produced — output the COMPLETE, FULL file "
                f"again, finishing all open tags / brackets / functions. Do NOT add "
                f"a preface. Output only the complete file code:**\n```\n{content[-1500:]}\n```\n"
            )
            retry = client.messages.create(
                model=self.model,
                max_tokens=16000,
                messages=[{"role": "user", "content": continuation_prompt}],
            )
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
- COLOR DISCIPLINE (CRITICAL — generated apps fail when these are violated):
  For NEUTRAL surfaces (backgrounds, text, borders) you MUST use the semantic
  CSS classes already defined in the template — do NOT pick `bg-slate-X` and
  `text-slate-Y` by hand. The semantic classes carry their own dark variants.

    Surface                  → class
    Page background          → surface-page
    Subtle bg (sidebar/nav)  → surface-nav       (has right border too)
    Elevated bg              → surface-elevated  (e.g. sticky header, footer)
    Card/panel               → surface-panel     (includes border + text)
    Muted bg (chips/disabled)→ surface-muted
    Form inputs              → surface-input     (bg+text+border+placeholder)
    Main body text           → text-default      (NEVER bare text-slate-800)
    Secondary text           → text-muted
    Captions / timestamps    → text-subtle
    Border                   → border-default
    Horizontal rule          → divider
    Status feedback          → surface-success / surface-warning / surface-danger

  For ACCENT colors (buttons, links, active nav, focus rings, icon highlights)
  use the `primary-*` Tailwind classes (primary-50 … primary-900). The primary
  palette has been swapped to the user's chosen color — `bg-primary-600`,
  `text-primary-700`, `hover:bg-primary-50`, etc.

  ABSOLUTE BANS:
  - NEVER write `bg-slate-900` without a matching `text-slate-100` (or use
    surface-page / surface-panel which already pair them).
  - NEVER write any `dark:bg-slate-X` without a `dark:text-Y` in the SAME
    className — foreground must always travel with its background.
  - NEVER write bare `text-slate-800` or `text-slate-900` without a dark variant
    — on dark:bg-slate-950 those are invisible. Use .text-default instead.
  - NEVER hardcode `bg-blue-*`, `text-blue-*`, `bg-emerald-*` (except status
    surfaces), `bg-orange-*`, `bg-purple-*`, `bg-pink-*` for accents — those
    break the palette swap.
  - NEVER mix `bg-white text-white` or `bg-slate-900 text-slate-900` (dead text).
  - NEVER use `text-slate-400` on a light background — too low contrast.
  - NEVER use `text-slate-600` on `bg-slate-800` or darker — too low contrast.

  Common component recipes:
    INPUTS / TEXTAREAS:
      ✅ <input className="surface-input w-full rounded-md px-3 py-2 text-sm" />
    NAV / SIDEBAR:
      ✅ <nav className="surface-nav w-64 h-screen p-4">
    TABLE ROWS (alternating):
      ✅ odd rows: surface-elevated  |  even rows: surface-page
      ✅ <tr className="surface-elevated hover:bg-slate-100 dark:hover:bg-slate-800">
    MODALS / DIALOGS:
      ✅ <div className="surface-panel rounded-2xl shadow-xl p-6">
    PAGE WRAPPER:
      ✅ <div className="surface-page min-h-screen p-6">
    STICKY HEADER:
      ✅ <header className="surface-elevated border-b border-default sticky top-0">

  Pattern examples:
    ✅  <div className="surface-page min-h-screen p-6">
    ✅  <div className="surface-panel rounded-xl p-4">
            <p className="text-muted text-sm">Last watered 3 days ago</p>
          </div>
    ✅  <button className="bg-primary-600 hover:bg-primary-700 text-white rounded-md px-4 py-2">
    ❌  <div className="bg-slate-900 p-4">         (no foreground — text disappears in dark)
    ❌  <p className="text-slate-800">              (invisible in dark — use text-default)
    ❌  <p className="text-slate-800 dark:text-slate-200">   (use .text-default instead)
    ❌  <input className="bg-white border-slate-300 text-slate-900">  (use surface-input)
    ❌  <button className="bg-blue-500 text-white">  (kills palette swap)
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

- API CLIENT MUST EXIST: when planning files, every frontend page or component
  that hits /api/ MUST import from `@/lib/api`. The `frontend/src/lib/api.js`
  file (already in your plan list) defines:
      import axios from "axios";
      export const api = axios.create({{
        baseURL: import.meta.env.VITE_API_BASE_URL || "http://localhost:8100",
      }});
      api.interceptors.request.use((config) => {{
        const token = localStorage.getItem("access_token");
        if (token) config.headers.Authorization = `Bearer ${{token}}`;
        return config;
      }});

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

- DARK MODE TOGGLE MUST ACTUALLY WORK: when generating ThemeContext.jsx (or
  any theme provider), the theme-change useEffect MUST apply/remove the `dark`
  class on `document.documentElement` — not just write to localStorage. The
  required pattern is:
      useEffect(() => {{
        const root = document.documentElement;
        if (theme === 'dark') {{
          root.classList.add('dark');
        }} else {{
          root.classList.remove('dark');
        }}
        localStorage.setItem('theme', theme);
      }}, [theme]);
  Without the root.classList toggle, Tailwind's dark: variants never activate
  and the page looks identical regardless of theme state. Also: read the
  initial theme from localStorage with a SSR-safe guard:
      const stored = typeof window !== 'undefined' ? localStorage.getItem('theme') : null;
      const [theme, setTheme] = useState(stored || 'light');
- COLOR PAIRING RECIPES (text-on-background contrast):
  Rule: contrast = |text_number - bg_number| must be ≥ 400. Always pair from
  opposite ends of the scale (light bg → dark text; dark bg → light text).

  PRIMARY ACCENT SURFACES (use these exact pairings, do not pick levels yourself):
      Light mode bg        Light text            Dark mode equivalent
      bg-primary-50        text-primary-900      dark:bg-primary-950  dark:text-primary-100
      bg-primary-100       text-primary-900      dark:bg-primary-900  dark:text-primary-100
      bg-primary-600       text-white            dark:bg-primary-500  dark:text-white
      bg-primary-700       text-white            dark:bg-primary-600  dark:text-white
      bg-primary-900       text-primary-100      dark:bg-primary-800  dark:text-primary-50

  TEXT-ONLY (no primary bg):
      Body text            → text-default              (slate-900 / dark:slate-100)
      Secondary info       → text-muted                (slate-600 / dark:slate-400)
      Captions, meta       → text-subtle               (slate-500 / dark:slate-400)
      Accent link/label    → text-primary-700 dark:text-primary-300  (on neutral bg only)

  ACTIVE/SELECTED NAV ITEM:
      ✅ bg-primary-50 text-primary-900 dark:bg-primary-950 dark:text-primary-100
      ✅ bg-primary-100 text-primary-700 dark:bg-primary-900 dark:text-primary-300

  BADGE / PILL patterns:
      ✅ bg-primary-100 text-primary-800 dark:bg-primary-900 dark:text-primary-200
      ✅ surface-muted  (for neutral/disabled chips)

  FORBIDDEN pairings (fail contrast check):
      ✗ text-primary-300 on bg-primary-100   (300 - 100 = 200, too close)
      ✗ text-primary-700 on bg-primary-900   (900 - 700 = 200, too close in dark)
      ✗ text-primary-400 on bg-white          (too light, fails WCAG AA)
      ✗ text-slate-800 on bg-slate-700       (100-gap — always invisible)
      ✗ text-slate-500 on bg-slate-400       (same-range — barely readable)
      ✗ text-slate-400 on bg-white           (too light for body text)
      ✗ text-slate-600 on dark:bg-slate-800  (slate-600 on dark bg is invisible)

  DARK MODE SPECIFIC CHECK: every place you write `dark:bg-slate-X`, the paired
  `dark:text-Y` in the same element must satisfy Y ≤ X - 400. Examples:
      dark:bg-slate-950 → dark:text-slate-100 ✅ (950-100=850)
      dark:bg-slate-900 → dark:text-slate-100 ✅ (900-100=800)
      dark:bg-slate-800 → dark:text-slate-200 ✅ (800-200=600)
      dark:bg-slate-800 → dark:text-slate-500 ✗ (800-500=300, too low)

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

- SQLALCHEMY 2.0 ANNOTATIONS (boot fails if violated):
  When writing files that subclass Base (typically backend/app/models.py),
  PICK ONE pattern and use it consistently across every model and every
  relationship in that file:

    Modern (preferred):
        from sqlalchemy.orm import Mapped, mapped_column, relationship
        class User(Base):
            __tablename__ = "users"
            id: Mapped[int] = mapped_column(primary_key=True)
            email: Mapped[str] = mapped_column(unique=True, index=True)
            tasks: Mapped[list["Task"]] = relationship(back_populates="user")

    Legacy (also valid):
        from sqlalchemy import Column, Integer, String, ForeignKey
        from sqlalchemy.orm import relationship
        class User(Base):
            __tablename__ = "users"
            id = Column(Integer, primary_key=True)
            email = Column(String, unique=True, index=True)
            tasks = relationship("Task", back_populates="user")

  NEVER mix them. NEVER write `tasks: list["Task"] = relationship(...)` —
  that's a type annotation without Mapped[] and SQLAlchemy refuses to boot.

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
  read `DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")`; if it starts
  with "sqlite", create the engine with connect_args={{"check_same_thread": False}};
  define Base = declarative_base(), SessionLocal, a get_db() generator, and
  create_tables() calling Base.metadata.create_all(bind=engine). SYNC SQLAlchemy only.
- model files: `from app.database import Base` (do NOT create a new declarative_base);
  use only portable column types (Integer, String, Text, Boolean, DateTime, Float,
  ForeignKey) — no JSONB/ARRAY/server-side UUID defaults.
- backend/app/seed.py: expose seed_demo_data() that inserts 3–5 realistic rows
  per entity in dependency order (parents before children), reading actual field
  names from models. Use real-sounding domain content, not placeholders. End with
  a `print(f"[seed] done — ...", flush=True)`. Idempotent (empty-table check).
  If auth exists, seed BOTH demo@example.com/demo1234 AND
  admin@example.com/admin1234 (both properly hashed).
- backend/app/main.py: import all models, then on startup call create_tables(),
  then call seed_demo_data() inside a try/except that prints any exception. CORS
  from os.getenv("CORS_ORIGINS", "http://localhost:5173").
- any frontend file that calls the API: import from `@/lib/api` and use the
  centralized `api` client — NEVER use bare fetch(). The client reads
  VITE_API_BASE_URL automatically and injects the auth token.

Output ONLY the file code. No markdown, no wrapper, no explanations."""

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
