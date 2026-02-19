"""
Verifiable Data — Source Provenance Evaluation Tests

10 use cases validating that every number has a verifiable source:
1.  Bond source documents present - Do bonds return linked SEC filings?
2.  Bond source documents structure - Do source docs have required fields?
3.  Bond source documents confidence - Are match_confidence values reasonable?
4.  Bond source documents field gating - Are docs excluded when field not requested?
5.  Financials source filing present - Does source_filing URL appear?
6.  Financials source filing is SEC URL - Is source_filing a valid SEC URL?
7.  Leverage calculation chain present - Does metadata include calculation inputs?
8.  Leverage calculation chain consistency - Does total_debt / ttm_ebitda ≈ leverage_ratio?
9.  Leverage filing URLs present - Are debt_filing_url and ttm_filing_urls populated?
10. Leverage data quality completeness - Are all expected metadata fields present?
"""

import pytest
import httpx

from tests.eval.scoring import (
    EvalResult, PrimitiveScore,
    compare_numeric, compare_exact,
)
from tests.eval.ground_truth import GroundTruthManager


# =============================================================================
# USE CASE 1: BOND SOURCE DOCUMENTS PRESENT
# =============================================================================

@pytest.mark.eval
@pytest.mark.asyncio
async def test_bond_source_documents_present(
    api_client: httpx.Client,
    ground_truth: GroundTruthManager,
):
    """Verify bonds with known document links return source_documents."""
    # CHTR has 96%+ document coverage
    response = api_client.get("/v1/bonds", params={
        "ticker": "CHTR",
        "fields": "name,cusip,source_documents",
        "limit": "20",
    })
    response.raise_for_status()
    data = response.json()

    bonds = data["data"]
    if not bonds:
        pytest.skip("No CHTR bonds found")

    # At least some bonds should have source documents (96%+ coverage)
    bonds_with_docs = [b for b in bonds if b.get("source_documents")]
    assert len(bonds_with_docs) >= 1, \
        f"Expected at least 1 bond with source_documents, got 0 out of {len(bonds)}"

    # Verify against ground truth: check DB has document links for CHTR
    gt = await ground_truth.get_source_document_count("CHTR")
    if gt and gt.value > 0:
        # If DB has linked docs, API should return them
        doc_pct = len(bonds_with_docs) / len(bonds) if bonds else 0
        assert doc_pct >= 0.5, \
            f"Only {doc_pct*100:.0f}% of bonds have source_documents, expected >= 50%"


@pytest.mark.eval
@pytest.mark.asyncio
async def test_bond_source_documents_match_database(
    api_client: httpx.Client,
    ground_truth: GroundTruthManager,
):
    """Verify source_documents count matches database for a specific bond."""
    # Get a bond with CUSIP
    response = api_client.get("/v1/bonds", params={
        "ticker": "CHTR",
        "has_cusip": "true",
        "fields": "name,cusip,source_documents",
        "limit": "10",
    })
    response.raise_for_status()
    bonds = response.json()["data"]

    for bond in bonds:
        cusip = bond.get("cusip")
        if not cusip:
            continue

        api_docs = bond.get("source_documents", [])
        gt = await ground_truth.get_source_documents_for_bond(cusip)

        if gt is None or not gt.value:
            continue

        # Document count should match
        result = compare_exact(
            expected=len(gt.value),
            actual=len(api_docs),
            test_id=f"verifiability.source_docs_count.{cusip}",
            source=gt.source,
        )
        assert result.passed, result.message
        return  # Only need to verify one

    pytest.skip("No bonds with verifiable source documents")


# =============================================================================
# USE CASE 2: BOND SOURCE DOCUMENTS STRUCTURE
# =============================================================================

@pytest.mark.eval
def test_bond_source_documents_have_required_fields(api_client: httpx.Client):
    """Verify each source document has the expected field structure."""
    response = api_client.get("/v1/bonds", params={
        "ticker": "CHTR",
        "fields": "name,cusip,source_documents",
        "limit": "20",
    })
    response.raise_for_status()
    bonds = response.json()["data"]

    required_fields = {
        "doc_type", "section_type", "filing_date",
        "sec_filing_url", "relationship", "match_confidence", "match_method",
    }

    checked = 0
    missing_fields = []

    for bond in bonds:
        for doc in bond.get("source_documents", []):
            checked += 1
            missing = required_fields - set(doc.keys())
            if missing:
                missing_fields.append({
                    "bond": bond.get("name", "unknown"),
                    "missing": list(missing),
                })

    if checked == 0:
        pytest.skip("No source documents found to validate structure")

    assert len(missing_fields) == 0, \
        f"Source documents missing fields: {missing_fields[:5]}"


# =============================================================================
# USE CASE 3: BOND SOURCE DOCUMENTS CONFIDENCE
# =============================================================================

@pytest.mark.eval
def test_bond_source_documents_confidence_range(api_client: httpx.Client):
    """Verify match_confidence values are in valid range [0, 1]."""
    response = api_client.get("/v1/bonds", params={
        "ticker": "CHTR",
        "fields": "name,cusip,source_documents",
        "limit": "20",
    })
    response.raise_for_status()
    bonds = response.json()["data"]

    invalid = []
    checked = 0

    for bond in bonds:
        for doc in bond.get("source_documents", []):
            conf = doc.get("match_confidence")
            if conf is not None:
                checked += 1
                if conf < 0 or conf > 1:
                    invalid.append({
                        "bond": bond.get("name"),
                        "confidence": conf,
                    })

    if checked == 0:
        pytest.skip("No source documents with match_confidence to validate")

    assert len(invalid) == 0, f"Invalid confidence values: {invalid}"


# =============================================================================
# USE CASE 4: BOND SOURCE DOCUMENTS FIELD GATING
# =============================================================================

@pytest.mark.eval
def test_bond_source_documents_excluded_when_not_requested(api_client: httpx.Client):
    """Verify source_documents is NOT present when not in fields parameter."""
    response = api_client.get("/v1/bonds", params={
        "ticker": "CHTR",
        "fields": "name,cusip,pricing",
        "limit": "5",
    })
    response.raise_for_status()
    bonds = response.json()["data"]

    if not bonds:
        pytest.skip("No CHTR bonds found")

    for bond in bonds:
        assert "source_documents" not in bond, \
            f"source_documents should not appear when not requested, but found on {bond.get('name')}"


# =============================================================================
# USE CASE 5: FINANCIALS SOURCE FILING PRESENT
# =============================================================================

@pytest.mark.eval
@pytest.mark.asyncio
async def test_financials_source_filing_present(
    api_client: httpx.Client,
    ground_truth: GroundTruthManager,
):
    """Verify source_filing URL is returned for financials records."""
    response = api_client.get("/v1/financials", params={
        "ticker": "CHTR",
        "fields": "ticker,fiscal_year,fiscal_quarter,filing_type,source_filing",
        "limit": "4",
    })
    response.raise_for_status()
    data = response.json()

    financials = data["data"]
    if not financials:
        pytest.skip("No CHTR financials data")

    # Check ground truth - does DB have source_filing?
    gt = await ground_truth.get_financials_source_filing("CHTR")

    if gt and gt.value:
        # If DB has source_filing, API should return it
        latest = financials[0]
        api_filing = latest.get("source_filing")
        assert api_filing is not None, \
            f"source_filing is None in API but '{gt.value}' in database"

        result = compare_exact(
            expected=gt.value,
            actual=api_filing,
            test_id="verifiability.source_filing.CHTR",
            source=gt.source,
        )
        assert result.passed, result.message
    else:
        # DB doesn't have source_filing - just verify the field exists in response
        latest = financials[0]
        assert "source_filing" in latest, \
            "source_filing field should be present (even if null)"


# =============================================================================
# USE CASE 6: FINANCIALS SOURCE FILING IS SEC URL
# =============================================================================

@pytest.mark.eval
def test_financials_source_filing_is_sec_url(api_client: httpx.Client):
    """Verify source_filing URLs point to SEC EDGAR."""
    response = api_client.get("/v1/financials", params={
        "fields": "ticker,source_filing",
        "limit": "20",
    })
    response.raise_for_status()
    financials = response.json()["data"]

    invalid_urls = []
    checked = 0

    for fin in financials:
        url = fin.get("source_filing")
        if url:
            checked += 1
            if not url.startswith("https://www.sec.gov/"):
                invalid_urls.append({
                    "ticker": fin.get("ticker"),
                    "url": url,
                })

    if checked == 0:
        pytest.skip("No financials with source_filing URLs to validate")

    assert len(invalid_urls) == 0, \
        f"Non-SEC filing URLs found: {invalid_urls[:5]}"


@pytest.mark.eval
def test_financials_source_filing_excluded_when_not_requested(api_client: httpx.Client):
    """Verify source_filing is NOT present when not in fields parameter."""
    response = api_client.get("/v1/financials", params={
        "ticker": "CHTR",
        "fields": "ticker,revenue,filing_type",
        "limit": "2",
    })
    response.raise_for_status()
    financials = response.json()["data"]

    if not financials:
        pytest.skip("No CHTR financials data")

    for fin in financials:
        assert "source_filing" not in fin, \
            "source_filing should not appear when not requested"


# =============================================================================
# USE CASE 7: LEVERAGE CALCULATION CHAIN PRESENT
# =============================================================================

@pytest.mark.eval
def test_leverage_calculation_chain_present(api_client: httpx.Client):
    """Verify leverage metadata includes calculation inputs when include_metadata=true."""
    response = api_client.get("/v1/companies", params={
        "ticker": "CHTR",
        "include_metadata": "true",
    })
    response.raise_for_status()
    data = response.json()

    companies = data["data"]
    assert len(companies) == 1, "Expected 1 company"

    company = companies[0]
    metadata = company.get("_metadata", {})
    ldq = metadata.get("leverage_data_quality")

    assert ldq is not None, "leverage_data_quality should be present in _metadata"

    # New fields from the calculation chain
    assert "total_debt_used" in ldq, "total_debt_used missing from leverage_data_quality"
    assert "ttm_ebitda_used" in ldq, "ttm_ebitda_used missing from leverage_data_quality"
    assert "debt_filing_url" in ldq, "debt_filing_url missing from leverage_data_quality"
    assert "ttm_filing_urls" in ldq, "ttm_filing_urls missing from leverage_data_quality"


# =============================================================================
# USE CASE 8: LEVERAGE CALCULATION CHAIN CONSISTENCY
# =============================================================================

@pytest.mark.eval
def test_leverage_calculation_chain_consistency(api_client: httpx.Client):
    """Verify total_debt_used / ttm_ebitda_used ≈ leverage_ratio."""
    response = api_client.get("/v1/companies", params={
        "ticker": "CHTR",
        "include_metadata": "true",
    })
    response.raise_for_status()
    data = response.json()

    company = data["data"][0]
    api_leverage = company.get("leverage_ratio")
    metadata = company.get("_metadata", {})
    ldq = metadata.get("leverage_data_quality", {})

    total_debt = ldq.get("total_debt_used")
    ttm_ebitda = ldq.get("ttm_ebitda_used")

    if not total_debt or not ttm_ebitda or not api_leverage:
        pytest.skip("Missing calculation chain values for consistency check")

    # Recompute leverage from components
    computed_leverage = total_debt / ttm_ebitda

    result = compare_numeric(
        expected=computed_leverage,
        actual=api_leverage,
        tolerance=0.05,  # 5% tolerance for rounding
        test_id="verifiability.leverage_chain_consistency.CHTR",
        source="total_debt_used / ttm_ebitda_used",
    )
    assert result.passed, \
        f"Leverage mismatch: {total_debt}/{ttm_ebitda} = {computed_leverage:.2f}, API says {api_leverage}"


@pytest.mark.eval
@pytest.mark.asyncio
async def test_leverage_total_debt_matches_metrics(
    api_client: httpx.Client,
    ground_truth: GroundTruthManager,
):
    """Verify total_debt_used in metadata matches company_metrics.total_debt."""
    response = api_client.get("/v1/companies", params={
        "ticker": "CHTR",
        "include_metadata": "true",
    })
    response.raise_for_status()
    data = response.json()

    company = data["data"][0]
    ldq = company.get("_metadata", {}).get("leverage_data_quality", {})
    api_debt_used = ldq.get("total_debt_used")

    if not api_debt_used:
        pytest.skip("total_debt_used not available in metadata")

    gt = await ground_truth.get_company_total_debt("CHTR")
    if not gt:
        pytest.skip("No ground truth total_debt for CHTR")

    result = compare_numeric(
        expected=gt.value,
        actual=api_debt_used,
        tolerance=0.01,  # Should be exact match (same source)
        test_id="verifiability.debt_used_matches_metrics.CHTR",
        source=gt.source,
    )
    assert result.passed, result.message


# =============================================================================
# USE CASE 9: LEVERAGE FILING URLS PRESENT
# =============================================================================

@pytest.mark.eval
@pytest.mark.asyncio
async def test_leverage_filing_urls_present(
    api_client: httpx.Client,
    ground_truth: GroundTruthManager,
):
    """Verify debt_filing_url and ttm_filing_urls are populated when available in DB."""
    response = api_client.get("/v1/companies", params={
        "ticker": "CHTR",
        "include_metadata": "true",
    })
    response.raise_for_status()
    data = response.json()

    company = data["data"][0]
    ldq = company.get("_metadata", {}).get("leverage_data_quality", {})

    # Check against ground truth
    gt = await ground_truth.get_leverage_source_filings("CHTR")
    if not gt:
        pytest.skip("No source_filings in company_metrics for CHTR")

    sf = gt.value  # The source_filings JSONB dict

    # If DB has debt_filing, API should expose it
    if sf.get("debt_filing"):
        api_debt_url = ldq.get("debt_filing_url")
        assert api_debt_url is not None, \
            f"debt_filing_url is None but DB has: {sf['debt_filing']}"
        assert api_debt_url.startswith("https://www.sec.gov/"), \
            f"debt_filing_url should be SEC URL, got: {api_debt_url}"

    # If DB has ttm_filings, API should expose them
    if sf.get("ttm_filings"):
        api_ttm_urls = ldq.get("ttm_filing_urls", [])
        assert len(api_ttm_urls) > 0, \
            f"ttm_filing_urls is empty but DB has {len(sf['ttm_filings'])} URLs"
        for url in api_ttm_urls:
            assert url.startswith("https://www.sec.gov/"), \
                f"ttm_filing_url should be SEC URL, got: {url}"


# =============================================================================
# USE CASE 10: LEVERAGE DATA QUALITY COMPLETENESS
# =============================================================================

@pytest.mark.eval
def test_leverage_data_quality_all_fields_present(api_client: httpx.Client):
    """Verify leverage_data_quality has all expected fields."""
    response = api_client.get("/v1/companies", params={
        "ticker": "CHTR",
        "include_metadata": "true",
    })
    response.raise_for_status()
    data = response.json()

    company = data["data"][0]
    ldq = company.get("_metadata", {}).get("leverage_data_quality", {})

    expected_fields = {
        # Existing fields
        "ebitda_source", "ebitda_quarters", "ebitda_quarters_with_da",
        "is_annualized", "ebitda_estimated", "ttm_quarters", "computed_at",
        # New provenance fields
        "total_debt_used", "ttm_ebitda_used",
        "debt_filing_url", "ttm_filing_urls", "debt_discrepancy_pct",
    }

    present_fields = set(ldq.keys())
    missing = expected_fields - present_fields

    assert len(missing) == 0, \
        f"leverage_data_quality missing fields: {missing}"


@pytest.mark.eval
def test_leverage_data_quality_reasonable_values(api_client: httpx.Client):
    """Verify leverage calculation chain values are reasonable."""
    response = api_client.get("/v1/companies", params={
        "ticker": "CHTR",
        "include_metadata": "true",
    })
    response.raise_for_status()
    data = response.json()

    company = data["data"][0]
    ldq = company.get("_metadata", {}).get("leverage_data_quality", {})

    total_debt = ldq.get("total_debt_used")
    ttm_ebitda = ldq.get("ttm_ebitda_used")
    ebitda_quarters = ldq.get("ebitda_quarters")
    ttm_quarters = ldq.get("ttm_quarters", [])
    debt_discrepancy = ldq.get("debt_discrepancy_pct")

    # total_debt_used should be positive (CHTR has ~$95B debt)
    if total_debt is not None:
        assert total_debt > 0, f"total_debt_used should be positive, got {total_debt}"

    # ttm_ebitda should be positive
    if ttm_ebitda is not None:
        assert ttm_ebitda > 0, f"ttm_ebitda_used should be positive, got {ttm_ebitda}"

    # ebitda_quarters should be 1-4
    if ebitda_quarters is not None:
        assert 1 <= ebitda_quarters <= 4, \
            f"ebitda_quarters should be 1-4, got {ebitda_quarters}"

    # ttm_quarters should be a list
    assert isinstance(ttm_quarters, list), \
        f"ttm_quarters should be a list, got {type(ttm_quarters)}"

    # debt_discrepancy_pct should be 0-200% if present
    if debt_discrepancy is not None:
        assert 0 <= debt_discrepancy <= 200, \
            f"debt_discrepancy_pct should be 0-200%, got {debt_discrepancy}"


# =============================================================================
# AGGREGATE SCORING
# =============================================================================

def collect_verifiability_score() -> PrimitiveScore:
    """Collect all test results into a PrimitiveScore."""
    return PrimitiveScore(primitive="verifiability")
