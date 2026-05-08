"""Claude Agent SDK driver. Implemented in Week 5.

Responsibilities:
  - Mount the approved blueprint into a sandbox container.
  - Drive the agent loop: write files, run, read errors, fix, repeat.
  - Return the path to the verified project directory.
"""
from pathlib import Path

import structlog

from app.config import settings

log = structlog.get_logger(__name__)


def is_mock() -> bool:
    return settings.mock_ai


def generate_project(blueprint: dict, workdir: Path) -> Path:
    """Run the Claude agent loop until the generated app boots cleanly."""
    if is_mock():
        log.info("claude.mock", stage="generate_project")
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "MOCK.txt").write_text("Mock generated project.")
        return workdir
    raise NotImplementedError("Implemented in Week 5")
