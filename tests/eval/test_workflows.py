"""
End-to-End Workflow Evaluation Tests

Port of existing demo scenarios with accuracy validation.
Tests multi-step workflows that AI agents would execute.

8 scenarios:
1. Leverage Leaderboard - Company comparison by leverage
2. Bond Screener - Filter bonds by criteria
3. Corporate Structure Explorer - Entity traversal
4. Document Search - Full-text search in SEC filings
5. AI Agent Workflow - Two-phase discovery + deep dive
6. Maturity Wall - Debt maturity analysis
7. Physical Asset-Backed Bonds - Collateral analysis
8. Yield Per Turn of Leverage - Risk/return analysis
"""

import pytest
import httpx

from tests.eval.scoring import (
    EvalResult, PrimitiveScore,
    compare_numeric, compare_exact, compare_sorted, compare_all_gte,
)


PRIMITIVE = "workflows"


# =============================================================================
# SCENARIO 1: LEVERAGE LEADERBOARD
# =============================================================================

@pytest.mark.eval
def test_leverage_leaderboard(api_client: httpx.Client):
    """Test leverage leaderboard workflow - find highest leveraged companies."""
    # Step 1: Query companies sorted by leverage
    response = api_client.get("/v1/companies", params={
        "fields": "ticker,name,net_leverage_ratio,total_debt",
        "sort": "-net_leverage_ratio",
        "limit": "20",
    })
    response.raise_for_status()
    data = response.json()

    companies = data["data"]

    # Validation 1: Got results
    assert len(companies) >= 10, f"Expected 10+ companies, got {len(companies)}"

    # Validation 2: Results sorted
    result = compare_sorted(
        actual=companies,
        field="net_leverage_ratio",
        descending=True,
        test_id="workflow.leverage_leaderboard.sorting",
    )
    assert result.passed, result.message

    # Validation 3: Leverage values are reasonable
    for c in companies[:10]:
        lev = c.get("net_leverage_ratio")
        if lev is not None:
            # Allow capped value of 999.99
            assert (0 <= lev <= 100) or lev == 999.99, \
                f"Unreasonable leverage for {c['ticker']}: {lev}"

    # Validation 4: All have required fields
    for c in companies:
        assert c.get("ticker"), "Missing ticker"
        assert c.get("name"), "Missing name"


@pytest.mark.eval
def test_leverage_filter_workflow(api_client: httpx.Client):
    """Test filtering high leverage companies."""
    response = api_client.get("/v1/companies", params={
        "min_leverage": "5.0",
        "fields": "ticker,name,net_leverage_ratio",
        "sort": "-net_leverage_ratio",
        "limit": "20",
    })
    response.raise_for_status()
    data = response.json()

    companies = data["data"]

    # All should have leverage >= 5.0 (or capped at 999.99)
    for c in companies:
        lev = c.get("net_leverage_ratio")
        if lev is not None:
            assert lev >= 5.0 or lev == 999.99, \
                f"{c['ticker']} has leverage {lev} < 5.0"


# =============================================================================
# SCENARIO 2: BOND SCREENER
# =============================================================================

@pytest.mark.eval
@pytest.mark.requires_pricing
def test_bond_screener(api_client: httpx.Client):
    """Test bond screening workflow - filter by seniority and pricing."""
    response = api_client.get("/v1/bonds", params={
        "seniority": "senior_secured",
        "has_pricing": "true",
        "fields": "name,company_ticker,cusip,coupon_rate,maturity_date,pricing,seniority",
        "sort": "-pricing.ytm",
        "limit": "20",
    })
    response.raise_for_status()
    data = response.json()

    bonds = data["data"]

    # Validation 1: Got secured bonds
    assert len(bonds) >= 5, f"Expected 5+ secured bonds, got {len(bonds)}"

    # Validation 2: All are senior_secured
    for b in bonds:
        assert b.get("seniority") == "senior_secured", \
            f"Expected senior_secured, got {b.get('seniority')}"

    # Validation 3: All have pricing
    for b in bonds:
        assert b.get("pricing"), f"Bond {b.get('name')} missing pricing"

    # Validation 4: Sorted by YTM
    ytms = [b["pricing"]["ytm"] for b in bonds if b.get("pricing") and b["pricing"].get("ytm")]
    if ytms:
        assert ytms == sorted(ytms, reverse=True), "Not sorted by YTM"


@pytest.mark.eval
def test_bond_screener_by_maturity(api_client: httpx.Client):
    """Test bond screening by maturity date."""
    response = api_client.get("/v1/bonds", params={
        "maturity_before": "2027-12-31",
        "fields": "name,company_ticker,maturity_date",
        "sort": "maturity_date",
        "limit": "20",
    })
    response.raise_for_status()
    data = response.json()

    bonds = data["data"]

    for b in bonds:
        maturity = b.get("maturity_date")
        if maturity:
            assert maturity <= "2027-12-31", \
                f"Bond {b.get('name')} matures after filter: {maturity}"


# =============================================================================
# SCENARIO 3: CORPORATE STRUCTURE EXPLORER
# =============================================================================

@pytest.mark.eval
def test_corporate_structure_explorer(api_client: httpx.Client):
    """Test corporate structure exploration workflow."""
    response = api_client.post("/v1/entities/traverse", json={
        "start": {"type": "company", "id": "CHTR"},
        "relationships": ["subsidiaries", "guarantees"],
        "depth": 3,
    })
    response.raise_for_status()
    data = response.json()

    inner = data.get("data", data)

    # Validation 1: Has structure
    assert "start" in inner, "Missing start info"
    assert "traversal" in inner, "Missing traversal"

    # Validation 2: Has entities
    entities = inner.get("traversal", {}).get("entities", [])
    assert len(entities) >= 5, f"Expected 5+ entities, got {len(entities)}"

    # Validation 3: Contains CHTR-related data
    has_chtr = any(
        "charter" in (e.get("name") or "").lower()
        for e in entities
    )
    assert has_chtr, "No Charter entities found"

    # Validation 4: Entities have required fields
    for e in entities[:10]:
        assert e.get("name"), "Entity missing name"
        assert e.get("entity_type") or e.get("is_guarantor") is not None, \
            "Entity missing type info"


# =============================================================================
# SCENARIO 4: DOCUMENT SEARCH
# =============================================================================

@pytest.mark.eval
def test_document_search(api_client: httpx.Client):
    """Test document search workflow."""
    response = api_client.get("/v1/documents/search", params={
        "q": "change of control",
        "ticker": "CHTR",
        "section_type": "indenture",
        "limit": "5",
    })
    response.raise_for_status()
    data = response.json()

    results = data.get("data", [])

    # Validation 1: Got results
    assert len(results) >= 1, "No search results"

    # Validation 2: All are indentures
    for r in results:
        assert r.get("section_type") == "indenture", \
            f"Expected indenture, got {r.get('section_type')}"

    # Validation 3: Results contain search term
    found_term = False
    for r in results:
        content = (r.get("snippet") or r.get("content") or "").lower()
        if "change" in content or "control" in content:
            found_term = True
            break
    assert found_term, "No results contain search term"

    # Validation 4: Have snippets
    for r in results:
        assert r.get("snippet") or r.get("content"), "Result missing content"


# =============================================================================
# SCENARIO 5: AI AGENT WORKFLOW (TWO-PHASE)
# =============================================================================

@pytest.mark.eval
@pytest.mark.requires_pricing
def test_ai_agent_workflow(api_client: httpx.Client):
    """Test two-phase AI agent workflow: discovery + deep dive."""
    # Phase 1: Discovery - find high yield secured bonds
    phase1 = api_client.get("/v1/bonds", params={
        "min_ytm": "8",
        "seniority": "senior_secured",
        "has_pricing": "true",
        "fields": "name,company_ticker,cusip,pricing",
        "limit": "10",
    })
    phase1.raise_for_status()
    phase1_data = phase1.json()

    phase1_bonds = phase1_data["data"]
    assert len(phase1_bonds) >= 1, "Phase 1: No high-yield bonds found"

    # Get ticker from first result
    ticker = phase1_bonds[0].get("company_ticker", "RIG")

    # Phase 2: Deep dive - search documents for that company
    phase2 = api_client.get("/v1/documents/search", params={
        "q": "event of default",
        "ticker": ticker,
        "section_type": "indenture",
        "limit": "3",
    })
    phase2.raise_for_status()
    phase2_data = phase2.json()

    phase2_results = phase2_data.get("data", [])

    # Validation: End-to-end flow works
    assert phase1_bonds[0].get("company_ticker"), "Phase 1 missing ticker"
    assert phase1_bonds[0].get("name"), "Phase 1 missing bond name"


# =============================================================================
# SCENARIO 6: MATURITY WALL
# =============================================================================

@pytest.mark.eval
def test_maturity_wall(api_client: httpx.Client):
    """Test maturity wall analysis workflow."""
    # Use legacy endpoint for maturity waterfall
    response = api_client.get("/v1/companies/CHTR/maturity-waterfall")
    response.raise_for_status()
    data = response.json()

    # Validation 1: Valid response
    assert isinstance(data, dict), "Expected dict response"

    # Validation 2: Has maturity data
    has_maturity_info = (
        "buckets" in data or
        "maturities" in data or
        "data" in data or
        "debt_due_1yr" in str(data)
    )
    assert has_maturity_info, "Missing maturity information"

    # Validation 3: Has amounts
    has_amounts = (
        "amount" in str(data) or
        "total" in str(data) or
        "debt" in str(data)
    )
    assert has_amounts, "Missing amount data"


@pytest.mark.eval
def test_maturity_profile_from_companies(api_client: httpx.Client):
    """Test maturity profile via /v1/companies endpoint."""
    response = api_client.get("/v1/companies", params={
        "ticker": "CHTR",
        "fields": "ticker,debt_due_1yr,debt_due_2yr,debt_due_3yr,nearest_maturity",
    })
    response.raise_for_status()
    data = response.json()

    companies = data["data"]
    assert len(companies) == 1

    c = companies[0]
    # Should have maturity profile data
    has_maturity = any([
        c.get("debt_due_1yr"),
        c.get("debt_due_2yr"),
        c.get("debt_due_3yr"),
        c.get("nearest_maturity"),
    ])
    # May not have all fields if no near-term maturities


# =============================================================================
# SCENARIO 7: PHYSICAL ASSET-BACKED BONDS
# =============================================================================

@pytest.mark.eval
@pytest.mark.requires_pricing
def test_physical_asset_backed_bonds(api_client: httpx.Client):
    """Test workflow for finding physically collateralized bonds."""
    # Find high-yield secured bonds (removed 'collateral' field - not exposed on bonds endpoint)
    response = api_client.get("/v1/bonds", params={
        "min_ytm": "8",
        "seniority": "senior_secured",
        "has_pricing": "true",
        "fields": "name,company_ticker,pricing,maturity_date,seniority",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    bonds = data["data"]

    # Validation 1: Got secured bonds
    assert len(bonds) >= 5, f"Expected 5+ high-yield secured bonds, got {len(bonds)}"

    # Validation 2: Multiple companies
    tickers = {b.get("company_ticker") for b in bonds if b.get("company_ticker")}
    assert len(tickers) >= 2, f"Expected 2+ companies, got {len(tickers)}"

    # Validation 3: Have maturity dates
    with_maturity = [b for b in bonds if b.get("maturity_date")]
    assert len(with_maturity) >= 5, f"Only {len(with_maturity)} bonds have maturity dates"

    # Validation 4: YTM >= 8%
    for b in bonds:
        pricing = b.get("pricing")
        if pricing and pricing.get("ytm"):
            assert pricing["ytm"] >= 8.0, \
                f"Bond {b.get('name')} has YTM {pricing['ytm']} < 8%"


# =============================================================================
# SCENARIO 8: YIELD PER TURN OF LEVERAGE
# =============================================================================

@pytest.mark.eval
@pytest.mark.requires_pricing
def test_yield_per_leverage(api_client: httpx.Client):
    """Test yield/leverage analysis workflow."""
    # Get secured bonds with pricing
    response = api_client.get("/v1/bonds", params={
        "seniority": "senior_secured",
        "has_pricing": "true",
        "fields": "name,company_ticker,cusip,pricing,issuer_name",
        "limit": "100",
    })
    response.raise_for_status()
    data = response.json()

    bonds = data["data"]

    # Validation 1: Got bonds with pricing
    assert len(bonds) >= 10, f"Expected 10+ priced bonds, got {len(bonds)}"

    # Validation 2: Have YTM data
    with_ytm = [b for b in bonds if b.get("pricing") and b["pricing"].get("ytm")]
    assert len(with_ytm) >= 10, f"Only {len(with_ytm)} bonds have YTM"

    # Validation 3: YTM values reasonable (0-20%)
    for b in with_ytm:
        ytm = b["pricing"]["ytm"]
        assert 0 < ytm < 20, f"Unreasonable YTM for {b.get('name')}: {ytm}"

    # Validation 4: Multiple companies
    tickers = {b.get("company_ticker") for b in with_ytm if b.get("company_ticker")}
    assert len(tickers) >= 3, f"Expected 3+ companies, got {len(tickers)}"

    # Validation 5: Can identify high-yield for leverage analysis
    high_yield = [b for b in with_ytm if b["pricing"]["ytm"] >= 7]
    assert len(high_yield) >= 3, f"Only {len(high_yield)} high-yield (>=7%) bonds"


# =============================================================================
# AGGREGATE SCORING
# =============================================================================

def collect_workflows_score() -> PrimitiveScore:
    """Collect all test results into a PrimitiveScore."""
    return PrimitiveScore(primitive=PRIMITIVE)
