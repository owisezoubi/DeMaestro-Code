"""Approval endpoint — marks a project as approved once summary + blueprint are ready."""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status

from app.auth.dependencies import CurrentUser
from app.models.project import ProjectStatus
from app.services import firestore_service

router = APIRouter()


@router.post("/{project_id}/approve")
async def approve_project(project_id: str, user: CurrentUser) -> dict:
    """Approve a project.

    Requires the project to be in awaiting_approval status and to already have
    a summary + blueprint (generated in the background after clarifications finish).
    """
    project = firestore_service.get_project(user.uid, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    if project.status != ProjectStatus.awaiting_approval:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot approve project in status '{project.status.value}'",
        )

    if not project.summary or not project.blueprint:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Summary and blueprint are still being generated. Please wait a moment.",
        )

    approved_at = datetime.now(timezone.utc)
    firestore_service.update_project(
        user.uid,
        project_id,
        {
            "status": ProjectStatus.approved,
            "approved_at": approved_at,
        },
    )

    return {
        "project_id": project_id,
        "status": ProjectStatus.approved,
        "approved_at": approved_at.isoformat(),
    }
