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

| Company | QA Score | Entities | Debt | Cost | Duration |
|---------|----------|----------|------|------|----------|
| AAPL | 94% | 20 | 7 | $0.0175 | ~60s |
| CRWV | 85% | 4 | 4 | $0.0122 | ~45s |
| RIG | 88% | 17 | 15 | $0.0301 | ~90s |

## Key Design Decisions

1. **Iterative QA feedback loop**: Extractions are verified against source filings with 5 automated checks. Targeted fixes are applied until quality threshold (85%) is met.

2. **Individual debt instruments, not totals**: The prompt explicitly instructs extraction of each bond/note/facility separately. "Long-term debt: $6B" is wrong; "8.75% Senior Notes due 2030: $500M" is correct.

3. **Name normalization**: Entity matching uses case-insensitive, punctuation-normalized comparison. "Transocean Ltd." matches "TRANSOCEAN LTD".

4. **Robust JSON parsing**: `parse_json_robust()` handles common LLM issues: trailing commas, truncated output, unquoted keys, markdown code blocks.

5. **Smart debt section extraction**: Large filings (5M+ chars) are processed with `extract_debt_sections()` which pulls debt-relevant portions using keyword matching.

6. **10-K prioritization**: SEC-API client explicitly fetches most recent 10-K first, since debt and subsidiary info is concentrated there.

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
| `app/services/extraction.py` | SEC-API/EDGAR clients, database save |
| `app/models/schema.py` | SQLAlchemy models |
| `app/api/routes.py` | FastAPI endpoints |
| `scripts/extract_iterative.py` | **CLI** (recommended) |

## Common Issues & Solutions

### Issue: LLM returns aggregated debt totals instead of individual instruments

**Symptom**: Extraction shows "Long-term debt: $6.2B" instead of individual notes/bonds.

**Solution**: The prompt in `tiered_extraction.py` explicitly instructs:
```
CRITICAL - EXTRACT INDIVIDUAL DEBT INSTRUMENTS, NOT TOTALS:
Example of WRONG: "Long-term debt" with total amount
Example of CORRECT: "8.75% Senior Notes due 2030" with specific amount
```

### Issue: Entity verification fails with JSON parse error

**Symptom**: `Expecting ',' delimiter: line 932 column 6`

**Solution**: `parse_json_robust()` in `qa_agent.py` handles malformed JSON:
- Removes trailing commas
- Closes unclosed brackets
- Handles markdown code blocks
- Fixes unquoted keys

### Issue: Parent entity not found (case mismatch)

**Symptom**: `Parent 'TRANSOCEAN LTD' not found for entity 'Subsidiary Name'`

**Solution**: `check_internal_consistency()` uses `normalize_name()`:
```python
def normalize_name(name: str) -> str:
    normalized = name.lower().strip()
    normalized = normalized.rstrip('.')  # "Ltd." -> "ltd"
    return normalized
```

### Issue: 10-K not being fetched, only recent 8-Ks

**Symptom**: Extraction misses debt info, only captures recent events.

**Solution**: `get_all_relevant_filings()` explicitly fetches 10-K first:
```python
# First, get most recent 10-K (critical for debt/structure)
ten_k_filings = self.get_filings_by_ticker(ticker, form_types=["10-K"], max_filings=1)
# Then get recent 10-Q and 8-K
other_filings = self.get_filings_by_ticker(ticker, form_types=["10-Q", "8-K"], max_filings=10)
```

### Issue: Large filing (5M+ chars) truncates debt sections

**Symptom**: Extraction misses debt instruments that exist in the filing.

**Solution**: `extract_debt_sections()` in `tiered_extraction.py` extracts debt-relevant portions:
```python
priority_keywords = [
    "debt - ",  # Footnote headings
    "% notes due",  # Specific instruments
    "credit agreement",
]
# Extracts 10K chars around each keyword match
```

### Issue: Gemini output truncated mid-JSON

**Symptom**: JSON cuts off with unclosed brackets.

**Solution**: Gemini config sets `max_output_tokens: 16000` and context is limited to 100K chars to leave room for response.

## QA Scoring

Each check contributes to the overall score:

| Check | Weight | Pass Criteria |
|-------|--------|---------------|
| Internal Consistency | 20% | No orphan references |
| Entity Verification | 20% | 80%+ entities found in Exhibit 21 |
| Debt Verification | 20% | Amounts match filing (+/- 10%) |
| Completeness | 20% | 80%+ expected items extracted |
| Structure | 20% | Valid hierarchy, holdco exists |

**Threshold**: 85% required to pass. Below threshold triggers fix iterations.

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

## Migrations

```bash
alembic upgrade head     # Apply all
alembic revision -m "description"  # Create new
```

Current:
- `001_initial_schema`: Core tables
- `002_ownership_links`: Complex ownership + VIE columns
