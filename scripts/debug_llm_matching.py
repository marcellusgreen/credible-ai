#!/usr/bin/env python3
"""Debug why LLM matching isn't finding results."""

import asyncio
import sys
import io
import os
import re

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from app.core.database import async_session_maker


async def debug():
    async with async_session_maker() as session:
        # Pick SPG's first unmatched note
        result = await session.execute(text("""
            SELECT
                di.id, di.name, di.instrument_type, di.interest_rate, di.maturity_date,
                di.outstanding, di.principal, c.ticker
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            WHERE c.ticker = 'SPG'
              AND di.is_active = true
              AND di.id NOT IN (SELECT debt_instrument_id FROM debt_instrument_documents)
            LIMIT 1
        """))
        inst_row = result.fetchone()
        if not inst_row:
            print("No unlinked instruments for SPG")
            return

        inst_id, inst_name, inst_type, rate, maturity, outstanding, principal, ticker = inst_row
        print(f"Instrument: {inst_name}")
        print(f"  Type: {inst_type}")
        print(f"  Rate: {rate}")
        print(f"  Maturity: {maturity}")
        print()

        # Get company_id
        result = await session.execute(text("""
            SELECT id FROM companies WHERE ticker = 'SPG'
        """))
        company_id = result.fetchone()[0]

        # Get all indentures
        result = await session.execute(text("""
            SELECT id, section_title, section_type, filing_date, LEFT(content, 2000) as preview
            FROM document_sections
            WHERE company_id = :cid AND section_type = 'indenture'
            ORDER BY filing_date DESC
            LIMIT 10
        """), {"cid": str(company_id)})
        docs = result.fetchall()

        print(f"Available indentures: {len(docs)}")

        # Extract rate/year for filtering
        # Rate is stored in basis points (e.g., 175 = 1.75%)
        if rate:
            rate_decimal = float(rate) / 100  # Convert basis points to percent
            rate_val = f"{rate_decimal:.2f}".rstrip('0').rstrip('.')  # "1.75"
        else:
            rate_val = None

        year_match = re.search(r'(\d{4})', str(maturity)) if maturity else None
        year_val = year_match.group(1) if year_match else None

        print(f"Looking for rate={rate_val}, year={year_val}")
        print()

        for doc in docs:
            doc_id, title, sec_type, filing_date, preview = doc
            print(f"Doc: {title[:60] if title else 'No title'} ({filing_date})")

            # Check if content has rate/year
            content = preview or ''
            has_rate = rate_val and rate_val in content
            has_year = year_val and year_val in content

            print(f"  Has rate ({rate_val}): {has_rate}")
            print(f"  Has year ({year_val}): {has_year}")

            # Always search in full content
            result2 = await session.execute(text("""
                SELECT content FROM document_sections WHERE id = :did
            """), {"did": str(doc_id)})
            full_content = result2.fetchone()[0] or ''

            # Search for rate anywhere in full content
            if rate_val and rate_val in full_content:
                pos = full_content.find(rate_val)
                print(f"  ** Rate {rate_val} found at position {pos}!")
                print(f"     Context: ...{full_content[max(0,pos-30):pos+50]}...")

            # Search for year anywhere
            if year_val and year_val in full_content:
                pos = full_content.find(year_val)
                print(f"  ** Year {year_val} found at position {pos}!")
                # Find if the rate appears near this year mention
                context = full_content[max(0,pos-1000):pos+1000]
                if rate_val and rate_val in context:
                    print(f"     AND rate {rate_val} appears near it!")

            print()


async def main():
    await debug()


if __name__ == "__main__":
    asyncio.run(main())
