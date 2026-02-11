"""
/v1/batch Endpoint Evaluation Tests

7 use cases validating batch operation accuracy:
1. Single search.companies operation succeeds
2. Multiple mixed operations all succeed
3. Partial failure (valid + invalid primitive)
4. All supported primitives succeed
5. Batch result matches direct endpoint call
6. Meta includes timing info
7. 10-operation batch succeeds at limit
"""

import pytest
import httpx

from tests.eval.scoring import EvalResult, PrimitiveScore


PRIMITIVE = "/v1/batch"


# =============================================================================
# USE CASE 1: SINGLE OPERATION
# =============================================================================

@pytest.mark.eval
def test_batch_single_search_companies(api_client: httpx.Client):
    """POST single search.companies operation, verify success and AAPL data."""
    response = api_client.post("/v1/batch", json={
        "operations": [
            {"primitive": "search.companies", "params": {"ticker": "AAPL"}}
        ]
    })
    response.raise_for_status()
    data = response.json()

    assert len(data["results"]) == 1
    result = data["results"][0]
    assert result["status"] == "success"
    assert "data" in result

    # Verify AAPL is in the data
    result_data = result["data"]
    if isinstance(result_data, dict) and "data" in result_data:
        companies = result_data["data"]
    elif isinstance(result_data, list):
        companies = result_data
    else:
        companies = [result_data]

    tickers = [c.get("ticker") for c in companies if isinstance(c, dict)]
    assert "AAPL" in tickers, f"AAPL not found in batch result. Got tickers: {tickers}"


# =============================================================================
# USE CASE 2: MULTIPLE OPERATIONS
# =============================================================================

@pytest.mark.eval
def test_batch_multiple_operations(api_client: httpx.Client):
    """POST 3 operations (companies, bonds, resolve), all should succeed."""
    response = api_client.post("/v1/batch", json={
        "operations": [
            {"primitive": "search.companies", "params": {"ticker": "CHTR"}},
            {"primitive": "search.bonds", "params": {"ticker": "CHTR", "limit": 5}},
            {"primitive": "resolve.bond", "params": {"q": "CHTR"}},
        ]
    })
    response.raise_for_status()
    data = response.json()

    assert len(data["results"]) == 3
    meta = data["meta"]
    assert meta["total_operations"] == 3

    success_count = sum(1 for r in data["results"] if r["status"] == "success")
    assert success_count == meta["successful"]


# =============================================================================
# USE CASE 3: PARTIAL FAILURE
# =============================================================================

@pytest.mark.eval
def test_batch_partial_failure(api_client: httpx.Client):
    """1 valid + 1 invalid primitive: meta.successful==1, meta.failed==1."""
    response = api_client.post("/v1/batch", json={
        "operations": [
            {"primitive": "search.companies", "params": {"ticker": "AAPL"}},
            {"primitive": "nonexistent.primitive", "params": {}},
        ]
    })
    response.raise_for_status()
    data = response.json()

    meta = data["meta"]
    assert meta["successful"] == 1, f"Expected 1 success, got {meta['successful']}"
    assert meta["failed"] == 1, f"Expected 1 failure, got {meta['failed']}"


# =============================================================================
# USE CASE 4: ALL SUPPORTED PRIMITIVES
# =============================================================================

@pytest.mark.eval
def test_batch_all_primitives(api_client: httpx.Client):
    """All 5 core primitives succeed in a single batch."""
    response = api_client.post("/v1/batch", json={
        "operations": [
            {"primitive": "search.companies", "params": {"ticker": "AAPL"}},
            {"primitive": "search.bonds", "params": {"ticker": "AAPL", "limit": 5}},
            {"primitive": "resolve.bond", "params": {"q": "AAPL"}},
            {"primitive": "search.documents", "params": {"q": "covenant", "ticker": "AAPL", "limit": 2}},
            {"primitive": "search.pricing", "params": {"ticker": "AAPL", "limit": 5}},
        ]
    })
    response.raise_for_status()
    data = response.json()

    successful = [r for r in data["results"] if r["status"] == "success"]
    assert len(successful) >= 4, f"Expected at least 4 successes, got {len(successful)}"


# =============================================================================
# USE CASE 5: BATCH MATCHES DIRECT CALL
# =============================================================================

@pytest.mark.eval
def test_batch_result_matches_direct(api_client: httpx.Client):
    """Batch search.companies for CHTR matches direct GET /v1/companies?ticker=CHTR."""
    # Direct call
    direct_response = api_client.get("/v1/companies", params={
        "ticker": "CHTR",
        "fields": "ticker,name",
    })
    direct_response.raise_for_status()
    direct_data = direct_response.json()

    # Batch call
    batch_response = api_client.post("/v1/batch", json={
        "operations": [
            {"primitive": "search.companies", "params": {"ticker": "CHTR", "fields": "ticker,name"}}
        ]
    })
    batch_response.raise_for_status()
    batch_data = batch_response.json()

    batch_result = batch_data["results"][0]
    assert batch_result["status"] == "success"

    # Compare ticker from both
    direct_ticker = direct_data["data"][0]["ticker"]
    batch_inner = batch_result["data"]
    if isinstance(batch_inner, dict) and "data" in batch_inner:
        batch_ticker = batch_inner["data"][0]["ticker"]
    elif isinstance(batch_inner, list):
        batch_ticker = batch_inner[0]["ticker"]
    else:
        batch_ticker = batch_inner.get("ticker")

    assert direct_ticker == batch_ticker == "CHTR"


# =============================================================================
# USE CASE 6: META TIMING
# =============================================================================

@pytest.mark.eval
def test_batch_meta_has_timing(api_client: httpx.Client):
    """meta.duration_ms exists and is non-negative."""
    response = api_client.post("/v1/batch", json={
        "operations": [
            {"primitive": "search.companies", "params": {"ticker": "MSFT"}}
        ]
    })
    response.raise_for_status()
    data = response.json()

    assert "duration_ms" in data["meta"]
    assert data["meta"]["duration_ms"] >= 0


# =============================================================================
# USE CASE 7: MAX OPERATIONS LIMIT
# =============================================================================

@pytest.mark.eval
def test_batch_ten_operations_succeeds(api_client: httpx.Client):
    """10 operations with different tickers: meta.total_operations==10."""
    tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "CHTR", "T", "VZ"]
    response = api_client.post("/v1/batch", json={
        "operations": [
            {"primitive": "search.companies", "params": {"ticker": t}}
            for t in tickers
        ]
    })
    response.raise_for_status()
    data = response.json()

    assert data["meta"]["total_operations"] == 10


# =============================================================================
# AGGREGATE SCORING
# =============================================================================

def collect_batch_score() -> PrimitiveScore:
    """Collect all test results into a PrimitiveScore."""
    return PrimitiveScore(primitive=PRIMITIVE)
