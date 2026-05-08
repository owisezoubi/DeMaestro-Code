"""Per-project Docker container lifecycle. Implemented in Week 5.

Hardening flags applied to every container:
  network_mode=none, mem_limit=512m, cpu_quota=50%, wall-clock timeout=5min
"""
from pathlib import Path

DEFAULT_FLAGS = dict(
    network_mode="none",
    mem_limit="512m",
    nano_cpus=int(0.5 * 1e9),  # 50% of one CPU core
    detach=True,
)


def launch(image: str, project_dir: Path):
    """Launch a hardened container and return the container handle. Stub."""
    raise NotImplementedError("Implemented in Week 5")
