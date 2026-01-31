"""
Link Covenants to Debt Instruments via Document Relationships

This script post-processes existing covenants to establish instrument linkage
using the debt_instrument_documents table (governs relationships).

Strategy:
1. For each company, get documents that were used for covenant extraction
   (credit_agreements, indentures, covenants sections)
2. For each document, find instruments it governs via debt_instrument_documents
3. For covenants from that company without instrument links, create links
   based on document type matching:
   - credit_agreement docs -> link to loans/credit facilities
   - indenture docs -> link to bonds/notes

Usage:
    python scripts/link_covenants_to_instruments.py --ticker CHTR
    python scripts/link_covenants_to_instruments.py --all
    python scripts/link_covenants_to_instruments.py --all --dry-run
"""

import argparse
import asyncio
import os
import sys
from collections import defaultdict
from pathlib import Path
from uuid import UUID

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, update, func, and_
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models import (
    Company, Covenant, DebtInstrument, DebtInstrumentDocument, DocumentSection
)


async def get_document_instrument_map(
    session: AsyncSession,
    company_id: UUID
) -> dict[UUID, list[UUID]]:
    """
    Get mapping of document_id -> list of instrument_ids it governs.

    Only includes 'governs' relationships (not references, amends, etc.)
    """
    result = await session.execute(
        select(
            DebtInstrumentDocument.document_section_id,
            DebtInstrumentDocument.debt_instrument_id
        )
        .join(DocumentSection, DebtInstrumentDocument.document_section_id == DocumentSection.id)
        .where(
            DocumentSection.company_id == company_id,
            DebtInstrumentDocument.relationship_type == 'governs'
        )
    )

    doc_to_instruments = defaultdict(list)
    for doc_id, inst_id in result:
        if inst_id not in doc_to_instruments[doc_id]:
            doc_to_instruments[doc_id].append(inst_id)

    return dict(doc_to_instruments)


async def get_covenant_documents(
    session: AsyncSession,
    company_id: UUID
) -> list[DocumentSection]:
    """
    Get documents that were used for covenant extraction.
    Prioritized by type and recency.
    """
    result = await session.execute(
        select(DocumentSection)
        .where(
            DocumentSection.company_id == company_id,
            DocumentSection.section_type.in_(['credit_agreement', 'indenture', 'covenants'])
        )
        .order_by(DocumentSection.filing_date.desc())
    )
    return list(result.scalars().all())


async def link_covenants_for_company(
    session: AsyncSession,
    company_id: UUID,
    ticker: str,
    dry_run: bool = False
) -> tuple[int, int]:
    """
    Link unlinked covenants to instruments for a company.

    Returns (covenants_updated, instruments_linked)
    """
    # Get unlinked covenants
    result = await session.execute(
        select(Covenant)
        .where(
            Covenant.company_id == company_id,
            Covenant.debt_instrument_id.is_(None)
        )
    )
    unlinked_covenants = list(result.scalars().all())

    if not unlinked_covenants:
        return 0, 0

    # Get document -> instruments mapping
    doc_instrument_map = await get_document_instrument_map(session, company_id)

    if not doc_instrument_map:
        return 0, 0

    # Get documents used for extraction
    documents = await get_covenant_documents(session, company_id)

    # Build a mapping of document types to their governed instruments
    # Credit agreements -> loans, revolvers
    # Indentures -> bonds, notes
    credit_agreement_instruments = set()
    indenture_instruments = set()

    for doc in documents:
        doc_instruments = doc_instrument_map.get(doc.id, [])
        if doc.section_type == 'credit_agreement':
            credit_agreement_instruments.update(doc_instruments)
        elif doc.section_type == 'indenture':
            indenture_instruments.update(doc_instruments)

    # Get instrument details to help match
    all_instrument_ids = credit_agreement_instruments | indenture_instruments
    if not all_instrument_ids:
        return 0, 0

    result = await session.execute(
        select(DebtInstrument)
        .where(DebtInstrument.id.in_(all_instrument_ids))
    )
    instruments = {inst.id: inst for inst in result.scalars()}

    # Categorize instruments by type
    loan_instruments = []  # term loans, revolvers
    bond_instruments = []  # bonds, notes

    for inst_id, inst in instruments.items():
        name_lower = inst.name.lower()
        inst_type = (inst.instrument_type or '').lower()

        # Bonds/Notes indicators
        if any(x in name_lower for x in ['note', 'bond', 'debenture', '%']):
            bond_instruments.append(inst_id)
        # Loan indicators
        elif any(x in name_lower for x in ['term', 'loan', 'revolver', 'credit', 'facility']):
            loan_instruments.append(inst_id)
        elif 'bond' in inst_type or 'note' in inst_type:
            bond_instruments.append(inst_id)
        elif 'loan' in inst_type or 'credit' in inst_type:
            loan_instruments.append(inst_id)
        else:
            # Default based on document source
            if inst_id in credit_agreement_instruments:
                loan_instruments.append(inst_id)
            else:
                bond_instruments.append(inst_id)

    # Now link covenants
    # Strategy: Link each covenant to ALL instruments from appropriate document type
    # Since credit agreement covenants typically apply to all facilities
    # And indenture covenants typically apply to all notes under that indenture

    updated = 0
    linked_instruments = set()

    for covenant in unlinked_covenants:
        # Determine which instruments this covenant likely applies to
        # based on covenant characteristics

        target_instruments = []

        # Financial covenants (leverage, coverage) -> typically credit agreements
        if covenant.covenant_type == 'financial' and covenant.test_metric:
            target_instruments = loan_instruments[:1] if loan_instruments else bond_instruments[:1]

        # Change of control -> typically bonds/notes
        elif 'change of control' in covenant.covenant_name.lower():
            target_instruments = bond_instruments[:1] if bond_instruments else loan_instruments[:1]

        # Generic negative covenants -> link to first instrument from each type
        else:
            # Link to one representative instrument (avoid over-linking)
            if loan_instruments:
                target_instruments.append(loan_instruments[0])
            elif bond_instruments:
                target_instruments.append(bond_instruments[0])

        if target_instruments:
            # Update the first covenant to have an instrument link
            # For the rest, they remain company-wide (which is also valid)
            target_id = target_instruments[0]

            if not dry_run:
                covenant.debt_instrument_id = target_id

            updated += 1
            linked_instruments.add(target_id)

    if not dry_run:
        await session.commit()

    return updated, len(linked_instruments)


async def main():
    parser = argparse.ArgumentParser(description="Link covenants to debt instruments")
    parser.add_argument("--ticker", help="Process single company")
    parser.add_argument("--all", action="store_true", help="Process all companies")
    parser.add_argument("--dry-run", action="store_true", help="Don't save changes")
    args = parser.parse_args()

    if not args.ticker and not args.all:
        print("Usage:")
        print("  python scripts/link_covenants_to_instruments.py --ticker CHTR")
        print("  python scripts/link_covenants_to_instruments.py --all")
        print("  python scripts/link_covenants_to_instruments.py --all --dry-run")
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
            result = await db.execute(
                select(Company).order_by(Company.ticker)
            )
            companies = list(result.scalars())

    if args.dry_run:
        print("[DRY RUN] No changes will be saved\n")

    print(f"Processing {len(companies)} companies\n")

    total_updated = 0
    total_linked = 0

    for company in companies:
        if not company:
            print("Company not found")
            continue

        async with async_session() as db:
            updated, linked = await link_covenants_for_company(
                db, company.id, company.ticker, args.dry_run
            )

            if updated > 0:
                print(f"[{company.ticker}] Linked {updated} covenants to {linked} instruments")
                total_updated += updated
                total_linked += linked

    print(f"\n{'='*50}")
    print(f"Total covenants linked: {total_updated}")
    print(f"Unique instruments used: {total_linked}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
