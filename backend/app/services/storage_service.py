"""Firebase Cloud Storage helpers — uploads of PDF inputs and ZIP exports."""
from datetime import timedelta
from pathlib import Path

from app.auth.firebase_admin import get_storage_bucket


def upload_bytes(blob_path: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    """Upload bytes to a path inside the default bucket. Returns the path."""
    bucket = get_storage_bucket()
    blob = bucket.blob(blob_path)
    blob.upload_from_string(data, content_type=content_type)
    return blob_path


def upload_file(blob_path: str, local_path: Path, content_type: str | None = None) -> str:
    bucket = get_storage_bucket()
    blob = bucket.blob(blob_path)
    blob.upload_from_filename(str(local_path), content_type=content_type)
    return blob_path


def upload_pdf(uid: str, project_id: str, input_id: str, pdf_bytes: bytes) -> str:
    """Upload a PDF to users/{uid}/projects/{projectId}/uploads/{inputId}.pdf.

    Returns the storage path.
    """
    path = f"users/{uid}/projects/{project_id}/uploads/{input_id}.pdf"
    return upload_bytes(path, pdf_bytes, content_type="application/pdf")


def signed_download_url(blob_path: str, expires_in_minutes: int = 15) -> str:
    bucket = get_storage_bucket()
    blob = bucket.blob(blob_path)
    return blob.generate_signed_url(
        expiration=timedelta(minutes=expires_in_minutes),
        method="GET",
        version="v4",
    )
