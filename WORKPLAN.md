# DebtStack Work Plan

Last Updated: 2026-01-22

## Current Status

**Database**: 189 companies | 5,979 entities | 2,849 debt instruments | 30 priced bonds | 5,750 document sections | 600+ financials | 4,881 guarantees | 230 collateral records
**Deployment**: Live at `https://credible-ai-production.up.railway.app`
**Infrastructure**: Railway + Neon PostgreSQL + Upstash Redis (complete)
**Leverage Coverage**: 155/189 companies (82%) - good coverage across all sectors
**Guarantee Coverage**: ~1,500/2,849 debt instruments (~53%) - up from 34.7% on 2026-01-21
**Collateral Coverage**: 230/230 senior_secured instruments (100%) with collateral type identified

## Recent Completed Work

### January 2026
- [x] Migrated to Neon PostgreSQL + Upstash Redis
- [x] Deployed to Railway
- [x] Built Primitives API (5 of 6 endpoints)
- [x] Removed GraphQL (REST primitives cover all use cases)
- [x] Simplified `primitives.py` codebase
- [x] Guarantee extraction pipeline (Exhibit 22 + indenture parsing)
- [x] Collateral table and extraction (real estate, equipment, receivables, etc.)

### Primitives API Status
| Endpoint | Status | Notes |
|----------|--------|-------|
| `GET /v1/companies` | ✅ Done | Field selection, filtering, sorting, `?include_metadata=true` |
| `GET /v1/bonds` | ✅ Done | Pricing joins, guarantor counts, collateral array |
| `GET /v1/bonds/resolve` | ✅ Done | CUSIP/ISIN/fuzzy matching |
| `POST /v1/entities/traverse` | ✅ Done | Graph traversal for guarantors, structure |
| `GET /v1/pricing` | ✅ Done | FINRA TRACE data |
| `GET /v1/documents/search` | ✅ Done | Full-text search across SEC filings |
| `POST /v1/batch` | ✅ Done | Batch operations (up to 10 parallel) |
| `GET /v1/companies/{ticker}/changes` | ✅ Done | Diff/changelog against historical snapshots |

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
**Status**: ⬜ TODO
**Effort**: 1 day

Build automated QC checks to identify data inconsistencies.

**Checks to implement:**
| Check | Description | Threshold |
|-------|-------------|-----------|
| Debt instrument vs. financial mismatch | Sum of debt instruments vs. total_debt in financials | >2x difference = flag |
| Entity count sanity | Companies with 0 entities | Should have at least 1 |
| Debt without issuer | Debt instruments with NULL issuer_id | Should be 0 |
| Orphan guarantees | Guarantees referencing non-existent entities | Should be 0 |
| Maturity date sanity | Bonds with maturity < today but is_active=true | Flag for review |
| Duplicate debt instruments | Same name + issuer + maturity | Flag for dedup |
| Missing debt amounts | Debt instruments with NULL principal/outstanding | Flag for re-extraction |

**Deliverable:** Create `scripts/qc_audit.py` that runs all checks and outputs report.

---

### Priority 4: API Edge Case Testing
**Status**: ⬜ TODO
**Effort**: 0.5 day

Test API robustness before public launch.

**Test cases:**
| Test | Endpoint | Expected |
|------|----------|----------|
| Empty ticker list | `GET /v1/companies?ticker=` | Return all (no filter) |
| Invalid ticker | `GET /v1/companies?ticker=XXXXX` | Empty result, not error |
| Invalid field | `GET /v1/companies?fields=fake_field` | 400 with valid fields list |
| Large result set | `GET /v1/bonds?limit=100` | Paginated response |
| Malformed traverse | `POST /v1/entities/traverse` with bad JSON | 422 validation error |
| Non-existent CUSIP | `GET /v1/bonds/resolve?cusip=000000000` | Empty matches |
| SQL injection attempt | `GET /v1/companies?ticker='; DROP TABLE--` | Safe, no injection |
| Rate limit | 101 requests in 1 minute | 429 on 101st |

**Deliverable:** Create `scripts/test_api_edge_cases.py` or add to test suite.

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
- [ ] QC audit script created and passing
- [ ] API edge cases tested
- [ ] No critical data inconsistencies
- [ ] Decide on NULL debt amounts handling (12 companies)

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
| Metrics computation | `app/services/extraction.py` (lines 1391-1512) |
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
