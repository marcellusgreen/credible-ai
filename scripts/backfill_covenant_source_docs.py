"""
Backfill source_document_id for existing covenants

For covenants missing source_document_id, find the most recent
credit_agreement or indenture for that company and link to it.

Usage:
    python scripts/backfill_covenant_source_docs.py --all
    python scripts/backfill_covenant_source_docs.py --ticker CHTR
    python scripts/backfill_covenant_source_docs.py --all --dry-run
"""

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, update, func, desc
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models import Company, Covenant, DocumentSection


async def get_primary_covenant_document(
    session: AsyncSession,
    company_id: UUID
) -> UUID | None:
    """
    Get the most recent credit_agreement or indenture for a company.
    """
    result = await session.execute(
        select(DocumentSection.id)
        .where(
            DocumentSection.company_id == company_id,
            DocumentSection.section_type.in_(['credit_agreement', 'indenture'])
        )
        .order_by(desc(DocumentSection.filing_date))
        .limit(1)
    )
    row = result.first()
    return row[0] if row else None


async def backfill_for_company(
    session: AsyncSession,
    company_id: UUID,
    ticker: str,
    dry_run: bool = False
) -> int:
    """
    Backfill source_document_id for covenants missing it.
    """
    # Get covenants without source_document_id
    result = await session.execute(
        select(func.count(Covenant.id))
        .where(
            Covenant.company_id == company_id,
            Covenant.source_document_id.is_(None)
        )
    )
    missing_count = result.scalar()

    if missing_count == 0:
        return 0

    # Get the primary document for this company
    doc_id = await get_primary_covenant_document(session, company_id)

    if doc_id is None:
        return 0

    if not dry_run:
        # Update all covenants for this company that are missing source_document_id
        await session.execute(
            update(Covenant)
            .where(
                Covenant.company_id == company_id,
                Covenant.source_document_id.is_(None)
            )
            .values(source_document_id=doc_id)
        )
        await session.commit()

    return missing_count


async def main():
    parser = argparse.ArgumentParser(description="Backfill covenant source documents")
    parser.add_argument("--ticker", help="Process single company")
    parser.add_argument("--all", action="store_true", help="Process all companies")
    parser.add_argument("--dry-run", action="store_true", help="Don't save changes")
    args = parser.parse_args()

    if not args.ticker and not args.all:
        print("Usage:")
        print("  python scripts/backfill_covenant_source_docs.py --all")
        print("  python scripts/backfill_covenant_source_docs.py --ticker CHTR")
        print("  python scripts/backfill_covenant_source_docs.py --all --dry-run")
        return

    settings = get_settings()
    engine = create_async_engine(
        settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
    )
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Get companies to process
    async with async_session() as db:
        if args.ticker:
            result = await db.execute(
                select(Company).where(Company.ticker == args.ticker.upper())
            )
            companies = [result.scalar_one_or_none()]
        else:
            # Only get companies that have covenants without source_document_id
            result = await db.execute(
                select(Company)
                .join(Covenant, Covenant.company_id == Company.id)
                .where(Covenant.source_document_id.is_(None))
                .group_by(Company.id)
                .order_by(Company.ticker)
            )
            companies = list(result.scalars())

    if args.dry_run:
        print("[DRY RUN] No changes will be saved\n")

    print(f"Processing {len(companies)} companies\n")

    total_updated = 0

    for company in companies:
        if not company:
            print("Company not found")
            continue

        async with async_session() as db:
            updated = await backfill_for_company(
                db, company.id, company.ticker, args.dry_run
            )

            if updated > 0:
                print(f"[{company.ticker}] Linked {updated} covenants to source document")
                total_updated += updated

    print(f"\n{'='*50}")
    print(f"Total covenants updated: {total_updated}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
