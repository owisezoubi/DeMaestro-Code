from app.ai.claude.agents.tester import _normalize_frontend_path


def test_prepends_api_for_api_client_relative_path():
    assert _normalize_frontend_path("/contact", True) == "/api/contact"


def test_no_change_when_already_prefixed():
    assert _normalize_frontend_path("/api/contact", True) == "/api/contact"


def test_no_change_for_raw_fetch_with_full_path():
    assert _normalize_frontend_path("/api/foo", False) == "/api/foo"


def test_no_change_for_raw_fetch_without_api_prefix():
    # Raw fetch is the LLM's mistake but not the parser's job to fix.
    assert _normalize_frontend_path("/contact", False) == "/contact"
