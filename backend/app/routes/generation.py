"""Generation pipeline routes (Weeks 4–6 — stubs)."""
from fastapi import APIRouter, HTTPException

from app.auth.dependencies import CurrentUser

router = APIRouter()


@router.post("/{project_id}/approve")
async def approve_summary(project_id: str, user: CurrentUser):
    """User approval gate for the Requirements Summary Document (FR9). Stub."""
    raise HTTPException(status_code=501, detail="Not implemented yet — Week 4")


@router.post("/{project_id}/generate")
async def trigger_generation(project_id: str, user: CurrentUser):
    """Kick off blueprint + code generation in the background. Stub."""
    raise HTTPException(status_code=501, detail="Not implemented yet — Week 5")


@router.get("/{project_id}/download")
async def download_zip(project_id: str, user: CurrentUser):
    """Return a signed URL to the generated ZIP (FR13). Stub."""
    raise HTTPException(status_code=501, detail="Not implemented yet — Week 6")
