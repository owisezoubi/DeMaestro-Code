#!/usr/bin/env python
"""Run ONLY the algorithmic (non-LLM) debugger helpers against a snapshot.

Useful for iterating on _fix_missing_admin_endpoint, _fix_admin_prefix_missing,
_fix_users_me_to_auth_me, etc. without spending any Claude tokens.

Usage:
  python backend/scripts/replay_debug.py ~/.demaestro/snapshots/cv-app__abc12345__...
  python backend/scripts/replay_debug.py ~/.demaestro/snapshots/cv-app__abc12345__... --inplace
  python backend/scripts/replay_debug.py ~/.demaestro/snapshots/cv-app__abc12345__... --errors "NameError: UploadFile"
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai.claude.agents.debugger import DebuggerAgent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot_dir", type=Path, help="Path to snapshot directory")
    parser.add_argument(
        "--inplace",
        action="store_true",
        help="Modify the snapshot in-place (default: copy to *_replay/)",
    )
    parser.add_argument(
        "--errors",
        nargs="*",
        default=[],
        metavar="ERROR",
        help="Error strings to pass to contract-log–driven fixers",
    )
    parser.add_argument(
        "--contract-log",
        default="",
        help="Raw contract log string (for fixers that parse it)",
    )
    args = parser.parse_args()

    src = args.snapshot_dir.resolve()
    if not src.is_dir():
        sys.exit(f"Not a directory: {src}")

    # Work on a copy unless --inplace
    if args.inplace:
        target = src
        print(f"Working in-place: {target}")
    else:
        target = src.parent / f"{src.name}_replay"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src, target)
        print(f"Working copy: {target}")

    # Load files (skip metadata)
    files: dict[str, str] = {}
    for path in target.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(target).as_posix()
        if rel.startswith("_"):
            continue
        try:
            files[rel] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            pass

    print(f"Loaded {len(files)} files")

    # Build a minimal test_results so contract-log fixers fire if desired
    test_results = {
        "errors": list(args.errors),
        "logs": {"contract": args.contract_log, "contract_advisory": ""},
        "passed_checks": {},
    }

    debugger = DebuggerAgent()
    changed = debugger.run_algorithmic_fixes(files, test_results=test_results)

    print(f"\nFiles changed: {len(changed)}")
    for rel_path in changed:
        dest = target / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(files[rel_path], encoding="utf-8")
        print(f"  ✎  {rel_path}")

    if not changed:
        print("  (no changes — all algorithmic fixers were no-ops)")

    # Save an updated manifest
    mf = target / "_manifest.json"
    manifest: dict = {}
    if mf.exists():
        try:
            manifest = json.loads(mf.read_text())
        except Exception:
            pass
    manifest["replay_changed_files"] = changed
    mf.write_text(json.dumps(manifest, indent=2))

    print(f"\nReplay complete.  Re-run tests with:")
    print(f"  python backend/scripts/replay_tests.py {target}")


if __name__ == "__main__":
    main()
