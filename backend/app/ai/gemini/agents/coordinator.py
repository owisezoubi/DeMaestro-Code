"""RequirementsCoordinator — orchestrates all Gemini agents."""
import concurrent.futures
import re
from typing import Optional

import structlog

from app.ai.gemini.agents.analyst import AnalystAgent
from app.ai.gemini.agents.blueprint import BlueprintAgent, BlueprintResponse
from app.ai.gemini.agents.clarification import ClarificationAgent
from app.ai.gemini.agents.completeness import CompletenessAgent
from app.ai.gemini.agents.summary import SummaryAgent
from app.ai.gemini.agents.validator import ValidatorAgent
from app.models.structured_requirements import AmbiguityFlag, StructuredRequirements


def _is_resolved_topic(field_path: str, resolved_topics: list[str]) -> bool:
    """Return True if field_path is covered by a previously resolved topic.

    Matching rule: strip array indices from field_path, then check whether
    the first dot-segment of any resolved topic matches the start of the
    canonical path.  Examples:
      resolved "auth.method"  →  prefix "auth."  →  filters "auth.providers"
      resolved "notifications"  →  prefix "notifications."  →  filters "notifications.email"
    """
    canonical = re.sub(r'\[\d+\]', '', field_path)
    for topic in resolved_topics:
        first_segment = topic.split('.')[0]
        if canonical == topic or canonical == first_segment or canonical.startswith(first_segment + '.'):
            return True
    return False


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
        resolved_topics: list[str] | None = None,
    ) -> StructuredRequirements:
        self.log.info("coordinator.apply_clarification.start", ambiguity_id=ambiguity_id)
        updated = self.clarification.apply_answer(current, ambiguity_id, answer)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_validator = executor.submit(self.validator.validate, updated)
            future_completeness = executor.submit(self.completeness.validate, updated)
            updated = future_validator.result(timeout=60)
            updated = future_completeness.result(timeout=60)

        # Dedup: drop any newly-generated ambiguity whose topic was already resolved.
        if resolved_topics:
            before = len(updated.ambiguities)
            filtered = [
                a for a in updated.ambiguities
                if not _is_resolved_topic(a.field_path, resolved_topics)
            ]
            if len(filtered) < before:
                self.log.info(
                    "coordinator.deduped_ambiguities",
                    removed=before - len(filtered),
                    resolved_topics=resolved_topics,
                )
                updated = updated.model_copy(update={"ambiguities": filtered})

        self.log.info(
            "coordinator.apply_clarification.done",
            new_version=updated.version,
            remaining_ambiguities=len(updated.ambiguities),
        )
        return updated

    def apply_clarifications_batch(
        self,
        current: StructuredRequirements,
        answers: list[dict],
        resolved_topics: list[str] | None = None,
    ) -> StructuredRequirements:
        self.log.info("coordinator.apply_clarifications_batch.start", count=len(answers))
        updated = self.clarification.apply_answers(current, answers)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fv = ex.submit(self.validator.validate, updated)
            fc = ex.submit(self.completeness.validate, updated)
            updated = fv.result(timeout=120)
            updated = fc.result(timeout=120)
        if resolved_topics:
            filtered = [a for a in updated.ambiguities
                        if not _is_resolved_topic(a.field_path, resolved_topics)]
            if len(filtered) < len(updated.ambiguities):
                updated = updated.model_copy(update={"ambiguities": filtered})
        self.log.info("coordinator.apply_clarifications_batch.done",
                      remaining=len(updated.ambiguities))
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
