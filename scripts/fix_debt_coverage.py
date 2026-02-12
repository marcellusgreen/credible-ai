#!/usr/bin/env python3
"""
Fix debt coverage for all companies.

This script:
1. Identifies companies with debt coverage issues (missing/excess)
2. Re-processes cached extractions to fix field name mapping issues
3. Links all debt instruments to documents
4. Refreshes company cache
5. Preserves TRACE pricing

Usage:
    # Analyze only (no changes)
    python scripts/fix_debt_coverage.py --analyze

    # Fix a single company
    python scripts/fix_debt_coverage.py --ticker AAL

    # Fix all companies with issues
    python scripts/fix_debt_coverage.py --all

    # Fix companies in a specific sector
    python scripts/fix_debt_coverage.py --sector Airline
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models import Company, DebtInstrument, CompanyFinancials, DebtInstrumentDocument, BondPricing
from app.services.extraction import refresh_company_cache


async def analyze_debt_coverage(session) -> dict:
    """Analyze debt coverage for all companies."""

    result = await session.execute(text('''
        WITH latest_financials AS (
            SELECT DISTINCT ON (company_id)
                company_id, total_debt, fiscal_year, fiscal_quarter
            FROM company_financials
            WHERE total_debt IS NOT NULL AND total_debt > 0
            ORDER BY company_id, fiscal_year DESC, fiscal_quarter DESC
        ),
        debt_stats AS (
            SELECT
                c.id,
                c.ticker,
                c.name,
                COALESCE(c.sector, 'Unknown') as sector,
                lf.total_debt as financials_total_debt,
                COUNT(di.id) as instrument_count,
                SUM(COALESCE(di.outstanding, 0)) as instruments_sum,
                SUM(CASE WHEN did.id IS NOT NULL THEN 1 ELSE 0 END) as linked_count,
                SUM(CASE WHEN bp.price_source = 'TRACE' THEN 1 ELSE 0 END) as trace_count
            FROM companies c
            LEFT JOIN latest_financials lf ON lf.company_id = c.id
            LEFT JOIN debt_instruments di ON di.company_id = c.id AND di.is_active = true
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            LEFT JOIN bond_pricing bp ON bp.debt_instrument_id = di.id
            GROUP BY c.id, c.ticker, c.name, c.sector, lf.total_debt
        )
        SELECT
            id, ticker, name, sector,
            instrument_count,
            instruments_sum,
            financials_total_debt,
            linked_count,
            trace_count,
            CASE
                WHEN financials_total_debt IS NULL OR financials_total_debt = 0 THEN 'NO_FINANCIALS'
                WHEN instruments_sum = 0 THEN 'MISSING_ALL'
                WHEN instruments_sum < financials_total_debt * 0.5 THEN 'MISSING_SIGNIFICANT'
                WHEN instruments_sum > financials_total_debt * 2 THEN 'EXCESS_SIGNIFICANT'
                WHEN instruments_sum < financials_total_debt * 0.8 THEN 'MISSING_SOME'
                WHEN instruments_sum > financials_total_debt * 1.2 THEN 'EXCESS_SOME'
                ELSE 'OK'
            END as status
        FROM debt_stats
        ORDER BY ticker
    '''))

    companies = []
    for row in result.fetchall():
        companies.append({
            'id': row[0],
            'ticker': row[1],
            'name': row[2],
            'sector': row[3],
            'instrument_count': row[4],
            'instruments_sum': row[5] or 0,
            'financials_total': row[6] or 0,
            'linked_count': row[7],
            'trace_count': row[8],
            'status': row[9],
            'unlinked': row[4] - row[7] if row[4] and row[7] else 0
        })

    return companies


async def cleanup_duplicates(session, ticker: str, company_id: UUID) -> dict:
    """Clean up duplicate instruments aggressively."""
    import re

    deactivated = 0

    # Step 1: Deactivate zero-value bond-type duplicates
    result = await session.execute(text('''
        UPDATE debt_instruments di
        SET is_active = false
        FROM companies c
        WHERE c.id = di.company_id
        AND c.id = :company_id
        AND di.is_active = true
        AND di.instrument_type = 'bond'
        AND (di.outstanding IS NULL OR di.outstanding = 0)
        RETURNING di.id
    '''), {'company_id': company_id})
    zero_bonds = len(result.fetchall())
    deactivated += zero_bonds

    # Step 2: Find instruments with same coupon/maturity pattern
    result = await session.execute(text('''
        SELECT di.id, di.name, di.outstanding, di.maturity_date, di.created_at
        FROM debt_instruments di
        WHERE di.company_id = :company_id AND di.is_active = true
        AND di.instrument_type IN ('senior_notes', 'senior_secured_notes', 'subordinated_notes', 'convertible_notes')
        ORDER BY di.name
    '''), {'company_id': company_id})

    notes = result.fetchall()
    groups = {}
    for note in notes:
        name = note[1] or ''
        coupon_match = re.search(r'(\d+\.?\d*)%', name)
        year_match = re.search(r'20(\d{2})', name)
        if coupon_match and year_match:
            key = (coupon_match.group(1), year_match.group(1))
            if key not in groups:
                groups[key] = []
            groups[key].append(note)

    # Deactivate duplicates (keep oldest)
    dup_ids = []
    for key, group_notes in groups.items():
        if len(group_notes) > 1:
            sorted_notes = sorted(group_notes, key=lambda x: x[4])  # by created_at
            for dup in sorted_notes[1:]:
                dup_ids.append(str(dup[0]))

    if dup_ids:
        result = await session.execute(text('''
            UPDATE debt_instruments
            SET is_active = false
            WHERE id = ANY(:ids)
            RETURNING id
        '''), {'ids': dup_ids})
        deactivated += len(result.fetchall())

    await session.commit()

    return {'status': 'cleaned', 'deactivated': deactivated, 'zero_bonds': zero_bonds, 'duplicates': len(dup_ids)}


async def fix_company_from_cache(session, ticker: str, company_id: UUID) -> dict:
    """Fix a company's debt instruments from cached extraction."""

    results_path = Path(f'results/{ticker.lower()}_iterative.json')
    if not results_path.exists():
        return {'status': 'no_cache', 'message': f'No cached extraction at {results_path}'}

    with open(results_path) as f:
        extraction = json.load(f)

    instruments = extraction.get('debt_instruments', [])
    if not instruments:
        return {'status': 'no_instruments', 'message': 'No instruments in cached extraction'}

    # Check for field name issues
    first = instruments[0]
    has_wrong_fields = 'outstanding_amount' in first or 'principal_amount' in first

    if not has_wrong_fields:
        return {'status': 'fields_ok', 'message': 'Field names already correct'}

    # Fix field names and update database
    updated = 0
    for inst_data in instruments:
        # Normalize field names
        name = inst_data.get('name') or inst_data.get('instrument_name')
        outstanding = inst_data.get('outstanding') or inst_data.get('outstanding_amount')
        principal = inst_data.get('principal') or inst_data.get('principal_amount')

        if not name:
            continue

        # Find matching instrument in database
        result = await session.execute(
            select(DebtInstrument).where(
                DebtInstrument.company_id == company_id,
                DebtInstrument.name == name
            )
        )
        db_instrument = result.scalar_one_or_none()

        if db_instrument:
            # Update if values are different and we have data
            if outstanding and (db_instrument.outstanding is None or db_instrument.outstanding == 0):
                db_instrument.outstanding = outstanding
                updated += 1
            if principal and (db_instrument.principal is None or db_instrument.principal == 0):
                db_instrument.principal = principal

    await session.commit()

    return {'status': 'updated', 'message': f'Updated {updated} instruments from cache'}


async def link_company_documents(session, ticker: str, company_id: UUID) -> dict:
    """Link all unlinked instruments to documents."""

    # Import linking functions
    from app.services.document_linking import link_documents_heuristic

    # Count unlinked before
    result = await session.execute(text('''
        SELECT COUNT(*) FROM debt_instruments di
        WHERE di.company_id = :id AND di.is_active = true
        AND NOT EXISTS (
            SELECT 1 FROM debt_instrument_documents did
            WHERE did.debt_instrument_id = di.id
        )
    '''), {'id': company_id})
    unlinked_before = result.scalar()

    if unlinked_before == 0:
        return {'status': 'all_linked', 'linked': 0}

    # Run heuristic linking
    try:
        linked = await link_documents_heuristic(session, company_id)
        return {'status': 'linked', 'linked': linked}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}


async def fix_company(session, ticker: str, company_id: UUID, dry_run: bool = False, status: str = None) -> dict:
    """Fix a single company's debt coverage."""

    results = {
        'ticker': ticker,
        'steps': []
    }

    if dry_run:
        cache_result = await fix_company_from_cache(session, ticker, company_id)
        results['steps'].append(('fix_from_cache', cache_result))
        return results

    # Step 1: Clean up duplicates (especially for EXCESS companies)
    if status in ('EXCESS_SOME', 'EXCESS_SIGNIFICANT'):
        cleanup_result = await cleanup_duplicates(session, ticker, company_id)
        results['steps'].append(('cleanup_duplicates', cleanup_result))

    # Step 2: Fix from cache if available
    cache_result = await fix_company_from_cache(session, ticker, company_id)
    results['steps'].append(('fix_from_cache', cache_result))

    # Step 3: Link documents
    link_result = await link_company_documents(session, ticker, company_id)
    results['steps'].append(('link_documents', link_result))

    # Step 4: Refresh cache
    try:
        await refresh_company_cache(session, company_id, ticker)
        results['steps'].append(('refresh_cache', {'status': 'ok'}))
    except Exception as e:
        results['steps'].append(('refresh_cache', {'status': 'error', 'message': str(e)}))

    return results


async def main():
    parser = argparse.ArgumentParser(description='Fix debt coverage for companies')
    parser.add_argument('--analyze', action='store_true', help='Analyze only, no changes')
    parser.add_argument('--ticker', type=str, help='Fix single company')
    parser.add_argument('--sector', type=str, help='Fix companies in sector')
    parser.add_argument('--all', action='store_true', help='Fix all companies with issues')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done')

    args = parser.parse_args()

    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        print('Error: DATABASE_URL required')
        sys.exit(1)

    engine = create_async_engine(database_url, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        # Analyze
        companies = await analyze_debt_coverage(session)

        if args.analyze:
            print('\nDEBT COVERAGE ANALYSIS')
            print('=' * 100)

            by_status = {}
            for c in companies:
                status = c['status']
                if status not in by_status:
                    by_status[status] = []
                by_status[status].append(c)

            for status, cos in sorted(by_status.items()):
                print(f'\n{status}: {len(cos)} companies')
                if status != 'OK':
                    for c in cos[:10]:
                        gap = ''
                        inst_sum = float(c['instruments_sum']) if c['instruments_sum'] else 0
                        fin_total = float(c['financials_total']) if c['financials_total'] else 0
                        if fin_total > 0:
                            gap_pct = (inst_sum - fin_total) * 100 / fin_total
                            gap = f' (gap: {gap_pct:+.1f}%)'
                        print(f'  {c["ticker"]}: {c["instrument_count"]} instruments, '
                              f'${inst_sum/1e11:.2f}B vs ${fin_total/1e11:.2f}B{gap}, '
                              f'{c["unlinked"]} unlinked')
                    if len(cos) > 10:
                        print(f'  ... and {len(cos) - 10} more')

            await engine.dispose()
            return

        # Filter companies to fix
        to_fix = []

        if args.ticker:
            to_fix = [c for c in companies if c['ticker'] == args.ticker.upper()]
        elif args.sector:
            to_fix = [c for c in companies if c['sector'] == args.sector and c['status'] != 'OK']
        elif args.all:
            to_fix = [c for c in companies if c['status'] not in ('OK', 'NO_FINANCIALS')]

        if not to_fix:
            print('No companies to fix')
            await engine.dispose()
            return

        print(f'\nFixing {len(to_fix)} companies...')
        print('=' * 80)

        for c in to_fix:
            print(f'\n{c["ticker"]}:')
            result = await fix_company(session, c['ticker'], c['id'], dry_run=args.dry_run, status=c['status'])
            for step_name, step_result in result['steps']:
                print(f'  {step_name}: {step_result}')

    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
