"""DeployerAgent — packages generated code into a downloadable ZIP.

Note: external deployment (Vercel/Render/GitHub) is deferred. See the
commented-out _deploy_to_vercel / _generate_deploy_button methods at the
bottom of this file for the partial implementation we'll re-enable later.
"""
import re
from datetime import datetime, timezone

import structlog

from app.config import settings
from app.models.generation_plan import GenerationPlan
from app.models.project import ProjectStatus
from app.services import firestore_service, storage_service, zip_service


def _slugify(name: str) -> str:
    """Turn 'Bistro 75' into 'bistro-75' for use as a ZIP filename."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "project"


class DeployerAgent:
    """Package generated files into a ZIP, upload to Cloud Storage, return signed URL."""

    def __init__(self) -> None:
        self.log = structlog.get_logger("DeployerAgent")

    def deploy(
        self,
        uid: str,
        project_id: str,
        generated_files: dict[str, str],
        plan: GenerationPlan,
        app_name: str | None = None,
    ) -> dict:
        """Package the generated app and return a signed download URL.

        app_name: human-readable project name used to build the ZIP filename
        (e.g. "Bistro 75" → "bistro-75.zip").  Falls back to project_id when absent.

        Returns: { status: "ready", zip_url: <signed_url> }
        """
        self.log.info("deploy.start", project_id=project_id, num_files=len(generated_files))

        zip_filename = f"{_slugify(app_name)}.zip" if app_name else f"{project_id}.zip"

        if settings.mock_ai:
            zip_url = f"https://mock-zip-url/{zip_filename}"
            self.log.info("deploy.mock.done", project_id=project_id, zip_url=zip_url)
            return {"status": "ready", "zip_url": zip_url}

        try:
            zip_bytes = zip_service.package_project(generated_files)
            self.log.info("deploy.zip_created", project_id=project_id, zip_bytes=len(zip_bytes))

            storage_path = storage_service.upload_zip(uid, project_id, zip_bytes, zip_filename=zip_filename)
            self.log.info("deploy.uploaded", project_id=project_id, path=storage_path)

            signed_url = storage_service.signed_download_url(
                storage_path,
                expires_in_days=7,
                download_filename=zip_filename,
            )

            firestore_service.update_project(uid, project_id, {
                "zip_url": signed_url,
                "zip_filename": zip_filename,
                "packaged_at": datetime.now(timezone.utc).isoformat(),
                "status": ProjectStatus.ready,
            })

            self.log.info("deploy.zip_ready", project_id=project_id, zip_url=signed_url, zip_filename=zip_filename)
            return {"status": "ready", "zip_url": signed_url}

        except Exception as exc:
            self.log.error("deploy.error", project_id=project_id, error=str(exc))
            return {"status": "error", "zip_url": None, "errors": [str(exc)]}


# --- DEFERRED: external deployment (re-enable later) ---
#
# The methods below implement Vercel/GitHub-based deployment. They are
# preserved here for when we re-enable external deployment in a future sprint.
#
# class DeployerAgent:  (continued)
#
#     def _upload_to_cloud_storage(
#         self, uid: str, project_id: str, zip_buffer: io.BytesIO
#     ) -> str:
#         from google.cloud import storage  # lazy import
#         client = storage.Client()
#         bucket = client.bucket(self.bucket_name)
#         blob_name = f"generated-apps/{uid}/{project_id}/app.zip"
#         blob = bucket.blob(blob_name)
#         blob.upload_from_string(zip_buffer.getvalue(), content_type="application/zip")
#         url = blob.public_url
#         self.log.info("_upload_to_cloud_storage.done", url=url)
#         return url
#
#     def _deploy_to_vercel(self, project_id: str, generated_files: dict[str, str]) -> dict:
#         import httpx
#         token = settings.vercel_token
#         if not token:
#             raise ValueError("VERCEL_TOKEN not configured")
#         files = [
#             {"file": path, "data": content, "encoding": "utf8"}
#             for path, content in generated_files.items()
#         ]
#         payload = {
#             "name": project_id,
#             "files": files,
#             "env": {
#                 "FIREBASE_PROJECT_ID": settings.firebase_storage_bucket.split(".")[0]
#                 if settings.firebase_storage_bucket else "",
#             },
#             "projectSettings": {"framework": "other"},
#         }
#         self.log.info("_deploy_to_vercel.request", project_id=project_id, num_files=len(files))
#         response = httpx.post(
#             "https://api.vercel.com/v13/deployments",
#             json=payload,
#             headers={"Authorization": f"Bearer {token}"},
#             timeout=60,
#         )
#         response.raise_for_status()
#         data = response.json()
#         url = data.get("url") or f"{project_id}.vercel.app"
#         if not url.startswith("http"):
#             url = f"https://{url}"
#         self.log.info("_deploy_to_vercel.done", deployment_id=data.get("id"), url=url)
#         return {"id": data.get("id", f"deployment-{project_id}"), "url": url}
#
#     def _generate_deploy_button(
#         self, project_id: str, zip_url: str, stack: str
#     ) -> dict:
#         return {
#             "id": f"deploy-button-{project_id}",
#             "url": (
#                 f"https://vercel.com/new?env=DATABASE_URL"
#                 f"&name={project_id}&repository-url={zip_url}"
#             ),
#         }
#
#     def _deploy_mock(self, project_id: str, zip_buffer: io.BytesIO) -> dict:
#         url = f"https://mock-deploy.vercel.app/{project_id}"
#         dep_id = f"mock-deploy-{project_id}"
#         self.log.info("deploy.mock.done", url=url, zip_size=len(zip_buffer.getvalue()))
#         return {
#             "status": "success",
#             "deployment_url": url,
#             "deployment_id": dep_id,
#             "errors": [],
#         }
