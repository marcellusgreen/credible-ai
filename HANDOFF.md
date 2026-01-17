# DebtStack.ai - Context Handoff

Use this prompt to continue work on DebtStack.ai after clearing context.

---

## Handoff Prompt

Copy and paste this to continue:

```
I'm continuing work on DebtStack.ai, a credit data API for AI agents. Please read CLAUDE.md for full context.

## Current State (January 2026)

**What's Built:**
- Iterative extraction with 5-check QA verification system
- Gemini for extraction (~$0.008/company), Claude for escalation
- SEC-API.io integration (paid tier) for filing retrieval
- PostgreSQL on Neon Cloud with FastAPI REST API
- Agent integration demos (online and offline)
- Batch extraction script for multiple companies

**Database:** 38 companies, 779 entities, 330 debt instruments

**Companies by Sector:**
- Tech: AAPL, MSFT, NVDA, GOOGL, META
- Telecom: CHTR, LUMN, DISH, FYBR, ATUS
- Offshore: RIG, VAL, DO, NE
- Airlines: AAL, UAL, DAL
- Energy: OXY, DVN, APA, SWN
- Media: PARA, WBD, FOX
- Autos: F, GM
- Banks: JPM, GS
- Plus: CZR, M, KSS, BBWI, HCA, CHS, KHC, SPG, CCL, CRWV, GE

**Known Issues:**
1. ~13 companies failed extraction (Gemini JSON parsing): AMZN, BAC, BA, CAT, MGM, WYNN, THC, KDP, HSY, VNO, SLG, RCL, NCLH
2. Some companies have 0 debt instruments (APA, F, GE, GS, META, NE, PARA, VAL) - may need re-extraction
3. Rate limiting added to QA agent (7s delays) but batch extraction can still hit limits

**Key Files:**
- `app/services/iterative_extraction.py` - Main extraction loop
- `app/services/qa_agent.py` - 5-check QA system
- `app/services/extraction.py` - SEC-API client, database save
- `scripts/batch_extract.py` - Batch extraction with CIK mappings
- `scripts/load_results_to_db.py` - Load JSON to database
- `demos/agent_demo_offline.py` - Test agent integration

**Environment:**
- GitHub: https://github.com/marcellusgreen/debtstack-ai
- Database: Neon PostgreSQL (connection in .env)
- APIs: Anthropic (paid), Gemini (free tier), SEC-API.io (paid)

**What I'd like to do next:**
[INSERT YOUR TASK HERE - e.g., "retry failed extractions", "deploy to Railway", "add authentication"]
```

---

## Quick Commands

```bash
# Start API
uvicorn app.main:app --reload --port 8001

# Test API
curl http://localhost:8001/v1/companies

# Extract single company
python scripts/extract_iterative.py --ticker AAPL --cik 0000320193 --save-db

# Batch extract
python scripts/batch_extract.py --batch telecom --delay 15

# Load results to database
python scripts/load_results_to_db.py

# Test agent demo
python demos/agent_demo_offline.py "What is Transocean's debt structure?"

# Check database
python -c "
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
import os
from dotenv import load_dotenv
load_dotenv()

async def check():
    engine = create_async_engine(os.getenv('DATABASE_URL'))
    async with async_sessionmaker(engine)() as s:
        r = await s.execute(text('SELECT COUNT(*) FROM companies'))
        print(f'Companies: {r.scalar()}')
    await engine.dispose()
asyncio.run(check())
"
```

---

## Failed Extractions to Retry

These companies failed due to Gemini JSON parsing issues. Retry one at a time:

| Ticker | CIK | Sector |
|--------|-----|--------|
| AMZN | 0001018724 | Tech |
| BAC | 0000070858 | Banks |
| BA | 0000012927 | Industrials |
| CAT | 0000018230 | Industrials |
| MGM | 0000789570 | Gaming |
| WYNN | 0001174922 | Gaming |
| THC | 0000070318 | Healthcare |
| KDP | 0001418135 | Consumer |
| HSY | 0000047111 | Consumer |
| VNO | 0000899629 | REITs |
| SLG | 0001040971 | REITs |
| RCL | 0000884887 | Cruises |
| NCLH | 0001513761 | Cruises |

```bash
# Example retry
python scripts/extract_iterative.py --ticker AMZN --cik 0001018724 --save-db
```

---

## Next Steps Options

1. **Retry failed extractions** - Run the 13 failed companies one at a time
2. **Deploy to Railway** - Production deployment with environment variables
3. **Add authentication** - API keys for production access
4. **Build landing page** - Marketing site for the API
5. **Re-extract 0-debt companies** - Some companies may have missed debt data
6. **Upgrade Gemini** - Paid tier for higher rate limits
