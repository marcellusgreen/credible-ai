#!/usr/bin/env python3
"""Detailed analysis of excess debt: matured bonds, duplicates, source breakdown."""
import asyncio
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


async def analyze():
    engine = create_async_engine(os.getenv('DATABASE_URL'), echo=False)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        # 1. Matured but still active
        r = await session.execute(text("""
            SELECT COUNT(*) FROM debt_instruments
            WHERE is_active = true AND maturity_date < CURRENT_DATE
        """))
        matured_count = r.scalar()
        print(f"=== MATURED BUT STILL ACTIVE: {matured_count} instruments ===")

        r = await session.execute(text("""
            SELECT c.ticker, COUNT(*) as cnt,
                   SUM(COALESCE(di.outstanding, 0)) / 100000000000.0 as outstanding_b
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            WHERE di.is_active = true AND di.maturity_date < CURRENT_DATE
            GROUP BY c.ticker
            ORDER BY cnt DESC
            LIMIT 15
        """))
        for row in r.fetchall():
            print(f"  {row[0]:6s}: {row[1]:3d} matured, ${row[2]:.2f}B outstanding")

        # 2. Duplicate count
        print()
        r = await session.execute(text("""
            SELECT COUNT(*) as dup_groups, SUM(cnt) as total_instruments FROM (
                SELECT company_id,
                       ROUND(interest_rate/100.0, 2) as rate_pct,
                       EXTRACT(YEAR FROM maturity_date) as mat_year,
                       COUNT(*) as cnt
                FROM debt_instruments
                WHERE is_active = true
                  AND interest_rate IS NOT NULL
                  AND maturity_date IS NOT NULL
                GROUP BY company_id, ROUND(interest_rate/100.0, 2), EXTRACT(YEAR FROM maturity_date)
                HAVING COUNT(*) > 1
            ) sub
        """))
        row = r.fetchone()
        print(f"=== DUPLICATES (same rate+year): {row[0]} groups, {row[1]} total instruments ===")

    async with sm() as session:
        # 3. Worst duplicate companies
        r = await session.execute(text("""
            SELECT c.ticker, SUM(sub.cnt - 1) as extra, COUNT(*) as groups
            FROM (
                SELECT company_id,
                       ROUND(interest_rate/100.0, 2) as rate_pct,
                       EXTRACT(YEAR FROM maturity_date) as mat_year,
                       COUNT(*) as cnt
                FROM debt_instruments
                WHERE is_active = true
                  AND interest_rate IS NOT NULL
                  AND maturity_date IS NOT NULL
                GROUP BY company_id, ROUND(interest_rate/100.0, 2), EXTRACT(YEAR FROM maturity_date)
                HAVING COUNT(*) > 1
            ) sub
            JOIN companies c ON c.id = sub.company_id
            GROUP BY c.ticker
            ORDER BY SUM(sub.cnt - 1) DESC
            LIMIT 20
        """))
        print("\nCompanies with most duplicates:")
        for row in r.fetchall():
            print(f"  {row[0]:6s}: {row[1]:3.0f} extra instruments in {row[2]} dup groups")

    async with sm() as session:
        # 4. MA detail
        print("\n=== MA (Mastercard) - sample instruments ===")
        r = await session.execute(text("""
            SELECT di.name, di.interest_rate, di.maturity_date, di.outstanding,
                   COALESCE(di.attributes->>'source', 'sec_extracted') as source
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            WHERE c.ticker = 'MA' AND di.is_active = true
            ORDER BY di.maturity_date, di.interest_rate
            LIMIT 25
        """))
        for row in r.fetchall():
            amt = f"${row[3]/100_000_000_000:.2f}B" if row[3] else "$0"
            rate = f"{row[1]/100:.3f}%" if row[1] else "?"
            print(f"  {row[0][:50]:50s} | {rate:>8s} | {str(row[2]):>10s} | {amt:>8s} | {row[4]}")

    async with sm() as session:
        # 5. NFLX detail
        print("\n=== NFLX (Netflix) - all instruments ===")
        r = await session.execute(text("""
            SELECT di.name, di.interest_rate, di.maturity_date, di.outstanding,
                   COALESCE(di.attributes->>'source', 'sec_extracted') as source
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            WHERE c.ticker = 'NFLX' AND di.is_active = true
            ORDER BY di.maturity_date, di.interest_rate
        """))
        for row in r.fetchall():
            amt = f"${row[3]/100_000_000_000:.2f}B" if row[3] else "$0"
            rate = f"{row[1]/100:.3f}%" if row[1] else "?"
            print(f"  {row[0][:50]:50s} | {rate:>8s} | {str(row[2]):>10s} | {amt:>8s} | {row[4]}")

    async with sm() as session:
        # 6. Source breakdown for excess companies
        print("\n=== SOURCE BREAKDOWN (excess companies, >120% ratio) ===")
        r = await session.execute(text("""
            WITH instrument_sums AS (
                SELECT di.company_id,
                       SUM(COALESCE(di.outstanding, 0)) as total_outstanding
                FROM debt_instruments di WHERE di.is_active = true
                GROUP BY di.company_id
            ),
            latest_fin AS (
                SELECT DISTINCT ON (company_id) company_id, total_debt
                FROM company_financials
                WHERE total_debt > 0
                ORDER BY company_id, fiscal_year DESC, fiscal_quarter DESC
            ),
            excess_companies AS (
                SELECT i.company_id
                FROM instrument_sums i
                JOIN latest_fin f ON f.company_id = i.company_id
                WHERE i.total_outstanding > f.total_debt * 1.2
            )
            SELECT
                COALESCE(di.attributes->>'source', 'sec_extracted') as source,
                COUNT(*) as cnt,
                SUM(COALESCE(di.outstanding, 0)) / 100000000000.0 as outstanding_b,
                SUM(CASE WHEN di.outstanding IS NULL OR di.outstanding = 0 THEN 1 ELSE 0 END) as zero_count
            FROM debt_instruments di
            JOIN excess_companies ec ON ec.company_id = di.company_id
            WHERE di.is_active = true
            GROUP BY COALESCE(di.attributes->>'source', 'sec_extracted')
            ORDER BY cnt DESC
        """))
        for row in r.fetchall():
            print(f"  {row[0]:20s}: {row[1]:5d} instruments, ${row[2]:.1f}B outstanding, {row[3]} with $0")

    async with sm() as session:
        # 7. Potential impact
        print("\n=== POTENTIAL IMPACT OF FIXES ===")

        r = await session.execute(text("""
            SELECT SUM(COALESCE(outstanding, 0)) / 100000000000.0
            FROM debt_instruments
            WHERE is_active = true AND maturity_date < CURRENT_DATE
        """))
        matured_b = r.scalar() or 0
        print(f"  Deactivating matured instruments: -{matured_count} instruments, -${matured_b:.1f}B")

        r = await session.execute(text("""
            WITH dup_groups AS (
                SELECT company_id,
                       ROUND(interest_rate/100.0, 2) as rate_pct,
                       EXTRACT(YEAR FROM maturity_date) as mat_year,
                       COUNT(*) as cnt,
                       SUM(COALESCE(outstanding, 0)) as total_outstanding,
                       MAX(COALESCE(outstanding, 0)) as max_outstanding
                FROM debt_instruments
                WHERE is_active = true
                  AND interest_rate IS NOT NULL
                  AND maturity_date IS NOT NULL
                GROUP BY company_id, ROUND(interest_rate/100.0, 2), EXTRACT(YEAR FROM maturity_date)
                HAVING COUNT(*) > 1
            )
            SELECT
                SUM(total_outstanding - max_outstanding) / 100000000000.0 as removable_b,
                SUM(cnt - 1) as removable_instruments
            FROM dup_groups
        """))
        row = r.fetchone()
        print(f"  Deduplicating (keep best per group): -{row[1]:.0f} instruments, -${row[0]:.1f}B")
        print(f"  Combined potential reduction: -${matured_b + (row[0] or 0):.1f}B")
        print(f"  Current total: $4,666B. After fixes: ~${4666 - matured_b - (row[0] or 0):.0f}B (vs $6,559B target)")

    await engine.dispose()

asyncio.run(analyze())
