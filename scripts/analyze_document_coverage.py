#!/usr/bin/env python3
"""
Analyze debt instrument document coverage (indentures, credit agreements).
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


async def analyze_document_coverage():
    database_url = os.getenv('DATABASE_URL')
    if 'postgresql://' in database_url and '+asyncpg' not in database_url:
        database_url = database_url.replace('postgresql://', 'postgresql+asyncpg://', 1)

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        print('=' * 80)
        print('DEBT INSTRUMENT DOCUMENT COVERAGE ANALYSIS')
        print('=' * 80)

        # Total active debt instruments
        result = await session.execute(text('''
            SELECT COUNT(*) FROM debt_instruments WHERE is_active = true
        '''))
        total_instruments = result.scalar()
        print(f'\nTotal active debt instruments: {total_instruments}')

        # Instruments with linked documents
        result = await session.execute(text('''
            SELECT COUNT(DISTINCT did.debt_instrument_id)
            FROM debt_instrument_documents did
            JOIN debt_instruments di ON di.id = did.debt_instrument_id
            WHERE di.is_active = true
        '''))
        with_docs = result.scalar()
        print(f'Instruments with linked documents: {with_docs} ({with_docs/total_instruments*100:.1f}%)')
        print(f'Instruments WITHOUT linked documents: {total_instruments - with_docs} ({(total_instruments - with_docs)/total_instruments*100:.1f}%)')

        # Breakdown by document type
        print('\n' + '-' * 80)
        print('BREAKDOWN BY DOCUMENT TYPE:')
        print('-' * 80)
        result = await session.execute(text('''
            SELECT
                ds.section_type,
                COUNT(DISTINCT did.debt_instrument_id) as instrument_count
            FROM debt_instrument_documents did
            JOIN document_sections ds ON ds.id = did.document_section_id
            JOIN debt_instruments di ON di.id = did.debt_instrument_id
            WHERE di.is_active = true
            GROUP BY ds.section_type
            ORDER BY instrument_count DESC
        '''))
        rows = result.fetchall()
        for row in rows:
            print(f'  {row.section_type:<30} {row.instrument_count:>6} instruments')

        # Breakdown by instrument type - with vs without docs
        print('\n' + '-' * 80)
        print('COVERAGE BY INSTRUMENT TYPE:')
        print('-' * 80)
        result = await session.execute(text('''
            SELECT
                di.instrument_type,
                COUNT(*) as total,
                COUNT(DISTINCT did.debt_instrument_id) as with_docs,
                SUM(di.principal) / 100.0 / 1e9 as total_principal_bn
            FROM debt_instruments di
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
            GROUP BY di.instrument_type
            ORDER BY total DESC
        '''))
        rows = result.fetchall()
        print(f'  {"Instrument Type":<25} {"Total":>8} {"With Docs":>10} {"Coverage":>10} {"Principal($B)":>14}')
        print('  ' + '-' * 70)
        for row in rows:
            coverage = (row.with_docs / row.total * 100) if row.total > 0 else 0
            principal = f'{row.total_principal_bn:.1f}' if row.total_principal_bn else 'N/A'
            print(f'  {row.instrument_type or "NULL":<25} {row.total:>8} {row.with_docs:>10} {coverage:>9.1f}% {principal:>14}')

        # Top companies without document coverage
        print('\n' + '-' * 80)
        print('TOP 15 COMPANIES WITH INSTRUMENTS MISSING DOCUMENTS:')
        print('-' * 80)
        result = await session.execute(text('''
            SELECT
                c.ticker,
                c.name,
                COUNT(*) as missing_docs,
                SUM(di.principal) / 100.0 / 1e9 as principal_bn
            FROM debt_instruments di
            JOIN entities e ON e.id = di.issuer_id
            JOIN companies c ON c.id = e.company_id
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
            AND did.id IS NULL
            GROUP BY c.ticker, c.name
            ORDER BY missing_docs DESC
            LIMIT 15
        '''))
        rows = result.fetchall()
        print(f'  {"Ticker":<8} {"Company":<35} {"Missing":>8} {"Principal($B)":>14}')
        print('  ' + '-' * 68)
        for row in rows:
            name = row.name[:33] if len(row.name) > 33 else row.name
            # Handle Unicode for Windows console
            name = name.encode('ascii', 'replace').decode('ascii')
            principal = f'{row.principal_bn:.1f}' if row.principal_bn else 'N/A'
            print(f'  {row.ticker:<8} {name:<35} {row.missing_docs:>8} {principal:>14}')

        # Sample instruments without documents
        print('\n' + '-' * 80)
        print('SAMPLE INSTRUMENTS WITHOUT LINKED DOCUMENTS (by principal):')
        print('-' * 80)
        result = await session.execute(text('''
            SELECT
                c.ticker,
                di.name,
                di.instrument_type,
                di.principal / 100.0 / 1e6 as principal_mm
            FROM debt_instruments di
            JOIN entities e ON e.id = di.issuer_id
            JOIN companies c ON c.id = e.company_id
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
            AND did.id IS NULL
            ORDER BY di.principal DESC NULLS LAST
            LIMIT 20
        '''))
        rows = result.fetchall()
        print(f'  {"Ticker":<6} {"Instrument":<45} {"Type":<18} {"Principal($MM)":>14}')
        print('  ' + '-' * 86)
        for row in rows:
            name = row.name[:43] if row.name and len(row.name) > 43 else (row.name or 'N/A')
            # Handle Unicode for Windows console
            name = name.encode('ascii', 'replace').decode('ascii')
            itype = row.instrument_type[:16] if row.instrument_type else 'N/A'
            principal = f'{row.principal_mm:,.0f}' if row.principal_mm else 'N/A'
            print(f'  {row.ticker:<6} {name:<45} {itype:<18} {principal:>14}')

        # Summary stats
        print('\n' + '-' * 80)
        print('SUMMARY:')
        print('-' * 80)

        # Principal coverage
        result = await session.execute(text('''
            SELECT
                SUM(CASE WHEN did.id IS NOT NULL THEN di.principal ELSE 0 END) / 100.0 / 1e9 as covered_bn,
                SUM(CASE WHEN did.id IS NULL THEN di.principal ELSE 0 END) / 100.0 / 1e9 as uncovered_bn,
                SUM(di.principal) / 100.0 / 1e9 as total_bn
            FROM debt_instruments di
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
        '''))
        row = result.fetchone()
        covered = row.covered_bn or 0
        uncovered = row.uncovered_bn or 0
        total = row.total_bn or 0
        coverage_pct = (covered / total * 100) if total > 0 else 0

        print(f'  Principal with documents:    ${covered:,.1f}B ({coverage_pct:.1f}%)')
        print(f'  Principal without documents: ${uncovered:,.1f}B ({100-coverage_pct:.1f}%)')
        print(f'  Total principal:             ${total:,.1f}B')

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(analyze_document_coverage())
