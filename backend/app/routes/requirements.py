"""Requirements ingestion routes (FR1, FR2)."""
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser
from app.models.project import ProjectStatus
from app.models.raw_input import RawInputCreate, RawInputDoc, RawInputType
from app.services import firestore_service, pdf_service, storage_service

router = APIRouter()

_MAX_PDF_BYTES = 10_485_760  # 10 MB


def _new_input_id() -> str:
    return uuid4().hex[:12]


def _require_project(uid: str, project_id: str):
    project = firestore_service.get_project(uid, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


# ---------- Response model for /status ----------

class ProjectStatusResponse(BaseModel):
    project_id: str
    status: ProjectStatus
    updated_at: Optional[datetime]
    raw_input_count: int
    ambiguity_count: int
    latest_version: Optional[int]


# ---------- Routes ----------

@router.post(
    "/{project_id}/requirements/text",
    response_model=RawInputDoc,
    status_code=status.HTTP_201_CREATED,
)
async def submit_text_requirements(
    project_id: str,
    payload: RawInputCreate,
    user: CurrentUser,
):
    """Submit raw requirements as plain text (FR1)."""
    _require_project(user.uid, project_id)

    input_id = _new_input_id()
    doc = RawInputDoc(
        id=input_id,
        type=RawInputType.text,
        content=payload.content,
        char_count=len(payload.content),
        timestamp=datetime.now(timezone.utc),
    )
    firestore_service.add_raw_input(user.uid, project_id, doc)
    return doc


@router.post(
    "/{project_id}/requirements/pdf",
    response_model=RawInputDoc,
    status_code=status.HTTP_201_CREATED,
)
async def submit_pdf_requirements(
    project_id: str,
    file: UploadFile = File(...),
    user: CurrentUser = None,
):
    """Submit requirements via PDF upload (FR2)."""
    _require_project(user.uid, project_id)

    pdf_bytes = await file.read()

    if len(pdf_bytes) > _MAX_PDF_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"PDF exceeds the 10 MB limit ({len(pdf_bytes)} bytes received)",
        )

    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Expected application/pdf, got {file.content_type!r}",
        )

    input_id = _new_input_id()
    storage_ref = storage_service.upload_pdf(user.uid, project_id, input_id, pdf_bytes)

    try:
        extracted_text = pdf_service.extract_text(pdf_bytes)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    doc = RawInputDoc(
        id=input_id,
        type=RawInputType.pdf,
        storage_ref=storage_ref,
        extracted_text=extracted_text,
        char_count=len(extracted_text),
        timestamp=datetime.now(timezone.utc),
    )
    firestore_service.add_raw_input(user.uid, project_id, doc)
    return doc


@router.get(
    "/{project_id}/requirements",
    response_model=list[RawInputDoc],
)
async def list_requirements(
    project_id: str,
    user: CurrentUser,
):
    """List all raw inputs for a project, newest first."""
    _require_project(user.uid, project_id)
    return firestore_service.list_raw_inputs(user.uid, project_id)


@router.get(
    "/{project_id}/status",
    response_model=ProjectStatusResponse,
)
async def project_status(
    project_id: str,
    user: CurrentUser,
):
    """Return current pipeline status and raw input count for polling."""
    project = _require_project(user.uid, project_id)
    raw_inputs = firestore_service.list_raw_inputs(user.uid, project_id)
    latest_sr = firestore_service.get_latest_structured_requirements(user.uid, project_id)
    return ProjectStatusResponse(
        project_id=project_id,
        status=project.status,
        updated_at=project.updated_at,
        raw_input_count=len(raw_inputs),
        ambiguity_count=len(latest_sr.ambiguities) if latest_sr else 0,
        latest_version=latest_sr.version if latest_sr else None,
    )
