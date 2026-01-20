# Extraction Modifications for Primitives API

## Current State

The extraction pipeline produces:
- **Entities**: Corporate structure (subsidiaries, JVs, VIEs)
- **Debt Instruments**: Bonds, loans, credit facilities
- **Guarantees**: Links between entities and debt
- **Financials**: Quarterly income, balance sheet, cash flow
- **Pricing**: Bond prices, YTM, spreads (from FINRA)

## Gaps Identified for Primitives API

### 1. Document Retention (NEW - Required for `search.documents`)

**Current**: Documents are downloaded, processed, then discarded.

**Needed**: Store document sections for semantic search and retrieval.

**Proposed Schema**:
```sql
CREATE TABLE company_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID REFERENCES companies(id),

    -- Filing metadata
    filing_type VARCHAR(20),        -- 10-K, 10-Q, 8-K, EX-21
    filing_date DATE,
    fiscal_year INT,
    fiscal_quarter INT,
    accession_number VARCHAR(30),
    sec_url TEXT,

    -- Section identification
    section_type VARCHAR(50),       -- debt_footnote, subsidiaries, mda, etc.
    section_title VARCHAR(200),

    -- Content
    raw_text TEXT,                  -- Full section text
    page_start INT,
    page_end INT,

    -- Metadata
    extracted_at TIMESTAMP DEFAULT NOW(),
    word_count INT,

    UNIQUE(company_id, accession_number, section_type)
);

-- For keyword search
CREATE INDEX idx_docs_company ON company_documents(company_id);
CREATE INDEX idx_docs_section ON company_documents(section_type);
CREATE INDEX idx_docs_fulltext ON company_documents USING GIN(to_tsvector('english', raw_text));
```

**Extraction Changes**:
```python
# In extraction.py, after downloading filing
async def extract_and_store_sections(filing_content: str, company_id: UUID, filing_meta: dict):
    """Extract key sections and store in database."""
    sections = {
        "debt_footnote": extract_debt_note(filing_content),
        "subsidiaries": extract_exhibit_21(filing_content),
        "mda": extract_mda_section(filing_content),
        "liquidity": extract_liquidity_section(filing_content),
        "covenants": extract_covenant_section(filing_content),
        "maturity_schedule": extract_maturity_table(filing_content),
        "guarantor_info": extract_obligor_group(filing_content),
        "risk_factors": extract_risk_factors(filing_content),
    }

    for section_type, content in sections.items():
        if content:
            await save_document_section(company_id, filing_meta, section_type, content)
```

### 2. Ticker Normalization (Bug Fix)

**Current Issue**: Some `company_metrics.ticker` values are CIKs instead of tickers.

**Fix**: Ensure extraction always saves proper ticker from company lookup.

```python
# In save_extraction_to_db()
# Always use the ticker passed in, not from extraction data
company.ticker = ticker.upper()  # Enforce uppercase
```

**Migration**:
```sql
-- Fix any CIK-as-ticker issues
UPDATE company_metrics cm
SET ticker = c.ticker
FROM companies c
WHERE cm.company_id = c.id
AND cm.ticker != c.ticker;
```

### 3. CUSIP Enhancement (Improved `resolve.bond`)

**Current**: CUSIPs are optional, often missing.

**Needed**: More aggressive CUSIP lookup during extraction.

**Changes**:
1. Use FINRA bond lookup during extraction
2. Store ISIN when available
3. Add CUSIP validation (9 chars, check digit)

```python
def validate_cusip(cusip: str) -> bool:
    """Validate CUSIP check digit."""
    if not cusip or len(cusip) != 9:
        return False
    # Luhn-like algorithm for CUSIP
    # ... implementation
    return True

async def lookup_cusip_for_bond(issuer: str, coupon: float, maturity: date) -> Optional[str]:
    """Try to find CUSIP for a bond from FINRA/external sources."""
    # ... implementation
```

### 4. Guarantor Chain Tracking (Enhanced `traverse.entities`)

**Current**: Guarantees stored as links, but chain depth not explicit.

**Needed**: Store guarantee priority/ranking for structural subordination analysis.

```sql
ALTER TABLE guarantees ADD COLUMN guarantee_rank INT;
ALTER TABLE guarantees ADD COLUMN is_parent_guarantee BOOLEAN DEFAULT FALSE;
ALTER TABLE guarantees ADD COLUMN coverage_percentage DECIMAL(5,2);  -- e.g., 100% or partial
```

### 5. Pricing Freshness (Enhanced `search.pricing`)

**Current**: Single price point per bond.

**Needed**: Price history for trend analysis.

```sql
CREATE TABLE bond_price_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    debt_instrument_id UUID REFERENCES debt_instruments(id),
    trade_date DATE,
    last_price DECIMAL(8,4),
    high_price DECIMAL(8,4),
    low_price DECIMAL(8,4),
    volume INT,
    trade_count INT,
    ytm_bps INT,
    spread_to_treasury_bps INT,

    UNIQUE(debt_instrument_id, trade_date)
);

CREATE INDEX idx_price_history_date ON bond_price_history(debt_instrument_id, trade_date DESC);
```

---

## Implementation Priority

### Phase 1: Bug Fixes (Immediate)
1. ✅ Fix ticker normalization in company_metrics
2. Add CUSIP validation
3. Ensure all extraction uses proper ticker

### Phase 2: Document Storage (1-2 weeks)
1. Create `company_documents` table
2. Modify extraction to store sections
3. Implement `search.documents` endpoint
4. Add full-text search

### Phase 3: Pricing History (2-3 weeks)
1. Create `bond_price_history` table
2. Daily price update script
3. Historical data backfill
4. Price trend endpoints

### Phase 4: Enhanced Guarantees (Future)
1. Add guarantee ranking
2. Parent guarantee detection
3. Structural subordination scoring improvements

---

## Extraction Pipeline Changes Summary

```python
# Current flow:
# 1. Download 10-K/10-Q from SEC-API
# 2. Extract entities + debt with Gemini
# 3. Run QA checks
# 4. Save to DB
# 5. Discard documents

# New flow:
# 1. Download 10-K/10-Q from SEC-API
# 2. Extract entities + debt with Gemini
# 3. Run QA checks
# 4. Save to DB
# 5. **NEW: Extract and store document sections**
# 6. **NEW: Lookup CUSIPs for new bonds**
# 7. **NEW: Update pricing history**
```

---

## Database Migration Plan

```sql
-- Migration 008: Add document storage
CREATE TABLE company_documents (...);
CREATE INDEX ...;

-- Migration 009: Add price history
CREATE TABLE bond_price_history (...);
CREATE INDEX ...;

-- Migration 010: Enhance guarantees
ALTER TABLE guarantees ADD COLUMN guarantee_rank INT;
ALTER TABLE guarantees ADD COLUMN is_parent_guarantee BOOLEAN;
```

---

## Cost Impact

| Change | Cost Impact |
|--------|-------------|
| Document storage | ~500KB per company per filing |
| Price history | ~10KB per bond per year |
| Enhanced extraction | +$0.002 per company (CUSIP lookup) |

**Storage estimate for 200 companies**:
- Documents: ~200MB (2 years of filings)
- Price history: ~50MB (1 year)
- Total: ~250MB additional

Neon free tier: 512MB → may need upgrade to Pro ($19/mo) for full document storage.
