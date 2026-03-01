# CLAUDE.md

Context for AI assistants working on the DebtStack.ai codebase.

> **IMPORTANT**: Before starting work, read `WORKPLAN.md` for current priorities, active tasks, and session history. Update the session log at the end of each session.

## What's Next

See `WORKPLAN.md` for full session history and priorities.

**All major infrastructure complete:**
- ~~Stripe billing~~ ✅ (2026-02-03) — Products, webhooks, checkout flow, 14/14 E2E tests passing
- ~~Ownership enrichment~~ ✅ (2026-02-24) — 13,113 entities with known parents (32% of non-root), 13,077 ownership links
- ~~Debt coverage~~ ✅ (2026-02-19) — 314/314 companies accounted for (197 genuinely OK + 94 benchmark-adjusted + 23 new)
- ~~Document linking~~ ✅ — 96.4% coverage (5,512/5,719 instruments linked)
- ~~Company expansion~~ ✅ — 211 → 314 companies
- ~~SDK/MCP/Docs~~ ✅ — PyPI v0.1.3, 6 MCP directories, docs.debtstack.ai
- ~~Chat assistant~~ ✅ — Gemini + 9 DebtStack API tools via SSE streaming
- ~~Analytics & alerting~~ ✅ — Vercel, PostHog, Sentry, Slack
- ~~QC~~ ✅ — 0 critical, 0 errors

**Remaining:**
1. Update API documentation with tier requirements
2. Grow usage / marketing

## Project Overview

DebtStack.ai is a credit data API for AI agents. It extracts corporate structure and debt information from SEC filings, then serves pre-computed responses via a FastAPI REST API.

**Target users**: AI agents that need credit analysis data (corporate structure, debt details, structural subordination).

## Current Status (February 2026)

**Database**: 314 companies | 5,838 active debt instruments | 3,131 with CUSIP | 3,064 with pricing | ~29,664 document sections | 5,962 guarantees | 1,088 collateral records | 2,088 covenants | 40,724 entities | ~2,631 financial quarters

**Deployment**: Railway with Neon PostgreSQL + Upstash Redis. Live at `https://api.debtstack.ai`.

**Key Metrics**:
- Document Linkage: 96.4% (5,512/5,719 instruments linked)
- Pricing Coverage: 3,064 bonds with TRACE pricing (95.8% of CUSIPs). All estimated/synthetic removed — only real TRACE data. Updated 3x daily by APScheduler (11 AM, 3 PM, 9 PM ET).
- Debt Coverage: 197 genuinely OK + 94 benchmark-adjusted = 314/314 accounted for. Overall $5,838B/$7,980B = 73.2%.
- Ownership: 13,113 entities with known parents (32% of non-root). Sources: LLM (5,647), GLEIF (3,415), UK Companies House (1,362).
- Data Quality: QC audit passing — 0 critical, 3 errors (all legitimate), 11 warnings.
- Eval Suite: 121/136 tests passing (89.0%).
- Finnhub Discovery: All 314 companies scanned. Cache: 271 companies, 1,904 bonds. **COMPLETE.**

**What's Working**:
- Chat Assistant: `/dashboard/chat` — Gemini 2.5 Pro + 9 DebtStack API tools via SSE streaming
- Three-Tier Pricing: Pay-as-You-Go ($0 + per-call), Pro ($199/mo), Business ($499/mo)
- Primitives API: 12 core endpoints optimized for AI agents (field selection, powerful filters)
- Auth & Credits: API key auth, tier-based access control, usage tracking
- Legacy REST API: 26 endpoints for detailed company data
- Observability: Vercel Analytics, PostHog, Sentry, Slack alerts
- Iterative extraction with QA feedback loop (5 checks, 85% threshold)
- Gemini for extraction (~$0.008), Claude for escalation

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

### Code Organization

**Utilities** (stateless helpers — no DB, no API calls):

| File | Domain |
|------|--------|
| `app/services/utils.py` | Text/parsing: `parse_json_robust`, `normalize_name`, `parse_date` |
| `app/services/extraction_utils.py` | SEC filings: `clean_filing_html`, `combine_filings`, `extract_debt_sections` |
| `app/services/llm_utils.py` | LLM clients: `get_gemini_model`, `call_gemini`, `LLMResponse` |
| `app/services/yield_calculation.py` | Financial math: YTM, duration, treasury yields |

**Services** (orchestrate DB, APIs, workflows):

| File | Purpose |
|------|---------|
| `app/services/sec_client.py` | SEC filing clients (`SecApiClient`, `SECEdgarClient`) |
| `app/services/extraction.py` | Core extraction + DB persistence |
| `app/services/iterative_extraction.py` | Main extraction with QA loop (entry point) |
| `app/services/tiered_extraction.py` | LLM clients, prompts, escalation |
| `app/services/base_extractor.py` | Base class for LLM extraction services |
| `app/services/section_extraction.py` | Document section extraction and storage |
| `app/services/document_linking.py` | Link instruments to source documents |
| `app/services/hierarchy_extraction.py` | Exhibit 21 + indenture ownership enrichment |
| `app/services/financial_extraction.py` | TTM financial statements from 10-K/10-Q |
| `app/services/guarantee_extraction.py` | Guarantee extraction from indentures |
| `app/services/collateral_extraction.py` | Collateral for secured debt |
| `app/services/covenant_extraction.py` | Structured covenant data extraction |
| `app/services/metrics.py` | Credit metrics (leverage, coverage ratios) |
| `app/services/qc.py` | Quality control checks |
| `app/services/qa_agent.py` | 5-check QA verification |
| `app/services/pricing_history.py` | Bond pricing history backfill and daily snapshots |
| `app/services/treasury_yields.py` | Treasury yield curve history from Treasury.gov |

**API Layer**:

| File | Purpose |
|------|---------|
| `app/api/primitives.py` | Primitives API — 12 core endpoints for agents |
| `app/api/auth.py` | Auth API — signup, user info |
| `app/api/routes.py` | Legacy FastAPI endpoints (26 routes) |
| `app/core/auth.py` | API key generation, validation, tier config |
| `app/core/cache.py` | Redis cache client (Upstash) |
| `app/core/scheduler.py` | APScheduler: pricing refresh + alert checks |
| `app/core/posthog.py` | PostHog analytics client |
| `app/core/alerting.py` | Slack webhook alerts |
| `app/core/monitoring.py` | Redis-based API metrics |

**Models & Config**:

| File | Purpose |
|------|---------|
| `app/models/schema.py` | SQLAlchemy models (includes User, UserCredits, UsageLog) |
| `app/core/config.py` | Environment configuration |
| `app/core/database.py` | Database connection |

**Scripts** (thin CLI wrappers importing from services):

| File | Purpose |
|------|---------|
| `scripts/extract_iterative.py` | Complete extraction pipeline |
| `scripts/recompute_metrics.py` | Metrics recomputation |
| `scripts/update_pricing.py` | Bond pricing updates |
| `scripts/backfill_debt_document_links.py` | Heuristic multi-strategy document linking |
| `scripts/link_to_base_indenture.py` | Fallback: link notes → base indenture |
| `scripts/link_to_credit_agreement.py` | Fallback: link loans → credit agreement |
| `scripts/analyze_gaps_v2.py` | Debt coverage gap analysis |
| `scripts/populate_benchmark_debt.py` | Set benchmark_total_debt for banks/utilities |
| `scripts/fetch_prospectus_sections.py` | Fetch 424B prospectus sections ($0) |
| `scripts/extract_intermediate_ownership.py` | LLM extraction of intermediate parents |
| `scripts/script_utils.py` | **Shared utilities** (DB sessions, parsers, progress, Windows handling) |
| `medici/scripts/ingest_knowledge.py` | RAG knowledge ingestion (see `medici/CLAUDE.md`) |

## Database Schema

**Core Tables**:
- `companies`: Master company data (ticker, name, CIK, sector)
- `entities`: Corporate entities with hierarchy (`parent_id`, `is_root`, VIE tracking)
- `ownership_links`: Complex ownership (JVs, partial ownership; `ownership_type`: "direct"/"indirect"/NULL)
- `debt_instruments`: All debt with terms, linked via `issuer_id`
- `guarantees`: Links debt to guarantor entities
- `collateral`: Asset-backed collateral for secured debt (type, description, priority)
- `covenants`: Structured covenant data from credit agreements/indentures
- `company_financials`: Quarterly financial statements (amounts in cents) — see Industry-Specific Metrics below
- `bond_pricing`: Current pricing (YTM, spreads in basis points)
- `bond_pricing_history`: Historical daily snapshots (Business tier only)
- `treasury_yield_history`: Historical US Treasury yield curves (1M-30Y tenors, 2021-present)
- `document_sections`: SEC filing sections for full-text search (TSVECTOR + GIN index)

**Auth Tables**:
- `users`: User accounts (email, api_key_hash, tier, stripe IDs, rate_limit_per_minute)
- `user_credits`: Credit balance and billing cycle
- `usage_log`: API usage tracking (endpoint, cost_usd, tier_at_time_of_request)

**Other Tables**: `company_cache` (pre-computed JSON + `extraction_status` JSONB), `company_metrics`, `team_members`, `coverage_requests`, `knowledge_chunks` (pgvector for RAG — see `medici/CLAUDE.md`)

### Industry-Specific Financial Metrics

The `company_financials.ebitda` field stores the industry-appropriate metric, with `ebitda_type` indicating which:

| `ebitda_type` | Industry | Metric |
|---------------|----------|--------|
| `"ebitda"` | Operating companies | EBITDA |
| `"ppnr"` | Banks | Pre-Provision Net Revenue |
| `"ffo"` | REITs | Funds From Operations |
| `"noi"` | Real Estate | Net Operating Income |

Banks/Financial Institutions: `companies.is_financial_institution = true` (12 companies: AXP, BAC, C, COF, GS, JPM, MS, PNC, SCHW, TFC, USB, WFC). Industry-specific fields in `company_financials`: `net_interest_income`, `non_interest_income`, `non_interest_expense`, `provision_for_credit_losses`.

**To add a new industry type:** Add flag to `companies` → add columns to `company_financials` → Alembic migration → extraction prompt in `financial_extraction.py` → calculation logic in `save_financials_to_db()` → detection in `extract_iterative.py`.

## API Endpoints

### Primitives API (12 endpoints — optimized for agents)

All require `X-API-Key` header (except `/v1/coverage/request`).

| Endpoint | Method | Cost | Purpose |
|----------|--------|------|---------|
| `/v1/companies` | GET | $0.05 | Search companies with field selection |
| `/v1/bonds` | GET | $0.05 | Search/screen bonds with pricing |
| `/v1/bonds/resolve` | GET | $0.05 | Map bond identifiers — free-text to CUSIP |
| `/v1/financials` | GET | $0.05 | Quarterly financial statements |
| `/v1/collateral` | GET | $0.05 | Collateral securing debt |
| `/v1/covenants` | GET | $0.05 | Structured covenant data |
| `/v1/companies/{ticker}/changes` | GET | $0.10 | Diff against historical snapshots |
| `/v1/covenants/compare` | GET | Biz | Compare covenants across companies |
| `/v1/entities/traverse` | POST | $0.15 | Graph traversal (guarantors, structure) |
| `/v1/documents/search` | GET | $0.15 | Full-text search across SEC filings |
| `/v1/batch` | POST | Sum | Execute multiple primitives in parallel |
| `/v1/coverage/request` | POST | Free | Request coverage for non-covered companies |

**Business-Only**: `/v1/bonds/{cusip}/pricing/history`, `/v1/export`, `/v1/usage/analytics`, `/v1/usage/trends`

**Pricing API (public)**: `/v1/pricing/tiers`, `/v1/pricing/calculate`, `/v1/pricing/my-usage`, `/v1/pricing/purchase-credits`, `/v1/pricing/upgrade`

**Auth**: `/v1/auth/signup` (POST), `/v1/auth/me` (GET)

**Legacy REST** (26 routes): `/v1/companies/{ticker}` — overview, structure, hierarchy, debt, metrics, financials, ratios, pricing, guarantees, entities, maturity-waterfall. Search: `/v1/search/companies`, `/v1/search/debt`, `/v1/search/entities`. Analytics: `/v1/compare/companies`, `/v1/analytics/sectors`. System: `/v1/ping`, `/v1/health`, `/v1/status`, `/v1/sectors`.

**Query features**: Field selection (`?fields=ticker,name`), sorting (`?sort=-net_leverage_ratio`), filtering (`?min_ytm=8.0&seniority=senior_unsecured`).

## Key Design Decisions

1. **Amounts in CENTS**: `$1 billion = 100_000_000_000 cents`
2. **Rates in BASIS POINTS**: `8.50% = 850 bps`
3. **Individual instruments**: Extract each bond separately, not totals
4. **Name normalization**: Case-insensitive, punctuation-normalized via `normalize_name()`
5. **Robust JSON parsing**: `parse_json_robust()` handles LLM output issues
6. **Estimated data must be flagged**: Always mark estimated/inferred data (e.g., `issue_date_estimated: true`)
7. **ALWAYS detect scale from source document**: Never assume scale — extract from SEC filing header. `detect_filing_scale()` in `financial_extraction.py`. **Never blindly apply scale fixes** — always verify against the source SEC filing first.

### Debt Instrument Types

Canonical taxonomy with normalization from 50+ LLM output variants:

| Type | Description |
|------|-------------|
| `senior_notes` | Investment-grade or high-yield bonds |
| `senior_secured_notes` | Secured bonds with collateral |
| `subordinated_notes` | Junior/subordinated bonds |
| `convertible_notes` | Bonds convertible to equity |
| `term_loan` | Bank term loans (A, B, or generic) |
| `revolver` | Revolving credit facilities |
| `abl` | Asset-based lending facilities |
| `commercial_paper` | Short-term unsecured notes |
| `equipment_trust` | EETCs, equipment financing |
| `government_loan` | PSP notes, government-backed loans |
| `finance_lease` | Capital/finance lease obligations |
| `mortgage` | Real estate secured debt |
| `bond` | Generic bonds (municipal, revenue, etc.) |
| `debenture` | Unsecured debentures |
| `preferred_stock` | Trust preferred securities |
| `other` | Catch-all for rare types |

The validator in `extraction.py` normalizes 50+ variants (e.g., `eetc` → `equipment_trust`, `notes` → `senior_notes`).

## Adding a New Company

```bash
# Get CIK from SEC EDGAR, then run complete pipeline:
python scripts/extract_iterative.py --ticker XXXX --cik 0001234567 --save-db
```

This runs all 11 steps: SEC download → core extraction with QA → save to DB → document sections → document linking → amount backfill → TTM financials → ownership hierarchy → guarantees → collateral → metrics → QC checks.

**Options**: `--core-only`, `--skip-financials`, `--skip-enrichment`, `--threshold 90`, `--force`, `--all`, `--resume`, `--limit N`

**Modular execution with `--step`**: Run individual steps for existing companies:
```bash
python scripts/extract_iterative.py --ticker AAL --step financials  # Re-extract financials
python scripts/extract_iterative.py --ticker AAL --step cache       # Refresh cache
python scripts/extract_iterative.py --ticker AAL --step metrics     # Update metrics
```

Available steps: `core`, `financials`, `hierarchy`, `guarantees`, `collateral`, `documents`, `amounts`, `covenants`, `metrics`, `finnhub`, `pricing`, `cache`.

**Idempotent**: Tracks extraction status in `company_cache.extraction_status` (JSONB). Status values: `success`, `no_data` (won't retry unless `--force`), `error` (will retry). ~2-5 min per company, ~$0.03-0.08.

### Individual Service CLIs

Each extraction service has its own CLI for debugging:
```bash
python -m app.services.section_extraction --ticker CHTR [--all] [--force]
python -m app.services.document_linking --ticker CHTR [--all --heuristic]
python -m app.services.financial_extraction --ticker CHTR --ttm --save-db
python -m app.services.hierarchy_extraction --ticker CHTR [--all]
python -m app.services.guarantee_extraction --ticker CHTR [--all]
python -m app.services.collateral_extraction --ticker CHTR [--all]
python -m app.services.covenant_extraction --ticker CHTR [--all] [--force]
python -m app.services.metrics --ticker CHTR [--all --dry-run]
python scripts/qc_master.py --ticker CHTR --verbose
python scripts/update_pricing.py --ticker CHTR
```

## Bond Pricing

TRACE pricing via Finnhub/FINRA. See `docs/BOND_PRICING.md` for detailed data flow, tables, and scripts.

**Key points**:
- Finnhub requires **ISIN, not CUSIP**. Convert: `US` + CUSIP + check digit.
- 3,064 bonds with TRACE pricing. All estimated/synthetic removed.
- APScheduler refreshes at 11 AM, 3 PM, 9 PM ET. Daily snapshots at 9 PM ET.
- Historical TRACE fallback when Finnhub returns no current data.
- Tables: `bond_pricing` (current), `bond_pricing_history` (daily snapshots), `treasury_yield_history`

## Testing

5 test layers. See `tests/` directory and `docs/EVAL_FRAMEWORK.md`.

```bash
# Quick: Unit tests only
python -m pytest tests/unit -v -s

# All pytest tests
python -m pytest tests/ -v -s

# Unified runner
python scripts/run_all_tests.py [--quick|--unit|--api|--qc|--json]

# API evaluation
python scripts/run_evals.py [--primitive companies|--update-baseline|--json]

# Data quality
python scripts/qc_master.py [--category integrity]
python scripts/qc_financials.py --verbose
```

**Test counts**: Unit (73), API Contract (15+), Eval Suite (~65), Edge Cases (50+), QC (30+). Target accuracy: 95%+.

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `DATABASE_URL` | Yes | PostgreSQL (use `sslmode=require` for Neon) |
| `REDIS_URL` | Optional | Redis cache (Upstash, use `rediss://` for TLS) |
| `ANTHROPIC_API_KEY` | Yes | Claude for escalation |
| `GEMINI_API_KEY` | Recommended | Gemini for extraction |
| `SEC_API_KEY` | Recommended | SEC-API.io for filing retrieval |
| `FINNHUB_API_KEY` | Optional | Finnhub for bond pricing |
| `SENTRY_DSN` | Optional | Sentry error tracking |
| `SLACK_WEBHOOK_URL` | Optional | Slack alerts |
| `POSTHOG_API_KEY` | Optional | PostHog analytics |

## Deployment

**Railway**: `railway.json`, Dockerfile, health check at `/v1/ping` or `/v1/health`.

**Local**: `uvicorn app.main:app --reload`

**Migrations**: `alembic upgrade head` / `alembic revision -m "description"` (001 through 027)

## Common Issues

| Issue | Solution |
|-------|----------|
| LLM returns debt totals | Prompt requires individual instruments |
| Entity name mismatch | `normalize_name()` handles case/punctuation |
| Large filing truncation | `extract_debt_sections()` extracts relevant portions |
| JSON parse errors | `parse_json_robust()` fixes common issues |
| "I/O operation on closed file" | Duplicate `sys.stdout` wrapping — only wrap in ONE place. If importing `script_utils`, don't wrap manually. |
| "Can't reconnect until invalid transaction is rolled back" | Neon idle connection drop — use fresh session per company (see pattern below) |
| Bank EBITDA shows 0 or NULL | Banks use PPNR not EBITDA — check `is_financial_institution` flag |
| Instrument totals mismatch | Check source SEC filing scale first. Banks include deposits in total_debt. |

### Neon Serverless Connection Pattern

Neon drops idle connections after ~10s. For batch scripts with LLM calls, use fresh session per company:

```python
# Wrong: single session goes idle during LLM calls
async with get_db_session() as session:
    for company in companies:
        result = await slow_operation(session, company)  # FAILS

# Right: fresh session per company
async with get_db_session() as session:
    company_info = [(c.id, c.ticker) for c in await get_companies(session)]

for company_id, ticker in company_info:
    async with get_db_session() as session:  # Fresh per company
        result = await slow_operation(session, company)
```

### Script Utilities (`scripts/script_utils.py`)

All scripts should use `script_utils.py` for consistent DB handling and Windows compatibility:

```python
from script_utils import get_db_session, print_header, run_async

async def main():
    print_header("SCRIPT NAME")
    async with get_db_session() as db:
        pass  # Do work

if __name__ == "__main__":
    run_async(main())
```

Key exports: `get_db_session`, `get_all_companies`, `get_company_by_ticker`, `create_base_parser`, `create_fix_parser`, `process_companies`, `run_async`, `print_header`/`print_summary`/`print_progress`.

**Windows note**: `script_utils` handles stdout UTF-8 encoding and `WindowsSelectorEventLoopPolicy` automatically. Scripts importing from it must NOT also wrap `sys.stdout` manually.

## Cost

| Stage | Cost |
|-------|------|
| Gemini extraction | ~$0.008 |
| QA checks (5x) | ~$0.006 |
| Fix iteration | ~$0.01 |
| Claude escalation | ~$0.15-0.50 |

Target: <$0.03 per company with 85%+ QA score.

## Reference Documentation

| Document | Content |
|----------|---------|
| `docs/BOND_PRICING.md` | Finnhub integration, data flow, tables, scripts, scheduler details |
| `docs/DEBT_COVERAGE_HISTORY.md` | Phases 1-9 backfill history, key learnings, structural gap categories |
| `docs/OWNERSHIP_EXTRACTION.md` | Ownership hierarchy extraction pipeline, Exhibit 21 + prospectus + GLEIF |
| `docs/AGENT_EXAMPLES.md` | Agent user journey, API usage examples, document search patterns |
| `docs/EVAL_FRAMEWORK.md` | Eval framework details and ground truth management |
| `docs/api/PRIMITIVES_API_SPEC.md` | Full Primitives API specification |
| `docs/operations/QA_TROUBLESHOOTING.md` | QA debugging guides |
| `medici/CLAUDE.md` | RAG knowledge base management |
