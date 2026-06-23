from app.ai.claude.agents.debugger import (
    _fix_landing_route, _ensure_public_home_page,
)


def test_injects_authgate_for_auth_gate_strategy():
    app_jsx = (
        'function App() {\n'
        '  return <Routes>\n'
        '    <Route path="/login" element={<LoginPage />} />\n'
        '  </Routes>;\n'
        '}\n'
    )
    files = {"frontend/src/App.jsx": app_jsx}
    results = {"blueprint": {"landing_strategy": "auth_gate"}}
    fixes = _fix_landing_route(results, files)
    out = fixes["frontend/src/App.jsx"]
    assert '<Route path="/" element={<AuthGate />}' in out
    assert 'import AuthGate from "@/components/AuthGate"' in out


def test_injects_home_for_public_home_strategy():
    app_jsx = (
        'function App() {\n'
        '  return <Routes>\n'
        '    <Route path="/gallery" element={<GalleryPage />} />\n'
        '  </Routes>;\n'
        '}\n'
    )
    files = {
        "frontend/src/App.jsx": app_jsx,
        "frontend/src/pages/HomePage.jsx": (
            "export default function HomePage() { return <div />; }"
        ),
    }
    results = {"blueprint": {"landing_strategy": "public_home"}}
    fixes = _fix_landing_route(results, files)
    out = fixes["frontend/src/App.jsx"]
    assert '<Route path="/" element={<HomePage />}' in out
    # Public strategy should NOT import AuthGate.
    assert "AuthGate" not in out


def test_no_change_if_root_route_already_present():
    app_jsx = (
        '<Routes>\n'
        '  <Route path="/" element={<HomePage />} />\n'
        '</Routes>\n'
    )
    files = {"frontend/src/App.jsx": app_jsx}
    fixes = _fix_landing_route(
        {"blueprint": {"landing_strategy": "public_home"}},
        files,
    )
    assert fixes == {}


def test_ensure_public_home_stubs_when_missing():
    files = {"frontend/src/App.jsx": "..."}
    results = {"blueprint": {"landing_strategy": "public_home"}}
    fixes = _ensure_public_home_page(results, files)
    assert "frontend/src/pages/HomePage.jsx" in fixes
    assert "export default function HomePage" in fixes[
        "frontend/src/pages/HomePage.jsx"
    ]


def test_ensure_public_home_noop_when_present():
    files = {
        "frontend/src/App.jsx": "...",
        "frontend/src/pages/HomePage.jsx": "existing",
    }
    results = {"blueprint": {"landing_strategy": "public_home"}}
    fixes = _ensure_public_home_page(results, files)
    assert fixes == {}


def test_ensure_public_home_noop_for_auth_gate():
    files = {"frontend/src/App.jsx": "..."}
    results = {"blueprint": {"landing_strategy": "auth_gate"}}
    fixes = _ensure_public_home_page(results, files)
    assert fixes == {}
