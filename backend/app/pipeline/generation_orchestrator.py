"""Generation orchestrator — full state machine (W5-P2).

State machine:
  approved → generating → generated → testing → tested
           → verifying → verified → deploying → deployed
  (on any failure) → failed

The debug loop sits inside testing: if tests fail, DebuggerAgent fixes one file
and we retest, up to 5 cycles / 3 attempts per file.
"""
import structlog

from app.ai.claude.agents.architect import ArchitectAgent
from app.ai.claude.agents.debugger import DebuggerAgent
from app.ai.claude.agents.deployer import DeployerAgent
from app.ai.claude.agents.generator import GeneratorAgent
from app.ai.claude.agents.tester import TesterAgent
from app.ai.claude.agents.verifier import VerifierAgent
from app.ai.gemini.agents.blueprint import BlueprintResponse
from app.models.project import ProjectStatus
from app.services import firestore_service

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

            generated_files: dict[str, str] = {}
            for file_path in plan.generation_order:
                file_to_gen = next((f for f in plan.files if f.path == file_path), None)
                if file_to_gen is None:
                    log.warning("generation.file_not_in_plan", file_path=file_path)
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

        Returns: { status, deployment_url, generated_files, errors }
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

            firestore_service.update_project(uid, project_id, {
                "total_files": len(plan.files),
                "generated_count": 0,
                "current_stage": "generating",
            })

            generated_files: dict[str, str] = {}
            for idx, file_path in enumerate(plan.generation_order):
                file_to_gen = next((f for f in plan.files if f.path == file_path), None)
                if file_to_gen is None:
                    continue
                firestore_service.update_project(uid, project_id, {
                    "current_file": file_path,
                    "generated_count": idx,
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

                if test_results["status"] == "success":
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
                else:
                    raise RuntimeError(f"Debug failed after {cycle + 1} cycles: {debug_result['errors']}")

            if not test_passed:
                raise RuntimeError(f"Tests still failing after {_MAX_TEST_CYCLES} debug cycles")

            firestore_service.update_project(uid, project_id, {
                "generated_files": generated_files,
                "status": ProjectStatus.tested,
            })

            # ── STEP 3: Verify ───────────────────────────────────────────────
            firestore_service.set_project_status(uid, project_id, ProjectStatus.verifying)
            firestore_service.update_project(uid, project_id, {"current_stage": "verifying"})

            verify_result = self.verifier.verify(generated_files, plan, blueprint)
            log.info("pipeline.verify.done", project_id=project_id, status=verify_result["status"])

            if verify_result["status"] == "fail":
                raise RuntimeError(f"Verification failed: {verify_result['issues']}")

            firestore_service.set_project_status(uid, project_id, ProjectStatus.verified)
            firestore_service.update_project(uid, project_id, {"current_stage": "verified"})

            # ── STEP 4: Deploy ───────────────────────────────────────────────
            firestore_service.set_project_status(uid, project_id, ProjectStatus.deploying)

            deploy_result = self.deployer.deploy(uid, project_id, generated_files, plan)
            log.info("pipeline.deploy.done", project_id=project_id, status=deploy_result["status"])

            if deploy_result["status"] != "success":
                raise RuntimeError(f"Deployment failed: {deploy_result['errors']}")

            firestore_service.update_project(uid, project_id, {
                "status": ProjectStatus.deployed,
                "deployment_url": deploy_result["deployment_url"],
                "deployment_id": deploy_result["deployment_id"],
                "current_stage": "deployed",
            })

            log.info("pipeline.done", project_id=project_id, deployment_url=deploy_result["deployment_url"])
            return {
                "status": "success",
                "deployment_url": deploy_result["deployment_url"],
                "generated_files": generated_files,
                "errors": [],
            }

        except Exception as exc:
            log.error("pipeline.error", project_id=project_id, error=str(exc))
            firestore_service.update_project(uid, project_id, {
                "status": ProjectStatus.failed,
                "error_message": str(exc),
            })
            return {
                "status": "error",
                "deployment_url": None,
                "generated_files": {},
                "errors": [str(exc)],
            }
