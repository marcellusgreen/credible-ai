#!/usr/bin/env python3
"""Check document types in database."""

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


async def check_docs():
    async with async_session_maker() as session:
        result = await session.execute(text("""
            SELECT doc_type, COUNT(*) as cnt
            FROM document_sections
            GROUP BY doc_type
            ORDER BY cnt DESC
        """))
        print("Document types in database:")
        for row in result.fetchall():
            print(f"  {row[0]}: {row[1]}")

        print()
        print("Sample doc_types for credit-related:")
        result = await session.execute(text("""
            SELECT DISTINCT doc_type
            FROM document_sections
            WHERE doc_type ILIKE '%credit%' OR doc_type ILIKE '%loan%' OR doc_type ILIKE '%facility%'
        """))
        for row in result.fetchall():
            print(f"  {row[0]}")


async def main():
    await check_docs()


if __name__ == "__main__":
    asyncio.run(main())
