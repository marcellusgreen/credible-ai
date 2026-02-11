"""
API contract tests for GET /v1/companies/{ticker}/changes endpoint.

Validates response structure and error handling for the changes diff endpoint.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Skip all tests in this module if no API key
pytestmark = pytest.mark.skipif(
    not os.getenv("DEBTSTACK_API_KEY") and not os.getenv("TEST_API_KEY"),
    reason="No API key configured"
)

# Use a recent date — snapshots may only exist for recent dates
SINCE_DATE = "2026-02-01"


def get_api_client():
    """Get configured API client."""
    import httpx
    base_url = os.getenv("TEST_API_URL", "https://api.debtstack.ai")
    api_key = os.getenv("DEBTSTACK_API_KEY") or os.getenv("TEST_API_KEY")
    return httpx.Client(
        base_url=base_url,
        headers={"X-API-Key": api_key},
        timeout=30.0
    )


def _is_no_snapshot(response) -> bool:
    """Check if a 404 response is a NO_SNAPSHOT error."""
    if response.status_code != 404:
        return False
    try:
        data = response.json()
        error = data.get("error", {})
        # The API nests the code in the message string or as a top-level code
        return (
            error.get("code") == "NO_SNAPSHOT"
            or "NO_SNAPSHOT" in str(error.get("message", ""))
            or error.get("code") == "not_found" and "NO_SNAPSHOT" in str(error.get("message", ""))
        )
    except Exception:
        return False


class TestChangesEndpoint:
    """Tests for GET /v1/companies/{ticker}/changes."""

    @pytest.mark.api
    def test_returns_200_with_valid_params(self):
        """Valid ticker and since date returns 200 (or skip if no snapshot)."""
        with get_api_client() as client:
            response = client.get("/v1/companies/CHTR/changes", params={"since": SINCE_DATE})
            if _is_no_snapshot(response):
                pytest.skip("No snapshot data available for CHTR")
            assert response.status_code == 200

    @pytest.mark.api
    def test_response_has_required_top_level_fields(self):
        """Response contains ticker, since, changes, summary."""
        with get_api_client() as client:
            response = client.get("/v1/companies/CHTR/changes", params={"since": SINCE_DATE})
            if _is_no_snapshot(response):
                pytest.skip("No snapshot data available")
            assert response.status_code == 200
            data = response.json()
            for field in ["ticker", "since", "changes", "summary"]:
                assert field in data, f"Missing top-level field: {field}"

    @pytest.mark.api
    def test_changes_has_required_keys(self):
        """Changes object contains expected change categories."""
        with get_api_client() as client:
            response = client.get("/v1/companies/CHTR/changes", params={"since": SINCE_DATE})
            if _is_no_snapshot(response):
                pytest.skip("No snapshot data available")
            assert response.status_code == 200
            data = response.json()
            changes = data["changes"]
            for key in ["new_debt", "removed_debt", "entity_changes", "metric_changes", "pricing_changes"]:
                assert key in changes, f"Missing changes key: {key}"

    @pytest.mark.api
    def test_entity_changes_has_added_removed(self):
        """entity_changes contains 'added' and 'removed' arrays."""
        with get_api_client() as client:
            response = client.get("/v1/companies/CHTR/changes", params={"since": SINCE_DATE})
            if _is_no_snapshot(response):
                pytest.skip("No snapshot data available")
            assert response.status_code == 200
            data = response.json()
            entity_changes = data["changes"]["entity_changes"]
            assert "added" in entity_changes
            assert "removed" in entity_changes
            assert isinstance(entity_changes["added"], list)
            assert isinstance(entity_changes["removed"], list)

    @pytest.mark.api
    def test_summary_field_types(self):
        """Summary counts are int and has_changes is bool."""
        with get_api_client() as client:
            response = client.get("/v1/companies/CHTR/changes", params={"since": SINCE_DATE})
            if _is_no_snapshot(response):
                pytest.skip("No snapshot data available")
            assert response.status_code == 200
            data = response.json()
            summary = data["summary"]
            if "has_changes" in summary:
                assert isinstance(summary["has_changes"], bool)
            for key in summary:
                if key.endswith("_count"):
                    assert isinstance(summary[key], int), f"{key} should be int"

    @pytest.mark.api
    def test_invalid_ticker_returns_404(self):
        """Non-existent ticker returns 404."""
        with get_api_client() as client:
            response = client.get("/v1/companies/ZZZZZZ/changes", params={"since": SINCE_DATE})
            assert response.status_code == 404

    @pytest.mark.api
    def test_missing_since_returns_422(self):
        """Missing since parameter returns 422."""
        with get_api_client() as client:
            response = client.get("/v1/companies/AAPL/changes")
            assert response.status_code == 422

    @pytest.mark.api
    def test_invalid_date_format_returns_422(self):
        """Invalid since date format returns 422."""
        with get_api_client() as client:
            response = client.get("/v1/companies/AAPL/changes", params={"since": "invalid"})
            assert response.status_code == 422

    @pytest.mark.api
    def test_future_date_handled(self):
        """Future since date returns 404 NO_SNAPSHOT or valid data."""
        with get_api_client() as client:
            response = client.get("/v1/companies/AAPL/changes", params={"since": "2030-01-01"})
            assert response.status_code in (200, 404)
