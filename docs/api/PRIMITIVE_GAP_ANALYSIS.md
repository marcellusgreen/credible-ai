# DebtStack Primitive-Based API Gap Analysis

## Overview

This document analyzes the current DebtStack extraction pipeline against 6 core primitives needed to answer 13 key user questions. For V1 MVP, we focus on **data provision** - delivering raw extracted data that enables users to draw their own conclusions.

---

## The 6 Core Primitives

| Primitive | Purpose | Description |
|-----------|---------|-------------|
| `search.companies()` | Discovery | Find companies by sector, debt metrics, risk flags |
| `search.bonds()` | Bond Discovery | Find bonds by yield, spread, seniority, maturity |
| `search.documents()` | Document Access | Access source filings (10-K, 10-Q, credit agreements) |
| `traverse.entities()` | Structure Navigation | Navigate corporate hierarchy, find guarantors |
| `search.pricing()` | Market Data | Get prices, yields, spreads for bonds |
| `resolve.bond()` | Bond Lookup | Get full details for a specific bond |

---

## Gap Analysis by Primitive

### 1. `search.companies()` - Company Discovery

**Current Status: ✅ FULLY SUPPORTED**

**What IS Extracted:**
- ✅ Company name, ticker, CIK
- ✅ Sector classification
- ✅ Total debt, secured debt, unsecured debt
- ✅ Entity count, guarantor count
- ✅ Risk flags: `has_structural_sub`, `has_floating_rate`, `has_near_term_maturity`
- ✅ Subordination risk scoring

**API Endpoints:**
- `GET /v1/search/companies` - Full filtering by sector, debt range, risk flags
- `GET /v1/companies` - List all with metrics
- `GET /v1/analytics/sectors` - Sector-level aggregations

**Gaps:**
- ❌ Industry classification often NULL (extraction prompt doesn't prioritize)
- ❌ Ratings (S&P, Moody's) not extracted - external data source needed
- 🔶 Leverage ratios only populated when financials extracted separately

**Priority:** LOW - Core functionality complete

---

### 2. `search.bonds()` - Bond Discovery

**Current Status: ✅ MOSTLY SUPPORTED**

**What IS Extracted:**
- ✅ Bond name, instrument type, seniority
- ✅ Security type (first_lien, second_lien, unsecured)
- ✅ Interest rate (fixed or floating spread)
- ✅ Maturity date
- ✅ Outstanding amount, principal, commitment
- ✅ Issuer entity reference
- ✅ Guarantor list
- 🔶 CUSIP/ISIN - extraction prompt updated, ~10% coverage currently

**API Endpoints:**
- `GET /v1/search/debt` - Full filtering: seniority, yield, spread, maturity, sector, guarantors
- `GET /v1/companies/{ticker}/debt` - Company-specific bonds
- `GET /v1/companies/{ticker}/maturity-waterfall` - Maturity profile

**Gaps:**
- ❌ **Covenant data not extracted** - Critical for credit analysis
  - Financial covenants (leverage tests, coverage tests)
  - Incurrence covenants
  - Basket sizes
- ❌ Call schedule not extracted - Important for yield calculations
- ❌ Original issue discount (OID) not captured
- 🔶 CUSIP coverage low (~10%) - Need CUSIP mapping job

**Priority:** HIGH - Covenants are critical for credit analysis

---

### 3. `search.documents()` - Document Access

**Current Status: ❌ NOT SUPPORTED**

**What IS Extracted:**
- ✅ `source_filing` URL stored in CompanyCache
- ✅ Filing date tracked

**What is MISSING:**
- ❌ **Raw filing content not stored** - Discarded after extraction
- ❌ **No document search** - Can't search within filings
- ❌ **Exhibit catalog not exposed** - Can't list/access Exhibit 10 (credit agreements)
- ❌ **Section extraction not available** - Can't pull specific sections (MD&A, debt footnotes)

**API Endpoints Needed:**
- `GET /v1/companies/{ticker}/documents` - List available filings
- `GET /v1/companies/{ticker}/documents/{doc_id}` - Get document content
- `GET /v1/search/documents` - Search across filings (keyword, section type)

**Priority:** CRITICAL - Users need source verification

**Implementation Options:**
1. **Store raw filings in S3/blob storage** - Full text searchable
2. **Cache key sections only** - Debt footnotes, Exhibit 21, credit agreements
3. **On-demand fetch** - Re-download from SEC when requested

---

### 4. `traverse.entities()` - Structure Navigation

**Current Status: ✅ FULLY SUPPORTED**

**What IS Extracted:**
- ✅ Full entity hierarchy with parent_id relationships
- ✅ Entity types (holdco, opco, subsidiary, spv, jv, finco, vie)
- ✅ Jurisdiction and formation type
- ✅ Guarantor status, borrower status
- ✅ Restricted/unrestricted status
- ✅ VIE flags and consolidation method
- ✅ JV relationships with partner names
- ✅ Ownership percentages

**API Endpoints:**
- `GET /v1/companies/{ticker}/structure` - Flat entity list with debt
- `GET /v1/companies/{ticker}/hierarchy` - Nested tree view
- `GET /v1/companies/{ticker}/ownership` - JVs and complex ownership
- `GET /v1/companies/{ticker}/entities` - Filtered entity list
- `GET /v1/companies/{ticker}/entities/{id}` - Entity detail with children
- `GET /v1/search/entities` - Cross-company entity search

**Gaps:**
- 🔶 Some companies missing Exhibit 21 → incomplete subsidiary list
- 🔶 Ownership percentages sometimes missing (default to 100%)

**Priority:** LOW - Core functionality complete

---

### 5. `search.pricing()` - Market Data

**Current Status: 🔶 PARTIALLY SUPPORTED**

**What IS Available:**
- ✅ Estimated pricing using Treasury + credit spreads
- ✅ YTM calculations
- ✅ Spread to treasury
- ✅ Staleness tracking
- ✅ Price source indicator ("estimated" vs "TRACE")

**API Endpoints:**
- `GET /v1/companies/{ticker}/pricing` - All bond prices for company
- `GET /v1/companies/{ticker}/debt/{id}/pricing` - Specific bond pricing
- `GET /v1/search/debt?min_ytm_bps=X&max_spread_bps=Y` - Yield/spread filtering

**Gaps:**
- ❌ **No real TRACE data** - Finnhub requires premium ($300/3mo)
- ❌ **No historical prices** - Only current snapshot
- ❌ **Limited CUSIP coverage** - ~10% of bonds have CUSIPs mapped
- ❌ **No bid/ask spread** - Only last price

**Priority:** IMPORTANT - Users need real market data for analysis

**Implementation Path:**
1. Run CUSIP mapping job across all companies
2. Upgrade to Finnhub premium for TRACE data
3. Add historical price table (daily snapshots)

---

### 6. `resolve.bond()` - Bond Detail Lookup

**Current Status: ✅ FULLY SUPPORTED**

**What IS Extracted:**
- ✅ Full bond terms (name, type, seniority, security)
- ✅ Amount details (commitment, principal, outstanding)
- ✅ Rate terms (type, rate, spread, benchmark, floor)
- ✅ Key dates (issue date, maturity date)
- ✅ Issuer entity reference
- ✅ Complete guarantor list
- ✅ JSONB attributes for flexible data

**API Endpoints:**
- `GET /v1/companies/{ticker}/debt/{id}` - Full bond detail
- `GET /v1/companies/{ticker}/debt/{id}/pricing` - Bond with pricing

**Gaps:**
- ❌ **Covenants not extracted** (same as search.bonds)
- ❌ **Call schedule not extracted**
- 🔶 CUSIP/ISIN coverage low

**Priority:** MEDIUM - Covenant extraction needed

---

## User Questions → Primitive Mapping

| # | User Question | Required Primitives | Current Support |
|---|---------------|---------------------|-----------------|
| 1 | "Show me high yield bonds maturing in 2025" | `search.bonds()` | ✅ Supported |
| 2 | "What companies have structural subordination?" | `search.companies()` | ✅ Supported |
| 3 | "Who are the guarantors on RIG's senior notes?" | `traverse.entities()`, `resolve.bond()` | ✅ Supported |
| 4 | "What's the YTM on ATUS 2028 notes?" | `search.pricing()`, `resolve.bond()` | 🔶 Estimated only |
| 5 | "Show me the debt footnote from CHTR's 10-K" | `search.documents()` | ❌ Not supported |
| 6 | "What bonds have covenant-lite terms?" | `search.bonds()` | ❌ Covenants not extracted |
| 7 | "List all VIEs across my coverage" | `traverse.entities()` | ✅ Supported |
| 8 | "What's the call schedule on this note?" | `resolve.bond()` | ❌ Not extracted |
| 9 | "Show me spreads for all telecom bonds" | `search.bonds()`, `search.pricing()` | 🔶 Estimated only |
| 10 | "What's the leverage covenant on this credit facility?" | `resolve.bond()` | ❌ Not extracted |
| 11 | "Which subsidiaries are unrestricted?" | `traverse.entities()` | ✅ Supported |
| 12 | "Historical price chart for this bond" | `search.pricing()` | ❌ No history |
| 13 | "Show me the credit agreement for this loan" | `search.documents()` | ❌ Not stored |

---

## Gap Summary by Priority

### CRITICAL (Block V1 MVP)

| Gap | Impact | Effort | Notes |
|-----|--------|--------|-------|
| Document storage/access | Users can't verify data | HIGH | Need S3/blob storage, document table |
| CUSIP coverage | Pricing useless without identifiers | MEDIUM | Run OpenFIGI mapping job |

### HIGH (Significantly Limits Value)

| Gap | Impact | Effort | Notes |
|-----|--------|--------|-------|
| Covenant extraction | Can't answer key credit questions | HIGH | Add to extraction prompt, new DB columns |
| Real TRACE pricing | Only estimates available | LOW | Finnhub premium upgrade ($300) |

### MEDIUM (Nice to Have for V1)

| Gap | Impact | Effort | Notes |
|-----|--------|--------|-------|
| Call schedule extraction | Affects yield analysis | MEDIUM | Add to prompt, new JSONB field |
| Historical prices | No trend analysis | MEDIUM | Daily price snapshots table |
| Credit ratings | No rating filtering | LOW | External data source (S&P Capital IQ?) |

### LOW (Post-V1)

| Gap | Impact | Effort | Notes |
|-----|--------|--------|-------|
| Industry classification | Slightly better filtering | LOW | Enhance prompt |
| Bid/ask spread | More market detail | MEDIUM | Different data source needed |

---

## Recommended V1 Roadmap

### Phase 1: Document Access (1-2 weeks)
1. Create `documents` table: `company_id`, `doc_type`, `filing_date`, `s3_url`, `sections_json`
2. Store raw filings to S3 during extraction
3. Extract and cache key sections (debt footnote, Exhibit 21, MD&A)
4. Add `/documents` API endpoints

### Phase 2: CUSIP/Pricing (1 week)
1. Run `map_cusips.py` across all 178 companies
2. Upgrade Finnhub to premium tier
3. Run `update_pricing.py` with real TRACE data
4. Verify pricing coverage > 50%

### Phase 3: Covenant Extraction (2 weeks)
1. Enhance extraction prompt with covenant instructions
2. Add new fields to `DebtInstrument`:
   - `financial_covenants`: JSONB (leverage_test, coverage_test, etc.)
   - `incurrence_covenants`: JSONB
   - `is_covenant_lite`: bool (already exists)
3. Add `call_schedule`: JSONB field
4. Run re-extraction on key companies (offshore, telecom, retail)

### Phase 4: Historical Pricing (Post-V1)
1. Create `bond_pricing_history` table
2. Daily cron job for TRACE updates
3. Add `/pricing/history` endpoints

---

## Extraction Prompt Changes Needed

### For Covenants (High Priority)
```
COVENANTS - Extract from credit agreements:
1. Financial Covenants (maintenance tests):
   - Maximum leverage ratio (e.g., "Total Debt/EBITDA not to exceed 5.0x")
   - Minimum interest coverage ratio
   - Fixed charge coverage ratio

2. Incurrence Covenants:
   - Debt incurrence test (permitted debt baskets)
   - Restricted payment test
   - Asset sale covenant

3. Covenant-Lite Indicators:
   - If term loan has no financial maintenance covenants: is_covenant_lite = true
   - Springing covenant? Note the trigger threshold

For each covenant captured, include:
- Test description
- Threshold/limit
- Current cushion if disclosed
```

### For Call Schedule (Medium Priority)
```
CALL SCHEDULE - For bonds/notes, extract call provisions:
- First call date
- Call prices by date (e.g., "105 until 2026, 102.5 until 2027, 100 thereafter")
- Make-whole provisions
- Change of control provisions
```

---

## Current Database Coverage Summary

| Table | Records | Notes |
|-------|---------|-------|
| companies | 178 | Good coverage |
| entities | 3,085 | Strong hierarchy data |
| debt_instruments | 1,805 | 1,460 tradeable (with CUSIPs mapped) |
| ownership_links | ~200 | JVs and complex ownership |
| bond_pricing | 30 | Low - needs CUSIP mapping first |
| company_financials | ~50 | Limited - need more extraction runs |

---

## Conclusion

DebtStack has **strong foundational extraction** for company and bond data, but lacks:

1. **Document access** - Critical for data verification
2. **Covenant extraction** - Critical for credit analysis
3. **Real market pricing** - Important for investment decisions

For V1 MVP, prioritize:
1. Document storage and access endpoints
2. CUSIP mapping for pricing enablement
3. Covenant extraction prompt enhancement

The primitive-based API design can be built on top of existing endpoints with targeted additions for document access and enhanced bond metadata.
