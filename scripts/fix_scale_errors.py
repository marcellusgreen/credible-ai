#!/usr/bin/env python3
"""
Fix scale errors in debt instrument amounts.

Compares instrument totals to financial totals and flags/fixes scale issues.
Scale errors typically show as 2x-10x mismatch (instruments too high or too low).

Usage:
    python scripts/fix_scale_errors.py --analyze
    python scripts/fix_scale_errors.py --ticker INTU --fix
    python scripts/fix_scale_errors.py --all --fix
"""

import argparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from script_utils import get_db_session, print_header, run_async


async def analyze_mismatches(db: AsyncSession) -> list[dict]:
    """Find companies with instrument/financial mismatches."""
    result = await db.execute(text('''
        WITH instrument_totals AS (
            SELECT company_id,
                   SUM(COALESCE(outstanding, 0)) as instrument_total,
                   COUNT(*) as instrument_count,
                   COUNT(*) FILTER (WHERE outstanding IS NOT NULL) as with_amounts
            FROM debt_instruments
            WHERE is_active = true
            GROUP BY company_id
        ),
        financial_totals AS (
            SELECT DISTINCT ON (company_id) company_id, total_debt, fiscal_year, fiscal_quarter
            FROM company_financials
            ORDER BY company_id, fiscal_year DESC, fiscal_quarter DESC
        )
        SELECT c.ticker, c.name,
               it.instrument_total,
               ft.total_debt,
               it.instrument_count,
               it.with_amounts,
               ft.fiscal_year, ft.fiscal_quarter,
               CASE
                   WHEN ft.total_debt > 0 THEN it.instrument_total::float / ft.total_debt
                   ELSE 0
               END as ratio
        FROM companies c
        JOIN instrument_totals it ON it.company_id = c.id
        JOIN financial_totals ft ON ft.company_id = c.id
        WHERE ft.total_debt > 0
        AND it.instrument_total > 0
        AND (
            it.instrument_total > ft.total_debt * 2
            OR it.instrument_total < ft.total_debt * 0.1
        )
        ORDER BY ABS(it.instrument_total::float / ft.total_debt - 1) DESC
    '''))

    mismatches = []
    for row in result.fetchall():
        ratio = float(row[8])
        # Determine likely issue
        if ratio > 1.5:
            if ratio > 5:
                issue = "SCALE_HIGH_10X"
                suggested_fix = "divide_1000"
            elif ratio > 1.8:
                issue = "SCALE_HIGH_2X"
                suggested_fix = "manual_review"
            else:
                issue = "SLIGHT_HIGH"
                suggested_fix = None
        elif ratio < 0.15:
            if row[5] < row[4] * 0.3:  # Less than 30% have amounts
                issue = "MISSING_AMOUNTS"
                suggested_fix = None
            else:
                issue = "SCALE_LOW"
                suggested_fix = "multiply_1000"
        else:
            issue = "UNKNOWN"
            suggested_fix = None

        mismatches.append({
            'ticker': row[0],
            'name': row[1],
            'instrument_total_cents': int(row[2]),
            'financial_total_cents': int(row[3]),
            'instrument_count': row[4],
            'with_amounts': row[5],
            'fiscal_year': row[6],
            'fiscal_quarter': row[7],
            'ratio': ratio,
            'issue': issue,
            'suggested_fix': suggested_fix
        })

    return mismatches


async def get_instrument_details(db: AsyncSession, ticker: str) -> list[dict]:
    """Get all instruments for a company with their amounts."""
    result = await db.execute(text('''
        SELECT d.id, d.name, d.outstanding, d.seniority, d.maturity_date
        FROM debt_instruments d
        JOIN companies c ON c.id = d.company_id
        WHERE c.ticker = :ticker AND d.is_active = true
        ORDER BY d.outstanding DESC NULLS LAST
    '''), {'ticker': ticker})

    instruments = []
    for row in result.fetchall():
        instruments.append({
            'id': str(row[0]),
            'name': row[1],
            'outstanding_cents': int(row[2]) if row[2] else None,
            'seniority': row[3],
            'maturity': row[4]
        })
    return instruments


async def fix_scale(db: AsyncSession, ticker: str, operation: str, execute: bool = False) -> dict:
    """Fix scale for a company's instruments."""
    instruments = await get_instrument_details(db, ticker)

    stats = {'updated': 0, 'skipped': 0}

    if operation == 'divide_1000':
        factor = 1000
        op_name = "dividing by 1000"
    elif operation == 'multiply_1000':
        factor = 1 / 1000
        op_name = "multiplying by 1000"
    else:
        print(f"  Unknown operation: {operation}")
        return stats

    print(f"  {op_name} for {len(instruments)} instruments:")

    for inst in instruments:
        if inst['outstanding_cents'] is None:
            stats['skipped'] += 1
            continue

        old_val = inst['outstanding_cents']
        if operation == 'divide_1000':
            new_val = old_val // 1000
        else:
            new_val = old_val * 1000

        old_b = old_val / 100 / 1e9
        new_b = new_val / 100 / 1e9

        print(f"    {inst['name'][:50]}: ${old_b:.2f}B -> ${new_b:.2f}B")

        if execute:
            await db.execute(text('''
                UPDATE debt_instruments
                SET outstanding = :new_val,
                    attributes = COALESCE(attributes, '{}'::jsonb) ||
                                 jsonb_build_object('scale_fix', :op, 'original_outstanding', :old_val)
                WHERE id = :id
            '''), {'new_val': new_val, 'op': operation, 'old_val': old_val, 'id': inst['id']})

        stats['updated'] += 1

    if execute:
        await db.commit()

    return stats


async def main():
    parser = argparse.ArgumentParser(description="Fix scale errors in debt instruments")
    parser.add_argument("--analyze", action="store_true", help="Analyze mismatches (default)")
    parser.add_argument("--ticker", type=str, help="Process single company")
    parser.add_argument("--all", action="store_true", help="Process all fixable companies")
    parser.add_argument("--fix", action="store_true", help="Apply fixes (otherwise dry run)")
    args = parser.parse_args()

    print_header("FIX SCALE ERRORS IN DEBT INSTRUMENTS")

    async with get_db_session() as db:
        mismatches = await analyze_mismatches(db)

        print(f"\nFound {len(mismatches)} companies with mismatches:\n")
        print(f"{'Ticker':<8} {'Instruments':>14} {'Financials':>14} {'Ratio':>8} {'Issue':<20} {'Fix'}")
        print("-" * 80)

        for m in mismatches:
            inst_b = m['instrument_total_cents'] / 100 / 1e9
            fin_b = m['financial_total_cents'] / 100 / 1e9
            fix = m['suggested_fix'] or '-'
            print(f"{m['ticker']:<8} ${inst_b:>12.2f}B ${fin_b:>12.2f}B {m['ratio']:>7.2f}x {m['issue']:<20} {fix}")

        if args.ticker:
            # Process single company
            match = next((m for m in mismatches if m['ticker'] == args.ticker.upper()), None)
            if not match:
                print(f"\n{args.ticker} not found in mismatch list")
                return

            if match['suggested_fix']:
                print(f"\n{'=' * 70}")
                print(f"Processing {args.ticker}")
                print(f"{'=' * 70}")
                stats = await fix_scale(db, args.ticker.upper(), match['suggested_fix'], execute=args.fix)
                print(f"\nUpdated: {stats['updated']}, Skipped: {stats['skipped']}")
                if not args.fix:
                    print("[DRY RUN] Use --fix to apply changes")
            else:
                print(f"\nNo automatic fix available for {args.ticker} ({match['issue']})")
                print("Manual review required - consider re-extracting financials or debt")

        elif args.all:
            # Process all fixable
            fixable = [m for m in mismatches if m['suggested_fix']]
            print(f"\n{'=' * 70}")
            print(f"Processing {len(fixable)} companies with automatic fixes")
            print(f"{'=' * 70}")

            total_stats = {'updated': 0, 'skipped': 0}
            for m in fixable:
                print(f"\n--- {m['ticker']} ({m['suggested_fix']}) ---")
                stats = await fix_scale(db, m['ticker'], m['suggested_fix'], execute=args.fix)
                total_stats['updated'] += stats['updated']
                total_stats['skipped'] += stats['skipped']

            print(f"\n{'=' * 70}")
            print(f"TOTAL: Updated {total_stats['updated']}, Skipped {total_stats['skipped']}")
            if not args.fix:
                print("[DRY RUN] Use --fix to apply changes")

        else:
            # Just analyze
            fixable = [m for m in mismatches if m['suggested_fix']]
            manual = [m for m in mismatches if not m['suggested_fix']]

            print(f"\n{'=' * 70}")
            print("SUMMARY")
            print(f"{'=' * 70}")
            print(f"Auto-fixable: {len(fixable)} companies")
            for m in fixable:
                print(f"  {m['ticker']}: {m['suggested_fix']}")

            print(f"\nManual review needed: {len(manual)} companies")
            for m in manual:
                print(f"  {m['ticker']}: {m['issue']} ({m['with_amounts']}/{m['instrument_count']} have amounts)")


if __name__ == "__main__":
    run_async(main())
