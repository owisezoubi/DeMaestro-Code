"""Project CRUD routes (skeleton)."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status

from app.auth.dependencies import CurrentUser
from app.models.project import ProjectCreate, ProjectMeta, ProjectStatus
from app.services import firestore_service as fs

router = APIRouter()


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


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str, user: CurrentUser):
    fs.delete_project(user.uid, project_id)
    return None
