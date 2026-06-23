from app.ai.claude.agents.debugger import _fix_method_mismatch


def test_handles_multi_method_served_list():
    # Backend serves both GET and PATCH; frontend asks PUT.
    # Expected: rewrite frontend PUT -> PATCH (most similar to PUT).
    test_results = {
        "logs": {
            "contract": (
                "CONTRACT MISS: METHOD PUT /api/admin/profile "
                "backend serves this path with: ['GET', 'PATCH']. "
                "suggestions: ['GET', 'PATCH']"
            ),
        },
    }
    files = {
        "frontend/src/pages/AdminProfile.jsx": (
            'import api from "@/lib/api";\n'
            'export function P() {\n'
            '  return api.put("/admin/profile", {});\n'
            '}\n'
        ),
    }
    fixes = _fix_method_mismatch(test_results, files)
    out = fixes.get("frontend/src/pages/AdminProfile.jsx", "")
    assert "api.patch" in out
    assert "api.put" not in out
