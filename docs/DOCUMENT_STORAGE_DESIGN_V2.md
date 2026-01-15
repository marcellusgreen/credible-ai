# Document Storage Design v2 - Speed Optimized

## Design Goals

1. **<100ms** for extracted sections (debt footnotes, Exhibit 21)
2. **<500ms** for full filing text
3. **Minimal storage cost** (<$5/month at scale)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        API Request                               │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Redis Cache (Hot Data)                        │
│                    TTL: 24 hours                                 │
│                    ~50ms response                                │
│                                                                  │
│   Key: "doc:{ticker}:{doc_type}:{section}"                      │
│   Value: compressed section text                                 │
└─────────────────────────────────────────────────────────────────┘
                                │ cache miss
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                PostgreSQL (Warm Data)                            │
│                ~100ms response                                   │
│                                                                  │
│   company_documents table                                        │
│   - Pre-extracted sections in JSONB                             │
│   - Indexed by company_id, doc_type                             │
└─────────────────────────────────────────────────────────────────┘
                                │ section not extracted
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│              S3 (Full Filing Archive)                            │
│              ~200-500ms response                                 │
│                                                                  │
│   Pre-stored during ETL (not lazy loaded)                       │
│   Compressed text files                                          │
│   CloudFront CDN for edge caching                               │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Changes from v1

| Aspect | v1 (Cost Optimized) | v2 (Speed Optimized) |
|--------|---------------------|----------------------|
| Full filings | Lazy load from SEC | **Pre-store in S3 during ETL** |
| Caching | None | **Redis for hot sections** |
| CDN | None | **CloudFront for S3** |
| First request | 2-5 seconds | **<500ms** |
| Storage cost | ~$0.20/mo | ~$2-5/mo |

---

## Storage Strategy

### During ETL (Extraction Time)

Store EVERYTHING upfront - no lazy loading:

```python
async def store_documents_during_extraction(
    company_id: UUID,
    ticker: str,
    filings: dict[str, str],
    db: AsyncSession,
    s3_client: S3Client,
) -> None:
    """Store all documents during extraction - no lazy loading."""

    for filing_key, content in filings.items():
        doc_type, filing_date = parse_filing_key(filing_key)

        # 1. Extract key sections for PostgreSQL (fast access)
        sections = extract_all_sections(content, doc_type)

        # 2. Store full cleaned text to S3 (archive)
        cleaned_text = clean_filing_html(content)
        s3_key = f"filings/{ticker}/{doc_type}_{filing_date}.txt.gz"

        # Compress before upload (~70% size reduction)
        compressed = gzip.compress(cleaned_text.encode('utf-8'))
        await s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_key,
            Body=compressed,
            ContentType='text/plain',
            ContentEncoding='gzip',
        )

        # 3. Store metadata + sections in PostgreSQL
        doc = CompanyDocument(
            company_id=company_id,
            doc_type=doc_type,
            filing_date=parse_date(filing_date),
            sections=sections,
            s3_key=s3_key,
            full_text_size=len(cleaned_text),
            sec_filing_url=build_sec_url(filing_key),
        )
        db.add(doc)

    await db.flush()
```

### Request Time

No fetching from SEC - everything pre-stored:

```python
async def get_document_section(
    ticker: str,
    filing_date: str,
    section: str,
    redis: Redis,
    db: AsyncSession,
) -> str:
    """Get document section with multi-tier caching."""

    cache_key = f"doc:{ticker}:{filing_date}:{section}"

    # 1. Check Redis (fastest - ~5ms)
    cached = await redis.get(cache_key)
    if cached:
        return decompress(cached)

    # 2. Check PostgreSQL sections (fast - ~50ms)
    doc = await db.execute(
        select(CompanyDocument)
        .where(CompanyDocument.company_id == company_id)
        .where(CompanyDocument.filing_date == filing_date)
    )
    doc = doc.scalar_one_or_none()

    if doc and section in doc.sections:
        content = doc.sections[section]
        # Populate Redis for next request
        await redis.setex(cache_key, 86400, compress(content))
        return content

    # 3. Section not pre-extracted - extract from S3 full text (~200ms)
    if doc and doc.s3_key:
        full_text = await get_from_s3(doc.s3_key)
        content = extract_section_from_text(full_text, section)
        if content:
            # Cache in Redis AND update PostgreSQL
            await redis.setex(cache_key, 86400, compress(content))
            doc.sections[section] = content
            await db.commit()
        return content

    return None
```

---

## Redis Caching Strategy

### What to Cache

| Data | TTL | Reason |
|------|-----|--------|
| Frequently accessed sections | 24 hours | Debt footnotes, Exhibit 21 |
| Full filing text | 1 hour | Large, less frequent |
| Search results | 5 minutes | Changes with new extractions |

### Cache Key Structure

```
doc:{ticker}:{doc_type}:{filing_date}:{section}
doc:RIG:10-K:2024-02-15:debt_footnote
doc:RIG:10-K:2024-02-15:exhibit_21

full:{ticker}:{doc_type}:{filing_date}
full:RIG:10-K:2024-02-15
```

### Memory Estimate

```
178 companies × 3 key sections × 100KB avg = ~53 MB
+ full filings (top 20 companies) × 2MB = ~40 MB
Total Redis: ~100 MB

Redis pricing: Free tier (30MB) won't work
              Upstash: $0.20/100MB = ~$0.20/month
              Railway Redis: ~$5/month
```

---

## S3 + CloudFront Configuration

### S3 Bucket Structure

```
debtstack-documents/
├── filings/
│   ├── AAPL/
│   │   ├── 10-K_2024-01-15.txt.gz
│   │   ├── 10-Q_2024-04-15.txt.gz
│   │   └── exhibit_21_2024-01-15.txt.gz
│   ├── RIG/
│   │   ├── 10-K_2024-02-15.txt.gz
│   │   └── ...
│   └── ...
└── indexes/
    └── manifest.json  # List of all stored documents
```

### CloudFront CDN

```
Origin: debtstack-documents.s3.amazonaws.com
Behaviors:
  - /filings/* → Cache 7 days (filings don't change)
  - Default → Cache 1 hour

Edge Locations: US-East, US-West (credit analysts mostly US-based)
```

**Benefit**: S3 in us-east-1 is ~100-200ms from West Coast. CloudFront edge cache: ~20-50ms.

### Cost Estimate

| Service | Usage | Monthly Cost |
|---------|-------|--------------|
| S3 Storage | 5 GB compressed | $0.12 |
| S3 Requests | 50K GET/month | $0.02 |
| CloudFront | 10 GB transfer | $0.85 |
| **Total S3+CDN** | | **~$1.00/month** |

---

## Latency Targets

| Request Type | Target | How Achieved |
|--------------|--------|--------------|
| Get section (hot) | <50ms | Redis cache |
| Get section (warm) | <100ms | PostgreSQL JSONB |
| Get section (cold) | <300ms | S3 + extract |
| Get full filing | <500ms | S3 + CloudFront |
| Search documents | <200ms | PostgreSQL indexes |

---

## Database Schema (Updated)

```sql
CREATE TABLE company_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,

    -- Document identification
    doc_type VARCHAR(50) NOT NULL,
    filing_date DATE NOT NULL,
    accession_number VARCHAR(25),

    -- Pre-extracted sections (PostgreSQL - fast access)
    sections JSONB DEFAULT '{}' NOT NULL,

    -- S3 reference for full text (always populated)
    s3_key VARCHAR(255) NOT NULL,
    full_text_size INTEGER,  -- bytes, for UI display
    full_text_hash VARCHAR(64),  -- SHA256, for cache invalidation

    -- External references
    sec_filing_url VARCHAR(500),

    -- Metadata
    extracted_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    UNIQUE(company_id, doc_type, filing_date)
);

-- Indexes for fast lookup
CREATE INDEX idx_docs_company ON company_documents(company_id);
CREATE INDEX idx_docs_company_type ON company_documents(company_id, doc_type);
CREATE INDEX idx_docs_date ON company_documents(filing_date DESC);

-- GIN index for section search (find all docs with debt_footnote)
CREATE INDEX idx_docs_sections ON company_documents USING GIN (sections);
```

---

## API Response Times (Expected)

### Scenario 1: Analyst checks RIG debt footnote

```
Request: GET /v1/companies/RIG/documents/2024-02-15/sections/debt_footnote

1. Redis check: 5ms → HIT (analyst checked yesterday)
2. Return cached content

Total: ~50ms ✅
```

### Scenario 2: First time accessing ATUS Exhibit 21

```
Request: GET /v1/companies/ATUS/documents/2024-02-15/sections/exhibit_21

1. Redis check: 5ms → MISS
2. PostgreSQL query: 30ms → Found in sections JSONB
3. Populate Redis cache: 5ms (async)
4. Return content

Total: ~80ms ✅
```

### Scenario 3: Request full 10-K text

```
Request: GET /v1/companies/RIG/documents/2024-02-15/full

1. Redis check: 5ms → MISS (full text not cached)
2. PostgreSQL get S3 key: 20ms
3. CloudFront/S3 fetch: 100-200ms
4. Decompress: 20ms
5. Return content

Total: ~250ms ✅
```

---

## Implementation Priority

### Phase 1: Core Storage (Week 1)
- [ ] Create `company_documents` table
- [ ] Modify extraction to store sections in PostgreSQL
- [ ] Modify extraction to upload full text to S3
- [ ] Add basic `/documents` API endpoints

### Phase 2: Caching (Week 2)
- [ ] Add Redis (Upstash or Railway)
- [ ] Implement cache-aside pattern for sections
- [ ] Add cache warming for popular companies

### Phase 3: CDN (Week 2-3)
- [ ] Set up CloudFront distribution
- [ ] Configure caching behaviors
- [ ] Update S3 fetch to use CloudFront URLs

---

## Trade-offs

| Aspect | Cost-Optimized (v1) | Speed-Optimized (v2) |
|--------|---------------------|----------------------|
| First request latency | 2-5 seconds | <500ms |
| Storage cost | ~$0.20/month | ~$3-5/month |
| Complexity | Simple | Redis + S3 + CDN |
| ETL time | Faster | +30s per company |
| Reliability | Depends on SEC | Self-contained |

**Recommendation**: v2 is worth the extra ~$3-5/month for professional users who expect fast responses.

---

## Summary

Speed optimizations:
1. **Pre-store everything during ETL** - no lazy loading
2. **Redis cache** for hot sections (~50ms)
3. **CloudFront CDN** for S3 files (~200ms vs 500ms)
4. **Compressed storage** - 70% smaller, faster transfers
5. **PostgreSQL JSONB** with GIN index for section search

Result: **<100ms for sections, <500ms for full filings**
