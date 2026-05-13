"""Gemini AI client for DeMaestro — shared fixtures and future stubs."""
from app.models.structured_requirements import (
    AmbiguityFlag,
    Entity,
    FundamentalStatus,
    RequirementCategory,
    RequirementValidation,
    StructuredRequirements,
    UserRequirement,
)

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


def generate_summary(structured: dict) -> str:
    """Render the user-facing Markdown Requirements Summary Document."""
    raise NotImplementedError("Implemented in Week 4")


def generate_blueprint(structured: dict) -> dict:
    """Produce the application blueprint after user approval."""
    raise NotImplementedError("Implemented in Week 4")
