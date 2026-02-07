#!/usr/bin/env python3
"""
Clean up matured debt instruments and related documents.

This script:
1. Marks debt instruments with maturity_date <= cutoff as inactive
2. Deletes document links for matured instruments
3. Optionally deletes documents that only reference historical maturity years

Usage:
    # Dry run to see what would be cleaned up
    python scripts/cleanup_matured_debt.py --dry-run

    # Execute cleanup (mark instruments inactive, delete links)
    python scripts/cleanup_matured_debt.py --execute

    # Also delete historical documents
    python scripts/cleanup_matured_debt.py --execute --delete-docs

    # Custom cutoff date (default: 2025-12-31)
    python scripts/cleanup_matured_debt.py --cutoff 2024-12-31 --dry-run
"""

import argparse
import sys
from datetime import date

from sqlalchemy import select, update, delete, func

from script_utils import get_db_session, print_header, run_async
from app.models import DebtInstrument, DebtInstrumentDocument, DocumentSection
from app.services.document_matching import extract_maturity_years_from_text


async def analyze_matured_data(session, cutoff_date: date) -> dict:
    """Analyze what would be cleaned up."""

    # Count matured instruments
    result = await session.execute(
        select(DebtInstrument)
        .where(DebtInstrument.maturity_date <= cutoff_date)
        .where(DebtInstrument.maturity_date.isnot(None))
    )
    matured_instruments = list(result.scalars().all())

    matured_active = [i for i in matured_instruments if i.is_active]
    matured_inactive = [i for i in matured_instruments if not i.is_active]

    # Count links to matured instruments
    matured_ids = [i.id for i in matured_instruments]
    result = await session.execute(
        select(func.count(DebtInstrumentDocument.id))
        .where(DebtInstrumentDocument.debt_instrument_id.in_(matured_ids))
    )
    links_to_matured = result.scalar() or 0

    # Get linked document IDs
    result = await session.execute(
        select(DebtInstrumentDocument.document_section_id).distinct()
    )
    linked_doc_ids = {row[0] for row in result.fetchall()}

    # Analyze unlinked documents
    result = await session.execute(
        select(DocumentSection)
        .where(DocumentSection.section_type.in_(['indenture', 'credit_agreement']))
    )
    all_docs = list(result.scalars().all())
    unlinked_docs = [d for d in all_docs if d.id not in linked_doc_ids]

    # Categorize unlinked docs by maturity years
    only_historical = []
    has_future = []
    no_years = []

    cutoff_year = cutoff_date.year

    for doc in unlinked_docs:
        content = (doc.content or '')[:30000]
        title = doc.section_title or ''
        years = set(extract_maturity_years_from_text(title + ' ' + content))

        if not years:
            no_years.append(doc)
        else:
            future_years = [y for y in years if y > cutoff_year]
            if future_years:
                has_future.append(doc)
            else:
                only_historical.append(doc)

    return {
        'matured_instruments': matured_instruments,
        'matured_active': matured_active,
        'matured_inactive': matured_inactive,
        'links_to_matured': links_to_matured,
        'unlinked_docs': unlinked_docs,
        'only_historical_docs': only_historical,
        'has_future_docs': has_future,
        'no_years_docs': no_years,
    }


async def execute_cleanup(
    session,
    cutoff_date: date,
    delete_docs: bool = False,
    dry_run: bool = True,
) -> dict:
    """Execute the cleanup."""

    analysis = await analyze_matured_data(session, cutoff_date)

    results = {
        'instruments_deactivated': 0,
        'links_deleted': 0,
        'docs_deleted': 0,
    }

    if dry_run:
        return results

    # 1. Mark matured instruments as inactive
    matured_active_ids = [i.id for i in analysis['matured_active']]
    if matured_active_ids:
        await session.execute(
            update(DebtInstrument)
            .where(DebtInstrument.id.in_(matured_active_ids))
            .values(is_active=False)
        )
        results['instruments_deactivated'] = len(matured_active_ids)

    # 2. Delete links to matured instruments
    matured_ids = [i.id for i in analysis['matured_instruments']]
    if matured_ids:
        result = await session.execute(
            delete(DebtInstrumentDocument)
            .where(DebtInstrumentDocument.debt_instrument_id.in_(matured_ids))
        )
        results['links_deleted'] = result.rowcount

    # 3. Optionally delete historical-only documents
    if delete_docs and analysis['only_historical_docs']:
        historical_doc_ids = [d.id for d in analysis['only_historical_docs']]

        # First delete any links to these documents (shouldn't be any, but just in case)
        await session.execute(
            delete(DebtInstrumentDocument)
            .where(DebtInstrumentDocument.document_section_id.in_(historical_doc_ids))
        )

        # Then delete the documents
        result = await session.execute(
            delete(DocumentSection)
            .where(DocumentSection.id.in_(historical_doc_ids))
        )
        results['docs_deleted'] = result.rowcount

    await session.commit()

    return results


async def main():
    parser = argparse.ArgumentParser(
        description="Clean up matured debt instruments and related documents"
    )
    parser.add_argument(
        "--cutoff",
        type=str,
        default="2025-12-31",
        help="Cutoff date for maturity (YYYY-MM-DD). Instruments maturing on or before this date are considered matured."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be cleaned up without making changes"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the cleanup"
    )
    parser.add_argument(
        "--delete-docs",
        action="store_true",
        help="Also delete documents that only reference historical maturity years"
    )
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.print_help()
        print("\nError: Specify --dry-run or --execute")
        sys.exit(1)

    # Parse cutoff date
    try:
        parts = args.cutoff.split('-')
        cutoff_date = date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        print(f"Error: Invalid date format: {args.cutoff}. Use YYYY-MM-DD")
        sys.exit(1)

    print_header("MATURED DEBT CLEANUP")
    print(f"Cutoff date: {cutoff_date}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'EXECUTE'}")
    print(f"Delete historical docs: {args.delete_docs}")
    print()

    async with get_db_session() as session:
        # Analyze
        print("Analyzing data...")
        analysis = await analyze_matured_data(session, cutoff_date)

        print(f"\n{'='*70}")
        print("ANALYSIS RESULTS")
        print("=" * 70)

        print(f"\nDEBT INSTRUMENTS (maturity <= {cutoff_date}):")
        print(f"  Total matured:        {len(analysis['matured_instruments'])}")
        print(f"  Currently active:     {len(analysis['matured_active'])} <- will be deactivated")
        print(f"  Already inactive:     {len(analysis['matured_inactive'])}")
        print(f"  Links to matured:     {analysis['links_to_matured']} <- will be deleted")

        if analysis['matured_active']:
            print(f"\n  Sample matured active instruments:")
            for inst in analysis['matured_active'][:5]:
                print(f"    {inst.maturity_date} | {(inst.name or 'No name')[:50]}")

        print(f"\nUNLINKED DOCUMENTS:")
        print(f"  Total unlinked:           {len(analysis['unlinked_docs'])}")
        print(f"  Only historical (<=2025): {len(analysis['only_historical_docs'])} <- can delete")
        print(f"  Has future years (>2025): {len(analysis['has_future_docs'])} <- keep")
        print(f"  No years found:           {len(analysis['no_years_docs'])} <- keep (may be base indentures)")

        if args.delete_docs:
            hist_indentures = [d for d in analysis['only_historical_docs'] if d.section_type == 'indenture']
            hist_credit = [d for d in analysis['only_historical_docs'] if d.section_type == 'credit_agreement']
            print(f"\n  Historical docs to delete:")
            print(f"    Indentures:        {len(hist_indentures)}")
            print(f"    Credit agreements: {len(hist_credit)}")

        # Execute if requested
        if args.execute:
            print(f"\n{'='*70}")
            print("EXECUTING CLEANUP")
            print("=" * 70)

            results = await execute_cleanup(
                session,
                cutoff_date,
                delete_docs=args.delete_docs,
                dry_run=False,
            )

            print(f"\nResults:")
            print(f"  Instruments deactivated: {results['instruments_deactivated']}")
            print(f"  Document links deleted:  {results['links_deleted']}")
            print(f"  Documents deleted:       {results['docs_deleted']}")

            # Verify final state
            result = await session.execute(
                select(func.count(DebtInstrument.id))
                .where(DebtInstrument.is_active == True)
            )
            active_instruments = result.scalar()

            result = await session.execute(
                select(func.count(DocumentSection.id))
                .where(DocumentSection.section_type.in_(['indenture', 'credit_agreement']))
            )
            total_docs = result.scalar()

            print(f"\nFinal state:")
            print(f"  Active instruments:  {active_instruments}")
            print(f"  Legal documents:     {total_docs}")
        else:
            print(f"\n{'='*70}")
            print("DRY RUN - No changes made")
            print("=" * 70)
            print(f"\nTo execute, run with --execute flag")
            if not args.delete_docs:
                print("To also delete historical documents, add --delete-docs flag")


if __name__ == "__main__":
    run_async(main())
