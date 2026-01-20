"""
Section Extraction Service for Document Search.

Extracts specific sections from SEC filings for full-text search:
- exhibit_21: Subsidiary list from 10-K Exhibit 21
- debt_footnote: Long-term debt details from Notes to Financial Statements
- mda_liquidity: Liquidity and Capital Resources from MD&A
- credit_agreement: Credit facility terms from 8-K Exhibit 10
- indenture: Bond indentures from 8-K Exhibit 4 (terms, covenants, redemption)
- guarantor_list: Guarantor subsidiaries from Notes
- covenants: Financial covenants from Notes/Exhibits
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Company, DocumentSection


# Section type constants
SECTION_TYPES = [
    "exhibit_21",
    "debt_footnote",
    "mda_liquidity",
    "credit_agreement",
    "indenture",
    "guarantor_list",
    "covenants",
]


@dataclass
class ExtractedSection:
    """A section extracted from a filing."""
    section_type: str
    section_title: str
    content: str
    doc_type: str  # '10-K', '10-Q', '8-K'
    filing_date: date
    sec_filing_url: Optional[str] = None


# =============================================================================
# SECTION EXTRACTION PATTERNS
# =============================================================================

# Pattern for Exhibit 21 - Subsidiaries of Registrant
# IMPORTANT: Must require actual subsidiary content (jurisdiction, state, country mentions)
# to avoid matching exhibit index pages that just list "Exhibit 21" as a reference
EXHIBIT_21_PATTERNS = [
    # Match "Subsidiaries of [Company]" followed by actual subsidiary list content
    # Require state/country/jurisdiction indicators to confirm it's actual subsidiary data
    r"(?i)(Subsidiaries\s+of\s+(?:the\s+)?(?:Registrant|[A-Z][A-Za-z\s\.,]+(?:Inc|Corp|LLC|Ltd|Co|Company))\.?)\s*(.{500,}?)(?=Exhibit\s*2[2-9]|Exhibit\s*[3-9]|Signatures|\Z)",
    # Match "List of Subsidiaries" with actual content
    r"(?i)(List\s+of\s+(?:Significant\s+)?Subsidiaries)\s*(.{500,}?)(?=Exhibit\s*2[2-9]|Exhibit\s*[3-9]|Signatures|\Z)",
    # Match exhibit header with subsidiary content - require jurisdiction words
    r"(?i)(Exhibit\s*21[.\s\-:]*(?:List\s+of\s+)?Subsidiaries[^\n]*)\s*(.{500,}?)(?=Exhibit\s*2[2-9]|Exhibit\s*[3-9]|Signatures|\Z)",
]

# Pattern for debt footnotes in Notes to Financial Statements
# Note: [\.\-\u2014\u2013] matches period, hyphen, em-dash, en-dash
# SEC API renders content without newlines, so we use lookahead to next Note
# Some companies use "Note X" prefix, others just use "X." (number with period)
DEBT_FOOTNOTE_PATTERNS = [
    # "Note X - Debt" or "Note X—Debt" (em-dash) - capture until next Note
    r"(?i)(Note\s*\d+[\.\-\u2014\u2013\s]+(?:Long[\-\u2014\u2013\s]*Term\s+)?Debt)\s*[.\s](.{1000,}?)(?=Note\s*\d+[\.\-\u2014\u2013]|\Z)",
    r"(?i)(Note\s*\d+[\.\-\u2014\u2013\s]+Debt\s+and\s+(?:Credit\s+)?Facilities)\s*(.{1000,}?)(?=Note\s*\d+[\.\-\u2014\u2013]|\Z)",
    r"(?i)(Note\s*\d+[\.\-\u2014\u2013\s]+Borrowings)\s*(.{1000,}?)(?=Note\s*\d+[\.\-\u2014\u2013]|\Z)",
    r"(?i)(Note\s*\d+[\.\-\u2014\u2013\s]+Notes\s+Payable)\s*(.{1000,}?)(?=Note\s*\d+[\.\-\u2014\u2013]|\Z)",
    # Additional patterns for variations
    r"(?i)(Note\s*\d+[\.\-\u2014\u2013\s]+Financing\s+Arrangements?)\s*(.{1000,}?)(?=Note\s*\d+[\.\-\u2014\u2013]|\Z)",
    r"(?i)(Note\s*\d+[\.\-\u2014\u2013\s]+(?:Short[\-\s]*Term\s+and\s+)?Long[\-\s]*Term\s+Debt)\s*(.{1000,}?)(?=Note\s*\d+[\.\-\u2014\u2013]|\Z)",
    r"(?i)(Note\s*\d+[\.\-\u2014\u2013\s]+Credit\s+Facilities?\s+and\s+Debt)\s*(.{1000,}?)(?=Note\s*\d+[\.\-\u2014\u2013]|\Z)",
    r"(?i)(Note\s*\d+[\.\-\u2014\u2013\s]+Indebtedness)\s*(.{1000,}?)(?=Note\s*\d+[\.\-\u2014\u2013]|\Z)",
    # "Long-Term Obligations" (used by KDP, others)
    r"(?i)(Note\s*\d+[\.\-\u2014\u2013\s]+Long[\-\s]*Term\s+Obligations?)\s*(.{1000,}?)(?=Note\s*\d+[\.\-\u2014\u2013]|\Z)",
    # "Senior Notes" or "Debt Securities"
    r"(?i)(Note\s*\d+[\.\-\u2014\u2013\s]+Senior\s+Notes)\s*(.{1000,}?)(?=Note\s*\d+[\.\-\u2014\u2013]|\Z)",
    r"(?i)(Note\s*\d+[\.\-\u2014\u2013\s]+Debt\s+Securities)\s*(.{1000,}?)(?=Note\s*\d+[\.\-\u2014\u2013]|\Z)",
    # Numbered sections without "Note" prefix: "3. Long-Term Obligations"
    # Used by KDP and others - "X. Long-Term Obligations and Borrowing Arrangements"
    r"(?i)(\d+\.\s*Long[\-\s]*Term\s+Obligations?\s*(?:and\s+Borrowing\s+Arrangements?)?)(.{1000,}?)(?=\d+\.\s*[A-Z]|\Z)",
    r"(?i)(\d+\.\s*(?:Long[\-\s]*Term\s+)?Debt(?:\s+and\s+(?:Credit\s+)?Facilities)?)(.{1000,}?)(?=\d+\.\s*[A-Z]|\Z)",
    r"(?i)(\d+\.\s*Borrowings?\s*(?:and\s+(?:Credit\s+)?(?:Facilities|Arrangements))?)(.{1000,}?)(?=\d+\.\s*[A-Z]|\Z)",
    # Pattern without "Note X" prefix - just section headers
    r"(?i)(Long[\-\s]*Term\s+Debt\s+and\s+(?:Credit\s+)?Facilities)\s*(.{1000,}?)(?=Note\s*\d+[\.\-\u2014\u2013]|Item\s+\d|\Z)",
    # Look for debt schedule tables (common format)
    r"(?i)(The\s+components\s+of\s+(?:long[\-\s]*term\s+)?debt)\s*(.{1000,}?)(?=Note\s*\d+[\.\-\u2014\u2013]|\Z)",
]

# Pattern for MD&A Liquidity section
# Works with both newline-separated and continuous text
MDA_LIQUIDITY_PATTERNS = [
    r"(?i)(Liquidity\s+and\s+Capital\s+Resources)\s*(.{1000,}?)(?=Critical\s+Accounting|Results\s+of\s+Operations|Item\s+\d|\Z)",
    r"(?i)(Liquidity,?\s+Capital\s+Resources)\s*(.{1000,}?)(?=Critical\s+Accounting|Results\s+of\s+Operations|Item\s+\d|\Z)",
    r"(?i)(Capital\s+Resources\s+and\s+Liquidity)\s*(.{1000,}?)(?=Critical\s+Accounting|Results\s+of\s+Operations|Item\s+\d|\Z)",
]

# Pattern for credit agreements (typically in 8-K Exhibit 10)
CREDIT_AGREEMENT_PATTERNS = [
    r"(?i)(Credit\s+Agreement)\s*(.{2000,}?)(?=Exhibit\s*\d|\Z)",
    r"(?i)(Amended\s+and\s+Restated\s+Credit)\s*(.{2000,}?)(?=Exhibit\s*\d|\Z)",
    r"(?i)(Senior\s+(?:Secured\s+)?Credit\s+Facility)\s*(.{2000,}?)(?=Exhibit\s*\d|\Z)",
    r"(?i)(Term\s+Loan\s+(?:Credit\s+)?Agreement)\s*(.{2000,}?)(?=Exhibit\s*\d|\Z)",
]

# Pattern for indentures (typically in 8-K Exhibit 4)
# Indentures contain bond terms, covenants, events of default, redemption provisions
INDENTURE_PATTERNS = [
    # Main indenture patterns
    r"(?i)(Indenture)\s+(?:dated|by\s+and\s+(?:between|among))(.{5000,}?)(?=EXHIBIT\s*[A-Z]|\Z)",
    r"(?i)(Supplemental\s+Indenture)\s*(.{5000,}?)(?=EXHIBIT\s*[A-Z]|\Z)",
    r"(?i)((?:First|Second|Third|Fourth|Fifth)\s+Supplemental\s+Indenture)\s*(.{5000,}?)(?=EXHIBIT\s*[A-Z]|\Z)",
    # Indenture with trustee naming
    r"(?i)(Indenture\s+among\s+[^,]+,\s+as\s+Issuer)\s*(.{5000,}?)(?=EXHIBIT\s*[A-Z]|\Z)",
    # Notes indenture
    r"(?i)((?:Senior\s+)?Notes\s+Indenture)\s*(.{5000,}?)(?=EXHIBIT\s*[A-Z]|\Z)",
]

# Pattern for guarantor information
GUARANTOR_PATTERNS = [
    r"(?i)(Guarantor\s+Subsidiaries)\s*(.{500,}?)(?=Note\s*\d+[\.\-\u2014\u2013]|Non[\-\u2014\u2013\s]*Guarantor|\Z)",
    r"(?i)(Subsidiary\s+Guarantors?)\s*(.{500,}?)(?=Note\s*\d+[\.\-\u2014\u2013]|Non[\-\u2014\u2013\s]*Guarantor|\Z)",
    r"(?i)(Guarantees?\s+of\s+(?:Senior\s+)?Notes)\s*(.{500,}?)(?=Note\s*\d+[\.\-\u2014\u2013]|\Z)",
    r"(?i)(Condensed\s+Consolidating\s+Financial)\s*(.{1000,}?)(?=Note\s*\d+[\.\-\u2014\u2013]|\Z)",
]

# Pattern for covenants
COVENANT_PATTERNS = [
    r"(?i)(Financial\s+Covenants?)\s*(.{500,}?)(?=Events?\s+of\s+Default|Note\s*\d+[\.\-\u2014\u2013]|\Z)",
    r"(?i)(Debt\s+Covenants?)\s*(.{500,}?)(?=Events?\s+of\s+Default|Note\s*\d+[\.\-\u2014\u2013]|\Z)",
    r"(?i)(Covenant\s+Compliance)\s*(.{500,}?)(?=Note\s*\d+[\.\-\u2014\u2013]|\Z)",
    r"(?i)(Restrictive\s+Covenants?)\s*(.{500,}?)(?=Note\s*\d+[\.\-\u2014\u2013]|\Z)",
]


def extract_section(content: str, patterns: list[str], max_length: int = 100000) -> tuple[Optional[str], Optional[str]]:
    """
    Extract a section from filing content using regex patterns.

    Returns (title, content) tuple or (None, None) if not found.
    """
    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            title = match.group(1).strip()
            section_content = match.group(2).strip()

            # Truncate title if too long (VARCHAR(255) limit)
            if len(title) > 250:
                title = title[:250] + "..."

            # Truncate content if too long
            if len(section_content) > max_length:
                section_content = section_content[:max_length] + "\n\n[TRUNCATED]"

            # Clean up whitespace
            section_content = re.sub(r'\n{3,}', '\n\n', section_content)
            section_content = re.sub(r' {3,}', '  ', section_content)

            return title, section_content

    return None, None


def is_valid_exhibit_21(content: str) -> bool:
    """
    Validate that exhibit_21 content is actually a subsidiary list,
    not an exhibit index or consent page.

    Returns True if content appears to be a real subsidiary list.
    """
    if not content or len(content) < 200:
        return False

    content_lower = content.lower()

    # Red flags - these indicate it's NOT a subsidiary list
    red_flags = [
        "consent of",  # Auditor consent (Exhibit 23)
        "power of attorney",  # Exhibit 24
        "certification of",  # Exhibit 31/32
        "hereby consent",
        "independent registered public accounting firm",
    ]

    for flag in red_flags:
        if flag in content_lower[:500]:  # Check first 500 chars
            return False

    # Green flags - these indicate it IS a subsidiary list
    # Real Exhibit 21s contain jurisdiction/state information
    green_flags = [
        "delaware",
        "nevada",
        "california",
        "new york",
        "texas",
        "florida",
        "ireland",
        "netherlands",
        "luxembourg",
        "cayman islands",
        "united kingdom",
        "jurisdiction",
        "state of incorporation",
        "place of incorporation",
        "country of organization",
    ]

    green_count = sum(1 for flag in green_flags if flag in content_lower)

    # Require at least 2 jurisdiction indicators
    return green_count >= 2


def extract_sections_from_filing(
    content: str,
    doc_type: str,
    filing_date: date,
    sec_filing_url: Optional[str] = None,
) -> list[ExtractedSection]:
    """
    Extract all relevant sections from a single filing.

    Args:
        content: Filing text content (cleaned HTML)
        doc_type: Filing type ('10-K', '10-Q', '8-K')
        filing_date: Date the filing was made
        sec_filing_url: URL to the SEC filing

    Returns:
        List of ExtractedSection objects
    """
    sections = []

    # Exhibit 21 - typically only in 10-K
    if doc_type == "10-K":
        title, section_content = extract_section(content, EXHIBIT_21_PATTERNS)
        # Validate it's actually a subsidiary list, not an exhibit index or consent
        if section_content and is_valid_exhibit_21(section_content):
            sections.append(ExtractedSection(
                section_type="exhibit_21",
                section_title=title or "Exhibit 21 - Subsidiaries",
                content=section_content,
                doc_type=doc_type,
                filing_date=filing_date,
                sec_filing_url=sec_filing_url,
            ))

    # Debt footnotes - in 10-K and 10-Q
    if doc_type in ("10-K", "10-Q"):
        title, section_content = extract_section(content, DEBT_FOOTNOTE_PATTERNS)
        if section_content:
            sections.append(ExtractedSection(
                section_type="debt_footnote",
                section_title=title or "Long-Term Debt",
                content=section_content,
                doc_type=doc_type,
                filing_date=filing_date,
                sec_filing_url=sec_filing_url,
            ))

    # MD&A Liquidity - in 10-K and 10-Q
    if doc_type in ("10-K", "10-Q"):
        title, section_content = extract_section(content, MDA_LIQUIDITY_PATTERNS)
        if section_content:
            sections.append(ExtractedSection(
                section_type="mda_liquidity",
                section_title=title or "Liquidity and Capital Resources",
                content=section_content,
                doc_type=doc_type,
                filing_date=filing_date,
                sec_filing_url=sec_filing_url,
            ))

    # Credit agreements - typically in 8-K
    if doc_type == "8-K":
        title, section_content = extract_section(content, CREDIT_AGREEMENT_PATTERNS)
        if section_content:
            sections.append(ExtractedSection(
                section_type="credit_agreement",
                section_title=title or "Credit Agreement",
                content=section_content,
                doc_type=doc_type,
                filing_date=filing_date,
                sec_filing_url=sec_filing_url,
            ))

    # Indentures - typically in 8-K (bond issuances)
    # Use larger max_length since indentures are full legal documents
    if doc_type == "8-K":
        title, section_content = extract_section(content, INDENTURE_PATTERNS, max_length=500000)
        if section_content:
            sections.append(ExtractedSection(
                section_type="indenture",
                section_title=title or "Indenture",
                content=section_content,
                doc_type=doc_type,
                filing_date=filing_date,
                sec_filing_url=sec_filing_url,
            ))

    # Guarantor information - in 10-K and 10-Q
    if doc_type in ("10-K", "10-Q"):
        title, section_content = extract_section(content, GUARANTOR_PATTERNS)
        if section_content:
            sections.append(ExtractedSection(
                section_type="guarantor_list",
                section_title=title or "Guarantor Subsidiaries",
                content=section_content,
                doc_type=doc_type,
                filing_date=filing_date,
                sec_filing_url=sec_filing_url,
            ))

    # Covenants - in 10-K, 10-Q, and 8-K
    title, section_content = extract_section(content, COVENANT_PATTERNS)
    if section_content:
        sections.append(ExtractedSection(
            section_type="covenants",
            section_title=title or "Financial Covenants",
            content=section_content,
            doc_type=doc_type,
            filing_date=filing_date,
            sec_filing_url=sec_filing_url,
        ))

    return sections


async def store_sections(
    db: AsyncSession,
    company_id: UUID,
    sections: list[ExtractedSection],
    replace_existing: bool = True,
) -> int:
    """
    Store extracted sections in the database.

    Args:
        db: Database session
        company_id: Company UUID
        sections: List of ExtractedSection objects
        replace_existing: If True, delete existing sections for this company/doc_type/filing_date

    Returns:
        Number of sections stored
    """
    if not sections:
        return 0

    stored_count = 0

    for section in sections:
        if replace_existing:
            # Delete existing section of same type/doc_type/filing_date
            await db.execute(
                delete(DocumentSection).where(
                    DocumentSection.company_id == company_id,
                    DocumentSection.doc_type == section.doc_type,
                    DocumentSection.filing_date == section.filing_date,
                    DocumentSection.section_type == section.section_type,
                )
            )

        # Create new section
        doc_section = DocumentSection(
            id=uuid4(),
            company_id=company_id,
            doc_type=section.doc_type,
            filing_date=section.filing_date,
            section_type=section.section_type,
            section_title=section.section_title,
            content=section.content,
            content_length=len(section.content),
            sec_filing_url=section.sec_filing_url,
        )
        db.add(doc_section)
        stored_count += 1

    await db.commit()
    return stored_count


async def extract_and_store_sections(
    db: AsyncSession,
    company_id: UUID,
    filings_content: dict[str, str],
    filing_urls: Optional[dict[str, str]] = None,
) -> int:
    """
    Extract sections from multiple filings and store them.

    This is the main entry point for section extraction, called after
    successful extraction in iterative_extraction.py.

    Args:
        db: Database session
        company_id: Company UUID
        filings_content: Dict of filing content keyed by "doc_type_date" (e.g., "10-K_2024-02-15")
        filing_urls: Optional dict of URLs keyed by same keys

    Returns:
        Total number of sections stored
    """
    total_stored = 0

    for key, content in filings_content.items():
        if not content or len(content) < 1000:
            continue

        # Parse key formats:
        # - Simple: "10-K_2024-02-15", "10-Q_2024-05-10"
        # - Exhibit 21: "exhibit_21_2024-02-15"
        # - Credit agreement: "credit_agreement_2024-02-15_EX-10_1"
        # - Indenture: "indenture_2024-02-15_EX-4_1"

        # Try to extract date from the key (date is always in YYYY-MM-DD format)
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', key)
        if not date_match:
            continue

        date_str = date_match.group(1)

        # Determine doc_type based on key prefix
        if key.startswith("exhibit_21"):
            # exhibit_21_2024-02-15 -> doc_type is 10-K (exhibits come from 10-K)
            doc_type = "10-K"
        elif key.startswith("credit_agreement"):
            # credit_agreement_2024-02-15_EX-10_1 -> doc_type is 8-K
            doc_type = "8-K"
        elif key.startswith("indenture"):
            # indenture_2024-02-15_EX-4_1 -> doc_type is 8-K
            doc_type = "8-K"
        elif key.startswith("10-K"):
            doc_type = "10-K"
        elif key.startswith("10-Q"):
            doc_type = "10-Q"
        elif key.startswith("8-K"):
            doc_type = "8-K"
        else:
            # Unknown key format, skip
            continue

        # Parse date
        try:
            filing_date = date.fromisoformat(date_str)
        except ValueError:
            continue

        # Get URL if available
        sec_url = filing_urls.get(key) if filing_urls else None

        # Extract sections
        sections = extract_sections_from_filing(
            content=content,
            doc_type=doc_type,
            filing_date=filing_date,
            sec_filing_url=sec_url,
        )

        # Store sections
        if sections:
            count = await store_sections(db, company_id, sections)
            total_stored += count

    return total_stored


async def get_company_sections(
    db: AsyncSession,
    company_id: UUID,
    section_types: Optional[list[str]] = None,
    doc_types: Optional[list[str]] = None,
) -> list[DocumentSection]:
    """
    Get all document sections for a company.

    Args:
        db: Database session
        company_id: Company UUID
        section_types: Filter by section types (e.g., ["debt_footnote", "exhibit_21"])
        doc_types: Filter by document types (e.g., ["10-K", "10-Q"])

    Returns:
        List of DocumentSection objects
    """
    query = select(DocumentSection).where(DocumentSection.company_id == company_id)

    if section_types:
        query = query.where(DocumentSection.section_type.in_(section_types))

    if doc_types:
        query = query.where(DocumentSection.doc_type.in_(doc_types))

    query = query.order_by(DocumentSection.filing_date.desc())

    result = await db.execute(query)
    return list(result.scalars().all())


async def delete_company_sections(
    db: AsyncSession,
    company_id: UUID,
) -> int:
    """
    Delete all document sections for a company.

    Returns:
        Number of sections deleted
    """
    result = await db.execute(
        delete(DocumentSection).where(DocumentSection.company_id == company_id)
    )
    await db.commit()
    return result.rowcount
