"""ModifierAgent — applies a user-requested change to an existing generated app.
Identifies the minimal set of files to touch and produces full replacements."""
import json
import re

import structlog
from anthropic import Anthropic

from app.ai.gemini.agents.blueprint import BlueprintResponse
from app.config import settings, get_agent_model
from app.models.generation_plan import GenerationPlan


class ModifierAgent:
    def __init__(self) -> None:
        self.model = get_agent_model("modifier")
        self.log = structlog.get_logger("ModifierAgent")
        self.token_counter: dict[str, int] = {"input": 0, "output": 0, "calls": 0}

    def modify(
        self,
        change_request: str,
        current_files: dict[str, str],
        plan: GenerationPlan,
        blueprint: BlueprintResponse,
        structured_requirements=None,
    ) -> dict:
        """Identify which 1–5 files need to change and produce new content for each.
        Returns: { status, modified_files: {path: content}, summary, errors }
        """
        self.log.info("modify.start", request_chars=len(change_request))

        if settings.mock_ai:
            return {
                "status": "fixed",
                "modified_files": {},
                "summary": f"Mock: would apply '{change_request[:50]}'",
                "errors": [],
            }

        # Build a concise file index so Claude can pick the right ones without
        # bloating the prompt with every file.
        file_index = []
        for path, content in current_files.items():
            if path.endswith((".py", ".jsx", ".js", ".tsx", ".ts", ".md", ".json")):
                first_line = (content or "").split("\n", 1)[0][:100]
                file_index.append(f"- {path}  ({len(content)} chars)  // {first_line}")

        req_statements = "(omitted)"
        if structured_requirements is not None:
            stmts = [r.statement for r in (structured_requirements.user_requirements or [])]
            if stmts:
                req_statements = "\n".join(f"- {s}" for s in stmts)

        prompt = f"""You are modifying an existing generated app. The user has requested ONE change:

USER REQUEST:
{change_request}

CURRENT REQUIREMENTS (treat as ground truth — your change MUST stay consistent):
{req_statements}

CURRENT FILE INDEX (path  | size | first-line summary):
{chr(10).join(file_index)}

APP CONTEXT:
- Tech stack: {plan.technology_stack}
- Database tables: {[t.name for t in blueprint.database_schema]}
- API routes: {[r.path for r in blueprint.api_routes]}
- Frontend pages: {[p.name for p in blueprint.frontend_pages]}

TASK:
1. Identify the MINIMAL set of files that must change (1–5 files maximum).
2. For each, output the FULL NEW FILE CONTENT (not a diff).
3. Keep all unrelated code intact — do not refactor anything outside the request.
4. Respect the existing import patterns, component styles, and DB conventions.
5. If the request needs a NEW file, include it.
6. Provide a one-sentence summary of what you changed for the user.

Output ONLY valid JSON in this exact shape (no markdown, no commentary):

{{
  "summary": "<one sentence describing the change>",
  "modified_files": {{
    "path/to/file1.jsx": "<full new content>",
    "path/to/file2.py": "<full new content>"
  }}
}}
"""
        client = Anthropic(api_key=settings.anthropic_api_key)
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=16000,
                messages=[{"role": "user", "content": prompt}],
            )
            if hasattr(response, "usage"):
                self.token_counter["input"] += response.usage.input_tokens
                self.token_counter["output"] += response.usage.output_tokens
            self.token_counter["calls"] += 1
            raw = response.content[0].text.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw.strip())

            if getattr(response, "stop_reason", None) == "max_tokens":
                self.log.warning(
                    "modify.truncated_retrying",
                    tail=raw[-200:],
                    length=len(raw),
                )
                retry_prompt = prompt + (
                    "\n\nYour previous attempt was TRUNCATED at the token limit. "
                    "Output the COMPLETE valid JSON with all opened braces closed. "
                    "Do not abbreviate, do not use markdown fences. Output must start "
                    "with `{` and end with `}` and be parseable by json.loads."
                )
                retry = client.messages.create(
                    model=self.model,
                    max_tokens=16000,
                    messages=[{"role": "user", "content": retry_prompt}],
                )
                raw = retry.content[0].text.strip()
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw.strip())

            parsed = json.loads(raw)
            modified = parsed.get("modified_files", {}) or {}
            self.log.info(
                "modify.done",
                num_files=len(modified),
                paths=list(modified.keys()),
            )
            return {
                "status": "fixed",
                "modified_files": modified,
                "summary": parsed.get("summary", "Applied requested change"),
                "errors": [],
            }
        except Exception as exc:
            self.log.error("modify.error", error=str(exc))
            return {
                "status": "error",
                "modified_files": {},
                "summary": "",
                "errors": [str(exc)],
            }
