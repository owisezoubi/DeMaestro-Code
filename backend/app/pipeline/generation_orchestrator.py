"""Generation orchestrator — full state machine (W5-P2).

State machine:
  approved → generating → generated → testing → tested
           → verifying → verified → packaging → ready | ready_with_warnings
  (on any failure) → failed

Best-effort packaging: if tests fail after all debug cycles, the pipeline
still packages the ZIP and sets status to ready_with_warnings so the user
can inspect and fix the code themselves.
"""
import ast
import dataclasses
import json
import os
import re as _re
import threading
from datetime import datetime, timezone
from pathlib import Path

import structlog

from app.ai.claude.agents.architect import ArchitectAgent
from app.config import settings, get_agent_model
from app.pipeline.checklist import coerce_checklist
from app.pipeline.checklist_runner import run_checklist, check_import_resolution
from app.ai.claude.agents.debugger import (
    DebuggerAgent,
    _is_infrastructure_error,
    _fix_upload_static_mount,
    _detect_duplicate_top_level_decls,
    _auto_stub_missing_contract_endpoints,
)
from app.ai.claude.agents.deployer import DeployerAgent
from app.ai.claude.agents.generator import (
    GeneratorAgent,
    _check_contract_coverage,
    _route_file_for_endpoint,
)
from app.ai.claude.agents.tester import TesterAgent
from app.ai.claude.agents.verifier import VerifierAgent
from app.ai.gemini.agents.blueprint import BlueprintResponse
from app.models.generation_plan import GenerationPlan
from app.models.project import ProjectStatus
from app.services import firestore_service
from app.services import template_service
from app.services import vercel_deployer

log = structlog.get_logger("GenerationOrchestrator")


def _resolve_app_name(
    sr,
    project_name_from_firestore: "str | None",
    project_id: str,
) -> str:
    """Resolve the human-readable app name in strict priority order:
       1. sr.app_name          — Analyst + Clarification result (best)
       2. project.name         — What the user typed in NewProject
       3. project_id (last)    — Hash fallback, ugly but never empty
    """
    for candidate in (
        getattr(sr, "app_name", None),
        project_name_from_firestore,
        project_id,
    ):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return "demaestro-app"

_MAX_TEST_CYCLES = 10

# ── Cancellation registry ────────────────────────────────────────────────────

_CANCELLATION_FLAGS: dict[str, bool] = {}
_ACTIVE_PROJECTS: dict[str, bool] = {}


def request_cancellation(project_id: str) -> None:
    _CANCELLATION_FLAGS[project_id] = True
    log.info("pipeline.cancellation_requested", project_id=project_id)


def is_cancelled(project_id: str) -> bool:
    return _CANCELLATION_FLAGS.get(project_id, False)


def clear_cancellation(project_id: str) -> None:
    _CANCELLATION_FLAGS.pop(project_id, None)


def is_active(project_id: str) -> bool:
    return _ACTIVE_PROJECTS.get(project_id, False)


def _mark_active(project_id: str) -> None:
    _ACTIVE_PROJECTS[project_id] = True


def _mark_inactive(project_id: str) -> None:
    _ACTIVE_PROJECTS.pop(project_id, None)

# ── Snapshot / replay system ─────────────────────────────────────────────────

SNAPSHOT_ROOT = Path.home() / ".demaestro" / "snapshots"

# Set DEMAESTRO_LLM_DEBUG=false to run only algorithmic debug fixes (no Claude
# calls) during test/debug cycles.  Useful for iterating on deterministic helpers
# without spending API tokens.
USE_LLM_DEBUG = os.environ.get("DEMAESTRO_LLM_DEBUG", "true").lower() == "true"


def _save_snapshot(
    project_id: str,
    files: dict,
    blueprint: dict,
    stage: str,
    plan_dict: dict | None = None,
    checklist: list | None = None,
    checklist_results: list | None = None,
) -> Path:
    """Save the current file tree + metadata to ~/.demaestro/snapshots/.

    stage: 'post_generate' | 'post_cycle_N' | 'final'
    Returns the snapshot directory path.
    Never raises — errors are logged and swallowed so the pipeline is not
    interrupted.
    """
    try:
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        slug = (blueprint.get("app_name") or project_id).lower()
        slug = _re.sub(r"[^a-z0-9]+", "-", slug).strip("-") or "project"
        dir_name = f"{slug}__{project_id[:8]}__{ts}__{stage}"
        target = SNAPSHOT_ROOT / dir_name
        target.mkdir(parents=True, exist_ok=True)

        for path, content in files.items():
            file_target = target / path
            file_target.parent.mkdir(parents=True, exist_ok=True)
            try:
                file_target.write_text(content, encoding="utf-8")
            except (UnicodeEncodeError, TypeError):
                file_target.write_bytes(content.encode("utf-8", errors="replace"))

        (target / "_blueprint.json").write_text(
            json.dumps(blueprint, indent=2), encoding="utf-8"
        )
        manifest: dict = {
            "project_id": project_id,
            "stage": stage,
            "saved_at": ts,
            "num_files": len(files),
        }
        if plan_dict:
            manifest["plan"] = plan_dict
        (target / "_manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        if checklist:
            (target / "checklist.json").write_text(
                json.dumps(checklist, indent=2, ensure_ascii=True), encoding="utf-8"
            )
        log.info("snapshot.saved", path=str(target), stage=stage, num_files=len(files))
        return target
    except Exception as exc:
        log.warning("snapshot.save_failed", stage=stage, project_id=project_id, error=str(exc))
        return SNAPSHOT_ROOT  # fallback — caller ignores return value in most cases


def _diff_imports(before: str, after: str) -> dict:
    """Return {added, removed} sets of import lines between two file versions."""
    _imp_re = _re.compile(
        r"^(?:from\s+\S+\s+import\s+[^\n]+|import\s+[^\n]+)$",
        _re.MULTILINE,
    )
    before_set = set(_imp_re.findall(before or ""))
    after_set  = set(_imp_re.findall(after or ""))
    return {
        "added":   sorted(after_set - before_set),
        "removed": sorted(before_set - after_set),
    }


_BUILTIN_NAMES = {
    "int", "str", "float", "bool", "list", "dict", "set", "tuple",
    "bytes", "None", "True", "False", "object", "type", "Any",
    "Optional", "Union", "Callable", "Iterable", "Mapping",
}


def _collect_imports(tree) -> set:
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _collect_annotation_names(tree) -> set:
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and node.annotation:
            for sub in ast.walk(node.annotation):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in node.args.args + node.args.kwonlyargs:
                if arg.annotation:
                    for sub in ast.walk(arg.annotation):
                        if isinstance(sub, ast.Name):
                            names.add(sub.id)
    return names


def _post_fix_validate(
    files: dict,
    modified_paths: list,
    pre_debug_snapshot: dict,
) -> None:
    """Cheap static pass over debugger-modified Python files.

    Catches two classes of bug immediately:
      - SyntaxError: rolls back to pre-debug content so the next cycle
        starts from a known-parseable file.
      - Annotation names not covered by any import: logs a warning so
        the "used but not imported" bug is visible in the same cycle.
    """
    for path in modified_paths:
        if not path.endswith(".py"):
            continue
        content = files.get(path, "")
        if not content:
            continue
        try:
            tree = ast.parse(content)
        except SyntaxError as exc:
            log.error("debug.post_fix.syntax_error", path=path, error=str(exc))
            if path in pre_debug_snapshot:
                files[path] = pre_debug_snapshot[path]
            continue

        imports = _collect_imports(tree)
        annotations = _collect_annotation_names(tree)
        missing = annotations - imports - _BUILTIN_NAMES
        if missing:
            log.warning(
                "debug.post_fix.unresolved_names",
                path=path,
                missing=sorted(missing),
            )


# Check names that block ZIP.  typecheck remains advisory.
# contract is REAL only for critical misses; the tester returns "advisory_failed"
# for minor endpoint gaps — that value is excluded here.
# smoke and reachability: now REAL (not advisory) — login failures block ZIP.
# fe_be_contract: REAL when DEMAESTRO_FE_BE_CONTRACT_STRICT=true (default).
_REAL_CHECK_NAMES: frozenset = frozenset({
    "install", "lint", "boot", "frontend_build", "contract", "route_ordering",
    "route_link_consistency", "navbar_renders_links",
    "smoke",           # strict auth check (register→login→/me must all pass)
    "reachability",    # endpoint existence probe (404/405/5xx = FAIL)
    "fe_be_contract",  # offline frontend↔backend body-shape match
    "verify",          # blueprint↔generated-code route existence check
    "browser_smoke",   # Playwright: catches runtime React errors (Objects not valid as child, etc.)
})


def _apply_strict_gate(
    test_results: dict,
    generated_files: dict,
    blueprint_dict: dict,
    plan_dict: dict,
    uid: str,
    project_id: str,
) -> set:
    """Centralised pre-deploy gate shared by run_full_pipeline and run_test_debug_only.

    Checks passed_checks for any REAL failure.  When failures are found, updates
    Firestore to tests_failed_recoverable and saves a snapshot.

    Returns the set of failing check names (empty = clear to proceed, non-empty =
    gate fired — caller must abort / return without deploying).
    """
    final_checks = (test_results or {}).get("passed_checks", {}) or {}
    gate_failures = {
        c for c, s in final_checks.items()
        if s == "failed" and c in _REAL_CHECK_NAMES
    }
    if not gate_failures:
        return set()

    gate_msg = (
        f"Tests did not pass: {', '.join(sorted(gate_failures))}. "
        "Generated files are preserved — use 'Re-run Tests' to retry."
    )
    log.error(
        "pipeline.strict_failure",
        project_id=project_id,
        real_failures=sorted(gate_failures),
        reason="real_failures_present_before_deploy",
    )
    firestore_service.update_project(uid, project_id, {
        "status": ProjectStatus.tests_failed_recoverable,
        "current_stage": "testing",
        "last_error": gate_msg,
        "last_failed_checks": sorted(gate_failures),
        "real_failures": sorted(gate_failures),
        "test_error_log": (
            "\n".join(test_results.get("errors", []) or [])
        )[:5000],
    })
    try:
        _save_snapshot(project_id, generated_files, blueprint_dict, "final", plan_dict)
    except Exception:
        pass
    return gate_failures


def _blueprint_auth_enabled(blueprint_dict: dict, sr=None) -> bool:
    """Return True when auth is enabled (the default), False when explicitly disabled."""
    # Check blueprint.frontend.auth.enabled first (set by _enforce_auth_policy).
    fe_auth = (
        blueprint_dict.get("frontend", {}).get("auth", {})
        if isinstance(blueprint_dict.get("frontend"), dict)
        else {}
    )
    if isinstance(fe_auth, dict) and "enabled" in fe_auth:
        return bool(fe_auth["enabled"])
    # Fall back to sr.auth_required: False = disabled, True/None = enabled.
    if sr is not None and getattr(sr, "auth_required", None) is False:
        return False
    return True


def _build_pages_md(blueprint: BlueprintResponse, app_name: str, auth_enabled: bool = True) -> str:
    """Build a human-readable PAGES.md summarising all routes and API endpoints."""
    lines = [
        f"# {app_name} — Pages",
        "",
        "Quick reference for what's in this app without running it.",
        "",
    ]

    pages = blueprint.frontend_pages
    if pages:
        lines += [
            "## Routes",
            "",
            "| Path | Page | Description |",
            "|------|------|-------------|",
        ]
        for p in pages:
            desc = getattr(p, "purpose", "") or ""
            lines.append(f"| `{p.route}` | {p.name} | {desc} |")
        lines.append("")

    routes = blueprint.api_routes
    if routes:
        lines += [
            "## API endpoints",
            "",
            "| Method | Path | Description |",
            "|--------|------|-------------|",
        ]
        for r in routes:
            lines.append(f"| {r.method} | `{r.path}` | {r.description} |")
        lines.append("")

    if auth_enabled:
        lines += [
            "## Demo credentials",
            "",
            "After running `start.sh`, the seed inserts:",
            "- `demo@example.com` / `demo1234` (regular user)",
            "- `admin@example.com` / `admin1234` (admin)",
            "",
        ]
    else:
        lines += [
            "## Access",
            "",
            "This app is fully public — no sign-in needed.",
            "",
        ]

    lines += [
        "## Running locally",
        "",
        "```bash",
        "bash start.sh",
        "```",
        "Open http://localhost:5273 in your browser.",
        "",
    ]
    return "\n".join(lines)

# Auth scaffold files — loaded from the stack template directory and injected
# verbatim into every project.  The LLM must never generate or overwrite these.
_AUTH_SCAFFOLD_FILES = frozenset({
    "backend/app/auth.py",
    "backend/app/main.py",               # pre-seeded with CORS + health + auth router
    "backend/app/routes/auth_routes.py",
    "frontend/src/lib/auth.js",
    "frontend/src/contexts/AuthContext.jsx",
})

# Variant template files that are loaded from the scaffold directory but must
# never appear verbatim in the generated output — they're renamed or discarded.
_NO_AUTH_VARIANT_FILES = frozenset({
    "backend/app/main_no_auth.py",
    "frontend/src/App_no_auth.jsx",
    "frontend/src/lib/routes_no_auth.js",
})

_TAILWIND_PALETTES: dict[str, dict[str, str]] = {
    "blue":    {"50":"#EFF6FF","100":"#DBEAFE","200":"#BFDBFE","300":"#93C5FD",
                "400":"#60A5FA","500":"#3B82F6","600":"#2563EB","700":"#1D4ED8","800":"#1E40AF","900":"#1E3A8A"},
    "indigo":  {"50":"#EEF2FF","100":"#E0E7FF","200":"#C7D2FE","300":"#A5B4FC",
                "400":"#818CF8","500":"#6366F1","600":"#4F46E5","700":"#4338CA","800":"#3730A3","900":"#312E81"},
    "red":     {"50":"#FEF2F2","100":"#FEE2E2","200":"#FECACA","300":"#FCA5A5",
                "400":"#F87171","500":"#EF4444","600":"#DC2626","700":"#B91C1C","800":"#991B1B","900":"#7F1D1D"},
    "rose":    {"50":"#FFF1F2","100":"#FFE4E6","200":"#FECDD3","300":"#FDA4AF",
                "400":"#FB7185","500":"#F43F5E","600":"#E11D48","700":"#BE123C","800":"#9F1239","900":"#881337"},
    "green":   {"50":"#F0FDF4","100":"#DCFCE7","200":"#BBF7D0","300":"#86EFAC",
                "400":"#4ADE80","500":"#22C55E","600":"#16A34A","700":"#15803D","800":"#166534","900":"#14532D"},
    "emerald": {"50":"#ECFDF5","100":"#D1FAE5","200":"#A7F3D0","300":"#6EE7B7",
                "400":"#34D399","500":"#10B981","600":"#059669","700":"#047857","800":"#065F46","900":"#064E3B"},
    "amber":   {"50":"#FFFBEB","100":"#FEF3C7","200":"#FDE68A","300":"#FCD34D",
                "400":"#FBBF24","500":"#F59E0B","600":"#D97706","700":"#B45309","800":"#92400E","900":"#78350F"},
    "orange":  {"50":"#FFF7ED","100":"#FFEDD5","200":"#FED7AA","300":"#FDBA74",
                "400":"#FB923C","500":"#F97316","600":"#EA580C","700":"#C2410C","800":"#9A3412","900":"#7C2D12"},
    "purple":  {"50":"#FAF5FF","100":"#F3E8FF","200":"#E9D5FF","300":"#D8B4FE",
                "400":"#C084FC","500":"#A855F7","600":"#9333EA","700":"#7E22CE","800":"#6B21A8","900":"#581C87"},
    "pink":    {"50":"#FDF2F8","100":"#FCE7F3","200":"#FBCFE8","300":"#F9A8D4",
                "400":"#F472B6","500":"#EC4899","600":"#DB2777","700":"#BE185D","800":"#9D174D","900":"#831843"},
    "teal":    {"50":"#F0FDFA","100":"#CCFBF1","200":"#99F6E4","300":"#5EEAD4",
                "400":"#2DD4BF","500":"#14B8A6","600":"#0D9488","700":"#0F766E","800":"#115E59","900":"#134E4A"},
    "slate":   {"50":"#F8FAFC","100":"#F1F5F9","200":"#E2E8F0","300":"#CBD5E1",
                "400":"#94A3B8","500":"#64748B","600":"#475569","700":"#334155","800":"#1E293B","900":"#0F172A"},
    "navy":    {"50":"#F2F6FB","100":"#DBE5F1","200":"#B8CCE2","300":"#8BAAD0",
                "400":"#5278A8","500":"#34588A","600":"#1F3A68","700":"#172C50","800":"#0F1E37","900":"#0A1428"},
}

_VIBE_KEYWORDS: dict[str, list[str]] = {
    "minimalist":   ["minimalist", "minimal", "clean and simple", "spare", "uncluttered"],
    "playful":      ["playful", "fun", "vibrant", "lively", "colorful"],
    "professional": ["professional", "corporate", "business-like", "formal"],
    "warm":         ["warm", "inviting", "cozy", "welcoming"],
    "elegant":      ["elegant", "luxurious", "premium", "refined"],
    "modern":       ["modern", "contemporary", "sleek"],
}


def _detect_design_brief(sr) -> dict | None:
    """Scan requirements for explicit color words. Returns a dict only when the
    user actually asked for a specific color ('orange and white', 'blue theme',
    'make it green'). Returns None so the safe neutral default ships unchanged."""
    haystack_parts: list[str] = []
    haystack_parts.append(sr.summary or "")
    for r in sr.user_requirements:
        haystack_parts.append(r.statement or "")
        haystack_parts.append(r.rationale or "")
    haystack = " ".join(haystack_parts).lower()

    chosen_color: str | None = None
    for color in _TAILWIND_PALETTES.keys():
        patterns = [
            rf"\b{color}\b\s+(?:and|with)\s+(?:white|black|gray|slate|cream|beige)",
            rf"(?:white|black|gray|slate|cream|beige)\s+(?:and|with)\s+\b{color}\b",
            rf"\b{color}\s+(?:colors?|theme|themes|palette|tones?|style|scheme|shade|hue|accent)",
            rf"\b(?:color|theme|palette|scheme|accent)\s+(?:is|of|in)?\s*\b{color}\b",
            rf"\b(?:in|with|using|prefer|like|want|go\s+with|styled?\s+in|use)\s+\w*\s*\b{color}\b",
            rf"\b(?:website|site|app|page|design|background)\s+(?:in|with)\s+\b{color}\b",
            rf"\b{color}\b\s+(?:website|site|app|page|design|background|UI|ui)",
        ]
        for pat in patterns:
            if _re.search(pat, haystack):
                chosen_color = color
                break
        if chosen_color:
            break

    # Return None when no explicit color is mentioned — let the neutral default ship as-is.
    if not chosen_color:
        return None

    chosen_vibe: str | None = None
    for vibe, keys in _VIBE_KEYWORDS.items():
        for k in keys:
            if k in haystack:
                chosen_vibe = vibe
                break
        if chosen_vibe:
            break

    return {
        "primary_color": chosen_color,
        "vibe": chosen_vibe,
        "cues": [c for c in [chosen_color, chosen_vibe] if c],
    }


def _hex_to_rgb_space(hex_color: str) -> str:
    """Convert '#EA580C' → '234 88 12' for CSS custom property values."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"{r} {g} {b}"


_BAD_COLOR_PATTERNS = [
    # bg-slate-800/900 without a paired text-slate-100/white/primary
    (_re.compile(r'className="[^"]*\bbg-slate-(?:8|9)\d\d\b(?:(?!text-(?:slate-[1-2]|white|primary))[^"])*"'),
     "bg-slate-{800-900} without text-slate-100/200/white/primary — text invisible"),
    # dark:bg without dark:text
    (_re.compile(r'className="[^"]*\bdark:bg-(?:slate|gray)-(?:8|9)\d\d\b(?:(?!dark:text-)[^"])*"'),
     "dark:bg-{slate|gray}-{800-900} without dark:text-X — text invisible in dark mode"),
    # bare text-slate-800/900 with no dark: variant — invisible on dark backgrounds
    (_re.compile(r'className="[^"]*\btext-slate-(?:8|9)\d\d\b(?:(?!dark:text-)[^"])*"'),
     "text-slate-{800-900} without dark:text-X — text invisible on dark surfaces, use text-default"),
    # bg-white text-white
    (_re.compile(r'className="[^"]*\bbg-white\b[^"]*\btext-white\b[^"]*"'),
     "bg-white text-white — text invisible"),
    # bg-slate-900 text-slate-900 (same-shade dead text)
    (_re.compile(r'className="[^"]*\bbg-slate-900\b[^"]*\btext-slate-900\b[^"]*"'),
     "bg-slate-900 text-slate-900 — text invisible (same shade)"),
    # text-slate-600 on dark backgrounds (dark:text-slate-600 is too dim)
    (_re.compile(r'className="[^"]*\bdark:text-slate-(?:5|6)\d\d\b[^"]*\bdark:bg-slate-(?:8|9)\d\d\b[^"]*"'),
     "dark:text-slate-{500-600} on dark:bg-slate-{800-900} — low contrast in dark mode"),
    # Hardcoded accent colors (break palette swap)
    (_re.compile(r'className="[^"]*\b(?:bg|text|border|ring)-(?:blue|red|emerald|orange|purple|pink|cyan)-(?:5|6|7)\d\d\b[^"]*"'),
     "Hardcoded accent color — use primary-* instead so palette swap works"),
    # text-primary-300 or lighter on light backgrounds (too faint)
    (_re.compile(r'className="[^"]*\btext-primary-[123]\d\d\b(?:(?!dark:)[^"])*"'),
     "text-primary-{100-300} without dark: qualifier — too light for body text on white"),
]


_BAD_THEME_PATTERN = _re.compile(
    r'(ThemeContext|ThemeProvider)[\s\S]+useEffect[\s\S]+localStorage\.setItem[\s\S]+\}\s*,\s*\[theme\]'
)


def _scan_color_violations(generated_files: dict) -> list:
    """Return a list of 'FILE:LINE problem' strings for color combos that will
    make the UI broken in dark mode or break the palette swap."""
    violations = []
    for path, content in generated_files.items():
        if not (path.endswith(".jsx") or path.endswith(".tsx")):
            continue
        for i, line in enumerate((content or "").splitlines(), 1):
            for pat, msg in _BAD_COLOR_PATTERNS:
                if pat.search(line):
                    violations.append(f"{path}:{i} — {msg}")
                    break
    for path, content in generated_files.items():
        if not (path.endswith(".jsx") or path.endswith(".js") or path.endswith(".tsx")):
            continue
        if "ThemeContext" in path or "ThemeProvider" in path or "theme" in path.lower():
            c = content or ""
            if "useEffect" in c and "documentElement" not in c:
                violations.append(
                    f"{path}:0 — ThemeContext useEffect missing documentElement.classList toggle "
                    "(dark mode will not visually apply)"
                )
            if "localStorage.setItem" in c and "classList" not in c:
                violations.append(
                    f"{path}:0 — ThemeContext writes to localStorage but never toggles "
                    "document.documentElement.classList — dark: variants stay inactive"
                )
    return violations


def _apply_design_brief_to_scaffolding(generated_files: dict, brief: dict | None) -> None:
    """Override --accent in index.css to match the user-requested color.
    No-op when brief is None. Never touches dark-mode config."""
    if brief is None:
        return
    color = brief.get("primary_color")
    if not color or color not in _TAILWIND_PALETTES:
        return
    palette = _TAILWIND_PALETTES[color]
    accent_hex = palette.get("600") or palette.get("500") or "#2563EB"
    accent_rgb = _hex_to_rgb_space(accent_hex)
    css_path = "frontend/src/index.css"
    css = generated_files.get(css_path)
    if not css or "--accent:" not in css:
        return
    new_css = _re.sub(
        r"(--accent:\s*)[\d ]+;",
        rf"\g<1>{accent_rgb};",
        css,
        count=1,
    )
    if new_css != css:
        generated_files[css_path] = new_css
        log.info("pipeline.design_brief_applied", color=color, accent_rgb=accent_rgb)


def _item_to_dict(item) -> dict:
    """Convert a checklist dataclass instance to a plain dict."""
    try:
        return dataclasses.asdict(item)
    except Exception:
        return {}


def _checklist_results_to_payload(results: list) -> list:
    """Convert CheckResult objects to serialisable dicts, deduping by item_id.

    Keeps the LATEST entry per item_id. Duplicates appear because live-refresh
    emits fresh results every debug cycle — without dedup the frontend can
    show >100%.
    """
    seen: dict = {}
    for r in results:
        seen[r.item_id] = {
            "item_id": r.item_id,
            "status": r.status,
            "reason": r.reason or "",
        }
    return list(seen.values())


class GenerationOrchestrator:
    def __init__(self) -> None:
        self.architect = ArchitectAgent()
        self.generator = GeneratorAgent()
        self.tester = TesterAgent()
        self.debugger = DebuggerAgent()
        self.verifier = VerifierAgent()
        self.deployer = DeployerAgent()

    # ── Post-deploy fix loop ──────────────────────────────────────────────────

    _MAX_POST_DEPLOY_FIXES = 2

    def _post_deploy_fix_loop(
        self,
        project_id: str,
        uid: str,
        deployment_result: dict,
        generated_files: dict,
        contract_paths: list[dict] | None = None,
    ) -> None:
        """After initial Vercel deploy, smoke-test the live URL.
        If 500s appear, call the agentic debugger, apply patches,
        and redeploy. Up to _MAX_POST_DEPLOY_FIXES cycles.
        Writes deployment_status / deployment_health back to Firestore.
        """
        url = deployment_result["url"]
        deployment_id = deployment_result["deployment_id"]
        project_name = deployment_result["project_name"]

        for attempt in range(1, self._MAX_POST_DEPLOY_FIXES + 1):
            import time as _time
            _time.sleep(5)  # let the function warm up

            smoke = vercel_deployer.post_deploy_smoke(url, contract_paths=contract_paths)
            if smoke["healthy"]:
                log.info(
                    "post_deploy.healthy",
                    project_id=project_id, url=url, attempt=attempt,
                )
                firestore_service.update_project(uid, project_id, {
                    "deployment_status": "ready",
                    "deployment_url": url,
                    "deployment_health": "ok",
                })
                return

            # Fetch runtime logs for additional context.
            runtime_logs = vercel_deployer.fetch_vercel_runtime_logs(
                os.environ.get("DEMAESTRO_VERCEL_TOKEN", ""),
                deployment_id,
            )
            log.warning(
                "post_deploy.unhealthy",
                project_id=project_id, attempt=attempt,
                endpoint_errors=smoke["endpoint_errors"][:5],
                runtime_log_count=len(runtime_logs),
            )

            # Build synthetic test_results so the agentic debugger understands the context.
            synthetic_errors = [
                f"POST-DEPLOY {e['method']} {e['path']} -> "
                f"{e['status']}: {e['body_preview'][:200]}"
                for e in smoke["endpoint_errors"]
            ]
            synthetic_results = {
                "errors": synthetic_errors,
                "logs": {
                    "post_deploy_smoke": "\n".join(synthetic_errors),
                    "vercel_runtime": "\n".join(
                        f"[{l['type']}] {l['text']}" for l in runtime_logs
                    ),
                },
                "passed_checks": {},
            }

            patches = self.debugger._agentic_holistic_fix(
                synthetic_results, generated_files, max_attempts=1,
            )
            if not patches:
                log.error(
                    "post_deploy.agentic_no_patches",
                    project_id=project_id, attempt=attempt,
                )
                firestore_service.update_project(uid, project_id, {
                    "deployment_status": "ready_with_errors",
                    "deployment_url": url,
                    "deployment_health": "errors",
                    "deployment_error": (
                        "Live app has runtime errors that agentic "
                        "fallback could not resolve. See Vercel logs."
                    ),
                })
                return

            generated_files.update(patches)
            firestore_service.update_project(uid, project_id, {
                "generated_files": generated_files,
                "deployment_status": "redeploying",
            })
            log.info(
                "post_deploy.patches_applied",
                project_id=project_id, attempt=attempt,
                files=list(patches.keys()),
            )

            try:
                deployment_result = vercel_deployer.deploy(
                    project_name=project_name,
                    files=generated_files,
                    wait_for_ready=True,
                )
                url = deployment_result["url"]
                deployment_id = deployment_result["deployment_id"]
                firestore_service.update_project(uid, project_id, {
                    "deployment_url": url,
                })
            except vercel_deployer.VercelDeployError as e:
                log.error("post_deploy.redeploy_failed", project_id=project_id, error=str(e))
                firestore_service.update_project(uid, project_id, {
                    "deployment_status": "failed",
                    "deployment_error": f"redeploy failed: {e}",
                })
                return

        log.warning("post_deploy.max_attempts_exhausted", project_id=project_id, url=url)
        firestore_service.update_project(uid, project_id, {
            "deployment_status": "ready_with_errors",
            "deployment_url": url,
            "deployment_health": "errors",
            "deployment_error": (
                f"Live app still failing after "
                f"{self._MAX_POST_DEPLOY_FIXES} post-deploy fix attempts. "
                "Manual debugging required."
            ),
        })

    # ── W5-P1 simple entry point (kept for backward-compat) ──────────────────

    def run_generation(self, uid: str, project_id: str) -> dict:
        """Architect + generate only (no test/debug/verify/deploy).

        Returns: { status, generated_files, plan, errors }
        """
        log.info("generation.start", project_id=project_id)
        log.info(
            "pipeline.models",
            project_id=project_id,
            architect=get_agent_model("architect"),
            generator=get_agent_model("generator"),
            debugger=get_agent_model("debugger"),
            verifier=get_agent_model("verifier"),
            modifier=get_agent_model("modifier"),
        )

        try:
            project = firestore_service.get_project(uid, project_id)
            if project is None:
                raise ValueError(f"Project {project_id} not found")

            sr = firestore_service.get_latest_structured_requirements(uid, project_id)
            if sr is None:
                raise ValueError(f"No structured requirements for project {project_id}")

            blueprint_dict = project.blueprint
            if blueprint_dict is None:
                raise ValueError(f"No blueprint for project {project_id}")
            blueprint = BlueprintResponse(**blueprint_dict)

            existing_plan = project.generation_plan or None
            existing_files = project.generated_files or {}
            resuming = bool(existing_plan and existing_files)
            if resuming:
                log.info(
                    "pipeline.resuming",
                    project_id=project_id,
                    files_already_generated=len(existing_files),
                )

            if resuming:
                plan = GenerationPlan(**existing_plan)
                generated_files: dict[str, str] = dict(existing_files)
                _checklist: list = []
            else:
                plan, _checklist = self.architect.architect(sr, blueprint)
                log.info("generation.architect.done", project_id=project_id, num_files=len(plan.files))

                firestore_service.update_project(uid, project_id, {
                    "generation_plan": plan.model_dump(),
                })

                app_slug = template_service.slugify(sr.app_name)
                scaffolding = template_service.load_stack_templates(plan.technology_stack, sr.app_name, app_slug)
                generated_files = dict(scaffolding)

                # Auth mode: False = explicitly disabled, True/None = enabled.
                _auth_on = sr.auth_required is not False

                # Remove variant-template files — they're reference patterns for
                # the LLM, not output files for the generated project.
                for _vf in _NO_AUTH_VARIANT_FILES:
                    generated_files.pop(_vf, None)

                if _auth_on:
                    auth_seeded = sorted(_AUTH_SCAFFOLD_FILES & generated_files.keys())
                    log.info(
                        "generation.scaffolding.seeded",
                        project_id=project_id,
                        num_scaffold=len(scaffolding),
                        auth_scaffold_files=auth_seeded,
                    )
                else:
                    # Remove auth scaffold files so the LLM cannot import them.
                    for _af in _AUTH_SCAFFOLD_FILES:
                        generated_files.pop(_af, None)
                    # Inject an AUTH_DISABLED note into the plan so the generator
                    # prompt explicitly knows auth is off for every file it writes.
                    _auth_disabled_note = (
                        "AUTH_DISABLED: this app is fully public — no auth scaffold. "
                        "Do NOT generate LoginPage, RegisterPage, AuthContext, auth "
                        "routes, JWT code, or any auth-related UI. Navbar has no "
                        "Sign In / Sign Up / Log out links."
                    )
                    plan.notes = (
                        (plan.notes + "\n\n" + _auth_disabled_note)
                        if plan.notes else _auth_disabled_note
                    )
                    log.info(
                        "generation.scaffolding.auth_skipped",
                        project_id=project_id,
                        reason="auth_disabled_in_requirements",
                    )

                extras = [d.strip() for d in (plan.extra_dependencies or []) if d and d.strip()]
                if extras:
                    req_path = "backend/requirements.txt"
                    base_req = generated_files.get(req_path, "")
                    present = base_req.lower()
                    to_add = [
                        d for d in extras
                        if d.split("==")[0].split(">")[0].split("<")[0].strip().lower() not in present
                    ]
                    if to_add:
                        generated_files[req_path] = (
                            base_req.rstrip()
                            + "\n# --- app-specific dependencies (declared by the architect) ---\n"
                            + "\n".join(to_add) + "\n"
                        )
                        log.info("pipeline.extra_deps_added", project_id=project_id, deps=to_add)

                fe_extras = [d.strip() for d in (plan.extra_frontend_dependencies or []) if d and d.strip()]
                if fe_extras:
                    pkg_path = "frontend/package.json"
                    pkg_raw = generated_files.get(pkg_path, "")
                    try:
                        pkg = json.loads(pkg_raw)
                        deps = pkg.setdefault("dependencies", {})
                        added = [name for name in fe_extras if name not in deps]
                        for name in added:
                            deps[name] = "latest"
                        if added:
                            generated_files[pkg_path] = json.dumps(pkg, indent=2) + "\n"
                            log.info("pipeline.extra_frontend_deps_added", project_id=project_id, deps=added)
                    except Exception as exc:
                        log.warning("pipeline.frontend_deps_merge_failed", project_id=project_id, error=str(exc))

                design_brief = _detect_design_brief(sr)
                if design_brief is not None:
                    _apply_design_brief_to_scaffolding(generated_files, design_brief)
                    brief_note = (
                        "DESIGN BRIEF (apply consistently across every page): "
                        f"accent color = {design_brief.get('primary_color') or 'default'}; "
                        f"vibe = {design_brief.get('vibe') or 'clean and modern'}. "
                        "Use `bg-accent text-accent-fg hover:bg-accent/90` for primary buttons "
                        "and `text-accent` for links and icon accents — the --accent CSS variable "
                        "has been set to the requested color. Surface and text variables remain "
                        "neutral readable defaults."
                    )
                    try:
                        plan.notes = (plan.notes + "\n\n" + brief_note) if plan.notes else brief_note
                    except Exception:
                        pass

                # routes.js must be project-specific — remove scaffold default so
                # the LLM generates the real, blueprint-derived version.
                if "frontend/src/lib/routes.js" in plan.generation_order:
                    generated_files.pop("frontend/src/lib/routes.js", None)

                firestore_service.update_project(uid, project_id, {
                    "generated_files": generated_files,
                })

            for file_path in plan.generation_order:
                if file_path in generated_files:
                    continue
                file_to_gen = next((f for f in plan.files if f.path == file_path), None)
                if file_to_gen is None:
                    log.warning("generation.file_not_in_plan", file_path=file_path)
                    continue
                content = self.generator.generate_file(
                    file_to_gen, plan, blueprint, generated_files,
                    structured_requirements=sr,
                )
                generated_files[file_path] = content
                firestore_service.update_project(uid, project_id, {
                    "generated_files": generated_files,
                })

            log.info("generation.generate.done", project_id=project_id, num_files=len(generated_files))

            # ── Contract coverage check + ONE targeted retry ──────────────────
            # Run immediately after generation so missing endpoints get a second
            # chance before the test cycle starts. This avoids burning 4 debug
            # cycles on endpoints the generator simply forgot.
            api_routes = blueprint.api_routes if hasattr(blueprint, "api_routes") else []
            if api_routes:
                misses = _check_contract_coverage(
                    [r.model_dump() for r in api_routes], generated_files
                )
                if misses:
                    log.warning(
                        "generator.coverage.missing",
                        project_id=project_id,
                        count=len(misses),
                        endpoints=[f"{m['method']} {m['path']}" for m in misses],
                    )
                    # Group misses by the route file that should own them.
                    import collections as _coll
                    by_file: dict = _coll.defaultdict(list)
                    for miss in misses:
                        by_file[_route_file_for_endpoint(miss)].append(miss)

                    for fp, eps in by_file.items():
                        existing = generated_files.get(fp, "")
                        ep_list = "\n".join(
                            f"  - {ep['method']} {ep['path']}"
                            + (f"  ({ep.get('description','')})" if ep.get("description") else "")
                            for ep in eps
                        )
                        retry_prompt = (
                            f"You previously generated {fp}. The contract requires these "
                            f"additional endpoints that are MISSING from your output:\n{ep_list}\n\n"
                            f"Current file contents:\n```python\n{existing}\n```\n\n"
                            "Return the COMPLETE file with the missing endpoints added. "
                            "Real handlers, not stubs. Keep ALL existing endpoints intact."
                        )
                        try:
                            from app.models.generation_plan import FileToGenerate
                            ep_summary = ", ".join(
                                f"{e['method']} {e['path']}" for e in eps
                            )
                            stub_file = FileToGenerate(
                                path=fp,
                                description=f"Add missing endpoints: {ep_summary}",
                                depends_on=[],
                                template="fastapi_route",
                            )
                            patched = self.generator.generate_file(
                                stub_file, plan, blueprint, generated_files,
                                structured_requirements=sr,
                            )
                            if patched and patched.strip():
                                generated_files[fp] = patched
                                log.info(
                                    "generator.coverage.retry_applied",
                                    project_id=project_id, file=fp,
                                    endpoints=[f"{e['method']} {e['path']}" for e in eps],
                                )
                        except Exception as retry_exc:
                            log.warning(
                                "generator.coverage.retry_failed",
                                project_id=project_id, file=fp, error=str(retry_exc),
                            )

                    # Re-check after targeted retry.
                    final_misses = _check_contract_coverage(
                        [r.model_dump() for r in api_routes], generated_files
                    )
                    if final_misses:
                        log.error(
                            "generator.coverage.unrecoverable",
                            project_id=project_id,
                            count=len(final_misses),
                            endpoints=[f"{m['method']} {m['path']}" for m in final_misses],
                            hint=(
                                "Architect specified endpoints the generator could not produce "
                                "after one targeted retry. Likely token cap or ambiguous spec. "
                                "Manual edit or smaller blueprint needed."
                            ),
                        )
                    else:
                        log.info(
                            "generator.coverage.retry_resolved",
                            project_id=project_id,
                            resolved=len(misses),
                        )

            upload_fixes = _fix_upload_static_mount({}, generated_files)
            if upload_fixes:
                generated_files.update(upload_fixes)
                log.info("pipeline.upload_static_mount.injected", project_id=project_id)

            violations = _scan_color_violations(generated_files)
            if violations:
                log.warning(
                    "pipeline.color_violations",
                    project_id=project_id,
                    count=len(violations),
                    sample=violations[:5],
                )
                plan.notes = (plan.notes or "") + (
                    "\n\nCOLOR DISCIPLINE VIOLATIONS (fix in generated files — use semantic "
                    "classes from index.css instead of bare bg-slate / hardcoded accent colors):\n"
                    + "\n".join(f"- {v}" for v in violations[:20])
                )

            firestore_service.update_project(uid, project_id, {
                "generated_files": generated_files,
                "generation_plan": plan.model_dump(),
                "status": ProjectStatus.generated,
            })

            return {
                "status": "success",
                "generated_files": generated_files,
                "plan": plan.model_dump(),
                "errors": [],
            }

        except Exception as exc:
            log.error("generation.error", project_id=project_id, error=str(exc))
            firestore_service.set_project_status(uid, project_id, ProjectStatus.failed)
            return {
                "status": "error",
                "generated_files": {},
                "plan": None,
                "errors": [str(exc)],
            }

    # ── End-of-cycle hard dedup ───────────────────────────────────────────────

    def _hard_dedup_jsx_files(self, files: dict) -> None:
        """Remove duplicate top-level function declarations from every .jsx/.tsx/.js
        file in `files`.  Keeps the FIRST occurrence; deletes subsequent duplicates
        including their entire body.

        Runs unconditionally at the end of every debug cycle -- this is the final
        safety net that removes Layout/BareLayout duplicates regardless of which
        helper introduced them.  Operates in-place on the `files` dict.

        Handles export / export default prefixes so that both
        'function Layout(' and 'export default function Layout(' are
        recognised as the same name.
        """
        import re as _re
        _func_re = _re.compile(
            r"^(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+(\w+)\s*\(",
            _re.MULTILINE,
        )

        for path, content in list(files.items()):
            if not path.endswith((".jsx", ".tsx", ".js")):
                continue
            if not content:
                continue

            seen: set = set()
            deletions: list = []

            for m in _func_re.finditer(content):
                name = m.group(1)
                if name not in seen:
                    seen.add(name)
                    continue
                # Duplicate — find the matching closing brace
                ob = content.find("{", m.end())
                if ob == -1:
                    continue
                depth, i = 0, ob
                while i < len(content):
                    if content[i] == "{":
                        depth += 1
                    elif content[i] == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    i += 1
                if depth != 0:
                    continue
                end = i + 1
                # Consume trailing newlines / semicolons for a clean deletion
                while end < len(content) and content[end] in "\n;":
                    end += 1
                deletions.append((m.start(), end, name))

            if not deletions:
                continue

            # Apply deletions from end to start so earlier offsets stay valid
            deletions.sort(reverse=True)
            new_content = content
            removed: list = []
            for start, end, name in deletions:
                new_content = new_content[:start] + new_content[end:]
                removed.append(name)

            if new_content != content:
                files[path] = new_content
                log.info(
                    "cycle.hard_dedup.removed",
                    path=path,
                    removed=removed,
                )

    # ── Re-run tests against already-generated files ─────────────────────────

    def run_test_debug_only(
        self,
        uid: str,
        project_id: str,
        use_llm_debug: bool = True,
    ) -> None:
        """Run ONLY the test+debug cycles against already-generated files.

        Called by the /rerun-tests endpoint.  Never invokes the architect or
        generator — zero generation tokens spent.  On STRICT failure sets
        tests_failed_recoverable so the user can iterate again.
        """
        try:
            project = firestore_service.get_project(uid, project_id)
            if project is None:
                raise ValueError(f"Project {project_id} not found")

            generated_files: dict[str, str] = dict(project.generated_files or {})
            if not generated_files:
                log.error("pipeline.rerun_tests.no_files", project_id=project_id)
                firestore_service.set_project_status(uid, project_id, ProjectStatus.failed)
                return

            plan_dict = project.generation_plan
            if not plan_dict:
                raise ValueError("No generation plan stored — cannot re-run tests without a plan")
            plan = GenerationPlan(**plan_dict)
            blueprint_dict = project.blueprint or {}

            if not use_llm_debug:
                self.debugger._llm_debug_enabled = False

            log.info(
                "pipeline.rerun_tests.start",
                project_id=project_id,
                num_files=len(generated_files),
                use_llm_debug=use_llm_debug,
            )

            # ── Test + debug loop ─────────────────────────────────────────
            attempt_count: dict[str, int] = {}
            test_passed = False
            best_effort = False
            infra_warning = False
            warning_msg = ""
            test_results: dict = {}
            last_cycle_fixed_files: set[str] = set()
            last_cycle_failing_checks: set[str] = set()

            for cycle in range(_MAX_TEST_CYCLES):
                firestore_service.set_project_status(uid, project_id, ProjectStatus.testing)
                firestore_service.update_project(uid, project_id, {"current_stage": "testing"})

                test_results = self.tester.run_tests(generated_files, plan)
                log.info(
                    "pipeline.rerun.test.done",
                    project_id=project_id,
                    cycle=cycle + 1,
                    status=test_results["status"],
                )

                if test_results["status"] in ("success", "skipped"):
                    test_passed = True
                    break

                if not use_llm_debug:
                    log.info("debug.llm_disabled", cycle=cycle + 1)
                firestore_service.update_project(uid, project_id, {"current_stage": "debugging"})
                debug_result = self.debugger.debug_and_fix(
                    test_results, generated_files, plan, attempt_count
                )

                if debug_result["status"] == "fixed":
                    fixed_files = debug_result["fixed_files"]
                    current_fixed = set(fixed_files.keys())
                    current_failing = {
                        c for c, s in test_results.get("passed_checks", {}).items()
                        if s == "failed"
                    }
                    if current_fixed & last_cycle_fixed_files and current_failing == last_cycle_failing_checks:
                        log.error("pipeline.rerun.fix_loop_no_progress",
                                  project_id=project_id, cycle=cycle + 1)
                        break
                    last_cycle_fixed_files = current_fixed
                    last_cycle_failing_checks = current_failing
                    _pre_fix_snap_tdo = {p: generated_files.get(p, "") for p in fixed_files}
                    generated_files.update(fixed_files)
                    # Post-cycle duplicate sweep — mirrors run_full_pipeline guard
                    for _dup_path, _dup_content in list(generated_files.items()):
                        if not _dup_path.endswith((".jsx", ".tsx", ".js")):
                            continue
                        if _dup_path not in fixed_files:
                            continue
                        _dups = _detect_duplicate_top_level_decls(_dup_content)
                        if not _dups:
                            continue
                        log.error(
                            "cycle.duplicate_decls_detected.rolling_back",
                            project_id=project_id,
                            path=_dup_path,
                            duplicates=_dups,
                        )
                        _rollback_orig = _pre_fix_snap_tdo.get(_dup_path, "")
                        if _rollback_orig:
                            generated_files[_dup_path] = _rollback_orig
                    # Hard dedup: unconditionally scrub duplicate top-level
                    # function declarations from every modified JSX file.
                    self._hard_dedup_jsx_files(generated_files)
                    attempt_count = debug_result["attempt_counts"]
                    firestore_service.update_project(uid, project_id,
                                                     {"generated_files": generated_files})
                elif debug_result["status"] == "skipped":
                    reason = debug_result.get("reason", "")
                    if "infrastructure" in reason.lower():
                        infra_warning = True
                        warning_msg = (
                            "Dependency installation failed. Download the ZIP and "
                            "install dependencies on your own machine."
                        )
                    else:
                        best_effort = True
                        warning_msg = (
                            "Code did not fully pass automated tests. Download the ZIP "
                            "— small manual fixes may be needed to run locally."
                        )
                    test_passed = True
                    break
                else:
                    break  # no progress — fall through to STRICT gate

            # ── STRICT gate ───────────────────────────────────────────────
            final_checks = (test_results or {}).get("passed_checks", {}) or {}
            real_failures = {
                c for c, s in final_checks.items()
                if s == "failed" and c in _REAL_CHECK_NAMES
            }

            if not test_passed and real_failures:
                error_msg = (
                    f"Tests did not pass after debug attempts: "
                    f"{', '.join(sorted(real_failures))}. "
                    "Your generated files are preserved — use 'Re-run Tests' to try "
                    "again, or 'Regenerate from Scratch' to start fresh."
                )
                log.error("pipeline.rerun.strict_failure",
                          project_id=project_id, real_failures=sorted(real_failures))
                firestore_service.update_project(uid, project_id, {
                    "status": ProjectStatus.tests_failed_recoverable,
                    "current_stage": "testing",
                    "last_error": error_msg,
                    "real_failures": sorted(real_failures),
                    "last_failed_checks": sorted(real_failures),
                    "test_error_log": (
                        "\n".join(test_results.get("errors", []) or [])
                    )[:5000],
                })
                _save_snapshot(project_id, generated_files, blueprint_dict,
                               "final", plan.model_dump())
                return

            # ── Final STRICT gate (shared with run_full_pipeline) ─────────
            # Fires even when test_passed=True (e.g., infra-skip path can leave
            # real failures in passed_checks that the first gate misses).
            if _apply_strict_gate(
                test_results, generated_files, blueprint_dict,
                plan.model_dump(), uid, project_id,
            ):
                return  # Firestore already updated by _apply_strict_gate

            # ── Tests passed → verify + deploy ────────────────────────────
            _verify_strict_rerun = (
                os.environ.get("DEMAESTRO_VERIFY_STRICT", "true").lower() != "false"
            )
            if not best_effort and not infra_warning and not settings.mock_ai:
                firestore_service.set_project_status(uid, project_id, ProjectStatus.verifying)
                try:
                    blueprint_obj = BlueprintResponse(**blueprint_dict)
                    verify_result = self.verifier.verify(generated_files, plan, blueprint_obj)
                    if verify_result["status"] == "fail":
                        issues = verify_result.get("issues", [])
                        log.warning(
                            "pipeline.rerun.verify_failed",
                            project_id=project_id,
                            num_issues=len(issues),
                        )
                        if _verify_strict_rerun:
                            raise RuntimeError(f"Verification failed: {issues}")
                        else:
                            best_effort = True
                            warning_msg = "Verify found issues -- set DEMAESTRO_VERIFY_STRICT=false to bypass."
                except RuntimeError:
                    raise
                except Exception as _ve:
                    log.warning("pipeline.rerun.verify_error", project_id=project_id,
                                error=str(_ve))
                    best_effort = True
                    warning_msg = "Verification skipped -- code may need manual review."

            firestore_service.set_project_status(uid, project_id, ProjectStatus.packaging)
            sr = firestore_service.get_latest_structured_requirements(uid, project_id)
            try:
                blueprint_obj = BlueprintResponse(**blueprint_dict)
                pages_md = _build_pages_md(
                    blueprint_obj,
                    (sr.app_name if sr else None) or project_id,
                    auth_enabled=(sr.auth_required is not False) if sr else True,
                )
                generated_files["PAGES.md"] = pages_md
            except Exception:
                pass  # PAGES.md is informational; never let it block packaging

            deploy_result = self.deployer.deploy(
                uid, project_id, generated_files, plan,
                app_name=(sr.app_name if sr else None),
            )
            if deploy_result["status"] != "ready":
                raise RuntimeError(f"Packaging failed: {deploy_result.get('errors', [])}")

            final_status = (
                ProjectStatus.ready_with_warnings
                if (best_effort or infra_warning)
                else ProjectStatus.ready
            )
            firestore_service.update_project(uid, project_id, {
                "generated_files": generated_files,
                "status": final_status,
                "current_stage": "ready",
                "zip_url": deploy_result["zip_url"],
                "last_error": warning_msg or None,
            })
            _save_snapshot(project_id, generated_files, blueprint_dict,
                           "final", plan.model_dump())
            log.info("pipeline.rerun_tests.done", project_id=project_id,
                     zip_url=deploy_result["zip_url"])

        except Exception as exc:
            log.error("pipeline.rerun_tests.error",
                      project_id=project_id, error=str(exc))
            firestore_service.update_project(uid, project_id, {
                "status": ProjectStatus.tests_failed_recoverable,
                "last_error": str(exc),
            })

    # ── W5-P2 full pipeline ──────────────────────────────────────────────────

    def run_full_pipeline(self, uid: str, project_id: str) -> dict:
        """Execute the full generation pipeline.

        Returns: { status, zip_url, generated_files, errors }
        """
        from app.config import get_agent_model, estimate_cost_usd, PRICING_PER_MILLION_TOKENS

        _mark_active(project_id)
        clear_cancellation(project_id)

        log.info("pipeline.start", project_id=project_id)
        _pipeline_started_at = datetime.now(timezone.utc)
        _debug_files_touched: list[str] = []
        _cycle_count = 0

        def _agent_tokens(agent_name: str) -> dict:
            """Return token usage for a named agent, or zeros if the agent
            doesn't exist on this orchestrator instance (e.g. modifier).

            Always returns the four-bucket shape expected by estimate_cost_usd:
            input_uncached, cache_write, cache_read, output, calls.
            Agents without caching (architect, tester, verifier) will have
            cache_write=0 and cache_read=0.
            """
            _zero = {
                "input_uncached": 0, "cache_write": 0,
                "cache_read": 0, "output": 0, "calls": 0,
            }
            _agent = getattr(self, agent_name, None)
            if _agent is None:
                return _zero
            _counter = getattr(_agent, "token_counter", None)
            if _counter is None:
                return _zero
            if isinstance(_counter, dict):
                return {
                    "input_uncached": int(_counter.get("input", 0)),
                    "cache_write":    int(_counter.get("cache_write", 0)),
                    "cache_read":     int(_counter.get("cache_read", 0)),
                    "output":         int(_counter.get("output", 0)),
                    "calls":          int(_counter.get("calls", 0)),
                }
            return {
                "input_uncached": int(getattr(_counter, "input", 0)),
                "cache_write":    int(getattr(_counter, "cache_write", 0)),
                "cache_read":     int(getattr(_counter, "cache_read", 0)),
                "output":         int(getattr(_counter, "output", 0)),
                "calls":          int(getattr(_counter, "calls", 0)),
            }

        def _agent_model(agent_name: str) -> str:
            """Return the model string for a named agent, or 'n/a' if absent."""
            _agent = getattr(self, agent_name, None)
            if _agent is None:
                return "n/a"
            return getattr(_agent, "model", "n/a")

        def _write_generation_metrics(final_status: str, **extra) -> None:
            """Persist cost + quality telemetry to the project doc.

            This function MUST NEVER raise — a metrics-writer failure must not
            taint a successful pipeline result.  All exceptions are swallowed
            and logged at ERROR level for operator review.
            """
            try:
                finished = datetime.now(timezone.utc)
                _agent_names = ("architect", "generator", "debugger",
                                "verifier", "modifier", "tester")
                tokens = {name: _agent_tokens(name) for name in _agent_names}
                models = {name: _agent_model(name) for name in _agent_names}
                # Supplement with get_agent_model() for agents with token tracking
                for name in ("architect", "generator", "debugger", "modifier"):
                    if models[name] == "n/a":
                        models[name] = get_agent_model(name)
                cost = estimate_cost_usd(tokens, models)
                # Cache savings: tokens read from cache would have cost
                # input_rate / MTok at full price; they were billed at 0.1×.
                # Net savings = cache_read × 0.9 × generator_input_rate.
                _total_cache_read = sum(
                    b.get("cache_read", 0) for b in tokens.values()
                )
                _gen_model = models.get("generator", "claude-sonnet-4-6")
                _gen_input_rate = (
                    PRICING_PER_MILLION_TOKENS
                    .get(_gen_model, PRICING_PER_MILLION_TOKENS["claude-sonnet-4-6"])
                    ["input"]
                )
                cache_savings_estimate = round(
                    _total_cache_read / 1_000_000 * 0.9 * _gen_input_rate, 4
                )
                _debugger = getattr(self, "debugger", None)
                validator_rejections = int(
                    getattr(_debugger, "validator_rejections", 0)
                ) if _debugger is not None else 0
                metrics = {
                    "generation_metrics": {
                        "started_at": _pipeline_started_at.isoformat(),
                        "finished_at": finished.isoformat(),
                        "duration_sec": int(
                            (finished - _pipeline_started_at).total_seconds()
                        ),
                        "cycle_count": _cycle_count,
                        "final_status": final_status,
                        "tokens_by_agent": tokens,
                        "models_used_by_agent": models,
                        "debugger_validator_rejections": validator_rejections,
                        "debug_files_touched": _debug_files_touched,
                        **extra,
                    },
                    "estimated_cost_usd": cost,
                    "cache_savings_estimate": cache_savings_estimate,
                }
                firestore_service.update_project(uid, project_id, metrics)
            except Exception as _metrics_exc:
                log.error(
                    "generation_metrics.write_failed",
                    project_id=project_id,
                    exc_type=type(_metrics_exc).__name__,
                    exc=str(_metrics_exc),
                )
                # SWALLOW — metrics failure must not affect pipeline status.

        try:
            project = firestore_service.get_project(uid, project_id)
            if project is None:
                raise ValueError(f"Project {project_id} not found")

            sr = firestore_service.get_latest_structured_requirements(uid, project_id)
            if sr is None:
                raise ValueError(f"No structured requirements for project {project_id}")

            blueprint_dict = project.blueprint
            if blueprint_dict is None:
                raise ValueError(f"No blueprint for project {project_id}")
            blueprint = BlueprintResponse(**blueprint_dict)

            existing_plan = project.generation_plan or None
            existing_files = project.generated_files or {}
            resuming = bool(existing_plan and existing_files)
            if resuming:
                log.info(
                    "pipeline.resuming",
                    project_id=project_id,
                    files_already_generated=len(existing_files),
                )

            # ── STEP 1: Architect + Generate ─────────────────────────────────
            if resuming:
                plan = GenerationPlan(**existing_plan)
                generated_files: dict[str, str] = dict(existing_files)
                _checklist_full: list = []
            else:
                firestore_service.set_project_status(uid, project_id, ProjectStatus.generating)
                plan, _checklist_full = self.architect.architect(sr, blueprint)
                log.info("pipeline.architect.done", project_id=project_id, num_files=len(plan.files))

                firestore_service.update_project(uid, project_id, {
                    "generation_plan": plan.model_dump(),
                    "checklist": [_item_to_dict(i) for i in _checklist_full],
                    "checklist_results": [
                        {"item_id": getattr(i, "id", "?"), "status": "pending", "reason": ""}
                        for i in _checklist_full
                    ],
                })

                # Cancellation checkpoint: after architect
                if is_cancelled(project_id):
                    log.info("pipeline.cancelled_by_user", project_id=project_id, at_phase="post_architect")
                    firestore_service.update_project(uid, project_id, {
                        "status": ProjectStatus.stopped,
                        "current_stage": "stopped",
                        "last_error": "Generation stopped by user.",
                    })
                    return {"status": "cancelled", "zip_url": None, "generated_files": {}, "errors": []}

                app_slug = template_service.slugify(sr.app_name)
                scaffolding = template_service.load_stack_templates(plan.technology_stack, sr.app_name, app_slug)
                generated_files = dict(scaffolding)

                # Auth mode: False = explicitly disabled, True/None = enabled.
                _auth_on = sr.auth_required is not False

                # Remove variant-template files — reference patterns, not output.
                for _vf in _NO_AUTH_VARIANT_FILES:
                    generated_files.pop(_vf, None)

                if _auth_on:
                    auth_seeded = sorted(_AUTH_SCAFFOLD_FILES & generated_files.keys())
                    log.info(
                        "pipeline.scaffolding.seeded",
                        project_id=project_id,
                        num_scaffold=len(scaffolding),
                        auth_scaffold_files=auth_seeded,
                    )
                else:
                    for _af in _AUTH_SCAFFOLD_FILES:
                        generated_files.pop(_af, None)
                    _auth_disabled_note = (
                        "AUTH_DISABLED: this app is fully public — no auth scaffold. "
                        "Do NOT generate LoginPage, RegisterPage, AuthContext, auth "
                        "routes, JWT code, or any auth-related UI. Navbar has no "
                        "Sign In / Sign Up / Log out links."
                    )
                    plan.notes = (
                        (plan.notes + "\n\n" + _auth_disabled_note)
                        if plan.notes else _auth_disabled_note
                    )
                    log.info(
                        "pipeline.scaffolding.auth_skipped",
                        project_id=project_id,
                        reason="auth_disabled_in_requirements",
                    )

                extras = [d.strip() for d in (plan.extra_dependencies or []) if d and d.strip()]
                if extras:
                    req_path = "backend/requirements.txt"
                    base_req = generated_files.get(req_path, "")
                    present = base_req.lower()
                    to_add = [
                        d for d in extras
                        if d.split("==")[0].split(">")[0].split("<")[0].strip().lower() not in present
                    ]
                    if to_add:
                        generated_files[req_path] = (
                            base_req.rstrip()
                            + "\n# --- app-specific dependencies (declared by the architect) ---\n"
                            + "\n".join(to_add) + "\n"
                        )
                        log.info("pipeline.extra_deps_added", project_id=project_id, deps=to_add)

                fe_extras = [d.strip() for d in (plan.extra_frontend_dependencies or []) if d and d.strip()]
                if fe_extras:
                    pkg_path = "frontend/package.json"
                    pkg_raw = generated_files.get(pkg_path, "")
                    try:
                        pkg = json.loads(pkg_raw)
                        deps = pkg.setdefault("dependencies", {})
                        added = [name for name in fe_extras if name not in deps]
                        for name in added:
                            deps[name] = "latest"
                        if added:
                            generated_files[pkg_path] = json.dumps(pkg, indent=2) + "\n"
                            log.info("pipeline.extra_frontend_deps_added", project_id=project_id, deps=added)
                    except Exception as exc:
                        log.warning("pipeline.frontend_deps_merge_failed", project_id=project_id, error=str(exc))

                design_brief = _detect_design_brief(sr)
                if design_brief is not None:
                    _apply_design_brief_to_scaffolding(generated_files, design_brief)
                    brief_note = (
                        "DESIGN BRIEF (apply consistently across every page): "
                        f"accent color = {design_brief.get('primary_color') or 'default'}; "
                        f"vibe = {design_brief.get('vibe') or 'clean and modern'}. "
                        "Use `bg-accent text-accent-fg hover:bg-accent/90` for primary buttons "
                        "and `text-accent` for links and icon accents — the --accent CSS variable "
                        "has been set to the requested color. Surface and text variables remain "
                        "neutral readable defaults."
                    )
                    try:
                        plan.notes = (plan.notes + "\n\n" + brief_note) if plan.notes else brief_note
                    except Exception:
                        pass

                # routes.js must be project-specific — remove scaffold default so
                # the LLM generates the real, blueprint-derived version.
                if "frontend/src/lib/routes.js" in plan.generation_order:
                    generated_files.pop("frontend/src/lib/routes.js", None)

                firestore_service.update_project(uid, project_id, {
                    "generated_files": generated_files,
                })

            app_files = [f for f in plan.files if f.path not in generated_files]
            already_done = len(generated_files)
            total = already_done + len(app_files)

            firestore_service.update_project(uid, project_id, {
                "total_files": total,
                "generated_count": already_done,
                "current_stage": "generating",
            })

            for idx, file_path in enumerate(plan.generation_order):
                if file_path in generated_files:
                    continue
                file_to_gen = next((f for f in plan.files if f.path == file_path), None)
                if file_to_gen is None:
                    continue
                firestore_service.update_project(uid, project_id, {
                    "current_file": file_path,
                    "generated_count": len(generated_files),
                })
                generated_files[file_path] = self.generator.generate_file(
                    file_to_gen, plan, blueprint, generated_files,
                    structured_requirements=sr,
                )
                firestore_service.update_project(uid, project_id, {
                    "generated_files": generated_files,
                })
                # Cancellation checkpoint: every 5 files during generation
                if idx % 5 == 4 and is_cancelled(project_id):
                    log.info("pipeline.cancelled_by_user", project_id=project_id, at_phase="generating")
                    firestore_service.update_project(uid, project_id, {
                        "status": ProjectStatus.stopped,
                        "current_stage": "stopped",
                        "last_error": "Generation stopped by user.",
                    })
                    return {
                        "status": "cancelled",
                        "zip_url": None,
                        "generated_files": generated_files,
                        "errors": [],
                    }

            firestore_service.update_project(uid, project_id, {
                "generation_plan": plan.model_dump(),
                "status": ProjectStatus.generated,
                "generated_count": len(generated_files),
                "current_file": None,
            })
            log.info("pipeline.generate.done", project_id=project_id, num_files=len(generated_files))

            upload_fixes = _fix_upload_static_mount({}, generated_files)
            if upload_fixes:
                generated_files.update(upload_fixes)
                firestore_service.update_project(uid, project_id, {"generated_files": generated_files})
                log.info("pipeline.upload_static_mount.injected", project_id=project_id)

            violations = _scan_color_violations(generated_files)
            if violations:
                log.warning(
                    "pipeline.color_violations",
                    project_id=project_id,
                    count=len(violations),
                    sample=violations[:5],
                )
                plan.notes = (plan.notes or "") + (
                    "\n\nCOLOR DISCIPLINE VIOLATIONS (fix in generated files — use semantic "
                    "classes from index.css instead of bare bg-slate / hardcoded accent colors):\n"
                    + "\n".join(f"- {v}" for v in violations[:20])
                )

            _save_snapshot(project_id, generated_files, blueprint_dict,
                           "post_generate", plan.model_dump())

            # ── Checklist coverage pass + targeted regen ─────────────────────
            # Runs once immediately after generation before the test loop.
            # This fills gaps before the test harness fires, saving debug cycles.
            if _checklist_full:
                try:
                    _cl_results = run_checklist(
                        _checklist_full, generated_files, {}, ""
                    )
                    firestore_service.update_project(uid, project_id, {
                        "checklist_results": _checklist_results_to_payload(_cl_results),
                    })

                    # Import-resolution gate: catch bad app.X imports before
                    # the test loop fires, saving a full test+debug cycle.
                    _import_failures = check_import_resolution(generated_files)
                    if _import_failures:
                        log.warning(
                            "checklist.import_failures",
                            project_id=project_id,
                            count=len(_import_failures),
                            failures=[r.reason for r in _import_failures[:5]],
                        )
                        _import_patches = self.debugger.run_algorithmic_fixes(
                            generated_files, test_results=None,
                        )
                        if _import_patches:
                            log.info(
                                "checklist.import_autofix_applied",
                                project_id=project_id,
                                files=_import_patches,
                            )

                    _cl_failed = [r for r in _cl_results if r.status == "fail"]
                    if _cl_failed:
                        log.info(
                            "checklist.targeted_regen.start",
                            project_id=project_id,
                            failed_count=len(_cl_failed),
                        )
                        _cl_by_id = {getattr(i, "id"): i for i in _checklist_full}
                        for _cr in _cl_failed:
                            _item = _cl_by_id.get(_cr.item_id)
                            if _item is None:
                                continue
                            try:
                                _patches = self.generator.generate_single_item(
                                    _item, generated_files
                                )
                                if _patches:
                                    generated_files.update(_patches)
                                    log.info(
                                        "checklist.targeted_regen.applied",
                                        project_id=project_id,
                                        item_id=_cr.item_id,
                                        patched=list(_patches.keys()),
                                    )
                            except Exception as _regen_exc:
                                log.warning(
                                    "checklist.targeted_regen_failed",
                                    project_id=project_id,
                                    item_id=_cr.item_id,
                                    error=str(_regen_exc),
                                )
                        # Re-run the checklist after targeted regen.
                        _cl_final = run_checklist(
                            _checklist_full, generated_files, {}, ""
                        )
                        firestore_service.update_project(uid, project_id, {
                            "checklist_results": _checklist_results_to_payload(_cl_final),
                        })
                        _still_failed = [r for r in _cl_final if r.status == "fail"]
                        if _still_failed:
                            log.info(
                                "checklist.after_regen",
                                project_id=project_id,
                                still_failing=[r.item_id for r in _still_failed],
                            )
                        else:
                            log.info(
                                "checklist.all_resolved",
                                project_id=project_id,
                            )
                        firestore_service.update_project(uid, project_id, {
                            "generated_files": generated_files,
                        })
                except Exception as _cl_exc:
                    log.warning(
                        "checklist.pass_failed",
                        project_id=project_id,
                        error=str(_cl_exc),
                    )

            # ── STEP 2: Test + Debug loop ────────────────────────────────────
            attempt_count: dict[str, int] = {}
            test_passed = False
            best_effort = False
            infra_warning = False
            warning_msg = ""
            test_results: dict = {}
            last_cycle_fixed_files: set[str] = set()
            last_cycle_failing_checks: set[str] = set()
            no_progress_cycles: int = 0
            _prev_cycle_import_diffs: dict[str, dict] = {}
            _last_rollback_signature: tuple | None = None
            _agentic_invocations: int = 0
            _MAX_AGENTIC_INVOCATIONS = 5
            _cycle_file_history: list[set[str]] = []  # per-cycle fixed-file sets

            for cycle in range(_MAX_TEST_CYCLES):
                firestore_service.set_project_status(uid, project_id, ProjectStatus.testing)
                firestore_service.update_project(uid, project_id, {"current_stage": "testing"})
                _cycle_count = cycle + 1

                test_results = self.tester.run_tests(generated_files, plan)
                log.info(
                    "pipeline.test.done",
                    project_id=project_id,
                    cycle=cycle + 1,
                    status=test_results["status"],
                )

                # Surface install failures to Firestore so the frontend can show useful info.
                if test_results["passed_checks"].get("install") == "failed":
                    install_log = test_results["logs"].get("install", "")
                    install_log_tail = install_log[-2000:]
                    log.warning(
                        "pipeline.install_failed",
                        project_id=project_id,
                        cycle=cycle + 1,
                        install_log=install_log_tail,
                    )
                    if _is_infrastructure_error(install_log):
                        friendly_msg = (
                            "Dependency installation failed in the test environment. "
                            "This is usually a Python/Node version incompatibility with a "
                            "generated dependency, not a code problem. Most generated files "
                            "are still useful — download the ZIP and try installing "
                            "dependencies on your own machine."
                        )
                        firestore_service.update_project(uid, project_id, {
                            "last_error": friendly_msg,
                            "install_error_log": install_log_tail,
                        })
                    else:
                        firestore_service.update_project(uid, project_id, {
                            "last_error": f"Install failed (cycle {cycle + 1}): {install_log[:500]}"
                        })

                # "success" = all checks passed; "skipped" = all checks skipped (missing tools)
                if test_results["status"] in ("success", "skipped"):
                    test_passed = True
                    break

                # Cancellation checkpoint: after test cycle
                if is_cancelled(project_id):
                    log.info("pipeline.cancelled_by_user", project_id=project_id, at_phase="post_test")
                    firestore_service.update_project(uid, project_id, {
                        "status": ProjectStatus.stopped,
                        "current_stage": "stopped",
                        "last_error": "Generation stopped by user.",
                    })
                    return {
                        "status": "cancelled",
                        "zip_url": None,
                        "generated_files": generated_files,
                        "errors": [],
                    }

                # ── Aggressive agentic trigger ────────────────────────────────
                # If a REAL check still fails after the first cycle, invoke the
                # agentic fallback proactively before the next algorithmic pass.
                _REAL_CHECK_NAMES_PROACTIVE = {
                    "boot", "lint", "frontend_build", "smoke",
                    "reachability", "contract",
                }
                _current_passed = test_results.get("passed_checks", {})
                _real_failing_now = {
                    c for c in _REAL_CHECK_NAMES_PROACTIVE
                    if _current_passed.get(c) == "failed"
                }
                if cycle >= 1 and _real_failing_now and _agentic_invocations < _MAX_AGENTIC_INVOCATIONS:
                    _agentic_invocations += 1
                    log.info(
                        "pipeline.agentic_fallback.proactive_trigger",
                        project_id=project_id,
                        cycle=cycle + 1,
                        invocation=_agentic_invocations,
                        real_failing=sorted(_real_failing_now),
                    )
                    agentic_fixes = self.debugger._agentic_holistic_fix(
                        test_results, generated_files,
                    )
                    if agentic_fixes:
                        generated_files.update(agentic_fixes)
                        firestore_service.update_project(uid, project_id, {
                            "generated_files": generated_files,
                        })
                        log.info(
                            "pipeline.agentic_fallback.applied_proactive",
                            project_id=project_id,
                            patched=list(agentic_fixes.keys()),
                        )
                        no_progress_cycles = 0
                        last_cycle_fixed_files = set()
                        last_cycle_failing_checks = set()
                        continue  # re-test immediately with agentic patches

                firestore_service.update_project(uid, project_id, {"current_stage": "debugging"})
                if not USE_LLM_DEBUG:
                    log.info("debug.llm_disabled", cycle=cycle + 1)
                debug_result = self.debugger.debug_and_fix(
                    test_results, generated_files, plan, attempt_count
                )
                log.info(
                    "pipeline.debug.done",
                    project_id=project_id,
                    cycle=cycle + 1,
                    status=debug_result["status"],
                )

                if debug_result["status"] == "fixed":
                    fixed_files = debug_result["fixed_files"]
                    current_fixed_files = set(fixed_files.keys())
                    current_failing_checks = {
                        c for c, s in test_results.get("passed_checks", {}).items()
                        if s == "failed"
                    }

                    # ── Ping-pong detection ───────────────────────────────────
                    # If the EXACT same set of files was "fixed" in 2+ consecutive
                    # cycles and the same checks are still failing, algorithmic
                    # fixers are undoing each other's work.  Escalate directly to
                    # the focused agentic fix that steers the LLM away from the
                    # oscillating files.
                    _cycle_file_history.append(current_fixed_files)
                    if len(_cycle_file_history) > 3:
                        _cycle_file_history.pop(0)
                    _PING_PONG_WINDOW = 2
                    _ping_pong = (
                        len(_cycle_file_history) >= _PING_PONG_WINDOW
                        and bool(current_fixed_files)
                        and all(
                            fs == current_fixed_files
                            for fs in _cycle_file_history[-_PING_PONG_WINDOW:]
                        )
                        and bool(current_failing_checks)
                        and current_failing_checks == last_cycle_failing_checks
                    )
                    if _ping_pong and _agentic_invocations < _MAX_AGENTIC_INVOCATIONS:
                        _agentic_invocations += 1
                        log.warning(
                            "pipeline.ping_pong_detected",
                            project_id=project_id,
                            cycle=cycle + 1,
                            files=sorted(current_fixed_files),
                            failing=sorted(current_failing_checks),
                            invocation=_agentic_invocations,
                        )
                        _pp_fixes = self.debugger.force_agentic_holistic_fix(
                            test_results=test_results,
                            generated_files=generated_files,
                            ping_pong_files=current_fixed_files,
                            failing_checks=current_failing_checks,
                        )
                        if _pp_fixes:
                            generated_files.update(_pp_fixes)
                            firestore_service.update_project(uid, project_id, {
                                "generated_files": generated_files,
                            })
                            log.info(
                                "pipeline.ping_pong.agentic_applied",
                                project_id=project_id,
                                patched=list(_pp_fixes.keys()),
                            )
                            _cycle_file_history.clear()
                            no_progress_cycles = 0
                            last_cycle_fixed_files = set()
                            last_cycle_failing_checks = set()
                            continue

                    # ── No-progress guard ────────────────────────────────────
                    # Two conditions that both indicate the debug loop is stuck:
                    #   A) Same checks still failing AND same files were "fixed"
                    #      (classic: debugger re-applies the same non-working patch)
                    #   B) Same checks still failing for 2 consecutive cycles
                    #      (even with different files: the LLM keeps trying new
                    #      approaches but nothing lands)
                    overlap = current_fixed_files & last_cycle_fixed_files
                    checks_stalled = (
                        current_failing_checks
                        and current_failing_checks == last_cycle_failing_checks
                    )

                    if checks_stalled:
                        if current_failing_checks == {"typecheck"} and no_progress_cycles < 1:
                            # Typecheck-only failures often need a second pass to settle
                            # (mypy fixes are subtle; give 2 cycles max before declaring no-progress).
                            log.info(
                                "fix_loop.typecheck_only_extra_cycle",
                                project_id=project_id,
                                cycle=cycle + 1,
                                no_progress_cycles=no_progress_cycles,
                            )
                            no_progress_cycles += 1
                        elif overlap or no_progress_cycles >= 0 or current_failing_checks:
                            # Either the same files keep being touched (immediate signal),
                            # OR checks stalled for a second consecutive cycle (2-cycle limit),
                            # OR there are still failing checks after a "fixed" result.
                            # Try the agentic holistic fallback before giving up.
                            if _agentic_invocations < _MAX_AGENTIC_INVOCATIONS:
                                _agentic_invocations += 1
                                log.info(
                                    "pipeline.agentic_fallback.triggering",
                                    project_id=project_id,
                                    cycle=cycle + 1,
                                    invocation=_agentic_invocations,
                                )
                                agentic_fixes = self.debugger._agentic_holistic_fix(
                                    test_results, generated_files,
                                )
                                if agentic_fixes:
                                    generated_files.update(agentic_fixes)
                                    firestore_service.update_project(uid, project_id, {
                                        "generated_files": generated_files,
                                    })
                                    log.info(
                                        "pipeline.agentic_fallback.applied",
                                        project_id=project_id,
                                        patched=list(agentic_fixes.keys()),
                                    )
                                    no_progress_cycles = 0
                                    last_cycle_fixed_files = set()
                                    last_cycle_failing_checks = set()
                                    continue  # re-test with patches applied
                                else:
                                    log.warning(
                                        "pipeline.agentic_fallback.no_patches",
                                        project_id=project_id,
                                        cycle=cycle + 1,
                                    )

                            human_msg = (
                                f"Fix loop made no progress after {cycle + 1} cycle(s). "
                                f"Failing checks: {sorted(current_failing_checks)}. "
                            )
                            if overlap:
                                human_msg += (
                                    f"The same files were repeatedly patched without effect: "
                                    f"{sorted(overlap)}. "
                                )
                            human_msg += (
                                "Use 'Regenerate from Scratch' or edit the blueprint to "
                                "simplify the endpoints that could not be synthesized."
                            )
                            log.error(
                                "pipeline.fix_loop_no_progress",
                                project_id=project_id,
                                cycle=cycle + 1,
                                repeated_fixes=sorted(overlap),
                                failing_checks=sorted(current_failing_checks),
                                no_progress_cycles=no_progress_cycles,
                                human_msg=human_msg,
                            )
                            break  # short-circuit: test_passed stays False → STRICT failure
                        else:
                            # First cycle with stalled checks — allow one more before aborting.
                            no_progress_cycles += 1
                    else:
                        no_progress_cycles = 0  # reset when checks actually change

                    last_cycle_fixed_files = current_fixed_files
                    last_cycle_failing_checks = current_failing_checks
                    # Snapshot originals before applying fixes for corruption rollback
                    _pre_fix_snapshot = {p: generated_files.get(p, "") for p in fixed_files}

                    # ── Import-oscillation detection ──────────────────────
                    # If cycle N added import X and cycle N+1 removes it (or vice
                    # versa), the fixers are undoing each other — abort rather than
                    # burning more cycles.
                    _cur_import_diffs: dict[str, dict] = {
                        p: _diff_imports(_pre_fix_snapshot.get(p, ""), new_fc)
                        for p, new_fc in fixed_files.items()
                    }
                    for _osc_path, _cur_diff in _cur_import_diffs.items():
                        _prev_diff = _prev_cycle_import_diffs.get(_osc_path, {})
                        _oscillating = [
                            imp for imp in _cur_diff.get("added", [])
                            if imp in _prev_diff.get("removed", [])
                        ] + [
                            imp for imp in _cur_diff.get("removed", [])
                            if imp in _prev_diff.get("added", [])
                        ]
                        if _oscillating:
                            _osc_imp = _oscillating[0]
                            _mh = _re.search(r"import\s+(\w+)", _osc_imp)
                            _mname = _mh.group(1) if _mh else "a referenced model"
                            _osc_msg = (
                                f"Generation failed: a missing dependency in "
                                f"{_osc_path} cannot be auto-fixed safely "
                                f"({_osc_imp!r} oscillation). The blueprint may "
                                f"reference {_mname} which doesn't exist in the "
                                "data model. Edit requirements to remove the "
                                "unsupported feature or click Retry."
                            )
                            log.error(
                                "fix_loop.import_oscillation",
                                project_id=project_id,
                                file=_osc_path,
                                oscillating_import=_osc_imp,
                                cycles=[cycle, cycle + 1],
                            )
                            firestore_service.update_project(uid, project_id, {
                                "status": ProjectStatus.failed,
                                "current_stage": "generating",
                                "error_message": _osc_msg,
                                "last_error": _osc_msg,
                            })
                            _write_generation_metrics(
                                "import_oscillation",
                                oscillating_import=_osc_imp,
                                oscillating_file=_osc_path,
                            )
                            return {
                                "status": "error",
                                "zip_url": None,
                                "generated_files": generated_files,
                                "errors": [_osc_msg],
                            }
                    _prev_cycle_import_diffs = _cur_import_diffs

                    generated_files.update(fixed_files)

                    # ── Post-cycle duplicate-declaration sweep ────────────────
                    # Unconditional safety net: rolls back any JSX file where
                    # this cycle introduced duplicate top-level declarations
                    # (e.g. two helpers both injecting function Layout()).
                    # Uses _pre_fix_snapshot so only this cycle's damage reverts.
                    for _dup_path, _dup_content in list(generated_files.items()):
                        if not _dup_path.endswith((".jsx", ".tsx", ".js")):
                            continue
                        if _dup_path not in fixed_files:
                            continue  # not touched this cycle
                        _dups = _detect_duplicate_top_level_decls(_dup_content)
                        if not _dups:
                            continue
                        log.error(
                            "cycle.duplicate_decls_detected.rolling_back",
                            project_id=project_id,
                            path=_dup_path,
                            duplicates=_dups,
                        )
                        _rollback_orig = _pre_fix_snapshot.get(_dup_path, "")
                        if _rollback_orig:
                            generated_files[_dup_path] = _rollback_orig

                    # Hard dedup: unconditionally scrub duplicate top-level
                    # function declarations from every modified JSX file.
                    self._hard_dedup_jsx_files(generated_files)

                    attempt_count = debug_result["attempt_counts"]
                    # Track which files the debugger touched for telemetry.
                    for _fpath in fixed_files:
                        if _fpath not in _debug_files_touched:
                            _debug_files_touched.append(_fpath)
                    # Corruption guard: if any LLM-modified Python file no longer
                    # parses, roll it back and fast-track to no-progress failure.
                    import ast as _ast
                    _corruption_detected = False
                    for _fpath, _new_content in fixed_files.items():
                        if not _fpath.endswith(".py") or not _new_content:
                            continue
                        try:
                            _ast.parse(_new_content)
                        except SyntaxError:
                            _orig = _pre_fix_snapshot.get(_fpath, "")
                            log.error(
                                "fix_loop.llm_corrupted_file",
                                project_id=project_id,
                                file=_fpath,
                                preview=_new_content[:200],
                            )
                            if _orig:
                                generated_files[_fpath] = _orig
                            _corruption_detected = True
                    if _corruption_detected:
                        no_progress_cycles = 2
                        log.error(
                            "fix_loop.skipped_due_to_corruption",
                            project_id=project_id,
                        )
                    # Post-fix static validation: catch "used but not imported" bugs
                    # and roll back any file whose content is no longer valid Python.
                    _post_fix_validate(
                        generated_files,
                        list(fixed_files.keys()),
                        _pre_fix_snapshot,
                    )

                    # ── Rollback loop detection ────────────────────────────────
                    # If _validate_or_rollback fires for the same file with the
                    # same reason on two consecutive cycles, the helper is stuck
                    # in a broken-inject→rollback loop. Abort immediately rather
                    # than burning more LLM cycles.
                    _rollbacks_this_cycle: list[tuple] = []
                    for _rp, _rc in generated_files.items():
                        if _rp in _pre_fix_snapshot and _rc == _pre_fix_snapshot[_rp]:
                            if _rp in fixed_files and fixed_files[_rp] != _rc:
                                # File was "fixed" but content matches pre-cycle — rolled back.
                                _rollbacks_this_cycle.append((_rp, "rolled_back"))
                    _rollback_sig = tuple(sorted(_rollbacks_this_cycle))
                    if _rollback_sig and _rollback_sig == _last_rollback_signature:
                        _rb_msg = (
                            f"Rollback loop detected after cycle {cycle + 1}: "
                            f"the same file(s) are being broken and rolled back every cycle: "
                            f"{[r[0] for r in _rollback_sig]}. "
                            "A helper is producing invalid output for these files. "
                            "Use 'Regenerate from Scratch' or edit the blueprint."
                        )
                        log.error(
                            "pipeline.rollback_loop_detected",
                            project_id=project_id,
                            signature=[r[0] for r in _rollback_sig],
                            cycle=cycle + 1,
                            hint=_rb_msg,
                        )
                        no_progress_cycles = 2  # force the no-progress exit on next check
                    _last_rollback_signature = _rollback_sig
                    # Persist fixed files to Firestore so resumed pipelines see the latest state
                    changed_files = list(fixed_files.keys())
                    try:
                        firestore_service.update_project(uid, project_id, {"generated_files": generated_files})
                        persisted_files = changed_files
                    except Exception as persist_exc:
                        persisted_files = []
                        log.error("pipeline.debug.persist_failed", project_id=project_id, error=str(persist_exc))
                    log.info(
                        "debugger.persistence.verify",
                        files_mutated_in_memory=changed_files,
                        files_persisted_to_store=persisted_files,
                    )
                    if set(changed_files) != set(persisted_files):
                        log.error(
                            "debugger.persistence.mismatch",
                            mutated_not_persisted=sorted(set(changed_files) - set(persisted_files)),
                        )
                    _save_snapshot(project_id, generated_files, blueprint_dict,
                                   f"post_cycle_{_cycle_count}", plan.model_dump())

                    # Live checklist refresh — push updated pass/fail counts to
                    # Firestore so the frontend counter ticks up in real time.
                    if _checklist_full:
                        try:
                            _cl_results = run_checklist(
                                _checklist_full, generated_files, {}, ""
                            )
                            firestore_service.update_project(uid, project_id, {
                                "checklist_results": _checklist_results_to_payload(_cl_results),
                            })
                            log.info(
                                "pipeline.checklist.live_refresh",
                                project_id=project_id,
                                cycle=cycle,
                                num_pass=sum(1 for r in _cl_results if r.status == "pass"),
                                num_fail=sum(1 for r in _cl_results if r.status != "pass"),
                            )
                        except Exception as _exc:
                            log.warning("pipeline.checklist.live_refresh_failed",
                                        project_id=project_id, error=str(_exc)[:200])

                    # Cancellation checkpoint: after debug cycle
                    if is_cancelled(project_id):
                        log.info("pipeline.cancelled_by_user", project_id=project_id, at_phase="post_debug")
                        firestore_service.update_project(uid, project_id, {
                            "status": ProjectStatus.stopped,
                            "current_stage": "stopped",
                            "last_error": "Generation stopped by user.",
                        })
                        return {
                            "status": "cancelled",
                            "zip_url": None,
                            "generated_files": generated_files,
                            "errors": [],
                        }
                elif debug_result["status"] == "skipped":
                    reason = debug_result.get("reason", "")
                    if "infrastructure" in reason.lower():
                        log.info("pipeline.debug.skipped_infra_errors", project_id=project_id, cycle=cycle + 1)
                        infra_warning = True
                        warning_msg = (
                            "Dependency installation failed in the test environment. "
                            "Download the ZIP and try installing dependencies on your own machine."
                        )
                    else:
                        # ── Agentic fallback: algorithmic debugger gave up; try the LLM
                        # agent before bailing to best-effort. ──────────────────────────
                        if _agentic_invocations < _MAX_AGENTIC_INVOCATIONS:
                            _agentic_invocations += 1
                            log.info(
                                "pipeline.agentic_fallback.triggering_from_skipped",
                                project_id=project_id,
                                cycle=cycle + 1,
                                invocation=_agentic_invocations,
                                skipped_reason=reason,
                            )
                            agentic_fixes = self.debugger._agentic_holistic_fix(
                                test_results, generated_files,
                            )
                            if agentic_fixes:
                                generated_files.update(agentic_fixes)
                                firestore_service.update_project(uid, project_id, {
                                    "generated_files": generated_files,
                                })
                                log.info(
                                    "pipeline.agentic_fallback.applied_from_skipped",
                                    project_id=project_id,
                                    patched=list(agentic_fixes.keys()),
                                )
                                no_progress_cycles = 0
                                last_cycle_fixed_files = set()
                                last_cycle_failing_checks = set()
                                continue  # back to test cycle with patches applied
                            else:
                                log.warning(
                                    "pipeline.agentic_fallback.no_patches_from_skipped",
                                    project_id=project_id,
                                    cycle=cycle + 1,
                                )

                        # Only if agentic also couldn't help: bail to best_effort.
                        log.warning("pipeline.debug.cannot_auto_fix", project_id=project_id, cycle=cycle + 1, reason=reason)
                        best_effort = True
                        _test_summary = ", ".join(
                            f"{c}: {s}" for c, s in test_results.get("passed_checks", {}).items()
                        )
                        warning_msg = (
                            "The generated code did not pass automated tests in our environment"
                            + (f" ({_test_summary})" if _test_summary else "")
                            + ". You can still download the ZIP — the code may "
                            "need small manual fixes to run locally. Check the README for setup."
                        )
                    test_passed = True
                    break
                else:
                    # Debug could not identify or fix the file — try agentic before bailing.
                    if _agentic_invocations < _MAX_AGENTIC_INVOCATIONS:
                        _agentic_invocations += 1
                        log.info(
                            "pipeline.agentic_fallback.triggering_from_cannot_fix",
                            project_id=project_id,
                            cycle=cycle + 1,
                            invocation=_agentic_invocations,
                        )
                        agentic_fixes = self.debugger._agentic_holistic_fix(
                            test_results, generated_files,
                        )
                        if agentic_fixes:
                            generated_files.update(agentic_fixes)
                            firestore_service.update_project(uid, project_id, {
                                "generated_files": generated_files,
                            })
                            log.info(
                                "pipeline.agentic_fallback.applied_from_cannot_fix",
                                project_id=project_id,
                                patched=list(agentic_fixes.keys()),
                            )
                            no_progress_cycles = 0
                            last_cycle_fixed_files = set()
                            last_cycle_failing_checks = set()
                            continue  # re-test with patches applied
                        else:
                            log.warning(
                                "pipeline.agentic_fallback.no_patches_from_cannot_fix",
                                project_id=project_id,
                                cycle=cycle + 1,
                            )

                    log.warning(
                        "pipeline.debug.cannot_fix",
                        project_id=project_id,
                        cycle=cycle + 1,
                        errors=debug_result.get("errors"),
                    )
                    break  # test_passed stays False → best_effort block below

            # STRICT vs advisory classification (uses module-level _REAL_CHECK_NAMES).
            _ADVISORY_CHECK_NAMES = {"typecheck", "smoke"}
            final_checks = (test_results or {}).get("passed_checks", {}) or {}
            real_failures = {
                check for check, st in final_checks.items()
                if st == "failed" and check in _REAL_CHECK_NAMES
            }
            advisory_failures = {
                check for check, st in final_checks.items()
                if st in ("failed", "advisory_failed") and check in _ADVISORY_CHECK_NAMES
            }
            # Also capture advisory_failed contract (non-critical misses)
            if final_checks.get("contract") == "advisory_failed":
                advisory_failures.add("contract")
            environmental_skips = {
                check for check, st in final_checks.items() if st == "skipped"
            }

            if not test_passed and real_failures:
                # STRICT: real failures that couldn't be auto-fixed.
                # Mark tests_failed_recoverable (not failed) — the generated files are
                # intact and the user can iterate via Re-run Tests without regenerating.
                error_msg = (
                    f"Tests did not pass after debug attempts: "
                    f"{', '.join(sorted(real_failures))}. "
                    "Your generated files are preserved — use 'Re-run Tests' to retry "
                    "after adjusting the scaffold or debugger, or 'Regenerate from Scratch' "
                    "to start fresh."
                )
                log.error(
                    "pipeline.strict_failure",
                    project_id=project_id,
                    real_failures=sorted(real_failures),
                    environmental_skips=sorted(environmental_skips),
                )
                firestore_service.update_project(uid, project_id, {
                    "status": ProjectStatus.tests_failed_recoverable,
                    "current_stage": "testing",
                    "last_error": error_msg,
                    "real_failures": sorted(real_failures),
                    "last_failed_checks": sorted(real_failures),
                    "test_error_log": ("\n".join(test_results.get("errors", []) or []))[:5000],
                })
                _write_generation_metrics("strict_failure", real_failures=sorted(real_failures))
                _save_snapshot(project_id, generated_files, blueprint_dict,
                               "final", plan.model_dump())
                return {
                    "status": "error",
                    "zip_url": None,
                    "generated_files": generated_files,
                    "errors": [error_msg],
                }

            # OK to package: tests passed, or only environmental skips/warnings remain.
            if not test_passed:
                best_effort = True
                _test_summary = ", ".join(
                    f"{c}: {s}" for c, s in final_checks.items()
                )
                warning_msg = (
                    "The generated code did not pass automated tests in our environment"
                    + (f" ({_test_summary})" if _test_summary else "")
                    + ". You can still download the ZIP — the code may "
                    "need small manual fixes to run locally. Check the README for setup."
                )
                firestore_service.update_project(uid, project_id, {
                    "generated_files": generated_files,
                    "last_error": warning_msg,
                    "test_error_log": ("\n".join(test_results.get("errors", [])))[:5000] or None,
                })
            else:
                # Persist advisory warning metadata so the UI can surface it.
                _advisory_logs = test_results.get("logs", {}) or {}
                _tc_output = _advisory_logs.get("typecheck", "")
                _tc_errors = _tc_output.count("\n") if _tc_output and _tc_output != "no venv available for mypy" else 0
                _contract_misses = (test_results.get("logs", {}) or {}).get("contract_advisory", "")
                _advisory_meta: dict = {}
                if "typecheck" in advisory_failures:
                    _advisory_meta["typecheck_warnings"] = _tc_errors
                    _advisory_meta["typecheck_advisory_output"] = _tc_output[-2000:]
                if "contract" in advisory_failures:
                    _advisory_meta["contract_advisory_misses"] = (
                        _contract_misses.split("; ") if _contract_misses else []
                    )
                firestore_service.update_project(uid, project_id, {
                    "generated_files": generated_files,
                    "status": ProjectStatus.tested,
                    **_advisory_meta,
                })

            # ── Final STRICT gate — shared with run_test_debug_only via _apply_strict_gate ──
            _gate_failures = _apply_strict_gate(
                test_results, generated_files, blueprint_dict,
                plan.model_dump(), uid, project_id,
            )
            if _gate_failures:
                _write_generation_metrics("strict_failure", real_failures=sorted(_gate_failures))
                return {
                    "status": "error",
                    "zip_url": None,
                    "generated_files": generated_files,
                    "errors": [
                        f"Tests did not pass: {', '.join(sorted(_gate_failures))}. "
                        "Generated files are preserved — use 'Re-run Tests' to retry."
                    ],
                }

            # ── STEP 3: Verify (skipped in mock mode, infra warnings, or code failures) ─
            _verify_strict = (
                os.environ.get("DEMAESTRO_VERIFY_STRICT", "true").lower() != "false"
            )
            if not best_effort and not infra_warning and not settings.mock_ai:
                firestore_service.set_project_status(uid, project_id, ProjectStatus.verifying)
                firestore_service.update_project(uid, project_id, {"current_stage": "verifying"})

                verify_result = self.verifier.verify(generated_files, plan, blueprint)
                log.info("pipeline.verify.done", project_id=project_id, status=verify_result["status"])

                if verify_result["status"] == "fail":
                    # ── Self-heal: try to auto-stub any missing routes before failing. ──
                    missing_routes = verify_result.get("missing_routes") or []
                    if missing_routes:
                        synthetic_log = "\n".join(
                            f"CONTRACT MISS: {m['method']} {m['path']}   suggestions: (none)"
                            for m in missing_routes
                        )
                        patches = _auto_stub_missing_contract_endpoints(
                            {"logs": {"contract": synthetic_log}},
                            generated_files,
                        )
                        if patches:
                            generated_files.update(patches)
                            log.info(
                                "verify.self_heal.applied",
                                project_id=project_id,
                                patched_files=list(patches.keys()),
                                endpoints=[
                                    f"{m['method']} {m['path']}" for m in missing_routes
                                ],
                            )
                            verify_result = self.verifier.verify(generated_files, plan, blueprint)
                            log.info(
                                "pipeline.verify.after_self_heal",
                                project_id=project_id,
                                status=verify_result["status"],
                            )

                if verify_result["status"] == "fail":
                    issues = verify_result.get("issues", [])
                    log.warning(
                        "pipeline.verify.failed",
                        project_id=project_id,
                        num_issues=len(issues),
                        issues=issues[:5],
                    )
                    if _verify_strict:
                        # Write to test_results so the STRICT gate catches it.
                        if test_results is None:
                            test_results = {}
                        test_results.setdefault("passed_checks", {})["verify"] = "failed"
                        test_results.setdefault("logs", {})["verify"] = "; ".join(issues[:10])
                        test_results.setdefault("errors", []).extend(issues[:5])
                        # Apply the STRICT gate immediately.
                        if _apply_strict_gate(
                            test_results, generated_files,
                            blueprint.model_dump() if hasattr(blueprint, "model_dump") else {},
                            plan.model_dump(), uid, project_id,
                        ):
                            return {
                                "status": "error",
                                "zip_url": None,
                                "generated_files": generated_files,
                                "errors": [f"Verify failed: {'; '.join(issues[:3])}"],
                            }
                    else:
                        best_effort = True
                        warning_msg = f"Verify found {len(issues)} issue(s) — set DEMAESTRO_VERIFY_STRICT=false to bypass."

                firestore_service.set_project_status(uid, project_id, ProjectStatus.verified)
                firestore_service.update_project(uid, project_id, {"current_stage": "verified"})
            else:
                reason = (
                    "mock_mode" if settings.mock_ai
                    else "infra_warning" if infra_warning
                    else "test_failed"
                )
                log.info("pipeline.verify.skipped", project_id=project_id, reason=reason)

            # ── Final checklist re-verification ─────────────────────────────
            # Runs once right before packaging so the UI and the ZIP agree on
            # what actually shipped. Wrapped in try/except — packaging must not
            # be blocked by a checklist failure.
            if _checklist_full:
                try:
                    _cl_final = run_checklist(
                        _checklist_full, generated_files, {}, ""
                    )
                    firestore_service.update_project(uid, project_id, {
                        "checklist_results": _checklist_results_to_payload(_cl_final),
                    })
                    log.info(
                        "pipeline.checklist.final_refresh",
                        project_id=project_id,
                        num_pass=sum(1 for r in _cl_final if r.status == "pass"),
                        total=len(_cl_final),
                    )
                except Exception as _exc:
                    log.warning("pipeline.checklist.final_refresh_failed",
                                project_id=project_id, error=str(_exc)[:200])

            # ── STEP 4: Package → ZIP ────────────────────────────────────────
            firestore_service.set_project_status(uid, project_id, ProjectStatus.packaging)
            firestore_service.update_project(uid, project_id, {"current_stage": "packaging"})

            # Inject PAGES.md at project root before zipping
            pages_md = _build_pages_md(
                blueprint,
                sr.app_name or project_id,
                auth_enabled=(sr.auth_required is not False),
            )
            generated_files["PAGES.md"] = pages_md

            deploy_result = self.deployer.deploy(
                uid, project_id, generated_files, plan, app_name=sr.app_name
            )
            log.info("pipeline.deploy.done", project_id=project_id, status=deploy_result["status"])

            if deploy_result["status"] != "ready":
                raise RuntimeError(f"Packaging failed: {deploy_result.get('errors', [])}")

            # ready_with_warnings when tests failed for any reason (code or infra).
            if best_effort or infra_warning:
                status_update: dict = {"status": ProjectStatus.ready_with_warnings, "current_stage": "ready"}
                if best_effort:
                    status_update["last_error"] = warning_msg
                firestore_service.update_project(uid, project_id, status_update)
            else:
                firestore_service.update_project(uid, project_id, {"current_stage": "ready"})

            log.info("pipeline.done", project_id=project_id, zip_url=deploy_result["zip_url"])

            # ── Auto-deploy to Vercel (background, non-blocking) ─────────────
            if vercel_deployer.is_auto_deploy_enabled():
                firestore_service.update_project(uid, project_id, {
                    "deployment_status": "deploying",
                    "deployment_url": None,
                    "deployment_error": None,
                })

                _project_doc = firestore_service.get_project(uid, project_id)
                _app_name = _resolve_app_name(
                    sr,
                    getattr(_project_doc, "name", None) if _project_doc else None,
                    project_id,
                )
                log.info(
                    "pipeline.deploy.app_name_resolved",
                    project_id=project_id,
                    sr_app_name=getattr(sr, "app_name", None),
                    project_name=getattr(_project_doc, "name", None) if _project_doc else None,
                    resolved=_app_name,
                )

                def _bg_deploy(
                    _uid=uid, _project_id=project_id,
                    _files=dict(generated_files),
                    _app_name=_app_name,
                ):
                    try:
                        result = vercel_deployer.deploy(
                            project_name=_app_name,
                            files=_files,
                            wait_for_ready=True,
                            stable_id=_project_id,
                        )
                        log.info(
                            "pipeline.auto_deploy.done",
                            project_id=_project_id, url=result["url"],
                        )
                        try:
                            firestore_service.update_project(_uid, _project_id, {
                                "deployment_status": "ready",
                                "deployment_url": result["url"],
                                "deployment_project_name": result["project_name"],
                                "deployment_display_name": result.get("display_name"),
                                "deployment_error": None,
                            })
                            log.info(
                                "pipeline.auto_deploy.firestore_updated",
                                project_id=_project_id,
                            )
                        except Exception as fs_exc:
                            log.error(
                                "pipeline.auto_deploy.firestore_update_failed",
                                project_id=_project_id,
                                url=result["url"],
                                error=str(fs_exc),
                            )
                        # Post-deploy smoke + self-healing loop.
                        try:
                            self._post_deploy_fix_loop(
                                _project_id, _uid, result, _files,
                                contract_paths=None,
                            )
                        except Exception as _pde:
                            log.error(
                                "pipeline.auto_deploy.post_deploy_loop_error",
                                project_id=_project_id, error=str(_pde),
                            )
                    except vercel_deployer.VercelDeployError as e:
                        log.error(
                            "pipeline.auto_deploy.failed",
                            project_id=_project_id, error=str(e),
                        )
                        try:
                            firestore_service.update_project(_uid, _project_id, {
                                "deployment_status": "failed",
                                "deployment_error": str(e),
                            })
                        except Exception as fs_exc:
                            log.error(
                                "pipeline.auto_deploy.firestore_update_failed_after_fail",
                                project_id=_project_id, error=str(fs_exc),
                            )
                    except Exception as e:
                        log.error(
                            "pipeline.auto_deploy.exception",
                            project_id=_project_id, error=str(e),
                        )
                        try:
                            firestore_service.update_project(_uid, _project_id, {
                                "deployment_status": "failed",
                                "deployment_error": f"unexpected: {e}",
                            })
                        except Exception:
                            pass

                threading.Thread(target=_bg_deploy, daemon=True).start()
            else:
                log.info(
                    "pipeline.auto_deploy.disabled",
                    project_id=project_id,
                    reason="env vars not set or DEMAESTRO_AUTO_DEPLOY=false",
                )
            # ─────────────────────────────────────────────────────────────────

            # Write metrics BEFORE returning so the doc is populated, but guard
            # with an outer try/except: _write_generation_metrics already swallows
            # internally, but this is an extra defence so no bug in the metrics
            # path can ever flip a successful pipeline result to an error.
            _save_snapshot(project_id, generated_files, blueprint_dict,
                           "final", plan.model_dump())
            try:
                _write_generation_metrics("success")
            except Exception as _m_exc:
                log.error("metrics.write.success_path_failed",
                          project_id=project_id, exc=str(_m_exc))
            return {
                "status": "success",
                "zip_url": deploy_result["zip_url"],
                "generated_files": generated_files,
                "errors": [warning_msg] if (best_effort or infra_warning) else [],
            }

        except Exception as exc:
            log.error("pipeline.error", project_id=project_id, error=str(exc))
            firestore_service.update_project(uid, project_id, {
                "status": ProjectStatus.failed,
                "error_message": str(exc),
            })
            try:
                _write_generation_metrics("exception", exception=str(exc))
            except Exception as _m_exc:
                log.error("metrics.write.exception_path_failed",
                          project_id=project_id, exc=str(_m_exc))
            return {
                "status": "error",
                "zip_url": None,
                "generated_files": {},
                "errors": [str(exc)],
            }
        finally:
            _mark_inactive(project_id)
