#!/usr/bin/env python3
"""
Extract collateral information for secured debt instruments.

Uses LLM to parse stored indentures, credit agreements, and debt footnotes
to identify collateral types (real estate, equipment, receivables, etc.).

Usage:
    python scripts/extract_collateral.py --ticker TSLA [--dry-run] [--verbose]
    python scripts/extract_collateral.py --all [--limit N]
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings

# Set up API keys before importing genai
_settings = get_settings()
if _settings.gemini_api_key:
    os.environ["GEMINI_API_KEY"] = _settings.gemini_api_key

import google.generativeai as genai
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.models import Company, DebtInstrument, DocumentSection, Collateral
from app.services.qa_agent import parse_json_robust


# =============================================================================
# PROMPTS
# =============================================================================

EXTRACT_COLLATERAL_PROMPT = """Analyze these SEC filing excerpts and identify what collateral secures each debt instrument.

DEBT INSTRUMENTS TO ANALYZE:
{debt_list}

DOCUMENT EXCERPTS:
{documents}

For each SECURED debt instrument, identify:
1. What type(s) of collateral secure it
2. Description of the collateral
3. Priority (first lien, second lien, etc.) if mentioned
4. Estimated value if disclosed

COLLATERAL TYPES (use these exact values):
- real_estate: Property, buildings, land, mortgages
- equipment: Machinery, vehicles, manufacturing equipment
- receivables: Accounts receivable, factoring facilities, AR-backed
- inventory: Raw materials, finished goods, work-in-progress
- securities: Pledged stocks, bonds, investments
- vehicles: Fleet vehicles, aircraft, ships, automobiles
- ip: Patents, trademarks, intellectual property
- cash: Cash deposits, restricted cash
- general_lien: Blanket lien on all/substantially all assets
- subsidiary_stock: Stock/equity of subsidiaries pledged
- solar_assets: Solar panels, energy systems, solar installations
- energy_assets: Energy storage, batteries, power systems

IMPORTANT GUIDANCE:
- "Asset-backed Notes" means the debt IS secured by assets - identify WHAT assets
- "Automotive Asset-backed Notes" = secured by vehicles (auto loans, vehicle leases, or vehicle inventory)
- "Energy Asset-backed Notes" or "Solar Asset-backed Notes" = secured by energy_assets or solar_assets
- "Cash Equity Debt" = typically secured by cash or equity/subsidiary_stock
- "Working Capital Facility" = typically secured by receivables and/or inventory
- Even if documents don't explicitly say "secured by X", infer from the debt name and context

Return JSON:
{{
  "collateral_mappings": [
    {{
      "debt_name": "Exact debt instrument name from list above",
      "collateral": [
        {{
          "type": "vehicles",
          "description": "Automotive loans and vehicle lease receivables",
          "priority": "first_lien",
          "estimated_value_usd": null
        }}
      ]
    }}
  ],
  "unsecured_debt": ["List of debt instruments that appear to be unsecured"],
  "uncertainties": ["Any debt where collateral type is unclear"]
}}

CRITICAL:
- You MUST return a mapping for EVERY secured debt instrument in the list
- If the name contains "Asset-backed", it IS secured - figure out by what
- Use exact debt names from the list provided
- Return valid JSON only"""


# =============================================================================
# MAIN EXTRACTION FUNCTION
# =============================================================================

async def extract_collateral_for_company(
    ticker: str,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict:
    """Extract collateral information for a company's secured debt."""
    settings = get_settings()

    stats = {
        "ticker": ticker,
        "secured_debt_count": 0,
        "documents_analyzed": 0,
        "collateral_created": 0,
        "debt_updated": 0,
        "errors": [],
    }

    # Connect to database
    url = settings.database_url.replace('postgresql://', 'postgresql+asyncpg://', 1)
    engine = create_async_engine(url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        # Get company
        company_result = await db.execute(
            select(Company).where(Company.ticker == ticker.upper())
        )
        company = company_result.scalar_one_or_none()
        if not company:
            print(f"Company not found: {ticker}")
            return stats

        # Get secured debt instruments
        debt_result = await db.execute(
            select(DebtInstrument)
            .where(DebtInstrument.company_id == company.id)
            .where(DebtInstrument.is_active == True)
            .where(DebtInstrument.seniority == 'senior_secured')
        )
        secured_debt = debt_result.scalars().all()
        stats["secured_debt_count"] = len(secured_debt)

        if not secured_debt:
            print(f"No secured debt found for {ticker}")
            return stats

        print(f"Found {len(secured_debt)} secured debt instruments for {ticker}")

        # Get relevant documents
        docs_result = await db.execute(
            select(DocumentSection)
            .where(DocumentSection.company_id == company.id)
            .where(DocumentSection.section_type.in_([
                'indenture', 'credit_agreement', 'debt_footnote', 'mda_liquidity'
            ]))
            .order_by(DocumentSection.filing_date.desc())
        )
        documents = docs_result.scalars().all()
        stats["documents_analyzed"] = len(documents)

        if not documents:
            print(f"No relevant documents found for {ticker}")
            return stats

        print(f"Found {len(documents)} documents to analyze")

        # Build debt list for prompt
        debt_list = "\n".join([
            f"- {d.name} ({d.instrument_type}, {d.seniority})"
            for d in secured_debt
        ])

        # Build document excerpts (truncate to fit context)
        doc_excerpts = []
        total_chars = 0
        max_chars = 60000  # Leave room for prompt and response

        for doc in documents:
            if total_chars + len(doc.content) > max_chars:
                # Truncate this document
                remaining = max_chars - total_chars
                if remaining > 1000:
                    doc_excerpts.append(f"=== {doc.section_type} ({doc.doc_type}) ===\n{doc.content[:remaining]}...")
                break
            doc_excerpts.append(f"=== {doc.section_type} ({doc.doc_type}) ===\n{doc.content}")
            total_chars += len(doc.content)

        documents_text = "\n\n".join(doc_excerpts)

        # Call LLM
        print("Calling Gemini for collateral extraction...")
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            generation_config={
                "temperature": 0.1,
                "response_mime_type": "application/json",
                "max_output_tokens": 8000,
            },
        )

        prompt = EXTRACT_COLLATERAL_PROMPT.format(
            debt_list=debt_list,
            documents=documents_text,
        )
        response = model.generate_content(prompt)

        data = parse_json_robust(response.text)
        if not data:
            print("  Failed to parse LLM response")
            stats["errors"].append("JSON parse error")
            return stats

        mappings = data.get("collateral_mappings", [])
        print(f"Extracted {len(mappings)} collateral mappings")

        if verbose:
            for m in mappings:
                collateral_types = [c.get("type") for c in m.get("collateral", [])]
                print(f"  {m.get('debt_name')}: {', '.join(collateral_types)}")

        # Build lookup for debt instruments
        debt_by_name = {}
        for d in secured_debt:
            debt_by_name[d.name.lower().strip()] = d
            # Also try partial matching
            name_words = d.name.lower().split()
            if len(name_words) >= 2:
                debt_by_name[" ".join(name_words[:3])] = d

        # Check for existing collateral
        existing_collateral = set()
        for d in secured_debt:
            coll_result = await db.execute(
                select(Collateral).where(Collateral.debt_instrument_id == d.id)
            )
            for c in coll_result.scalars().all():
                existing_collateral.add((d.id, c.collateral_type))

        # Process mappings
        new_collateral = []
        debt_ids_with_collateral = set()

        for mapping in mappings:
            debt_name = mapping.get("debt_name", "").lower().strip()
            collateral_items = mapping.get("collateral", [])

            # Find matching debt instrument
            debt = debt_by_name.get(debt_name)
            if not debt:
                # Try partial match
                for key, d in debt_by_name.items():
                    if debt_name in key or key in debt_name:
                        debt = d
                        break

            if not debt:
                if verbose:
                    print(f"  [SKIP] Debt not found: {mapping.get('debt_name')}")
                continue

            for coll in collateral_items:
                coll_type = coll.get("type")
                if not coll_type:
                    continue

                # Check if already exists
                if (debt.id, coll_type) in existing_collateral:
                    if verbose:
                        print(f"  [EXISTS] {debt.name} -> {coll_type}")
                    continue

                # Create collateral record
                collateral_record = Collateral(
                    debt_instrument_id=debt.id,
                    collateral_type=coll_type,
                    description=coll.get("description"),
                    priority=coll.get("priority"),
                    estimated_value=int(coll.get("estimated_value_usd", 0) * 100) if coll.get("estimated_value_usd") else None,
                )
                new_collateral.append(collateral_record)
                debt_ids_with_collateral.add(debt.id)
                existing_collateral.add((debt.id, coll_type))

                if verbose:
                    print(f"  [NEW] {debt.name} -> {coll_type}")

        stats["collateral_created"] = len(new_collateral)

        if not dry_run and new_collateral:
            # Add collateral records
            for coll in new_collateral:
                db.add(coll)

            # Update confidence level for debt with collateral
            for debt_id in debt_ids_with_collateral:
                await db.execute(
                    update(DebtInstrument)
                    .where(DebtInstrument.id == debt_id)
                    .values(collateral_data_confidence='extracted')
                )
                stats["debt_updated"] += 1

            await db.commit()
            print(f"\nCommitted {len(new_collateral)} new collateral records")
        else:
            print(f"\nDRY RUN - Would create {len(new_collateral)} collateral records")

    await engine.dispose()
    return stats


async def extract_collateral_batch(limit: int = None, dry_run: bool = False, verbose: bool = False):
    """Extract collateral for all companies with secured debt."""
    settings = get_settings()
    url = settings.database_url.replace('postgresql://', 'postgresql+asyncpg://', 1)
    engine = create_async_engine(url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Get companies with secured debt
    async with async_session() as db:
        result = await db.execute(text('''
            SELECT DISTINCT c.ticker
            FROM companies c
            JOIN debt_instruments d ON d.company_id = c.id
            WHERE d.seniority = 'senior_secured' AND d.is_active = true
            ORDER BY c.ticker
        '''))
        tickers = [row[0] for row in result.fetchall()]

    await engine.dispose()

    if limit:
        tickers = tickers[:limit]

    print(f"Processing {len(tickers)} companies with secured debt...")
    print("=" * 60)

    total_stats = {
        "companies_processed": 0,
        "collateral_created": 0,
        "errors": [],
    }

    for i, ticker in enumerate(tickers):
        print(f"\n[{i+1}/{len(tickers)}] {ticker}")
        try:
            stats = await extract_collateral_for_company(
                ticker=ticker,
                dry_run=dry_run,
                verbose=verbose,
            )
            total_stats["companies_processed"] += 1
            total_stats["collateral_created"] += stats["collateral_created"]
            if stats["errors"]:
                total_stats["errors"].extend([(ticker, e) for e in stats["errors"]])
        except Exception as e:
            print(f"  [ERROR] {e}")
            total_stats["errors"].append((ticker, str(e)))

        await asyncio.sleep(1)  # Rate limiting

    print("\n" + "=" * 60)
    print("BATCH EXTRACTION SUMMARY")
    print("=" * 60)
    print(f"Companies processed:   {total_stats['companies_processed']}")
    print(f"Collateral created:    {total_stats['collateral_created']}")
    if total_stats["errors"]:
        print(f"Errors ({len(total_stats['errors'])}):")
        for ticker, error in total_stats["errors"][:10]:
            print(f"  {ticker}: {error[:60]}")


async def main():
    parser = argparse.ArgumentParser(description="Extract collateral for secured debt")
    parser.add_argument("--ticker", help="Company ticker")
    parser.add_argument("--all", action="store_true", help="Process all companies with secured debt")
    parser.add_argument("--limit", type=int, help="Limit number of companies")
    parser.add_argument("--dry-run", action="store_true", help="Don't save changes")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    if args.ticker:
        stats = await extract_collateral_for_company(
            ticker=args.ticker,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
        print("\n" + "=" * 50)
        print("SUMMARY")
        print("=" * 50)
        print(f"Ticker:              {stats['ticker']}")
        print(f"Secured debt:        {stats['secured_debt_count']}")
        print(f"Documents analyzed:  {stats['documents_analyzed']}")
        print(f"Collateral created:  {stats['collateral_created']}")
    elif args.all:
        await extract_collateral_batch(
            limit=args.limit,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
