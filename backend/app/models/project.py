"""Project-related Pydantic models."""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ProjectStatus(str, Enum):
    """Allowed status transitions:

        new → structuring → clarifying → awaiting_approval
        awaiting_approval → approved → generating → testing → debugging
          → verifying → packaging → ready | ready_with_warnings
        generating / testing / debugging → failed                  (unrecoverable)
        testing / debugging → tests_failed_recoverable             (STRICT mode: test failure)
        failed → awaiting_approval                                 (via reset_generation_state on retry)
        tests_failed_recoverable → testing                         (via /rerun-tests)
        tests_failed_recoverable → generating                      (via /generate, full reset)
        ready | ready_with_warnings → modifying → testing → packaging → ready

    Semantics:
        failed                      — genuinely unrecoverable (bad blueprint, deploy crash,
                                      exception during generation). Requires full regeneration.
        tests_failed_recoverable    — tests failed but generated files are intact; the user
                                      can iterate cheaply via Re-run Tests without paying for
                                      another generation cycle.
    """

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
    modifying = "modifying"
    regenerating = "regenerating"
    deployment_failed = "deployment_failed"
    packaging = "packaging"
    ready = "ready"
    ready_with_warnings = "ready_with_warnings"
    stopped = "stopped"
    failed = "failed"
    tests_failed_recoverable = "tests_failed_recoverable"


class StackChoice(str, Enum):
    python_sqlite = "python-sqlite"
    python_postgres = "python-postgres"
    node_mongo = "node-mongo"


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


class ProjectMeta(BaseModel):
    """Project document stored at users/{uid}/projects/{projectId}."""

    model_config = ConfigDict(extra="ignore")

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
    zip_url: Optional[str] = None
    packaged_at: Optional[str] = None
    deployment_url: Optional[str] = None
    deployment_id: Optional[str] = None
    deployment_status: Optional[str] = None
    deployment_error: Optional[str] = None
    deployment_project_name: Optional[str] = None
    deployment_display_name: Optional[str] = None
    error_message: Optional[str] = None
    last_error: Optional[str] = None
    install_error_log: Optional[str] = None
    test_error_log: Optional[str] = None
    clarification_progress: dict[str, str] = Field(default_factory=dict)
    resolved_topics: list[str] = Field(default_factory=list)
    user_added_requirements: list[str] = Field(default_factory=list)
    last_change_request: Optional[str] = None
    modification_round: Optional[int] = None
    modification_round_completed: Optional[int] = None
    last_modification_summary: Optional[str] = None
    modified_files_last: list[str] = Field(default_factory=list)
    modification_history: list[dict] = Field(default_factory=list)
    current_file: Optional[str] = None
    generated_count: int = 0
    total_files: Optional[int] = None
    current_stage: Optional[str] = None  # None after reset; set to stage name during pipeline
    last_failed_checks: list[str] = Field(default_factory=list)
    # Fields written by the pipeline and reset between retry attempts
    real_failures: list[str] = Field(default_factory=list)
    environmental_skips: list[str] = Field(default_factory=list)
    debug_cycle: int = 0
    generation_started_at: Optional[datetime] = None
    generation_finished_at: Optional[datetime] = None
    test_results_last: Optional[dict] = None
    # Advisory check results (informational; never block ZIP)
    typecheck_warnings: int = 0
    typecheck_advisory_output: Optional[str] = None
    contract_advisory_misses: list[str] = Field(default_factory=list)
    # Checklist — raw items emitted by architect + per-item check results
    checklist: list = Field(default_factory=list)
    checklist_results: list = Field(default_factory=list)
