"""Loads deterministic stack scaffolding templates with placeholder substitution."""
import re
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).parent.parent / "ai" / "claude" / "stack_templates"


def load_stack_templates(stack: str, app_name: str, app_slug: str) -> dict[str, str]:
    """Load all template files for a stack, returning {relative_path: content}.

    Placeholders {{app_name}} and {{app_slug}} are substituted in every file.
    """
    stack_dir = _TEMPLATES_DIR / stack
    if not stack_dir.exists():
        raise ValueError(f"Unknown stack: {stack!r} — no templates at {stack_dir}")

    files: dict[str, str] = {}
    for path in sorted(stack_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(stack_dir).as_posix()
        content = path.read_text(encoding="utf-8")
        content = content.replace("{{app_name}}", app_name)
        content = content.replace("{{app_slug}}", app_slug)
        files[rel] = content
    return files


def slugify(app_name: str) -> str:
    """Convert 'Collaborative Notes' → 'collaborative-notes'."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", app_name.lower()).strip("-")
    return slug or "app"
