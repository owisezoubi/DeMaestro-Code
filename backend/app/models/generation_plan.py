"""Pydantic models for the code generation plan."""
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


def _normalize_to_str(v: Any) -> str:
    """Coerce any Pydantic input to a string. Used as a before-validator."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (dict, list)):
        try:
            return json.dumps(v, indent=2)
        except Exception:
            return str(v)
    return str(v)


class FileToGenerate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path: str
    description: str
    depends_on: list[str] = []
    template: str  # "react_page", "react_component", "fastapi_route", "db_schema", "service", "none"

    @field_validator("path", "description", "template", mode="before")
    @classmethod
    def coerce_str(cls, v: Any) -> str:
        return _normalize_to_str(v)


class GenerationPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    technology_stack: str  # "python-postgres", "python-sqlite", "node-mongo"
    files: list[FileToGenerate]
    generation_order: list[str]
    notes: str
    extra_dependencies: list[str] = []
    extra_frontend_dependencies: list[str] = []
    # Populated by architect when the app is a single-page site (portfolio, profile, etc.).
    # Each entry: {"path": "/api/<slug>/data", "returns": ["entity1", ...], "public": true}
    aggregate_endpoints: list[dict] = []

    @field_validator("technology_stack", "notes", mode="before")
    @classmethod
    def coerce_str(cls, v: Any) -> str:
        return _normalize_to_str(v)
