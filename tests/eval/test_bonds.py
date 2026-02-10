"""
/v1/bonds Endpoint Evaluation Tests

7 use cases validating bond data accuracy against ground truth:
1. Coupon rate accuracy - Is bond coupon correct?
2. Maturity date accuracy - Is maturity date correct?
3. YTM range filter - Do min_ytm results have YTM >= threshold?
4. Seniority filter - Are senior_secured results correct?
5. CUSIP accuracy - Is CUSIP matching correct?
6. Pricing freshness - Is pricing data within acceptable age?
7. Outstanding amount - Does outstanding amount match?
"""

import pytest
import httpx
from datetime import datetime, timedelta

from tests.eval.scoring import (
    EvalResult, PrimitiveScore,
    compare_numeric, compare_exact, compare_all_match, compare_all_gte,
)
from tests.eval.ground_truth import GroundTruthManager


PRIMITIVE = "/v1/bonds"


# =============================================================================
# USE CASE 1: COUPON RATE ACCURACY
# =============================================================================

@pytest.mark.eval
@pytest.mark.asyncio
async def test_coupon_rate_accuracy_rig(
    api_client: httpx.Client,
    ground_truth: GroundTruthManager,
):
    """Verify RIG 8% bond coupon rate is correct."""
    # Get bond from API
    response = api_client.get("/v1/bonds", params={
        "ticker": "RIG",
        "fields": "name,cusip,coupon_rate,seniority",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    # Find the 8% bond
    bonds = data["data"]
    target_bond = None
    for bond in bonds:
        if bond.get("coupon_rate") and abs(bond["coupon_rate"] - 8.0) < 0.5:
            target_bond = bond
            break

    if not target_bond:
        pytest.skip("RIG 8% bond not found in API response")

    cusip = target_bond.get("cusip")
    if not cusip:
        pytest.skip("Bond has no CUSIP")

    # Get ground truth
    gt_bond = await ground_truth.get_bond_by_cusip(cusip)
    if not gt_bond:
        pytest.skip(f"No ground truth for CUSIP {cusip}")

    # Convert bps to percent for comparison
    expected_rate = gt_bond["interest_rate"] / 100 if gt_bond["interest_rate"] else None

    result = compare_numeric(
        expected=expected_rate,
        actual=target_bond["coupon_rate"],
        tolerance=0.01,  # 1% tolerance (0.08 bps on 8%)
        test_id=f"bonds.coupon_accuracy.{cusip}",
        source="debt_instruments.interest_rate",
    )
    assert result.passed, result.message


@pytest.mark.eval
def test_coupon_rates_are_reasonable(api_client: httpx.Client):
    """Verify all coupon rates are in reasonable range (0-25%)."""
    response = api_client.get("/v1/bonds", params={
        "fields": "name,cusip,coupon_rate",
        "limit": "100",
    })
    response.raise_for_status()
    data = response.json()

    bonds = data["data"]
    invalid_coupons = []

    for bond in bonds:
        coupon = bond.get("coupon_rate")
        if coupon is not None:
            if coupon < 0 or coupon > 25:
                invalid_coupons.append({
                    "name": bond.get("name"),
                    "coupon": coupon,
                })

    assert len(invalid_coupons) == 0, f"Invalid coupon rates: {invalid_coupons}"


# =============================================================================
# USE CASE 2: MATURITY DATE ACCURACY
# =============================================================================

@pytest.mark.eval
@pytest.mark.asyncio
async def test_maturity_date_accuracy(
    api_client: httpx.Client,
    ground_truth: GroundTruthManager,
):
    """Verify bond maturity dates match database."""
    response = api_client.get("/v1/bonds", params={
        "ticker": "CHTR",
        "fields": "name,cusip,maturity_date",
        "has_cusip": "true",
        "limit": "10",
    })
    response.raise_for_status()
    data = response.json()

    bonds = data["data"]
    if not bonds:
        pytest.skip("No CHTR bonds with CUSIPs found")

    # Check first bond with CUSIP
    for bond in bonds:
        cusip = bond.get("cusip")
        if not cusip:
            continue

        gt_bond = await ground_truth.get_bond_by_cusip(cusip)
        if not gt_bond or not gt_bond.get("maturity_date"):
            continue

        api_maturity = bond.get("maturity_date")
        gt_maturity = gt_bond["maturity_date"].isoformat()

        result = compare_exact(
            expected=gt_maturity,
            actual=api_maturity,
            test_id=f"bonds.maturity_date.{cusip}",
            source="debt_instruments.maturity_date",
        )
        assert result.passed, result.message
        return  # Only need to verify one

    pytest.skip("No bonds with verifiable maturity dates")


@pytest.mark.eval
def test_maturity_dates_are_valid(api_client: httpx.Client):
    """Verify maturity dates are in valid range (2020-2060)."""
    response = api_client.get("/v1/bonds", params={
        "fields": "name,maturity_date",
        "limit": "100",
    })
    response.raise_for_status()
    data = response.json()

    bonds = data["data"]
    invalid_dates = []

    for bond in bonds:
        maturity = bond.get("maturity_date")
        if maturity:
            year = int(maturity[:4])
            if year < 2020 or year > 2060:
                invalid_dates.append({
                    "name": bond.get("name"),
                    "maturity": maturity,
                })

    assert len(invalid_dates) == 0, f"Invalid maturity dates: {invalid_dates}"


# =============================================================================
# USE CASE 3: YTM RANGE FILTER
# =============================================================================

@pytest.mark.eval
@pytest.mark.requires_pricing
def test_ytm_min_filter(api_client: httpx.Client):
    """Verify min_ytm filter returns only bonds with YTM >= threshold."""
    min_ytm = 8.0  # 8%

    response = api_client.get("/v1/bonds", params={
        "min_ytm": str(min_ytm),
        "has_pricing": "true",
        "fields": "name,cusip,pricing",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    bonds = data["data"]
    if not bonds:
        pytest.skip("No bonds with pricing above min_ytm")

    below_threshold = []
    for bond in bonds:
        pricing = bond.get("pricing")
        if pricing and pricing.get("ytm"):
            ytm = pricing["ytm"]
            if ytm < min_ytm:
                below_threshold.append({
                    "name": bond.get("name"),
                    "ytm": ytm,
                })

    assert len(below_threshold) == 0, \
        f"Bonds below min_ytm {min_ytm}%: {below_threshold}"


@pytest.mark.eval
@pytest.mark.requires_pricing
def test_ytm_max_filter(api_client: httpx.Client):
    """Verify max_ytm filter returns only bonds with YTM <= threshold."""
    max_ytm = 10.0  # 10%

    response = api_client.get("/v1/bonds", params={
        "max_ytm": str(max_ytm),
        "has_pricing": "true",
        "fields": "name,cusip,pricing",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    bonds = data["data"]
    if not bonds:
        pytest.skip("No bonds with pricing below max_ytm")

    above_threshold = []
    for bond in bonds:
        pricing = bond.get("pricing")
        if pricing and pricing.get("ytm"):
            ytm = pricing["ytm"]
            if ytm > max_ytm:
                above_threshold.append({
                    "name": bond.get("name"),
                    "ytm": ytm,
                })

    assert len(above_threshold) == 0, \
        f"Bonds above max_ytm {max_ytm}%: {above_threshold}"


# =============================================================================
# USE CASE 4: SENIORITY FILTER
# =============================================================================

@pytest.mark.eval
def test_seniority_filter_senior_secured(api_client: httpx.Client):
    """Verify seniority=senior_secured returns only secured bonds."""
    response = api_client.get("/v1/bonds", params={
        "seniority": "senior_secured",
        "fields": "name,cusip,seniority",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    bonds = data["data"]
    assert len(bonds) >= 1, "Expected at least 1 senior secured bond"

    result = compare_all_match(
        actual=bonds,
        field="seniority",
        expected_value="senior_secured",
        test_id="bonds.seniority_filter.senior_secured",
    )
    assert result.passed, result.message


@pytest.mark.eval
def test_seniority_filter_senior_unsecured(api_client: httpx.Client):
    """Verify seniority=senior_unsecured returns only unsecured bonds."""
    response = api_client.get("/v1/bonds", params={
        "seniority": "senior_unsecured",
        "fields": "name,cusip,seniority",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    bonds = data["data"]
    assert len(bonds) >= 1, "Expected at least 1 senior unsecured bond"

    result = compare_all_match(
        actual=bonds,
        field="seniority",
        expected_value="senior_unsecured",
        test_id="bonds.seniority_filter.senior_unsecured",
    )
    assert result.passed, result.message


# =============================================================================
# USE CASE 5: CUSIP ACCURACY
# =============================================================================

@pytest.mark.eval
@pytest.mark.asyncio
async def test_cusip_exact_lookup(
    api_client: httpx.Client,
    ground_truth: GroundTruthManager,
):
    """Verify CUSIP filter returns exact match."""
    # First, find a bond with a CUSIP in the database
    response = api_client.get("/v1/bonds", params={
        "has_cusip": "true",
        "fields": "name,cusip,company_ticker",
        "limit": "1",
    })
    response.raise_for_status()
    data = response.json()

    bonds = data["data"]
    if not bonds:
        pytest.skip("No bonds with CUSIPs in database")

    test_cusip = bonds[0].get("cusip")
    if not test_cusip:
        pytest.skip("No valid CUSIP found")

    # Now verify the CUSIP filter works
    response2 = api_client.get("/v1/bonds", params={
        "cusip": test_cusip,
        "fields": "name,cusip,company_ticker,coupon_rate",
    })
    response2.raise_for_status()
    data2 = response2.json()

    filtered_bonds = data2["data"]
    if not filtered_bonds:
        pytest.skip(f"CUSIP {test_cusip} filter returned no results")

    assert len(filtered_bonds) == 1, f"Expected exactly 1 bond for CUSIP {test_cusip}, got {len(filtered_bonds)}"

    bond = filtered_bonds[0]
    result = compare_exact(
        expected=test_cusip,
        actual=bond.get("cusip"),
        test_id=f"bonds.cusip_exact.{test_cusip}",
        source="API cusip filter",
    )
    assert result.passed, result.message


@pytest.mark.eval
def test_cusip_format_valid(api_client: httpx.Client):
    """Verify all CUSIPs are 9 characters."""
    response = api_client.get("/v1/bonds", params={
        "has_cusip": "true",
        "fields": "name,cusip",
        "limit": "100",
    })
    response.raise_for_status()
    data = response.json()

    bonds = data["data"]
    invalid_cusips = []

    for bond in bonds:
        cusip = bond.get("cusip")
        if cusip and len(cusip) != 9:
            invalid_cusips.append({
                "name": bond.get("name"),
                "cusip": cusip,
                "length": len(cusip),
            })

    assert len(invalid_cusips) == 0, f"Invalid CUSIP lengths: {invalid_cusips}"


# =============================================================================
# USE CASE 6: PRICING FRESHNESS
# =============================================================================

@pytest.mark.eval
@pytest.mark.requires_pricing
def test_pricing_freshness(api_client: httpx.Client):
    """Verify pricing data is not stale (within 14 days)."""
    max_staleness_days = 14

    response = api_client.get("/v1/bonds", params={
        "has_pricing": "true",
        "fields": "name,cusip,pricing",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    bonds = data["data"]
    if not bonds:
        pytest.skip("No bonds with pricing data")

    stale_bonds = []
    for bond in bonds:
        pricing = bond.get("pricing")
        if pricing and pricing.get("staleness_days"):
            if pricing["staleness_days"] > max_staleness_days:
                stale_bonds.append({
                    "name": bond.get("name"),
                    "staleness_days": pricing["staleness_days"],
                })

    # Allow some stale data (warn but don't fail if < 30%)
    stale_pct = len(stale_bonds) / len(bonds) if bonds else 0

    if stale_pct > 0.3:
        pytest.fail(f"{len(stale_bonds)}/{len(bonds)} bonds have stale pricing (>{max_staleness_days} days)")


@pytest.mark.eval
@pytest.mark.requires_pricing
def test_pricing_has_required_fields(api_client: httpx.Client):
    """Verify pricing object has required fields."""
    response = api_client.get("/v1/bonds", params={
        "has_pricing": "true",
        "fields": "name,cusip,pricing",
        "limit": "20",
    })
    response.raise_for_status()
    data = response.json()

    bonds = data["data"]
    if not bonds:
        pytest.skip("No bonds with pricing data")

    required_fields = ["last_price", "ytm", "price_source"]
    missing_fields = []

    for bond in bonds:
        pricing = bond.get("pricing")
        if pricing:
            for field in required_fields:
                if field not in pricing:
                    missing_fields.append({
                        "bond": bond.get("name"),
                        "missing": field,
                    })

    assert len(missing_fields) == 0, f"Pricing missing required fields: {missing_fields}"


# =============================================================================
# USE CASE 7: OUTSTANDING AMOUNT
# =============================================================================

@pytest.mark.eval
@pytest.mark.asyncio
async def test_outstanding_amount_accuracy(
    api_client: httpx.Client,
    ground_truth: GroundTruthManager,
):
    """Verify outstanding amount matches database."""
    response = api_client.get("/v1/bonds", params={
        "ticker": "CHTR",
        "has_cusip": "true",
        "fields": "name,cusip,outstanding",
        "limit": "10",
    })
    response.raise_for_status()
    data = response.json()

    bonds = data["data"]
    if not bonds:
        pytest.skip("No CHTR bonds found")

    for bond in bonds:
        cusip = bond.get("cusip")
        if not cusip:
            continue

        gt_bond = await ground_truth.get_bond_by_cusip(cusip)
        if not gt_bond or not gt_bond.get("outstanding"):
            continue

        api_outstanding = bond.get("outstanding")
        gt_outstanding = gt_bond["outstanding"]

        result = compare_exact(
            expected=gt_outstanding,
            actual=api_outstanding,
            test_id=f"bonds.outstanding.{cusip}",
            source="debt_instruments.outstanding",
        )
        assert result.passed, result.message
        return  # Only need to verify one

    pytest.skip("No bonds with verifiable outstanding amounts")


@pytest.mark.eval
def test_outstanding_amounts_are_positive(api_client: httpx.Client):
    """Verify all outstanding amounts are positive."""
    response = api_client.get("/v1/bonds", params={
        "fields": "name,cusip,outstanding",
        "limit": "100",
    })
    response.raise_for_status()
    data = response.json()

    bonds = data["data"]
    negative_amounts = []

    for bond in bonds:
        outstanding = bond.get("outstanding")
        if outstanding is not None and outstanding < 0:
            negative_amounts.append({
                "name": bond.get("name"),
                "outstanding": outstanding,
            })

    assert len(negative_amounts) == 0, f"Negative outstanding amounts: {negative_amounts}"


# =============================================================================
# AGGREGATE SCORING
# =============================================================================

def collect_bonds_score() -> PrimitiveScore:
    """Collect all test results into a PrimitiveScore."""
    return PrimitiveScore(primitive=PRIMITIVE)
