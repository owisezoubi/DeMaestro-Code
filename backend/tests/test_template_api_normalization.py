from pathlib import Path

TEMPLATE_PATH = Path(__file__).resolve().parents[1] / (
    "app/ai/claude/stack_templates/python-postgres/"
    "frontend/src/lib/api.js"
)


def test_api_template_has_normalize_path():
    content = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "function normalizePath" in content
    # Tolerates both /api/x and /x forms.
    assert 'path.startsWith(BASE + "/")' in content
    # Returns base + path for relative paths.
    assert "${BASE}${sep}${path}" in content


def test_api_template_uses_normalize_path_in_request():
    content = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "normalizePath(path)" in content
    # Confirm the old line is gone.
    assert (
        'path.startsWith("http") ? path : `${BASE}${path}`'
        not in content
    )
