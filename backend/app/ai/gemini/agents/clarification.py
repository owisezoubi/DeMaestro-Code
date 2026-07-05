"""ClarificationAgent — selects the next question and applies clarification answers."""
import json
from typing import Optional

from app.ai.gemini.agents._base import GeminiAgent
from app.models.structured_requirements import (
    AmbiguityFlag,
    RequirementCategory,
    StructuredRequirements,
)

_AUTH_FIELD_PATHS = {"auth.method", "auth.required"}


class ClarificationAgent(GeminiAgent):
    agent_name = "ClarificationAgent"
    prompt_name = "clarification"
    model = "gemini-2.5-flash"
    temperature = 0.2

    def select_next_question(self, sr: StructuredRequirements) -> Optional[AmbiguityFlag]:
        if not sr.ambiguities:
            return None

        def _priority(ambi: AmbiguityFlag) -> int:
            # Highest: auth fields or security-category requirement.
            if ambi.field_path in _AUTH_FIELD_PATHS or ambi.field_path.startswith("auth"):
                return 0
            req = None
            if ambi.requirement_id:
                req = next(
                    (r for r in sr.user_requirements if r.id == ambi.requirement_id),
                    None,
                )
            if req and req.category == RequirementCategory.security:
                return 0
            # Tied to a functional or data requirement.
            if req and req.category in (RequirementCategory.functional, RequirementCategory.data):
                return 1
            # Set-level (not tied to any requirement).
            if ambi.requirement_id is None:
                return 2
            return 3

        return min(sr.ambiguities, key=_priority)

    def apply_answer(
        self,
        current: StructuredRequirements,
        ambiguity_id: str,
        answer: str,
    ) -> StructuredRequirements:
        if self.is_mock():
            result = current.model_copy(
                update={
                    "version": current.version + 1,
                    "ambiguities": [a for a in current.ambiguities if a.id != ambiguity_id],
                }
            )
        else:
            system_prompt = self._load_prompt()
            user_content = (
                f"Current requirements:\n{current.model_dump_json(indent=2)}\n\n"
                f"Clarification answers:\n"
                f"{json.dumps([{'question_id': ambiguity_id, 'answer': answer}], indent=2)}"
            )
            result = self._call_gemini(
                system_prompt=system_prompt,
                user_content=user_content,
                response_model=StructuredRequirements,
                stage="apply_clarification",
            )

        # Post-merge guarantee: if the clarified ambiguity was about the
        # app name, overwrite sr.app_name directly so downstream stages
        # (deployment slug, ZIP filename, Vercel URL) see the user's choice
        # regardless of how the LLM reinterpreted the answer.
        resolved_ambi = next(
            (a for a in current.ambiguities if a.id == ambiguity_id),
            None,
        )
        if resolved_ambi and getattr(resolved_ambi, "field_path", None) == "app_name":
            result = result.model_copy(update={"app_name": answer.strip()})
            self.log.info(
                "clarification.app_name_updated",
                ambiguity_id=ambiguity_id,
                new_name=answer.strip(),
            )

        self._set_state(
            last_answered_ambiguity=ambiguity_id,
            last_version_after=result.version,
        )
        return result

    def apply_answers(
        self,
        current: StructuredRequirements,
        answers: list[dict],
    ) -> StructuredRequirements:
        """Apply multiple clarification answers in one pass.
        answers: [{"ambiguity_id": str, "answer": str}, ...]"""
        if self.is_mock():
            answered = {a["ambiguity_id"] for a in answers}
            result = current.model_copy(update={
                "version": current.version + 1,
                "ambiguities": [a for a in current.ambiguities if a.id not in answered],
            })
        else:
            system_prompt = self._load_prompt()
            user_content = (
                f"Current requirements:\n{current.model_dump_json(indent=2)}\n\n"
                f"Clarification answers:\n"
                f"{json.dumps([{'question_id': a['ambiguity_id'], 'answer': a['answer']} for a in answers], indent=2)}"
            )
            result = self._call_gemini(
                system_prompt=system_prompt,
                user_content=user_content,
                response_model=StructuredRequirements,
                stage="apply_clarifications_batch",
            )
        self._set_state(last_answered_count=len(answers), last_version_after=result.version)
        return result

    def process(
        self,
        sr: StructuredRequirements,
        ambiguity_id: str,
        answer: str,
    ) -> StructuredRequirements:
        return self.apply_answer(sr, ambiguity_id, answer)
