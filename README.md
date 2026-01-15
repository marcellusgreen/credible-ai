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

**178 companies | 3,085 entities | 1,805 debt instruments | 30 priced bonds**

Coverage includes S&P 100 and NASDAQ 100 companies across all sectors:

| Sector | Sample Companies |
|--------|------------------|
| Tech | AAPL, MSFT, NVDA, GOOGL, META, AMZN, TSLA |
| Financials | JPM, GS, BAC, WFC, MS |
| Healthcare | JNJ, UNH, LLY, ABBV, MRK |
| Consumer | WMT, COST, HD, MCD, KO, PEP |
| Telecom/Cable | CHTR, ATUS, TMUS, LUMN |
| Energy | XOM, CVX, OXY |
| Offshore Drilling | RIG, VAL, DO, NE |
| Airlines | AAL, UAL, DAL |

## Features

- **26 REST API endpoints** for comprehensive credit data access
- **Iterative QA Extraction**: 5 automated verification checks with targeted fixes until 85%+ quality threshold
- **Individual Debt Instruments**: Each bond, note, and credit facility extracted separately (not just totals)
- **Complex Corporate Structures**: Multiple owners, joint ventures, VIEs, partial ownership
- **Financial Statements**: Quarterly income statement, balance sheet, cash flow from 10-Q/10-K
- **Credit Ratios**: Leverage, interest coverage, margins, liquidity metrics
- **Bond Pricing**: YTM and spread-to-treasury calculations
- **Pre-computed Responses**: Sub-second API serving via cached JSON with ETag support

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

### 5. Query

```bash
# Get corporate structure with debt at each entity
curl http://localhost:8000/v1/companies/AAPL/structure

# Get all debt instruments
curl http://localhost:8000/v1/companies/AAPL/debt

# Get quarterly financials
curl http://localhost:8000/v1/companies/CHTR/financials

# Search high-yield debt
curl "http://localhost:8000/v1/search/debt?min_rate=700&seniority=senior_secured"
```

## API Endpoints

### Company Data
| Endpoint | Description |
|----------|-------------|
| `GET /v1/companies` | List all companies with metrics |
| `GET /v1/companies/{ticker}` | Company overview |
| `GET /v1/companies/{ticker}/structure` | Entity hierarchy with debt |
| `GET /v1/companies/{ticker}/hierarchy` | Nested tree view |
| `GET /v1/companies/{ticker}/debt` | All debt instruments |
| `GET /v1/companies/{ticker}/metrics` | Credit metrics |
| `GET /v1/companies/{ticker}/financials` | Quarterly financial statements |
| `GET /v1/companies/{ticker}/ratios` | Credit ratios (leverage, coverage) |
| `GET /v1/companies/{ticker}/pricing` | Bond pricing (YTM, spreads) |
| `GET /v1/companies/{ticker}/guarantees` | Guarantee relationships |
| `GET /v1/companies/{ticker}/maturity-waterfall` | Debt maturity by year |

### Search & Analytics
| Endpoint | Description |
|----------|-------------|
| `GET /v1/search/companies` | Search with filters (sector, debt, risk flags) |
| `GET /v1/search/debt` | Search debt across all companies |
| `GET /v1/search/entities` | Search entities across all companies |
| `GET /v1/compare/companies` | Side-by-side comparison (up to 10) |
| `GET /v1/analytics/sectors` | Sector-level aggregations |

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

# Extract financials
python scripts/extract_financials.py --ticker CHTR --save-db
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
│   ├── api/routes.py              # FastAPI endpoints (26 routes)
│   ├── core/config.py             # Configuration
│   ├── core/database.py           # Database connection
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
│   └── ACCOUNT_SETUP.md           # Vendor account setup
└── results/                       # Extraction outputs
```

## Documentation

- `docs/DEPLOYMENT.md` - Full deployment guide for Railway
- `docs/ACCOUNT_SETUP.md` - Step-by-step vendor account setup
- `CLAUDE.md` - AI assistant context for development

## License

MIT
