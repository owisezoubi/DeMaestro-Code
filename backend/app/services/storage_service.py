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
    """Upload a PDF to users/{uid}/projects/{projectId}/uploads/{inputId}.pdf."""
    path = f"users/{uid}/projects/{project_id}/uploads/{input_id}.pdf"
    return upload_bytes(path, pdf_bytes, content_type="application/pdf")


def upload_zip(uid: str, project_id: str, zip_bytes: bytes, zip_filename: str | None = None) -> str:
    """Upload a generated-project ZIP to Cloud Storage.

    zip_filename: user-visible filename (e.g. "bistro-75.zip"); defaults to
    "{project_id}.zip" when not supplied.  The storage path always uses
    project_id for uniqueness; zip_filename only affects the export filename.

    Returns the storage object path.
    """
    fname = zip_filename or f"{project_id}.zip"
    path = f"users/{uid}/projects/{project_id}/exports/{fname}"
    return upload_bytes(path, zip_bytes, content_type="application/zip")


def signed_download_url(
    blob_path: str,
    expires_in_days: int = 7,
    download_filename: str | None = None,
) -> str:
    """Return a signed URL valid for expires_in_days days (default 7).

    download_filename: when set, the signed URL carries a Content-Disposition
    header so the browser saves the file under that name (e.g. "bistro-75.zip").
    """
    bucket = get_storage_bucket()
    blob = bucket.blob(blob_path)
    kwargs: dict = {
        "expiration": timedelta(days=expires_in_days),
        "method": "GET",
        "version": "v4",
    }
    if download_filename:
        kwargs["response_disposition"] = f'attachment; filename="{download_filename}"'
    return blob.generate_signed_url(**kwargs)
