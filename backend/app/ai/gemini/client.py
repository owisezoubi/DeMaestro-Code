"""Gemini client wrapper. Implemented in Week 3."""
import structlog

from app.config import settings

log = structlog.get_logger(__name__)


def is_mock() -> bool:
    return settings.mock_ai


def structure_requirements(raw_input: str) -> dict:
    """Convert raw user input into a structured requirements JSON contract."""
    if is_mock():
        log.info("gemini.mock", stage="structure_requirements")
        return {"app_name": "TodoApp", "stack": "python-sqlite", "entities": []}
    raise NotImplementedError("Implemented in Week 3")


def detect_ambiguity(structured: dict) -> list[dict]:
    """Return a list of clarification questions for unclear/missing fields."""
    if is_mock():
        return []
    raise NotImplementedError("Implemented in Week 3")


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
