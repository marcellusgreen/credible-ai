#!/usr/bin/env python3
"""
Analyze secured facilities that are missing collateral details.
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


async def analyze_missing_collateral():
    database_url = os.getenv('DATABASE_URL')
    if 'postgresql://' in database_url and '+asyncpg' not in database_url:
        database_url = database_url.replace('postgresql://', 'postgresql+asyncpg://', 1)

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        print('=' * 80)
        print('SECURED FACILITIES WITHOUT COLLATERAL DETAIL')
        print('=' * 80)

        # Get all secured instruments without collateral, with full details
        result = await session.execute(text('''
            SELECT
                c.ticker,
                c.name as company_name,
                d.name as instrument_name,
                d.instrument_type,
                d.seniority,
                d.security_type,
                d.principal / 100.0 / 1000000 as principal_mm
            FROM debt_instruments d
            JOIN entities e ON e.id = d.issuer_id
            JOIN companies c ON c.id = e.company_id
            WHERE d.is_active = true
            AND (d.seniority = 'senior_secured' OR d.security_type IN ('first_lien', 'second_lien'))
            AND NOT EXISTS (SELECT 1 FROM collateral col WHERE col.debt_instrument_id = d.id)
            ORDER BY c.ticker, d.principal DESC NULLS LAST
        '''))
        rows = result.fetchall()

        print(f'\nTotal secured facilities without collateral: {len(rows)}')
        print(f'Total principal: ${sum(r.principal_mm or 0 for r in rows):,.0f}MM')

        # Group by company for analysis
        by_company = {}
        for row in rows:
            if row.ticker not in by_company:
                by_company[row.ticker] = []
            by_company[row.ticker].append(row)

        print(f'\nAffects {len(by_company)} companies')

        # Print full detail by company
        print('\n' + '=' * 80)
        print('DETAIL BY COMPANY:')
        print('=' * 80)

        for ticker in sorted(by_company.keys()):
            instruments = by_company[ticker]
            total_principal = sum(r.principal_mm or 0 for r in instruments)
            company_name = instruments[0].company_name
            if len(company_name) > 40:
                company_name = company_name[:40]
            print(f'\n{ticker} ({company_name})')
            print(f'  Instruments: {len(instruments)}, Total Principal: ${total_principal:,.0f}MM')
            print('-' * 78)

            for inst in instruments:
                name = inst.instrument_name if inst.instrument_name else 'N/A'
                if len(name) > 45:
                    name = name[:45]
                itype = inst.instrument_type if inst.instrument_type else 'N/A'
                if len(itype) > 15:
                    itype = itype[:15]
                sec_type = inst.security_type if inst.security_type else 'N/A'
                if len(sec_type) > 12:
                    sec_type = sec_type[:12]
                principal = f'${inst.principal_mm:,.0f}MM' if inst.principal_mm else 'N/A'
                print(f'  - {name:<45} {itype:<15} {sec_type:<12} {principal:>12}')

        # Summary by likely collateral type (inferred from instrument)
        print('\n' + '=' * 80)
        print('LIKELY COLLATERAL TYPES (inferred from instrument names):')
        print('=' * 80)

        categories = {
            'ABL/Receivables': [],
            'Real Estate/Mortgage': [],
            'Equipment/Vehicles': [],
            'General Corporate': [],
            'Other/Unknown': []
        }

        for row in rows:
            name_lower = (row.instrument_name or '').lower()
            itype_lower = (row.instrument_type or '').lower()

            if any(x in name_lower or x in itype_lower for x in ['abl', 'receivable', 'securitiz', 'factoring']):
                categories['ABL/Receivables'].append(row)
            elif any(x in name_lower or x in itype_lower for x in ['mortgage', 'real estate', 'property', 'fhlb']):
                categories['Real Estate/Mortgage'].append(row)
            elif any(x in name_lower or x in itype_lower for x in ['equipment', 'vehicle', 'fleet', 'aircraft', 'ship', 'eetc']):
                categories['Equipment/Vehicles'].append(row)
            elif any(x in name_lower or x in itype_lower for x in ['revolver', 'term loan', 'credit facilit', 'senior secured']):
                categories['General Corporate'].append(row)
            else:
                categories['Other/Unknown'].append(row)

        for cat_name, items in categories.items():
            if items:
                total = sum(r.principal_mm or 0 for r in items)
                print(f'\n{cat_name}: {len(items)} instruments, ${total:,.0f}MM')
                for item in items[:5]:  # Show top 5
                    name = item.instrument_name[:50] if item.instrument_name else 'N/A'
                    print(f'    - {item.ticker}: {name}')
                if len(items) > 5:
                    print(f'    ... and {len(items) - 5} more')

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(analyze_missing_collateral())
