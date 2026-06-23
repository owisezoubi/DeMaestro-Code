"""Unit tests for deterministic debugger fix helpers."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from app.ai.claude.agents.debugger import (
    _rewrite_admin_prefix,
    _fix_admin_prefix_missing,
    _dedup_jsx_layout_decls_fixer,
    _detect_duplicate_top_level_decls,
    _is_python_parseable,
    _validate_or_rollback,
)


def _do_rewrite(content: str, known_admin_resources: set) -> str:
    """Thin wrapper so test bodies stay readable."""
    return _rewrite_admin_prefix(content, known_admin_resources)


# ── _rewrite_admin_prefix ────────────────────────────────────────────────────

def test_admin_prefix_basic_double_quote():
    content = 'apiClient.get("/api/profile")'
    result = _do_rewrite(content, {"profile"})
    assert result == 'apiClient.get("/api/admin/profile")'


def test_admin_prefix_with_trailing_semicolon():
    content = 'apiClient.get("/api/profile");'
    result = _do_rewrite(content, {"profile"})
    assert result == 'apiClient.get("/api/admin/profile");'
    assert ";" not in result.split('"')[1]  # no semicolon inside the URL


def test_admin_prefix_single_quote():
    content = "apiClient.get('/api/profile')"
    result = _do_rewrite(content, {"profile"})
    assert result == "apiClient.get('/api/admin/profile')"


def test_admin_prefix_with_subpath():
    content = 'apiClient.put("/api/profile/123")'
    result = _do_rewrite(content, {"profile"})
    assert result == 'apiClient.put("/api/admin/profile/123")'


def test_public_endpoint_unchanged():
    content = 'apiClient.post("/api/contact")'
    result = _do_rewrite(content, {"profile"})  # "contact" NOT in set
    assert result == 'apiClient.post("/api/contact")'


def test_auth_unchanged():
    content = 'apiClient.post("/api/auth/login")'
    result = _do_rewrite(content, {"profile"})
    assert result == 'apiClient.post("/api/auth/login")'


def test_mixed_quote_not_matched():
    """A URL opened with " and closed with ' must not be rewritten."""
    content = 'apiClient.get("/api/profile\')'
    result = _do_rewrite(content, {"profile"})
    assert result == content  # malformed literal — leave untouched


def test_already_admin_unchanged():
    content = 'apiClient.get("/api/admin/profile")'
    result = _do_rewrite(content, {"profile"})
    assert result == content  # already correct


def test_multiple_resources_in_one_file():
    content = (
        'api.get("/api/users");\n'
        'api.get("/api/posts");\n'
        'api.get("/api/contact");\n'
    )
    result = _do_rewrite(content, {"users", "posts"})
    assert '"/api/admin/users"' in result
    assert '"/api/admin/posts"' in result
    assert '"/api/contact"' in result  # not in set — unchanged


# ── _fix_admin_prefix_missing integration ───────────────────────────────────

def _make_test_results(contract_log: str) -> dict:
    return {"logs": {"contract": contract_log, "contract_advisory": ""}}


def test_fix_admin_prefix_missing_end_to_end():
    contract_log = (
        "CONTRACT MISS: ADMIN-PREFIX GET /api/profile "
        "(called from AdminProfile.jsx) "
        "suggestions: change to /api/admin/profile;"
    )
    generated_files = {
        "frontend/src/pages/AdminProfile.jsx": 'const r = api.get("/api/profile");',
    }
    fixes = _fix_admin_prefix_missing(_make_test_results(contract_log), generated_files)
    assert "frontend/src/pages/AdminProfile.jsx" in fixes
    result = fixes["frontend/src/pages/AdminProfile.jsx"]
    assert '"/api/admin/profile"' in result
    assert ";" not in result.split('"')[1]


def test_fix_admin_prefix_missing_no_semicolon_corruption():
    """Regression: corrected path must never contain a trailing semicolon."""
    contract_log = (
        "CONTRACT MISS: ADMIN-PREFIX POST /api/dashboard "
        "(called from Dashboard.jsx) "
        "suggestions: change to /api/admin/dashboard;"
    )
    generated_files = {
        "frontend/src/pages/Dashboard.jsx": (
            'const a = api.get("/api/dashboard");\n'
            'const b = api.post("/api/dashboard");\n'
        ),
    }
    fixes = _fix_admin_prefix_missing(_make_test_results(contract_log), generated_files)
    if fixes:
        content = fixes.get("frontend/src/pages/Dashboard.jsx", "")
        for line in content.splitlines():
            if "/api/" in line:
                # Extract URL between quotes
                import re
                urls = re.findall(r'["\']([^"\']+)["\']', line)
                for url in urls:
                    assert not url.endswith(";"), f"Stray semicolon in URL: {url!r}"


# ── _dedup_jsx_layout_decls_fixer ────────────────────────────────────────────

def _apply_fixer(fixer, files):
    """Apply a fixer's returned patches to files in-place, mirroring orchestrator."""
    patches = fixer({}, files)
    for p, c in (patches or {}).items():
        files[p] = c
    return patches


def test_hard_dedup_removes_duplicate_layout():
    content = (
        "function Layout() { return <div>a</div> }\n\n"
        "function BareLayout() { return <div>b</div> }\n\n"
        "function Layout() { return <div>c</div> }\n"
    )
    files = {"frontend/src/App.jsx": content}
    _apply_fixer(_dedup_jsx_layout_decls_fixer, files)
    result = files["frontend/src/App.jsx"]
    assert result.count("function Layout(") == 1, "should keep exactly one Layout"
    assert "return <div>a</div>" in result, "should keep first occurrence"
    assert "return <div>c</div>" not in result, "should remove second occurrence"


def test_hard_dedup_removes_export_default_duplicate():
    content = (
        "export default function Layout() { return <div>first</div> }\n\n"
        "function Layout() { return <div>second</div> }\n"
    )
    files = {"frontend/src/App.jsx": content}
    _apply_fixer(_dedup_jsx_layout_decls_fixer, files)
    result = files["frontend/src/App.jsx"]
    assert "Layout" in result
    assert "second" not in result, "duplicate Layout should be removed"


def test_hard_dedup_idempotent():
    content = "function Layout() { return <div /> }\n"
    files = {"frontend/src/App.jsx": content}
    fixes = _dedup_jsx_layout_decls_fixer({}, files)
    assert fixes == {}, "no changes when no duplicates"
    assert files["frontend/src/App.jsx"] == content


def test_hard_dedup_leaves_non_jsx_files_alone():
    content = "def Layout(): pass\ndef Layout(): pass\n"
    files = {"backend/app/routes/items.py": content}
    fixes = _dedup_jsx_layout_decls_fixer({}, files)
    assert fixes == {}, "should not touch Python files"


# ── _detect_duplicate_top_level_decls ────────────────────────────────────────

def test_detect_dupe_handles_export_default():
    content = (
        "export default function Layout() { return null }\n"
        "function Layout() { return null }\n"
    )
    dupes = _detect_duplicate_top_level_decls(content)
    assert "Layout" in dupes, "should detect duplicate across export variants"


def test_detect_dupe_no_false_positive():
    content = (
        "function Layout() { return null }\n"
        "function BareLayout() { return null }\n"
    )
    dupes = _detect_duplicate_top_level_decls(content)
    assert dupes == [], "different names should not be flagged as duplicates"


# ── _is_python_parseable ─────────────────────────────────────────────────────

def test_is_python_parseable_valid():
    assert _is_python_parseable("x = 1\n") is True


def test_is_python_parseable_invalid():
    assert _is_python_parseable("def foo(\n") is False


# ── _validate_or_rollback: escalation when pre-cycle is also broken ───────────

import structlog as _structlog
_noop_log = _structlog.get_logger("test")


def test_validate_or_rollback_escalates_when_pre_cycle_also_broken():
    broken = "def foo(\n"  # SyntaxError
    files_before = {"backend/app/routes/favorites.py": broken}
    files_after = {"backend/app/routes/favorites.py": broken + "# comment\n"}
    all_fixes = {"backend/app/routes/favorites.py": files_after["backend/app/routes/favorites.py"]}

    validated_files, validated_fixes = _validate_or_rollback(
        files_before, files_after, all_fixes, _noop_log
    )
    # Should NOT revert (both are broken) -- keeps post-cycle content
    assert validated_files["backend/app/routes/favorites.py"] == files_after["backend/app/routes/favorites.py"]
    # Should remove from fixes (so LLM debug branch runs)
    assert "backend/app/routes/favorites.py" not in validated_fixes


def test_validate_or_rollback_reverts_when_pre_cycle_is_good():
    good = "x = 1\n"
    broken = "def foo(\n"
    files_before = {"backend/app/routes/items.py": good}
    files_after = {"backend/app/routes/items.py": broken}
    all_fixes = {"backend/app/routes/items.py": broken}

    validated_files, validated_fixes = _validate_or_rollback(
        files_before, files_after, all_fixes, _noop_log
    )
    # Should revert to the good pre-cycle content
    assert validated_files["backend/app/routes/items.py"] == good
    assert "backend/app/routes/items.py" not in validated_fixes
