# Document Storage Design

## Problem

Users need access to source documents (10-K, credit agreements, debt footnotes) to verify extracted data. Storing all raw filings is expensive and unnecessary.

## Solution: Smart Tiered Storage

### Tier 1: Extracted Sections (Always Stored in PostgreSQL)

Store **key sections as JSONB** in a new `company_documents` table. These are small, high-value, and frequently accessed.

**Estimated size**: ~500KB per company = **~100 MB for 178 companies**

Sections to store:
- `exhibit_21` - Subsidiary list (critical for structure verification)
- `debt_footnote` - Long-term debt table from 10-K/10-Q
- `credit_agreement_summary` - Key terms from Exhibit 10
- `guarantor_footnote` - Guarantor listing from debt footnotes
- `md_and_a_liquidity` - Liquidity and Capital Resources section

### Tier 2: Full Filing Text (S3 with Lazy Loading)

Store cleaned text versions in S3 **only when first requested**. Evict after 90 days unused.

**Estimated size**: ~2 MB per filing × 15 filings × 178 companies = **~5 GB**

Cost at scale: ~$0.10/month for S3 storage

### Tier 3: Raw Filings (Never Store)

Provide SEC EDGAR URLs. Users can access raw HTML/XBRL directly from SEC.

---

## Database Schema

### New Table: `company_documents`

```sql
CREATE TABLE company_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,

    -- Document identification
    doc_type VARCHAR(50) NOT NULL,  -- '10-K', '10-Q', '8-K', 'exhibit_21', etc.
    filing_date DATE NOT NULL,
    accession_number VARCHAR(25),

    -- Extracted sections (stored in PostgreSQL)
    sections JSONB DEFAULT '{}',
    -- Example: {
    --   "exhibit_21": "SUBSIDIARIES OF THE REGISTRANT...",
    --   "debt_footnote": "NOTE 9 - LONG-TERM DEBT...",
    --   "guarantor_summary": "The following subsidiaries guarantee..."
    -- }

    -- External references
    sec_filing_url VARCHAR(500),     -- Direct link to SEC EDGAR
    s3_full_text_key VARCHAR(255),   -- S3 key if full text cached

    -- Metadata
    extracted_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_accessed_at TIMESTAMP WITH TIME ZONE,

    UNIQUE(company_id, doc_type, filing_date)
);

CREATE INDEX idx_company_docs_company ON company_documents(company_id);
CREATE INDEX idx_company_docs_type ON company_documents(doc_type);
CREATE INDEX idx_company_docs_date ON company_documents(filing_date DESC);
```

### Section Extraction During ETL

Modify `iterative_extraction.py` to extract and store key sections:

```python
async def extract_and_store_sections(
    company_id: UUID,
    filings: dict[str, str],
    db: AsyncSession
) -> None:
    """Extract key sections from filings and store in company_documents."""

    for filing_key, content in filings.items():
        # Parse filing key: "10-K_2024-02-15"
        parts = filing_key.split("_")
        doc_type = parts[0]
        filing_date = parts[1] if len(parts) > 1 else None

        sections = {}

        # Extract Exhibit 21 (subsidiaries)
        if "exhibit_21" in filing_key.lower():
            sections["exhibit_21"] = clean_and_truncate(content, max_chars=100000)

        # Extract debt footnote
        if doc_type in ["10-K", "10-Q"]:
            debt_section = extract_debt_footnote(content)
            if debt_section:
                sections["debt_footnote"] = debt_section

            # Extract MD&A liquidity section
            mda_section = extract_mda_liquidity(content)
            if mda_section:
                sections["md_and_a_liquidity"] = mda_section

        # Extract credit agreement summary from Exhibit 10
        if "exhibit_10" in filing_key.lower():
            sections["credit_agreement"] = extract_credit_summary(content)

        if sections:
            doc = CompanyDocument(
                company_id=company_id,
                doc_type=doc_type,
                filing_date=parse_date(filing_date),
                sections=sections,
                sec_filing_url=build_sec_url(filing_key),
            )
            db.add(doc)

    await db.flush()
```

---

## API Endpoints

### List Documents

```
GET /v1/companies/{ticker}/documents
```

Response:
```json
{
  "data": {
    "company": {"ticker": "RIG", "name": "Transocean Ltd."},
    "documents": [
      {
        "doc_type": "10-K",
        "filing_date": "2024-02-15",
        "available_sections": ["exhibit_21", "debt_footnote", "md_and_a_liquidity"],
        "sec_url": "https://www.sec.gov/Archives/edgar/data/..."
      },
      {
        "doc_type": "10-Q",
        "filing_date": "2024-05-10",
        "available_sections": ["debt_footnote"],
        "sec_url": "..."
      }
    ]
  }
}
```

### Get Document Section

```
GET /v1/companies/{ticker}/documents/{filing_date}/sections/{section_name}
```

Response:
```json
{
  "data": {
    "company": {"ticker": "RIG", "name": "Transocean Ltd."},
    "document": {
      "doc_type": "10-K",
      "filing_date": "2024-02-15"
    },
    "section": {
      "name": "debt_footnote",
      "content": "NOTE 9 - LONG-TERM DEBT\n\nThe following table summarizes...",
      "content_length": 15234
    },
    "sec_url": "https://www.sec.gov/..."
  }
}
```

### Get Full Filing (Lazy Load)

```
GET /v1/companies/{ticker}/documents/{filing_date}/full
```

This endpoint:
1. Checks if full text is in S3 cache
2. If not, fetches from SEC-API, cleans, stores in S3
3. Returns cleaned text or S3 presigned URL for large files

---

## Section Extraction Functions

### Debt Footnote Extraction

```python
def extract_debt_footnote(content: str, max_chars: int = 50000) -> Optional[str]:
    """Extract the debt footnote from 10-K/10-Q content."""
    content_lower = content.lower()

    # Find debt footnote start
    patterns = [
        r"note\s+\d+[\s\-–—]+.*?(?:long[\-\s]?term\s+)?debt",
        r"note\s+\d+[\s\-–—]+.*?borrowings",
        r"note\s+\d+[\s\-–—]+.*?credit\s+(?:facilities|agreements)",
    ]

    for pattern in patterns:
        match = re.search(pattern, content_lower)
        if match:
            start = match.start()
            # Find next "Note X" to determine end
            next_note = re.search(r"\bnote\s+\d+[\s\-–—]", content_lower[start + 100:])
            end = start + 100 + next_note.start() if next_note else start + max_chars
            return content[start:min(end, start + max_chars)]

    return None
```

### MD&A Liquidity Extraction

```python
def extract_mda_liquidity(content: str, max_chars: int = 30000) -> Optional[str]:
    """Extract Liquidity and Capital Resources from MD&A."""
    content_lower = content.lower()

    patterns = [
        r"liquidity\s+and\s+capital\s+resources",
        r"sources\s+and\s+uses\s+of\s+(?:cash|funds)",
    ]

    for pattern in patterns:
        match = re.search(pattern, content_lower)
        if match:
            start = match.start()
            # Find next major section header
            next_section = re.search(
                r"\n(?:results\s+of\s+operations|critical\s+accounting|item\s+\d)",
                content_lower[start + 100:]
            )
            end = start + 100 + next_section.start() if next_section else start + max_chars
            return content[start:min(end, start + max_chars)]

    return None
```

---

## Storage Cost Estimate

### PostgreSQL (Tier 1 - Sections)

| Item | Size | Count | Total |
|------|------|-------|-------|
| exhibit_21 | 50 KB | 178 | 9 MB |
| debt_footnote | 100 KB | 356 | 36 MB |
| md_and_a_liquidity | 50 KB | 356 | 18 MB |
| credit_agreements | 100 KB | 200 | 20 MB |
| **Total** | | | **~83 MB** |

Fits easily in Neon free tier (512 MB) or paid tier.

### S3 (Tier 2 - Full Text Cache)

| Item | Size | Lifetime | Monthly Cost |
|------|------|----------|--------------|
| Cached full filings | ~5 GB | 90 day eviction | ~$0.12 |
| S3 requests | ~10K/mo | | ~$0.04 |
| **Total** | | | **~$0.16/month** |

### Total Storage Cost

**~$0.20/month** for document storage at current scale.

At 1,000 companies: **~$1-2/month**

---

## Migration Plan

### Step 1: Create Table

```bash
alembic revision -m "add_company_documents"
alembic upgrade head
```

### Step 2: Backfill Existing Data

Run a one-time script to re-fetch filings and extract sections:

```bash
python scripts/backfill_document_sections.py --all
```

### Step 3: Modify Extraction Pipeline

Update `iterative_extraction.py` to call `extract_and_store_sections()` after successful extraction.

### Step 4: Add API Endpoints

Add routes to `app/api/routes.py` for document access.

---

## Summary

Instead of storing all documents (~30-60 GB), we:

1. **Extract and store key sections** in PostgreSQL (~100 MB)
2. **Lazy-load full text** to S3 on demand (~5 GB, cached)
3. **Link to SEC EDGAR** for raw files (no storage)

Total cost: **~$0.20/month** vs **~$5-10/month** for full storage.

Users get fast access to the sections they actually need (debt footnotes, guarantor lists) while maintaining the ability to access full filings when required.
