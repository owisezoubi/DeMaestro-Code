"""Tests for the pipeline orchestrator (W3-B). No real Firebase or Gemini calls."""
import time
from datetime import datetime, timezone
from unittest.mock import patch

from app.models.project import ProjectStatus
from app.models.raw_input import RawInputDoc, RawInputType
from app.pipeline.orchestrator import kick_off_structuring


def test_kick_off_structuring_in_mock_mode_does_not_crash(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "mock_ai", True)

    fake_input = RawInputDoc(
        id="abc123000000",
        type=RawInputType.text,
        content="I want a todo app",
        char_count=17,
        timestamp=datetime.now(timezone.utc),
    )

    with (
        patch("app.services.firestore_service.list_raw_inputs", return_value=[fake_input]),
        patch("app.services.firestore_service.set_project_status") as mock_set_status,
        patch("app.services.firestore_service.add_structured_requirements") as mock_add_sr,
    ):
        kick_off_structuring("uid123", "proj456")
        time.sleep(0.5)

        statuses = [call.args[2] for call in mock_set_status.call_args_list]
        assert statuses[0] == ProjectStatus.structuring
        assert statuses[1] in (ProjectStatus.clarifying, ProjectStatus.awaiting_approval)

        mock_add_sr.assert_called_once()
