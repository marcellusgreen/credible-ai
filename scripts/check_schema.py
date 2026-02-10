#!/usr/bin/env python3
"""Check database schema."""

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


async def check_schema():
    async with async_session_maker() as session:
        # Check document_sections columns
        result = await session.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'document_sections'
            ORDER BY column_name
        """))
        print("document_sections columns:")
        for row in result.fetchall():
            print(f"  {row[0]}")

        # Check debt_instrument_documents columns
        result = await session.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'debt_instrument_documents'
            ORDER BY column_name
        """))
        print()
        print("debt_instrument_documents columns:")
        for row in result.fetchall():
            print(f"  {row[0]}")


async def main():
    await check_schema()


if __name__ == "__main__":
    asyncio.run(main())
