from app.pipeline.checklist_runner import check_import_resolution


def test_check_import_resolution_flags_missing_module():
    files = {
        "backend/app/routes/foo.py": (
            "from app.notifications import Notification\n"
            "from app.models import User\n"
        ),
        "backend/app/models.py": "class Notification: pass\nclass User: pass\n",
    }
    results = check_import_resolution(files)
    assert any("app.notifications" in r.reason for r in results)
    assert any(
        "app.models" in r.reason and "Did you mean" in r.reason
        for r in results
    )


def test_check_import_resolution_passes_valid():
    files = {
        "backend/app/routes/foo.py": "from app.models import User\n",
        "backend/app/models.py": "class User: pass\n",
    }
    results = check_import_resolution(files)
    assert results == []


def test_fix_bad_module_imports_rewrites_to_models():
    from app.ai.claude.agents.debugger import _fix_bad_module_imports
    files = {
        "backend/app/routes/foo.py": (
            "from app.notifications import Notification\n"
            "from fastapi import APIRouter\n"
        ),
        "backend/app/models.py": "class Notification: pass\n",
    }
    fixes = _fix_bad_module_imports({}, files)
    assert "backend/app/routes/foo.py" in fixes
    out = fixes["backend/app/routes/foo.py"]
    assert "from app.models import Notification" in out
    assert "from app.notifications" not in out


def test_fix_auth_scaffold_integrity_replaces_jwt_encode():
    from app.ai.claude.agents.debugger import _fix_auth_scaffold_integrity
    content = (
        "import jwt\n"
        "from fastapi import APIRouter\n"
        "@router.post('/login')\n"
        "def login(...):\n"
        "    token = jwt.encode({'sub': str(user.id)}, KEY, 'HS256')\n"
        "    return {'access_token': token}\n"
    )
    files = {"backend/app/routes/auth_routes.py": content}
    fixes = _fix_auth_scaffold_integrity({}, files)
    out = fixes["backend/app/routes/auth_routes.py"]
    assert "create_access_token(user.id)" in out
    assert "from app.auth import" in out


def test_fix_auth_scaffold_integrity_idempotent():
    from app.ai.claude.agents.debugger import _fix_auth_scaffold_integrity
    content = (
        "from app.auth import create_access_token\n"
        "@router.post('/login')\n"
        "def login(...):\n"
        "    return {'access_token': create_access_token(user.id)}\n"
    )
    files = {"backend/app/routes/auth_routes.py": content}
    fixes = _fix_auth_scaffold_integrity({}, files)
    assert fixes == {}
