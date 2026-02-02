# CLAUDE.md

Context for AI assistants working on the DebtStack.ai codebase.

> **IMPORTANT**: Before starting work, read `WORKPLAN.md` for current priorities, active tasks, and session history. Update the session log at the end of each session.

## What's Next

**Immediate**: Create Stripe products for new pricing tiers in Stripe Dashboard and update environment variables with actual price IDs.

**Then**:
1. Finnhub pricing expansion (30 → 200+ bonds)
2. SDK publication to PyPI
3. Mintlify docs deployment to docs.debtstack.ai

## Project Overview

DebtStack.ai is a credit data API for AI agents. It extracts corporate structure and debt information from SEC filings, then serves pre-computed responses via a FastAPI REST API.

**Target users**: AI agents that need credit analysis data (corporate structure, debt details, structural subordination).

## Current Status (February 2026)

**Database**: 201 companies | 5,374 entities | 3,056 active debt instruments | 30 priced bonds | 13,862 document sections | 3,831 guarantees | 626 collateral records

**Document Coverage**: 93% of instruments linked (2,829 / 3,056) via `DebtInstrumentDocument` junction table

**Entity Distribution**: 94 companies (47%) have 50+ entities, 71 (35%) have 11-50, 34 (17%) have 2-10, 2 have 1 entity (ODFL, TTD - legitimate single-entity companies with no Exhibit 21 subsidiaries)

**Ownership Coverage**: 199/201 companies have identified root entity (`is_root=true`); 862 explicit parent-child relationships

**Data Quality**: QC audit passing - 0 critical, 0 errors, 4 warnings (2026-01-29)

**Deployment**: Railway with Neon PostgreSQL + Upstash Redis
- Live at: `https://credible-ai-production.up.railway.app`

**What's Working**:
- **Three-Tier Pricing**: Pay-as-You-Go ($0 + per-call), Pro ($199/mo), Business ($499/mo)
- **Primitives API**: 11 core endpoints optimized for AI agents (field selection, powerful filters)
- **Business-Only Endpoints**: Historical pricing, covenant compare, bulk export, usage analytics
- **Auth & Credits**: API key auth, tier-based access control, usage tracking with cost
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

This separation keeps business logic testable and reusable while scripts handle CLI concerns.

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
| `scripts/extract_iterative.py` | Complete extraction pipeline CLI (thin wrapper) |
| `scripts/recompute_metrics.py` | Metrics recomputation CLI (thin wrapper) |
| `scripts/script_utils.py` | Shared CLI utilities (DB sessions, parsers, progress) |
| `app/models/schema.py` | SQLAlchemy models (includes User, UserCredits, UsageLog) |
| `app/core/config.py` | Environment configuration |
| `app/core/database.py` | Database connection |

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
- `company_financials`: Quarterly financial statements (amounts in cents)
- `bond_pricing`: Pricing data (YTM, spreads in basis points)
- `document_sections`: SEC filing sections for full-text search (TSVECTOR + GIN index)

**Auth Tables**:
- `users`: User accounts (email, api_key_hash, tier, stripe IDs, rate_limit_per_minute, team_seats)
- `user_credits`: Credit balance and billing cycle (credits_remaining, credits_purchased, credits_used)
- `usage_log`: API usage tracking (endpoint, cost_usd, tier_at_time_of_request)

**Pricing Tables** (Three-Tier System):
- `bond_pricing_history`: Historical bond pricing snapshots (Business tier only)
- `team_members`: Business tier multi-seat team management
- `coverage_requests`: Business tier custom company coverage requests

**Cache Tables**:
- `company_cache`: Pre-computed JSON responses + `extraction_status` (JSONB tracking step attempts)
- `company_metrics`: Computed credit metrics + `source_filings` JSONB for TTM provenance

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
```

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

For accurate leverage ratios, use `--ttm` to extract 4 quarters of financial data:
- Fetches most recent 3 10-Qs (Q1, Q2, Q3) + 1 10-K (full year as Q4 proxy)
- Uses `periodOfReport` from SEC API to determine fiscal quarter
- Stores `filing_type` field ("10-K" or "10-Q") for each record
- If fewer than 4 quarters available, annualizes what's available

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
Finnhub API (FINRA TRACE)
    ↓
scripts/update_pricing.py (daily batch)
    ↓
bond_pricing table (current prices)
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

# Backfill historical data (optional)
python scripts/backfill_pricing_history.py --days 365
```

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `DATABASE_URL` | Yes | PostgreSQL (use `sslmode=require` for Neon) |
| `REDIS_URL` | Optional | Redis cache (Upstash, use `rediss://` for TLS) |
| `ANTHROPIC_API_KEY` | Yes | Claude for escalation |
| `GEMINI_API_KEY` | Recommended | Gemini for extraction |
| `SEC_API_KEY` | Recommended | SEC-API.io for filing retrieval |
| `FINNHUB_API_KEY` | Optional | Finnhub for bond pricing (~$100/mo tier) |

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

Shared utilities for CLI scripts:

```python
from script_utils import (
    get_db_session,         # Async database session context manager
    get_all_companies,      # Get all companies with CIKs
    get_company_by_ticker,  # Get single company by ticker
    create_base_parser,     # Base argparse with --ticker/--all/--limit
    create_fix_parser,      # Fix script parser with --save/--verbose
    create_extract_parser,  # Extraction parser with --cik/--save-db/--skip-existing
    print_header,           # Print formatted header
    print_summary,          # Print stats summary
    print_progress,         # Print progress indicator
    process_companies,      # Batch process companies with progress
    run_async,              # Run async function with Windows event loop handling
)
```

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
BASE = "https://credible-ai-production.up.railway.app/v1"

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
