# DebtStack Work Plan

Last Updated: 2026-01-18

## Current Status

**Database**: 178 companies | 3,085 entities | 1,805 debt instruments | 30 priced bonds | **5,456 document sections**
**Deployment**: Live at `https://credible-ai-production.up.railway.app`
**Infrastructure**: Railway + Neon PostgreSQL + Upstash Redis (complete)

## Recent Completed Work

### January 2026
- [x] Migrated to Neon PostgreSQL + Upstash Redis
- [x] Deployed to Railway
- [x] Built Primitives API (5 of 6 endpoints)
- [x] Removed GraphQL (REST primitives cover all use cases)
- [x] Simplified `primitives.py` codebase

### Primitives API Status
| Endpoint | Status | Notes |
|----------|--------|-------|
| `GET /v1/companies` | ✅ Done | Field selection, filtering, sorting |
| `GET /v1/bonds` | ✅ Done | Pricing joins, guarantor counts |
| `GET /v1/bonds/resolve` | ✅ Done | CUSIP/ISIN/fuzzy matching |
| `POST /v1/entities/traverse` | ✅ Done | Graph traversal for guarantors, structure |
| `GET /v1/pricing` | ✅ Done | FINRA TRACE data |
| `GET /v1/documents/search` | ✅ Done | Full-text search across SEC filings |

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
**Status**: 📋 PLANNED
**Effort**: Large (3-5 days)
**Priority**: HIGH (implement first)

Add metadata to every field in API responses:
- `confidence_score` (0-1 float) - How confident we are in the extracted value
- `extraction_method` (enum) - "direct_parse", "llm_inference", "calculated"
- `last_updated` (ISO timestamp) - When this field was last updated
- `source_filing` (URL) - Direct SEC link to specific section

**Example Response:**
```json
{
  "total_debt": {
    "value": 5000000000,
    "confidence": 0.95,
    "last_updated": "2024-01-15T10:30:00Z",
    "source_filing": "https://sec.gov/Archives/edgar/data/.../10-k.htm#debt",
    "extraction_method": "direct_parse"
  }
}
```

**Implementation Steps:**

| Step | Description | Files |
|------|-------------|-------|
| 1.1 | Create migration for `extraction_metadata` table | `alembic/versions/010_add_extraction_metadata.py` |
| 1.2 | Add `FieldMetadata` Pydantic model | `app/models/metadata.py` (new) |
| 1.3 | Update extraction pipeline to track confidence | `app/services/iterative_extraction.py` |
| 1.4 | Store QA scores and extraction method per-field | `app/services/qa_agent.py` |
| 1.5 | Add `?include_metadata=true` parameter to primitives | `app/api/primitives.py` |
| 1.6 | Create response wrapper that adds metadata | `app/api/primitives.py` |
| 1.7 | Link entities/debt to source DocumentSection | `app/models/schema.py` |

**Database Changes:**
```sql
-- New table for extraction metadata
CREATE TABLE extraction_metadata (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES companies(id),
    entity_type VARCHAR(50),  -- 'company', 'entity', 'debt', 'financial'
    entity_id UUID,
    field_name VARCHAR(100),
    confidence_score NUMERIC(3,2),
    extraction_method VARCHAR(50),
    source_section_id UUID REFERENCES document_sections(id),
    extracted_at TIMESTAMP,
    extractor_model VARCHAR(50),
    qa_check_passed BOOLEAN
);

-- Add source linking to existing tables
ALTER TABLE entities ADD COLUMN source_section_id UUID REFERENCES document_sections(id);
ALTER TABLE debt_instruments ADD COLUMN source_section_id UUID REFERENCES document_sections(id);
```

**Confidence Score Mapping:**
| Source | Confidence |
|--------|------------|
| Direct parse from table | 0.95-1.0 |
| LLM extraction (passed QA) | 0.80-0.95 |
| LLM extraction (failed QA, fixed) | 0.60-0.80 |
| Calculated from other fields | 0.85-0.95 |
| Estimated/inferred | 0.40-0.60 |
| Manual entry | 1.0 |

**Conflicts**: None identified
**Dependencies**: Requires `issue_date_estimated` pattern (already implemented)

---

### Enhancement 2: Diff/Changelog Endpoints
**Status**: 📋 PLANNED
**Effort**: Medium-Large (2-3 days)
**Priority**: LOW (implement last)

New endpoint: `GET /v1/companies/{ticker}/changes?since={iso_date}`

Returns deltas since specified date:
- `new_bonds`: Bonds issued after the date
- `covenant_amendments`: Changes to existing covenants
- `entity_restructurings`: Subsidiary additions/removals
- `pricing_changes`: Significant yield/price movements (>50bps)

**Example Response:**
```json
{
  "ticker": "RIG",
  "since": "2024-01-01",
  "changes": {
    "new_bonds": [
      {"name": "8.75% Senior Notes due 2030", "issue_date": "2024-03-15", "principal": 500000000}
    ],
    "covenant_amendments": [
      {"description": "Leverage covenant waived through 2024", "filing_date": "2024-02-10"}
    ],
    "entity_restructurings": [
      {"type": "addition", "entity_name": "Transocean Holdings LLC", "date": "2024-01-20"}
    ],
    "pricing_changes": [
      {"cusip": "893830AK8", "old_ytm": 850, "new_ytm": 920, "change_bps": 70}
    ]
  },
  "snapshot_date": "2024-06-15"
}
```

**Implementation Steps:**

| Step | Description | Files |
|------|-------------|-------|
| 2.1 | Create `company_snapshots` table for quarterly snapshots | `alembic/versions/012_add_company_snapshots.py` |
| 2.2 | Create snapshot service | `app/services/snapshot_service.py` (new) |
| 2.3 | Add `/v1/companies/{ticker}/changes` endpoint | `app/api/primitives.py` |
| 2.4 | Implement diff logic for each change type | `app/services/diff_service.py` (new) |
| 2.5 | Create quarterly snapshot cron job | `scripts/create_quarterly_snapshots.py` |

**Database Changes:**
```sql
CREATE TABLE company_snapshots (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES companies(id),
    snapshot_date DATE NOT NULL,
    snapshot_type VARCHAR(20),  -- 'quarterly', 'monthly', 'manual'

    -- Snapshot data (denormalized JSON for fast comparison)
    entities_snapshot JSONB,        -- All entities at point in time
    debt_snapshot JSONB,            -- All debt instruments
    metrics_snapshot JSONB,         -- CompanyMetrics at point in time

    created_at TIMESTAMP,
    UNIQUE(company_id, snapshot_date)
);

CREATE INDEX idx_snapshots_company_date ON company_snapshots(company_id, snapshot_date);
```

**Conflicts**: None identified
**Dependencies**: Enhancement 1 (confidence scores) helpful for tracking data provenance

---

### Enhancement 3: Batch Operations
**Status**: 📋 PLANNED
**Effort**: Medium (1-2 days)
**Priority**: MEDIUM (implement second)

New endpoint: `POST /v1/batch`

Accepts array of operations in single request:
```json
{
  "operations": [
    {"primitive": "search.bonds", "params": {"ticker": "TSLA"}},
    {"primitive": "traverse.entities", "params": {"ticker": "TSLA"}},
    {"primitive": "search.pricing", "params": {"cusip": "88160RAG3"}}
  ]
}
```

Returns array of results with per-operation status:
```json
{
  "results": [
    {"status": "success", "data": {...}, "operation_id": 0},
    {"status": "success", "data": {...}, "operation_id": 1},
    {"status": "error", "error": {"code": "NOT_FOUND", "message": "..."}, "operation_id": 2}
  ],
  "meta": {
    "total_operations": 3,
    "successful": 2,
    "failed": 1,
    "duration_ms": 234
  }
}
```

**Implementation Steps:**

| Step | Description | Files |
|------|-------------|-------|
| 3.1 | Define batch request/response schemas | `app/api/primitives.py` |
| 3.2 | Create primitive dispatcher | `app/services/batch_service.py` (new) |
| 3.3 | Implement parallel execution with asyncio.gather | `app/services/batch_service.py` |
| 3.4 | Add `/v1/batch` endpoint | `app/api/primitives.py` |
| 3.5 | Add batch-specific rate limiting (count ops, not requests) | `app/core/cache.py` |
| 3.6 | Document batch endpoint | `docs/PRIMITIVES_API_SPEC.md` |

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
- Operations executed in parallel where possible
- Total batch timeout: 30 seconds
- Rate limit: Each operation counts against rate limit

**Conflicts**: Rate limiting needs adjustment to count operations not requests
**Dependencies**: None

---

## Implementation Order (Recommended)

| Order | Enhancement | Rationale |
|-------|-------------|-----------|
| 1st | **Confidence Scores + Metadata** | Foundation for data quality transparency. Already have `issue_date_estimated` pattern. Most valuable for agent trust. |
| 2nd | **Batch Operations** | Quick win, high agent utility. No schema changes, pure API layer. |
| 3rd | **Diff/Changelog** | Requires historical data accumulation. Start snapshotting now, implement query later. |

---

## Backlog

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
