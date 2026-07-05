"""Per-item checklist check runner.

Each check function returns a CheckResult. The driver run_checklist()
iterates all items and never lets a single check raise -- exceptions
degrade to a fail result with the traceback as the reason.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Literal, Optional

import structlog

from app.pipeline.checklist import (
    ChecklistItem,
    ModelItem,
    PageItem,
    RouteItem,
    SeedItem,
    StyleItem,
)

log = structlog.get_logger("checklist_runner")


# ============================================================
# Result type
# ============================================================

@dataclass
class CheckResult:
    item_id: str
    status: Literal["pass", "partial", "fail"]
    reason: str = ""
    file: Optional[str] = None


# ============================================================
# Individual checkers
# ============================================================

# Models that the scaffold owns — defined in auth_models.py and re-exported
# from models.py. Always pass without the substring check.
_SCAFFOLD_OWNED_MODELS: frozenset[str] = frozenset({"User"})


def check_model_item(item: ModelItem, files: dict) -> CheckResult:
    """Verify the model class exists in models.py with expected columns."""
    # Scaffold-owned models live in auth_models.py and are always present.
    if item.class_name in _SCAFFOLD_OWNED_MODELS:
        return CheckResult(item_id=item.id, status="pass", reason="provided by scaffold")

    models_content = files.get("backend/app/models.py", "")
    auth_models_content = files.get("backend/app/auth_models.py", "")

    # Check re-export in models.py counts as "present".
    reexport_present = (
        f"from app.auth_models import {item.class_name}" in models_content
    )
    if reexport_present:
        return CheckResult(item_id=item.id, status="pass")

    if not models_content:
        return CheckResult(
            item_id=item.id, status="fail",
            reason="backend/app/models.py not found in generated files",
            file="backend/app/models.py",
        )

    # Try parsing models.py; fall back to auth_models.py if class not found there.
    for source_path, source_content in (
        ("backend/app/models.py", models_content),
        ("backend/app/auth_models.py", auth_models_content),
    ):
        if not source_content:
            continue
        try:
            tree = ast.parse(source_content)
        except SyntaxError as exc:
            return CheckResult(
                item_id=item.id, status="fail",
                reason=f"{source_path} has a syntax error: {exc}",
                file=source_path,
            )

        class_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == item.class_name:
                class_node = node
                break

        if class_node is None:
            continue

        # Collect annotated names from the class body.
        annotated_names: set[str] = set()
        for child in ast.walk(class_node):
            if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                annotated_names.add(child.target.id)

        # Also check raw attribute assignments (for older SQLAlchemy style).
        assigned_names: set[str] = set()
        for child in class_node.body:
            if isinstance(child, ast.Assign):
                for t in child.targets:
                    if isinstance(t, ast.Name):
                        assigned_names.add(t.id)

        all_names = annotated_names | assigned_names

        missing = []
        for col in item.columns:
            col_name = col.get("name", "")
            if col_name and col_name not in all_names:
                missing.append(col_name)

        if missing:
            return CheckResult(
                item_id=item.id, status="partial",
                reason=f"Class {item.class_name!r} present but missing columns: {missing}",
                file=source_path,
            )

        return CheckResult(item_id=item.id, status="pass")

    return CheckResult(
        item_id=item.id, status="fail",
        reason=f"Class {item.class_name!r} not found in models.py or auth_models.py",
        file="backend/app/models.py",
    )


def check_route_item(
    item: RouteItem, openapi_paths: dict, files: dict,
) -> CheckResult:
    """Verify route exists in openapi with the correct HTTP method."""
    method = item.method.lower()
    path = item.path

    # Check openapi paths dict first (most reliable when available).
    if openapi_paths:
        path_entry = openapi_paths.get(path) or openapi_paths.get(path.rstrip("/"))
        if path_entry:
            if method in path_entry:
                return CheckResult(item_id=item.id, status="pass")
            return CheckResult(
                item_id=item.id, status="fail",
                reason=f"{item.method} not found in OpenAPI for path {path!r}; available: {list(path_entry.keys())}",
            )
        return CheckResult(
            item_id=item.id, status="fail",
            reason=f"Path {path!r} not found in OpenAPI spec",
        )

    # Fallback: scan generated route files for @router.<method>(...) decorator.
    probe = re.sub(r"^/api", "", path)
    method_pat = re.escape(method)

    _ROUTER_PREFIX_RE = re.compile(
        r'APIRouter\s*\([^)]*prefix\s*=\s*["\']([^"\']*)["\']',
    )

    # Scaffold auth routes live at a fixed path — include them in the scan.
    _SCAFFOLD_ROUTE_FILES = frozenset({
        "backend/app/routes/auth_routes.py",
        "backend/app/auth.py",
    })

    for fp, content in files.items():
        is_routes_dir = fp.startswith("backend/app/routes/")
        is_scaffold_route = fp in _SCAFFOLD_ROUTE_FILES
        if (not is_routes_dir and not is_scaffold_route) or not content:
            continue
        pm = _ROUTER_PREFIX_RE.search(content)
        router_prefix = pm.group(1) if pm else ""
        if router_prefix and probe.startswith(router_prefix):
            deco_path = probe[len(router_prefix):] or "/"
        else:
            deco_path = probe

        # Build a pattern that allows {param} placeholders.
        deco_escaped = re.escape(deco_path)
        deco_escaped = re.sub(r"\\{[^}]+\\}", r"[^\"']*", deco_escaped)
        # When the effective decorator path is "/" or "", accept either.
        if deco_path in ("/", ""):
            deco_pat = r'[/]?'
        else:
            deco_pat = deco_escaped

        decorator_re = re.compile(
            rf'@router\.{method_pat}\s*\(\s*["\']' + deco_pat + r'["\']',
        )
        if decorator_re.search(content):
            return CheckResult(item_id=item.id, status="pass")

    return CheckResult(
        item_id=item.id, status="fail",
        reason=f"@router.{method}({probe!r}) decorator not found in any route file",
    )


def check_page_item(item: PageItem, files: dict) -> CheckResult:
    """Verify page file exists (with multi-variant lookup), has
    default export, and route is wired into App.jsx or routes.js.
    """
    # Try the exact path first.
    page_content = files.get(item.file_path, "")
    actual_path = item.file_path

    if not page_content:
        # Multi-variant lookup: handle common LLM naming differences
        # (MyPlantsPage.jsx vs MyPlants.jsx, Page suffix added/stripped).
        base = item.file_path.split("/")[-1]
        stem = re.sub(r"\.(jsx|tsx)$", "", base)
        alt_stems = {stem, stem + "Page", re.sub(r"Page$", "", stem)}
        alt_stems = {s for s in alt_stems if s}
        for path, content in files.items():
            if not path.startswith("frontend/src/pages/"):
                continue
            if not re.search(r"\.(jsx|tsx)$", path):
                continue
            if not content:
                continue
            file_stem = re.sub(r"\.(jsx|tsx)$", "", path.split("/")[-1])
            if file_stem not in alt_stems:
                continue
            page_content = content
            actual_path = path
            break

    if not page_content:
        return CheckResult(
            item_id=item.id, status="fail",
            reason=(
                f"Page file not found at {item.file_path!r} "
                f"or any close variant under frontend/src/pages/"
            ),
            file=item.file_path,
        )

    # Default export check.
    if "export default" not in page_content:
        return CheckResult(
            item_id=item.id, status="fail",
            reason=f"Page {actual_path!r} has no default export",
            file=actual_path,
        )

    # Route wiring check.
    route = item.route
    for routing_path in (
        "frontend/src/App.jsx",
        "frontend/src/App.tsx",
        "frontend/src/lib/routes.js",
        "frontend/src/lib/routes.ts",
    ):
        content = files.get(routing_path, "")
        if content and route in content:
            return CheckResult(item_id=item.id, status="pass")

    return CheckResult(
        item_id=item.id, status="fail",
        reason=f"Route {route!r} not found in App.jsx or routes.js",
        file=actual_path,
    )


def check_nav_reachability(
    page_items: list[PageItem], files: dict,
) -> list[CheckResult]:
    """For each page with nav_label, verify the route appears somewhere reachable."""
    results = []
    navbar_candidates = [
        "frontend/src/components/Navbar.jsx",
        "frontend/src/components/Navbar.tsx",
        "frontend/src/components/NavBar.jsx",
        "frontend/src/components/nav/Navbar.jsx",
        "frontend/src/lib/routes.js",
        "frontend/src/lib/routes.ts",
    ]

    nav_content = ""
    for nc in navbar_candidates:
        c = files.get(nc, "")
        if c:
            nav_content += c + "\n"

    for item in page_items:
        if not item.nav_label:
            continue
        route = item.route
        if route in nav_content or item.nav_label in nav_content:
            results.append(CheckResult(
                item_id=f"nav_reachability.{item.id}",
                status="pass",
            ))
        else:
            results.append(CheckResult(
                item_id=f"nav_reachability.{item.id}",
                status="fail",
                reason=(
                    f"Page {item.file_path!r} has nav_label={item.nav_label!r} "
                    f"but route {route!r} not found in Navbar or routes.js"
                ),
            ))

    return results


def check_seed_item(
    item: SeedItem, boot_log: str, files: dict | None = None,
) -> CheckResult:
    """Verify seed item.
    Mode 1 (boot_log empty): check seed.py for model usage.
    Mode 2 (boot_log present): check boot output for seed row mention.
    """
    if not boot_log:
        # Gate-time: check seed.py file content.
        if not files:
            return CheckResult(
                item_id=item.id, status="fail",
                reason="No seed.py file context available",
            )
        seed_content = files.get("backend/app/seed.py", "")
        if not seed_content:
            return CheckResult(
                item_id=item.id, status="fail",
                reason="seed.py file not generated",
            )
        if re.search(rf"\b{re.escape(item.model)}\s*\(", seed_content):
            return CheckResult(item_id=item.id, status="pass")
        return CheckResult(
            item_id=item.id, status="fail",
            reason=(
                f"seed.py does not reference {item.model!r}; "
                f"seed function may not insert rows for this model"
            ),
        )

    # Post-boot: check boot log.
    model_lower = item.model.lower()
    seed_section = boot_log.lower()
    if "seed" not in seed_section:
        return CheckResult(
            item_id=item.id, status="fail",
            reason="Boot log contains no '[seed]' output",
        )
    if model_lower in seed_section:
        return CheckResult(item_id=item.id, status="pass")
    snake = re.sub(r"([A-Z])", r"_\1", item.model).lower().lstrip("_")
    for table_name in (snake + "s", snake.replace("_", " ") + "s",
                       snake.replace("_", " ")):
        if table_name in seed_section:
            return CheckResult(item_id=item.id, status="pass")
    return CheckResult(
        item_id=item.id, status="fail",
        reason=(
            f"Boot log does not mention model {item.model!r}; "
            "seed may not have run or may be missing rows"
        ),
    )


def check_style_item(item: StyleItem, files: dict) -> CheckResult:
    """Verify CSS var is defined in index.css or tailwind.config."""
    css_var = item.css_var
    css_value = item.value

    css_path = "frontend/src/index.css"
    tailwind_path = "frontend/tailwind.config.js"

    for path in (css_path, tailwind_path):
        content = files.get(path, "")
        if content and css_var in content:
            return CheckResult(item_id=item.id, status="pass")

    return CheckResult(
        item_id=item.id, status="fail",
        reason=(
            f"CSS variable {css_var!r} (expected value {css_value!r}) "
            f"not found in {css_path} or {tailwind_path}"
        ),
    )


# ============================================================
# Import-resolution check
# ============================================================

_KNOWN_STDLIB_AND_DEPS = {
    "os", "sys", "re", "json", "datetime", "typing", "pathlib",
    "collections", "functools", "itertools", "uuid", "hashlib",
    "inspect", "contextlib", "asyncio",
    "fastapi", "starlette", "pydantic", "sqlalchemy", "passlib",
    "jose", "structlog", "anthropic", "google",
    "app.database", "app.models", "app.schemas", "app.auth",
    "app.seed", "app.main",
}


def _suggest_correct_module(
    names: list[str], existing_modules: set[str],
) -> str:
    """Crude heuristic: if any imported name starts with a capital,
    it's probably a class/model — suggest app.models."""
    if any(n and n[0].isupper() for n in names):
        if "app.models" in existing_modules:
            return "app.models"
    return ""


def check_import_resolution(
    files: dict,
) -> list[CheckResult]:
    """For each generated .py file under backend/app/, walk import
    AST nodes. For any `from app.X import Y` where app.X does NOT
    exist as a file in `files` and is not in _KNOWN_STDLIB_AND_DEPS,
    report a failure with a clear reason.
    """
    results = []
    file_modules: set[str] = set()
    for path in files:
        if not path.startswith("backend/app/"):
            continue
        if not path.endswith(".py"):
            continue
        parts = path[len("backend/"):].rsplit(".py", 1)[0].split("/")
        module = ".".join(parts)
        file_modules.add(module)

    for path, content in files.items():
        if not path.startswith("backend/app/"):
            continue
        if not path.endswith(".py") or not content:
            continue
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            mod = node.module
            if not mod or not mod.startswith("app."):
                continue
            if mod in _KNOWN_STDLIB_AND_DEPS:
                continue
            if mod in file_modules:
                continue
            imported_names = [n.name for n in node.names]
            suggestion = _suggest_correct_module(imported_names, file_modules)
            results.append(CheckResult(
                item_id=f"import.{path}",
                status="fail",
                reason=(
                    f"{path} imports from `{mod}` which does not exist. "
                    f"Imported names: {imported_names}. "
                    + (f"Did you mean `{suggestion}`?" if suggestion else "")
                ),
                file=path,
            ))
    return results


# ============================================================
# Driver
# ============================================================

def run_checklist(
    checklist: list,
    files: dict,
    openapi_paths: dict,
    boot_log: str,
) -> list[CheckResult]:
    """Run all checklist item checks. Never raises."""
    results: list[CheckResult] = []

    for item in checklist:
        try:
            item_type = getattr(item, "type", None)
            if item_type == "model":
                results.append(check_model_item(item, files))
            elif item_type == "route":
                results.append(check_route_item(item, openapi_paths, files))
            elif item_type == "page":
                results.append(check_page_item(item, files))
            elif item_type == "seed":
                results.append(check_seed_item(item, boot_log, files))
            elif item_type == "style":
                results.append(check_style_item(item, files))
        except Exception as exc:
            results.append(CheckResult(
                item_id=getattr(item, "id", "unknown"),
                status="fail",
                reason=f"check raised: {type(exc).__name__}: {exc}",
            ))

    # Nav reachability is cross-item.
    try:
        page_items = [i for i in checklist if getattr(i, "type", None) == "page"]
        results.extend(check_nav_reachability(page_items, files))
    except Exception as exc:
        log.warning("checklist_runner.nav_reachability_failed", error=str(exc))

    passed = sum(1 for r in results if r.status == "pass")
    partial = sum(1 for r in results if r.status == "partial")
    failed = sum(1 for r in results if r.status == "fail")
    log.info(
        "checklist.coverage",
        total=len(results),
        passed=passed,
        partial=partial,
        failed=failed,
        failed_items=[r.item_id for r in results if r.status == "fail"],
    )

    return results
