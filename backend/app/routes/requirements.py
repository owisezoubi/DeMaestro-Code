"""Requirements ingestion routes (Week 2 — currently a stub)."""
from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from app.auth.dependencies import CurrentUser

router = APIRouter()


@router.post("/{project_id}/requirements/text")
async def submit_text_requirements(
    project_id: str,
    text: str = Form(...),
    user: CurrentUser = None,
):
    """Submit raw requirements as text (FR1). Stub for Week 2."""
    raise HTTPException(status_code=501, detail="Not implemented yet — Week 2")


@router.post("/{project_id}/requirements/pdf")
async def submit_pdf_requirements(
    project_id: str,
    file: UploadFile = File(...),
    user: CurrentUser = None,
):
    """Submit requirements via PDF upload (FR2). Stub for Week 2."""
    raise HTTPException(status_code=501, detail="Not implemented yet — Week 2")


@router.get("/{project_id}/status")
async def project_status(project_id: str, user: CurrentUser):
    """Return current pipeline status for polling. Stub."""
    return {"project_id": project_id, "status": "awaiting_input"}
