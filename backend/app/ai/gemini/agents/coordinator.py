"""RequirementsCoordinator — orchestrates Analyst, Validator, and Clarification agents."""
from typing import Optional

import structlog

from app.ai.gemini.agents.analyst import AnalystAgent
from app.ai.gemini.agents.clarification import ClarificationAgent
from app.ai.gemini.agents.validator import ValidatorAgent
from app.models.structured_requirements import AmbiguityFlag, StructuredRequirements


class RequirementsCoordinator:
    def __init__(self) -> None:
        self.analyst = AnalystAgent()
        self.validator = ValidatorAgent()
        self.clarification = ClarificationAgent()
        self.log = structlog.get_logger("RequirementsCoordinator")

    def analyze(self, raw_input: str) -> StructuredRequirements:
        self.log.info("coordinator.analyze.start")
        sr = self.analyst.analyze(raw_input)
        sr = self.validator.validate(sr)
        self.log.info(
            "coordinator.analyze.done",
            num_requirements=len(sr.user_requirements),
            num_ambiguities=len(sr.ambiguities),
        )
        return sr

    def apply_clarification(
        self,
        current: StructuredRequirements,
        ambiguity_id: str,
        answer: str,
    ) -> StructuredRequirements:
        self.log.info("coordinator.apply_clarification.start", ambiguity_id=ambiguity_id)
        updated = self.clarification.apply_answer(current, ambiguity_id, answer)
        updated = self.validator.validate(updated)
        self.log.info(
            "coordinator.apply_clarification.done",
            new_version=updated.version,
            remaining_ambiguities=len(updated.ambiguities),
        )
        return updated

    def next_question(self, sr: StructuredRequirements) -> Optional[AmbiguityFlag]:
        return self.clarification.select_next_question(sr)
