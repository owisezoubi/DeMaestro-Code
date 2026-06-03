"""ArchitectAgent — reads approved StructuredRequirements + BlueprintResponse,
outputs a GenerationPlan with file structure and generation order."""
import json
import re

import structlog
from anthropic import Anthropic

from app.ai.gemini.agents.blueprint import BlueprintResponse
from app.config import settings
from app.models.generation_plan import FileToGenerate, GenerationPlan
from app.models.structured_requirements import StructuredRequirements


class ArchitectAgent:
    def __init__(self) -> None:
        self.model = settings.claude_model
        self.log = structlog.get_logger("ArchitectAgent")

    def architect(self, sr: StructuredRequirements, blueprint: BlueprintResponse) -> GenerationPlan:
        """Analyze blueprint and return a file structure + generation order."""
        self.log.info("architect.start", app_name=sr.app_name)

        if settings.mock_ai:
            plan = self._build_mock_plan(sr)
            self.log.info("architect.done.mock", num_files=len(plan.files))
            return plan

        client = Anthropic(api_key=settings.anthropic_api_key)
        context = {
            "structured_requirements": sr.model_dump(),
            "blueprint": blueprint.model_dump(),
        }

        response = client.messages.create(
            model=self.model,
            max_tokens=4000,
            messages=[
                {
                    "role": "user",
                    "content": self._build_architect_prompt(context),
                }
            ],
        )

        plan_text = response.content[0].text
        plan = self._parse_generation_plan(plan_text)

        self.log.info("architect.done", num_files=len(plan.files), stack=plan.technology_stack)
        return plan

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
- Backend: read DATABASE_URL, CORS_ORIGINS (comma-separated), and the port from the
  environment — bind host 0.0.0.0, port int(os.getenv("PORT", "8000")). Never
  hardcode localhost in backend code.
- Frontend: the API base URL must come from import.meta.env.VITE_API_BASE_URL
  (default "http://localhost:8000"); never hardcode a backend URL in components.
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

**Rules:**
- Start with the database: models.py defining a table for EVERY entity (all using
  the shared Base from app.database), then seed.py (demo data).
- Include backend/app/seed.py in both `files` and `generation_order`.
- Then FastAPI app setup and auth routes
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
- API CONTRACT: every fetch in the frontend MUST hit a path that exists in the
  backend — verify your plan covers both ends of each call.
- DB: database.py MUST `load_dotenv()`, default DATABASE_URL to
  sqlite:///./app.db, pass connect_args={{"check_same_thread": False}} for
  sqlite, use SYNC SQLAlchemy and portable column types.
- IMPORTS: every @/components/ X import must point to a file in your plan OR
  one of the 12 core shadcn components. Bare npm imports must be in
  extra_frontend_dependencies.

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
- SETUP.md

Focus your plan on APPLICATION code only: models, schemas, routes, services,
pages, and app-specific components. Do not generate scaffolding.

Output ONLY valid JSON, no markdown wrapper."""

    def _parse_generation_plan(self, plan_text: str) -> GenerationPlan:
        # Strip markdown code fences if present
        cleaned = re.sub(r"^```(?:json)?\s*", "", plan_text.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)
        plan_dict = json.loads(cleaned.strip())
        return GenerationPlan(**plan_dict)

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
        )
