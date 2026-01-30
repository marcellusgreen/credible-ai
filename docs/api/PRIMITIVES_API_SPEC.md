# DebtStack Primitives API Specification

## Overview

This document specifies the REST API design for DebtStack's 7 core primitives, optimized for AI agents writing code in sandboxes.

**Design Philosophy:**
- One endpoint per primitive
- Simple HTTP GET/POST (no query languages)
- Field selection for context optimization
- Consistent JSON response structure
- Clear, actionable error messages

**Base URL:** `https://api.debtstack.ai/v1`

---

## Authentication

All requests require an API key in the Authorization header:

```
Authorization: Bearer ds_live_xxxxxxxxxxxx
```

**Rate Limits (V1):**
- 100 requests/minute per API key
- Rate limit headers included in all responses:
  - `X-RateLimit-Limit: 100`
  - `X-RateLimit-Remaining: 97`
  - `X-RateLimit-Reset: 1704067200`

---

## Response Structure

### Success Response
```json
{
  "data": { ... },
  "meta": {
    "total": 150,
    "limit": 50,
    "offset": 0,
    "request_id": "req_abc123"
  }
}
```

### Error Response
```json
{
  "error": {
    "code": "INVALID_TICKER",
    "message": "Company ticker 'XYZ' not found in database",
    "details": {
      "ticker": "XYZ",
      "suggestion": "Did you mean 'XOM'?"
    }
  }
}
```

### Error Codes
| Code | HTTP Status | Description |
|------|-------------|-------------|
| `INVALID_TICKER` | 404 | Company not found |
| `INVALID_CUSIP` | 404 | Bond CUSIP not found |
| `INVALID_PARAMETER` | 400 | Invalid query parameter |
| `RATE_LIMITED` | 429 | Too many requests |
| `UNAUTHORIZED` | 401 | Invalid or missing API key |
| `INTERNAL_ERROR` | 500 | Server error |

---

## Primitive 1: search.companies

### Endpoint
```
GET /v1/companies
```

### Purpose
Filter and retrieve companies across the dataset for peer screening, sector analysis, and leverage comparisons.

### Query Parameters

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| `ticker` | string | Comma-separated tickers | `AAPL,MSFT,GOOGL` |
| `sector` | string | Filter by sector | `Technology` |
| `industry` | string | Filter by industry | `Software` |
| `min_leverage` | float | Minimum leverage ratio | `3.0` |
| `max_leverage` | float | Maximum leverage ratio | `6.0` |
| `min_net_leverage` | float | Minimum net leverage | `2.0` |
| `max_net_leverage` | float | Maximum net leverage | `5.0` |
| `min_debt` | int | Minimum total debt (cents) | `100000000000` |
| `max_debt` | int | Maximum total debt (cents) | `500000000000` |
| `rating_bucket` | string | Rating bucket: `IG`, `HY-BB`, `HY-B`, `HY-CCC`, `NR` | `HY-BB` |
| `has_structural_sub` | bool | Has structural subordination | `true` |
| `has_floating_rate` | bool | Has floating rate debt | `true` |
| `has_near_term_maturity` | bool | Debt maturing within 24 months | `true` |
| `fields` | string | Comma-separated fields to return | `ticker,name,net_leverage` |
| `sort` | string | Sort field, prefix `-` for desc | `-net_leverage` |
| `limit` | int | Results per page (max 100) | `50` |
| `offset` | int | Pagination offset | `0` |
| `include_metadata` | bool | Include extraction metadata (qa_score, timestamps, warnings) | `false` |

### Available Fields
```
ticker, name, sector, industry, cik
total_debt, secured_debt, unsecured_debt, net_debt
leverage_ratio, net_leverage_ratio, interest_coverage, secured_leverage
entity_count, guarantor_count
subordination_risk, subordination_score
has_structural_sub, has_floating_rate, has_near_term_maturity
has_holdco_debt, has_opco_debt, has_unrestricted_subs
nearest_maturity, weighted_avg_maturity
debt_due_1yr, debt_due_2yr, debt_due_3yr
sp_rating, moodys_rating, rating_bucket
```

### Example Request
```bash
# Which of the MAG7 has the highest net leverage?
curl "https://api.debtstack.ai/v1/companies?ticker=AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA&fields=ticker,name,net_leverage_ratio&sort=-net_leverage_ratio&limit=1" \
  -H "Authorization: Bearer ds_live_xxx"
```

### Example Response
```json
{
  "data": [
    {
      "ticker": "TSLA",
      "name": "Tesla, Inc.",
      "net_leverage_ratio": 1.8
    }
  ],
  "meta": {
    "total": 7,
    "limit": 1,
    "offset": 0,
    "filters_applied": {
      "ticker": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]
    }
  }
}
```

---

## Primitive 2: search.bonds

### Endpoint
```
GET /v1/bonds
```

### Purpose
Filter and retrieve bonds across all companies for yield hunting, bond screening, and seniority analysis.

### Query Parameters

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| `ticker` | string | Filter by company ticker(s) | `RIG,VAL` |
| `cusip` | string | Filter by CUSIP(s) | `89157VAG8` |
| `sector` | string | Filter by company sector | `Energy` |
| `seniority` | string | `senior_secured`, `senior_unsecured`, `subordinated` | `senior_unsecured` |
| `security_type` | string | `first_lien`, `second_lien`, `unsecured` | `first_lien` |
| `instrument_type` | string | `term_loan_b`, `senior_notes`, `revolver`, etc. | `senior_notes` |
| `issuer_type` | string | Issuer entity type: `holdco`, `opco`, `subsidiary` | `opco` |
| `rate_type` | string | `fixed`, `floating` | `fixed` |
| `min_coupon` | float | Minimum coupon rate (%) | `6.0` |
| `max_coupon` | float | Maximum coupon rate (%) | `9.0` |
| `min_ytm` | float | Minimum yield to maturity (%) | `7.0` |
| `max_ytm` | float | Maximum yield to maturity (%) | `10.0` |
| `min_spread` | int | Minimum spread to treasury (bps) | `300` |
| `max_spread` | int | Maximum spread to treasury (bps) | `600` |
| `maturity_before` | date | Maturity before date | `2028-12-31` |
| `maturity_after` | date | Maturity after date | `2025-01-01` |
| `min_outstanding` | int | Minimum outstanding (cents) | `50000000000` |
| `has_pricing` | bool | Has pricing data | `true` |
| `has_guarantors` | bool | Has guarantor entities | `true` |
| `has_cusip` | bool | Has CUSIP (tradeable) | `true` |
| `currency` | string | Currency code | `USD` |
| `fields` | string | Fields to return | `name,cusip,ytm,spread` |
| `sort` | string | Sort field | `-ytm` |
| `limit` | int | Results per page | `50` |
| `offset` | int | Pagination offset | `0` |

### Available Fields
```
id, name, cusip, isin
company_ticker, company_name, company_sector
issuer_name, issuer_type, issuer_id
instrument_type, seniority, security_type
commitment, principal, outstanding, currency
rate_type, coupon_rate, spread_bps, benchmark, floor_bps
issue_date, maturity_date
is_active, is_drawn
pricing.last_price, pricing.ytm, pricing.spread, pricing.staleness_days
guarantor_count
```

### Example Request
```bash
# Show me all senior unsecured bonds yielding >8%
curl "https://api.debtstack.ai/v1/bonds?seniority=senior_unsecured&min_ytm=8.0&has_pricing=true&fields=name,cusip,company_ticker,coupon_rate,maturity_date,pricing&sort=-pricing.ytm" \
  -H "Authorization: Bearer ds_live_xxx"
```

### Example Response
```json
{
  "data": [
    {
      "name": "8.00% Senior Notes due 2027",
      "cusip": "893830AK8",
      "company_ticker": "RIG",
      "coupon_rate": 8.0,
      "maturity_date": "2027-02-01",
      "pricing": {
        "last_price": 94.25,
        "ytm": 9.42,
        "spread": 512,
        "staleness_days": 1
      }
    },
    {
      "name": "6.875% Senior Notes due 2026",
      "cusip": "89157VAG8",
      "company_ticker": "ATUS",
      "coupon_rate": 6.875,
      "maturity_date": "2026-03-01",
      "pricing": {
        "last_price": 91.50,
        "ytm": 8.75,
        "spread": 485,
        "staleness_days": 0
      }
    }
  ],
  "meta": {
    "total": 23,
    "limit": 50,
    "offset": 0,
    "filters_applied": {
      "seniority": "senior_unsecured",
      "min_ytm": 8.0,
      "has_pricing": true
    }
  }
}
```

---

## Primitive 3: search.documents

### Endpoint
```
GET /v1/documents/search
```

### Purpose
Full-text search across SEC filing sections for covenant analysis, debt structure research, and credit agreement review.

### Query Parameters

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| `q` | string | **Required.** Full-text search query | `subordinated` |
| `ticker` | string | Comma-separated company tickers | `RIG,CHTR` |
| `doc_type` | string | Filing type: `10-K`, `10-Q`, `8-K` | `10-K` |
| `section_type` | string | Section type (see below) | `debt_footnote` |
| `filed_after` | date | Minimum filing date | `2024-01-01` |
| `filed_before` | date | Maximum filing date | `2025-12-31` |
| `fields` | string | Comma-separated fields to return | `ticker,section_type,snippet` |
| `sort` | string | Sort: `-relevance` (default), `-filing_date`, `filing_date` | `-relevance` |
| `limit` | int | Results per page (max 100, default 50) | `20` |
| `offset` | int | Pagination offset | `0` |
| `format` | string | Response format: `json` (default), `csv` | `json` |

### Section Types
| Section Type | Description | Source |
|--------------|-------------|--------|
| `exhibit_21` | Subsidiary list | 10-K Exhibit 21 |
| `debt_footnote` | Long-term debt details (Note 9/10) | 10-K/10-Q Notes |
| `mda_liquidity` | Liquidity and Capital Resources | MD&A section |
| `credit_agreement` | Full credit facility documents | 8-K Exhibit 10 |
| `indenture` | Bond indentures (covenants, events of default) | 8-K Exhibit 4 |
| `guarantor_list` | Guarantor subsidiaries | Notes |
| `covenants` | Financial covenant details | Notes/Exhibits |

### Available Fields
```
id, ticker, company_name
doc_type, filing_date, section_type, section_title
snippet, content, content_length
relevance_score
```

### Example Request
```bash
# Find all mentions of 'subordinated' in debt footnotes
curl "https://api.debtstack.ai/v1/documents/search?q=subordinated&section_type=debt_footnote&fields=ticker,section_type,snippet,relevance_score" \
  -H "Authorization: Bearer ds_live_xxx"
```

### Example Response
```json
{
  "data": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "ticker": "RIG",
      "company_name": "Transocean Ltd.",
      "doc_type": "10-K",
      "filing_date": "2024-02-15",
      "section_type": "debt_footnote",
      "section_title": "Note 9 - Long-Term Debt",
      "snippet": "...senior <b>subordinated</b> notes due 2028...",
      "relevance_score": 0.85
    },
    {
      "id": "550e8400-e29b-41d4-a716-446655440001",
      "ticker": "CHTR",
      "company_name": "Charter Communications, Inc.",
      "doc_type": "10-K",
      "filing_date": "2024-02-23",
      "section_type": "debt_footnote",
      "section_title": "Note 8 - Long-Term Debt",
      "snippet": "...structurally <b>subordinated</b> to all obligations of our subsidiaries...",
      "relevance_score": 0.78
    }
  ],
  "meta": {
    "query": "subordinated",
    "total": 42,
    "limit": 50,
    "offset": 0,
    "filters_applied": {
      "section_type": "debt_footnote"
    }
  }
}
```

### Example: Search Credit Agreements for Covenants
```bash
# Find maintenance covenants in credit agreements
curl "https://api.debtstack.ai/v1/documents/search?q=maintenance%20covenant&section_type=credit_agreement&ticker=CHTR&sort=-filing_date" \
  -H "Authorization: Bearer ds_live_xxx"
```

### Example: Search Indentures for Events of Default
```bash
# Find event of default clauses in indentures
curl "https://api.debtstack.ai/v1/documents/search?q=event%20of%20default&section_type=indenture&limit=10" \
  -H "Authorization: Bearer ds_live_xxx"
```

### Notes
- Search uses PostgreSQL full-text search with relevance ranking
- Snippets highlight matching terms with `<b>` tags
- Credit agreements and indentures are stored as full documents (up to 500K chars)
- Use `content` field to retrieve full section text (large responses)

---

## Primitive 4: traverse.entities

### Endpoint
```
POST /v1/entities/traverse
```

### Purpose
Follow entity relationships (guarantees, subsidiaries, borrowers) for guarantor chains, org structure, and structural subordination analysis.

### Request Body

```json
{
  "start": {
    "type": "company|bond|entity",
    "id": "RIG|89157VAG8|uuid"
  },
  "relationships": ["guarantees", "subsidiaries", "parents", "debt"],
  "direction": "outbound|inbound|both",
  "depth": 3,
  "filters": {
    "entity_type": ["opco", "subsidiary"],
    "is_guarantor": true,
    "jurisdiction": "Delaware"
  },
  "fields": ["name", "entity_type", "jurisdiction", "is_guarantor", "debt_at_entity"]
}
```

### Relationship Types
| Relationship | Description |
|--------------|-------------|
| `guarantees` | Entities that guarantee a bond (inbound) or bonds an entity guarantees (outbound) |
| `subsidiaries` | Child entities owned by parent |
| `parents` | Parent entities (ownership chain) |
| `debt` | Debt instruments issued at entity |
| `borrowers` | Entities that are borrowers on debt |

### Example Request
```bash
# Which entities guarantee Bond B?
curl -X POST "https://api.debtstack.ai/v1/entities/traverse" \
  -H "Authorization: Bearer ds_live_xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "start": {
      "type": "bond",
      "id": "89157VAG8"
    },
    "relationships": ["guarantees"],
    "direction": "inbound",
    "fields": ["name", "entity_type", "jurisdiction", "is_guarantor"]
  }'
```

### Example Response
```json
{
  "data": {
    "start": {
      "type": "bond",
      "id": "89157VAG8",
      "name": "6.875% Senior Notes due 2026",
      "company": "ATUS"
    },
    "traversal": {
      "relationship": "guarantees",
      "direction": "inbound",
      "entities": [
        {
          "id": "uuid-1",
          "name": "Altice USA, Inc.",
          "entity_type": "holdco",
          "jurisdiction": "Delaware",
          "is_guarantor": true,
          "guarantee_type": "full"
        },
        {
          "id": "uuid-2",
          "name": "CSC Holdings, LLC",
          "entity_type": "opco",
          "jurisdiction": "Delaware",
          "is_guarantor": true,
          "guarantee_type": "full"
        }
      ]
    },
    "summary": {
      "total_guarantors": 2,
      "guarantee_coverage": "full"
    }
  }
}
```

### Example: Full Corporate Structure
```bash
# Show me RIG's full entity structure
curl -X POST "https://api.debtstack.ai/v1/entities/traverse" \
  -H "Authorization: Bearer ds_live_xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "start": {
      "type": "company",
      "id": "RIG"
    },
    "relationships": ["subsidiaries"],
    "direction": "outbound",
    "depth": 10,
    "fields": ["name", "entity_type", "jurisdiction", "is_guarantor", "is_vie", "debt_at_entity"]
  }'
```

---

## Primitive 5: search.pricing (DEPRECATED)

> **⚠️ DEPRECATED**: This endpoint is deprecated as of 2026-01-30. Use `GET /v1/bonds?has_pricing=true` instead.
> **Removal Date**: 2026-06-01
>
> **Migration**: The `/v1/bonds` endpoint now always includes pricing data in responses. Use these filters:
> - `has_pricing=true` - Only bonds with pricing
> - `min_ytm=8.0` - Filter by yield
> - `ticker=RIG` - Filter by company

### Endpoint
```
GET /v1/pricing
```

### Purpose
Retrieve bond pricing from FINRA TRACE for yield analysis, distress signals, and relative value.

### Query Parameters

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| `ticker` | string | Company ticker(s) | `RIG,VAL` |
| `cusip` | string | CUSIP(s) | `89157VAG8,893830AK8` |
| `date` | date | Pricing as of date | `2024-01-15` |
| `date_from` | date | History start date | `2024-01-01` |
| `date_to` | date | History end date | `2024-01-31` |
| `aggregation` | string | `latest`, `daily`, `weekly` | `latest` |
| `min_ytm` | float | Minimum YTM (%) | `7.0` |
| `max_ytm` | float | Maximum YTM (%) | `15.0` |
| `min_spread` | int | Minimum spread (bps) | `400` |
| `fields` | string | Fields to return | `cusip,last_price,ytm,spread` |
| `sort` | string | Sort field | `-ytm` |
| `limit` | int | Results per page | `50` |

### Available Fields
```
cusip, isin, bond_name
company_ticker, company_name
last_price, last_trade_date, last_trade_volume
ytm, ytm_bps
spread, spread_bps, treasury_benchmark
price_source, staleness_days
coupon_rate, maturity_date, seniority
```

### Example Request
```bash
# Get current pricing for all RIG bonds
curl "https://api.debtstack.ai/v1/pricing?ticker=RIG&aggregation=latest&fields=cusip,bond_name,last_price,ytm,spread,staleness_days" \
  -H "Authorization: Bearer ds_live_xxx"
```

### Example Response
```json
{
  "data": [
    {
      "cusip": "893830AK8",
      "bond_name": "8.00% Senior Notes due 2027",
      "last_price": 94.25,
      "ytm": 9.42,
      "spread": 512,
      "staleness_days": 1
    },
    {
      "cusip": "893830AL6",
      "bond_name": "7.50% Senior Notes due 2031",
      "last_price": 87.50,
      "ytm": 9.85,
      "spread": 548,
      "staleness_days": 2
    }
  ],
  "meta": {
    "total": 8,
    "priced_count": 6,
    "stale_count": 2,
    "as_of": "2024-01-15T16:00:00Z"
  }
}
```

### Example: Pricing History
```bash
# Get pricing history for CUSIP 89157VAG8 over last 2 weeks
curl "https://api.debtstack.ai/v1/pricing?cusip=89157VAG8&date_from=2024-01-01&date_to=2024-01-15&aggregation=daily" \
  -H "Authorization: Bearer ds_live_xxx"
```

---

## Primitive 6: resolve.bond

### Endpoint
```
GET /v1/bonds/resolve
```

### Purpose
Map between bond descriptions, CUSIPs, and issuers for CUSIP lookup, bond matching, and identifier conversion.

### Query Parameters

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| `q` | string | Free-text search | `RIG 8% 2027` |
| `cusip` | string | Exact CUSIP lookup | `89157VAG8` |
| `isin` | string | Exact ISIN lookup | `US89157VAG86` |
| `ticker` | string | Company ticker | `RIG` |
| `coupon` | float | Coupon rate (%) | `8.0` |
| `maturity_year` | int | Maturity year | `2027` |
| `match_mode` | string | `exact`, `fuzzy` | `fuzzy` |
| `limit` | int | Max matches | `5` |

### Example Request
```bash
# What's the CUSIP for RIG's 6.8% 2028 notes?
curl "https://api.debtstack.ai/v1/bonds/resolve?q=RIG%206.8%25%202028&match_mode=fuzzy" \
  -H "Authorization: Bearer ds_live_xxx"
```

### Example Response
```json
{
  "data": {
    "query": "RIG 6.8% 2028",
    "matches": [
      {
        "confidence": 0.95,
        "bond": {
          "id": "uuid-123",
          "name": "6.80% Senior Notes due 2038",
          "cusip": "893830AF9",
          "isin": "US893830AF95",
          "company_ticker": "RIG",
          "company_name": "Transocean Ltd.",
          "coupon_rate": 6.80,
          "maturity_date": "2038-03-15",
          "seniority": "senior_unsecured",
          "outstanding": 10530000000
        }
      }
    ],
    "exact_match": false,
    "suggestions": [
      "Did you mean 6.80% 2038 (not 2028)?"
    ]
  }
}
```

### Example: CUSIP Lookup
```bash
# Find bond details for CUSIP 89157VAG8
curl "https://api.debtstack.ai/v1/bonds/resolve?cusip=89157VAG8" \
  -H "Authorization: Bearer ds_live_xxx"
```

### Example Response
```json
{
  "data": {
    "query": "89157VAG8",
    "matches": [
      {
        "confidence": 1.0,
        "bond": {
          "id": "uuid-456",
          "name": "6.875% Senior Notes due 2026",
          "cusip": "89157VAG8",
          "isin": "US89157VAG85",
          "company_ticker": "ATUS",
          "company_name": "Altice USA, Inc.",
          "coupon_rate": 6.875,
          "maturity_date": "2026-03-01",
          "seniority": "senior_unsecured",
          "issuer": {
            "name": "CSC Holdings, LLC",
            "entity_type": "opco"
          },
          "guarantor_count": 5,
          "outstanding": 150000000000
        }
      }
    ],
    "exact_match": true
  }
}
```

---

## Primitive 7: batch

### Endpoint
```
POST /v1/batch
```

### Purpose
Execute multiple primitive operations in a single request for efficient batch processing. Operations run in parallel where possible.

### Request Body

```json
{
  "operations": [
    {
      "primitive": "search.companies",
      "params": {
        "ticker": "AAPL,MSFT",
        "fields": "ticker,name,net_leverage_ratio"
      }
    },
    {
      "primitive": "search.bonds",
      "params": {
        "ticker": "AAPL",
        "has_pricing": true
      }
    },
    {
      "primitive": "resolve.bond",
      "params": {
        "q": "AAPL 3.85% 2046"
      }
    }
  ]
}
```

### Supported Primitives
| Primitive | Description |
|-----------|-------------|
| `search.companies` | Maps to GET /v1/companies |
| `search.bonds` | Maps to GET /v1/bonds |
| `resolve.bond` | Maps to GET /v1/bonds/resolve |
| `traverse.entities` | Maps to POST /v1/entities/traverse |
| `search.pricing` | Maps to GET /v1/bonds?has_pricing=true (deprecated: /v1/pricing) |
| `search.documents` | Maps to GET /v1/documents/search |

### Limits
- Maximum 10 operations per batch request
- Each operation counts against rate limits individually
- Operations execute in parallel for performance

### Example Request
```bash
# Get company metrics AND bonds in one call
curl -X POST "https://api.debtstack.ai/v1/batch" \
  -H "Authorization: Bearer ds_live_xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "operations": [
      {
        "primitive": "search.companies",
        "params": {
          "ticker": "RIG",
          "fields": "ticker,name,net_leverage_ratio,total_debt"
        }
      },
      {
        "primitive": "search.bonds",
        "params": {
          "ticker": "RIG",
          "has_pricing": true,
          "fields": "name,cusip,maturity_date,pricing"
        }
      }
    ]
  }'
```

### Example Response
```json
{
  "results": [
    {
      "primitive": "search.companies",
      "status": "success",
      "data": [
        {
          "ticker": "RIG",
          "name": "Transocean Ltd.",
          "net_leverage_ratio": 4.2,
          "total_debt": 750000000000
        }
      ],
      "meta": {"total": 1}
    },
    {
      "primitive": "search.bonds",
      "status": "success",
      "data": [
        {
          "name": "8.00% Senior Notes due 2027",
          "cusip": "893830AK8",
          "maturity_date": "2027-02-01",
          "pricing": {
            "last_price": 94.25,
            "ytm": 9.42,
            "spread": 512
          }
        }
      ],
      "meta": {"total": 6}
    }
  ],
  "meta": {
    "total_operations": 2,
    "successful": 2,
    "failed": 0
  }
}
```

### Error Handling
If one operation fails, other operations still execute. Check individual `status` fields:

```json
{
  "results": [
    {
      "primitive": "search.companies",
      "status": "success",
      "data": [...]
    },
    {
      "primitive": "resolve.bond",
      "status": "error",
      "error": {
        "code": "INVALID_QUERY",
        "message": "No matching bonds found for query 'XYZ 5% 2025'"
      }
    }
  ]
}
```

---

## Primitive 8: changes

### Endpoint
```
GET /v1/companies/{ticker}/changes
```

### Purpose
Compare current company data against a historical snapshot to identify what changed. Useful for monitoring debt structure changes, new issuances, maturities, and metric shifts.

### Query Parameters

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| `since` | date | **Required.** Compare changes since this date (YYYY-MM-DD) | `2025-01-01` |

### How It Works
1. The system stores periodic snapshots of company data (quarterly by default)
2. This endpoint compares the current live data against the snapshot from the specified date
3. Returns a diff showing what changed between then and now

### Example Request
```bash
# What changed for RIG since Q4 2025?
curl "https://api.debtstack.ai/v1/companies/RIG/changes?since=2025-10-01" \
  -H "Authorization: Bearer ds_live_xxx"
```

### Example Response
```json
{
  "data": {
    "ticker": "RIG",
    "company_name": "Transocean Ltd.",
    "snapshot_date": "2025-10-01",
    "current_date": "2026-01-18",
    "changes": {
      "new_debt": [
        {
          "name": "9.00% Senior Secured Notes due 2029",
          "cusip": "893830AM4",
          "principal": 50000000000,
          "seniority": "senior_secured",
          "issue_date": "2025-11-15"
        }
      ],
      "removed_debt": [
        {
          "name": "6.50% Senior Notes due 2025",
          "cusip": "893830AE2",
          "principal": 30000000000,
          "reason": "matured"
        }
      ],
      "entity_changes": {
        "added": 2,
        "removed": 0,
        "added_entities": [
          {"name": "Transocean Offshore Services Ltd.", "entity_type": "subsidiary"}
        ]
      },
      "metric_changes": {
        "total_debt": {
          "previous": 720000000000,
          "current": 750000000000,
          "change": 30000000000,
          "change_pct": 4.2
        },
        "net_leverage_ratio": {
          "previous": 3.9,
          "current": 4.2,
          "change": 0.3
        },
        "guarantor_count": {
          "previous": 45,
          "current": 47,
          "change": 2
        }
      },
      "pricing_changes": [
        {
          "cusip": "893830AK8",
          "name": "8.00% Senior Notes due 2027",
          "previous_price": 96.50,
          "current_price": 94.25,
          "price_change": -2.25,
          "previous_ytm": 8.75,
          "current_ytm": 9.42,
          "ytm_change": 0.67
        }
      ]
    },
    "summary": {
      "debt_added": 50000000000,
      "debt_removed": 30000000000,
      "net_debt_change": 20000000000,
      "new_issuances": 1,
      "maturities": 1,
      "entity_changes": 2
    }
  },
  "meta": {
    "snapshot_type": "quarterly",
    "days_between": 109
  }
}
```

### Use Cases

**Monitor New Issuances:**
```python
response = requests.get(
    f"{BASE_URL}/companies/CHTR/changes",
    params={"since": "2025-07-01"},
    headers={"Authorization": f"Bearer {API_KEY}"}
)

changes = response.json()["data"]["changes"]
for new_bond in changes.get("new_debt", []):
    print(f"New issuance: {new_bond['name']} - ${new_bond['principal']/100_000_000_000:.1f}B")
```

**Track Leverage Changes:**
```python
metrics = response.json()["data"]["changes"].get("metric_changes", {})
if "net_leverage_ratio" in metrics:
    prev = metrics["net_leverage_ratio"]["previous"]
    curr = metrics["net_leverage_ratio"]["current"]
    print(f"Net leverage: {prev}x → {curr}x ({'+' if curr > prev else ''}{curr-prev:.1f}x)")
```

**Identify Maturing Debt:**
```python
removed = response.json()["data"]["changes"].get("removed_debt", [])
matured = [d for d in removed if d.get("reason") == "matured"]
print(f"{len(matured)} bonds matured since {since_date}")
```

### Notes
- Snapshots are created quarterly by default (can also be monthly or manual)
- If no snapshot exists for the requested date, returns the closest available snapshot
- Changes are computed by diffing current live data against the historical snapshot
- Pricing changes only shown for bonds that have pricing data in both periods

---

## Agent Code Examples

### Question 1: "Which of the MAG7 has the highest net leverage?"

```python
import requests

API_KEY = "ds_live_xxx"
BASE_URL = "https://api.debtstack.ai/v1"

# Single API call with field selection
response = requests.get(
    f"{BASE_URL}/companies",
    params={
        "ticker": "AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA",
        "fields": "ticker,name,net_leverage_ratio",
        "sort": "-net_leverage_ratio",
        "limit": 1
    },
    headers={"Authorization": f"Bearer {API_KEY}"}
)

result = response.json()["data"][0]
print(f"{result['name']} ({result['ticker']}): {result['net_leverage_ratio']}x net leverage")
```

### Question 2: "Show me all BBB bonds yielding more than 8%"

```python
response = requests.get(
    f"{BASE_URL}/bonds",
    params={
        "rating_bucket": "IG",  # BBB is investment grade
        "min_ytm": 8.0,
        "has_pricing": True,
        "fields": "name,cusip,company_ticker,coupon_rate,maturity_date,pricing",
        "sort": "-pricing.ytm"
    },
    headers={"Authorization": f"Bearer {API_KEY}"}
)

for bond in response.json()["data"]:
    print(f"{bond['company_ticker']} {bond['name']}: {bond['pricing']['ytm']:.2f}% YTM")
```

### Question 3: "What does Company A's maturity wall look like over the next 5 years?"

```python
from datetime import date, timedelta

five_years = date.today() + timedelta(days=5*365)

response = requests.get(
    f"{BASE_URL}/bonds",
    params={
        "ticker": "CHTR",
        "maturity_before": five_years.isoformat(),
        "fields": "name,maturity_date,outstanding,seniority",
        "sort": "maturity_date"
    },
    headers={"Authorization": f"Bearer {API_KEY}"}
)

# Group by year
by_year = {}
for bond in response.json()["data"]:
    year = bond["maturity_date"][:4]
    by_year[year] = by_year.get(year, 0) + bond["outstanding"]

for year, amount in sorted(by_year.items()):
    print(f"{year}: ${amount/100_000_000_000:.1f}B")
```

### Question 4: "Which entities are the borrowers and guarantors of Bond B?"

```python
response = requests.post(
    f"{BASE_URL}/entities/traverse",
    headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    },
    json={
        "start": {"type": "bond", "id": "89157VAG8"},
        "relationships": ["guarantees"],
        "direction": "inbound",
        "fields": ["name", "entity_type", "jurisdiction", "is_guarantor", "is_borrower"]
    }
)

data = response.json()["data"]
print(f"Bond: {data['start']['name']}")
print("Guarantors:")
for entity in data["traversal"]["entities"]:
    role = "Borrower + Guarantor" if entity.get("is_borrower") else "Guarantor"
    print(f"  - {entity['name']} ({entity['entity_type']}) - {role}")
```

### Question 5: "Why is Bond B trading at a higher yield than Bond A (same issuer)?"

```python
# Get pricing for both bonds
response = requests.get(
    f"{BASE_URL}/pricing",
    params={
        "cusip": "893830AK8,893830AL6",  # Two RIG bonds
        "fields": "cusip,bond_name,last_price,ytm,spread,seniority,maturity_date"
    },
    headers={"Authorization": f"Bearer {API_KEY}"}
)

bonds = sorted(response.json()["data"], key=lambda x: x["ytm"])
bond_a, bond_b = bonds[0], bonds[1]

print(f"Bond A: {bond_a['bond_name']}")
print(f"  YTM: {bond_a['ytm']:.2f}%, Spread: {bond_a['spread']}bps")
print(f"  Seniority: {bond_a['seniority']}, Maturity: {bond_a['maturity_date']}")
print()
print(f"Bond B: {bond_b['bond_name']}")
print(f"  YTM: {bond_b['ytm']:.2f}%, Spread: {bond_b['spread']}bps")
print(f"  Seniority: {bond_b['seniority']}, Maturity: {bond_b['maturity_date']}")
print()
print("Potential reasons for yield difference:")
if bond_a["seniority"] != bond_b["seniority"]:
    print(f"  - Different seniority: {bond_a['seniority']} vs {bond_b['seniority']}")
if bond_a["maturity_date"] != bond_b["maturity_date"]:
    print(f"  - Different maturity: {bond_a['maturity_date']} vs {bond_b['maturity_date']}")
```

### Question 6: "Does Bond A have a guarantee from the parent company?"

```python
# Find guarantors for the bond
response = requests.post(
    f"{BASE_URL}/entities/traverse",
    headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    },
    json={
        "start": {"type": "bond", "id": "893830AK8"},
        "relationships": ["guarantees"],
        "direction": "inbound",
        "fields": ["name", "entity_type", "is_guarantor"]
    }
)

guarantors = response.json()["data"]["traversal"]["entities"]
holdco_guarantors = [g for g in guarantors if g["entity_type"] == "holdco"]

if holdco_guarantors:
    print(f"Yes! Parent company guarantee from: {holdco_guarantors[0]['name']}")
else:
    print("No parent company guarantee. Guarantors are:")
    for g in guarantors:
        print(f"  - {g['name']} ({g['entity_type']})")
```

### Question 7: "Break down Company A's secured vs unsecured debt"

```python
# Use field selection to get debt breakdown
response = requests.get(
    f"{BASE_URL}/companies",
    params={
        "ticker": "RIG",
        "fields": "ticker,name,total_debt,secured_debt,unsecured_debt,secured_leverage"
    },
    headers={"Authorization": f"Bearer {API_KEY}"}
)

data = response.json()["data"][0]
total = data["total_debt"] / 100_000_000_000
secured = data["secured_debt"] / 100_000_000_000
unsecured = data["unsecured_debt"] / 100_000_000_000
secured_pct = (data["secured_debt"] / data["total_debt"]) * 100

print(f"{data['name']} ({data['ticker']}) Debt Breakdown:")
print(f"  Total Debt:     ${total:.2f}B")
print(f"  Secured Debt:   ${secured:.2f}B ({secured_pct:.1f}%)")
print(f"  Unsecured Debt: ${unsecured:.2f}B ({100-secured_pct:.1f}%)")
```

### Question 8: "List all of Company A's Joint Ventures"

```python
response = requests.post(
    f"{BASE_URL}/entities/traverse",
    headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    },
    json={
        "start": {"type": "company", "id": "RIG"},
        "relationships": ["subsidiaries"],
        "direction": "outbound",
        "depth": 10,
        "filters": {"entity_type": ["jv"]},
        "fields": ["name", "jurisdiction", "ownership_pct", "jv_partner_name"]
    }
)

jvs = response.json()["data"]["traversal"]["entities"]
print(f"Joint Ventures for RIG ({len(jvs)} found):")
for jv in jvs:
    pct = jv.get("ownership_pct", "Unknown")
    partner = jv.get("jv_partner_name", "Unknown partner")
    print(f"  - {jv['name']} ({pct}% ownership, partner: {partner})")
```

---

## REST vs GraphQL Comparison

### Example 1: Get company with specific fields

**REST (Simple):**
```python
requests.get("/v1/companies?ticker=RIG&fields=ticker,name,net_leverage")
```

**GraphQL (Complex):**
```python
query = """
query {
  company(ticker: "RIG") {
    ticker
    name
    metrics {
      netLeverageRatio
    }
  }
}
"""
requests.post("/graphql", json={"query": query})
```

### Example 2: Search bonds with filters

**REST (Simple):**
```python
requests.get("/v1/bonds?seniority=senior_unsecured&min_ytm=8.0&sort=-ytm")
```

**GraphQL (Complex):**
```python
query = """
query {
  bonds(seniority: "senior_unsecured", minYtmPct: 8.0) {
    name
    pricing {
      ytmPct
    }
  }
}
"""
requests.post("/graphql", json={"query": query})
```

### Example 3: Get bond guarantors

**REST (Simple):**
```python
requests.post("/v1/entities/traverse", json={
    "start": {"type": "bond", "id": "89157VAG8"},
    "relationships": ["guarantees"],
    "direction": "inbound"
})
```

**GraphQL (Complex):**
```python
query = """
query {
  bond(cusip: "89157VAG8") {
    name
    guarantors {
      name
      entityType
    }
  }
}
"""
requests.post("/graphql", json={"query": query})
```

**Why REST wins for agents:**
1. No query language to construct
2. Standard HTTP semantics
3. Works with curl, any HTTP library
4. Easier to debug and log
5. Better caching with GET requests

---

## Implementation Priority

### Phase 1: Core Search (Week 1-2)
1. `GET /v1/companies` - Extend existing endpoint with field selection
2. `GET /v1/bonds` - Extend existing `/search/debt` with more filters
3. `GET /v1/bonds/resolve` - New endpoint for identifier resolution

### Phase 2: Traversal & Pricing (Week 3-4)
4. `POST /v1/entities/traverse` - New graph traversal endpoint
5. `GET /v1/pricing` - Extract from existing pricing endpoints

### Phase 3: Documents (Week 5-6)
6. `GET /v1/companies/{ticker}/documents` - Requires document storage/indexing

### Database Indexes Needed
```sql
-- For bonds search
CREATE INDEX idx_debt_seniority_maturity ON debt_instruments(seniority, maturity_date);
CREATE INDEX idx_debt_company_seniority ON debt_instruments(company_id, seniority);
CREATE INDEX idx_debt_cusip ON debt_instruments(cusip) WHERE cusip IS NOT NULL;

-- For pricing search
CREATE INDEX idx_pricing_ytm ON bond_pricing(ytm_bps) WHERE ytm_bps IS NOT NULL;
CREATE INDEX idx_pricing_spread ON bond_pricing(spread_to_treasury_bps) WHERE spread_to_treasury_bps IS NOT NULL;

-- For entity traversal
CREATE INDEX idx_guarantees_guarantor ON guarantees(guarantor_id);
CREATE INDEX idx_entities_company_type ON entities(company_id, entity_type);
```

### Caching Strategy
- **Company list:** Redis, 5 min TTL
- **Bond search:** Redis, 1 min TTL (pricing changes)
- **Entity traversal:** Redis, 10 min TTL
- **Pricing:** Redis, 1 min TTL
- Use ETag headers for client-side caching

---

## SDK Preview (V2)

```python
from debtstack import DebtStack

ds = DebtStack(api_key="ds_live_xxx")

# Search companies
companies = ds.companies.search(
    ticker=["AAPL", "MSFT", "GOOGL"],
    fields=["ticker", "name", "net_leverage_ratio"],
    sort="-net_leverage_ratio"
)

# Search bonds
bonds = ds.bonds.search(
    seniority="senior_unsecured",
    min_ytm=8.0,
    has_pricing=True
)

# Traverse entities
guarantors = ds.entities.traverse(
    start=ds.Bond("89157VAG8"),
    relationships=["guarantees"],
    direction="inbound"
)

# Resolve bond
bond = ds.bonds.resolve("RIG 8% 2027")
print(bond.cusip)  # "893830AK8"
```

---

## Summary

This API design provides:

1. **8 atomic primitives** that can answer any credit analysis question:
   - `search.companies` - Company screening with field selection
   - `search.bonds` - Bond search with yield/spread filters
   - `search.documents` - Full-text search across SEC filings
   - `traverse.entities` - Graph traversal for guarantor chains
   - `search.pricing` - Bond pricing from FINRA TRACE
   - `resolve.bond` - CUSIP/identifier resolution
   - `batch` - Execute multiple operations in one call
   - `changes` - Track debt structure changes over time
2. **Simple REST semantics** - GET for reads, POST for complex operations
3. **Field selection** - Agents request only what they need
4. **Powerful filtering** - Rich query parameters for precise searches
5. **Consistent responses** - Predictable JSON structure
6. **Clear errors** - Actionable error messages with suggestions
7. **Composability** - Chain multiple calls to answer complex questions
8. **Batch operations** - Efficient multi-query requests
9. **Change tracking** - Historical diff/changelog for monitoring

The design follows proven patterns from Stripe, Plaid, and other infrastructure APIs that developers already know and trust.
