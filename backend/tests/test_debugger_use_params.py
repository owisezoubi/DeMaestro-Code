from app.ai.claude.agents.debugger import (
    _fix_use_params_name_mismatch,
)


def test_renames_destructure_to_match_route_param():
    app_jsx = (
        'function App() {\n'
        '  return (\n'
        '    <Routes>\n'
        '      <Route path="/plants/:id" element={<PlantDetailPage />} />\n'
        '    </Routes>\n'
        '  )\n'
        '}\n'
    )
    page = (
        'import { useParams } from "react-router-dom"\n'
        'export default function PlantDetailPage() {\n'
        '  const { plantId } = useParams()\n'
        '  return <div>{plantId}</div>\n'
        '}\n'
    )
    files = {
        "frontend/src/App.jsx": app_jsx,
        "frontend/src/pages/PlantDetailPage.jsx": page,
    }
    fixes = _fix_use_params_name_mismatch({}, files)
    out = fixes["frontend/src/pages/PlantDetailPage.jsx"]
    assert "const { id: plantId } = useParams()" in out


def test_no_change_when_already_matching():
    app_jsx = (
        '<Route path="/plants/:id" element={<PlantDetailPage />} />\n'
    )
    page = (
        'import { useParams } from "react-router-dom"\n'
        'export default function PlantDetailPage() {\n'
        '  const { id } = useParams()\n'
        '  return <div>{id}</div>\n'
        '}\n'
    )
    files = {
        "frontend/src/App.jsx": app_jsx,
        "frontend/src/pages/PlantDetailPage.jsx": page,
    }
    fixes = _fix_use_params_name_mismatch({}, files)
    assert fixes == {}


def test_handles_multiple_route_params():
    app_jsx = (
        '<Route path="/teams/:teamId/users/:userId" '
        'element={<TeamUserPage />} />\n'
    )
    page = (
        'import { useParams } from "react-router-dom"\n'
        'export default function TeamUserPage() {\n'
        '  const { team, user } = useParams()\n'
        '  return <div>{team} {user}</div>\n'
        '}\n'
    )
    files = {
        "frontend/src/App.jsx": app_jsx,
        "frontend/src/pages/TeamUserPage.jsx": page,
    }
    fixes = _fix_use_params_name_mismatch({}, files)
    out = fixes["frontend/src/pages/TeamUserPage.jsx"]
    assert "teamId: team" in out
    assert "userId: user" in out


def test_skips_when_useParams_uses_params_object_form():
    """const params = useParams() is not a destructure, skip it."""
    app_jsx = (
        '<Route path="/plants/:id" element={<PlantDetailPage />} />\n'
    )
    page = (
        'export default function PlantDetailPage() {\n'
        '  const params = useParams()\n'
        '  const id = params.id || params.plantId\n'
        '  return <div>{id}</div>\n'
        '}\n'
    )
    files = {
        "frontend/src/App.jsx": app_jsx,
        "frontend/src/pages/PlantDetailPage.jsx": page,
    }
    fixes = _fix_use_params_name_mismatch({}, files)
    assert fixes == {}


def test_skips_routes_with_no_params():
    app_jsx = (
        '<Route path="/dashboard" element={<DashboardPage />} />\n'
    )
    page = (
        'export default function DashboardPage() {\n'
        '  return <div>Dashboard</div>\n'
        '}\n'
    )
    files = {
        "frontend/src/App.jsx": app_jsx,
        "frontend/src/pages/DashboardPage.jsx": page,
    }
    fixes = _fix_use_params_name_mismatch({}, files)
    assert fixes == {}


def test_handles_self_closing_element_syntax():
    app_jsx = (
        '<Route path="/plants/:plantId" element={<PlantDetailPage/>} />\n'
    )
    page = (
        'export default function PlantDetailPage() {\n'
        '  const { id } = useParams()\n'
        '  return <div>{id}</div>\n'
        '}\n'
    )
    files = {
        "frontend/src/App.jsx": app_jsx,
        "frontend/src/pages/PlantDetailPage.jsx": page,
    }
    fixes = _fix_use_params_name_mismatch({}, files)
    out = fixes["frontend/src/pages/PlantDetailPage.jsx"]
    assert "plantId: id" in out
