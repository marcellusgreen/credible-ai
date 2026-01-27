#!/usr/bin/env python3
"""
Enhanced collateral extraction for secured debt instruments.

This script re-runs collateral extraction with improvements:
1. Better query to find secured instruments (checks both seniority AND security_type)
2. Fuzzy matching of debt names instead of exact match
3. More document content per instrument
4. Enhanced prompt with more collateral types
5. Extracts collateral even when specific instrument can't be matched (creates for all secured)
"""

import asyncio
import os
import sys
import argparse
from uuid import uuid4
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import text, select, or_
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

load_dotenv()

# Companies with secured debt missing collateral
TARGET_COMPANIES = [
    'ABBV', 'AMC', 'APP', 'BHC', 'CAR', 'CEG', 'CHS', 'CLF', 'CNK', 'CRWV',
    'DAL', 'DO', 'EXC', 'HCA', 'HTZ', 'INTU', 'JPM', 'KSS', 'LOW', 'MSTR',
    'NEE', 'NRG', 'PLTR', 'RCL', 'SLG', 'SPG', 'SWN', 'THC', 'TTD', 'WBD',
    'WYNN', 'X'
]


def fuzzy_match(name1: str, name2: str, threshold: float = 0.6) -> bool:
    """Check if two names are similar enough to match."""
    if not name1 or not name2:
        return False
    name1 = name1.lower().strip()
    name2 = name2.lower().strip()

    # Exact match
    if name1 == name2:
        return True

    # One contains the other
    if name1 in name2 or name2 in name1:
        return True

    # Fuzzy match
    ratio = SequenceMatcher(None, name1, name2).ratio()
    return ratio >= threshold


async def get_secured_instruments(session: AsyncSession, company_id):
    """Get all secured debt instruments for a company."""
    from app.models import DebtInstrument

    result = await session.execute(
        select(DebtInstrument).where(
            DebtInstrument.company_id == company_id,
            DebtInstrument.is_active == True,
            or_(
                DebtInstrument.seniority.in_(['senior_secured', 'secured']),
                DebtInstrument.security_type.in_(['first_lien', 'second_lien'])
            )
        )
    )
    return list(result.scalars().all())


async def get_document_content(session: AsyncSession, company_id, filings: dict = None) -> str:
    """Get document content for collateral extraction, prioritizing sections with collateral info."""
    from app.models import DocumentSection
    from sqlalchemy import or_

    # First, look for documents that explicitly mention collateral/secured
    result = await session.execute(
        select(DocumentSection).where(
            DocumentSection.company_id == company_id,
            or_(
                DocumentSection.content.ilike('%secured by%'),
                DocumentSection.content.ilike('%collateral%'),
                DocumentSection.content.ilike('%first-priority lien%'),
                DocumentSection.content.ilike('%pledged%'),
                DocumentSection.content.ilike('%security interest%')
            )
        ).order_by(DocumentSection.section_type)
    )
    collateral_docs = list(result.scalars().all())

    # Also get credit agreements and debt footnotes
    result = await session.execute(
        select(DocumentSection).where(
            DocumentSection.company_id == company_id,
            DocumentSection.section_type.in_([
                'credit_agreement', 'indenture', 'debt_footnote',
                'debt_overview', 'long_term_debt'
            ])
        ).order_by(DocumentSection.section_type)
    )
    standard_docs = list(result.scalars().all())

    # Combine and dedupe
    seen_ids = set()
    all_docs = []
    for doc in collateral_docs + standard_docs:
        if doc.id not in seen_ids:
            seen_ids.add(doc.id)
            all_docs.append(doc)

    if all_docs:
        # Extract relevant sections from each document
        content_parts = []
        for d in all_docs[:10]:  # Limit to 10 docs
            doc_content = d.content

            # Try to extract collateral-specific sections
            collateral_sections = extract_collateral_sections(doc_content)
            if collateral_sections:
                content_parts.append(f"=== {d.section_type.upper()} (collateral sections) ===\n{collateral_sections}")
            else:
                # Use first 30K chars if no specific sections found
                content_parts.append(f"=== {d.section_type.upper()} ===\n{doc_content[:30000]}")

        return "\n\n".join(content_parts)[:150000]

    # Fall back to raw filings if available
    if filings:
        content = ""
        for key, filing_content in filings.items():
            if filing_content and ('debt' in key.lower() or '10-k' in key.lower()):
                content += f"\n\n=== {key} ===\n{filing_content[:50000]}"
        return content[:150000]

    return ""


def extract_collateral_sections(content: str) -> str:
    """Extract sections of content that discuss collateral."""
    import re

    sections = []

    # Patterns that indicate collateral discussion
    patterns = [
        r'(?:secured|collateralized)\s+by[^.]*\.(?:[^.]*\.){0,5}',
        r'(?:first|second)-priority\s+lien[^.]*\.(?:[^.]*\.){0,5}',
        r'All\s+obligations\s+under[^.]*secured[^.]*\.(?:[^.]*\.){0,10}',
        r'Collateral[^.]*includes?[^.]*\.(?:[^.]*\.){0,5}',
        r'(?:pledged|pledge)\s+(?:of\s+)?(?:substantially\s+all|all)[^.]*\.(?:[^.]*\.){0,5}',
        r'security\s+interest\s+in[^.]*\.(?:[^.]*\.){0,5}',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, content, re.IGNORECASE | re.DOTALL)
        for match in matches:
            if len(match) > 50:  # Skip very short matches
                sections.append(match.strip())

    # Also look for bullet points describing collateral
    bullet_pattern = r'(?:�|\*|-)\s*(?:a\s+)?(?:first|second)-priority\s+lien[^�\*\-\n]*'
    bullet_matches = re.findall(bullet_pattern, content, re.IGNORECASE)
    sections.extend(bullet_matches)

    if sections:
        return "\n\n".join(set(sections))[:20000]

    return ""


async def extract_collateral_enhanced(
    session: AsyncSession,
    company_id,
    ticker: str,
    secured_instruments: list,
    doc_content: str,
    dry_run: bool = False
) -> dict:
    """Enhanced collateral extraction with better matching."""
    import google.generativeai as genai
    from app.models import Collateral
    from app.services.utils import parse_json_robust
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.gemini_api_key:
        return {'error': 'No Gemini API key'}

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")

    # Build detailed debt list with IDs for matching
    debt_list = []
    for i, inst in enumerate(secured_instruments[:30]):  # Limit to 30
        seniority = inst.seniority or 'unknown'
        sec_type = inst.security_type or 'unknown'
        principal = f"${inst.principal / 100 / 1e6:,.0f}MM" if inst.principal else "N/A"
        debt_list.append(f"{i+1}. {inst.name} | Seniority: {seniority} | Security: {sec_type} | Principal: {principal}")

    debt_str = "\n".join(debt_list)

    prompt = f"""Analyze this company's SEC filings to identify COLLATERAL securing these debt instruments.

COMPANY: {ticker}

SECURED DEBT INSTRUMENTS (numbered for reference):
{debt_str}

FILING CONTENT:
{doc_content[:100000]}

INSTRUCTIONS:
1. Find specific language describing what assets secure each debt instrument
2. Look for: "secured by", "collateralized by", "pledged", "first lien on", "security interest in"
3. Common collateral includes: real estate, equipment, receivables, inventory, vehicles, aircraft, ships, intellectual property, subsidiary stock, cash, securities
4. For credit facilities, look for "substantially all assets" or similar general security language
5. Include the EXACT quote from the filing describing the collateral

COLLATERAL TYPES (use these exact values):
- real_estate: Property, land, buildings, mortgages
- equipment: Machinery, rigs, manufacturing equipment
- receivables: Accounts receivable, notes receivable, securitization assets
- inventory: Raw materials, finished goods, work in progress
- vehicles: Aircraft, ships, trucks, fleet vehicles
- cash: Cash deposits, restricted cash
- ip: Intellectual property, patents, trademarks
- subsidiary_stock: Stock/equity of subsidiaries
- securities: Investment securities, marketable securities
- energy_assets: Oil/gas reserves, pipelines, power plants
- general_lien: "Substantially all assets" or blanket security interest

Return JSON with ONE collateral record per debt instrument (use the PRIMARY collateral type):
{{
  "collateral": [
    {{
      "debt_number": 1,
      "debt_name": "exact or close name from list",
      "collateral_type": "PRIMARY type from list above",
      "description": "comprehensive description of ALL collateral securing this debt",
      "source_quote": "exact quote from filing describing the collateral",
      "priority": "first_lien or second_lien"
    }}
  ],
  "notes": "any relevant observations about the collateral structure"
}}

IMPORTANT: Return only ONE record per debt instrument. If multiple collateral types secure one instrument, choose the PRIMARY type and include ALL types in the description.
- For credit facilities with blanket liens: use "general_lien"
- For ABL/receivables facilities: use "receivables"
- For mortgage-backed: use "real_estate"
- For equipment/vehicle financing: use "equipment" or "vehicles"

If a debt instrument clearly references being secured but you can't identify specific collateral, use "general_lien" with description "Secured by substantially all assets of borrower and guarantors".

Return ONLY valid JSON."""

    try:
        response = model.generate_content(prompt)
        result_data = parse_json_robust(response.text)

        if not result_data:
            return {'error': 'Failed to parse response', 'raw': response.text[:500]}

        collateral_items = result_data.get('collateral', [])
        created = 0
        matched = 0
        skipped = 0

        for c in collateral_items:
            # Try to match by number first
            debt_num = c.get('debt_number')
            instrument = None

            if debt_num and 1 <= debt_num <= len(secured_instruments):
                instrument = secured_instruments[debt_num - 1]
                matched += 1
            else:
                # Fall back to fuzzy name matching
                debt_name = c.get('debt_name', '')
                for inst in secured_instruments:
                    if fuzzy_match(debt_name, inst.name):
                        instrument = inst
                        matched += 1
                        break

            if not instrument:
                skipped += 1
                continue

            # Check if collateral already exists for this instrument
            existing = await session.execute(
                select(Collateral).where(Collateral.debt_instrument_id == instrument.id)
            )
            if existing.scalar_one_or_none():
                skipped += 1
                continue

            if dry_run:
                print(f"    [DRY RUN] Would create: {instrument.name[:40]} -> {c.get('collateral_type')}")
                created += 1
                continue

            # Create collateral record
            collateral = Collateral(
                id=uuid4(),
                debt_instrument_id=instrument.id,
                collateral_type=c.get('collateral_type', 'general_lien'),
                description=c.get('description', ''),
                priority=c.get('priority'),
            )
            session.add(collateral)
            created += 1

        if not dry_run and created > 0:
            await session.commit()

        return {
            'extracted': len(collateral_items),
            'matched': matched,
            'created': created,
            'skipped': skipped,
            'notes': result_data.get('notes', '')
        }

    except Exception as e:
        return {'error': str(e)}


async def process_company(session: AsyncSession, ticker: str, dry_run: bool = False) -> dict:
    """Process a single company for enhanced collateral extraction."""
    from app.models import Company

    # Get company
    result = await session.execute(
        select(Company).where(Company.ticker == ticker)
    )
    company = result.scalar_one_or_none()

    if not company:
        return {'ticker': ticker, 'error': 'Company not found'}

    # Get secured instruments
    secured = await get_secured_instruments(session, company.id)

    if not secured:
        return {'ticker': ticker, 'secured_count': 0, 'message': 'No secured instruments'}

    # Check how many already have collateral
    from app.models import Collateral
    result = await session.execute(
        select(Collateral).where(
            Collateral.debt_instrument_id.in_([i.id for i in secured])
        )
    )
    existing_collateral = len(list(result.scalars().all()))

    # Get instruments WITHOUT collateral
    instruments_with_collateral = set()
    result = await session.execute(
        select(Collateral.debt_instrument_id).where(
            Collateral.debt_instrument_id.in_([i.id for i in secured])
        )
    )
    for row in result:
        instruments_with_collateral.add(row[0])

    instruments_missing_collateral = [i for i in secured if i.id not in instruments_with_collateral]

    if not instruments_missing_collateral:
        return {
            'ticker': ticker,
            'secured_count': len(secured),
            'existing_collateral': existing_collateral,
            'message': 'All secured instruments already have collateral'
        }

    # Get document content
    doc_content = await get_document_content(session, company.id)

    if not doc_content:
        return {
            'ticker': ticker,
            'secured_count': len(secured),
            'missing_collateral': len(instruments_missing_collateral),
            'error': 'No document content available'
        }

    # Run enhanced extraction
    result = await extract_collateral_enhanced(
        session, company.id, ticker, instruments_missing_collateral, doc_content, dry_run
    )

    return {
        'ticker': ticker,
        'secured_count': len(secured),
        'existing_collateral': existing_collateral,
        'missing_collateral': len(instruments_missing_collateral),
        **result
    }


async def main():
    parser = argparse.ArgumentParser(description="Enhanced collateral extraction")
    parser.add_argument("--ticker", type=str, help="Process single ticker")
    parser.add_argument("--all", action="store_true", help="Process all 32 target companies")
    parser.add_argument("--dry-run", action="store_true", help="Don't save to database")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of companies to process")
    args = parser.parse_args()

    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    if 'postgresql://' in database_url and '+asyncpg' not in database_url:
        database_url = database_url.replace('postgresql://', 'postgresql+asyncpg://', 1)

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    print("=" * 70)
    print("ENHANCED COLLATERAL EXTRACTION")
    print("=" * 70)

    if args.dry_run:
        print("[DRY RUN MODE - No changes will be saved]")

    tickers = []
    if args.ticker:
        tickers = [args.ticker.upper()]
    elif args.all:
        tickers = TARGET_COMPANIES
        if args.limit > 0:
            tickers = tickers[:args.limit]
    else:
        print("Specify --ticker TICKER or --all")
        sys.exit(1)

    print(f"\nProcessing {len(tickers)} companies...")

    total_created = 0
    results = []

    async with async_session() as session:
        for i, ticker in enumerate(tickers):
            print(f"\n[{i+1}/{len(tickers)}] {ticker}")
            print("-" * 40)

            result = await process_company(session, ticker, args.dry_run)
            results.append(result)

            if 'error' in result:
                print(f"  ERROR: {result['error']}")
            elif 'message' in result:
                print(f"  {result['message']}")
            else:
                print(f"  Secured instruments: {result.get('secured_count', 0)}")
                print(f"  Already had collateral: {result.get('existing_collateral', 0)}")
                print(f"  Missing collateral: {result.get('missing_collateral', 0)}")
                print(f"  Extracted: {result.get('extracted', 0)}")
                print(f"  Created: {result.get('created', 0)}")
                if result.get('notes'):
                    print(f"  Notes: {result['notes'][:100]}")
                total_created += result.get('created', 0)

    await engine.dispose()

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Companies processed: {len(results)}")
    print(f"Total collateral records created: {total_created}")

    errors = [r for r in results if 'error' in r]
    if errors:
        print(f"\nErrors ({len(errors)}):")
        for r in errors:
            print(f"  - {r['ticker']}: {r['error']}")


if __name__ == "__main__":
    asyncio.run(main())
