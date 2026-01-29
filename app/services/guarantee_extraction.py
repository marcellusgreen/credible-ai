"""
Guarantee Extraction Service
============================

Extracts guarantee relationships from indentures and credit agreements.
Links debt instruments to their guarantor entities.

USAGE
-----
    from app.services.guarantee_extraction import extract_guarantees

    count = await extract_guarantees(session, company_id, ticker, filings)
"""

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entity, DebtInstrument, Guarantee, DocumentSection, DebtInstrumentDocument
from app.services.base_extractor import BaseExtractor, ExtractionContext
from app.services.llm_utils import LLMResponse


def _normalize_name(name: str) -> str:
    """Normalize entity name for matching."""
    if not name:
        return ""
    # Remove common suffixes and punctuation
    name = name.lower().strip()
    for suffix in [', inc.', ', inc', ' inc.', ' inc', ', llc', ' llc',
                   ', l.p.', ' l.p.', ', lp', ' lp', ', ltd.', ', ltd',
                   ' ltd.', ' ltd', ', corp.', ', corp', ' corp.', ' corp']:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    return name.replace(',', '').replace('.', '').strip()


def _fuzzy_match_entity(name: str, entity_map: dict, threshold: float = 0.85) -> Optional[UUID]:
    """
    Find entity ID by fuzzy name matching.

    Returns entity ID if match found, None otherwise.
    """
    if not name:
        return None

    normalized = _normalize_name(name)

    # Exact match first
    if normalized in entity_map:
        return entity_map[normalized]

    # Try original lowercase
    name_lower = name.lower().strip()
    if name_lower in entity_map:
        return entity_map[name_lower]

    # Fuzzy match
    best_ratio = 0
    best_match = None
    for key, entity_id in entity_map.items():
        ratio = SequenceMatcher(None, normalized, key).ratio()
        if ratio > best_ratio and ratio >= threshold:
            best_ratio = ratio
            best_match = entity_id

    return best_match


@dataclass
class ParsedGuarantee:
    """Parsed guarantee from LLM response."""
    debt_name: str
    guarantor_names: list[str]
    guarantee_type: str = "full"


class GuaranteeExtractor(BaseExtractor):
    """
    Extracts guarantee relationships from SEC filings.

    STEPS
    -----
    1. Load entities and debt instruments for company
    2. Get indenture/credit agreement documents
    3. Build prompt with entity and debt lists
    4. Parse LLM response for guarantee relationships
    5. Create Guarantee records linking debt to guarantors
    """

    async def load_context(self, context: ExtractionContext) -> ExtractionContext:
        """Load entities, instruments, and their linked documents."""
        session = context.session
        company_id = context.company_id

        # Load ALL entities (not limited - needed for guarantor matching)
        result = await session.execute(
            select(Entity).where(Entity.company_id == company_id)
        )
        context.entities = list(result.scalars().all())

        # Load debt instruments that need guarantee extraction
        # (either no guarantees yet, or unknown confidence)
        result = await session.execute(
            select(DebtInstrument).where(
                DebtInstrument.company_id == company_id,
                DebtInstrument.is_active == True,
                DebtInstrument.guarantee_data_confidence.in_(['unknown', None])
            )
        )
        context.instruments = list(result.scalars().all())

        if not context.instruments:
            # All instruments already have guarantees extracted
            return context

        # PRIORITY 1: Get documents linked to these specific instruments
        instrument_ids = [i.id for i in context.instruments]
        result = await session.execute(
            select(DocumentSection)
            .join(DebtInstrumentDocument, DebtInstrumentDocument.document_section_id == DocumentSection.id)
            .where(DebtInstrumentDocument.debt_instrument_id.in_(instrument_ids))
            .order_by(DebtInstrumentDocument.match_confidence.desc())
        )
        linked_docs = list(result.scalars().all())

        # Store instrument-to-document mapping for targeted extraction
        context.metadata['instrument_docs'] = {}
        for inst in context.instruments:
            result = await session.execute(
                select(DocumentSection)
                .join(DebtInstrumentDocument, DebtInstrumentDocument.document_section_id == DocumentSection.id)
                .where(DebtInstrumentDocument.debt_instrument_id == inst.id)
            )
            inst_docs = list(result.scalars().all())
            if inst_docs:
                context.metadata['instrument_docs'][inst.id] = inst_docs

        # PRIORITY 2: Always include guarantor lists and Exhibit 22 (company-wide guarantor info)
        result = await session.execute(
            select(DocumentSection).where(
                DocumentSection.company_id == company_id,
                DocumentSection.section_type.in_(['guarantor_list', 'exhibit_22'])
            ).order_by(DocumentSection.filing_date.desc()).limit(5)
        )
        guarantor_docs = list(result.scalars().all())

        # PRIORITY 3: If few linked docs, add standard debt documents
        if len(linked_docs) < 5:
            result = await session.execute(
                select(DocumentSection).where(
                    DocumentSection.company_id == company_id,
                    DocumentSection.section_type.in_([
                        'indenture', 'credit_agreement', 'debt_footnote'
                    ])
                ).order_by(DocumentSection.section_type).limit(20)
            )
            fallback_docs = list(result.scalars().all())
        else:
            fallback_docs = []

        # Combine and dedupe, prioritizing linked docs
        seen_ids = set()
        all_docs = []
        for doc in linked_docs + guarantor_docs + fallback_docs:
            if doc.id not in seen_ids:
                seen_ids.add(doc.id)
                all_docs.append(doc)

        context.documents = all_docs[:30]  # Increased from 10 to 30
        return context

    async def get_prompt(self, context: ExtractionContext) -> str:
        """Build prompt for guarantee extraction."""
        if not context.instruments or not context.entities:
            return ""

        # Build content from documents or filings
        doc_content = ""
        if context.documents:
            doc_content = "\n\n".join([
                f"=== {d.section_type} ===\n{d.content[:30000]}"
                for d in context.documents
            ])
        else:
            for key, content in list(context.filings.items())[:3]:
                if content:
                    doc_content += f"\n\n=== {key} ===\n{content[:30000]}"

        if not doc_content:
            return ""

        # Build entity and debt lists (include all entities for matching)
        entity_list = "\n".join([
            f"- {e.name}" for e in context.entities[:200]
        ])
        debt_list = "\n".join([
            f"{i+1}. {inst.name}" for i, inst in enumerate(context.instruments[:30])
        ])

        return f"""Analyze these documents to extract guarantee relationships for {context.ticker}.

ENTITIES (use exact names):
{entity_list}

DEBT INSTRUMENTS:
{debt_list}

DOCUMENTS:
{doc_content[:50000]}

Return JSON:
{{
  "guarantees": [
    {{"debt_name": "exact instrument name", "guarantor_names": ["Entity 1", "Entity 2"], "guarantee_type": "full"}}
  ]
}}

Only include guarantors that EXACTLY match entity names above."""

    async def parse_result(
        self,
        response: LLMResponse,
        context: ExtractionContext
    ) -> list[ParsedGuarantee]:
        """Parse guarantee relationships from LLM response."""
        guarantees = []
        for g in response.data.get('guarantees', []):
            guarantees.append(ParsedGuarantee(
                debt_name=g.get('debt_name', ''),
                guarantor_names=g.get('guarantor_names', []),
                guarantee_type=g.get('guarantee_type', 'full'),
            ))
        return guarantees

    async def save_result(
        self,
        items: list[ParsedGuarantee],
        context: ExtractionContext
    ) -> int:
        """Save guarantee records to database and update confidence."""
        session = context.session

        # Build lookup maps with normalized names
        entity_map = {}
        for e in context.entities:
            entity_map[e.name.lower()] = e.id
            entity_map[_normalize_name(e.name)] = e.id

        instrument_map = {i.name.lower(): i for i in context.instruments}

        guarantees_created = 0
        instruments_processed = set()

        for parsed in items:
            # Find matching instrument (fuzzy match)
            instrument = instrument_map.get(parsed.debt_name.lower())
            if not instrument:
                # Try fuzzy match
                for inst in context.instruments:
                    if SequenceMatcher(None, parsed.debt_name.lower(), inst.name.lower()).ratio() > 0.8:
                        instrument = inst
                        break

            if not instrument:
                continue

            instruments_processed.add(instrument.id)

            for guarantor_name in parsed.guarantor_names:
                # Use fuzzy matching for entity
                entity_id = _fuzzy_match_entity(guarantor_name, entity_map)
                if not entity_id:
                    continue

                # Check if guarantee already exists
                existing = await session.execute(
                    select(Guarantee).where(
                        Guarantee.debt_instrument_id == instrument.id,
                        Guarantee.guarantor_id == entity_id
                    )
                )
                if existing.scalar_one_or_none():
                    continue

                guarantee = Guarantee(
                    id=uuid4(),
                    debt_instrument_id=instrument.id,
                    guarantor_id=entity_id,
                    guarantee_type=parsed.guarantee_type,
                )
                session.add(guarantee)
                guarantees_created += 1

        # Update confidence for all processed instruments
        for inst in context.instruments:
            if inst.id in instruments_processed:
                inst.guarantee_data_confidence = 'extracted'
            elif inst.guarantee_data_confidence in ['unknown', None]:
                # No guarantees found - mark as extracted (no guarantors)
                inst.guarantee_data_confidence = 'extracted'

        await session.commit()
        return guarantees_created


# Convenience function matching original API
async def extract_guarantees(
    session: AsyncSession,
    company_id: UUID,
    ticker: str,
    filings: dict
) -> int:
    """
    Extract guarantee relationships from indentures and credit agreements.

    PARAMETERS
    ----------
    session : AsyncSession
        Database session
    company_id : UUID
        Company UUID
    ticker : str
        Stock ticker
    filings : dict
        Dict of filing content by type

    RETURNS
    -------
    int
        Number of guarantees created
    """
    extractor = GuaranteeExtractor()
    return await extractor.extract(
        session=session,
        company_id=company_id,
        ticker=ticker,
        filings=filings,
    )


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse
    import asyncio
    import sys

    from sqlalchemy import select, or_
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.orm import sessionmaker

    # Fix Windows encoding
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    # Add parent to path for imports
    sys.path.insert(0, str(__file__).replace('app/services/guarantee_extraction.py', ''))

    from app.core.config import get_settings
    from app.models import Company, DebtInstrument

    async def main():
        parser = argparse.ArgumentParser(description="Extract guarantees")
        parser.add_argument("--ticker", help="Company ticker")
        parser.add_argument("--all", action="store_true", help="Process all companies")
        parser.add_argument("--limit", type=int, help="Limit companies")
        args = parser.parse_args()

        if not args.ticker and not args.all:
            print("Usage: python -m app.services.guarantee_extraction --ticker CHTR")
            print("       python -m app.services.guarantee_extraction --all [--limit N]")
            return

        settings = get_settings()
        engine = create_async_engine(
            settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
        )
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with async_session() as db:
            if args.ticker:
                companies = [await db.scalar(
                    select(Company).where(Company.ticker == args.ticker.upper())
                )]
            else:
                result = await db.execute(
                    select(Company)
                    .join(DebtInstrument, DebtInstrument.company_id == Company.id)
                    .where(DebtInstrument.guarantee_data_confidence.in_(['unknown', None]))
                    .group_by(Company.id)
                    .order_by(Company.ticker)
                )
                companies = list(result.scalars())
                if args.limit:
                    companies = companies[:args.limit]

        print(f"Processing {len(companies)} companies")
        extractor = GuaranteeExtractor()
        total = 0

        for company in companies:
            if not company:
                continue
            async with async_session() as db:
                print(f"[{company.ticker}] {company.name}")
                count = await extractor.extract(db, company.id, company.ticker, {})
                print(f"  Guarantees: {count}")
                total += count
            await asyncio.sleep(1)

        print(f"\nTotal guarantees created: {total}")
        await engine.dispose()

    asyncio.run(main())
