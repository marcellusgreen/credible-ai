#!/usr/bin/env python3
"""Debug SPG document structure."""

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


async def debug():
    async with async_session_maker() as session:
        # Get company_id for SPG
        result = await session.execute(text("""
            SELECT id FROM companies WHERE ticker = 'SPG'
        """))
        company_id = result.fetchone()[0]

        # Get all section types for SPG
        result = await session.execute(text("""
            SELECT section_type, COUNT(*) as cnt
            FROM document_sections
            WHERE company_id = :cid
            GROUP BY section_type
            ORDER BY cnt DESC
        """), {"cid": str(company_id)})

        print("SPG document section types:")
        for row in result.fetchall():
            print(f"  {row[0]}: {row[1]}")

        # Search ALL documents for "1.75" (the rate for the 2028 notes)
        print()
        print("Searching ALL SPG documents for '1.75'...")
        result = await session.execute(text("""
            SELECT id, section_type, section_title, filing_date
            FROM document_sections
            WHERE company_id = :cid
              AND content LIKE '%1.75%'
        """), {"cid": str(company_id)})

        docs = result.fetchall()
        print(f"Found {len(docs)} documents containing '1.75':")
        for doc in docs[:20]:
            doc_id, sec_type, title, filing_date = doc
            print(f"  {sec_type}: {title[:50] if title else 'No title'} ({filing_date})")

        # Search for "2028" in notes context
        print()
        print("Searching ALL SPG documents for '2028' near 'Notes'...")
        result = await session.execute(text("""
            SELECT id, section_type, section_title, filing_date
            FROM document_sections
            WHERE company_id = :cid
              AND content LIKE '%2028%Notes%'
        """), {"cid": str(company_id)})

        docs = result.fetchall()
        print(f"Found {len(docs)} documents containing '2028...Notes':")
        for doc in docs[:20]:
            doc_id, sec_type, title, filing_date = doc
            print(f"  {sec_type}: {title[:50] if title else 'No title'} ({filing_date})")

        # Check an indenture content to understand its structure
        print()
        print("Sample indenture first 1000 chars:")
        result = await session.execute(text("""
            SELECT content FROM document_sections
            WHERE company_id = :cid AND section_type = 'indenture'
            ORDER BY filing_date DESC LIMIT 1
        """), {"cid": str(company_id)})
        content = result.fetchone()[0]
        print(content[:1500] if content else "No content")

        # Check the debt_footnote content
        print()
        print("Sample debt_footnote first 1500 chars:")
        result = await session.execute(text("""
            SELECT content FROM document_sections
            WHERE company_id = :cid AND section_type = 'debt_footnote'
            ORDER BY filing_date DESC LIMIT 1
        """), {"cid": str(company_id)})
        row = result.fetchone()
        if row:
            print(row[0][:1500] if row[0] else "No content")


async def main():
    await debug()


if __name__ == "__main__":
    asyncio.run(main())
