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

        prompt = self._build_generate_prompt(file_to_gen, plan, blueprint, previously_generated, template)

        client = Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=8000,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        )

        content = response.content[0].text
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
        template: str | None = None,
    ) -> str:
        deps_context = ""
        for dep_path in file_to_gen.depends_on:
            if dep_path in previously_generated:
                deps_context += f"\n\n**Dependency: {dep_path}**\n```\n{previously_generated[dep_path]}\n```"

        template_hint = (
            f"\n\n**Use this template as a starting point:**\n```\n{template}\n```" if template else ""
        )

        return f"""You are a code generator. Generate ONLY the file content, no explanations.

**File to generate:** {file_to_gen.path}
**Description:** {file_to_gen.description}
**Template:** {file_to_gen.template}

**Blueprint (for reference):**
- Database tables: {[t.name for t in blueprint.database_schema]}
- API routes: {[r.path for r in blueprint.api_routes]}
- Frontend pages: {[p.name for p in blueprint.frontend_pages]}

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

**File-specific requirements (follow whichever applies to {file_to_gen.path}):**
- backend/app/database.py: at the top do `from dotenv import load_dotenv; load_dotenv()`;
  read `DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")`; if it starts
  with "sqlite", create the engine with connect_args={{"check_same_thread": False}};
  define Base = declarative_base(), SessionLocal, a get_db() generator, and
  create_tables() calling Base.metadata.create_all(bind=engine). SYNC SQLAlchemy only.
- model files: `from app.database import Base` (do NOT create a new declarative_base);
  use only portable column types (Integer, String, Text, Boolean, DateTime, Float,
  ForeignKey) — no JSONB/ARRAY/server-side UUID defaults.
- backend/app/seed.py: expose seed_demo_data() that inserts several demo rows per
  entity ONLY when its table is empty (idempotent); if auth exists, seed a demo user
  demo@example.com / demo1234 (hashed).
- backend/app/main.py: import all models, then on startup call create_tables() then
  seed_demo_data(); configure CORS from os.getenv("CORS_ORIGINS",
  "http://localhost:5173").
- any frontend file that calls the API: use
  `const API = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000"` and
  prefix every request with `${{API}}`. Never hardcode a backend URL.

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

  const { data: items, isLoading } = useQuery({
    queryKey: ["items"],
    queryFn: async () => {
      const API = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
      const res = await fetch(`${API}/api/items`);
      return res.json();
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
