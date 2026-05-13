"""Gemini AI client for DeMaestro (W3-A)."""
import json
import time
from functools import lru_cache
from pathlib import Path

import google.generativeai as genai
import structlog

from app.config import settings
from app.models.structured_requirements import (
    AmbiguityFlag,
    ClarificationAnswer,
    Entity,
    FundamentalStatus,
    RequirementCategory,
    RequirementValidation,
    StructuredRequirements,
    UserRequirement,
)

log = structlog.get_logger(__name__)

_PROMPT_DIR = Path(__file__).parent / "prompts"

_passing_validation = RequirementValidation(
    atomicity=FundamentalStatus.passes,
    unambiguity=FundamentalStatus.passes,
    verifiability=FundamentalStatus.passes,
    consistency=FundamentalStatus.not_evaluated,
)

_MOCK_SR = StructuredRequirements(
    app_name="TodoApp",
    summary=(
        "A simple task management application that allows authenticated users to create, "
        "organize, and track their personal todo items. Users can manage tasks with titles, "
        "descriptions, and completion status."
    ),
    entities=[
        Entity(
            name="User",
            description="An authenticated user of the application",
            fields=["id", "email", "created_at"],
        ),
        Entity(
            name="Todo",
            description="A task item belonging to a user",
            fields=["id", "title", "description", "completed", "created_at"],
            relationships=["belongs to User"],
        ),
    ],
    user_requirements=[
        UserRequirement(
            id="UR-01",
            statement="A logged-in user can create a todo item by submitting a title and optional description.",
            rationale="Creating tasks is the primary user goal of the application.",
            acceptance_criteria=[
                "POST /todos with valid title returns 201 and the created item",
                "POST /todos without authentication returns 401",
            ],
            priority="must",
            category=RequirementCategory.functional,
            validation=_passing_validation,
        ),
        UserRequirement(
            id="UR-02",
            statement="A logged-in user can mark one of their todo items as completed.",
            rationale="Tracking task completion is the core value of a todo application.",
            acceptance_criteria=[
                "PATCH /todos/{id} with completed=true returns 200 when user owns the item",
                "PATCH /todos/{id} returns 403 when user does not own the item",
            ],
            priority="must",
            category=RequirementCategory.functional,
            validation=_passing_validation,
        ),
        UserRequirement(
            id="UR-03",
            statement="A logged-in user can view a list of all todo items they have created.",
            rationale="Users need to see their outstanding and completed tasks to manage their workload.",
            acceptance_criteria=[
                "GET /todos returns 200 with only the authenticated user's items",
                "GET /todos without authentication returns 401",
            ],
            priority="must",
            category=RequirementCategory.functional,
            validation=_passing_validation,
        ),
        UserRequirement(
            id="UR-04",
            statement="A logged-in user can delete a todo item they created.",
            rationale="Users need to remove obsolete tasks to keep their list manageable.",
            acceptance_criteria=[
                "DELETE /todos/{id} returns 204 when user owns the item",
                "DELETE /todos/{id} returns 403 when user does not own the item",
            ],
            priority="should",
            category=RequirementCategory.functional,
            validation=_passing_validation,
        ),
    ],
    ambiguities=[
        AmbiguityFlag(
            id="AMB-01",
            field_path="auth.method",
            reason="Authentication is implied but the method (email/password, OAuth, magic link) was not specified.",
            suggested_options=["email/password", "Google OAuth", "GitHub OAuth", "magic link"],
            requirement_id=None,
        ),
    ],
)


def is_mock() -> bool:
    return settings.mock_ai


@lru_cache(maxsize=8)
def _load_prompt(name: str) -> str:
    path = _PROMPT_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def _call_gemini(system_prompt: str, user_content: str, *, stage: str = "gemini") -> dict:
    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(
        model_name=settings.gemini_model,
        generation_config=genai.types.GenerationConfig(
            temperature=0.2,
            response_mime_type="application/json",
        ),
        system_instruction=system_prompt,
    )
    start = time.time()
    try:
        response = model.generate_content(user_content)
        duration_ms = round((time.time() - start) * 1000)
        log.info(
            "gemini.call",
            stage=stage,
            model=settings.gemini_model,
            prompt_chars=len(system_prompt),
            response_chars=len(response.text),
            duration_ms=duration_ms,
        )
        return json.loads(response.text)
    except Exception as exc:
        duration_ms = round((time.time() - start) * 1000)
        log.error("gemini.error", stage=stage, error=str(exc), duration_ms=duration_ms)
        raise


def apply_clarifications(
    current: StructuredRequirements,
    answers: list[ClarificationAnswer],
) -> StructuredRequirements:
    """Update a StructuredRequirements document based on user clarification answers."""
    if is_mock():
        log.info("gemini.mock", stage="apply_clarifications")
        return current.model_copy(update={"version": current.version + 1, "ambiguities": []})

    system_prompt = _load_prompt("apply_clarifications")
    user_content = (
        f"Current requirements:\n{current.model_dump_json(indent=2)}\n\n"
        f"Clarification answers:\n{json.dumps([a.model_dump() for a in answers], indent=2)}"
    )
    raw = _call_gemini(system_prompt, user_content, stage="apply_clarifications")
    try:
        return StructuredRequirements.model_validate(raw)
    except Exception as exc:
        log.error(
            "gemini.schema_error",
            stage="apply_clarifications",
            raw_preview=str(raw)[:500],
            error=str(exc),
        )
        raise ValueError(f"Gemini returned invalid schema: {exc}") from exc


def generate_summary(structured: dict) -> str:
    """Render the user-facing Markdown Requirements Summary Document."""
    if is_mock():
        return "# Mock Requirements Summary\n\nDemo content."
    raise NotImplementedError("Implemented in Week 4")


def generate_blueprint(structured: dict) -> dict:
    """Produce the application blueprint after user approval."""
    if is_mock():
        return {"stack": "python-sqlite", "schema": [], "routes": [], "pages": []}
    raise NotImplementedError("Implemented in Week 4")
