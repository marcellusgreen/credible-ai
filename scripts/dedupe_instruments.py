#!/usr/bin/env python3
"""
Deduplicate debt instruments.

Safely removes duplicate debt instruments while preserving unique data.

Strategy:
1. EXACT duplicates (same name, maturity, issuer, rate, amount) - delete extras, keep oldest
2. NEAR duplicates (same name, maturity, issuer, different nulls) - merge data, delete extras
3. DIFFERENT issuers - only merge known aliases (e.g., Apache Corp -> APA Corp)

Usage:
    python scripts/dedupe_instruments.py           # Dry run
    python scripts/dedupe_instruments.py --save    # Apply changes
"""

import argparse
import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

load_dotenv()

# Known issuer aliases (entity names that refer to the same legal entity)
ISSUER_ALIASES = {
    # APA Corporation was formerly Apache Corporation
    'Apache Corporation': 'APA Corporation',
}


def normalize_issuer(name):
    """Normalize issuer name using known aliases."""
    if name in ISSUER_ALIASES:
        return ISSUER_ALIASES[name]
    return name


async def main():
    parser = argparse.ArgumentParser(description="Deduplicate debt instruments")
    parser.add_argument("--save", action="store_true", help="Apply changes to database")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output")
    args = parser.parse_args()

    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    if 'postgresql://' in database_url and '+asyncpg' not in database_url:
        database_url = database_url.replace('postgresql://', 'postgresql+asyncpg://', 1)

    engine = create_async_engine(database_url)

    async with engine.begin() as conn:
        # Get all potential duplicates
        # Use COALESCE to handle NULL maturity dates (NULL = NULL is false in SQL)
        result = await conn.execute(text('''
            SELECT di.id, c.ticker, di.name, di.maturity_date,
                   e.name as issuer_name, di.issuer_id,
                   di.interest_rate, di.outstanding, di.principal, di.commitment,
                   di.cusip, di.isin, di.rate_type, di.seniority, di.security_type,
                   di.benchmark, di.spread_bps, di.floor_bps,
                   di.created_at
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            LEFT JOIN entities e ON e.id = di.issuer_id
            WHERE (c.ticker, COALESCE(di.name, ''), COALESCE(di.maturity_date, '1900-01-01'::date)) IN (
                SELECT c2.ticker, COALESCE(di2.name, ''), COALESCE(di2.maturity_date, '1900-01-01'::date)
                FROM debt_instruments di2
                JOIN companies c2 ON c2.id = di2.company_id
                GROUP BY c2.ticker, COALESCE(di2.name, ''), COALESCE(di2.maturity_date, '1900-01-01'::date)
                HAVING COUNT(*) > 1
            )
            ORDER BY c.ticker, di.name, di.maturity_date, di.created_at
        '''))
        rows = result.fetchall()

        if not rows:
            print("No duplicates found")
            return

        # Group by (ticker, name, maturity, normalized_issuer)
        groups = defaultdict(list)
        for row in rows:
            di_id, ticker, name, maturity, issuer_name, issuer_id, rate, outstanding, principal, commitment, \
                cusip, isin, rate_type, seniority, security_type, benchmark, spread_bps, floor_bps, created_at = row

            # Normalize issuer name
            norm_issuer = normalize_issuer(issuer_name) if issuer_name else None

            key = (ticker, name or '', str(maturity), norm_issuer)
            groups[key].append({
                'id': di_id,
                'issuer_id': issuer_id,
                'issuer_name': issuer_name,
                'rate': rate,
                'outstanding': outstanding,
                'principal': principal,
                'commitment': commitment,
                'cusip': cusip,
                'isin': isin,
                'rate_type': rate_type,
                'seniority': seniority,
                'security_type': security_type,
                'benchmark': benchmark,
                'spread_bps': spread_bps,
                'floor_bps': floor_bps,
                'created_at': created_at
            })

        # Analyze and plan deduplication
        to_delete = []
        to_update = []
        skipped = []

        for key, instruments in groups.items():
            ticker, name, maturity, norm_issuer = key

            if len(instruments) < 2:
                continue

            # Check if all have same issuer (after normalization)
            issuer_ids = set(i['issuer_id'] for i in instruments)

            if len(issuer_ids) > 1:
                # Different issuers - skip unless they're known aliases
                original_issuers = set(i['issuer_name'] for i in instruments)
                normalized_issuers = set(normalize_issuer(n) if n else None for n in original_issuers)

                if len(normalized_issuers) > 1:
                    # Still different after normalization - skip
                    skipped.append({
                        'key': key,
                        'count': len(instruments),
                        'reason': f"Different issuers: {original_issuers}"
                    })
                    continue

            # Keep the first one (oldest), merge data, delete the rest
            keeper = instruments[0]
            extras = instruments[1:]

            # Merge non-null values from extras into keeper
            merged_updates = {}
            for extra in extras:
                for field in ['rate', 'outstanding', 'principal', 'commitment', 'cusip', 'isin',
                              'rate_type', 'seniority', 'security_type', 'benchmark', 'spread_bps', 'floor_bps']:
                    if keeper[field] is None and extra[field] is not None:
                        merged_updates[field] = extra[field]
                        keeper[field] = extra[field]  # Update keeper for subsequent merges

            if merged_updates:
                to_update.append({
                    'id': keeper['id'],
                    'updates': merged_updates,
                    'key': key
                })

            for extra in extras:
                to_delete.append({
                    'id': extra['id'],
                    'key': key
                })

        # Report
        print(f"\n{'='*60}")
        print("DEDUPLICATION ANALYSIS")
        print(f"{'='*60}")
        print(f"\nDuplicate groups found: {len(groups)}")
        print(f"Records to delete: {len(to_delete)}")
        print(f"Records to update (merge data): {len(to_update)}")
        print(f"Groups skipped (different issuers): {len(skipped)}")

        if args.verbose and skipped:
            print(f"\n--- Skipped groups (different issuers) ---")
            for s in skipped[:10]:
                print(f"  {s['key'][0]}: \"{s['key'][1]}\" - {s['reason']}")
            if len(skipped) > 10:
                print(f"  ... and {len(skipped) - 10} more")

        if args.verbose and to_delete:
            print(f"\n--- Sample deletions ---")
            by_ticker = defaultdict(list)
            for d in to_delete:
                by_ticker[d['key'][0]].append(d['key'][1])
            for ticker, names in list(by_ticker.items())[:10]:
                print(f"  {ticker}: {len(names)} duplicates")
                for name in names[:3]:
                    print(f"    - \"{name}\"")
                if len(names) > 3:
                    print(f"    ... and {len(names) - 3} more")

        if args.save:
            print(f"\n--- Applying changes ---")

            # First, update keepers with merged data
            for upd in to_update:
                set_clauses = []
                params = {'id': upd['id']}
                for field, value in upd['updates'].items():
                    db_field = field
                    set_clauses.append(f"{db_field} = :{field}")
                    params[field] = value

                if set_clauses:
                    query = f"UPDATE debt_instruments SET {', '.join(set_clauses)} WHERE id = :id"
                    await conn.execute(text(query), params)

            print(f"  Updated {len(to_update)} records with merged data")

            # Then delete extras
            if to_delete:
                delete_ids = [d['id'] for d in to_delete]

                # First, update any guarantees pointing to deleted instruments
                # (reassign to the keeper)
                for key, instruments in groups.items():
                    if len(instruments) < 2:
                        continue
                    keeper_id = instruments[0]['id']
                    extra_ids = [i['id'] for i in instruments[1:]]

                    for extra_id in extra_ids:
                        # Delete guarantees that would create duplicates, update the rest
                        await conn.execute(text('''
                            DELETE FROM guarantees
                            WHERE debt_instrument_id = :extra_id
                            AND guarantor_id IN (
                                SELECT guarantor_id FROM guarantees
                                WHERE debt_instrument_id = :keeper_id
                            )
                        '''), {'keeper_id': keeper_id, 'extra_id': extra_id})

                        await conn.execute(text('''
                            UPDATE guarantees SET debt_instrument_id = :keeper_id
                            WHERE debt_instrument_id = :extra_id
                        '''), {'keeper_id': keeper_id, 'extra_id': extra_id})

                        # Delete collateral that would create duplicates, update the rest
                        await conn.execute(text('''
                            DELETE FROM collateral
                            WHERE debt_instrument_id = :extra_id
                            AND collateral_type IN (
                                SELECT collateral_type FROM collateral
                                WHERE debt_instrument_id = :keeper_id
                            )
                        '''), {'keeper_id': keeper_id, 'extra_id': extra_id})

                        await conn.execute(text('''
                            UPDATE collateral SET debt_instrument_id = :keeper_id
                            WHERE debt_instrument_id = :extra_id
                        '''), {'keeper_id': keeper_id, 'extra_id': extra_id})

                # Now delete the duplicates
                for delete_id in delete_ids:
                    await conn.execute(text('''
                        DELETE FROM debt_instruments WHERE id = :id
                    '''), {'id': delete_id})

                print(f"  Deleted {len(to_delete)} duplicate records")
        else:
            print(f"\n[DRY RUN] Run with --save to apply changes")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
