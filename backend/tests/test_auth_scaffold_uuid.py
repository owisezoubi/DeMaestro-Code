"""Tests for scaffold auth.py UUID tolerance.

Uses importlib to load the scaffold file directly (bypasses the
hyphenated directory name that prevents normal Python imports).
"""
import importlib.util
import sys
import types
import unittest.mock as mock
from pathlib import Path

SCAFFOLD_AUTH = Path(__file__).parent.parent / (
    "app/ai/claude/stack_templates/python-postgres/backend/app/auth.py"
)
SCAFFOLD_AUTH_ROUTES = Path(__file__).parent.parent / (
    "app/ai/claude/stack_templates/python-postgres/backend/app/routes/auth_routes.py"
)


def _load_scaffold_auth():
    """Load scaffold auth.py with stub deps for app.database / app.models."""
    db_mod = types.ModuleType("app.database")
    db_mod.get_db = mock.MagicMock()
    models_mod = types.ModuleType("app.models")
    models_mod.User = mock.MagicMock()
    # Temporarily register stubs so the import succeeds.
    prev_db = sys.modules.get("app.database")
    prev_models = sys.modules.get("app.models")
    sys.modules["app.database"] = db_mod
    sys.modules["app.models"] = models_mod
    try:
        spec = importlib.util.spec_from_file_location("_scaffold_auth", SCAFFOLD_AUTH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if prev_db is None:
            sys.modules.pop("app.database", None)
        else:
            sys.modules["app.database"] = prev_db
        if prev_models is None:
            sys.modules.pop("app.models", None)
        else:
            sys.modules["app.models"] = prev_models
    return mod


def _load_scaffold_auth_routes():
    """Load scaffold auth_routes.py with stub deps."""
    db_mod = types.ModuleType("app.database")
    db_mod.get_db = mock.MagicMock()
    models_mod = types.ModuleType("app.models")
    models_mod.User = mock.MagicMock()
    auth_mod = _load_scaffold_auth()
    prev = {}
    for k, v in [
        ("app.database", db_mod),
        ("app.models", models_mod),
        ("app.auth", auth_mod),
    ]:
        prev[k] = sys.modules.get(k)
        sys.modules[k] = v
    try:
        spec = importlib.util.spec_from_file_location(
            "_scaffold_auth_routes", SCAFFOLD_AUTH_ROUTES
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for k, v in prev.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return mod


def test_token_round_trip_with_int_id():
    from jose import jwt
    auth = _load_scaffold_auth()
    token = auth.create_access_token(42)
    payload = jwt.decode(token, auth.SECRET_KEY, algorithms=[auth.ALGORITHM])
    assert payload["sub"] == "42"


def test_token_round_trip_with_uuid_string():
    from jose import jwt
    auth = _load_scaffold_auth()
    uid = "6da56dbe-7eef-4b31-87cb-d137c3adb89a"
    token = auth.create_access_token(uid)
    payload = jwt.decode(token, auth.SECRET_KEY, algorithms=[auth.ALGORITHM])
    assert payload["sub"] == uid


def test_user_response_accepts_uuid_string():
    routes = _load_scaffold_auth_routes()
    UserResponse = routes.UserResponse
    u = UserResponse(id="6da56dbe-7eef-4b31-87cb-d137c3adb89a", email="a@b.c")
    assert isinstance(u.id, str)


def test_user_response_accepts_int():
    routes = _load_scaffold_auth_routes()
    UserResponse = routes.UserResponse
    u = UserResponse(id=42, email="a@b.c")
    assert u.id == 42


def test_user_response_serializes_uuid():
    routes = _load_scaffold_auth_routes()
    UserResponse = routes.UserResponse
    u = UserResponse(id="6da56dbe-7eef-4b31-87cb-d137c3adb89a", email="a@b.c")
    assert u.model_dump()["id"] == "6da56dbe-7eef-4b31-87cb-d137c3adb89a"
