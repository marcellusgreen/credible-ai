"""
/v1/financials Endpoint Evaluation Tests

8 use cases validating financial data accuracy against ground truth:
1. Revenue accuracy - Does revenue match company_financials?
2. EBITDA accuracy - Does EBITDA match or recalculate correctly?
3. Net income accuracy - Does net income match?
4. Debt amounts accuracy - Does total_debt match?
5. Cash accuracy - Does cash match company_financials?
6. Quarter filter - Does fiscal_quarter filter work?
7. Ticker filter - Does ticker filter return correct company?
8. Field selection - Do requested fields appear?
"""

import pytest
import httpx

from tests.eval.scoring import (
    EvalResult, PrimitiveScore,
    compare_numeric, compare_exact, compare_all_match,
)
from tests.eval.ground_truth import GroundTruthManager


PRIMITIVE = "/v1/financials"


# =============================================================================
# USE CASE 1: REVENUE ACCURACY
# =============================================================================

@pytest.mark.eval
@pytest.mark.asyncio
async def test_revenue_accuracy_aapl(
    api_client: httpx.Client,
    ground_truth: GroundTruthManager,
):
    """Verify AAPL revenue matches database."""
    response = api_client.get("/v1/financials", params={
        "ticker": "AAPL",
        "fields": "ticker,fiscal_year,fiscal_quarter,revenue",
        "limit": "4",
    })
    response.raise_for_status()
    data = response.json()

    financials = data["data"]
    if not financials:
        pytest.skip("No AAPL financials data")

    latest = financials[0]
    fiscal_year = latest.get("fiscal_year")
    fiscal_quarter = latest.get("fiscal_quarter")

    if not fiscal_year or not fiscal_quarter:
        pytest.skip("Missing fiscal period info")

    gt = await ground_truth.get_quarterly_financials("AAPL", fiscal_year, fiscal_quarter)
    if gt is None:
        pytest.skip(f"No ground truth for AAPL {fiscal_year}Q{fiscal_quarter}")

    api_revenue = latest.get("revenue")
    gt_revenue = gt.value.get("revenue")

    result = compare_numeric(
        expected=gt_revenue,
        actual=api_revenue,
        tolerance=0.01,  # 1% tolerance
        test_id=f"financials.revenue.AAPL.{fiscal_year}Q{fiscal_quarter}",
        source=gt.source,
    )
    assert result.passed, result.message


@pytest.mark.eval
def test_revenue_values_are_positive(api_client: httpx.Client):
    """Verify revenue values are positive."""
    response = api_client.get("/v1/financials", params={
        "fields": "ticker,revenue",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    financials = data["data"]
    negative = []

    for fin in financials:
        revenue = fin.get("revenue")
        if revenue is not None and revenue < 0:
            negative.append({"ticker": fin.get("ticker"), "revenue": revenue})

    assert len(negative) == 0, f"Negative revenues: {negative}"


# =============================================================================
# USE CASE 2: EBITDA ACCURACY
# =============================================================================

@pytest.mark.eval
@pytest.mark.asyncio
async def test_ebitda_accuracy_chtr(
    api_client: httpx.Client,
    ground_truth: GroundTruthManager,
):
    """Verify CHTR EBITDA matches database."""
    response = api_client.get("/v1/financials", params={
        "ticker": "CHTR",
        "fields": "ticker,fiscal_year,fiscal_quarter,ebitda,operating_income,depreciation_amortization",
        "limit": "4",
    })
    response.raise_for_status()
    data = response.json()

    financials = data["data"]
    if not financials:
        pytest.skip("No CHTR financials data")

    latest = financials[0]
    fiscal_year = latest.get("fiscal_year")
    fiscal_quarter = latest.get("fiscal_quarter")

    if not fiscal_year or not fiscal_quarter:
        pytest.skip("Missing fiscal period info")

    gt = await ground_truth.get_quarterly_financials("CHTR", fiscal_year, fiscal_quarter)
    if gt is None:
        pytest.skip(f"No ground truth for CHTR {fiscal_year}Q{fiscal_quarter}")

    api_ebitda = latest.get("ebitda")
    gt_ebitda = gt.value.get("ebitda")

    # EBITDA might be directly stored or calculated
    if api_ebitda and gt_ebitda:
        result = compare_numeric(
            expected=gt_ebitda,
            actual=api_ebitda,
            tolerance=0.05,  # 5% tolerance for calculations
            test_id=f"financials.ebitda.CHTR.{fiscal_year}Q{fiscal_quarter}",
            source=gt.source,
        )
        assert result.passed, result.message


@pytest.mark.eval
def test_ebitda_calculation_consistency(api_client: httpx.Client):
    """Verify EBITDA = Operating Income + D&A when both present."""
    response = api_client.get("/v1/financials", params={
        "fields": "ticker,ebitda,operating_income,depreciation_amortization",
        "limit": "20",
    })
    response.raise_for_status()
    data = response.json()

    financials = data["data"]
    inconsistent = []

    for fin in financials:
        ebitda = fin.get("ebitda")
        op_income = fin.get("operating_income")
        da = fin.get("depreciation_amortization")

        if ebitda and op_income and da:
            calculated = op_income + da
            diff_pct = abs(ebitda - calculated) / abs(calculated) if calculated else 0

            if diff_pct > 0.1:  # 10% tolerance
                inconsistent.append({
                    "ticker": fin.get("ticker"),
                    "ebitda": ebitda,
                    "calculated": calculated,
                    "diff_pct": f"{diff_pct*100:.1f}%",
                })

    # Allow some inconsistencies (different accounting treatments)
    assert len(inconsistent) <= 2, f"EBITDA inconsistencies: {inconsistent}"


# =============================================================================
# USE CASE 3: NET INCOME ACCURACY
# =============================================================================

@pytest.mark.eval
@pytest.mark.asyncio
async def test_net_income_accuracy(
    api_client: httpx.Client,
    ground_truth: GroundTruthManager,
):
    """Verify net income matches database."""
    response = api_client.get("/v1/financials", params={
        "ticker": "MSFT",
        "fields": "ticker,fiscal_year,fiscal_quarter,net_income",
        "limit": "4",
    })
    response.raise_for_status()
    data = response.json()

    financials = data["data"]
    if not financials:
        pytest.skip("No MSFT financials data")

    latest = financials[0]
    fiscal_year = latest.get("fiscal_year")
    fiscal_quarter = latest.get("fiscal_quarter")

    if not fiscal_year or not fiscal_quarter:
        pytest.skip("Missing fiscal period info")

    gt = await ground_truth.get_quarterly_financials("MSFT", fiscal_year, fiscal_quarter)
    if gt is None:
        pytest.skip(f"No ground truth for MSFT {fiscal_year}Q{fiscal_quarter}")

    api_net_income = latest.get("net_income")
    gt_net_income = gt.value.get("net_income")

    if api_net_income and gt_net_income:
        result = compare_numeric(
            expected=gt_net_income,
            actual=api_net_income,
            tolerance=0.01,
            test_id=f"financials.net_income.MSFT.{fiscal_year}Q{fiscal_quarter}",
            source=gt.source,
        )
        assert result.passed, result.message


# =============================================================================
# USE CASE 4: DEBT AMOUNTS ACCURACY
# =============================================================================

@pytest.mark.eval
@pytest.mark.asyncio
async def test_debt_amounts_accuracy(
    api_client: httpx.Client,
    ground_truth: GroundTruthManager,
):
    """Verify total_debt matches database."""
    response = api_client.get("/v1/financials", params={
        "ticker": "CHTR",
        "fields": "ticker,fiscal_year,fiscal_quarter,total_debt",
        "limit": "4",
    })
    response.raise_for_status()
    data = response.json()

    financials = data["data"]
    if not financials:
        pytest.skip("No CHTR financials data")

    latest = financials[0]
    fiscal_year = latest.get("fiscal_year")
    fiscal_quarter = latest.get("fiscal_quarter")

    if not fiscal_year or not fiscal_quarter:
        pytest.skip("Missing fiscal period info")

    gt = await ground_truth.get_quarterly_financials("CHTR", fiscal_year, fiscal_quarter)
    if gt is None:
        pytest.skip(f"No ground truth for CHTR {fiscal_year}Q{fiscal_quarter}")

    api_debt = latest.get("total_debt")
    gt_debt = gt.value.get("total_debt")

    if api_debt and gt_debt:
        result = compare_numeric(
            expected=gt_debt,
            actual=api_debt,
            tolerance=0.05,  # 5% tolerance
            test_id=f"financials.total_debt.CHTR.{fiscal_year}Q{fiscal_quarter}",
            source=gt.source,
        )
        assert result.passed, result.message


# =============================================================================
# USE CASE 5: CASH ACCURACY
# =============================================================================

@pytest.mark.eval
@pytest.mark.asyncio
async def test_cash_accuracy(
    api_client: httpx.Client,
    ground_truth: GroundTruthManager,
):
    """Verify cash matches database."""
    response = api_client.get("/v1/financials", params={
        "ticker": "AAPL",
        "fields": "ticker,fiscal_year,fiscal_quarter,cash",
        "limit": "4",
    })
    response.raise_for_status()
    data = response.json()

    financials = data["data"]
    if not financials:
        pytest.skip("No AAPL financials data")

    latest = financials[0]
    fiscal_year = latest.get("fiscal_year")
    fiscal_quarter = latest.get("fiscal_quarter")

    if not fiscal_year or not fiscal_quarter:
        pytest.skip("Missing fiscal period info")

    gt = await ground_truth.get_quarterly_financials("AAPL", fiscal_year, fiscal_quarter)
    if gt is None:
        pytest.skip(f"No ground truth for AAPL {fiscal_year}Q{fiscal_quarter}")

    api_cash = latest.get("cash")
    gt_cash = gt.value.get("cash_and_equivalents")

    if api_cash and gt_cash:
        result = compare_numeric(
            expected=gt_cash,
            actual=api_cash,
            tolerance=0.05,
            test_id=f"financials.cash.AAPL.{fiscal_year}Q{fiscal_quarter}",
            source=gt.source,
        )
        assert result.passed, result.message


@pytest.mark.eval
def test_cash_values_are_non_negative(api_client: httpx.Client):
    """Verify cash values are non-negative."""
    response = api_client.get("/v1/financials", params={
        "fields": "ticker,cash",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    financials = data["data"]
    negative = []

    for fin in financials:
        cash = fin.get("cash")
        if cash is not None and cash < 0:
            negative.append({"ticker": fin.get("ticker"), "cash": cash})

    assert len(negative) == 0, f"Negative cash values: {negative}"


# =============================================================================
# USE CASE 6: QUARTER FILTER
# =============================================================================

@pytest.mark.eval
def test_fiscal_quarter_filter(api_client: httpx.Client):
    """Verify fiscal_quarter filter returns only matching quarters."""
    response = api_client.get("/v1/financials", params={
        "ticker": "AAPL",
        "fiscal_quarter": "3",
        "fields": "ticker,fiscal_year,fiscal_quarter",
        "limit": "10",
    })
    response.raise_for_status()
    data = response.json()

    financials = data["data"]

    for fin in financials:
        assert fin.get("fiscal_quarter") == 3, \
            f"Expected Q3, got Q{fin.get('fiscal_quarter')}"


@pytest.mark.eval
def test_fiscal_year_filter(api_client: httpx.Client):
    """Verify fiscal_year filter returns only matching years."""
    response = api_client.get("/v1/financials", params={
        "fiscal_year": "2024",
        "fields": "ticker,fiscal_year,fiscal_quarter",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    financials = data["data"]

    for fin in financials:
        assert fin.get("fiscal_year") == 2024, \
            f"Expected 2024, got {fin.get('fiscal_year')}"


# =============================================================================
# USE CASE 7: TICKER FILTER
# =============================================================================

@pytest.mark.eval
def test_ticker_filter_single(api_client: httpx.Client):
    """Verify single ticker filter returns only that company."""
    response = api_client.get("/v1/financials", params={
        "ticker": "GOOGL",
        "fields": "ticker,company_name,revenue",
        "limit": "10",
    })
    response.raise_for_status()
    data = response.json()

    financials = data["data"]
    assert len(financials) >= 1, "Expected at least 1 result"

    result = compare_all_match(
        actual=financials,
        field="ticker",
        expected_value="GOOGL",
        test_id="financials.ticker_filter.GOOGL",
    )
    assert result.passed, result.message


@pytest.mark.eval
def test_ticker_filter_multiple(api_client: httpx.Client):
    """Verify multiple ticker filter."""
    tickers = ["AAPL", "MSFT"]

    response = api_client.get("/v1/financials", params={
        "ticker": ",".join(tickers),
        "fields": "ticker,revenue",
        "limit": "20",
    })
    response.raise_for_status()
    data = response.json()

    financials = data["data"]
    returned_tickers = {f["ticker"] for f in financials}

    # Should only have the requested tickers
    unexpected = returned_tickers - set(tickers)
    assert len(unexpected) == 0, f"Unexpected tickers: {unexpected}"


# =============================================================================
# USE CASE 8: FIELD SELECTION
# =============================================================================

@pytest.mark.eval
def test_field_selection_income_statement(api_client: httpx.Client):
    """Verify income statement field selection."""
    fields = ["ticker", "revenue", "operating_income", "net_income"]

    response = api_client.get("/v1/financials", params={
        "ticker": "AAPL",
        "fields": ",".join(fields),
        "limit": "1",
    })
    response.raise_for_status()
    data = response.json()

    financials = data["data"]
    if not financials:
        pytest.skip("No financials data")

    fin = financials[0]

    # Check all requested fields present
    missing = [f for f in fields if f not in fin]
    assert len(missing) == 0, f"Missing fields: {missing}"


@pytest.mark.eval
def test_field_selection_balance_sheet(api_client: httpx.Client):
    """Verify balance sheet field selection."""
    fields = ["ticker", "total_assets", "total_liabilities", "total_debt", "cash"]

    response = api_client.get("/v1/financials", params={
        "ticker": "MSFT",
        "fields": ",".join(fields),
        "limit": "1",
    })
    response.raise_for_status()
    data = response.json()

    financials = data["data"]
    if not financials:
        pytest.skip("No financials data")

    fin = financials[0]

    # Check all requested fields present
    missing = [f for f in fields if f not in fin]
    assert len(missing) == 0, f"Missing fields: {missing}"


# =============================================================================
# AGGREGATE SCORING
# =============================================================================

def collect_financials_score() -> PrimitiveScore:
    """Collect all test results into a PrimitiveScore."""
    return PrimitiveScore(primitive=PRIMITIVE)
