"""Generation orchestrator — full state machine (W5-P2).

State machine:
  approved → generating → generated → testing → tested
           → verifying → verified → packaging → ready | ready_with_warnings
  (on any failure) → failed

Best-effort packaging: if tests fail after all debug cycles, the pipeline
still packages the ZIP and sets status to ready_with_warnings so the user
can inspect and fix the code themselves.
"""
import json
import re as _re
import structlog

from app.ai.claude.agents.architect import ArchitectAgent
from app.config import settings
from app.ai.claude.agents.debugger import DebuggerAgent, _is_infrastructure_error
from app.ai.claude.agents.deployer import DeployerAgent
from app.ai.claude.agents.generator import GeneratorAgent
from app.ai.claude.agents.tester import TesterAgent
from app.ai.claude.agents.verifier import VerifierAgent
from app.ai.gemini.agents.blueprint import BlueprintResponse
from app.models.generation_plan import GenerationPlan
from app.models.project import ProjectStatus
from app.services import firestore_service
from app.services import template_service

log = structlog.get_logger("GenerationOrchestrator")

_MAX_TEST_CYCLES = 10

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


def _detect_design_brief(sr) -> dict:
    """Scan the structured requirements for visual styling cues. Returns a dict
    like {'primary_color': 'blue', 'vibe': 'minimalist', 'cues': [...]}."""
    haystack_parts: list[str] = []
    haystack_parts.append(sr.summary or "")
    for r in sr.user_requirements:
        haystack_parts.append(r.statement or "")
        haystack_parts.append(r.rationale or "")
    haystack = " ".join(haystack_parts).lower()

    chosen_color: str | None = None
    for color in _TAILWIND_PALETTES.keys():
        # Any standalone mention of the color word that is reasonably close to
        # a style cue ("color", "theme", "palette", "tones", "style", "look",
        # "scheme", "shade") — or just a strong standalone mention.
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


def _apply_design_brief_to_scaffolding(generated_files: dict, brief: dict) -> None:
    """Mutate the seeded tailwind.config.js so the primary palette matches the
    user-requested color. No-op when no color was detected."""
    color = brief.get("primary_color")
    if not color or color not in _TAILWIND_PALETTES:
        return
    path = "frontend/tailwind.config.js"
    config = generated_files.get(path)
    if not config:
        return
    palette = _TAILWIND_PALETTES[color]
    new_block = "primary: {\n"
    for k, v in palette.items():
        new_block += f'          {k}: "{v}",\n'
    new_block += "        },"
    new_config, count = _re.subn(
        r"primary:\s*\{[^{}]*\},",
        new_block,
        config,
        count=1,
        flags=_re.DOTALL,
    )
    if count > 0:
        generated_files[path] = new_config
        log.info("pipeline.design_brief_applied", color=color)


class GenerationOrchestrator:
    def __init__(self) -> None:
        self.architect = ArchitectAgent()
        self.generator = GeneratorAgent()
        self.tester = TesterAgent()
        self.debugger = DebuggerAgent()
        self.verifier = VerifierAgent()
        self.deployer = DeployerAgent()

    # ── W5-P1 simple entry point (kept for backward-compat) ──────────────────

    def run_generation(self, uid: str, project_id: str) -> dict:
        """Architect + generate only (no test/debug/verify/deploy).

        Returns: { status, generated_files, plan, errors }
        """
        log.info("generation.start", project_id=project_id)

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
            else:
                plan = self.architect.architect(sr, blueprint)
                log.info("generation.architect.done", project_id=project_id, num_files=len(plan.files))

                firestore_service.update_project(uid, project_id, {
                    "generation_plan": plan.model_dump(),
                })

                app_slug = template_service.slugify(sr.app_name)
                scaffolding = template_service.load_stack_templates(plan.technology_stack, sr.app_name, app_slug)
                generated_files = dict(scaffolding)
                log.info("generation.scaffolding.seeded", project_id=project_id, num_scaffold=len(scaffolding))

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
                if design_brief["cues"]:
                    _apply_design_brief_to_scaffolding(generated_files, design_brief)
                    brief_note = (
                        "DESIGN BRIEF (apply consistently across every page): "
                        f"primary color = {design_brief.get('primary_color') or 'default'}; "
                        f"vibe = {design_brief.get('vibe') or 'clean and modern'}. "
                        "Use the standard Tailwind `primary-*` classes (primary-50 .. primary-900) "
                        "for buttons, links, accents, and highlights — the primary palette has "
                        "already been swapped to match the requested color."
                    )
                    try:
                        plan.notes = (plan.notes + "\n\n" + brief_note) if plan.notes else brief_note
                    except Exception:
                        pass

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

    # ── W5-P2 full pipeline ──────────────────────────────────────────────────

    def run_full_pipeline(self, uid: str, project_id: str) -> dict:
        """Execute the full generation pipeline.

        Returns: { status, zip_url, generated_files, errors }
        """
        log.info("pipeline.start", project_id=project_id)

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
            else:
                firestore_service.set_project_status(uid, project_id, ProjectStatus.generating)
                plan = self.architect.architect(sr, blueprint)
                log.info("pipeline.architect.done", project_id=project_id, num_files=len(plan.files))

                firestore_service.update_project(uid, project_id, {
                    "generation_plan": plan.model_dump(),
                })

                app_slug = template_service.slugify(sr.app_name)
                scaffolding = template_service.load_stack_templates(plan.technology_stack, sr.app_name, app_slug)
                generated_files = dict(scaffolding)
                log.info("pipeline.scaffolding.seeded", project_id=project_id, num_scaffold=len(scaffolding))

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
                if design_brief["cues"]:
                    _apply_design_brief_to_scaffolding(generated_files, design_brief)
                    brief_note = (
                        "DESIGN BRIEF (apply consistently across every page): "
                        f"primary color = {design_brief.get('primary_color') or 'default'}; "
                        f"vibe = {design_brief.get('vibe') or 'clean and modern'}. "
                        "Use the standard Tailwind `primary-*` classes (primary-50 .. primary-900) "
                        "for buttons, links, accents, and highlights — the primary palette has "
                        "already been swapped to match the requested color."
                    )
                    try:
                        plan.notes = (plan.notes + "\n\n" + brief_note) if plan.notes else brief_note
                    except Exception:
                        pass

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

            firestore_service.update_project(uid, project_id, {
                "generation_plan": plan.model_dump(),
                "status": ProjectStatus.generated,
                "generated_count": len(generated_files),
                "current_file": None,
            })
            log.info("pipeline.generate.done", project_id=project_id, num_files=len(generated_files))

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

            # ── STEP 2: Test + Debug loop ────────────────────────────────────
            attempt_count: dict[str, int] = {}
            test_passed = False
            best_effort = False
            infra_warning = False
            warning_msg = ""
            test_results: dict = {}

            for cycle in range(_MAX_TEST_CYCLES):
                firestore_service.set_project_status(uid, project_id, ProjectStatus.testing)
                firestore_service.update_project(uid, project_id, {"current_stage": "testing"})

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

                firestore_service.update_project(uid, project_id, {"current_stage": "debugging"})
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
                    generated_files.update(debug_result["fixed_files"])
                    attempt_count = debug_result["attempt_counts"]
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
                    # Debug could not identify or fix the file — package best-effort anyway.
                    log.warning(
                        "pipeline.debug.cannot_fix",
                        project_id=project_id,
                        cycle=cycle + 1,
                        errors=debug_result.get("errors"),
                    )
                    break  # test_passed stays False → best_effort block below

            # Identify which failed checks are environmental vs real code/contract bugs.
            final_checks = (test_results or {}).get("passed_checks", {}) or {}
            real_failures = {
                check for check, st in final_checks.items()
                if st == "failed" and check not in ("smoke", "typecheck")
            }
            environmental_skips = {
                check for check, st in final_checks.items() if st == "skipped"
            }

            if not test_passed and real_failures:
                # STRICT: real failures that couldn't be auto-fixed — mark FAILED, no ZIP.
                error_msg = (
                    f"Generation failed: {', '.join(sorted(real_failures))} did not pass "
                    "after debug attempts. No ZIP packaged — please review the requirements "
                    "or click Retry to start a fresh generation."
                )
                log.error(
                    "pipeline.strict_failure",
                    project_id=project_id,
                    real_failures=sorted(real_failures),
                    environmental_skips=sorted(environmental_skips),
                )
                firestore_service.update_project(uid, project_id, {
                    "status": ProjectStatus.failed,
                    "current_stage": "generating",
                    "error_message": error_msg,
                    "last_failed_checks": sorted(real_failures),
                    "test_error_log": ("\n".join(test_results.get("errors", []) or []))[:5000],
                })
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
                firestore_service.update_project(uid, project_id, {
                    "generated_files": generated_files,
                    "status": ProjectStatus.tested,
                })

            # ── STEP 3: Verify (skipped in mock mode, infra warnings, or code failures) ─
            if not best_effort and not infra_warning and not settings.mock_ai:
                firestore_service.set_project_status(uid, project_id, ProjectStatus.verifying)
                firestore_service.update_project(uid, project_id, {"current_stage": "verifying"})

                verify_result = self.verifier.verify(generated_files, plan, blueprint)
                log.info("pipeline.verify.done", project_id=project_id, status=verify_result["status"])

                if verify_result["status"] == "fail":
                    raise RuntimeError(f"Verification failed: {verify_result['issues']}")

                firestore_service.set_project_status(uid, project_id, ProjectStatus.verified)
                firestore_service.update_project(uid, project_id, {"current_stage": "verified"})
            else:
                reason = (
                    "mock_mode" if settings.mock_ai
                    else "infra_warning" if infra_warning
                    else "test_failed"
                )
                log.info("pipeline.verify.skipped", project_id=project_id, reason=reason)

            # ── STEP 4: Package → ZIP ────────────────────────────────────────
            firestore_service.set_project_status(uid, project_id, ProjectStatus.packaging)
            firestore_service.update_project(uid, project_id, {"current_stage": "packaging"})

            deploy_result = self.deployer.deploy(uid, project_id, generated_files, plan)
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
            return {
                "status": "error",
                "zip_url": None,
                "generated_files": {},
                "errors": [str(exc)],
            }
