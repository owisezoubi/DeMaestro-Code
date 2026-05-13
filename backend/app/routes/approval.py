"""Approval endpoint — generates summary + blueprint and marks project approved."""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status

from app.ai.gemini.agents.coordinator import RequirementsCoordinator
from app.auth.dependencies import CurrentUser
from app.models.project import ProjectStatus
from app.services import firestore_service

router = APIRouter()


@router.post("/{project_id}/approve")
async def approve_project(project_id: str, user: CurrentUser) -> dict:
    """Approve a project in awaiting_approval status and generate summary + blueprint."""
    project = firestore_service.get_project(user.uid, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    if project.status != ProjectStatus.awaiting_approval:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot approve project in status '{project.status.value}'",
        )

    sr = firestore_service.get_latest_structured_requirements(user.uid, project_id)
    if sr is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No structured requirements found for this project",
        )

    coordinator = RequirementsCoordinator()
    summary = coordinator.generate_summary(sr)
    blueprint_obj = coordinator.generate_blueprint(sr)

    approved_at = datetime.now(timezone.utc)
    firestore_service.update_project(
        user.uid,
        project_id,
        {
            "status": ProjectStatus.approved,
            "summary": summary,
            "blueprint": blueprint_obj.model_dump(),
            "approved_at": approved_at,
        },
    )

    return {
        "project_id": project_id,
        "summary": summary,
        "blueprint": blueprint_obj.model_dump(),
        "approved_at": approved_at.isoformat(),
    }
