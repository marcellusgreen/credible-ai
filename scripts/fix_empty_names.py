#!/usr/bin/env python3
"""
Fix empty instrument names by generating descriptive names from available data.

Generates names like:
- "4.50% Senior Notes due 2029" (when rate and maturity known)
- "Senior Secured Term Loan due 2028" (when no rate but type/maturity known)
- "Commercial Paper" (for CP instruments)

Usage:
    python scripts/fix_empty_names.py           # Dry run
    python scripts/fix_empty_names.py --save    # Apply changes
"""

import argparse

from sqlalchemy import text

from script_utils import get_db_session, print_header, run_async


def generate_name(rate, rate_type, maturity, inst_type, seniority):
    """Generate a descriptive name from available instrument data."""
    parts = []

    # Add rate if available
    if rate is not None and rate > 0:
        rate_pct = rate / 100  # Convert from basis points representation
        rate_str = "{:.3f}".format(rate_pct).rstrip('0').rstrip('.')
        parts.append("{}%".format(rate_str))
    elif rate_type == 'floating':
        parts.append("Floating Rate")

    # Add instrument type (some types include seniority info)
    type_names = {
        'senior_notes': 'Senior Notes',
        'notes': 'Notes',
        'bond': 'Bond',
        'bonds': 'Bonds',
        'debentures': 'Debentures',
        'term_loan': 'Term Loan',
        'term_note': 'Term Note',
        'revolver': 'Revolver',
        'revolving_credit_facility': 'Revolver',
        'credit_facility': 'Credit Facility',
        'commercial_paper': 'Commercial Paper',
        'convertible_notes': 'Convertible Notes',
        'subordinated_notes': 'Subordinated Notes',
        'junior_subordinated_notes': 'Junior Subordinated Notes',
        'structured_notes': 'Structured Notes',
        'structured_liabilities': 'Structured Liabilities',
        'advances': 'Advances',
        'mtn_program': 'MTN',
        'medium_term_notes': 'Medium-Term Notes',
        'trust_preferred': 'Trust Preferred',
        'capital_supplementary_bonds': 'Capital Supplementary Bonds',
        'long_term_debt': 'Long-Term Debt',
        'senior_unsecured_notes': 'Senior Notes',
    }

    # Types that already include seniority info
    seniority_in_type = {
        'senior_notes', 'subordinated_notes', 'junior_subordinated_notes',
        'senior_unsecured_notes'
    }

    # Add seniority prefix only if not already in type
    if inst_type not in seniority_in_type:
        seniority_names = {
            'senior_secured': 'Senior Secured',
            'senior_unsecured': 'Senior',
            'subordinated': 'Subordinated',
            'junior_subordinated': 'Junior Subordinated',
        }
        if seniority and seniority in seniority_names:
            parts.append(seniority_names[seniority])

    # Add instrument type
    if inst_type and inst_type in type_names:
        parts.append(type_names[inst_type])
    elif inst_type:
        # Use the raw type name, cleaned up
        parts.append(inst_type.replace('_', ' ').title())

    # Add maturity if available
    if maturity:
        year = maturity.year
        parts.append("due {}".format(year))

    # Join parts
    if parts:
        return ' '.join(parts)

    return None  # Can't generate a meaningful name


async def main():
    parser = argparse.ArgumentParser(description="Fix empty instrument names")
    parser.add_argument("--save", action="store_true", help="Apply changes to database")
    args = parser.parse_args()

    print_header("FIX EMPTY INSTRUMENT NAMES")

    async with get_db_session() as session:
        conn = await session.connection()
        # Find all empty-name instruments
        result = await conn.execute(text('''
            SELECT di.id, c.ticker, di.name, di.maturity_date,
                   di.interest_rate, di.rate_type, di.instrument_type, di.seniority
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            WHERE di.name IS NULL OR di.name = ''
            ORDER BY c.ticker, di.maturity_date
        '''))
        rows = result.fetchall()

        if not rows:
            print("No empty-name instruments found")
            return

        print("Found {} instruments with empty names".format(len(rows)))
        print()

        updates = []
        failures = []

        for row in rows:
            di_id, ticker, name, maturity, rate, rate_type, inst_type, seniority = row

            new_name = generate_name(rate, rate_type, maturity, inst_type, seniority)

            if new_name:
                updates.append({
                    'id': di_id,
                    'ticker': ticker,
                    'old_name': name or '(empty)',
                    'new_name': new_name,
                    'maturity': maturity,
                })
            else:
                failures.append({
                    'id': di_id,
                    'ticker': ticker,
                    'rate': rate,
                    'type': inst_type,
                    'maturity': maturity,
                })

        print("Can generate names for {} instruments".format(len(updates)))
        print("Cannot generate names for {} instruments".format(len(failures)))
        print()

        # Show samples by ticker
        by_ticker = {}
        for u in updates:
            if u['ticker'] not in by_ticker:
                by_ticker[u['ticker']] = []
            by_ticker[u['ticker']].append(u)

        print("--- Sample name generations ---")
        shown = 0
        for ticker in sorted(by_ticker.keys()):
            items = by_ticker[ticker]
            print("{}:".format(ticker))
            for item in items[:3]:
                print('  "{}" (maturity: {})'.format(item['new_name'], item['maturity']))
            if len(items) > 3:
                print("  ... and {} more".format(len(items) - 3))
            shown += 1
            if shown >= 10:
                remaining = len(by_ticker) - shown
                if remaining > 0:
                    print("... and {} more tickers".format(remaining))
                break
        print()

        if failures:
            print("--- Cannot generate names for ---")
            for f in failures[:5]:
                print("  {}: type={}, rate={}, maturity={}".format(
                    f['ticker'], f['type'], f['rate'], f['maturity']))
            if len(failures) > 5:
                print("  ... and {} more".format(len(failures) - 5))
            print()

        if args.save:
            print("--- Applying changes ---")
            for u in updates:
                await conn.execute(text('''
                    UPDATE debt_instruments SET name = :name WHERE id = :id
                '''), {'id': u['id'], 'name': u['new_name']})
            print("Updated {} instrument names".format(len(updates)))
        else:
            print("[DRY RUN] Run with --save to apply changes")


if __name__ == "__main__":
    run_async(main())
