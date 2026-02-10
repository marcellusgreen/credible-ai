#!/usr/bin/env python3
"""Check indenture document quality."""

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
        # How many indentures are actually Description of Securities?
        result = await session.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE content LIKE '%DESCRIPTION OF SECURITIES%' OR content LIKE '%DESCRIPTION OF EACH REGISTRANT%') as desc_of_sec,
                COUNT(*) as total
            FROM document_sections
            WHERE section_type = 'indenture'
        """))
        row = result.fetchone()
        print(f"Indenture sections that are 'Description of Securities': {row[0]} / {row[1]}")
        print(f"  (These are NOT actual indentures - they describe equity, not debt)")

        # Show breakdown
        print()
        print("Sample of misclassified 'indentures' (first 10):")
        result = await session.execute(text("""
            SELECT c.ticker, ds.section_title, ds.filing_date, LEFT(ds.content, 200) as preview
            FROM document_sections ds
            JOIN companies c ON c.id = ds.company_id
            WHERE ds.section_type = 'indenture'
              AND (ds.content LIKE '%DESCRIPTION OF SECURITIES%' OR ds.content LIKE '%DESCRIPTION OF EACH REGISTRANT%')
            ORDER BY c.ticker, ds.filing_date DESC
            LIMIT 10
        """))
        for row in result.fetchall():
            ticker, title, date, preview = row
            print(f"  {ticker}: {title[:40] if title else 'No title'} ({date})")

        # Check actual indentures (those that mention principal amount)
        print()
        print("Actual indentures (mentioning principal amount):")
        result = await session.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE content LIKE '%principal amount%') as actual_indentures,
                COUNT(*) as total
            FROM document_sections
            WHERE section_type = 'indenture'
              AND NOT (content LIKE '%DESCRIPTION OF SECURITIES%' OR content LIKE '%DESCRIPTION OF EACH REGISTRANT%')
        """))
        row = result.fetchone()
        print(f"  Likely real indentures: {row[0]} / {row[1]}")


async def main():
    await check()


if __name__ == "__main__":
    asyncio.run(main())
