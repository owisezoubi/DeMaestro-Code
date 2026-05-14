"""Project CRUD routes (skeleton)."""
import threading
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Body, HTTPException, status

from app.auth.dependencies import CurrentUser
from app.models.project import ProjectCreate, ProjectMeta, ProjectStatus
from app.services import firestore_service as fs

log = structlog.get_logger(__name__)

router = APIRouter()


def _regenerate_with_changes(uid: str, project_id: str, change_request: str) -> None:
    """Re-run the generation pipeline with change context (background thread)."""
    try:
        project = fs.get_project(uid, project_id)
        if project is None:
            raise ValueError(f"Project {project_id} not found")

        plan_dict = project.generation_plan
        if plan_dict is None:
            raise ValueError("No generation plan found for this project")

        blueprint_dict = project.blueprint
        if blueprint_dict is None:
            raise ValueError("No blueprint found for this project")

        old_files = project.generated_files or {}

        fs.update_project(uid, project_id, {"status": ProjectStatus.regenerating})

        from app.ai.claude.agents.debugger import DebuggerAgent
        from app.ai.claude.agents.deployer import DeployerAgent
        from app.ai.claude.agents.verifier import VerifierAgent
        from app.ai.gemini.agents.blueprint import BlueprintResponse
        from app.models.generation_plan import GenerationPlan

        plan = GenerationPlan(**plan_dict)
        blueprint = BlueprintResponse(**blueprint_dict)

        # Feed the change request to DebuggerAgent as a synthetic error so it
        # applies targeted edits without regenerating everything from scratch.
        test_results = {
            "status": "change_requested",
            "errors": [f"User requested: {change_request}"],
            "passed_checks": {},
        }

        debug_result = DebuggerAgent().debug_and_fix(test_results, old_files, plan, attempt_count={})

        if debug_result["status"] != "fixed":
            raise RuntimeError(f"Could not generate changes: {debug_result['errors']}")

        updated_files = {**old_files, **debug_result["fixed_files"]}

        verify_result = VerifierAgent().verify(updated_files, plan, blueprint)
        if verify_result["status"] != "pass":
            raise RuntimeError(f"Verification failed: {verify_result['issues']}")

        deploy_result = DeployerAgent().deploy(uid, project_id, updated_files, plan)
        if deploy_result["status"] != "success":
            raise RuntimeError(f"Deployment failed: {deploy_result['errors']}")

        modification_round = project.modification_round or 1
        fs.update_project(uid, project_id, {
            "status": ProjectStatus.deployed,
            "generated_files": updated_files,
            "deployment_url": deploy_result["deployment_url"],
            "modification_round_completed": modification_round,
            "error_message": None,
        })
        log.info("regenerate_with_changes.done", project_id=project_id, round=modification_round)

    except Exception as exc:
        log.error("regenerate_with_changes.error", project_id=project_id, error=str(exc))
        fs.update_project(uid, project_id, {
            "status": ProjectStatus.deployment_failed,
            "error_message": str(exc),
        })


@router.get("", response_model=list[ProjectMeta])
async def list_projects(user: CurrentUser):
    """List all projects belonging to the current user."""
    return fs.list_projects(user.uid)


@router.post("", response_model=ProjectMeta, status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectCreate, user: CurrentUser):
    """Create a new project."""
    project_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)
    meta = ProjectMeta(
        id=project_id,
        name=payload.name,
        status=ProjectStatus.awaiting_input,
        created_at=now,
        updated_at=now,
    )
    fs.create_project(user.uid, meta)
    return meta


@router.get("/{project_id}", response_model=ProjectMeta)
async def get_project(project_id: str, user: CurrentUser):
    project = fs.get_project(user.uid, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.delete("/{project_id}")
async def delete_project(project_id: str, user: CurrentUser) -> dict:
    """Delete a project and all associated data."""
    project = fs.get_project(user.uid, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    fs.delete_project(user.uid, project_id)
    return {"message": "Project deleted successfully", "project_id": project_id}


@router.post("/{project_id}/request-changes")
async def request_changes(
    project_id: str,
    user: CurrentUser,
    request_body: dict = Body(...),
) -> dict:
    """Request changes to a deployed app. Starts background regeneration."""
    project = fs.get_project(user.uid, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status != ProjectStatus.deployed:
        raise HTTPException(status_code=400, detail=f"Cannot request changes from status {project.status}")

    change_request = request_body.get("change_request")
    if not change_request:
        raise HTTPException(status_code=400, detail="change_request is required")

    modification_round = (project.modification_round or 0) + 1
    fs.update_project(user.uid, project_id, {
        "status": ProjectStatus.modifying,
        "last_change_request": change_request,
        "modification_round": modification_round,
    })

    threading.Thread(
        target=_regenerate_with_changes,
        args=(user.uid, project_id, change_request),
        daemon=True,
    ).start()

    return {"message": "Changes requested. Regenerating...", "project_id": project_id}
