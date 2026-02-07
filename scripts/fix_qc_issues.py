#!/usr/bin/env python3
"""
Fix QC issues identified by investigate_qc_issues.py

This script applies automated fixes where safe:
1. guarantor_flag_missing - Update is_guarantor flag
2. fixed_no_rate - Parse rate from instrument name
3. missing_maturity - Parse year from instrument name
"""

import re
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from script_utils import get_db_session, print_header, run_async


async def fix_guarantor_flags(session: AsyncSession) -> int:
    """Fix entities that are guarantors but have is_guarantor=false."""
    print("\n" + "=" * 70)
    print("FIX 1: GUARANTOR_FLAG_MISSING")
    print("=" * 70)

    # First, count affected
    result = await session.execute(text("""
        SELECT COUNT(DISTINCT e.id)
        FROM entities e
        JOIN guarantees g ON g.guarantor_id = e.id
        WHERE e.is_guarantor = false OR e.is_guarantor IS NULL
    """))
    count = result.scalar()
    print(f"Found {count} entities with incorrect is_guarantor flag")

    if count > 0:
        # Apply fix
        await session.execute(text("""
            UPDATE entities
            SET is_guarantor = true
            WHERE id IN (SELECT DISTINCT guarantor_id FROM guarantees)
            AND (is_guarantor = false OR is_guarantor IS NULL)
        """))
        await session.commit()
        print(f"Fixed {count} entities - set is_guarantor = true")

    return count


async def fix_interest_rates_from_names(session: AsyncSession) -> int:
    """Parse interest rates from instrument names like '5.25% Notes'."""
    print("\n" + "=" * 70)
    print("FIX 2: FIXED_NO_RATE (parse from name)")
    print("=" * 70)

    # Get instruments with fixed rate type but no rate, where name contains a rate
    result = await session.execute(text("""
        SELECT id, name
        FROM debt_instruments
        WHERE is_active = true
        AND rate_type = 'fixed'
        AND interest_rate IS NULL
        AND name ~ '\\d+\\.\\d+%'
    """))
    rows = result.fetchall()

    print(f"Found {len(rows)} fixed-rate instruments with rate in name")

    fixed_count = 0
    # Pattern to extract rate like 5.25% or 5.250%
    rate_pattern = re.compile(r'(\d+\.\d+)%')

    for row in rows:
        match = rate_pattern.search(row.name)
        if match:
            rate_str = match.group(1)
            # Convert to basis points (stored as integer, e.g., 5.25% = 525)
            rate_bps = int(float(rate_str) * 100)

            await session.execute(text("""
                UPDATE debt_instruments
                SET interest_rate = :rate
                WHERE id = :id
            """), {"rate": rate_bps, "id": row.id})
            fixed_count += 1

    if fixed_count > 0:
        await session.commit()
        print(f"Fixed {fixed_count} instruments - parsed rate from name")

    return fixed_count


async def fix_maturity_from_names(session: AsyncSession) -> int:
    """Parse maturity year from instrument names like 'Notes due 2027'."""
    print("\n" + "=" * 70)
    print("FIX 3: MISSING_MATURITY (parse from name)")
    print("=" * 70)

    # Get instruments without maturity where name contains 'due YYYY' or just 'YYYY'
    result = await session.execute(text("""
        SELECT id, name
        FROM debt_instruments
        WHERE is_active = true
        AND maturity_date IS NULL
        AND (name ~ 'due\\s+20\\d{2}' OR name ~ '20\\d{2}\\s+(Notes|Bonds|Senior)')
    """))
    rows = result.fetchall()

    print(f"Found {len(rows)} instruments with maturity year in name")

    fixed_count = 0
    # Pattern to extract year
    year_pattern = re.compile(r'(?:due\s+)?(20\d{2})')

    for row in rows:
        match = year_pattern.search(row.name)
        if match:
            year = int(match.group(1))
            # Set maturity to Dec 31 of that year (conservative estimate)
            maturity_date = date(year, 12, 31)

            await session.execute(text("""
                UPDATE debt_instruments
                SET maturity_date = :maturity
                WHERE id = :id
            """), {"maturity": maturity_date, "id": row.id})
            fixed_count += 1

    if fixed_count > 0:
        await session.commit()
        print(f"Fixed {fixed_count} instruments - parsed maturity year from name")

    return fixed_count


async def deduplicate_instruments(session: AsyncSession) -> int:
    """Remove duplicate instruments (same issuer + name + maturity)."""
    print("\n" + "=" * 70)
    print("FIX 4: DUPLICATE_INSTRUMENTS (keep one, remove others)")
    print("=" * 70)

    # Find duplicates - keep the one with the most complete data (first id as tiebreaker)
    result = await session.execute(text("""
        WITH duplicates AS (
            SELECT
                issuer_id,
                name,
                maturity_date,
                COUNT(*) AS cnt,
                MIN(id::text)::uuid AS keep_id
            FROM debt_instruments
            WHERE is_active = true
            GROUP BY issuer_id, name, maturity_date
            HAVING COUNT(*) > 1
        )
        SELECT d.id
        FROM debt_instruments d
        JOIN duplicates dup ON
            d.issuer_id = dup.issuer_id
            AND (d.name = dup.name OR (d.name IS NULL AND dup.name IS NULL))
            AND (d.maturity_date = dup.maturity_date OR (d.maturity_date IS NULL AND dup.maturity_date IS NULL))
        WHERE d.is_active = true
        AND d.id != dup.keep_id
    """))
    rows = result.fetchall()

    print(f"Found {len(rows)} duplicate instruments to deactivate")

    if len(rows) > 0:
        ids_to_deactivate = [row.id for row in rows]
        # Deactivate instead of delete to preserve audit trail
        await session.execute(text("""
            UPDATE debt_instruments
            SET is_active = false
            WHERE id = ANY(:ids)
        """), {"ids": ids_to_deactivate})
        await session.commit()
        print(f"Deactivated {len(ids_to_deactivate)} duplicate instruments")

    return len(rows)


async def main():
    print_header("QC ISSUE AUTO-FIX")

    async with get_db_session() as session:
        total_fixed = 0

        # Fix 1: Guarantor flags
        fixed = await fix_guarantor_flags(session)
        total_fixed += fixed

        # Fix 2: Interest rates from names
        fixed = await fix_interest_rates_from_names(session)
        total_fixed += fixed

        # Fix 3: Maturity from names
        fixed = await fix_maturity_from_names(session)
        total_fixed += fixed

        # Fix 4: Deduplicate instruments
        fixed = await deduplicate_instruments(session)
        total_fixed += fixed

    print("\n" + "=" * 70)
    print(f"TOTAL FIXES APPLIED: {total_fixed}")
    print("=" * 70)
    print("\nRun investigate_qc_issues.py to verify fixes")


if __name__ == "__main__":
    run_async(main())
