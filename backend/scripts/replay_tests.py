#!/usr/bin/env python
"""Replay the test phase against a saved snapshot — no LLM, no Claude tokens.

Usage:
  python backend/scripts/replay_tests.py ~/.demaestro/snapshots/cv-app__abc12345__...
  python backend/scripts/replay_tests.py ~/.demaestro/snapshots/cv-app__abc12345__... --stack python-postgres
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai.claude.agents.tester import TesterAgent
from app.models.generation_plan import GenerationPlan


def _load_snapshot(snapshot_dir: Path) -> tuple[dict, dict, dict]:
    """Return (files, manifest, blueprint)."""
    files: dict[str, str] = {}
    for path in snapshot_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(snapshot_dir).as_posix()
        if rel.startswith("_"):
            continue  # skip metadata files
        try:
            files[rel] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            pass  # skip binary files

    manifest: dict = {}
    mf = snapshot_dir / "_manifest.json"
    if mf.exists():
        manifest = json.loads(mf.read_text())

    blueprint: dict = {}
    bf = snapshot_dir / "_blueprint.json"
    if bf.exists():
        blueprint = json.loads(bf.read_text())

    return files, manifest, blueprint


def _build_plan(manifest: dict, stack_override: str | None) -> GenerationPlan:
    """Reconstruct a minimal GenerationPlan from snapshot metadata."""
    plan_data = manifest.get("plan") or {}
    stack = stack_override or plan_data.get("technology_stack") or "python-postgres"

    if plan_data:
        try:
            return GenerationPlan(**plan_data)
        except Exception:
            pass

    # Fallback: minimal plan with just the stack
    return GenerationPlan(
        technology_stack=stack,
        files=[],
        generation_order=[],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot_dir", type=Path, help="Path to snapshot directory")
    parser.add_argument(
        "--stack",
        default=None,
        help="Override technology stack (default: read from manifest)",
    )
    args = parser.parse_args()

    if not args.snapshot_dir.is_dir():
        sys.exit(f"Not a directory: {args.snapshot_dir}")

    files, manifest, blueprint = _load_snapshot(args.snapshot_dir)
    print(f"Loaded {len(files)} files from snapshot")
    print(f"  stage: {manifest.get('stage', 'unknown')}  saved: {manifest.get('saved_at', '?')}")

    plan = _build_plan(manifest, args.stack)
    print(f"  stack: {plan.technology_stack}")

    tester = TesterAgent()
    results = tester.run_tests(files, plan)

    print("\n=== Test Results ===")
    for check, status in results.get("passed_checks", {}).items():
        marker = "✓" if status == "passed" else ("~" if status == "skipped" else "✗")
        print(f"  {marker} {check}: {status}")

    errors = results.get("errors") or []
    if errors:
        print("\n=== Errors ===")
        for e in errors[:10]:
            print(f"  {e[:200]}")

    real_failures = [
        c for c, s in results.get("passed_checks", {}).items()
        if s == "failed" and c in {"install", "lint", "boot", "frontend_build", "contract"}
    ]
    print(f"\nReal failures: {real_failures}")
    sys.exit(0 if not real_failures else 1)


if __name__ == "__main__":
    main()
