#!/usr/bin/env python3
"""
Fix false ownership relationships in the database.

Problem: Exhibit 21 parsing defaulted all subsidiaries to be "direct" children of the root
company, which is incorrect. We should only show parent relationships we have evidence for.

This script:
1. PRESERVES entities with TRUE intermediate parents (parent is not root)
2. PRESERVES key entities (issuers/guarantors) - their link to root is meaningful
3. SETS parent_id = NULL for non-key entities falsely marked as direct children of root
4. Updates ownership_links table correspondingly

Usage:
    # Dry run - see what would change
    python scripts/fix_false_ownership.py

    # Apply changes
    python scripts/fix_false_ownership.py --save-db

    # Single company
    python scripts/fix_false_ownership.py --ticker RIG --save-db
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings


async def get_stats(session: AsyncSession, ticker: str = None) -> dict:
    """Get current ownership statistics."""
    ticker_filter = "AND c.ticker = :ticker" if ticker else ""
    params = {"ticker": ticker} if ticker else {}

    async def count_query(query: str) -> int:
        result = await session.execute(text(query), params)
        return result.scalar() or 0

    total = await count_query(f'''
        SELECT COUNT(*) FROM entities e
        JOIN companies c ON e.company_id = c.id
        WHERE e.is_root = false {ticker_filter}
    ''')

    parent_is_root = await count_query(f'''
        SELECT COUNT(*) FROM entities e
        JOIN companies c ON e.company_id = c.id
        JOIN entities p ON e.parent_id = p.id
        WHERE p.is_root = true {ticker_filter}
    ''')

    parent_intermediate = await count_query(f'''
        SELECT COUNT(*) FROM entities e
        JOIN companies c ON e.company_id = c.id
        JOIN entities p ON e.parent_id = p.id
        WHERE p.is_root = false {ticker_filter}
    ''')

    key_entities = await count_query(f'''
        SELECT COUNT(DISTINCT e.id)
        FROM entities e
        JOIN companies c ON e.company_id = c.id
        WHERE (
            e.id IN (SELECT issuer_id FROM debt_instruments WHERE issuer_id IS NOT NULL)
            OR e.id IN (SELECT guarantor_id FROM guarantees WHERE guarantor_id IS NOT NULL)
        ) {ticker_filter}
    ''')

    to_fix = await count_query(f'''
        SELECT COUNT(*) FROM entities e
        JOIN companies c ON e.company_id = c.id
        JOIN entities p ON e.parent_id = p.id
        WHERE p.is_root = true
        AND e.id NOT IN (SELECT issuer_id FROM debt_instruments WHERE issuer_id IS NOT NULL)
        AND e.id NOT IN (SELECT guarantor_id FROM guarantees WHERE guarantor_id IS NOT NULL)
        {ticker_filter}
    ''')

    return {
        "total": total,
        "parent_is_root": parent_is_root,
        "parent_intermediate": parent_intermediate,
        "key_entities": key_entities,
        "to_fix": to_fix,
        "to_preserve": parent_intermediate + key_entities,
    }


async def fix_false_ownership(session: AsyncSession, ticker: str = None, save_db: bool = False) -> dict:
    """
    Fix false ownership relationships.

    Sets parent_id = NULL for entities that:
    - Have parent_id pointing to root company
    - Are NOT issuers or guarantors (key entities)

    Preserves:
    - Entities with intermediate parents (true relationships)
    - Key entities (issuers/guarantors)
    """

    ticker_filter = "AND c.ticker = :ticker" if ticker else ""
    params = {"ticker": ticker} if ticker else {}

    # Get entities to fix
    result = await session.execute(text(f'''
        SELECT e.id, e.name, c.ticker, e.parent_id
        FROM entities e
        JOIN companies c ON e.company_id = c.id
        JOIN entities p ON e.parent_id = p.id
        WHERE p.is_root = true
        AND e.id NOT IN (SELECT issuer_id FROM debt_instruments WHERE issuer_id IS NOT NULL)
        AND e.id NOT IN (SELECT guarantor_id FROM guarantees WHERE guarantor_id IS NOT NULL)
        {ticker_filter}
        ORDER BY c.ticker, e.name
    '''), params)

    entities_to_fix = result.fetchall()

    stats = {
        "entities_fixed": 0,
        "ownership_links_removed": 0,
        "by_company": {},
    }

    if not entities_to_fix:
        print("No entities to fix.")
        return stats

    # Group by company for reporting
    for _, _, company_ticker, _ in entities_to_fix:
        stats["by_company"][company_ticker] = stats["by_company"].get(company_ticker, 0) + 1

    if save_db:
        # Update entities - set parent_id to NULL
        entity_ids = [str(row[0]) for row in entities_to_fix]

        # Do in batches to avoid query size limits
        batch_size = 500
        for i in range(0, len(entity_ids), batch_size):
            batch = entity_ids[i:i+batch_size]
            batch_str = ",".join(f"'{eid}'" for eid in batch)

            # Update entities
            await session.execute(text(f'''
                UPDATE entities
                SET parent_id = NULL
                WHERE id IN ({batch_str})
            '''))

            # Remove corresponding ownership_links
            result = await session.execute(text(f'''
                DELETE FROM ownership_links
                WHERE child_entity_id IN ({batch_str})
                AND parent_entity_id IN (
                    SELECT id FROM entities WHERE is_root = true
                )
            '''))
            stats["ownership_links_removed"] += result.rowcount

        await session.commit()
        stats["entities_fixed"] = len(entities_to_fix)
    else:
        stats["entities_fixed"] = len(entities_to_fix)
        stats["ownership_links_removed"] = len(entities_to_fix)  # Estimate

    return stats


async def main():
    parser = argparse.ArgumentParser(description="Fix false ownership relationships")
    parser.add_argument("--ticker", help="Process single company")
    parser.add_argument("--save-db", action="store_true", help="Save changes to database")
    parser.add_argument("--show-preserved", action="store_true", help="Show preserved relationships")
    args = parser.parse_args()

    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        print("=" * 70)
        print("FIX FALSE OWNERSHIP RELATIONSHIPS")
        print("=" * 70)
        print(f"Mode: {'SAVE TO DB' if args.save_db else 'DRY RUN'}")
        if args.ticker:
            print(f"Company: {args.ticker}")
        print()

        # Get before stats
        print("BEFORE:")
        print("-" * 70)
        stats_before = await get_stats(session, args.ticker)
        print(f"  Total non-root entities: {stats_before['total']}")
        print(f"  Parent is root company: {stats_before['parent_is_root']}")
        print(f"  Parent is intermediate (TRUE): {stats_before['parent_intermediate']}")
        print(f"  Key entities (issuers/guarantors): {stats_before['key_entities']}")
        print(f"  To fix (false direct links): {stats_before['to_fix']}")
        print(f"  To preserve: {stats_before['to_preserve']}")
        print()

        if args.show_preserved:
            # Show what we're preserving
            ticker_filter = "AND c.ticker = :ticker" if args.ticker else ""
            params = {"ticker": args.ticker} if args.ticker else {}

            result = await session.execute(text(f'''
                SELECT c.ticker, e.name, p.name as parent_name, p.is_root
                FROM entities e
                JOIN companies c ON e.company_id = c.id
                JOIN entities p ON e.parent_id = p.id
                WHERE p.is_root = false {ticker_filter}
                ORDER BY c.ticker, e.name
                LIMIT 50
            '''), params)

            preserved = result.fetchall()
            print("PRESERVED INTERMEDIATE RELATIONSHIPS (sample):")
            print("-" * 70)
            for row in preserved[:30]:
                child = row[1][:35] if row[1] else ''
                parent = row[2][:30] if row[2] else ''
                print(f"  [{row[0]}] {child} -> {parent}")
            if len(preserved) > 30:
                print(f"  ... and {len(preserved) - 30} more")
            print()

        # Fix
        fix_stats = await fix_false_ownership(session, args.ticker, args.save_db)

        print("CHANGES:")
        print("-" * 70)
        print(f"  Entities fixed (parent_id set to NULL): {fix_stats['entities_fixed']}")
        print(f"  Ownership links removed: {fix_stats['ownership_links_removed']}")

        if fix_stats['by_company']:
            print()
            print("  By company (top 20):")
            sorted_companies = sorted(fix_stats['by_company'].items(), key=lambda x: -x[1])
            for ticker, count in sorted_companies[:20]:
                print(f"    {ticker}: {count} entities")
            if len(sorted_companies) > 20:
                print(f"    ... and {len(sorted_companies) - 20} more companies")

        if args.save_db:
            # Get after stats
            print()
            print("AFTER:")
            print("-" * 70)
            stats_after = await get_stats(session, args.ticker)
            print(f"  Total non-root entities: {stats_after['total']}")
            print(f"  Parent is root company: {stats_after['parent_is_root']}")
            print(f"  Parent is intermediate (TRUE): {stats_after['parent_intermediate']}")
            print(f"  Entities with NULL parent: {stats_after['total'] - stats_after['parent_is_root'] - stats_after['parent_intermediate']}")
        else:
            print()
            print("Run with --save-db to apply changes.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
