# QA Troubleshooting Guide

This document captures edge cases and debugging strategies for the QA agent system.

## QA System Overview

The QA agent runs 6 checks after each extraction:
1. **Internal Consistency** - validates references exist (no LLM)
2. **Entity Verification** - confirms subsidiaries match Exhibit 21
3. **Debt Verification** - confirms debt amounts match filing footnotes
4. **Completeness Check** - looks for missed entities/debt
5. **Structure Verification** - validates hierarchy makes sense
6. **JV/VIE Verification** - confirms complex ownership is captured

Scoring: PASS=100, WARN=70, FAIL=0. Threshold: 85% to pass without escalation.

## Debugging Workflow

### Step 1: Identify the Failing Check

Run QA on a specific company:
```bash
python scripts/rerun_qa.py --ticker MSFT
```

Output shows which checks failed and why.

### Step 2: Check Document Sections

View stored sections for a company:
```sql
SELECT section_type, section_title, content_length, created_at
FROM document_sections
WHERE company_id = (SELECT id FROM companies WHERE ticker = 'MSFT')
ORDER BY section_type;
```

### Step 3: Re-extract Sections if Needed

```bash
python scripts/reextract_sections.py --ticker MSFT
```

---

## Common Issues and Solutions

### 1. Debt Verification: 99% Discrepancies (Unit Conversion)

**Symptom**: QA reports ~99% discrepancies like:
```
extracted_cents: 55000000000
expected: 550000000 (wrong!)
difference_pct: 99.0
```

**Root Cause**: QA LLM forgetting to multiply dollars by 100 to get cents.

**How We Fixed It**: Added explicit worked examples to `DEBT_VERIFICATION_PROMPT`:
```
WORKED EXAMPLES:
1. Filing: "$550 million"
   → dollars = 550,000,000
   → cents = 550,000,000 × 100 = 55,000,000,000 cents ✓

SANITY CHECK: Extracted amounts should have ~11-12 digits for hundreds of millions of dollars.
```

**If it recurs**: Check that the prompt in `app/services/qa_agent.py` still has the conversion examples.

---

### 2. Debt Verification: 68-89% Discrepancies (Outstanding vs Issuance)

**Symptom**: QA reports large discrepancies for companies with old debt:
```
instrument: "2009 issuance"
filing_amount_text: "$ 3.8 billion"  # WRONG - this is original issuance
extracted_cents: 52000000000         # CORRECT - this is current outstanding ($520M)
difference_pct: 86.32
```

**Root Cause**: Debt schedules show BOTH original issuance AND current outstanding. Filing format:
```
"2009 issuance of $ 3.8 billion ... $ 520 $ 520"
                  ↑ original         ↑ current outstanding
```

QA was grabbing the header amount ($3.8B) instead of the column amount ($520M).

**How We Fixed It**: Added section to `DEBT_VERIFICATION_PROMPT`:
```
=== CRITICAL: OUTSTANDING vs ISSUANCE AMOUNTS ===

EXAMPLE - Microsoft debt schedule format:
"2009 issuance of $ 3.8 billion ... $ 520 $ 520"
- $3.8 billion = ORIGINAL ISSUANCE (when the debt was first issued)
- $520 = CURRENT OUTSTANDING (what's still owed today)

YOU MUST compare extracted amounts to CURRENT OUTSTANDING, not original issuance!
```

**If it recurs**: The QA may be confused by unusual table formats. Check the actual debt_footnote content and add more examples to the prompt if needed.

---

### 3. Entity Verification: Fails Despite Valid Data

**Symptom**: Entity verification fails with message like:
```
"Only 1/12 entities verified, 0 potentially missing"
```
But the extracted entities are correct.

**Root Cause**: The exhibit_21 content in the database is not actually the subsidiary list. Common bad content:
- Auditor consent page ("Consent of Deloitte & Touche LLP")
- Cover page or table of contents
- Power of attorney document

**How We Fixed It**: Added `is_valid_exhibit_21()` validation in `app/services/section_extraction.py`:
```python
def is_valid_exhibit_21(content: str) -> bool:
    """Validate that exhibit_21 content is actually a subsidiary list."""
    red_flags = ["consent of", "power of attorney", "certification of", ...]
    green_flags = ["delaware", "nevada", "ireland", "where incorporated", ...]

    # Reject if red flags in first 500 chars
    # Accept if multiple green flags present
```

**Debugging Steps**:
1. Check exhibit_21 content in database:
   ```sql
   SELECT LEFT(content, 500) FROM document_sections
   WHERE company_id = (SELECT id FROM companies WHERE ticker = 'DO')
   AND section_type = 'exhibit_21';
   ```

2. If it's bad content, re-extract:
   ```bash
   python scripts/reextract_sections.py --ticker DO --section-type exhibit_21
   ```

3. If SEC-API doesn't have valid Exhibit 21, it may need to be fetched directly from EDGAR.

---

### 4. Missing Debt Footnote

**Symptom**: Debt verification skipped with "No debt content available" or low completeness score.

**Root Cause**: Company uses non-standard section naming:
- Standard: "Note 9 - Long-Term Debt"
- Non-standard: "3. Long-Term Obligations and Borrowing Arrangements"

**How We Fixed It**: Added patterns to `DEBT_FOOTNOTE_PATTERNS` in `app/services/section_extraction.py`:
```python
# Numbered sections without "Note" prefix
r"(?i)(\d+\.\s*Long[\-\s]*Term\s+Obligations?\s*(?:and\s+Borrowing\s+Arrangements?)?)(.{1000,}?)(?=\d+\.\s*[A-Z]|\Z)",
r"(?i)(\d+\.\s*(?:Long[\-\s]*Term\s+)?Debt(?:\s+and\s+(?:Credit\s+)?Facilities)?)(.{1000,}?)(?=\d+\.\s*[A-Z]|\Z)",
```

**Debugging Steps**:
1. Check what sections exist:
   ```sql
   SELECT section_type, section_title FROM document_sections
   WHERE company_id = (SELECT id FROM companies WHERE ticker = 'KDP');
   ```

2. If debt_footnote is missing, check the 10-K content to see what the section is called.

3. Add new pattern to `DEBT_FOOTNOTE_PATTERNS` if needed.

---

### 5. Small Discrepancies Under 5% Still Flagged

**Symptom**: QA flags discrepancies that are ≤5%:
```
extracted=35700000000  ($357M)
expected=36000000000   ($360M)
difference_pct: 0.83
```

**Root Cause**: LLM not consistently applying the 5% tolerance rule.

**How We Fixed It**: Added explicit tolerance examples:
```
TOLERANCE RULE: Do NOT report discrepancies for differences ≤5%. These are acceptable:
- $357 million extracted vs $360 million in filing = 0.8% difference = ACCEPTABLE
```

**Note**: This is somewhat LLM-dependent and may occasionally still occur. The difference is often legitimate (different filing dates, rounding).

---

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `scripts/rerun_qa.py` | Re-run QA on specific companies |
| `scripts/reextract_sections.py` | Re-extract document sections |
| `scripts/extract_iterative.py` | Full extraction with QA loop |

### rerun_qa.py Options
```bash
# Single company
python scripts/rerun_qa.py --ticker MSFT

# Multiple companies
python scripts/rerun_qa.py --tickers MSFT,AAPL,GOOGL

# Companies with scale errors (99% discrepancies)
python scripts/rerun_qa.py --scale-errors
```

### reextract_sections.py Options
```bash
# All sections for a company
python scripts/reextract_sections.py --ticker MSFT

# Specific section type
python scripts/reextract_sections.py --ticker DO --section-type exhibit_21
```

---

## Adding New Patterns

### Debt Footnote Patterns

Location: `app/services/section_extraction.py`, `DEBT_FOOTNOTE_PATTERNS`

When adding a new pattern:
1. Test regex on the actual filing content first
2. Ensure lookahead `(?=\d+\.\s*[A-Z]|\Z)` captures the section boundary
3. Minimum length `{1000,}` prevents matching section headers only

### Exhibit 21 Validation

Location: `app/services/section_extraction.py`, `is_valid_exhibit_21()`

Red flags (content is NOT a subsidiary list):
- "consent of", "power of attorney", "certification of"
- "pursuant to", "registered public accounting firm"

Green flags (content IS a subsidiary list):
- Jurisdiction names: "delaware", "nevada", "ireland", "cayman"
- Headers: "where incorporated", "state of incorporation", "jurisdiction"
- Phrases: "subsidiaries of", "wholly owned", "significant subsidiaries"

---

## Monitoring QA Quality

After making prompt changes, validate against known companies:

```bash
# Companies that had issues
python scripts/rerun_qa.py --tickers MSFT,KSS,DO,KDP,VRSK,ODFL

# Check for regressions on previously passing companies
python scripts/rerun_qa.py --tickers AAPL,GOOGL,AMZN
```

Track scores over time to catch regressions.
