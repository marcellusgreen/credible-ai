#!/usr/bin/env python3
"""
Backfill SEC Filing URLs for Existing Document Sections

This script populates the sec_filing_url field for document_sections
that are missing URLs, without re-extracting the content.

It works by:
1. Finding sections with NULL sec_filing_url
2. Looking up the company's SEC filings via SEC-API or EDGAR
3. Matching sections to filings by doc_type and filing_date
4. Updating the sec_filing_url field

Usage:
    python scripts/backfill_sec_urls.py --ticker CHTR
    python scripts/backfill_sec_urls.py --all --limit 10
    python scripts/backfill_sec_urls.py --all --dry-run
"""

import argparse
import asyncio
import os
import sys
from datetime import date
from collections import defaultdict

from dotenv import load_dotenv
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import Company, DocumentSection
from app.services.sec_client import SecApiClient, SECEdgarClient

load_dotenv()


async def get_filing_urls_for_company(
    ticker: str,
    cik: str,
    sec_api_key: str = None,
) -> dict[str, str]:
    """
    Get a mapping of filing keys to SEC URLs.

    Returns dict like:
        {
            "10-K_2024-02-15": "https://www.sec.gov/...",
            "indenture_2024-01-10_EX-4_1": "https://www.sec.gov/...",
        }
    """
    filing_urls = {}

    if sec_api_key:
        try:
            client = SecApiClient(sec_api_key)
            _, filing_urls = await client.get_all_relevant_filings(ticker, cik=cik)
        except Exception as e:
            print(f"    SEC-API error: {e}")

    if not filing_urls:
        try:
            edgar = SECEdgarClient()
            _, filing_urls = await edgar.get_all_relevant_filings(cik)
            await edgar.close()
        except Exception as e:
            print(f"    EDGAR error: {e}")

    return filing_urls


def match_section_to_url(
    section: DocumentSection,
    filing_urls: dict[str, str],
) -> str | None:
    """
    Try to match a document section to its SEC filing URL.

    Matching strategy (prioritized):
    1. For exhibit-based sections (exhibit_21, indenture, credit_agreement),
       look for exhibit-specific URLs first
    2. Direct match: doc_type + date (e.g., "10-K_2024-02-15")
    3. Fuzzy date match within 7 days
    4. Any URL with matching doc_type (last resort)
    """
    filing_date_str = section.filing_date.isoformat() if section.filing_date else None
    if not filing_date_str:
        return None

    doc_type = section.doc_type or ""
    section_type = section.section_type or ""

    # Strategy 0: For exhibit-based section types, look for exhibit-specific URLs FIRST
    # This ensures indentures get indenture URLs, not 8-K URLs
    section_to_prefix = {
        "exhibit_21": "exhibit_21_",
        "exhibit_22": "exhibit_22_",
        "indenture": "indenture_",
        "credit_agreement": "credit_agreement_",
    }

    prefix = section_to_prefix.get(section_type)
    if prefix:
        # Look for exhibit-specific URL matching this date
        for key, url in filing_urls.items():
            if key.startswith(prefix) and filing_date_str in key:
                return url
        # Also try fuzzy date match for exhibits (within 7 days)
        section_date = section.filing_date
        for key, url in filing_urls.items():
            if key.startswith(prefix):
                try:
                    # Extract date from key
                    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', key)
                    if date_match:
                        key_date = date.fromisoformat(date_match.group(1))
                        if abs((key_date - section_date).days) <= 7:
                            return url
                except (ValueError, IndexError):
                    continue

    # Strategy 1: Direct key match (doc_type + date)
    direct_key = f"{doc_type}_{filing_date_str}"
    if direct_key in filing_urls:
        return filing_urls[direct_key]

    # Strategy 2: Try common variations
    # Some keys might have form type variations like "10-K/A"
    for key, url in filing_urls.items():
        if key.startswith(f"{doc_type}_") and filing_date_str in key:
            return url
        # Also try without the /A suffix
        if doc_type.endswith("/A"):
            base_type = doc_type.replace("/A", "")
            if key.startswith(f"{base_type}_") and filing_date_str in key:
                return url

    # Strategy 3: Fuzzy date match (within 7 days)
    section_date = section.filing_date
    for key, url in filing_urls.items():
        # Check if this key is for the same form type
        if not key.startswith(doc_type):
            continue
        try:
            # Extract date from key (format: "10-K_2024-02-15")
            parts = key.split("_")
            if len(parts) >= 2:
                key_date_str = parts[1][:10]  # Take first 10 chars in case of extra suffix
                if len(key_date_str) == 10 and key_date_str[4] == "-":
                    key_date = date.fromisoformat(key_date_str)
                    if abs((key_date - section_date).days) <= 7:
                        return url
        except (ValueError, IndexError):
            continue

    # Strategy 4: Any URL with matching doc_type (last resort)
    for key, url in filing_urls.items():
        if key.startswith(f"{doc_type}_"):
            return url

    return None


async def backfill_company(
    session,
    company: Company,
    sec_api_key: str,
    dry_run: bool = False,
) -> dict:
    """Backfill SEC URLs for a single company."""
    ticker = company.ticker
    cik = company.cik

    if not cik:
        return {"ticker": ticker, "status": "skip", "reason": "no CIK"}

    # Get sections missing URLs
    query = (
        select(DocumentSection)
        .where(DocumentSection.company_id == company.id)
        .where(DocumentSection.sec_filing_url.is_(None))
    )
    result = await session.execute(query)
    sections = result.scalars().all()

    if not sections:
        return {"ticker": ticker, "status": "skip", "reason": "no sections missing URLs"}

    print(f"\n  {ticker}: {len(sections)} sections missing URLs")

    # Get filing URLs
    filing_urls = await get_filing_urls_for_company(ticker, cik, sec_api_key)

    if not filing_urls:
        return {"ticker": ticker, "status": "error", "reason": "could not fetch filing URLs"}

    print(f"    Found {len(filing_urls)} filing URLs")

    # Match sections to URLs
    matched = 0
    unmatched = 0
    updates = []

    for section in sections:
        url = match_section_to_url(section, filing_urls)
        if url:
            matched += 1
            updates.append({"id": section.id, "url": url})
        else:
            unmatched += 1

    print(f"    Matched: {matched}, Unmatched: {unmatched}")

    if dry_run:
        print(f"    [DRY RUN] Would update {len(updates)} sections")
        return {
            "ticker": ticker,
            "status": "dry_run",
            "matched": matched,
            "unmatched": unmatched,
        }

    # Apply updates
    for upd in updates:
        await session.execute(
            update(DocumentSection)
            .where(DocumentSection.id == upd["id"])
            .values(sec_filing_url=upd["url"])
        )

    await session.commit()

    print(f"    [OK] Updated {len(updates)} sections")

    return {
        "ticker": ticker,
        "status": "success",
        "matched": matched,
        "unmatched": unmatched,
    }


async def get_companies_with_missing_urls(session, limit: int = None):
    """Get companies that have sections missing SEC URLs."""
    # Subquery to find companies with NULL sec_filing_url sections
    subquery = (
        select(DocumentSection.company_id)
        .where(DocumentSection.sec_filing_url.is_(None))
        .distinct()
    )

    query = (
        select(Company)
        .where(Company.id.in_(subquery))
        .order_by(Company.ticker)
    )

    if limit:
        query = query.limit(limit)

    result = await session.execute(query)
    return result.scalars().all()


async def get_missing_url_stats(session):
    """Get statistics on missing URLs."""
    # Total sections
    total_query = select(func.count(DocumentSection.id))
    total_result = await session.execute(total_query)
    total = total_result.scalar()

    # Sections with URLs
    with_url_query = (
        select(func.count(DocumentSection.id))
        .where(DocumentSection.sec_filing_url.isnot(None))
    )
    with_url_result = await session.execute(with_url_query)
    with_url = with_url_result.scalar()

    # Sections without URLs
    without_url = total - with_url

    return {
        "total": total,
        "with_url": with_url,
        "without_url": without_url,
        "pct_with_url": (with_url / total * 100) if total > 0 else 0,
    }


async def main():
    parser = argparse.ArgumentParser(description="Backfill SEC filing URLs for document sections")
    parser.add_argument("--ticker", help="Process single company")
    parser.add_argument("--all", action="store_true", help="Process all companies with missing URLs")
    parser.add_argument("--limit", type=int, help="Limit number of companies to process")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually update database")
    parser.add_argument("--stats", action="store_true", help="Just show statistics")
    args = parser.parse_args()

    if not args.ticker and not args.all and not args.stats:
        parser.error("Must specify --ticker, --all, or --stats")

    # Get config
    database_url = os.getenv("DATABASE_URL", "")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    sec_api_key = os.getenv("SEC_API_KEY")

    if not database_url:
        print("Error: DATABASE_URL not set")
        sys.exit(1)

    # Connect to database with connection pooling settings for long-running operations
    engine = create_async_engine(
        database_url,
        echo=False,
        pool_pre_ping=True,
        pool_recycle=300,
    )
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    # Show stats
    if args.stats or args.all:
        async with async_session() as session:
            stats = await get_missing_url_stats(session)
        print(f"\nDocument Section URL Statistics:")
        print(f"  Total sections: {stats['total']:,}")
        print(f"  With URL: {stats['with_url']:,} ({stats['pct_with_url']:.1f}%)")
        print(f"  Missing URL: {stats['without_url']:,}")

        if args.stats:
            await engine.dispose()
            return

    results = []

    if args.ticker:
        # Single company
        async with async_session() as session:
            query = select(Company).where(Company.ticker == args.ticker.upper())
            result = await session.execute(query)
            company = result.scalar_one_or_none()

            if not company:
                print(f"Error: Company {args.ticker} not found")
                sys.exit(1)

            result = await backfill_company(session, company, sec_api_key, args.dry_run)
            results.append(result)

    elif args.all:
        # Get list of companies first
        async with async_session() as session:
            companies = await get_companies_with_missing_urls(session, args.limit)
        print(f"\nFound {len(companies)} companies with missing URLs")

        # Process each company with its own session to avoid timeout issues
        for i, company in enumerate(companies):
            try:
                async with async_session() as session:
                    result = await backfill_company(session, company, sec_api_key, args.dry_run)
                    results.append(result)
            except Exception as e:
                print(f"  {company.ticker}: Error - {e}")
                results.append({"ticker": company.ticker, "status": "error", "reason": str(e)[:100]})

            # Add delay to avoid rate limiting
            if i < len(companies) - 1:
                await asyncio.sleep(1)

    await engine.dispose()

    # Summary
    print(f"\n{'='*60}")
    print("Summary:")

    success = [r for r in results if r.get("status") == "success"]
    skipped = [r for r in results if r.get("status") == "skip"]
    errors = [r for r in results if r.get("status") == "error"]
    dry_runs = [r for r in results if r.get("status") == "dry_run"]

    total_matched = sum(r.get("matched", 0) for r in results)
    total_unmatched = sum(r.get("unmatched", 0) for r in results)

    if dry_runs:
        print(f"  Dry run: {len(dry_runs)} companies")
    if success:
        print(f"  Success: {len(success)} companies")
    if skipped:
        print(f"  Skipped: {len(skipped)} companies")
    if errors:
        print(f"  Errors: {len(errors)} companies")

    print(f"  Total matched: {total_matched}")
    print(f"  Total unmatched: {total_unmatched}")


if __name__ == "__main__":
    asyncio.run(main())
