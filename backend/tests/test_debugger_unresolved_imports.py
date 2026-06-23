import json

from app.ai.claude.agents.debugger import (
    _fix_unresolved_identifiers,
    _CANONICAL_IMPORTS,
)


def test_adds_missing_button_import():
    content = (
        'export function MyPage() {\n'
        '  return <div><Button>Click</Button></div>\n'
        '}\n'
    )
    files = {"frontend/src/pages/MyPage.jsx": content}
    fixes = _fix_unresolved_identifiers({}, files)
    out = fixes["frontend/src/pages/MyPage.jsx"]
    assert 'import { Button } from "@/components/ui/button"' in out


def test_adds_missing_useparams_hook():
    content = (
        'export function Detail() {\n'
        '  const { id } = useParams()\n'
        '  return <div>{id}</div>\n'
        '}\n'
    )
    files = {"frontend/src/pages/Detail.jsx": content}
    fixes = _fix_unresolved_identifiers({}, files)
    out = fixes["frontend/src/pages/Detail.jsx"]
    assert "useParams" in out
    assert "react-router-dom" in out


def test_syncs_package_json_for_external_dep():
    # One file already has the import (no auto-add needed for that file),
    # but a second file uses Slot without importing it — should trigger
    # both an import fix and a package.json sync.
    another = (
        'export function Page() { return <Slot /> }\n'
    )
    files = {
        "frontend/src/components/ui/x.jsx": (
            'import { Slot } from "@radix-ui/react-slot"\n'
            'export const X = () => <Slot />\n'
        ),
        "frontend/package.json": '{"dependencies":{"react":"^18.0.0"}}\n',
        "frontend/src/pages/Page.jsx": another,
    }
    fixes = _fix_unresolved_identifiers({}, files)
    pkg = json.loads(fixes["frontend/package.json"])
    assert "@radix-ui/react-slot" in pkg["dependencies"]


def test_does_not_double_import_existing():
    content = (
        'import { Button } from "@/components/ui/button"\n'
        'export function MyPage() {\n'
        '  return <Button>Click</Button>\n'
        '}\n'
    )
    files = {"frontend/src/pages/MyPage.jsx": content}
    fixes = _fix_unresolved_identifiers({}, files)
    assert fixes == {}


def test_respects_aliased_import():
    content = (
        'import { Foo as Button } from "somewhere"\n'
        'export function P() { return <Button /> }\n'
    )
    files = {"frontend/src/pages/P.jsx": content}
    fixes = _fix_unresolved_identifiers({}, files)
    # Button is in scope via alias — no auto-import added.
    assert fixes == {}


def test_unknown_identifier_not_auto_fixed():
    content = (
        'export function P() {\n'
        '  return <CustomThing />\n'
        '}\n'
    )
    files = {"frontend/src/pages/P.jsx": content}
    fixes = _fix_unresolved_identifiers({}, files)
    # CustomThing is not in the whitelist — no invented import.
    assert "frontend/src/pages/P.jsx" not in fixes
