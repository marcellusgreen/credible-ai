# CLAUDE.md

Context for AI assistants working on the DebtStack.ai codebase.

> **IMPORTANT**: Before starting work, read `WORKPLAN.md` for current priorities, active tasks, and session history. Update the session log at the end of each session.

## Project Overview

DebtStack.ai is a credit data API for AI agents. It extracts corporate structure and debt information from SEC filings, then serves pre-computed responses via a FastAPI REST API.

**Target users**: AI agents that need credit analysis data (corporate structure, debt details, structural subordination).

## Current Status (January 2026)

**Database**: 189 companies | 5,979 entities | 2,849 debt instruments | 30 priced bonds | 5,750 document sections | 4,881 guarantees | 230 collateral records

**Deployment**: Railway with Neon PostgreSQL + Upstash Redis
- Live at: `https://credible-ai-production.up.railway.app`

**What's Working**:
- **Primitives API**: 8 core endpoints optimized for AI agents (field selection, powerful filters)
- **Legacy REST API**: 26 endpoints for detailed company data
- Iterative extraction with QA feedback loop (5 checks, 85% threshold)
- Gemini for extraction (~$0.008), Claude for escalation
- SEC-API.io integration (paid tier)
- PostgreSQL on Neon Cloud, Redis on Upstash
- S&P 100 / NASDAQ 100 company coverage
- Financial statement extraction (income, balance sheet, cash flow)
- Credit ratio calculations
- Bond pricing with YTM and spread calculations

## Architecture

```
SEC-API.io (10-K, 10-Q, 8-K, Exhibits)
    ↓
Gemini Extraction (~$0.008)
    ↓
QA Agent: 5 verification checks (~$0.006)
    ↓
Score >= 85%? → PostgreSQL → Cache → API
    ↓ No
Targeted Fixes → Loop up to 3x → Escalate to Claude
```

## Key Files

| File | Purpose |
|------|---------|
| `app/api/primitives.py` | **Primitives API** - 8 core endpoints for agents |
| `app/api/routes.py` | Legacy FastAPI endpoints (26 routes) |
| `app/core/cache.py` | Redis cache client (Upstash) |
| `app/services/iterative_extraction.py` | Main extraction with QA loop |
| `app/services/qa_agent.py` | 5-check QA verification |
| `app/services/tiered_extraction.py` | LLM clients and prompts |
| `app/services/extraction.py` | SEC clients, filing processing |
| `app/services/financial_extraction.py` | Financial statements with TTM support |
| `app/models/schema.py` | SQLAlchemy models |
| `app/core/config.py` | Environment configuration |
| `app/core/database.py` | Database connection |

## Database Schema

**Core Tables**:
- `companies`: Master company data (ticker, name, CIK, sector)
- `entities`: Corporate entities with hierarchy (`parent_id`, VIE tracking)
- `ownership_links`: Complex ownership (JVs, partial ownership)
- `debt_instruments`: All debt with terms, linked via `issuer_id`
- `guarantees`: Links debt to guarantor entities
- `collateral`: Asset-backed collateral for secured debt (type, description, priority)
- `company_financials`: Quarterly financial statements (amounts in cents)
- `bond_pricing`: Pricing data (YTM, spreads in basis points)
- `document_sections`: SEC filing sections for full-text search (TSVECTOR + GIN index)

**Cache Tables**:
- `company_cache`: Pre-computed JSON responses
- `company_metrics`: Computed credit metrics

## API Endpoints

### Primitives API (8 endpoints - optimized for agents)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/companies` | GET | Search companies with field selection |
| `/v1/bonds` | GET | Search bonds with pricing filters |
| `/v1/bonds/resolve` | GET | Resolve CUSIP/bond identifiers |
| `/v1/entities/traverse` | POST | Graph traversal (guarantors, structure) |
| `/v1/pricing` | GET | Bond pricing data |
| `/v1/documents/search` | GET | Full-text search across SEC filings |
| `/v1/batch` | POST | Execute multiple primitives in parallel |
| `/v1/companies/{ticker}/changes` | GET | Diff against historical snapshots |

**Field selection**: `?fields=ticker,name,net_leverage_ratio`
**Sorting**: `?sort=-net_leverage_ratio` (prefix `-` for descending)
**Filtering**: `?min_ytm=8.0&seniority=senior_unsecured`

### Legacy REST Endpoints (26 total)

**Company**: `/v1/companies/{ticker}` - overview, structure, hierarchy, debt, metrics, financials, ratios, pricing, guarantees, entities, maturity-waterfall

**Search**: `/v1/search/companies`, `/v1/search/debt`, `/v1/search/entities`

**Analytics**: `/v1/compare/companies`, `/v1/analytics/sectors`

**System**: `/v1/ping`, `/v1/health`, `/v1/status`, `/v1/sectors`

## Key Design Decisions

1. **Amounts in CENTS**: `$1 billion = 100_000_000_000 cents`
2. **Rates in BASIS POINTS**: `8.50% = 850 bps`
3. **Individual instruments**: Extract each bond separately, not totals
4. **Name normalization**: Case-insensitive, punctuation-normalized
5. **Robust JSON parsing**: `parse_json_robust()` handles LLM output issues
6. **Estimated data must be flagged**: When data cannot be extracted from source documents after repeated attempts and must be estimated/inferred, it MUST be clearly marked as estimated. Users should always know when they're seeing inferred data vs. extracted data. Example: `issue_date_estimated: true` indicates the date was inferred from maturity date and typical bond tenors, not extracted from SEC filings.

## Running Extractions

```bash
# Single company with QA
python scripts/extract_iterative.py --ticker AAPL --cik 0000320193 --save-db

# Batch extraction
python scripts/batch_index.py --phase 1

# Extract financials (single quarter)
python scripts/extract_financials.py --ticker CHTR --save-db

# Extract TTM financials (4 quarters - recommended for leverage ratios)
python scripts/extract_financials.py --ticker CHTR --ttm --save-db

# Update pricing
python scripts/update_pricing.py --ticker AAPL

# Recompute metrics (after extracting financials)
python scripts/recompute_metrics.py --ticker CHTR
```

### TTM (Trailing Twelve Months) Extraction

For accurate leverage ratios, use `--ttm` to extract 4 quarters of financial data:
- Fetches most recent 3 10-Qs (Q1, Q2, Q3) + 1 10-K (full year as Q4 proxy)
- Uses `periodOfReport` from SEC API to determine fiscal quarter
- TTM EBITDA = Sum of 4 quarters (more accurate than annualizing single quarter)
- If fewer than 4 quarters available, annualizes what's available

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `DATABASE_URL` | Yes | PostgreSQL (use `sslmode=require` for Neon) |
| `REDIS_URL` | Optional | Redis cache (Upstash, use `rediss://` for TLS) |
| `ANTHROPIC_API_KEY` | Yes | Claude for escalation |
| `GEMINI_API_KEY` | Recommended | Gemini for extraction |
| `SEC_API_KEY` | Recommended | SEC-API.io for filing retrieval |

## Deployment

**Railway** (recommended):
- Config: `railway.json`
- Dockerfile for container builds
- Health check: `/v1/ping` (simple) or `/v1/health` (with DB)
- Environment variables set in Railway dashboard

**Local**:
```bash
uvicorn app.main:app --reload
```

## Migrations

```bash
alembic upgrade head     # Apply all
alembic revision -m "description"  # Create new
```

Current migrations: 001 (initial) through 012 (collateral_table)

## Common Issues

| Issue | Solution |
|-------|----------|
| LLM returns debt totals | Prompt requires individual instruments |
| Entity name mismatch | `normalize_name()` handles case/punctuation |
| Large filing truncation | `extract_debt_sections()` extracts relevant portions |
| JSON parse errors | `parse_json_robust()` fixes common issues |
| Company not found by ticker | Falls back to CIK search |

### QA False Positives

| Symptom | Root Cause | Solution |
|---------|------------|----------|
| 99% debt discrepancies | QA comparing cents to dollars | Prompt has worked examples with explicit conversion |
| 68-89% debt discrepancies | QA using original issuance not current outstanding | Prompt distinguishes "2009 issuance of $3.8B" header vs "$520M" outstanding |
| Entity verification fails with valid data | Exhibit 21 contains auditor consent, not subsidiaries | `is_valid_exhibit_21()` validates content before storing |
| Missing debt footnote | Non-standard naming like "3. Long-Term Obligations" | `DEBT_FOOTNOTE_PATTERNS` includes numbered sections |

See `docs/operations/QA_TROUBLESHOOTING.md` for detailed debugging guides.

## Cost

| Stage | Cost |
|-------|------|
| Gemini extraction | ~$0.008 |
| QA checks (5x) | ~$0.006 |
| Fix iteration | ~$0.01 |
| Claude escalation | ~$0.15-0.50 |

**Target: <$0.03 per company** with 85%+ QA score.

## Agent Usage Examples

```python
import requests

BASE = "https://credible-ai-production.up.railway.app/v1"

# Q: Which MAG7 company has highest leverage?
r = requests.get(f"{BASE}/companies", params={
    "ticker": "AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA",
    "fields": "ticker,name,net_leverage_ratio",
    "sort": "-net_leverage_ratio",
    "limit": 1
})

# Q: Find high-yield bonds
r = requests.get(f"{BASE}/bonds", params={
    "seniority": "senior_unsecured",
    "min_ytm": 8.0,
    "has_pricing": True
})

# Q: Who guarantees this bond?
r = requests.post(f"{BASE}/entities/traverse", json={
    "start": {"type": "bond", "id": "893830AK8"},
    "relationships": ["guarantees"],
    "direction": "inbound"
})

# Q: Resolve bond identifier
r = requests.get(f"{BASE}/bonds/resolve", params={"q": "RIG 8% 2027"})

# Q: Search for covenant language in credit agreements
r = requests.get(f"{BASE}/documents/search", params={
    "q": "maintenance covenant",
    "section_type": "credit_agreement",
    "ticker": "CHTR"
})

# Q: Find indentures with redemption provisions
r = requests.get(f"{BASE}/documents/search", params={
    "q": "optional redemption",
    "section_type": "indenture"
})
```

See `docs/PRIMITIVES_API_SPEC.md` for full specification.
