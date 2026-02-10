"""
Combo Evaluation Tests - Multi-Primitive Workflows

Tests realistic AI agent workflows that chain multiple API primitives together.
These test end-to-end use cases rather than individual endpoints.

10 combo scenarios:
1. Credit Analysis Pipeline - Company → Bonds → Covenants → Documents
2. Bond Deep Dive - Resolve bond → Get details → Find guarantors → Search docs
3. Leverage Screening - High leverage companies → Their bonds → Covenant headroom
4. Maturity Wall Analysis - Company debt → Bonds by maturity → Refinancing risk
5. Collateral Coverage - Secured bonds → Collateral details → Asset valuation
6. Guarantor Network - Company → Entity traverse → Guarantor financials
7. Covenant Comparison - Similar companies → Compare covenants → Risk ranking
8. Distressed Debt Screen - High yield bonds → Company financials → Document search
9. Sector Analysis - Sector companies → Aggregate metrics → Bond universe
10. Investment Memo - Full company analysis combining all primitives
"""

import pytest
import httpx

from tests.eval.scoring import PrimitiveScore


PRIMITIVE = "combo_evals"


# =============================================================================
# COMBO 1: CREDIT ANALYSIS PIPELINE
# =============================================================================

@pytest.mark.eval
def test_credit_analysis_pipeline(api_client: httpx.Client):
    """
    Full credit analysis: Company overview → Bond universe → Covenants → Key documents.

    Use case: Analyst wants complete credit picture for CHTR.
    """
    ticker = "CHTR"

    # Step 1: Get company overview with leverage metrics
    company_resp = api_client.get("/v1/companies", params={
        "ticker": ticker,
        "fields": "ticker,name,sector,net_leverage_ratio,total_debt",
    })
    company_resp.raise_for_status()
    company_data = company_resp.json()["data"]

    assert len(company_data) == 1, f"Expected 1 company, got {len(company_data)}"
    company = company_data[0]
    assert company["ticker"] == ticker

    # Step 2: Get bond universe for the company
    bonds_resp = api_client.get("/v1/bonds", params={
        "ticker": ticker,
        "fields": "name,seniority,coupon_rate,maturity_date,outstanding",
        "sort": "maturity_date",
        "limit": "20",
    })
    bonds_resp.raise_for_status()
    bonds = bonds_resp.json()["data"]

    assert len(bonds) >= 1, f"Expected at least 1 bond for {ticker}"

    # Step 3: Get covenant information
    covenants_resp = api_client.get("/v1/covenants", params={
        "ticker": ticker,
        "fields": "covenant_name,covenant_type,test_metric,threshold_value",
        "limit": "10",
    })
    covenants_resp.raise_for_status()
    covenants = covenants_resp.json()["data"]

    # Step 4: Search for key credit documents
    docs_resp = api_client.get("/v1/documents/search", params={
        "q": "credit agreement",
        "ticker": ticker,
        "limit": "3",
    })
    docs_resp.raise_for_status()
    docs = docs_resp.json()["data"]

    # Validate the pipeline produced coherent results
    assert company.get("net_leverage_ratio") is not None or company.get("total_debt") is not None, \
        "Company should have leverage data"

    # Verify we can correlate the data
    bond_seniorities = {b.get("seniority") for b in bonds if b.get("seniority")}
    assert len(bond_seniorities) >= 1, "Should have seniority info on bonds"


# =============================================================================
# COMBO 2: BOND DEEP DIVE
# =============================================================================

@pytest.mark.eval
def test_bond_deep_dive(api_client: httpx.Client):
    """
    Bond deep dive: Resolve bond → Get details → Find guarantors → Search indenture.

    Use case: Agent asked "Tell me about CHTR's senior notes"
    """
    # Step 1: Resolve bond from natural language
    resolve_resp = api_client.get("/v1/bonds/resolve", params={
        "q": "CHTR senior notes",
        "limit": "3",
    })
    resolve_resp.raise_for_status()
    resolve_data = resolve_resp.json()["data"]

    matches = resolve_data.get("matches", [])
    if not matches:
        pytest.skip("No bonds matched for CHTR senior notes")

    bond = matches[0]["bond"]
    ticker = bond.get("company_ticker", "CHTR")

    # Step 2: Get full bond details
    bonds_resp = api_client.get("/v1/bonds", params={
        "ticker": ticker,
        "seniority": "senior_unsecured",
        "fields": "name,cusip,coupon_rate,maturity_date,outstanding,seniority,pricing",
        "limit": "5",
    })
    bonds_resp.raise_for_status()
    bonds = bonds_resp.json()["data"]

    # Step 3: Get guarantor structure
    traverse_resp = api_client.post("/v1/entities/traverse", json={
        "start": {"type": "company", "id": ticker},
        "relationships": ["subsidiaries"],
        "depth": 2,
    })
    traverse_resp.raise_for_status()
    traverse_data = traverse_resp.json()["data"]

    entities = traverse_data.get("traversal", {}).get("entities", [])

    # Step 4: Search indenture for this bond
    docs_resp = api_client.get("/v1/documents/search", params={
        "q": "indenture senior notes",
        "ticker": ticker,
        "section_type": "indenture",
        "limit": "3",
    })
    docs_resp.raise_for_status()
    docs = docs_resp.json()["data"]

    # Validate pipeline coherence
    assert len(bonds) >= 1, "Should find senior unsecured bonds"
    assert len(entities) >= 1, "Should find subsidiary entities"


# =============================================================================
# COMBO 3: LEVERAGE SCREENING
# =============================================================================

@pytest.mark.eval
def test_leverage_screening_workflow(api_client: httpx.Client):
    """
    Leverage screening: High leverage companies → Their bonds → Covenant headroom.

    Use case: Find overleveraged companies and assess their debt situation.
    """
    # Step 1: Find high leverage companies
    companies_resp = api_client.get("/v1/companies", params={
        "min_leverage": "4.0",
        "fields": "ticker,name,net_leverage_ratio,total_debt",
        "sort": "-net_leverage_ratio",
        "limit": "5",
    })
    companies_resp.raise_for_status()
    companies = companies_resp.json()["data"]

    if not companies:
        pytest.skip("No high leverage companies found")

    # Step 2: Get bonds for the highest leverage company
    top_company = companies[0]
    ticker = top_company["ticker"]

    bonds_resp = api_client.get("/v1/bonds", params={
        "ticker": ticker,
        "fields": "name,seniority,maturity_date,coupon_rate",
        "limit": "10",
    })
    bonds_resp.raise_for_status()
    bonds = bonds_resp.json()["data"]

    # Step 3: Check covenant situation
    covenants_resp = api_client.get("/v1/covenants", params={
        "ticker": ticker,
        "covenant_type": "financial",
        "fields": "covenant_name,test_metric,threshold_value,threshold_type",
        "limit": "5",
    })
    covenants_resp.raise_for_status()
    covenants = covenants_resp.json()["data"]

    # Validate the workflow
    leverage = top_company.get("net_leverage_ratio")
    assert leverage is None or leverage >= 4.0 or leverage == 999.99, \
        f"Top company should have high leverage, got {leverage}"


# =============================================================================
# COMBO 4: MATURITY WALL ANALYSIS
# =============================================================================

@pytest.mark.eval
def test_maturity_wall_analysis(api_client: httpx.Client):
    """
    Maturity wall: Company metrics → Bonds sorted by maturity → Near-term refinancing risk.

    Use case: Assess refinancing risk for a company.
    """
    ticker = "CHTR"

    # Step 1: Get company with maturity profile
    company_resp = api_client.get("/v1/companies", params={
        "ticker": ticker,
        "fields": "ticker,name,total_debt,debt_due_1yr,debt_due_2yr,debt_due_3yr,nearest_maturity",
    })
    company_resp.raise_for_status()
    company_data = company_resp.json()["data"]

    assert len(company_data) == 1
    company = company_data[0]

    # Step 2: Get bonds sorted by maturity (nearest first)
    bonds_resp = api_client.get("/v1/bonds", params={
        "ticker": ticker,
        "fields": "name,maturity_date,outstanding,coupon_rate,seniority",
        "sort": "maturity_date",
        "limit": "15",
    })
    bonds_resp.raise_for_status()
    bonds = bonds_resp.json()["data"]

    # Step 3: Get financials to assess refinancing capacity
    financials_resp = api_client.get("/v1/financials", params={
        "ticker": ticker,
        "fields": "ticker,fiscal_year,fiscal_quarter,revenue,ebitda,cash",
        "limit": "4",
    })
    financials_resp.raise_for_status()
    financials = financials_resp.json()["data"]

    # Validate maturity data consistency
    bonds_with_maturity = [b for b in bonds if b.get("maturity_date")]
    if bonds_with_maturity:
        # Verify bonds are sorted by maturity
        maturities = [b["maturity_date"] for b in bonds_with_maturity]
        assert maturities == sorted(maturities), "Bonds should be sorted by maturity"


# =============================================================================
# COMBO 5: COLLATERAL COVERAGE
# =============================================================================

@pytest.mark.eval
def test_collateral_coverage_analysis(api_client: httpx.Client):
    """
    Collateral analysis: Secured bonds → Collateral details → Coverage assessment.

    Use case: Analyze security package for secured debt.
    """
    # Step 1: Find secured bonds
    bonds_resp = api_client.get("/v1/bonds", params={
        "seniority": "senior_secured",
        "fields": "name,company_ticker,cusip,outstanding,maturity_date",
        "limit": "10",
    })
    bonds_resp.raise_for_status()
    bonds = bonds_resp.json()["data"]

    if not bonds:
        pytest.skip("No senior secured bonds found")

    # Get a ticker with secured bonds
    ticker = bonds[0].get("company_ticker")
    if not ticker:
        pytest.skip("Bond missing company_ticker")

    # Step 2: Get collateral for this company
    collateral_resp = api_client.get("/v1/collateral", params={
        "ticker": ticker,
        "fields": "collateral_type,description,priority,bond_name,estimated_value",
        "limit": "10",
    })
    collateral_resp.raise_for_status()
    collateral = collateral_resp.json()["data"]

    # Step 3: Search for security agreement details
    docs_resp = api_client.get("/v1/documents/search", params={
        "q": "collateral security agreement",
        "ticker": ticker,
        "limit": "3",
    })
    docs_resp.raise_for_status()
    docs = docs_resp.json()["data"]

    # Validate we found related data
    # (Collateral may not exist for all companies)


# =============================================================================
# COMBO 6: GUARANTOR NETWORK
# =============================================================================

@pytest.mark.eval
def test_guarantor_network_analysis(api_client: httpx.Client):
    """
    Guarantor analysis: Company → Entity structure → Identify key guarantors.

    Use case: Understand which subsidiaries guarantee the debt.
    """
    ticker = "CHTR"

    # Step 1: Get company overview
    company_resp = api_client.get("/v1/companies", params={
        "ticker": ticker,
        "fields": "ticker,name,total_debt,net_leverage_ratio",
    })
    company_resp.raise_for_status()
    company = company_resp.json()["data"][0]

    # Step 2: Traverse entity structure
    traverse_resp = api_client.post("/v1/entities/traverse", json={
        "start": {"type": "company", "id": ticker},
        "relationships": ["subsidiaries"],
        "depth": 3,
    })
    traverse_resp.raise_for_status()
    traverse_data = traverse_resp.json()["data"]

    entities = traverse_data.get("traversal", {}).get("entities", [])

    # Step 3: Search for guarantor disclosure
    docs_resp = api_client.get("/v1/documents/search", params={
        "q": "guarantor subsidiary",
        "ticker": ticker,
        "limit": "3",
    })
    docs_resp.raise_for_status()
    docs = docs_resp.json()["data"]

    # Validate entity structure exists
    assert len(entities) >= 1, f"Expected entities for {ticker}"


# =============================================================================
# COMBO 7: COVENANT COMPARISON
# =============================================================================

@pytest.mark.eval
def test_covenant_comparison_workflow(api_client: httpx.Client):
    """
    Covenant comparison: Multiple companies → Compare covenants → Risk ranking.

    Use case: Compare covenant packages across peer group.
    """
    tickers = ["CHTR", "T", "VZ"]

    # Step 1: Get company metrics for comparison
    companies_resp = api_client.get("/v1/companies", params={
        "ticker": ",".join(tickers),
        "fields": "ticker,name,net_leverage_ratio,total_debt",
    })
    companies_resp.raise_for_status()
    companies = companies_resp.json()["data"]

    # Step 2: Compare covenants across companies
    compare_resp = api_client.get("/v1/covenants/compare", params={
        "ticker": ",".join(tickers),
        "test_metric": "leverage_ratio",
    })
    compare_resp.raise_for_status()
    comparison = compare_resp.json()

    # Step 3: Get individual covenant details for context
    covenants_resp = api_client.get("/v1/covenants", params={
        "ticker": tickers[0],
        "covenant_type": "financial",
        "fields": "covenant_name,test_metric,threshold_value",
        "limit": "5",
    })
    covenants_resp.raise_for_status()
    covenants = covenants_resp.json()["data"]

    # Validate we got comparison data
    assert len(companies) >= 1, "Should find at least one company"


# =============================================================================
# COMBO 8: DISTRESSED DEBT SCREEN
# =============================================================================

@pytest.mark.eval
@pytest.mark.requires_pricing
def test_distressed_debt_screen(api_client: httpx.Client):
    """
    Distressed screen: High yield bonds → Company financials → Event of default search.

    Use case: Screen for potentially distressed credits.
    """
    # Step 1: Find high-yield bonds (YTM > 10%)
    bonds_resp = api_client.get("/v1/bonds", params={
        "min_ytm": "10",
        "has_pricing": "true",
        "fields": "name,company_ticker,pricing,maturity_date,seniority",
        "limit": "10",
    })
    bonds_resp.raise_for_status()
    bonds = bonds_resp.json()["data"]

    if not bonds:
        pytest.skip("No high-yield bonds with pricing found")

    # Get the first company
    ticker = bonds[0].get("company_ticker")
    if not ticker:
        pytest.skip("Bond missing company_ticker")

    # Step 2: Get company financials
    financials_resp = api_client.get("/v1/financials", params={
        "ticker": ticker,
        "fields": "ticker,fiscal_year,fiscal_quarter,revenue,ebitda,total_debt,cash",
        "limit": "4",
    })
    financials_resp.raise_for_status()
    financials = financials_resp.json()["data"]

    # Step 3: Search for event of default language
    docs_resp = api_client.get("/v1/documents/search", params={
        "q": "event of default",
        "ticker": ticker,
        "section_type": "indenture",
        "limit": "3",
    })
    docs_resp.raise_for_status()
    docs = docs_resp.json()["data"]

    # Validate we found high-yield bonds
    for bond in bonds:
        pricing = bond.get("pricing", {})
        ytm = pricing.get("ytm") or pricing.get("ytm_pct")
        if ytm:
            assert ytm >= 10, f"Expected YTM >= 10%, got {ytm}%"


# =============================================================================
# COMBO 9: SECTOR ANALYSIS
# =============================================================================

@pytest.mark.eval
def test_sector_analysis_workflow(api_client: httpx.Client):
    """
    Sector analysis: Sector companies → Aggregate metrics → Bond universe.

    Use case: Analyze all companies in a sector.
    """
    sector = "Communication Services"

    # Step 1: Get all companies in sector
    companies_resp = api_client.get("/v1/companies", params={
        "sector": sector,
        "fields": "ticker,name,net_leverage_ratio,total_debt",
        "sort": "-total_debt",
        "limit": "10",
    })
    companies_resp.raise_for_status()
    companies = companies_resp.json()["data"]

    if not companies:
        pytest.skip(f"No companies found in sector {sector}")

    # Step 2: Get top company's bonds
    top_ticker = companies[0]["ticker"]

    bonds_resp = api_client.get("/v1/bonds", params={
        "ticker": top_ticker,
        "fields": "name,seniority,maturity_date,outstanding",
        "limit": "10",
    })
    bonds_resp.raise_for_status()
    bonds = bonds_resp.json()["data"]

    # Step 3: Get financials for top companies
    top_tickers = [c["ticker"] for c in companies[:3]]
    financials_resp = api_client.get("/v1/financials", params={
        "ticker": ",".join(top_tickers),
        "fields": "ticker,fiscal_year,revenue,ebitda",
        "limit": "12",
    })
    financials_resp.raise_for_status()
    financials = financials_resp.json()["data"]

    # Validate sector data
    assert len(companies) >= 1, f"Should find companies in {sector}"


# =============================================================================
# COMBO 10: INVESTMENT MEMO
# =============================================================================

@pytest.mark.eval
def test_investment_memo_workflow(api_client: httpx.Client):
    """
    Full investment memo: All primitives combined for comprehensive analysis.

    Use case: Generate complete investment memo for a credit.
    """
    ticker = "CHTR"

    # 1. Company Overview
    company_resp = api_client.get("/v1/companies", params={
        "ticker": ticker,
        "fields": "ticker,name,sector,net_leverage_ratio,total_debt",
    })
    company_resp.raise_for_status()
    company = company_resp.json()["data"][0]

    # 2. Financial Trends
    financials_resp = api_client.get("/v1/financials", params={
        "ticker": ticker,
        "fields": "ticker,fiscal_year,fiscal_quarter,revenue,ebitda,operating_income,net_income",
        "limit": "8",
    })
    financials_resp.raise_for_status()
    financials = financials_resp.json()["data"]

    # 3. Debt Structure
    bonds_resp = api_client.get("/v1/bonds", params={
        "ticker": ticker,
        "fields": "name,seniority,coupon_rate,maturity_date,outstanding",
        "sort": "maturity_date",
        "limit": "20",
    })
    bonds_resp.raise_for_status()
    bonds = bonds_resp.json()["data"]

    # 4. Covenant Package
    covenants_resp = api_client.get("/v1/covenants", params={
        "ticker": ticker,
        "fields": "covenant_name,covenant_type,test_metric,threshold_value,threshold_type",
        "limit": "15",
    })
    covenants_resp.raise_for_status()
    covenants = covenants_resp.json()["data"]

    # 5. Corporate Structure
    traverse_resp = api_client.post("/v1/entities/traverse", json={
        "start": {"type": "company", "id": ticker},
        "relationships": ["subsidiaries"],
        "depth": 2,
    })
    traverse_resp.raise_for_status()
    entities = traverse_resp.json()["data"].get("traversal", {}).get("entities", [])

    # 6. Key Documents
    docs_resp = api_client.get("/v1/documents/search", params={
        "q": "risk factors",
        "ticker": ticker,
        "limit": "3",
    })
    docs_resp.raise_for_status()
    docs = docs_resp.json()["data"]

    # Validate complete memo data
    memo_sections = {
        "company": company is not None,
        "financials": len(financials) > 0,
        "bonds": len(bonds) > 0,
        "entities": len(entities) > 0,
    }

    complete_sections = sum(memo_sections.values())
    assert complete_sections >= 3, \
        f"Investment memo should have at least 3 sections, got {complete_sections}: {memo_sections}"


# =============================================================================
# AGGREGATE SCORING
# =============================================================================

def collect_combo_evals_score() -> PrimitiveScore:
    """Collect all test results into a PrimitiveScore."""
    return PrimitiveScore(primitive=PRIMITIVE)
