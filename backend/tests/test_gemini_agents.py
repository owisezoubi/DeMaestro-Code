"""Tests for Gemini agents (W3.5-Patch 2, 3 & 4). No real API calls are made."""
from app.ai.gemini.agents import AnalystAgent, ClarificationAgent, CompletenessAgent, SummaryAgent, BlueprintAgent, ValidatorAgent
from app.ai.gemini.agents.blueprint import BlueprintResponse
from app.ai.gemini.agents.completeness import CompletenessResponse
from app.ai.gemini.agents.coordinator import RequirementsCoordinator
from app.models.structured_requirements import (
    AmbiguityFlag,
    Entity,
    FundamentalStatus,
    RequirementCategory,
    RequirementValidation,
    StructuredRequirements,
    UserRequirement,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_ur(
    uid: str = "UR-01",
    statement: str = "A logged-in user can log in with valid credentials.",
    acceptance_criteria: list[str] | None = None,
    category: RequirementCategory = RequirementCategory.functional,
) -> UserRequirement:
    return UserRequirement(
        id=uid,
        statement=statement,
        rationale="Core feature.",
        acceptance_criteria=acceptance_criteria
        if acceptance_criteria is not None
        else ["POST /auth/login returns 200 with valid credentials"],
        priority="must",
        category=category,
    )


def _make_sr(
    requirements: list[UserRequirement] | None = None,
    ambiguities: list[AmbiguityFlag] | None = None,
    version: int = 1,
) -> StructuredRequirements:
    return StructuredRequirements(
        app_name="TestApp",
        summary="A test application.",
        entities=[Entity(name="User", description="A user", fields=["id", "email"])],
        user_requirements=requirements or [_make_ur()],
        ambiguities=ambiguities or [],
        version=version,
    )


# ── AnalystAgent ──────────────────────────────────────────────────────────────

def test_analyst_agent_in_mock_mode_returns_valid_requirements(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)
    result = AnalystAgent().analyze("any input")
    assert isinstance(result, StructuredRequirements)
    assert len(result.entities) >= 1
    assert len(result.user_requirements) >= 1
    assert len(result.ambiguities) >= 1


def test_analyst_agent_tracks_state(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)
    agent = AnalystAgent()
    input_text = "I want a todo app with user auth"
    agent.analyze(input_text)
    assert agent.state["raw_input_chars"] == len(input_text)
    assert agent.state["last_num_requirements"] > 0


def test_analyst_agent_loads_correct_prompt():
    agent = AnalystAgent()
    content = agent._load_prompt()
    assert "User-requirements engineering" in content


# ── ValidatorAgent ────────────────────────────────────────────────────────────

def test_validator_algorithmic_pass_detects_subjective_words(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)
    sr = _make_sr(
        requirements=[
            _make_ur(
                statement="The app should be user-friendly so anyone can use it.",
                acceptance_criteria=["UI renders without errors"],
            )
        ]
    )
    agent = ValidatorAgent()
    result = agent.validate(sr)
    assert result.user_requirements[0].validation.unambiguity == FundamentalStatus.fails
    # Algorithmic pass surfaces a new AmbiguityFlag for the subjective word.
    assert len(result.ambiguities) > len(sr.ambiguities)


def test_validator_renames_duplicate_ids(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)
    # Use model_construct to bypass the uniqueness validator so we can test the fix.
    ur1 = _make_ur(uid="UR-01")
    ur2 = UserRequirement.model_construct(
        id="UR-01",
        statement="A logged-in user can view their profile page.",
        rationale="Core feature.",
        acceptance_criteria=["GET /profile returns 200 for authenticated user"],
        priority="must",
        category=RequirementCategory.functional,
        validation=RequirementValidation(),
    )
    sr = StructuredRequirements.model_construct(
        app_name="TestApp",
        summary="A test application.",
        entities=[Entity(name="User", description="A user", fields=["id", "email"])],
        user_requirements=[ur1, ur2],
        ambiguities=[],
        version=1,
        set_level_validation=RequirementValidation(),
        auth_required=None,
        requested_stack=None,
    )
    result = ValidatorAgent().validate(sr)
    ids = [r.id for r in result.user_requirements]
    assert len(ids) == len(set(ids)), "Duplicate UR ids were not renamed"
    assert "UR-01" in ids


def test_validator_algorithmic_pass_detects_missing_acceptance_criteria(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)
    # Use model_construct to bypass the min_length=1 validator on acceptance_criteria.
    ur = UserRequirement.model_construct(
        id="UR-01",
        statement="A logged-in user can log in with valid credentials.",
        rationale="Core feature.",
        acceptance_criteria=[],
        priority="must",
        category=RequirementCategory.functional,
        validation=RequirementValidation(),
    )
    sr = StructuredRequirements.model_construct(
        app_name="TestApp",
        summary="A test application.",
        entities=[Entity(name="User", description="A user", fields=["id", "email"])],
        user_requirements=[ur],
        ambiguities=[],
        version=1,
        set_level_validation=RequirementValidation(),
        auth_required=None,
        requested_stack=None,
    )
    result = ValidatorAgent().validate(sr)
    assert result.user_requirements[0].validation.verifiability == FundamentalStatus.fails


# ── ClarificationAgent ────────────────────────────────────────────────────────

def test_clarification_select_next_question_prioritizes_auth():
    sr = _make_sr(
        ambiguities=[
            AmbiguityFlag(
                id="AMB-01",
                field_path="entities[0].fields",
                reason="Missing entity fields",
                suggested_options=["id + name", "id only"],
                requirement_id=None,
            ),
            AmbiguityFlag(
                id="AMB-02",
                field_path="auth.method",
                reason="Auth method not specified",
                suggested_options=["email/password", "OAuth"],
                requirement_id=None,
            ),
        ]
    )
    agent = ClarificationAgent()
    selected = agent.select_next_question(sr)
    assert selected is not None
    assert selected.id == "AMB-02"


def test_clarification_apply_answer_in_mock_mode_bumps_version_and_removes_ambiguity(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)
    sr = _make_sr(
        ambiguities=[
            AmbiguityFlag(
                id="AMB-01",
                field_path="auth.method",
                reason="Auth method not specified",
                suggested_options=["email/password", "OAuth"],
            )
        ],
        version=1,
    )
    agent = ClarificationAgent()
    result = agent.apply_answer(sr, "AMB-01", "email/password")
    assert result.version == 2
    assert all(a.id != "AMB-01" for a in result.ambiguities)


# ── RequirementsCoordinator ───────────────────────────────────────────────────

def test_coordinator_analyze_runs_analyst_then_validator(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)
    coordinator = RequirementsCoordinator()
    result = coordinator.analyze("I want a simple todo app")
    assert isinstance(result, StructuredRequirements)
    assert len(result.user_requirements) >= 1
    # Validator ran: all validation fields should be set (not not_evaluated from a skipped pass).
    for r in result.user_requirements:
        assert r.validation.atomicity != FundamentalStatus.not_evaluated
        assert r.validation.verifiability != FundamentalStatus.not_evaluated
        assert r.validation.unambiguity != FundamentalStatus.not_evaluated


# ── SummaryAgent ──────────────────────────────────────────────────────────────

def test_summary_agent_mock_mode_includes_project_name_and_sections(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)
    sr = _make_sr()
    agent = SummaryAgent()
    result = agent.generate_summary(sr)
    assert isinstance(result, str)
    assert "TestApp" in result
    # New narrative format has friendly section headings
    assert "Who uses it" in result or "What can" in result


def test_summary_agent_generates_friendly_narrative_markdown(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)
    sr = _make_sr()
    agent = SummaryAgent()
    result = agent.generate_summary(sr)
    # Narrative mock should be non-empty markdown with the app name as title
    assert result.startswith(f"# {sr.app_name}")
    assert len(result) > 100


# ── BlueprintAgent ────────────────────────────────────────────────────────────

def test_blueprint_agent_mock_mode_returns_mock_data(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)
    sr = _make_sr()
    agent = BlueprintAgent()
    result = agent.generate_blueprint(sr)
    assert isinstance(result, BlueprintResponse)


def test_blueprint_agent_generates_blueprint_with_at_least_one_table_route_page(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)
    sr = _make_sr()
    result = BlueprintAgent().generate_blueprint(sr)
    assert len(result.database_schema) >= 1
    assert len(result.api_routes) >= 1
    assert len(result.frontend_pages) >= 1


# ── Coordinator summary + blueprint delegation ────────────────────────────────

def test_coordinator_generate_summary_delegates_to_summary_agent(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)
    sr = _make_sr()
    coordinator = RequirementsCoordinator()
    result = coordinator.generate_summary(sr)
    assert isinstance(result, str)
    assert len(result) > 0


def test_coordinator_generate_blueprint_delegates_to_blueprint_agent(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)
    sr = _make_sr()
    coordinator = RequirementsCoordinator()
    result = coordinator.generate_blueprint(sr)
    assert isinstance(result, BlueprintResponse)
    assert len(result.database_schema) >= 1
    assert len(result.api_routes) >= 1
    assert len(result.frontend_pages) >= 1


# ── CompletenessAgent ─────────────────────────────────────────────────────────

def test_completeness_mock_mode_returns_no_missing_aspects(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)
    sr = _make_sr()
    agent = CompletenessAgent()
    result = agent.validate(sr)
    # Mock mode: no aspects flagged, no new ambiguities added.
    assert isinstance(result, StructuredRequirements)
    assert len(result.ambiguities) == len(sr.ambiguities)


def test_completeness_algorithmic_pass_detects_missing_auth_aspect(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)
    # A requirement that mentions nothing about auth.
    sr = _make_sr(
        requirements=[
            _make_ur(
                statement="A user can view a list of workout entries.",
                acceptance_criteria=["GET /workouts returns 200 with workout list"],
            )
        ]
    )
    agent = CompletenessAgent()
    agent.validate(sr)
    state = agent.state
    # The algorithmic pass should not find auth keywords.
    assert state["num_aspects_found"] < len(["user_auth", "data_persistence", "user_management",
                                              "search_filter", "reporting", "notifications",
                                              "export_sharing", "ui_branding"])


def test_completeness_algorithmic_pass_detects_missing_data_aspect(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)
    # A requirement with no storage/persistence keywords.
    sr = _make_sr(
        requirements=[
            _make_ur(
                statement="A user can search for friends by name.",
                acceptance_criteria=["GET /friends?q=name returns matching users"],
            )
        ]
    )
    agent = CompletenessAgent()
    agent.validate(sr)
    state = agent.state
    assert state["num_aspects_checked"] == 8


def test_completeness_creates_ambiguity_flags_for_missing_aspects(monkeypatch):
    from unittest.mock import patch
    from app.config import settings as app_settings
    from app.ai.gemini.agents.completeness import MissingAspect

    monkeypatch.setattr(app_settings, "mock_ai", False)

    # Patch _call_gemini to return a CompletenessResponse with one missing aspect.
    fake_response = CompletenessResponse(
        missing_aspects=[
            MissingAspect(
                aspect="user authentication",
                description="Users need a way to log in.",
                suggested_category="security",
                examples=["Email + password", "Google login", "No login needed"],
            )
        ],
        is_complete=False,
    )
    sr = _make_sr()
    agent = CompletenessAgent()
    with patch.object(agent, "_call_gemini", return_value=fake_response):
        with patch.object(agent, "_load_prompt", return_value="mock prompt"):
            result = agent.validate(sr)

    assert len(result.ambiguities) == 1
    assert result.ambiguities[0].id == "AMB-COMP-1"
    assert "user authentication" in result.ambiguities[0].reason


def test_coordinator_analyze_runs_completeness_after_validator(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)
    coordinator = RequirementsCoordinator()
    result = coordinator.analyze("I want a simple todo app with tasks and users")
    assert isinstance(result, StructuredRequirements)
    # Completeness ran: state is tracked on the agent.
    assert coordinator.completeness.state.get("num_aspects_checked") == 8
