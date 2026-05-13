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
        return f"""# {sr.app_name}: Your Personal Fitness Companion

FitTrack is a simple app that helps you track your workouts, see how you're improving over time, and stay motivated alongside friends — all in one place.

## Who uses it?

- People who want to get fit or stay active
- Athletes training for a specific goal
- Friends who like to motivate each other

## What can users do?

- User can log a new workout — just pick the type, duration, and how hard it felt
- User can see colorful charts showing how their fitness has improved week by week
- User can compare their progress with friends to stay motivated
- User can set personal fitness goals and get reminders to keep on track
- User can export their workout history to share with a personal trainer
- User can browse their full workout history to see how far they've come

## Why does this matter?

Sticking to a fitness routine is much easier when you can actually see your progress and have friends cheering you on. {sr.app_name} makes all of that effortless and even a little fun."""

    def process(self, sr: StructuredRequirements) -> str:
        return self.generate_summary(sr)
