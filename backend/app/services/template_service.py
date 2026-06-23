"""Loads deterministic stack scaffolding templates with placeholder substitution."""

import re
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).parent.parent / "ai" / "claude" / "stack_templates"

# Directory and file patterns to skip during template scan.
_SKIP_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
}
_SKIP_EXTS = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".so",
    ".dylib",
    ".dll",
    ".o",
    ".class",
    ".jar",
    ".zip",
    ".tar",
    ".gz",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".pdf",
    ".mp3",
    ".mp4",
    ".wav",
    ".webp",
}


def load_stack_templates(stack: str, app_name: str, app_slug: str) -> dict[str, str]:
    """Load all template files for a stack, returning {relative_path: content}.

    Placeholders {{app_name}} and {{app_slug}} are substituted in every file.
    Binary files and cache directories are skipped silently.
    """
    stack_dir = _TEMPLATES_DIR / stack
    if not stack_dir.exists():
        raise ValueError(f"Unknown stack: {stack!r} — no templates at {stack_dir}")

    files: dict[str, str] = {}
    for path in sorted(stack_dir.rglob("*")):
        if not path.is_file():
            continue
        # Skip anything inside a cache/build directory.
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        # Skip known binary extensions.
        if path.suffix.lower() in _SKIP_EXTS:
            continue
        # Defensive read — if a file we did not anticipate is binary,
        # skip rather than crash the pipeline.
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        content = content.replace("{{app_name}}", app_name)
        content = content.replace("{{app_slug}}", app_slug)
        rel = path.relative_to(stack_dir).as_posix()
        files[rel] = content
    return files


def slugify(app_name: str) -> str:
    """Convert 'Collaborative Notes' → 'collaborative-notes'."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", app_name.lower()).strip("-")
    return slug or "app"
