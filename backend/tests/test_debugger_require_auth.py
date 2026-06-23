from app.ai.claude.agents.debugger import _fix_require_auth_wrapping


def test_wraps_protected_route():
    app_jsx = (
        'import { Route } from "react-router-dom";\n'
        'import { AuthProvider, useAuth } from "@/contexts/AuthContext";\n'
        'function App() {\n'
        '  return (\n'
        '    <Routes>\n'
        '      <Route path="/dashboard" element={<DashboardPage />} />\n'
        '    </Routes>\n'
        '  );\n'
        '}\n'
    )
    files = {"frontend/src/App.jsx": app_jsx}
    test_results = {"checklist": []}
    fixes = _fix_require_auth_wrapping(test_results, files)
    out = fixes["frontend/src/App.jsx"]
    assert "<RequireAuth><DashboardPage />" in out
    assert 'import RequireAuth from "@/components/RequireAuth"' in out


def test_skips_when_auth_disabled():
    app_jsx = (
        'function App() {\n'
        '  // AUTH_DISABLED\n'
        '  return <Routes><Route path="/dashboard" element={<Page />} /></Routes>;\n'
        '}\n'
    )
    files = {"frontend/src/App.jsx": app_jsx}
    fixes = _fix_require_auth_wrapping({"checklist": []}, files)
    assert fixes == {}


def test_idempotent_when_already_wrapped():
    app_jsx = (
        'import { useAuth } from "@/contexts/AuthContext";\n'
        'import RequireAuth from "@/components/RequireAuth";\n'
        '<Route path="/dashboard" element={\n'
        '  <RequireAuth><DashboardPage /></RequireAuth>\n'
        '} />\n'
    )
    files = {"frontend/src/App.jsx": app_jsx}
    fixes = _fix_require_auth_wrapping({"checklist": []}, files)
    assert fixes == {}
