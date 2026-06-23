"""VerifierAgent — static checks on generated code. Pure Python, no LLM."""
import structlog

from app.ai.gemini.agents.blueprint import BlueprintResponse
from app.models.generation_plan import GenerationPlan


def _file_needs_fastapi_import(file_path: str, content: str) -> bool:
    """Whether a Python file in routes/ should be required to import from fastapi."""
    if len(content.strip()) < 50:
        return False
    if file_path.endswith("__init__.py"):
        return False
    route_patterns = ["@router.", "@app.", "APIRouter", "app = FastAPI("]
    return any(p in content for p in route_patterns)


class VerifierAgent:
    """Verify generated code against the blueprint: routes, imports, DB schema."""

    def __init__(self) -> None:
        self.log = structlog.get_logger("VerifierAgent")

    def verify(
        self,
        generated_files: dict[str, str],
        plan: GenerationPlan,
        blueprint: BlueprintResponse,
    ) -> dict:
        """Run static verification checks.

        Returns: { status, issues }
        """
        self.log.info("verify.start", num_files=len(generated_files))

        issues: list[str] = []

        try:
            route_issues, missing_routes = self._verify_routes_exist(generated_files, blueprint)
            issues.extend(route_issues)
            issues.extend(self._verify_imports(generated_files, plan.technology_stack))
            issues.extend(self._verify_db_schema(generated_files, blueprint))

            status = "pass" if not issues else "fail"
            self.log.info("verify.done", status=status, num_issues=len(issues))
            return {
                "status": status,
                "issues": issues,
                "missing_routes": missing_routes,
            }

        except Exception as exc:
            self.log.error("verify.error", error=str(exc))
            return {"status": "error", "issues": [str(exc)], "missing_routes": []}

    # ── individual checks ─────────────────────────────────────────────────────

    def _verify_routes_exist(
        self, files: dict[str, str], blueprint: BlueprintResponse
    ) -> tuple[list[str], list[dict]]:
        """Check every blueprint API route has a matching decorator or path string.

        Returns (issues_strings, missing_routes_structured).
        """
        issues: list[str] = []
        missing_routes: list[dict] = []
        all_content = "\n".join(files.values())

        for route in blueprint.api_routes:
            method = route.method.lower()
            path = route.path
            # Match @router.<method> OR the quoted path string
            if (
                f"@router.{method}" not in all_content
                and f'"{path}"' not in all_content
                and f"'{path}'" not in all_content
            ):
                issues.append(f"Route {route.method.upper()} {path} not found in generated code")
                missing_routes.append({"method": route.method.upper(), "path": path})

        return issues, missing_routes

    def _verify_imports(
        self, files: dict[str, str], stack: str
    ) -> list[str]:
        """Check that route files contain required framework imports."""
        issues = []

        for file_path, content in files.items():
            if "python" in stack and file_path.endswith(".py") and "routes" in file_path:
                if _file_needs_fastapi_import(file_path, content):
                    if "from fastapi import" not in content and "import fastapi" not in content.lower():
                        issues.append(f"{file_path}: missing FastAPI import")

            elif "node" in stack and (
                file_path.endswith(".jsx") or file_path.endswith(".tsx")
            ):
                if "import" not in content:
                    issues.append(f"{file_path}: missing React import")

        return issues

    def _verify_db_schema(
        self, files: dict[str, str], blueprint: BlueprintResponse
    ) -> list[str]:
        """Check that at least one model file references a blueprint table name."""
        if not blueprint.database_schema:
            return []

        for file_path, content in files.items():
            if "models" in file_path:
                if any(table.name in content for table in blueprint.database_schema):
                    return []  # found at least one match

        return ["No database models found matching blueprint schema tables"]
