"""DeployerAgent — ZIP generated files, upload to Cloud Storage, trigger Vercel."""
import io
import zipfile

import structlog

from app.config import settings
from app.models.generation_plan import GenerationPlan


class DeployerAgent:
    """Deploy generated app: ZIP → Cloud Storage → Vercel / deploy-button."""

    def __init__(self) -> None:
        self.log = structlog.get_logger("DeployerAgent")
        self.bucket_name = settings.firebase_storage_bucket or "demaestro-b912b.firebasestorage.app"

    def deploy(
        self,
        uid: str,
        project_id: str,
        generated_files: dict[str, str],
        plan: GenerationPlan,
    ) -> dict:
        """Deploy the generated app.

        Returns: { status, deployment_url, deployment_id, errors }
        """
        self.log.info("deploy.start", project_id=project_id, stack=plan.technology_stack)

        try:
            zip_buffer = self._create_zip(generated_files)

            if settings.mock_ai:
                return self._deploy_mock(project_id, zip_buffer)

            zip_url = self._upload_to_cloud_storage(uid, project_id, zip_buffer)

            if plan.technology_stack == "python-postgres":
                deployment = self._deploy_to_vercel(project_id, generated_files)
            else:
                deployment = self._generate_deploy_button(project_id, zip_url, plan.technology_stack)

            self.log.info("deploy.done", deployment_id=deployment.get("id"))
            return {
                "status": "success",
                "deployment_url": deployment["url"],
                "deployment_id": deployment["id"],
                "errors": [],
            }

        except Exception as exc:
            self.log.error("deploy.error", error=str(exc))
            return {
                "status": "error",
                "deployment_url": None,
                "deployment_id": None,
                "errors": [str(exc)],
            }

    # ── helpers ───────────────────────────────────────────────────────────────

    def _create_zip(self, files: dict[str, str]) -> io.BytesIO:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path, content in files.items():
                zf.writestr(file_path, content)
        buf.seek(0)
        self.log.info("_create_zip.done", zip_size=len(buf.getvalue()))
        return buf

    def _deploy_mock(self, project_id: str, zip_buffer: io.BytesIO) -> dict:
        url = f"https://mock-deploy.vercel.app/{project_id}"
        dep_id = f"mock-deploy-{project_id}"
        self.log.info("deploy.mock.done", url=url, zip_size=len(zip_buffer.getvalue()))
        return {
            "status": "success",
            "deployment_url": url,
            "deployment_id": dep_id,
            "errors": [],
        }

    def _upload_to_cloud_storage(
        self, uid: str, project_id: str, zip_buffer: io.BytesIO
    ) -> str:
        from google.cloud import storage  # lazy import — avoids import error in mock mode

        client = storage.Client()
        bucket = client.bucket(self.bucket_name)
        blob_name = f"generated-apps/{uid}/{project_id}/app.zip"
        blob = bucket.blob(blob_name)
        blob.upload_from_string(zip_buffer.getvalue(), content_type="application/zip")
        url = blob.public_url
        self.log.info("_upload_to_cloud_storage.done", url=url)
        return url

    def _deploy_to_vercel(self, project_id: str, generated_files: dict[str, str]) -> dict:
        import httpx

        token = settings.vercel_token
        if not token:
            raise ValueError("VERCEL_TOKEN not configured")

        files = [
            {"file": path, "data": content, "encoding": "utf8"}
            for path, content in generated_files.items()
        ]

        payload = {
            "name": project_id,
            "files": files,
            "env": {
                "FIREBASE_PROJECT_ID": settings.firebase_storage_bucket.split(".")[0] if settings.firebase_storage_bucket else "",
            },
            "projectSettings": {"framework": "other"},
        }

        self.log.info("_deploy_to_vercel.request", project_id=project_id, num_files=len(files))
        response = httpx.post(
            "https://api.vercel.com/v13/deployments",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        url = data.get("url") or f"{project_id}.vercel.app"
        if not url.startswith("http"):
            url = f"https://{url}"
        self.log.info("_deploy_to_vercel.done", deployment_id=data.get("id"), url=url)
        return {"id": data.get("id", f"deployment-{project_id}"), "url": url}

    def _generate_deploy_button(
        self, project_id: str, zip_url: str, stack: str
    ) -> dict:
        return {
            "id": f"deploy-button-{project_id}",
            "url": (
                f"https://vercel.com/new?env=DATABASE_URL"
                f"&name={project_id}&repository-url={zip_url}"
            ),
        }
