#!/usr/bin/env python3
"""
Backfill document sections for full-text search.

Re-fetches SEC filings and extracts sections (debt footnotes, MD&A, etc.)
for existing companies in the database.

Usage:
    # Single company
    python scripts/backfill_document_sections.py --ticker CHTR

    # Batch of companies (top 20 by pricing data - good for testing)
    python scripts/backfill_document_sections.py --batch 20

    # All companies
    python scripts/backfill_document_sections.py --all

    # Dry run (don't save to database)
    python scripts/backfill_document_sections.py --ticker CHTR --dry-run

Environment variables:
    SEC_API_KEY - Required for faster filing retrieval
    DATABASE_URL - PostgreSQL connection string
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

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.core.config import get_settings
from app.models import Company, DocumentSection, BondPricing, DebtInstrument
from app.services.extraction import SecApiClient, SECEdgarClient
from app.services.section_extraction import (
    ExtractedSection,
    extract_sections_from_filing,
    store_sections,
    delete_company_sections,
    SECTION_TYPES,
)

settings = get_settings()


async def download_filings(ticker: str, cik: str, sec_api_key: str = None) -> dict[str, str]:
    """Download all relevant filings for a company."""
    filings = {}

    if sec_api_key:
        print(f"    Downloading via SEC-API...")
        sec_client = SecApiClient(sec_api_key)
        filings = await sec_client.get_all_relevant_filings(ticker, cik=cik)
        exhibit_21 = sec_client.get_exhibit_21(ticker)
        if exhibit_21:
            filings['exhibit_21'] = exhibit_21
    else:
        print(f"    Downloading via SEC EDGAR (slower)...")
        edgar = SECEdgarClient()
        filings = await edgar.get_all_relevant_filings(cik)
        await edgar.close()

    return filings


async def backfill_company(
    db,
    company: Company,
    sec_api_key: str,
    dry_run: bool = False,
) -> dict:
    """Backfill document sections for a single company."""
    ticker = company.ticker
    cik = company.cik

    if not cik:
        return {"ticker": ticker, "status": "skip", "reason": "no CIK"}

    print(f"\n  Processing {ticker} (CIK: {cik})...")

    # Download filings
    try:
        filings = await download_filings(ticker, cik, sec_api_key)
    except Exception as e:
        return {"ticker": ticker, "status": "error", "reason": f"download failed: {e}"}

    if not filings:
        return {"ticker": ticker, "status": "skip", "reason": "no filings found"}

    print(f"    Downloaded {len(filings)} filings")

    # Extract sections from each filing
    all_sections = []
    for key, content in filings.items():
        if not content or len(content) < 1000:
            continue

        # Skip PDF files (binary content that can't be stored as text)
        if content.startswith('%PDF') or b'%PDF' in content[:100].encode('utf-8', errors='ignore'):
            print(f"      Skipping PDF file: {key}")
            continue

        # Parse key formats:
        # - Simple: "10-K_2024-02-15", "10-Q_2024-05-10"
        # - Exhibit 21: "exhibit_21_2024-02-15"
        # - Credit agreement: "credit_agreement_2024-02-15_EX-10_1"
        # - Indenture: "indenture_2024-02-15_EX-4_1"

        # Try to extract date from the key (date is always in YYYY-MM-DD format)
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', key)
        if not date_match:
            continue

        date_str = date_match.group(1)

        # Parse date
        try:
            filing_date = date.fromisoformat(date_str)
        except ValueError:
            continue

        # For exhibit documents (credit agreements and indentures), store the entire
        # document as a section rather than trying to extract from it
        if key.startswith("credit_agreement"):
            # Extract a title from the beginning of the document
            title_match = re.search(r'(?i)((?:Amended\s+and\s+Restated\s+)?(?:Credit|Loan|Facility)\s+Agreement[^.]{0,100})', content[:2000])
            title = title_match.group(1).strip()[:250] if title_match else f"Credit Agreement ({key})"

            # Truncate content if too long (500K max for full documents)
            section_content = content[:500000]
            if len(content) > 500000:
                section_content += "\n\n[TRUNCATED]"

            section = ExtractedSection(
                section_type="credit_agreement",
                section_title=title,
                content=section_content,
                doc_type="8-K",
                filing_date=filing_date,
            )
            # Use ASCII-safe title for printing
            safe_title = title[:60].encode('ascii', 'replace').decode('ascii')
            print(f"      Found: credit_agreement ({len(section_content):,} chars) - {safe_title}...")
            all_sections.append(section)
            continue

        elif key.startswith("indenture"):
            # Extract a title from the beginning of the document
            title_match = re.search(r'(?i)((?:Supplemental\s+)?Indenture[^.]{0,150})', content[:3000])
            title = title_match.group(1).strip()[:250] if title_match else f"Indenture ({key})"

            # Truncate content if too long (500K max for full documents)
            section_content = content[:500000]
            if len(content) > 500000:
                section_content += "\n\n[TRUNCATED]"

            section = ExtractedSection(
                section_type="indenture",
                section_title=title,
                content=section_content,
                doc_type="8-K",
                filing_date=filing_date,
            )
            # Use ASCII-safe title for printing
            safe_title = title[:60].encode('ascii', 'replace').decode('ascii')
            print(f"      Found: indenture ({len(section_content):,} chars) - {safe_title}...")
            all_sections.append(section)
            continue

        # Determine doc_type for regular filings
        if key.startswith("exhibit_21"):
            doc_type = "10-K"
        elif key.startswith("10-K"):
            doc_type = "10-K"
        elif key.startswith("10-Q"):
            doc_type = "10-Q"
        elif key.startswith("8-K"):
            doc_type = "8-K"
        else:
            # Unknown key format, skip
            continue

        # Extract sections from regular filings using regex patterns
        sections = extract_sections_from_filing(
            content=content,
            doc_type=doc_type,
            filing_date=filing_date,
        )

        for section in sections:
            print(f"      Found: {section.section_type} ({len(section.content):,} chars)")
        all_sections.extend(sections)

    if not all_sections:
        return {"ticker": ticker, "status": "skip", "reason": "no sections extracted"}

    section_counts = {}
    for s in all_sections:
        section_counts[s.section_type] = section_counts.get(s.section_type, 0) + 1

    if dry_run:
        print(f"    [DRY RUN] Would store {len(all_sections)} sections: {section_counts}")
        return {
            "ticker": ticker,
            "status": "dry_run",
            "sections": len(all_sections),
            "by_type": section_counts,
        }

    # Delete existing sections for this company
    await delete_company_sections(db, company.id)

    # Store new sections
    stored = await store_sections(db, company.id, all_sections, replace_existing=False)

    print(f"    [OK] Stored {stored} sections: {section_counts}")

    return {
        "ticker": ticker,
        "status": "success",
        "sections": stored,
        "by_type": section_counts,
    }


async def get_companies_with_pricing(db, limit: int = None):
    """Get companies that have pricing data (good for testing)."""
    query = (
        select(Company)
        .join(DebtInstrument, DebtInstrument.company_id == Company.id)
        .join(BondPricing, BondPricing.debt_instrument_id == DebtInstrument.id)
        .where(BondPricing.last_price.isnot(None))
        .distinct()
        .order_by(Company.ticker)
    )
    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    return list(result.scalars().all())


async def get_all_companies(db):
    """Get all companies."""
    result = await db.execute(
        select(Company).order_by(Company.ticker)
    )
    return list(result.scalars().all())


async def get_company_by_ticker(db, ticker: str):
    """Get a single company by ticker."""
    result = await db.execute(
        select(Company).where(Company.ticker == ticker.upper())
    )
    return result.scalar_one_or_none()


async def main():
    parser = argparse.ArgumentParser(
        description="Backfill document sections for full-text search"
    )
    parser.add_argument("--ticker", help="Single company ticker")
    parser.add_argument("--batch", type=int, help="Batch size (companies with pricing data)")
    parser.add_argument("--all", action="store_true", help="Process all companies")
    parser.add_argument("--dry-run", action="store_true", help="Don't save to database")
    parser.add_argument("--offset", type=int, default=0, help="Skip first N companies")
    args = parser.parse_args()

    if not any([args.ticker, args.batch, args.all]):
        parser.print_help()
        print("\nError: Specify --ticker, --batch, or --all")
        sys.exit(1)

    sec_api_key = os.getenv("SEC_API_KEY")
    if not sec_api_key:
        print("Warning: SEC_API_KEY not set. Using slower SEC EDGAR fallback.")

    database_url = settings.database_url
    if not database_url:
        print("Error: DATABASE_URL not set")
        sys.exit(1)

    # Convert postgres:// to postgresql+asyncpg://
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif database_url.startswith("postgresql://") and "+asyncpg" not in database_url:
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(database_url, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as db:
        # Get companies to process
        if args.ticker:
            company = await get_company_by_ticker(db, args.ticker)
            if not company:
                print(f"Error: Company {args.ticker} not found")
                sys.exit(1)
            companies = [company]
        elif args.batch:
            companies = await get_companies_with_pricing(db, limit=args.batch)
            print(f"Found {len(companies)} companies with pricing data")
        else:  # --all
            companies = await get_all_companies(db)
            print(f"Found {len(companies)} total companies")

        # Apply offset
        if args.offset > 0:
            companies = companies[args.offset:]
            print(f"Skipping first {args.offset}, processing {len(companies)} remaining")

        print(f"\n{'='*60}")
        print(f"BACKFILL DOCUMENT SECTIONS")
        print(f"{'='*60}")
        print(f"Companies: {len(companies)}")
        print(f"Dry run: {args.dry_run}")
        print(f"SEC API: {'Yes' if sec_api_key else 'No (slow mode)'}")

    # Process companies with fresh sessions to avoid connection timeouts
    results = []
    for i, company in enumerate(companies):
        print(f"\n[{i+1}/{len(companies)}] {company.ticker}")

        # Create fresh session for each company to avoid connection timeout
        async with async_session() as company_db:
            try:
                result = await backfill_company(company_db, company, sec_api_key, args.dry_run)
            except Exception as e:
                # Use ASCII-safe error message to avoid encoding issues
                error_msg = str(e)[:200].encode('ascii', 'replace').decode('ascii')
                print(f"    [ERROR] {error_msg}")
                result = {"ticker": company.ticker, "status": "error", "reason": error_msg[:100]}
            results.append(result)

        # Rate limiting for SEC API
        if sec_api_key and i < len(companies) - 1:
            await asyncio.sleep(0.5)  # Be nice to SEC API

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")

    success = [r for r in results if r["status"] == "success"]
    skipped = [r for r in results if r["status"] == "skip"]
    errors = [r for r in results if r["status"] == "error"]
    dry_runs = [r for r in results if r["status"] == "dry_run"]

    print(f"Success: {len(success)}")
    print(f"Skipped: {len(skipped)}")
    print(f"Errors: {len(errors)}")
    if dry_runs:
        print(f"Dry run: {len(dry_runs)}")

    total_sections = sum(r.get("sections", 0) for r in success + dry_runs)
    print(f"\nTotal sections: {total_sections}")

    # Section type breakdown
    type_totals = {}
    for r in success + dry_runs:
        for stype, count in r.get("by_type", {}).items():
            type_totals[stype] = type_totals.get(stype, 0) + count

    if type_totals:
        print("\nBy section type:")
        for stype in sorted(type_totals.keys()):
            print(f"  {stype}: {type_totals[stype]}")

    if errors:
        print("\nErrors:")
        for r in errors:
            print(f"  {r['ticker']}: {r.get('reason', 'unknown')}")

    if skipped:
        print("\nSkipped:")
        for r in skipped:
            print(f"  {r['ticker']}: {r.get('reason', 'unknown')}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
