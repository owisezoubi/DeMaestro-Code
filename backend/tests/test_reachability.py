"""Unit tests for reachability classification and DELETE stub idempotency."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ai.claude.agents.tester import _classify_reachability_status
from app.ai.claude.agents.debugger import _generate_handler_stub


def test_reachability_distinguishes_fastapi_404_from_handler_404():
    # FastAPI default 404 — route genuinely missing
    v, r = _classify_reachability_status(
        status=404, method="GET",
        body_text='{"detail":"Not Found"}',
    )
    assert v == "FAIL"
    assert "route does not exist" in r

    # Handler-returned 404 — route exists, row not found
    v, r = _classify_reachability_status(
        status=404, method="DELETE",
        body_text='{"detail":"Plant not found"}',
    )
    assert v == "PASS"
    assert "route exists" in r

    # 200 always passes
    v, r = _classify_reachability_status(
        status=200, method="GET", body_text="{}",
    )
    assert v == "PASS"

    # 401/403 always pass
    v, r = _classify_reachability_status(
        status=401, method="GET", body_text="",
    )
    assert v == "PASS"

    # 422 on write verbs passes
    v, r = _classify_reachability_status(
        status=422, method="POST",
        body_text='{"detail":[{"loc":["body","email"],"msg":"required"}]}',
    )
    assert v == "PASS"

    # 405 fails
    v, r = _classify_reachability_status(
        status=405, method="POST", body_text="",
    )
    assert v == "FAIL"

    # 500 fails
    v, r = _classify_reachability_status(
        status=500, method="GET", body_text="",
    )
    assert v == "FAIL"


def test_handler_404_with_non_json_body_treated_as_fastapi_default():
    # If body isn't valid JSON at all, be conservative: treat as default 404.
    v, r = _classify_reachability_status(
        status=404, method="GET", body_text="Not Found",
    )
    assert v == "FAIL"
    assert "route does not exist" in r


def test_handler_404_empty_body_treated_as_fastapi_default():
    v, r = _classify_reachability_status(
        status=404, method="GET", body_text="",
    )
    assert v == "FAIL"


def test_auto_stub_delete_returns_204_when_not_found():
    stub = _generate_handler_stub(
        method="delete",
        handler_path="/{plant_id}/favorite",
        func_name="delete_plants_favorite",
        endpoint={
            "method": "DELETE",
            "path": "/api/plants/{plant_id}/favorite",
            "auth": True,
        },
    )
    assert "Response(status_code=204)" in stub
    assert "404" not in stub
    # Both branches (found and not found) must return 204
    assert stub.count("Response(status_code=204)") == 2


def test_auto_stub_delete_has_no_raise_http_404():
    stub = _generate_handler_stub(
        method="delete",
        handler_path="/{item_id}",
        func_name="delete_items",
        endpoint={"method": "DELETE", "path": "/api/items/{item_id}", "auth": False},
    )
    assert "status_code=404" not in stub
    assert "Response(status_code=204)" in stub
