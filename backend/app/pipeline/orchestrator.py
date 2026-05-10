"""Pipeline orchestrator for DeMaestro (W3-B).

Each pipeline stage runs in a daemon thread so the HTTP request returns
immediately and Firestore status is updated as work progresses.
"""
import threading

import structlog

from app.ai.gemini import client as gemini
from app.models.project import ProjectStatus
from app.models.raw_input import RawInputType
from app.models.structured_requirements import ClarificationAnswer
from app.services import firestore_service

log = structlog.get_logger(__name__)


# ---------- Structuring ----------

def _run_structuring(uid: str, project_id: str) -> None:
    try:
        firestore_service.set_project_status(uid, project_id, ProjectStatus.structuring)

        raw_inputs = firestore_service.list_raw_inputs(uid, project_id)
        parts = []
        for ri in raw_inputs:
            if ri.type == RawInputType.text and ri.content:
                parts.append(ri.content)
            elif ri.type == RawInputType.pdf and ri.extracted_text:
                parts.append(ri.extracted_text)
        combined = "\n\n".join(parts)

        if not combined.strip():
            log.error("pipeline.structuring.empty_input", uid=uid, project_id=project_id)
            firestore_service.set_project_status(uid, project_id, ProjectStatus.failed)
            return

        sr = gemini.structure_requirements(combined)
        firestore_service.add_structured_requirements(uid, project_id, sr)

        next_status = (
            ProjectStatus.awaiting_approval if not sr.ambiguities else ProjectStatus.clarifying
        )
        firestore_service.set_project_status(uid, project_id, next_status)

    except Exception as exc:
        log.error(
            "pipeline.structuring.error",
            uid=uid,
            project_id=project_id,
            error=str(exc),
        )
        firestore_service.set_project_status(uid, project_id, ProjectStatus.failed)


def kick_off_structuring(uid: str, project_id: str) -> None:
    """Start the structuring stage in a background daemon thread."""
    threading.Thread(target=_run_structuring, args=(uid, project_id), daemon=True).start()


# ---------- Clarification ----------

def _run_apply_clarification(
    uid: str,
    project_id: str,
    ambiguity_id: str,
    question: str,
    answer: str,
) -> None:
    try:
        current = firestore_service.get_latest_structured_requirements(uid, project_id)
        if current is None:
            log.error(
                "pipeline.clarification.no_sr",
                uid=uid,
                project_id=project_id,
            )
            return

        clarification = ClarificationAnswer(question_id=ambiguity_id, answer=answer)
        updated = gemini.apply_clarifications(current, [clarification])
        firestore_service.add_structured_requirements(uid, project_id, updated)
        firestore_service.add_clarification_turn(uid, project_id, ambiguity_id, question, answer)

        next_status = (
            ProjectStatus.awaiting_approval if not updated.ambiguities else ProjectStatus.clarifying
        )
        firestore_service.set_project_status(uid, project_id, next_status)

    except Exception as exc:
        log.error(
            "pipeline.clarification.error",
            uid=uid,
            project_id=project_id,
            error=str(exc),
        )
        firestore_service.set_project_status(uid, project_id, ProjectStatus.failed)


def apply_clarification_in_background(
    uid: str,
    project_id: str,
    ambiguity_id: str,
    question: str,
    answer: str,
) -> None:
    """Apply one clarification answer in a background daemon thread."""
    threading.Thread(
        target=_run_apply_clarification,
        args=(uid, project_id, ambiguity_id, question, answer),
        daemon=True,
    ).start()
