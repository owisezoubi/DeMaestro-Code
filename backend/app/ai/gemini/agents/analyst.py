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

    def analyze(self, raw_input: str, project_name: str | None = None) -> StructuredRequirements:
        """Entry point. Converts raw text into a StructuredRequirements.

        `project_name` is the name the user typed into the DeMaestro UI when
        creating the project. It's injected into the Analyst context so
        the LLM can compare it to any name mentioned in the requirements
        text and flag a conflict if they differ.
        """
        self._set_state(raw_input_chars=len(raw_input))
        self.log.info("analyst.start", input_chars=len(raw_input), project_name=project_name)

        if self.is_mock():
            self.log.info("analyst.mock")
            result = _MOCK_SR
        else:
            system_prompt = self._load_prompt()
            preamble = f"PROJECT_NAME: {project_name}\n\n" if project_name else ""
            user_content = f"{preamble}REQUIREMENTS:\n{raw_input}"
            result = self._call_gemini(
                system_prompt=system_prompt,
                user_content=user_content,
                response_model=StructuredRequirements,
                stage="analyze",
            )

        if project_name and (not result.app_name or not result.app_name.strip()):
            result = result.model_copy(update={"app_name": project_name})

        self._set_state(
            last_num_requirements=len(result.user_requirements),
            last_num_ambiguities=len(result.ambiguities),
        )
        self.log.info(
            "analyst.done",
            app_name=result.app_name,
            num_entities=len(result.entities),
            num_requirements=len(result.user_requirements),
            num_ambiguities=len(result.ambiguities),
        )
        return result

    def process(self, raw_input: str, project_name: str | None = None) -> StructuredRequirements:
        return self.analyze(raw_input, project_name=project_name)
