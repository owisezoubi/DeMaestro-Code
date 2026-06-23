"""Unit tests for _auto_stub_missing_contract_endpoints reachability extension."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ai.claude.agents.debugger import (
    _auto_stub_missing_contract_endpoints,
    _REACHABILITY_MISS_RE,
)


def test_reachability_miss_regex_matches_arrow_unicode():
    log = "2/29 endpoints unreachable: DELETE /api/plants/{plant_id}/favorite → route does not exist"
    matches = list(_REACHABILITY_MISS_RE.finditer(log))
    assert len(matches) == 1
    assert matches[0].group("method") == "DELETE"
    assert matches[0].group("path") == "/api/plants/{plant_id}/favorite"


def test_reachability_miss_regex_matches_arrow_ascii():
    log = "1/10 endpoints unreachable: GET /api/foo -> route does not exist"
    matches = list(_REACHABILITY_MISS_RE.finditer(log))
    assert len(matches) == 1
    assert matches[0].group("method") == "GET"


def test_reachability_miss_regex_handles_multiple_failures():
    log = (
        "2/29 endpoints unreachable: "
        "DELETE /api/plants/{plant_id}/favorite → route does not exist; "
        "DELETE /api/users/{user_id}/follow → route does not exist"
    )
    matches = list(_REACHABILITY_MISS_RE.finditer(log))
    assert len(matches) == 2
    paths = [m.group("path") for m in matches]
    assert "/api/plants/{plant_id}/favorite" in paths
    assert "/api/users/{user_id}/follow" in paths


def test_reachability_miss_skips_405_method_mismatch():
    log = "1/10 endpoints unreachable: GET /api/foo → wrong HTTP method"
    matches = list(_REACHABILITY_MISS_RE.finditer(log))
    assert len(matches) == 0


def test_auto_stub_picks_up_reachability_misses():
    test_results = {
        "logs": {
            "reachability": (
                "2/29 endpoints unreachable: "
                "DELETE /api/plants/{plant_id}/favorite → route does not exist; "
                "DELETE /api/users/{user_id}/follow → route does not exist"
            ),
            "contract": "",
        },
    }
    generated_files = {
        "backend/app/routes/plants.py": (
            "from fastapi import APIRouter, Depends, HTTPException\n"
            "from sqlalchemy.orm import Session\n"
            "from app.database import get_db\n"
            "from app.auth import get_current_user\n"
            "from app.models import User\n\n"
            'router = APIRouter(prefix="/plants", tags=["plants"])\n\n'
            '@router.post("/{plant_id}/favorite")\n'
            "def fav(plant_id: int, db: Session = Depends(get_db),\n"
            "        current_user: User = Depends(get_current_user)):\n"
            '    return {"ok": True}\n'
        ),
        "backend/app/routes/users.py": (
            "from fastapi import APIRouter, Depends, HTTPException\n"
            "from sqlalchemy.orm import Session\n"
            "from app.database import get_db\n"
            "from app.auth import get_current_user\n"
            "from app.models import User\n\n"
            'router = APIRouter(prefix="/users", tags=["users"])\n\n'
            '@router.post("/{user_id}/follow")\n'
            "def fol(user_id: int, db: Session = Depends(get_db),\n"
            "        current_user: User = Depends(get_current_user)):\n"
            '    return {"ok": True}\n'
        ),
    }
    fixes = _auto_stub_missing_contract_endpoints(test_results, generated_files)
    assert "backend/app/routes/plants.py" in fixes
    assert "backend/app/routes/users.py" in fixes
    assert '@router.delete("/{plant_id}/favorite")' in fixes["backend/app/routes/plants.py"]
    assert '@router.delete("/{user_id}/follow")' in fixes["backend/app/routes/users.py"]
