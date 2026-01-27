#!/usr/bin/env python3
"""
Investigate secured debt instruments without collateral records.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

load_dotenv()


async def investigate_secured():
    database_url = os.getenv('DATABASE_URL')
    if 'postgresql://' in database_url and '+asyncpg' not in database_url:
        database_url = database_url.replace('postgresql://', 'postgresql+asyncpg://', 1)

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        print('=' * 80)
        print('SECURED WITHOUT COLLATERAL - DETAILED BREAKDOWN')
        print('=' * 80)

        # Total count
        result = await session.execute(text('''
            SELECT COUNT(*) FROM debt_instruments d
            WHERE d.is_active = true
            AND (d.seniority = 'senior_secured' OR d.security_type IN ('first_lien', 'second_lien'))
            AND NOT EXISTS (SELECT 1 FROM collateral c WHERE c.debt_instrument_id = d.id)
        '''))
        total = result.scalar()
        print(f'\nTotal secured instruments without collateral records: {total}')

        # Breakdown by seniority
        print('\n' + '-' * 80)
        print('BY SENIORITY:')
        print('-' * 80)
        result = await session.execute(text('''
            SELECT
                d.seniority,
                COUNT(*) as cnt
            FROM debt_instruments d
            WHERE d.is_active = true
            AND (d.seniority = 'senior_secured' OR d.security_type IN ('first_lien', 'second_lien'))
            AND NOT EXISTS (SELECT 1 FROM collateral c WHERE c.debt_instrument_id = d.id)
            GROUP BY d.seniority
            ORDER BY cnt DESC
        '''))
        for row in result.fetchall():
            print(f'  {row.seniority or "NULL":<25} {row.cnt:>5}')

        # Breakdown by security_type
        print('\n' + '-' * 80)
        print('BY SECURITY TYPE:')
        print('-' * 80)
        result = await session.execute(text('''
            SELECT
                d.security_type,
                COUNT(*) as cnt
            FROM debt_instruments d
            WHERE d.is_active = true
            AND (d.seniority = 'senior_secured' OR d.security_type IN ('first_lien', 'second_lien'))
            AND NOT EXISTS (SELECT 1 FROM collateral c WHERE c.debt_instrument_id = d.id)
            GROUP BY d.security_type
            ORDER BY cnt DESC
        '''))
        for row in result.fetchall():
            print(f'  {row.security_type or "NULL":<25} {row.cnt:>5}')

        # Breakdown by instrument_type
        print('\n' + '-' * 80)
        print('BY INSTRUMENT TYPE:')
        print('-' * 80)
        result = await session.execute(text('''
            SELECT
                d.instrument_type,
                COUNT(*) as cnt
            FROM debt_instruments d
            WHERE d.is_active = true
            AND (d.seniority = 'senior_secured' OR d.security_type IN ('first_lien', 'second_lien'))
            AND NOT EXISTS (SELECT 1 FROM collateral c WHERE c.debt_instrument_id = d.id)
            GROUP BY d.instrument_type
            ORDER BY cnt DESC
        '''))
        for row in result.fetchall():
            print(f'  {row.instrument_type or "NULL":<25} {row.cnt:>5}')

        # Breakdown by company
        print('\n' + '-' * 80)
        print('TOP 15 COMPANIES WITH SECURED DEBT WITHOUT COLLATERAL:')
        print('-' * 80)
        result = await session.execute(text('''
            SELECT
                c.ticker,
                c.name,
                COUNT(*) as cnt
            FROM debt_instruments d
            JOIN entities e ON e.id = d.issuer_id
            JOIN companies c ON c.id = e.company_id
            WHERE d.is_active = true
            AND (d.seniority = 'senior_secured' OR d.security_type IN ('first_lien', 'second_lien'))
            AND NOT EXISTS (SELECT 1 FROM collateral c2 WHERE c2.debt_instrument_id = d.id)
            GROUP BY c.ticker, c.name
            ORDER BY cnt DESC
            LIMIT 15
        '''))
        print(f'  {"Ticker":<8} {"Company":<40} {"Count":>6}')
        print('  ' + '-' * 56)
        for row in result.fetchall():
            name = row.name[:38] if len(row.name) > 38 else row.name
            print(f'  {row.ticker:<8} {name:<40} {row.cnt:>6}')

        # Sample instruments
        print('\n' + '-' * 80)
        print('SAMPLE SECURED INSTRUMENTS WITHOUT COLLATERAL (by principal):')
        print('-' * 80)
        result = await session.execute(text('''
            SELECT
                c.ticker,
                d.name,
                d.instrument_type,
                d.seniority,
                d.security_type,
                d.principal / 100.0 / 1000000 as principal_mm
            FROM debt_instruments d
            JOIN entities e ON e.id = d.issuer_id
            JOIN companies c ON c.id = e.company_id
            WHERE d.is_active = true
            AND (d.seniority = 'senior_secured' OR d.security_type IN ('first_lien', 'second_lien'))
            AND NOT EXISTS (SELECT 1 FROM collateral c2 WHERE c2.debt_instrument_id = d.id)
            ORDER BY d.principal DESC NULLS LAST
            LIMIT 20
        '''))
        print(f'  {"Ticker":<6} {"Instrument":<35} {"Type":<15} {"Seniority":<18} {"$MM":>10}')
        print('  ' + '-' * 88)
        for row in result.fetchall():
            name = (row.name[:33] if row.name and len(row.name) > 33 else (row.name or 'N/A'))
            itype = (row.instrument_type[:13] if row.instrument_type else 'N/A')
            seniority = (row.seniority[:16] if row.seniority else 'N/A')
            principal = f'{row.principal_mm:,.0f}' if row.principal_mm else 'N/A'
            print(f'  {row.ticker:<6} {name:<35} {itype:<15} {seniority:<18} {principal:>10}')

        # Check how many DO have collateral
        print('\n' + '-' * 80)
        print('COMPARISON - SECURED DEBT WITH VS WITHOUT COLLATERAL:')
        print('-' * 80)

        result = await session.execute(text('''
            SELECT
                CASE WHEN EXISTS (SELECT 1 FROM collateral c WHERE c.debt_instrument_id = d.id)
                     THEN 'Has Collateral'
                     ELSE 'No Collateral'
                END as status,
                COUNT(*) as cnt,
                SUM(d.principal) / 100.0 / 1000000000 as total_principal_bn
            FROM debt_instruments d
            WHERE d.is_active = true
            AND (d.seniority = 'senior_secured' OR d.security_type IN ('first_lien', 'second_lien'))
            GROUP BY CASE WHEN EXISTS (SELECT 1 FROM collateral c WHERE c.debt_instrument_id = d.id)
                     THEN 'Has Collateral'
                     ELSE 'No Collateral'
                END
        '''))
        print(f'  {"Status":<20} {"Count":>8} {"Total Principal ($B)":>22}')
        print('  ' + '-' * 52)
        for row in result.fetchall():
            principal = f'{row.total_principal_bn:,.1f}' if row.total_principal_bn else 'N/A'
            print(f'  {row.status:<20} {row.cnt:>8} {principal:>22}')

        # Root cause analysis
        print('\n' + '-' * 80)
        print('ROOT CAUSE ANALYSIS:')
        print('-' * 80)
        print('''
  Why secured debt may not have collateral records:

  1. EXTRACTION GAP: Our collateral extraction step may not have found/parsed
     the collateral description from the 10-K/indenture documents.

  2. GENERIC COLLATERAL: Many secured loans have broad collateral descriptions
     like "substantially all assets" which may not have been extracted as
     discrete collateral items.

  3. DOCUMENT AVAILABILITY: Detailed collateral schedules are often in exhibits
     to credit agreements, not in the main 10-K filing.

  4. FIRST LIEN CREDIT FACILITIES: These typically have collateral but the
     specific pledged assets may not be itemized in public filings.

  5. DATA MODEL: Our collateral table expects specific asset descriptions,
     but many indentures use general language.
''')

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(investigate_secured())
