# DebtStack.ai

*Formerly Credible AI - the GitHub repo URL reflects the old name*

> The credit API for AI agents

Corporate structure and debt analysis is complex. Even with AI, achieving accuracy, speed, and cost-effectiveness requires significant engineering. DebtStack.ai does this hard work once, giving you instant API access to pre-computed, quality-assured credit data.

## Why DebtStack?

**The Problem**: Extracting accurate corporate structure and debt data from SEC filings is surprisingly hard:

- **Accuracy challenges**: LLMs return malformed JSON, misinterpret amounts (cents vs dollars), confuse entity names, and aggregate data instead of extracting individual instruments
- **Speed variability**: A single extraction can take 90-300 seconds with multiple LLM calls, retries, and QA loops
- **Cost uncertainty**: Ad-hoc extraction costs $0.03-0.50+ per company, compounding with retries
- **Expertise required**: Understanding 10-K structure, Exhibit 21, debt footnotes, VIEs, and credit agreement terminology

**The Solution**: DebtStack runs extraction once with rigorous QA, then serves pre-computed data via fast API.

## Current Database

**200+ companies** covering S&P 100 and NASDAQ 100, with thousands of debt instruments, real-time bond pricing, and searchable SEC filing sections. Soon expanding to 1,000+ companies.

Coverage spans all major sectors:

| Sector | Sample Companies |
|--------|------------------|
| Tech | AAPL, MSFT, NVDA, GOOGL, META, AMZN, TSLA, ORCL, AVGO |
| Financials | JPM, GS, BAC, WFC, MS |
| Healthcare | JNJ, UNH, LLY, ABBV, MRK, IDXX, DXCM |
| Consumer | WMT, COST, HD, MCD, KO, PEP, ORLY |
| Telecom/Cable | CHTR, ATUS, TMUS, LUMN |
| Energy | XOM, CVX, OXY |
| Offshore Drilling | RIG, VAL, DO, NE |
| Airlines | AAL, UAL, DAL |
| Semiconductors | NVDA, AMD, AVGO, GFS, CDNS |

## Features

- **Primitives API**: 11 core endpoints optimized for AI agents with field selection and filtering
- **Three-Tier Pricing**: Pay-as-You-Go ($0 + per-call), Pro ($199/mo unlimited), Business ($499/mo full access)
- **Authentication**: API key auth with tier-based access control and credit tracking
- **Observability**: Sentry error tracking, PostHog analytics, Slack alerting
- **Iterative QA Extraction**: 5 automated verification checks with targeted fixes until 85%+ quality threshold
- **Individual Debt Instruments**: Each bond, note, and credit facility extracted separately (not just totals)
- **Structured Covenants**: 1,181 covenants (financial, negative, protective) linked to specific instruments
- **Guarantee Relationships**: 3,831 guarantee records linking debt to guarantor subsidiaries
- **Collateral Tracking**: 626 collateral records with asset types (equipment, vehicles, real estate, etc.)
- **Corporate Ownership Hierarchy**: Nested parent-child structures from SEC Exhibit 21 and indenture parsing
- **Ownership Transparency**: API indicates confidence level for each relationship (verified vs unknown)
- **Direct/Indirect Subsidiaries**: Clear classification of ownership relationships where evidence exists
- **Complex Corporate Structures**: Multiple owners, joint ventures, VIEs, partial ownership
- **Financial Statements**: Quarterly income statement, balance sheet, cash flow from 10-Q/10-K
- **Credit Ratios**: Leverage, interest coverage, margins, liquidity metrics
- **Bond Pricing**: YTM and spread-to-treasury calculations (Finnhub/FINRA TRACE)
- **Treasury Yield History**: 5+ years of daily US Treasury yields (1M-30Y tenors) for accurate historical spread analysis
- **Document Search**: Full-text search across 4,957 indentures and 2,946 credit agreements
- **Pre-computed Responses**: Sub-second API serving via cached JSON with ETag support

## Data Quality Principles

**Estimated data is always flagged.** When data cannot be extracted from SEC filings after repeated attempts and must be estimated or inferred, the API clearly indicates this:

- `issue_date_estimated: true` - Issue date was inferred from maturity date and typical bond tenors (e.g., 10-year for senior notes), not extracted from the filing
- Future estimated fields will follow the same pattern: `{field}_estimated: true`

This transparency ensures you always know when you're working with extracted data vs. inferred data.

### Leverage Ratio Data Quality

Leverage ratios require TTM (Trailing Twelve Months) EBITDA calculations. We track data quality metadata so you know exactly how reliable each ratio is:

```bash
# Get leverage with data quality metadata
curl "/v1/companies?ticker=AAPL&include_metadata=true"
```

Returns:
```json
{
  "ticker": "AAPL",
  "leverage_ratio": 0.63,
  "_metadata": {
    "leverage_data_quality": {
      "ebitda_source": "annual_10k",     // Used 10-K annual figures
      "ebitda_quarters": 4,               // Equivalent to 4 quarters
      "is_annualized": false,             // Not extrapolated
      "ebitda_estimated": false,          // D&A was available
      "ttm_quarters": ["2025FY"]          // Period used
    }
  }
}
```

**TTM EBITDA Calculation Rules:**
- **10-K filing**: Use annual figures directly (already represents full year)
- **10-Q filing**: Sum trailing 4 quarters
- **<4 quarters available**: Annualize (flagged as `is_annualized: true`)
- **No D&A data**: Use operating income as proxy (flagged as `ebitda_estimated: true`)

### Ownership Relationship Transparency

Corporate ownership hierarchies are complex. SEC Exhibit 21 lists subsidiaries but rarely shows intermediate holding structures. We only show parent-child relationships where we have evidence:

```json
{
  "structure": {
    "name": "Transocean Ltd.",
    "ownership_confidence": "root",
    "children": [
      {
        "name": "Transocean Inc.",
        "ownership_confidence": "key_entity",
        "children": [
          {
            "name": "Transocean Offshore Deepwater Holdings",
            "ownership_confidence": "verified"
          }
        ]
      }
    ]
  },
  "ownership_coverage": {
    "known_relationships": 3,
    "unknown_relationships": 227,
    "coverage_pct": 1.3
  },
  "other_subsidiaries": {
    "count": 227,
    "note": "Parent relationships unknown from public SEC filings"
  }
}
```

**Ownership confidence levels:**
- `root` - Ultimate parent company
- `key_entity` - Issuer or guarantor (relationship matters for credit analysis)
- `verified` - Intermediate parent verified from indenture/credit agreement
- `unknown` - From Exhibit 21 only (listed in `other_subsidiaries`, not in hierarchy tree)

### Data Freshness Advantage

DebtStack extracts directly from the latest SEC EDGAR filings, providing **12-18 months fresher data** than LLMs like ChatGPT or Gemini, which rely on stale training data.

| Source | Data Period | NVDA EBITDA Example |
|--------|-------------|---------------------|
| **DebtStack** | Nov 2025 (FY2026 Q3) | $121B |
| Gemini/ChatGPT | Q1 2025 (knowledge cutoff) | $39B |

This matters for fast-growing companies where financial metrics change significantly between LLM training updates.

## Quick Start

### 1. Clone and Setup

```bash
git clone https://github.com/marcellusgreen/credible-ai.git
cd credible
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your API keys
```

### 2. Database Setup

**Neon Cloud (Recommended)**
1. Create a free database at [neon.tech](https://neon.tech)
2. Copy the connection string to `.env`:
   ```
   DATABASE_URL=postgresql+asyncpg://user:pass@host/db?sslmode=require
   ```

### 3. Run Migrations

```bash
alembic upgrade head
```

### 4. Run the API

```bash
uvicorn app.main:app --reload
```

### 5. Get Your API Key

Sign up at [debtstack.ai](https://debtstack.ai) to get your API key. Three pricing tiers available:
- **Pay-as-You-Go**: $0/month, pay per API call ($0.05-$0.15)
- **Pro**: $199/month, unlimited queries
- **Business**: $499/month, full access + historical pricing + bulk export

### 6. Query

```bash
# Set your API key
export DEBTSTACK_API_KEY="ds_your_api_key_here"

# Search companies with field selection
curl -H "X-API-Key: $DEBTSTACK_API_KEY" \
  "https://api.debtstack.ai/v1/companies?ticker=AAPL,MSFT,GOOGL&fields=ticker,name,net_leverage_ratio"

# Search bonds with pricing
curl -H "X-API-Key: $DEBTSTACK_API_KEY" \
  "https://api.debtstack.ai/v1/bonds?seniority=senior_unsecured&min_ytm=8.0"

# Traverse entity relationships (find guarantors)
curl -H "X-API-Key: $DEBTSTACK_API_KEY" \
  -X POST "https://api.debtstack.ai/v1/entities/traverse" \
  -H "Content-Type: application/json" \
  -d '{"start":{"type":"bond","id":"893830AK8"},"relationships":["guarantees"]}'
```

## API Endpoints

### Authentication

| Endpoint | Description |
|----------|-------------|
| `POST /v1/auth/signup` | Create account and get API key |
| `GET /v1/auth/me` | Get current user info and credits (requires API key) |

All other endpoints require an API key passed via `X-API-Key` header.

### Primitives API (Optimized for AI Agents)

These 11 endpoints are designed for agents writing code - simple REST, field selection, powerful filtering.

| Endpoint | Cost (Pay-as-You-Go) | Description |
|----------|---------------------|-------------|
| `GET /v1/companies` | $0.05 | Search companies with field selection and 15+ filters |
| `GET /v1/bonds` | $0.05 | Search/screen bonds with pricing, filters for yield, seniority, maturity |
| `GET /v1/bonds/resolve` | $0.05 | Map bond identifiers - free-text to CUSIP (e.g., "RIG 8% 2027") |
| `GET /v1/financials` | $0.05 | Quarterly financial statements (income, balance sheet, cash flow) |
| `GET /v1/collateral` | $0.05 | Collateral securing debt (types, values, priority) |
| `GET /v1/covenants` | $0.05 | Search structured covenant data (financial, negative, protective) |
| `GET /v1/covenants/compare` | Business only | Compare covenants across multiple companies |
| `GET /v1/companies/{ticker}/changes` | $0.10 | Diff against historical snapshots |
| `POST /v1/entities/traverse` | $0.15 | Graph traversal for guarantor chains, org structure |
| `GET /v1/documents/search` | $0.15 | Full-text search across SEC filings |
| `POST /v1/batch` | Sum of ops | Execute multiple primitives in parallel |

**Example - Field Selection:**
```bash
curl "/v1/companies?ticker=AAPL,MSFT&fields=ticker,name,net_leverage_ratio&sort=-net_leverage_ratio"
```

**Example - Bond Search (Screening):**
```bash
# Find high-yield senior unsecured bonds with pricing data
curl "/v1/bonds?seniority=senior_unsecured&min_ytm=8.0&has_pricing=true"
```

**Example - Bond Resolve (Identifier Lookup):**
```bash
# Map trader shorthand to CUSIP
curl "/v1/bonds/resolve?q=RIG%208%25%202027"

# Lookup by CUSIP
curl "/v1/bonds/resolve?cusip=89157VAG8"
```

**Example - Financials (TTM):**
```bash
# Get trailing twelve months financials for AAPL
curl "/v1/financials?ticker=AAPL&period=TTM"

# Get all Q3 2025 financials for comparison
curl "/v1/financials?fiscal_year=2025&fiscal_quarter=3&format=csv"
```

**Example - Collateral (Recovery Analysis):**
```bash
# Find all first-lien collateral for RIG
curl "/v1/collateral?ticker=RIG&priority=first_lien"

# Get collateral with valuations
curl "/v1/collateral?has_valuation=true&collateral_type=equipment"
```

**Example - Entity Traversal:**
```bash
curl -X POST "/v1/entities/traverse" -d '{"start":{"type":"bond","id":"893830AK8"},"relationships":["guarantees"]}'
```

**Example - Document Search (Deep Dive on a Bond):**
```bash
# After selecting a bond, ask questions about covenants, defaults, etc.
curl "/v1/documents/search?q=event+of+default&ticker=RIG&section_type=indenture"
```

### Advanced Primitives

#### Traverse Entities - `POST /v1/entities/traverse`

Navigate the corporate structure graph. Follow guarantor chains, find subsidiary hierarchies, or trace ownership relationships.

**Request Body:**
```json
{
  "start": {
    "type": "bond" | "entity" | "company",
    "id": "CUSIP, entity UUID, or ticker"
  },
  "relationships": ["guarantees", "subsidiaries", "parent"],
  "direction": "outbound" | "inbound" | "both",
  "max_depth": 3
}
```

**Relationship Types:**
| Relationship | Direction | Description |
|--------------|-----------|-------------|
| `guarantees` | inbound | Find entities that guarantee a bond |
| `subsidiaries` | outbound | Find entities owned by a parent |
| `parent` | outbound | Find the parent of an entity |
| `issuer` | outbound | Find who issued a bond |

**Example - Find Bond Guarantors:**
```bash
curl -X POST "/v1/entities/traverse" \
  -H "Content-Type: application/json" \
  -d '{
    "start": {"type": "bond", "id": "893830AK8"},
    "relationships": ["guarantees"],
    "direction": "inbound"
  }'

# Returns:
{
  "start": {
    "type": "bond",
    "id": "893830AK8",
    "name": "8.000% Senior Secured Notes due 2027"
  },
  "paths": [
    {
      "relationship": "guarantees",
      "entities": [
        {"name": "Transocean Inc.", "jurisdiction": "Cayman Islands"},
        {"name": "Transocean Offshore Deepwater Drilling Inc.", "jurisdiction": "Delaware"},
        {"name": "Triton Holding Company", "jurisdiction": "Cayman Islands"}
      ]
    }
  ],
  "total_guarantors": 47
}
```

**Example - Find Corporate Hierarchy:**
```bash
curl -X POST "/v1/entities/traverse" \
  -d '{
    "start": {"type": "company", "id": "CHTR"},
    "relationships": ["subsidiaries"],
    "max_depth": 2
  }'
```

**Use Cases:**
- Structural subordination analysis (which subs guarantee which debt)
- Recovery analysis (trace collateral through entity chains)
- Regulatory exposure (find entities in specific jurisdictions)

---

#### Search Documents - `GET /v1/documents/search`

Full-text search across 13,862 SEC filing sections including indentures, credit agreements, and debt footnotes.

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `q` | string | Search query (supports phrases: `"event of default"`) |
| `ticker` | string | Filter to specific company |
| `section_type` | string | `indenture`, `credit_agreement`, `debt_footnote`, `exhibit_21` |
| `filing_type` | string | `10-K`, `10-Q`, `8-K` |
| `limit` | int | Max results (default: 10, max: 50) |
| `highlight` | bool | Include matching snippets (default: true) |

**Example - Find Covenant Language:**
```bash
curl "/v1/documents/search?q=restricted+payment+dividend&ticker=RIG&section_type=indenture"

# Returns:
{
  "query": "restricted payment dividend",
  "total_matches": 12,
  "results": [
    {
      "section_type": "indenture",
      "document_title": "Indenture dated April 11, 2024",
      "filing_date": "2024-04-15",
      "snippet": "...shall not, and shall not permit any Restricted Subsidiary to, directly or indirectly, declare or pay any **dividend** or make any **payment** or distribution...",
      "relevance_score": 0.89,
      "source_url": "https://www.sec.gov/Archives/edgar/data/..."
    }
  ]
}
```

**Common Search Queries:**
| Query | Documents | Use Case |
|-------|-----------|----------|
| `"event of default"` | 3,608 | Default triggers, grace periods |
| `"change of control"` | 2,050 | Put provisions, 101% repurchase rights |
| `collateral` | 1,752 | Security package analysis |
| `"asset sale"` | 976 | Mandatory prepayment triggers |
| `"make-whole"` | 679 | Early redemption premiums |
| `"restricted payment"` | 464 | Dividend/buyback restrictions |

**Use Cases:**
- Answer specific covenant questions ("Can they pay dividends?")
- Find default triggers and grace periods
- Research change of control provisions
- Analyze collateral and security packages

### Agent Workflow: Discovery → Deep Dive

```
DISCOVERY (Structured Data)              DEEP DIVE (Document Search)
───────────────────────────              ───────────────────────────
1. GET /v1/bonds?min_ytm=800
2. Filter by collateral, seniority
3. User picks specific bond ───────────► 4. GET /v1/documents/search
                                            ?q=covenant&ticker=XXX
                                         5. Agent summarizes snippets
                                         6. User sees plain English
```

**DebtStack provides**: Structured data + document snippets + source links
**Your agent provides**: Query conversion + summarization + presentation

See `docs/PRIMITIVES_API_SPEC.md` for full specification.

### System
| Endpoint | Description |
|----------|-------------|
| `GET /v1/ping` | Simple health check |
| `GET /v1/health` | Full health check with database |
| `GET /v1/status` | API status and data coverage |
| `GET /v1/sectors` | Sectors with company counts |

## Deployment

### Railway (Recommended)

1. **Connect GitHub**: Link your repo at [railway.app](https://railway.app)
2. **Add Environment Variables**:
   ```
   DATABASE_URL=postgresql+asyncpg://...
   ANTHROPIC_API_KEY=sk-ant-...
   GEMINI_API_KEY=...
   SEC_API_KEY=...
   ```
3. **Deploy**: Railway auto-deploys on push

See `docs/DEPLOYMENT.md` for detailed instructions.

### Docker

```bash
docker-compose up -d
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `REDIS_URL` | Optional | Redis cache (Upstash) |
| `ANTHROPIC_API_KEY` | Yes | Claude API for escalation |
| `GEMINI_API_KEY` | Recommended | Gemini API for extraction |
| `SEC_API_KEY` | Recommended | SEC-API.io for filing retrieval |
| `FINNHUB_API_KEY` | Optional | Finnhub for bond pricing (FINRA TRACE) |
| `STRIPE_API_KEY` | Optional | Stripe for payment processing |
| `STRIPE_WEBHOOK_SECRET` | Optional | Stripe webhook verification |
| `SENTRY_DSN` | Optional | Sentry error tracking |
| `SLACK_WEBHOOK_URL` | Optional | Slack alerts for error spikes |

## Extraction

Extract new companies using the iterative extraction system:

```bash
# Single company with QA (idempotent - safe to re-run)
python scripts/extract_iterative.py --ticker AAPL --cik 0000320193 --save-db

# Force re-run all steps (ignores skip conditions)
python scripts/extract_iterative.py --ticker AAPL --cik 0000320193 --save-db --force

# Batch extraction for all companies
python scripts/extract_iterative.py --all --save-db

# Resume batch from last processed company
python scripts/extract_iterative.py --all --save-db --resume

# Extract financials (single quarter)
python scripts/extract_financials.py --ticker CHTR --save-db

# Extract TTM financials (4 quarters - recommended for accurate leverage)
python scripts/extract_financials.py --ticker CHTR --ttm --save-db

# Recompute metrics after extracting financials
# Uses smart 10-K vs 10-Q logic:
#   - If latest filing is 10-K: uses annual figures directly (already TTM)
#   - If latest filing is 10-Q: sums trailing 4 quarters
python scripts/recompute_metrics.py --ticker CHTR

# Recompute metrics for all companies
python scripts/recompute_metrics.py

# Extract ownership hierarchy from Exhibit 21 HTML indentation
python scripts/extract_exhibit21_hierarchy.py --ticker CHTR --save-db

# Backfill treasury yields (free from Treasury.gov, needed for spread calculations)
python scripts/backfill_treasury_yields.py --from-year 2021 --to-year 2026

# Backfill historical bond pricing (requires Finnhub premium)
python scripts/backfill_pricing_history.py --all --days 1095 --with-spreads
```

The extraction pipeline is **idempotent** - safe to re-run on existing companies:
- Skips steps where data already exists
- Tracks extraction status (success/no_data/error) in database
- Detects when new quarterly financials are available
- Use `--force` to override skip logic and re-extract everything

**Extraction steps (11 total):**
1. Downloads 10-K, 10-Q, 8-K filings via SEC-API.io
2. Extracts entities and debt instruments with Gemini (~$0.008)
3. Runs 5 QA checks against source filings (~$0.006)
4. Applies targeted fixes if QA score < 85%
5. Escalates to Claude if still failing
6. Extracts document sections (indentures, credit agreements, debt footnotes)
7. Links debt instruments to their governing documents
8. Extracts TTM financials (4 quarters)
9. Parses Exhibit 21 for subsidiary hierarchy
10. Extracts guarantees and collateral (using linked documents)
11. Computes metrics and runs QC validation

**Typical cost: $0.02-0.03 per company**

### Script Utilities

All scripts use shared utilities from `scripts/script_utils.py` for consistency:

```python
from script_utils import get_db_session, print_header, run_async

async def main():
    print_header("SCRIPT NAME")
    async with get_db_session() as db:
        # Work with database session
        pass

if __name__ == "__main__":
    run_async(main())
```

Key utilities:
- `get_db_session()` - Async database session with proper cleanup
- `run_async()` - Runs async code with Windows event loop handling
- `print_header()`, `print_summary()` - Consistent CLI output
- `create_base_parser()` - Common CLI arguments (`--ticker`, `--all`, `--limit`)

## Project Structure

```
credible/
├── app/
│   ├── api/
│   │   ├── primitives.py          # Primitives API (11 core endpoints)
│   │   ├── auth.py                # Auth API (signup, user info)
│   │   ├── pricing_api.py         # Pricing API (tiers, credits, usage)
│   │   ├── historical_pricing.py  # Historical bond pricing (Business)
│   │   ├── export.py              # Bulk export (Business)
│   │   ├── usage.py               # Usage analytics (Business)
│   │   └── routes.py              # Legacy REST endpoints
│   ├── core/
│   │   ├── config.py              # Configuration
│   │   ├── database.py            # Database connection
│   │   ├── cache.py               # Redis cache client
│   │   ├── auth.py                # API key generation, validation, tier config
│   │   ├── monitoring.py          # Redis-based API metrics
│   │   ├── alerting.py            # Slack webhook alerts
│   │   └── scheduler.py           # APScheduler (pricing + alert jobs)
│   ├── models/schema.py           # SQLAlchemy models
│   └── services/
│       ├── # UTILITIES (stateless helpers)
│       ├── utils.py                 # Core: JSON parsing, name normalization
│       ├── extraction_utils.py      # SEC filing: HTML cleaning, content combining
│       ├── llm_utils.py             # LLM clients: Gemini, Claude, cost tracking
│       ├── yield_calculation.py     # Financial math: YTM, duration
│       │
│       ├── # SERVICES (orchestration)
│       ├── sec_client.py            # SEC filing clients (SecApiClient, SECEdgarClient)
│       ├── base_extractor.py        # Base class for LLM extraction services
│       ├── extraction.py            # ExtractionService + DB persistence
│       ├── iterative_extraction.py  # Main extraction with QA loop
│       ├── hierarchy_extraction.py  # Exhibit 21 parsing, ownership
│       ├── guarantee_extraction.py  # Guarantee relationships
│       ├── collateral_extraction.py # Collateral for secured debt
│       ├── covenant_extraction.py   # Structured covenant extraction
│       ├── qa_agent.py              # 5-check verification system
│       ├── financial_extraction.py  # Quarterly financials
│       ├── bond_pricing.py          # Pricing calculations
│       ├── pricing_history.py       # Historical pricing backfill
│       └── treasury_yields.py       # Treasury yield curves
├── scripts/                       # CLI tools
│   ├── script_utils.py            # Shared utilities (DB, parsers, progress)
│   ├── extract_iterative.py       # Complete extraction pipeline
│   ├── recompute_metrics.py       # Metrics recomputation
│   └── ...                        # See CLAUDE.md for full list
├── alembic/                       # Database migrations
├── docs/                          # Documentation
│   ├── DEPLOYMENT.md              # Deployment guide
│   ├── ACCOUNT_SETUP.md           # Vendor account setup
│   └── PRIMITIVES_API_SPEC.md     # Primitives API specification
└── results/                       # Extraction outputs
```

## Documentation

- `docs/PRIMITIVES_API_SPEC.md` - **Primitives API specification** with examples
- `docs/DEPLOYMENT.md` - Full deployment guide for Railway
- `docs/ACCOUNT_SETUP.md` - Step-by-step vendor account setup
- `CLAUDE.md` - AI assistant context for development

## License

MIT
