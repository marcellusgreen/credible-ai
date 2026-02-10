# DebtStack Evaluation Framework

Comprehensive eval suite that validates API correctness against source documents, tracks accuracy scores, and detects regressions.

## Overview

The eval framework replaces the demo scenarios with rigorous, ground-truth-based testing. It:

1. **Validates against source documents** - Compares API results to SEC filings and database records
2. **Tracks accuracy scores** - Quantifies correctness per endpoint (target: 95%+)
3. **Detects regressions** - Compares results to baseline, flags changes
4. **Runs after extractions** - Integrates into post-extraction workflow

## Quick Start

```bash
# Run all evals
python scripts/run_evals.py

# Run single primitive
python scripts/run_evals.py --primitive companies

# Update baseline after verified fix
python scripts/run_evals.py --update-baseline

# JSON output for CI
python scripts/run_evals.py --json

# List available primitives
python scripts/run_evals.py --list
```

## Test Coverage

| Primitive | Use Cases | Description |
|-----------|-----------|-------------|
| `/v1/companies` | 8 | Leverage accuracy, debt totals, sorting, filtering |
| `/v1/bonds` | 7 | Coupon rates, maturity dates, pricing, seniority |
| `/v1/bonds/resolve` | 6 | Free-text parsing, CUSIP lookup, fuzzy matching |
| `/v1/financials` | 8 | Revenue, EBITDA, cash, quarterly filtering |
| `/v1/collateral` | 5 | Collateral types, debt linking, priority |
| `/v1/covenants` | 6 | Covenant types, thresholds, metrics |
| `/v1/covenants/compare` | 4 | Multi-company comparison |
| `/v1/entities/traverse` | 7 | Guarantor counts, parent-child links, depth |
| `/v1/documents/search` | 6 | Term presence, relevance, snippets |
| **Workflows** | 8 | End-to-end multi-step scenarios |

**Total: ~65 test cases**

## Ground Truth Strategy

### Tier 1: Database-Verified (Highest confidence)
- **Source**: Data extracted from SEC filings with document links
- **Examples**: Debt instruments with linked indentures, financials from 10-K/10-Q
- **Validation**: Compare API response to direct DB query

### Tier 2: Cross-Table Consistency
- **Source**: Multiple tables that should agree
- **Examples**: Leverage ratio = total_debt / TTM_EBITDA, debt sum vs financials
- **Validation**: Recalculate from component data

### Tier 3: Document Text Verification
- **Source**: Raw SEC filing text in document_sections
- **Examples**: Bond name appears in indenture, covenant threshold in credit agreement
- **Validation**: Full-text search in source document

## File Structure

```
tests/eval/
├── __init__.py                    # Package init
├── conftest.py                    # Fixtures: API client, DB session
├── ground_truth.py                # Ground truth data management
├── scoring.py                     # Accuracy calculation, regression detection
│
├── test_companies.py              # 8 use cases
├── test_bonds.py                  # 7 use cases
├── test_bonds_resolve.py          # 6 use cases
├── test_financials.py             # 8 use cases
├── test_collateral.py             # 5 use cases
├── test_covenants.py              # 6 use cases
├── test_covenants_compare.py      # 4 use cases
├── test_entities_traverse.py      # 7 use cases
├── test_documents_search.py       # 6 use cases
├── test_workflows.py              # 8 end-to-end scenarios
│
├── baseline/                      # Stored baseline results
│   ├── companies_baseline.json
│   └── ...
└── results/                       # Test run results
    └── ...
```

## Sample Use Cases

### `/v1/companies` - Leverage Accuracy

```python
@pytest.mark.eval
async def test_leverage_accuracy_chtr(api_client, ground_truth):
    """Verify CHTR leverage ratio matches database value (within 5%)."""
    # Get from API
    response = api_client.get("/v1/companies", params={
        "ticker": "CHTR",
        "fields": "ticker,net_leverage_ratio",
    })
    api_leverage = response.json()["data"][0]["net_leverage_ratio"]

    # Get ground truth
    gt = await ground_truth.get_company_leverage("CHTR")

    result = compare_numeric(
        expected=gt.value,
        actual=api_leverage,
        tolerance=0.05,  # 5% tolerance
        test_id="companies.leverage_accuracy.CHTR",
        source=gt.source,
    )
    assert result.passed, result.message
```

### `/v1/bonds` - Seniority Filter

```python
@pytest.mark.eval
def test_seniority_filter_senior_secured(api_client):
    """Verify seniority=senior_secured returns only secured bonds."""
    response = api_client.get("/v1/bonds", params={
        "seniority": "senior_secured",
        "fields": "name,cusip,seniority",
        "limit": "50",
    })
    bonds = response.json()["data"]

    result = compare_all_match(
        actual=bonds,
        field="seniority",
        expected_value="senior_secured",
        test_id="bonds.seniority_filter.senior_secured",
    )
    assert result.passed, result.message
```

## Scoring System

### Per-Test Result
```python
@dataclass
class TestResult:
    test_id: str           # "companies.leverage_accuracy.CHTR"
    passed: bool
    expected: Any
    actual: Any
    tolerance: float       # e.g., 0.05 for 5%
    error_pct: float       # Actual deviation
    ground_truth_source: str  # "company_financials.ttm_ebitda"
```

### Aggregate Score
```python
@dataclass
class PrimitiveScore:
    primitive: str         # "/v1/companies"
    tests_passed: int
    tests_total: int
    accuracy_pct: float    # 0-100
```

## Regression Detection

### Baseline Storage
```json
// tests/eval/baseline/companies_baseline.json
{
  "generated_at": "2026-01-31T12:00:00Z",
  "results": {
    "passed": 8,
    "failed": 0,
    "total": 8
  }
}
```

### Rules
1. **Fail->Pass**: Improvement (log, update baseline)
2. **Pass->Fail**: Regression (alert, block deployment)
3. **Value change >10%**: Flag for review even if passing

## CLI Usage

```bash
# Run all evals
python scripts/run_evals.py

# Run single primitive
python scripts/run_evals.py --primitive companies
python scripts/run_evals.py -p bonds

# Update baseline after verified fix
python scripts/run_evals.py --update-baseline
python scripts/run_evals.py --update-baseline --primitive companies

# Report only (from last run)
python scripts/run_evals.py --report-only

# JSON output for CI
python scripts/run_evals.py --json

# Quiet mode
python scripts/run_evals.py --quiet

# List available primitives
python scripts/run_evals.py --list
```

## Report Output

```
=======================================================
DebtStack Evaluation Report
Timestamp: 2026-01-31 12:00:00
=======================================================

OVERALL ACCURACY: 94.7%

  Passed:  54
  Failed:  3
  Skipped: 2
  Total:   59

Duration: 45.23s

FAILURES:
----------------------------------------
  • test_bonds.test_pricing_freshness
    Expected <7 days, got 12 days
  • test_collateral.test_valuation_present
    Expected non-null, got null for 2/5
  • test_documents.test_relevance_ranking
    Top result changed from baseline

STATUS: ⚠ WARNING (80-95%)
```

## Integration

### After Extraction
```bash
# Run extraction, then evals
python scripts/extract_iterative.py --ticker CHTR --save-db && \
python scripts/run_evals.py --primitive companies
```

### CI Pipeline
```yaml
- name: Run Evals
  run: |
    python scripts/run_evals.py --json > eval_results.json
    if [ $(jq '.failed' eval_results.json) -gt 0 ]; then
      exit 1
    fi
```

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `DEBTSTACK_API_KEY` or `TEST_API_KEY` | Yes | API authentication |
| `TEST_API_URL` | No | API URL (defaults to production) |
| `DATABASE_URL` | For ground truth | Database for ground truth queries |

## Adding New Tests

1. Add test function to appropriate `test_*.py` file
2. Use `@pytest.mark.eval` marker
3. Use comparison functions from `scoring.py`
4. Document ground truth source
5. Run and update baseline if passing

Example:
```python
@pytest.mark.eval
def test_new_use_case(api_client):
    """Description of what this validates."""
    response = api_client.get("/v1/endpoint", params={...})
    data = response.json()

    # Validate with appropriate comparison
    result = compare_exact(
        expected="expected_value",
        actual=data["field"],
        test_id="endpoint.new_use_case",
    )
    assert result.passed, result.message
```

## Troubleshooting

### Tests Skipped
- **Missing API key**: Set `DEBTSTACK_API_KEY` env var
- **No data for ticker**: Company may not have that data extracted
- **No ground truth**: Database may not have records

### Tests Failing
- **Tolerance exceeded**: May need to adjust tolerance or fix extraction
- **Wrong filter results**: Check API implementation
- **Missing fields**: Check field selection implementation

### Regressions
- Compare current vs baseline
- Check recent changes
- Verify ground truth is still valid
