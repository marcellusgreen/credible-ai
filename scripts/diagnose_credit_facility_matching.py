#!/usr/bin/env python3
"""
Diagnose why credit facilities aren't matching to available credit agreements.
"""

import asyncio
import io
import os
import re
import sys
from collections import defaultdict

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

load_dotenv()


def extract_facility_keywords(text: str) -> set[str]:
    """Extract facility type keywords from text."""
    keywords = set()
    text_lower = text.lower()

    if re.search(r'\brevolv', text_lower):
        keywords.add('revolving')
    if re.search(r'\bterm\s+loan', text_lower):
        keywords.add('term_loan')
    if re.search(r'\bterm\s+loan\s+a\b', text_lower):
        keywords.add('term_loan_a')
    if re.search(r'\bterm\s+loan\s+b\b', text_lower):
        keywords.add('term_loan_b')
    if re.search(r'\babl\b|\basset[- ]based', text_lower):
        keywords.add('abl')
    if re.search(r'\bdelayed\s+draw', text_lower):
        keywords.add('delayed_draw')
    if re.search(r'\bcredit\s+facility', text_lower):
        keywords.add('credit_facility')
    if re.search(r'\bcredit\s+agreement', text_lower):
        keywords.add('credit_agreement')

    return keywords


def extract_amounts_from_text(text: str) -> list[int]:
    """Extract dollar amounts from text (in cents)."""
    amounts = []

    # Pattern: $X billion or $X.X billion
    for match in re.finditer(r'\$\s*([\d,]+(?:\.\d+)?)\s*billion', text, re.IGNORECASE):
        try:
            value = float(match.group(1).replace(',', ''))
            amounts.append(int(value * 1_000_000_000_00))
        except ValueError:
            pass

    # Pattern: $X million or $X.X million
    for match in re.finditer(r'\$\s*([\d,]+(?:\.\d+)?)\s*million', text, re.IGNORECASE):
        try:
            value = float(match.group(1).replace(',', ''))
            amounts.append(int(value * 1_000_000_00))
        except ValueError:
            pass

    return amounts


async def diagnose():
    database_url = os.getenv('DATABASE_URL')
    if 'postgresql://' in database_url and '+asyncpg' not in database_url:
        database_url = database_url.replace('postgresql://', 'postgresql+asyncpg://', 1)

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        print("=" * 100)
        print("CREDIT FACILITY MATCHING DIAGNOSIS")
        print("=" * 100)

        # Get unlinked credit facilities with available CAs
        result = await session.execute(text('''
            WITH unlinked AS (
                SELECT
                    c.id as company_id,
                    c.ticker,
                    di.id as instrument_id,
                    di.name,
                    di.instrument_type,
                    di.commitment / 100.0 / 1e9 as commitment_bn,
                    di.issue_date
                FROM debt_instruments di
                JOIN entities e ON e.id = di.issuer_id
                JOIN companies c ON c.id = e.company_id
                LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
                WHERE di.is_active = true AND did.id IS NULL
                AND di.instrument_type IN ('revolver', 'term_loan_a', 'term_loan_b', 'term_loan')
            ),
            company_cas AS (
                SELECT company_id, COUNT(*) as ca_count
                FROM document_sections
                WHERE section_type = 'credit_agreement'
                GROUP BY company_id
            )
            SELECT u.*, COALESCE(cc.ca_count, 0) as ca_count
            FROM unlinked u
            LEFT JOIN company_cas cc ON cc.company_id = u.company_id
            WHERE COALESCE(cc.ca_count, 0) > 0
            ORDER BY u.commitment_bn DESC NULLS LAST
        '''))
        facilities = result.fetchall()

        print(f"\nAnalyzing {len(facilities)} unlinked credit facilities with CAs available...")

        # Group by company
        by_company = defaultdict(list)
        for f in facilities:
            by_company[f.ticker].append(f)

        print(f"\nCompanies with unlinked facilities: {len(by_company)}")

        # Analyze each company
        for ticker in sorted(by_company.keys())[:10]:  # Top 10 companies
            company_facilities = by_company[ticker]
            company_id = company_facilities[0].company_id

            print(f"\n{'='*80}")
            print(f"COMPANY: {ticker} ({len(company_facilities)} unlinked facilities)")
            print("="*80)

            # Get credit agreements for this company
            ca_result = await session.execute(text('''
                SELECT id, section_title, filing_date, LEFT(content, 5000) as content_preview
                FROM document_sections
                WHERE company_id = :cid AND section_type = 'credit_agreement'
                ORDER BY filing_date DESC
                LIMIT 10
            '''), {'cid': str(company_id)})
            credit_agreements = ca_result.fetchall()

            print(f"\nAvailable Credit Agreements ({len(credit_agreements)}):")
            for ca in credit_agreements[:5]:
                title = (ca.section_title or "")[:60]
                date = ca.filing_date
                keywords = extract_facility_keywords(ca.content_preview or "")
                amounts = extract_amounts_from_text(ca.content_preview or "")
                amounts_str = ", ".join([f"${a/1e11:.1f}B" for a in amounts[:3]]) if amounts else "none found"
                print(f"  - [{date}] {title}")
                print(f"    Keywords: {keywords}")
                print(f"    Amounts: {amounts_str}")

            print(f"\nUnlinked Facilities:")
            for f in company_facilities:
                name = (f.name or "")[:50]
                ftype = f.instrument_type
                commitment = f"${f.commitment_bn:.1f}B" if f.commitment_bn else "N/A"
                issue = f.issue_date
                print(f"  - {name}")
                print(f"    Type: {ftype}, Commitment: {commitment}, Issue: {issue}")

                # Check why it might not match
                inst_keywords = extract_facility_keywords(f.name or "")
                print(f"    Inst Keywords: {inst_keywords}")

        # Summary of matching issues
        print("\n" + "=" * 100)
        print("MATCHING ISSUE ANALYSIS")
        print("=" * 100)

        # Count by instrument type
        type_counts = defaultdict(int)
        for f in facilities:
            type_counts[f.instrument_type] += 1

        print("\nUnlinked by type:")
        for t, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f"  {t}: {count}")

        # Check if issue is keyword matching or amount matching
        print("\nPotential issues:")
        print("  1. Credit agreements may not mention specific facility type (revolver vs term loan)")
        print("  2. Amount matching may be off due to amendments changing commitment size")
        print("  3. Multiple facilities under one credit agreement (multi-tranche)")
        print("  4. Filing date may not match issue date (amendments filed later)")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(diagnose())
