#!/usr/bin/env python3
"""
Batch extract guarantees for all companies.

Runs both:
1. fetch_guarantor_subsidiaries.py - Fetches Exhibit 22.1 from SEC, creates entities
2. extract_guarantees.py - Extracts guarantees from stored indentures/credit agreements

Usage:
    python scripts/batch_extract_guarantees.py [--limit N] [--skip-existing]
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings

# Import the extraction functions
from scripts.fetch_guarantor_subsidiaries import fetch_and_create_guarantors
from scripts.extract_guarantees import extract_guarantees_for_company


async def get_companies_to_process(db: AsyncSession, skip_existing: bool = False) -> list[dict]:
    """Get list of companies that need guarantee extraction."""

    query = '''
        WITH company_stats AS (
            SELECT
                c.ticker,
                (SELECT COUNT(*) FROM debt_instruments d WHERE d.company_id = c.id AND d.is_active = true) as total_debt,
                (SELECT COUNT(*) FROM debt_instruments d WHERE d.company_id = c.id AND d.is_active = true AND d.seniority = 'senior_secured') as secured_debt,
                (SELECT COUNT(DISTINCT g.debt_instrument_id)
                 FROM guarantees g
                 JOIN debt_instruments d ON g.debt_instrument_id = d.id
                 WHERE d.company_id = c.id) as with_guarantees,
                (SELECT COUNT(*) FROM document_sections ds WHERE ds.company_id = c.id AND ds.section_type IN ('indenture', 'credit_agreement')) as doc_count
            FROM companies c
        )
        SELECT ticker, total_debt, secured_debt, with_guarantees, doc_count
        FROM company_stats
        WHERE total_debt > 0
        AND doc_count > 0
    '''

    if skip_existing:
        # Only process companies with incomplete guarantee coverage
        query += ' AND (secured_debt > 0 AND with_guarantees < secured_debt)'

    query += ' ORDER BY secured_debt DESC, total_debt DESC'

    result = await db.execute(text(query))

    companies = []
    for row in result.fetchall():
        companies.append({
            'ticker': row[0],
            'total_debt': row[1],
            'secured_debt': row[2],
            'with_guarantees': row[3],
            'doc_count': row[4],
        })

    return companies


async def batch_extract_guarantees(
    limit: int = None,
    skip_existing: bool = False,
):
    """Run guarantee extraction for all companies."""
    settings = get_settings()
    url = settings.database_url.replace('postgresql://', 'postgresql+asyncpg://', 1)
    engine = create_async_engine(url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Get companies to process
    async with async_session() as db:
        companies = await get_companies_to_process(db, skip_existing)

    if limit:
        companies = companies[:limit]

    print(f"Processing {len(companies)} companies...")
    print("=" * 60)

    # Stats
    total_stats = {
        'companies_processed': 0,
        'exhibit_22_found': 0,
        'entities_created': 0,
        'guarantees_from_exhibit': 0,
        'guarantees_from_docs': 0,
        'errors': [],
    }

    for i, company in enumerate(companies):
        ticker = company['ticker']
        print(f"\n[{i+1}/{len(companies)}] {ticker} (debt: {company['total_debt']}, secured: {company['secured_debt']}, docs: {company['doc_count']})")

        try:
            # Step 1: Fetch Exhibit 22.1 and create entities
            print(f"  Fetching Exhibit 22.1...")
            ex22_stats = await fetch_and_create_guarantors(
                ticker=ticker,
                dry_run=False,
                verbose=False,
            )

            if ex22_stats['exhibit_22_found'] or ex22_stats['exhibit_21_found']:
                total_stats['exhibit_22_found'] += 1
            total_stats['entities_created'] += ex22_stats['entities_created']
            total_stats['guarantees_from_exhibit'] += ex22_stats['guarantees_created']

            print(f"    Exhibit: {ex22_stats['guarantors_parsed']} guarantors, {ex22_stats['entities_created']} new entities, {ex22_stats['guarantees_created']} guarantees")

            # Step 2: Extract guarantees from stored documents (indentures, credit agreements)
            print(f"  Extracting from documents...")
            doc_stats = await extract_guarantees_for_company(
                ticker=ticker,
                dry_run=False,
                verbose=False,
            )

            total_stats['guarantees_from_docs'] += doc_stats['new_guarantees_created']

            print(f"    Documents: {doc_stats['documents_analyzed']} docs analyzed, {doc_stats['new_guarantees_created']} new guarantees")

            total_stats['companies_processed'] += 1

        except Exception as e:
            print(f"  [ERROR] {e}")
            total_stats['errors'].append((ticker, str(e)))

        # Small delay to avoid rate limiting
        await asyncio.sleep(1)

    await engine.dispose()

    # Print summary
    print("\n" + "=" * 60)
    print("BATCH EXTRACTION SUMMARY")
    print("=" * 60)
    print(f"Companies processed:     {total_stats['companies_processed']}")
    print(f"Exhibit 22/21 found:     {total_stats['exhibit_22_found']}")
    print(f"Entities created:        {total_stats['entities_created']}")
    print(f"Guarantees (exhibit):    {total_stats['guarantees_from_exhibit']}")
    print(f"Guarantees (documents):  {total_stats['guarantees_from_docs']}")
    print(f"Total new guarantees:    {total_stats['guarantees_from_exhibit'] + total_stats['guarantees_from_docs']}")

    if total_stats['errors']:
        print(f"\nErrors ({len(total_stats['errors'])}):")
        for ticker, error in total_stats['errors'][:10]:
            print(f"  {ticker}: {error[:60]}")


async def main():
    parser = argparse.ArgumentParser(description="Batch extract guarantees for all companies")
    parser.add_argument("--limit", type=int, help="Limit number of companies to process")
    parser.add_argument("--skip-existing", action="store_true", help="Skip companies with full guarantee coverage")
    args = parser.parse_args()

    await batch_extract_guarantees(
        limit=args.limit,
        skip_existing=args.skip_existing,
    )


if __name__ == "__main__":
    asyncio.run(main())
