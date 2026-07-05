"""Project CRUD routes (skeleton)."""
import threading
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Body, HTTPException, status

from app.auth.dependencies import CurrentUser
from app.models.project import ProjectCreate, ProjectMeta, ProjectStatus
from app.models.raw_input import RawInputOut
from app.services import firestore_service as fs

log = structlog.get_logger(__name__)

router = APIRouter()


def _regenerate_with_changes(uid: str, project_id: str, change_request: str) -> None:
    """Apply a user-requested change end-to-end: modify → test → debug → verify →
    repackage. Updates the project doc with the new ZIP and a change summary."""
    from datetime import datetime, timezone

    from app.ai.claude.agents.debugger import DebuggerAgent
    from app.ai.claude.agents.deployer import DeployerAgent
    from app.ai.claude.agents.modifier import ModifierAgent
    from app.ai.claude.agents.tester import TesterAgent
    from app.ai.claude.agents.verifier import VerifierAgent
    from app.ai.gemini.agents.blueprint import BlueprintResponse
    from app.models.generation_plan import GenerationPlan
    from app.pipeline.generation_orchestrator import _MAX_TEST_CYCLES

    try:
        project = fs.get_project(uid, project_id)
        if project is None:
            raise ValueError(f"Project {project_id} not found")
        old_files = project.generated_files or {}
        plan_dict = project.generation_plan
        bp_dict = project.blueprint
        if not plan_dict or not bp_dict:
            raise ValueError("Project missing plan or blueprint — cannot modify")
        plan = GenerationPlan(**plan_dict)
        blueprint = BlueprintResponse(**bp_dict)

        # 1. Modifier identifies + edits files
        fs.update_project(uid, project_id, {
            "status": ProjectStatus.modifying, "current_stage": "modifying",
        })
        from app.services import firestore_service as _fs
        sr = _fs.get_latest_structured_requirements(uid, project_id)
        mod_result = ModifierAgent().modify(
            change_request, old_files, plan, blueprint,
            structured_requirements=sr,
        )
        if mod_result["status"] != "fixed" or not mod_result["modified_files"]:
            raise RuntimeError(
                f"Could not apply changes: {mod_result.get('errors') or 'no files changed'}"
            )
        updated_files = {**old_files, **mod_result["modified_files"]}
        changed_paths = list(mod_result["modified_files"].keys())

        # 2. Full test/debug cycle on the modified app
        fs.update_project(uid, project_id, {
            "status": ProjectStatus.testing, "current_stage": "testing",
        })
        attempt_count: dict = {}
        for _cycle in range(_MAX_TEST_CYCLES):
            test_results = TesterAgent().run_tests(updated_files, plan)
            if test_results["status"] in ("success", "skipped"):
                break
            debug_result = DebuggerAgent().debug_and_fix(
                test_results, updated_files, plan, attempt_count
            )
            if debug_result.get("status") == "fixed":
                updated_files.update(debug_result["fixed_files"])
                attempt_count = debug_result.get("attempt_counts", attempt_count)
            else:
                break  # accept best-effort

        # 3. Verifier (advisory; do not block packaging on warnings)
        try:
            VerifierAgent().verify(updated_files, plan, blueprint)
        except Exception as exc:
            log.warning("modify.verify_warning", project_id=project_id, error=str(exc))

        # 4. Repackage and produce new ZIP URL
        fs.update_project(uid, project_id, {"current_stage": "packaging"})
        deploy_result = DeployerAgent().deploy(uid, project_id, updated_files, plan)
        if deploy_result.get("status") != "ready":
            raise RuntimeError(f"Packaging failed: {deploy_result.get('errors', [])}")

        # 5. Update project with new state + record in history
        modification_round = project.modification_round or 1
        history_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request": change_request,
            "summary": mod_result.get("summary", ""),
            "files_changed": changed_paths,
            "round": modification_round,
        }
        new_history = (project.modification_history or []) + [history_entry]
        new_history = new_history[-20:]

        fs.update_project(uid, project_id, {
            "status": ProjectStatus.ready,
            "current_stage": "ready",
            "generated_files": updated_files,
            "zip_url": deploy_result["zip_url"],
            "modification_round_completed": modification_round,
            "last_modification_summary": mod_result.get("summary", ""),
            "modified_files_last": changed_paths,
            "modification_history": new_history,
            "error_message": None,
        })
        log.info(
            "modify.complete",
            project_id=project_id,
            round=modification_round,
            files_changed=changed_paths,
        )
    except Exception as exc:
        log.error("modify.error", project_id=project_id, error=str(exc))
        fs.update_project(uid, project_id, {
            "status": ProjectStatus.failed,
            "error_message": str(exc),
        })


# Statuses where the backend is actively running LLM-heavy work.
# Note: there is no separate "debugging" status — debug cycles run
# under ProjectStatus.testing (the orchestrator sets it each cycle).
_ACTIVE_STATUSES = {
    ProjectStatus.generating,
    ProjectStatus.testing,
    ProjectStatus.deploying,
    ProjectStatus.blueprinting,
    ProjectStatus.packaging,
    ProjectStatus.modifying,
    ProjectStatus.regenerating,
    ProjectStatus.verifying,
}


@router.get("/active")
async def get_active_project(user: CurrentUser) -> dict:
    """Return the user's currently running project (if any), else null.

    Polled by the frontend's global ActiveGenerationBanner every 3 s.
    Returns a lightweight dict rather than the full ProjectMeta to keep
    responses small.
    """
    active = fs.list_projects_in_statuses(user.uid, _ACTIVE_STATUSES)
    if not active:
        return {"project": None}
    p = active[0]
    return {
        "project": {
            "id": p.id,
            "name": p.name,
            "status": p.status.value,
            "current_stage": p.current_stage,
            "current_file": p.current_file,
            "generated_count": p.generated_count or 0,
            "total_files": p.total_files,
        },
    }


@router.get("", response_model=list[ProjectMeta])
async def list_projects(user: CurrentUser):
    """List all projects belonging to the current user."""
    return fs.list_projects(user.uid)


@router.post("", response_model=ProjectMeta, status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectCreate, user: CurrentUser):
    """Create a new project."""
    project_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)
    meta = ProjectMeta(
        id=project_id,
        name=payload.name,
        status=ProjectStatus.awaiting_input,
        created_at=now,
        updated_at=now,
    )
    fs.create_project(user.uid, meta)
    return meta


@router.get("/{project_id}", response_model=ProjectMeta)
async def get_project(project_id: str, user: CurrentUser):
    project = fs.get_project(user.uid, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/{project_id}/raw-inputs", response_model=list[RawInputOut])
async def get_project_raw_inputs(project_id: str, user: CurrentUser):
    """Return all raw requirement inputs for a project."""
    project = fs.get_project(user.uid, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    docs = fs.list_raw_inputs(user.uid, project_id)
    return [RawInputOut.from_doc(doc) for doc in docs]


@router.delete("/{project_id}")
async def delete_project(project_id: str, user: CurrentUser) -> dict:
    """Delete a project and all associated data."""
    project = fs.get_project(user.uid, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    fs.delete_project(user.uid, project_id)
    return {"message": "Project deleted successfully", "project_id": project_id}


@router.post("/{project_id}/request-changes")
async def request_changes(
    project_id: str,
    user: CurrentUser,
    request_body: dict = Body(...),
) -> dict:
    """Request changes to a deployed app. Starts background regeneration."""
    project = fs.get_project(user.uid, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status not in (ProjectStatus.ready, ProjectStatus.ready_with_warnings):
        raise HTTPException(status_code=400, detail=f"Cannot request changes from status {project.status}")

    change_request = request_body.get("change_request")
    if not change_request:
        raise HTTPException(status_code=400, detail="change_request is required")

    modification_round = (project.modification_round or 0) + 1
    fs.update_project(user.uid, project_id, {
        "status": ProjectStatus.modifying,
        "last_change_request": change_request,
        "modification_round": modification_round,
    })

    threading.Thread(
        target=_regenerate_with_changes,
        args=(user.uid, project_id, change_request),
        daemon=True,
    ).start()

    return {"message": "Changes requested. Regenerating...", "project_id": project_id}


@router.get("/{project_id}/deployment-status")
async def deployment_status(project_id: str, user: CurrentUser):
    """Return current Vercel deployment status for this project."""
    project = fs.get_project(user.uid, project_id)
    log.info(
        "deployment_status.endpoint.called",
        project_id=project_id, uid=user.uid,
        found=bool(project),
        status=project.deployment_status if project else None,
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "status": project.deployment_status or None,
        "url": project.deployment_url or None,
        "error": project.deployment_error or None,
        "project_name": project.deployment_project_name or None,
        "display_name": project.deployment_display_name or None,
    }


@router.post("/{project_id}/deploy")
async def deploy_project(project_id: str, user: CurrentUser):
    """Manually trigger a Vercel deployment for this project."""
    from app.services import vercel_deployer

    log.info("deploy.endpoint.called", project_id=project_id, uid=user.uid)

    if not vercel_deployer.is_configured():
        raise HTTPException(
            status_code=400,
            detail=(
                "Vercel deployment not configured. Set "
                "DEMAESTRO_VERCEL_TOKEN and DEMAESTRO_POSTGRES_URL "
                "in backend/.env, then restart the DeMaestro backend. "
                "Check the backend logs for vercel_deployer."
                "not_configured events for which var is missing."
            ),
        )
    project = fs.get_project(user.uid, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    files = project.generated_files or {}
    if not files:
        raise HTTPException(status_code=400, detail="Project has no generated files")

    fs.update_project(user.uid, project_id, {
        "deployment_status": "deploying",
        "deployment_url": None,
        "deployment_error": None,
    })

    app_name = project.name or project_id

    def _bg(_uid=user.uid, _pid=project_id, _files=dict(files), _name=app_name):
        try:
            result = vercel_deployer.deploy(
                project_name=_name,
                files=_files,
                wait_for_ready=True,
                stable_id=_pid,
            )
            log.info("deploy.bg.success", project_id=_pid, url=result["url"])
            try:
                live = result.get("live_smoke") or {}
                deployment_status = "ready"
                deployment_error = None
                if isinstance(live, dict) and live.get("ok") is False:
                    deployment_status = "ready_degraded"
                    deployment_error = live.get("reason") or "live smoke failed"
                    log.warning(
                        "deploy.bg.live_smoke_failed",
                        project_id=_pid,
                        reason=live.get("reason"),
                        failures=live.get("failures", [])[:5],
                    )

                update_payload = {
                    "deployment_status": deployment_status,
                    "deployment_url": result["url"],
                    "deployment_project_name": result["project_name"],
                    "deployment_display_name": result.get("display_name"),
                    "deployment_error": deployment_error,
                }
                # If the deployer's pre-deploy normalizers patched any files,
                # persist them back so ZIP downloads and the file explorer
                # show the same code that was actually shipped to Vercel.
                patched = result.get("patched_files")
                if patched and patched != _files:
                    changed = [
                        p for p in patched
                        if _files.get(p) != patched.get(p)
                    ]
                    if changed:
                        update_payload["generated_files"] = patched
                        log.info(
                            "deploy.bg.files_persisted",
                            project_id=_pid,
                            changed=changed[:20],
                            count=len(changed),
                        )
                fs.update_project(_uid, _pid, update_payload)
                log.info("deploy.bg.firestore_updated", project_id=_pid)
            except Exception as fs_exc:
                log.error(
                    "deploy.bg.firestore_update_failed",
                    project_id=_pid, url=result["url"], error=str(fs_exc),
                )
        except vercel_deployer.VercelDeployError as e:
            log.error("deploy.bg.vercel_failed", project_id=_pid, error=str(e))
            try:
                fs.update_project(_uid, _pid, {
                    "deployment_status": "failed",
                    "deployment_error": str(e),
                })
            except Exception as fs_exc:
                log.error(
                    "deploy.bg.firestore_update_failed_after_vercel_fail",
                    project_id=_pid, error=str(fs_exc),
                )
        except Exception as e:
            log.error("deploy.bg.unexpected", project_id=_pid, error=str(e))
            try:
                fs.update_project(_uid, _pid, {
                    "deployment_status": "failed",
                    "deployment_error": f"unexpected: {e}",
                })
            except Exception:
                pass

    threading.Thread(target=_bg, daemon=True).start()
    return {"status": "deploying"}
