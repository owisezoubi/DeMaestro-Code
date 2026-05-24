"""Pydantic v2 models for user-requirements engineering."""
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class RequirementCategory(str, Enum):
    functional = "functional"
    data = "data"
    interface = "interface"
    security = "security"
    performance = "performance"
    constraint = "constraint"


class FundamentalStatus(str, Enum):
    passes = "passes"
    fails = "fails"
    not_evaluated = "not_evaluated"


class RequirementValidation(BaseModel):
    atomicity: FundamentalStatus = FundamentalStatus.not_evaluated
    unambiguity: FundamentalStatus = FundamentalStatus.not_evaluated
    verifiability: FundamentalStatus = FundamentalStatus.not_evaluated
    consistency: FundamentalStatus = FundamentalStatus.not_evaluated
    notes: list[str] = []


class UserRequirement(BaseModel):
    id: str
    statement: str = Field(..., min_length=10)
    rationale: str
    acceptance_criteria: list[str] = Field(..., min_length=1)
    priority: Literal["must", "should", "could"]
    category: RequirementCategory
    validation: RequirementValidation = Field(default_factory=RequirementValidation)


class Entity(BaseModel):
    name: str
    description: str
    fields: list[str]
    relationships: list[str] = []


class AmbiguityFlag(BaseModel):
    id: str
    field_path: str = ""
    reason: str
    suggested_options: list[str] = []
    requirement_id: Optional[str] = None


class StructuredRequirements(BaseModel):
    app_name: str
    summary: str
    entities: list[Entity]
    user_requirements: list[UserRequirement]
    auth_required: Optional[bool] = None
    requested_stack: Optional[str] = None
    ambiguities: list[AmbiguityFlag]
    set_level_validation: RequirementValidation = Field(default_factory=RequirementValidation)
    version: int = 1

    @model_validator(mode="after")
    def at_least_one_entity(self) -> "StructuredRequirements":
        if not self.entities:
            raise ValueError("at least one entity is required")
        return self

    @field_validator("user_requirements", mode="after")
    @classmethod
    def validate_user_requirements(cls, v: list[UserRequirement]) -> list[UserRequirement]:
        if not v:
            raise ValueError("at least one user_requirement is required")
        ids = [r.id for r in v]
        if len(ids) != len(set(ids)):
            raise ValueError("UserRequirement ids must be unique")
        return v

    @field_validator("ambiguities", mode="after")
    @classmethod
    def validate_ambiguities(cls, v: list[AmbiguityFlag]) -> list[AmbiguityFlag]:
        ids = [a.id for a in v]
        if len(ids) != len(set(ids)):
            raise ValueError("AmbiguityFlag ids must be unique")
        return v

    @field_validator("version", mode="after")
    @classmethod
    def validate_version(cls, v: int) -> int:
        if v < 1:
            raise ValueError("version must be >= 1")
        return v


class ClarificationAnswer(BaseModel):
    question_id: str
    answer: str
