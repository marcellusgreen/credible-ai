"""
Collateral Extraction Service for DebtStack.ai

Extracts collateral information for secured debt instruments.
"""

import re
from difflib import SequenceMatcher
from uuid import UUID, uuid4

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DebtInstrument, Collateral, DocumentSection
from app.services.utils import parse_json_robust
from app.core.config import get_settings


def _extract_collateral_sections(content: str) -> str:
    """Extract sections of content that discuss collateral."""
    sections = []

    # Patterns that indicate collateral discussion
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
            if len(match) > 50:  # Skip very short matches
                sections.append(match.strip())

    # Also look for bullet points describing collateral
    bullet_pattern = r'(?:•|\*|-)\s*(?:a\s+)?(?:first|second)-priority\s+lien[^•\*\-\n]*'
    bullet_matches = re.findall(bullet_pattern, content, re.IGNORECASE)
    sections.extend(bullet_matches)

    if sections:
        return "\n\n".join(set(sections))[:20000]

    return ""


def _fuzzy_match_debt_name(name1: str, name2: str, threshold: float = 0.6) -> bool:
    """Check if two debt names are similar enough to match."""
    if not name1 or not name2:
        return False
    name1 = name1.lower().strip()
    name2 = name2.lower().strip()

    # Exact match
    if name1 == name2:
        return True

    # One contains the other
    if name1 in name2 or name2 in name1:
        return True

    # Fuzzy match
    ratio = SequenceMatcher(None, name1, name2).ratio()
    return ratio >= threshold


async def extract_collateral(
    session: AsyncSession,
    company_id: UUID,
    ticker: str,
    filings: dict
) -> int:
    """
    Extract collateral for secured debt instruments.

    Enhanced version with:
    - Better query to find secured instruments (checks seniority AND security_type)
    - Fuzzy matching of debt names
    - Extracts collateral-specific sections from documents
    - More comprehensive prompt with additional collateral types

    Args:
        session: Database session
        company_id: Company UUID
        ticker: Stock ticker
        filings: Dict of filing content by type

    Returns:
        Number of collateral records created
    """
    import google.generativeai as genai

    settings = get_settings()
    if not settings.gemini_api_key:
        return 0

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")

    # Get secured debt instruments - check both seniority AND security_type
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
    secured_instruments = list(result.scalars().all())

    if not secured_instruments:
        return 0

    # Filter to instruments that don't already have collateral
    instruments_with_collateral = set()
    result = await session.execute(
        select(Collateral.debt_instrument_id).where(
            Collateral.debt_instrument_id.in_([i.id for i in secured_instruments])
        )
    )
    for row in result:
        instruments_with_collateral.add(row[0])

    instruments_missing_collateral = [i for i in secured_instruments if i.id not in instruments_with_collateral]

    if not instruments_missing_collateral:
        return 0

    # Get document content - prioritize sections with collateral info
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

    # Also get credit agreements and debt footnotes
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

    doc_content = ""
    if all_docs:
        content_parts = []
        for d in all_docs[:10]:
            collateral_sections = _extract_collateral_sections(d.content)
            if collateral_sections:
                content_parts.append(f"=== {d.section_type.upper()} (collateral sections) ===\n{collateral_sections}")
            else:
                content_parts.append(f"=== {d.section_type.upper()} ===\n{d.content[:30000]}")
        doc_content = "\n\n".join(content_parts)[:150000]
    else:
        for key, content in list(filings.items())[:2]:
            if content:
                doc_content += f"\n\n=== {key} ===\n{content[:50000]}"

    if not doc_content:
        return 0

    # Build detailed debt list with numbers for matching
    debt_list = []
    for i, inst in enumerate(instruments_missing_collateral[:30]):
        seniority = inst.seniority or 'unknown'
        sec_type = inst.security_type or 'unknown'
        principal = f"${inst.principal / 100 / 1e6:,.0f}MM" if inst.principal else "N/A"
        debt_list.append(f"{i+1}. {inst.name} | Seniority: {seniority} | Security: {sec_type} | Principal: {principal}")

    debt_str = "\n".join(debt_list)

    prompt = f"""Analyze this company's SEC filings to identify COLLATERAL securing these debt instruments.

COMPANY: {ticker}

SECURED DEBT INSTRUMENTS (numbered for reference):
{debt_str}

FILING CONTENT:
{doc_content[:100000]}

INSTRUCTIONS:
1. Find specific language describing what assets secure each debt instrument
2. Look for: "secured by", "collateralized by", "pledged", "first lien on", "security interest in"
3. Common collateral includes: real estate, equipment, receivables, inventory, vehicles, aircraft, ships, intellectual property, subsidiary stock, cash, securities
4. For credit facilities, look for "substantially all assets" or similar general security language

COLLATERAL TYPES (use these exact values):
- real_estate: Property, land, buildings, mortgages
- equipment: Machinery, rigs, manufacturing equipment
- receivables: Accounts receivable, notes receivable, securitization assets
- inventory: Raw materials, finished goods, work in progress
- vehicles: Aircraft, ships, trucks, fleet vehicles
- cash: Cash deposits, restricted cash
- ip: Intellectual property, patents, trademarks
- subsidiary_stock: Stock/equity of subsidiaries
- securities: Investment securities, marketable securities
- energy_assets: Oil/gas reserves, pipelines, power plants
- general_lien: "Substantially all assets" or blanket security interest

Return JSON with ONE collateral record per debt instrument (use the PRIMARY collateral type):
{{
  "collateral": [
    {{
      "debt_number": 1,
      "debt_name": "exact or close name from list",
      "collateral_type": "PRIMARY type from list above",
      "description": "comprehensive description of ALL collateral securing this debt",
      "priority": "first_lien or second_lien"
    }}
  ]
}}

IMPORTANT: Return only ONE record per debt instrument. If multiple collateral types secure one instrument, choose the PRIMARY type and include ALL types in the description.

Return ONLY valid JSON."""

    try:
        response = model.generate_content(prompt)
        result_data = parse_json_robust(response.text)

        collateral_created = 0
        for c in result_data.get('collateral', []):
            # Try to match by number first
            debt_num = c.get('debt_number')
            instrument = None

            if debt_num and 1 <= debt_num <= len(instruments_missing_collateral):
                instrument = instruments_missing_collateral[debt_num - 1]
            else:
                # Fall back to fuzzy name matching
                debt_name = c.get('debt_name', '')
                for inst in instruments_missing_collateral:
                    if _fuzzy_match_debt_name(debt_name, inst.name):
                        instrument = inst
                        break

            if not instrument:
                continue

            # Check if collateral already exists for this instrument
            existing = await session.execute(
                select(Collateral).where(Collateral.debt_instrument_id == instrument.id)
            )
            if existing.scalar_one_or_none():
                continue

            collateral = Collateral(
                id=uuid4(),
                debt_instrument_id=instrument.id,
                collateral_type=c.get('collateral_type', 'general_lien'),
                description=c.get('description', ''),
                priority=c.get('priority'),
            )
            session.add(collateral)
            collateral_created += 1

        await session.commit()
        return collateral_created
    except Exception as e:
        print(f"      Collateral extraction error: {e}")
        return 0
