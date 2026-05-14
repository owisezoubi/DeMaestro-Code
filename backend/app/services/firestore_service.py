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

from google.cloud import firestore as _fs

from app.auth.firebase_admin import get_firestore_client
from app.models.project import ProjectMeta, ProjectStatus
from app.models.raw_input import RawInputDoc
from app.models.structured_requirements import StructuredRequirements


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


def get_project(uid: str, project_id: str) -> Optional[ProjectMeta]:
    snap = _project_doc(uid, project_id).get()
    if not snap.exists:
        return None
    return ProjectMeta.model_validate({**snap.to_dict(), "id": snap.id})


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
) -> str:
    """Record a clarification Q&A turn and return its turn id."""
    turn_id = uuid.uuid4().hex[:12]
    _clarifications_col(uid, project_id).document(turn_id).set(
        {
            "ambiguity_id": ambiguity_id,
            "question": question,
            "answer": answer,
            "timestamp": datetime.now(timezone.utc),
        }
    )
    return turn_id


def list_clarification_turns(uid: str, project_id: str) -> list[dict]:
    """Return all clarification turns for a project, oldest first."""
    snaps = _clarifications_col(uid, project_id).order_by("timestamp").stream()
    return [{**s.to_dict(), "id": s.id} for s in snaps]
