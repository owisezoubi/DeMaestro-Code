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
    # One of: "auth_gate" | "public_home" | "public_landing_with_login".
    # "auth_gate"  — every meaningful page requires login (Planty, dashboards).
    # "public_home" — no accounts or fully public content (galleries, catalogs).
    # "public_landing_with_login" — public landing + optional user accounts.
    # Defaults to "auth_gate" for backward compat with old blueprints.
    landing_strategy: str = "auth_gate"


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
                        {"name": "name", "type": "text"},
                        {"name": "created_at", "type": "timestamp"},
                    ],
                    description="Stores user accounts and profile information",
                ),
                DatabaseTable(
                    name="Workouts",
                    columns=[
                        {"name": "id", "type": "uuid", "primary_key": True},
                        {"name": "user_id", "type": "uuid", "foreign_key": "users.id"},
                        {"name": "type", "type": "text"},
                        {"name": "duration_minutes", "type": "integer"},
                        {"name": "calories_burned", "type": "integer"},
                        {"name": "date", "type": "date"},
                        {"name": "notes", "type": "text"},
                    ],
                    description="Records each individual workout session",
                ),
                DatabaseTable(
                    name="Goals",
                    columns=[
                        {"name": "id", "type": "uuid", "primary_key": True},
                        {"name": "user_id", "type": "uuid", "foreign_key": "users.id"},
                        {"name": "title", "type": "text"},
                        {"name": "target_date", "type": "date"},
                        {"name": "completed", "type": "boolean"},
                    ],
                    description="Tracks fitness goals set by users",
                ),
                DatabaseTable(
                    name="Friendships",
                    columns=[
                        {"name": "id", "type": "uuid", "primary_key": True},
                        {"name": "user_id", "type": "uuid", "foreign_key": "users.id"},
                        {"name": "friend_id", "type": "uuid", "foreign_key": "users.id"},
                        {"name": "created_at", "type": "timestamp"},
                    ],
                    description="Connects users who follow each other's progress",
                ),
            ],
            api_routes=[
                APIRoute(
                    method="POST",
                    path="/api/auth/register",
                    description="Create a new account",
                    request_schema="email, name, password",
                    response_schema="user object with token",
                ),
                APIRoute(
                    method="POST",
                    path="/api/auth/login",
                    description="Sign in to your account",
                    request_schema="email, password",
                    response_schema="user object with token",
                ),
                APIRoute(
                    method="GET",
                    path="/api/workouts",
                    description="View all past workouts",
                    request_schema="None",
                    response_schema="List of workout objects",
                ),
                APIRoute(
                    method="POST",
                    path="/api/workouts",
                    description="Log a new workout",
                    request_schema="type, duration_minutes, calories_burned, date, notes",
                    response_schema="Created workout object",
                ),
                APIRoute(
                    method="PUT",
                    path="/api/workouts/{id}",
                    description="Edit a workout's details",
                    request_schema="Any workout fields to update",
                    response_schema="Updated workout object",
                ),
                APIRoute(
                    method="DELETE",
                    path="/api/workouts/{id}",
                    description="Delete a workout entry",
                    request_schema="None",
                    response_schema="Success confirmation",
                ),
                APIRoute(
                    method="GET",
                    path="/api/goals",
                    description="View all fitness goals",
                    request_schema="None",
                    response_schema="List of goal objects",
                ),
                APIRoute(
                    method="POST",
                    path="/api/goals",
                    description="Set a new fitness goal",
                    request_schema="title, target_date",
                    response_schema="Created goal object",
                ),
                APIRoute(
                    method="GET",
                    path="/api/friends",
                    description="View friends and their recent activity",
                    request_schema="None",
                    response_schema="List of friend profiles with stats",
                ),
            ],
            frontend_pages=[
                FrontendPage(
                    name="Dashboard",
                    route="/dashboard",
                    purpose="Quick overview of recent workouts and progress stats",
                    key_components=["StatsCard", "RecentWorkouts", "NavBar"],
                ),
                FrontendPage(
                    name="Log Workout",
                    route="/workouts/new",
                    purpose="Simple form to record a new workout session",
                    key_components=["WorkoutForm", "TypePicker", "DurationInput"],
                ),
                FrontendPage(
                    name="Progress Charts",
                    route="/progress",
                    purpose="Visual graphs showing fitness improvements over time",
                    key_components=["LineChart", "CaloriesChart", "DateRangePicker"],
                ),
                FrontendPage(
                    name="My Goals",
                    route="/goals",
                    purpose="Set and track personal fitness goals",
                    key_components=["GoalCard", "GoalForm", "ProgressBar"],
                ),
                FrontendPage(
                    name="Friends",
                    route="/friends",
                    purpose="See friends' recent workouts and compare progress",
                    key_components=["FriendList", "ActivityFeed", "AddFriendSearch"],
                ),
                FrontendPage(
                    name="Workout History",
                    route="/workouts",
                    purpose="Browse and search all past workouts",
                    key_components=["WorkoutTable", "SearchBar", "FilterPanel"],
                ),
            ],
            technology_stack_notes=(
                "React + Vite frontend, FastAPI backend, "
                "SQLite database by default (Postgres via DATABASE_URL)"
            ),
        )

    def process(self, sr: StructuredRequirements) -> BlueprintResponse:
        return self.generate_blueprint(sr)
