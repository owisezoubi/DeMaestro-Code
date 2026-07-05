"""ArchitectAgent — reads approved StructuredRequirements + BlueprintResponse,
outputs a GenerationPlan with file structure and generation order.
Also emits a checklist of atomic verifiable items alongside the plan.
"""
import json
import re

import structlog
from anthropic import Anthropic

from app.ai.gemini.agents.blueprint import BlueprintResponse
from app.config import settings, get_agent_model
from app.models.generation_plan import FileToGenerate, GenerationPlan
from app.models.structured_requirements import StructuredRequirements
from app.pipeline.checklist import ChecklistValidationError, validate_checklist, coerce_checklist

_log = structlog.get_logger("architect")

# Equivalence classes for blueprint API routes. Keys are regex patterns that
# match "METHOD /path" strings; values are the single canonical form.
# Applied before the generator sees the blueprint so naming drift is killed
# at the source, not discovered at contract-check time.
_CANONICAL_URL_RULES: list[tuple[str, str]] = [
    # "list current user's X" → always /me sub-path
    (r"^(GET /api/\w+)/my-\w+$",            r"\1/me"),
    (r"^(GET /api/\w+)/mine$",              r"\1/me"),
    (r"^GET /api/my-(\w+)$",               r"GET /api/\1/me"),
    # Password change → always under /auth/me
    (r"^POST /api/auth/change-password$",   "POST /api/auth/me/password"),
    (r"^POST /api/profile/password$",       "POST /api/auth/me/password"),
    (r"^POST /api/users/me/password$",      "POST /api/auth/me/password"),
    (r"^PUT /api/auth/password$",           "POST /api/auth/me/password"),
    (r"^PATCH /api/auth/password$",         "POST /api/auth/me/password"),
    # Current-user profile → always /auth/me
    (r"^GET /api/users/me$",               "GET /api/auth/me"),
    (r"^GET /api/profile$",                "GET /api/auth/me"),
    (r"^GET /api/profile/me$",             "GET /api/auth/me"),
    # Profile update method drift
    (r"^PUT /api/users/me$",               "PATCH /api/auth/me"),
    (r"^PUT /api/profile$",                "PATCH /api/auth/me"),
]


def canonicalize_api_routes(api_routes: list[dict]) -> list[dict]:
    """Rewrite known URL equivalence classes to the single canonical form.

    Kills naming drift before generation starts. Logs every rewrite.
    Deduplicates entries that collapse onto the same (method, path) key.
    """
    out: list[dict] = []
    for route in api_routes:
        method = (route.get("method") or "GET").upper()
        path = route.get("path") or ""
        key = f"{method} {path}"
        new_key = key
        for pattern, replacement in _CANONICAL_URL_RULES:
            if re.fullmatch(pattern, key):
                new_key = re.sub(pattern, replacement, key)
                _log.info(
                    "contract.canonicalized_route",
                    before=key, after=new_key,
                )
                break
        new_method, new_path = new_key.split(" ", 1)
        out.append({**route, "method": new_method, "path": new_path})

    # Dedup entries that collapsed onto the same (method, path) key.
    seen: set[str] = set()
    deduped: list[dict] = []
    for r in out:
        k = f"{r['method']} {r['path']}"
        if k in seen:
            continue
        seen.add(k)
        deduped.append(r)

    return deduped


# Plain string (no f-prefix) so the JSON examples inside are never
# misread as Python format specifiers.
_CHECKLIST_PROMPT_BLOCK = """

CHECKLIST OUTPUT REQUIREMENT:
Alongside the blueprint, produce a top-level "checklist" array. Each entry is
an atomic, verifiable unit. The checklist is the source of truth for what the
generator must produce.

For every model in the data layer, add a model item:
  {"id": "model.User", "type": "model",
   "table_name": "users", "class_name": "User",
   "columns": [
     {"name": "email", "type": "str", "nullable": false, "unique": true},
     {"name": "password_hash", "type": "str", "nullable": false}
   ]}

For every API endpoint, add a route item:
  {"id": "route.POST.api.orders", "type": "route",
   "method": "POST", "path": "/api/orders",
   "auth_required": true,
   "request_body": {"items": "list", "delivery_type": "str"},
   "response_shape": {"id": "int", "status": "str", "total": "float"},
   "summary": "Create a new order"}

For every frontend page, add a page item:
  {"id": "page.MenuPage", "type": "page",
   "file_path": "frontend/src/pages/MenuPage.jsx",
   "route": "/menu", "requires_auth": false,
   "nav_label": "Menu",
   "linked_actions": [{"label": "Add to Cart", "target": "/cart"}]}

For seed data, add a seed item per model with realistic example rows:
  {"id": "seed.menu_items", "type": "seed",
   "model": "MenuItem", "count": 20,
   "sample_data": [{"name": "Margherita Pizza", "price": 12.99, "description": "Classic tomato and mozzarella"}]}

MANDATORY SEED MINIMUMS — sparse seeds make the deployed app look broken:

For any model that appears in a user-facing LIST endpoint (browse, menu, gallery,
feed, catalog, directory), set count to AT LEAST:
  - 15 for browse-heavy apps (menus, recipes, portfolios, catalogs, marketplaces)
  - 10 for list-mainly apps (todos, notes, contacts, projects)
  - 5 for admin-facing lists (orders, submissions, reports)

For SUPPORTING models (categories, tags, statuses) seed a realistic default
set — e.g. for a menu app: 5 categories (Appetizers, Mains, Desserts, Drinks, Sides).

For MEDIA columns (image_url, avatar, cover_image, photo), use:
  https://picsum.photos/seed/<slug>/<width>/<height>
so seeded rows have real-looking images without requiring image hosting.

For PRICE / NUMERIC columns, use realistic domain ranges:
  - Restaurant menu: $6–$35 per dish
  - E-commerce: $10–$200 per product
  A $2 pizza or $0 product looks broken.

For palette or styling rules, add style items.

CRITICAL — the scaffold's index.css uses RGB-triplet CSS variables so
Tailwind can compose alpha with `rgb(var(--accent) / <alpha-value>)`.
You MUST emit:
  - css_var: "--accent"        (the ONLY palette var to override; NEVER use --color-primary, --primary, etc.)
  - value:   "R G B" triplet   (e.g. "220 38 38" for red-600). NEVER hex, NEVER rgb(...), NEVER hsl(...).

Examples by intent:
  red:     {"id": "style.primary_color", "type": "style", "css_var": "--accent", "value": "220 38 38",   "scope": "global"}
  blue:    {"id": "style.primary_color", "type": "style", "css_var": "--accent", "value": "37 99 235",   "scope": "global"}
  green:   {"id": "style.primary_color", "type": "style", "css_var": "--accent", "value": "22 163 74",   "scope": "global"}
  amber:   {"id": "style.primary_color", "type": "style", "css_var": "--accent", "value": "217 119 6",   "scope": "global"}
  purple:  {"id": "style.primary_color", "type": "style", "css_var": "--accent", "value": "124 58 237",  "scope": "global"}
  pink:    {"id": "style.primary_color", "type": "style", "css_var": "--accent", "value": "219 39 119",  "scope": "global"}
  emerald: {"id": "style.primary_color", "type": "style", "css_var": "--accent", "value": "5 150 105",   "scope": "global"}
  teal:    {"id": "style.primary_color", "type": "style", "css_var": "--accent", "value": "13 148 136",  "scope": "global"}
  slate:   {"id": "style.primary_color", "type": "style", "css_var": "--accent", "value": "71 85 105",   "scope": "global"}
  black:   {"id": "style.primary_color", "type": "style", "css_var": "--accent", "value": "23 23 23",    "scope": "global"}

If the user requested a color verbatim (e.g. "red website"), you MUST
emit exactly one style item with css_var "--accent" matching that color.
Do NOT add other style items for that intent — overriding only --accent
keeps surface and text neutrals readable.

Coverage rules (every item below MUST have a checklist entry):
- Every route the blueprint contract specifies MUST have a route item.
- Every page the blueprint UI specifies MUST have a page item.
- Every model the data layer needs MUST have a model item.
- Every model that needs demo data MUST have a seed item.
- If the user requested a primary color, include a style item for it.

The full response shape is:
{
  "technology_stack": "...",
  "files": [...],
  "generation_order": [...],
  "notes": "...",
  "extra_dependencies": [],
  "extra_frontend_dependencies": [],
  "aggregate_endpoints": [],
  "checklist": [...]
}
"""


class ArchitectAgent:
    def __init__(self) -> None:
        self.model = get_agent_model("architect")
        self.log = structlog.get_logger("ArchitectAgent")
        self.token_counter: dict[str, int] = {"input": 0, "output": 0, "calls": 0}

    def architect(
        self,
        sr: StructuredRequirements,
        blueprint: BlueprintResponse,
    ) -> tuple[GenerationPlan, list]:
        """Analyze blueprint and return (generation_plan, checklist).

        The checklist is a list of typed ChecklistItem dataclasses.
        On mock_ai the checklist is empty.
        """
        self.log.info("architect.start", app_name=sr.app_name)

        if settings.mock_ai:
            plan, _cl = self._build_mock_plan(sr)
            self.log.info("architect.done.mock", num_files=len(plan.files))
            return plan, _cl

        client = Anthropic(api_key=settings.anthropic_api_key)
        blueprint_dict = self._enforce_auth_policy(blueprint.model_dump(), sr=sr)
        context = {
            "structured_requirements": sr.model_dump(),
            "blueprint": blueprint_dict,
        }

        response = client.messages.create(
            model=self.model,
            max_tokens=16000,
            messages=[{"role": "user", "content": self._build_architect_prompt(context)}],
        )
        self._track_tokens(response)
        plan_text = response.content[0].text

        try:
            plan, raw_checklist = self._parse_generation_plan(plan_text)
        except (json.JSONDecodeError, ValueError) as exc:
            self.log.warning(
                "architect.parse_failed_retrying",
                error=str(exc),
                tail=plan_text[-300:],
                length=len(plan_text),
            )
            retry_prompt = (
                self._build_architect_prompt(context)
                + "\n\nYour previous attempt was TRUNCATED or produced invalid JSON. "
                  "Output the COMPLETE valid JSON in full. "
                  "Do not abbreviate, do not use markdown code fences. Output must start "
                  "with `{` and end with `}` and be parseable by json.loads. If the plan "
                  "is large, reduce the number of files to the minimum needed but keep "
                  "the JSON syntactically complete."
            )
            retry = client.messages.create(
                model=self.model,
                max_tokens=16000,
                messages=[{"role": "user", "content": retry_prompt}],
            )
            self._track_tokens(retry)
            plan_text = retry.content[0].text
            plan, raw_checklist = self._parse_generation_plan(plan_text)
            self.log.info("architect.retry_succeeded", length=len(plan_text))

        # Validate the checklist; retry once if invalid.
        checklist: list = []
        if raw_checklist:
            try:
                validate_checklist(raw_checklist)
                checklist = coerce_checklist(raw_checklist)
                self.log.info("architect.checklist.valid", count=len(checklist))
            except ChecklistValidationError as val_exc:
                self.log.warning(
                    "architect.checklist.invalid_retrying",
                    error=str(val_exc),
                )
                fix_prompt = (
                    self._build_architect_prompt(context)
                    + f"\n\nYour previous checklist was invalid: {val_exc}. "
                      "Produce a corrected checklist. Every item MUST have an 'id' "
                      "and a 'type' ('model', 'route', 'page', 'seed', or 'style'). "
                      "Return the same JSON structure as before with a corrected checklist."
                )
                try:
                    fix_resp = client.messages.create(
                        model=self.model,
                        max_tokens=16000,
                        messages=[{"role": "user", "content": fix_prompt}],
                    )
                    self._track_tokens(fix_resp)
                    _, raw_checklist2 = self._parse_generation_plan(fix_resp.content[0].text)
                    validate_checklist(raw_checklist2)
                    checklist = coerce_checklist(raw_checklist2)
                    self.log.info("architect.checklist.retry_valid", count=len(checklist))
                except Exception as retry_exc:
                    self.log.error(
                        "architect.checklist.retry_failed",
                        error=str(retry_exc),
                    )
                    checklist = []
        else:
            self.log.warning("architect.checklist.missing")

        self.log.info(
            "architect.done",
            num_files=len(plan.files),
            checklist_items=len(checklist),
            stack=plan.technology_stack,
            model=self.model,
            tokens=self.token_counter,
        )
        return plan, checklist

    def _track_tokens(self, response) -> None:
        if hasattr(response, "usage"):
            self.token_counter["input"] += response.usage.input_tokens
            self.token_counter["output"] += response.usage.output_tokens
        self.token_counter["calls"] += 1

    def _build_architect_prompt(self, context: dict) -> str:
        return f"""You are a software architect planning the code generation for a web application.

**Structured Requirements:**
{json.dumps(context['structured_requirements'], indent=2)}

**Blueprint (DB Schema, API Routes, Frontend Pages):**
{json.dumps(context['blueprint'], indent=2)}

**Your Task:**
Design a complete file structure and generation order for this application.

Use this stack: React 18 (Vite) frontend + FastAPI backend.

DATABASE — the generated app MUST run with zero setup on any machine:
- The generated `database.py` must read DATABASE_URL from the environment and
  DEFAULT to "sqlite:///./app.db" when it is unset.
- When the URL starts with "sqlite", create the engine with
  connect_args={{"check_same_thread": False}}.
- Use SYNCHRONOUS SQLAlchemy only: create_engine, sessionmaker, a get_db()
  dependency, and a create_tables() that calls Base.metadata.create_all(bind=engine).
  Do NOT use async engines or async drivers (no create_async_engine, asyncpg,
  aiosqlite).
- Use ONLY portable column types (Integer, String, Text, Boolean, DateTime, Float,
  ForeignKey). Do NOT use PostgreSQL-only types (JSONB, ARRAY, server-side UUID
  defaults) so the same models work on both SQLite and Postgres.
- main.py must import the models before calling create_tables().
- `database.py` must load a local .env at import time BEFORE reading env vars:
  `from dotenv import load_dotenv; load_dotenv()` (python-dotenv is installed), so a
  user-supplied DATABASE_URL in .env is honored.
- Define `Base = declarative_base()` in database.py. EVERY model file must
  `from app.database import Base` (one shared Base) — never call declarative_base()
  again — so create_tables() actually creates all tables.

DEMO / SEED DATA — REQUIRED, the app must never open empty:
- Include `backend/app/seed.py` exposing `seed_demo_data()` that inserts AT LEAST
  3–5 realistic demo rows per entity (with valid foreign keys linking them).
  Realistic = real-sounding values matching the app's domain, not "Test 1" /
  "Lorem ipsum". For a restaurant: actual menu items + prices. For a task
  planner: actual tasks. Etc.
- Seed Categories / parent entities FIRST, then dependent rows referencing the
  real ids.
- Idempotent: check `db.query(Model).count() == 0` before inserting each entity.
- Print a final log line: `print(f"[seed] done — N items=...", flush=True)` so
  it's visible in the backend startup log.
- If the app has authentication, seed BOTH a demo customer
  (demo@example.com / demo1234) AND a demo admin (admin@example.com / admin1234)
  so the user can immediately try both roles.
- main.py MUST `from app.seed import seed_demo_data` and call
  `seed_demo_data()` once on startup AFTER create_tables(), wrapped in
  try/except that prints any exception (no silent failures).

DEPLOYMENT-READY CONFIG (keep config env-driven; do NOT add deploy files):
- Backend: read DATABASE_URL, CORS_ORIGINS (comma-separated), and the port
  from the environment — bind host 0.0.0.0, port int(os.getenv("PORT",
  os.getenv("BACKEND_PORT", "8100"))). The default port is 8100 (not 8000)
  because DeMaestro itself runs on 8000. Never hardcode localhost in backend code.
- Frontend: VITE_API_BASE_URL defaults to "http://localhost:8100", VITE_PORT
  defaults to 5273. The API base URL must come from import.meta.env.VITE_API_BASE_URL;
  never hardcode a backend URL in components.
- Keep psycopg2-binary available so production can switch to managed Postgres just by
  setting DATABASE_URL. Do NOT create vercel.json, render.yaml, or Procfile.

DEPENDENCIES — a pinned, known-good CORE requirements.txt is already provided
(fastapi, uvicorn, sqlalchemy, pydantic, pydantic-settings, python-dotenv,
python-jose, passlib, psycopg2-binary, python-multipart, alembic). You do NOT rewrite
requirements.txt. If the app needs ADDITIONAL Python packages (e.g. "apscheduler" for
scheduling, "pillow" for images), list them in the plan field `extra_dependencies` as
plain PyPI names WITHOUT version numbers (e.g. ["apscheduler", "pillow"]). They will
be appended to requirements.txt automatically. Keep the list minimal, use only
well-known PyPI packages, and never list a package that's already in the core.

FRONTEND PACKAGES — a pinned core package.json is already provided (React, Vite,
React Router, TanStack Query, axios, Tailwind, shadcn/ui components, lucide-react).
You do NOT rewrite package.json. If the frontend needs ADDITIONAL npm packages (e.g.
"recharts" for charts, "date-fns" for dates), list them in `extra_frontend_dependencies`
as plain npm names WITHOUT versions (scoped names like "@fullcalendar/react" are fine).
They will be merged into package.json automatically. Keep the list minimal; never list
a package already in the core.

Output a JSON GenerationPlan with:
1. technology_stack: "python-postgres"
2. files: array of file objects, each with:
   - path: file path relative to project root (e.g., "backend/app/routes/users.py")
   - description: 1 sentence describing what this file does
   - depends_on: list of file paths this file depends on (e.g., ["backend/app/models.py"])
   - template: one of ["react_page", "react_component", "fastapi_route", "db_schema", "service", "none"]
3. generation_order: ordered list of file paths to generate (respect dependencies)
4. notes: key architecture decisions and constraints
5. extra_dependencies: array of extra PyPI package names the app needs beyond the core
   (names only, no versions); [] if none.
6. extra_frontend_dependencies: array of extra npm package names beyond the core
   (names only, no versions); [] if none.
7. aggregate_endpoints: [] normally; populated when the app is a single-page site.
   Each entry: {{"path": "/api/<slug>/data", "returns": ["entity1", "entity2", ...], "public": true}}
   Example: [{{"path": "/api/portfolio/data", "returns": ["projects", "education", "skills"], "public": true}}]

**Rules:**
- Start with the database: models.py defining a table for EVERY entity (all using
  the shared Base from app.database), then seed.py (demo data).
- Include backend/app/seed.py in both `files` and `generation_order`.
- Then FastAPI app setup (NO auth routes — auth is a static scaffold)
- Then data routes (CRUD endpoints for each entity)
- Then React pages (login, dashboard, entity pages, forms)
- Total files: 10-20 APPLICATION code files (do not count scaffolding)
- Every frontend layout/context/component referenced by an import statement must be
  in your `files` list. If you import @/components/X, @/contexts/Y, or @/layouts/Z,
  those files must appear in the plan. The debugger will stub anything missing as a
  fallback, but the plan should be self-consistent.
- The generator may import shadcn/ui components beyond the 12 core ones (tabs,
  dialog, dropdown-menu, alert-dialog, etc.) because the pipeline will stub any
  missing ones automatically. But for visual polish in real demos, prefer the 12
  guaranteed components.
- DEPENDENCIES PRE-DECLARE: if the planned UI uses react-hook-form, zod,
  @hookform/resolvers, date-fns, recharts, framer-motion, lodash, axios — list
  them in extra_frontend_dependencies. If the backend uses apscheduler, celery,
  redis, requests, httpx, boto3 — list them in extra_dependencies. The debugger
  will recover if you forget, but it costs cycles.
- DEFENSIVE FRONTEND: every useQuery destructure must have a default
  (`data: items = []`); every .filter/.map/.reduce on async data must use
  `(value ?? [])`. Always render a loading state while isLoading.
- SEED DATA: seed.py must insert AT LEAST 3–5 rows per entity with realistic
  domain content (not "Test 1"), parents before children, idempotent
  (count()==0 check), and end with a printed summary line.
- TWO DEMO USERS: if auth exists, seed demo@example.com/demo1234 AND
  admin@example.com/admin1234.
- AGGREGATE ENDPOINT RULE — if the frontend is a single-page site (portfolio,
  profile, landing page, dashboard, marketing site) that displays MULTIPLE entity
  types in one view, the backend MUST expose a single aggregate endpoint at
  /api/<app-slug>/data that returns all public content in one response:

      GET /api/portfolio/data  → {{ projects: [...], education: [...],
                                    experience: [...], skills: [...], about: {{...}} }}

  Per-entity CRUD endpoints (POST /api/projects, PATCH /api/projects/{{id}}, etc.)
  are STILL required for the admin panel to edit content. The aggregate endpoint
  is read-only for public visitors.

  DETECT this case when: ALL user-facing pages pull data from MORE THAN ONE entity
  AND the frontend has fewer than 4 distinct visitor-facing routes. That signals a
  single-page composition.

  When you detect a single-page site, include backend/app/routes/aggregate.py in
  your `files` list and `generation_order`, and populate `aggregate_endpoints` with:
      [{{"path": "/api/<slug>/data", "returns": ["entity1", "entity2", ...], "public": true}}]

  For a CRUD-heavy app with multiple distinct pages (todo app, e-commerce, etc.):
  leave `aggregate_endpoints` as [] — no aggregate route is needed.

- API CONTRACT: every fetch in the frontend MUST hit a path that exists in the
  backend — verify your plan covers both ends of each call.
- AUTH IS PROVIDED AS A STATIC SCAFFOLD — DO NOT include auth endpoints in your
  route list and do NOT plan auth files. The following are guaranteed to exist
  in every generated app without you doing anything:
      POST /api/auth/register  — JSON {{email, password, name}} → {{access_token}}
      POST /api/auth/login     — JSON {{email, password}} → {{access_token}}
      GET  /api/auth/me        — Bearer token → user profile
  backend/app/auth.py, backend/app/routes/auth_routes.py, frontend/src/lib/auth.js,
  and frontend/src/contexts/AuthContext.jsx are static scaffold files. Never list
  them in your plan — the pipeline injects them automatically.
  For the frontend login/register pages: call authApi.login() / authApi.register()
  (imported from @/lib/auth) and use the useAuth() hook from @/contexts/AuthContext.
  The scaffold handles JWT storage, header injection, and token refresh.
- SHARED LAYOUT — the frontend MUST use a single shared Layout component that
  renders chrome (Navbar, Footer) around every main page. The architecture is:

      App.jsx
      ├── Route element=Layout      ← has Navbar + Footer via Outlet
      │     ├── HomePage (/)
      │     ├── MenuPage (/menu)
      │     └── ... (all main pages)
      └── Route element=BareLayout  ← no chrome, clean focused card
            ├── LoginPage (/login)
            └── RegisterPage (/register)

  Rules:
  * Every app MUST have a Navbar component (plan it: frontend/src/components/Navbar.jsx).
  * Every app MUST have a routes config (plan it: frontend/src/lib/routes.js).
    The routes config is the single source of truth for navigation — Navbar.jsx
    and App.jsx both read from it. Include an entry for EVERY page listed in
    the blueprint with show_in_nav: true and the correct requires value:
      null = public, "guest" = logged-out only, "auth" = logged-in, "admin" = admin.
  * Footer.jsx is auto-injected from the scaffold — do NOT include it in the plan.
  * Auth pages (login, register, forgot-password) go in BareLayout; all others
    go in Layout.
  * Never plan a page that imports or renders Navbar or Footer directly;
    those live in the Layout component. The generator will strip them if found.

- SCROLL ANIMATIONS — if the requirements mention scroll animations, fade-in
  effects, "appear as I scroll", "animate in", "reveal on scroll", or similar:
  * Add a note in plan.notes: "Uses scroll-triggered animations — import
    useIntersectionObserver from @/hooks/useIntersectionObserver (scaffold hook,
    NOT inline-defined)."
  * Do NOT add any npm animation library to extra_frontend_dependencies for
    this pattern — the scaffold hook covers it with pure CSS transitions.
  * Components that use scroll animations should receive the [ref, isVisible]
    pair from useIntersectionObserver() and toggle a CSS class on isVisible.

- COLOR DISCIPLINE: tell the generator (via plan.notes) to use the semantic
  Tailwind classes for ALL neutral surfaces. Available classes (from CSS
  variables defined in index.css): bg-surface-page (page background),
  bg-surface-panel + border-surface-border (cards/panels),
  text-text-default (body text), text-text-muted (secondary text).
  For accent colors use bg-accent text-accent-fg hover:bg-accent/90.
  Never use bg-white text-white or same-tone foreground/background combos.
  Never hardcode bg-blue-*, bg-emerald-*, etc. for accents.
- ROUTE STATUS CODES: when planning DELETE / soft-delete / "remove" endpoints,
  decide if they should return 200 with a confirmation body OR 204 with no body.
  Specify this choice in plan.notes so the generator writes the route +
  schema + frontend handler consistently. Default to 200 unless the user
  explicitly wants empty responses — 200 is more forgiving.
- AUTHENTICATED API CLIENT: the frontend MUST have one centralized axios client
  at `frontend/src/lib/api.js` that injects the Bearer token from localStorage.
  Every page/component that calls /api/ imports it. Bare fetch() to /api/
  endpoints is forbidden because it bypasses authentication. Specify in
  plan.notes that the generator must use this client uniformly across all
  generated files.
- DB: database.py MUST `load_dotenv()`, default DATABASE_URL to
  sqlite:///./app.db, pass connect_args={{"check_same_thread": False}} for
  sqlite, use SYNC SQLAlchemy and portable column types.
- IMPORTS: every @/components/ X import must point to a file in your plan OR
  one of the 12 core shadcn components. Bare npm imports must be in
  extra_frontend_dependencies.
- COLOR MODE POLICY:
  - Default: ONE color mode only. Do NOT include ThemeContext.jsx,
    ThemeProvider, ThemeToggle.jsx, `dark:` Tailwind classes, or a theme
    toggle in the navbar unless the user explicitly asks for it.
  - ONLY include a light/dark toggle if the user requirements EXPLICITLY ask
    for dark mode, theme toggle, or color scheme switching.
  - If the requirements do not mention it, do NOT add it speculatively.

## Files you MUST include in your plan (application-specific, not scaffolded)

- frontend/src/lib/routes.js  — routes config (single source of truth for nav)
- frontend/src/components/Navbar.jsx  — reads from routes.js, renders nav links

## Files you must NOT include in your plan

These scaffolding files are provided by deterministic templates and will overwrite
anything you generate. Including them wastes tokens and causes generation failures.

- backend/requirements.txt
- backend/Dockerfile
- backend/app/__init__.py
- backend/app/routes/__init__.py
- frontend/package.json
- frontend/Dockerfile
- frontend/vite.config.js
- frontend/tailwind.config.js
- frontend/postcss.config.js
- frontend/index.html
- frontend/src/main.jsx
- frontend/src/index.css
- frontend/src/lib/utils.js
- frontend/src/components/ui/*.jsx  (button, card, input, label, textarea, badge, alert,
  avatar, separator, scroll-area, skeleton, tooltip)
- docker-compose.yml
- .env.example
- backend/app/auth.py           (auth scaffold — auto-injected)
- backend/app/routes/auth_routes.py  (auth scaffold — auto-injected)
- frontend/src/lib/auth.js      (auth scaffold — auto-injected)
- frontend/src/contexts/AuthContext.jsx  (auth scaffold — auto-injected)
- frontend/src/components/Footer.jsx  (footer scaffold — auto-injected, default impl)
- frontend/src/lib/api.js             (axios client scaffold — auto-injected, Vite-proxy baseURL)
- frontend/src/hooks/useIntersectionObserver.js  (hook scaffold — auto-injected)
- frontend/src/hooks/useMediaQuery.js             (hook scaffold — auto-injected)
- frontend/src/hooks/useDebounce.js               (hook scaffold — auto-injected)
- frontend/src/hooks/useLocalStorage.js           (hook scaffold — auto-injected)
- frontend/src/hooks/index.js                     (hook scaffold — auto-injected)
- SETUP.md

Focus your plan on APPLICATION code only: models, schemas, routes, services,
pages, and app-specific components. Do not generate scaffolding.

AUTH GATE — check `structured_requirements.auth_required` (boolean or null):

  If auth_required is FALSE (explicitly disabled):
    - Do NOT include LoginPage.jsx, RegisterPage.jsx, or ForgotPasswordPage.jsx
      in the file plan.
    - Do NOT include any /auth/* routes in the api_routes list.
    - Do NOT import or reference auth.py, auth_routes.py, AuthContext, or auth.js.
    - Do NOT seed the auth scaffold files in the plan notes.
    - The User model is unnecessary unless another entity has a foreign key to it.
      Omit it from models.py if unused.
    - The app is fully public — no login, no JWT, no session.
    - seed.py should NOT seed demo@example.com or admin@example.com.
    - main.py must NOT import or include auth_router.
    - Navbar has NO "Sign In" / "Sign Up" / "Log out" links.
    - In plan.notes add: "AUTH_DISABLED: app is fully public, no auth scaffold."

  If auth_required is TRUE or null (default, auth enabled):
    - Include the full auth scaffold as described above.
    - Include LoginPage.jsx, RegisterPage.jsx in the plan.
    - Include /auth/* routes (handled by static scaffold — do NOT plan them).

AUTH PROVIDERS (HARD CONSTRAINT):
The ONLY supported authentication method is email + password. The scaffold's
auth.py and auth_routes.py implement this and nothing else.
- NEVER plan files for Google OAuth, GitHub OAuth, Facebook, Apple, Microsoft,
  magic links, MFA, TOTP, SMS OTP, or any other provider.
- NEVER include routes like /auth/google, /auth/callback, /auth/oauth, or
  /auth/login/<provider> in the api_routes list.
- NEVER add "authlib", "itsdangerous", "google-auth", "python-social-auth",
  "django-allauth", or similar to extra_dependencies.
- LoginPage.jsx and RegisterPage.jsx contain ONLY email + password fields.
  No Google button. No "Or continue with" divider.
- If the requirements mention any alternative auth method, ignore it silently.
  The analyst already downgraded it upstream.

Output ONLY valid JSON, no markdown wrapper.""" + _CHECKLIST_PROMPT_BLOCK

    def _enforce_auth_policy(self, blueprint_dict: dict, sr=None) -> dict:
        """Enforce the auth policy on the blueprint dict before the architect uses it.

        1. Strips social/OAuth routes and providers (always).
        2. When sr.auth_required is explicitly False, strips all auth pages and
           routes from the blueprint so the architect never plans them.
        """
        _OAUTH_PATH_RE = re.compile(
            r"/(?:google|github|facebook|apple|microsoft|twitter|oauth|callback|social)"
            r"(?:/|$)",
            re.IGNORECASE,
        )
        # Strip OAuth routes from api_routes list.
        api_routes = blueprint_dict.get("api_routes", [])
        filtered_routes = []
        for route in api_routes:
            path = route.get("path", "")
            if _OAUTH_PATH_RE.search(path):
                self.log.info("architect.auth_policy.removed_oauth_route", path=path)
                continue
            filtered_routes.append(route)
        blueprint_dict["api_routes"] = filtered_routes

        # ── Canonicalize API route URLs ───────────────────────────────────────
        # Rewrite naming-drift variants to the single canonical form so the
        # generator and contract checker always agree on URL spelling.
        before_count = len(blueprint_dict["api_routes"])
        blueprint_dict["api_routes"] = canonicalize_api_routes(
            blueprint_dict["api_routes"]
        )
        after_count = len(blueprint_dict["api_routes"])
        if after_count != before_count:
            self.log.info(
                "contract.canonicalized",
                before=before_count, after=after_count,
                dropped=before_count - after_count,
            )

        # ── Blueprint path validation ─────────────────────────────────────────
        # Validate every contract path before generation starts. Catches
        # malformed paths early so they don't produce mysterious contract misses.
        for route in blueprint_dict.get("api_routes", []):
            path = route.get("path", "")
            if not path:
                continue
            if not path.startswith("/api/"):
                self.log.warning(
                    "architect.blueprint_path.missing_api_prefix",
                    path=path,
                    fix="prepending /api",
                )
                route["path"] = "/api/" + path.lstrip("/")
            elif "/api/api/" in path:
                self.log.error(
                    "architect.blueprint_path.doubled_api_prefix",
                    path=path,
                    fix="collapsing /api/api/ → /api/",
                )
                route["path"] = path.replace("/api/api/", "/api/", 1)
            if route["path"].endswith("/") and len(route["path"]) > 1:
                route["path"] = route["path"].rstrip("/")

        # Enforce providers list if the blueprint carries one.
        frontend = blueprint_dict.get("frontend", {})
        if isinstance(frontend, dict):
            auth = frontend.get("auth", {})
            if isinstance(auth, dict):
                providers = auth.get("providers", [])
                filtered = [p for p in providers if p == "email_password"]
                if filtered != providers:
                    self.log.info(
                        "architect.auth_policy.downgraded_providers",
                        original=providers, kept=filtered,
                    )
                    auth["providers"] = filtered or (["email_password"] if providers else [])
                    frontend["auth"] = auth
                    blueprint_dict["frontend"] = frontend

        # When auth is explicitly disabled: strip all auth pages and auth API routes.
        auth_disabled = (
            sr is not None
            and getattr(sr, "auth_required", None) is False
        )
        if auth_disabled:
            self.log.info("architect.auth_policy.no_auth_mode")

            # Strip /auth/* routes from the blueprint.
            blueprint_dict["api_routes"] = [
                r for r in blueprint_dict.get("api_routes", [])
                if not r.get("path", "").startswith("/auth/")
            ]

            # Strip auth-related pages from frontend_pages.
            _AUTH_PAGE_NAMES = frozenset({
                "loginpage", "registerpage", "forgotpasswordpage",
                "login", "register", "forgotpassword", "signin", "signup",
            })
            blueprint_dict["frontend_pages"] = [
                p for p in blueprint_dict.get("frontend_pages", [])
                if p.get("name", "").lower().replace(" ", "") not in _AUTH_PAGE_NAMES
            ]

            # Write the no-auth flag into the blueprint for downstream use.
            blueprint_dict.setdefault("frontend", {})["auth"] = {
                "enabled": False, "providers": []
            }

        return blueprint_dict

    def _parse_generation_plan(self, plan_text: str) -> tuple[GenerationPlan, list]:
        """Parse architect response into (GenerationPlan, raw_checklist list)."""
        cleaned = re.sub(r"^```(?:json)?\s*", "", plan_text.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)
        raw = json.loads(cleaned.strip())
        raw_checklist = raw.pop("checklist", []) or []
        plan = GenerationPlan(**raw)
        return plan, raw_checklist

    def _build_mock_plan(self, sr: StructuredRequirements) -> GenerationPlan:
        files = [
            FileToGenerate(
                path="backend/app/models.py",
                description="SQLAlchemy ORM models for all database tables.",
                depends_on=[],
                template="db_schema",
            ),
            FileToGenerate(
                path="backend/app/database.py",
                description="Database connection and session factory.",
                depends_on=[],
                template="none",
            ),
            FileToGenerate(
                path="backend/app/routes/auth.py",
                description="FastAPI auth routes: register, login, logout.",
                depends_on=["backend/app/models.py", "backend/app/database.py"],
                template="fastapi_route",
            ),
            FileToGenerate(
                path="backend/app/routes/items.py",
                description=f"FastAPI CRUD routes for {sr.entities[0].name if sr.entities else 'items'}.",
                depends_on=["backend/app/models.py", "backend/app/database.py"],
                template="fastapi_route",
            ),
            FileToGenerate(
                path="backend/app/main.py",
                description="FastAPI application factory and route registration.",
                depends_on=["backend/app/routes/auth.py", "backend/app/routes/items.py"],
                template="none",
            ),
            FileToGenerate(
                path="backend/app/seed.py",
                description="Idempotent demo-data seeding for all tables.",
                depends_on=["backend/app/models.py", "backend/app/database.py"],
                template="none",
            ),
            FileToGenerate(
                path="frontend/src/pages/Login.jsx",
                description="Login page with email/password form.",
                depends_on=[],
                template="react_page",
            ),
            FileToGenerate(
                path="frontend/src/pages/Dashboard.jsx",
                description="Main dashboard page showing summary stats.",
                depends_on=[],
                template="react_page",
            ),
        ]
        return GenerationPlan(
            technology_stack="python-postgres",
            files=files,
            generation_order=[f.path for f in files],
            notes=f"Mock plan for {sr.app_name}. Stack: React 18 + FastAPI + SQLite (Postgres-ready).",
        ), []
