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
from datetime import datetime, timezone
from typing import Optional

from app.auth.firebase_admin import get_firestore_client
from app.models.project import ProjectMeta


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
    """NOTE: this only deletes the project doc, not subcollections.
    Full cascade is left for a later improvement (Week 6 polish)."""
    _project_doc(uid, project_id).delete()
