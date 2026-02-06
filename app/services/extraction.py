"""
SEC EDGAR Filing Extraction Service
====================================

Core extraction service for SEC filings. Downloads filings and extracts
corporate structure + debt data using Claude.

COMPONENTS (now split into separate modules)
--------------------------------------------
- sec_client.py: SecApiClient, SECEdgarClient, FilingInfo
- llm_utils.py: LLM client utilities
- base_extractor.py: Base class for extraction services

This file contains:
- Extraction prompt and models
- ExtractionService class
- Database persistence functions

USAGE
-----
    from app.services.extraction import ExtractionService, save_extraction_to_db

    service = ExtractionService(anthropic_api_key, sec_api_key)
    result = await service.extract_company(cik, ticker)
    company_id = await save_extraction_to_db(session, result, ticker)
"""

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional
from uuid import UUID, uuid4

import anthropic
import httpx
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Company, CompanyCache, CompanyFinancials, CompanyMetrics, Collateral,
    DebtInstrument, DebtInstrumentDocument, DocumentSection, Entity, Guarantee, OwnershipLink
)
from app.services.utils import clean_filing_html

# Re-export SEC clients for backwards compatibility
from app.services.sec_client import SecApiClient, SECEdgarClient, FilingInfo


# =============================================================================
# EXTRACTION PROMPT
# =============================================================================

EXTRACTION_PROMPT = """You are a credit analyst extracting corporate structure and debt data from SEC filings.

TASK: Extract ALL subsidiaries and ALL debt instruments from these filings. Be thorough and comprehensive.

=== WHERE TO FIND DATA ===

SUBSIDIARIES - Search for these patterns:
1. "Exhibit 21" or "Subsidiaries of the Registrant" - THIS IS THE PRIMARY SOURCE. It lists ALL significant subsidiaries.
2. Look for tables with columns like: "Name of Subsidiary", "State/Jurisdiction", "Ownership %"
3. The exhibit often appears near the end of 10-K filings
4. Also check: "Significant Subsidiaries", "List of Subsidiaries", "Consolidated Subsidiaries"

DEBT INSTRUMENTS - Search for these patterns:
1. "Long-term debt" or "Long-Term Debt" table in Notes to Financial Statements
2. "Debt and Credit Facilities" or "Notes Payable" sections
3. "Liquidity and Capital Resources" in MD&A
4. Tables showing: Principal Amount, Interest Rate, Maturity Date
5. 8-K filings for new credit agreements (Exhibit 10.1)
6. Look for: "Term Loan", "Revolving Credit", "Senior Notes", "Commercial Paper"

OUTSTANDING AMOUNTS - Critical for debt:
1. Look for "carrying value", "principal amount", "aggregate principal", "outstanding balance"
2. Check the debt maturity schedule table
3. For notes: face value or principal amount
4. For term loans: outstanding balance after amortization
5. For revolvers: drawn amount vs commitment

=== OUTPUT FORMAT ===

Return a valid JSON object:

{{
  "company_name": "Full legal company name",
  "ticker": "Stock ticker symbol",
  "sector": "Industry sector (Technology, Healthcare, Consumer, Industrial, Financial, Energy, etc.)",
  "entities": [
    {{
      "name": "Exact legal entity name as shown in filings",
      "entity_type": "holdco|opco|subsidiary|spv|jv|finco|vie",
      "jurisdiction": "State or country (e.g., Delaware, California, Ireland, Netherlands)",
      "formation_type": "LLC|Corp|LP|Ltd|Inc|NV|BV|GmbH|SA",
      "owners": [
        {{
          "parent_name": "Name of parent entity (must match another entity's name exactly)",
          "ownership_pct": 100,
          "ownership_type": "direct",
          "is_joint_venture": false,
          "jv_partner_name": null
        }}
      ],
      "consolidation_method": "full",
      "is_guarantor": false,
      "is_borrower": false,
      "is_restricted": true,
      "is_unrestricted": false,
      "is_material": true,
      "is_domestic": true,
      "is_vie": false,
      "vie_primary_beneficiary": false
    }}
  ],
  "debt_instruments": [
    {{
      "name": "Descriptive name (e.g., '3.75% Senior Notes due 2027' or 'Term Loan B')",
      "instrument_type": "term_loan_b|term_loan_a|revolver|senior_notes|senior_secured_notes|subordinated_notes|abl|convertible_notes|commercial_paper",
      "seniority": "senior_secured|senior_unsecured|subordinated|junior_subordinated",
      "security_type": "first_lien|second_lien|unsecured",
      "issuer_name": "Name of issuing entity (must match an entity name exactly)",
      "commitment": null,
      "principal": 150000000000,
      "outstanding": 150000000000,
      "currency": "USD",
      "rate_type": "fixed|floating",
      "interest_rate": 375,
      "spread_bps": null,
      "benchmark": null,
      "floor_bps": null,
      "issue_date": "2020-05-15",
      "maturity_date": "2027-05-15",
      "guarantor_names": [],
      "attributes": {{}}
    }}
  ],
  "uncertainties": []
}}

=== CRITICAL RULES ===

AMOUNTS (all in cents, multiply dollars by 100):
- $1.5 billion = 150000000000 (cents)
- $500 million = 50000000000 (cents)
- $10 million = 1000000000 (cents)
- ALWAYS populate "outstanding" - use principal if outstanding not explicitly stated
- For term loans, use the current outstanding balance
- For notes, use the aggregate principal amount
- For revolvers, use drawn amount for "outstanding", total facility size for "commitment"

INTEREST RATES (in basis points):
- 3.75% = 375 bps
- 8.50% = 850 bps
- SOFR + 200bps: set spread_bps=200, benchmark="SOFR", rate_type="floating"

ENTITY HIERARCHY:
- First entity should be the ultimate parent (holdco) with owners: []
- All other entities need an owner that references another entity by exact name
- entity_type guide:
  - "holdco": Ultimate parent company (the public company)
  - "opco": Main operating company
  - "finco": Financing subsidiary (issues debt)
  - "subsidiary": Regular subsidiary
  - "spv": Special purpose vehicle
  - "jv": Joint venture (set is_joint_venture: true)
  - "vie": Variable interest entity

SUBSIDIARIES TO INCLUDE:
- Include ALL subsidiaries from Exhibit 21, not just "significant" ones
- Include foreign subsidiaries with their country jurisdiction
- If ownership % not stated, assume 100%
- For indirect ownership, still link to immediate parent

GUARANTORS:
- Check credit agreements for "Guarantor" or "Subsidiary Guarantor" lists
- Domestic subsidiaries are often guarantors on secured debt
- Add their names to guarantor_names array on the debt instrument
- Also set is_guarantor: true on the entity

=== FILINGS TO ANALYZE ===

<filings>
{filing_content}
</filings>

=== FINAL CHECKLIST ===
Before returning, verify:
1. Did I extract ALL subsidiaries from Exhibit 21? (There are often 50-200+ subsidiaries)
2. Did I populate "outstanding" for EVERY debt instrument? (Use principal if needed)
3. Did I set the correct issuer_name that matches an entity?
4. Did I identify guarantors from credit agreement language?
5. Are all amounts in CENTS (not dollars)?

Return ONLY the JSON object, no explanation or markdown."""


# =============================================================================
# VALIDATION MODELS
# =============================================================================


class OwnerInfo(BaseModel):
    """Ownership relationship for an entity."""
    parent_name: str
    ownership_pct: Optional[float] = 100.0
    ownership_type: str = "direct"  # direct, indirect, economic_only, voting_only
    is_joint_venture: bool = False
    jv_partner_name: Optional[str] = None


class ExtractedEntity(BaseModel):
    """Validated entity from extraction."""
    name: str
    entity_type: str
    jurisdiction: Optional[str] = None
    formation_type: Optional[str] = None
    # New: support multiple owners
    owners: list[OwnerInfo] = Field(default_factory=list)
    # Legacy: keep for backwards compatibility
    parent_name: Optional[str] = None
    ownership_pct: Optional[float] = 100.0
    # Consolidation
    consolidation_method: Optional[str] = "full"  # full, equity_method, proportional, vie, unconsolidated
    # Status flags
    is_guarantor: bool = False
    is_borrower: bool = False
    is_restricted: bool = True
    is_unrestricted: bool = False
    is_material: bool = False
    is_domestic: bool = True
    # VIE flags
    is_vie: bool = False
    vie_primary_beneficiary: bool = False

    @field_validator("entity_type")
    @classmethod
    def validate_entity_type(cls, v: str) -> str:
        valid_types = {"holdco", "opco", "subsidiary", "spv", "jv", "finco", "vie"}
        if v.lower() not in valid_types:
            return "subsidiary"  # Default fallback
        return v.lower()

    def get_owners(self) -> list[OwnerInfo]:
        """Get all owners, falling back to legacy parent_name if owners is empty."""
        if self.owners:
            return self.owners
        elif self.parent_name:
            return [OwnerInfo(
                parent_name=self.parent_name,
                ownership_pct=self.ownership_pct or 100.0,
                ownership_type="direct"
            )]
        return []


class ExtractedDebtInstrument(BaseModel):
    """Validated debt instrument from extraction."""
    name: str
    instrument_type: str
    seniority: str
    security_type: Optional[str] = None
    issuer_name: str
    commitment: Optional[int] = None
    principal: Optional[int] = None
    outstanding: Optional[int] = None
    currency: str = "USD"
    rate_type: Optional[str] = None
    interest_rate: Optional[int] = None
    spread_bps: Optional[int] = None
    benchmark: Optional[str] = None
    floor_bps: Optional[int] = None
    issue_date: Optional[str] = None
    maturity_date: Optional[str] = None
    guarantor_names: list[str] = Field(default_factory=list)
    attributes: dict = Field(default_factory=dict)

    @field_validator("seniority")
    @classmethod
    def validate_seniority(cls, v: str) -> str:
        valid = {"senior_secured", "senior_unsecured", "subordinated", "junior_subordinated"}
        if v.lower() not in valid:
            return "senior_unsecured"
        return v.lower()

    @field_validator("security_type")
    @classmethod
    def validate_security_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        valid = {"first_lien", "second_lien", "unsecured"}
        if v.lower() not in valid:
            return "unsecured"
        return v.lower()


class ExtractionResult(BaseModel):
    """Complete validated extraction result."""
    company_name: str
    ticker: Optional[str] = None
    sector: Optional[str] = None
    entities: list[ExtractedEntity]
    debt_instruments: list[ExtractedDebtInstrument]
    uncertainties: list[str] = Field(default_factory=list)


# =============================================================================
# EXTRACTION SERVICE
# =============================================================================


class ExtractionService:
    """Service for extracting corporate structure from SEC filings."""

    def __init__(self, anthropic_api_key: str, sec_api_key: str = None):
        self.client = anthropic.Anthropic(api_key=anthropic_api_key)
        self.edgar = SECEdgarClient()
        self.sec_api = SecApiClient(sec_api_key) if sec_api_key else None

    async def close(self):
        await self.edgar.close()

    def _clean_filing_content(self, content: str, max_chars: int = 100000) -> str:
        """Clean and truncate filing content for Claude."""
        # Remove HTML tags
        content = re.sub(r"<[^>]+>", " ", content)
        # Remove excessive whitespace
        content = re.sub(r"\s+", " ", content)
        # Remove common boilerplate
        content = re.sub(r"UNITED STATES SECURITIES AND EXCHANGE COMMISSION.*?FORM \d+-\w+", "", content, flags=re.DOTALL)
        # Truncate if too long
        if len(content) > max_chars:
            content = content[:max_chars] + "\n\n[TRUNCATED]"
        return content.strip()

    def _combine_filings(self, filings: dict[str, str], max_total_chars: int = 350000) -> str:
        """
        Combine multiple filings into a single prompt-ready string.
        Prioritizes more recent filings and important document types.
        """
        # Sort filings by priority and date
        def filing_priority(key: str) -> tuple:
            # Extract date and type
            parts = key.split("_")
            form_type = parts[0] if parts else ""
            date_str = parts[1] if len(parts) > 1 else "1900-01-01"

            # Priority order: 10-K > 8-K with exhibits > 10-Q > 8-K > exhibits
            type_priority = {
                "10-K": 0,
                "8-K": 1,
                "10-Q": 2,
                "exhibit": 3,
            }

            priority = 4
            for t, p in type_priority.items():
                if t in form_type or t in key:
                    priority = p
                    break

            return (priority, date_str)  # Lower is higher priority, newer dates preferred

        sorted_keys = sorted(filings.keys(), key=filing_priority)

        combined_parts = []
        total_chars = 0
        chars_per_filing = max_total_chars // max(len(filings), 1)

        for key in sorted_keys:
            content = filings[key]
            cleaned = self._clean_filing_content(content, max_chars=chars_per_filing)

            if total_chars + len(cleaned) > max_total_chars:
                # Truncate to fit
                remaining = max_total_chars - total_chars
                if remaining > 10000:  # Only include if meaningful amount left
                    cleaned = cleaned[:remaining] + "\n[TRUNCATED]"
                else:
                    break

            section = f"\n{'='*60}\nFILING: {key}\n{'='*60}\n{cleaned}"
            combined_parts.append(section)
            total_chars += len(section)

        return "\n\n".join(combined_parts)

    def _parse_extraction_response(self, response_text: str) -> ExtractionResult:
        """Parse and validate Claude's extraction response."""
        # Try to extract JSON from response
        try:
            # Handle potential markdown code blocks
            if "```json" in response_text:
                json_match = re.search(r"```json\s*(.*?)\s*```", response_text, re.DOTALL)
                if json_match:
                    response_text = json_match.group(1)
            elif "```" in response_text:
                json_match = re.search(r"```\s*(.*?)\s*```", response_text, re.DOTALL)
                if json_match:
                    response_text = json_match.group(1)

            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse extraction response as JSON: {e}")

        return ExtractionResult(**data)

    async def extract_from_filings(
        self, filings: dict[str, str]
    ) -> ExtractionResult:
        """Extract corporate structure from multiple filings using Claude."""
        combined_content = self._combine_filings(filings)

        print(f"  Combined filings: {len(combined_content):,} characters")

        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=8000,
            messages=[
                {
                    "role": "user",
                    "content": EXTRACTION_PROMPT.format(filing_content=combined_content),
                }
            ],
        )

        response_text = response.content[0].text
        return self._parse_extraction_response(response_text)

    async def extract_company(
        self, cik: str, ticker: str
    ) -> ExtractionResult:
        """
        Full extraction pipeline: download all relevant filings and extract structure.

        Uses SEC-API.io if available (faster, no rate limits), falls back to direct SEC EDGAR.
        """
        filings = {}

        # Try SEC-API.io first (faster, no rate limits)
        if self.sec_api and self.sec_api.query_api:
            print(f"\n  Fetching filings via SEC-API.io...")
            filings, _ = await self.sec_api.get_all_relevant_filings(ticker, include_exhibits=True)

        # Fall back to direct SEC EDGAR if SEC-API didn't work
        if not filings:
            print(f"\n  Fetching filings from SEC EDGAR (direct)...")
            filings, _ = await self.edgar.get_all_relevant_filings(cik, include_exhibits=True)

        if not filings:
            raise ValueError(f"No filings found for {ticker} (CIK: {cik})")

        print(f"\n  Extracting with Claude...")

        # Extract using Claude
        result = await self.extract_from_filings(filings)

        # Override ticker if not extracted
        if not result.ticker:
            result.ticker = ticker.upper()

        return result


# =============================================================================
# DATABASE SAVE LOGIC
# =============================================================================


def slugify(text: str) -> str:
    """Create URL-safe slug from text."""
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:255]


def parse_date(date_str: Optional[str]) -> Optional[date]:
    """Parse date string to date object."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None


def estimate_issue_date(
    maturity_date: Optional[date],
    instrument_name: str,
    instrument_type: str,
) -> Optional[date]:
    """
    Estimate issue date when not explicitly provided.

    Uses common bond/loan tenors:
    - Senior notes: typically 7-10 year tenor
    - Term loans: typically 5-7 year tenor
    - Revolvers: typically 5 year tenor
    - If name contains year hints (e.g., "due 2030"), infer from that
    """
    if not maturity_date:
        return None

    # Default tenors by instrument type (in years)
    default_tenors = {
        "senior_notes": 10,
        "senior_secured_notes": 7,
        "subordinated_notes": 10,
        "term_loan_b": 7,
        "term_loan_a": 5,
        "term_loan": 7,
        "revolver": 5,
        "abl": 5,
        "convertible_notes": 5,
    }

    tenor_years = default_tenors.get(instrument_type.lower(), 7)

    # Check for tenor hints in name (e.g., "5-year", "10yr")
    import re
    tenor_match = re.search(r'(\d+)[-\s]?(?:year|yr)', instrument_name.lower())
    if tenor_match:
        tenor_years = int(tenor_match.group(1))

    # Calculate estimated issue date
    from dateutil.relativedelta import relativedelta
    estimated = maturity_date - relativedelta(years=tenor_years)

    return estimated


# =============================================================================
# IDEMPOTENT DATA CHECKS
# =============================================================================


async def check_existing_data(db: AsyncSession, ticker: str) -> dict:
    """
    Check what data already exists for a company.

    Returns dict with:
        - exists: bool - Whether the company exists
        - company_id: UUID - The company ID if exists
        - entity_count: int - Number of entities
        - debt_count: int - Number of debt instruments
        - has_financials: bool - Whether financials exist
        - has_hierarchy: bool - Whether ownership links exist
        - guarantee_count: int - Number of guarantees
        - collateral_count: int - Number of collateral records
        - document_section_count: int - Number of document sections
    """
    ticker = ticker.upper()

    # Check if company exists
    result = await db.execute(
        select(Company).where(Company.ticker == ticker)
    )
    company = result.scalar_one_or_none()

    if not company:
        return {'exists': False}

    company_id = company.id

    # Count entities
    result = await db.execute(
        select(func.count()).select_from(Entity).where(Entity.company_id == company_id)
    )
    entity_count = result.scalar() or 0

    # Count debt instruments
    result = await db.execute(
        select(func.count()).select_from(DebtInstrument).where(DebtInstrument.company_id == company_id)
    )
    debt_count = result.scalar() or 0

    # Check financials
    result = await db.execute(
        select(func.count()).select_from(CompanyFinancials).where(CompanyFinancials.company_id == company_id)
    )
    financials_count = result.scalar() or 0

    # Check ownership links (hierarchy)
    result = await db.execute(
        select(func.count()).select_from(OwnershipLink).where(
            OwnershipLink.parent_entity_id.in_(
                select(Entity.id).where(Entity.company_id == company_id)
            )
        )
    )
    ownership_link_count = result.scalar() or 0

    # Count guarantees
    result = await db.execute(
        select(func.count()).select_from(Guarantee)
        .join(DebtInstrument)
        .where(DebtInstrument.company_id == company_id)
    )
    guarantee_count = result.scalar() or 0

    # Count collateral
    result = await db.execute(
        select(func.count()).select_from(Collateral)
        .join(DebtInstrument)
        .where(DebtInstrument.company_id == company_id)
    )
    collateral_count = result.scalar() or 0

    # Count document sections
    result = await db.execute(
        select(func.count()).select_from(DocumentSection).where(DocumentSection.company_id == company_id)
    )
    document_section_count = result.scalar() or 0

    # Count document links (DebtInstrumentDocument)
    result = await db.execute(
        select(func.count()).select_from(DebtInstrumentDocument)
        .join(DebtInstrument)
        .where(DebtInstrument.company_id == company_id)
    )
    document_link_count = result.scalar() or 0

    # Get extraction status from cache
    result = await db.execute(
        select(CompanyCache).where(CompanyCache.company_id == company_id)
    )
    cache = result.scalar_one_or_none()
    extraction_status = cache.extraction_status if cache else None

    return {
        'exists': True,
        'company_id': company_id,
        'entity_count': entity_count,
        'debt_count': debt_count,
        'has_financials': financials_count > 0,
        'financials_count': financials_count,
        'has_hierarchy': ownership_link_count > 0,
        'ownership_link_count': ownership_link_count,
        'guarantee_count': guarantee_count,
        'collateral_count': collateral_count,
        'document_section_count': document_section_count,
        'document_link_count': document_link_count,
        'extraction_status': extraction_status or {},
    }


async def update_extraction_status(
    db: AsyncSession,
    company_id: UUID,
    step: str,
    status: str,
    details: str = None,
    metadata: dict = None,
) -> None:
    """
    Update extraction status for a specific step.

    Args:
        db: Database session
        company_id: Company UUID
        step: Step name (core, document_sections, financials, hierarchy, guarantees, collateral)
        status: Status (success, no_data, error)
        details: Optional details about the result
        metadata: Optional additional metadata (e.g., {"latest_quarter": "2025Q3"})
    """
    from datetime import datetime

    # Get or create cache record
    result = await db.execute(
        select(CompanyCache).where(CompanyCache.company_id == company_id)
    )
    cache = result.scalar_one_or_none()

    if not cache:
        # Cache doesn't exist yet, will be created by save/merge functions
        return

    # Update extraction_status JSONB field
    current_status = cache.extraction_status or {}
    current_status[step] = {
        'status': status,
        'attempted_at': datetime.utcnow().isoformat(),
    }
    if details:
        current_status[step]['details'] = details
    if metadata:
        current_status[step].update(metadata)

    cache.extraction_status = current_status
    await db.commit()


async def merge_extraction_to_db(
    db: AsyncSession,
    extraction: ExtractionResult,
    ticker: str,
    cik: Optional[str] = None,
    filing_date: Optional[date] = None,
    update_existing: bool = True,
) -> tuple[UUID, dict]:
    """
    Merge extracted data with existing database records (idempotent).

    Unlike save_extraction_to_db which replaces all data, this function:
    - Adds NEW entities not already in DB
    - Adds NEW debt instruments not already in DB
    - Optionally UPDATES existing entities/debt with new field values
    - Updates company metadata (name, sector)

    Args:
        db: Database session
        extraction: Extracted data
        ticker: Stock ticker
        cik: SEC CIK number
        filing_date: Filing date for cache
        update_existing: If True, update fields on existing records (default True)

    Returns:
        Tuple of (company_id, stats_dict)
    """
    ticker = ticker.upper()
    stats = {
        'entities_added': 0,
        'entities_updated': 0,
        'debt_added': 0,
        'debt_updated': 0,
        'guarantees_added': 0,
    }

    # 1. Get or create company
    result = await db.execute(
        select(Company).where(Company.ticker == ticker)
    )
    company = result.scalar_one_or_none()

    if company:
        # Update company metadata
        company.name = extraction.company_name
        company.sector = extraction.sector
        if cik:
            company.cik = cik
    else:
        company = Company(
            id=uuid4(),
            ticker=ticker,
            name=extraction.company_name,
            sector=extraction.sector,
            cik=cik,
        )
        db.add(company)

    await db.flush()
    company_id = company.id

    # 2. Get existing entities (for matching and updating)
    result = await db.execute(
        select(Entity).where(Entity.company_id == company_id)
    )
    existing_entities = result.scalars().all()

    # Build lookups: normalized name -> entity object
    existing_entity_by_name = {}
    for e in existing_entities:
        existing_entity_by_name[e.name.lower().strip()] = e

    entity_name_to_id = {e.name: e.id for e in existing_entities}

    # 3. Process entities: add new OR update existing
    for ext_entity in extraction.entities:
        normalized_name = ext_entity.name.lower().strip()
        existing_entity = existing_entity_by_name.get(normalized_name)

        # Determine structure tier
        tier = 3
        if ext_entity.entity_type == "holdco":
            tier = 1
        elif ext_entity.entity_type in ("opco", "finco"):
            tier = 2
        elif ext_entity.entity_type == "subsidiary":
            tier = 3
        elif ext_entity.entity_type in ("spv", "vie"):
            tier = 4

        owners = ext_entity.get_owners()
        primary_ownership_pct = owners[0].ownership_pct if owners else 100.0

        if existing_entity:
            # UPDATE existing entity if update_existing is True
            if update_existing:
                updated = False

                # Update fields if they have new non-null values
                if ext_entity.jurisdiction and ext_entity.jurisdiction != existing_entity.jurisdiction:
                    existing_entity.jurisdiction = ext_entity.jurisdiction
                    updated = True
                if ext_entity.formation_type and ext_entity.formation_type != existing_entity.formation_type:
                    existing_entity.formation_type = ext_entity.formation_type
                    updated = True
                if ext_entity.entity_type and ext_entity.entity_type != existing_entity.entity_type:
                    existing_entity.entity_type = ext_entity.entity_type
                    existing_entity.structure_tier = tier
                    updated = True
                if ext_entity.is_guarantor and not existing_entity.is_guarantor:
                    existing_entity.is_guarantor = True
                    updated = True
                if ext_entity.is_borrower and not existing_entity.is_borrower:
                    existing_entity.is_borrower = True
                    updated = True
                if ext_entity.is_unrestricted and not existing_entity.is_unrestricted:
                    existing_entity.is_unrestricted = True
                    updated = True
                if ext_entity.is_vie and not existing_entity.is_vie:
                    existing_entity.is_vie = True
                    updated = True

                if updated:
                    stats['entities_updated'] += 1

            entity_name_to_id[ext_entity.name] = existing_entity.id
        else:
            # ADD new entity
            entity_id = uuid4()
            entity_name_to_id[ext_entity.name] = entity_id

            entity = Entity(
                id=entity_id,
                company_id=company_id,
                name=ext_entity.name,
                slug=slugify(ext_entity.name),
                entity_type=ext_entity.entity_type,
                jurisdiction=ext_entity.jurisdiction,
                formation_type=ext_entity.formation_type,
                structure_tier=tier,
                ownership_pct=primary_ownership_pct,
                is_guarantor=ext_entity.is_guarantor,
                is_borrower=ext_entity.is_borrower,
                is_restricted=ext_entity.is_restricted,
                is_unrestricted=ext_entity.is_unrestricted,
                is_material=ext_entity.is_material,
                is_domestic=ext_entity.is_domestic,
                is_vie=ext_entity.is_vie,
                vie_primary_beneficiary=ext_entity.vie_primary_beneficiary,
                consolidation_method=ext_entity.consolidation_method,
            )
            db.add(entity)
            stats['entities_added'] += 1

    await db.flush()

    # 4. Set parent relationships for entities (new and existing without parents)
    for ext_entity in extraction.entities:
        entity_id = entity_name_to_id.get(ext_entity.name)
        if not entity_id:
            continue

        owners = ext_entity.get_owners()
        if not owners:
            continue

        primary_owner = owners[0]
        parent_id = entity_name_to_id.get(primary_owner.parent_name)
        if parent_id:
            result = await db.execute(select(Entity).where(Entity.id == entity_id))
            entity = result.scalar_one_or_none()
            if entity and entity.parent_id != parent_id:
                entity.parent_id = parent_id

    await db.flush()

    # 5. Get existing debt instruments
    result = await db.execute(
        select(DebtInstrument).where(DebtInstrument.company_id == company_id)
    )
    existing_debt = result.scalars().all()

    existing_debt_by_name = {}
    for d in existing_debt:
        existing_debt_by_name[d.name.lower().strip()] = d

    used_debt_slugs = {d.slug for d in existing_debt if d.slug}

    # 6. Process debt instruments: add new OR update existing
    for ext_debt in extraction.debt_instruments:
        normalized_name = ext_debt.name.lower().strip()
        existing_debt_inst = existing_debt_by_name.get(normalized_name)

        parsed_issue_date = parse_date(ext_debt.issue_date)
        parsed_maturity_date = parse_date(ext_debt.maturity_date)
        issue_date_estimated = False

        if not parsed_issue_date and parsed_maturity_date:
            parsed_issue_date = estimate_issue_date(
                parsed_maturity_date,
                ext_debt.name,
                ext_debt.instrument_type,
            )
            issue_date_estimated = True

        if existing_debt_inst:
            # UPDATE existing debt instrument if update_existing is True
            if update_existing:
                updated = False

                # Update outstanding amount if newer/different
                new_outstanding = ext_debt.outstanding or ext_debt.principal
                if new_outstanding and new_outstanding != existing_debt_inst.outstanding:
                    existing_debt_inst.outstanding = new_outstanding
                    updated = True

                # Update interest rate if provided
                if ext_debt.interest_rate and ext_debt.interest_rate != existing_debt_inst.interest_rate:
                    existing_debt_inst.interest_rate = ext_debt.interest_rate
                    updated = True

                # Update spread if provided
                if ext_debt.spread_bps and ext_debt.spread_bps != existing_debt_inst.spread_bps:
                    existing_debt_inst.spread_bps = ext_debt.spread_bps
                    updated = True

                # Update maturity date if provided and different
                if parsed_maturity_date and parsed_maturity_date != existing_debt_inst.maturity_date:
                    existing_debt_inst.maturity_date = parsed_maturity_date
                    updated = True

                # Update issue date if not estimated and different
                if parsed_issue_date and not issue_date_estimated:
                    if parsed_issue_date != existing_debt_inst.issue_date:
                        existing_debt_inst.issue_date = parsed_issue_date
                        existing_debt_inst.issue_date_estimated = False
                        updated = True

                # Update benchmark if provided
                if ext_debt.benchmark and ext_debt.benchmark != existing_debt_inst.benchmark:
                    existing_debt_inst.benchmark = ext_debt.benchmark
                    updated = True

                # Update rate_type if provided
                if ext_debt.rate_type and ext_debt.rate_type != existing_debt_inst.rate_type:
                    existing_debt_inst.rate_type = ext_debt.rate_type
                    updated = True

                # Update seniority if different
                if ext_debt.seniority and ext_debt.seniority != existing_debt_inst.seniority:
                    existing_debt_inst.seniority = ext_debt.seniority
                    updated = True

                # Update security_type if provided
                if ext_debt.security_type and ext_debt.security_type != existing_debt_inst.security_type:
                    existing_debt_inst.security_type = ext_debt.security_type
                    updated = True

                if updated:
                    stats['debt_updated'] += 1

            # Add any new guarantees for existing debt
            for guarantor_name in ext_debt.guarantor_names:
                guarantor_id = entity_name_to_id.get(guarantor_name)
                if guarantor_id:
                    # Check if guarantee already exists
                    existing_guarantee = await db.execute(
                        select(Guarantee).where(
                            Guarantee.debt_instrument_id == existing_debt_inst.id,
                            Guarantee.guarantor_id == guarantor_id
                        )
                    )
                    if not existing_guarantee.scalar_one_or_none():
                        guarantee = Guarantee(
                            id=uuid4(),
                            debt_instrument_id=existing_debt_inst.id,
                            guarantor_id=guarantor_id,
                            guarantee_type="full",
                        )
                        db.add(guarantee)
                        stats['guarantees_added'] += 1
        else:
            # ADD new debt instrument
            issuer_id = entity_name_to_id.get(ext_debt.issuer_name)
            if not issuer_id:
                continue

            # Generate unique slug
            base_slug = slugify(ext_debt.name)
            slug = base_slug
            counter = 2
            while slug in used_debt_slugs:
                slug = f"{base_slug}-{counter}"
                counter += 1
            used_debt_slugs.add(slug)

            debt_id = uuid4()

            debt = DebtInstrument(
                id=debt_id,
                company_id=company_id,
                issuer_id=issuer_id,
                name=ext_debt.name,
                slug=slug,
                instrument_type=ext_debt.instrument_type,
                seniority=ext_debt.seniority,
                security_type=ext_debt.security_type,
                commitment=ext_debt.commitment,
                principal=ext_debt.principal,
                outstanding=ext_debt.outstanding or ext_debt.principal,
                currency=ext_debt.currency,
                rate_type=ext_debt.rate_type,
                interest_rate=ext_debt.interest_rate,
                spread_bps=ext_debt.spread_bps,
                benchmark=ext_debt.benchmark,
                floor_bps=ext_debt.floor_bps,
                issue_date=parsed_issue_date,
                issue_date_estimated=issue_date_estimated,
                maturity_date=parsed_maturity_date,
                attributes=ext_debt.attributes,
            )
            db.add(debt)
            stats['debt_added'] += 1

            # Create guarantees for new debt
            for guarantor_name in ext_debt.guarantor_names:
                guarantor_id = entity_name_to_id.get(guarantor_name)
                if guarantor_id:
                    guarantee = Guarantee(
                        id=uuid4(),
                        debt_instrument_id=debt_id,
                        guarantor_id=guarantor_id,
                        guarantee_type="full",
                    )
                    db.add(guarantee)
                    stats['guarantees_added'] += 1

    await db.flush()

    # 7. Refresh cache/metrics
    await refresh_company_cache(db, company_id, ticker, filing_date)

    await db.commit()

    return company_id, stats


async def save_extraction_to_db(
    db: AsyncSession,
    extraction: ExtractionResult,
    ticker: str,
    cik: Optional[str] = None,
    filing_date: Optional[date] = None,
) -> UUID:
    """
    Save extracted data to normalized database tables.
    Returns the company ID.
    """
    ticker = ticker.upper()

    # 1. Create or update company
    result = await db.execute(
        select(Company).where(Company.ticker == ticker)
    )
    company = result.scalar_one_or_none()

    if company:
        company.name = extraction.company_name
        company.sector = extraction.sector
        if cik:
            company.cik = cik
    else:
        company = Company(
            id=uuid4(),
            ticker=ticker,
            name=extraction.company_name,
            sector=extraction.sector,
            cik=cik,
        )
        db.add(company)

    await db.flush()
    company_id = company.id

    # 2. Delete existing data in correct order (respect foreign key constraints)
    # First delete debt instruments (references entities via issuer_id)
    await db.execute(delete(DebtInstrument).where(DebtInstrument.company_id == company_id))
    # Then delete ownership links (references entities)
    await db.execute(delete(OwnershipLink).where(
        OwnershipLink.parent_entity_id.in_(
            select(Entity.id).where(Entity.company_id == company_id)
        )
    ))
    # Finally delete entities
    await db.execute(delete(Entity).where(Entity.company_id == company_id))
    await db.flush()

    # 3. Create entities (first pass - no parent relationships)
    entity_name_to_id: dict[str, UUID] = {}

    for i, ext_entity in enumerate(extraction.entities):
        entity_id = uuid4()
        entity_name_to_id[ext_entity.name] = entity_id

        # Determine structure tier based on entity type
        tier = 3  # default to opco level
        if ext_entity.entity_type == "holdco":
            tier = 1
        elif ext_entity.entity_type in ("opco", "finco"):
            tier = 2
        elif ext_entity.entity_type == "subsidiary":
            tier = 3
        elif ext_entity.entity_type in ("spv", "vie"):
            tier = 4

        # Get primary ownership percentage (for simple parent_id relationship)
        owners = ext_entity.get_owners()
        primary_ownership_pct = owners[0].ownership_pct if owners else 100.0

        entity = Entity(
            id=entity_id,
            company_id=company_id,
            name=ext_entity.name,
            slug=slugify(ext_entity.name),
            entity_type=ext_entity.entity_type,
            jurisdiction=ext_entity.jurisdiction,
            formation_type=ext_entity.formation_type,
            structure_tier=tier,
            ownership_pct=primary_ownership_pct,
            is_guarantor=ext_entity.is_guarantor,
            is_borrower=ext_entity.is_borrower,
            is_restricted=ext_entity.is_restricted,
            is_unrestricted=ext_entity.is_unrestricted,
            is_material=ext_entity.is_material,
            is_domestic=ext_entity.is_domestic,
            is_vie=ext_entity.is_vie,
            vie_primary_beneficiary=ext_entity.vie_primary_beneficiary,
            consolidation_method=ext_entity.consolidation_method,
        )
        db.add(entity)

    await db.flush()

    # 4. Set parent relationships and create ownership_links
    for ext_entity in extraction.entities:
        entity_id = entity_name_to_id[ext_entity.name]
        owners = ext_entity.get_owners()

        if not owners:
            continue

        # Set primary parent (first owner) for simple tree navigation
        primary_owner = owners[0]
        if primary_owner.parent_name in entity_name_to_id:
            parent_id = entity_name_to_id[primary_owner.parent_name]
            result = await db.execute(
                select(Entity).where(Entity.id == entity_id)
            )
            entity = result.scalar_one()
            entity.parent_id = parent_id

        # Create ownership_links for ALL owners (including the primary)
        for owner in owners:
            # Try to find parent by exact name first
            parent_entity_id = entity_name_to_id.get(owner.parent_name)

            # If not found, try case-insensitive match
            if parent_entity_id is None:
                parent_name_lower = owner.parent_name.lower().strip()
                for name, eid in entity_name_to_id.items():
                    if name.lower().strip() == parent_name_lower:
                        parent_entity_id = eid
                        break

            if parent_entity_id is None:
                # Parent not found - this is an external owner or name mismatch
                # For JVs with external partners, still record the relationship
                # using the holdco as parent (first entity)
                if owner.is_joint_venture or owner.jv_partner_name:
                    # Find the holdco (first entity, usually the parent company)
                    holdco_id = next(iter(entity_name_to_id.values()), None)
                    if holdco_id:
                        ownership_link = OwnershipLink(
                            id=uuid4(),
                            parent_entity_id=holdco_id,
                            child_entity_id=entity_id,
                            ownership_pct=owner.ownership_pct,
                            ownership_type=owner.ownership_type or "jv_external",
                            is_joint_venture=True,
                            jv_partner_name=owner.jv_partner_name or owner.parent_name,  # Capture external partner name
                            consolidation_method=ext_entity.consolidation_method,
                        )
                        db.add(ownership_link)
                continue

            ownership_link = OwnershipLink(
                id=uuid4(),
                parent_entity_id=parent_entity_id,
                child_entity_id=entity_id,
                ownership_pct=owner.ownership_pct,
                ownership_type=owner.ownership_type,
                is_joint_venture=owner.is_joint_venture,
                jv_partner_name=owner.jv_partner_name,
                consolidation_method=ext_entity.consolidation_method,
            )
            db.add(ownership_link)

    await db.flush()

    # 5. Create debt instruments and guarantees
    used_debt_slugs: set[str] = set()  # Track used slugs to handle duplicates

    for ext_debt in extraction.debt_instruments:
        issuer_id = entity_name_to_id.get(ext_debt.issuer_name)
        if not issuer_id:
            # Issuer not found, skip this debt
            print(f"  Warning: Issuer '{ext_debt.issuer_name}' not found for debt '{ext_debt.name}'")
            continue

        # Generate unique slug (handle duplicates by appending counter)
        base_slug = slugify(ext_debt.name)
        slug = base_slug
        counter = 2
        while slug in used_debt_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1
        used_debt_slugs.add(slug)

        debt_id = uuid4()

        # Parse dates
        parsed_issue_date = parse_date(ext_debt.issue_date)
        parsed_maturity_date = parse_date(ext_debt.maturity_date)
        issue_date_estimated = False

        # If issue_date not provided, try to estimate from maturity and instrument type
        if not parsed_issue_date and parsed_maturity_date:
            parsed_issue_date = estimate_issue_date(
                parsed_maturity_date,
                ext_debt.name,
                ext_debt.instrument_type,
            )
            issue_date_estimated = True  # Mark as estimated

        debt = DebtInstrument(
            id=debt_id,
            company_id=company_id,
            issuer_id=issuer_id,
            name=ext_debt.name,
            slug=slug,
            instrument_type=ext_debt.instrument_type,
            seniority=ext_debt.seniority,
            security_type=ext_debt.security_type,
            commitment=ext_debt.commitment,
            principal=ext_debt.principal,
            outstanding=ext_debt.outstanding or ext_debt.principal,
            currency=ext_debt.currency,
            rate_type=ext_debt.rate_type,
            interest_rate=ext_debt.interest_rate,
            spread_bps=ext_debt.spread_bps,
            benchmark=ext_debt.benchmark,
            floor_bps=ext_debt.floor_bps,
            issue_date=parsed_issue_date,
            issue_date_estimated=issue_date_estimated,
            maturity_date=parsed_maturity_date,
            attributes=ext_debt.attributes,
        )
        db.add(debt)
        await db.flush()

        # Create guarantees
        for guarantor_name in ext_debt.guarantor_names:
            guarantor_id = entity_name_to_id.get(guarantor_name)
            if guarantor_id:
                guarantee = Guarantee(
                    id=uuid4(),
                    debt_instrument_id=debt_id,
                    guarantor_id=guarantor_id,
                    guarantee_type="full",
                )
                db.add(guarantee)

    await db.flush()

    # 6. Compute and save cache/metrics
    await refresh_company_cache(db, company_id, ticker, filing_date)

    await db.commit()
    return company_id


async def refresh_company_cache(
    db: AsyncSession,
    company_id: UUID,
    ticker: str,
    filing_date: Optional[date] = None,
) -> None:
    """Compute and save pre-computed API responses."""

    # Get company
    result = await db.execute(
        select(Company).where(Company.id == company_id)
    )
    company = result.scalar_one()

    # Get entities
    result = await db.execute(
        select(Entity).where(Entity.company_id == company_id)
    )
    entities = result.scalars().all()

    # Get debt instruments with guarantees
    result = await db.execute(
        select(DebtInstrument).where(DebtInstrument.company_id == company_id)
    )
    debt_instruments = result.scalars().all()

    # Build entity lookup
    entity_by_id = {e.id: e for e in entities}

    # Get guarantees
    guarantees_by_debt: dict[UUID, list[UUID]] = {}
    for debt in debt_instruments:
        result = await db.execute(
            select(Guarantee).where(Guarantee.debt_instrument_id == debt.id)
        )
        guarantees = result.scalars().all()
        guarantees_by_debt[debt.id] = [g.guarantor_id for g in guarantees]

    # ==========================================================================
    # Build response_company
    # ==========================================================================
    response_company = {
        "ticker": ticker,
        "name": company.name,
        "sector": company.sector,
        "cik": company.cik,
        "entity_count": len(entities),
        "debt_instrument_count": len(debt_instruments),
        "total_debt": sum(d.outstanding or 0 for d in debt_instruments),
        "as_of_date": filing_date.isoformat() if filing_date else None,
    }

    # ==========================================================================
    # Build response_structure (entity tree)
    # ==========================================================================

    # Identify key entities (issuers and guarantors) - these have known relationships
    issuer_ids = {d.issuer_id for d in debt_instruments if d.issuer_id}
    guarantor_ids = set()
    for debt_id, gids in guarantees_by_debt.items():
        guarantor_ids.update(gids)
    key_entity_ids = issuer_ids | guarantor_ids

    # Classify entities by ownership confidence
    entities_with_known_parent = []  # parent_id is set AND parent is not root, OR entity is key entity
    entities_with_unknown_parent = []  # parent_id is NULL and not root

    root_entity_ids = {e.id for e in entities if e.is_root}

    for e in entities:
        if e.is_root:
            continue
        if e.parent_id is not None:
            # Has a parent - check if it's intermediate (not root)
            parent = entity_by_id.get(e.parent_id)
            if parent and not parent.is_root:
                entities_with_known_parent.append(e)
            elif e.id in key_entity_ids:
                # Key entity linked to root - this is meaningful
                entities_with_known_parent.append(e)
            else:
                # Non-key entity linked to root - we now set these to NULL
                entities_with_unknown_parent.append(e)
        else:
            # No parent set
            if e.id in key_entity_ids:
                entities_with_known_parent.append(e)
            else:
                entities_with_unknown_parent.append(e)

    def build_entity_tree(entity: Entity) -> dict:
        children = [e for e in entities if e.parent_id == entity.id]

        # Get full debt instrument details for this entity
        entity_debt_instruments = [d for d in debt_instruments if d.issuer_id == entity.id]
        debt_details = []
        for d in entity_debt_instruments:
            guar_ids = guarantees_by_debt.get(d.id, [])
            guarantor_names = [entity_by_id[g].name for g in guar_ids if g in entity_by_id]

            debt_details.append({
                "id": str(d.id),
                "name": d.name,
                "type": d.instrument_type,
                "seniority": d.seniority,
                "security_type": d.security_type,
                "outstanding": d.outstanding,
                "principal": d.principal,
                "currency": d.currency,
                "rate_type": d.rate_type,
                "interest_rate": d.interest_rate,
                "spread_bps": d.spread_bps,
                "benchmark": d.benchmark,
                "maturity_date": d.maturity_date.isoformat() if d.maturity_date else None,
                "guarantor_count": len(guarantor_names),
                "guarantors": guarantor_names,
            })

        # Determine ownership confidence for this entity
        is_key = entity.id in key_entity_ids
        has_intermediate_parent = (
            entity.parent_id is not None
            and entity.parent_id not in root_entity_ids
        )

        if entity.is_root:
            ownership_confidence = "root"
        elif has_intermediate_parent:
            ownership_confidence = "verified"  # From indenture/credit agreement parsing
        elif is_key:
            ownership_confidence = "key_entity"  # Issuer or guarantor
        else:
            ownership_confidence = "unknown"  # From Exhibit 21 only

        return {
            "id": str(entity.id),
            "name": entity.name,
            "type": entity.entity_type,
            "tier": entity.structure_tier,
            "jurisdiction": entity.jurisdiction,
            "is_guarantor": entity.is_guarantor,
            "is_borrower": entity.is_borrower,
            "is_unrestricted": entity.is_unrestricted,
            "ownership_pct": float(entity.ownership_pct) if entity.ownership_pct else 100.0,
            "ownership_confidence": ownership_confidence,
            "debt_at_entity": {
                "total": sum(d.outstanding or 0 for d in entity_debt_instruments),
                "instrument_count": len(entity_debt_instruments),
                "instruments": debt_details,
            },
            "children": [build_entity_tree(c) for c in children],
        }

    # Find root entities (is_root=True or no parent for backwards compatibility)
    root_entities = [e for e in entities if e.is_root or (e.parent_id is None and not any(
        other.parent_id == e.id for other in entities
    ) == False and e.is_root)]
    # Simpler: just use is_root
    root_entities = [e for e in entities if e.is_root]
    if not root_entities:
        # Fallback: entities with no parent
        root_entities = [e for e in entities if e.parent_id is None]

    structure_tree = [build_entity_tree(e) for e in root_entities]

    # Build list of entities with unknown parent (for transparency)
    unknown_parent_list = [
        {"name": e.name, "jurisdiction": e.jurisdiction, "entity_type": e.entity_type}
        for e in sorted(entities_with_unknown_parent, key=lambda x: x.name or "")
    ]

    response_structure = {
        "company": {"ticker": ticker, "name": company.name, "sector": company.sector},
        "structure": structure_tree[0] if len(structure_tree) == 1 else {"roots": structure_tree},
        "summary": {
            "total_entities": len(entities),
            "guarantor_count": sum(1 for e in entities if e.is_guarantor),
            "restricted_count": sum(1 for e in entities if e.is_restricted),
            "unrestricted_count": sum(1 for e in entities if e.is_unrestricted),
            "total_debt": sum(d.outstanding or 0 for d in debt_instruments),
        },
        "ownership_coverage": {
            "known_relationships": len(entities_with_known_parent),
            "unknown_relationships": len(entities_with_unknown_parent),
            "key_entities": len(key_entity_ids),
            "coverage_pct": round(
                len(entities_with_known_parent) / max(len(entities) - 1, 1) * 100, 1
            ) if len(entities) > 1 else 100.0,
            "note": "Ownership relationships are only shown where we have evidence from SEC filings (indentures, credit agreements). "
                    "Entities with unknown parent are subsidiaries listed in Exhibit 21 where the intermediate holding structure is not disclosed."
        },
        "other_subsidiaries": {
            "count": len(unknown_parent_list),
            "note": "These subsidiaries exist but their parent company within the corporate structure is unknown from public SEC filings.",
            "entities": unknown_parent_list[:50],  # Limit to 50 for response size
            "truncated": len(unknown_parent_list) > 50,
        } if unknown_parent_list else None,
        "meta": {
            "as_of_date": filing_date.isoformat() if filing_date else None,
            "confidence": "high" if len(entities_with_unknown_parent) == 0 else "partial",
        },
    }

    # ==========================================================================
    # Build response_debt
    # ==========================================================================
    debt_by_seniority: dict[str, int] = {}
    debt_list = []
    for d in debt_instruments:
        issuer = entity_by_id.get(d.issuer_id)
        guarantor_ids = guarantees_by_debt.get(d.id, [])
        guarantor_names = [entity_by_id[g].name for g in guarantor_ids if g in entity_by_id]

        debt_by_seniority[d.seniority] = debt_by_seniority.get(d.seniority, 0) + (d.outstanding or 0)

        debt_list.append({
            "id": str(d.id),
            "name": d.name,
            "type": d.instrument_type,
            "seniority": d.seniority,
            "security_type": d.security_type,
            "issuer": issuer.name if issuer else None,
            "principal": d.principal,
            "outstanding": d.outstanding,
            "currency": d.currency,
            "rate_type": d.rate_type,
            "interest_rate": d.interest_rate,
            "spread_bps": d.spread_bps,
            "benchmark": d.benchmark,
            "maturity_date": d.maturity_date.isoformat() if d.maturity_date else None,
            "guarantor_count": len(guarantor_names),
            "guarantors": guarantor_names,
        })

    response_debt = {
        "company": {"ticker": ticker, "name": company.name},
        "summary": {
            "total_debt": sum(d.outstanding or 0 for d in debt_instruments),
            "debt_by_seniority": debt_by_seniority,
            "instrument_count": len(debt_instruments),
            "nearest_maturity": min(
                (d.maturity_date for d in debt_instruments if d.maturity_date),
                default=None,
            ),
        },
        "instruments": debt_list,
        "meta": {"as_of_date": filing_date.isoformat() if filing_date else None},
    }
    if response_debt["summary"]["nearest_maturity"]:
        response_debt["summary"]["nearest_maturity"] = response_debt["summary"]["nearest_maturity"].isoformat()

    # ==========================================================================
    # Compute ETag
    # ==========================================================================
    cache_content = json.dumps(
        {"structure": response_structure, "debt": response_debt}, sort_keys=True
    ).encode()
    etag = hashlib.md5(cache_content).hexdigest()[:16]

    # ==========================================================================
    # Save to company_cache
    # ==========================================================================
    result = await db.execute(
        select(CompanyCache).where(CompanyCache.company_id == company_id)
    )
    cache = result.scalar_one_or_none()

    if cache:
        cache.response_company = response_company
        cache.response_structure = response_structure
        cache.response_debt = response_debt
        cache.etag = etag
        cache.computed_at = datetime.utcnow()
        cache.source_filing_date = filing_date
        cache.total_debt = sum(d.outstanding or 0 for d in debt_instruments)
        cache.entity_count = len(entities)
        cache.sector = company.sector
    else:
        cache = CompanyCache(
            company_id=company_id,
            ticker=ticker,
            response_company=response_company,
            response_structure=response_structure,
            response_debt=response_debt,
            etag=etag,
            source_filing_date=filing_date,
            total_debt=sum(d.outstanding or 0 for d in debt_instruments),
            entity_count=len(entities),
            sector=company.sector,
        )
        db.add(cache)

    # ==========================================================================
    # Save to company_metrics
    # ==========================================================================
    total_debt = sum(d.outstanding or 0 for d in debt_instruments)
    secured_debt = sum(d.outstanding or 0 for d in debt_instruments if d.seniority == "senior_secured")
    unsecured_debt = total_debt - secured_debt

    nearest_maturity = min(
        (d.maturity_date for d in debt_instruments if d.maturity_date),
        default=None,
    )

    # Compute flags
    has_holdco_debt = any(
        entity_by_id.get(d.issuer_id) and entity_by_id[d.issuer_id].structure_tier == 1
        for d in debt_instruments
    )
    has_opco_debt = any(
        entity_by_id.get(d.issuer_id) and entity_by_id[d.issuer_id].structure_tier >= 3
        for d in debt_instruments
    )
    has_structural_sub = has_holdco_debt and has_opco_debt
    has_unrestricted_subs = any(e.is_unrestricted for e in entities)
    has_floating_rate = any(d.rate_type == "floating" for d in debt_instruments)

    # Maturity profile calculations
    from datetime import timedelta
    today = date.today()

    # Debt due in each year bucket
    debt_due_1yr = sum(
        d.outstanding or 0 for d in debt_instruments
        if d.maturity_date and d.maturity_date <= today + timedelta(days=365)
    )
    debt_due_2yr = sum(
        d.outstanding or 0 for d in debt_instruments
        if d.maturity_date and today + timedelta(days=365) < d.maturity_date <= today + timedelta(days=730)
    )
    debt_due_3yr = sum(
        d.outstanding or 0 for d in debt_instruments
        if d.maturity_date and today + timedelta(days=730) < d.maturity_date <= today + timedelta(days=1095)
    )

    # Near-term maturity flag (debt due in next 24 months)
    has_near_term_maturity = (debt_due_1yr > 0) or (debt_due_2yr > 0)

    # Weighted average maturity (in years)
    if total_debt > 0:
        weighted_avg_maturity = sum(
            (d.outstanding or 0) * max(0, (d.maturity_date - today).days / 365.0)
            for d in debt_instruments
            if d.maturity_date and d.outstanding
        ) / total_debt
    else:
        weighted_avg_maturity = None

    # Simple subordination score
    if has_structural_sub:
        subordination_risk = "moderate"
        subordination_score = 5.0
    elif has_holdco_debt:
        subordination_risk = "low"
        subordination_score = 2.0
    else:
        subordination_risk = "low"
        subordination_score = 1.0

    result = await db.execute(
        select(CompanyMetrics).where(CompanyMetrics.ticker == ticker)
    )
    metrics = result.scalar_one_or_none()

    if metrics:
        metrics.sector = company.sector
        metrics.industry = company.industry
        metrics.total_debt = total_debt
        metrics.secured_debt = secured_debt
        metrics.unsecured_debt = unsecured_debt
        metrics.entity_count = len(entities)
        metrics.guarantor_count = sum(1 for e in entities if e.is_guarantor)
        metrics.nearest_maturity = nearest_maturity
        metrics.debt_due_1yr = debt_due_1yr
        metrics.debt_due_2yr = debt_due_2yr
        metrics.debt_due_3yr = debt_due_3yr
        metrics.weighted_avg_maturity = weighted_avg_maturity
        metrics.has_near_term_maturity = has_near_term_maturity
        metrics.subordination_risk = subordination_risk
        metrics.subordination_score = subordination_score
        metrics.has_holdco_debt = has_holdco_debt
        metrics.has_opco_debt = has_opco_debt
        metrics.has_structural_sub = has_structural_sub
        metrics.has_unrestricted_subs = has_unrestricted_subs
        metrics.has_floating_rate = has_floating_rate
    else:
        metrics = CompanyMetrics(
            ticker=ticker,
            company_id=company_id,
            sector=company.sector,
            industry=company.industry,
            total_debt=total_debt,
            secured_debt=secured_debt,
            unsecured_debt=unsecured_debt,
            entity_count=len(entities),
            guarantor_count=sum(1 for e in entities if e.is_guarantor),
            nearest_maturity=nearest_maturity,
            debt_due_1yr=debt_due_1yr,
            debt_due_2yr=debt_due_2yr,
            debt_due_3yr=debt_due_3yr,
            weighted_avg_maturity=weighted_avg_maturity,
            has_near_term_maturity=has_near_term_maturity,
            subordination_risk=subordination_risk,
            subordination_score=subordination_score,
            has_holdco_debt=has_holdco_debt,
            has_opco_debt=has_opco_debt,
            has_structural_sub=has_structural_sub,
            has_unrestricted_subs=has_unrestricted_subs,
            has_floating_rate=has_floating_rate,
        )
        db.add(metrics)

    await db.flush()
