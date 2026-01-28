"""
Collateral Extraction Service
=============================

Extracts collateral information for secured debt instruments.

USAGE
-----
    from app.services.collateral_extraction import extract_collateral

    count = await extract_collateral(session, company_id, ticker, filings)
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DebtInstrument, Collateral, DocumentSection
from app.services.base_extractor import BaseExtractor, ExtractionContext
from app.services.llm_utils import LLMResponse


def _extract_collateral_sections(content: str) -> str:
    """
    Extract sections of content that discuss collateral.

    PARAMETERS
    ----------
    content : str
        Full document content

    RETURNS
    -------
    str
        Extracted collateral-related sections
    """
    sections = []

    patterns = [
        r'(?:secured|collateralized)\s+by[^.]*\.(?:[^.]*\.){0,5}',
        r'(?:first|second)-priority\s+lien[^.]*\.(?:[^.]*\.){0,5}',
        r'All\s+obligations\s+under[^.]*secured[^.]*\.(?:[^.]*\.){0,10}',
        r'Collateral[^.]*includes?[^.]*\.(?:[^.]*\.){0,5}',
        r'(?:pledged|pledge)\s+(?:of\s+)?(?:substantially\s+all|all)[^.]*\.(?:[^.]*\.){0,5}',
        r'security\s+interest\s+in[^.]*\.(?:[^.]*\.){0,5}',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, content, re.IGNORECASE | re.DOTALL)
        for match in matches:
            if len(match) > 50:
                sections.append(match.strip())

    bullet_pattern = r'(?:•|\*|-)\s*(?:a\s+)?(?:first|second)-priority\s+lien[^•\*\-\n]*'
    bullet_matches = re.findall(bullet_pattern, content, re.IGNORECASE)
    sections.extend(bullet_matches)

    if sections:
        return "\n\n".join(set(sections))[:20000]
    return ""


def _fuzzy_match_debt_name(name1: str, name2: str, threshold: float = 0.6) -> bool:
    """
    Check if two debt names are similar enough to match.

    PARAMETERS
    ----------
    name1 : str
        First debt name
    name2 : str
        Second debt name
    threshold : float
        Minimum similarity ratio (default: 0.6)

    RETURNS
    -------
    bool
        True if names match
    """
    if not name1 or not name2:
        return False
    name1 = name1.lower().strip()
    name2 = name2.lower().strip()

    if name1 == name2:
        return True
    if name1 in name2 or name2 in name1:
        return True

    ratio = SequenceMatcher(None, name1, name2).ratio()
    return ratio >= threshold


@dataclass
class ParsedCollateral:
    """Parsed collateral from LLM response."""
    debt_number: Optional[int]
    debt_name: str
    collateral_type: str
    description: str
    priority: Optional[str] = None


class CollateralExtractor(BaseExtractor):
    """
    Extracts collateral information for secured debt.

    STEPS
    -----
    1. Find secured debt instruments without collateral
    2. Get documents with collateral language
    3. Extract collateral-specific sections
    4. Build prompt with numbered debt list
    5. Parse and save collateral records
    """

    async def load_context(self, context: ExtractionContext) -> ExtractionContext:
        """Load secured instruments and relevant documents."""
        session = context.session
        company_id = context.company_id

        # Get secured debt instruments
        result = await session.execute(
            select(DebtInstrument).where(
                DebtInstrument.company_id == company_id,
                DebtInstrument.is_active == True,
                or_(
                    DebtInstrument.seniority.in_(['senior_secured', 'secured']),
                    DebtInstrument.security_type.in_(['first_lien', 'second_lien'])
                )
            )
        )
        secured = list(result.scalars().all())

        if not secured:
            return context

        # Filter to those without collateral
        result = await session.execute(
            select(Collateral.debt_instrument_id).where(
                Collateral.debt_instrument_id.in_([i.id for i in secured])
            )
        )
        has_collateral = {row[0] for row in result}
        context.instruments = [i for i in secured if i.id not in has_collateral]

        if not context.instruments:
            return context

        # Get documents with collateral keywords
        result = await session.execute(
            select(DocumentSection).where(
                DocumentSection.company_id == company_id,
                or_(
                    DocumentSection.content.ilike('%secured by%'),
                    DocumentSection.content.ilike('%collateral%'),
                    DocumentSection.content.ilike('%first-priority lien%'),
                    DocumentSection.content.ilike('%pledged%'),
                    DocumentSection.content.ilike('%security interest%')
                )
            ).order_by(DocumentSection.section_type)
        )
        collateral_docs = list(result.scalars().all())

        # Also get standard debt documents
        result = await session.execute(
            select(DocumentSection).where(
                DocumentSection.company_id == company_id,
                DocumentSection.section_type.in_([
                    'credit_agreement', 'indenture', 'debt_footnote',
                    'debt_overview', 'long_term_debt'
                ])
            ).order_by(DocumentSection.section_type)
        )
        standard_docs = list(result.scalars().all())

        # Combine and dedupe
        seen_ids = set()
        all_docs = []
        for doc in collateral_docs + standard_docs:
            if doc.id not in seen_ids:
                seen_ids.add(doc.id)
                all_docs.append(doc)

        context.documents = all_docs[:10]
        return context

    async def get_prompt(self, context: ExtractionContext) -> str:
        """Build prompt for collateral extraction."""
        if not context.instruments:
            return ""

        # Build content from documents
        doc_content = ""
        if context.documents:
            content_parts = []
            for d in context.documents:
                collateral_sections = _extract_collateral_sections(d.content)
                if collateral_sections:
                    content_parts.append(
                        f"=== {d.section_type.upper()} (collateral sections) ===\n{collateral_sections}"
                    )
                else:
                    content_parts.append(
                        f"=== {d.section_type.upper()} ===\n{d.content[:30000]}"
                    )
            doc_content = "\n\n".join(content_parts)[:150000]
        else:
            for key, content in list(context.filings.items())[:2]:
                if content:
                    doc_content += f"\n\n=== {key} ===\n{content[:50000]}"

        if not doc_content:
            return ""

        # Build numbered debt list
        debt_list = []
        for i, inst in enumerate(context.instruments[:30]):
            seniority = inst.seniority or 'unknown'
            sec_type = inst.security_type or 'unknown'
            principal = f"${inst.principal / 100 / 1e6:,.0f}MM" if inst.principal else "N/A"
            debt_list.append(
                f"{i+1}. {inst.name} | Seniority: {seniority} | Security: {sec_type} | Principal: {principal}"
            )
        debt_str = "\n".join(debt_list)

        return f"""Analyze this company's SEC filings to identify COLLATERAL securing these debt instruments.

COMPANY: {context.ticker}

SECURED DEBT INSTRUMENTS (numbered for reference):
{debt_str}

FILING CONTENT:
{doc_content[:100000]}

INSTRUCTIONS:
1. Find specific language describing what assets secure each debt instrument
2. Look for: "secured by", "collateralized by", "pledged", "first lien on", "security interest in"
3. Common collateral includes: real estate, equipment, receivables, inventory, vehicles, aircraft, ships, intellectual property, subsidiary stock, cash, securities

COLLATERAL TYPES (use these exact values):
- real_estate: Property, land, buildings, mortgages
- equipment: Machinery, rigs, manufacturing equipment
- receivables: Accounts receivable, notes receivable
- inventory: Raw materials, finished goods
- vehicles: Aircraft, ships, trucks, fleet
- cash: Cash deposits, restricted cash
- ip: Intellectual property, patents, trademarks
- subsidiary_stock: Stock/equity of subsidiaries
- securities: Investment securities
- energy_assets: Oil/gas reserves, pipelines
- general_lien: "Substantially all assets" or blanket security

Return JSON with ONE collateral record per debt instrument:
{{
  "collateral": [
    {{
      "debt_number": 1,
      "debt_name": "exact or close name from list",
      "collateral_type": "PRIMARY type from list above",
      "description": "comprehensive description of ALL collateral",
      "priority": "first_lien or second_lien"
    }}
  ]
}}

IMPORTANT: Return only ONE record per debt instrument. Choose PRIMARY type and list all in description.

Return ONLY valid JSON."""

    async def parse_result(
        self,
        response: LLMResponse,
        context: ExtractionContext
    ) -> list[ParsedCollateral]:
        """Parse collateral from LLM response."""
        items = []
        for c in response.data.get('collateral', []):
            items.append(ParsedCollateral(
                debt_number=c.get('debt_number'),
                debt_name=c.get('debt_name', ''),
                collateral_type=c.get('collateral_type', 'general_lien'),
                description=c.get('description', ''),
                priority=c.get('priority'),
            ))
        return items

    async def save_result(
        self,
        items: list[ParsedCollateral],
        context: ExtractionContext
    ) -> int:
        """Save collateral records to database."""
        session = context.session
        instruments = context.instruments
        created = 0

        for parsed in items:
            # Match by number first
            instrument = None
            if parsed.debt_number and 1 <= parsed.debt_number <= len(instruments):
                instrument = instruments[parsed.debt_number - 1]
            else:
                # Fuzzy name match
                for inst in instruments:
                    if _fuzzy_match_debt_name(parsed.debt_name, inst.name):
                        instrument = inst
                        break

            if not instrument:
                continue

            # Check if already exists
            existing = await session.execute(
                select(Collateral).where(Collateral.debt_instrument_id == instrument.id)
            )
            if existing.scalar_one_or_none():
                continue

            collateral = Collateral(
                id=uuid4(),
                debt_instrument_id=instrument.id,
                collateral_type=parsed.collateral_type,
                description=parsed.description,
                priority=parsed.priority,
            )
            session.add(collateral)
            created += 1

        await session.commit()
        return created


# Convenience function matching original API
async def extract_collateral(
    session: AsyncSession,
    company_id: UUID,
    ticker: str,
    filings: dict
) -> int:
    """
    Extract collateral for secured debt instruments.

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
        Number of collateral records created
    """
    extractor = CollateralExtractor()
    return await extractor.extract(
        session=session,
        company_id=company_id,
        ticker=ticker,
        filings=filings,
    )
