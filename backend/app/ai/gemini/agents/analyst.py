"""AnalystAgent — converts raw user input into a structured requirements document."""
from app.ai.gemini.agents._base import GeminiAgent
from app.ai.gemini.client import _MOCK_SR  # reuse the existing mock fixture
from app.models.structured_requirements import StructuredRequirements


class AnalystAgent(GeminiAgent):
    """Reads raw text and produces a StructuredRequirements document.

    State tracked in self._state:
      - raw_input_chars: length of the analyzed input
      - last_num_requirements: count of requirements produced last call
      - last_num_ambiguities: count of ambiguities flagged last call
    """

    agent_name = "AnalystAgent"
    prompt_name = "analyst"
    model = "gemini-2.5-flash"
    temperature = 0.2

    def analyze(self, raw_input: str) -> StructuredRequirements:
        """Entry point. Converts raw text into a StructuredRequirements."""
        self._set_state(raw_input_chars=len(raw_input))
        self.log.info("analyst.start", input_chars=len(raw_input))

        if self.is_mock():
            self.log.info("analyst.mock")
            result = _MOCK_SR
        else:
            system_prompt = self._load_prompt()
            result = self._call_gemini(
                system_prompt=system_prompt,
                user_content=raw_input,
                response_model=StructuredRequirements,
                stage="analyze",
            )

        self._set_state(
            last_num_requirements=len(result.user_requirements),
            last_num_ambiguities=len(result.ambiguities),
        )
        self.log.info(
            "analyst.done",
            num_entities=len(result.entities),
            num_requirements=len(result.user_requirements),
            num_ambiguities=len(result.ambiguities),
        )
        return result

    def process(self, raw_input: str) -> StructuredRequirements:
        return self.analyze(raw_input)
