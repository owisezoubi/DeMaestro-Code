"""Firebase Admin SDK initialization and Firestore client accessor."""

import base64
import json
from pathlib import Path
from typing import Optional

import firebase_admin
from firebase_admin import auth, credentials, firestore, storage
import structlog

from app.config import settings

log = structlog.get_logger(__name__)

_app: Optional[firebase_admin.App] = None


def _resolve_credentials() -> Optional[credentials.Certificate]:
    """Return Firebase credentials from one of two sources, in order:

    1. The FIREBASE_SERVICE_ACCOUNT_B64 env var (cloud deploys like Render)
       — the JSON service account encoded as base64.
    2. A JSON file at FIREBASE_SERVICE_ACCOUNT_PATH (local development).
    """
    # Cloud path: env var takes precedence
    if settings.firebase_service_account_b64:
        try:
            decoded = base64.b64decode(settings.firebase_service_account_b64)
            data = json.loads(decoded)
            log.info(
                "firebase_credentials_from_env",
                project_id=data.get("project_id"),
            )
            return credentials.Certificate(data)
        except Exception as exc:
            log.error("firebase_b64_decode_failed", error=str(exc))
            return None

    # Local path: JSON file
    cred_path: Path = settings.firebase_service_account_full_path
    if not cred_path.exists():
        log.warning(
            "firebase_credentials_missing",
            path=str(cred_path),
            hint=(
                "Set FIREBASE_SERVICE_ACCOUNT_B64 env var (cloud) or place "
                "service-account JSON at this path (local). "
                "See SETUP_GUIDE.md §2.6."
            ),
        )
        return None

    log.info("firebase_credentials_from_file", path=str(cred_path))
    return credentials.Certificate(str(cred_path))


def init_firebase() -> Optional[firebase_admin.App]:
    """Initialize the Firebase Admin SDK once at app startup."""
    global _app
    if _app is not None:
        return _app

    cred = _resolve_credentials()
    if cred is None:
        return None

    # Pass the storage bucket name so storage.bucket() works without args.
    options = None
    if settings.firebase_storage_bucket:
        options = {"storageBucket": settings.firebase_storage_bucket}

    _app = firebase_admin.initialize_app(cred, options)
    log.info(
        "firebase_initialized",
        project_id=cred.project_id,
        storage_bucket=settings.firebase_storage_bucket or "(not set)",
    )
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
