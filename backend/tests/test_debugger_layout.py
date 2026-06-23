"""Unit tests for Layout/BareLayout dedup and injection guard helpers."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ai.claude.agents.debugger import (
    _hard_dedup_jsx_files,
    _app_jsx_has_layout,
    _fix_app_jsx_layout_pattern,
    _resolve_layout_import_inline_conflict,
)


# ── _hard_dedup_jsx_files ────────────────────────────────────────────────────

def test_dedup_removes_duplicate_layout():
    content = (
        "import React from 'react'\n\n"
        "function Layout() {\n"
        "  return <div>first</div>\n"
        "}\n\n"
        "function BareLayout() {\n"
        "  return <div>bare</div>\n"
        "}\n\n"
        "function Layout() {\n"
        "  return <div>second</div>\n"
        "}\n"
    )
    files = {"frontend/src/App.jsx": content}
    changed = _hard_dedup_jsx_files(files)
    assert "frontend/src/App.jsx" in changed
    result = files["frontend/src/App.jsx"]
    assert result.count("function Layout(") == 1
    assert "first" in result
    assert "second" not in result


def test_dedup_removes_duplicate_bare_layout():
    content = (
        "function BareLayout() { return <div>a</div> }\n\n"
        "function BareLayout() { return <div>b</div> }\n"
    )
    files = {"frontend/src/App.jsx": content}
    _hard_dedup_jsx_files(files)
    assert files["frontend/src/App.jsx"].count("function BareLayout(") == 1


def test_dedup_idempotent_when_no_duplicates():
    content = (
        "function Layout() { return <div /> }\n\n"
        "function BareLayout() { return <div /> }\n"
    )
    files = {"frontend/src/App.jsx": content}
    changed = _hard_dedup_jsx_files(files)
    assert changed == []


def test_dedup_handles_nested_braces_in_function_body():
    content = (
        "function Layout() {\n"
        "  const x = { a: 1, b: { c: 2 } }\n"
        "  return <div>{x.a}</div>\n"
        "}\n\n"
        "function Layout() {\n"
        "  return <div>second</div>\n"
        "}\n"
    )
    files = {"frontend/src/App.jsx": content}
    _hard_dedup_jsx_files(files)
    result = files["frontend/src/App.jsx"]
    assert result.count("function Layout(") == 1
    assert "x.a" in result
    assert "second" not in result


# ── _app_jsx_has_layout ──────────────────────────────────────────────────────

def test_app_jsx_has_layout_detects_function_declaration():
    content = "function Layout() { return <div /> }\n"
    has_l, has_b = _app_jsx_has_layout(content)
    assert has_l is True
    assert has_b is False


def test_app_jsx_has_layout_detects_const_arrow():
    content = "const Layout = () => <div />\n"
    has_l, _ = _app_jsx_has_layout(content)
    assert has_l is True


def test_app_jsx_has_layout_detects_export_default():
    content = "export default function Layout() { return null }\n"
    has_l, _ = _app_jsx_has_layout(content)
    assert has_l is True


# ── _fix_app_jsx_layout_pattern injection guard ──────────────────────────────

def test_layout_injector_skips_when_both_present():
    """Injector must NOT add Layout when both Layout and BareLayout exist.
    This is the root cause of the duplicate-declaration bug."""
    content = (
        "function Layout() { return <div /> }\n\n"
        "function BareLayout() { return <div /> }\n"
    )
    files = {"frontend/src/App.jsx": content}
    changed = _fix_app_jsx_layout_pattern({}, files)
    assert changed == {}, "should return empty dict when both present"
    assert files["frontend/src/App.jsx"].count("function Layout(") == 1
    assert files["frontend/src/App.jsx"].count("function BareLayout(") == 1


# ── _resolve_layout_import_inline_conflict ────────────────────────────────────

def test_resolves_default_import_with_inline_function():
    content = (
        "import Layout from '@/components/Layout'\n"
        "import React from 'react'\n\n"
        "function Layout() { return <div /> }\n"
    )
    out = _resolve_layout_import_inline_conflict(content)
    assert "import Layout from" not in out
    assert "import React from 'react'" in out
    assert "function Layout()" in out


def test_resolves_named_import_with_inline_function():
    content = (
        "import { Layout } from '@/components/Layout'\n"
        "import React from 'react'\n\n"
        "function Layout() { return <div /> }\n"
    )
    out = _resolve_layout_import_inline_conflict(content)
    assert "import { Layout }" not in out
    assert "import React from 'react'" in out


def test_filters_layout_from_mixed_named_import():
    content = (
        "import { Header, Layout, Footer } from '@/components'\n\n"
        "function Layout() { return <div /> }\n"
    )
    out = _resolve_layout_import_inline_conflict(content)
    # Layout removed from import, Header and Footer survive
    pre_func = out.split("function Layout")[0]
    assert "Layout" not in pre_func or "{ Header" in pre_func
    assert "Header" in out and "Footer" in out
    assert "function Layout()" in out


def test_resolves_both_layout_and_barelayout():
    content = (
        "import { Layout, BareLayout } from '@/components'\n\n"
        "function Layout() { return <div /> }\n"
        "function BareLayout() { return <div /> }\n"
    )
    out = _resolve_layout_import_inline_conflict(content)
    assert "import { Layout, BareLayout }" not in out
    assert "function Layout()" in out
    assert "function BareLayout()" in out


def test_handles_aliased_import():
    content = (
        "import { Foo as Layout } from '@/components'\n\n"
        "function Layout() { return <div /> }\n"
    )
    out = _resolve_layout_import_inline_conflict(content)
    assert "Foo as Layout" not in out
    assert "function Layout()" in out


def test_idempotent_when_no_conflict():
    content = (
        "import { Layout } from '@/components'\n"
        "export default function App() { return <Layout /> }\n"
    )
    out = _resolve_layout_import_inline_conflict(content)
    assert out == content


def test_no_change_when_no_inline_decl():
    content = (
        "import { Layout } from '@/components'\n"
        "const App = () => <Layout />\n"
    )
    out = _resolve_layout_import_inline_conflict(content)
    assert out == content
