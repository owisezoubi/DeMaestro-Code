"""BlueprintAgent — generates a database/API/frontend blueprint from StructuredRequirements."""
from pydantic import BaseModel

from app.ai.gemini.agents._base import GeminiAgent
from app.models.structured_requirements import StructuredRequirements


class DatabaseTable(BaseModel):
    name: str
    columns: list[dict]
    description: str


class APIRoute(BaseModel):
    method: str
    path: str
    description: str
    request_schema: str
    response_schema: str


class FrontendPage(BaseModel):
    name: str
    route: str
    purpose: str
    key_components: list[str]


class BlueprintResponse(BaseModel):
    database_schema: list[DatabaseTable]
    api_routes: list[APIRoute]
    frontend_pages: list[FrontendPage]
    technology_stack_notes: str


class BlueprintAgent(GeminiAgent):
    agent_name = "BlueprintAgent"
    prompt_name = "blueprint"
    model = "gemini-2.5-flash"
    temperature = 0.2

    def generate_blueprint(self, sr: StructuredRequirements) -> BlueprintResponse:
        self.log.info("blueprint.start", app_name=sr.app_name)

        if self.is_mock():
            result = self._build_mock_blueprint()
        else:
            system_prompt = self._load_prompt()
            result = self._call_gemini(
                system_prompt=system_prompt,
                user_content=sr.model_dump_json(),
                response_model=BlueprintResponse,
                stage="generate_blueprint",
            )

        self._set_state(
            num_tables=len(result.database_schema),
            num_routes=len(result.api_routes),
            num_pages=len(result.frontend_pages),
        )
        self.log.info(
            "blueprint.done",
            num_tables=len(result.database_schema),
            num_routes=len(result.api_routes),
            num_pages=len(result.frontend_pages),
        )
        return result

    def _build_mock_blueprint(self) -> BlueprintResponse:
        return BlueprintResponse(
            database_schema=[
                DatabaseTable(
                    name="Users",
                    columns=[
                        {"name": "id", "type": "uuid", "primary_key": True},
                        {"name": "email", "type": "text", "unique": True},
                        {"name": "created_at", "type": "timestamp"},
                    ],
                    description="Stores authenticated user accounts",
                )
            ],
            api_routes=[
                APIRoute(
                    method="GET",
                    path="/api/users",
                    description="List all users",
                    request_schema="None",
                    response_schema="List of user objects with id and email",
                )
            ],
            frontend_pages=[
                FrontendPage(
                    name="Dashboard",
                    route="/dashboard",
                    purpose="Main entry point for authenticated users",
                    key_components=["UserList", "Header", "NavBar"],
                )
            ],
            technology_stack_notes="React + Vite frontend, FastAPI backend, PostgreSQL database",
        )

    def process(self, sr: StructuredRequirements) -> BlueprintResponse:
        return self.generate_blueprint(sr)
