"""Generation pipeline routes — W5-P2."""
import threading

from fastapi import APIRouter, HTTPException

from app.auth.dependencies import CurrentUser
from app.models.project import ProjectStatus
from app.pipeline.generation_orchestrator import GenerationOrchestrator
from app.services import firestore_service

router = APIRouter()


@router.post("/{project_id}/generate")
async def trigger_generation(project_id: str, user: CurrentUser) -> dict:
    """Start full generation pipeline (background thread). Requires status 'approved'."""
    project = firestore_service.get_project(user.uid, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status != ProjectStatus.approved:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot generate from status '{project.status.value}'",
        )

    firestore_service.set_project_status(user.uid, project_id, ProjectStatus.generating)

    orchestrator = GenerationOrchestrator()
    thread = threading.Thread(
        target=orchestrator.run_full_pipeline,
        args=(user.uid, project_id),
        daemon=True,
    )
    thread.start()

    return {"status": "generating", "project_id": project_id}


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

    return {
        "project_id": project_id,
        "status": project.status.value,
        "generated_count": generated_count,
        "total_files": total_files,
        "deployment_url": project.deployment_url,
        "deployment_id": project.deployment_id,
        "error_message": project.error_message,
    }


@router.get("/{project_id}/download")
async def download_zip(project_id: str, user: CurrentUser):
    """Return a signed URL to the generated ZIP (FR13). Stub."""
    raise HTTPException(status_code=501, detail="Not implemented yet — Week 6")
