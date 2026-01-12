# Credible.ai

> The credit API for AI agents

Corporate structure and debt analysis is complex. Even with AI, achieving accuracy, speed, and cost-effectiveness requires significant engineering. Credible.ai does this hard work once, giving you instant API access to pre-computed, quality-assured credit data.

## Why Credible?

**The Problem**: Extracting accurate corporate structure and debt data from SEC filings is surprisingly hard:

- **Accuracy challenges**: LLMs return malformed JSON, misinterpret amounts (cents vs dollars), confuse entity names ("TRANSOCEAN LTD" vs "Transocean Ltd."), and aggregate data instead of extracting individual instruments
- **Speed variability**: A single extraction can take 90-300 seconds with multiple LLM calls, retries, and QA loops
- **Cost uncertainty**: Ad-hoc extraction costs $0.03-0.50+ per company, compounding with retries
- **Expertise required**: Understanding 10-K structure, Exhibit 21, debt footnotes, VIEs, and credit agreement terminology

**The Solution**: Credible runs extraction once with rigorous QA, then serves pre-computed data via fast API.

## Features

- **Iterative QA Extraction**: 5 automated verification checks with targeted fixes until 85%+ quality threshold
- **Cost-Optimized**: Gemini for extraction (~$0.008), Claude for escalation only when needed
- **Individual Debt Instruments**: Extracts each bond, note, and credit facility separately (not just totals)
- **Complex Corporate Structures**: Supports multiple owners, joint ventures, VIEs, partial ownership
- **Pre-computed API Responses**: Sub-second serving via cached JSON with ETag support
- **SEC-API.io Integration**: Fast, reliable filing retrieval without SEC rate limits

## Tested Results

| Company | Ticker | QA Score | Entities | Debt Instruments | Cost | Duration |
|---------|--------|----------|----------|------------------|------|----------|
| Apple | AAPL | 94% | 20 | 7 | $0.0175 | ~60s |
| CoreWeave | CRWV | 85% | 4 | 4 | $0.0122 | ~45s |
| Transocean | RIG | 88% | 17 | 15 | $0.0301 | ~90s |

**Target: <$0.03 per company** with 85%+ QA score and individual debt instrument extraction.

## Quick Start

### 1. Clone and Setup

```bash
cd credible
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your API keys
```

### 2. Database Setup

**Option A: Neon Cloud (Recommended)**
1. Create a free database at [neon.tech](https://neon.tech)
2. Copy the connection string to `.env`:
   ```
   DATABASE_URL=postgresql+asyncpg://user:pass@host/db?ssl=require
   ```

**Option B: Docker**
```bash
docker-compose up -d
```

### 3. Run Migrations

```bash
alembic upgrade head
```

### 4. Extract Your First Company

```bash
# Iterative extraction with QA (recommended)
python scripts/extract_iterative.py --ticker AAPL --cik 0000320193

# Options:
#   --threshold 90      # Quality threshold (default: 85%)
#   --max-iterations 5  # Max fix iterations (default: 3)
#   --save-db           # Save to database
```

The extraction:
1. Downloads 10-K, 10-Q, 8-K filings via SEC-API.io
2. Extracts individual entities and debt instruments with Gemini
3. Runs 5 QA checks against source filings
4. Applies targeted fixes if QA score < threshold
5. Escalates to Claude if still failing
6. Saves to database and pre-computes API responses

### 5. Run the API

```bash
uvicorn app.main:app --reload
```

### 6. Query

```bash
# Get corporate structure with debt at each entity
curl http://localhost:8000/v1/companies/AAPL/structure

# Get all debt instruments
curl http://localhost:8000/v1/companies/AAPL/debt
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /v1/companies` | List all companies with metrics |
| `GET /v1/companies/{ticker}` | Company overview |
| `GET /v1/companies/{ticker}/structure` | Entity hierarchy with full debt details |
| `GET /v1/companies/{ticker}/debt` | All debt instruments with guarantors |

## Structure Response

The `/structure` endpoint returns the corporate tree with debt at each entity:

```json
{
  "company": {"ticker": "RIG", "name": "Transocean Ltd."},
  "structure": {
    "name": "Transocean Ltd.",
    "type": "holdco",
    "is_guarantor": true,
    "debt_at_entity": {
      "total": 0,
      "instruments": []
    },
    "children": [
      {
        "name": "Transocean International Limited",
        "type": "finco",
        "debt_at_entity": {
          "total": 688100000000,
          "instruments": [
            {
              "name": "8.75% Senior Secured Notes due 2030",
              "seniority": "senior_secured",
              "security_type": "first_lien",
              "interest_rate": 875,
              "maturity_date": "2030-02-01",
              "guarantors": ["Transocean Ltd."]
            },
            {
              "name": "8.25% Senior Notes due 2029",
              "seniority": "senior_unsecured",
              "outstanding": 88900000000,
              "maturity_date": "2029-05-01"
            }
          ]
        }
      }
    ]
  }
}
```

## QA Verification System

Every extraction runs through 5 automated checks:

| Check | What it Verifies |
|-------|------------------|
| **Internal Consistency** | Parent/issuer/guarantor references exist, amounts reasonable |
| **Entity Verification** | Entities match Exhibit 21 subsidiaries list |
| **Debt Verification** | Amounts match filing footnotes |
| **Completeness Check** | No major entities or debt instruments missed |
| **Structure Verification** | Hierarchy is valid, holdco identified |

If QA score < 85%, the system applies targeted fixes:
- Entity fixes: Add missing subsidiaries, correct parent references
- Debt fixes: Correct amounts (cents conversion), add missing instruments
- Completeness fixes: Fill gaps identified by QA

## Handling Complex Companies

Some companies require special handling:

**Known Complex Companies** (in `tiered_extraction.py`):
- Offshore drilling: RIG, DO, NE, VAL
- PE-backed with complex debt: KKR, APO, BX
- Retail with many subsidiaries: DLTR, DG

**Common Challenges & Solutions**:

| Challenge | Solution |
|-----------|----------|
| Large 10-K (5M+ chars) | `extract_debt_sections()` pulls debt-relevant portions |
| Aggregated debt totals | Prompt explicitly requires individual instruments |
| Entity name mismatches | Case-insensitive, punctuation-normalized matching |
| Truncated JSON from LLM | `parse_json_robust()` fixes common issues |
| Missing 10-K in recent filings | SEC-API explicitly fetches most recent 10-K first |

## Cost Breakdown

| Component | Cost | Notes |
|-----------|------|-------|
| Gemini extraction | ~$0.008 | Initial extraction |
| QA checks (5x) | ~$0.006 | Entity, debt, completeness verification |
| Fix iterations | ~$0.01/iter | Usually 1-2 iterations |
| Claude escalation | ~$0.15-0.50 | Only when Gemini fails |

**Typical total: $0.02-0.03 per company**

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection (use `ssl=require` for Neon) |
| `ANTHROPIC_API_KEY` | Yes | Claude API for escalation |
| `GEMINI_API_KEY` | Recommended | Gemini API for Tier 1 extraction |
| `SEC_API_KEY` | Recommended | SEC-API.io for fast filing retrieval |
| `DEEPSEEK_API_KEY` | Optional | Alternative Tier 1 model |

## Project Structure

```
credible/
├── app/
│   ├── services/
│   │   ├── iterative_extraction.py  # Main extraction with QA loop
│   │   ├── qa_agent.py              # 5-check verification system
│   │   ├── tiered_extraction.py     # LLM clients, prompts, helpers
│   │   └── extraction.py            # SEC clients, database save
│   ├── models/schema.py             # SQLAlchemy models
│   ├── api/routes.py                # FastAPI endpoints
│   └── main.py                      # FastAPI app
├── scripts/
│   ├── extract_iterative.py         # CLI (recommended)
│   ├── extract_tiered.py            # CLI (no QA)
│   └── qa_extraction.py             # CLI (QA report only)
├── alembic/versions/                # Database migrations
└── results/                         # Extraction outputs
```

## Example CIKs

| Company | Ticker | CIK | Complexity |
|---------|--------|-----|------------|
| Apple | AAPL | 0000320193 | Simple |
| NVIDIA | NVDA | 0001045810 | Simple |
| CoreWeave | CRWV | 0001769628 | Medium |
| Transocean | RIG | 0001451505 | Complex |

## Deployment

### Railway
1. Connect GitHub repo to Railway
2. Add environment variables
3. Deploy (uses `railway.toml`)

### Any Platform
1. Create PostgreSQL database
2. Set environment variables
3. Run `alembic upgrade head`
4. Deploy FastAPI app

## License

MIT
