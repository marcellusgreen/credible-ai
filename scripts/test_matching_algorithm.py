#!/usr/bin/env python3
"""
Test the matching algorithm on specific failing instruments.

This verifies that find_all_matching_documents() correctly matches
instruments when the indenture exists.
"""

import asyncio
import io
import os
import re
import sys

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

load_dotenv()

# Import the matching functions
from app.services.document_matching import (
    find_all_matching_documents,
    extract_note_descriptions,
)
from app.models import DebtInstrument, DocumentSection


async def test():
    database_url = os.getenv('DATABASE_URL')
    if 'postgresql://' in database_url and '+asyncpg' not in database_url:
        database_url = database_url.replace('postgresql://', 'postgresql+asyncpg://', 1)

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        print("=" * 100)
        print("TESTING DOCUMENT MATCHING ALGORITHM")
        print("=" * 100)

        # Test case: HCA 5.25% due 2049
        print("\n\n--- Test Case: HCA 5.25% Senior Secured Notes due 2049 ---")

        # Get the instrument
        result = await session.execute(text('''
            SELECT di.*, e.company_id, e.name as issuer_name
            FROM debt_instruments di
            JOIN entities e ON e.id = di.issuer_id
            WHERE di.name LIKE '%5.25%' AND di.name LIKE '%2049%'
            AND e.company_id = (SELECT id FROM companies WHERE ticker = 'HCA')
            LIMIT 1
        '''))
        row = result.fetchone()
        if not row:
            print("Instrument not found")
            return

        # Convert to DebtInstrument-like object for the function
        from datetime import date
        from uuid import UUID

        class MockInstrument:
            def __init__(self, row):
                self.id = row.id
                self.name = row.name
                self.interest_rate = row.interest_rate
                self.maturity_date = row.maturity_date
                self.issue_date = row.issue_date
                self.cusip = row.cusip
                self.isin = row.isin
                self.principal = row.principal
                self.outstanding = row.outstanding
                self.seniority = row.seniority
                self.instrument_type = row.instrument_type

        instrument = MockInstrument(row)
        company_id = row.company_id
        issuer_name = row.issuer_name

        print(f"Instrument: {instrument.name}")
        print(f"Interest rate: {instrument.interest_rate} bps")
        print(f"Maturity date: {instrument.maturity_date}")

        # Get indentures for this company (limit to most recent 50)
        result = await session.execute(text('''
            SELECT * FROM document_sections
            WHERE company_id = :cid AND section_type = 'indenture'
            ORDER BY filing_date DESC
            LIMIT 50
        '''), {'cid': str(company_id)})
        indenture_rows = result.fetchall()

        # Convert to DocumentSection-like objects
        class MockDocument:
            def __init__(self, row):
                self.id = row.id
                self.section_title = row.section_title
                self.content = row.content
                self.filing_date = row.filing_date
                self.section_type = row.section_type

        indentures = [MockDocument(r) for r in indenture_rows]

        print(f"Checking against {len(indentures)} indentures...")

        # Test the matching function
        matches = find_all_matching_documents(
            instrument,
            indentures,
            issuer_name=issuer_name,
            min_confidence=0.40,
        )

        if matches:
            print(f"\nFOUND {len(matches)} MATCHES!")
            for i, match in enumerate(matches[:5]):
                print(f"\n  Match {i+1}:")
                print(f"    Confidence: {match.match_confidence:.2f}")
                print(f"    Method: {match.match_method}")
                print(f"    Signals: {[s.signal_type for s in match.signals]}")
                # Get document title
                doc = next((d for d in indentures if d.id == match.document_section_id), None)
                if doc:
                    title = (doc.section_title or "")[:80].encode('ascii', 'replace').decode('ascii')
                    print(f"    Document: {title}")
        else:
            print("\nNO MATCHES FOUND!")

            # Debug: what descriptions are we looking for?
            inst_coupon = instrument.interest_rate / 100 if instrument.interest_rate else None
            inst_year = instrument.maturity_date.year if instrument.maturity_date else None
            inst_descriptions = extract_note_descriptions(instrument.name or "")
            if inst_coupon and inst_year:
                constructed = f"{inst_coupon:.2f}% notes {inst_year}"
                if constructed not in inst_descriptions:
                    inst_descriptions.append(constructed)

            print(f"\n  Instrument descriptions to match: {inst_descriptions}")

            # Check each indenture
            for ind in indentures[:5]:
                content = (ind.content or "")[:50000]
                doc_descriptions = extract_note_descriptions(content)
                print(f"\n  Indenture: {(ind.section_title or '')[:50]}...")
                print(f"    Note descriptions found: {doc_descriptions[:5]}")

                # Check for our target
                for inst_desc in inst_descriptions:
                    if inst_desc in doc_descriptions:
                        print(f"    *** FOUND: {inst_desc} ***")

        # Now test another failing case: WYNN 5.625% due 2028
        print("\n\n--- Test Case: WYNN 5 5/8% Senior Notes due 2028 ---")

        result = await session.execute(text('''
            SELECT di.*, e.company_id, e.name as issuer_name
            FROM debt_instruments di
            JOIN entities e ON e.id = di.issuer_id
            WHERE di.name ILIKE '%5%8%2028%' OR di.name ILIKE '%5.625%2028%'
            AND e.company_id = (SELECT id FROM companies WHERE ticker = 'WYNN')
            LIMIT 1
        '''))
        row = result.fetchone()
        if row:
            instrument = MockInstrument(row)
            company_id = row.company_id
            issuer_name = row.issuer_name

            print(f"Instrument: {instrument.name}")
            print(f"Interest rate: {instrument.interest_rate} bps")

            # Get indentures
            result = await session.execute(text('''
                SELECT * FROM document_sections
                WHERE company_id = :cid AND section_type = 'indenture'
                ORDER BY filing_date DESC
                LIMIT 30
            '''), {'cid': str(company_id)})
            indenture_rows = result.fetchall()
            indentures = [MockDocument(r) for r in indenture_rows]

            print(f"Checking against {len(indentures)} indentures...")

            matches = find_all_matching_documents(
                instrument,
                indentures,
                issuer_name=issuer_name,
                min_confidence=0.40,
            )

            if matches:
                print(f"\nFOUND {len(matches)} MATCHES!")
                for i, match in enumerate(matches[:3]):
                    print(f"\n  Match {i+1}:")
                    print(f"    Confidence: {match.match_confidence:.2f}")
                    print(f"    Method: {match.match_method}")
                    doc = next((d for d in indentures if d.id == match.document_section_id), None)
                    if doc:
                        title = (doc.section_title or "")[:80].encode('ascii', 'replace').decode('ascii')
                        print(f"    Document: {title}")
            else:
                print("\nNO MATCHES FOUND!")
                # Debug
                inst_coupon = instrument.interest_rate / 100 if instrument.interest_rate else None
                inst_year = instrument.maturity_date.year if instrument.maturity_date else None
                inst_descriptions = extract_note_descriptions(instrument.name or "")
                if inst_coupon and inst_year:
                    constructed = f"{inst_coupon:.2f}% notes {inst_year}"
                    if constructed not in inst_descriptions:
                        inst_descriptions.append(constructed)
                print(f"\n  Instrument descriptions to match: {inst_descriptions}")
        else:
            print("Instrument not found")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(test())
