"""DebuggerAgent — uses Claude to fix test failures one file at a time."""
import re

import structlog
from anthropic import Anthropic

from app.config import settings
from app.models.generation_plan import GenerationPlan

_INFRASTRUCTURE_ERROR_PATTERNS = [
    "No such file or directory:",
    "command not found",
    "FileNotFoundError",
    "EACCES",
    "ENOENT",
    "Could not connect to",
    "Connection refused",
    "Timeout",
]


def _is_infrastructure_error(msg: str) -> bool:
    return any(p.lower() in msg.lower() for p in _INFRASTRUCTURE_ERROR_PATTERNS)


class DebuggerAgent:
    """Parses test errors and uses Claude to fix them.

    Incremental: fixes one file per call. Hard cap of 3 attempts per file.
    In mock mode: prepends a comment fix without calling Claude.
    """

    MAX_ATTEMPTS_PER_FILE = 3

    def __init__(self) -> None:
        self.model = settings.claude_model
        self.log = structlog.get_logger("DebuggerAgent")

    def debug_and_fix(
        self,
        test_results: dict,
        generated_files: dict[str, str],
        plan: GenerationPlan,
        attempt_count: dict[str, int] | None = None,
    ) -> dict:
        """Analyze test failures and fix the most critical file.

        Returns: { status, fixed_files, attempt_counts, errors }
        """
        if attempt_count is None:
            attempt_count = {}

        errors = test_results.get("errors", [])
        self.log.info("debug_and_fix.start", num_errors=len(errors))

        if not errors:
            return {"status": "success", "fixed_files": {}, "attempt_counts": attempt_count, "errors": []}

        # Filter infrastructure errors — missing tools are not code bugs.
        code_errors = [e for e in errors if not _is_infrastructure_error(e)]
        for e in errors:
            if _is_infrastructure_error(e):
                self.log.info("debug.skipped_infrastructure_error", error=e[:120])

        if not code_errors:
            return {
                "status": "skipped",
                "reason": "All errors are infrastructure, not code",
                "fixed_files": {},
                "attempt_counts": attempt_count,
                "errors": [],
            }

        try:
            file_to_fix = self._extract_file_from_error(code_errors[0])

            if not file_to_fix or file_to_fix not in generated_files:
                self.log.warning("debug_and_fix.cannot_identify_file", error=code_errors[0])
                return {
                    "status": "error",
                    "fixed_files": {},
                    "attempt_counts": attempt_count,
                    "errors": [f"Could not identify file to fix from: {code_errors[0][:120]}"],
                }

            current_attempts = attempt_count.get(file_to_fix, 0)
            if current_attempts >= self.MAX_ATTEMPTS_PER_FILE:
                self.log.warning(
                    "debug_and_fix.max_attempts_reached", file=file_to_fix, attempts=current_attempts
                )
                return {
                    "status": "error",
                    "fixed_files": {},
                    "attempt_counts": attempt_count,
                    "errors": [
                        f"Max {self.MAX_ATTEMPTS_PER_FILE} attempts reached for {file_to_fix}"
                    ],
                }

            original_content = generated_files[file_to_fix]

            if settings.mock_ai:
                fixed_content = self._fix_mock(file_to_fix, original_content, code_errors[0])
            else:
                fixed_content = self._fix_with_claude(
                    file_to_fix, original_content, code_errors[0], test_results
                )

            new_attempt_count = {**attempt_count, file_to_fix: current_attempts + 1}

            self.log.info(
                "debug_and_fix.done",
                fixed_file=file_to_fix,
                attempt=new_attempt_count[file_to_fix],
            )
            return {
                "status": "fixed",
                "fixed_files": {file_to_fix: fixed_content},
                "attempt_counts": new_attempt_count,
                "errors": [],
            }

        except Exception as exc:
            self.log.error("debug_and_fix.error", error=str(exc))
            return {
                "status": "error",
                "fixed_files": {},
                "attempt_counts": attempt_count,
                "errors": [str(exc)],
            }

    # ── helpers ───────────────────────────────────────────────────────────────

    def _extract_file_from_error(self, error_msg: str) -> str | None:
        """Heuristic: extract the first file path from an error message."""
        match = re.search(r"([\w/.-]+\.(?:py|jsx?|tsx?))", error_msg)
        return match.group(1) if match else None

    def _fix_mock(self, file_path: str, original_content: str, error_msg: str) -> str:
        """Mock fix: strip the broken line and prepend a correction comment."""
        short_err = error_msg[:80].replace("\n", " ")
        return f"# FIXED: {short_err}\n{original_content}"

    def _fix_with_claude(
        self,
        file_path: str,
        original_content: str,
        error_msg: str,
        test_results: dict,
    ) -> str:
        """Call Claude Sonnet to produce a minimal fix for the failing file."""
        checks = test_results.get("passed_checks", {})
        prompt = (
            "You are a code debugger. Fix ONE file based on test errors.\n\n"
            f"**File to fix:** {file_path}\n\n"
            f"**Original content:**\n```\n{original_content}\n```\n\n"
            f"**Test error:**\n{error_msg}\n\n"
            "**Test results summary:**\n"
            f"- Install: {checks.get('install')}\n"
            f"- Lint: {checks.get('lint')}\n"
            f"- Typecheck: {checks.get('typecheck')}\n"
            f"- Boot: {checks.get('boot')}\n\n"
            "**Task:**\n"
            "- Identify why the test failed\n"
            "- Fix ONLY the necessary code in this file\n"
            "- Do NOT rewrite the entire file; make minimal changes\n"
            "- Preserve the structure and logic; fix the specific error\n\n"
            "Output ONLY the fixed file content. No explanations, no markdown."
        )

        client = Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
        )

        fixed = response.content[0].text
        # Strip accidental code-fence wrapping
        fixed = re.sub(r"^```\w*\n", "", fixed.strip())
        fixed = re.sub(r"\n```$", "", fixed)

        self.log.info("_fix_with_claude.done", file=file_path, content_length=len(fixed))
        return fixed
