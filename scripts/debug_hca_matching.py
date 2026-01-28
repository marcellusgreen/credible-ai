#!/usr/bin/env python3
"""Debug why HCA 5.25% due 2049 isn't matching to its indenture."""

import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

load_dotenv()


def extract_note_descriptions(text: str) -> list[str]:
    """Extract full note descriptions from text."""
    descriptions = []

    # Pattern 1: rate% [qualifier] notes due [month] [day,] year
    pattern = r'(\d+\.?\d*)\s*%\s*(senior\s+secured\s+|senior\s+unsecured\s+|senior\s+|subordinated\s+)?notes?\s+due\s+(?:\w+\s+)?(?:\d{1,2},?\s+)?(\d{4})'

    for match in re.finditer(pattern, text, re.IGNORECASE):
        rate = match.group(1)
        year = match.group(3)
        try:
            rate_float = float(rate)
            normalized = f"{rate_float:.2f}% notes {year}"
            descriptions.append(normalized)
        except ValueError:
            pass

    return list(set(descriptions))


async def debug():
    database_url = os.getenv('DATABASE_URL')
    if 'postgresql://' in database_url and '+asyncpg' not in database_url:
        database_url = database_url.replace('postgresql://', 'postgresql+asyncpg://', 1)

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # Get HCA company
        result = await session.execute(text('''
            SELECT id FROM companies WHERE ticker = 'HCA'
        '''))
        company_id = result.scalar()

        print("=" * 100)
        print("DEBUG: HCA 5.25% Senior Secured Notes due 2049")
        print("=" * 100)

        # Get the instrument
        result = await session.execute(text('''
            SELECT id, name, interest_rate, maturity_date
            FROM debt_instruments
            WHERE name LIKE '%5.25%' AND name LIKE '%2049%'
            AND issuer_id IN (SELECT id FROM entities WHERE company_id = :cid)
        '''), {'cid': str(company_id)})
        instruments = result.fetchall()

        print(f"\nFound {len(instruments)} matching instruments:")
        for inst in instruments:
            print(f"  - {inst.name}")
            print(f"    Interest rate: {inst.interest_rate} bps")
            print(f"    Maturity: {inst.maturity_date}")

            # Build expected description
            coupon = inst.interest_rate / 100 if inst.interest_rate else None
            year = inst.maturity_date.year if inst.maturity_date else None
            if coupon and year:
                expected = f"{coupon:.2f}% notes {year}"
                print(f"    Expected description: {expected}")

        # Get indentures for HCA
        result = await session.execute(text('''
            SELECT id, section_title, LEFT(content, 100000) as content
            FROM document_sections
            WHERE company_id = :cid AND section_type = 'indenture'
            ORDER BY filing_date DESC
        '''), {'cid': str(company_id)})
        indentures = result.fetchall()

        print(f"\n\nFound {len(indentures)} indentures for HCA")

        # Check each indenture for "5.25" and "2049"
        print("\nSearching for 5.25% and 2049 in indentures...")
        for ind in indentures:
            title = ind.section_title or ""
            content = ind.content or ""

            # Check for our target rate and year
            has_rate = "5.25" in content or "5.250" in content
            has_year = "2049" in content

            if has_rate and has_year:
                print(f"\n  FOUND in: {title[:80]}")

                # Extract all note descriptions from this indenture
                descs = extract_note_descriptions(content[:50000])
                print(f"  Note descriptions extracted: {len(descs)}")
                for d in descs[:10]:
                    print(f"    - {d}")

                # Check if our target is there
                if "5.25% notes 2049" in descs:
                    print(f"\n  *** TARGET DESCRIPTION FOUND: 5.25% notes 2049 ***")
                else:
                    print(f"\n  Target '5.25% notes 2049' NOT in extracted descriptions")

                # Look for raw pattern
                raw_match = re.search(r'5\.25\d*\s*%[^%]*2049', content[:20000], re.IGNORECASE)
                if raw_match:
                    context = content[max(0, raw_match.start()-50):raw_match.end()+100]
                    print(f"\n  Raw match context:")
                    print(f"    ...{context[:200]}...")

        # Now simulate what the actual matching algorithm does
        print("\n\n" + "=" * 100)
        print("SIMULATING MATCHING ALGORITHM")
        print("=" * 100)

        # Get one instrument
        inst = instruments[0] if instruments else None
        if inst:
            inst_coupon = inst.interest_rate / 100 if inst.interest_rate else None
            inst_year = inst.maturity_date.year if inst.maturity_date else None
            inst_descriptions = extract_note_descriptions(inst.name or "")

            if inst_coupon and inst_year:
                constructed = f"{inst_coupon:.2f}% notes {inst_year}"
                if constructed not in inst_descriptions:
                    inst_descriptions.append(constructed)

            print(f"\nInstrument descriptions to match: {inst_descriptions}")

            # Check each indenture
            for ind in indentures[:5]:
                title = ind.section_title or ""
                content = (ind.content or "")[:50000]

                title_descs = extract_note_descriptions(title)
                content_descs = extract_note_descriptions(content)

                print(f"\nIndenture: {title[:60]}...")
                print(f"  Title descriptions: {title_descs[:5]}")
                print(f"  Content descriptions: {content_descs[:10]}")

                # Check for match
                for inst_desc in inst_descriptions:
                    if inst_desc in title_descs:
                        print(f"  MATCH IN TITLE: {inst_desc}")
                    if inst_desc in content_descs:
                        print(f"  MATCH IN CONTENT: {inst_desc}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(debug())
