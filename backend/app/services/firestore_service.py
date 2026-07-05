"""Thin wrappers around Firestore for users + projects.

Document layout (matches the Phase B Architecture Guide):

    users/{uid}                                    user metadata
      └── projects/{projectId}                     per-project doc
            ├── raw_inputs/{inputId}
            ├── structured_requirements/{ver}
            ├── clarifications/{turnId}
            ├── summary_documents/{ver}
            ├── blueprints/{ver}
            ├── ai_calls/{callId}
            ├── verification_logs/{logId}
            └── exports/{exportId}
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from pydantic import ValidationError

from google.cloud import firestore as _fs

from app.auth.firebase_admin import get_firestore_client
from app.models.project import ProjectMeta, ProjectStatus
from app.models.raw_input import RawInputDoc
from app.models.structured_requirements import StructuredRequirements

_svc_log = structlog.get_logger("firestore_service")


def _users():
    return get_firestore_client().collection("users")


def _project_doc(uid: str, project_id: str):
    return _users().document(uid).collection("projects").document(project_id)


# ---------- Users ----------
def upsert_user_profile(
    uid: str,
    email: Optional[str] = None,
    last_login_at: Optional[datetime] = None,
) -> None:
    """Create the user profile doc on first login; update last_login_at otherwise."""
    user_ref = _users().document(uid)
    snap = user_ref.get()
    now = datetime.now(timezone.utc)
    if not snap.exists:
        user_ref.set(
            {
                "uid": uid,
                "email": email,
                "created_at": now,
                "last_login_at": last_login_at or now,
            }
        )
    else:
        user_ref.update(
            {
                "last_login_at": last_login_at or now,
                **({"email": email} if email else {}),
            }
        )


# ---------- Projects ----------
def list_projects(uid: str) -> list[ProjectMeta]:
    snaps = (
        _users()
        .document(uid)
        .collection("projects")
        .order_by("created_at", direction="DESCENDING")
        .stream()
    )
    return [ProjectMeta.model_validate({**s.to_dict(), "id": s.id}) for s in snaps]


def list_projects_in_statuses(uid: str, statuses: set) -> list[ProjectMeta]:
    """Return projects whose status is one of the given ProjectStatus values."""
    values = [s.value if hasattr(s, "value") else s for s in statuses]
    snaps = (
        _users()
        .document(uid)
        .collection("projects")
        .where("status", "in", values)
        .stream()
    )
    out = []
    for s in snaps:
        try:
            out.append(ProjectMeta.model_validate({**s.to_dict(), "id": s.id}))
        except Exception:
            pass
    return out


def get_project(uid: str, project_id: str) -> Optional[ProjectMeta]:
    snap = _project_doc(uid, project_id).get()
    if not snap.exists:
        return None
    raw = {**snap.to_dict(), "id": snap.id}
    try:
        return ProjectMeta.model_validate(raw)
    except ValidationError as exc:
        _svc_log.error(
            "firestore_service.get_project.validation_failed",
            project_id=project_id,
            errors=exc.errors(),
            raw_keys=list(raw.keys()),
        )
        # Repair: coerce known nullable / list fields to their defaults and retry once.
        repaired = dict(raw)
        for field in ("current_stage", "current_file", "last_error", "last_modification_summary"):
            if repaired.get(field) is None:
                repaired[field] = None  # already None; ensure key is present
        for field in (
            "last_failed_checks", "real_failures", "environmental_skips",
            "modified_files_last", "resolved_topics", "user_added_requirements",
            "modification_history",
        ):
            if repaired.get(field) is None:
                repaired[field] = []
        for field in ("clarification_progress",):
            if repaired.get(field) is None:
                repaired[field] = {}
        for int_field, default in (("generated_count", 0), ("debug_cycle", 0)):
            if repaired.get(int_field) is None:
                repaired[int_field] = default
        try:
            return ProjectMeta.model_validate(repaired)
        except ValidationError:
            raise exc  # re-raise the original with the original fields


def create_project(uid: str, meta: ProjectMeta) -> None:
    _project_doc(uid, meta.id).set(meta.model_dump(exclude={"id"}))


def update_project(uid: str, project_id: str, fields: dict) -> None:
    fields = dict(fields)
    fields["updated_at"] = datetime.now(timezone.utc)
    _project_doc(uid, project_id).update(fields)


def delete_project(uid: str, project_id: str) -> None:
    """Delete project document and all known subcollections."""
    project_ref = _project_doc(uid, project_id)
    subcollections = [
        "raw_inputs",
        "structured_requirements",
        "clarifications",
        "summary_documents",
        "blueprints",
        "ai_calls",
        "verification_logs",
        "exports",
    ]
    for subcol in subcollections:
        for doc in project_ref.collection(subcol).stream():
            doc.reference.delete()
    project_ref.delete()


def reset_generation_state(uid: str, project_id: str) -> None:
    """Wipe all generation + deployment fields so the pipeline can start fresh.

    Preserves: name, description, structured_requirements, blueprint, checklist
    — the user's captured intent stays intact.  Idempotent.
    """
    _log = structlog.get_logger("firestore_service")
    project_ref = _project_doc(uid, project_id)

    snap = project_ref.get()
    files_cleared = 0
    if snap.exists:
        old_files = snap.to_dict().get("generated_files") or {}
        files_cleared = len(old_files) if isinstance(old_files, dict) else 0

    project_ref.update({
        "status": ProjectStatus.awaiting_approval,
        "generated_files": {},
        "generation_plan": None,
        "generated_count": 0,
        "total_files": 0,
        "current_file": None,
        "current_stage": None,
        "last_failed_checks": [],
        "real_failures": [],
        "environmental_skips": [],
        "last_error": None,
        "error_message": None,
        "test_error_log": None,
        "install_error_log": None,
        "debug_cycle": 0,
        "generation_started_at": None,
        "generation_finished_at": None,
        "test_results_last": None,
        "modified_files_last": [],
        "checklist_results": [],
        "test_results": None,
        "typecheck_warnings": 0,
        "typecheck_advisory_output": None,
        "contract_advisory_misses": [],
        "zip_url": None,
        # Deployment fields — new deploy overwrites in-place but clear
        # the URL immediately so the UI doesn't show the old live link
        # while the new build is running.
        "deployment_url": None,
        "deployment_status": None,
        "deployment_project_name": None,
        "deployment_display_name": None,
        "deployment_error": None,
        "updated_at": datetime.now(timezone.utc),
    })
    _log.info("generation.reset_done", project_id=project_id, files_deleted=files_cleared)


# ---------- Raw inputs ----------

def _raw_inputs_col(uid: str, project_id: str):
    return _project_doc(uid, project_id).collection("raw_inputs")


def add_raw_input(uid: str, project_id: str, raw_input: RawInputDoc) -> str:
    """Write a RawInputDoc to Firestore and return its ID."""
    ref = _raw_inputs_col(uid, project_id).document(raw_input.id)
    ref.set(raw_input.model_dump(exclude={"id"}))
    return raw_input.id


def list_raw_inputs(uid: str, project_id: str) -> list[RawInputDoc]:
    """Return all raw inputs for a project, newest first."""
    snaps = (
        _raw_inputs_col(uid, project_id)
        .order_by("timestamp", direction="DESCENDING")
        .stream()
    )
    return [RawInputDoc.model_validate({**s.to_dict(), "id": s.id}) for s in snaps]


def set_project_status(uid: str, project_id: str, status: ProjectStatus) -> None:
    """Convenience wrapper that stamps the project with a new status."""
    update_project(uid, project_id, {"status": status})


def increment_clarification_round(uid: str, project_id: str) -> int:
    """Atomically increment the project's clarification_round counter.
    Returns the new value."""
    ref = _project_doc(uid, project_id)
    ref.update({"clarification_round": _fs.Increment(1)})
    snap = ref.get()
    return snap.to_dict().get("clarification_round", 0)


# ---------- Structured requirements ----------

def _structured_requirements_col(uid: str, project_id: str):
    return _project_doc(uid, project_id).collection("structured_requirements")


def add_structured_requirements(uid: str, project_id: str, sr: StructuredRequirements) -> str:
    """Write a StructuredRequirements version doc and return its doc id (e.g. 'v1')."""
    ver_id = f"v{sr.version}"
    _structured_requirements_col(uid, project_id).document(ver_id).set(sr.model_dump())
    return ver_id


def get_latest_structured_requirements(uid: str, project_id: str) -> Optional[StructuredRequirements]:
    """Return the highest-versioned StructuredRequirements, or None if none exist."""
    snaps = list(_structured_requirements_col(uid, project_id).stream())
    if not snaps:
        return None
    best = max(snaps, key=lambda s: s.to_dict().get("version", 0))
    return StructuredRequirements.model_validate(best.to_dict())


# ---------- Clarification turns ----------

def _clarifications_col(uid: str, project_id: str):
    return _project_doc(uid, project_id).collection("clarifications")


def add_clarification_turn(
    uid: str,
    project_id: str,
    ambiguity_id: str,
    question: str,
    answer: str,
    suggested_options: list | None = None,
    field_path: str | None = None,
) -> str:
    """Record a clarification Q&A turn and return its turn id."""
    turn_id = uuid.uuid4().hex[:12]
    _clarifications_col(uid, project_id).document(turn_id).set(
        {
            "ambiguity_id": ambiguity_id,
            "question": question,
            "answer": answer,
            "suggested_options": suggested_options or [],
            "field_path": field_path,
            "timestamp": datetime.now(timezone.utc),
        }
    )
    return turn_id


def add_resolved_topic(uid: str, project_id: str, topic: str) -> None:
    """Atomically append a resolved topic. ArrayUnion drops duplicates."""
    _project_doc(uid, project_id).update({
        "resolved_topics": _fs.ArrayUnion([topic]),
        "updated_at": datetime.now(timezone.utc),
    })


def get_resolved_topics(uid: str, project_id: str) -> list[str]:
    """Return the list of canonical topics already resolved for this project."""
    snap = _project_doc(uid, project_id).get()
    if not snap.exists:
        return []
    return snap.to_dict().get("resolved_topics", [])


def list_clarification_turns(uid: str, project_id: str) -> list[dict]:
    """Return all clarification turns for a project, oldest first."""
    snaps = _clarifications_col(uid, project_id).order_by("timestamp").stream()
    return [{**s.to_dict(), "id": s.id} for s in snaps]
