# DebtStack Work Plan

Last Updated: 2026-01-17

## Current Status

**Database**: 177 companies | 3,085 entities | 1,805 debt instruments | 30 priced bonds
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
| `GET /v1/companies/{ticker}/documents` | ❌ Not started | Phase 2 feature |

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
**Status**: ⏸️ DEFERRED - Phase 2
**Effort**: Large - new feature

The 6th primitive `search.documents` isn't implemented. Would enable:
- "Get RIG's debt structure from Note 9"
- "Find all mentions of 'covenant' in recent 10-Ks"

**Requirements**:
1. New table for document sections/chunks
2. Section extraction (Note 9, MD&A, Risk Factors)
3. Full-text search (PostgreSQL FTS or external)
4. New API endpoint

**Defer to Phase 2**.

---

## Backlog

### Data Quality
- [ ] Validate CUSIP mappings against FINRA (deferred - CUSIPs not needed for MVP)
- [x] Fix any ticker/CIK mismatches - Done 2026-01-17 (137 companies updated)
- [ ] Clean up entity name normalization edge cases

### API Enhancements
- [x] Add CSV export option for bulk data - Done 2026-01-17
- [ ] Add ETag caching headers
- [ ] Rate limiting implementation

### Extraction Pipeline
- [ ] Extract ISINs from filings (deferred - not needed for MVP)
- [ ] Extract issue_date more reliably
- [ ] Extract floor_bps for floating rate debt

### Infrastructure
- [x] Clean up temp directories (`tmpclaude-*`) - Done 2026-01-17, added to .gitignore
- [ ] Set up monitoring/alerting
- [ ] Add API usage analytics

---

## File Reference

| Purpose | File |
|---------|------|
| Primitives API | `app/api/primitives.py` |
| Legacy REST API | `app/api/routes.py` |
| Extraction with QA | `app/services/iterative_extraction.py` |
| Metrics computation | `app/services/extraction.py` (lines 1391-1512) |
| Database schema | `app/models/schema.py` |
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
