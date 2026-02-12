#!/usr/bin/env python3
"""Analyze debt coverage gaps in detail."""
import asyncio
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

async def check():
    engine = create_async_engine(os.getenv('DATABASE_URL'), echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as session:
        # Check MISSING_ALL companies - what do their instruments look like?
        print('=== MISSING_ALL: Instrument details ===')
        result = await session.execute(text("""
            SELECT c.ticker, c.name,
                COUNT(di.id) as total_instruments,
                SUM(CASE WHEN di.outstanding IS NULL THEN 1 ELSE 0 END) as null_outstanding,
                SUM(CASE WHEN di.outstanding = 0 THEN 1 ELSE 0 END) as zero_outstanding,
                SUM(CASE WHEN di.outstanding > 0 THEN 1 ELSE 0 END) as has_outstanding,
                SUM(CASE WHEN di.principal IS NULL THEN 1 ELSE 0 END) as null_principal,
                SUM(CASE WHEN di.principal > 0 THEN 1 ELSE 0 END) as has_principal
            FROM companies c
            JOIN debt_instruments di ON di.company_id = c.id AND di.is_active = true
            LEFT JOIN (
                SELECT DISTINCT ON (company_id) company_id, total_debt
                FROM company_financials
                WHERE total_debt IS NOT NULL AND total_debt > 0
                ORDER BY company_id, fiscal_year DESC, fiscal_quarter DESC
            ) lf ON lf.company_id = c.id
            WHERE lf.total_debt > 0
            AND NOT EXISTS (
                SELECT 1 FROM debt_instruments di2
                WHERE di2.company_id = c.id AND di2.is_active = true AND di2.outstanding > 0
            )
            GROUP BY c.ticker, c.name
            ORDER BY total_instruments DESC
        """))
        for row in result.fetchall():
            print(f'  {row[0]} ({row[1]}): {row[2]} instruments, {row[3]} null outstanding, {row[4]} zero outstanding, {row[6]} null principal, {row[7]} has principal')

        # Check a few EXCESS companies
        print()
        print('=== EXCESS_SIGNIFICANT: Sample instrument details ===')
        result = await session.execute(text("""
            SELECT c.ticker,
                COUNT(di.id) as total,
                SUM(CASE WHEN di.outstanding IS NULL THEN 1 ELSE 0 END) as null_out,
                SUM(CASE WHEN di.outstanding > 0 THEN 1 ELSE 0 END) as has_out,
                COUNT(DISTINCT di.name) as unique_names,
                SUM(COALESCE(di.outstanding, 0)) as sum_outstanding
            FROM companies c
            JOIN debt_instruments di ON di.company_id = c.id AND di.is_active = true
            WHERE c.ticker IN ('AAPL','AVGO','APA','APH','AMGN','BA','ABBV')
            GROUP BY c.ticker
            ORDER BY c.ticker
        """))
        for row in result.fetchall():
            print(f'  {row[0]}: {row[1]} instruments ({row[4]} unique names), {row[2]} null outstanding, {row[3]} has outstanding, sum=${float(row[5])/1e11:.2f}B')

        # Check for duplicate names in EXCESS companies
        print()
        print('=== EXCESS: Duplicate instrument names (top 30) ===')
        result = await session.execute(text("""
            SELECT c.ticker, di.name, COUNT(*) as cnt, SUM(COALESCE(di.outstanding, 0)) as total_out
            FROM companies c
            JOIN debt_instruments di ON di.company_id = c.id AND di.is_active = true
            WHERE c.ticker IN ('AAPL','AVGO','APA','APH','AMGN','BA')
            GROUP BY c.ticker, di.name
            HAVING COUNT(*) > 1
            ORDER BY COUNT(*) DESC
            LIMIT 30
        """))
        for row in result.fetchall():
            out = float(row[3]) / 1e11 if row[3] else 0
            print(f'  {row[0]}: "{row[1]}" x{row[2]} (sum=${out:.2f}B)')

        # Check AAPL specifically - biggest excess
        print()
        print('=== AAPL: Sample instruments (first 20) ===')
        result = await session.execute(text("""
            SELECT di.name, di.outstanding, di.instrument_type, di.maturity_date,
                   di.attributes->>'source' as source
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            WHERE c.ticker = 'AAPL' AND di.is_active = true
            ORDER BY di.outstanding DESC NULLS LAST
            LIMIT 20
        """))
        for row in result.fetchall():
            out = float(row[1]) / 1e11 if row[1] else 0
            print(f'  {row[0]}: ${out:.2f}B, type={row[2]}, maturity={row[3]}, source={row[4]}')

        # Check how many are Finnhub-discovered vs SEC-extracted
        print()
        print('=== Source breakdown for EXCESS companies ===')
        result = await session.execute(text("""
            SELECT c.ticker,
                SUM(CASE WHEN di.attributes->>'source' = 'finnhub_discovery' THEN 1 ELSE 0 END) as finnhub,
                SUM(CASE WHEN di.attributes->>'source' IS NULL OR di.attributes->>'source' != 'finnhub_discovery' THEN 1 ELSE 0 END) as sec_extracted,
                SUM(CASE WHEN di.attributes->>'source' = 'finnhub_discovery' THEN COALESCE(di.outstanding, 0) ELSE 0 END) as finnhub_outstanding,
                SUM(CASE WHEN di.attributes->>'source' IS NULL OR di.attributes->>'source' != 'finnhub_discovery' THEN COALESCE(di.outstanding, 0) ELSE 0 END) as sec_outstanding
            FROM companies c
            JOIN debt_instruments di ON di.company_id = c.id AND di.is_active = true
            WHERE c.ticker IN ('AAPL','AVGO','APA','APH','AMGN','BA','ABBV','ABNB','ACN','APP')
            GROUP BY c.ticker
            ORDER BY c.ticker
        """))
        for row in result.fetchall():
            fh_out = float(row[3]) / 1e11 if row[3] else 0
            sec_out = float(row[4]) / 1e11 if row[4] else 0
            print(f'  {row[0]}: {row[1]} finnhub (${fh_out:.2f}B) + {row[2]} SEC (${sec_out:.2f}B)')

        # Check the overall scale of outstanding amounts - are they in cents or dollars?
        print()
        print('=== Scale check: AAPL top 5 instruments raw values ===')
        result = await session.execute(text("""
            SELECT di.name, di.outstanding, di.principal
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            WHERE c.ticker = 'AAPL' AND di.is_active = true AND di.outstanding > 0
            ORDER BY di.outstanding DESC
            LIMIT 5
        """))
        for row in result.fetchall():
            print(f'  {row[0]}: outstanding={row[1]}, principal={row[2]}')

        # Also check a MISSING_ALL company's instruments
        print()
        print('=== IBM: Sample instruments (MISSING_ALL) ===')
        result = await session.execute(text("""
            SELECT di.name, di.outstanding, di.principal, di.instrument_type,
                   di.attributes->>'source' as source
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            WHERE c.ticker = 'IBM' AND di.is_active = true
            ORDER BY di.name
            LIMIT 15
        """))
        for row in result.fetchall():
            print(f'  {row[0]}: outstanding={row[1]}, principal={row[2]}, type={row[3]}, source={row[4]}')

    await engine.dispose()

asyncio.run(check())
