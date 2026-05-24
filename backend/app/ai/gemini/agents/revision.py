"""RevisionAgent — incrementally integrates user edits into approved requirements."""
import json

from app.ai.gemini.agents._base import GeminiAgent
from app.models.structured_requirements import (
    RequirementCategory,
    StructuredRequirements,
    UserRequirement,
)


class RevisionAgent(GeminiAgent):
    agent_name = "RevisionAgent"
    prompt_name = "revision"
    model = "gemini-2.5-flash"
    temperature = 0.2

    def integrate(
        self, current: StructuredRequirements, new_requirements: list[str], edited_answers: list[dict]
    ) -> StructuredRequirements:
        if self.is_mock():
            base = len(current.user_requirements)
            extra = []
            for i, text in enumerate(new_requirements):
                stmt = text if len(text) >= 10 else f"{text} (user-added)"
                extra.append(UserRequirement(
                    id=f"UR-{base + i + 1:02d}",
                    statement=stmt,
                    rationale="Added by the user during review.",
                    acceptance_criteria=["The described behavior works."],
                    priority="should",
                    category=RequirementCategory.functional,
                ))
            return current.model_copy(update={
                "version": current.version + 1,
                "user_requirements": current.user_requirements + extra,
                "ambiguities": [],
            })
        system_prompt = self._load_prompt()
        user_content = (
            f"CURRENT (already-approved) requirements:\n{current.model_dump_json(indent=2)}\n\n"
            f"EDITED answers to earlier questions:\n{json.dumps(edited_answers, indent=2)}\n\n"
            f"NEW requirements the user added:\n{json.dumps(new_requirements, indent=2)}"
        )
        return self._call_gemini(
            system_prompt=system_prompt,
            user_content=user_content,
            response_model=StructuredRequirements,
            stage="integrate_revision",
        )

    def process(
        self, current: StructuredRequirements, new_requirements: list[str], edited_answers: list[dict]
    ) -> StructuredRequirements:
        return self.integrate(current, new_requirements, edited_answers)
