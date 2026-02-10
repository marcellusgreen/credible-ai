#!/usr/bin/env python3
"""Check what linked credit facilities look like."""

import asyncio
import sys
import io
import os

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from app.core.database import async_session_maker


async def check():
    async with async_session_maker() as session:
        # Check what linked documents look like
        result = await session.execute(text("""
            SELECT
                di.name,
                di.instrument_type,
                ds.doc_type,
                ds.section_type,
                ds.section_title,
                c.ticker
            FROM debt_instrument_documents did
            JOIN debt_instruments di ON di.id = did.debt_instrument_id
            JOIN document_sections ds ON ds.id = did.document_section_id
            JOIN companies c ON c.id = di.company_id
            WHERE di.instrument_type IN ('revolver', 'term_loan_a', 'term_loan_b', 'term_loan')
            LIMIT 20
        """))
        print("Sample linked credit facilities:")
        print("=" * 100)
        for row in result.fetchall():
            name, itype, doc_type, sec_type, sec_title, ticker = row
            print(f"{ticker}: {itype} - {name[:40]}")
            print(f"    -> {doc_type} / {sec_type} / {(sec_title[:50] if sec_title else 'None')}")

        # Count linked vs unlinked credit facilities
        print()
        print("=" * 100)
        result = await session.execute(text("""
            SELECT
                di.instrument_type,
                COUNT(DISTINCT di.id) as total,
                COUNT(DISTINCT did.debt_instrument_id) as linked
            FROM debt_instruments di
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
              AND di.instrument_type IN ('revolver', 'term_loan_a', 'term_loan_b', 'term_loan', 'abl', 'credit_facility')
              AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL)
            GROUP BY di.instrument_type
            ORDER BY total DESC
        """))
        print("\nCredit facility coverage by type:")
        for row in result.fetchall():
            itype, total, linked = row
            pct = linked / total * 100 if total > 0 else 0
            print(f"  {itype:20}: {linked:3}/{total:3} ({pct:.1f}%)")


async def main():
    await check()


if __name__ == "__main__":
    asyncio.run(main())
