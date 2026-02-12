#!/usr/bin/env python3
"""
Phase 6: Backfill outstanding amounts from document_sections table.

Targeted script for MISSING_ALL companies that have instruments in DB but $0
outstanding amounts. Uses the richer document_sections table (multiple debt
footnotes per company across 10-Ks and 10-Qs) instead of the single
company_cache.debt_footnote from Phase 4.

Strategy:
  1. Gather all relevant document sections for a company (debt_footnotes +
     mda_liquidity + desc_securities), ordered by priority
  2. Send instrument list + document content to Gemini with a focused prompt:
     "Here are the instruments, find their outstanding amounts"
  3. Try multiple documents until all instruments have amounts or docs exhausted
  4. Match results back to DB using rate + maturity year scoring
  5. Update outstanding field directly on matched instruments

Usage:
    # Analyze what instruments need amounts and what docs are available
    python scripts/backfill_amounts_from_docs.py --analyze

    # Fix single company (dry run)
    python scripts/backfill_amounts_from_docs.py --fix --ticker CSGP --dry-run

    # Fix all MISSING_ALL companies
    python scripts/backfill_amounts_from_docs.py --fix

    # Fix all MISSING_ALL + MISSING_SIGNIFICANT
    python scripts/backfill_amounts_from_docs.py --fix --all-missing

    # Use a specific model
    python scripts/backfill_amounts_from_docs.py --fix --model gemini-2.5-pro
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, text, func, and_, or_
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models import Company, DebtInstrument, DocumentSection
from app.services.utils import parse_json_robust


# =============================================================================
# CONSTANTS
# =============================================================================

# Document priority order for amount extraction
DOC_PRIORITY = [
    ('10-K', 'debt_footnote'),      # Priority 1: Annual, most comprehensive
    ('10-Q', 'debt_footnote'),      # Priority 2: Quarterly, more recent
    ('10-K', 'mda_liquidity'),      # Priority 3: MD&A sometimes has debt summaries
    ('10-Q', 'mda_liquidity'),      # Priority 4: Quarterly MD&A
    ('10-K', 'desc_securities'),    # Priority 5: Banks — detailed bond descriptions
    ('10-Q', 'desc_securities'),    # Priority 6: Quarterly desc_securities
]

# Gemini cost per million tokens
COST_PER_MILLION = {
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.5-pro": {"input": 1.25, "output": 5.00},
    "gemini-2.5-pro-preview-05-06": {"input": 1.25, "output": 5.00},
}

# Max content size to send to Gemini (characters)
MAX_CONTENT_CHARS = 120_000


# =============================================================================
# PROMPT
# =============================================================================

TARGETED_EXTRACTION_PROMPT = """Find the CURRENT OUTSTANDING AMOUNT for each of these specific debt instruments from the SEC filing excerpt below.

Company: {company_name}

INSTRUMENTS TO FIND:
{instrument_list}

DOCUMENT ({filing_type} filed {filing_date}):
{content}

Return JSON with the amounts you found. For each instrument where you can determine the outstanding amount, include it in the response.

{{
    "amounts": [
        {{
            "instrument_index": <1-based index from the list above>,
            "outstanding_cents": <amount in CENTS — multiply dollars by 100>,
            "confidence": "high" | "medium" | "low",
            "notes": "<brief note on where you found this, e.g. 'from debt maturity table'>"
        }}
    ],
    "scale_detected": "<scale stated in document, e.g. 'in millions', 'in thousands', or 'dollars'>",
    "instruments_not_found": [<list of instrument indices not found in this document>]
}}

CRITICAL RULES:
- ALL amounts must be in CENTS (multiply dollar amounts by 100)
- $1 billion = 100,000,000,000 cents
- $500 million = 50,000,000,000 cents
- $1 million = 100,000,000 cents
- $1 thousand = 100,000 cents
- DETECT THE SCALE from the document header/tables (look for "in millions", "in thousands", etc.)
- If amounts are "in millions", multiply by 100,000,000 (1M dollars = 100M cents)
- If amounts are "in thousands", multiply by 100,000 (1K dollars = 100K cents)
- For revolvers/credit facilities, use the DRAWN amount (not total commitment/capacity)
- Match instruments by coupon rate AND maturity year — these are the primary identifiers
- If an instrument appears with a slightly different name but same rate and maturity, that's a match
- Return ONLY instruments you can confidently match — do not guess
- If a document shows "$0" or "fully repaid" for an instrument, do NOT include it
"""


# =============================================================================
# GEMINI INTERFACE
# =============================================================================

async def get_gemini_response(prompt: str, max_retries: int = 3,
                               model_name: str = 'gemini-2.0-flash') -> dict:
    """Call Gemini and parse JSON response."""
    from google import genai

    client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))

    is_pro = 'pro' in model_name.lower()
    max_tokens = 32768 if is_pro else 8192

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={
                    'temperature': 0.1,
                    'max_output_tokens': max_tokens,
                }
            )
            if not response.text:
                finish = response.candidates[0].finish_reason if response.candidates else 'unknown'
                print(f"    Warning: empty response (finish_reason={finish}), retrying...")
                time.sleep(5)
                continue
            result = parse_json_robust(response.text)
            if result and isinstance(result, dict) and 'amounts' in result:
                return result
            # Maybe it returned just the amounts array
            if result and isinstance(result, list):
                return {'amounts': result}
            print(f"    Warning: unexpected response format, retrying...")
        except Exception as e:
            if '429' in str(e) or 'quota' in str(e).lower():
                wait = 30 * (attempt + 1)
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"    Error: {e}")
                if attempt < max_retries - 1:
                    time.sleep(5)
    return {'amounts': []}


# =============================================================================
# MATCHING LOGIC (reused from backfill_outstanding_from_filings.py)
# =============================================================================

def match_extracted_to_instrument(extracted: dict, db_instruments: list,
                                   already_matched: set) -> tuple:
    """Match an extracted amount to a DB instrument by index or rate+maturity.

    The prompt asks Gemini to return instrument_index (1-based), so we use that
    as the primary match. Falls back to rate+maturity scoring if index is missing
    or invalid.

    Returns (db_instrument, confidence) or (None, 0).
    """
    # Primary: match by instrument_index (1-based)
    idx = extracted.get('instrument_index')
    if idx is not None:
        try:
            idx_int = int(idx) - 1  # Convert to 0-based
            if 0 <= idx_int < len(db_instruments):
                inst = db_instruments[idx_int]
                if inst.id not in already_matched:
                    return inst, 1.0
        except (ValueError, TypeError):
            pass

    # Fallback: rate + maturity matching (shouldn't be needed often)
    ext_rate = extracted.get('coupon_rate')
    ext_year = extracted.get('maturity_year')
    ext_name = (extracted.get('name') or '').lower()

    if not ext_rate and not ext_year:
        return None, 0

    best_match = None
    best_score = 0

    for db_inst in db_instruments:
        if db_inst.id in already_matched:
            continue
        score = 0
        db_name = (db_inst.name or '').lower()

        # Rate match
        if ext_rate is not None:
            try:
                ext_rate_f = float(ext_rate)
            except (ValueError, TypeError):
                ext_rate_f = None

            if ext_rate_f is not None:
                db_rate_match = re.search(r'(\d+\.?\d*)\s*%', db_name)
                if db_rate_match:
                    db_rate = float(db_rate_match.group(1))
                    if abs(db_rate - ext_rate_f) < 0.15:
                        score += 0.5
                if score < 0.5 and db_inst.interest_rate:
                    db_rate_pct = db_inst.interest_rate / 100.0
                    if abs(db_rate_pct - ext_rate_f) < 0.15:
                        score += 0.5

        # Year match
        if ext_year is not None:
            try:
                ext_year_i = int(ext_year)
            except (ValueError, TypeError):
                ext_year_i = None
            if ext_year_i is not None:
                db_year_match = re.search(r'20(\d{2})', db_name)
                if db_year_match:
                    db_year = int('20' + db_year_match.group(1))
                    if db_year == ext_year_i:
                        score += 0.5
                if score < 1.0 and db_inst.maturity_date:
                    if db_inst.maturity_date.year == ext_year_i:
                        score += 0.5

        # Type bonus
        if ext_name:
            for term in ['term loan', 'revolver', 'revolving', 'credit facility',
                         'debenture', 'commercial paper', 'finance lease', 'mortgage']:
                if term in ext_name and term in db_name:
                    score += 0.3
                    break

        if score > best_score and score >= 0.8:
            best_score = score
            best_match = db_inst

    return best_match, best_score


# =============================================================================
# CORE FUNCTIONS
# =============================================================================

async def get_documents_for_company(session, company_id) -> list:
    """Get all relevant document sections ordered by priority.

    Returns list of DocumentSection objects ordered by:
    1. Section type priority (debt_footnote > mda_liquidity > desc_securities)
    2. Doc type priority (10-K > 10-Q)
    3. Filing date (newest first within each priority group)
    """
    result = await session.execute(
        select(DocumentSection).where(
            DocumentSection.company_id == company_id,
            DocumentSection.section_type.in_([
                'debt_footnote', 'mda_liquidity', 'desc_securities'
            ])
        ).order_by(DocumentSection.filing_date.desc())
    )
    all_docs = list(result.scalars().all())

    # Sort by priority
    def doc_sort_key(doc):
        for i, (dt, st) in enumerate(DOC_PRIORITY):
            if doc.doc_type == dt and doc.section_type == st:
                return (i, -doc.filing_date.toordinal())
        return (len(DOC_PRIORITY), -doc.filing_date.toordinal())

    all_docs.sort(key=doc_sort_key)
    return all_docs


async def get_instruments_needing_amounts(session, company_id) -> list:
    """Get active instruments with NULL or 0 outstanding."""
    result = await session.execute(
        select(DebtInstrument).where(
            DebtInstrument.company_id == company_id,
            DebtInstrument.is_active == True,
            or_(
                DebtInstrument.outstanding == None,
                DebtInstrument.outstanding <= 0,
            )
        )
    )
    return list(result.scalars().all())


def format_instrument_list(instruments: list) -> str:
    """Format instrument list for the Gemini prompt."""
    lines = []
    for i, inst in enumerate(instruments, 1):
        parts = [f'{i}. "{inst.name}"']

        # Add identifiers
        details = []
        if inst.cusip:
            details.append(f"CUSIP: {inst.cusip}")

        # Rate
        rate_match = re.search(r'(\d+\.?\d*)\s*%', inst.name or '')
        if rate_match:
            details.append(f"rate: {rate_match.group(1)}%")
        elif inst.interest_rate:
            details.append(f"rate: {inst.interest_rate / 100:.2f}%")

        # Maturity
        year_match = re.search(r'20(\d{2})', inst.name or '')
        if year_match:
            details.append(f"maturity: 20{year_match.group(1)}")
        elif inst.maturity_date:
            details.append(f"maturity: {inst.maturity_date.year}")

        # Type
        if inst.instrument_type:
            details.append(f"type: {inst.instrument_type}")

        if details:
            parts.append(f"({', '.join(details)})")

        lines.append(' '.join(parts))

    return '\n'.join(lines)


def prepare_content(doc: 'DocumentSection') -> str:
    """Prepare document content for the prompt, with smart truncation."""
    content = doc.content

    # For large docs, try to find the debt-relevant portion
    if len(content) > 30_000:
        debt_start = None
        for pattern in [
            r'(?i)DEBT\s+AND\s+DERIVATIVE',
            r'(?i)LONG[\-\s]TERM\s+DEBT\b(?!.*?balance\s+sheet)',
            r'(?i)NOTES?\s+PAYABLE\s+AND\s+LONG',
            r'(?i)\d+\.\s+DEBT\b',
            r'(?i)\d+\.\s+LONG[\-\s]TERM\s+(DEBT|OBLIGATIONS)',
            r'(?i)\d+\.\s+BORROWINGS',
            r'(?i)Senior\s+Notes.*?due\s+20\d{2}',
        ]:
            m = re.search(pattern, content)
            if m:
                debt_start = max(0, m.start() - 200)
                break

        if debt_start and debt_start > 5000:
            content = content[debt_start:]

    if len(content) > MAX_CONTENT_CHARS:
        content = content[:MAX_CONTENT_CHARS] + "\n... [truncated]"

    return content


async def extract_amounts_from_doc(doc, instruments, company_name,
                                    model_name='gemini-2.0-flash') -> list:
    """Send targeted prompt to Gemini, return list of matched amounts.

    Returns list of dicts: [{instrument_index, outstanding_cents, confidence, notes}, ...]
    """
    instrument_list = format_instrument_list(instruments)
    content = prepare_content(doc)

    prompt = TARGETED_EXTRACTION_PROMPT.format(
        company_name=company_name,
        instrument_list=instrument_list,
        filing_type=doc.doc_type,
        filing_date=str(doc.filing_date),
        content=content,
    )

    result = await get_gemini_response(prompt, model_name=model_name)
    amounts = result.get('amounts', [])

    # Filter out invalid entries — Gemini may use different key names
    valid = []
    for entry in amounts:
        amt = entry.get('outstanding_cents') or entry.get('outstanding_amount_cents')
        if amt and isinstance(amt, (int, float)) and amt > 0:
            # Normalize to 'outstanding_cents' key
            entry['outstanding_cents'] = int(amt)
            valid.append(entry)
    return valid


class _Proxy:
    """Lightweight proxy for detached ORM objects used outside session scope."""
    pass


async def process_company(async_session, company_id, company_ticker,
                           company_name, dry_run=False,
                           model_name='gemini-2.0-flash') -> dict:
    """Process one company: try documents in priority order until all instruments filled.

    Uses a fresh session per Gemini call to avoid connection timeouts.
    """
    stats = {
        'ticker': company_ticker,
        'status': 'ok',
        'instruments_needing': 0,
        'docs_available': 0,
        'docs_tried': 0,
        'updated': 0,
        'still_missing': 0,
        'details': [],
    }

    # Get instruments and docs with one session
    async with async_session() as session:
        instruments = await get_instruments_needing_amounts(session, company_id)
        docs = await get_documents_for_company(session, company_id)

        # Detach objects from session for use outside
        # We need the IDs and data but will re-query for updates
        instrument_data = []
        for inst in instruments:
            instrument_data.append({
                'id': inst.id,
                'name': inst.name,
                'cusip': inst.cusip,
                'interest_rate': inst.interest_rate,
                'maturity_date': inst.maturity_date,
                'instrument_type': inst.instrument_type,
                'attributes': dict(inst.attributes) if inst.attributes else {},
            })
        doc_data = []
        for doc in docs:
            doc_data.append({
                'id': doc.id,
                'doc_type': doc.doc_type,
                'section_type': doc.section_type,
                'filing_date': doc.filing_date,
                'content': doc.content,
                'content_length': doc.content_length,
            })

    stats['instruments_needing'] = len(instrument_data)
    stats['docs_available'] = len(doc_data)

    if not instrument_data:
        stats['status'] = 'none_missing'
        return stats

    if not doc_data:
        stats['status'] = 'no_documents'
        return stats

    # Track which instruments still need amounts (by index into instrument_data)
    remaining_indices = set(range(len(instrument_data)))
    matched_amounts = {}  # instrument_data index -> {amount, doc_info}

    # Early exit: if ALL remaining instruments are revolvers/term loans,
    # they likely have $0 drawn — try at most 2 docs then stop
    REVOLVER_TYPES = {'revolver', 'revolving_credit_facility', 'term_loan', 'term_loan_a',
                      'term_loan_b', 'abl'}
    all_revolvers = all(
        instrument_data[i].get('instrument_type', '') in REVOLVER_TYPES
        for i in remaining_indices
    )
    max_consecutive_misses = 2 if all_revolvers else 3  # Less patience for all-revolvers

    # Track consecutive misses per priority level to skip ahead
    consecutive_misses = 0
    last_priority_key = None

    # Try documents in priority order
    for doc_info in doc_data:
        if not remaining_indices:
            break  # All instruments filled

        # Check if we've moved to a new priority level
        priority_key = (doc_info['doc_type'], doc_info['section_type'])
        if priority_key != last_priority_key:
            consecutive_misses = 0
            last_priority_key = priority_key

        # Skip remaining docs at this priority level after consecutive misses
        if consecutive_misses >= max_consecutive_misses:
            continue

        stats['docs_tried'] += 1

        # Build the instruments list for this call (only those still needing amounts)
        # We need to map between the "remaining" list and the full list
        remaining_instruments_for_prompt = []
        remaining_to_full_idx = {}
        for prompt_idx, full_idx in enumerate(sorted(remaining_indices)):
            d = instrument_data[full_idx]
            proxy = _Proxy()
            proxy.name = d['name']
            proxy.cusip = d['cusip']
            proxy.interest_rate = d['interest_rate']
            proxy.maturity_date = d['maturity_date']
            proxy.instrument_type = d['instrument_type']
            proxy.id = d['id']
            remaining_instruments_for_prompt.append(proxy)
            remaining_to_full_idx[prompt_idx] = full_idx

        doc_proxy = _Proxy()
        doc_proxy.doc_type = doc_info['doc_type']
        doc_proxy.section_type = doc_info['section_type']
        doc_proxy.filing_date = doc_info['filing_date']
        doc_proxy.content = doc_info['content']
        doc_proxy.content_length = doc_info['content_length']

        # Call Gemini
        extracted = await extract_amounts_from_doc(
            doc_proxy, remaining_instruments_for_prompt,
            company_name, model_name
        )

        doc_label = f"{doc_info['doc_type']} {doc_info['section_type']} ({doc_info['filing_date']})"

        if not extracted:
            consecutive_misses += 1
            stats['details'].append(f"  {doc_label}: 0 amounts found")
            continue

        # Process results
        found_in_doc = 0
        for entry in extracted:
            idx_1based = entry.get('instrument_index')
            if idx_1based is None:
                continue
            try:
                prompt_idx = int(idx_1based) - 1  # Convert to 0-based prompt index
            except (ValueError, TypeError):
                continue

            if prompt_idx not in remaining_to_full_idx:
                continue

            full_idx = remaining_to_full_idx[prompt_idx]
            if full_idx not in remaining_indices:
                continue

            amt = entry.get('outstanding_cents') or entry.get('outstanding_amount_cents')
            if not amt or amt <= 0:
                continue

            matched_amounts[full_idx] = {
                'amount': int(amt),
                'doc_type': doc_info['doc_type'],
                'section_type': doc_info['section_type'],
                'filing_date': str(doc_info['filing_date']),
                'confidence': entry.get('confidence', 'medium'),
                'notes': entry.get('notes', ''),
            }
            remaining_indices.discard(full_idx)
            found_in_doc += 1

        if found_in_doc > 0:
            consecutive_misses = 0  # Reset on success
        else:
            consecutive_misses += 1

        stats['details'].append(f"  {doc_label}: {found_in_doc} amounts found")

        # Brief delay between Gemini calls
        if remaining_indices:
            await asyncio.sleep(1)

    # Now update the database
    if matched_amounts and not dry_run:
        async with async_session() as session:
            for full_idx, match_info in matched_amounts.items():
                inst_id = instrument_data[full_idx]['id']
                result = await session.execute(
                    select(DebtInstrument).where(DebtInstrument.id == inst_id)
                )
                db_inst = result.scalar_one_or_none()
                if db_inst:
                    db_inst.outstanding = match_info['amount']
                    if not db_inst.principal or db_inst.principal <= 0:
                        db_inst.principal = match_info['amount']

                    # Tag source info in attributes
                    attrs = dict(db_inst.attributes) if db_inst.attributes else {}
                    attrs.update({
                        'amount_source': 'doc_backfill',
                        'amount_doc_type': match_info['doc_type'],
                        'amount_section_type': match_info['section_type'],
                        'amount_doc_date': match_info['filing_date'],
                        'amount_confidence': match_info['confidence'],
                        'amount_updated_at': datetime.now().strftime('%Y-%m-%d'),
                    })
                    db_inst.attributes = attrs

            await session.commit()

    stats['updated'] = len(matched_amounts)
    stats['still_missing'] = len(remaining_indices)

    if dry_run and matched_amounts:
        stats['details'].append(f"  DRY RUN: would update {len(matched_amounts)} instruments:")
        for full_idx, match_info in matched_amounts.items():
            name = instrument_data[full_idx]['name'] or instrument_data[full_idx].get('instrument_type', '(unnamed)')
            amt_dollars = match_info['amount'] / 100
            if amt_dollars >= 1_000_000_000:
                amt_str = f"${amt_dollars / 1_000_000_000:.1f}B"
            elif amt_dollars >= 1_000_000:
                amt_str = f"${amt_dollars / 1_000_000:.1f}M"
            else:
                amt_str = f"${amt_dollars:,.0f}"
            stats['details'].append(
                f"    {name}: {amt_str} (from {match_info['doc_type']} {match_info['filing_date']})"
            )

    return stats


# =============================================================================
# ANALYZE MODE
# =============================================================================

async def analyze(async_session):
    """Show what instruments need amounts and what docs are available."""
    async with async_session() as session:
        # Get MISSING_ALL companies
        result = await session.execute(text("""
            WITH instrument_stats AS (
                SELECT
                    c.id as company_id,
                    c.ticker,
                    c.name,
                    COUNT(di.id) as total_instruments,
                    SUM(CASE WHEN di.outstanding IS NULL OR di.outstanding = 0 THEN 1 ELSE 0 END) as missing_count,
                    COALESCE(SUM(di.outstanding), 0) as instruments_sum
                FROM companies c
                JOIN debt_instruments di ON di.company_id = c.id AND di.is_active = true
                GROUP BY c.id, c.ticker, c.name
            ),
            doc_stats AS (
                SELECT
                    ds.company_id,
                    COUNT(*) FILTER (WHERE ds.section_type = 'debt_footnote') as debt_footnotes,
                    COUNT(*) FILTER (WHERE ds.section_type = 'mda_liquidity') as mda_sections,
                    COUNT(*) FILTER (WHERE ds.section_type = 'desc_securities') as desc_securities,
                    MAX(ds.filing_date) FILTER (WHERE ds.section_type = 'debt_footnote') as latest_footnote,
                    MAX(ds.content_length) FILTER (WHERE ds.section_type = 'debt_footnote') as max_footnote_size
                FROM document_sections ds
                WHERE ds.section_type IN ('debt_footnote', 'mda_liquidity', 'desc_securities')
                GROUP BY ds.company_id
            ),
            financials AS (
                SELECT DISTINCT ON (company_id)
                    company_id,
                    total_debt
                FROM company_financials
                WHERE total_debt IS NOT NULL AND total_debt > 0
                ORDER BY company_id, fiscal_year DESC, fiscal_quarter DESC
            )
            SELECT
                ist.ticker,
                ist.name,
                ist.total_instruments,
                ist.missing_count,
                ist.instruments_sum,
                COALESCE(f.total_debt, 0) as total_debt,
                COALESCE(ds.debt_footnotes, 0) as debt_footnotes,
                COALESCE(ds.mda_sections, 0) as mda_sections,
                COALESCE(ds.desc_securities, 0) as desc_securities,
                ds.latest_footnote,
                ds.max_footnote_size,
                CASE
                    WHEN ist.instruments_sum = 0 THEN 'MISSING_ALL'
                    WHEN ist.instruments_sum < COALESCE(f.total_debt, 0) * 0.5 THEN 'MISSING_SIGNIFICANT'
                    ELSE 'OTHER'
                END as status
            FROM instrument_stats ist
            LEFT JOIN doc_stats ds ON ds.company_id = ist.company_id
            LEFT JOIN financials f ON f.company_id = ist.company_id
            WHERE ist.missing_count > 0
            ORDER BY
                CASE WHEN ist.instruments_sum = 0 THEN 0 ELSE 1 END,
                ist.missing_count DESC
        """))

        rows = result.fetchall()

    print("=" * 130)
    print("PHASE 6: BACKFILL AMOUNTS FROM DOCUMENT SECTIONS")
    print("=" * 130)
    print()
    print(f"{'Ticker':8s} {'Status':20s} {'Missing':>8s} {'Total':>6s} {'Debt':>12s} "
          f"{'Footnotes':>10s} {'MDA':>5s} {'Desc':>5s} {'Latest FN':>12s} {'FN Size':>8s}")
    print("-" * 130)

    missing_all_count = 0
    missing_sig_count = 0
    total_instruments_fixable = 0
    total_with_docs = 0

    for row in rows:
        (ticker, name, total_inst, missing, inst_sum, total_debt,
         footnotes, mda, desc_sec, latest_fn, max_fn_size, status) = row

        if status == 'MISSING_ALL':
            missing_all_count += 1
        elif status == 'MISSING_SIGNIFICANT':
            missing_sig_count += 1

        has_docs = footnotes > 0 or mda > 0 or desc_sec > 0
        if has_docs:
            total_with_docs += 1
            total_instruments_fixable += missing

        # Format debt
        if total_debt > 0:
            debt_dollars = total_debt / 100
            if debt_dollars >= 1_000_000_000:
                debt_str = f"${debt_dollars / 1_000_000_000:.0f}B"
            elif debt_dollars >= 1_000_000:
                debt_str = f"${debt_dollars / 1_000_000:.0f}M"
            else:
                debt_str = f"${debt_dollars:,.0f}"
        else:
            debt_str = "n/a"

        fn_size_str = f"{max_fn_size / 1000:.0f}K" if max_fn_size else "-"

        print(f"  {ticker:6s} {status:20s} {missing:8d} {total_inst:6d} {debt_str:>12s} "
              f"{footnotes:10d} {mda:5d} {desc_sec:5d} {str(latest_fn or '-'):>12s} {fn_size_str:>8s}")

    print("-" * 130)
    print(f"  MISSING_ALL: {missing_all_count} companies")
    print(f"  MISSING_SIGNIFICANT: {missing_sig_count} companies")
    print(f"  {total_with_docs} companies have usable documents ({total_instruments_fixable} instruments fixable)")
    est_cost = total_with_docs * 0.005
    print(f"  Estimated Gemini cost: ~${est_cost:.2f} (Flash)")


# =============================================================================
# FIX MODE
# =============================================================================

async def fix_all(async_session, ticker=None, dry_run=False,
                  model_name='gemini-2.0-flash', all_missing=False):
    """Fix outstanding amounts for target companies."""

    # Get list of companies to process
    async with async_session() as session:
        if ticker:
            result = await session.execute(
                select(Company).where(Company.ticker == ticker.upper())
            )
            company = result.scalar_one_or_none()
            if not company:
                print(f"Company not found: {ticker}")
                return
            companies_data = [(company.id, company.ticker, company.name)]
        else:
            # Get MISSING_ALL (and optionally MISSING_SIGNIFICANT) companies
            status_filter = """
                HAVING COALESCE(SUM(di.outstanding), 0) = 0
            """ if not all_missing else """
                HAVING SUM(CASE WHEN di.outstanding IS NULL OR di.outstanding = 0 THEN 1 ELSE 0 END) > 0
            """

            result = await session.execute(text(f"""
                SELECT c.id, c.ticker, c.name
                FROM companies c
                JOIN debt_instruments di ON di.company_id = c.id AND di.is_active = true
                WHERE EXISTS (
                    SELECT 1 FROM document_sections ds
                    WHERE ds.company_id = c.id
                    AND ds.section_type IN ('debt_footnote', 'mda_liquidity', 'desc_securities')
                )
                GROUP BY c.id, c.ticker, c.name
                {status_filter}
                ORDER BY c.ticker
            """))
            companies_data = [(row[0], row[1], row[2]) for row in result.fetchall()]

    mode = 'DRY RUN' if dry_run else 'LIVE'
    scope = 'ALL MISSING' if all_missing else 'MISSING_ALL only'
    print("=" * 100)
    print(f"PHASE 6: BACKFILL AMOUNTS FROM DOCS ({mode})")
    print("=" * 100)
    print(f"Processing {len(companies_data)} companies ({scope}, model: {model_name})")
    print()

    total_updated = 0
    total_instruments = 0
    companies_fixed = 0

    for i, (cid, cticker, cname) in enumerate(companies_data):
        print(f"[{i + 1}/{len(companies_data)}] {cticker}: ", end='', flush=True)

        try:
            result = await process_company(
                async_session, cid, cticker, cname,
                dry_run=dry_run, model_name=model_name
            )

            total_instruments += result['instruments_needing']

            if result['status'] == 'none_missing':
                print("no missing instruments")
            elif result['status'] == 'no_documents':
                print(f"no documents ({result['instruments_needing']} instruments need amounts)")
            elif result['updated'] > 0:
                companies_fixed += 1
                total_updated += result['updated']
                print(f"updated {result['updated']}/{result['instruments_needing']} "
                      f"({result['docs_tried']} docs tried, "
                      f"{result['still_missing']} still missing)")
            else:
                print(f"0 matched from {result['docs_tried']} docs "
                      f"({result['instruments_needing']} instruments)")

            # Print details
            for detail in result.get('details', []):
                print(detail)

        except Exception as e:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()

        # Delay between companies
        if i < len(companies_data) - 1:
            await asyncio.sleep(1)

    print()
    print("=" * 100)
    prefix = "WOULD UPDATE" if dry_run else "UPDATED"
    print(f"SUMMARY: {prefix} {total_updated} instruments across {companies_fixed} companies")
    print(f"         {total_instruments} total instruments needed amounts")
    print("=" * 100)


# =============================================================================
# MAIN
# =============================================================================

async def main():
    parser = argparse.ArgumentParser(
        description='Backfill outstanding amounts from document_sections table'
    )
    parser.add_argument('--analyze', action='store_true',
                        help='Show what instruments need amounts and what docs are available')
    parser.add_argument('--fix', action='store_true',
                        help='Extract and update outstanding amounts')
    parser.add_argument('--ticker', type=str,
                        help='Single company ticker')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be updated without committing')
    parser.add_argument('--all-missing', action='store_true',
                        help='Include MISSING_SIGNIFICANT companies (default: MISSING_ALL only)')
    parser.add_argument('--model', type=str, default='gemini-2.0-flash',
                        help='Gemini model name (default: gemini-2.0-flash)')

    args = parser.parse_args()

    if not args.analyze and not args.fix:
        parser.print_help()
        return

    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        print('Error: DATABASE_URL required')
        sys.exit(1)

    if not os.getenv('GEMINI_API_KEY') and args.fix:
        print('Error: GEMINI_API_KEY required for --fix')
        sys.exit(1)

    engine = create_async_engine(database_url, echo=False, pool_pre_ping=True)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    try:
        if args.analyze:
            await analyze(async_session)
        elif args.fix:
            await fix_all(
                async_session,
                ticker=args.ticker,
                dry_run=args.dry_run,
                model_name=args.model,
                all_missing=args.all_missing,
            )
    finally:
        await engine.dispose()


if __name__ == '__main__':
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
