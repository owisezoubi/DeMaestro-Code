"""Vercel serverless entry point. Re-exports the FastAPI app."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "backend"))

from app.main import app  # noqa: E402, F401
