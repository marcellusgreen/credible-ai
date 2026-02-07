#!/usr/bin/env python3
"""
Reclassify misclassified document sections.

Many documents classified as 'credit_agreement' are actually:
- Stock incentive plans
- Executive compensation agreements
- Severance plans
- Deferred compensation plans
- Stockholder agreements
- Indemnification agreements

This script reclassifies them to 'other' section_type.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from script_utils import get_db_session, print_header, run_async

# Patterns that indicate a document is NOT a credit agreement
MISCLASSIFIED_PATTERNS = [
    # Compensation/equity plans
    '%stock incentive%',
    '%equity incentive%',
    '%stock option%',
    '%performance share%',
    '%restricted stock%',
    '%executive officer%',
    '%compensation plan%',
    '%severance%',
    '%deferred compensation%',
    '%bonus plan%',
    '%incentive plan%',
    '%employee stock%',
    # Corporate agreements (not debt)
    '%stockholder agreement%',
    '%stockholders agreement%',
    '%shareholder agreement%',
    '%indemnification agreement%',
    '%voting agreement%',
    '%registration rights%',
    # Other non-debt documents
    '%limited liability company agreement%',
    '%llc agreement%',
    '%partnership agreement%',
    '%joint venture%',
]

# Patterns that confirm it IS a real credit agreement
REAL_CREDIT_AGREEMENT_PATTERNS = [
    '%revolving credit%',
    '%term loan%',
    '%credit facility%',
    '%borrower%lender%',
    '%commitment fee%',
    '%interest rate%libor%',
    '%interest rate%sofr%',
    '%maturity date%principal%',
    '%repayment%',
    '%covenant%',
]


async def analyze_misclassified(session) -> dict:
    """Analyze misclassified documents."""
    # Build the WHERE clause for misclassified patterns
    misclassified_conditions = " OR ".join([f"content ILIKE '{p}'" for p in MISCLASSIFIED_PATTERNS])

    # Count total credit agreements
    result = await session.execute(text("""
        SELECT COUNT(*) FROM document_sections WHERE section_type = 'credit_agreement'
    """))
    total = result.scalar()

    # Count misclassified
    result = await session.execute(text(f"""
        SELECT COUNT(*) FROM document_sections
        WHERE section_type = 'credit_agreement'
        AND ({misclassified_conditions})
    """))
    misclassified = result.scalar()

    # Sample misclassified
    result = await session.execute(text(f"""
        SELECT c.ticker, ds.section_title, LEFT(ds.content, 150) as preview
        FROM document_sections ds
        JOIN companies c ON c.id = ds.company_id
        WHERE ds.section_type = 'credit_agreement'
        AND ({misclassified_conditions})
        LIMIT 10
    """))
    samples = result.fetchall()

    return {
        'total': total,
        'misclassified': misclassified,
        'samples': samples
    }


async def reclassify_documents(session, dry_run: bool = True) -> int:
    """Reclassify misclassified credit agreements to 'other'."""
    # Build the WHERE clause for misclassified patterns
    misclassified_conditions = " OR ".join([f"content ILIKE '{p}'" for p in MISCLASSIFIED_PATTERNS])

    # Also exclude documents that have real credit agreement indicators
    real_ca_conditions = " OR ".join([f"content ILIKE '{p}'" for p in REAL_CREDIT_AGREEMENT_PATTERNS])

    if dry_run:
        # Count what would be updated
        result = await session.execute(text(f"""
            SELECT COUNT(*) FROM document_sections
            WHERE section_type = 'credit_agreement'
            AND ({misclassified_conditions})
            AND NOT ({real_ca_conditions})
        """))
        count = result.scalar()
        return count
    else:
        # Actually update
        result = await session.execute(text(f"""
            UPDATE document_sections
            SET section_type = 'other',
                updated_at = NOW()
            WHERE section_type = 'credit_agreement'
            AND ({misclassified_conditions})
            AND NOT ({real_ca_conditions})
        """))
        await session.commit()
        return result.rowcount


async def main():
    import argparse
    parser = argparse.ArgumentParser(description='Reclassify misclassified document sections')
    parser.add_argument('--dry-run', action='store_true', default=True,
                        help='Show what would be changed without making changes (default)')
    parser.add_argument('--execute', action='store_true',
                        help='Actually perform the reclassification')
    parser.add_argument('--analyze', action='store_true',
                        help='Show detailed analysis of misclassified documents')
    args = parser.parse_args()

    print_header("RECLASSIFY MISCLASSIFIED DOCUMENT SECTIONS")

    async with get_db_session() as session:

        if args.analyze:
            stats = await analyze_misclassified(session)
            print(f'\nTotal credit_agreement sections: {stats["total"]}')
            print(f'Potentially misclassified: {stats["misclassified"]}')
            print(f'\nSample misclassified documents:')
            for s in stats['samples']:
                title = s.section_title[:50] if s.section_title else 'N/A'
                title = title.encode('ascii', 'replace').decode('ascii')
                preview = s.preview[:100] if s.preview else ''
                preview = preview.encode('ascii', 'replace').decode('ascii').replace('\n', ' ')
                print(f'\n  {s.ticker}: {title}')
                print(f'    {preview}...')
            print()

        dry_run = not args.execute
        count = await reclassify_documents(session, dry_run=dry_run)

        if dry_run:
            print(f'\n[DRY RUN] Would reclassify {count} documents from credit_agreement to other')
            print('\nTo execute, run with --execute flag')
        else:
            print(f'\n[EXECUTED] Reclassified {count} documents from credit_agreement to other')

            # Show new totals
            result = await session.execute(text("""
                SELECT section_type, COUNT(*)
                FROM document_sections
                WHERE section_type IN ('credit_agreement', 'other')
                GROUP BY section_type
            """))
            for row in result.fetchall():
                print(f'  {row[0]}: {row[1]}')


if __name__ == "__main__":
    run_async(main())
