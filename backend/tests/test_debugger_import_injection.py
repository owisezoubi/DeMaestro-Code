from app.ai.claude.agents.debugger import _inject_imports_into_jsx


def test_appends_new_import_after_existing_singleline_imports():
    content = (
        'import React from "react";\n'
        'import { Card } from "@/components/ui/card";\n'
        '\n'
        'export function P() { return <div><Slot /></div>; }\n'
    )
    out = _inject_imports_into_jsx(
        content, {"@radix-ui/react-slot": ["Slot"]},
    )
    assert 'import { Slot } from "@radix-ui/react-slot";' in out
    # Original imports untouched.
    assert 'import { Card } from "@/components/ui/card";' in out


def test_does_not_break_multiline_import():
    content = (
        'import { Card } from "@/components/ui/card";\n'
        'import {\n'
        '  User,\n'
        '  Settings,\n'
        '} from "lucide-react";\n'
        '\n'
        'export function P() { return <div><Slot /></div>; }\n'
    )
    out = _inject_imports_into_jsx(
        content, {"@radix-ui/react-slot": ["Slot"]},
    )
    # The multi-line import must remain intact.
    assert 'User,\n  Settings,\n} from "lucide-react";' in out
    # New import inserted AFTER the multi-line block.
    slot_idx = out.find('"@radix-ui/react-slot"')
    lucide_idx = out.find('"lucide-react"')
    assert slot_idx > lucide_idx


def test_merges_with_existing_import_from_same_source():
    content = (
        'import { Edit } from "lucide-react";\n'
        '\n'
        'export function P() { return <Settings />; }\n'
    )
    out = _inject_imports_into_jsx(
        content, {"lucide-react": ["Settings"]},
    )
    # ONE import line, both names present.
    assert out.count('from "lucide-react"') == 1
    assert "Edit" in out and "Settings" in out


def test_does_not_duplicate_existing_name():
    content = (
        'import { Edit, Settings } from "lucide-react";\n'
        '\n'
        'export function P() { return <Settings />; }\n'
    )
    out = _inject_imports_into_jsx(
        content, {"lucide-react": ["Settings"]},
    )
    # No change.
    assert out == content


def test_handles_aliased_imports():
    content = (
        'import { Edit as Pencil } from "lucide-react";\n'
        '\n'
        'export function P() { return <Settings />; }\n'
    )
    out = _inject_imports_into_jsx(
        content, {"lucide-react": ["Settings"]},
    )
    # Settings is added; Edit as Pencil preserved.
    assert "Settings" in out
    assert "Edit as Pencil" in out


def test_multiple_sources_in_one_call():
    content = 'export function P() { return <div />; }\n'
    out = _inject_imports_into_jsx(content, {
        "@radix-ui/react-slot": ["Slot"],
        "lucide-react": ["Edit"],
    })
    assert "Slot" in out and "Edit" in out
    assert out.count('from "@radix-ui/react-slot"') == 1
    assert out.count('from "lucide-react"') == 1
