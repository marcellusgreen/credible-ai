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

**189 companies | 5,979 entities | 2,849 debt instruments | 30 priced bonds | 4,881 guarantees | 230 collateral records**

Coverage includes S&P 100 and NASDAQ 100 companies across all sectors:

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

- **Primitives API**: 8 core endpoints optimized for AI agents with field selection
- **Authentication**: API key auth with credit-based usage tracking
- **Iterative QA Extraction**: 5 automated verification checks with targeted fixes until 85%+ quality threshold
- **Individual Debt Instruments**: Each bond, note, and credit facility extracted separately (not just totals)
- **Guarantee Relationships**: 4,881 guarantee records linking debt to guarantor subsidiaries
- **Collateral Tracking**: 230 collateral records with asset types (equipment, vehicles, real estate, etc.)
- **Complex Corporate Structures**: Multiple owners, joint ventures, VIEs, partial ownership
- **Financial Statements**: Quarterly income statement, balance sheet, cash flow from 10-Q/10-K
- **Credit Ratios**: Leverage, interest coverage, margins, liquidity metrics
- **Bond Pricing**: YTM and spread-to-treasury calculations
- **Pre-computed Responses**: Sub-second API serving via cached JSON with ETag support

## Data Quality Principles

**Estimated data is always flagged.** When data cannot be extracted from SEC filings after repeated attempts and must be estimated or inferred, the API clearly indicates this:

- `issue_date_estimated: true` - Issue date was inferred from maturity date and typical bond tenors (e.g., 10-year for senior notes), not extracted from the filing
- Future estimated fields will follow the same pattern: `{field}_estimated: true`

This transparency ensures you always know when you're working with extracted data vs. inferred data.

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

Sign up at [debtstack.ai](https://debtstack.ai) to get your API key. Free tier includes 1,000 credits/month.

### 6. Query

```bash
# Set your API key
export DEBTSTACK_API_KEY="ds_your_api_key_here"

# Search companies with field selection
curl -H "X-API-Key: $DEBTSTACK_API_KEY" \
  "https://credible-ai-production.up.railway.app/v1/companies?ticker=AAPL,MSFT,GOOGL&fields=ticker,name,net_leverage_ratio"

# Search bonds with pricing
curl -H "X-API-Key: $DEBTSTACK_API_KEY" \
  "https://credible-ai-production.up.railway.app/v1/bonds?seniority=senior_unsecured&min_ytm=8.0"

# Traverse entity relationships (find guarantors)
curl -H "X-API-Key: $DEBTSTACK_API_KEY" \
  -X POST "https://credible-ai-production.up.railway.app/v1/entities/traverse" \
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

These 8 endpoints are designed for agents writing code - simple REST, field selection, powerful filtering.

| Endpoint | Credits | Description |
|----------|---------|-------------|
| `GET /v1/companies` | 1 | Search companies with field selection and 15+ filters |
| `GET /v1/bonds` | 1 | Search bonds across all companies with pricing |
| `GET /v1/bonds/resolve` | 1 | Resolve bond identifiers (CUSIP lookup, fuzzy search) |
| `GET /v1/pricing` | 1 | Bond pricing data with YTM/spread filters |
| `GET /v1/companies/{ticker}/changes` | 2 | Diff/changelog against historical snapshots |
| `POST /v1/entities/traverse` | 3 | Graph traversal for guarantor chains, org structure |
| `GET /v1/documents/search` | 3 | Full-text search across SEC filings |
| `POST /v1/batch` | Sum | Execute multiple primitives in parallel |

**Example - Field Selection:**
```bash
curl "/v1/companies?ticker=AAPL,MSFT&fields=ticker,name,net_leverage_ratio&sort=-net_leverage_ratio"
```

**Example - Bond Search:**
```bash
curl "/v1/bonds?seniority=senior_unsecured&min_ytm=8.0&has_pricing=true"
```

**Example - Entity Traversal:**
```bash
curl -X POST "/v1/entities/traverse" -d '{"start":{"type":"bond","id":"893830AK8"},"relationships":["guarantees"]}'
```

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
| `ANTHROPIC_API_KEY` | Yes | Claude API for escalation |
| `GEMINI_API_KEY` | Recommended | Gemini API for extraction |
| `SEC_API_KEY` | Recommended | SEC-API.io for filing retrieval |

## Extraction

Extract new companies using the iterative extraction system:

```bash
# Single company with QA
python scripts/extract_iterative.py --ticker AAPL --cik 0000320193 --save-db

# Batch extraction
python scripts/batch_index.py --phase 1

# Extract financials (single quarter)
python scripts/extract_financials.py --ticker CHTR --save-db

# Extract TTM financials (4 quarters - recommended for accurate leverage)
python scripts/extract_financials.py --ticker CHTR --ttm --save-db

# Recompute metrics after extracting financials
python scripts/recompute_metrics.py --ticker CHTR
```

The extraction:
1. Downloads 10-K, 10-Q, 8-K filings via SEC-API.io
2. Extracts entities and debt instruments with Gemini (~$0.008)
3. Runs 5 QA checks against source filings (~$0.006)
4. Applies targeted fixes if QA score < 85%
5. Escalates to Claude if still failing
6. Saves to database and pre-computes API responses

**Typical cost: $0.02-0.03 per company**

## Project Structure

```
credible/
├── app/
│   ├── api/
│   │   ├── routes.py              # Legacy FastAPI endpoints
│   │   └── primitives.py          # Primitives API (8 core endpoints)
│   ├── core/
│   │   ├── config.py              # Configuration
│   │   ├── database.py            # Database connection
│   │   └── cache.py               # Redis cache client
│   ├── models/schema.py           # SQLAlchemy models
│   └── services/
│       ├── iterative_extraction.py  # Main extraction with QA loop
│       ├── qa_agent.py              # 5-check verification system
│       ├── tiered_extraction.py     # LLM clients and prompts
│       ├── extraction.py            # SEC clients, filing processing
│       ├── financial_extraction.py  # Quarterly financials
│       └── bond_pricing.py          # Pricing calculations
├── scripts/                       # CLI tools
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
