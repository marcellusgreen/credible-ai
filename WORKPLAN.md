# DebtStack Work Plan

Last Updated: 2026-01-31

## Current Status

**Database**: 201 companies | 5,374 entities | 2,485 debt instruments | 591 with CUSIP/ISIN | 30 priced bonds | 7,700+ document sections (4,877 indentures, 2,889 credit agreements) | 600+ financials | 4,881 guarantees | 230 collateral records | 1,181 covenants
**Deployment**: Live at `https://credible-ai-production.up.railway.app`
**Infrastructure**: Railway + Neon PostgreSQL + Upstash Redis (complete)
**Leverage Coverage**: 155/189 companies (82%) - good coverage across all sectors
**Guarantee Coverage**: ~1,500/2,485 debt instruments (~60%) - up from 34.7% on 2026-01-21
**Collateral Coverage**: 230/230 senior_secured instruments (100%) with collateral type identified
**Document Coverage**: 100% (2,560/2,557 linkable instruments)
**Ownership Coverage**: 199/201 companies have identified root entity; 862 explicit parent-child relationships extracted
**Covenant Coverage**: 1,181 covenants across 201 companies (100%), 92.5% linked to specific instruments
**Data Quality**: QC audit passing - 0 critical, 0 errors, 4 warnings (2026-01-26)

---

## What's Next

### Immediate Priority: Stripe Billing Integration
**Why**: Auth system is complete but billing is not connected. Users can sign up but can't upgrade to Pro tier.

**Steps:**
1. Create Stripe products (Free, Pro tiers)
2. Add webhook handler for subscription events
3. Implement checkout flow for tier upgrades
4. Gate pricing endpoints behind Pro tier

### Secondary Priorities (in order):
1. **Finnhub Pricing Expansion** - Expand from 30 to 200+ bonds with pricing data
2. **SDK Publication** - Publish `debtstack-ai` to PyPI for easy Python integration
3. **Mintlify Docs Deployment** - Deploy docs to `docs.debtstack.ai`
4. **Scale Error Investigation** - Verify INTU/META/etc. scale issues against source SEC filings (don't auto-fix)

## Recent Completed Work

### January 2026
- [x] **Structured Covenant Extraction & API** (2026-01-31) - Implemented full covenant extraction pipeline:
  - Created `app/services/covenant_extraction.py` (three-layer architecture: pure functions → prompt building → DB operations)
  - Created `alembic/versions/020_add_covenants_table.py` migration
  - Created `alembic/versions/021_expand_threshold_value_precision.py` (fixed overflow for large dollar thresholds)
  - Added `Covenant` model to `app/models/schema.py`
  - Added `GET /v1/covenants` endpoint (search/filter structured covenant data)
  - Added `GET /v1/covenants/compare` endpoint (compare covenants across companies)
  - Extracted 1,181 covenants across all 201 companies (100% coverage)
  - Created `scripts/link_covenants_to_instruments.py` to backfill instrument linkage (92.5% linked)
  - Created `scripts/backfill_covenant_source_docs.py` to backfill source document links (100%)
  - Updated Mintlify docs (`docs/api-reference/covenants/search.mdx`, `compare.mdx`)
  - Updated `docs/api/PRIMITIVES_API_SPEC.md` with Primitives 9 & 10
- [x] **Service Module Refactoring** (2026-01-28) - Moved extraction business logic from scripts to services:
  - Created `app/services/hierarchy_extraction.py` (Exhibit 21 parsing, ownership hierarchy)
  - Created `app/services/guarantee_extraction.py` (guarantee relationships)
  - Created `app/services/collateral_extraction.py` (secured debt collateral)
  - Created `app/services/qc.py` (quality control checks)
  - Created `app/services/metrics.py` (credit metrics computation)
  - Refactored `scripts/extract_iterative.py`: 1681 → 931 lines (-750 lines)
  - Refactored `scripts/recompute_metrics.py` to thin CLI wrapper
  - Added `source_filings` JSONB to `company_metrics` for TTM provenance tracking (migration 018)
  - Added `source_filing_url` field to financial extraction
- [x] **Idempotent Extraction Pipeline** (2026-01-26) - Made `extract_iterative.py` safe to re-run:
  - Added `check_existing_data()` to detect what data already exists
  - Added `merge_extraction_to_db()` to preserve existing data while adding new
  - Added `update_extraction_status()` to track step outcomes (success/no_data/error)
  - Added `extraction_status` JSONB column to `company_cache` (migration 017)
  - Integrated full Exhibit 21 parsing from `extract_exhibit21_hierarchy.py`
  - **Fixed div-based Exhibit 21 parsing** - META and other Wdesk-generated filings now parse correctly (35 subsidiaries extracted for META)
  - Skip logic: core extraction if entity_count > 20 AND debt_count > 0
  - Skip logic: document sections if count > 5
  - Skip logic: financials tracks `latest_quarter` (e.g., "2025Q3") and re-extracts when ~60 days past next quarter end
  - Skip logic: hierarchy/guarantees/collateral skip if status='no_data' (source unavailable)
  - Added `--force` flag to override skip conditions
  - Added `--all` batch mode with `--resume` support
- [x] Migrated to Neon PostgreSQL + Upstash Redis
- [x] Deployed to Railway
- [x] Built Primitives API (5 of 6 endpoints)
- [x] Removed GraphQL (REST primitives cover all use cases)
- [x] Simplified `primitives.py` codebase
- [x] Guarantee extraction pipeline (Exhibit 22 + indenture parsing)
- [x] Collateral table and extraction (real estate, equipment, receivables, etc.)
- [x] **Document coverage to 100%** - All debt instruments linked to governing documents

### Primitives API Status (10 endpoints)
| Endpoint | Status | Notes |
|----------|--------|-------|
| `GET /v1/companies` | ✅ Done | Field selection, filtering, sorting, `?include_metadata=true` |
| `GET /v1/bonds` | ✅ Done | Pricing joins, guarantor counts, collateral array |
| `GET /v1/bonds/resolve` | ✅ Done | CUSIP/ISIN/fuzzy matching |
| `POST /v1/entities/traverse` | ✅ Done | Graph traversal for guarantors, structure |
| `GET /v1/pricing` | ⚠️ Deprecated | Use `GET /v1/bonds?has_pricing=true` instead (removal: 2026-06-01) |
| `GET /v1/documents/search` | ✅ Done | Full-text search across SEC filings |
| `POST /v1/batch` | ✅ Done | Batch operations (up to 10 parallel) |
| `GET /v1/companies/{ticker}/changes` | ✅ Done | Diff/changelog against historical snapshots |
| `GET /v1/covenants` | ✅ Done | Search structured covenant data (1,181 covenants) |
| `GET /v1/covenants/compare` | ✅ Done | Compare covenants across multiple companies |

---

## Active Work: Extraction → Primitives Data Gaps

### Problem
The Primitives API exposes fields that the extraction pipeline doesn't fully populate yet.

### Gap Analysis (as of 2026-01-16)

#### Priority 1: Compute Missing Metrics (No extraction changes needed)
**Status**: ✅ COMPLETE (2026-01-16)
**Effort**: Small - just add calculations to `extraction.py`

These fields are now being computed:

| Field | Status | Notes |
|-------|--------|-------|
| `debt_due_1yr` | ✅ Done | Sum of debt maturing in 0-12 months |
| `debt_due_2yr` | ✅ Done | Sum of debt maturing in 12-24 months |
| `debt_due_3yr` | ✅ Done | Sum of debt maturing in 24-36 months |
| `weighted_avg_maturity` | ✅ Done | Weighted average maturity in years |
| `has_near_term_maturity` | ✅ Done | True if debt due in next 24 months |
| `industry` | ✅ Done | Copied from `Company.industry` |

**Changes made**:
- Modified `app/services/extraction.py` lines 1416-1445 to compute maturity profile
- Added `scripts/recompute_metrics.py` to backfill existing data
- Ran recompute for all 178 companies

---

#### Priority 2: Leverage Ratios (Requires Financials)
**Status**: ✅ PARTIALLY COMPLETE (2026-01-17)
**Effort**: Medium

| Field | Status | Notes |
|-------|--------|-------|
| `leverage_ratio` | ✅ Done | total_debt / EBITDA (annualized) |
| `net_leverage_ratio` | ✅ Done | (total_debt - cash) / EBITDA |
| `interest_coverage` | ✅ Done | EBITDA / interest_expense |
| `secured_leverage` | ✅ Done | secured_debt / EBITDA |
| `net_debt` | ✅ Done | total_debt - cash |
| `is_leveraged_loan` | ✅ Done | True if leverage > 4x |

**Completed**:
- ✅ Financials extraction tested and working (`scripts/extract_financials.py`)
- ✅ Extracted financials for 12 companies (see results below)
- ✅ Updated `scripts/recompute_metrics.py` to calculate leverage ratios from financials
- ✅ Added sanity checks (skip leverage >100x to handle bad data)
- ✅ Ran `recompute_metrics.py` - updated all 178 companies

**Results** (10 companies with valid leverage ratios):
| Ticker | Leverage | Int Coverage | Notes |
|--------|----------|--------------|-------|
| AAL | 0.4x | 1.4x | |
| CCL | 0.4x | 9.4x | |
| CHTR | 4.5x | 4.4x | LEV>4x flag |
| CZR | 3.4x | 1.5x | Casino - high leverage typical |
| DAL | 1.9x | 13.4x | |
| DVN | 0.4x | 16.4x | Oil & Gas |
| HCA | 0.6x | 5.9x | |
| LUMN | 6.8x | 1.8x | LEV>4x flag, stressed telecom |
| OXY | 1.2x | 11.3x | Oil & Gas |
| SPG | 5.7x | 4.7x | LEV>4x flag, REIT |

**Companies with bad extraction data** (leverage skipped):
| Ticker | Issue | Notes |
|--------|-------|-------|
| GM | Scale error | Revenue shows millions instead of billions |
| MSFT | Corrupted EBITDA | Two numbers concatenated together |
| DISH | Scale error | All values off by ~1000x |
| RIG | Scale error | Revenue/EBITDA way too low |

**Known Issue**: Gemini extraction has inconsistent scale handling for some companies. Anthropic API credits depleted so Claude fallback unavailable.

**Remaining Work**:
- Fix Gemini extraction quality issues (or wait for API credits)
- Extract financials for more companies as needed

---

#### Priority 3: Credit Ratings
**Status**: ⏸️ SKIPPED (2026-01-17)
**Reason**: Low ROI - companies don't disclose specific ratings in SEC filings. Would require paid API (S&P Capital IQ, Bloomberg) or manual entry. Skip for MVP.

---

#### Priority 4: Document Search Primitive (New Feature)
**Status**: ✅ COMPLETE
**Effort**: Large - new feature

The 6th primitive `GET /v1/documents/search` enables full-text search across SEC filing sections:
- "Find all mentions of 'subordinated' in debt footnotes"
- "Search for 'covenant' across recent 10-Ks"
- "Find companies with credit agreement amendments"

**Implementation Steps**:
| Step | Status | Description |
|------|--------|-------------|
| 1. Migration | ✅ Done | `009_add_document_sections.py` - table + GIN index + trigger |
| 2. SQLAlchemy Model | ✅ Done | Added `DocumentSection` to `schema.py` |
| 3. Section Extraction | ✅ Done | `app/services/section_extraction.py` |
| 4. API Endpoint | ✅ Done | `GET /v1/documents/search` in `primitives.py` |
| 5. ETL Integration | ✅ Done | Hooked into `scripts/extract_iterative.py` |
| 6. Backfill Script | ✅ Done | `scripts/backfill_document_sections.py` |
| 7. Documentation | ✅ Done | Updated CLAUDE.md, PRIMITIVES_API_SPEC.md |

**Section Types** (7 types):
- `exhibit_21` - Subsidiary list from 10-K Exhibit 21
- `debt_footnote` - Long-term debt details from Notes
- `mda_liquidity` - Liquidity and Capital Resources from MD&A
- `credit_agreement` - Credit facility terms from 8-K Exhibit 10 (full documents)
- `indenture` - Bond indentures from 8-K Exhibit 4 (full documents)
- `guarantor_list` - Guarantor subsidiaries from Notes
- `covenants` - Financial covenants from Notes/Exhibits

**Section Statistics** (as of 2026-01-18):
| Section Type | Count | Avg Size |
|--------------|-------|----------|
| credit_agreement | 1,720 | ~100K chars |
| mda_liquidity | 1,098 | - |
| covenants | 815 | - |
| indenture | 796 | ~100K chars |
| debt_footnote | 552 | - |
| guarantor_list | 247 | - |
| exhibit_21 | 228 | - |
| **Total** | **5,456** | - |

**Database**: `document_sections` table with PostgreSQL full-text search (TSVECTOR + GIN index)

---

## Active Work: Agent-Friendly Enhancements

Three enhancements to make DebtStack more attractive for AI agent consumption.

### Enhancement 1: Confidence Scores + Metadata
**Status**: ✅ COMPLETE (2026-01-18)
**Effort**: Large (3-5 days)
**Priority**: HIGH (implement first)

Adds extraction metadata to API responses via `?include_metadata=true` parameter.

**What was implemented:**
- New `extraction_metadata` table storing per-company quality metrics
- `?include_metadata=true` parameter on `/v1/companies` endpoint
- Returns `_metadata` object with: qa_score, extraction_method, timestamps, warnings
- Backfilled metadata for all 177 existing companies

**Example Response:**
```json
{
  "ticker": "AAPL",
  "name": "Apple Inc.",
  "_metadata": {
    "qa_score": 0.95,
    "extraction_method": "gemini",
    "data_version": 1,
    "structure_extracted_at": "2026-01-15T10:30:00Z",
    "debt_extracted_at": "2026-01-15T10:30:00Z",
    "financials_extracted_at": "2026-01-18T13:45:00Z",
    "field_confidence": {"debt_instruments": 0.92},
    "warnings": ["3 estimated issue dates"]
  }
}
```

**Files created:**
- `alembic/versions/010_add_extraction_metadata.py` - Migration
- `scripts/backfill_extraction_metadata.py` - Backfill script

**Files modified:**
- `app/models/schema.py` - Added `ExtractionMetadata` model
- `app/models/__init__.py` - Export new model
- `app/api/primitives.py` - Added `include_metadata` parameter

---

### Enhancement 2: Diff/Changelog Endpoints
**Status**: ✅ COMPLETE (2026-01-18)
**Effort**: Medium-Large (2-3 days)
**Priority**: LOW (implement last)

New endpoint: `GET /v1/companies/{ticker}/changes?since={iso_date}`

**What was implemented:**
- New `company_snapshots` table storing quarterly point-in-time snapshots
- `GET /v1/companies/{ticker}/changes?since=YYYY-MM-DD` endpoint
- Diff logic comparing current data against historical snapshot
- Returns: new_debt, removed_debt, entity_changes, metric_changes, pricing_changes

**Example Response:**
```json
{
  "data": {
    "ticker": "RIG",
    "company_name": "Transocean Ltd.",
    "snapshot_date": "2026-01-18",
    "current_date": "2026-01-18",
    "changes": {
      "new_debt": [],
      "removed_debt": [],
      "entity_changes": {"added": 0, "removed": 0},
      "metric_changes": {"total_debt": {"previous": 750000000000, "current": 750000000000}},
      "pricing_changes": []
    },
    "summary": {"debt_added": 0, "debt_removed": 0, "net_debt_change": 0}
  }
}
```

**Files created:**
- `alembic/versions/011_add_company_snapshots.py` - Migration
- `scripts/create_snapshot.py` - Create snapshots (quarterly/monthly/manual)

**Files modified:**
- `app/models/schema.py` - Added `CompanySnapshot` model
- `app/models/__init__.py` - Export new model
- `app/api/primitives.py` - Added `/v1/companies/{ticker}/changes` endpoint

**Initial snapshot created:** 2026-01-18 for all 177 companies

---

### Enhancement 3: Batch Operations
**Status**: ✅ COMPLETE (2026-01-18)
**Effort**: Medium (1-2 days)
**Priority**: MEDIUM (implement second)

New endpoint `POST /v1/batch` for executing multiple primitives in a single request.

**What was implemented:**
- `POST /v1/batch` endpoint accepting up to 10 operations
- Parallel execution via `asyncio.gather`
- Independent failures (one operation's error doesn't affect others)
- Per-operation status and duration tracking

**Example Request:**
```json
{
  "operations": [
    {"primitive": "search.companies", "params": {"ticker": "AAPL,MSFT", "fields": "ticker,net_leverage_ratio"}},
    {"primitive": "search.bonds", "params": {"ticker": "TSLA", "has_pricing": true}},
    {"primitive": "resolve.bond", "params": {"q": "RIG 8% 2027"}}
  ]
}
```

**Example Response:**
```json
{
  "results": [
    {"operation_id": 0, "status": "success", "data": {...}},
    {"operation_id": 1, "status": "success", "data": {...}},
    {"operation_id": 2, "status": "error", "error": {"code": "NOT_FOUND", "message": "..."}}
  ],
  "meta": {
    "total_operations": 3,
    "successful": 2,
    "failed": 1,
    "duration_ms": 234
  }
}
```

**Supported Primitives:**
| Primitive Name | Maps To |
|---------------|---------|
| `search.companies` | `GET /v1/companies` |
| `search.bonds` | `GET /v1/bonds` |
| `resolve.bond` | `GET /v1/bonds/resolve` |
| `traverse.entities` | `POST /v1/entities/traverse` |
| `search.pricing` | `GET /v1/pricing` |
| `search.documents` | `GET /v1/documents/search` |

**Limits:**
- Max 10 operations per batch request
- Operations executed in parallel via asyncio.gather

**Files modified:**
- `app/api/primitives.py` - Added batch endpoint and handlers

---

## Implementation Order (Recommended)

| Order | Enhancement | Status |
|-------|-------------|--------|
| 1st | **Confidence Scores + Metadata** | ✅ COMPLETE |
| 2nd | **Batch Operations** | ✅ COMPLETE |
| 3rd | **Diff/Changelog** | ✅ COMPLETE |

**All 3 Agent-Friendly Enhancements are now complete!**

---

## Completed Work: Guarantee Extraction Pipeline

**Status**: ✅ COMPLETE (2026-01-21)
**Goal**: Extract guarantee relationships from SEC filings to populate guarantor data

### Results

| Metric | Before | After |
|--------|--------|-------|
| Total entities | 3,085 | 6,068 |
| Guarantor entities | ~200 | 3,582 |
| Total guarantees | ~390 | 4,426 |
| Debt with guarantees | ~390 | 701 |
| **Guarantee coverage** | **21.6%** | **34.7%** |

**Coverage by seniority:**
| Seniority | Coverage | Notes |
|-----------|----------|-------|
| Senior secured | 71% (120/170) | Most important for guarantees |
| Senior unsecured | 31% (578/1842) | Often no guarantees by design |
| Subordinated | 40% (2/5) | |

### Implementation

**Scripts created:**
| Script | Purpose |
|--------|---------|
| `scripts/fetch_guarantor_subsidiaries.py` | Fetches Exhibit 22.1 from SEC EDGAR, parses guarantor names using LLM, creates entities and guarantees |
| `scripts/extract_guarantees.py` | Extracts guarantees from stored indentures/credit agreements in document_sections |
| `scripts/batch_extract_guarantees.py` | Batch processing for all companies |
| `scripts/update_guarantee_confidence.py` | Updates `guarantee_data_confidence` field based on data quality |

**Database changes:**
- Added `guarantee_data_confidence` field to `debt_instruments` table (migration `ff16d034683e`)
- Confidence levels: `verified` (Exhibit 22), `extracted` (LLM), `partial`, `unknown`

**Confidence distribution:**
| Level | Count | Description |
|-------|-------|-------------|
| Verified | 50 | From Exhibit 22, high confidence |
| Extracted | 1,915 | From LLM parsing, medium confidence |
| Partial | 50 | Incomplete data |
| Unknown | 8 | No analysis performed |

**Data sources:**
1. **Exhibit 22.1** - SEC-mandated list of guarantor subsidiaries (since 2021)
2. **Exhibit 21** - Subsidiaries of the Registrant (fallback)
3. **Stored indentures/credit agreements** - Parsed for guarantor mentions

**API exposure:**
- `guarantee_data_confidence` field added to `/v1/bonds` endpoint response

---

## Active Work: Quality Control & Data Validation

**Status**: 🟡 IN PROGRESS
**Goal**: Ensure data quality before distribution launch

### Current Quality Metrics

| Metric | Current | Target | Gap |
|--------|---------|--------|-----|
| Companies with leverage ratios | 155/178 (87.1%) | 160/178 (90%) | 5 companies |
| Companies with financials | 178/178 (100%) | 178/178 (100%) | ✅ Complete |
| Known extraction errors | 0 companies | 0 | ✅ Fixed |
| Bonds with pricing | 30 | 100+ | Need pricing expansion |
| Bonds with CUSIPs | ~40% | 60%+ | Low priority |
| Debt with NULL amounts | 12 companies | 0 | Companies report aggregate only |

---

### Priority 1: Fix Known Extraction Errors
**Status**: ✅ COMPLETE (2026-01-20)
**Effort**: Completed

All scale detection issues fixed. Improved `detect_filing_scale()` to:
1. Search ALL occurrences of financial statement headers (not just first, which was often in TOC)
2. Added `$000` notation pattern (common for "in thousands")
3. Added "In thousands of U.S. dollars" pattern
4. Changed default to "dollars" with warning when no scale found

**Companies fixed:**
| Ticker | Issue | Resolution |
|--------|-------|------------|
| ACN | Scale error - showing trillions instead of billions | Fixed - was reading "in millions" from wrong section |
| ROST | Scale error - showing millions instead of billions | Fixed - `$000` notation now detected |
| SNPS | Scale error - showing trillions instead of billions | Fixed - "in thousands" found in actual financial statements |
| TTD | Scale error | Fixed |
| CHS | Scale error | Fixed |

---

### Priority 2: Expand Leverage Ratio Coverage
**Status**: ✅ MOSTLY COMPLETE (2026-01-20)
**Progress**: 155/178 companies (87.1%) - up from 57%

**Root causes for remaining 23 companies:**
1. **No debt** (54 companies): Many tech companies have zero debt (valid - no leverage ratio needed)
2. **Banks/financials** (2 companies): Different capital structure metrics apply
3. **NULL EBITDA** (remaining): Some companies don't report operating income in standard format

**Completed:**
- ✅ Integrated TTM extraction into main `extract_iterative.py` pipeline
- ✅ Added `--tickers` and `--force` flags to `batch_index.py` for targeted re-extraction
- ✅ Fixed scale detection bugs causing 1000x errors
- ✅ Re-extracted financials for scale error companies (ACN, ROST, SNPS, TTD, CHS)
- ✅ Recomputed metrics for all affected companies

---

### Priority 3: Debt Amounts NULL Issue
**Status**: 🟡 RESEARCH COMPLETE - DECISION NEEDED
**Effort**: Varies by option

**Problem:** 12 companies have debt instruments with NULL outstanding amounts (AAPL, VZ, T, KO, NFLX, etc.)

**Root Cause Analysis (2026-01-20):**
- Investigated AAPL as test case
- Apple's 10-K reports aggregate debt only: "$91.3 billion of fixed-rate notes outstanding"
- Individual bond names found in indentures, but **amounts not disclosed per tranche**
- This is a valid SEC disclosure practice - some companies only report aggregates

**QA Agent Updates:**
- Changed debt verification from FAIL to WARN when all amounts are NULL
- Added message: "individual amounts may not be disclosed"
- Companies like AAPL now pass QA with 85% score

**Data Enrichment Research (2026-01-20):**

| Source | Capability | Access | Notes |
|--------|------------|--------|-------|
| **FINRA bondReference API** | Query by `issuerName`, returns CUSIP, coupon, maturity, amounts | Requires FINRA API key + CUSIP license from S&P | Most promising - can query "Apple" and get all bonds |
| **Finnhub Bond API** | Query by ISIN/CUSIP/FIGI, returns `amountOutstanding` | Paid API, no issuer search | Requires knowing ISIN/CUSIP first |
| **SEC Prospectuses (FWP, 424B2)** | ISINs/CUSIPs in filings | Free (SEC-API.io) | `scripts/extract_isins.py` found 42 CUSIPs for AAPL |
| **OpenFIGI** | Map identifiers, coverage for bonds | Free with rate limits | Mapping tool, not a data source |

**Existing Tool:** `scripts/extract_isins.py` - extracts ISINs from prospectuses
- Tested on AAPL: Found **91 unique CUSIPs** from 29 FWP filings
- Enhanced to extract structured data: coupon, maturity year, CUSIP, ISIN
- **Key Finding (2026-01-20):** Direct matching by coupon+year doesn't work well because:
  - Our extracted bonds may be Euro-denominated (0.000% coupons typical for EUR)
  - FWP filings have CUSIPs only for USD bonds
  - Bond names extracted from 10-K/indentures don't always match prospectus data exactly
- **Recommendation:** The script extracts valid CUSIPs, but matching logic needs refinement

**Options:**

1. **Accept as-is** (Recommended for MVP):
   - Mark companies as "aggregate debt only"
   - Leverage ratios still work from financials
   - No additional cost/effort
   - Effort: None

2. **FINRA API Integration**:
   - Query `bondReference` by issuerName to get all bonds with amounts
   - Requires: FINRA API key + S&P CUSIP license
   - Effort: Medium (2-3 days)
   - Cost: License fees TBD

3. **SEC Prospectus Parsing** (extend existing script):
   - Use `extract_isins.py` to get CUSIPs
   - Parse bond details (coupon, maturity) from text near each ISIN
   - Match to our database by coupon + maturity year
   - Effort: Medium (2-3 days)
   - Cost: SEC-API.io usage only

4. **Finnhub Enrichment** (if we have CUSIPs):
   - Once we have CUSIPs (via option 3), query Finnhub for `amountOutstanding`
   - Effort: Small (1 day)
   - Cost: Finnhub subscription

**Recommendation:** Accept as-is for MVP, revisit after launch if users request per-bond amounts.

**Affected Companies (12):**
AAPL, VZ, T, KO, NFLX, FOX, LULU, MS, COF, WELL, PLTR, UAL

---

### Priority 4: Data Consistency Validation
**Status**: ✅ COMPLETE (2026-01-25)
**Effort**: 1 day

Build automated QC checks to identify data inconsistencies.

**Checks implemented in `scripts/qc_audit.py`:**
| Check | Description | Result |
|-------|-------------|--------|
| Debt instrument vs. financial mismatch | Sum of instruments vs. total_debt | 15 warnings (known edge cases) |
| Entity count sanity | Companies with 0 entities | ✅ All pass |
| Debt without issuer | Instruments with NULL issuer_id | ✅ All pass |
| Orphan guarantees | Guarantees referencing non-existent entities | ✅ All pass |
| Maturity date sanity | Bonds matured but is_active=true | ✅ Fixed 77 bonds |
| Duplicate debt instruments | Same name + issuer + maturity | 65 warnings (review needed) |
| Missing debt amounts | NULL outstanding amounts | 54 companies (accepted - aggregate only) |
| Companies without financials | Missing financial data | 2 companies (CSGP, DXCM) |
| Invalid leverage ratios | Out-of-bounds metrics | ✅ All pass |
| ISIN/CUSIP format | Identifier validation | ✅ All pass |

**Results:** 0 critical/errors, 5 warnings - AUDIT PASSED

---

### Priority 4: API Edge Case Testing
**Status**: ✅ COMPLETE (2026-01-25)
**Effort**: 0.5 day

Test API robustness before public launch.

**Tests in `scripts/test_api_edge_cases.py`:**
| Test Category | Tests | Result |
|---------------|-------|--------|
| Health endpoints | /v1/ping, /v1/health | ✅ 2/2 pass |
| Empty/missing params | Empty ticker, no params, missing required | ✅ 3/3 pass |
| Invalid tickers | Non-existent, special chars, very long | ✅ 3/3 pass |
| Invalid fields | Invalid field name, all invalid | ✅ 2/2 pass |
| Pagination | Large limit, negative limit, offset beyond | ✅ 3/3 pass |
| Malformed JSON | Invalid JSON, empty body, wrong structure | ✅ 3/3 pass |
| Non-existent IDs | CUSIP, company changes | ✅ 2/2 pass |
| SQL injection | 6 injection attempts | ✅ 6/6 pass |
| Content negotiation | JSON, CSV, invalid format | ✅ 3/3 pass |
| Error response format | 404, error structure | ✅ 2/2 pass |

**Results:** 29/29 tests passed - ALL TESTS PASSED

---

### Priority 5: Pricing Data Expansion
**Status**: ⬜ TODO (LOW)
**Effort**: Depends on data source

Currently only 30 bonds have pricing. Options:
- [ ] Expand FINRA TRACE pulls to more bonds
- [ ] Add pricing for high-yield issuers (more interesting for analysis)
- [ ] Document which bonds have/don't have pricing

**Note:** Pricing is valuable but not blocking. Document search and structure data are the core differentiators.

---

### QC Completion Criteria

Before distribution launch:
- [x] All known-bad extractions fixed ✅ (2026-01-20)
- [x] Leverage ratio coverage ≥ 80% ✅ (87.1% achieved)
- [x] QC audit script created and passing ✅ (2026-01-25) - 0 errors, 5 warnings
- [x] API edge cases tested ✅ (2026-01-25) - 29/29 tests passed
- [x] No critical data inconsistencies ✅ (2026-01-25) - audit finds no critical/error level issues
- [x] Decide on NULL debt amounts handling ✅ - accepted as-is (aggregate-only reporting)

---

## Active Work: Launch Prerequisites

**Status**: 🟡 IN PROGRESS
**Goal**: Ship to production with auth, billing, and expanded pricing

### Priority 1: Authentication & User Management
**Status**: ✅ COMPLETE (2026-01-22)
**Effort**: Completed in 1 session

Implemented API key-based authentication (simpler than OAuth for API-first product).

| Step | Description | Status |
|------|-------------|--------|
| 1. Database schema | Created `users`, `user_credits`, `usage_log` tables | ✅ Done |
| 2. SQLAlchemy models | Added `User`, `UserCredits`, `UsageLog` models | ✅ Done |
| 3. API key generation | `ds_` prefixed keys with SHA-256 hashing | ✅ Done |
| 4. Auth middleware | API key validation via `X-API-Key` header | ✅ Done |
| 5. Auth endpoints | `/v1/auth/signup`, `/me`, `/credits`, `/usage`, `/api-keys` | ✅ Done |
| 6. Credit system | Tier-based limits, overage support, billing cycles | ✅ Done |
| 7. Rate limiting | Per-user rate limits based on tier | ✅ Done |

**Files created:**
- `alembic/versions/013_add_auth_tables.py` - Migration
- `app/core/auth.py` - Auth utilities (key gen, validation, credits)
- `app/api/auth.py` - Auth endpoints

**Files modified:**
- `app/models/schema.py` - Added User, UserCredits, UsageLog models
- `app/models/__init__.py` - Export new models
- `app/core/config.py` - Added auth config variables
- `app/core/cache.py` - Added user-based rate limiting
- `app/main.py` - Updated middleware, added auth router
- `.env.example` - Added auth/billing config

**Auth Endpoints:**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/auth/signup` | POST | Create account, returns API key |
| `/v1/auth/me` | GET | Get current user info |
| `/v1/auth/credits` | GET | Get credit balance |
| `/v1/auth/usage` | GET | Get usage history |
| `/v1/auth/api-keys` | POST | Regenerate API key |
| `/v1/auth/pricing` | GET | Get pricing info (public) |

**Tier Configuration (Simplified 2026-01-23):**
| Tier | Price | Queries | Bond Pricing | Rate Limit |
|------|-------|---------|--------------|------------|
| Free | $0 | 2-3/day | No | 10/min |
| Pro | $49/mo | Unlimited | Real-time | 120/min |
| Enterprise | Custom | Unlimited | Real-time + Historical | Custom |

---

### Priority 2: Pricing & Billing (Stripe)
**Status**: ⬜ TODO
**Effort**: 1 day

| Step | Description | Status |
|------|-------------|--------|
| 1. Stripe setup | Create Free and Pro products | ⬜ TODO |
| 2. Webhook handler | Handle subscription events | ⬜ TODO |
| 3. Upgrade flow | Dashboard button to initiate Stripe checkout | ⬜ TODO |
| 4. Tier gating | Block pricing endpoints for Free tier | ⬜ TODO |

**Note:** Auth system complete. Need to update `app/core/auth.py` to match simplified tiers.

---

### Priority 3: Finnhub Pricing Expansion
**Status**: ⬜ TODO
**Effort**: Phase 1: 1 day | Phase 2: 0.5 day
**Cost**: ~$100/month for bond data tier

**Goal**: Price existing instruments with CUSIPs - don't create new instruments.

**Why this approach:**
- Our value is the **structure** (guarantees, collateral, hierarchy, issuers)
- 591 instruments already have CUSIPs linked to rich structural data
- Adding "thin" instruments (just CUSIP + price) would dilute quality
- Pricing refresh should be fast and efficient

**Current State:**
| Metric | Count |
|--------|-------|
| Instruments with CUSIP | 591 |
| Currently priced | 20 (3.4%) |
| Target | 400+ (where Finnhub has data) |

---

#### Phase 1: Current Pricing (MVP)

**Data Mapping - Finnhub → DebtStack:**

| Finnhub Field | DebtStack Table.Column | Notes |
|---------------|------------------------|-------|
| `close` | `bond_pricing.last_price` | Clean price as % of par |
| `yield` | `bond_pricing.ytm_bps` | Convert to basis points |
| `volume` | `bond_pricing.last_trade_volume` | Face value traded |
| `t` (timestamp) | `bond_pricing.last_trade_date` | Unix → datetime |
| `"Finnhub"` | `bond_pricing.price_source` | Track data source |

**Optional enrichment to `debt_instruments.attributes`:**
```json
{
  "finnhub": {
    "figi": "BBG00...",
    "callable": true,
    "coupon_type": "fixed"
  }
}
```

**Implementation Steps:**

| Step | Description | Status |
|------|-------------|--------|
| 1. Get Finnhub API key | Sign up for bond data tier (~$100/mo) | ⬜ Pending |
| 2. Add env variable | `FINNHUB_API_KEY` to config | ⬜ TODO |
| 3. CUSIP → ISIN conversion | Finnhub uses ISIN; add "US" prefix + check digit | ⬜ TODO |
| 4. Update pricing script | Modify `scripts/update_pricing.py` for Finnhub API | ⬜ TODO |
| 5. Batch lookup | Query 591 CUSIPs, update `bond_pricing` | ⬜ TODO |
| 6. Daily refresh | Railway cron or external scheduler | ⬜ TODO |

**API Notes:**
- Finnhub Bond Price API: `GET /bond/price?isin={ISIN}`
- Finnhub Bond Profile API: `GET /bond/profile?isin={ISIN}` (for FIGI, callable status)
- Rate limits: Check Finnhub tier for calls/minute
- Data source: FINRA TRACE (same as Bloomberg/Reuters)

---

#### Phase 2: Historical Pricing

**Why historical matters:**
- A bond at 85 today means different things if it was 95 last month vs. 75
- Credit deterioration signals: spread widening over time
- Enables backtesting and trend analysis

**Schema Addition:**
```sql
CREATE TABLE bond_pricing_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    debt_instrument_id UUID NOT NULL REFERENCES debt_instruments(id) ON DELETE CASCADE,
    price_date DATE NOT NULL,
    price NUMERIC(8,4),           -- Clean price as % of par
    ytm_bps INTEGER,              -- Yield to maturity in basis points
    spread_bps INTEGER,           -- Spread to treasury
    volume BIGINT,                -- Face value traded (cents)
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (debt_instrument_id, price_date)
);

CREATE INDEX idx_bond_pricing_history_instrument ON bond_pricing_history(debt_instrument_id);
CREATE INDEX idx_bond_pricing_history_date ON bond_pricing_history(price_date);
```

**API Exposure:**
```bash
# Current price (default - from bond_pricing table)
GET /v1/bonds?cusip=76825DAJ7&fields=pricing

# Historical prices (opt-in - from bond_pricing_history)
GET /v1/pricing/history?cusip=76825DAJ7&from=2025-01-01&to=2026-01-27
```

**Storage Estimate:**
- 591 bonds × 365 days/year = ~216K rows/year
- ~50 bytes/row = ~11 MB/year (trivial)

**Retention Policy:**
- Keep 2 years of daily data
- Option: Aggregate to weekly for older periods (defer until needed)

**Implementation Steps:**

| Step | Description | Status |
|------|-------------|--------|
| 1. Migration | Create `bond_pricing_history` table | ⬜ TODO |
| 2. Daily snapshot | After updating `bond_pricing`, copy to history | ⬜ TODO |
| 3. API endpoint | Add `/v1/pricing/history` with date range params | ⬜ TODO |
| 4. Backfill | Optional: Fetch historical data from Finnhub candle API | ⬜ TODO |

---

**Not in scope** (for now):
- Creating new instruments from Finnhub bond universe
- Enriching instruments without CUSIPs (would require matching by issuer/coupon/maturity)
- Real-time streaming prices (batch daily is sufficient for credit analysis)

---

### Priority 4: Website Updates
**Status**: ✅ COMPLETE (2026-01-23)
**Effort**: 0.5 day

| Change | Current | Launch | Status |
|--------|---------|--------|--------|
| Status badge | "Private Beta" | Removed | ✅ Done |
| Primary CTA | "Get Early Access" → waitlist | "Start Free" → signup | ✅ Done |
| Pricing section | Not shown | `/pricing` page with Free/Starter/Growth/Scale/Enterprise | ✅ Done |
| Data stats | Not shown | "189 companies, 2,849 debt instruments, 5,750+ docs, 4,881 guarantees" | ✅ Done |
| Beta language | "Launching soon", "Join waitlist" | Removed, replaced with signup CTAs | ✅ Done |
| Copyright | 2025 | 2026 | ✅ Done |
| Footer links | Missing pricing/docs | Added Pricing, Docs links | ✅ Done |

**Files Modified:**
- `debtstack-website/app/page.tsx` - Updated hero, CTAs, footer, removed waitlist
- `debtstack-website/app/dashboard/page.tsx` - Added Contact Sales option
- `debtstack-website/app/pricing/page.tsx` - **NEW** - Full pricing page with tiers, FAQ, credit costs

---

### Priority 5: SDK Publication
**Status**: ⬜ TODO
**Effort**: 0.5 day

| Step | Description | Status |
|------|-------------|--------|
| 1. Final review | Ensure SDK matches current API | ⬜ TODO |
| 2. PyPI account | Create account if needed | ⬜ TODO |
| 3. Publish | `python -m build && twine upload dist/*` | ⬜ TODO |
| 4. Test install | `pip install debtstack-ai` | ⬜ TODO |

---

### Priority 5: Explorer Page
**Status**: ✅ PARTIAL (2026-01-23)
**Effort**: 0.5 day

Interactive visualization page at `/explorer` where users can:
- Enter any ticker from the 189 covered companies
- See corporate structure chart with entities and debt at each level
- Pro users see bond pricing data, Free users see structure only

| Step | Description | Status |
|------|-------------|--------|
| 1. Build `/explorer` page | Ticker input + structure visualization | ✅ Done |
| 2. API integration | Fetch from `/v1/companies/{ticker}` endpoints | ✅ Done |
| 3. Tier gating | Show pricing only for Pro users | ⬜ TODO |

**Note**: Page built at `debtstack-website/app/explorer/page.tsx`. Shows entity tree with debt instruments. However, entity ownership hierarchy data is incomplete (see Data Quality Issues section) - most entities show as direct children of top HoldCo instead of nested structure.

---

### Priority 6: Mintlify Documentation
**Status**: ✅ COMPLETE (2026-01-23)
**Effort**: 1 day

Set up docs at `docs.debtstack.ai` using Mintlify with:
- Quickstart guide
- Authentication docs
- API reference (auto-generated from OpenAPI)
- Interactive API playground
- Code examples (Python, curl)

| Step | Description | Status |
|------|-------------|--------|
| 1. Mintlify setup | Create project, connect domain | ✅ Done |
| 2. OpenAPI spec | Ensure FastAPI spec is clean | ✅ Done |
| 3. Write core docs | Quickstart, auth, examples | ✅ Done |
| 4. Deploy | Connect to docs.debtstack.ai | ⬜ TODO |

**Documentation Created:**
- `debtstack-website/docs/mint.json` - Mintlify configuration
- `debtstack-website/docs/introduction.mdx` - Welcome & overview
- `debtstack-website/docs/quickstart.mdx` - Getting started guide
- `debtstack-website/docs/authentication.mdx` - API keys, rate limits, credits
- `debtstack-website/docs/concepts/` - Data model, field selection, pagination
- `debtstack-website/docs/api-reference/` - Full API reference for all 8 primitives
- `debtstack-website/docs/guides/` - AI agents, LangChain, MCP integration guides

---

### Launch Checklist

- [x] API key authentication system ✅ (2026-01-22)
- [x] API key generation and validation ✅ (2026-01-22)
- [x] Credit tracking and limits ✅ (2026-01-22)
- [x] Per-tier rate limiting ✅ (2026-01-22)
- [x] QC audit passing (0 critical/errors) ✅ (2026-01-25)
- [x] Duplicate instruments cleaned up ✅ (2026-01-25) - 87 removed
- [ ] **Stripe billing connected** ← NEXT
- [ ] Finnhub pricing expanded (200+ bonds)
- [x] Website updated (CTA, pricing, remove beta) ✅ (2026-01-23)
- [x] Explorer page (structure visualization) ✅ (2026-01-23) - needs hierarchy data fix
- [x] Mintlify docs created ✅ (2026-01-23) - needs deployment
- [ ] SDK published to PyPI
- [ ] Mintlify docs deployed to docs.debtstack.ai

---

## Completed Work: Document-to-Instrument Linking

**Status**: ✅ COMPLETE (2026-01-24)
**Goal**: Link every debt instrument to its governing legal document (indenture or credit agreement)

### Results

| Metric | Before | After |
|--------|--------|-------|
| Document coverage | 69.4% | **100%** |
| Linked instruments | 1,779 | 2,560 |
| Linkable instruments | 2,563 | 2,557 |
| No-doc-expected | 88 | 94 |

### Implementation

**Phase 1: Data Quality Fixes**
| Script | Purpose | Impact |
|--------|---------|--------|
| `fix_missing_interest_rates.py` | Extract rates from names like "4.50% Notes" | 43 instruments fixed |
| `fix_missing_maturity_dates.py` | Extract years from names like "due 2030" | 219 instruments fixed |
| `fix_empty_instrument_names.py` | LLM extracts names from debt footnotes | 56 instruments fixed |

**Phase 2: Smart Matching**
| Script | Purpose | Impact |
|--------|---------|--------|
| `smart_document_matching.py` | Pattern-based matching (searches full doc content) | 31 matches |

**Phase 3: Fallback Linking**
| Script | Purpose | Impact |
|--------|---------|--------|
| `link_to_base_indenture.py` | Links notes to base/supplemental indentures (60% confidence) | 578 instruments linked |
| `link_to_credit_agreement.py` | Links loans/revolvers to credit agreements (60% confidence) | 190 instruments linked |

**Phase 4: Exclusions**
| Script | Purpose | Impact |
|--------|---------|--------|
| `mark_no_doc_expected.py` | Marks commercial paper, bank loans, etc. | 94 instruments excluded |

### Key Learnings

1. **Don't game metrics** - Initially suggested marking term loans as "no document expected" to inflate coverage. User correction: *"term loan documentation is critical"*. The real fix was better matching, not exclusions.

2. **Base indentures govern all notes** - Most companies have a single base indenture from the 1990s-2000s under which all notes are issued. When no specific supplemental indenture exists, linking to the base indenture (with lower confidence) is correct.

3. **Pattern matching > LLM for large documents** - Credit agreements are 400K+ chars. Searching for "Term A-6" directly is faster and more reliable than asking an LLM to find it in truncated content.

4. **Fix data quality first** - Many instruments couldn't match because they had NULL rates or maturities that were embedded in their own names.

5. **Lower confidence is better than no link** - A 60% confidence link to a base indenture is more useful for credit analysis than leaving the instrument unlinked.

### Confidence Levels

| Match Method | Confidence | Description |
|--------------|------------|-------------|
| `cusip_isin_match` | 0.95 | Direct identifier match |
| `smart_name_match` | 0.85 | Pattern found instrument name in document |
| `smart_rate_maturity_match` | 0.80 | Rate + maturity year found in document |
| `base_indenture_fallback` | 0.60 | Linked to base indenture (no specific found) |
| `suppl_indenture_fallback` | 0.55 | Linked to supplemental indenture |
| `credit_agreement_fallback` | 0.60 | Linked to most recent credit agreement |

---

## Data Quality Issues

### Entity Ownership Hierarchy (Nested Structure)
**Status**: ✅ COMPLETE
**Completed**: 2026-01-23

**Problem**: Entity parent-child relationships showed flat structure instead of nested ownership chains.

**Solution**: Two-phase approach:
1. Parse Exhibit 21 HTML indentation for companies that use it
2. Use Gemini LLM to fill remaining gaps for key entities (guarantors/issuers)

**Implementation**:
- `scripts/extract_exhibit21_hierarchy.py` - Parses SEC Exhibit 21 HTML indentation
- `scripts/extract_orphan_parents.py` - Uses Gemini to assign parents to orphan guarantors

**Final Results**:
| Metric | Before | After |
|--------|--------|-------|
| Entities with parent_id | 45.7% | **82.2%** |
| Total ownership_links | 2,468 | **4,427** |
| Direct subsidiaries | 2,140 | 4,206 |
| Indirect subsidiaries | 328 | 221 |
| Guarantors with parent | ~85% | **99.6%** |

**Key Achievement**: Guarantor entities (the ones that matter most for credit analysis) now have 99.6% parent coverage - only 3 orphans remain.

**Scripts**:
```bash
# Extract hierarchy from Exhibit 21 HTML indentation
python scripts/extract_exhibit21_hierarchy.py --all --save-db

# Fill remaining gaps for guarantor entities using Gemini
python scripts/extract_orphan_parents.py --top 50 --save-db
```

---

## Backlog

### Future Opportunity: Network Effects & Competitive Moat
**Status**: 📋 BACKLOG
**Priority**: MEDIUM - Strategic for long-term defensibility

Traditional data APIs lack network effects. These features create compounding value as usage grows.

#### User-Contributed Data Layer

| Feature | Description | Effort |
|---------|-------------|--------|
| **Error flagging** | Let users flag data errors ("wrong CUSIP", "missing guarantor"). Each correction improves data for everyone. | Small |
| **Custom entity mappings** | Users link internal IDs to DebtStack entities. Creates lookup table others can use. | Medium |
| **Private → Public pipeline** | Users contribute anonymized queries or data in exchange for credits. | Medium |

#### Agent Workflow Sharing

| Feature | Description | Effort |
|---------|-------------|--------|
| **Query templates** | Public query patterns ("distressed screen", "covenant breach detector"). More users = better templates. | Small |
| **Agent recipes** | LangChain/MCP workflows users can fork. DebtStack becomes hub for credit analysis agents. | Medium |

#### Coverage Expansion via Usage

| Feature | Description | Effort |
|---------|-------------|--------|
| **Demand-driven extraction** | Track requested but uncovered tickers. Prioritize extraction based on demand. | Small |
| **User-funded extraction** | Users pay to add a company. Once added, available to everyone. | Medium |

#### Data Network Effects

| Feature | Description | Effort |
|---------|-------------|--------|
| **Cross-reference density** | More companies = more guarantor chain connections discovered. Entity relationships across companies (shared subsidiaries, JV partners) visible only at scale. | Inherent |
| **Temporal depth** | Historical covenant breach patterns, restructuring signals. Longer history = more valuable for training/backtesting. | Inherent (builds over time) |

#### Switching Cost Moats

| Feature | Description | Effort |
|---------|-------------|--------|
| **Integration lock-in** | Deep LangChain/MCP integration creates code dependencies. | Already implemented |
| **Derived data products** | Users build dashboards, alerts, reports on top of DebtStack. Breaking changes = switching cost. | Inherent |

**Fastest to implement with real network effects:**
1. Error flagging (users improve data quality for everyone)
2. Demand-driven extraction (usage signals drive coverage)
3. Query template sharing (community content)

---

### Completed: Covenant Relationship Extraction
**Status**: ✅ COMPLETE (2026-01-24)
**Effort**: Medium (implemented in 1 day)
**Priority**: MEDIUM - Enriches ownership hierarchy with credit-specific relationships

Extracts additional relationship data from stored indentures (796) and credit agreements (1,720) to supplement Exhibit 21 hierarchy.

**Relationship Types Extracted:**

| Relationship Type | Storage Location | Description |
|-------------------|------------------|-------------|
| **Unrestricted Subsidiaries** | `entities.is_unrestricted` | Flag entities designated as unrestricted |
| **Guarantee Conditions** | `guarantees.conditions` (JSONB) | Release/add triggers for guarantees |
| **Cross-Default Triggers** | `cross_default_links` (new table) | Links between debt instruments with thresholds |
| **Non-Guarantor Disclosure** | `company_metrics.non_guarantor_disclosure` (JSONB) | "Non-guarantor subs own X% of EBITDA" |

**Database Changes (Migration 015):**
1. Added `conditions` JSONB column to `guarantees` table
2. Added `non_guarantor_disclosure` JSONB column to `company_metrics` table
3. Created `cross_default_links` table with indexes

**Scripts Created:**
| Script | Purpose |
|--------|---------|
| `scripts/extract_covenant_relationships.py` | Main extraction using Gemini 2.0 Flash |
| `scripts/audit_covenant_relationships.py` | Data quality audit |

**Usage:**
```bash
# Single company
python scripts/extract_covenant_relationships.py --ticker CHTR --save-db

# Batch all companies
python scripts/extract_covenant_relationships.py --all --save-db

# Audit results
python scripts/audit_covenant_relationships.py --verbose
```

**Expected Results:**
- ~50-100 unrestricted subsidiaries flagged across 189 companies
- ~500-1000 guarantees with release trigger data
- ~200-500 cross-default links
- ~30-50 companies with non-guarantor EBITDA/asset percentages

**Estimated Cost:** ~$1.50 for all 189 companies (189 * $0.008/call)

---

### Future Opportunity: Structured Covenant Extraction
**Status**: 📋 BACKLOG
**Effort**: Large (6-10 days)
**Priority**: LOW - High differentiation, but defer until distribution complete

**What it would provide:**
- Parsed covenant thresholds (e.g., "max leverage 5.0x", "min interest coverage 2.0x")
- Covenant type classification (maintenance vs. incurrence)
- Compliance headroom calculation (current metric vs. threshold)
- Bond-to-covenant linking via new `covenants` table

**Why it's valuable:**
- No public API provides structured covenant data (Bloomberg/CapIQ do, but expensive)
- Critical for credit analysis (covenant breach = default risk signal)
- Leverages existing document corpus (796 indentures, 1,720 credit agreements, 815 covenant sections)

**Current state:** Document search already provides 80% of value—users can search "maintenance covenant" and get snippets. Structured extraction is the "premium" layer.

**Defer until:**
- Distribution playbook Phase 1 complete (SDK published, LangChain PR merged)
- User requests for structured covenant data
- Competitor emergence requiring differentiation

**Implementation steps (when ready):**
1. Design `covenants` table (debt_instrument_id, covenant_type, metric, threshold, current_value, headroom)
2. Build extraction prompts for covenant parsing from indentures/credit agreements
3. Add QA checks for threshold validation (numeric parsing, metric matching)
4. Create `GET /v1/covenants` endpoint with filtering
5. Backfill from existing document sections
6. Add `has_maintenance_covenant` filter to `/v1/bonds`

---

### Website Demo Scenarios
**Status**: 📋 BACKLOG
**Priority**: MEDIUM - Important for developer adoption

Add guided demo scenarios to Mintlify docs showing real-world API usage patterns.

**Recommended Demos:**
1. **Bond Screener** - Filter by yield, seniority, collateral
   ```
   GET /v1/bonds?min_ytm=800&seniority=senior_secured&has_pricing=true
   ```
2. **Corporate Structure** - Entity traversal, guarantee relationships
   ```
   POST /v1/entities/traverse
   {"start": {"type": "company", "ticker": "CHTR"}, "relationships": ["parent_of", "guarantees"]}
   ```
3. **Document Search** - Full-text search across SEC filings
   ```
   GET /v1/documents/search?q=change+of+control&ticker=CHTR&section_type=indenture
   ```

**Implementation:**
- Create `docs/guides/scenarios.mdx` in debtstack-website repo
- Use Mintlify's built-in API playground (already configured in `docs.json`)
- Keep existing `/explorer` tab for visual corporate structure demos

**Website Architecture Decision:**
- Website repo: `debtstack-website` (Vercel) - marketing, demos, dashboard
- API repo: `credible` (Railway) - API, extraction, data pipeline
- Docs: Mintlify hosted, source in `debtstack-website/docs/`
- Website calls production API with demo API key for interactive demos

---

### Data Quality
- [ ] Validate CUSIP mappings against FINRA (deferred - CUSIPs not needed for MVP)
- [x] Fix any ticker/CIK mismatches - Done 2026-01-17 (137 companies updated)
- [x] Clean up entity name normalization edge cases - Done 2026-01-17

### API Enhancements
- [x] Add CSV export option for bulk data - Done 2026-01-17
- [x] Add ETag caching headers - Done 2026-01-17
- [x] Rate limiting implementation - Done 2026-01-17

### Extraction Pipeline
- [ ] Extract ISINs from filings (deferred - not needed for MVP)
- [x] Extract issue_date more reliably - Done 2026-01-17
- [x] Extract floor_bps for floating rate debt - Done 2026-01-17
- [x] Fix scale detection in financial extraction - Done 2026-01-18 (reads scale from filing)

### Infrastructure
- [x] Clean up temp directories (`tmpclaude-*`) - Done 2026-01-17, added to .gitignore
- [x] Set up monitoring/alerting - Done 2026-01-17
- [x] Add API usage analytics - Done 2026-01-17

---

## File Reference

| Purpose | File |
|---------|------|
| Primitives API | `app/api/primitives.py` |
| Legacy REST API | `app/api/routes.py` |
| Extraction with QA | `app/services/iterative_extraction.py` |
| Hierarchy extraction | `app/services/hierarchy_extraction.py` |
| Guarantee extraction | `app/services/guarantee_extraction.py` |
| Collateral extraction | `app/services/collateral_extraction.py` |
| Metrics computation | `app/services/metrics.py` |
| QC checks | `app/services/qc.py` |
| Database schema | `app/models/schema.py` |
| Monitoring/analytics | `app/core/monitoring.py` |
| Financials extraction | `scripts/extract_financials.py` |
| Pricing updates | `scripts/update_pricing.py` |
| CUSIP mapping | `scripts/map_cusips.py` |
| Recompute metrics | `scripts/recompute_metrics.py` (backfill existing data) |
| Fix ticker/CIK | `scripts/fix_ticker_cik.py` (CIK to ticker mapping) |
| Guarantee extraction | `scripts/extract_guarantees.py` (from indentures) |
| Exhibit 22 fetching | `scripts/fetch_guarantor_subsidiaries.py` (from SEC) |
| Batch guarantees | `scripts/batch_extract_guarantees.py` (all companies) |
| Confidence update | `scripts/update_guarantee_confidence.py` |
| Collateral extraction | `scripts/extract_collateral.py` |
| Covenant relationships | `scripts/extract_covenant_relationships.py` |
| Covenant audit | `scripts/audit_covenant_relationships.py` |
| Fix missing rates | `scripts/fix_missing_interest_rates.py` |
| Fix missing maturities | `scripts/fix_missing_maturity_dates.py` |
| Fix empty names | `scripts/fix_empty_instrument_names.py` |
| Smart doc matching | `scripts/smart_document_matching.py` |
| Link to base indenture | `scripts/link_to_base_indenture.py` |
| Link to credit agreement | `scripts/link_to_credit_agreement.py` |
| Mark no-doc-expected | `scripts/mark_no_doc_expected.py` |
| Financial QC audit | `scripts/qc_financials.py` |
| Fix QC issues | `scripts/fix_qc_financials.py` |
| Deduplicate instruments | `scripts/deduplicate_instruments.py` |

---

## How to Resume Work

When starting a new session, read this file first, then:

1. **To expand financials coverage** (Priority 2 continuation):
   ```bash
   # Extract financials for more companies using Claude for better quality
   python scripts/extract_financials.py --ticker GM --use-claude --save-db
   python scripts/extract_financials.py --ticker MSFT --use-claude --save-db
   python scripts/extract_financials.py --ticker SPG --use-claude --save-db
   python scripts/extract_financials.py --ticker DISH --use-claude --save-db

   # After extraction, recompute metrics to populate leverage ratios
   python scripts/recompute_metrics.py
   ```

2. **For Priority 3 (ratings)** - DEFERRED:
   - Automated extraction from filings has low yield (companies don't disclose specific ratings)
   - Created `scripts/extract_ratings.py` but it's not reliable
   - Best approach: Manual entry for key bonds using FINRA or public sources

3. **For Priority 4 (document search)**:
   - Design document storage schema
   - Build section extraction for Note 9, MD&A

---

## Session Log

### 2026-01-28 (Session 27) - Service Module Refactoring & Website Demo Planning

**Objective:** Refactor extraction logic into service modules, plan website demos.

**Part 1: Service Module Refactoring**
- ✅ Created `app/services/hierarchy_extraction.py` - Exhibit 21 parsing, ownership hierarchy
- ✅ Created `app/services/guarantee_extraction.py` - Guarantee relationships from indentures
- ✅ Created `app/services/collateral_extraction.py` - Collateral for secured debt
- ✅ Created `app/services/qc.py` - Quality control checks
- ✅ Created `app/services/metrics.py` - Credit metrics computation with TTM tracking
- ✅ Refactored `scripts/extract_iterative.py`: 1681 → 931 lines (-750 lines removed)
- ✅ Refactored `scripts/recompute_metrics.py` to thin CLI wrapper
- ✅ Added `source_filings` JSONB to `company_metrics` (migration 018)
- ✅ Added `source_filing_url` to financial extraction for provenance tracking
- ✅ Verified all imports and tests pass

**Part 2: Source Filing Provenance**
- ✅ Added tracking for TTM calculations requiring multiple 10-Qs
- ✅ `source_filings` JSONB stores: debt_source, debt_filing, ttm_quarters, ttm_filings, computed_at

**Part 3: Website Demo Planning**
- ✅ Explored `debtstack-website` codebase structure
- ✅ Reviewed existing `/explorer` tab (corporate structure visualizer)
- ✅ Reviewed existing `LiveDemo` component (animated code demos)
- ✅ Reviewed Mintlify docs setup (`docs.json`, API reference MDX files)
- ✅ **Decision:** Use Mintlify's built-in API playground instead of custom playground
- ✅ **Decision:** Keep `/explorer` for visual demos, add scenarios guide to Mintlify docs

**Recommended Demo Scenarios:**
1. Bond Screener - `GET /v1/bonds?min_ytm=800&seniority=senior_secured`
2. Corporate Structure - `POST /v1/entities/traverse`
3. Document Search - `GET /v1/documents/search?q=change+of+control`

**Files Created:**
| File | Purpose |
|------|---------|
| `app/services/hierarchy_extraction.py` | Exhibit 21 parsing, ownership hierarchy |
| `app/services/guarantee_extraction.py` | Guarantee relationships |
| `app/services/collateral_extraction.py` | Collateral extraction |
| `app/services/qc.py` | Quality control checks |
| `app/services/metrics.py` | Credit metrics computation |
| `alembic/versions/018_add_source_filings_to_metrics.py` | Migration for source_filings |

**Files Modified:**
| File | Changes |
|------|---------|
| `scripts/extract_iterative.py` | Refactored to use service modules (-750 lines) |
| `scripts/recompute_metrics.py` | Now thin CLI wrapper |
| `app/models/schema.py` | Added `source_filings` JSONB to CompanyMetrics |
| `app/services/financial_extraction.py` | Added `source_filing_url` field |

---

### 2026-01-27 (Session 26) - Finnhub Planning & Railway Deployment Fix

**Objective:** Plan Finnhub pricing integration, fix Railway deployment, document user journey.

**Part 1: Finnhub Integration Planning**
- ✅ Researched Finnhub Bond APIs (profile, price, tick)
- ✅ Confirmed Finnhub does NOT provide credit ratings (S&P/Moody's/Fitch)
- ✅ Documented data mapping: Finnhub → `bond_pricing` table
- ✅ Planned Phase 1 (current pricing) and Phase 2 (historical pricing)
- ✅ Added `bond_pricing_history` table design for daily snapshots
- ✅ Confirmed 591 instruments have both CUSIP and ISIN (perfect overlap)

**Part 2: Railway Deployment Fix**
- ✅ Identified uncommitted changes blocking deployment
- ✅ Added `stripe>=7.0.0` to requirements.txt (missing dependency)
- ✅ Committed and pushed - deployment now successful

**Part 3: Document Search User Journey**
- ✅ Documented two-phase user journey:
  - **Discovery**: `/v1/bonds` - filter by yield, collateral, seniority
  - **Deep Dive**: `/v1/documents/search` - answer bond-specific questions
- ✅ Confirmed Option C approach: API returns snippets, agent summarizes
- ✅ Validated search coverage:
  - 3,608 docs with "event of default"
  - 2,050 docs with "change of control"
  - 1,752 docs with "collateral"
  - 976 docs with "asset sale"

**Key Insight - User Journey:**
```
Discovery (Primitives)              Deep Dive (Document Search)
───────────────────────            ─────────────────────────────
1. Find bonds: yield >8%
2. Filter: secured by equipment
3. User picks specific bond ──────► 4. "What are the covenants?"
                                    5. "Any make-whole premium?"
                                    6. "What triggers default?"
```

**Files Modified:**
- `requirements.txt` - Added stripe dependency
- `WORKPLAN.md` - Finnhub phases, user journey documentation
- `CLAUDE.md` - Finnhub integration section, env vars

---

### 2026-01-26 (Session 25) - Duplicate Instruments Fix & Code Simplification

**Objective:** Fix duplicate instruments QC warning and simplify extraction code.

**Part 1: Duplicate Instruments Fix**
- ✅ Ran dedupe script - found 18 groups, deleted 2 true duplicates
- ✅ Fixed `dedupe_instruments.py` to handle guarantee unique constraint violations
- ✅ Fixed `qc_master.py` to include `issuer_id` in duplicate detection query
  - Issue: Different entities within same company can have same-named instruments (e.g., holdco vs opco notes)
  - Fix: Added `di.issuer_id` to GROUP BY clause so different issuers aren't flagged as duplicates

**Part 2: Code Simplification**
- ✅ Created `app/services/extraction_utils.py` - consolidated shared utilities (~400 lines deduplicated)
  - `clean_filing_html()` - SEC filing HTML/XBRL cleaning
  - `truncate_content()` - Smart content truncation at sentence boundaries
  - `combine_filings()` - Combine multiple filings with priority ordering
  - `extract_debt_sections()` - Extract debt-related content with keyword priority (PRIORITY > GENERAL > JV/VIE)
  - `ModelTier` enum - LLM model tiers with cost data
  - `calculate_cost()` - LLM token cost calculation
  - `LLMUsage` dataclass - Token tracking across calls
  - `validate_extraction_structure()`, `validate_entity_references()`, `validate_debt_amounts()` - Validation helpers

- ✅ Created `scripts/script_utils.py` - shared CLI script utilities
  - `get_db_session()` - Async database session context manager
  - `get_all_companies()`, `get_company_by_ticker()` - Company lookup helpers
  - `create_base_parser()`, `create_fix_parser()`, `create_extract_parser()` - CLI argument parsers
  - `print_header()`, `print_summary()`, `print_progress()` - Output formatting
  - `process_companies()` - Batch processing with progress tracking
  - `run_async()` - Async runner with Windows event loop policy

- ✅ Updated `app/services/utils.py` - re-exports for backwards compatibility
- ✅ Updated `app/services/tiered_extraction.py` - uses shared utilities
- ✅ Verified all imports and functionality work correctly

**QC Results After Fixes:**
| Check | Before | After |
|-------|--------|-------|
| Critical | 0 | 0 |
| Errors | 0 | 0 |
| Warnings | 5 | 4 |

**Files Created:**
| File | Purpose |
|------|---------|
| `app/services/extraction_utils.py` | Consolidated extraction utilities |
| `scripts/script_utils.py` | Shared CLI script utilities |

**Files Modified:**
| File | Changes |
|------|---------|
| `app/services/utils.py` | Added re-exports for backwards compatibility |
| `app/services/tiered_extraction.py` | Now imports from extraction_utils.py |
| `scripts/qc_master.py` | Added issuer_id to duplicate detection |
| `scripts/dedupe_instruments.py` | Handle guarantee constraint violations |

---

### 2026-01-25 (Session 24) - Financial QC Script & Critical Data Fixes

**Objective:** Continue financial data quality work - fix critical scale errors and improve QC script.

**Part 1: Deleted Impossible Records**
- ✅ Identified 2 records with revenue > $1T (scale errors):
  - CDNS Q4 2024: $4641B revenue (should be ~$1B)
  - SNPS Q2 2025: $1604B revenue (should be ~$1.5B)
- ✅ Created `scripts/fix_qc_financials.py` to surgically fix QC issues
- ✅ Deleted the 2 impossible records (older quarters, newer correct data exists)

**Part 2: QC Results After Fix**
- **Before**: 2 critical, 14 errors
- **After**: 0 critical, 14 errors

**Remaining 14 Errors (Understood):**

| Category | Tickers | Issue | Resolution |
|----------|---------|-------|------------|
| EBITDA > Revenue | MSTR (2 records) | Bitcoin company - EBITDA includes unrealized BTC gains | Valid - not an error |
| 10-K extraction failures | GM, MS, SCHW, PGR, WELL, GEV, X, DO | Q4/10-K records missing revenue but have EBITDA | 10-K format differs; older records |
| Missing assets | OXY, CVX, COP | Recent quarters with debt but zero assets | Extraction failures |

**Key Insight:** The remaining 14 errors are **extraction failures** (missing data), not **scale errors** (wrong data). The QC script correctly identifies these as needing re-extraction.

**Scripts Created/Updated:**
| Script | Purpose |
|--------|---------|
| `fix_qc_financials.py` | Surgical fix for QC-flagged records (delete impossible, report issues) |

**Files Modified:**
- `WORKPLAN.md`: Updated with session log
- `scripts/qc_financials.py`: Already validates against source (from previous session)

---

### 2026-01-25 (Session 23) - QC Audit, Data Cleanup & Warning Deep Dive

**Objective:** Run QC audit, investigate warnings, and fix data quality issues.

**Part 1: Initial QC Audit**
- ✅ Ran `scripts/qc_audit.py --verbose` - identified 5 warnings, 0 critical/errors
- ✅ Ran `scripts/qc_audit.py --fix` - auto-fixed 77 matured bonds (marked inactive)

**Part 2: Deep Dive into Warnings**

Investigated each warning category in detail:

**Duplicates Analysis:**
- BAC: 8 instruments with empty names (extraction failures)
- JNJ: 4 copies of same note (LLM retry issue)
- APA: 14 duplicate sets across all notes
- TMUS: 13 extra copies across various notes

**Debt/Financial Mismatch Analysis:**
- Banks (JPM): $5.4B instruments vs $427B financials - banks have deposits/wholesale funding not in notes
- Missing amounts (KO, UNH): Only 2-6 of 10-40 instruments have amounts
- Scale concerns (INTU, META): Investigated - META issued $30B new debt after Q3 financials
- **Critical Learning**: NEVER blindly fix scale errors - always verify against source SEC filing

**Part 3: Fixes Applied**

| Fix | Result |
|-----|--------|
| Deduplicated instruments | 87 duplicates removed (63 sets) |
| Added CIKs for CSGP, DXCM | CIK 0001057352, 0001093557 |
| Extracted DXCM financials | Q3 2025: $2.4B debt, $1.2B revenue |
| Fixed Pydantic float coercion | Financial extraction now handles LLM float outputs |

**Part 4: Updated QC Results (After Fixes)**
| Check | Status | Before | After |
|-------|--------|--------|-------|
| Duplicate instruments | ✅ PASS | 65 sets | 0 |
| Matured bonds active | ✅ PASS | 77 | 0 |
| Companies without financials | ✅ PASS | 2 | 0 |
| Debt/financial mismatch | ⚠️ WARN | 15 | 15 (understood) |
| Missing amounts | ℹ️ INFO | 66.4% | 68.8% |

**Scripts Created:**
| Script | Purpose |
|--------|---------|
| `deduplicate_instruments.py` | Remove duplicate debt instruments, keep best record |
| `fix_scale_errors.py` | Analyze scale mismatches (NOT for blind auto-fix) |

**Key Learnings Added to CLAUDE.md:**
1. **ALWAYS detect scale from source document** - never assume or blindly fix
2. **Banks have different debt structures** - total_debt includes deposits, not just public notes
3. **Recent bond issuances cause temporary mismatches** - compare against filing dates

**Final QC Status:** 0 critical/errors, 2 warnings (understood and documented)

---

### 2026-01-25 (Session 22) - Ownership Hierarchy & Entity Root Identification

**Objective:** Fix flattened ownership hierarchy and accurately represent parent/child relationships.

**Problem Identified:**
- 89% of non-root entities had `parent_id` pointing to root company (tier 1)
- Tier 3/4 entities should have tier 2/3 parents, not root directly
- No way to distinguish root entities from orphans (both had `parent_id = NULL`)

**Part 1: Ownership Extraction from Documents**
- ✅ Created `fix_ownership_hierarchy.py` - extracts ONLY explicit ownership statements
- ✅ No inferences - only relationships with direct quotes from SEC filings
- ✅ Ran on all 194 companies
- **Results:** 1,033 explicit relationships found, 862 parent updates applied

**Part 2: Direct vs Indirect Ownership**
- ✅ Updated script to track `ownership_type` based on explicit language:
  - `"direct"` - only if evidence says "direct subsidiary"
  - `"indirect"` - only if evidence says "indirect subsidiary"
  - `NULL` - if evidence just says "subsidiary" without specifying

**Ownership Type Distribution:**
| Type | Count | Meaning |
|------|-------|---------|
| direct | 4,309 | Explicitly stated as "direct subsidiary" |
| indirect | 134 | Explicitly stated as "indirect subsidiary" |
| NULL | 748 | Documents say "subsidiary" without specifying |

**Part 3: Root Entity Identification**
- ✅ Created migration `016_add_entity_is_root.py` - adds `is_root` boolean to entities
- ✅ Updated Entity model in `schema.py`
- ✅ Ran migration - set `is_root=true` for 198 tier-1 entities with NULL parent
- ✅ Fixed 5 companies missing root flag (HTZ, CAR, HCA, DHR, JPM)

**Entity State Meanings:**
| `is_root` | `parent_id` | Meaning |
|-----------|-------------|---------|
| `true` | `NULL` | Ultimate parent company (199 companies) |
| `false` | UUID | Has known parent |
| `false` | `NULL` | Orphan - parent unknown (105 entities) |

**Final Coverage:**
- Companies with exactly 1 root: 199
- Companies with 0 roots: 0
- Companies with multiple roots: 2 (dual-listed: ATUS, CCL - valid)

**Scripts Created:**
| Script | Purpose |
|--------|---------|
| `fix_ownership_hierarchy.py` | Extract explicit ownership from SEC filings |
| `extract_intermediate_ownership.py` | Find intermediate parent relationships |
| `extract_ownership_from_docs.py` | Extract parent-child from indentures |

**Database Changes:**
- Migration 016: Added `is_root` boolean column to entities table
- Updated `ownership_links.ownership_type` to allow NULL (unknown)

**Key Insight:** SEC filings rarely contain explicit intermediate ownership chains. Documents typically say entities are "subsidiaries of the Company" without specifying the intermediate holding company hierarchy. The extraction now accurately represents what's documented - no inferences.

---

### 2026-01-24 (Session 21) - Document Coverage to 100%

**Objective:** Achieve 100% document coverage - link every debt instrument to its governing legal document.

**Starting Point:** 69.4% coverage (1,779 linked out of 2,563 linkable instruments)

**Part 1: Data Quality Fixes**
- ✅ Created `fix_missing_interest_rates.py` - extracts rates from instrument names
  - Pattern: "4.50% Senior Notes due 2030" → interest_rate = 450 bps
  - Fixed 43 instruments with NULL rates
- ✅ Used existing `fix_missing_maturity_dates.py` - extracted 219 maturity dates
- ✅ Used existing `fix_empty_instrument_names.py` - LLM extracted 56 names from footnotes

**Part 2: Smart Document Matching**
- ✅ Ran `smart_document_matching.py --all --save --limit 50`
- Pattern-based matching searches document content for:
  - Term loan identifiers: "Term A-6", "Term B-3", "Revolving Loan C"
  - Rate + maturity combinations: "4.50%" near "2030"
- Coverage improved to ~70%

**Part 3: Fallback Linking (Major Breakthrough)**
- ✅ Created `link_to_base_indenture.py` - links notes to base indentures
  - Key insight: Most companies have ONE base indenture (dated 1990s-2000s) under which ALL notes are issued
  - Older notes' supplemental indentures were filed years ago; we only have recent filings
  - Solution: Link to base indenture with 60% confidence when specific not found
  - Linked 578 instruments → Coverage jumped to 88.3%
- ✅ Created `link_to_credit_agreement.py` - links loans/revolvers to credit agreements
  - Term loans and revolvers link to most recent credit agreement
  - Linked 190 instruments → Coverage reached 95.2%
- ✅ Enhanced `link_to_base_indenture.py` to handle supplemental indentures
  - Some companies (BIIB) only have supplemental indentures, not base
  - Added fallback: base indenture → supplemental indenture
  - Linked 100 more → Coverage reached 99.7%

**Part 4: Final Cleanup**
- ✅ Extracted documents for companies missing them (MCHP, MNST, NOW, TTD, DIS, REGN, UAL, VAL, FUN, ORCL)
- ✅ Ran `link_to_base_indenture.py` and `link_to_credit_agreement.py` for newly extracted docs
- ✅ Marked remaining generic instruments as `no_document_expected`:
  - C: "long_term_debt" (catch-all bucket)
  - LOW: "Mortgage notes" (secured by mortgages, not public indenture)
  - FOX: "Senior Notes" (generic with no details)
  - DXCM: 3 instruments (company has no CIK, can't extract docs)

**Final Result:** 100% document coverage (2,560 linked / 2,557 linkable)

**Mistakes Made & Corrected:**
1. **Initially suggested marking term loans as "no document expected"** to inflate coverage
   - User correction: "term loan documentation is critical"
   - Real fix: Better matching, not exclusions
2. **LLM matching was truncating documents** - only sending 2,000 chars of 400K+ char credit agreements
   - Fix: Created `smart_document_matching.py` that searches full content first
3. **Filtered out supplemental indentures** in base indenture linker
   - Fix: Added fallback to supplemental when no base exists

**Scripts Created:**
| Script | Purpose |
|--------|---------|
| `fix_missing_interest_rates.py` | Extract rates from instrument names |
| `link_to_base_indenture.py` | Fallback linking to base/supplemental indentures |
| `link_to_credit_agreement.py` | Fallback linking to credit agreements |

**Files Modified:**
- `CLAUDE.md`: Updated stats, added document linking section
- `WORKPLAN.md`: Added Document Coverage completed work section

---

### 2026-01-22 (Session 19) - Collateral Fixes & Company Expansion

**Objective:** Fix collateral mis-tagging and add missing S&P/NASDAQ 100 companies.

**Part 1: Collateral Type Corrections**
- ✅ Identified systematic issue: `general_lien` being used as default instead of industry-specific types
- ✅ Fixed 36 collateral records across asset-heavy industries:
  | Company | Old Type | New Type | Asset Description |
  |---------|----------|----------|-------------------|
  | RIG, DO, NE, VAL | general_lien | equipment | Drilling rigs |
  | CCL, NCLH | general_lien | vehicles | Cruise ships |
  | DAL, AAL | general_lien | vehicles | Aircraft |
  | CZR | general_lien | real_estate | Casino properties |
  | GM, CPRT | general_lien | vehicles | Automotive assets |
  | DISH | general_lien | ip | Spectrum licenses |
  | SWN | general_lien | energy_assets | Oil & gas reserves |
  | MSTR | general_lien | securities | Bitcoin holdings |
  | CRWV | general_lien | equipment | GPU servers |

**Part 2: Final Collateral Distribution**
| Type | Amount | % of Total |
|------|--------|------------|
| vehicles | $116.69B | 5.04% |
| general_lien | $90.97B | 3.93% |
| real_estate | $26.00B | 1.12% |
| equipment | $11.04B | 0.48% |
| securities | $7.27B | 0.31% |
| receivables | $5.53B | 0.24% |
| ip | $3.96B | 0.17% |
| energy_assets | $2.75B | 0.12% |
| cash | $1.57B | 0.07% |
| subsidiary_stock | $1.37B | 0.06% |
| inventory | $1.24B | 0.05% |

**Part 3: Missing Company Analysis**
- ✅ Identified 11 S&P/NASDAQ 100 companies missing from database
- ✅ Cross-referenced with batch_index.py company lists

**Part 4: Extracted Missing Companies**
Added 12 new companies (ORCL was also missing):

| Ticker | Company | Debt Instruments | QA Score |
|--------|---------|-----------------|----------|
| ORCL | Oracle Corporation | 23 | 95% |
| AVGO | Broadcom Inc. | Multiple | 95% |
| CDNS | Cadence Design Systems | Multiple | - |
| CSGP | CoStar Group | Multiple | 90% |
| CTSH | Cognizant | Multiple | 90% |
| DXCM | DexCom Inc. | Multiple | 95% |
| GEV | GE Vernova | Multiple | - |
| GFS | GLOBALFOUNDRIES | Multiple | 88% |
| IDXX | IDEXX Laboratories | Multiple | - |
| NOW | ServiceNow | Multiple | - |
| ORLY | O'Reilly Automotive | Multiple | 90% |
| XOM | Exxon Mobil | Multiple | 85% |

**Part 5: Data Fixes During Extraction**
- ✅ Fixed GFS company name (was "Fruit of the Loom" → "GLOBALFOUNDRIES Inc.")
- ✅ Fixed DXCM benchmark field truncation (too long for VARCHAR(50))
- ✅ Fixed CSGP duplicate entity issue (Ten-X, Inc.)
- ✅ Added collateral for DXCM Credit Facility (general_lien)

**Part 6: Oracle Unsecured Debt Verification**
- Investigated why Oracle has no secured debt
- Confirmed: Oracle's term loans are intentionally unsecured (unusual but correct)
- Strong investment-grade rating allows unsecured borrowing at favorable rates

---

### 2026-01-22 (Session 20) - Authentication & User Management

**Objective:** Implement API key authentication, credit system, and user management.

**Completed:**
1. **Database Migration** (`alembic/versions/013_add_auth_tables.py`)
   - `users` table: id, email, api_key_hash, api_key_prefix, tier, stripe_customer_id, is_active
   - `user_credits` table: credits_remaining, credits_monthly_limit, overage_credits_used, billing_cycle_start
   - `usage_log` table: endpoint, method, credits_used, response_status, response_time_ms, ip_address

2. **SQLAlchemy Models** (`app/models/schema.py`)
   - Added `User`, `UserCredits`, `UsageLog` models with relationships
   - Exported from `app/models/__init__.py`

3. **Auth Utilities** (`app/core/auth.py`)
   - API key generation: `ds_` prefix + 32 hex chars
   - SHA-256 hashing for storage
   - Tier configuration: credits, rate limits, overage rates
   - Endpoint credit costs: 1-3 credits per endpoint
   - Credit check/deduct functions with billing cycle reset
   - `require_auth` and `get_current_user` dependencies

4. **Auth Endpoints** (`app/api/auth.py`)
   | Endpoint | Method | Description |
   |----------|--------|-------------|
   | `/v1/auth/signup` | POST | Create account, returns API key |
   | `/v1/auth/me` | GET | Get current user info |
   | `/v1/auth/credits` | GET | Get credit balance |
   | `/v1/auth/usage` | GET | Get usage history |
   | `/v1/auth/api-keys` | POST | Regenerate API key |
   | `/v1/auth/pricing` | GET | Get pricing info (public) |

5. **Config Updates** (`app/core/config.py`, `.env.example`)
   - Added Stripe config vars (api_key, webhook_secret)
   - Added per-tier rate limits
   - Added auth_bypass for development

6. **Rate Limiting Updates** (`app/core/cache.py`, `app/main.py`)
   - User-based rate limiting via API key hash
   - Per-tier limits (10/min free → 1000/min enterprise)

**Tier Configuration:**
| Tier | Credits/Month | Rate Limit | Overage |
|------|---------------|------------|---------|
| Free | 1,000 | 10/min | Hard cap |
| Starter | 3,000 | 60/min | $0.02/credit |
| Growth | 15,000 | 120/min | $0.015/credit |
| Scale | 50,000 | 300/min | $0.01/credit |
| Enterprise | 1,000,000 | 1000/min | Custom |

**Credit Costs:**
| Endpoint | Credits |
|----------|---------|
| /v1/companies | 1 |
| /v1/bonds | 1 |
| /v1/bonds/resolve | 1 |
| /v1/pricing | 1 |
| /v1/companies/{ticker}/changes | 2 |
| /v1/entities/traverse | 3 |
| /v1/documents/search | 3 |

**Next Steps:**
- Run migration: `alembic upgrade head`
- Test auth endpoints locally
- Integrate Stripe for payment processing
- Add credit deduction to protected endpoints (optional - can use dependency)

---

**Final Statistics:**
- Companies: 178 → 189 (+11)
- Entities: ~3,085 → 5,979
- Debt instruments: ~1,805 → 2,849
- Collateral records: 116 → 230 (100% coverage of senior_secured)
- Guarantees: 4,426 → 4,881

**Files modified:**
- `CLAUDE.md`: Updated database statistics
- `WORKPLAN.md`: Updated status and session log

---

### 2026-01-21 (Session 18 continued) - Collateral Table Implementation

**Objective:** Add collateral tracking to distinguish asset-backed vs guarantee-secured debt.

**Part 1: Schema Changes**
- ✅ Created migration `012_add_collateral_table.py` with:
  - `collateral` table (id, debt_instrument_id, collateral_type, description, priority, estimated_value)
  - `collateral_data_confidence` field on debt_instruments
  - Indexes on debt_instrument_id and collateral_type
- ✅ Added `Collateral` model to schema.py with relationship to DebtInstrument
- ✅ Exported Collateral from models/__init__.py

**Part 2: Collateral Extraction Script**
- ✅ Created `scripts/extract_collateral.py` with LLM-based extraction
- ✅ Improved prompt to infer collateral types from debt names (e.g., "Asset-backed Notes" → vehicles/energy_assets)
- ✅ Supports multiple collateral types per debt instrument

**Part 3: Batch Extraction Results**
- ✅ Ran extraction for 41 companies with secured debt
- ✅ Created 116 collateral records
- ✅ 99/170 secured debt instruments (58%) now have collateral type identified

**Collateral types extracted:**
| Type | Count | Examples |
|------|-------|----------|
| general_lien | 62 | Blanket liens on assets |
| real_estate | 15 | Mortgages, first mortgage bonds |
| receivables | 12 | AR facilities, working capital |
| ip | 9 | SkyMiles program (Delta) |
| vehicles | 6 | Aircraft, cruise ships, auto loans |
| equipment | 3 | Manufacturing equipment |
| inventory | 3 | Retail inventory |
| cash | 2 | Cash collateral |
| subsidiary_stock | 2 | Pledged subsidiary equity |
| securities | 1 | Pledged investments |
| energy_assets | 1 | Solar/energy systems |

**Part 4: API Updates**
- ✅ Added `collateral` array to `/v1/bonds` response
- ✅ Added `collateral_data_confidence` field

**Files Created:**
- `alembic/versions/012_add_collateral_table.py`
- `scripts/extract_collateral.py`

**Files Modified:**
- `app/models/schema.py` - Added Collateral model
- `app/models/__init__.py` - Export Collateral
- `app/api/primitives.py` - Added collateral to bond response

---

### 2026-01-21 (Session 18) - Guarantee Extraction Pipeline

**Objective:** Build and run batch guarantee extraction to populate guarantor data for all companies.

**Part 1: Created Batch Extraction Pipeline**
- ✅ Created `scripts/batch_extract_guarantees.py` combining:
  - Exhibit 22.1/21 fetching from SEC EDGAR
  - LLM-based guarantee extraction from stored indentures/credit agreements
- ✅ Fixed duplicate document section detection bug (used `scalars().first()` instead of `scalar_one_or_none()`)

**Part 2: Ran Batch Extraction**
- ✅ Processed 124 companies with debt and stored documents
- ✅ Found Exhibit 22/21 for 82 companies
- ✅ Created 2,458 new entities from exhibits
- ✅ Created 3,188 new guarantees (2,858 from exhibits, 330 from documents)
- ✅ Retried 10 failed companies (duplicate entity errors, rate limits)

**Part 3: Final Results**
| Metric | Before | After |
|--------|--------|-------|
| Total entities | 3,085 | 6,068 |
| Guarantor entities | ~200 | 3,582 |
| Total guarantees | ~390 | 4,426 |
| Guarantee coverage | 21.6% | 34.7% |
| Senior secured coverage | - | 71% |

**Part 4: Updated Confidence Levels**
- ✅ Ran `update_guarantee_confidence.py` to set data quality indicators
- ✅ 50 verified, 1,915 extracted, 50 partial, 8 unknown

**Errors Encountered:**
- 7 companies: "Multiple rows were found" - duplicate document sections (fixed)
- 2 companies: Gemini rate limit (429) - retried after delay
- 1 company: JSON parse error from LLM

**Files Created:**
- `scripts/batch_extract_guarantees.py` - Batch guarantee extraction

**Files Modified:**
- `scripts/fetch_guarantor_subsidiaries.py` - Fixed duplicate detection bug

---

### 2026-01-20 (Session 17) - Bond Data Enrichment Research

**Objective:** Research how to get individual bond amounts for 12 companies that only report aggregate debt in SEC filings.

**Key Question:** User asked "if we pull by ISIN how do we know which isin to pull" (regarding Finnhub)

**Part 1: Data Source Research**

1. **Finnhub Bond API**:
   - Endpoints: bond/profile, bond/candle, bond/tick, bond/yield-curve
   - Requires ISIN/CUSIP/FIGI to query - NO issuer-based search
   - Returns `amountOutstanding` if you have the identifier
   - Limitation: Must know bond identifiers first

2. **FINRA APIs**:
   - `bondReference` endpoint exists but **NOT in free tier**
   - Free tier (Public Credential): Only market aggregates, 10GB/month limit
   - Paid tier: $1,650/month + S&P CUSIP license required
   - Developer portal: https://developer.finra.org/

3. **OpenFIGI**:
   - Identifier mapping service, not a data source
   - Can convert between ISIN/CUSIP/FIGI but doesn't provide amounts

**Part 2: SEC Filing Approach (Recommended)**

User insight: *"the filings should be used to source the ISINs... the latest ones will only include relevant/active bonds. using FINRA as the source of truth could result in old retired bonds showing up."*

Enhanced `scripts/extract_isins.py`:
- Fixed CUSIP parsing (Apple filings have space: "037833 DX5")
- Tested on AAPL: Found **91 unique CUSIPs** from 29 FWP filings
- Extracts: coupon rate, maturity year, CUSIP, ISIN, principal amount

**Matching Challenge Discovered:**
- Our DB has 7 AAPL bonds: 0.000% 2025, 0.500% 2031, 1.375% 2029, etc.
- Prospectuses show different bonds: 0.550% 2025, 1.650% 2031, 2.200% 2029, etc.
- **Root cause**:
  - 0.000% and 0.500% coupons are typical for Euro-denominated bonds
  - FWP filings with CUSIPs are for USD issuances only
  - EUR bonds use ISINs (not CUSIPs) and often don't appear in FWP filings

**Database Check:**
- 0/1812 debt instruments have CUSIPs populated
- 0/1812 have ISINs populated
- Columns exist (`cusip`, `isin`) but not being filled during extraction

**Recommendation:**
- Accept "aggregate debt only" for MVP (leverage ratios work from financials)
- The `extract_isins.py` script works for extracting CUSIPs from USD bond prospectuses
- Matching logic needs refinement to handle currency differences

**Files Modified:**
- `scripts/extract_isins.py`: Fixed CUSIP regex for spaced format, improved extraction
- `WORKPLAN.md`: Updated Priority 3 with research findings

---

### 2026-01-20 (Session 16) - Scale Detection Fixes & Leverage Expansion

**Part 1: QA Agent Improvements**
- ✅ Changed debt verification from FAIL to WARN when all instruments have NULL amounts
- ✅ Added pre-check to catch NULL amounts before LLM verification
- ✅ Root cause: Some companies (AAPL, etc.) only report aggregate debt, not per-tranche amounts

**Part 2: Financial Scale Detection Fixes**
Major improvements to `detect_filing_scale()` in `financial_extraction.py`:
- ✅ Added `$000` notation pattern (common for "in thousands")
- ✅ Added "In thousands of U.S. dollars" pattern
- ✅ Changed Priority 1 search to check ALL header occurrences (not just first, which was often TOC)
- ✅ Reduced search window from 3000 to 500 chars after header (scale is right after title)
- ✅ Reverted default to "dollars" with warning (explicit detection required)

**Part 3: Re-extraction of Scale Error Companies**
- ✅ Re-extracted TTM financials for: ACN, ROST, SNPS, TTD, CHS
- ✅ Recomputed metrics for all affected companies
- ✅ All now show correct values (e.g., ACN: $18.7B quarterly revenue, not $18.7T)

**Part 4: Batch Script Enhancements**
- ✅ Added `--tickers` flag to `batch_index.py` for comma-separated list
- ✅ Added `--force` flag to override skip-existing behavior
- ✅ Integrated TTM extraction into main `extract_iterative.py` pipeline

**Results:**
- Leverage ratio coverage: **87.1%** (155/178) - up from 57%
- All known scale errors fixed
- QA now properly handles aggregate-only debt disclosure

**Files Modified:**
- `app/services/qa_agent.py`: NULL amount handling
- `app/services/financial_extraction.py`: Scale detection improvements
- `scripts/batch_index.py`: Added --tickers and --force flags
- `scripts/extract_iterative.py`: TTM integration (from previous session)

**Temp Files Cleaned:**
- Removed `scripts/explore_aapl_debt.py`
- Removed `explore_output.txt`

---

### 2026-01-18 (Session 15) - TTM Financial Extraction

**Key Change**: Implemented TTM (Trailing Twelve Months) financial extraction for more accurate leverage ratios.

**Problem Identified**: Single-quarter EBITDA annualized (multiplied by 4) was inaccurate for companies with seasonal revenue or volatile quarters.

**Solution**: Extract all 4 quarters separately and sum them for TTM metrics.

**Files modified:**
- `app/services/financial_extraction.py`:
  - Added `extract_ttm_financials()` function to fetch 3 10-Qs + 1 10-K
  - Fixed `determine_fiscal_period()` to use `periodOfReport` (actual period end) instead of `filedAt` (SEC filing date)
  - Added `filing_data` parameter to `extract_financials()` to accept pre-fetched filing metadata
- `scripts/extract_financials.py`:
  - Added `--ttm` flag for TTM extraction mode
  - Added `extract_ttm()` function with TTM summary output
- `scripts/recompute_metrics.py`:
  - Added `get_ttm_financials()` function to fetch last 4 quarters from DB
  - Updated leverage calculation to sum TTM EBITDA from available quarters
  - Falls back to annualizing if fewer than 4 quarters available

**Usage:**
```bash
# Extract TTM financials (recommended)
python scripts/extract_financials.py --ticker CHTR --ttm --save-db

# Recompute metrics using TTM data
python scripts/recompute_metrics.py --ticker CHTR
```

**Testing:** Verified with CHTR - extracted Q3 2025, Q2 2025, Q1 2025, Q4 2024.

**Known Limitation:** 10-K contains full year figures, not just Q4. For most accurate TTM, would need to compute Q4 = 10-K annual - 9-month YTD. Current implementation uses 10-K as Q4 proxy which slightly overstates TTM totals.

**Documentation Updated:**
- CLAUDE.md: Added TTM extraction section, updated endpoint count to 8, added new key files
- README.md: Added TTM commands to extraction section, updated endpoint count to 8

---

### 2026-01-18 (Session 14) - Deployment & Financial Extraction Improvements
**Part 1: Deployed to Railway**
- ✅ Committed and pushed documentation updates
- ✅ All 8 primitives API endpoints now live in production

**Part 2: Financial Extraction Improvements**
- ✅ Enhanced `recompute_metrics.py` to compute EBITDA from components:
  - Priority: Direct EBITDA > Computed (OpInc + D&A) > Operating Income alone
- ✅ Enhanced `financial_extraction.py` with improved D&A extraction:
  - Added detailed prompt guidance for finding D&A in cash flow statement
  - Added `income_tax_expense` field to extraction schema
  - Added cash flow section keywords to capture D&A
  - Instruct LLM to NOT compute EBITDA (we compute it ourselves)

**Metrics Coverage:**
| Metric | Coverage |
|--------|----------|
| Leverage ratio | 101/177 (57%) |
| Net leverage | 143/177 (81%) |
| Interest coverage | 132/177 (75%) |
| Leveraged loans (>4x) | 21 companies |

**Known Issues:**
- Some companies have debt instrument scale errors (debt instruments >> financial statement debt)
- Affected: INTU, APH, BX, BIIB, ABBV, ADI, SNPS, NCLH (instruments 4-20x higher than financials)
- Gemini extraction not reliably capturing cash flow statement data (D&A often missing)
- Would need Claude (API credits depleted) or manual correction

### 2026-01-18 (Session 13) - Documentation & Diff/Changelog Completion
**Part 1: Updated PRIMITIVES_API_SPEC.md**
- ✅ Added `include_metadata` parameter to Primitive 1 (search.companies)
- ✅ Added Primitive 7: batch (`POST /v1/batch`) with full documentation
- ✅ Added Primitive 8: changes (`GET /v1/companies/{ticker}/changes`) with full documentation
- ✅ Updated Summary section to reflect 8 primitives
- ✅ Removed old "Batch Operations (V2)" placeholder

**Part 2: Updated WORKPLAN.md**
- ✅ Added `/v1/companies/{ticker}/changes` to Primitives API Status table
- ✅ Updated Enhancement 2 (Diff/Changelog) status to COMPLETE
- ✅ Updated Implementation Order table to show all 3 enhancements complete

**All Agent-Friendly Enhancements Complete:**
| Enhancement | Status |
|-------------|--------|
| 1. Confidence Scores + Metadata | ✅ |
| 2. Diff/Changelog Endpoints | ✅ |
| 3. Batch Operations | ✅ |

### 2026-01-18 (Session 12) - Agent-Friendly Enhancements Implementation
**Part 1: Enhancement A - Confidence Scores + Metadata**
- ✅ Created migration `alembic/versions/010_add_extraction_metadata.py`
- ✅ Added `ExtractionMetadata` model to `schema.py`
- ✅ Stores: qa_score, extraction_method, timestamps, field_confidence, warnings
- ✅ Added `?include_metadata=true` parameter to `/v1/companies` endpoint
- ✅ Created `scripts/backfill_extraction_metadata.py` for existing data
- ✅ Backfilled metadata for all 177 companies

**Part 2: Enhancement B - Batch Operations**
- ✅ Added `POST /v1/batch` endpoint to `primitives.py`
- ✅ Supports 6 primitives: search.companies, search.bonds, resolve.bond, traverse.entities, search.pricing, search.documents
- ✅ Parallel execution via `asyncio.gather`
- ✅ Max 10 operations per request
- ✅ Independent failures (one error doesn't affect others)
- ✅ Returns per-operation status and total duration_ms

**Part 3: Batch Financial Extraction**
- ✅ Fixed scale detection to search backwards from financial data markers
- ✅ Extracted financials for 176/177 companies (only ATUS missing - no 10-Q filings)
- ✅ Recomputed metrics for all companies with updated leverage ratios

**Database Stats After Session:**
| Table | Rows |
|-------|------|
| companies | 177 |
| entities | 3,085 |
| debt_instruments | 1,805 |
| company_financials | 176 |
| extraction_metadata | 177 |
| document_sections | 5,456 |
| bond_pricing | 30 |

**Files created:**
- `alembic/versions/010_add_extraction_metadata.py`
- `scripts/backfill_extraction_metadata.py`

**Files modified:**
- `app/models/schema.py` - Added ExtractionMetadata model
- `app/models/__init__.py` - Export new model
- `app/api/primitives.py` - Added include_metadata param + batch endpoint
- `app/services/financial_extraction.py` - Fixed scale detection

### 2026-01-18 (Session 11) - Financial Scale Fix & Agent Enhancements Plan
**Part 1: Fixed Financial Extraction Scale Detection**
- ✅ Replaced heuristic-based scale validation with filing-based detection
- ✅ Added `detect_filing_scale()` function that reads "in millions", "in thousands" from SEC filings
- ✅ Prioritizes scale indicators near financial statement headers (balance sheets, income statements)
- ✅ Added `apply_filing_scale()` function to convert raw LLM output to cents
- ✅ Updated prompts to tell LLM to extract raw numbers, not convert units
- ✅ Tested with HD ($41.4B), COST ($67.3B), JNJ ($24B), PFE ($16.7B), MRK ($17.3B) - all correct

**Files modified**:
- `app/services/financial_extraction.py`: Replaced `validate_and_correct_scale()` with `detect_filing_scale()` + `apply_filing_scale()`

**Part 2: Agent-Friendly Enhancements Plan**
- ✅ Analyzed current extraction metadata tracking (QA scores, iterations, models)
- ✅ Identified gap: metadata tracked in-memory but NOT persisted to database
- ✅ Designed three enhancements for AI agent consumption:

| Enhancement | Effort | Priority |
|-------------|--------|----------|
| 1. Confidence Scores + Metadata | Large (3-5 days) | HIGH (1st) |
| 2. Diff/Changelog Endpoints | Medium-Large (2-3 days) | LOW (3rd) |
| 3. Batch Operations | Medium (1-2 days) | MEDIUM (2nd) |

**Key Design Decisions**:
- Enhancement 1: Store field-level metadata in new `extraction_metadata` table, add `?include_metadata=true` param
- Enhancement 2: Use quarterly snapshots (JSONB) for diff comparison, implement later after data accumulates
- Enhancement 3: Pure API layer, no schema changes, parallel execution via asyncio.gather

**Conflicts Identified**:
- Rate limiting needs adjustment for batch ops (count operations not requests)
- None for Enhancement 1 or 2

**Files modified**:
- `WORKPLAN.md`: Added complete specifications for all three enhancements

### 2026-01-18 (Session 10) - Indentures, CIKs & Extraction Quality
**Part 1: Indentures & Credit Agreements Enhancement**
- ✅ Enhanced Document Search to capture full indentures and credit agreements
- ✅ Completed Step 7 (Documentation): Updated CLAUDE.md and PRIMITIVES_API_SPEC.md

**Part 2: Fixed Missing CIKs**
- ✅ Added CIKs for 34 companies that were missing them
- ✅ Looked up CIKs from SEC EDGAR company_tickers.json
- ✅ Manual lookup for 5 companies not in primary list (CHS, DISH, DO, PARA, SWN)
- ✅ All 177 companies now have CIK numbers

**Part 3: Backfilled Document Sections for 34 Companies**
- ✅ Created `scripts/backfill_remaining.py` to batch process companies
- ✅ Successfully backfilled 33 companies (SPG initially failed due to PDF content)
- ✅ Added PDF skip logic to `backfill_document_sections.py`
- ✅ Retried and completed SPG successfully
- **Final stats**: 5,456 total sections across 177 companies

**Part 4: Fixed Gemini Extraction Quality Issues**
- ✅ Added `validate_and_correct_scale()` function to `financial_extraction.py`:
  - Converts string values to integers (Gemini sometimes returns strings)
  - Handles array responses (Gemini sometimes wraps in array)
  - Detects and corrects scale errors (10x, 100x, 1000x, 1M)
  - Uses company-specific thresholds (mega-cap, large-cap, mid-cap)
- ✅ Re-extracted financials for GM, MSFT, DISH, RIG with correct scale
- ✅ Ran recompute_metrics.py to update leverage ratios

**Files modified**:
- `app/services/extraction.py`: EX-4/EX-10 download logic
- `app/services/section_extraction.py`: Added indenture type
- `app/services/financial_extraction.py`: Scale validation/correction
- `scripts/backfill_document_sections.py`: PDF skip, ASCII-safe errors
- `CLAUDE.md`, `docs/PRIMITIVES_API_SPEC.md`: Documentation updates

**Files created**:
- `scripts/backfill_remaining.py`: Batch backfill helper script

**Part 5: Expanded Financials Coverage**
- ✅ Created `scripts/batch_extract_financials.py` for batch extraction
- ✅ Extracted financials for 66 high-debt companies (>$5B debt)
- ✅ All extractions successful with scale validation
- **Final stats**: 80 companies with financials, 47 with leverage ratios
- **Known issue**: Some scale corrections over-corrected (HD, COST) - needs threshold tuning
- Companies with valid high leverage (>4x): ABBV (6.2x), CHTR (4.5x), LUMN (6.8x), SPG (5.7x)

### 2026-01-17 (Session 9) - Document Search Primitive
- ✅ Completed Priority 4: Document Search Primitive (6/7 steps done)
- ✅ Step 1: Created migration `alembic/versions/009_add_document_sections.py`:
  - `document_sections` table with: id, company_id (FK), doc_type, filing_date, section_type, section_title, content, content_length, search_vector (TSVECTOR), sec_filing_url, timestamps
  - GIN index on `search_vector` for FTS performance
  - B-tree indexes on company_id, doc_type, section_type, filing_date
  - Composite index on (company_id, doc_type, section_type)
  - Trigger to auto-compute `search_vector` on INSERT/UPDATE (title weight 'A', content weight 'B')
- ✅ Step 2: Added `DocumentSection` model to `app/models/schema.py`, exported from `__init__.py`
- ✅ Step 3: Created `app/services/section_extraction.py`:
  - Regex patterns for 6 section types: exhibit_21, debt_footnote, mda_liquidity, credit_agreement, guarantor_list, covenants
  - `extract_sections_from_filing()` - extracts sections from filing content
  - `extract_and_store_sections()` - main entry point for ETL integration
  - `store_sections()`, `get_company_sections()`, `delete_company_sections()` - DB operations
- ✅ Step 4: Added `GET /v1/documents/search` endpoint to `app/api/primitives.py`:
  - Full-text search using PostgreSQL `plainto_tsquery` and `ts_rank_cd`
  - Snippet generation with `ts_headline` (highlighted matches)
  - Filters: q (required), ticker, doc_type, section_type, filed_after, filed_before
  - Sorting: -relevance (default), -filing_date, filing_date
  - Field selection, pagination, CSV export, ETag caching
- ✅ Step 5: Integrated into ETL pipeline (`scripts/extract_iterative.py`):
  - Calls `extract_and_store_sections()` after saving extraction to database
  - Uses filings content that's already downloaded
- ✅ Step 6: Created `scripts/backfill_document_sections.py`:
  - `--ticker CHTR` for single company
  - `--batch 20` for companies with pricing data (testing)
  - `--all` for all companies
  - `--dry-run` to preview without saving
- ✅ Applied migration: `alembic upgrade head`
- ✅ Tested backfill with RIG: 7 sections extracted (2 debt_footnote, 2 mda_liquidity, 2 covenants, 1 exhibit_21)
- ✅ Ran Phase 1 backfill: 3 companies (ATUS, CRWV, RIG), 30 total sections
- ✅ Search verified: Queries for "debt", "covenant", "credit facility" all working
- ✅ Full backfill complete: **1,283 sections** across **141 companies**
- **Section breakdown**: mda_liquidity (427), covenants (325), debt_footnote (208), exhibit_21 (178), guarantor_list (95), credit_agreement (50)
- **Skipped**: 35 companies without CIKs, 2 with no sections extracted

### 2026-01-17 (Session 8)
- ✅ Improved entity name normalization in `utils.py`:
  - Handles multiple spaces, "The " prefix, common suffix variations
  - Normalizes Inc/Inc./Corporation, LLC/L.L.C., Ltd/Ltd./Limited, Corp/Corp./Corporation
  - Handles commas before suffixes (e.g., "Foo, Inc." -> "foo inc")
- ✅ Added ETag caching headers to Primitives API:
  - Added `If-None-Match` header support to `/v1/companies`, `/v1/bonds`, `/v1/pricing`
  - Returns 304 Not Modified when data unchanged
  - Adds `ETag` and `Cache-Control: private, max-age=60` headers to responses
  - ETags generated from MD5 hash of response content
- ✅ Implemented rate limiting:
  - 100 requests per minute per IP (sliding window via Redis)
  - Returns 429 with `Retry-After` header when exceeded
  - Health/docs endpoints exempt from rate limiting
  - Adds `X-RateLimit-*` headers to all responses
  - Fails open if Redis unavailable
- ✅ Improved issue_date extraction:
  - Enhanced extraction prompts to emphasize issue_date
  - Added `estimate_issue_date()` function to infer from maturity date + typical tenors
  - Senior notes: 10yr, secured notes: 7yr, term loans: 5-7yr, revolvers: 5yr
  - Auto-estimates on extraction if LLM doesn't provide issue_date
  - Created `scripts/backfill_issue_dates.py` to update existing records
- **Files modified**:
  - `app/services/utils.py`: Enhanced `normalize_name()` function
  - `app/api/primitives.py`: Added ETag helper functions and header support
  - `app/core/cache.py`: Added `check_rate_limit()` function
  - `app/main.py`: Added rate limiting middleware
  - `app/services/tiered_extraction.py`: Enhanced extraction prompts for issue_date
  - `app/services/extraction.py`: Added `estimate_issue_date()` function
- **Files created**:
  - `scripts/backfill_issue_dates.py`: Backfill issue_date for existing records
- ✅ Improved floor_bps extraction for floating rate debt:
  - Enhanced extraction prompts with specific guidance for floor extraction
  - Added examples: "SOFR floor of 0.50%" = floor_bps: 50
  - Note: floor_bps cannot be estimated (unlike issue_date) - must be extracted from filings
  - Current coverage: 15/316 floating rate instruments (4.7%) - will improve on re-extraction
- ✅ Added monitoring and API usage analytics:
  - Created `app/core/monitoring.py` with request tracking via Redis
  - Tracks: total requests, endpoint breakdown, latency buckets, error rates, rate limit hits
  - Hourly metrics (48hr TTL) and daily metrics (30 day TTL)
  - Unique client IP tracking per day
  - Alert checks for high error rate (>5% 5xx) and high rate limiting (>10%)
  - Added `GET /v1/analytics/usage` endpoint with hourly/daily metrics
  - Integrated into `main.py` middleware using fire-and-forget async
- **Files modified**:
  - `app/main.py`: Added monitoring integration to logging and rate limit middlewares
  - `app/api/routes.py`: Added `/v1/analytics/usage` endpoint
- **Files created**:
  - `app/core/monitoring.py`: Monitoring and analytics module

### 2026-01-17 (Session 7)
- ✅ Added CSV export to Primitives API:
  - Added `format=csv` parameter to `/v1/companies`, `/v1/bonds`, `/v1/pricing`
  - CSV flattens nested objects (e.g., `pricing.ytm` -> `pricing_ytm`)
  - Returns `Content-Disposition: attachment` header for download
  - Example: `GET /v1/companies?format=csv&limit=100`
- ✅ Deferred CUSIP/ISIN extraction:
  - Investigated SEC filings - CUSIPs/ISINs rarely disclosed in 10-Ks
  - Found in FWP/prospectus filings but matching to bonds is complex
  - **Decision**: Not needed for MVP - current data (issuer, coupon, maturity) sufficient
- **Files modified**:
  - `app/api/primitives.py`: Added CSV export helpers and format parameter

### 2026-01-17 (Session 6)
- ✅ Fixed ticker/CIK mismatches:
  - Created `scripts/fix_ticker_cik.py` with CIK to ticker mapping for 138 companies
  - Mapped CIK numbers to proper stock tickers (e.g., "0000021344" -> "KO")
  - Updated 137 companies (1 skipped - duplicate Micron entry)
  - Deleted duplicate Micron entry (CIK 0001709048) - empty record with no data
  - All 177 companies now have proper ticker symbols
  - CIK numbers preserved in `Company.cik` field for SEC lookups
- **Files created**:
  - `scripts/fix_ticker_cik.py`: CIK to ticker mapping script

### 2026-01-17 (Session 5)
- ✅ Expanded financials coverage:
  - Extracted financials for: GM, SPG, DISH, OXY, DVN, CZR, RIG, LUMN
  - 10 companies now have valid leverage ratios (see Priority 2 results table)
  - Some extractions had scale issues (GM, DISH, RIG) - Gemini quality problem
  - Anthropic API credits depleted, can't use Claude fallback
- ✅ Investigated Priority 3 (Credit Ratings):
  - Created `scripts/extract_ratings.py` with regex patterns
  - **Finding**: Companies rarely disclose specific S&P/Moody's ratings in SEC filings
  - CHTR, CZR, RIG tested - none had explicit rating disclosures
  - **Decision**: Defer automated extraction, recommend manual entry or paid API
- **Files created**:
  - `scripts/extract_ratings.py`: Rating extraction script (limited utility)
- **Next steps**:
  - Manual rating entry for key bonds (if needed)
  - Consider Priority 4 (document search) or other backlog items

### 2026-01-17 (Session 4)
- ✅ Completed Priority 2: Leverage Ratios (partial coverage)
  - Fixed numeric overflow in `recompute_metrics.py` (weighted_avg_maturity and leverage ratio bounds)
  - Added sanity checks to skip bad data (leverage > 100x indicates extraction error)
  - Successfully ran `recompute_metrics.py` for all 178 companies
  - 5 companies now have leverage ratios: AAL, CCL, CHTR, DAL, HCA
  - GM leverage skipped (bad data from Gemini), but interest coverage calculated
- **Changes made**:
  - `scripts/recompute_metrics.py`: Added bounds checking for all numeric fields
- **Next steps**:
  - Extract financials for more companies using Claude (`--use-claude`)
  - Move to Priority 3 (credit ratings) or expand financials coverage

### 2026-01-16 (Session 3)
- ✅ Started Priority 2: Leverage Ratios
  - Tested `scripts/extract_financials.py` - works correctly
  - Extracted financials for 5 demo companies (CHTR, DAL, HCA, CCL, AAL)
  - Updated `scripts/recompute_metrics.py` to calculate leverage ratios from `CompanyFinancials`
  - Tested: CHTR shows 4.5x leverage, 4.4x interest coverage
- **Issues found**:
  - GM extraction returned wrong scale (Gemini quality issue)
  - MSFT extraction had corrupted EBITDA (overflow)
  - Recommend using `--use-claude` flag for problematic companies
- **Not completed**:
  - Database not updated with leverage ratios yet (need to run `recompute_metrics.py`)
  - More companies need financials extraction
- **Next session**: Run recompute, extract more financials, then Priority 3 (ratings)

### 2026-01-16 (Session 2)
- ✅ Implemented Priority 1: Maturity profile metrics
  - Added maturity bucket calculations to `extraction.py`
  - Added `industry` field to metrics
  - Created `scripts/recompute_metrics.py` for backfilling
  - Ran recompute for all 178 companies
- **Observations**:
  - Many companies have CIK as ticker (data quality issue)
  - Some companies (AAPL, META, etc.) have debt instruments but NULL amounts
  - ~60% of companies show NEAR_MAT flag (debt due within 24 months)

### 2026-01-16 (Session 1)
- Reviewed Primitives API implementation (`primitives.py`)
- Analyzed gaps between API fields and extraction data
- Created this work plan document

---

## ✅ COMPLETED: Structured Covenant Data Extraction & API

> **Status**: COMPLETED on 2026-01-31
> - 1,181 covenants extracted across 201 companies (100% coverage)
> - 92.5% linked to specific debt instruments
> - 100% have source document linkage
> - API endpoints live: `GET /v1/covenants`, `GET /v1/covenants/compare`

### Objective Assessment

#### What We Have (Current State)

| Asset | Count | Description |
|-------|-------|-------------|
| Indentures | 4,862 | Full legal documents, avg 79KB |
| Credit Agreements | 2,929 | Full legal documents, avg 90KB |
| Covenant Sections | 1,515 | Pre-extracted covenant text, avg 38KB |
| Filing Dates | 100% | All documents have filing dates for amendment tracking |
| Debt-Document Links | 2,831 instruments | 70% have "governs" relationship identified |

**Key Finding**: We have filing dates on all documents, which allows us to identify the **most recent governing document** for each instrument - solving the amendment problem.

#### Covenant Coverage in Documents

| Covenant Type | Companies | Coverage | Extraction Difficulty |
|---------------|-----------|----------|----------------------|
| Merger restrictions | 198 | 99% | Easy (boolean) |
| Lien restrictions | 196 | 97% | Easy (boolean) |
| Indebtedness incurrence | 193 | 96% | Medium (thresholds) |
| Dividend restrictions | 192 | 95% | Medium (conditions) |
| Change of control | 158 | 79% | Easy (boolean + trigger) |
| Leverage ratio | 100 | 50% | **Hard** (number extraction) |
| Asset sale restrictions | 99 | 49% | Medium (thresholds) |
| Restricted payments | 66 | 33% | Hard (baskets) |
| Fixed charge coverage | 57 | 28% | **Hard** (number extraction) |
| Interest coverage | 46 | 23% | **Hard** (number extraction) |

#### The Amendment Problem - SOLVED

**Problem**: Multiple documents exist per instrument (base + amendments). How do we know which has current covenants?

**Solution**: We have `filing_date` on all `document_sections` + `relationship_type='governs'` in `debt_instrument_documents`.

Strategy: For each instrument, use the **most recent document with relationship_type='governs'**.

Example for CHTR:
- Term A-7 Loan → 2024-12-09 credit_agreement (most recent)
- 6.550% Senior Secured Notes → 2026-01-14 indenture (most recent)

#### Accuracy Assessment

**High Confidence (80%+ accuracy achievable):**
- Negative covenants: Liens, mergers, asset sales, restricted payments
- Protective covenants: Change of control, make-whole provisions
- Incurrence tests: Debt incurrence ratios
- Classification: Covenant-lite vs. maintenance

**Medium Confidence (60-80% accuracy):**
- Financial covenant thresholds (e.g., "leverage shall not exceed 4.50x")
- Step-down schedules
- Cure periods and grace periods

**Lower Confidence (needs human review):**
- Basket calculations and exceptions
- Complex carve-outs
- Cross-default threshold amounts

#### Recommendation

Implement a **two-tier approach**:

1. **Tier 1 - Structured Extraction (stored in DB)**: Extract high-confidence covenant data via LLM once, store in new `covenants` table
2. **Tier 2 - API Primitives**: Serve structured data via new endpoints + enhanced search for deep-dive

---

### Implementation Plan

#### Phase 1: Database Schema

Create new `covenants` table:

```sql
CREATE TABLE covenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    debt_instrument_id UUID REFERENCES debt_instruments(id),
    company_id UUID REFERENCES companies(id),
    source_document_id UUID REFERENCES document_sections(id),

    -- Covenant identification
    covenant_type VARCHAR(50) NOT NULL,  -- 'financial', 'negative', 'incurrence', 'protective'
    covenant_name VARCHAR(200) NOT NULL, -- 'Maximum Leverage Ratio', 'Restricted Payments', etc.

    -- Financial covenant specifics (nullable for non-financial)
    test_metric VARCHAR(50),      -- 'leverage_ratio', 'interest_coverage', 'fixed_charge'
    threshold_value DECIMAL(10,4), -- e.g., 4.50 for 4.50x leverage
    threshold_type VARCHAR(20),   -- 'maximum', 'minimum'
    test_frequency VARCHAR(20),   -- 'quarterly', 'annual', 'incurrence'

    -- Covenant details
    description TEXT,             -- Brief description
    has_step_down BOOLEAN,        -- Has scheduled tightening
    cure_period_days INT,         -- Grace period before default

    -- Extraction metadata
    extraction_confidence DECIMAL(3,2), -- 0.00-1.00
    extracted_at TIMESTAMP WITH TIME ZONE,
    source_text TEXT,             -- Verbatim text from document

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_covenants_company ON covenants(company_id);
CREATE INDEX idx_covenants_instrument ON covenants(debt_instrument_id);
CREATE INDEX idx_covenants_type ON covenants(covenant_type);
```

#### Phase 2: Extraction Service

Create `app/services/covenant_extraction.py`:

1. **get_governing_document(instrument_id)**: Returns most recent document with `relationship_type='governs'`

2. **extract_covenants_from_document(document_id)**: LLM-based extraction
   - Prompt includes covenant taxonomy
   - Returns structured JSON with confidence scores
   - Cost: ~$0.02-0.05 per document (Gemini)

3. **extract_covenants_for_company(ticker)**: Orchestrates extraction for all instruments

**LLM Prompt Strategy**:
```
Given this credit agreement/indenture, extract the following covenants:

FINANCIAL COVENANTS (with numerical thresholds):
- Maximum Leverage Ratio (Total Debt / EBITDA)
- Maximum First Lien Leverage Ratio
- Minimum Interest Coverage Ratio
- Minimum Fixed Charge Coverage Ratio

NEGATIVE COVENANTS (yes/no + brief description):
- Limitations on Liens
- Limitations on Indebtedness
- Limitations on Restricted Payments
- Limitations on Asset Sales
- Limitations on Affiliate Transactions

INCURRENCE TESTS:
- Debt incurrence ratio threshold
- Secured debt incurrence ratio

PROTECTIVE COVENANTS:
- Change of Control provisions (put price, e.g., 101%)
- Make-whole provisions

Return JSON with confidence scores (0.0-1.0) for each extraction.
```

#### Phase 3: API Endpoints

##### Endpoint 1: `GET /v1/covenants`

Search and filter structured covenant data.

```
GET /v1/covenants?ticker=CHTR&covenant_type=financial
GET /v1/covenants?test_metric=leverage_ratio&max_threshold=5.0
GET /v1/covenants?covenant_name=change_of_control
```

**Response:**
```json
{
  "data": [
    {
      "id": "uuid",
      "ticker": "CHTR",
      "instrument_name": "Term B-5 Loan",
      "cusip": null,
      "covenant_type": "financial",
      "covenant_name": "Maximum First Lien Leverage Ratio",
      "test_metric": "first_lien_leverage",
      "threshold_value": 4.50,
      "threshold_type": "maximum",
      "test_frequency": "quarterly",
      "has_step_down": false,
      "cure_period_days": 30,
      "extraction_confidence": 0.92,
      "source_document_date": "2024-12-09"
    }
  ],
  "meta": {
    "total": 15,
    "covenant_types": ["financial", "negative", "protective"]
  }
}
```

##### Endpoint 2: `GET /v1/covenants/compare`

Compare covenants across multiple instruments/companies.

```
GET /v1/covenants/compare?ticker=CHTR,ATUS,LUMN&test_metric=leverage_ratio
```

##### Endpoint 3: `GET /v1/covenants/headroom` (Future)

Calculate covenant headroom based on current financials.

```
GET /v1/covenants/headroom?ticker=CHTR
```

**Response:**
```json
{
  "ticker": "CHTR",
  "financial_covenants": [
    {
      "covenant_name": "Maximum First Lien Leverage Ratio",
      "threshold": 4.50,
      "current_ratio": 3.82,
      "headroom": 0.68,
      "headroom_pct": 15.1,
      "cushion_ebitda_drop": 850000000000
    }
  ]
}
```

#### Phase 4: Extraction Pipeline

```bash
# Extract covenants for single company
python -m app.services.covenant_extraction --ticker CHTR --save-db

# Batch extraction (prioritize companies with credit facilities)
python -m app.services.covenant_extraction --all --save-db --limit 50

# Re-extract after new filings
python -m app.services.covenant_extraction --ticker CHTR --force
```

**Estimated costs:**
- Per company: ~$0.05-0.15 (depending on # of instruments)
- All 201 companies: ~$15-30

#### Phase 5: Data Quality

1. **Confidence thresholds**: Only store covenants with confidence >= 0.7
2. **Human review queue**: Flag low-confidence extractions for review
3. **Source linking**: Always store `source_text` verbatim for verification
4. **Amendment tracking**: When new filings arrive, flag existing covenants as potentially stale

---

### Files to Create/Modify

#### New Files
- `app/models/schema.py` - Add `Covenant` model
- `app/services/covenant_extraction.py` - Extraction service
- `alembic/versions/XXX_add_covenants_table.py` - Migration

#### Modified Files
- `app/api/primitives.py` - Add `/v1/covenants` endpoint
- `app/models/__init__.py` - Export new model
- `README.md`, `CLAUDE.md` - Document new primitive

---

### Verification Plan

1. **Unit test**: Extract covenants from known document, verify thresholds match
2. **Spot check**: Compare extracted leverage ratio to what analyst would read
3. **Coverage test**: Run on 20 companies, measure extraction success rate
4. **API test**: Query `/v1/covenants?ticker=CHTR`, verify response structure

---

### Risk Assessment

| Risk | Mitigation |
|------|------------|
| LLM hallucination | Store source_text, require confidence threshold |
| Amendment not captured | Use filing_date to get most recent governing doc |
| Complex covenant language | Start with financial covenants (most structured) |
| Cost overrun | Use Gemini (cheap), batch efficiently |

---

### Estimated Effort

- Schema + migration: 1 hour
- Extraction service: 3-4 hours
- API endpoint: 2 hours
- Initial extraction (50 companies): 1 hour runtime, ~$5 cost
- Testing + refinement: 2 hours

**Total: ~8-10 hours of development**

---

### Open Questions

1. Should we extract covenants at the **company level** (credit agreements often cover multiple facilities) or **instrument level**?
   - Recommendation: Company level for credit agreements, instrument level for indentures

2. How do we handle covenant-lite loans (no maintenance covenants)?
   - Flag as `is_covenant_lite: true` at instrument level

3. Should headroom calculation be real-time or pre-computed?
   - Start with pre-computed in `company_metrics`, add real-time later

---

### Final Architecture

The extraction service (`app/services/covenant_extraction.py`) uses a three-layer architecture for modularity and testability:

#### Layer 1: Pure Functions (for unit testing, no dependencies)

| Function | Description |
|----------|-------------|
| `extract_covenant_sections(text)` | Regex extraction of covenant-relevant text from documents |
| `parse_covenant_response(data)` | Parse LLM JSON response → `ParsedCovenant` objects |
| `fuzzy_match_debt_name(name1, name2)` | Match extracted debt names to known instruments |

#### Layer 2: Prompt Building (no DB, can test with mocked LLM)

| Function | Description |
|----------|-------------|
| `build_covenant_prompt(content, ticker, instruments)` | Builds structured LLM prompt with covenant taxonomy, metric definitions, and JSON schema |

#### Layer 3: Database Operations (production use)

| Function | Description |
|----------|-------------|
| `get_governing_document(session, instrument_id)` | Get most recent document with `relationship_type='governs'` |
| `get_company_covenant_documents(session, company_id)` | Get covenant-related documents (credit agreements, indentures, covenant sections) |
| `get_document_instrument_map(session, company_id)` | Get document → instrument mappings for linkage |
| `CovenantExtractor` class | Full extraction pipeline using `BaseExtractor` pattern |
| `extract_covenants(session, company_id, ticker)` | Convenience function for extraction |

#### CLI Usage

```bash
# Single company
python -m app.services.covenant_extraction --ticker CHTR

# Batch extraction
python -m app.services.covenant_extraction --all --limit 50

# Force re-extraction
python -m app.services.covenant_extraction --ticker CHTR --force

# Post-process to link existing covenants to instruments
python scripts/link_covenants_to_instruments.py --all
```

#### Instrument Linkage

Covenants are linked to debt instruments via:
1. **Explicit matching**: LLM extracts `debt_name` field, fuzzy-matched to instruments
2. **Document-based**: Uses `debt_instrument_documents` (governs relationships) to link covenants from credit agreements → loans, indentures → bonds
3. **Post-processing**: `scripts/link_covenants_to_instruments.py` can backfill linkage for existing covenants

Current linkage: **92.5%** of covenants linked to specific instruments.
