"""Checklist schema and validation for checklist-driven generation.

Five atomic item types: model, route, page, seed, style.
Each item is an independently verifiable unit of app structure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

import jsonschema


# ============================================================
# Dataclasses
# ============================================================

@dataclass
class ModelItem:
    id: str
    type: Literal["model"]
    table_name: str
    class_name: str
    columns: list[dict]
    relationships: list[dict] = field(default_factory=list)


@dataclass
class RouteItem:
    id: str
    type: Literal["route"]
    method: str
    path: str
    auth_required: bool
    request_body: Optional[dict] = None
    response_shape: Optional[dict] = None
    summary: str = ""


@dataclass
class PageItem:
    id: str
    type: Literal["page"]
    file_path: str
    route: str
    requires_auth: bool
    nav_label: Optional[str] = None
    linked_actions: list[dict] = field(default_factory=list)


@dataclass
class SeedItem:
    id: str
    type: Literal["seed"]
    model: str
    count: int
    sample_data: list[dict]


@dataclass
class StyleItem:
    id: str
    type: Literal["style"]
    css_var: str
    value: str
    scope: Literal["global", "scoped"] = "global"


ChecklistItem = ModelItem | RouteItem | PageItem | SeedItem | StyleItem


# ============================================================
# JSON Schema
# ============================================================

_COLUMN_SCHEMA = {
    "type": "object",
    "required": ["name", "type"],
    "properties": {
        "name":     {"type": "string"},
        "type":     {"type": "string"},
        "nullable": {"type": "boolean"},
        "unique":   {"type": "boolean"},
    },
    "additionalProperties": True,
}

_MODEL_SCHEMA = {
    "type": "object",
    "required": ["id", "type", "table_name", "class_name", "columns"],
    "properties": {
        "id":            {"type": "string"},
        "type":          {"type": "string", "const": "model"},
        "table_name":    {"type": "string"},
        "class_name":    {"type": "string"},
        "columns":       {"type": "array", "items": _COLUMN_SCHEMA},
        "relationships": {"type": "array"},
    },
    "additionalProperties": True,
}

_ROUTE_SCHEMA = {
    "type": "object",
    "required": ["id", "type", "method", "path", "auth_required"],
    "properties": {
        "id":            {"type": "string"},
        "type":          {"type": "string", "const": "route"},
        "method":        {"type": "string"},
        "path":          {"type": "string"},
        "auth_required": {"type": "boolean"},
        "request_body":  {"type": ["object", "null"]},
        "response_shape":{"type": ["object", "null"]},
        "summary":       {"type": "string"},
    },
    "additionalProperties": True,
}

_PAGE_SCHEMA = {
    "type": "object",
    "required": ["id", "type", "file_path", "route", "requires_auth"],
    "properties": {
        "id":             {"type": "string"},
        "type":           {"type": "string", "const": "page"},
        "file_path":      {"type": "string"},
        "route":          {"type": "string"},
        "requires_auth":  {"type": "boolean"},
        "nav_label":      {"type": ["string", "null"]},
        "linked_actions": {"type": "array"},
    },
    "additionalProperties": True,
}

_SEED_SCHEMA = {
    "type": "object",
    "required": ["id", "type", "model", "count", "sample_data"],
    "properties": {
        "id":          {"type": "string"},
        "type":        {"type": "string", "const": "seed"},
        "model":       {"type": "string"},
        "count":       {"type": "integer", "minimum": 1},
        "sample_data": {"type": "array"},
    },
    "additionalProperties": True,
}

_STYLE_SCHEMA = {
    "type": "object",
    "required": ["id", "type", "css_var", "value"],
    "properties": {
        "id":      {"type": "string"},
        "type":    {"type": "string", "const": "style"},
        "css_var": {"type": "string"},
        "value":   {"type": "string"},
        "scope":   {"type": "string", "enum": ["global", "scoped"]},
    },
    "additionalProperties": True,
}

CHECKLIST_JSON_SCHEMA = {
    "type": "array",
    "items": {
        "oneOf": [
            _MODEL_SCHEMA,
            _ROUTE_SCHEMA,
            _PAGE_SCHEMA,
            _SEED_SCHEMA,
            _STYLE_SCHEMA,
        ],
    },
    "minItems": 1,
}


# ============================================================
# Validation
# ============================================================

class ChecklistValidationError(ValueError):
    pass


def validate_checklist(checklist: list) -> None:
    """Validate a raw checklist list against the JSON schema.

    Raises ChecklistValidationError with the offending item ID on failure.
    """
    try:
        jsonschema.validate(instance=checklist, schema=CHECKLIST_JSON_SCHEMA)
    except jsonschema.ValidationError as exc:
        # Try to extract an item ID for the error message.
        offending_id = "unknown"
        try:
            path = list(exc.absolute_path)
            if path and isinstance(path[0], int):
                offending_id = str(checklist[path[0]].get("id", path[0]))
        except Exception:
            pass
        raise ChecklistValidationError(
            f"Checklist item {offending_id!r} failed validation: {exc.message}"
        ) from exc


# ============================================================
# Coercion: raw dict list -> typed dataclass list
# ============================================================

def coerce_checklist(raw: list) -> list[ChecklistItem]:
    """Convert a list of raw dicts (from LLM JSON) to typed dataclass instances.

    Skips items with unknown types rather than raising.
    """
    result: list[ChecklistItem] = []
    for item in raw:
        try:
            t = item.get("type")
            if t == "model":
                result.append(ModelItem(
                    id=item["id"],
                    type="model",
                    table_name=item["table_name"],
                    class_name=item["class_name"],
                    columns=item.get("columns", []),
                    relationships=item.get("relationships", []),
                ))
            elif t == "route":
                result.append(RouteItem(
                    id=item["id"],
                    type="route",
                    method=item["method"].upper(),
                    path=item["path"],
                    auth_required=bool(item.get("auth_required", True)),
                    request_body=item.get("request_body"),
                    response_shape=item.get("response_shape"),
                    summary=item.get("summary", ""),
                ))
            elif t == "page":
                result.append(PageItem(
                    id=item["id"],
                    type="page",
                    file_path=item["file_path"],
                    route=item["route"],
                    requires_auth=bool(item.get("requires_auth", False)),
                    nav_label=item.get("nav_label"),
                    linked_actions=item.get("linked_actions", []),
                ))
            elif t == "seed":
                result.append(SeedItem(
                    id=item["id"],
                    type="seed",
                    model=item["model"],
                    count=int(item.get("count", 1)),
                    sample_data=item.get("sample_data", []),
                ))
            elif t == "style":
                result.append(StyleItem(
                    id=item["id"],
                    type="style",
                    css_var=item["css_var"],
                    value=item["value"],
                    scope=item.get("scope", "global"),
                ))
        except (KeyError, TypeError, ValueError):
            pass
    return result
