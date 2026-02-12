# CLAUDE.md

Context for AI assistants working on the DebtStack.ai codebase.

> **IMPORTANT**: Before starting work, read `WORKPLAN.md` for current priorities, active tasks, and session history. Update the session log at the end of each session.

## What's Next

**Immediate**: Phase 7.5 EXCESS cleanup complete — OK companies 73→96, EXCESS_SOME 53→15, EXCESS_SIGNIFICANT steady at 5. Step 8 cleared 36 revolver/ABL capacity amounts ($55B, $0 cost). LLM review at 1.5x threshold reviewed 95 companies ($2.01 cost), deactivating 180 instruments and clearing 91 wrong amounts. Remaining 15 EXCESS_SOME include 3 banks (BAC, C, JPM — structural) and 12 others with minor excess. 5 EXCESS_SIGNIFICANT have known root causes (DO pre-reorg bonds, NEM face values, PAYX/ETN wrong total_debt, UBER wrong backfill amounts). Next: tackle remaining MISSING_SIGNIFICANT (60 companies), fix THC/PAYX total_debt financials. See WORKPLAN.md.

**Then**:
1. Continue company expansion (211 → 288, Tier 2-5 remaining)
2. Complete Finnhub discovery (~50 companies remaining), link discovered bonds to documents
3. SDK publication to PyPI
4. Mintlify docs deployment to docs.debtstack.ai
5. ~~Set up Railway cron job for daily pricing collection~~ ✅ Done — APScheduler in-process
6. ~~Analytics, error tracking & alerting~~ ✅ Done — Vercel Analytics, PostHog, Sentry, Slack alerts

## Project Overview

DebtStack.ai is a credit data API for AI agents. It extracts corporate structure and debt information from SEC filings, then serves pre-computed responses via a FastAPI REST API.

**Target users**: AI agents that need credit analysis data (corporate structure, debt details, structural subordination).

## Current Status (February 2026)

**Database**: 211 companies | 6,016 debt instruments | 2,926 with CUSIP | 4,712 with pricing | 14,511 document sections | 4,165 guarantees | 713 collateral records | 1,247 covenants | 28,128 entities | 1,816 financial quarters

**Company Expansion**: 211 companies (up from 201) — added 10 Tier 1 massive-debt issuers (CMCSA, DUK, CVS, USB, SO, TFC, ET, PNC, PCG, BMY)

**Debt Coverage Gaps**: 96/211 companies (46%) have instrument outstanding within 80-120% of total debt ("OK"). Phases 1-7.5 updated 1,013+ instruments, deactivated 180+ duplicates/aggregates, cleared 127 wrong amounts. EXCESS reduced from 58→20 (15 EXCESS_SOME + 5 EXCESS_SIGNIFICANT). MISSING_ALL at 8 (FTNT, META, ON, PANW, PG, TTD, USB, VAL). 60 MISSING_SIGNIFICANT remain. 5 EXCESS_SIGNIFICANT have known root causes (DO, NEM, PAYX, ETN, UBER).

**Pricing Coverage**: 4,712 bonds with pricing via Finnhub/FINRA TRACE.

**Finnhub Discovery**: 161/211 companies scanned, ~50 remaining

**Document Coverage**: 14,511 document sections across 211 companies

**Ownership Coverage**: 28,128 entities across 211 companies

**Data Quality**: QC audit passing - 0 critical, 0 errors, 4 warnings (2026-01-29). Fixed 38 mislabeled seniority records (2026-02-06).

**Eval Suite**: 121/136 tests passing (89.0%). 2 workflow tests fixed by secured bond pricing expansion.

**Deployment**: Railway with Neon PostgreSQL + Upstash Redis
- Live at: `https://api.debtstack.ai` (CNAME via Vercel DNS → Railway)

**What's Working**:
- **Three-Tier Pricing**: Pay-as-You-Go ($0 + per-call), Pro ($199/mo), Business ($499/mo)
- **Primitives API**: 11 core endpoints optimized for AI agents (field selection, powerful filters)
- **Business-Only Endpoints**: Historical pricing, covenant compare, bulk export, usage analytics
- **Auth & Credits**: API key auth, tier-based access control, usage tracking with cost
- **Legacy REST API**: 26 endpoints for detailed company data
- **Observability**: Vercel Analytics, PostHog (events/funnels), Sentry (error tracking), Slack alerts
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

### Code Organization

**Utilities** are stateless helper functions (no DB, no API calls):

| File | Domain | Functions |
|------|--------|-----------|
| `app/services/utils.py` | Text/parsing | `parse_json_robust`, `normalize_name`, `parse_date` |
| `app/services/extraction_utils.py` | SEC filings | `clean_filing_html`, `combine_filings`, `extract_debt_sections` |
| `app/services/llm_utils.py` | LLM clients | `get_gemini_model`, `call_gemini`, `LLMResponse` |
| `app/services/yield_calculation.py` | Financial math | YTM, duration, treasury yields |

**Services** do complete jobs (orchestrate DB, APIs, workflows):

| File | Purpose |
|------|---------|
| `app/services/sec_client.py` | SEC filing clients (`SecApiClient`, `SECEdgarClient`) |
| `app/services/extraction.py` | Core extraction service + DB persistence |
| `app/services/base_extractor.py` | Base class for LLM extraction services |
| `app/services/section_extraction.py` | Extract and store document sections from filings |
| `app/services/document_linking.py` | Link debt instruments to source documents (indentures, credit agreements) |
| `app/services/hierarchy_extraction.py` | Exhibit 21 + indenture ownership enrichment |
| `app/services/financial_extraction.py` | TTM financial statements from 10-K/10-Q |
| `app/services/guarantee_extraction.py` | Guarantee extraction from indentures |
| `app/services/collateral_extraction.py` | Collateral for secured debt |
| `app/services/covenant_extraction.py` | Structured covenant data extraction |
| `app/services/metrics.py` | Credit metrics (leverage, coverage ratios) |
| `app/services/qc.py` | Quality control checks |

**Scripts** are thin CLI wrappers that import from services:
- `scripts/extract_iterative.py` - Complete extraction pipeline
- `scripts/recompute_metrics.py` - Metrics recomputation
- `scripts/script_utils.py` - **Shared utilities** (DB sessions, parsers, progress)

This separation keeps business logic testable and reusable while scripts handle CLI concerns. All scripts should use `script_utils.py` for consistent database handling and Windows compatibility.

## Key Files

| File | Purpose |
|------|---------|
| `app/api/primitives.py` | **Primitives API** - 11 core endpoints for agents |
| `app/api/auth.py` | **Auth API** - signup, user info |
| `app/api/routes.py` | Legacy FastAPI endpoints (26 routes) |
| `app/core/auth.py` | API key generation, validation, tier config |
| `app/core/cache.py` | Redis cache client (Upstash) |
| `app/services/utils.py` | Core utilities: JSON parsing, name normalization, dates |
| `app/services/extraction_utils.py` | SEC filing utilities: HTML cleaning, content combining |
| `app/services/llm_utils.py` | LLM client utilities: Gemini, Claude, cost tracking |
| `app/services/sec_client.py` | SEC clients: SecApiClient, SECEdgarClient |
| `app/services/base_extractor.py` | Base class for LLM extraction services |
| `app/services/extraction.py` | ExtractionService + database persistence |
| `app/services/iterative_extraction.py` | Main extraction with QA loop (entry point) |
| `app/services/tiered_extraction.py` | LLM clients for extraction, prompts, escalation |
| `app/services/hierarchy_extraction.py` | Exhibit 21 + indenture ownership enrichment |
| `app/services/section_extraction.py` | Document section extraction and storage |
| `app/services/document_linking.py` | Link instruments to source documents |
| `app/services/guarantee_extraction.py` | Guarantee relationships from indentures |
| `app/services/collateral_extraction.py` | Collateral extraction for secured debt |
| `app/services/metrics.py` | Credit metrics computation with TTM tracking |
| `app/services/qc.py` | Quality control checks |
| `app/services/qa_agent.py` | 5-check QA verification |
| `app/services/tiered_extraction.py` | LLM clients and prompts |
| `app/services/financial_extraction.py` | Financial statements with TTM support |
| `app/services/pricing_history.py` | Bond pricing history backfill and daily snapshots |
| `app/services/treasury_yields.py` | Treasury yield curve history from Treasury.gov |
| `scripts/extract_iterative.py` | Complete extraction pipeline CLI (thin wrapper) |
| `scripts/recompute_metrics.py` | Metrics recomputation CLI (thin wrapper) |
| `scripts/backfill_amounts_from_docs.py` | Phase 6: Multi-doc targeted outstanding amount backfill |
| `scripts/backfill_outstanding_from_filings.py` | Phase 2/4: Outstanding amount backfill from single footnote |
| `scripts/fix_excess_instruments.py` | Phase 3/7/7.5: Dedup, deactivate matured, LLM review, revolver clears |
| `scripts/analyze_gaps_v2.py` | Debt coverage gap analysis (MISSING_ALL/SIGNIFICANT/EXCESS) |
| `scripts/script_utils.py` | Shared CLI utilities (DB sessions, parsers, progress) |
| `app/models/schema.py` | SQLAlchemy models (includes User, UserCredits, UsageLog) |
| `app/core/config.py` | Environment configuration |
| `app/core/database.py` | Database connection |
| `app/core/alerting.py` | Slack webhook alerts (check_and_alert called every 15 min) |
| `app/core/monitoring.py` | Redis-based API metrics and alert condition checks |
| `app/core/scheduler.py` | APScheduler jobs: pricing refresh + alert checks |

## Database Schema

**Core Tables**:
- `companies`: Master company data (ticker, name, CIK, sector)
- `entities`: Corporate entities with hierarchy (`parent_id`, `is_root`, VIE tracking)
  - `is_root=true`: Ultimate parent company
  - `is_root=false` + `parent_id IS NOT NULL`: Has known parent
  - `is_root=false` + `parent_id IS NULL`: Orphan (parent unknown)
- `ownership_links`: Complex ownership (JVs, partial ownership)
  - `ownership_type`: "direct", "indirect", or NULL (unknown - documents say "subsidiary" without specifying)
- `debt_instruments`: All debt with terms, linked via `issuer_id`
- `guarantees`: Links debt to guarantor entities
- `collateral`: Asset-backed collateral for secured debt (type, description, priority)
- `covenants`: Structured covenant data extracted from credit agreements/indentures
- `company_financials`: Quarterly financial statements (amounts in cents) - see **Industry-Specific Metrics** below
- `bond_pricing`: Pricing data (YTM, spreads in basis points)
- `document_sections`: SEC filing sections for full-text search (TSVECTOR + GIN index)

**Auth Tables**:
- `users`: User accounts (email, api_key_hash, tier, stripe IDs, rate_limit_per_minute, team_seats)
- `user_credits`: Credit balance and billing cycle (credits_remaining, credits_purchased, credits_used)
- `usage_log`: API usage tracking (endpoint, cost_usd, tier_at_time_of_request)

**Pricing Tables** (Three-Tier System):
- `bond_pricing_history`: Historical bond pricing snapshots (Business tier only)
- `treasury_yield_history`: Historical US Treasury yield curves for spread calculations (1M-30Y tenors, 2021-present)
- `team_members`: Business tier multi-seat team management
- `coverage_requests`: Business tier custom company coverage requests

**Cache Tables**:
- `company_cache`: Pre-computed JSON responses + `extraction_status` (JSONB tracking step attempts)
- `company_metrics`: Computed credit metrics + `source_filings` JSONB for TTM provenance

### Industry-Specific Financial Metrics

Different industries use different primary profitability metrics. The `company_financials.ebitda` field stores the industry-appropriate metric, with `ebitda_type` indicating which:

| `ebitda_type` | Industry | Metric | Calculation |
|---------------|----------|--------|-------------|
| `"ebitda"` | Operating companies | EBITDA | Operating Income + D&A |
| `"ppnr"` | Banks | Pre-Provision Net Revenue | NII + Non-Interest Income - Non-Interest Expense |
| `"ffo"` | REITs | Funds From Operations | Net Income + D&A - Gains on Sales |
| `"noi"` | Real Estate | Net Operating Income | Rental Income - Operating Expenses |

**Current Support:**
- Banks/Financial Institutions: `companies.is_financial_institution = true` (12 companies: AXP, BAC, C, COF, GS, JPM, MS, PNC, SCHW, TFC, USB, WFC)
- Industry-specific fields in `company_financials`: `net_interest_income`, `non_interest_income`, `non_interest_expense`, `provision_for_credit_losses`

**Bank Sub-Types (extraction differences):**
| Type | Examples | Income Statement Structure | Notes |
|------|----------|---------------------------|-------|
| Commercial Banks | BAC, JPM, WFC, PNC, USB, TFC, C, COF | NII + Non-Interest Income - Non-Interest Expense | Standard bank prompt works well |
| Credit Card / Consumer Finance | AXP, COF | Similar to commercial banks | May show "Net Interest Income" differently |
| Investment Banks | GS, MS | "Net Revenues" by segment (Institutional, Wealth Mgmt) | Different terminology - "Net Revenues" not "NII" |
| Brokerages | SCHW | Mix of NII and fee income | Works with bank prompt |

**Known Limitation:** Investment banks (GS, MS) may have lower extraction success rate for bank-specific fields due to different income statement structure. They use "Net Revenues" and segment-based reporting rather than traditional NII/Non-Interest Income breakdown.

**To add a new industry type:**
1. Add flag to `companies` table (e.g., `is_reit`) or use `sector` detection
2. Add industry-specific columns to `company_financials` in schema.py
3. Create Alembic migration for new columns
4. Add extraction prompt in `app/services/financial_extraction.py` (see `BANK_FINANCIAL_EXTRACTION_PROMPT`)
5. Add calculation logic in `save_financials_to_db()` to compute the metric and set `ebitda_type`
6. Update `extract_iterative.py` to detect and flag the industry type

**API Response:**
The `/v1/financials` endpoint returns `ebitda_type` so consumers know which metric they're seeing. Bank-specific fields (`net_interest_income`, etc.) are included but null for non-banks.

## API Endpoints

### Auth Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/auth/signup` | POST | Create account, returns API key |
| `/v1/auth/me` | GET | Get user info and credits (requires API key) |

### Three-Tier Pricing

| Tier | Price | Rate Limit | Features |
|------|-------|------------|----------|
| Pay-as-You-Go | $0/mo + per-call | 60/min | All basic endpoints, pay $0.05-$0.15/call |
| Pro | $199/mo | 100/min | Unlimited queries to basic endpoints |
| Business | $499/mo | 500/min | All endpoints + historical pricing + bulk export + 5 team seats |

**Endpoint Costs (Pay-as-You-Go)**:
- Simple ($0.05): `/companies`, `/bonds`, `/bonds/resolve`, `/financials`, `/collateral`, `/covenants`
- Complex ($0.10): `/companies/{ticker}/changes`
- Advanced ($0.15): `/entities/traverse`, `/documents/search`, `/batch`

**Business-Only Endpoints**: `/covenants/compare`, `/bonds/{cusip}/pricing/history`, `/export`, `/usage/analytics`

### Primitives API (11 endpoints - optimized for agents)

All require `X-API-Key` header.

| Endpoint | Method | Pay-as-You-Go Cost | Purpose |
|----------|--------|-------------------|---------|
| `/v1/companies` | GET | $0.05 | Search companies with field selection |
| `/v1/bonds` | GET | $0.05 | Search/screen bonds with pricing (YTM, seniority, maturity filters) |
| `/v1/bonds/resolve` | GET | $0.05 | Map bond identifiers - free-text to CUSIP (e.g., "RIG 8% 2027") |
| `/v1/financials` | GET | $0.05 | Quarterly financial statements (income, balance sheet, cash flow) |
| `/v1/collateral` | GET | $0.05 | Collateral securing debt (types, values, priority) |
| `/v1/companies/{ticker}/changes` | GET | $0.10 | Diff against historical snapshots |
| `/v1/covenants` | GET | $0.05 | Search structured covenant data (financial, negative, protective) |
| `/v1/covenants/compare` | GET | Business only | Compare covenants across multiple companies |
| `/v1/entities/traverse` | POST | $0.15 | Graph traversal (guarantors, structure) |
| `/v1/documents/search` | GET | $0.15 | Full-text search across SEC filings |
| `/v1/batch` | POST | Sum of ops | Execute multiple primitives in parallel |

### Business-Only Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/bonds/{cusip}/pricing/history` | GET | Historical bond pricing (up to 2 years) |
| `/v1/export` | GET | Bulk data export (CSV/JSON, up to 50K records) |
| `/v1/usage/analytics` | GET | Usage analytics dashboard |
| `/v1/usage/trends` | GET | Usage trends over time |

### Pricing API (Public + Authenticated)

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/v1/pricing/tiers` | GET | No | Public tier info |
| `/v1/pricing/calculate` | POST | No | Cost calculator |
| `/v1/pricing/my-usage` | GET | Yes | User's usage stats |
| `/v1/pricing/purchase-credits` | POST | Yes | Buy credit packages |
| `/v1/pricing/upgrade` | POST | Yes | Upgrade subscription |

**Field selection**: `?fields=ticker,name,net_leverage_ratio`
**Sorting**: `?sort=-net_leverage_ratio` (prefix `-` for descending)
**Filtering**: `?min_ytm=8.0&seniority=senior_unsecured`

**Bonds vs Bonds/Resolve:**
| `/v1/bonds` | `/v1/bonds/resolve` |
|-------------|---------------------|
| Bulk search/screening | Identifier resolution |
| Filters: yield, seniority, maturity | Free-text parsing: "RIG 8% 2027" |
| Always includes pricing | Fuzzy + exact match modes |
| Use: "Show me all IG bonds with 5%+ yield" | Use: "What's the CUSIP for RIG's 8% 2027?" |

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
7. **ALWAYS detect scale from source document**: SEC filings state their scale explicitly (e.g., "in millions", "in thousands", "$000"). NEVER assume scale - always extract it from the filing header. The `detect_filing_scale()` function in `financial_extraction.py` handles this. **Critical lesson**: When fixing apparent scale errors, ALWAYS verify against the source SEC filing first. Do not blindly multiply/divide - the extraction may be correct and the comparison data may be stale or from a different source.
8. **Comprehensive debt instrument taxonomy**: Extract ALL types of corporate debt, not just bonds. See table below.

### Debt Instrument Types

The extraction uses a canonical taxonomy with normalization from 50+ LLM output variants:

| Type | Description | Examples |
|------|-------------|----------|
| `senior_notes` | Investment-grade or high-yield bonds | 5.75% Senior Notes due 2029 |
| `senior_secured_notes` | Secured bonds with collateral | 8.5% Senior Secured Notes due 2028 |
| `subordinated_notes` | Junior/subordinated bonds | 6.25% Subordinated Notes due 2030 |
| `convertible_notes` | Bonds convertible to equity | 6.50% Convertible Senior Notes |
| `term_loan` | Bank term loans (A, B, or generic) | 2023 Term Loan Facility |
| `revolver` | Revolving credit facilities | 2023 Revolving Credit Facility |
| `abl` | Asset-based lending facilities | ABL Facility |
| `commercial_paper` | Short-term unsecured notes | Commercial Paper Program |
| `equipment_trust` | EETCs, equipment financing | Enhanced Equipment Trust Certificates |
| `government_loan` | PSP notes, government-backed loans | PSP1 Promissory Note |
| `finance_lease` | Capital/finance lease obligations | Finance Lease Obligations |
| `mortgage` | Real estate secured debt | Mortgage Notes |
| `bond` | Generic bonds (municipal, revenue, etc.) | Special Facility Revenue Bonds |
| `debenture` | Unsecured debentures | Debentures due 2035 |
| `preferred_stock` | Trust preferred, preferred securities | Trust Preferred Securities |
| `other` | Catch-all for rare types | Structured liabilities, repos |

**Commonly missed types** (now explicitly prompted):
- Term Loans and Revolvers (bank debt, not capital markets)
- Equipment Trust Certificates (aircraft/equipment financing)
- Government/PSP Loans (CARES Act, Payroll Support Program)
- Special Facility Revenue Bonds (airport, industrial revenue bonds)

The validator in `extraction.py` normalizes 50+ variants (e.g., `eetc` → `equipment_trust`, `term_loan_b` → `term_loan`).

## Adding a New Company

**One command** to add a complete company with all data:

```bash
# Get the company's CIK from SEC EDGAR first:
# https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=COMPANY+NAME

# Run complete extraction pipeline
python scripts/extract_iterative.py --ticker XXXX --cik 0001234567 --save-db
```

This single command runs all 11 steps:

| Step | What It Does | Data Created |
|------|--------------|--------------|
| 1 | Download SEC filings | Cached for reuse |
| 2 | Core extraction with QA | Company, Entities, Debt Instruments |
| 3 | Save to database | Core records |
| 4 | Document sections | Searchable SEC filing sections |
| 5 | Document linking | Links instruments to indentures/credit agreements |
| 6 | TTM financials | 4 quarters of financial data |
| 7 | Ownership hierarchy | parent_id, is_root relationships |
| 8 | Guarantees | Guarantee relationships (uses linked docs) |
| 9 | Collateral | Collateral for secured debt (uses linked docs) |
| 10 | Metrics | Leverage ratios, maturity profile |
| 11 | QC checks | Validation and data quality |

**Options:**
```bash
--core-only        # Fastest: only entities + debt (skip financials, enrichment)
--skip-financials  # Skip TTM financial extraction
--skip-enrichment  # Skip guarantees, collateral, hierarchy
--threshold 90     # QA score threshold (default: 85%)
--force            # Re-run all steps (ignore skip conditions)
--all              # Process all companies in database
--resume           # Resume batch from last processed company
--limit N          # Limit batch to N companies
--step STEP        # Run only a specific step (see below)
```

**Modular Execution with `--step`:**

Run individual extraction steps for existing companies without the full pipeline:

```bash
# Re-extract financials only
python scripts/extract_iterative.py --ticker AAL --step financials

# Refresh cache after manual DB fixes
python scripts/extract_iterative.py --ticker AAL --step cache

# Re-run guarantees extraction
python scripts/extract_iterative.py --ticker AAL --step guarantees

# Update metrics after data changes
python scripts/extract_iterative.py --ticker AAL --step metrics
```

| Step | Purpose | Requires |
|------|---------|----------|
| `core` | Re-run entity and debt extraction | GEMINI_API_KEY, CIK |
| `financials` | Extract 8 quarters of financial data | GEMINI_API_KEY, CIK |
| `hierarchy` | Extract ownership from Exhibit 21 | CIK |
| `guarantees` | Extract guarantee relationships | CIK |
| `collateral` | Extract collateral data | CIK |
| `documents` | Link documents to instruments | - |
| `covenants` | Extract covenant data | CIK |
| `metrics` | Recompute derived metrics | - |
| `finnhub` | Run Finnhub bond discovery | FINNHUB_API_KEY |
| `pricing` | Update bond pricing | FINNHUB_API_KEY |
| `cache` | Refresh company cache | - |

**Idempotent Extraction:**

The script is safe to re-run on existing companies. It tracks extraction status in `company_cache.extraction_status` (JSONB):

```json
{
  "hierarchy": {"status": "no_data", "attempted_at": "2026-01-26T10:30:00", "details": "No Exhibit 21 found"},
  "financials": {"status": "success", "latest_quarter": "2025Q3", "attempted_at": "..."}
}
```

**Status values:**
- `success` - Step completed with data (includes metadata like `latest_quarter`)
- `no_data` - Source data unavailable (won't retry unless `--force`)
- `error` - Step failed (will retry on next run)

**Smart skip logic:**
- Financials: Checks if new quarter available (~60 days after quarter end)
- Hierarchy: Skips if no Exhibit 21 was found previously
- Guarantees/Collateral: Skips if already extracted or source unavailable

**Estimated time**: 2-5 minutes per company
**Estimated cost**: ~$0.03-0.08 per company

### Manual/Individual Steps

Each extraction service has its own CLI for debugging or re-running specific steps:

```bash
# Document sections
python -m app.services.section_extraction --ticker CHTR
python -m app.services.section_extraction --all --limit 10
python -m app.services.section_extraction --ticker CHTR --force  # Re-extract

# Document linking (links instruments to indentures/credit agreements)
python -m app.services.document_linking --ticker CHTR
python -m app.services.document_linking --all --heuristic  # Fast, no LLM

# Financials (TTM = 4 quarters)
python -m app.services.financial_extraction --ticker CHTR --ttm --save-db
python -m app.services.financial_extraction --ticker CHTR --claude  # Use Claude instead of Gemini

# Ownership hierarchy (from Exhibit 21)
python -m app.services.hierarchy_extraction --ticker CHTR
python -m app.services.hierarchy_extraction --all --limit 10

# Guarantees
python -m app.services.guarantee_extraction --ticker CHTR
python -m app.services.guarantee_extraction --all

# Collateral
python -m app.services.collateral_extraction --ticker CHTR
python -m app.services.collateral_extraction --all

# Covenants (structured covenant data from credit agreements/indentures)
python -m app.services.covenant_extraction --ticker CHTR
python -m app.services.covenant_extraction --all --limit 50
python -m app.services.covenant_extraction --ticker CHTR --force  # Re-extract

# Metrics recompute
python -m app.services.metrics --ticker CHTR
python -m app.services.metrics --all --dry-run  # Preview changes

# QC check
python scripts/qc_master.py --ticker CHTR --verbose

# Pricing update (requires Finnhub)
python scripts/update_pricing.py --ticker CHTR
```

### Document-to-Instrument Linking

Scripts for linking debt instruments to their governing legal documents:

```bash
# Fix data quality issues first
python scripts/fix_missing_interest_rates.py --save    # Extract rates from names
python scripts/fix_missing_maturity_dates.py --save    # Extract years from names
python scripts/fix_empty_instrument_names.py --ticker CHTR --save  # LLM extracts names

# Smart pattern-based matching (for term loans, revolvers)
python scripts/smart_document_matching.py --ticker CHTR --save
python scripts/smart_document_matching.py --all --save --limit 50

# Fallback linking (when specific documents not found)
python scripts/link_to_base_indenture.py --save        # Links notes to base/supplemental indentures
python scripts/link_to_credit_agreement.py --save      # Links loans/revolvers to credit agreements

# Mark instruments that don't need documents
python scripts/mark_no_doc_expected.py --execute       # Commercial paper, bank loans, etc.
```

### Ownership Hierarchy Extraction

Corporate ownership hierarchy is extracted from multiple sources:

**From Exhibit 21 (initial extraction):**
```bash
python scripts/extract_exhibit21_hierarchy.py --ticker CHTR --save-db
python scripts/extract_exhibit21_hierarchy.py --all --save-db
```

**From Indentures/Credit Agreements (explicit relationships only):**
```bash
# Extract explicit parent-child relationships from SEC filings
python scripts/fix_ownership_hierarchy.py --ticker CHTR           # Dry run
python scripts/fix_ownership_hierarchy.py --ticker CHTR --save-db # Save to DB
python scripts/fix_ownership_hierarchy.py --all --save-db         # All companies
```

**Key fields:**
- `entities.is_root`: `true` = ultimate parent company, `false` = subsidiary/orphan
- `entities.parent_id`: UUID of parent entity, or NULL if root/unknown
- `ownership_links.ownership_type`: "direct", "indirect", or NULL (unknown)

**Entity states:**
| `is_root` | `parent_id` | Meaning |
|-----------|-------------|---------|
| `true` | `NULL` | Ultimate parent company |
| `false` | UUID | Has known parent |
| `false` | `NULL` | Orphan (parent unknown from SEC filings) |

**Note:** SEC filings rarely contain explicit intermediate ownership chains. Documents typically say entities are "subsidiaries of the Company" without specifying intermediate holding companies. The extraction only captures what's explicitly documented - no inferences.

### Ownership Data Honesty

We only show parent-child relationships where we have evidence from SEC filings. The `/structure` endpoint includes transparency fields:

**Entity-level `ownership_confidence`:**
- `root` - Ultimate parent company
- `key_entity` - Issuer or guarantor with known relationship to parent
- `verified` - Intermediate parent verified from indenture/credit agreement parsing
- `unknown` - Listed in Exhibit 21 but parent relationship unknown (not shown in hierarchy tree)

**Response-level `ownership_coverage`:**
```json
{
  "ownership_coverage": {
    "known_relationships": 3,
    "unknown_relationships": 227,
    "key_entities": 4,
    "coverage_pct": 1.3,
    "note": "Ownership relationships are only shown where we have evidence..."
  }
}
```

**`other_subsidiaries` section:** Lists entities with unknown parent relationships separately.

**`meta.confidence`:** Shows "partial" when some relationships are unknown, "high" when all are known.

### Fix False Ownership Script

If ownership data needs cleanup:
```bash
# Dry run - see what would change
python scripts/fix_false_ownership.py

# Apply changes (sets parent_id=NULL for non-key entities with unverified relationships)
python scripts/fix_false_ownership.py --save-db

# Single company
python scripts/fix_false_ownership.py --ticker RIG --save-db
```

### TTM EBITDA Calculation (Leverage Ratios)

The metrics service (`app/services/metrics.py`) calculates TTM EBITDA for leverage ratios using smart filing-type detection:

**Rule: 10-K vs 10-Q Logic**
- **If latest filing is 10-K**: Use annual figures directly (already represents full year TTM)
- **If latest filing is 10-Q**: Sum trailing 4 quarters of 10-Q data

This prevents the common error of mixing annual and quarterly figures.

**EBITDA Computation:**
1. Use `ebitda` field if directly available
2. Otherwise compute: `operating_income + depreciation_amortization`
3. Fallback: Use `operating_income` alone if D&A unavailable (flagged as estimated)

**Annualization:**
- If fewer than 4 quarters of 10-Q data available, extrapolate: `ttm_ebitda = sum * (4 / quarters_available)`
- This is flagged in metadata so users know data quality

**Data Quality Tracking (`source_filings` JSONB):**
```json
{
  "ebitda_source": "annual_10k",       // or "quarterly_sum"
  "ebitda_quarters": 4,                 // Number of quarters used
  "ebitda_quarters_with_da": 4,         // Quarters where D&A was available
  "is_annualized": false,               // True if extrapolated from <4 quarters
  "ebitda_estimated": false,            // True if some quarters used OpInc only
  "ttm_quarters": ["2025FY"],           // Periods used (e.g., "2025FY" or "2025Q3,2025Q2,...")
  "computed_at": "2026-01-29T16:32:47Z"
}
```

**API Exposure:**
```bash
GET /v1/companies?ticker=AAPL&include_metadata=true
```

Returns `_metadata.leverage_data_quality` with all tracking fields, enabling users to assess data reliability.

**Current Coverage (201 companies):**
| Category | Count | Percent |
|----------|-------|---------|
| Full TTM (4 quarters or 10-K) | 73 | 36% |
| Annualized (<4 quarters) | 111 | 55% |
| No EBITDA data | 17 | 8% |

**Data Freshness vs LLMs:**
DebtStack extracts from the latest SEC filings (Oct-Dec 2025), while LLMs like Gemini/ChatGPT have knowledge cutoffs ~18 months stale. Comparison testing showed:
- Companies where periods align: 100% match rate (within 15% tolerance)
- Apparent "mismatches" are due to LLM data being from Q1-Q2 2024

### TTM Financial Extraction

For accurate leverage ratios, use `--ttm` to extract 8 quarters (2 years) of financial data:
- Fetches 8 10-Qs (NOT 10-Ks) for clean quarterly data
- Uses `periodOfReport` from SEC API to determine fiscal quarter
- Stores `filing_type` field ("10-Q") for each record
- If fewer than 4 quarters available, annualizes what's available

**Why 8 10-Qs instead of 10-K + 10-Qs:**
- 10-K contains ANNUAL figures, not Q4 quarterly data
- Extracting Q4 from 10-K requires subtracting 9-month YTD from annual (error-prone)
- Using 8 10-Qs gives clean quarterly data without math
- Also provides 2 years of data for YoY comparisons and trend analysis

**Target: 8 quarters per company** for full coverage (TTM + YoY comparison)

### Financial Data Quality Control

Run QC audit to validate financial data accuracy:

```bash
# Full audit - checks for scale errors, extraction failures
python scripts/qc_financials.py --verbose

# Check single company
python scripts/qc_financials.py --ticker AAPL

# Spot-check random companies
python scripts/qc_financials.py --sample 10

# Fix QC issues (delete impossible records, report issues)
python scripts/fix_qc_financials.py              # Dry run
python scripts/fix_qc_financials.py --save-db    # Apply fixes
```

**QC Checks:**
1. **Sanity checks** (no source doc needed): Revenue > $1T, EBITDA > Revenue, Debt > 10x Assets
2. **Source validation**: Re-reads scale from SEC filing header, compares stored vs. source values
3. **Leverage quality**: Flags high leverage (>20x) with insufficient EBITDA quarters
4. **Leverage consistency**: Compares stored leverage vs calculated (debt/EBITDA)

**Current Status (2026-01-29):** 0 critical, 0 errors, 4 warnings

## Testing

DebtStack has a comprehensive testing infrastructure with 5 layers:

### Test Structure

```
tests/                          # pytest test suite
├── conftest.py                 # Fixtures, sample data
├── unit/                       # Pure function tests (no DB/API)
│   ├── test_name_normalization.py    # Entity name matching
│   ├── test_fuzzy_matching.py        # Guarantor matching
│   ├── test_maturity_parsing.py      # Date extraction
│   └── test_document_classification.py
├── integration/                # Service tests (mocked)
├── api/                        # API contract tests
│   └── test_companies_endpoint.py
└── eval/                       # API accuracy evaluation suite
    ├── conftest.py             # API client, DB fixtures
    ├── ground_truth.py         # Ground truth data management
    ├── scoring.py              # Accuracy calculation, regression detection
    ├── test_companies.py       # 8 use cases
    ├── test_bonds.py           # 7 use cases
    ├── test_bonds_resolve.py   # 6 use cases
    ├── test_financials.py      # 8 use cases
    ├── test_collateral.py      # 5 use cases
    ├── test_covenants.py       # 6 use cases
    ├── test_covenants_compare.py  # 4 use cases
    ├── test_entities_traverse.py  # 7 use cases
    ├── test_documents_search.py   # 6 use cases
    ├── test_workflows.py       # 8 E2E scenarios
    └── baseline/               # Regression detection baselines

scripts/                        # Standalone test scripts
├── run_all_tests.py           # Unified test runner
├── run_evals.py               # Eval framework runner
├── test_api_edge_cases.py     # Security/robustness tests
├── qc_master.py               # Data quality checks
└── qc_financials.py           # Financial validation
```

### Running Tests

```bash
# Quick: Unit tests only (no external deps, fast)
python -m pytest tests/unit -v -s

# All pytest tests
python -m pytest tests/ -v -s

# Unified runner (includes E2E scripts)
python scripts/run_all_tests.py              # All suites
python scripts/run_all_tests.py --quick      # Unit only
python scripts/run_all_tests.py --unit       # Unit only
python scripts/run_all_tests.py --api        # API contract tests
python scripts/run_all_tests.py --qc         # Include QC checks
python scripts/run_all_tests.py --json       # JSON output

# API Evaluation Framework (replaces test_demo_scenarios.py)
python scripts/run_evals.py                  # Run all evals
python scripts/run_evals.py --primitive companies  # Single primitive
python scripts/run_evals.py --update-baseline      # Update baseline
python scripts/run_evals.py --json           # JSON output for CI

# Data quality
python scripts/qc_master.py                  # Full QC audit
python scripts/qc_master.py --category integrity  # Specific category
python scripts/qc_financials.py --verbose    # Financial validation
```

### Eval Framework

The eval framework validates API correctness against ground truth from database/SEC filings:

| Primitive | Use Cases | Description |
|-----------|-----------|-------------|
| `/v1/companies` | 8 | Leverage accuracy, debt totals, sorting, filtering |
| `/v1/bonds` | 7 | Coupon rates, maturity dates, pricing, seniority |
| `/v1/bonds/resolve` | 6 | Free-text parsing, CUSIP lookup, fuzzy matching |
| `/v1/financials` | 8 | Revenue, EBITDA, cash, quarterly filtering |
| `/v1/collateral` | 5 | Collateral types, debt linking, priority |
| `/v1/covenants` | 6 | Covenant types, thresholds, metrics |
| `/v1/covenants/compare` | 4 | Multi-company comparison |
| `/v1/entities/traverse` | 7 | Guarantor counts, parent-child links |
| `/v1/documents/search` | 6 | Term presence, relevance, snippets |
| **Workflows** | 8 | End-to-end multi-step scenarios |

**Target accuracy**: 95%+. See `docs/EVAL_FRAMEWORK.md` for details.

### Test Coverage

| Category | Tests | Purpose |
|----------|-------|---------|
| Unit | 73 | Pure functions: name normalization, fuzzy matching, parsing |
| API Contract | 15+ | Endpoint schemas, response types |
| Eval Suite | ~65 | Ground-truth validation per endpoint |
| API Edge Cases | 50+ | Security, error handling, edge cases |
| QC Checks | 30+ | Data integrity, business logic, completeness |

### Adding Tests

1. **Unit tests** go in `tests/unit/test_*.py` - no DB, no API, no mocks
2. **API tests** go in `tests/api/test_*.py` - require `DEBTSTACK_API_KEY`
3. **Eval tests** go in `tests/eval/test_*.py` - use `@pytest.mark.eval`
4. Use `@pytest.mark.unit` or `@pytest.mark.api` markers
5. Install dev deps: `pip install -r requirements-dev.txt`

### Covenant Relationship Extraction

Extract additional relationship data from indentures (796) and credit agreements (1,720):

```bash
# Single company (dry run)
python scripts/extract_covenant_relationships.py --ticker CHTR

# Single company (save to database)
python scripts/extract_covenant_relationships.py --ticker CHTR --save-db

# Batch all companies
python scripts/extract_covenant_relationships.py --all --save-db

# Audit extraction results
python scripts/audit_covenant_relationships.py --verbose
```

**Data Extracted:**
- **Unrestricted subsidiaries**: Updates `entities.is_unrestricted` flag
- **Guarantee conditions**: Adds release/add triggers to `guarantees.conditions` (JSONB)
- **Cross-default links**: Creates records in `cross_default_links` table
- **Non-guarantor disclosure**: Adds EBITDA/asset percentages to `company_metrics.non_guarantor_disclosure`

## Bond Pricing (Finnhub Integration)

Bond pricing data comes from Finnhub, which sources from FINRA TRACE (same data as Bloomberg/Reuters).

### Data Flow

```
Finnhub API (FINRA TRACE)           Treasury.gov
    ↓                                    ↓
scripts/update_pricing.py           scripts/backfill_treasury_yields.py
    ↓                                    ↓
bond_pricing table (current)        treasury_yield_history table
    ↓                                    ↓
scripts/collect_daily_pricing.py ←──────┘ (for spread calc)
    ↓
bond_pricing_history table (daily snapshots)
```

### Finnhub API Endpoints

| Endpoint | Purpose | Key Fields |
|----------|---------|------------|
| `GET /bond/price?isin={ISIN}` | Current pricing | close, yield, volume, timestamp |
| `GET /bond/profile?isin={ISIN}` | Bond characteristics | FIGI, callable, coupon_type |

**Note:** Finnhub requires ISIN, not CUSIP. Convert US CUSIPs by adding "US" prefix + check digit.

### Data Mapping

| Finnhub Field | DebtStack Column | Notes |
|---------------|------------------|-------|
| `close` | `bond_pricing.last_price` | Clean price as % of par (e.g., 94.25) |
| `yield` | `bond_pricing.ytm_bps` | Convert to basis points (6.82% → 682) |
| `volume` | `bond_pricing.last_trade_volume` | Face value in cents |
| `t` (timestamp) | `bond_pricing.last_trade_date` | Unix → datetime |
| `"Finnhub"` | `bond_pricing.price_source` | Track data source |

### Tables

**`bond_pricing`** - Current prices (one row per instrument):
- `last_price`: Clean price as % of par
- `ytm_bps`: Yield to maturity in basis points
- `spread_to_treasury_bps`: Spread over benchmark treasury
- `staleness_days`: Days since last trade
- `price_source`: "TRACE", "Finnhub", "estimated"

**`bond_pricing_history`** - Historical daily snapshots:
- `price_date`: Date of snapshot
- `price`, `ytm_bps`, `spread_bps`, `volume`
- Unique constraint on (debt_instrument_id, price_date)

**`treasury_yield_history`** - Historical treasury yield curves:
- `yield_date`: Date of yield curve
- `benchmark`: Tenor (1M, 3M, 6M, 1Y, 2Y, 3Y, 5Y, 7Y, 10Y, 20Y, 30Y)
- `yield_pct`: Yield as percentage (e.g., 4.25 for 4.25%)
- `source`: "treasury.gov" or "finnhub"
- Coverage: 13,970 records from 2021-01-04 to present

### API Exposure

```bash
# Current prices (from bond_pricing) - pricing always included in bonds response
GET /v1/bonds?has_pricing=true&fields=name,cusip,pricing
GET /v1/bonds?ticker=RIG&fields=name,cusip,pricing

# Historical prices (from bond_pricing_history)
GET /v1/pricing/history?cusip=76825DAJ7&from=2025-01-01&to=2026-01-27
```

**Note:** The `/v1/pricing` endpoint is deprecated (removal: 2026-06-01). Use `/v1/bonds?has_pricing=true` instead.

### Pricing Response Format

```json
{
  "name": "8.000% Senior Notes due 2027",
  "cusip": "76825DAJ7",
  "pricing": {
    "price": 94.25,
    "ytm_pct": 9.82,
    "spread_bps": 450,
    "as_of": "2026-01-24",
    "source": "Finnhub"
  }
}
```

### Scripts

```bash
# Update current prices for all instruments with CUSIPs
python scripts/update_pricing.py --all

# Update single company
python scripts/update_pricing.py --ticker CHTR

# Backfill treasury yields (free from Treasury.gov)
python scripts/backfill_treasury_yields.py --from-year 2021 --to-year 2026
python scripts/backfill_treasury_yields.py --stats  # Check coverage

# Backfill historical bond pricing (requires Finnhub premium)
python scripts/backfill_pricing_history.py --all --days 1095  # 3 years
python scripts/backfill_pricing_history.py --all --with-spreads  # Use historical treasury yields
python scripts/backfill_pricing_history.py --ticker CHTR --skip-yields  # Faster, no YTM calc

# Daily pricing collection (for cron job)
python scripts/collect_daily_pricing.py --all
```

### Services

**`app/services/pricing_history.py`** - Bond pricing history service:
- `fetch_historical_candles()` - Fetch from Finnhub
- `calculate_ytm_for_price()` - Sync YTM calculation
- `calculate_spread_for_price()` - Spread using historical treasury yields
- `backfill_bond_history()` - Single bond backfill
- `copy_current_to_history()` - Daily snapshot

**`app/services/treasury_yields.py`** - Treasury yield service:
- `fetch_treasury_gov_yields()` - Fetch from Treasury.gov by year (free)
- `backfill_treasury_yields()` - Multi-year backfill
- `get_treasury_yield_for_date()` - Single date/benchmark lookup
- `get_treasury_curve_for_date()` - Full curve for a date

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `DATABASE_URL` | Yes | PostgreSQL (use `sslmode=require` for Neon) |
| `REDIS_URL` | Optional | Redis cache (Upstash, use `rediss://` for TLS) |
| `ANTHROPIC_API_KEY` | Yes | Claude for escalation |
| `GEMINI_API_KEY` | Recommended | Gemini for extraction |
| `SEC_API_KEY` | Recommended | SEC-API.io for filing retrieval |
| `FINNHUB_API_KEY` | Optional | Finnhub for bond pricing (~$100/mo tier) |
| `SENTRY_DSN` | Optional | Sentry error tracking (FastAPI auto-integration) |
| `SLACK_WEBHOOK_URL` | Optional | Slack incoming webhook for alert notifications |

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

Current migrations: 001 (initial) through 020 (add_covenants_table)

## Utilities & Services Architecture

The codebase distinguishes between **utilities** (stateless helpers) and **services** (orchestrate complete jobs):

### Utilities (Stateless Functions)

**`app/services/utils.py`** - Core text/parsing utilities:
```python
from app.services.utils import (
    parse_json_robust,    # Parse JSON from LLM output (handles code blocks, trailing commas)
    normalize_name,       # Normalize entity names for matching (case, suffixes)
    parse_date,           # Parse various date formats to date objects
    extract_sections,     # Extract sections by keywords with context
    clean_html,           # Simple HTML tag removal
)
```

**`app/services/extraction_utils.py`** - SEC filing utilities:
```python
from app.services.extraction_utils import (
    clean_filing_html,      # Clean HTML/XBRL from SEC filings
    truncate_content,       # Smart truncation at sentence boundaries
    combine_filings,        # Combine multiple filings with priority
    extract_debt_sections,  # Extract debt-related content using keyword priority
    ModelTier,              # LLM model tiers enum
    LLMUsage,               # Token tracking dataclass
    calculate_cost,         # Calculate LLM token costs
)
```

**`app/services/llm_utils.py`** - LLM client utilities:
```python
from app.services.llm_utils import (
    get_gemini_model,     # Get configured Gemini model
    get_claude_client,    # Get configured Anthropic client
    call_gemini,          # Call Gemini with JSON parsing
    call_claude,          # Call Claude with JSON parsing
    LLMResponse,          # Standardized response dataclass
    calculate_cost,       # Calculate cost from LLMResponse
)
```

### Services (Orchestration)

**`app/services/sec_client.py`** - SEC filing clients:
```python
from app.services.sec_client import SecApiClient, SECEdgarClient, FilingInfo

# SEC-API.io (faster, no rate limits)
client = SecApiClient(api_key="...")
filings = await client.get_all_relevant_filings("AAPL")

# Direct EDGAR (free, rate-limited)
edgar = SECEdgarClient()
filings = await edgar.get_all_relevant_filings(cik="0000320193")
```

**`app/services/base_extractor.py`** - Base class for LLM extraction:
```python
from app.services.base_extractor import BaseExtractor, ExtractionContext

class GuaranteeExtractor(BaseExtractor):
    async def get_prompt(self, context: ExtractionContext) -> str: ...
    async def parse_result(self, response, context) -> list: ...
    async def save_result(self, items, context) -> int: ...
```

This pattern is used by `guarantee_extraction.py` and `collateral_extraction.py`.

### Script Utilities (`scripts/script_utils.py`)

Shared utilities for CLI scripts. **All new scripts should use these utilities** to ensure consistency and proper Windows/async handling.

```python
from script_utils import (
    # Database
    get_db_session,         # Async database session context manager
    get_all_companies,      # Get all companies with CIKs
    get_company_by_ticker,  # Get single company by ticker

    # CLI parsers
    create_base_parser,     # Base argparse with --ticker/--all/--limit
    create_fix_parser,      # Fix script parser with --save/--verbose
    create_extract_parser,  # Extraction parser with --cik/--save-db/--skip-existing

    # Output formatting
    print_header,           # Print formatted header
    print_subheader,        # Print formatted subheader
    print_summary,          # Print stats summary
    print_progress,         # Print progress indicator

    # Batch processing
    process_companies,      # Batch process companies with progress
    run_async,              # Run async function with Windows event loop handling
)
```

**Standard script pattern:**
```python
#!/usr/bin/env python3
"""Script description."""

from script_utils import get_db_session, print_header, run_async

async def main():
    print_header("SCRIPT NAME")

    async with get_db_session() as db:
        # Do work with db session
        pass

if __name__ == "__main__":
    run_async(main())
```

**Benefits:**
- Eliminates ~15-20 lines of boilerplate per script (sys.path, dotenv, engine creation)
- Handles Windows event loop policy automatically
- Handles UTF-8 output encoding on Windows
- Proper async session cleanup with rollback on errors
- Consistent CLI output formatting

## Debt Coverage Backfill (Phases 1-6)

Multi-phase effort to populate `outstanding` amounts on debt instruments. Progress: 32 → 96 OK companies.

### Phase Summary

| Phase | Script | Method | Instruments | Cost |
|-------|--------|--------|-------------|------|
| 1 | (manual SQL) | Recover amounts from cached extraction results | 291 | $0 |
| 2 | `backfill_outstanding_from_filings.py` | Gemini extracts from single debt footnote | 282 | ~$0.10 |
| 3 | `fix_excess_instruments.py` | Dedup by rate+year, deactivate matured | 1,304 deactivated | $0 |
| 4 | `backfill_outstanding_from_filings.py` | Re-extraction with broader section search | 30 | ~$0.10 |
| 5 | `extract_iterative.py --step core` | Full re-extraction for MISSING_ALL | 13/14 companies | ~$0.50 |
| 6 | `backfill_amounts_from_docs.py` | Multi-doc targeted extraction | 440 | ~$2.00 |
| 7 | `fix_excess_instruments.py --fix-llm-review` | Claude reviews EXCESS_SIGNIFICANT (>200%) | 49 deactivated, 45 cleared | ~$0.42 |
| 7.5 | `fix_excess_instruments.py` | Step 8 revolver clears + LLM review at 1.5x | 36 revolver clears + 180 deactivated, 91 cleared | ~$2.01 |

### Backfill Scripts

```bash
# Phase 2/4: Extract from single debt footnote (broad "extract ALL" prompt)
python scripts/backfill_outstanding_from_filings.py --analyze
python scripts/backfill_outstanding_from_filings.py --fix --ticker AAPL
python scripts/backfill_outstanding_from_filings.py --fix --dry-run

# Phase 3: Dedup and deactivate
python scripts/fix_excess_instruments.py --analyze
python scripts/fix_excess_instruments.py --deduplicate --dry-run
python scripts/fix_excess_instruments.py --deactivate-matured

# Phase 6: Multi-doc targeted extraction (sends instrument list to Gemini)
python scripts/backfill_amounts_from_docs.py --analyze
python scripts/backfill_amounts_from_docs.py --fix --ticker CSGP --dry-run
python scripts/backfill_amounts_from_docs.py --fix                    # MISSING_ALL only
python scripts/backfill_amounts_from_docs.py --fix --all-missing      # + MISSING_SIGNIFICANT
python scripts/backfill_amounts_from_docs.py --fix --model gemini-2.5-pro
```

### Key Learnings from Debt Coverage Work

**What works well:**
1. **Targeted prompts beat broad extraction** — Phase 6 sends the specific instrument list ("find amounts for THESE instruments") vs Phase 2's "extract ALL instruments". Targeted approach gets better matches because Gemini knows exactly what to look for.
2. **Multi-document iteration** — Try 10-K debt footnote first (most comprehensive), fall back to 10-Q footnotes (more recent), then MDA/desc_securities. PLD: 59/59 from single 10-K. UAL: 6/6 across 12 docs.
3. **Rate + maturity year matching** — Simple scoring (0.5 for rate match within 0.15%, 0.5 for year match, threshold 0.8) reliably deduplicates and matches instruments across sources.
4. **Instrument index matching** — Asking Gemini to return `instrument_index` (1-based reference to the input list) is more reliable than post-hoc fuzzy matching.
5. **Provenance tagging** — Store `amount_source`, `amount_doc_type`, `amount_doc_date` in `attributes` JSONB for debugging.
6. **Fresh session per company** — Neon drops idle connections during 10-60s Gemini calls. Use `async_sessionmaker` and open/close per company.

**What fails or has low yield:**
1. **Revolvers/term loans** — Usually $0 drawn; correct to skip. GEV (4 revolvers), LULU (2 revolvers), PLTR (3 revolvers) all correctly returned 0 amounts.
2. **Aggregate-only footnotes** — PG's 33K debt footnote only has maturity schedule totals by year, not per-instrument amounts. Need a different doc section or manual entry.
3. **Banks without debt footnotes** — COF, USB, MS have no `debt_footnote` sections. Bank debt is structured differently (deposits, wholesale funding). Try `desc_securities` or `mda_liquidity`.
4. **Duplicate instruments with no rates** — TEAM has 3 pairs of duplicate "Senior Notes due 20XX" with no coupon rates, making matching impossible. Fix data quality first (`fix_missing_interest_rates.py`).
5. **Gemini key name inconsistency** — Gemini returns `outstanding_amount_cents` instead of `outstanding_cents` despite the prompt specifying the latter. Always check for both: `entry.get('outstanding_cents') or entry.get('outstanding_amount_cents')`.
6. **Poor stored footnote quality** — ~40% of `debt_footnote` sections contain entire 10-Q filings truncated at 100K instead of just the debt note. Need section re-extraction with better boundary detection.

**Remaining structural gaps (won't be fixed by more LLM calls):**
- PG: Aggregate-only footnote (67 instruments) — needs different section or prospectus data
- Banks (COF, USB, MS): No debt footnotes — need section re-extraction or manual entry
- Companies with only revolvers (GEV, LULU, PLTR): Correctly $0 — not real gaps
- VRTX: Single instrument, only footnote is from year 2000

## Common Issues

| Issue | Solution |
|-------|----------|
| LLM returns debt totals | Prompt requires individual instruments |
| LLM uses wrong JSON key for amounts | Check both `outstanding_cents` and `outstanding_amount_cents` |
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

### Document Linking Issues

| Symptom | Root Cause | Solution |
|---------|------------|----------|
| Low document coverage for notes | Only recent SEC filings extracted; historical indentures missing | Use `link_to_base_indenture.py` - most notes are issued under a single base indenture |
| Term loans not matching | Credit agreements are 400K+ chars; LLM matching truncates | Use `smart_document_matching.py` - searches full content for patterns first |
| NULL rates/maturities blocking matches | Instrument names contain this info but fields are NULL | Run `fix_missing_interest_rates.py` and `fix_missing_maturity_dates.py` first |
| Instruments show as unlinked but are generic | Catch-all entries like "Other long-term debt" | Mark as `no_document_expected` in attributes JSONB |

**Key Learnings from Document Coverage Work:**
1. **Don't game metrics** - Term loans/revolvers need credit agreements; don't exclude them to inflate coverage
2. **Base indentures govern all notes** - A company's 1990s base indenture covers notes issued in 2020s
3. **Pattern matching beats LLM for large docs** - Search for "Term A-6" directly rather than asking LLM
4. **Fix data quality first** - Many matches fail due to NULL rates/maturities that can be extracted from names
5. **Lower confidence is better than no link** - 60% confidence base indenture link is more useful than nothing

### Financial Extraction Issues

| Symptom | Root Cause | Solution |
|---------|------------|----------|
| Q4 data missing or wrong | Tried to extract Q4 from 10-K (has annual data) | Use 8 10-Qs instead - gives clean quarterly data |
| Bank EBITDA shows as 0 or NULL | Banks don't have EBITDA - they use PPNR | Check `is_financial_institution` flag, use bank prompt |
| Investment bank fields NULL | GS/MS use "Net Revenues" not "NII" | Known limitation - different income statement structure |
| Partial quarters extracted | Gemini rate limits (429 errors) | Script handles with 60s waits; re-run to fill gaps |

**Key Learnings from Financial Extraction Work:**
1. **Never use 10-K for quarterly data** - 10-K has annual figures; extracting Q4 requires subtracting 9-month YTD (error-prone)
2. **Use 8 10-Qs for 2 years of clean data** - Simpler, more reliable, enables YoY comparisons
3. **Banks need special handling** - Different income statement structure (NII, PPNR vs Revenue, EBITDA)
4. **Investment banks are different from commercial banks** - GS/MS may not extract cleanly with bank prompt
5. **CLI scripts need `load_dotenv()`** - When running `python -m app.services.X`, env vars aren't loaded automatically

### Scale Error Issues

| Symptom | Root Cause | Solution |
|---------|------------|----------|
| Instrument totals 2-10x higher than financials | Scale mismatch - extraction used wrong multiplier | **ALWAYS check source SEC filing** for scale (e.g., "in millions") before fixing |
| Instrument totals much lower than financials | May be missing amounts, not scale error | Check how many instruments have NULL outstanding |
| Banks show huge mismatch | Banks report total debt including deposits/wholesale funding | Not an error - extraction only captures public notes |
| Recent debt issuances cause mismatch | Financials are quarterly; new bonds issued after quarter end | Compare against most recent filing date |

**Critical Rule**: NEVER blindly apply scale fixes (multiply/divide by 1000). Always:
1. Read the source SEC filing to find the stated scale
2. Verify the extraction matches the source document
3. Consider that comparison data (financials) may be stale or from different period

### SEC Filing URL Issues

| Symptom | Root Cause | Solution |
|---------|------------|----------|
| `sec_filing_url` points to main 10-K/10-Q instead of exhibit | Section extracted from exhibit content but got parent filing URL | Use exhibit-specific URL from `filing_urls` dict with keys like `exhibit_21_{date}`, `indenture_{date}_EX-4_1` |
| URL returns empty page or directory listing | URL is iXBRL wrapper requiring JS, or accession number format wrong | Fetch the filing's `index.json` to find actual exhibit filenames |
| Content doesn't match fetched URL | URL is correct but content is XBRL-wrapped; phrase matching fails on XML tags | Content IS in the page - search for distinctive phrases, not single words |

**SEC URL Architecture:**

The SEC client (`app/services/sec_client.py`) returns two dicts from `get_all_relevant_filings()`:
- `filings_content`: Dict of filing key → content (cleaned text)
- `filing_urls`: Dict of filing key → SEC EDGAR URL

**Filing keys follow these patterns:**
| Key Pattern | Document Type | Example |
|-------------|---------------|---------|
| `10-K_{date}` | Main 10-K filing | `10-K_2024-02-15` |
| `10-Q_{date}` | Main 10-Q filing | `10-Q_2024-11-05` |
| `8-K_{date}` | Main 8-K filing | `8-K_2024-01-10` |
| `exhibit_21_{date}` | Exhibit 21 (subsidiaries) | `exhibit_21_2024-02-15` |
| `indenture_{date}_{exhibit}` | Indenture (EX-4.x) | `indenture_2024-01-10_EX-4_2` |
| `credit_agreement_{date}_{exhibit}` | Credit agreement (EX-10.x) | `credit_agreement_2024-01-10_EX-10_1` |

**Critical Rule for URL Assignment:**
When extracting sections, ALWAYS match the section type to the appropriate URL:
- `exhibit_21` sections → use `exhibit_21_{date}` URL (not `10-K_{date}`)
- `indenture` sections → use `indenture_{date}_*` URL (not `8-K_{date}`)
- `credit_agreement` sections → use `credit_agreement_{date}_*` URL
- `debt_footnote`, `mda_liquidity`, `covenants` from 10-K/10-Q → use main filing URL (correct)

**Backfill Script (`scripts/backfill_sec_urls.py`):**
- Looks up SEC EDGAR API to find filing URLs for documents missing `sec_filing_url`
- Falls back to main filing URL if exhibit URL not found (acceptable but not ideal)
- Run with `--dry-run` first, then `--all` to backfill

## Cost

| Stage | Cost |
|-------|------|
| Gemini extraction | ~$0.008 |
| QA checks (5x) | ~$0.006 |
| Fix iteration | ~$0.01 |
| Claude escalation | ~$0.15-0.50 |

**Target: <$0.03 per company** with 85%+ QA score.

## Agent User Journey

The API supports a two-phase workflow for credit analysis:

### Phase 1: Discovery (Primitives API)

Screen and filter the bond universe using structured data:

```python
import requests
BASE = "https://api.debtstack.ai/v1"

# Find high-yield bonds with equipment collateral
r = requests.get(f"{BASE}/bonds", params={
    "min_ytm": 800,  # 8.0% in basis points
    "seniority": "senior_secured",
    "has_pricing": True,
    "fields": "name,cusip,ticker,ytm_pct,collateral"
})

# Compare leverage across companies
r = requests.get(f"{BASE}/companies", params={
    "ticker": "RIG,VAL,DO,NE",
    "fields": "ticker,name,net_leverage_ratio,total_debt",
    "sort": "-net_leverage_ratio"
})

# What are CHTR's financial covenants?
r = requests.get(f"{BASE}/covenants", params={
    "ticker": "CHTR",
    "covenant_type": "financial",
    "fields": "covenant_name,test_metric,threshold_value,threshold_type"
})

# Compare leverage covenants across cable companies
r = requests.get(f"{BASE}/covenants/compare", params={
    "ticker": "CHTR,ATUS,LUMN",
    "test_metric": "leverage_ratio"
})
```

### Phase 2: Deep Dive (Document Search)

Once user selects a specific bond, answer questions using document search:

```python
# User selected: "RIG 8.75% Senior Secured Notes 2030"
ticker = "RIG"

# Q: "What are the negative covenants?"
r = requests.get(f"{BASE}/documents/search", params={
    "q": "shall not covenant",
    "ticker": ticker,
    "section_type": "indenture"
})

# Q: "Any make-whole premium for early redemption?"
r = requests.get(f"{BASE}/documents/search", params={
    "q": "make-whole redemption price treasury",
    "ticker": ticker,
    "section_type": "indenture"
})

# Q: "What triggers an event of default?"
r = requests.get(f"{BASE}/documents/search", params={
    "q": "event of default failure to pay",
    "ticker": ticker,
    "section_type": "indenture"
})

# Q: "Can they pay dividends?"
r = requests.get(f"{BASE}/documents/search", params={
    "q": "restricted payment dividend distribution",
    "ticker": ticker,
    "section_type": "indenture"
})
```

### How It Works

```
Discovery                              Deep Dive
─────────────────────────             ─────────────────────────────
1. GET /v1/bonds?min_ytm=800
2. Filter results by collateral
3. User picks "RIG 8.75% 2030" ──────► 4. GET /v1/documents/search
                                          ?q=covenant&ticker=RIG
                                       5. Agent summarizes snippets
                                       6. User sees plain English answer
```

**DebtStack provides**: Structured data + document snippets + source links
**Agent provides**: Query conversion + summarization + presentation

### Document Search Coverage

| Term | Docs Found | Use Case |
|------|------------|----------|
| "event of default" | 3,608 | Default triggers, grace periods |
| "change of control" | 2,050 | Put provisions, 101% repurchase |
| "collateral" | 1,752 | Security package analysis |
| "asset sale" | 976 | Mandatory prepayment triggers |
| "make-whole" | 679 | Early redemption premiums |
| "restricted payment" | 464 | Dividend/buyback restrictions |

## Additional Agent Examples

```python
# Q: Which MAG7 company has highest leverage?
r = requests.get(f"{BASE}/companies", params={
    "ticker": "AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA",
    "fields": "ticker,name,net_leverage_ratio",
    "sort": "-net_leverage_ratio",
    "limit": 1
})

# Q: Who guarantees this bond?
r = requests.post(f"{BASE}/entities/traverse", json={
    "start": {"type": "bond", "id": "893830AK8"},
    "relationships": ["guarantees"],
    "direction": "inbound"
})

# Q: Resolve bond identifier
r = requests.get(f"{BASE}/bonds/resolve", params={"q": "RIG 8% 2027"})
```

See `docs/PRIMITIVES_API_SPEC.md` for full specification.
