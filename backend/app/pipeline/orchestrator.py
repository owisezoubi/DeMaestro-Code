"""Pipeline orchestrator. Implemented progressively across Weeks 3–6.

Spawns a background thread per project that walks through:
  awaiting_input -> structuring -> clarifying -> awaiting_approval ->
  blueprinting -> generating -> verifying -> packaging -> ready
"""
from threading import Thread

import structlog

log = structlog.get_logger(__name__)


def kick_off(uid: str, project_id: str) -> None:
    """Start the pipeline thread for a project. Stub."""
    log.info("pipeline.kick_off", uid=uid, project_id=project_id)
    # Real impl will Thread(target=_run_pipeline, ...).start()
    raise NotImplementedError("Implemented progressively across Weeks 3–6")
