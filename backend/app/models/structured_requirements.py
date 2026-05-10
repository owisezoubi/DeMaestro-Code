"""Pydantic v2 models for the structured requirements contract (W3-A)."""
from typing import Literal, Optional

from pydantic import BaseModel, field_validator, model_validator


class Entity(BaseModel):
    name: str
    description: str
    fields: list[str]
    relationships: list[str] = []


class FeatureRequirement(BaseModel):
    id: str
    description: str
    priority: Literal["must", "should", "could"]


class AmbiguityFlag(BaseModel):
    id: str
    field_path: str
    reason: str
    suggested_options: list[str] = []


class StructuredRequirements(BaseModel):
    app_name: str
    summary: str
    entities: list[Entity]
    features: list[FeatureRequirement]
    auth_required: Optional[bool] = None
    requested_stack: Optional[str] = None
    ambiguities: list[AmbiguityFlag]
    version: int = 1

    @field_validator("entities", mode="after")
    @classmethod
    def at_least_one_entity(cls, v: list[Entity]) -> list[Entity]:
        if not v:
            raise ValueError("at least one entity is required")
        return v

    @field_validator("features", mode="after")
    @classmethod
    def validate_features(cls, v: list[FeatureRequirement]) -> list[FeatureRequirement]:
        if not v:
            raise ValueError("at least one feature is required")
        ids = [f.id for f in v]
        if len(ids) != len(set(ids)):
            raise ValueError("FeatureRequirement ids must be unique")
        return v

    @field_validator("ambiguities", mode="after")
    @classmethod
    def validate_ambiguities(cls, v: list[AmbiguityFlag]) -> list[AmbiguityFlag]:
        ids = [a.id for a in v]
        if len(ids) != len(set(ids)):
            raise ValueError("AmbiguityFlag ids must be unique")
        return v


class ClarificationAnswer(BaseModel):
    question_id: str
    answer: str
