#!/usr/bin/env python3
"""
Re-extract document sections for specific companies.

This script fetches filings and re-runs section extraction with the
improved patterns.

Usage:
    python scripts/reextract_sections.py --ticker KDP
    python scripts/reextract_sections.py --tickers KDP,DO,KSS,MSFT,VRSK
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.services.extraction import SecApiClient, SECEdgarClient
from app.services.section_extraction import (
    extract_sections_from_filing,
    store_sections,
    SECTION_TYPES,
)


async def get_company_info(session: AsyncSession, ticker: str) -> dict:
    """Get company info from database."""
    result = await session.execute(
        text("SELECT id, ticker, cik, name FROM companies WHERE ticker = :ticker"),
        {"ticker": ticker}
    )
    row = result.fetchone()
    if not row:
        return {}
    return {"id": row[0], "ticker": row[1], "cik": row[2], "name": row[3]}


async def get_existing_sections(session: AsyncSession, company_id) -> dict:
    """Get existing section types for a company."""
    result = await session.execute(
        text("""
            SELECT section_type, COUNT(*) as cnt, MAX(filing_date) as latest
            FROM document_sections
            WHERE company_id = :company_id
            GROUP BY section_type
        """),
        {"company_id": company_id}
    )
    return {row[0]: {"count": row[1], "latest": row[2]} for row in result.fetchall()}


async def reextract_for_company(
    ticker: str,
    session: AsyncSession,
    sec_client: SecApiClient,
    edgar_client: SECEdgarClient,
) -> dict:
    """Re-extract sections for a single company."""

    print(f"\n{'='*60}")
    print(f"Re-extracting sections for {ticker}")
    print(f"{'='*60}")

    # Get company info
    company = await get_company_info(session, ticker)
    if not company:
        return {"ticker": ticker, "error": "Company not found"}

    print(f"  Company: {company['name']} (CIK: {company['cik']})")

    # Get existing sections
    existing = await get_existing_sections(session, company["id"])
    print(f"  Existing sections: {list(existing.keys())}")

    # Fetch filings
    print(f"  Fetching filings...")
    filings = {}

    try:
        if sec_client:
            filings = await sec_client.get_all_relevant_filings(ticker)
            exhibit_21 = sec_client.get_exhibit_21(ticker)
            if exhibit_21:
                filings["exhibit_21"] = exhibit_21
    except Exception as e:
        print(f"  SEC-API error: {e}")

    if not filings:
        try:
            filings = await edgar_client.get_all_relevant_filings(company["cik"])
        except Exception as e:
            print(f"  EDGAR error: {e}")

    if not filings:
        return {"ticker": ticker, "error": "No filings retrieved"}

    print(f"  Retrieved {len(filings)} filings: {list(filings.keys())[:5]}...")

    # Extract sections from each filing
    total_extracted = 0
    section_counts = {st: 0 for st in SECTION_TYPES}

    for key, content in filings.items():
        if not content or len(content) < 1000:
            continue

        # Determine doc_type and date
        import re
        from datetime import date

        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', key)
        if not date_match:
            continue

        date_str = date_match.group(1)
        filing_date = date.fromisoformat(date_str)

        if key.startswith("10-K"):
            doc_type = "10-K"
        elif key.startswith("10-Q"):
            doc_type = "10-Q"
        elif key.startswith("8-K"):
            doc_type = "8-K"
        elif key.startswith("exhibit_21"):
            doc_type = "10-K"
        else:
            continue

        # Extract sections
        sections = extract_sections_from_filing(
            content=content,
            doc_type=doc_type,
            filing_date=filing_date,
        )

        if sections:
            for s in sections:
                section_counts[s.section_type] += 1

            # Store sections
            count = await store_sections(session, company["id"], sections)
            total_extracted += count

    print(f"\n  Extraction results:")
    for section_type, count in section_counts.items():
        status = "NEW!" if count > 0 and section_type not in existing else ""
        if count > 0:
            print(f"    {section_type}: {count} {status}")

    # Check what's still missing
    missing = [st for st in SECTION_TYPES if section_counts[st] == 0 and st not in existing]
    if missing:
        print(f"\n  Still missing: {missing}")

    return {
        "ticker": ticker,
        "total_extracted": total_extracted,
        "section_counts": section_counts,
        "missing": missing,
    }


async def main():
    parser = argparse.ArgumentParser(description="Re-extract document sections")
    parser.add_argument("--ticker", help="Single ticker")
    parser.add_argument("--tickers", help="Comma-separated tickers")
    parser.add_argument("--dry-run", action="store_true", help="Don't store, just show what would be extracted")
    args = parser.parse_args()

    # Determine tickers
    if args.ticker:
        tickers = [args.ticker.upper()]
    elif args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
    else:
        # Default problem companies
        tickers = ["KDP", "DO", "KSS", "MSFT", "VRSK"]

    # Setup
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("Error: DATABASE_URL not set")
        sys.exit(1)

    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(database_url, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    sec_api_key = os.getenv("SEC_API_KEY")
    sec_client = SecApiClient(sec_api_key) if sec_api_key else None
    edgar_client = SECEdgarClient()

    # Process each ticker
    results = []
    for ticker in tickers:
        async with async_session() as session:
            result = await reextract_for_company(ticker, session, sec_client, edgar_client)
            results.append(result)

        await asyncio.sleep(1)  # Rate limiting

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")

    for r in results:
        if "error" in r:
            print(f"  {r['ticker']}: ERROR - {r['error']}")
        else:
            print(f"  {r['ticker']}: {r['total_extracted']} sections extracted")
            if r.get("missing"):
                print(f"    Still missing: {r['missing']}")

    await edgar_client.close()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
