"""Unit tests for JSX code-quality debugger helpers."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ai.claude.agents.debugger import (
    _fix_component_rendered_as_child,
    _fix_shadcn_button_aschild,
)


# ── _fix_component_rendered_as_child ─────────────────────────────────────────

def test_fix_component_rendered_as_child_rewrites_empty_state_icon():
    content = (
        'export function EmptyState({ icon, title }) {\n'
        '  return (\n'
        '    <div className="empty">\n'
        '      {icon && <div className="icon">{icon}</div>}\n'
        '      <h3>{title}</h3>\n'
        '    </div>\n'
        '  )\n'
        '}\n'
    )
    files = {"frontend/src/components/ui/empty-state.jsx": content}
    fixes = _fix_component_rendered_as_child({}, files)
    assert "frontend/src/components/ui/empty-state.jsx" in fixes
    out = fixes["frontend/src/components/ui/empty-state.jsx"]
    assert "<__C" in out or "<IconComp" in out
    assert ">{icon}</div>" not in out


def test_fix_component_rendered_as_child_skips_already_correct():
    content = (
        'export function EmptyState({ icon }) {\n'
        '  const Icon = icon;\n'
        '  return <div>{Icon ? <Icon /> : null}</div>;\n'
        '}\n'
    )
    files = {"frontend/src/components/ui/empty-state.jsx": content}
    fixes = _fix_component_rendered_as_child({}, files)
    assert fixes == {}


def test_fix_component_rendered_as_child_skips_non_target_files():
    content = (
        'export function Page({ icon }) {\n'
        '  return <div>{icon}</div>;\n'
        '}\n'
    )
    # Not in the target paths list, so must be skipped.
    files = {"frontend/src/pages/SomePage.jsx": content}
    fixes = _fix_component_rendered_as_child({}, files)
    assert fixes == {}


def test_fix_component_rendered_as_child_skips_when_no_icon_content():
    content = (
        'export function EmptyState({ title }) {\n'
        '  return <div><h3>{title}</h3></div>;\n'
        '}\n'
    )
    files = {"frontend/src/components/ui/empty-state.jsx": content}
    fixes = _fix_component_rendered_as_child({}, files)
    assert fixes == {}


# ── _fix_shadcn_button_aschild ────────────────────────────────────────────────

def test_fix_shadcn_button_adds_aschild_destructure():
    content = (
        'import * as React from "react"\n'
        'export const Button = React.forwardRef(\n'
        '  ({ className, variant, ...props }, ref) => {\n'
        '    return (\n'
        '      <button className={className} ref={ref} {...props} />\n'
        '    )\n'
        '  }\n'
        ')\n'
    )
    files = {"frontend/src/components/ui/button.jsx": content}
    fixes = _fix_shadcn_button_aschild({}, files)
    out = fixes.get("frontend/src/components/ui/button.jsx", "")
    assert "asChild = false" in out
    assert "Slot" in out
    assert "<Comp" in out


def test_fix_shadcn_button_idempotent():
    content = (
        'import * as React from "react"\n'
        'import { Slot } from "@radix-ui/react-slot"\n'
        'export const Button = React.forwardRef(\n'
        '  ({ className, asChild = false, ...props }, ref) => {\n'
        '    const Comp = asChild ? Slot : "button";\n'
        '    return <Comp className={className} ref={ref} {...props} />;\n'
        '  }\n'
        ')\n'
    )
    files = {"frontend/src/components/ui/button.jsx": content}
    fixes = _fix_shadcn_button_aschild({}, files)
    assert fixes == {}


def test_fix_shadcn_button_skips_missing_file():
    fixes = _fix_shadcn_button_aschild({}, {})
    assert fixes == {}


def test_fix_shadcn_button_skips_when_no_bare_button():
    content = (
        'import * as React from "react"\n'
        'export const Button = ({ children }) => <span>{children}</span>;\n'
    )
    files = {"frontend/src/components/ui/button.jsx": content}
    fixes = _fix_shadcn_button_aschild({}, files)
    assert fixes == {}
