"""Project-related Pydantic models."""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ProjectStatus(str, Enum):
    awaiting_input = "awaiting_input"
    structuring = "structuring"
    clarifying = "clarifying"
    awaiting_approval = "awaiting_approval"
    approved = "approved"
    blueprinting = "blueprinting"
    generating = "generating"
    generated = "generated"
    testing = "testing"
    tested = "tested"
    verifying = "verifying"
    verified = "verified"
    deploying = "deploying"
    deployed = "deployed"
    packaging = "packaging"
    ready = "ready"
    failed = "failed"


class StackChoice(str, Enum):
    python_sqlite = "python-sqlite"
    python_postgres = "python-postgres"
    node_mongo = "node-mongo"


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


class ProjectMeta(BaseModel):
    """Project document stored at users/{uid}/projects/{projectId}."""
    id: str
    name: str
    status: ProjectStatus = ProjectStatus.awaiting_input
    stack_choice: Optional[StackChoice] = None
    clarification_round: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    summary: Optional[str] = None
    blueprint: Optional[dict] = None
    approved_at: Optional[datetime] = None
    approval_notes: Optional[str] = None
    generated_files: Optional[dict] = None  # { file_path: content }
    generation_plan: Optional[dict] = None  # GenerationPlan as dict
    deployment_url: Optional[str] = None
    deployment_id: Optional[str] = None
    error_message: Optional[str] = None
