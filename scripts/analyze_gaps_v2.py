#!/usr/bin/env python3
"""Corrected debt coverage gap analysis - avoids JOIN multiplication."""
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
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as session:
        # Corrected query - uses subqueries to avoid JOIN multiplication
        result = await session.execute(text("""
            WITH latest_financials AS (
                SELECT DISTINCT ON (company_id)
                    company_id, total_debt, fiscal_year, fiscal_quarter
                FROM company_financials
                WHERE total_debt IS NOT NULL AND total_debt > 0
                ORDER BY company_id, fiscal_year DESC, fiscal_quarter DESC
            ),
            instrument_stats AS (
                SELECT
                    company_id,
                    COUNT(*) as instrument_count,
                    SUM(COALESCE(outstanding, 0)) as instruments_sum,
                    SUM(CASE WHEN outstanding IS NULL THEN 1 ELSE 0 END) as null_outstanding,
                    SUM(CASE WHEN outstanding = 0 THEN 1 ELSE 0 END) as zero_outstanding,
                    SUM(CASE WHEN outstanding > 0 THEN 1 ELSE 0 END) as has_outstanding
                FROM debt_instruments
                WHERE is_active = true
                GROUP BY company_id
            )
            SELECT
                c.ticker, c.name,
                COALESCE(ist.instrument_count, 0) as instrument_count,
                COALESCE(ist.instruments_sum, 0) as instruments_sum,
                COALESCE(lf.total_debt, 0) as financials_total_debt,
                lf.fiscal_year, lf.fiscal_quarter,
                COALESCE(ist.null_outstanding, 0) as null_outstanding,
                COALESCE(ist.zero_outstanding, 0) as zero_outstanding,
                COALESCE(ist.has_outstanding, 0) as has_outstanding,
                CASE
                    WHEN lf.total_debt IS NULL OR lf.total_debt = 0 THEN 'NO_FINANCIALS'
                    WHEN COALESCE(ist.instruments_sum, 0) = 0 THEN 'MISSING_ALL'
                    WHEN ist.instruments_sum < lf.total_debt * 0.5 THEN 'MISSING_SIGNIFICANT'
                    WHEN ist.instruments_sum > lf.total_debt * 2 THEN 'EXCESS_SIGNIFICANT'
                    WHEN ist.instruments_sum < lf.total_debt * 0.8 THEN 'MISSING_SOME'
                    WHEN ist.instruments_sum > lf.total_debt * 1.2 THEN 'EXCESS_SOME'
                    ELSE 'OK'
                END as status
            FROM companies c
            LEFT JOIN latest_financials lf ON lf.company_id = c.id
            LEFT JOIN instrument_stats ist ON ist.company_id = c.id
            ORDER BY c.ticker
        """))

        companies = []
        for row in result.fetchall():
            companies.append({
                'ticker': row[0], 'name': row[1],
                'count': row[2], 'sum': row[3], 'total_debt': row[4],
                'fy': row[5], 'fq': row[6],
                'null_out': row[7], 'zero_out': row[8], 'has_out': row[9],
                'status': row[10]
            })

        # Summary
        by_status = {}
        for c in companies:
            s = c['status']
            if s not in by_status:
                by_status[s] = []
            by_status[s].append(c)

        print("=" * 110)
        print("DEBT COVERAGE ANALYSIS (CORRECTED - no JOIN multiplication)")
        print("=" * 110)

        for status in ['OK', 'EXCESS_SOME', 'EXCESS_SIGNIFICANT', 'MISSING_SOME', 'MISSING_SIGNIFICANT', 'MISSING_ALL', 'NO_FINANCIALS']:
            cos = by_status.get(status, [])
            print(f"\n{status}: {len(cos)} companies")
            if status != 'OK' and cos:
                for c in cos:
                    inst_sum = float(c['sum']) / 1e11 if c['sum'] else 0
                    fin_total = float(c['total_debt']) / 1e11 if c['total_debt'] else 0
                    gap = ''
                    if fin_total > 0:
                        gap_pct = (inst_sum - fin_total) * 100 / fin_total
                        gap = f" (gap: {gap_pct:+.1f}%)"
                    null_info = ''
                    if c['null_out'] > 0 or c['zero_out'] > 0:
                        null_info = f" [{c['null_out']} null, {c['zero_out']} zero, {c['has_out']} with amt]"
                    print(f"  {c['ticker']:6s}: {c['count']:3d} instruments, ${inst_sum:8.2f}B vs ${fin_total:8.2f}B{gap}{null_info}")

        # Grand totals
        total_inst_sum = sum(float(c['sum']) for c in companies) / 1e11
        total_fin = sum(float(c['total_debt']) for c in companies) / 1e11
        print(f"\n{'=' * 110}")
        print(f"TOTALS: {sum(c['count'] for c in companies)} instruments, ${total_inst_sum:.2f}B outstanding vs ${total_fin:.2f}B total debt")
        print(f"Overall ratio: {total_inst_sum/total_fin*100:.1f}%" if total_fin > 0 else "")

    await engine.dispose()

asyncio.run(analyze())
