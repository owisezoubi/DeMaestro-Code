"""Firebase Admin SDK initialization and Firestore client accessor."""
from pathlib import Path
from typing import Optional

import firebase_admin
from firebase_admin import auth, credentials, firestore, storage
import structlog

from app.config import settings

log = structlog.get_logger(__name__)

_app: Optional[firebase_admin.App] = None


def init_firebase() -> firebase_admin.App:
    """Initialize the Firebase Admin SDK once at app startup."""
    global _app
    if _app is not None:
        return _app

    cred_path: Path = settings.firebase_service_account_full_path
    if not cred_path.exists():
        log.warning(
            "firebase_credentials_missing",
            path=str(cred_path),
            hint="Place service-account JSON at this path. See SETUP_GUIDE.md §2.6.",
        )
        # Allow the app to start in dev so endpoints that don't need Firebase
        # remain reachable (e.g. /health). Endpoints that *do* need Firebase
        # will fail with a clear error when called.
        return None  # type: ignore[return-value]

    cred = credentials.Certificate(str(cred_path))
    _app = firebase_admin.initialize_app(cred)
    log.info("firebase_initialized", project_id=cred.project_id)
    return _app


def get_firestore_client():
    """Lazily return the Firestore client."""
    if _app is None:
        init_firebase()
    return firestore.client()


def get_storage_bucket():
    """Lazily return the default Storage bucket."""
    if _app is None:
        init_firebase()
    return storage.bucket()


def verify_id_token(id_token: str) -> dict:
    """Verify a Firebase ID token from a frontend request.

    Returns the decoded claims (uid, email, ...) on success, raises on failure.
    """
    return auth.verify_id_token(id_token)
