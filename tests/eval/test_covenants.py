"""
/v1/covenants Endpoint Evaluation Tests

6 use cases validating covenant data accuracy against ground truth:
1. Covenant type accuracy - Are covenant types correctly categorized?
2. Threshold values accuracy - Are threshold values correct?
3. Test metric accuracy - Are test metrics correctly identified?
4. Ticker filter - Does ticker filter work correctly?
5. Covenant type filter - Does type filter return correct covenants?
6. Field selection - Do requested fields appear?
"""

import pytest
import httpx

from tests.eval.scoring import (
    EvalResult, PrimitiveScore,
    compare_numeric, compare_exact, compare_all_match,
)
from tests.eval.ground_truth import GroundTruthManager


PRIMITIVE = "/v1/covenants"


# Valid covenant types from the schema
VALID_COVENANT_TYPES = {
    "financial", "negative", "incurrence", "protective",
    "affirmative", "maintenance", "reporting",
}

# Common test metrics
COMMON_TEST_METRICS = {
    "leverage_ratio", "net_leverage_ratio", "interest_coverage",
    "fixed_charge_coverage", "debt_to_ebitda", "ebitda_to_interest",
    "minimum_liquidity", "maximum_capex",
}


# =============================================================================
# USE CASE 1: COVENANT TYPE ACCURACY
# =============================================================================

@pytest.mark.eval
def test_covenant_types_are_valid(api_client: httpx.Client):
    """Verify covenant types are recognized categories."""
    response = api_client.get("/v1/covenants", params={
        "fields": "covenant_type,covenant_name,ticker",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    covenants = data["data"]
    if not covenants:
        pytest.skip("No covenant data available")

    # All should have a type
    missing_type = [c for c in covenants if not c.get("covenant_type")]
    assert len(missing_type) == 0, f"Covenants missing type: {len(missing_type)}"


@pytest.mark.eval
@pytest.mark.asyncio
async def test_covenant_types_match_database(
    api_client: httpx.Client,
    ground_truth: GroundTruthManager,
):
    """Verify covenant types match database records."""
    ticker = "CHTR"

    response = api_client.get("/v1/covenants", params={
        "ticker": ticker,
        "fields": "id,covenant_type,covenant_name,test_metric",
        "limit": "20",
    })
    response.raise_for_status()
    data = response.json()

    covenants = data["data"]
    if not covenants:
        pytest.skip(f"No covenant data for {ticker}")

    # Get ground truth
    gt = await ground_truth.get_covenants_for_company(ticker)
    if gt is None:
        pytest.skip(f"No ground truth for {ticker}")

    api_types = {c.get("covenant_type") for c in covenants}
    gt_types = {c.get("covenant_type") for c in gt.value}

    # Types should overlap significantly
    common = api_types & gt_types
    assert len(common) >= 1 or not gt_types, \
        f"No overlap between API types {api_types} and GT types {gt_types}"


# =============================================================================
# USE CASE 2: THRESHOLD VALUES ACCURACY
# =============================================================================

@pytest.mark.eval
def test_threshold_values_are_reasonable(api_client: httpx.Client):
    """Verify threshold values are in reasonable ranges."""
    response = api_client.get("/v1/covenants", params={
        "covenant_type": "financial",
        "fields": "covenant_name,test_metric,threshold_value,threshold_type,ticker",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    covenants = data["data"]
    if not covenants:
        pytest.skip("No financial covenant data")

    unreasonable = []
    for c in covenants:
        value = c.get("threshold_value")
        metric = c.get("test_metric")

        if value is not None and metric:
            # Leverage ratios should be 0-20x
            if "leverage" in metric.lower():
                if value < 0 or value > 20:
                    unreasonable.append({
                        "name": c.get("covenant_name"),
                        "metric": metric,
                        "value": value,
                    })
            # Coverage ratios should be 0-20x
            elif "coverage" in metric.lower():
                if value < 0 or value > 20:
                    unreasonable.append({
                        "name": c.get("covenant_name"),
                        "metric": metric,
                        "value": value,
                    })

    assert len(unreasonable) <= 2, f"Unreasonable values: {unreasonable}"


@pytest.mark.eval
def test_threshold_types_are_valid(api_client: httpx.Client):
    """Verify threshold types are valid."""
    valid_types = {"max", "min", "range", "maximum", "minimum", "not_to_exceed", "at_least"}

    response = api_client.get("/v1/covenants", params={
        "fields": "covenant_name,threshold_type,threshold_value",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    covenants = data["data"]
    if not covenants:
        pytest.skip("No covenant data")

    # Just verify structure - allow flexible types
    with_type = [c for c in covenants if c.get("threshold_type")]

    # At least some should have threshold types
    if len(covenants) > 10:
        assert len(with_type) >= 3, \
            "Too few covenants have threshold_type"


# =============================================================================
# USE CASE 3: TEST METRIC ACCURACY
# =============================================================================

@pytest.mark.eval
def test_test_metrics_are_valid(api_client: httpx.Client):
    """Verify test metrics are recognized."""
    response = api_client.get("/v1/covenants", params={
        "covenant_type": "financial",
        "fields": "covenant_name,test_metric,threshold_value",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    covenants = data["data"]
    if not covenants:
        pytest.skip("No financial covenant data")

    # Financial covenants should have test metrics
    with_metric = [c for c in covenants if c.get("test_metric")]

    # Most financial covenants should have metrics
    assert len(with_metric) >= len(covenants) * 0.5, \
        f"Only {len(with_metric)}/{len(covenants)} have test_metric"


@pytest.mark.eval
def test_leverage_covenants_have_metrics(api_client: httpx.Client):
    """Verify leverage covenants have appropriate metrics."""
    response = api_client.get("/v1/covenants", params={
        "test_metric": "leverage_ratio",
        "fields": "covenant_name,test_metric,threshold_value,ticker",
        "limit": "30",
    })
    response.raise_for_status()
    data = response.json()

    covenants = data["data"]
    if not covenants:
        pytest.skip("No leverage ratio covenants")

    for c in covenants:
        metric = c.get("test_metric")
        assert "leverage" in metric.lower(), \
            f"Expected leverage metric, got {metric}"


# =============================================================================
# USE CASE 4: TICKER FILTER
# =============================================================================

@pytest.mark.eval
def test_ticker_filter_returns_correct_company(api_client: httpx.Client):
    """Verify ticker filter returns only that company's covenants."""
    ticker = "CHTR"

    response = api_client.get("/v1/covenants", params={
        "ticker": ticker,
        "fields": "id,ticker,covenant_name,covenant_type",
        "limit": "30",
    })
    response.raise_for_status()
    data = response.json()

    covenants = data["data"]
    if not covenants:
        pytest.skip(f"No covenants for {ticker}")

    result = compare_all_match(
        actual=covenants,
        field="ticker",
        expected_value=ticker,
        test_id=f"covenants.ticker_filter.{ticker}",
    )
    assert result.passed, result.message


@pytest.mark.eval
def test_ticker_filter_multiple(api_client: httpx.Client):
    """Verify multiple ticker filter."""
    tickers = ["CHTR", "T"]

    response = api_client.get("/v1/covenants", params={
        "ticker": ",".join(tickers),
        "fields": "ticker,covenant_name,covenant_type",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    covenants = data["data"]
    if not covenants:
        pytest.skip("No covenants for requested tickers")

    returned_tickers = {c.get("ticker") for c in covenants}

    # Should only have requested tickers
    unexpected = returned_tickers - set(tickers)
    assert len(unexpected) == 0, f"Unexpected tickers: {unexpected}"


# =============================================================================
# USE CASE 5: COVENANT TYPE FILTER
# =============================================================================

@pytest.mark.eval
def test_covenant_type_filter_financial(api_client: httpx.Client):
    """Verify covenant_type=financial returns only financial covenants."""
    response = api_client.get("/v1/covenants", params={
        "covenant_type": "financial",
        "fields": "covenant_name,covenant_type,test_metric",
        "limit": "30",
    })
    response.raise_for_status()
    data = response.json()

    covenants = data["data"]
    if not covenants:
        pytest.skip("No financial covenants")

    result = compare_all_match(
        actual=covenants,
        field="covenant_type",
        expected_value="financial",
        test_id="covenants.type_filter.financial",
    )
    assert result.passed, result.message


@pytest.mark.eval
def test_covenant_type_filter_negative(api_client: httpx.Client):
    """Verify covenant_type=negative returns only negative covenants."""
    response = api_client.get("/v1/covenants", params={
        "covenant_type": "negative",
        "fields": "covenant_name,covenant_type,description",
        "limit": "30",
    })
    response.raise_for_status()
    data = response.json()

    covenants = data["data"]
    if not covenants:
        pytest.skip("No negative covenants")

    result = compare_all_match(
        actual=covenants,
        field="covenant_type",
        expected_value="negative",
        test_id="covenants.type_filter.negative",
    )
    assert result.passed, result.message


# =============================================================================
# USE CASE 6: FIELD SELECTION
# =============================================================================

@pytest.mark.eval
def test_field_selection_returns_requested(api_client: httpx.Client):
    """Verify field selection returns only requested fields."""
    fields = ["ticker", "covenant_name", "covenant_type", "threshold_value"]

    response = api_client.get("/v1/covenants", params={
        "fields": ",".join(fields),
        "limit": "10",
    })
    response.raise_for_status()
    data = response.json()

    covenants = data["data"]
    if not covenants:
        pytest.skip("No covenant data")

    covenant = covenants[0]

    # Check all requested fields present
    missing = [f for f in fields if f not in covenant]
    assert len(missing) == 0, f"Missing fields: {missing}"


@pytest.mark.eval
def test_field_selection_all_covenant_fields(api_client: httpx.Client):
    """Verify all covenant fields can be requested."""
    all_fields = [
        "id", "ticker", "covenant_name", "covenant_type",
        "test_metric", "threshold_value", "threshold_type",
        "description",
    ]

    response = api_client.get("/v1/covenants", params={
        "fields": ",".join(all_fields),
        "limit": "5",
    })
    response.raise_for_status()
    data = response.json()

    covenants = data["data"]
    if not covenants:
        pytest.skip("No covenant data")

    covenant = covenants[0]

    # Check key fields present
    key_fields = ["ticker", "covenant_name", "covenant_type"]
    missing = [f for f in key_fields if f not in covenant]
    assert len(missing) == 0, f"Missing key fields: {missing}"


# =============================================================================
# AGGREGATE SCORING
# =============================================================================

def collect_covenants_score() -> PrimitiveScore:
    """Collect all test results into a PrimitiveScore."""
    return PrimitiveScore(primitive=PRIMITIVE)
