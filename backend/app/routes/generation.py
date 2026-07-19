"""Generation pipeline routes — W5-P2."""
import io
import threading
import zipfile

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.auth.dependencies import CurrentUser
from app.models.project import ProjectStatus
from app.pipeline.generation_orchestrator import GenerationOrchestrator
from app.services import firestore_service

router = APIRouter()
log = structlog.get_logger(__name__)

# Statuses that mean generation is actively running — double-triggering would corrupt state.
_IN_PROGRESS_STATUSES = {
    ProjectStatus.generating,
    ProjectStatus.testing,
    ProjectStatus.tested,
    ProjectStatus.verifying,
    ProjectStatus.verified,
    ProjectStatus.packaging,
    ProjectStatus.blueprinting,
    ProjectStatus.regenerating,
}

# Statuses from which a full generation run can be started/resumed.
_GENERATE_FROM_STATUSES = {
    ProjectStatus.approved,                 # first-time run after approval
    ProjectStatus.failed,                   # retry after unrecoverable failure
    ProjectStatus.tests_failed_recoverable, # full regen instead of re-running tests
    ProjectStatus.stopped,                  # resume after user stopped the run
}


@router.post("/{project_id}/generate")
async def trigger_generation(project_id: str, user: CurrentUser) -> dict:
    """Start the full generation pipeline in a background thread.

    Accepts status 'approved' (first-time run), 'failed' (retry after an
    unrecoverable failure), or 'tests_failed_recoverable' (full regeneration
    from scratch chosen over re-running tests).  Resets all generation
    artifacts and starts fresh; requirements and blueprint are preserved.
    """
    project = firestore_service.get_project(user.uid, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status in _IN_PROGRESS_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="Generation is already in progress for this project.",
        )

    if project.status not in _GENERATE_FROM_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot generate from status '{project.status.value}'",
        )

    # Single-active-run lock: block if another project is already running.
    active = firestore_service.list_projects_in_statuses(user.uid, _IN_PROGRESS_STATUSES)
    active_others = [p for p in active if p.id != project_id]
    if active_others:
        blocking = active_others[0]
        raise HTTPException(
            status_code=409,
            detail={
                "code": "another_generation_running",
                "blocking_project_id": blocking.id,
                "blocking_project_name": blocking.name,
                "blocking_status": blocking.status.value,
                "message": (
                    f"Generation is already running for '{blocking.name}'. "
                    "Wait for it to finish before starting a new one."
                ),
            },
        )

    if project.status in {ProjectStatus.failed, ProjectStatus.tests_failed_recoverable}:
        log.info(
            "generation.retry_from_failed",
            project_id=project_id,
            previous_status=project.status.value,
            previous_real_failures=project.last_failed_checks or [],
        )
        firestore_service.reset_generation_state(user.uid, project_id)

    firestore_service.set_project_status(user.uid, project_id, ProjectStatus.generating)

    orchestrator = GenerationOrchestrator()
    thread = threading.Thread(
        target=orchestrator.run_full_pipeline,
        args=(user.uid, project_id),
        daemon=True,
    )
    thread.start()

    return {"status": "generating", "project_id": project_id}


@router.post("/{project_id}/rerun-tests")
async def rerun_tests(
    project_id: str,
    user: CurrentUser,
    use_llm_debug: bool = True,
) -> dict:
    """Re-run the test+debug phase using the project's existing generated files.

    No generation step — no architect or generator calls.  The generated file
    tree persisted in Firestore is loaded as-is and fed directly to the test
    runner.

    use_llm_debug=false makes the debug cycles use only algorithmic fixers
    (no Claude calls, effectively free).  use_llm_debug=true (default) allows
    Claude to fix files that the algorithmic pass couldn't handle (~$1–2 for
    the debugger alone).
    """
    project = firestore_service.get_project(user.uid, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status in _IN_PROGRESS_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="A generation or test run is already in progress.",
        )

    _allowed_from = {
        ProjectStatus.tests_failed_recoverable,
        ProjectStatus.ready,
        ProjectStatus.ready_with_warnings,
    }
    if project.status not in _allowed_from:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot re-run tests from status '{project.status.value}'. "
                "Use /generate to start a full regeneration, or wait until the "
                "current run finishes."
            ),
        )

    if not project.generated_files:
        raise HTTPException(
            status_code=422,
            detail="No generated files found — run a full generation first.",
        )

    # Reset test-related state; preserve all generation artefacts.
    firestore_service.update_project(user.uid, project_id, {
        "status": ProjectStatus.testing.value,
        "current_stage": "testing",
        "last_failed_checks": [],
        "real_failures": [],
        "debug_cycle": 0,
        "last_error": None,
        "test_error_log": None,
    })

    log.info(
        "generation.rerun_tests.triggered",
        project_id=project_id,
        use_llm_debug=use_llm_debug,
        num_files=len(project.generated_files),
    )

    orchestrator = GenerationOrchestrator()
    thread = threading.Thread(
        target=orchestrator.run_test_debug_only,
        args=(user.uid, project_id),
        kwargs={"use_llm_debug": use_llm_debug},
        daemon=True,
    )
    thread.start()

    return {"status": "test_started", "use_llm_debug": use_llm_debug, "project_id": project_id}


@router.get("/{project_id}/generation-status")
async def get_generation_status(project_id: str, user: CurrentUser) -> dict:
    """Poll for current generation / deployment status with progress data."""
    project = firestore_service.get_project(user.uid, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    generated_count = len(project.generated_files or {})
    total_files = (
        len(project.generation_plan.get("files", []))
        if project.generation_plan
        else 0
    )

    # Load the latest structured requirements (for the blueprint panel).
    sr = firestore_service.get_latest_structured_requirements(user.uid, project_id)
    sr_payload = None
    if sr is not None:
        try:
            sr_payload = {
                "app_name": sr.app_name,
                "user_requirements": [
                    {
                        "id": getattr(r, "id", None),
                        "statement": r.statement,
                        "priority": r.priority,
                        "acceptance_criteria": r.acceptance_criteria or [],
                    }
                    for r in (sr.user_requirements or [])
                ],
                "entities": [
                    {
                        "name": e.name,
                        "description": e.description,
                        "fields": e.fields or [],
                    }
                    for e in (sr.entities or [])
                ],
            }
        except Exception:
            sr_payload = None

    # Fetch the original text the user typed on /projects/new (or extracted PDF text).
    initial_request = None
    try:
        raw_inputs = firestore_service.list_raw_inputs(user.uid, project_id)
        if raw_inputs:
            first = raw_inputs[0]
            initial_request = first.content or first.extracted_text
    except Exception:
        initial_request = None

    return {
        "project_id": project_id,
        "status": project.status.value,
        "name": project.name,
        "summary": project.summary,
        "blueprint": project.blueprint,
        "structured_requirements": sr_payload,
        "generated_count": project.generated_count or generated_count,
        "total_files": project.total_files or total_files,
        "current_file": project.current_file,
        "current_stage": project.current_stage,
        "zip_url": project.zip_url,
        "last_error": project.last_error,
        "error_message": project.error_message,
        "test_error_log": project.test_error_log,
        # Failed-test recovery fields — used by the Re-run Tests UI
        "real_failures": project.real_failures or [],
        "last_failed_checks": project.last_failed_checks or [],
        # Advisory checks — informational only, never block ZIP
        "typecheck_warnings": project.typecheck_warnings or 0,
        "typecheck_advisory_output": project.typecheck_advisory_output,
        "contract_advisory_misses": project.contract_advisory_misses or [],
        # Checklist coverage
        "checklist": project.checklist or [],
        "checklist_results": project.checklist_results or [],
        # File explorer — always returned; empty until generation completes.
        "generated_files": project.generated_files or {},
        # Original user input
        "initial_request": initial_request,
    }


@router.post("/{project_id}/stop")
async def stop_generation(project_id: str, user: CurrentUser) -> dict:
    """Request cancellation of an in-progress generation pipeline."""
    from app.pipeline.generation_orchestrator import is_active, request_cancellation
    project = firestore_service.get_project(user.uid, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not is_active(project_id):
        return {"status": "not_active", "message": "No active generation to stop"}
    request_cancellation(project_id)
    return {"status": "cancellation_requested", "message": "Generation will stop at next safe point"}


@router.post("/{project_id}/restart-from-scratch")
async def restart_from_scratch(project_id: str, user: CurrentUser) -> dict:
    """Reset all generation state and restart the full pipeline from scratch."""
    from app.pipeline.generation_orchestrator import is_active, clear_cancellation
    project = firestore_service.get_project(user.uid, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if is_active(project_id):
        raise HTTPException(
            status_code=409,
            detail="Cannot restart while generation is running. Stop the current run first.",
        )
    # Broadened — a user can start over from ANY terminal state, not just
    # failure states. This includes shipped projects (`ready`, `deployed`)
    # where the user wants a fresh code generation.
    _RESTARTABLE_STATUSES = {
        ProjectStatus.stopped,
        ProjectStatus.failed,
        ProjectStatus.tests_failed_recoverable,
        ProjectStatus.ready,
        ProjectStatus.ready_with_warnings,
        ProjectStatus.deployed,
        ProjectStatus.modifying,
        ProjectStatus.regenerating,
    }
    if project.status not in _RESTARTABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot restart from status '{project.status.value}'. "
                   f"Wait for the current phase to finish first.",
        )
    clear_cancellation(project_id)
    firestore_service.reset_generation_state(user.uid, project_id)
    firestore_service.set_project_status(user.uid, project_id, ProjectStatus.generating)

    orchestrator = GenerationOrchestrator()
    import threading as _threading
    thread = _threading.Thread(
        target=orchestrator.run_full_pipeline,
        args=(user.uid, project_id),
        daemon=True,
    )
    thread.start()
    return {"status": "restarted", "message": "Generation started from scratch"}


@router.get("/{project_id}/download")
async def download_zip(project_id: str, user: CurrentUser):
    """Download generated code as a ZIP archive."""
    project = firestore_service.get_project(user.uid, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.generated_files:
        raise HTTPException(status_code=404, detail="No generated files available")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in project.generated_files.items():
            zf.writestr(path, content)
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={project_id}.zip"},
    )
