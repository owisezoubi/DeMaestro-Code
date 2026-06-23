from app.ai.claude.agents.tester import _extract_body_keys_balanced


def test_extracts_body_after_headers_in_fetch():
    content = (
        'const res = await fetch("/api/auth/login", {\n'
        '  method: "POST",\n'
        '  headers: { "Content-Type": "application/json" },\n'
        '  body: JSON.stringify({ email, password }),\n'
        '});\n'
    )
    out = _extract_body_keys_balanced(content)
    assert len(out) == 1
    assert out[0]["url"] == "/api/auth/login"
    assert out[0]["body_keys"] == ["email", "password"]


def test_extracts_body_before_headers():
    content = (
        'await fetch("/api/auth/register", {\n'
        '  body: JSON.stringify({ email, password, name }),\n'
        '  headers: { "Content-Type": "application/json" },\n'
        '  method: "POST",\n'
        '});\n'
    )
    out = _extract_body_keys_balanced(content)
    assert out[0]["body_keys"] == ["email", "name", "password"]


def test_extracts_from_api_post():
    content = (
        'const r = await api.post("/orders", { items, delivery_type });\n'
    )
    out = _extract_body_keys_balanced(content)
    assert out[0]["url"] == "/orders"
    assert out[0]["method"] == "POST"
    assert out[0]["body_keys"] == ["delivery_type", "items"]


def test_handles_shorthand_and_full_form():
    content = (
        'api.post("/x", { email, password: pw, name: getName() });\n'
    )
    out = _extract_body_keys_balanced(content)
    assert out[0]["body_keys"] == ["email", "name", "password"]


def test_handles_nested_object_in_value():
    content = (
        'api.post("/x", { user: { id: 1, role: "admin" }, ts });\n'
    )
    out = _extract_body_keys_balanced(content)
    assert out[0]["body_keys"] == ["ts", "user"]


def test_handles_array_in_value():
    content = (
        'api.post("/x", { items: [1, 2, 3], note });\n'
    )
    out = _extract_body_keys_balanced(content)
    assert out[0]["body_keys"] == ["items", "note"]


def test_ignores_spread_entries():
    content = (
        'api.post("/x", { ...rest, email });\n'
    )
    out = _extract_body_keys_balanced(content)
    # Spread can't be statically resolved; we capture only `email`.
    assert "email" in out[0]["body_keys"]
    assert "rest" not in out[0]["body_keys"]


def test_multiple_calls_in_one_file():
    content = (
        'api.post("/login", { email, password });\n'
        'api.post("/register", { email, password, name });\n'
    )
    out = _extract_body_keys_balanced(content)
    assert len(out) == 2
    urls = {o["url"] for o in out}
    assert urls == {"/login", "/register"}


def test_returns_empty_keys_when_no_body():
    content = 'api.get("/plants");\n'
    out = _extract_body_keys_balanced(content)
    # GET has no body -- extractor may or may not return an entry;
    # if it does, body_keys is empty.
    for o in out:
        assert o["body_keys"] == []
