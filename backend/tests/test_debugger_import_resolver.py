from app.ai.claude.agents.debugger import _ensure_required_imports


def test_injects_missing_get_current_user_import():
    src = (
        "from fastapi import APIRouter, Depends\n"
        "router = APIRouter()\n"
        "\n"
        "@router.get('/me')\n"
        "def me(current_user = Depends(get_current_user)):\n"
        "    return current_user\n"
    )
    out = _ensure_required_imports(src)
    assert "from app.auth import get_current_user" in out
    # File still parses.
    import ast
    ast.parse(out)


def test_idempotent_when_imports_present():
    src = (
        "from fastapi import APIRouter, Depends\n"
        "from app.auth import get_current_user\n"
        "router = APIRouter()\n"
        "@router.get('/me')\n"
        "def me(current_user = Depends(get_current_user)):\n"
        "    return current_user\n"
    )
    assert _ensure_required_imports(src) == src


def test_no_change_when_only_scaffold_helpers_unused():
    src = (
        "x = 1\n"
        "y = x + 2\n"
    )
    assert _ensure_required_imports(src) == src


def test_returns_unchanged_on_syntax_error():
    src = "def broken(:\n    pass\n"
    assert _ensure_required_imports(src) == src


def test_handles_multiple_missing_imports_from_same_module():
    src = (
        "@router.get('/me')\n"
        "def me(\n"
        "    current_user = Depends(get_current_user),\n"
        "    db = Depends(get_db),\n"
        "):\n"
        "    return current_user\n"
    )
    out = _ensure_required_imports(src)
    assert "from app.auth import get_current_user" in out
    assert "from app.database import get_db" in out


def test_inserts_after_existing_import_block():
    src = (
        '"""Module docstring."""\n'
        "from fastapi import APIRouter\n"
        "\n"
        "router = APIRouter()\n"
        "\n"
        "@router.get('/me')\n"
        "def me(current_user = Depends(get_current_user)):\n"
        "    return current_user\n"
    )
    out = _ensure_required_imports(src)
    lines = out.splitlines()
    # All "from"/"import" lines should be contiguous, before
    # `router = APIRouter()`.
    assert any(
        "from app.auth" in ln for ln in lines
    )
