"""Tests for Gemini agents (W3.5-Patch 2 & 3). No real API calls are made."""
from app.ai.gemini.agents import AnalystAgent, ClarificationAgent, ValidatorAgent
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
