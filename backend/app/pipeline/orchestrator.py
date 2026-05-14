"""Pipeline orchestrator for DeMaestro (W3-B).

Each pipeline stage runs in a daemon thread so the HTTP request returns
immediately and Firestore status is updated as work progresses.
"""
import threading

import structlog

from app.ai.gemini.agents.coordinator import RequirementsCoordinator
from app.models.project import ProjectStatus
from app.models.raw_input import RawInputType
from app.services import firestore_service

log = structlog.get_logger(__name__)

_INITIAL_AMBIGUITY_CAP = 3
_ROUND_CAP = {1: 2, 2: 1}


# ---------- Summary + Blueprint generation ----------

def _generate_and_store_summary_blueprint(uid: str, project_id: str) -> None:
    """Generate summary + blueprint and write them into the project doc."""
    try:
        log.info("generate_summary_blueprint.start", project_id=project_id)
        sr = firestore_service.get_latest_structured_requirements(uid, project_id)
        if sr is None:
            log.error("generate_summary_blueprint.no_sr", project_id=project_id)
            return

        coordinator = RequirementsCoordinator()
        summary = coordinator.generate_summary(sr)
        blueprint_obj = coordinator.generate_blueprint(sr)

        firestore_service.update_project(uid, project_id, {
            "summary": summary,
            "blueprint": blueprint_obj.model_dump(),
        })
        log.info(
            "generate_summary_blueprint.done",
            project_id=project_id,
            summary_length=len(summary),
            num_tables=len(blueprint_obj.database_schema),
        )
    except Exception as exc:
        log.error(
            "generate_summary_blueprint.error",
            project_id=project_id,
            error=str(exc),
        )


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

        coordinator = RequirementsCoordinator()
        sr = coordinator.analyze(combined)

        if len(sr.ambiguities) > _INITIAL_AMBIGUITY_CAP:
            log.info(
                "trimmed_initial_ambiguities",
                uid=uid,
                project_id=project_id,
                original_count=len(sr.ambiguities),
            )
            sr = sr.model_copy(update={"ambiguities": sr.ambiguities[:_INITIAL_AMBIGUITY_CAP]})

        firestore_service.add_structured_requirements(uid, project_id, sr)

        next_status = (
            ProjectStatus.awaiting_approval if not sr.ambiguities else ProjectStatus.clarifying
        )
        firestore_service.set_project_status(uid, project_id, next_status)

        # Pre-generate summary + blueprint if no clarifications are needed.
        if not sr.ambiguities:
            _generate_and_store_summary_blueprint(uid, project_id)

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

        new_round = firestore_service.increment_clarification_round(uid, project_id)

        if new_round >= 3:
            log.warning(
                "forced_finish_after_three_rounds",
                uid=uid,
                project_id=project_id,
                new_round=new_round,
            )
            updated = current.model_copy(
                update={"ambiguities": [], "version": current.version + 1}
            )
            firestore_service.add_structured_requirements(uid, project_id, updated)
            firestore_service.add_clarification_turn(uid, project_id, ambiguity_id, question, answer)
            firestore_service.set_project_status(uid, project_id, ProjectStatus.awaiting_approval)
            # Clarifications are done — generate summary + blueprint now.
            _generate_and_store_summary_blueprint(uid, project_id)
            return

        coordinator = RequirementsCoordinator()
        updated = coordinator.apply_clarification(current, ambiguity_id, answer)

        round_cap = _ROUND_CAP.get(new_round)
        if round_cap is not None and len(updated.ambiguities) > round_cap:
            log.info(
                f"trimmed_clarification_round_{new_round}",
                uid=uid,
                project_id=project_id,
                original_count=len(updated.ambiguities),
                cap=round_cap,
            )
            updated = updated.model_copy(update={"ambiguities": updated.ambiguities[:round_cap]})

        firestore_service.add_structured_requirements(uid, project_id, updated)
        firestore_service.add_clarification_turn(uid, project_id, ambiguity_id, question, answer)

        next_status = (
            ProjectStatus.awaiting_approval if not updated.ambiguities else ProjectStatus.clarifying
        )
        firestore_service.set_project_status(uid, project_id, next_status)

        # Clarifications are done — generate summary + blueprint immediately.
        if not updated.ambiguities:
            log.info("clarifications.complete", uid=uid, project_id=project_id)
            _generate_and_store_summary_blueprint(uid, project_id)

    except Exception as exc:
        log.error(
            "pipeline.clarification.error",
            uid=uid,
            project_id=project_id,
            error=str(exc),
        )
        # Keep project in clarifying (not failed) so the user can retry.
        # Store the error message so the frontend can surface it if needed.
        try:
            firestore_service.update_project(
                uid, project_id, {"last_error": str(exc)}
            )
        except Exception:
            pass  # best-effort; don't mask the original error


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
