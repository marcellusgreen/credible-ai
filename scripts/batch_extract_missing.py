#!/usr/bin/env python3
"""
Batch extract debt data for companies missing debt instruments.

NOTE: Run with PYTHONUNBUFFERED=1 for real-time output.

Runs the full iterative extraction pipeline for each company:
1. Corporate structure (entities, hierarchy)
2. Debt instruments (bonds, loans, credit facilities)
3. Document sections (indentures, credit agreements, exhibits)
4. TTM financials (for leverage ratios)

Then runs:
5. Guarantee extraction (from Exhibit 22 and documents)
6. Collateral extraction (for secured debt)

Usage:
    python scripts/batch_extract_missing.py [--limit N] [--dry-run]
"""

import argparse
import asyncio

from sqlalchemy import text

from script_utils import get_db_session, print_header, run_async
from app.core.config import get_settings


async def get_missing_companies() -> list[tuple[str, str]]:
    """Get companies that have no active debt instruments."""
    async with get_db_session() as db:
        result = await db.execute(text('''
            SELECT c.ticker, c.cik
            FROM companies c
            WHERE c.cik IS NOT NULL
            AND NOT EXISTS (
                SELECT 1 FROM debt_instruments d
                WHERE d.company_id = c.id AND d.is_active = true
            )
            ORDER BY c.ticker
        '''))
        companies = [(row[0], row[1]) for row in result.fetchall()]

    return companies


async def run_extraction_for_company(ticker: str, cik: str, dry_run: bool = False) -> dict:
    """Run full extraction pipeline for a single company."""
    from scripts.extract_iterative import run_iterative_extraction
    from scripts.fetch_guarantor_subsidiaries import fetch_and_create_guarantors
    from scripts.extract_guarantees import extract_guarantees_for_company
    from scripts.extract_collateral import extract_collateral_for_company

    settings = get_settings()

    stats = {
        'ticker': ticker,
        'extraction_success': False,
        'debt_count': 0,
        'entity_count': 0,
        'guarantees_created': 0,
        'collateral_created': 0,
        'error': None,
    }

    if dry_run:
        print(f"  [DRY RUN] Would extract {ticker} (CIK: {cik})")
        return stats

    try:
        # Step 1: Run iterative extraction (entities, debt, documents, financials)
        print(f"  Step 1: Running iterative extraction...")
        result = await run_iterative_extraction(
            ticker=ticker,
            cik=cik,
            gemini_api_key=settings.gemini_api_key,
            anthropic_api_key=settings.anthropic_api_key,
            sec_api_key=settings.sec_api_key,
            quality_threshold=80.0,  # Lower threshold to capture more
            max_iterations=2,
            save_results=False,
            save_to_db=True,
            database_url=settings.database_url,
            skip_financials=False,
        )

        if result:
            stats['extraction_success'] = True
            # IterativeExtractionResult has .extraction dict with 'entities' and 'debt' keys
            extraction_data = getattr(result, 'extraction', {}) or {}
            if isinstance(extraction_data, dict):
                debt_list = extraction_data.get('debt', []) or []
                entity_list = extraction_data.get('entities', []) or []
            else:
                # Fallback for other formats
                debt_list = getattr(extraction_data, 'debt', []) or []
                entity_list = getattr(extraction_data, 'entities', []) or []
            stats['debt_count'] = len(debt_list) if debt_list else 0
            stats['entity_count'] = len(entity_list) if entity_list else 0
            print(f"    Extracted {stats['debt_count']} debt instruments, {stats['entity_count']} entities")

        # Step 2: Fetch Exhibit 22 and create guarantors
        print(f"  Step 2: Fetching Exhibit 22/21 for guarantors...")
        try:
            guar_stats = await fetch_and_create_guarantors(
                ticker=ticker,
                dry_run=False,
                verbose=False,
            )
            stats['guarantees_created'] += guar_stats.get('guarantees_created', 0)
            print(f"    Created {guar_stats.get('guarantees_created', 0)} guarantees from exhibits")
        except Exception as e:
            print(f"    [WARN] Exhibit fetch failed: {e}")

        # Step 3: Extract guarantees from documents
        print(f"  Step 3: Extracting guarantees from documents...")
        try:
            doc_stats = await extract_guarantees_for_company(
                ticker=ticker,
                dry_run=False,
                verbose=False,
            )
            stats['guarantees_created'] += doc_stats.get('new_guarantees_created', 0)
            print(f"    Created {doc_stats.get('new_guarantees_created', 0)} guarantees from documents")
        except Exception as e:
            print(f"    [WARN] Document guarantee extraction failed: {e}")

        # Step 4: Extract collateral for secured debt
        print(f"  Step 4: Extracting collateral information...")
        try:
            coll_stats = await extract_collateral_for_company(
                ticker=ticker,
                dry_run=False,
                verbose=False,
            )
            stats['collateral_created'] = coll_stats.get('collateral_created', 0)
            print(f"    Created {stats['collateral_created']} collateral records")
        except Exception as e:
            print(f"    [WARN] Collateral extraction failed: {e}")

    except Exception as e:
        stats['error'] = str(e)
        print(f"  [ERROR] {e}")

    return stats


async def batch_extract_missing(limit: int = None, dry_run: bool = False):
    """Run extraction for all companies missing debt data."""
    companies = await get_missing_companies()

    if limit:
        companies = companies[:limit]

    print_header("BATCH EXTRACT MISSING COMPANIES")
    print(f"Found {len(companies)} companies missing debt data")
    print("=" * 60)

    total_stats = {
        'processed': 0,
        'successful': 0,
        'total_debt': 0,
        'total_guarantees': 0,
        'total_collateral': 0,
        'errors': [],
    }

    for i, (ticker, cik) in enumerate(companies):
        print(f"\n[{i+1}/{len(companies)}] {ticker} (CIK: {cik})")

        stats = await run_extraction_for_company(ticker, cik, dry_run)

        total_stats['processed'] += 1
        if stats['extraction_success']:
            total_stats['successful'] += 1
            total_stats['total_debt'] += stats['debt_count']
            total_stats['total_guarantees'] += stats['guarantees_created']
            total_stats['total_collateral'] += stats['collateral_created']

        if stats['error']:
            total_stats['errors'].append((ticker, stats['error']))

        # Rate limiting delay
        if not dry_run:
            await asyncio.sleep(2)

    # Print summary
    print("\n" + "=" * 60)
    print("BATCH EXTRACTION SUMMARY")
    print("=" * 60)
    print(f"Companies processed:    {total_stats['processed']}")
    print(f"Successful extractions: {total_stats['successful']}")
    print(f"Total debt instruments: {total_stats['total_debt']}")
    print(f"Total guarantees:       {total_stats['total_guarantees']}")
    print(f"Total collateral:       {total_stats['total_collateral']}")

    if total_stats['errors']:
        print(f"\nErrors ({len(total_stats['errors'])}):")
        for ticker, error in total_stats['errors'][:10]:
            print(f"  {ticker}: {error[:80]}")


async def main():
    parser = argparse.ArgumentParser(description="Batch extract missing company debt data")
    parser.add_argument("--limit", type=int, help="Limit number of companies")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually extract")
    args = parser.parse_args()

    await batch_extract_missing(limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    run_async(main())
