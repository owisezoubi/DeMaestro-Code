"""Structuring and clarification endpoints (W3-B)."""
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth.dependencies import CurrentUser
from app.models.project import ProjectStatus
from app.models.structured_requirements import AmbiguityFlag, StructuredRequirements
from app.pipeline import orchestrator as pipeline
from app.services import firestore_service

router = APIRouter()


def _require_project(uid: str, project_id: str):
    project = firestore_service.get_project(uid, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


class AnswerPayload(BaseModel):
    answer: str = Field(..., min_length=1)


@router.post(
    "/{project_id}/structure",
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_structuring(project_id: str, user: CurrentUser):
    """Kick off the structuring pipeline stage (async)."""
    project = _require_project(user.uid, project_id)

    if project.status not in (ProjectStatus.awaiting_input, ProjectStatus.failed):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project is in status '{project.status}' — cannot start structuring",
        )

    raw_inputs = firestore_service.list_raw_inputs(user.uid, project_id)
    if not raw_inputs:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No requirements submitted yet",
        )

    pipeline.kick_off_structuring(user.uid, project_id)
    return {"project_id": project_id, "status": "structuring"}


@router.get(
    "/{project_id}/structured",
    response_model=StructuredRequirements,
)
async def get_structured(project_id: str, user: CurrentUser):
    """Return the latest StructuredRequirements document."""
    _require_project(user.uid, project_id)

    sr = firestore_service.get_latest_structured_requirements(user.uid, project_id)
    if sr is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No structured requirements yet",
        )
    return sr


@router.get(
    "/{project_id}/clarifications",
    response_model=list[AmbiguityFlag],
)
async def get_clarifications(project_id: str, user: CurrentUser):
    """Return the open ambiguity flags from the latest StructuredRequirements."""
    _require_project(user.uid, project_id)

    sr = firestore_service.get_latest_structured_requirements(user.uid, project_id)
    if sr is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No structured requirements yet",
        )
    return sr.ambiguities


@router.post(
    "/{project_id}/clarifications/{ambiguity_id}/answer",
)
async def answer_clarification(
    project_id: str,
    ambiguity_id: str,
    payload: AnswerPayload,
    user: CurrentUser,
):
    """Submit an answer to one ambiguity flag with a 5-minute safety-net timeout."""
    project = _require_project(user.uid, project_id)

    if project.status != ProjectStatus.clarifying:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project is in status '{project.status}' — must be 'clarifying' to answer",
        )

    sr = firestore_service.get_latest_structured_requirements(user.uid, project_id)
    if sr is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No structured requirements yet",
        )

    flag = next((a for a in sr.ambiguities if a.id == ambiguity_id), None)
    if flag is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ambiguity '{ambiguity_id}' not found",
        )

    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                pipeline._run_apply_clarification,
                user.uid, project_id, ambiguity_id, flag.reason, payload.answer,
            ),
            timeout=300,
        )
    except asyncio.TimeoutError:
        firestore_service.update_project(
            user.uid, project_id, {"last_error": "Clarification processing timeout"}
        )
        return JSONResponse(
            status_code=504,
            content={
                "detail": "AI processing taking longer than expected — your answer may still be applied",
                "status": "still_processing",
            },
        )

    return {"project_id": project_id, "status": "applied_clarification"}


# ---------- Batch clarifications ----------

class ProgressPayload(BaseModel):
    answers: dict[str, str]


@router.get("/{project_id}/clarifications/progress")
async def get_clarification_progress(project_id: str, user: CurrentUser):
    project = _require_project(user.uid, project_id)
    return {"answers": project.clarification_progress or {}}


@router.put("/{project_id}/clarifications/progress")
async def save_clarification_progress(
    project_id: str, payload: ProgressPayload, user: CurrentUser,
):
    _require_project(user.uid, project_id)
    cleaned = {k: v for k, v in (payload.answers or {}).items() if v and v.strip()}
    firestore_service.update_project(user.uid, project_id, {
        "clarification_progress": cleaned,
    })
    return {"saved": len(cleaned)}


# ---------- Batch clarifications ----------

class BatchAnswerItem(BaseModel):
    ambiguity_id: str
    answer: str = Field(..., min_length=1)


class BatchAnswersPayload(BaseModel):
    answers: list[BatchAnswerItem] = Field(..., min_length=1)


@router.post("/{project_id}/clarifications/answers")
async def answer_clarifications_batch(
    project_id: str, payload: BatchAnswersPayload, user: CurrentUser
):
    """Submit a full round of answers in one batch and refine once."""
    project = _require_project(user.uid, project_id)
    if project.status != ProjectStatus.clarifying:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project is in status '{project.status}' — must be 'clarifying'",
        )
    sr = firestore_service.get_latest_structured_requirements(user.uid, project_id)
    if sr is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No structured requirements yet"
        )
    answers = [a.model_dump() for a in payload.answers]
    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                pipeline._run_apply_clarifications_batch, user.uid, project_id, answers
            ),
            timeout=600,
        )
    except asyncio.TimeoutError:
        firestore_service.update_project(
            user.uid, project_id, {"last_error": "Batch clarification timeout"}
        )
        return JSONResponse(
            status_code=504,
            content={
                "detail": "AI still refining — answers may still apply",
                "status": "still_processing",
            },
        )
    return {"project_id": project_id, "status": "applied_batch"}


# ---------- Revision ----------

class QAItem(BaseModel):
    question: str = ""
    answer: str = Field(..., min_length=1)
    ambiguity_id: Optional[str] = None


class ReviseRequest(BaseModel):
    answers: list[QAItem] = []
    new_requirements: list[str] = []


_REVISABLE = {
    ProjectStatus.awaiting_approval,
    ProjectStatus.clarifying,
    ProjectStatus.ready,
    ProjectStatus.ready_with_warnings,
    ProjectStatus.failed,
}


@router.get("/{project_id}/clarification-history")
async def clarification_history(project_id: str, user: CurrentUser):
    """Return all clarification Q&A turns (oldest first) for display in the editor."""
    _require_project(user.uid, project_id)
    return firestore_service.list_clarification_turns(user.uid, project_id)


@router.post("/{project_id}/requirements/revise", status_code=status.HTTP_202_ACCEPTED)
async def revise_requirements(project_id: str, payload: ReviseRequest, user: CurrentUser):
    """Re-run requirements analysis with edited answers + new requirements."""
    project = _require_project(user.uid, project_id)
    if project.status not in _REVISABLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot edit requirements while project is '{project.status.value}'",
        )

    cleaned_new = [r.strip() for r in payload.new_requirements if r and r.strip()]
    answers = [a.model_dump() for a in payload.answers if a.answer.strip()]
    if not answers and not cleaned_new:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nothing to revise — provide at least one answer or a new requirement",
        )

    existing_added = project.user_added_requirements or []
    merged_added = existing_added + [r for r in cleaned_new if r not in existing_added]

    firestore_service.update_project(user.uid, project_id, {
        "status": ProjectStatus.structuring,
        "summary": None,
        "blueprint": None,
        "approved_at": None,
        "clarification_round": 0,
        "last_error": None,
        "clarification_progress": {},
        "user_added_requirements": merged_added,
    })
    pipeline.kick_off_revision(user.uid, project_id, answers, cleaned_new)
    return {"project_id": project_id, "status": "structuring"}
