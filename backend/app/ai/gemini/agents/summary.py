"""SummaryAgent — generates a readable Markdown summary from a StructuredRequirements document."""
from app.ai.gemini.agents._base import GeminiAgent
from app.models.structured_requirements import StructuredRequirements


class SummaryAgent(GeminiAgent):
    agent_name = "SummaryAgent"
    prompt_name = "summary"
    model = "gemini-2.5-flash"
    temperature = 0.3

    def generate_summary(self, sr: StructuredRequirements) -> str:
        self.log.info("summary.start", app_name=sr.app_name, num_reqs=len(sr.user_requirements))

        if self.is_mock():
            result = self._build_mock_summary(sr)
        else:
            system_prompt = self._load_prompt()
            result = self._call_gemini_text(
                system_prompt=system_prompt,
                user_content=sr.model_dump_json(),
                stage="generate_summary",
            )

        self._set_state(summary_length=len(result))
        self.log.info("summary.done", summary_length=len(result))
        return result

    def _build_mock_summary(self, sr: StructuredRequirements) -> str:
        category_counts: dict[str, int] = {}
        for r in sr.user_requirements:
            cat = r.category.value
            category_counts[cat] = category_counts.get(cat, 0) + 1

        lines = [
            f"# {sr.app_name}",
            "",
            sr.summary,
            "",
            f"**Total Requirements:** {len(sr.user_requirements)}",
            "",
            "## Requirements by Category",
            "",
        ]
        for cat, count in sorted(category_counts.items()):
            lines.append(f"- **{cat}**: {count}")
        lines += ["", "## All Requirements", ""]
        for r in sr.user_requirements:
            lines.append(f"- {r.statement}")

        return "\n".join(lines)

    def process(self, sr: StructuredRequirements) -> str:
        return self.generate_summary(sr)
