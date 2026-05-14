"""Pydantic models for the code generation plan."""
from pydantic import BaseModel


class FileToGenerate(BaseModel):
    path: str
    description: str
    depends_on: list[str] = []
    template: str  # "react_page", "react_component", "fastapi_route", "db_schema", "service", "none"


class GenerationPlan(BaseModel):
    technology_stack: str  # "python-postgres", "python-sqlite", "node-mongo"
    files: list[FileToGenerate]
    generation_order: list[str]
    notes: str
