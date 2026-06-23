#!/usr/bin/env python
"""Keep only the N most recent snapshots per project.

Groups snapshots by project-id prefix (second __ segment) and removes
the oldest beyond the retention limit.

Usage:
  python backend/scripts/prune_snapshots.py
  python backend/scripts/prune_snapshots.py --keep 5
  python backend/scripts/prune_snapshots.py --dry-run
"""
import argparse
import shutil
import sys
from collections import defaultdict
from pathlib import Path

SNAPSHOT_ROOT = Path.home() / ".demaestro" / "snapshots"
DEFAULT_KEEP = 3


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep",
        type=int,
        default=DEFAULT_KEEP,
        help=f"Number of snapshots to keep per project (default: {DEFAULT_KEEP})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be removed without deleting",
    )
    args = parser.parse_args()

    if not SNAPSHOT_ROOT.exists():
        print(f"Snapshot root does not exist: {SNAPSHOT_ROOT}")
        sys.exit(0)

    groups: dict[str, list[Path]] = defaultdict(list)
    for d in SNAPSHOT_ROOT.iterdir():
        if not d.is_dir():
            continue
        parts = d.name.split("__")
        if len(parts) >= 2:
            project_key = parts[1]  # 8-char project_id prefix
        else:
            project_key = d.name
        groups[project_key].append(d)

    total_removed = 0
    for project_key, dirs in sorted(groups.items()):
        # Sort by directory name (which embeds the ISO timestamp) — newest first
        dirs.sort(key=lambda p: p.name, reverse=True)
        to_remove = dirs[args.keep:]
        for old in to_remove:
            if args.dry_run:
                print(f"[dry-run] would remove: {old.name}")
            else:
                shutil.rmtree(old)
                print(f"Removed: {old.name}")
            total_removed += 1

    if total_removed == 0:
        print("Nothing to prune.")
    elif args.dry_run:
        print(f"\n{total_removed} snapshot(s) would be removed (--dry-run, no changes made).")
    else:
        print(f"\nRemoved {total_removed} snapshot(s).")


if __name__ == "__main__":
    main()
