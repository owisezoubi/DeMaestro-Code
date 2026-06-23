"""Lightweight tests that the middleware and health endpoint are present
and correctly structured in the scaffold main.py template."""
from pathlib import Path

MAIN_PY = Path(__file__).resolve().parents[1] / (
    "app/ai/claude/stack_templates/python-postgres/"
    "backend/app/main.py"
)


def test_middleware_present():
    content = MAIN_PY.read_text(encoding="utf-8")
    assert "_ensure_db_ready" in content
    assert "Base.metadata.create_all" in content
    assert "seed_demo_data" in content


def test_middleware_is_idempotent_via_flag():
    content = MAIN_PY.read_text(encoding="utf-8")
    assert "_db_ready = False" in content
    assert "if not _db_ready:" in content
    assert "_db_ready = True" in content


def test_seed_skipped_when_db_not_empty():
    content = MAIN_PY.read_text(encoding="utf-8")
    # Seed is guarded by a user-count check (not the old needs_seed flag).
    assert "user_count" in content
    assert "skipped" in content.lower()


def test_health_db_endpoint_present():
    content = MAIN_PY.read_text(encoding="utf-8")
    assert "/_health/db" in content
    assert "row_counts" in content
