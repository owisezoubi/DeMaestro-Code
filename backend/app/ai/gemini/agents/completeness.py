"""CompletenessAgent — finds gaps in StructuredRequirements that users forgot to mention."""
from typing import Optional

from pydantic import BaseModel

from app.ai.gemini.agents._base import GeminiAgent
from app.models.structured_requirements import (
    AmbiguityFlag,
    RequirementCategory,
    StructuredRequirements,
)

_DOMAIN_ASPECTS = [
    ("user_auth", "user authentication or login"),
    ("data_persistence", "data persistence or storage"),
    ("user_management", "user management or profiles"),
    ("search_filter", "search or filtering"),
    ("reporting", "reporting, analytics, or dashboards"),
    ("notifications", "notifications or alerts"),
    ("export_sharing", "export, sharing, or integrations"),
    ("ui_branding", "UI or design preferences"),
]

_CATEGORY_KEYWORDS: dict[RequirementCategory, list[str]] = {
    RequirementCategory.functional: ["can", "user", "create", "view", "update", "delete", "search"],
    RequirementCategory.data: ["store", "save", "data", "database", "record", "field"],
    RequirementCategory.security: ["auth", "login", "password", "secure", "permission", "role"],
    RequirementCategory.performance: ["fast", "speed", "scale", "response time", "load"],
}

_ASPECT_KEYWORDS: dict[str, list[str]] = {
    "user_auth": ["login", "sign in", "sign up", "register", "auth", "password", "credential", "oauth"],
    "data_persistence": ["save", "store", "database", "persist", "record", "history", "storage"],
    "user_management": ["profile", "account", "settings", "user management", "admin", "role"],
    "search_filter": ["search", "filter", "find", "query", "sort", "browse"],
    "reporting": ["report", "analytics", "dashboard", "chart", "graph", "statistics", "insight"],
    "notifications": ["notif", "alert", "remind", "email", "push", "message"],
    "export_sharing": ["export", "share", "import", "integrat", "download", "upload"],
    "ui_branding": ["brand", "theme", "color", "logo", "design", "style", "mobile"],
}


class MissingAspect(BaseModel):
    aspect: str
    description: str
    suggested_category: str
    examples: list[str]


class CompletenessResponse(BaseModel):
    missing_aspects: list[MissingAspect]
    is_complete: bool


class CompletenessAgent(GeminiAgent):
    agent_name = "CompletenessAgent"
    prompt_name = "completeness"
    model = "gemini-2.5-flash"
    temperature = 0.2

    def validate(self, sr: StructuredRequirements) -> StructuredRequirements:
        sr = sr.model_copy(deep=True)

        # ---- Algorithmic pass ----
        self.log.info("completeness.algorithmic_pass")

        all_text = " ".join(
            r.statement.lower() + " " + r.rationale.lower()
            for r in sr.user_requirements
        )

        # Categories present
        categories_covered = {r.category for r in sr.user_requirements}
        all_categories = {
            RequirementCategory.functional,
            RequirementCategory.data,
            RequirementCategory.security,
        }
        missing_categories = all_categories - categories_covered

        # Domain aspects present
        aspects_covered = []
        aspects_missing = []
        for key, label in _DOMAIN_ASPECTS:
            keywords = _ASPECT_KEYWORDS.get(key, [])
            if any(kw in all_text for kw in keywords):
                aspects_covered.append(key)
            else:
                aspects_missing.append(key)

        gap_count = len(missing_categories) + len(aspects_missing)
        self.log.info(
            "completeness.algorithmic_pass",
            categories_covered=[c.value for c in categories_covered],
            aspects_covered=aspects_covered,
            aspects_missing=aspects_missing,
            gap_count=gap_count,
        )

        if len(categories_covered) < 3:
            self.log.warning(
                "completeness.few_categories",
                count=len(categories_covered),
            )
        if len(aspects_covered) < 5:
            self.log.warning(
                "completeness.few_aspects",
                count=len(aspects_covered),
            )

        # ---- LLM semantic pass ----
        if self.is_mock():
            response = CompletenessResponse(missing_aspects=[], is_complete=True)
        else:
            system_prompt = self._load_prompt()
            response = self._call_gemini(
                system_prompt=system_prompt,
                user_content=sr.model_dump_json(indent=2),
                response_model=CompletenessResponse,
                stage="completeness",
            )

        # ---- Create AmbiguityFlags for missing aspects ----
        existing_amb_ids = {a.id for a in sr.ambiguities}
        counter = 1

        def _next_comp_id() -> str:
            nonlocal counter
            while f"AMB-COMP-{counter}" in existing_amb_ids:
                counter += 1
            new_id = f"AMB-COMP-{counter}"
            existing_amb_ids.add(new_id)
            counter += 1
            return new_id

        for aspect in response.missing_aspects:
            flag = AmbiguityFlag(
                id=_next_comp_id(),
                field_path="set",
                reason=(
                    f"I want to make sure your app is complete. "
                    f"Do you need {aspect.aspect}? {aspect.description}"
                ),
                suggested_options=aspect.examples,
                requirement_id=None,
            )
            sr.ambiguities.append(flag)

        if response.missing_aspects:
            sr.set_level_validation.notes.append(
                f"Completeness check flagged {len(response.missing_aspects)} potentially missing aspect(s)."
            )

        self._set_state(
            num_aspects_checked=len(_DOMAIN_ASPECTS),
            num_aspects_found=len(aspects_covered),
            num_gaps_flagged=len(response.missing_aspects),
        )
        self.log.info(
            "completeness.done",
            num_aspects_found=len(aspects_covered),
            num_gaps_flagged=len(response.missing_aspects),
        )
        return sr

    def process(self, sr: StructuredRequirements) -> StructuredRequirements:
        return self.validate(sr)
