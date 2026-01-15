# CLAUDE.md

This file provides context for AI assistants working on the DebtStack.ai codebase.

## Project Overview

DebtStack.ai is a credit data API for AI agents. It extracts corporate structure and debt information from SEC filings, then serves pre-computed responses via a FastAPI REST API.

**Target users**: AI agents that need credit analysis data (corporate structure, debt details, structural subordination).

**Key insight**: Even with AI, achieving accuracy, speed, and cost-effectiveness for corporate structure extraction requires significant engineering. This is why pre-computed, quality-assured API access is valuable.

## Current Status (January 2026)

**Database**: 178 companies | 3,085 entities | 1,805 debt instruments (1,460 tradeable) | 30 pricing records

**What's Working**:
- ✅ Iterative extraction with QA feedback loop
- ✅ 5-check QA verification system (parallelized for speed)
- ✅ Gemini for extraction, Claude for escalation
- ✅ SEC-API.io integration (paid tier)
- ✅ PostgreSQL on Neon Cloud
- ✅ FastAPI REST API (20 endpoints)
- ✅ Agent integration demos
- ✅ Batch extraction scripts (by sector + by index)
- ✅ S&P 100 / NASDAQ 100 company coverage
- ✅ Parallel QA checks and file downloads (40% faster)
- ✅ Smart model selection (Claude for large companies)
- ✅ Financial statement extraction from 10-Q/10-K (income statement, balance sheet, cash flow)
- ✅ Credit ratio calculations (leverage, interest coverage, margins)
- ✅ SEC Rule 13-01 Obligor Group extraction (asset leakage analysis)
- ✅ Bond pricing with tiered approach (Finnhub → Estimated)
- ✅ Estimated pricing using Treasury yields + credit spreads
- ✅ YTM and spread-to-treasury calculations

**Bond Pricing Architecture**:
```
Tier 1: Finnhub API (TRACE data via ISIN) - requires premium subscription ($300/3 months)
   ↓ fallback
Tier 2: Estimated pricing - Treasury yields + credit spread curves by rating/maturity
```

- **Estimated pricing working**: 30 bonds priced with YTM and spreads
- **Finnhub ready**: API key configured, code ready for premium upgrade
- **FINRA TRACE scraper abandoned**: Modal handling complexity, estimated pricing sufficient

**Recent Optimizations**:
- Parallelized QA checks using `asyncio.gather()` (~15s saved per extraction)
- Parallelized SEC-API file downloads (~3s saved)
- Combined fix prompts for single-call fixes (~8s saved)
- Smart model selection based on Exhibit 21 size (>30KB uses Claude)
- Increased Gemini `max_output_tokens` to 32000 for complex companies

**Next Steps**:
1. Upgrade to Finnhub premium for real TRACE pricing ($300/3 months)
2. Map CUSIPs/ISINs for existing debt instruments (extraction prompt already updated)
3. Complete Phase 2 index extraction (~90 more companies)
4. Deploy to Railway
5. Add authentication/API keys for production
6. Add covenant extraction to prompts
7. Build landing page

## Architecture

### Iterative Extraction with QA (Recommended)

```
SEC-API.io / SEC EDGAR (10-K, 10-Q, 8-K, Exhibits)
    ↓
Initial Extraction: Gemini (~$0.008)
    ↓
QA Agent: 5 verification checks (~$0.006)
  - Internal consistency (free, no LLM)
  - Entity verification (vs Exhibit 21)
  - Debt verification (vs filing amounts)
  - Completeness check
  - Structure verification
    ↓
Score >= 85%? ──Yes──► PostgreSQL → Cache → API
    ↓ No
Targeted Fixes (entities/debt/completeness)
    ↓
[Loop up to 3 iterations]
    ↓
Still failing? → Escalate to Claude Sonnet/Opus
```

### Tested Results

| Company | Ticker | QA Score | Entities | Debt | Cost | Duration |
|---------|--------|----------|----------|------|------|----------|
| Apple | AAPL | 94% | 20 | 7 | $0.0175 | ~60s |
| CoreWeave | CRWV | 85% | 4 | 4 | $0.0122 | ~45s |
| Transocean | RIG | 88% | 17 | 15 | $0.0301 | ~90s |
| Altice USA | ATUS | 76%* | 18 | 14 | $0.0138 | ~42s |

*ATUS has hierarchy issues the QA correctly identified

## Key Design Decisions

1. **Iterative QA feedback loop**: Extractions are verified against source filings with 5 automated checks. Targeted fixes are applied until quality threshold (85%) is met.

2. **Individual debt instruments, not totals**: The prompt explicitly instructs extraction of each bond/note/facility separately. "Long-term debt: $6B" is wrong; "8.75% Senior Notes due 2030: $500M" is correct.

3. **Name normalization**: Entity matching uses case-insensitive, punctuation-normalized comparison. "Transocean Ltd." matches "TRANSOCEAN LTD".

4. **Robust JSON parsing**: `parse_json_robust()` handles common LLM issues: trailing commas, truncated output, unquoted keys, markdown code blocks.

5. **Smart debt section extraction**: Large filings (5M+ chars) are processed with `extract_debt_sections()` which pulls debt-relevant portions using keyword matching.

6. **10-K prioritization**: SEC-API client explicitly fetches most recent 10-K first, since debt and subsidiary info is concentrated there.

7. **CIK fallback**: If ticker search returns no results, falls back to CIK-based search. Some companies (like Altice) aren't indexed by ticker.

8. **HTML/XBRL cleaning**: Modern SEC filings use inline XBRL. `clean_filing_html()` strips tags to extract readable text for QA verification.

## Database Schema

### Core Tables

- **companies**: Master company data (ticker, name, CIK, sector)
- **entities**: Corporate entities with hierarchy
  - `parent_id`: Primary parent for tree traversal
  - `is_vie`, `vie_primary_beneficiary`: VIE tracking
  - `consolidation_method`: full, equity_method, proportional, vie, unconsolidated
- **ownership_links**: Complex ownership (multiple parents, JVs, partial ownership)
- **debt_instruments**: All debt with full terms, linked via `issuer_id`
- **guarantees**: Links debt_instruments to guarantor entities
- **company_financials**: Quarterly financial statement data
  - Income statement: revenue, ebitda, interest_expense, net_income, etc.
  - Balance sheet: cash, total_assets, total_debt, stockholders_equity, etc.
  - Cash flow: operating_cash_flow, capex, financing_cash_flow, etc.
  - All amounts in cents (BigInteger)
- **obligor_group_financials**: SEC Rule 13-01 Obligor Group data
  - Obligor Group financials (issuer + guarantors combined)
  - Asset/revenue/EBITDA leakage metrics
  - Links to related debt instruments
- **bond_pricing**: Bond pricing data (Finnhub TRACE or estimated)
  - Last price, trade date, volume
  - YTM and spread to treasury (in basis points)
  - Staleness tracking for data quality
  - Price source: "TRACE" or "estimated"

### Denormalized Tables

- **company_cache**: Pre-computed JSON responses (`response_structure`, `response_debt`)
- **company_metrics**: Computed credit metrics (`has_structural_sub`, `subordination_score`)

## Key Files

| File | Purpose |
|------|---------|
| `app/services/iterative_extraction.py` | **Main** - Iterative extraction with QA feedback loop |
| `app/services/qa_agent.py` | QA verification agent with 5 parallel checks + `parse_json_robust()` |
| `app/services/tiered_extraction.py` | LLM clients, prompts, `extract_debt_sections()` |
| `app/services/extraction.py` | SEC-API/EDGAR clients, `clean_filing_html()`, parallel downloads |
| `app/services/financial_extraction.py` | Quarterly financial data extraction from 10-Q/10-K |
| `app/services/obligor_group_extraction.py` | SEC Rule 13-01 Obligor Group extraction |
| `app/services/cusip_mapping.py` | OpenFIGI CUSIP mapping service |
| `app/services/bond_pricing.py` | Tiered bond pricing (Finnhub → Estimated) |
| `app/services/estimated_pricing.py` | Credit spread model for estimated prices |
| `app/services/yield_calculation.py` | YTM and spread calculation |
| `app/models/schema.py` | SQLAlchemy models |
| `app/api/routes.py` | FastAPI endpoints (22 routes) |
| `scripts/extract_iterative.py` | **CLI** for single company (recommended) |
| `scripts/extract_financials.py` | CLI for financial data extraction |
| `scripts/extract_obligor_group.py` | CLI for Obligor Group extraction |
| `scripts/map_cusips.py` | CLI for CUSIP mapping |
| `scripts/update_pricing.py` | CLI for pricing updates |
| `scripts/batch_extract.py` | Batch extraction by sector |
| `scripts/batch_index.py` | Batch extraction for S&P 100 / NASDAQ 100 |
| `scripts/load_results_to_db.py` | Load JSON results to database |
| `demos/agent_integration.py` | Chatbot demo with full API integration |
| `demos/agent_demo_offline.py` | Offline demo using local JSON files |

## QA Agent Deep Dive

The QA agent (`qa_agent.py`) runs 6 checks and calculates a weighted score.

### Check Details

| Check | LLM? | What It Does | Pass Criteria |
|-------|------|--------------|---------------|
| Internal Consistency | No | Validates parent/issuer/guarantor refs exist | No orphan references |
| Entity Verification | Yes | Compares entities to Exhibit 21 | 80%+ entities found |
| Debt Verification | Yes | Compares amounts to filing footnotes | Amounts match (+/- 10%) |
| Completeness | Yes | Looks for missed entities/debt | 80%+ items extracted |
| Structure Verification | Yes | Validates hierarchy logic | Valid tree, holdco exists |
| **JV/VIE Verification** | Yes | **Verifies JVs, VIEs, complex ownership captured** | **JVs/VIEs in filing are extracted** |

### Scoring

```
PASS = 100 points (weighted per check)
WARN = 70 points
FAIL = 0 points
SKIP = not counted

Threshold = 85% average to pass
```

### Key Functions

- `normalize_name()`: Case-insensitive, strips trailing periods
- `clean_html()`: Strips HTML tags from Exhibit 21
- `parse_json_robust()`: Handles malformed LLM JSON responses
- `run_qa()`: Orchestrates all 6 checks in parallel

## Common Issues & Solutions

### Filing Content Issues

#### Issue: No Exhibit 21 found for entity verification

**Symptom**: `Entity Verification: SKIPPED - No Exhibit 21 content available`

**Cause**: Exhibit 21 keyed as `exhibit_21_2025-02-13` not `exhibit_21`

**Solution**: `run_qa()` searches for any key containing "exhibit_21":
```python
for key, content in filings.items():
    if "exhibit_21" in key.lower() or "exhibit 21" in key.lower():
        exhibit_21 = content
        break
```

#### Issue: Debt verification shows 0/N verified

**Symptom**: `Debt Verification: 0/9 debt instruments verified`

**Cause**: Filing content is raw HTML/XBRL, not readable text

**Solution**: `clean_filing_html()` in `extraction.py` strips tags:
```python
# Check if content needs cleaning
if content.strip().startswith('<') or content.strip().startswith('<?xml'):
    content = clean_filing_html(content)
```

#### Issue: Company not found via ticker

**Symptom**: `Found 0 filings via SEC-API`

**Cause**: Some companies (like ATUS) aren't indexed by ticker

**Solution**: `get_filings_by_ticker()` falls back to CIK search:
```python
if not filings and cik:
    query["query"]["query_string"]["query"] = f'cik:{cik_num} AND ({form_query})'
```

### Extraction Issues

#### Issue: LLM returns aggregated debt totals

**Symptom**: "Long-term debt: $6.2B" instead of individual notes

**Solution**: Prompt explicitly instructs individual instruments:
```
CRITICAL - EXTRACT INDIVIDUAL DEBT INSTRUMENTS, NOT TOTALS:
Example of WRONG: "Long-term debt" with total amount
Example of CORRECT: "8.75% Senior Notes due 2030" with specific amount
```

#### Issue: Parent entity not found (case mismatch)

**Symptom**: `Parent 'TRANSOCEAN LTD' not found for entity 'Subsidiary'`

**Solution**: `normalize_name()` handles case and punctuation:
```python
def normalize_name(name: str) -> str:
    normalized = name.lower().strip()
    normalized = normalized.rstrip('.')  # "Ltd." -> "ltd"
    return normalized
```

#### Issue: 10-K not being fetched

**Symptom**: Extraction misses debt info, only captures 8-K events

**Solution**: `get_all_relevant_filings()` fetches 10-K first:
```python
ten_k_filings = self.get_filings_by_ticker(ticker, form_types=["10-K"], max_filings=1)
other_filings = self.get_filings_by_ticker(ticker, form_types=["10-Q", "8-K"], max_filings=10)
```

#### Issue: Large filing truncates debt sections

**Symptom**: Missing debt instruments despite existing in filing

**Solution**: `extract_debt_sections()` extracts debt-relevant portions:
```python
priority_keywords = ["debt - ", "% notes due", "credit agreement"]
# Extracts 10K chars around each keyword match
```

#### Issue: Gemini JSON output truncated

**Symptom**: JSON cuts off with unclosed brackets

**Solution**:
- `max_output_tokens: 16000` in Gemini config
- Context limited to 100K chars
- `parse_json_robust()` closes unclosed brackets

### JSON Parsing Issues

#### Issue: Entity verification fails with JSON parse error

**Symptom**: `Expecting ',' delimiter: line 932 column 6`

**Solution**: `parse_json_robust()` handles:
- Trailing commas
- Unclosed brackets (truncated output)
- Markdown code blocks
- Unquoted keys
- Single quotes instead of double

## Extraction Prompt Key Points

From `EXTRACTION_PROMPT_TEMPLATE` in `tiered_extraction.py`:

1. **Amounts in CENTS**: `$1 billion = 100000000000 cents`
2. **Rates in basis points**: `8.50% = 850 bps`
3. **Individual instruments**: Extract each bond/note separately
4. **Entity references must match**: `guarantor_names` must exactly match entity names
5. **First entity is holdco**: `owners: []` for the ultimate parent

## Running Extractions

```bash
# Single company extraction with QA (recommended)
python scripts/extract_iterative.py --ticker RIG --cik 0001451505 --save-db

# Options:
#   --threshold 90      # Quality threshold (default: 85%)
#   --max-iterations 5  # Max fix iterations (default: 3)
#   --save-db           # Save to database
#   --no-save           # Don't save result files

# Batch extraction by sector
python scripts/batch_extract.py --batch telecom --delay 15
python scripts/batch_extract.py --batch all --delay 30

# Available batches: telecom, offshore, airlines, gaming, retail, healthcare,
#                    energy, media, autos, tech, banks, industrials, consumer,
#                    reits, cruises

# Batch extraction for index companies (S&P 100 / NASDAQ 100)
python scripts/batch_index.py --phase 1           # Top 50 by market cap
python scripts/batch_index.py --phase 2           # Remaining ~90 companies
python scripts/batch_index.py --phase all         # All companies
python scripts/batch_index.py --list              # Just list companies
python scripts/batch_index.py --ticker TSLA       # Single company

# Load existing JSON results to database
python scripts/load_results_to_db.py
python scripts/load_results_to_db.py --ticker AAPL

# Simple tiered (no QA)
python scripts/extract_tiered.py --ticker AAPL --cik 0000320193 --tier1 gemini

# QA report only
python scripts/qa_extraction.py --ticker AAPL --cik 0000320193

# Extract quarterly financial data from 10-Q/10-K
python scripts/extract_financials.py --ticker CHTR
python scripts/extract_financials.py --ticker CHTR --save-db
python scripts/extract_financials.py --ticker CHTR --filing-type 10-K
python scripts/extract_financials.py --batch demo --save-db  # CHTR, DAL, HCA, CCL, AAL

# Extract SEC Rule 13-01 Obligor Group data
python scripts/extract_obligor_group.py --ticker CHTR
python scripts/extract_obligor_group.py --ticker CHTR --save-db
python scripts/extract_obligor_group.py --batch demo --save-db

# Map debt instruments to CUSIPs via OpenFIGI
python scripts/map_cusips.py --ticker AAPL
python scripts/map_cusips.py --ticker AAPL --dry-run
python scripts/map_cusips.py --all --limit 50

# Update bond pricing (Finnhub TRACE or estimated)
python scripts/update_pricing.py --ticker AAPL      # Single company
python scripts/update_pricing.py --stale-only       # Only stale prices
python scripts/update_pricing.py --all --limit 50   # Batch update
python scripts/update_pricing.py --summary          # Show pricing stats
```

## Rate Limiting

QA checks now run in parallel using `asyncio.gather()` for faster extraction. For batch extraction, use `--delay 10` or higher between companies to avoid rate limits.

If you see `429 You exceeded your current quota` errors, increase delays or ensure you're using paid Gemini tier (`gemini-2.0-flash`, not `gemini-2.0-flash-exp`).

## API Keys Required

| Key | Required | Purpose |
|-----|----------|---------|
| `ANTHROPIC_API_KEY` | Yes | Claude for escalation |
| `GEMINI_API_KEY` | Recommended | Gemini for Tier 1 (cheapest) |
| `SEC_API_KEY` | Recommended | SEC-API.io for fast filing retrieval |
| `DEEPSEEK_API_KEY` | Optional | Alternative Tier 1 |
| `OPENFIGI_API_KEY` | Optional | OpenFIGI for CUSIP mapping (25 req/min vs 5) |
| `FINNHUB_API_KEY` | Optional | Finnhub for TRACE pricing (premium required for bonds) |
| `FINRA_CLIENT_ID` | Optional | FINRA API (aggregate treasury data only) |
| `FINRA_CLIENT_SECRET` | Optional | FINRA API secret |

## Known Complex Companies

These companies are flagged in `KNOWN_COMPLEX` for special handling:

- **Offshore drilling**: RIG, DO, NE, VAL (complex debt structures)
- **Cable/Telecom**: ATUS, CHTR (many subsidiaries, significant debt)
- **PE-backed**: KKR, APO, BX (multiple layers)
- **Retail**: DLTR, DG (many subsidiaries)

## Cost Optimization

| Stage | Cost | Notes |
|-------|------|-------|
| Gemini extraction | ~$0.008 | Initial extraction |
| QA checks (5x) | ~$0.006 | Verification |
| Fix iteration | ~$0.01 | Per iteration |
| Claude escalation | ~$0.15-0.50 | Only when needed |
| Financial extraction | ~$0.01 | Quarterly financials from 10-Q |

**Target: <$0.03 per company** with 85%+ QA score.
**Financial extraction: ~$0.01 per quarter** for income statement, balance sheet, cash flow.

## Debugging Tips

1. **Check filing content**: Run extraction with print statements to see what content is being passed
2. **Test clean_filing_html()**: Verify debt keywords appear after cleaning
3. **Check Exhibit 21 key**: Print `filings.keys()` to see actual key names
4. **Verify QA prompts**: Check that prompts receive the expected content length
5. **Review QA report**: The `atus_iterative_result.json` shows detailed check results

## Agent Integration

The `demos/` directory contains an agent demo showing how AI agents use the DebtStack API.

### Files

| File | Description |
|------|-------------|
| `demos/api_demo.py` | Simple API response demo for developers (no external deps) |
| `demos/agent_demo.py` | AI agent demo for marketing visualizations (requires Anthropic API) |

### Tool Definitions

The demo defines 10 tools for Claude function calling:

| Tool | Description |
|------|-------------|
| `get_company_structure` | Corporate hierarchy with debt at each entity (flat list) |
| `get_company_hierarchy` | **NEW** Nested tree view of corporate structure |
| `get_company_debt` | All debt instruments with full details |
| `get_company_overview` | Basic company info and metrics |
| `get_company_pricing` | Bond pricing (price, YTM, spread) |
| `get_company_ownership` | **NEW** JVs, complex ownership, partial stakes |
| `search_bonds` | **Enhanced** Search bonds by yield, spread, issuer type, guarantors, sector |
| `search_entities` | **NEW** Search entities across ALL companies (find all VIEs, all Delaware entities) |
| `get_sector_analytics` | **NEW** Sector-level aggregations (avg leverage, total debt) |
| `list_available_companies` | List all companies in database |

### Running Demos

```bash
# API Demo (for developers - shows raw API responses)
python demos/api_demo.py              # Interactive menu
python demos/api_demo.py --all        # Show all endpoints
python demos/api_demo.py --ticker AAPL --endpoint waterfall

# Agent Demo (for marketing - requires Anthropic API key)
python demos/agent_demo.py            # Offline interactive
python demos/agent_demo.py --live     # With live API
python demos/agent_demo.py --demo     # Run demo questions
```

### Key Implementation Details

1. **System prompt** instructs Claude about:
   - Amounts in CENTS (divide by 100 for dollars)
   - Rates in BASIS POINTS (divide by 100 for percentage)
   - Always cite "DebtStack API (extracted from SEC filings)"

2. **Tool execution loop**: Processes tool calls until `stop_reason != "tool_use"`

3. **Offline mode**: `load_extraction()` reads from `results/{ticker}_iterative.json`

## API Endpoints

The API has 26 endpoints organized by function:

### Company Endpoints
| Endpoint | Description |
|----------|-------------|
| `GET /v1/companies` | List all companies with metrics |
| `GET /v1/companies/{ticker}` | Company overview |
| `GET /v1/companies/{ticker}/structure` | Entity hierarchy with debt (flat list) |
| `GET /v1/companies/{ticker}/hierarchy` | **NEW** Entity hierarchy as nested tree |
| `GET /v1/companies/{ticker}/ownership` | **NEW** JVs, complex ownership, partial stakes |
| `GET /v1/companies/{ticker}/debt` | All debt instruments |
| `GET /v1/companies/{ticker}/metrics` | Detailed credit metrics |
| `GET /v1/companies/{ticker}/entities` | List entities with filters |
| `GET /v1/companies/{ticker}/entities/{id}` | Entity detail with children |
| `GET /v1/companies/{ticker}/entities/{id}/debt` | Debt at specific entity |
| `GET /v1/companies/{ticker}/guarantees` | All guarantee relationships |
| `GET /v1/companies/{ticker}/debt/{id}` | Debt instrument detail |
| `GET /v1/companies/{ticker}/financials` | Quarterly financial statements |
| `GET /v1/companies/{ticker}/ratios` | Computed credit ratios (leverage, coverage) |
| `GET /v1/companies/{ticker}/obligor-group` | SEC Rule 13-01 Obligor Group data |
| `GET /v1/companies/{ticker}/pricing` | Bond pricing for all instruments |
| `GET /v1/companies/{ticker}/debt/{id}/pricing` | Pricing for specific bond |
| `GET /v1/companies/{ticker}/maturity-waterfall` | Debt maturity waterfall by year |

### Search Endpoints
| Endpoint | Description |
|----------|-------------|
| `GET /v1/search/companies` | Search with filters (sector, debt range, risk flags) |
| `GET /v1/search/debt` | Search debt across all companies (enhanced filters) |
| `GET /v1/search/entities` | **NEW** Search entities across ALL companies |

### Analytics Endpoints
| Endpoint | Description |
|----------|-------------|
| `GET /v1/compare/companies` | Side-by-side comparison (up to 10) |
| `GET /v1/analytics/sectors` | **NEW** Sector-level aggregations (leverage, debt totals) |

### System Endpoints
| Endpoint | Description |
|----------|-------------|
| `GET /v1/health` | Health check |
| `GET /v1/status` | API status and data coverage |
| `GET /v1/sectors` | List sectors with company counts |

### Search Filters

**Company Search** (`/v1/search/companies`):
- `q`: Text search (name or ticker)
- `sector`: Filter by sector
- `min_debt`, `max_debt`: Debt range (in cents)
- `has_secured_debt`: Boolean filter
- `has_structural_sub`: Boolean filter
- `has_near_term_maturity`: Boolean filter
- `sort_by`: ticker, total_debt, entity_count
- `sort_order`: asc, desc

**Debt Search** (`/v1/search/debt`):
- `seniority`: senior_secured, senior_unsecured, subordinated
- `security_type`: first_lien, second_lien, unsecured
- `instrument_type`: term_loan_b, senior_notes, etc.
- `min_rate`, `max_rate`: Interest rate range (basis points)
- `maturity_before`, `maturity_after`: Date filters
- `rate_type`: fixed, floating
- `issuer_type`: **NEW** Filter by issuer entity type (holdco, opco, subsidiary, spv)
- `has_guarantors`: **NEW** Filter debt with/without guarantors
- `min_outstanding`, `max_outstanding`: **NEW** Outstanding amount range
- `has_cusip`: **NEW** Filter tradeable bonds
- `currency`: **NEW** Filter by currency
- `sector`: **NEW** Filter by company sector
- `min_ytm_bps`, `max_ytm_bps`: Yield to maturity range
- `min_spread_bps`, `max_spread_bps`: Spread to treasury range
- `has_pricing`: Filter bonds with pricing data

**Entity Search** (`/v1/search/entities`): **NEW**
- `entity_type`: holdco, opco, subsidiary, spv, jv, finco, vie
- `jurisdiction`: Filter by jurisdiction (e.g., Delaware, Cayman Islands)
- `is_guarantor`: Filter by guarantor status
- `is_vie`: Filter VIE entities
- `is_unrestricted`: Filter unrestricted subsidiaries
- `has_debt`: Filter entities with/without debt issued
- `q`: Text search on entity name

## Migrations

```bash
alembic upgrade head     # Apply all
alembic revision -m "description"  # Create new
```

Current:
- `001_initial_schema`: Core tables
- `002_ownership_links`: Complex ownership + VIE columns
- `003_expand_benchmark_column`: Expanded benchmark/rate_type columns
- `004_add_company_financials`: Quarterly financial statement table
- `005_add_obligor_group_financials`: SEC Rule 13-01 Obligor Group table
- `006_add_bond_pricing`: Bond pricing table
- `007_make_cusip_nullable`: Allow NULL cusip for estimated pricing
