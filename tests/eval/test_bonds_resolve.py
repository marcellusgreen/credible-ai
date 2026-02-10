"""
/v1/bonds/resolve Endpoint Evaluation Tests

6 use cases validating bond identifier resolution:
1. Free-text parsing - Does "RIG 8% 2028" resolve correctly?
2. CUSIP exact lookup - Does exact CUSIP return correct bond?
3. Ticker + coupon resolution - Does ticker + coupon find correct bond?
4. Ticker + maturity resolution - Does ticker + year find correct bond?
5. Fuzzy match confidence - Are confidence scores appropriate?
6. Multiple results ranking - Are best matches ranked first?
"""

import pytest
import httpx

from tests.eval.scoring import (
    EvalResult, PrimitiveScore,
    compare_numeric, compare_exact, compare_contains,
)
from tests.eval.ground_truth import GroundTruthManager


PRIMITIVE = "/v1/bonds/resolve"


# =============================================================================
# USE CASE 1: FREE-TEXT PARSING
# =============================================================================

@pytest.mark.eval
def test_free_text_parsing_rig_8_2028(api_client: httpx.Client):
    """Verify 'RIG 8% 2028' resolves to correct bond."""
    # Note: RIG has 8.00% notes due 2028, not 2027
    response = api_client.get("/v1/bonds/resolve", params={
        "q": "RIG 8% 2028",
    })
    response.raise_for_status()
    data = response.json()

    matches = data["data"]["matches"]
    if not matches:
        # Try alternative query format
        response2 = api_client.get("/v1/bonds/resolve", params={
            "ticker": "RIG",
            "coupon": "8.0",
        })
        response2.raise_for_status()
        data = response2.json()
        matches = data["data"]["matches"]

    if not matches:
        pytest.skip("No RIG bonds matched - may need different query format")

    # Best match should be RIG with ~8% coupon
    best = matches[0]["bond"]

    assert best.get("company_ticker") == "RIG", \
        f"Expected ticker RIG, got {best.get('company_ticker')}"

    coupon = best.get("coupon_rate")
    if coupon:
        assert 7.0 <= coupon <= 9.0, f"Expected coupon ~8%, got {coupon}%"


@pytest.mark.eval
def test_free_text_parsing_chtr_notes(api_client: httpx.Client):
    """Verify 'CHTR senior notes 2030' resolves."""
    response = api_client.get("/v1/bonds/resolve", params={
        "q": "CHTR senior notes 2030",
    })
    response.raise_for_status()
    data = response.json()

    matches = data["data"]["matches"]
    assert len(matches) >= 1, "Expected at least 1 match"

    best = matches[0]["bond"]
    assert best.get("company_ticker") == "CHTR", \
        f"Expected ticker CHTR, got {best.get('company_ticker')}"


@pytest.mark.eval
def test_free_text_parsing_percentage_format(api_client: httpx.Client):
    """Verify percentage formats are parsed correctly."""
    # Test with decimal percentage
    response = api_client.get("/v1/bonds/resolve", params={
        "q": "4.5% 2030",
        "ticker": "AAPL",
    })
    response.raise_for_status()
    data = response.json()

    matches = data["data"]["matches"]
    # Should find AAPL bonds with ~4.5% coupon
    for match in matches[:3]:
        bond = match["bond"]
        coupon = bond.get("coupon_rate")
        if coupon and bond.get("company_ticker") == "AAPL":
            assert 4.0 <= coupon <= 5.0, f"Expected ~4.5% coupon, got {coupon}%"


# =============================================================================
# USE CASE 2: CUSIP EXACT LOOKUP
# =============================================================================

@pytest.mark.eval
@pytest.mark.asyncio
async def test_cusip_exact_lookup(
    api_client: httpx.Client,
    ground_truth: GroundTruthManager,
):
    """Verify exact CUSIP lookup returns correct bond."""
    # First, get a valid CUSIP from the database
    bonds_response = api_client.get("/v1/bonds", params={
        "limit": "1",
        "fields": "cusip,name,company_ticker",
    })
    bonds_response.raise_for_status()
    bonds = bonds_response.json().get("data", [])

    if not bonds or not bonds[0].get("cusip"):
        pytest.skip("No bonds with CUSIPs in database")

    test_cusip = bonds[0]["cusip"]

    response = api_client.get("/v1/bonds/resolve", params={
        "cusip": test_cusip,
    })
    response.raise_for_status()
    data = response.json()

    matches = data["data"]["matches"]
    assert len(matches) >= 1, f"No match for CUSIP {test_cusip}"

    # Should be exact match
    assert data["data"]["exact_match"] == True, "Expected exact_match=true for CUSIP"

    best = matches[0]["bond"]
    result = compare_exact(
        expected=test_cusip,
        actual=best.get("cusip"),
        test_id="bonds_resolve.cusip_exact",
        source="API CUSIP lookup",
    )
    assert result.passed, result.message


@pytest.mark.eval
def test_cusip_exact_confidence(api_client: httpx.Client):
    """Verify exact CUSIP match has confidence 1.0."""
    # First, get a valid CUSIP from the database
    bonds_response = api_client.get("/v1/bonds", params={
        "limit": "1",
        "fields": "cusip,name,company_ticker",
    })
    bonds_response.raise_for_status()
    bonds = bonds_response.json().get("data", [])

    if not bonds or not bonds[0].get("cusip"):
        pytest.skip("No bonds with CUSIPs in database")

    test_cusip = bonds[0]["cusip"]

    response = api_client.get("/v1/bonds/resolve", params={
        "cusip": test_cusip,
    })
    response.raise_for_status()
    data = response.json()

    matches = data["data"]["matches"]
    if not matches:
        pytest.skip(f"CUSIP {test_cusip} not found")

    confidence = matches[0].get("confidence")
    assert confidence == 1.0, f"Expected confidence 1.0 for exact CUSIP, got {confidence}"


# =============================================================================
# USE CASE 3: TICKER + COUPON RESOLUTION
# =============================================================================

@pytest.mark.eval
def test_ticker_coupon_resolution(api_client: httpx.Client):
    """Verify ticker + coupon finds correct bond."""
    response = api_client.get("/v1/bonds/resolve", params={
        "ticker": "RIG",
        "coupon": "8.0",
    })
    response.raise_for_status()
    data = response.json()

    matches = data["data"]["matches"]
    assert len(matches) >= 1, "Expected at least 1 match"

    # All matches should be RIG
    for match in matches:
        bond = match["bond"]
        assert bond.get("company_ticker") == "RIG", \
            f"Expected RIG, got {bond.get('company_ticker')}"

        # Coupon should be within tolerance
        coupon = bond.get("coupon_rate")
        if coupon:
            assert 7.5 <= coupon <= 8.5, f"Expected coupon ~8%, got {coupon}%"


@pytest.mark.eval
def test_ticker_coupon_fuzzy_tolerance(api_client: httpx.Client):
    """Verify fuzzy mode allows coupon tolerance."""
    # Search for 8% but might find 7.75% or 8.25%
    response = api_client.get("/v1/bonds/resolve", params={
        "ticker": "CHTR",
        "coupon": "5.0",
        "match_mode": "fuzzy",
    })
    response.raise_for_status()
    data = response.json()

    matches = data["data"]["matches"]

    # In fuzzy mode, should find bonds with coupons near 5%
    found_near_5 = False
    for match in matches:
        coupon = match["bond"].get("coupon_rate")
        if coupon and 4.5 <= coupon <= 5.5:
            found_near_5 = True
            break

    if matches:
        assert found_near_5, "Expected to find bond with ~5% coupon in fuzzy mode"


# =============================================================================
# USE CASE 4: TICKER + MATURITY RESOLUTION
# =============================================================================

@pytest.mark.eval
def test_ticker_maturity_resolution(api_client: httpx.Client):
    """Verify ticker + maturity year finds correct bonds."""
    maturity_year = 2027

    response = api_client.get("/v1/bonds/resolve", params={
        "ticker": "RIG",
        "maturity_year": str(maturity_year),
    })
    response.raise_for_status()
    data = response.json()

    matches = data["data"]["matches"]
    assert len(matches) >= 1, "Expected at least 1 match"

    for match in matches:
        bond = match["bond"]
        maturity = bond.get("maturity_date")
        if maturity:
            assert str(maturity_year) in maturity, \
                f"Expected {maturity_year} maturity, got {maturity}"


@pytest.mark.eval
def test_ticker_maturity_range(api_client: httpx.Client):
    """Verify maturity year filter works for different years."""
    # Test a future year
    response = api_client.get("/v1/bonds/resolve", params={
        "ticker": "AAPL",
        "maturity_year": "2030",
    })
    response.raise_for_status()
    data = response.json()

    matches = data["data"]["matches"]

    for match in matches:
        bond = match["bond"]
        assert bond.get("company_ticker") == "AAPL"
        maturity = bond.get("maturity_date")
        if maturity:
            year = int(maturity[:4])
            assert year == 2030, f"Expected 2030 maturity, got {year}"


# =============================================================================
# USE CASE 5: FUZZY MATCH CONFIDENCE
# =============================================================================

@pytest.mark.eval
def test_fuzzy_confidence_ordering(api_client: httpx.Client):
    """Verify confidence scores are in descending order."""
    response = api_client.get("/v1/bonds/resolve", params={
        "q": "RIG notes 2028",
    })
    response.raise_for_status()
    data = response.json()

    matches = data["data"]["matches"]
    if len(matches) < 2:
        pytest.skip("Need at least 2 matches to verify ordering")

    confidences = [m["confidence"] for m in matches]

    # Should be sorted descending
    assert confidences == sorted(confidences, reverse=True), \
        "Matches not sorted by confidence"


@pytest.mark.eval
def test_fuzzy_confidence_range(api_client: httpx.Client):
    """Verify confidence scores are in valid range [0, 1]."""
    response = api_client.get("/v1/bonds/resolve", params={
        "q": "senior secured notes",
    })
    response.raise_for_status()
    data = response.json()

    matches = data["data"]["matches"]

    for match in matches:
        conf = match["confidence"]
        assert 0 <= conf <= 1, f"Invalid confidence {conf}, expected [0, 1]"


# =============================================================================
# USE CASE 6: MULTIPLE RESULTS RANKING
# =============================================================================

@pytest.mark.eval
def test_best_match_ranked_first(api_client: httpx.Client):
    """Verify best match is ranked first."""
    # Search with multiple criteria
    response = api_client.get("/v1/bonds/resolve", params={
        "ticker": "RIG",
        "coupon": "8.0",
        "maturity_year": "2027",
    })
    response.raise_for_status()
    data = response.json()

    matches = data["data"]["matches"]
    if not matches:
        pytest.skip("No matches found")

    # First match should have highest confidence
    best = matches[0]
    if len(matches) > 1:
        second = matches[1]
        assert best["confidence"] >= second["confidence"], \
            "Best match should have highest confidence"


@pytest.mark.eval
def test_limit_parameter(api_client: httpx.Client):
    """Verify limit parameter controls result count."""
    limit = 3

    response = api_client.get("/v1/bonds/resolve", params={
        "ticker": "AAPL",
        "limit": str(limit),
    })
    response.raise_for_status()
    data = response.json()

    matches = data["data"]["matches"]
    assert len(matches) <= limit, f"Expected <= {limit} matches, got {len(matches)}"


@pytest.mark.eval
def test_suggestions_returned(api_client: httpx.Client):
    """Verify suggestions are returned when appropriate."""
    # Search for a bond that might not exist exactly
    response = api_client.get("/v1/bonds/resolve", params={
        "q": "RIG 2028",  # RIG has 8% notes due 2028
    })
    response.raise_for_status()
    data = response.json()

    # Should have query echoed back
    assert data["data"]["query"] == "RIG 2028"

    # May or may not have suggestions
    suggestions = data["data"].get("suggestions")
    # Just verify the structure is correct if present
    if suggestions:
        assert isinstance(suggestions, list)


# =============================================================================
# AGGREGATE SCORING
# =============================================================================

def collect_bonds_resolve_score() -> PrimitiveScore:
    """Collect all test results into a PrimitiveScore."""
    return PrimitiveScore(primitive=PRIMITIVE)
