"""ValidatorAgent — algorithmic + LLM semantic audit of StructuredRequirements."""
import json
import re
from typing import Literal, Optional

from pydantic import BaseModel

from app.ai.gemini.agents._base import GeminiAgent
from app.models.structured_requirements import (
    AmbiguityFlag,
    FundamentalStatus,
    StructuredRequirements,
)

_SUBJECTIVE_WORDS = [
    "fast", "slow", "easy", "easy to use", "user-friendly", "intuitive",
    "quickly", "simple", "modern", "clean", "good",
]


class RequirementIssue(BaseModel):
    requirement_id: str
    fundamental: Literal["atomicity", "unambiguity", "verifiability", "consistency"]
    severity: Literal["error", "warning"]
    description: str
    suggested_fix: Optional[str] = None


class ValidatorResponse(BaseModel):
    issues: list[RequirementIssue]
    is_complete: bool = True  # Gemini may include this; we accept but don't require it


class ValidatorAgent(GeminiAgent):
    agent_name = "ValidatorAgent"
    prompt_name = "validator"
    model = "gemini-2.5-flash"
    temperature = 0.1

    def validate(self, sr: StructuredRequirements) -> StructuredRequirements:
        sr = sr.model_copy(deep=True)

        # Track all existing AMB ids to avoid collisions when adding new ones.
        existing_amb_ids = {a.id for a in sr.ambiguities}
        _counter = [1]

        def _next_val_id() -> str:
            while f"AMB-VAL-{_counter[0]}" in existing_amb_ids:
                _counter[0] += 1
            new_id = f"AMB-VAL-{_counter[0]}"
            existing_amb_ids.add(new_id)
            _counter[0] += 1
            return new_id

        # ---- Algorithmic pass ----
        self.log.info("validator.algorithmic_pass")

        # Uniqueness: rename duplicate UR ids in-place.
        seen_ids: set[str] = set()
        all_ids: set[str] = {r.id for r in sr.user_requirements}
        next_n = len(sr.user_requirements) + 1
        for r in sr.user_requirements:
            if r.id in seen_ids:
                candidate = f"UR-{next_n:02d}"
                while candidate in all_ids or candidate in seen_ids:
                    next_n += 1
                    candidate = f"UR-{next_n:02d}"
                r.id = candidate
                seen_ids.add(candidate)
                all_ids.add(candidate)
                next_n += 1
            else:
                seen_ids.add(r.id)
        sr.set_level_validation.consistency = FundamentalStatus.passes

        num_algorithmic_failures = 0
        for idx, r in enumerate(sr.user_requirements):
            # Atomicity heuristic
            if " and " in r.statement and len(r.statement) > 120:
                r.validation.atomicity = FundamentalStatus.fails
                r.validation.notes.append(
                    "Statement may describe two needs — consider splitting"
                )
                num_algorithmic_failures += 1
            else:
                r.validation.atomicity = FundamentalStatus.passes

            # Verifiability
            if len(r.acceptance_criteria) == 0 or any(
                len(c) < 10 for c in r.acceptance_criteria
            ):
                r.validation.verifiability = FundamentalStatus.fails
                r.validation.notes.append(
                    "Add at least one testable acceptance criterion"
                )
                num_algorithmic_failures += 1
            else:
                r.validation.verifiability = FundamentalStatus.passes

            # Unambiguity — lexical scan
            found_word: Optional[str] = None
            stmt_lower = r.statement.lower()
            for word in _SUBJECTIVE_WORDS:
                if word in stmt_lower:
                    found_word = word
                    break
            if found_word:
                r.validation.unambiguity = FundamentalStatus.fails
                note = f"Replace subjective word '{found_word}' with a measurable criterion"
                r.validation.notes.append(note)
                num_algorithmic_failures += 1
                # Surface as AmbiguityFlag so the user can resolve it.
                sr.ambiguities.append(
                    AmbiguityFlag(
                        id=_next_val_id(),
                        field_path=f"user_requirements[{idx}].statement",
                        reason=note,
                        suggested_options=["Replace with a measurable criterion"],
                        requirement_id=r.id,
                    )
                )
            else:
                r.validation.unambiguity = FundamentalStatus.passes

        # ---- LLM semantic pass ----
        if self.is_mock():
            response = ValidatorResponse(issues=[])
        else:
            try:
                response = self._call_gemini_with_fallback(sr)
            except Exception as exc:
                self.log.warning("validator.semantic_pass.fallback", error=str(exc))
                response = ValidatorResponse(issues=[])

        # Apply LLM issues.
        ur_id_to_idx = {r.id: i for i, r in enumerate(sr.user_requirements)}
        for issue in response.issues:
            # Mark the fundamental on the corresponding requirement.
            if issue.requirement_id in ur_id_to_idx:
                r = sr.user_requirements[ur_id_to_idx[issue.requirement_id]]
                setattr(r.validation, issue.fundamental, FundamentalStatus.fails)
                if issue.description not in r.validation.notes:
                    r.validation.notes.append(issue.description)

            if issue.fundamental == "consistency" and issue.requirement_id == "set":
                sr.set_level_validation.consistency = FundamentalStatus.fails
                if issue.description not in sr.set_level_validation.notes:
                    sr.set_level_validation.notes.append(issue.description)

            if issue.severity == "error":
                if issue.requirement_id in ur_id_to_idx:
                    field_path = (
                        f"user_requirements[{ur_id_to_idx[issue.requirement_id]}].statement"
                    )
                    req_id: Optional[str] = issue.requirement_id
                elif issue.requirement_id == "set":
                    field_path = "set"
                    req_id = None
                else:
                    continue

                sr.ambiguities.append(
                    AmbiguityFlag(
                        id=_next_val_id(),
                        field_path=field_path,
                        reason=issue.description,
                        suggested_options=[
                            issue.suggested_fix or "Provide a more specific statement"
                        ],
                        requirement_id=req_id,
                    )
                )

        self._set_state(
            num_issues_found=len(response.issues),
            num_algorithmic_failures=num_algorithmic_failures,
        )
        self.log.info(
            "validator.done",
            num_issues_found=len(response.issues),
            num_algorithmic_failures=num_algorithmic_failures,
        )
        return sr

    def _call_gemini_with_fallback(self, sr: StructuredRequirements) -> ValidatorResponse:
        """Call Gemini for semantic validation with a two-stage JSON fallback.

        Stage 1: use _call_gemini (structured JSON mode).
        Stage 2: if that fails, call _call_gemini_text and manually strip code fences.
        Both stages raise on unrecoverable failures so the caller can fall back to
        ValidatorResponse(issues=[]).
        """
        system_prompt = self._load_prompt()
        user_content = sr.model_dump_json(indent=2)

        # Stage 1 — structured JSON mode
        try:
            return self._call_gemini(
                system_prompt=system_prompt,
                user_content=user_content,
                response_model=ValidatorResponse,
                stage="validator",
            )
        except (ValueError, Exception) as exc:
            self.log.warning(
                "validator.gemini_json_failed_trying_text", error=str(exc)
            )

        # Stage 2 — plain text + manual parse
        raw = self._call_gemini_text(
            system_prompt=system_prompt,
            user_content=user_content,
            stage="validator_text_fallback",
        )
        raw = raw.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw.strip())

        data = json.loads(raw)  # raises JSONDecodeError if still malformed
        return ValidatorResponse(**data)

    def process(self, sr: StructuredRequirements) -> StructuredRequirements:
        return self.validate(sr)
