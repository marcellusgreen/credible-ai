#!/usr/bin/env python3
"""
Fix missing outstanding amounts for debt instruments.

Two-phase approach:
1. Phase 1: Populate from cached extraction files (field name mapping issues)
2. Phase 2: For remaining gaps, use instrument name to find amounts in SEC filings

Usage:
    # Analyze only
    python scripts/fix_outstanding_amounts.py --analyze

    # Fix from cache (safe - uses existing extraction data)
    python scripts/fix_outstanding_amounts.py --fix-from-cache

    # Fix single company
    python scripts/fix_outstanding_amounts.py --fix-from-cache --ticker AAPL

    # Dry run
    python scripts/fix_outstanding_amounts.py --fix-from-cache --dry-run
"""
import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models import Company, DebtInstrument
from app.services.extraction import refresh_company_cache


def extract_outstanding_from_cache(instruments_data: list[dict]) -> dict[str, int]:
    """Extract outstanding amounts from cached extraction data.

    Handles all known field name variants from LLM extractions.
    Returns dict of instrument_name -> outstanding_cents.
    """
    results = {}

    for inst in instruments_data:
        # Get instrument name (multiple possible field names)
        name = (
            inst.get('name')
            or inst.get('instrument_name')
            or inst.get('instrument_id')
            or ''
        )
        if not name:
            continue

        # Extract outstanding amount from all known field name variants
        outstanding = None

        # Standard field names (181 companies)
        if inst.get('outstanding') is not None:
            outstanding = inst['outstanding']
        elif inst.get('principal') is not None:
            outstanding = inst['principal']

        # Variant: outstanding_amount / principal_amount (AAL, ADSK, CSGP, DIS, ET, KLAC, MGM, MRVL, ON, ROP, TEAM, VRTX)
        if outstanding is None and inst.get('outstanding_amount') is not None:
            outstanding = inst['outstanding_amount']
        if outstanding is None and inst.get('principal_amount') is not None:
            outstanding = inst['principal_amount']

        # Variant: outstanding_amount_cents / face_value_cents (CB, CNK, GEV, GEHC, NOW)
        if outstanding is None and inst.get('outstanding_amount_cents') is not None:
            outstanding = inst['outstanding_amount_cents']
        if outstanding is None and inst.get('face_value_cents') is not None:
            outstanding = inst['face_value_cents']
        if outstanding is None and inst.get('original_amount_cents') is not None:
            outstanding = inst['original_amount_cents']

        # Variant: outstanding_principal / outstanding_principal_amount_cents (AMC, FTNT)
        if outstanding is None and inst.get('outstanding_principal') is not None:
            outstanding = inst['outstanding_principal']
        if outstanding is None and inst.get('outstanding_principal_amount_cents') is not None:
            outstanding = inst['outstanding_principal_amount_cents']
        if outstanding is None and inst.get('principal_amount_cents') is not None:
            outstanding = inst['principal_amount_cents']

        # Variant: principal_amount_outstanding / principal_amount_initial (KDP)
        if outstanding is None and inst.get('principal_amount_outstanding') is not None:
            outstanding = inst['principal_amount_outstanding']
        if outstanding is None and inst.get('principal_amount_initial') is not None:
            outstanding = inst['principal_amount_initial']

        # Variant: face_amount (ET)
        if outstanding is None and inst.get('face_amount') is not None:
            outstanding = inst['face_amount']

        # Variant: drawn_amount / commitment_amount (ROP revolvers)
        if outstanding is None and inst.get('drawn_amount') is not None:
            outstanding = inst['drawn_amount']

        # Skip if still no amount, or zero/negative
        if outstanding is None or outstanding <= 0:
            continue

        results[name] = int(outstanding)

    return results


def normalize_for_matching(name: str) -> str:
    """Normalize instrument name for fuzzy matching."""
    # Lowercase, remove extra whitespace, strip punctuation
    s = name.lower().strip()
    s = re.sub(r'[%\-–—,()]', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    # Normalize common terms
    s = s.replace('senior unsecured ', '').replace('senior secured ', '')
    s = s.replace(' notes ', ' ').replace(' note ', ' ')
    s = s.replace(' due ', ' ')
    return s.strip()


def match_cache_to_db(cache_amounts: dict[str, int], db_instruments: list) -> list[tuple]:
    """Match cached extraction amounts to DB instruments.

    Returns list of (db_instrument, amount_cents) tuples.
    """
    matches = []

    # Build normalized lookup from cache
    cache_normalized = {}
    for name, amount in cache_amounts.items():
        norm = normalize_for_matching(name)
        cache_normalized[norm] = (name, amount)

    for db_inst in db_instruments:
        if db_inst.outstanding and db_inst.outstanding > 0:
            continue  # Already has amount

        db_norm = normalize_for_matching(db_inst.name or '')

        # Try exact normalized match
        if db_norm in cache_normalized:
            matches.append((db_inst, cache_normalized[db_norm][1]))
            continue

        # Try matching by rate + year
        db_rate = re.search(r'(\d+\.?\d*)\s*%', db_inst.name or '')
        db_year = re.search(r'20(\d{2})', db_inst.name or '')

        if db_rate and db_year:
            rate_str = db_rate.group(1)
            year_str = db_year.group(1)
            for cache_norm, (orig_name, amount) in cache_normalized.items():
                cache_rate = re.search(r'(\d+\.?\d*)\s*%', orig_name)
                cache_year = re.search(r'20(\d{2})', orig_name)
                if cache_rate and cache_year:
                    if cache_rate.group(1) == rate_str and cache_year.group(1) == year_str:
                        matches.append((db_inst, amount))
                        break

    return matches


async def analyze(session):
    """Analyze outstanding amount gaps."""
    result = await session.execute(text("""
        WITH instrument_stats AS (
            SELECT
                c.ticker, c.name as company_name,
                COUNT(*) as total,
                SUM(CASE WHEN di.outstanding IS NULL THEN 1 ELSE 0 END) as null_out,
                SUM(CASE WHEN di.outstanding = 0 THEN 1 ELSE 0 END) as zero_out,
                SUM(CASE WHEN di.outstanding > 0 THEN 1 ELSE 0 END) as has_out
            FROM companies c
            JOIN debt_instruments di ON di.company_id = c.id AND di.is_active = true
            GROUP BY c.ticker, c.name
        )
        SELECT * FROM instrument_stats
        WHERE null_out > 0 OR zero_out > 0
        ORDER BY (null_out + zero_out) DESC
    """))

    total_missing = 0
    total_instruments = 0
    companies_with_gaps = 0

    print("=" * 100)
    print("OUTSTANDING AMOUNT GAP ANALYSIS")
    print("=" * 100)
    print(f"{'Ticker':8s} {'Total':>6s} {'Null':>6s} {'Zero':>6s} {'Has Amt':>8s} {'Cache?':>7s} {'Cache Amts':>10s}")
    print("-" * 100)

    for row in result.fetchall():
        ticker, company_name, total, null_out, zero_out, has_out = row
        missing = null_out + zero_out
        total_missing += missing
        total_instruments += total
        companies_with_gaps += 1

        # Check if cache file exists and has amounts
        cache_path = Path(f'results/{ticker.lower()}_iterative.json')
        cache_status = 'no'
        cache_amounts = 0
        if cache_path.exists():
            with open(cache_path) as f:
                data = json.load(f)
            amounts = extract_outstanding_from_cache(data.get('debt_instruments', []))
            cache_amounts = len(amounts)
            cache_status = 'yes' if cache_amounts > 0 else 'empty'

        print(f"  {ticker:6s} {total:6d} {null_out:6d} {zero_out:6d} {has_out:8d} {cache_status:>7s} {cache_amounts:10d}")

    print("-" * 100)
    print(f"  TOTAL: {total_instruments} instruments, {total_missing} missing amounts across {companies_with_gaps} companies")


async def fix_from_cache(session, ticker: str = None, dry_run: bool = False):
    """Fix outstanding amounts from cached extraction data."""

    # Get companies to fix
    if ticker:
        result = await session.execute(
            select(Company).where(Company.ticker == ticker.upper())
        )
        companies = [result.scalar_one()]
    else:
        result = await session.execute(select(Company).order_by(Company.ticker))
        companies = list(result.scalars().all())

    total_updated = 0
    total_skipped = 0
    companies_fixed = 0

    print("=" * 100)
    print(f"FIX OUTSTANDING AMOUNTS FROM CACHE {'(DRY RUN)' if dry_run else ''}")
    print("=" * 100)

    for company in companies:
        cache_path = Path(f'results/{company.ticker.lower()}_iterative.json')
        if not cache_path.exists():
            continue

        with open(cache_path) as f:
            data = json.load(f)

        cache_amounts = extract_outstanding_from_cache(data.get('debt_instruments', []))
        if not cache_amounts:
            continue

        # Get DB instruments needing amounts
        result = await session.execute(
            select(DebtInstrument).where(
                DebtInstrument.company_id == company.id,
                DebtInstrument.is_active == True,
            )
        )
        db_instruments = list(result.scalars().all())

        # Filter to those missing amounts
        missing = [di for di in db_instruments if not di.outstanding or di.outstanding <= 0]
        if not missing:
            continue

        # Match cache to DB
        matches = match_cache_to_db(cache_amounts, missing)

        if matches:
            companies_fixed += 1
            updated_count = 0
            for db_inst, amount_cents in matches:
                if not dry_run:
                    db_inst.outstanding = amount_cents
                    if not db_inst.principal or db_inst.principal <= 0:
                        db_inst.principal = amount_cents
                updated_count += 1

            if not dry_run:
                await session.commit()

            total_updated += updated_count
            print(f"  {company.ticker}: {updated_count}/{len(missing)} instruments updated from cache "
                  f"({len(missing) - updated_count} still missing)")
        else:
            total_skipped += len(missing)

    if not dry_run and companies_fixed > 0:
        print()
        print("Refreshing caches for updated companies...")
        # Re-query to refresh caches
        for company in companies:
            cache_path = Path(f'results/{company.ticker.lower()}_iterative.json')
            if not cache_path.exists():
                continue
            with open(cache_path) as f:
                data = json.load(f)
            cache_amounts = extract_outstanding_from_cache(data.get('debt_instruments', []))
            if cache_amounts:
                try:
                    await refresh_company_cache(session, company.id, company.ticker)
                except Exception as e:
                    print(f"  {company.ticker}: cache refresh failed: {e}")

    print()
    print(f"{'=' * 100}")
    print(f"SUMMARY: {total_updated} instruments updated across {companies_fixed} companies")
    print(f"         {total_skipped} instruments still missing amounts (no cache match)")


async def main():
    parser = argparse.ArgumentParser(description='Fix missing outstanding amounts')
    parser.add_argument('--analyze', action='store_true', help='Analyze gaps only')
    parser.add_argument('--fix-from-cache', action='store_true', help='Fix from cached extractions')
    parser.add_argument('--ticker', type=str, help='Single company ticker')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done')

    args = parser.parse_args()

    if not args.analyze and not args.fix_from_cache:
        parser.print_help()
        return

    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        print('Error: DATABASE_URL required')
        sys.exit(1)

    engine = create_async_engine(database_url, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        if args.analyze:
            await analyze(session)
        elif args.fix_from_cache:
            await fix_from_cache(session, ticker=args.ticker, dry_run=args.dry_run)

    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
