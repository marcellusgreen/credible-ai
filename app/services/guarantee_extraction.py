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
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entity, DebtInstrument, Guarantee, DocumentSection
from app.services.base_extractor import BaseExtractor, ExtractionContext
from app.services.llm_utils import LLMResponse


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
        """Load entities, instruments, and documents."""
        session = context.session
        company_id = context.company_id

        # Load entities
        result = await session.execute(
            select(Entity).where(Entity.company_id == company_id)
        )
        context.entities = list(result.scalars().all())

        # Load debt instruments
        result = await session.execute(
            select(DebtInstrument).where(
                DebtInstrument.company_id == company_id,
                DebtInstrument.is_active == True,
            )
        )
        context.instruments = list(result.scalars().all())

        # Load relevant documents
        result = await session.execute(
            select(DocumentSection).where(
                DocumentSection.company_id == company_id,
                DocumentSection.section_type.in_([
                    'indenture', 'credit_agreement', 'guarantor_list'
                ])
            ).limit(5)
        )
        context.documents = list(result.scalars().all())

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

        # Build entity and debt lists
        entity_list = "\n".join([
            f"- {e.name}" for e in context.entities[:50]
        ])
        debt_list = "\n".join([
            f"- {i.name}" for i in context.instruments[:30]
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
        """Save guarantee records to database."""
        session = context.session

        # Build lookup maps
        entity_map = {e.name.lower(): e.id for e in context.entities}
        instrument_map = {i.name.lower(): i for i in context.instruments}

        guarantees_created = 0

        for parsed in items:
            # Find matching instrument
            instrument = instrument_map.get(parsed.debt_name.lower())
            if not instrument:
                continue

            for guarantor_name in parsed.guarantor_names:
                entity_id = entity_map.get(guarantor_name.lower())
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
