"""RequirementsCoordinator — orchestrates all Gemini agents."""
from typing import Optional

import structlog

from app.ai.gemini.agents.analyst import AnalystAgent
from app.ai.gemini.agents.blueprint import BlueprintAgent, BlueprintResponse
from app.ai.gemini.agents.clarification import ClarificationAgent
from app.ai.gemini.agents.completeness import CompletenessAgent
from app.ai.gemini.agents.summary import SummaryAgent
from app.ai.gemini.agents.validator import ValidatorAgent
from app.models.structured_requirements import AmbiguityFlag, StructuredRequirements


class RequirementsCoordinator:
    def __init__(self) -> None:
        self.analyst = AnalystAgent()
        self.validator = ValidatorAgent()
        self.completeness = CompletenessAgent()
        self.clarification = ClarificationAgent()
        self.summary = SummaryAgent()
        self.blueprint = BlueprintAgent()
        self.log = structlog.get_logger("RequirementsCoordinator")

    def analyze(self, raw_input: str) -> StructuredRequirements:
        self.log.info("coordinator.analyze.start")
        sr = self.analyst.analyze(raw_input)
        sr = self.validator.validate(sr)
        sr = self.completeness.validate(sr)
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
        updated = self.completeness.validate(updated)
        self.log.info(
            "coordinator.apply_clarification.done",
            new_version=updated.version,
            remaining_ambiguities=len(updated.ambiguities),
        )
        return updated

    def next_question(self, sr: StructuredRequirements) -> Optional[AmbiguityFlag]:
        return self.clarification.select_next_question(sr)

    def generate_summary(self, sr: StructuredRequirements) -> str:
        self.log.info("coordinator.generate_summary.start", app_name=sr.app_name)
        result = self.summary.generate_summary(sr)
        self.log.info("coordinator.generate_summary.done", summary_length=len(result))
        return result

    def generate_blueprint(self, sr: StructuredRequirements) -> BlueprintResponse:
        self.log.info("coordinator.generate_blueprint.start", app_name=sr.app_name)
        result = self.blueprint.generate_blueprint(sr)
        self.log.info(
            "coordinator.generate_blueprint.done",
            num_tables=len(result.database_schema),
        )
        return result
