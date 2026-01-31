"""
Covenant Extraction Service
===========================

Extracts structured covenant data from credit agreements and indentures
stored in the database.

ARCHITECTURE
------------
Three layers for modularity and testability:

1. PURE FUNCTIONS (no DB, no LLM) - for unit testing
   - extract_covenant_sections(text) -> str
   - parse_covenant_response(json_data) -> list[ParsedCovenant]
   - fuzzy_match_debt_name(name1, name2) -> bool

2. LLM FUNCTIONS (no DB) - for integration testing
   - build_covenant_prompt(content, ticker, instruments) -> str

3. ORCHESTRATION (DB + LLM) - production use
   - CovenantExtractor class (BaseExtractor pattern)
   - extract_covenants(session, company_id, ticker) -> int

USAGE
-----
    from app.services.covenant_extraction import extract_covenants

    count = await extract_covenants(session, company_id, ticker)

CLI
---
    python -m app.services.covenant_extraction --ticker CHTR
    python -m app.services.covenant_extraction --all --limit 50
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Company, DebtInstrument, DocumentSection, DebtInstrumentDocument, Covenant
)
from app.services.base_extractor import BaseExtractor, ExtractionContext
from app.services.llm_utils import LLMResponse


# =============================================================================
# CONSTANTS
# =============================================================================

COVENANT_TYPES = ['financial', 'negative', 'incurrence', 'protective']

FINANCIAL_METRICS = [
    'leverage_ratio',           # Total Debt / EBITDA
    'first_lien_leverage',      # First Lien Debt / EBITDA
    'secured_leverage',         # Secured Debt / EBITDA
    'net_leverage_ratio',       # Net Debt / EBITDA
    'interest_coverage',        # EBITDA / Interest Expense
    'fixed_charge_coverage',    # EBITDA - CapEx / Fixed Charges
    'debt_to_capitalization',   # Total Debt / (Debt + Equity)
]


# =============================================================================
# DATA CLASS
# =============================================================================

@dataclass
class ParsedCovenant:
    """Parsed covenant data structure."""
    covenant_type: str
    covenant_name: str
    debt_name: Optional[str] = None
    test_metric: Optional[str] = None
    threshold_value: Optional[float] = None
    threshold_type: Optional[str] = None
    test_frequency: Optional[str] = None
    description: Optional[str] = None
    has_step_down: bool = False
    step_down_schedule: Optional[dict] = None
    cure_period_days: Optional[int] = None
    put_price_pct: Optional[float] = None
    confidence: float = 0.8
    source_text: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            'covenant_type': self.covenant_type,
            'covenant_name': self.covenant_name,
            'debt_name': self.debt_name,
            'test_metric': self.test_metric,
            'threshold_value': self.threshold_value,
            'threshold_type': self.threshold_type,
            'test_frequency': self.test_frequency,
            'description': self.description,
            'has_step_down': self.has_step_down,
            'step_down_schedule': self.step_down_schedule,
            'cure_period_days': self.cure_period_days,
            'put_price_pct': self.put_price_pct,
            'confidence': self.confidence,
            'source_text': self.source_text,
        }


# =============================================================================
# LAYER 1: PURE FUNCTIONS (no DB, no LLM)
# =============================================================================

def extract_covenant_sections(content: str, max_length: int = 50000) -> str:
    """
    Extract sections of content that discuss covenants.

    Pure function - no database or LLM calls. Useful for reducing
    token usage by focusing on covenant-relevant text.

    PARAMETERS
    ----------
    content : str
        Full document content (credit agreement, indenture, etc.)
    max_length : int
        Maximum length of returned text (default: 50000)

    RETURNS
    -------
    str
        Extracted covenant-related sections, or empty string if none found
    """
    sections = []

    # Financial covenant patterns
    financial_patterns = [
        r'(?:leverage|debt)\s+(?:ratio|to)\s+(?:ebitda|earnings)[^.]*(?:shall\s+not\s+exceed|not\s+to\s+exceed|maximum)[^.]*\.(?:[^.]*\.){0,3}',
        r'(?:interest|fixed\s+charge)\s+coverage\s+ratio[^.]*(?:shall\s+be|minimum|at\s+least)[^.]*\.(?:[^.]*\.){0,3}',
        r'(?:consolidated\s+)?(?:total\s+)?debt[^.]*(?:shall\s+not|not\s+to)[^.]*\.(?:[^.]*\.){0,3}',
    ]

    # Negative covenant patterns
    negative_patterns = [
        r'limitation(?:s)?\s+on\s+(?:liens|indebtedness|restricted\s+payments|asset\s+sales)[^.]*\.(?:[^.]*\.){0,5}',
        r'(?:negative|restrictive)\s+covenant[^.]*\.(?:[^.]*\.){0,10}',
        r'(?:shall\s+not|will\s+not)\s+(?:incur|create|permit|make)[^.]*(?:lien|debt|indebtedness)[^.]*\.(?:[^.]*\.){0,3}',
    ]

    # Protective covenant patterns
    protective_patterns = [
        r'change\s+(?:of|in)\s+control[^.]*(?:put|repurchase|offer\s+to\s+purchase)[^.]*\.(?:[^.]*\.){0,5}',
        r'(?:upon|following)\s+a?\s*change\s+(?:of|in)\s+control[^.]*\.(?:[^.]*\.){0,5}',
    ]

    # Covenant-lite patterns
    cov_lite_patterns = [
        r'(?:covenant-lite|cov-lite|no\s+(?:financial\s+)?maintenance)[^.]*\.(?:[^.]*\.){0,3}',
        r'(?:incurrence|springing)\s+(?:covenant|test)[^.]*\.(?:[^.]*\.){0,3}',
    ]

    all_patterns = financial_patterns + negative_patterns + protective_patterns + cov_lite_patterns

    for pattern in all_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE | re.DOTALL)
        for match in matches:
            if len(match) > 50:
                sections.append(match.strip())

    # Section headers (capture more context)
    header_patterns = [
        r'(?:SECTION|ARTICLE)\s+\d+[.\s]+(?:COVENANTS|NEGATIVE\s+COVENANTS|FINANCIAL\s+COVENANTS)[^.]*\.(?:[^.]*\.){0,20}',
        r'(?:Financial|Negative|Affirmative)\s+Covenants[^.]*\.(?:[^.]*\.){0,15}',
    ]

    for pattern in header_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE | re.DOTALL)
        sections.extend(matches)

    if sections:
        unique_sections = list(set(sections))
        return "\n\n".join(unique_sections)[:max_length]
    return ""


def parse_covenant_response(data: dict) -> list[ParsedCovenant]:
    """
    Parse LLM JSON response into ParsedCovenant objects.

    Pure function - no database or LLM calls.

    PARAMETERS
    ----------
    data : dict
        Parsed JSON from LLM response with 'covenants' array

    RETURNS
    -------
    list[ParsedCovenant]
        List of parsed covenant objects
    """
    items = []

    # Check for covenant-lite flag
    is_covenant_lite = data.get('is_covenant_lite', False)
    if is_covenant_lite:
        items.append(ParsedCovenant(
            covenant_type='financial',
            covenant_name='Covenant-Lite',
            description='No financial maintenance covenants. Incurrence tests only.',
            confidence=0.9,
        ))

    for c in data.get('covenants', []):
        covenant_type = c.get('covenant_type', 'negative')
        if covenant_type not in COVENANT_TYPES:
            covenant_type = 'negative'

        items.append(ParsedCovenant(
            covenant_type=covenant_type,
            covenant_name=c.get('covenant_name', ''),
            debt_name=c.get('debt_name'),
            test_metric=c.get('test_metric'),
            threshold_value=c.get('threshold_value'),
            threshold_type=c.get('threshold_type'),
            test_frequency=c.get('test_frequency'),
            description=c.get('description'),
            has_step_down=c.get('has_step_down', False),
            step_down_schedule=c.get('step_down_schedule'),
            cure_period_days=c.get('cure_period_days'),
            put_price_pct=c.get('put_price_pct'),
            confidence=c.get('confidence', 0.8),
            source_text=c.get('source_text', '')[:2000] if c.get('source_text') else None,
        ))

    return items


def fuzzy_match_debt_name(name1: str, name2: str, threshold: float = 0.6) -> bool:
    """
    Check if two debt instrument names are similar enough to match.

    Pure function for matching extracted debt names to known instruments.

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


# =============================================================================
# LAYER 2: PROMPT BUILDING (no DB)
# =============================================================================

def build_covenant_prompt(
    content: str,
    ticker: str,
    instrument_names: list[str] = None,
    max_content_length: int = 120000,
) -> str:
    """
    Build the LLM prompt for covenant extraction.

    PARAMETERS
    ----------
    content : str
        Document content (credit agreement, indenture, etc.)
    ticker : str
        Company ticker symbol
    instrument_names : list[str], optional
        List of known debt instrument names for context
    max_content_length : int
        Maximum content length to include (default: 120000)

    RETURNS
    -------
    str
        Formatted prompt for LLM
    """
    # Try to extract focused covenant sections first
    covenant_sections = extract_covenant_sections(content)
    if covenant_sections:
        doc_content = f"=== COVENANT SECTIONS ===\n{covenant_sections}"
    else:
        doc_content = content[:max_content_length]

    # Build instrument list
    if instrument_names:
        debt_str = "\n".join(f"{i+1}. {name}" for i, name in enumerate(instrument_names[:20]))
    else:
        debt_str = "No debt instruments provided"

    return f"""Analyze this document to extract COVENANT information for {ticker}.

COMPANY: {ticker}

DEBT INSTRUMENTS (for reference):
{debt_str}

DOCUMENT CONTENT:
{doc_content}

INSTRUCTIONS:
Extract ALL covenants from the document. For each covenant, identify:

1. FINANCIAL COVENANTS (with numerical thresholds):
   - Maximum Leverage Ratio (Total Debt / EBITDA)
   - Maximum First Lien Leverage Ratio
   - Maximum Secured Leverage Ratio
   - Minimum Interest Coverage Ratio (EBITDA / Interest)
   - Minimum Fixed Charge Coverage Ratio
   - Note: If covenant-lite (no maintenance covenants), indicate this

2. NEGATIVE COVENANTS (restrictions):
   - Limitations on Liens
   - Limitations on Indebtedness/Debt Incurrence
   - Limitations on Restricted Payments (dividends, distributions)
   - Limitations on Asset Sales
   - Limitations on Affiliate Transactions
   - Merger/Consolidation restrictions

3. INCURRENCE TESTS (tests that apply when taking actions):
   - Debt incurrence ratio threshold
   - Secured debt incurrence test

4. PROTECTIVE COVENANTS:
   - Change of Control (put price percentage, e.g., 101%)
   - Make-whole provisions

Return JSON:
{{
  "is_covenant_lite": true/false,
  "covenants": [
    {{
      "covenant_type": "financial|negative|incurrence|protective",
      "covenant_name": "Maximum Leverage Ratio",
      "debt_name": "Credit Agreement" or null (if company-wide),
      "test_metric": "leverage_ratio" (for financial only),
      "threshold_value": 4.50 (for financial only),
      "threshold_type": "maximum|minimum",
      "test_frequency": "quarterly|annual|incurrence",
      "description": "Brief description of the covenant",
      "has_step_down": false,
      "step_down_schedule": null or {{"Q1 2026": 4.25, "Q1 2027": 4.00}},
      "cure_period_days": 30 (if mentioned),
      "put_price_pct": 101.0 (for change of control only),
      "confidence": 0.85,
      "source_text": "Exact quote from document (max 500 chars)"
    }}
  ]
}}

METRICS for test_metric field:
- leverage_ratio: Total Debt / EBITDA
- first_lien_leverage: First Lien Debt / EBITDA
- secured_leverage: Secured Debt / EBITDA
- net_leverage_ratio: Net Debt / EBITDA
- interest_coverage: EBITDA / Interest Expense
- fixed_charge_coverage: (EBITDA - CapEx) / Fixed Charges

Return ONLY valid JSON."""


# =============================================================================
# LAYER 3: DATABASE FUNCTIONS
# =============================================================================

async def get_governing_document(
    session: AsyncSession,
    instrument_id: UUID
) -> Optional[DocumentSection]:
    """
    Get the most recent document that governs a debt instrument.

    Uses filing_date to find the latest document with relationship_type='governs'.

    PARAMETERS
    ----------
    session : AsyncSession
        Database session
    instrument_id : UUID
        Debt instrument UUID

    RETURNS
    -------
    DocumentSection or None
        Most recent governing document
    """
    result = await session.execute(
        select(DocumentSection)
        .join(DebtInstrumentDocument, DebtInstrumentDocument.document_section_id == DocumentSection.id)
        .where(
            DebtInstrumentDocument.debt_instrument_id == instrument_id,
            DebtInstrumentDocument.relationship_type == 'governs'
        )
        .order_by(desc(DocumentSection.filing_date))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_company_covenant_documents(
    session: AsyncSession,
    company_id: UUID,
    limit: int = 20,
) -> list[DocumentSection]:
    """
    Get documents that likely contain covenant information for a company.

    Prioritizes credit agreements and indentures, then falls back to
    covenant sections and debt footnotes.

    PARAMETERS
    ----------
    session : AsyncSession
        Database session
    company_id : UUID
        Company UUID
    limit : int
        Maximum documents to return (default: 20)

    RETURNS
    -------
    list[DocumentSection]
        Documents containing covenant information
    """
    # Priority 1: Credit agreements and indentures
    result = await session.execute(
        select(DocumentSection).where(
            DocumentSection.company_id == company_id,
            DocumentSection.section_type.in_(['credit_agreement', 'indenture'])
        ).order_by(desc(DocumentSection.filing_date))
    )
    primary_docs = list(result.scalars().all())

    # Priority 2: Pre-extracted covenant sections
    result = await session.execute(
        select(DocumentSection).where(
            DocumentSection.company_id == company_id,
            DocumentSection.section_type == 'covenants'
        ).order_by(desc(DocumentSection.filing_date))
    )
    covenant_docs = list(result.scalars().all())

    # Priority 3: Debt footnotes (backup)
    result = await session.execute(
        select(DocumentSection).where(
            DocumentSection.company_id == company_id,
            DocumentSection.section_type.in_(['debt_footnote', 'debt_overview', 'long_term_debt'])
        ).order_by(desc(DocumentSection.filing_date)).limit(5)
    )
    backup_docs = list(result.scalars().all())

    # Dedupe and prioritize
    seen_ids = set()
    docs = []
    for doc in primary_docs + covenant_docs + backup_docs:
        if doc.id not in seen_ids:
            seen_ids.add(doc.id)
            docs.append(doc)

    return docs[:limit]


async def get_document_instrument_map(
    session: AsyncSession,
    company_id: UUID,
) -> dict[UUID, list[UUID]]:
    """
    Get mapping of document_id -> list of instrument_ids it governs.

    PARAMETERS
    ----------
    session : AsyncSession
        Database session
    company_id : UUID
        Company UUID

    RETURNS
    -------
    dict[UUID, list[UUID]]
        Mapping of document ID to list of governed instrument IDs
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

    doc_to_instruments: dict[UUID, list[UUID]] = {}
    for doc_id, inst_id in result:
        if doc_id not in doc_to_instruments:
            doc_to_instruments[doc_id] = []
        if inst_id not in doc_to_instruments[doc_id]:
            doc_to_instruments[doc_id].append(inst_id)

    return doc_to_instruments


# =============================================================================
# LAYER 3: ORCHESTRATION (BaseExtractor pattern)
# =============================================================================

class CovenantExtractor(BaseExtractor):
    """
    Extracts covenant information from credit agreements and indentures.

    Uses BaseExtractor pattern for integration with extraction pipeline.
    """

    async def load_context(self, context: ExtractionContext) -> ExtractionContext:
        """Load documents and instruments for covenant extraction."""
        session = context.session
        company_id = context.company_id

        # Get covenant-related documents from database
        context.documents = await get_company_covenant_documents(session, company_id)

        if not context.documents:
            return context

        # Get active debt instruments for context
        result = await session.execute(
            select(DebtInstrument).where(
                DebtInstrument.company_id == company_id,
                DebtInstrument.is_active == True
            ).order_by(DebtInstrument.seniority, DebtInstrument.name)
        )
        context.instruments = list(result.scalars().all())

        # Get document -> instrument mapping for linkage
        doc_instrument_map = await get_document_instrument_map(session, company_id)
        context.metadata['doc_instrument_map'] = doc_instrument_map

        # Build instrument lookup by ID
        context.metadata['instruments_by_id'] = {inst.id: inst for inst in context.instruments}

        # Check for existing covenants (to avoid duplicates)
        result = await session.execute(
            select(Covenant.covenant_name, Covenant.debt_instrument_id).where(
                Covenant.company_id == company_id
            )
        )
        existing = {(row[0], row[1]) for row in result}
        context.metadata['existing_covenants'] = existing

        return context

    async def get_prompt(self, context: ExtractionContext) -> str:
        """Build prompt for covenant extraction."""
        if not context.documents:
            return ""

        # Combine document content with smart truncation
        doc_content_parts = []
        for doc in context.documents[:10]:
            covenant_sections = extract_covenant_sections(doc.content)
            if covenant_sections:
                doc_content_parts.append(
                    f"=== {doc.section_type.upper()} ({doc.filing_date}) - Covenant Sections ===\n{covenant_sections}"
                )
            else:
                doc_content_parts.append(
                    f"=== {doc.section_type.upper()} ({doc.filing_date}) ===\n{doc.content[:40000]}"
                )

        doc_content = "\n\n".join(doc_content_parts)

        if not doc_content:
            return ""

        # Get instrument names for context
        instrument_names = [inst.name for inst in context.instruments[:20]]

        return build_covenant_prompt(doc_content, context.ticker, instrument_names)

    async def parse_result(
        self,
        response: LLMResponse,
        context: ExtractionContext
    ) -> list[ParsedCovenant]:
        """Parse covenants from LLM response."""
        if not response.data:
            return []
        return parse_covenant_response(response.data)

    async def save_result(
        self,
        items: list[ParsedCovenant],
        context: ExtractionContext
    ) -> int:
        """Save covenant records to database."""
        session = context.session
        company_id = context.company_id
        instruments = context.instruments
        existing = context.metadata.get('existing_covenants', set())
        doc_instrument_map = context.metadata.get('doc_instrument_map', {})
        instruments_by_id = context.metadata.get('instruments_by_id', {})
        documents = context.documents or []
        created = 0

        # Build categorized instrument lists from document relationships
        # Credit agreements -> loans; Indentures -> bonds
        loan_instruments = []
        bond_instruments = []
        source_document_id = None

        for doc in documents:
            governed_ids = doc_instrument_map.get(doc.id, [])
            if doc.section_type == 'credit_agreement':
                loan_instruments.extend(governed_ids)
                if source_document_id is None and governed_ids:
                    source_document_id = doc.id
            elif doc.section_type == 'indenture':
                bond_instruments.extend(governed_ids)
                if source_document_id is None and governed_ids:
                    source_document_id = doc.id

        # Use first document as source if none have governed instruments
        if source_document_id is None and documents:
            source_document_id = documents[0].id

        for parsed in items:
            if not parsed.covenant_name:
                continue

            # Skip low confidence
            if parsed.confidence < 0.7:
                continue

            # Match to debt instrument
            instrument_id = None

            # 1. First try explicit debt name matching
            if parsed.debt_name:
                for inst in instruments:
                    if fuzzy_match_debt_name(parsed.debt_name, inst.name):
                        instrument_id = inst.id
                        break

            # 2. If no match, use document-based linkage
            if instrument_id is None:
                # Financial covenants typically from credit agreements
                if parsed.covenant_type == 'financial' and parsed.test_metric:
                    if loan_instruments:
                        instrument_id = loan_instruments[0]
                    elif bond_instruments:
                        instrument_id = bond_instruments[0]
                # Change of control typically from indentures
                elif 'change of control' in parsed.covenant_name.lower():
                    if bond_instruments:
                        instrument_id = bond_instruments[0]
                    elif loan_instruments:
                        instrument_id = loan_instruments[0]
                # Other covenants -> prefer loans if available
                else:
                    if loan_instruments:
                        instrument_id = loan_instruments[0]
                    elif bond_instruments:
                        instrument_id = bond_instruments[0]

            # Skip duplicates
            if (parsed.covenant_name, instrument_id) in existing:
                continue

            # Create record with source document
            covenant = Covenant(
                id=uuid4(),
                company_id=company_id,
                debt_instrument_id=instrument_id,
                source_document_id=source_document_id,
                covenant_type=parsed.covenant_type,
                covenant_name=parsed.covenant_name,
                test_metric=parsed.test_metric,
                threshold_value=Decimal(str(parsed.threshold_value)) if parsed.threshold_value else None,
                threshold_type=parsed.threshold_type,
                test_frequency=parsed.test_frequency,
                description=parsed.description,
                has_step_down=parsed.has_step_down,
                step_down_schedule=parsed.step_down_schedule,
                cure_period_days=parsed.cure_period_days,
                put_price_pct=Decimal(str(parsed.put_price_pct)) if parsed.put_price_pct else None,
                extraction_confidence=Decimal(str(parsed.confidence)),
                extracted_at=datetime.now(timezone.utc),
                source_text=parsed.source_text,
            )
            session.add(covenant)
            existing.add((parsed.covenant_name, instrument_id))
            created += 1

        await session.commit()
        return created


# =============================================================================
# CONVENIENCE FUNCTION
# =============================================================================

async def extract_covenants(
    session: AsyncSession,
    company_id: UUID,
    ticker: str,
    filings: dict = None,
    force: bool = False,
) -> int:
    """
    Extract covenants for a company and save to database.

    PARAMETERS
    ----------
    session : AsyncSession
        Database session
    company_id : UUID
        Company UUID
    ticker : str
        Stock ticker
    filings : dict
        Dict of filing content by type (optional, uses DB docs if not provided)
    force : bool
        If True, re-extract even if covenants exist

    RETURNS
    -------
    int
        Number of covenant records created
    """
    if not force:
        result = await session.execute(
            select(Covenant).where(Covenant.company_id == company_id).limit(1)
        )
        if result.scalar_one_or_none():
            return 0

    extractor = CovenantExtractor()
    return await extractor.extract(
        session=session,
        company_id=company_id,
        ticker=ticker,
        filings=filings or {},
    )


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse
    import asyncio
    import sys

    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker

    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    sys.path.insert(0, str(__file__).replace('app/services/covenant_extraction.py', ''))

    from app.core.config import get_settings

    async def main():
        parser = argparse.ArgumentParser(description="Extract covenants from SEC filings")
        parser.add_argument("--ticker", help="Company ticker")
        parser.add_argument("--all", action="store_true", help="Process all companies")
        parser.add_argument("--limit", type=int, help="Limit companies to process")
        parser.add_argument("--force", action="store_true", help="Re-extract even if data exists")
        args = parser.parse_args()

        if not args.ticker and not args.all:
            print("Usage:")
            print("  python -m app.services.covenant_extraction --ticker CHTR")
            print("  python -m app.services.covenant_extraction --all [--limit N]")
            print("  python -m app.services.covenant_extraction --ticker CHTR --force")
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
                # Get companies with covenant-related documents
                result = await db.execute(
                    select(Company)
                    .join(DocumentSection, DocumentSection.company_id == Company.id)
                    .where(DocumentSection.section_type.in_(['credit_agreement', 'indenture', 'covenants']))
                    .group_by(Company.id)
                    .order_by(Company.ticker)
                )
                companies = list(result.scalars())
                if args.limit:
                    companies = companies[:args.limit]

        print(f"Processing {len(companies)} companies")
        extractor = CovenantExtractor()
        total = 0

        for company in companies:
            if not company:
                print("Company not found")
                continue

            async with async_session() as db:
                print(f"\n[{company.ticker}] {company.name}")

                # Check existing covenants
                if not args.force:
                    result = await db.execute(
                        select(Covenant).where(Covenant.company_id == company.id).limit(1)
                    )
                    if result.scalar_one_or_none():
                        print("  Skipping: covenants already extracted (use --force)")
                        continue

                count = await extractor.extract(db, company.id, company.ticker, {})
                print(f"  Covenants extracted: {count}")
                total += count

            await asyncio.sleep(1)

        print(f"\n{'='*50}")
        print(f"Total covenant records created: {total}")
        await engine.dispose()

    asyncio.run(main())
