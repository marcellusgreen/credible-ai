# CLAUDE.md

This file provides context for AI assistants working on the Credible.ai codebase.

## Project Overview

Credible.ai is a credit data API for AI agents. It extracts corporate structure and debt information from SEC filings, then serves pre-computed responses via a FastAPI REST API.

**Target users**: AI agents that need credit analysis data (corporate structure, debt details, structural subordination).

**Key insight**: Even with AI, achieving accuracy, speed, and cost-effectiveness for corporate structure extraction requires significant engineering. This is why pre-computed, quality-assured API access is valuable.

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

### Denormalized Tables

- **company_cache**: Pre-computed JSON responses (`response_structure`, `response_debt`)
- **company_metrics**: Computed credit metrics (`has_structural_sub`, `subordination_score`)

## Key Files

| File | Purpose |
|------|---------|
| `app/services/iterative_extraction.py` | **Main** - Iterative extraction with QA feedback loop |
| `app/services/qa_agent.py` | QA verification agent with 5 checks + `parse_json_robust()` |
| `app/services/tiered_extraction.py` | LLM clients, prompts, `extract_debt_sections()` |
| `app/services/extraction.py` | SEC-API/EDGAR clients, `clean_filing_html()`, database save |
| `app/models/schema.py` | SQLAlchemy models |
| `app/api/routes.py` | FastAPI endpoints |
| `scripts/extract_iterative.py` | **CLI** (recommended) |
| `demos/agent_integration.py` | Chatbot demo with full API integration |
| `demos/agent_demo_offline.py` | Offline demo using local JSON files |

## QA Agent Deep Dive

The QA agent (`qa_agent.py`) runs 5 checks and calculates a weighted score.

### Check Details

| Check | LLM? | What It Does | Pass Criteria |
|-------|------|--------------|---------------|
| Internal Consistency | No | Validates parent/issuer/guarantor refs exist | No orphan references |
| Entity Verification | Yes | Compares entities to Exhibit 21 | 80%+ entities found |
| Debt Verification | Yes | Compares amounts to filing footnotes | Amounts match (+/- 10%) |
| Completeness | Yes | Looks for missed entities/debt | 80%+ items extracted |
| Structure Verification | Yes | Validates hierarchy logic | Valid tree, holdco exists |

### Scoring

```
PASS = 20 points
WARN = 10 points
FAIL = 0 points
SKIP = 10 points (neutral)

Total possible = 100 points
Threshold = 85 points to pass
```

### Key Functions

- `normalize_name()`: Case-insensitive, strips trailing periods
- `clean_html()`: Strips HTML tags from Exhibit 21
- `parse_json_robust()`: Handles malformed LLM JSON responses
- `run_qa()`: Orchestrates all 5 checks

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
# Iterative with QA (recommended)
python scripts/extract_iterative.py --ticker RIG --cik 0001451505

# Options:
#   --threshold 90      # Quality threshold (default: 85%)
#   --max-iterations 5  # Max fix iterations (default: 3)
#   --save-db           # Save to database
#   --no-save           # Don't save result files

# Simple tiered (no QA)
python scripts/extract_tiered.py --ticker AAPL --cik 0000320193 --tier1 gemini

# QA report only
python scripts/qa_extraction.py --ticker AAPL --cik 0000320193
```

## API Keys Required

| Key | Required | Purpose |
|-----|----------|---------|
| `ANTHROPIC_API_KEY` | Yes | Claude for escalation |
| `GEMINI_API_KEY` | Recommended | Gemini for Tier 1 (cheapest) |
| `SEC_API_KEY` | Recommended | SEC-API.io for fast filing retrieval |
| `DEEPSEEK_API_KEY` | Optional | Alternative Tier 1 |

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

**Target: <$0.03 per company** with 85%+ QA score.

## Debugging Tips

1. **Check filing content**: Run extraction with print statements to see what content is being passed
2. **Test clean_filing_html()**: Verify debt keywords appear after cleaning
3. **Check Exhibit 21 key**: Print `filings.keys()` to see actual key names
4. **Verify QA prompts**: Check that prompts receive the expected content length
5. **Review QA report**: The `atus_iterative_result.json` shows detailed check results

## Agent Integration

The `demos/` directory contains chatbot integration examples showing how AI agents use the Credible API.

### Files

| File | Description |
|------|-------------|
| `demos/agent_integration.py` | Full integration requiring running API |
| `demos/agent_demo_offline.py` | Offline demo using local JSON files |

### Tool Definitions

Both demos define 4 tools for Claude function calling:

```python
TOOLS = [
    {
        "name": "get_company_structure",
        "description": "Get corporate structure with debt at each entity",
        "input_schema": {"properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}
    },
    {
        "name": "get_company_debt",
        "description": "Get all debt instruments with full details",
        "input_schema": {"properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}
    },
    {
        "name": "get_company_overview",
        "description": "Get basic company info and metrics",
        "input_schema": {"properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}
    },
    {
        "name": "list_available_companies",
        "description": "List all companies in database",
        "input_schema": {"properties": {}, "required": []}
    }
]
```

### Running Demos

```bash
# Offline (no API needed)
python demos/agent_demo_offline.py
python demos/agent_demo_offline.py "What is Transocean's debt structure?"

# Full integration (requires running API)
uvicorn app.main:app --reload
python demos/agent_integration.py
```

### Key Implementation Details

1. **System prompt** instructs Claude about:
   - Amounts in CENTS (divide by 100 for dollars)
   - Rates in BASIS POINTS (divide by 100 for percentage)
   - Always cite "Credible API (extracted from SEC filings)"

2. **Tool execution loop**: Processes tool calls until `stop_reason != "tool_use"`

3. **Offline mode**: `load_extraction()` reads from `results/{ticker}_iterative.json`

## Migrations

```bash
alembic upgrade head     # Apply all
alembic revision -m "description"  # Create new
```

Current:
- `001_initial_schema`: Core tables
- `002_ownership_links`: Complex ownership + VIE columns
