from app.ai.claude.agents.debugger import _dedup_default_imports


def test_removes_duplicate_default_import_different_paths():
    content = (
        'import React from "react";\n'
        'import AdminMessagesPage from "@/pages/admin/AdminMessagesPage";\n'
        'import AdminMessagesPage from "@/pages/AdminMessagesPage";\n'
        'export default function App() { return null; }\n'
    )
    files = {"frontend/src/App.jsx": content}
    fixes = _dedup_default_imports({}, files)
    out = fixes["frontend/src/App.jsx"]
    assert out.count("import AdminMessagesPage") == 1
    # First occurrence kept.
    assert "@/pages/admin/AdminMessagesPage" in out
    assert '@/pages/AdminMessagesPage"' not in out


def test_no_change_when_no_duplicates():
    content = (
        'import React from "react";\n'
        'import App from "@/App";\n'
    )
    files = {"frontend/src/App.jsx": content}
    fixes = _dedup_default_imports({}, files)
    assert fixes == {}


def test_does_not_dedup_named_imports_from_same_source():
    content = (
        'import { Button } from "@/components/ui/button";\n'
        'import { Card } from "@/components/ui/card";\n'
    )
    files = {"frontend/src/App.jsx": content}
    fixes = _dedup_default_imports({}, files)
    assert fixes == {}


def test_handles_three_duplicates():
    content = (
        'import X from "a";\n'
        'import X from "b";\n'
        'import X from "c";\n'
    )
    files = {"frontend/src/App.jsx": content}
    fixes = _dedup_default_imports({}, files)
    out = fixes["frontend/src/App.jsx"]
    assert out.count("import X") == 1
    assert '"a"' in out
