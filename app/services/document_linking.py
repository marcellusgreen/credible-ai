"""
Document Linking Service
========================

Links debt instruments to their source documents (indentures, credit agreements).
Populates the DebtInstrumentDocument junction table.

USAGE
-----
    from app.services.document_linking import link_documents

    count = await link_documents(session, company_id, ticker)

    # Or via CLI:
    python -m app.services.document_linking --ticker CHTR
    python -m app.services.document_linking --all --limit 10
"""

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DebtInstrument,
    DocumentSection,
    DebtInstrumentDocument,
)
from app.services.base_extractor import BaseExtractor, ExtractionContext
from app.services.llm_utils import LLMResponse


def _normalize_for_matching(text: str) -> str:
    """Normalize text for matching."""
    if not text:
        return ""
    return text.lower().replace(",", "").replace(".", "").replace("-", " ").strip()


def _extract_rate_from_name(name: str) -> Optional[str]:
    """Extract interest rate from debt name like '5.250% Notes'."""
    import re
    match = re.search(r'(\d+\.?\d*)\s*%', name)
    return match.group(1) if match else None


def _extract_year_from_name(name: str) -> Optional[str]:
    """Extract maturity year from debt name like 'Notes due 2025'."""
    import re
    match = re.search(r'(?:due|maturing?)\s*(\d{4})', name, re.IGNORECASE)
    return match.group(1) if match else None


@dataclass
class ParsedDocumentLink:
    """Parsed document link from LLM response."""
    debt_number: int
    document_number: int
    confidence: float
    relationship_type: str = "governs"
    evidence: str = ""


class DocumentLinker(BaseExtractor):
    """
    Links debt instruments to their source documents.

    STEPS
    -----
    1. Load debt instruments without document links
    2. Load indentures and credit agreements
    3. Use LLM to match instruments to documents
    4. Create DebtInstrumentDocument records
    """

    async def load_context(self, context: ExtractionContext) -> ExtractionContext:
        """Load instruments and documents."""
        session = context.session
        company_id = context.company_id

        # Get debt instruments without document links (or with low confidence links)
        result = await session.execute(
            select(DebtInstrument)
            .outerjoin(
                DebtInstrumentDocument,
                DebtInstrumentDocument.debt_instrument_id == DebtInstrument.id
            )
            .where(
                DebtInstrument.company_id == company_id,
                DebtInstrument.is_active == True,
            )
            .group_by(DebtInstrument.id)
            .having(func.count(DebtInstrumentDocument.id) == 0)
        )
        context.instruments = list(result.scalars().all())

        if not context.instruments:
            return context

        # Load indentures and credit agreements
        result = await session.execute(
            select(DocumentSection).where(
                DocumentSection.company_id == company_id,
                DocumentSection.section_type.in_([
                    'indenture', 'credit_agreement', 'desc_securities'
                ])
            ).order_by(DocumentSection.filing_date.desc())
        )
        context.documents = list(result.scalars().all())

        return context

    async def get_prompt(self, context: ExtractionContext) -> str:
        """Build prompt for document matching."""
        if not context.instruments or not context.documents:
            return ""

        # Build numbered debt list with key identifiers
        debt_list = []
        for i, inst in enumerate(context.instruments[:50]):
            rate = _extract_rate_from_name(inst.name) or inst.interest_rate
            maturity = inst.maturity_date.year if inst.maturity_date else _extract_year_from_name(inst.name)
            debt_list.append(
                f"{i+1}. {inst.name} | Type: {inst.instrument_type} | "
                f"Rate: {rate or 'N/A'} | Maturity: {maturity or 'N/A'}"
            )
        debt_str = "\n".join(debt_list)

        # Build numbered document list with snippets
        doc_list = []
        for i, doc in enumerate(context.documents[:30]):
            # Get first 500 chars as preview
            preview = doc.content[:500].replace("\n", " ")[:200]
            doc_list.append(
                f"{i+1}. [{doc.section_type}] Filed: {doc.filing_date} | "
                f"Preview: {preview}..."
            )
        doc_str = "\n".join(doc_list)

        return f"""Match these debt instruments to their governing legal documents for {context.ticker}.

DEBT INSTRUMENTS (numbered):
{debt_str}

LEGAL DOCUMENTS (numbered):
{doc_str}

MATCHING RULES:
1. Match bonds/notes to INDENTURES containing same coupon rate and maturity year
2. Match term loans/revolvers to CREDIT AGREEMENTS with matching facility type
3. A document may govern multiple instruments (e.g., one indenture for a series of notes)
4. Only match if you're confident - better to skip than make wrong match

Return JSON:
{{
  "matches": [
    {{"debt_number": 1, "document_number": 3, "confidence": 0.95, "evidence": "5.25% rate and 2025 maturity match"}},
    {{"debt_number": 2, "document_number": 3, "confidence": 0.90, "evidence": "Same indenture governs both note series"}}
  ]
}}

Only include matches with confidence >= 0.7. Return empty matches array if no confident matches."""

    async def parse_result(
        self,
        response: LLMResponse,
        context: ExtractionContext
    ) -> list[ParsedDocumentLink]:
        """Parse document links from LLM response."""
        links = []
        for m in response.data.get('matches', []):
            confidence = m.get('confidence', 0)
            if confidence < 0.7:
                continue
            links.append(ParsedDocumentLink(
                debt_number=m.get('debt_number', 0),
                document_number=m.get('document_number', 0),
                confidence=confidence,
                evidence=m.get('evidence', ''),
            ))
        return links

    async def save_result(
        self,
        items: list[ParsedDocumentLink],
        context: ExtractionContext
    ) -> int:
        """Save document links to database."""
        session = context.session
        instruments = context.instruments
        documents = context.documents
        created = 0

        for parsed in items:
            # Validate indices
            if not (1 <= parsed.debt_number <= len(instruments)):
                continue
            if not (1 <= parsed.document_number <= len(documents)):
                continue

            instrument = instruments[parsed.debt_number - 1]
            document = documents[parsed.document_number - 1]

            # Check if link already exists
            existing = await session.execute(
                select(DebtInstrumentDocument).where(
                    DebtInstrumentDocument.debt_instrument_id == instrument.id,
                    DebtInstrumentDocument.document_section_id == document.id,
                )
            )
            if existing.scalar_one_or_none():
                continue

            link = DebtInstrumentDocument(
                id=uuid4(),
                debt_instrument_id=instrument.id,
                document_section_id=document.id,
                relationship_type=parsed.relationship_type,
                match_confidence=parsed.confidence,
                match_method='llm',
                match_evidence={'evidence': parsed.evidence},
                created_by='document_linking_service',
            )
            session.add(link)
            created += 1

        await session.commit()
        return created


async def link_documents(
    session: AsyncSession,
    company_id: UUID,
    ticker: str,
) -> int:
    """
    Link debt instruments to their source documents.

    PARAMETERS
    ----------
    session : AsyncSession
        Database session
    company_id : UUID
        Company UUID
    ticker : str
        Stock ticker

    RETURNS
    -------
    int
        Number of document links created
    """
    linker = DocumentLinker()
    return await linker.extract(
        session=session,
        company_id=company_id,
        ticker=ticker,
        filings={},
    )


# Also provide heuristic matching for when LLM isn't needed
async def link_documents_heuristic(
    session: AsyncSession,
    company_id: UUID,
) -> int:
    """
    Link documents using heuristic matching (no LLM).

    Matches based on:
    - Coupon rate + maturity year for bonds
    - Facility type keywords for loans

    Faster and cheaper than LLM but less accurate.
    """
    # Get unlinked instruments
    result = await session.execute(
        select(DebtInstrument)
        .outerjoin(
            DebtInstrumentDocument,
            DebtInstrumentDocument.debt_instrument_id == DebtInstrument.id
        )
        .where(
            DebtInstrument.company_id == company_id,
            DebtInstrument.is_active == True,
        )
        .group_by(DebtInstrument.id)
        .having(func.count(DebtInstrumentDocument.id) == 0)
    )
    instruments = list(result.scalars().all())

    if not instruments:
        return 0

    # Get documents
    result = await session.execute(
        select(DocumentSection).where(
            DocumentSection.company_id == company_id,
            DocumentSection.section_type.in_(['indenture', 'credit_agreement'])
        )
    )
    documents = list(result.scalars().all())

    created = 0

    for inst in instruments:
        rate = _extract_rate_from_name(inst.name)
        year = _extract_year_from_name(inst.name)
        inst_type = inst.instrument_type or ''

        best_match = None
        best_score = 0

        for doc in documents:
            score = 0
            content_lower = doc.content[:5000].lower()

            # Check for rate match
            if rate and rate in content_lower:
                score += 0.4

            # Check for year match
            if year and year in content_lower:
                score += 0.3

            # Check for instrument type match
            if 'term loan' in inst_type and 'term loan' in content_lower:
                score += 0.3
            if 'revolver' in inst_type and ('revolv' in content_lower or 'revolver' in content_lower):
                score += 0.3
            if 'notes' in inst_type.lower() and doc.section_type == 'indenture':
                score += 0.2

            # Check instrument name in document
            name_norm = _normalize_for_matching(inst.name)
            if len(name_norm) > 10:
                ratio = SequenceMatcher(None, name_norm, content_lower[:2000]).ratio()
                if ratio > 0.3:
                    score += ratio * 0.3

            if score > best_score and score >= 0.5:
                best_score = score
                best_match = doc

        if best_match:
            # Check if exists
            existing = await session.execute(
                select(DebtInstrumentDocument).where(
                    DebtInstrumentDocument.debt_instrument_id == inst.id,
                    DebtInstrumentDocument.document_section_id == best_match.id,
                )
            )
            if not existing.scalar_one_or_none():
                link = DebtInstrumentDocument(
                    id=uuid4(),
                    debt_instrument_id=inst.id,
                    document_section_id=best_match.id,
                    relationship_type='governs',
                    match_confidence=best_score,
                    match_method='heuristic',
                    match_evidence={'rate': rate, 'year': year},
                    created_by='document_linking_heuristic',
                )
                session.add(link)
                created += 1

    await session.commit()
    return created


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse
    import asyncio
    import sys

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.orm import sessionmaker

    # Fix Windows encoding
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    # Add parent to path for imports
    sys.path.insert(0, str(__file__).replace('app/services/document_linking.py', ''))

    from app.core.config import get_settings
    from app.models import Company

    async def main():
        parser = argparse.ArgumentParser(description="Link debt instruments to source documents")
        parser.add_argument("--ticker", help="Company ticker")
        parser.add_argument("--all", action="store_true", help="Process all companies")
        parser.add_argument("--limit", type=int, help="Limit companies")
        parser.add_argument("--heuristic", action="store_true", help="Use heuristic matching (no LLM)")
        args = parser.parse_args()

        if not args.ticker and not args.all:
            print("Usage: python -m app.services.document_linking --ticker CHTR")
            print("       python -m app.services.document_linking --all [--limit N]")
            print("       python -m app.services.document_linking --all --heuristic")
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
                # Get companies with unlinked debt
                result = await db.execute(
                    select(Company)
                    .join(DebtInstrument, DebtInstrument.company_id == Company.id)
                    .outerjoin(
                        DebtInstrumentDocument,
                        DebtInstrumentDocument.debt_instrument_id == DebtInstrument.id
                    )
                    .group_by(Company.id)
                    .having(
                        func.count(DebtInstrument.id) > func.count(DebtInstrumentDocument.id)
                    )
                    .order_by(Company.ticker)
                )
                companies = list(result.scalars())
                if args.limit:
                    companies = companies[:args.limit]

        print(f"Processing {len(companies)} companies")
        total = 0

        for company in companies:
            if not company:
                continue
            async with async_session() as db:
                print(f"[{company.ticker}] {company.name}")
                if args.heuristic:
                    count = await link_documents_heuristic(db, company.id)
                else:
                    count = await link_documents(db, company.id, company.ticker)
                print(f"  Links created: {count}")
                total += count
            await asyncio.sleep(0.5)

        print(f"\nTotal document links created: {total}")
        await engine.dispose()

    asyncio.run(main())
