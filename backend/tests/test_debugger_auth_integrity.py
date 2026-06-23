from app.ai.claude.agents.debugger import _fix_auth_scaffold_integrity


def test_strips_local_create_access_token():
    content = (
        "from fastapi import APIRouter\n"
        "from jose import jwt\n"
        "from datetime import datetime, timedelta\n"
        "SECRET_KEY = 'local-bad-key'\n"
        "ALGORITHM = 'HS256'\n"
        "\n"
        "def create_access_token(user_id):\n"
        "    payload = {'sub': str(user_id), 'exp': datetime.utcnow() + timedelta(minutes=30)}\n"
        "    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)\n"
        "\n"
        "router = APIRouter()\n"
        "@router.post('/login')\n"
        "def login(): return {'access_token': create_access_token(1)}\n"
    )
    files = {"backend/app/routes/auth_routes.py": content}
    fixes = _fix_auth_scaffold_integrity({}, files)
    out = fixes["backend/app/routes/auth_routes.py"]
    assert "def create_access_token" not in out
    assert "SECRET_KEY =" not in out
    assert "from app.auth import" in out and "create_access_token" in out


def test_replaces_inline_jwt_encode():
    content = (
        "from jose import jwt\n"
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.post('/login')\n"
        "def login():\n"
        "    return {'access_token': jwt.encode({'sub': '1'}, 'k', algorithm='HS256')}\n"
    )
    files = {"backend/app/routes/auth_routes.py": content}
    fixes = _fix_auth_scaffold_integrity({}, files)
    out = fixes["backend/app/routes/auth_routes.py"]
    assert "jwt.encode" not in out
    assert "create_access_token(user.id)" in out


def test_idempotent_when_clean():
    content = (
        "from fastapi import APIRouter\n"
        "from app.auth import create_access_token, get_current_user\n"
        "router = APIRouter()\n"
        "@router.post('/login')\n"
        "def login(): return {'access_token': create_access_token(1)}\n"
    )
    files = {"backend/app/routes/auth_routes.py": content}
    fixes = _fix_auth_scaffold_integrity({}, files)
    assert fixes == {}
