#!/usr/bin/env python3
"""
Reverse match unlinked documents to debt instruments.

This script takes the opposite approach from the normal matching:
- Starts from unlinked documents (indentures, credit agreements)
- Extracts identifiers (issuer name, dates, coupons, CUSIPs)
- Finds matching debt instruments

Usage:
    # Dry run to see what would be matched
    python scripts/reverse_match_documents.py --dry-run

    # Single company
    python scripts/reverse_match_documents.py --ticker CHTR

    # All companies
    python scripts/reverse_match_documents.py --all

    # Save matches to database
    python scripts/reverse_match_documents.py --all --save-db
"""

import argparse
import io
import sys
from collections import defaultdict

# Handle Windows encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from script_utils import get_db_session, print_header, run_async
from app.models import Company, DebtInstrument, DocumentSection, DebtInstrumentDocument
from app.services.document_matching import (
    match_unlinked_documents_to_instruments,
    store_reverse_match_links,
    BOND_TYPES,
    LOAN_TYPES,
)


async def main():
    parser = argparse.ArgumentParser(
        description="Reverse match unlinked documents to debt instruments"
    )
    parser.add_argument("--ticker", help="Single company ticker")
    parser.add_argument("--all", action="store_true", help="Process all companies")
    parser.add_argument("--dry-run", action="store_true", help="Don't save to database")
    parser.add_argument("--save-db", action="store_true", help="Save matches to database")
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.50,
        help="Minimum confidence to store link (default: 0.50)"
    )
    parser.add_argument("--limit", type=int, help="Max documents to process")
    args = parser.parse_args()

    if not any([args.ticker, args.all, args.dry_run]):
        parser.print_help()
        print("\nError: Specify --ticker, --all, or --dry-run")
        sys.exit(1)

    print_header("REVERSE DOCUMENT MATCHING")
    print(f"Min confidence: {args.min_confidence}")
    print(f"Save to DB: {args.save_db}")
    print()

    async with get_db_session() as session:
        # Get company ID if ticker specified
        company_id = None
        if args.ticker:
            result = await session.execute(
                select(Company).where(Company.ticker == args.ticker.upper())
            )
            company = result.scalar_one_or_none()
            if not company:
                print(f"Error: Company {args.ticker} not found")
                sys.exit(1)
            company_id = company.id
            print(f"Processing company: {args.ticker}")

        # Run reverse matching
        print("\nRunning reverse document matching...")
        result = await match_unlinked_documents_to_instruments(
            session,
            company_id=company_id,
            min_confidence=args.min_confidence,
        )

        print(f"\nResults:")
        print(f"  Total unlinked documents: {result['total_unlinked_docs']}")
        print(f"  Documents with matches:   {result['matched_docs']}")
        print(f"  New potential links:      {len(result['new_links'])}")
        print(f"  Still unmatched docs:     {len(result['unmatched_docs'])}")

        # Group new links by confidence level
        high_conf = [m for m in result['new_links'] if m.match_confidence >= 0.70]
        med_conf = [m for m in result['new_links'] if 0.50 <= m.match_confidence < 0.70]
        low_conf = [m for m in result['new_links'] if m.match_confidence < 0.50]

        print(f"\nBy confidence level:")
        print(f"  High (>=0.70):  {len(high_conf)}")
        print(f"  Medium (0.50-0.70): {len(med_conf)}")
        print(f"  Low (<0.50):    {len(low_conf)}")

        # Group by match method
        by_method = defaultdict(int)
        for m in result['new_links']:
            by_method[m.match_method] += 1

        print(f"\nBy match method:")
        for method, count in sorted(by_method.items(), key=lambda x: -x[1]):
            print(f"  {method}: {count}")

        # Show sample matches
        if result['new_links']:
            print(f"\n{'='*70}")
            print("SAMPLE MATCHES (first 20)")
            print("=" * 70)

            # Get document and instrument info for display
            doc_ids = {m.document_section_id for m in result['new_links'][:20]}
            inst_ids = {m.debt_instrument_id for m in result['new_links'][:20]}

            doc_result = await session.execute(
                select(DocumentSection).where(DocumentSection.id.in_(doc_ids))
            )
            docs_by_id = {d.id: d for d in doc_result.scalars().all()}

            inst_result = await session.execute(
                select(DebtInstrument).where(DebtInstrument.id.in_(inst_ids))
            )
            insts_by_id = {i.id: i for i in inst_result.scalars().all()}

            for m in result['new_links'][:20]:
                doc = docs_by_id.get(m.document_section_id)
                inst = insts_by_id.get(m.debt_instrument_id)
                if doc and inst:
                    doc_title = (doc.section_title or "No title")[:50]
                    inst_name = (inst.name or "No name")[:40]
                    print(f"\n  [{m.match_confidence:.2f}] {m.match_method}")
                    print(f"    Doc:  {doc_title}")
                    print(f"    Inst: {inst_name}")
                    if m.match_evidence.get('signals'):
                        sig = m.match_evidence['signals'][0]
                        print(f"    Signal: {sig.get('type')} - {sig.get('doc_value', '')[:50]}")

        # Save to database if requested
        if args.save_db and not args.dry_run:
            print(f"\n{'='*70}")
            print("SAVING TO DATABASE")
            print("=" * 70)

            # Filter to only store high-confidence matches
            matches_to_store = [m for m in result['new_links'] if m.match_confidence >= args.min_confidence]
            print(f"Storing {len(matches_to_store)} matches with confidence >= {args.min_confidence}")

            created = await store_reverse_match_links(
                session,
                matches_to_store,
                created_by="reverse_algorithm",
                skip_existing=True,
            )

            print(f"Created {created} new document links")

        # Show remaining unmatched document stats
        if result['unmatched_docs']:
            print(f"\n{'='*70}")
            print("UNMATCHED DOCUMENTS ANALYSIS")
            print("=" * 70)

            # Get details of unmatched docs
            unmatched_result = await session.execute(
                select(DocumentSection)
                .options(selectinload(DocumentSection.company))
                .where(DocumentSection.id.in_(result['unmatched_docs'][:100]))
            )
            unmatched_docs = list(unmatched_result.scalars().all())

            by_type = defaultdict(int)
            by_company = defaultdict(int)
            for d in unmatched_docs:
                by_type[d.section_type] += 1
                ticker = d.company.ticker if d.company else "UNKNOWN"
                by_company[ticker] += 1

            print(f"\nUnmatched by document type:")
            for dtype, count in sorted(by_type.items(), key=lambda x: -x[1]):
                print(f"  {dtype}: {count}")

            print(f"\nCompanies with most unmatched docs (top 15):")
            for ticker, count in sorted(by_company.items(), key=lambda x: -x[1])[:15]:
                print(f"  {ticker}: {count}")


if __name__ == "__main__":
    run_async(main())
