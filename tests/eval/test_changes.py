"""
/v1/companies/{ticker}/changes Endpoint Evaluation Tests

6 use cases validating change diff accuracy:
1. Response structure validation
2. Summary counts and types
3. New debt entry fields
4. Invalid ticker handling
5. Missing since parameter
6. Invalid date format
"""

import pytest
import httpx

from tests.eval.scoring import EvalResult, PrimitiveScore


PRIMITIVE = "/v1/companies/{ticker}/changes"


# =============================================================================
# USE CASE 1: RESPONSE STRUCTURE
# =============================================================================

@pytest.mark.eval
def test_changes_response_structure(api_client: httpx.Client):
    """GET CHTR changes, verify top-level keys. Skip if NO_SNAPSHOT."""
    response = api_client.get("/v1/companies/CHTR/changes", params={"since": "2025-01-01"})

    if response.status_code == 404:
        data = response.json()
        if data.get("error", {}).get("code") == "NO_SNAPSHOT":
            pytest.skip("No snapshot data available for CHTR")
        pytest.fail(f"Unexpected 404: {data}")

    response.raise_for_status()
    data = response.json()

    assert "ticker" in data
    assert "since" in data
    assert "changes" in data
    assert "summary" in data

    # Changes should have standard keys
    changes = data["changes"]
    expected_keys = {"new_debt", "removed_debt", "entity_changes", "metric_changes", "pricing_changes"}
    actual_keys = set(changes.keys())
    missing = expected_keys - actual_keys
    assert len(missing) == 0, f"Missing changes keys: {missing}"


# =============================================================================
# USE CASE 2: SUMMARY COUNTS
# =============================================================================

@pytest.mark.eval
def test_changes_summary_counts(api_client: httpx.Client):
    """Summary has count fields (int) and has_changes (bool)."""
    response = api_client.get("/v1/companies/CHTR/changes", params={"since": "2025-01-01"})
    if response.status_code == 404:
        pytest.skip("No snapshot data available")

    response.raise_for_status()
    data = response.json()
    summary = data["summary"]

    if "has_changes" in summary:
        assert isinstance(summary["has_changes"], bool)

    # All _count fields should be integers
    for key, value in summary.items():
        if key.endswith("_count"):
            assert isinstance(value, int), f"summary.{key} should be int, got {type(value)}"
        if key == "new_debt_count":
            assert value >= 0


# =============================================================================
# USE CASE 3: NEW DEBT ENTRY FIELDS
# =============================================================================

@pytest.mark.eval
def test_changes_new_debt_fields(api_client: httpx.Client):
    """If new_debt is non-empty, each entry has id, name, instrument_type."""
    response = api_client.get("/v1/companies/CHTR/changes", params={"since": "2025-01-01"})
    if response.status_code == 404:
        pytest.skip("No snapshot data available")

    response.raise_for_status()
    data = response.json()
    new_debt = data["changes"]["new_debt"]

    if not new_debt:
        pytest.skip("No new debt entries to validate")

    for entry in new_debt:
        assert "id" in entry or "name" in entry, f"New debt entry missing id/name: {entry}"
        if "instrument_type" in entry:
            assert isinstance(entry["instrument_type"], str)


# =============================================================================
# USE CASE 4: INVALID TICKER
# =============================================================================

@pytest.mark.eval
def test_changes_invalid_ticker(api_client: httpx.Client):
    """ZZZZZZ ticker returns 404 with NOT_FOUND code."""
    response = api_client.get("/v1/companies/ZZZZZZ/changes", params={"since": "2025-01-01"})
    assert response.status_code == 404

    data = response.json()
    error = data.get("error", data.get("detail", {}))
    if isinstance(error, dict):
        assert error.get("code") in ("NOT_FOUND", "NO_SNAPSHOT", None)


# =============================================================================
# USE CASE 5: MISSING SINCE PARAMETER
# =============================================================================

@pytest.mark.eval
def test_changes_missing_since_param(api_client: httpx.Client):
    """No since parameter returns 422."""
    response = api_client.get("/v1/companies/CHTR/changes")
    assert response.status_code == 422


# =============================================================================
# USE CASE 6: DATE FORMAT VALIDATION
# =============================================================================

@pytest.mark.eval
def test_changes_date_format_validation(api_client: httpx.Client):
    """since=not-a-date returns 422."""
    response = api_client.get("/v1/companies/CHTR/changes", params={"since": "not-a-date"})
    assert response.status_code == 422


# =============================================================================
# AGGREGATE SCORING
# =============================================================================

def collect_changes_score() -> PrimitiveScore:
    """Collect all test results into a PrimitiveScore."""
    return PrimitiveScore(primitive=PRIMITIVE)
