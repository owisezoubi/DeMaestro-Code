"""Generation orchestrator — full state machine (W5-P2).

State machine:
  approved → generating → generated → testing → tested
           → verifying → verified → packaging → ready | ready_with_warnings
  (on any failure) → failed

Best-effort packaging: if tests fail after all debug cycles, the pipeline
still packages the ZIP and sets status to ready_with_warnings so the user
can inspect and fix the code themselves.
"""
import structlog

from app.ai.claude.agents.architect import ArchitectAgent
from app.config import settings
from app.ai.claude.agents.debugger import DebuggerAgent, _is_infrastructure_error
from app.ai.claude.agents.deployer import DeployerAgent
from app.ai.claude.agents.generator import GeneratorAgent
from app.ai.claude.agents.tester import TesterAgent
from app.ai.claude.agents.verifier import VerifierAgent
from app.ai.gemini.agents.blueprint import BlueprintResponse
from app.models.project import ProjectStatus
from app.services import firestore_service
from app.services import template_service

log = structlog.get_logger("GenerationOrchestrator")

_MAX_TEST_CYCLES = 5


class GenerationOrchestrator:
    def __init__(self) -> None:
        self.architect = ArchitectAgent()
        self.generator = GeneratorAgent()
        self.tester = TesterAgent()
        self.debugger = DebuggerAgent()
        self.verifier = VerifierAgent()
        self.deployer = DeployerAgent()

    # ── W5-P1 simple entry point (kept for backward-compat) ──────────────────

    def run_generation(self, uid: str, project_id: str) -> dict:
        """Architect + generate only (no test/debug/verify/deploy).

        Returns: { status, generated_files, plan, errors }
        """
        log.info("generation.start", project_id=project_id)

        try:
            project = firestore_service.get_project(uid, project_id)
            if project is None:
                raise ValueError(f"Project {project_id} not found")

            sr = firestore_service.get_latest_structured_requirements(uid, project_id)
            if sr is None:
                raise ValueError(f"No structured requirements for project {project_id}")

            blueprint_dict = project.blueprint
            if blueprint_dict is None:
                raise ValueError(f"No blueprint for project {project_id}")
            blueprint = BlueprintResponse(**blueprint_dict)

            plan = self.architect.architect(sr, blueprint)
            log.info("generation.architect.done", project_id=project_id, num_files=len(plan.files))

            app_slug = template_service.slugify(sr.app_name)
            scaffolding = template_service.load_stack_templates(plan.technology_stack, sr.app_name, app_slug)
            generated_files: dict[str, str] = dict(scaffolding)
            log.info("generation.scaffolding.seeded", project_id=project_id, num_scaffold=len(scaffolding))

            for file_path in plan.generation_order:
                file_to_gen = next((f for f in plan.files if f.path == file_path), None)
                if file_to_gen is None:
                    log.warning("generation.file_not_in_plan", file_path=file_path)
                    continue
                if file_path in scaffolding:
                    log.warning("generation.template_collision", file_path=file_path)
                    continue
                content = self.generator.generate_file(
                    file_to_gen, plan, blueprint, generated_files
                )
                generated_files[file_path] = content

            log.info("generation.generate.done", project_id=project_id, num_files=len(generated_files))

            firestore_service.update_project(uid, project_id, {
                "generated_files": generated_files,
                "generation_plan": plan.model_dump(),
                "status": ProjectStatus.generated,
            })

            return {
                "status": "success",
                "generated_files": generated_files,
                "plan": plan.model_dump(),
                "errors": [],
            }

        except Exception as exc:
            log.error("generation.error", project_id=project_id, error=str(exc))
            firestore_service.set_project_status(uid, project_id, ProjectStatus.failed)
            return {
                "status": "error",
                "generated_files": {},
                "plan": None,
                "errors": [str(exc)],
            }

    # ── W5-P2 full pipeline ──────────────────────────────────────────────────

    def run_full_pipeline(self, uid: str, project_id: str) -> dict:
        """Execute the full generation pipeline.

        Returns: { status, zip_url, generated_files, errors }
        """
        log.info("pipeline.start", project_id=project_id)

        try:
            project = firestore_service.get_project(uid, project_id)
            if project is None:
                raise ValueError(f"Project {project_id} not found")

            sr = firestore_service.get_latest_structured_requirements(uid, project_id)
            if sr is None:
                raise ValueError(f"No structured requirements for project {project_id}")

            blueprint_dict = project.blueprint
            if blueprint_dict is None:
                raise ValueError(f"No blueprint for project {project_id}")
            blueprint = BlueprintResponse(**blueprint_dict)

            # ── STEP 1: Architect + Generate ─────────────────────────────────
            firestore_service.set_project_status(uid, project_id, ProjectStatus.generating)

            plan = self.architect.architect(sr, blueprint)
            log.info("pipeline.architect.done", project_id=project_id, num_files=len(plan.files))

            app_slug = template_service.slugify(sr.app_name)
            scaffolding = template_service.load_stack_templates(plan.technology_stack, sr.app_name, app_slug)
            generated_files: dict[str, str] = dict(scaffolding)
            log.info("pipeline.scaffolding.seeded", project_id=project_id, num_scaffold=len(scaffolding))

            app_files = [f for f in plan.files if f.path not in scaffolding]
            total = len(scaffolding) + len(app_files)

            firestore_service.update_project(uid, project_id, {
                "total_files": total,
                "generated_count": len(scaffolding),
                "current_stage": "generating",
            })

            for idx, file_path in enumerate(plan.generation_order):
                file_to_gen = next((f for f in plan.files if f.path == file_path), None)
                if file_to_gen is None:
                    continue
                if file_path in scaffolding:
                    log.warning("pipeline.template_collision", file_path=file_path)
                    continue
                firestore_service.update_project(uid, project_id, {
                    "current_file": file_path,
                    "generated_count": len(scaffolding) + idx,
                })
                generated_files[file_path] = self.generator.generate_file(
                    file_to_gen, plan, blueprint, generated_files
                )

            firestore_service.update_project(uid, project_id, {
                "generated_files": generated_files,
                "generation_plan": plan.model_dump(),
                "status": ProjectStatus.generated,
                "generated_count": len(generated_files),
                "current_file": None,
            })
            log.info("pipeline.generate.done", project_id=project_id, num_files=len(generated_files))

            # ── STEP 2: Test + Debug loop ────────────────────────────────────
            attempt_count: dict[str, int] = {}
            test_passed = False
            best_effort = False
            infra_warning = False
            warning_msg = ""
            test_results: dict = {}

            for cycle in range(_MAX_TEST_CYCLES):
                firestore_service.set_project_status(uid, project_id, ProjectStatus.testing)
                firestore_service.update_project(uid, project_id, {"current_stage": "testing"})

                test_results = self.tester.run_tests(generated_files, plan)
                log.info(
                    "pipeline.test.done",
                    project_id=project_id,
                    cycle=cycle + 1,
                    status=test_results["status"],
                )

                # Surface install failures to Firestore so the frontend can show useful info.
                if test_results["passed_checks"].get("install") == "failed":
                    install_log = test_results["logs"].get("install", "")
                    install_log_tail = install_log[-2000:]
                    log.warning(
                        "pipeline.install_failed",
                        project_id=project_id,
                        cycle=cycle + 1,
                        install_log=install_log_tail,
                    )
                    if _is_infrastructure_error(install_log):
                        friendly_msg = (
                            "Dependency installation failed in the test environment. "
                            "This is usually a Python/Node version incompatibility with a "
                            "generated dependency, not a code problem. Most generated files "
                            "are still useful — download the ZIP and try installing "
                            "dependencies on your own machine."
                        )
                        firestore_service.update_project(uid, project_id, {
                            "last_error": friendly_msg,
                            "install_error_log": install_log_tail,
                        })
                    else:
                        firestore_service.update_project(uid, project_id, {
                            "last_error": f"Install failed (cycle {cycle + 1}): {install_log[:500]}"
                        })

                # "success" = all checks passed; "skipped" = all checks skipped (missing tools)
                if test_results["status"] in ("success", "skipped"):
                    test_passed = True
                    break

                firestore_service.update_project(uid, project_id, {"current_stage": "debugging"})
                debug_result = self.debugger.debug_and_fix(
                    test_results, generated_files, plan, attempt_count
                )
                log.info(
                    "pipeline.debug.done",
                    project_id=project_id,
                    cycle=cycle + 1,
                    status=debug_result["status"],
                )

                if debug_result["status"] == "fixed":
                    generated_files.update(debug_result["fixed_files"])
                    attempt_count = debug_result["attempt_counts"]
                elif debug_result["status"] == "skipped":
                    reason = debug_result.get("reason", "")
                    if "infrastructure" in reason.lower():
                        log.info("pipeline.debug.skipped_infra_errors", project_id=project_id, cycle=cycle + 1)
                        infra_warning = True
                        warning_msg = (
                            "Dependency installation failed in the test environment. "
                            "Download the ZIP and try installing dependencies on your own machine."
                        )
                    else:
                        log.warning("pipeline.debug.cannot_auto_fix", project_id=project_id, cycle=cycle + 1, reason=reason)
                        best_effort = True
                        _test_summary = ", ".join(
                            f"{c}: {s}" for c, s in test_results.get("passed_checks", {}).items()
                        )
                        warning_msg = (
                            "The generated code did not pass automated tests in our environment"
                            + (f" ({_test_summary})" if _test_summary else "")
                            + ". You can still download the ZIP — the code may "
                            "need small manual fixes to run locally. Check the README for setup."
                        )
                    test_passed = True
                    break
                else:
                    # Debug could not identify or fix the file — package best-effort anyway.
                    log.warning(
                        "pipeline.debug.cannot_fix",
                        project_id=project_id,
                        cycle=cycle + 1,
                        errors=debug_result.get("errors"),
                    )
                    break  # test_passed stays False → best_effort block below

            # Best-effort: always package the ZIP even if tests never passed.
            if not test_passed:
                best_effort = True
                _test_summary = ", ".join(
                    f"{c}: {s}" for c, s in test_results.get("passed_checks", {}).items()
                )
                warning_msg = (
                    "The generated code did not pass automated tests in our environment"
                    + (f" ({_test_summary})" if _test_summary else "")
                    + ". You can still download the ZIP — the code may "
                    "need small manual fixes to run locally. Check the README for setup."
                )
                _test_error_log = ("\n".join(test_results.get("errors", [])))[:5000] or None
                log.warning(
                    "pipeline.tests_failed_packaging_anyway",
                    project_id=project_id,
                    error=warning_msg,
                )
                firestore_service.update_project(uid, project_id, {
                    "generated_files": generated_files,
                    "last_error": warning_msg,
                    "test_error_log": _test_error_log,
                })
            else:
                firestore_service.update_project(uid, project_id, {
                    "generated_files": generated_files,
                    "status": ProjectStatus.tested,
                })

            # ── STEP 3: Verify (skipped in mock mode, infra warnings, or code failures) ─
            if not best_effort and not infra_warning and not settings.mock_ai:
                firestore_service.set_project_status(uid, project_id, ProjectStatus.verifying)
                firestore_service.update_project(uid, project_id, {"current_stage": "verifying"})

                verify_result = self.verifier.verify(generated_files, plan, blueprint)
                log.info("pipeline.verify.done", project_id=project_id, status=verify_result["status"])

                if verify_result["status"] == "fail":
                    raise RuntimeError(f"Verification failed: {verify_result['issues']}")

                firestore_service.set_project_status(uid, project_id, ProjectStatus.verified)
                firestore_service.update_project(uid, project_id, {"current_stage": "verified"})
            else:
                reason = (
                    "mock_mode" if settings.mock_ai
                    else "infra_warning" if infra_warning
                    else "test_failed"
                )
                log.info("pipeline.verify.skipped", project_id=project_id, reason=reason)

            # ── STEP 4: Package → ZIP ────────────────────────────────────────
            firestore_service.set_project_status(uid, project_id, ProjectStatus.packaging)
            firestore_service.update_project(uid, project_id, {"current_stage": "packaging"})

            deploy_result = self.deployer.deploy(uid, project_id, generated_files, plan)
            log.info("pipeline.deploy.done", project_id=project_id, status=deploy_result["status"])

            if deploy_result["status"] != "ready":
                raise RuntimeError(f"Packaging failed: {deploy_result.get('errors', [])}")

            # ready_with_warnings when tests failed for any reason (code or infra).
            if best_effort or infra_warning:
                status_update: dict = {"status": ProjectStatus.ready_with_warnings, "current_stage": "ready"}
                if best_effort:
                    status_update["last_error"] = warning_msg
                firestore_service.update_project(uid, project_id, status_update)
            else:
                firestore_service.update_project(uid, project_id, {"current_stage": "ready"})

            log.info("pipeline.done", project_id=project_id, zip_url=deploy_result["zip_url"])
            return {
                "status": "success",
                "zip_url": deploy_result["zip_url"],
                "generated_files": generated_files,
                "errors": [warning_msg] if (best_effort or infra_warning) else [],
            }

        except Exception as exc:
            log.error("pipeline.error", project_id=project_id, error=str(exc))
            firestore_service.update_project(uid, project_id, {
                "status": ProjectStatus.failed,
                "error_message": str(exc),
            })
            return {
                "status": "error",
                "zip_url": None,
                "generated_files": {},
                "errors": [str(exc)],
            }
