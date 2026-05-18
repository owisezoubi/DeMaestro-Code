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
Design a complete file structure and generation order for this application. Use the default stack: React 18 + FastAPI + PostgreSQL.

Output a JSON GenerationPlan with:
1. technology_stack: "python-postgres"
2. files: array of file objects, each with:
   - path: file path relative to project root (e.g., "backend/app/routes/users.py")
   - description: 1 sentence describing what this file does
   - depends_on: list of file paths this file depends on (e.g., ["backend/app/models.py"])
   - template: one of ["react_page", "react_component", "fastapi_route", "db_schema", "service", "none"]
3. generation_order: ordered list of file paths to generate (respect dependencies)
4. notes: key architecture decisions and constraints

**Rules:**
- Start with database schema (models.py, create_tables.sql)
- Then FastAPI app setup and auth routes
- Then data routes (CRUD endpoints for each entity)
- Then React pages (login, dashboard, entity pages, forms)
- Total files: 10-20 APPLICATION code files (do not count scaffolding)

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
- frontend/src/components/ui/*.jsx  (button, card, input, label, textarea, badge, alert, avatar, separator, scroll-area, skeleton, tooltip)
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
            notes=f"Mock plan for {sr.app_name}. Stack: React 18 + FastAPI + PostgreSQL.",
        )
