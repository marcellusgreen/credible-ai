# Ownership Hierarchy Extraction

Corporate ownership hierarchy is extracted from multiple sources.

## From Exhibit 21 (initial extraction)

```bash
python scripts/extract_exhibit21_hierarchy.py --ticker CHTR --save-db
python scripts/extract_exhibit21_hierarchy.py --all --save-db
```

## From Indentures/Credit Agreements (explicit relationships)

```bash
python scripts/fix_ownership_hierarchy.py --ticker CHTR           # Dry run
python scripts/fix_ownership_hierarchy.py --ticker CHTR --save-db # Save
python scripts/fix_ownership_hierarchy.py --all --save-db         # All companies
```

## From Prospectus Supplements + LLM (intermediate ownership)

```bash
# Step 1: Fetch 424B prospectus sections (regex extraction, $0 LLM cost)
python scripts/fetch_prospectus_sections.py --analyze
python scripts/fetch_prospectus_sections.py --ticker CHTR --save
python scripts/fetch_prospectus_sections.py --all --save

# Step 2: Extract intermediate parent-child relationships via Gemini Flash
python scripts/extract_intermediate_ownership.py --analyze
python scripts/extract_intermediate_ownership.py --ticker CHTR --save
python scripts/extract_intermediate_ownership.py --all --save [--batch-size 150]
```

**Results (2026-02-24):** 6,054 prospectus sections across 259 companies. 5,647 parents via LLM, 3,415 via GLEIF, 1,362 via UK Companies House. Total 13,113 entities with known parents. Pipeline uses batch commits (every 25) to avoid Neon timeout.

## Entity States

| `is_root` | `parent_id` | Meaning |
|-----------|-------------|---------|
| `true` | `NULL` | Ultimate parent company |
| `false` | UUID | Has known parent |
| `false` | `NULL` | Orphan (parent unknown from SEC filings) |

## Ownership Data Honesty

We only show parent-child relationships where we have evidence from SEC filings.

**Entity-level `ownership_confidence`:** `root`, `key_entity`, `verified`, `unknown`

**Response-level `ownership_coverage`:** Includes `known_relationships`, `unknown_relationships`, `key_entities`, `coverage_pct`.

**`other_subsidiaries`:** Lists entities with unknown parent relationships separately.

**`meta.confidence`:** "partial" when some relationships unknown, "high" when all known.

## Fix False Ownership

```bash
python scripts/fix_false_ownership.py              # Dry run
python scripts/fix_false_ownership.py --save-db     # Apply
python scripts/fix_false_ownership.py --ticker RIG --save-db
```

## TTM EBITDA Calculation

The metrics service (`app/services/metrics.py`) calculates TTM EBITDA for leverage ratios:

- **10-K**: Use annual figures directly (already TTM)
- **10-Q**: Sum trailing 4 quarters

EBITDA computation: `ebitda` field → `operating_income + depreciation_amortization` → `operating_income` alone (flagged as estimated).

If <4 quarters available, extrapolates: `ttm_ebitda = sum * (4 / quarters_available)` (flagged in metadata).

**Data Quality Tracking** via `source_filings` JSONB: `ebitda_source`, `ebitda_quarters`, `is_annualized`, `ebitda_estimated`, `ttm_quarters`.

**TTM Financial Extraction**: Uses 8 10-Qs (not 10-K + 10-Qs) for clean quarterly data without math. 10-K has annual figures, not Q4 quarterly data.

## Financial Data Quality Control

```bash
python scripts/qc_financials.py --verbose
python scripts/qc_financials.py --ticker AAPL
python scripts/fix_qc_financials.py [--save-db]
```

QC checks: sanity (Revenue > $1T, EBITDA > Revenue, Debt > 10x Assets), source validation, leverage quality, leverage consistency.
