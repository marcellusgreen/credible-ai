# CLAUDE.md

Context for AI assistants working on the DebtStack.ai codebase.

## Project Overview

DebtStack.ai is a credit data API for AI agents. It extracts corporate structure and debt information from SEC filings, then serves pre-computed responses via a FastAPI REST API.

**Target users**: AI agents that need credit analysis data (corporate structure, debt details, structural subordination).

## Current Status (January 2026)

**Database**: 178 companies | 3,085 entities | 1,805 debt instruments | 30 priced bonds

**Deployment**: Railway (in progress) with Neon PostgreSQL

**What's Working**:
- Iterative extraction with QA feedback loop (5 checks, 85% threshold)
- Gemini for extraction (~$0.008), Claude for escalation
- SEC-API.io integration (paid tier)
- PostgreSQL on Neon Cloud
- FastAPI REST API (26 endpoints)
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
| `app/api/routes.py` | FastAPI endpoints (26 routes) |
| `app/services/iterative_extraction.py` | Main extraction with QA loop |
| `app/services/qa_agent.py` | 5-check QA verification |
| `app/services/tiered_extraction.py` | LLM clients and prompts |
| `app/services/extraction.py` | SEC clients, filing processing |
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
- `company_financials`: Quarterly financial statements (amounts in cents)
- `bond_pricing`: Pricing data (YTM, spreads in basis points)

**Cache Tables**:
- `company_cache`: Pre-computed JSON responses
- `company_metrics`: Computed credit metrics

## API Endpoints (26 total)

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

## Running Extractions

```bash
# Single company with QA
python scripts/extract_iterative.py --ticker AAPL --cik 0000320193 --save-db

# Batch extraction
python scripts/batch_index.py --phase 1

# Extract financials
python scripts/extract_financials.py --ticker CHTR --save-db

# Update pricing
python scripts/update_pricing.py --ticker AAPL
```

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `DATABASE_URL` | Yes | PostgreSQL (use `sslmode=require` for Neon) |
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

Current migrations: 001 (initial) through 007 (cusip nullable)

## Common Issues

| Issue | Solution |
|-------|----------|
| LLM returns debt totals | Prompt requires individual instruments |
| Entity name mismatch | `normalize_name()` handles case/punctuation |
| Large filing truncation | `extract_debt_sections()` extracts relevant portions |
| JSON parse errors | `parse_json_robust()` fixes common issues |
| Company not found by ticker | Falls back to CIK search |

## Cost

| Stage | Cost |
|-------|------|
| Gemini extraction | ~$0.008 |
| QA checks (5x) | ~$0.006 |
| Fix iteration | ~$0.01 |
| Claude escalation | ~$0.15-0.50 |

**Target: <$0.03 per company** with 85%+ QA score.
