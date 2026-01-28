"""
Guarantee Extraction Service for DebtStack.ai

Extracts guarantee relationships from indentures and credit agreements.
Links debt instruments to their guarantor entities.
"""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entity, DebtInstrument, Guarantee, DocumentSection
from app.services.utils import parse_json_robust
from app.core.config import get_settings


async def extract_guarantees(
    session: AsyncSession,
    company_id: UUID,
    ticker: str,
    filings: dict
) -> int:
    """
    Extract guarantee relationships from indentures and credit agreements.

    Args:
        session: Database session
        company_id: Company UUID
        ticker: Stock ticker
        filings: Dict of filing content by type

    Returns:
        Number of guarantees created
    """
    import google.generativeai as genai

    settings = get_settings()
    if not settings.gemini_api_key:
        return 0

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")

    # Get entities and debt instruments
    result = await session.execute(
        select(Entity).where(Entity.company_id == company_id)
    )
    entities = list(result.scalars().all())
    entity_names = [e.name for e in entities]
    entity_map = {e.name.lower(): e.id for e in entities}

    result = await session.execute(
        select(DebtInstrument).where(DebtInstrument.company_id == company_id)
    )
    instruments = list(result.scalars().all())

    if not instruments or not entities:
        return 0

    # Get document sections (indentures, credit agreements)
    result = await session.execute(
        select(DocumentSection).where(
            DocumentSection.company_id == company_id,
            DocumentSection.section_type.in_(['indenture', 'credit_agreement', 'guarantor_list'])
        ).limit(5)
    )
    docs = list(result.scalars().all())

    # Build context from filings if no stored docs
    doc_content = ""
    if docs:
        doc_content = "\n\n".join([f"=== {d.section_type} ===\n{d.content[:30000]}" for d in docs])
    else:
        # Use raw filings
        for key, content in list(filings.items())[:3]:
            if content:
                doc_content += f"\n\n=== {key} ===\n{content[:30000]}"

    if not doc_content:
        return 0

    # Build prompt
    entity_list = "\n".join([f"- {name}" for name in entity_names[:50]])
    debt_list = "\n".join([f"- {i.name}" for i in instruments[:30]])

    prompt = f"""Analyze these documents to extract guarantee relationships for {ticker}.

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

    try:
        response = model.generate_content(prompt)
        result_data = parse_json_robust(response.text)

        guarantees_created = 0
        for g in result_data.get('guarantees', []):
            debt_name = g.get('debt_name', '').lower()
            # Find matching instrument
            instrument = next((i for i in instruments if i.name and i.name.lower() == debt_name), None)
            if not instrument:
                continue

            for guarantor_name in g.get('guarantor_names', []):
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
                    guarantee_type=g.get('guarantee_type', 'full'),
                )
                session.add(guarantee)
                guarantees_created += 1

        await session.commit()
        return guarantees_created
    except Exception as e:
        print(f"      Guarantee extraction error: {e}")
        return 0
