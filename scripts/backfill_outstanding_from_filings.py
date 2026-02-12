#!/usr/bin/env python3
"""
Phase 2: Backfill outstanding amounts from SEC filing debt footnotes.

For instruments with missing outstanding amounts, extracts the debt schedule
from the company's 10-K debt footnote and matches by coupon rate + maturity year.

Uses Gemini Flash for cheap extraction (~$0.002 per company).

Usage:
    # Analyze what can be fixed
    python scripts/backfill_outstanding_from_filings.py --analyze

    # Fix all companies
    python scripts/backfill_outstanding_from_filings.py --fix

    # Fix single company
    python scripts/backfill_outstanding_from_filings.py --fix --ticker AAPL

    # Dry run
    python scripts/backfill_outstanding_from_filings.py --fix --dry-run
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models import Company, DebtInstrument, DocumentSection
from app.services.utils import parse_json_robust


AMOUNT_EXTRACTION_PROMPT = """Extract ALL debt instruments with their outstanding amounts from this debt footnote/schedule.

Company: {company_name}

Debt Section:
{debt_content}

Return a JSON array of ALL debt instruments found. For EACH instrument, include:
{{
    "instruments": [
        {{
            "name": "Full name (e.g., '4.500% Senior Notes due 2032')",
            "outstanding_cents": <outstanding amount in CENTS - multiply dollars by 100>,
            "coupon_rate": <coupon rate as a number, e.g., 4.5 for 4.500%>,
            "maturity_year": <4-digit year, e.g., 2032>
        }}
    ]
}}

CRITICAL RULES:
- ALL amounts must be in CENTS (multiply dollar amounts by 100)
- $1 billion = 100,000,000,000 cents
- $500 million = 50,000,000,000 cents
- $1 million = 100,000,000 cents
- DETECT THE SCALE from the document (look for "in millions", "in thousands", etc.)
- If the table says amounts are "in millions", multiply by 100,000,000 (1M dollars = 100M cents)
- Include ALL instruments: notes, term loans, revolvers (drawn amount), debentures, etc.
- For revolvers/credit facilities, use the DRAWN amount (not total commitment)
- Skip instruments that are fully repaid ($0 outstanding)
- Include the coupon rate as a decimal number (4.500% = 4.5)
- Include the maturity year as a 4-digit number
"""


async def get_gemini_response(prompt: str, max_retries: int = 3, model_name: str = 'gemini-2.0-flash') -> dict:
    """Call Gemini and parse JSON response using the new google.genai SDK."""
    from google import genai

    client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))

    # Pro models use "thinking" tokens, need higher output limit
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
            if result and isinstance(result, dict) and 'instruments' in result:
                return result
            # Try if it's a list directly
            if result and isinstance(result, list):
                return {'instruments': result}
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
    return {'instruments': []}


def match_instrument(extracted: dict, db_instruments: list) -> tuple:
    """Match an extracted instrument to a DB instrument by rate + maturity year.

    Returns (db_instrument, confidence) or (None, 0).
    """
    ext_rate = extracted.get('coupon_rate')
    ext_year = extracted.get('maturity_year')
    ext_name = (extracted.get('name') or '').lower()

    if not ext_rate and not ext_year:
        return None, 0

    best_match = None
    best_score = 0

    for db_inst in db_instruments:
        rate_matched = False
        year_matched = False
        score = 0
        db_name = (db_inst.name or '').lower()

        # Match by coupon rate (from name or interest_rate field)
        if ext_rate is not None:
            try:
                ext_rate_f = float(ext_rate)
            except (ValueError, TypeError):
                ext_rate_f = None

            if ext_rate_f is not None:
                # Check name for rate (tolerance: 0.15% to handle rounding)
                db_rate_match = re.search(r'(\d+\.?\d*)\s*%', db_name)
                if db_rate_match:
                    db_rate = float(db_rate_match.group(1))
                    if abs(db_rate - ext_rate_f) < 0.15:
                        rate_matched = True
                        score += 0.5

                # Check interest_rate field (in bps, tolerance: 15 bps)
                if not rate_matched and db_inst.interest_rate:
                    db_rate_pct = db_inst.interest_rate / 100.0
                    if abs(db_rate_pct - ext_rate_f) < 0.15:
                        rate_matched = True
                        score += 0.5

        # Match by maturity year (from name or maturity_date field)
        if ext_year is not None:
            try:
                ext_year_i = int(ext_year)
            except (ValueError, TypeError):
                ext_year_i = None

            if ext_year_i is not None:
                # Check name for year
                db_year_match = re.search(r'20(\d{2})', db_name)
                if db_year_match:
                    db_year = int('20' + db_year_match.group(1))
                    if db_year == ext_year_i:
                        year_matched = True
                        score += 0.5

                # Check maturity_date field
                if not year_matched and db_inst.maturity_date:
                    if db_inst.maturity_date.year == ext_year_i:
                        year_matched = True
                        score += 0.5

        # Bonus for instrument type match
        if ext_name:
            for term in ['term loan', 'revolver', 'revolving', 'credit facility',
                         'debenture', 'commercial paper', 'finance lease', 'mortgage']:
                if term in ext_name and term in db_name:
                    score += 0.3
                    break

        # Require at least rate+year match (score >= 1.0) for notes,
        # or type match for non-rate instruments (revolvers, term loans)
        if score > best_score and score >= 0.8:
            best_score = score
            best_match = db_inst

    return best_match, best_score


async def analyze(session):
    """Analyze which companies have debt footnotes and missing amounts."""
    result = await session.execute(text("""
        SELECT
            c.ticker,
            c.name,
            COUNT(di.id) as total_instruments,
            SUM(CASE WHEN di.outstanding IS NULL OR di.outstanding = 0 THEN 1 ELSE 0 END) as missing_amounts,
            (SELECT COUNT(*) FROM document_sections ds
             WHERE ds.company_id = c.id AND ds.section_type = 'debt_footnote') as debt_footnotes,
            (SELECT MAX(ds.filing_date) FROM document_sections ds
             WHERE ds.company_id = c.id AND ds.section_type = 'debt_footnote') as latest_footnote
        FROM companies c
        JOIN debt_instruments di ON di.company_id = c.id AND di.is_active = true
        GROUP BY c.id, c.ticker, c.name
        HAVING SUM(CASE WHEN di.outstanding IS NULL OR di.outstanding = 0 THEN 1 ELSE 0 END) > 0
        ORDER BY SUM(CASE WHEN di.outstanding IS NULL OR di.outstanding = 0 THEN 1 ELSE 0 END) DESC
    """))

    total_missing = 0
    total_with_footnotes = 0
    total_fixable = 0

    print("=" * 110)
    print("PHASE 2: BACKFILL FROM SEC FILING DEBT FOOTNOTES")
    print("=" * 110)
    print(f"{'Ticker':8s} {'Missing':>8s} {'Total':>6s} {'Footnotes':>10s} {'Latest':>12s} {'Fixable?':>9s}")
    print("-" * 110)

    for row in result.fetchall():
        ticker, name, total, missing, footnotes, latest = row
        total_missing += missing
        fixable = footnotes > 0 and missing > 0
        if fixable:
            total_with_footnotes += 1
            total_fixable += missing
        status = 'YES' if fixable else 'no footnote'
        print(f"  {ticker:6s} {missing:8d} {total:6d} {footnotes or 0:10d} {str(latest or 'none'):>12s} {status:>9s}")

    print("-" * 110)
    print(f"  TOTAL: {total_missing} instruments missing amounts")
    print(f"  {total_with_footnotes} companies have debt footnotes ({total_fixable} instruments fixable)")
    print(f"  Estimated Gemini cost: ~${total_with_footnotes * 0.002:.2f}")


async def fix_company(session, company, dry_run: bool = False, model_name: str = 'gemini-2.0-flash') -> dict:
    """Fix outstanding amounts for a single company from debt footnote."""

    # Get debt footnote
    result = await session.execute(
        select(DocumentSection)
        .where(
            DocumentSection.company_id == company.id,
            DocumentSection.section_type == 'debt_footnote'
        )
        .order_by(DocumentSection.filing_date.desc())
        .limit(1)
    )
    footnote = result.scalar_one_or_none()
    if not footnote:
        return {'status': 'no_footnote', 'updated': 0}

    # Get instruments missing amounts
    result = await session.execute(
        select(DebtInstrument).where(
            DebtInstrument.company_id == company.id,
            DebtInstrument.is_active == True,
        )
    )
    all_instruments = list(result.scalars().all())
    missing = [di for di in all_instruments if not di.outstanding or di.outstanding <= 0]

    if not missing:
        return {'status': 'none_missing', 'updated': 0}

    # Extract just the debt-relevant portion from the content
    content = footnote.content

    # Try to find the actual debt section within the stored content
    # Some debt_footnote entries contain entire filing, not just the footnote
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
            # Start a bit before the match for context
            debt_start = max(0, m.start() - 200)
            break

    if debt_start and debt_start > 5000:
        # The debt section is deep in the content, extract from there
        content = content[debt_start:]

    if len(content) > 50000:
        content = content[:50000] + "\n... [truncated]"

    # Call Gemini
    prompt = AMOUNT_EXTRACTION_PROMPT.format(
        company_name=company.name,
        debt_content=content
    )

    extracted = await get_gemini_response(prompt, model_name=model_name)
    instruments = extracted.get('instruments', [])

    if not instruments:
        return {'status': 'no_extraction', 'updated': 0}

    # Match and update
    updated = 0
    unmatched_extractions = []
    matched_ids = set()
    for ext_inst in instruments:
        amount = ext_inst.get('outstanding_cents') or ext_inst.get('outstanding_amount_cents')
        if not amount or amount <= 0:
            continue

        db_inst, confidence = match_instrument(ext_inst, missing)
        if db_inst and db_inst.id not in matched_ids:
            matched_ids.add(db_inst.id)
            if not dry_run:
                db_inst.outstanding = int(amount)
                if not db_inst.principal or db_inst.principal <= 0:
                    db_inst.principal = int(amount)
            updated += 1
        else:
            unmatched_extractions.append(ext_inst)

    if not dry_run and updated > 0:
        await session.commit()

    return {
        'status': 'ok',
        'extracted': len(instruments),
        'updated': updated,
        'still_missing': len(missing) - updated,
        'unmatched': unmatched_extractions[:5]  # Sample for debugging
    }


async def fix_all(async_session, ticker: str = None, dry_run: bool = False, model_name: str = 'gemini-2.0-flash'):
    """Fix outstanding amounts for all companies.

    Uses a fresh DB session per company to avoid connection timeouts
    during long Gemini API calls.
    """

    # Get list of companies to process
    async with async_session() as session:
        if ticker:
            result = await session.execute(
                select(Company).where(Company.ticker == ticker.upper())
            )
            companies_data = [(c.id, c.ticker, c.name) for c in [result.scalar_one()]]
        else:
            result = await session.execute(text("""
                SELECT c.id, c.ticker, c.name
                FROM companies c
                JOIN debt_instruments di ON di.company_id = c.id AND di.is_active = true
                WHERE EXISTS (
                    SELECT 1 FROM document_sections ds
                    WHERE ds.company_id = c.id AND ds.section_type = 'debt_footnote'
                )
                GROUP BY c.id, c.ticker, c.name
                HAVING SUM(CASE WHEN di.outstanding IS NULL OR di.outstanding = 0 THEN 1 ELSE 0 END) > 0
                ORDER BY SUM(CASE WHEN di.outstanding IS NULL OR di.outstanding = 0 THEN 1 ELSE 0 END) DESC
            """))
            companies_data = [(row[0], row[1], row[2]) for row in result.fetchall()]

    print("=" * 100)
    print(f"PHASE 2: BACKFILL OUTSTANDING FROM DEBT FOOTNOTES {'(DRY RUN)' if dry_run else ''}")
    print("=" * 100)
    print(f"Processing {len(companies_data)} companies (model: {model_name})...")
    print()

    total_updated = 0
    total_extracted = 0
    companies_fixed = 0

    for i, (cid, cticker, cname) in enumerate(companies_data):
        print(f"[{i+1}/{len(companies_data)}] {cticker}: ", end='', flush=True)
        try:
            # Fresh session per company to avoid connection timeout
            async with async_session() as session:
                result = await session.execute(
                    select(Company).where(Company.id == cid)
                )
                company = result.scalar_one()
                result = await fix_company(session, company, dry_run=dry_run, model_name=model_name)
                if result['status'] == 'ok' and result['updated'] > 0:
                    companies_fixed += 1
                    total_updated += result['updated']
                    total_extracted += result['extracted']
                    print(f"extracted {result['extracted']}, matched {result['updated']}, "
                          f"{result['still_missing']} still missing")
                elif result['status'] == 'ok':
                    unmatched = result.get('unmatched', [])
                    sample = '; '.join(f"{u.get('name','?')}({u.get('coupon_rate','?')}%/{u.get('maturity_year','?')})" for u in unmatched[:3])
                    print(f"extracted {result['extracted']} but 0 matched. Samples: {sample}")
                else:
                    print(f"{result['status']}")
        except Exception as e:
            print(f"ERROR: {e}")

        # Small delay to avoid Gemini rate limits
        if i < len(companies_data) - 1:
            await asyncio.sleep(1)

    print()
    print("=" * 100)
    print(f"SUMMARY: {total_updated} instruments updated across {companies_fixed} companies")
    print(f"         {total_extracted} total instruments extracted from footnotes")


async def main():
    parser = argparse.ArgumentParser(description='Backfill outstanding amounts from SEC filings')
    parser.add_argument('--analyze', action='store_true', help='Analyze gaps')
    parser.add_argument('--fix', action='store_true', help='Fix from debt footnotes')
    parser.add_argument('--ticker', type=str, help='Single company')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done')
    parser.add_argument('--pro', action='store_true', help='Use Gemini Pro instead of Flash')
    parser.add_argument('--model', type=str, default=None, help='Gemini model name (e.g. gemini-2.5-pro-preview-05-06)')

    args = parser.parse_args()

    if not args.analyze and not args.fix:
        parser.print_help()
        return

    # Determine model
    if args.model:
        model_name = args.model
    elif args.pro:
        model_name = 'gemini-2.5-pro'
    else:
        model_name = 'gemini-2.0-flash'

    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        print('Error: DATABASE_URL required')
        sys.exit(1)

    engine = create_async_engine(database_url, echo=False, pool_pre_ping=True)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    if args.analyze:
        async with async_session() as session:
            await analyze(session)
    elif args.fix:
        await fix_all(async_session, ticker=args.ticker, dry_run=args.dry_run, model_name=model_name)

    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
