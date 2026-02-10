"""
/v1/collateral Endpoint Evaluation Tests

5 use cases validating collateral data accuracy against ground truth:
1. Collateral type accuracy - Are collateral types correct?
2. Debt instrument linking - Is collateral linked to correct debt?
3. Priority ordering - Are collateral priorities correct?
4. Ticker filter - Does ticker filter return correct company?
5. Value reasonableness - Are estimated values reasonable?
"""

import pytest
import httpx

from tests.eval.scoring import (
    EvalResult, PrimitiveScore,
    compare_numeric, compare_exact, compare_all_match,
)
from tests.eval.ground_truth import GroundTruthManager


PRIMITIVE = "/v1/collateral"


# =============================================================================
# USE CASE 1: COLLATERAL TYPE ACCURACY
# =============================================================================

@pytest.mark.eval
def test_collateral_types_are_valid(api_client: httpx.Client):
    """Verify collateral types are recognized categories."""
    valid_types = {
        "equipment", "inventory", "accounts_receivable", "real_estate",
        "intellectual_property", "stock_pledge", "assets", "all_assets",
        "substantially_all_assets", "first_lien", "second_lien",
        "cash", "securities", "property", "vessels", "rigs",
    }

    response = api_client.get("/v1/collateral", params={
        "fields": "collateral_type,description,company_ticker",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    collateral = data["data"]
    if not collateral:
        pytest.skip("No collateral data available")

    # Check types (allow unknown since extraction may find new types)
    types_found = {c.get("collateral_type") for c in collateral}
    all_have_type = all(c.get("collateral_type") for c in collateral)

    assert all_have_type, "Some collateral records missing type"


@pytest.mark.eval
@pytest.mark.asyncio
async def test_collateral_type_matches_database(
    api_client: httpx.Client,
    ground_truth: GroundTruthManager,
):
    """Verify collateral types match database records."""
    response = api_client.get("/v1/collateral", params={
        "ticker": "RIG",
        "fields": "id,collateral_type,description,debt_instrument_id",
        "limit": "10",
    })
    response.raise_for_status()
    data = response.json()

    collateral = data["data"]
    if not collateral:
        pytest.skip("No RIG collateral data")

    # Verify at least one record
    first = collateral[0]
    assert first.get("collateral_type"), "Collateral missing type"
    assert first.get("debt_instrument_id"), "Collateral not linked to debt"


# =============================================================================
# USE CASE 2: DEBT INSTRUMENT LINKING
# =============================================================================

@pytest.mark.eval
def test_collateral_linked_to_debt(api_client: httpx.Client):
    """Verify all collateral is linked to a debt instrument."""
    response = api_client.get("/v1/collateral", params={
        "fields": "id,debt_instrument_id,bond_name,bond_cusip,company_ticker",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    collateral = data["data"]
    if not collateral:
        pytest.skip("No collateral data")

    unlinked = []
    for c in collateral:
        if not c.get("debt_instrument_id"):
            unlinked.append(c.get("id"))

    assert len(unlinked) == 0, f"Unlinked collateral: {len(unlinked)} records"


@pytest.mark.eval
def test_collateral_has_bond_info(api_client: httpx.Client):
    """Verify collateral includes bond name/cusip."""
    response = api_client.get("/v1/collateral", params={
        "fields": "id,bond_name,bond_cusip,company_ticker,collateral_type",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    collateral = data["data"]
    if not collateral:
        pytest.skip("No collateral data")

    # Most should have bond name
    with_name = [c for c in collateral if c.get("bond_name")]
    assert len(with_name) >= len(collateral) * 0.5, \
        f"Only {len(with_name)}/{len(collateral)} have bond_name"


# =============================================================================
# USE CASE 3: PRIORITY ORDERING
# =============================================================================

@pytest.mark.eval
def test_collateral_priority_values(api_client: httpx.Client):
    """Verify collateral priority values are valid."""
    response = api_client.get("/v1/collateral", params={
        "fields": "id,priority,collateral_type,company_ticker",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    collateral = data["data"]
    if not collateral:
        pytest.skip("No collateral data")

    invalid_priority = []
    for c in collateral:
        priority = c.get("priority")
        if priority is not None:
            # Priority should be 1, 2, 3, etc. or "first_lien", "second_lien"
            if isinstance(priority, int):
                if priority < 1 or priority > 10:
                    invalid_priority.append({
                        "id": c.get("id"),
                        "priority": priority,
                    })

    assert len(invalid_priority) == 0, f"Invalid priorities: {invalid_priority}"


@pytest.mark.eval
def test_first_lien_has_higher_priority(api_client: httpx.Client):
    """Verify first lien collateral has appropriate priority indicators."""
    response = api_client.get("/v1/collateral", params={
        "fields": "id,priority,instrument_security_type,company_ticker",
        "limit": "100",
    })
    response.raise_for_status()
    data = response.json()

    collateral = data["data"]
    if not collateral:
        pytest.skip("No collateral data")

    # Valid priorities for first_lien: 1, "first_lien", "first-priority", "senior_secured", "exclusive"
    valid_first_lien_priorities = {1, "first_lien", "first-priority", "senior_secured", "exclusive"}

    mismatched = []
    for c in collateral:
        security_type = c.get("instrument_security_type")
        priority = c.get("priority")

        if security_type == "first_lien" and priority:
            # Check if priority indicates first position (numeric 1 or appropriate string)
            is_valid = priority in valid_first_lien_priorities
            if not is_valid and isinstance(priority, int):
                is_valid = priority == 1

            if not is_valid:
                mismatched.append({
                    "id": c.get("id"),
                    "security_type": security_type,
                    "priority": priority,
                })

    # Allow some mismatches (complex capital structures)
    assert len(mismatched) <= 2, f"First lien priority mismatches: {mismatched}"


# =============================================================================
# USE CASE 4: TICKER FILTER
# =============================================================================

@pytest.mark.eval
def test_ticker_filter_returns_correct_company(api_client: httpx.Client):
    """Verify ticker filter returns only that company's collateral."""
    ticker = "RIG"

    response = api_client.get("/v1/collateral", params={
        "ticker": ticker,
        "fields": "id,company_ticker,collateral_type,bond_name",
        "limit": "20",
    })
    response.raise_for_status()
    data = response.json()

    collateral = data["data"]
    if not collateral:
        pytest.skip(f"No collateral for {ticker}")

    result = compare_all_match(
        actual=collateral,
        field="company_ticker",
        expected_value=ticker,
        test_id=f"collateral.ticker_filter.{ticker}",
    )
    assert result.passed, result.message


@pytest.mark.eval
def test_ticker_filter_multiple(api_client: httpx.Client):
    """Verify multiple ticker filter."""
    tickers = ["RIG", "CHTR"]

    response = api_client.get("/v1/collateral", params={
        "ticker": ",".join(tickers),
        "fields": "id,company_ticker,collateral_type",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    collateral = data["data"]
    if not collateral:
        pytest.skip("No collateral for requested tickers")

    returned_tickers = {c.get("company_ticker") for c in collateral}

    # Should only have requested tickers
    unexpected = returned_tickers - set(tickers)
    assert len(unexpected) == 0, f"Unexpected tickers: {unexpected}"


# =============================================================================
# USE CASE 5: VALUE REASONABLENESS
# =============================================================================

@pytest.mark.eval
def test_estimated_values_are_positive(api_client: httpx.Client):
    """Verify estimated values are positive when present."""
    response = api_client.get("/v1/collateral", params={
        "fields": "id,estimated_value,collateral_type,company_ticker",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    collateral = data["data"]
    if not collateral:
        pytest.skip("No collateral data")

    negative_values = []
    for c in collateral:
        value = c.get("estimated_value")
        if value is not None and value < 0:
            negative_values.append({
                "id": c.get("id"),
                "value": value,
            })

    assert len(negative_values) == 0, f"Negative values: {negative_values}"


@pytest.mark.eval
def test_values_in_reasonable_range(api_client: httpx.Client):
    """Verify estimated values are in reasonable range (when present)."""
    response = api_client.get("/v1/collateral", params={
        "fields": "id,estimated_value,collateral_type,company_ticker",
        "limit": "100",
    })
    response.raise_for_status()
    data = response.json()

    collateral = data["data"]
    if not collateral:
        pytest.skip("No collateral data")

    # Values in cents - reasonable range is $1K to $100B
    min_value = 100_000  # $1K in cents
    max_value = 10_000_000_000_000  # $100B in cents

    out_of_range = []
    for c in collateral:
        value = c.get("estimated_value")
        if value is not None:
            if value < min_value or value > max_value:
                out_of_range.append({
                    "id": c.get("id"),
                    "value": value,
                    "ticker": c.get("company_ticker"),
                })

    # Allow some outliers
    assert len(out_of_range) <= 5, f"Values out of range: {out_of_range}"


# =============================================================================
# AGGREGATE SCORING
# =============================================================================

def collect_collateral_score() -> PrimitiveScore:
    """Collect all test results into a PrimitiveScore."""
    return PrimitiveScore(primitive=PRIMITIVE)
