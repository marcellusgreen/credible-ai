#!/usr/bin/env python3
"""Analyze remaining unlinked instruments."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

load_dotenv()


async def analyze():
    database_url = os.getenv('DATABASE_URL')
    if 'postgresql://' in database_url and '+asyncpg' not in database_url:
        database_url = database_url.replace('postgresql://', 'postgresql+asyncpg://', 1)

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        print('=' * 80)
        print('ANALYSIS: REMAINING INSTRUMENTS WITHOUT DOCUMENT LINKS')
        print('=' * 80)

        # Get all unlinked instruments with details
        result = await session.execute(text('''
            WITH unlinked AS (
                SELECT
                    c.id as company_id,
                    c.ticker,
                    di.id,
                    di.name,
                    di.instrument_type,
                    di.principal / 100.0 / 1e9 as principal_bn,
                    di.maturity_date
                FROM debt_instruments di
                JOIN entities e ON e.id = di.issuer_id
                JOIN companies c ON c.id = e.company_id
                LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
                WHERE di.is_active = true AND did.id IS NULL
                AND di.instrument_type IN (
                    'senior_notes', 'senior_secured_notes', 'senior_unsecured_notes',
                    'convertible_notes', 'subordinated_notes', 'debentures',
                    'revolver', 'term_loan_a', 'term_loan_b', 'term_loan'
                )
            ),
            company_docs AS (
                SELECT
                    company_id,
                    COUNT(DISTINCT CASE WHEN section_type = 'indenture' THEN id END) as indentures,
                    COUNT(DISTINCT CASE WHEN section_type = 'credit_agreement' THEN id END) as credit_agreements
                FROM document_sections
                WHERE section_type IN ('indenture', 'credit_agreement')
                GROUP BY company_id
            )
            SELECT
                u.ticker,
                u.name,
                u.instrument_type,
                u.principal_bn,
                u.maturity_date,
                COALESCE(cd.indentures, 0) as indentures,
                COALESCE(cd.credit_agreements, 0) as credit_agreements,
                CASE
                    WHEN u.instrument_type IN ('revolver', 'term_loan_a', 'term_loan_b', 'term_loan') THEN 'credit_facility'
                    ELSE 'notes'
                END as category
            FROM unlinked u
            LEFT JOIN company_docs cd ON cd.company_id = u.company_id
            ORDER BY u.principal_bn DESC NULLS LAST
        '''))
        rows = result.fetchall()

        print(f'\nTotal unlinked instruments: {len(rows)}')

        # Categorize
        notes_with_indentures = [r for r in rows if r.category == 'notes' and r.indentures > 0]
        notes_no_indentures = [r for r in rows if r.category == 'notes' and r.indentures == 0]
        facilities_with_cas = [r for r in rows if r.category == 'credit_facility' and r.credit_agreements > 0]
        facilities_no_cas = [r for r in rows if r.category == 'credit_facility' and r.credit_agreements == 0]

        print(f'\n1. NOTES with indentures available (matching failed): {len(notes_with_indentures)}')
        print(f'2. NOTES with NO indentures in database: {len(notes_no_indentures)}')
        print(f'3. CREDIT FACILITIES with CAs available (matching failed): {len(facilities_with_cas)}')
        print(f'4. CREDIT FACILITIES with NO CAs in database: {len(facilities_no_cas)}')

        # Analyze notes with indentures - why didn't they match?
        print('\n' + '-' * 80)
        print('CATEGORY 1: Notes with indentures available but no match')
        print('-' * 80)

        # Check for patterns in names
        aggregated = [r for r in notes_with_indentures if r.name and '-' in r.name and '%' in r.name]
        generic = [r for r in notes_with_indentures if r.name and ('medium-term' in r.name.lower() or 'various' in r.name.lower() or 'other' in r.name.lower())]

        print(f'  - Aggregated ranges (e.g., "2.60% - 3.20% due 2030"): {len(aggregated)}')
        print(f'  - Generic names (medium-term, various, other): {len(generic)}')
        print(f'  - Specific notes that failed to match: {len(notes_with_indentures) - len(aggregated) - len(generic)}')

        # Sample of specific notes that should have matched
        specific = [r for r in notes_with_indentures if r not in aggregated and r not in generic]
        print(f'\n  Sample specific notes (should have indenture):')
        for r in specific[:10]:
            name = (r.name or 'N/A')[:50]
            name = name.encode('ascii', 'replace').decode('ascii')
            principal = f'${r.principal_bn:.1f}B' if r.principal_bn else 'N/A'
            print(f'    {r.ticker}: {name} ({principal}, {r.indentures} indentures avail)')

        # Notes without any indentures
        print('\n' + '-' * 80)
        print('CATEGORY 2: Notes with NO indentures in database')
        print('-' * 80)
        print(f'  Total: {len(notes_no_indentures)}')
        print(f'\n  Companies missing indentures:')
        companies_no_ind = {}
        for r in notes_no_indentures:
            if r.ticker not in companies_no_ind:
                companies_no_ind[r.ticker] = 0
            companies_no_ind[r.ticker] += 1
        for ticker, count in sorted(companies_no_ind.items(), key=lambda x: -x[1])[:10]:
            print(f'    {ticker}: {count} notes')

        # Credit facilities analysis
        print('\n' + '-' * 80)
        print('CATEGORY 3: Credit facilities with CAs available but no match')
        print('-' * 80)
        print(f'  Total: {len(facilities_with_cas)}')
        print(f'\n  Sample:')
        for r in facilities_with_cas[:10]:
            name = (r.name or 'N/A')[:45]
            name = name.encode('ascii', 'replace').decode('ascii')
            print(f'    {r.ticker}: {name} ({r.credit_agreements} CAs avail)')

        # Credit facilities without CAs
        print('\n' + '-' * 80)
        print('CATEGORY 4: Credit facilities with NO CAs in database')
        print('-' * 80)
        print(f'  Total: {len(facilities_no_cas)}')
        if facilities_no_cas:
            print(f'\n  Sample:')
            for r in facilities_no_cas[:10]:
                name = (r.name or 'N/A')[:45]
                name = name.encode('ascii', 'replace').decode('ascii')
                print(f'    {r.ticker}: {name}')

        # Total principal breakdown
        print('\n' + '-' * 80)
        print('PRINCIPAL BREAKDOWN')
        print('-' * 80)
        total_principal = sum(r.principal_bn or 0 for r in rows)
        notes_principal = sum(r.principal_bn or 0 for r in rows if r.category == 'notes')
        facilities_principal = sum(r.principal_bn or 0 for r in rows if r.category == 'credit_facility')

        print(f'  Total unlinked principal: ${total_principal:.1f}B')
        print(f'    Notes: ${notes_principal:.1f}B')
        print(f'    Credit facilities: ${facilities_principal:.1f}B')

        # Top instruments by principal
        print(f'\n  Top 15 unlinked instruments by principal:')
        for r in rows[:15]:
            name = (r.name or 'N/A')[:40]
            name = name.encode('ascii', 'replace').decode('ascii')
            principal = f'${r.principal_bn:.1f}B' if r.principal_bn else 'N/A'
            docs = f'{r.indentures}i/{r.credit_agreements}ca'
            print(f'    {r.ticker:<6} {name:<42} {principal:>10} ({docs})')

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(analyze())
