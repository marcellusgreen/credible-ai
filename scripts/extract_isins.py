#!/usr/bin/env python3
"""
Extract ISINs from SEC filings and update debt instruments.

ISINs are typically found in:
- FWP (Free Writing Prospectus) filings
- 424B2/424B5 (Prospectus supplements)

US ISIN format: US + 9-char CUSIP + 1 check digit
Example: US14987EAB39 -> CUSIP: 14987EAB3

Usage:
    python scripts/extract_isins.py --ticker CHTR          # Single company
    python scripts/extract_isins.py --all --limit 20       # Multiple companies
    python scripts/extract_isins.py --ticker CHTR --dry-run # Preview only
"""

import argparse
import asyncio
import os
import re
import sys
from datetime import date

from dotenv import load_dotenv

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models import Company, DebtInstrument
from app.services.extraction import SecApiClient

settings = get_settings()


def extract_cusip_from_isin(isin: str) -> str | None:
    """
    Extract CUSIP from a US ISIN.

    US ISIN format: US + 9-digit CUSIP + 1 check digit
    Example: US14987EAB39 -> 14987EAB3
    """
    if not isin:
        return None

    isin = isin.strip().upper()

    # Must be 12 characters starting with US
    if len(isin) != 12 or not isin.startswith("US"):
        return None

    # Extract CUSIP: characters 2-11 (skip US prefix and check digit)
    cusip = isin[2:11]

    # Validate CUSIP format (9 alphanumeric characters)
    if not re.match(r'^[0-9A-Z]{9}$', cusip):
        return None

    return cusip


def find_isins_in_text(text: str) -> list[str]:
    """Find all ISINs in text."""
    if not text:
        return []

    # Pattern for ISIN: 2 letter country code + 10 alphanumeric
    # Focus on US ISINs but capture others too
    pattern = r'\b([A-Z]{2}[0-9A-Z]{10})\b'

    matches = re.findall(pattern, text)

    # Filter to valid-looking ISINs (US ones are most useful)
    valid = []
    for m in matches:
        # Skip obvious non-ISINs (like INCORPORATED, etc)
        if m.startswith("US") or m.startswith("XS"):
            valid.append(m)

    return list(set(valid))


async def extract_isins_for_company(
    db: AsyncSession,
    sec_client: SecApiClient,
    company: Company,
    dry_run: bool = False,
) -> dict:
    """Extract ISINs for a company from SEC filings."""

    ticker = company.ticker
    results = {
        "ticker": ticker,
        "filings_checked": 0,
        "isins_found": [],
        "cusips_extracted": [],
        "bonds_updated": 0,
        "errors": [],
    }

    # Get company's debt instruments
    debt_result = await db.execute(
        select(DebtInstrument)
        .where(DebtInstrument.company_id == company.id)
        .where(DebtInstrument.cusip.is_(None))  # Only unmapped bonds
        .where(DebtInstrument.is_active == True)
    )
    bonds = list(debt_result.scalars().all())

    if not bonds:
        results["errors"].append("No unmapped bonds found")
        return results

    # Search FWP and prospectus filings for ISINs
    all_isins = []

    for form_type in ["FWP", "424B2", "424B5"]:
        try:
            filings = sec_client.get_filings_by_ticker(
                ticker=ticker,
                form_types=[form_type],
                max_filings=10,
            )

            for filing in filings:
                results["filings_checked"] += 1
                url = filing.get("linkToFilingDetails") or filing.get("linkToHtml")

                if not url:
                    continue

                try:
                    content = sec_client.get_filing_content(url)
                    if content:
                        isins = find_isins_in_text(content)
                        all_isins.extend(isins)
                except Exception as e:
                    results["errors"].append(f"Error reading {form_type}: {str(e)[:50]}")

        except Exception as e:
            results["errors"].append(f"Error fetching {form_type}: {str(e)[:50]}")

    # Deduplicate ISINs
    all_isins = list(set(all_isins))
    results["isins_found"] = all_isins

    # Extract CUSIPs from US ISINs
    cusip_isin_map = {}
    for isin in all_isins:
        cusip = extract_cusip_from_isin(isin)
        if cusip:
            cusip_isin_map[cusip] = isin
            results["cusips_extracted"].append(cusip)

    if not cusip_isin_map:
        return results

    # Try to match CUSIPs to our bonds
    # This is tricky without exact matching data
    # For now, we'll store all found CUSIPs and let manual review match them

    # If we have bonds and CUSIPs, try simple heuristic matching
    # based on similar naming or just assign sequentially for now

    # For a more sophisticated approach, we'd need to:
    # 1. Parse bond details from the filing text near the ISIN
    # 2. Match by coupon rate and maturity date

    # For now, just report what we found
    print(f"  Found {len(cusip_isin_map)} US CUSIPs for {len(bonds)} unmapped bonds")

    return results


async def main():
    parser = argparse.ArgumentParser(description="Extract ISINs from SEC filings")
    parser.add_argument("--ticker", help="Single ticker to process")
    parser.add_argument("--all", action="store_true", help="Process all companies")
    parser.add_argument("--limit", type=int, default=10, help="Max companies to process")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    args = parser.parse_args()

    # Initialize SEC API client
    api_key = os.environ.get("SEC_API_KEY")
    if not api_key:
        print("ERROR: SEC_API_KEY environment variable not set")
        return
    sec_client = SecApiClient(api_key)

    # Create async engine
    database_url = settings.database_url
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        # Get companies to process
        if args.ticker:
            result = await db.execute(
                select(Company).where(Company.ticker == args.ticker.upper())
            )
            companies = list(result.scalars().all())
            if not companies:
                print(f"Company {args.ticker} not found")
                return
        elif args.all:
            # Get companies with unmapped bonds
            result = await db.execute(
                select(Company)
                .join(DebtInstrument)
                .where(DebtInstrument.cusip.is_(None))
                .where(DebtInstrument.is_active == True)
                .distinct()
                .limit(args.limit)
            )
            companies = list(result.scalars().all())
        else:
            parser.print_help()
            return

        print(f"Processing {len(companies)} companies...")
        if args.dry_run:
            print("(DRY RUN - no changes will be saved)")
        print()

        total_isins = 0
        total_cusips = 0

        for company in companies:
            print(f"\n{'='*60}")
            print(f"{company.ticker}: {company.name[:40]}")
            print(f"{'='*60}")

            result = await extract_isins_for_company(
                db, sec_client, company, args.dry_run
            )

            print(f"  Filings checked: {result['filings_checked']}")
            print(f"  ISINs found: {len(result['isins_found'])}")
            for isin in result['isins_found'][:10]:
                cusip = extract_cusip_from_isin(isin)
                cusip_str = f" -> CUSIP: {cusip}" if cusip else " (non-US)"
                print(f"    {isin}{cusip_str}")

            if result['errors']:
                print(f"  Errors: {len(result['errors'])}")
                for err in result['errors'][:3]:
                    print(f"    - {err}")

            total_isins += len(result['isins_found'])
            total_cusips += len(result['cusips_extracted'])

        print(f"\n{'='*60}")
        print(f"SUMMARY")
        print(f"{'='*60}")
        print(f"Companies processed: {len(companies)}")
        print(f"Total ISINs found: {total_isins}")
        print(f"US CUSIPs extracted: {total_cusips}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
