"""DebuggerAgent — uses Claude to fix test failures one file at a time."""
import json
import re
from typing import Optional

import structlog
from anthropic import Anthropic

from app.config import settings
from app.models.generation_plan import GenerationPlan

_INFRASTRUCTURE_ERROR_PATTERNS = [
    # Missing tools / filesystem / network
    "No such file or directory:",
    "command not found",
    "FileNotFoundError",
    "EACCES",
    "ENOENT",
    "Could not connect to",
    "Connection refused",
    "Timeout",
    # pip / wheel-build failures (environment, not code)
    "Failed building wheel for",
    "Could not build wheels for",
    "ERROR: Failed building wheel",
    "error: failed-wheel-build",
    "error: command '/usr/bin/clang' failed",
    "error: command 'gcc' failed",
    "Microsoft Visual C++ 14.0 or greater is required",
    "Requirement already satisfied:",
    "ERROR: Could not find a version that satisfies",
    "ERROR: No matching distribution found for",
    # npm dependency failures
    "npm ERR! code",
    "npm error code",
    "ERESOLVE unable to resolve dependency tree",
]

# Primary: matches flake8/ruff output lines like `./backend/app/auth.py:225:16: W292 ...`
_LINT_LINE_RE = re.compile(r"^\s*\./?(?P<path>[\w./\\-]+\.[a-z]+):\d+:\d+:")

# Secondary: matches Python traceback lines `  File "/tmp/.../backend/app/models.py", line 42`
_TRACEBACK_FILE_RE = re.compile(r'File\s+"(?P<path>[^"]+\.py)",\s+line\s+\d+')

# Fallback: matches `path/to/file.py:` references regardless of what follows the colon.
# Covers both ast.parse-style "file.py: SyntaxError" and "file.py:42" formats.
_FILE_REF_RE = re.compile(r"(?P<path>[\w./\\-]+\.(?:py|jsx?|tsx?)):")

# Paths that belong to installed libraries, not generated user code.
_LIBRARY_SKIP_PREFIXES = (".testenv/", "site-packages/", ".venv/", "/venv/", "<frozen", "lib/python")

# Patterns that indicate a real (non-infrastructure) code error worth attempting a blind fix.
_SUBSTANTIVE_ERROR_PATTERNS = ["Traceback", "Error:", "Exception:", "sqlalchemy."]


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
        """Analyze test failures and fix all identifiable files.

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
            full_error_text = "\n".join(code_errors)
            files_to_fix = self._extract_files_from_error(full_error_text)

            if not files_to_fix:
                # Attempt a blind fix when no file paths are extractable but the error is real code.
                if any(p in full_error_text for p in _SUBSTANTIVE_ERROR_PATTERNS):
                    self.log.info("debug.attempting_blind_fix", error_preview=full_error_text[:100])
                    blind = self._blind_fix_attempt(full_error_text, generated_files)
                    if blind and blind.get("file_path") and blind["file_path"] in generated_files:
                        fp = blind["file_path"]
                        new_counts = dict(attempt_count)
                        new_counts[fp] = new_counts.get(fp, 0) + 1
                        self.log.info("debug.blind_fix.applied", file=fp)
                        return {
                            "status": "fixed",
                            "fixed_files": {fp: blind["fixed_content"]},
                            "attempt_counts": new_counts,
                            "errors": [],
                        }
                self.log.warning("debug.cannot_identify_file", error=full_error_text[:200])
                return {
                    "status": "skipped",
                    "reason": "Could not auto-fix; surfaced to user",
                    "fixed_files": {},
                    "attempt_counts": attempt_count,
                    "errors": [f"Could not identify any file to fix from: {full_error_text[:120]}"],
                }

            fixed_files: dict[str, str] = {}
            new_attempt_count = dict(attempt_count)

            for file_to_fix in files_to_fix:
                if file_to_fix not in generated_files:
                    self.log.warning("debug_and_fix.file_not_in_generated", file=file_to_fix)
                    continue

                current_attempts = new_attempt_count.get(file_to_fix, 0)
                if current_attempts >= self.MAX_ATTEMPTS_PER_FILE:
                    self.log.warning(
                        "debug_and_fix.max_attempts_reached",
                        file=file_to_fix,
                        attempts=current_attempts,
                    )
                    continue

                # Find the most relevant error line for this specific file.
                error_for_file = next(
                    (e for e in code_errors if file_to_fix in e), code_errors[0]
                )
                original_content = generated_files[file_to_fix]

                if settings.mock_ai:
                    fixed_content = self._fix_mock(file_to_fix, original_content, error_for_file)
                else:
                    fixed_content = self._fix_with_claude(
                        file_to_fix, original_content, error_for_file, test_results
                    )

                fixed_files[file_to_fix] = fixed_content
                new_attempt_count[file_to_fix] = current_attempts + 1
                self.log.info(
                    "debug_and_fix.fixed_file",
                    file=file_to_fix,
                    attempt=new_attempt_count[file_to_fix],
                )

            if not fixed_files:
                return {
                    "status": "error",
                    "fixed_files": {},
                    "attempt_counts": new_attempt_count,
                    "errors": [
                        f"Max {self.MAX_ATTEMPTS_PER_FILE} attempts reached for all identified files"
                    ],
                }

            self.log.info("debug_and_fix.done", num_fixed=len(fixed_files))
            return {
                "status": "fixed",
                "fixed_files": fixed_files,
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

    def _extract_files_from_error(self, error_message: str) -> list[str]:
        """Extract unique generated-project file paths from lint output OR Python traceback.

        Filters out library paths (.testenv/, site-packages/, .venv/) that aren't user code.
        Normalises absolute tmpdir paths to relative project paths (backend/… or frontend/…).
        """
        files: set[str] = set()
        for line in error_message.splitlines():
            m = _LINT_LINE_RE.match(line) or _TRACEBACK_FILE_RE.search(line)
            if not m:
                m = _FILE_REF_RE.search(line)
            if not m:
                continue
            path = m.group("path")
            # Skip library / interpreter internals
            if any(skip in path for skip in _LIBRARY_SKIP_PREFIXES):
                continue
            # Normalise absolute tmpdir paths to relative project paths.
            # e.g. /private/var/.../demaestro_test_xxx/backend/app/models.py → backend/app/models.py
            for prefix in ("backend/", "frontend/"):
                idx = path.find(prefix)
                if idx >= 0:
                    path = path[idx:]
                    break
            else:
                path = path.lstrip("./")
            if path:
                files.add(path)
        return sorted(files)

    def _fix_mock(self, file_path: str, original_content: str, error_msg: str) -> str:
        """Mock fix: strip the broken line and prepend a correction comment."""
        short_err = error_msg[:80].replace("\n", " ")
        return f"# FIXED: {short_err}\n{original_content}"

    def _blind_fix_attempt(
        self, error: str, all_files: dict[str, str]
    ) -> Optional[dict]:
        """When file extraction fails but the error is real, ask Claude to identify and fix.

        Returns {"file_path": "...", "fixed_content": "..."} or None if Claude cannot help.
        Capped at one call to avoid runaway; mock mode returns None immediately.
        """
        if settings.mock_ai:
            return None

        prompt = (
            "Below is a build error from a generated Python+FastAPI project. "
            "Identify which file most likely contains the bug, and return a fix.\n\n"
            f"Error:\n{error[:3000]}\n\n"
            f"Available files:\n{json.dumps(list(all_files.keys()), indent=2)}\n\n"
            'Respond with JSON: {"file_path": "...", "fixed_content": "..."} '
            'or {"file_path": null} if you cannot determine the cause.'
        )
        try:
            client = Anthropic(api_key=settings.anthropic_api_key)
            response = client.messages.create(
                model=self.model,
                max_tokens=8000,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            text = re.sub(r"^```\w*\n", "", text)
            text = re.sub(r"\n```$", "", text)
            result = json.loads(text)
            if result.get("file_path"):
                self.log.info("_blind_fix_attempt.success", file=result["file_path"])
                return result
            return None
        except Exception as exc:
            self.log.warning("_blind_fix_attempt.failed", error=str(exc))
            return None

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
