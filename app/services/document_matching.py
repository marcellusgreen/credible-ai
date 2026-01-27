"""
Document Matching Service

Links debt instruments (bonds, loans) to their governing legal documents
(indentures, credit agreements) stored in document_sections.

Matching strategies:
1. CUSIP/ISIN matching: Direct identifier match (highest confidence)
2. Bonds -> Indentures: Match by coupon rate, maturity year, seniority terms
3. Loans -> Credit Agreements: Match by facility type, commitment amount

Confidence scoring:
- >= 0.70: Auto-link with is_verified=False
- 0.50-0.69: Link but flag for review
- < 0.50: Don't link, add to unmatched report
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Company, DebtInstrument, DocumentSection, DebtInstrumentDocument, Entity


# Instrument type classification
BOND_TYPES = {
    "notes", "bonds", "senior_notes", "senior_secured_notes",
    "senior_unsecured_notes", "subordinated_notes", "convertible_notes",
    "debentures",
}

LOAN_TYPES = {
    "revolving_credit_facility", "term_loan", "term_loan_a", "term_loan_b",
    "term_loan_c", "revolver", "abl_facility", "credit_facility",
    "delayed_draw_term_loan",
}


def classify_document_relationship(title: str, content_preview: str = "") -> str:
    """
    Classify a document's relationship type based on its title and content.

    Returns one of:
    - 'governs': Base indenture or original credit agreement
    - 'supplements': Supplemental indenture
    - 'amends': Amendment to existing document
    - 'related': Other related document (e.g., officer's certificate)
    """
    title_lower = (title or "").lower()
    content_lower = (content_preview or "")[:2000].lower()

    # Check for supplemental indenture patterns
    supplemental_patterns = [
        r'supplemental\s+indenture',
        r'first\s+supplemental',
        r'second\s+supplemental',
        r'third\s+supplemental',
        r'\d+(?:st|nd|rd|th)\s+supplemental',
        r'supplemental\s+no\.',
    ]
    for pattern in supplemental_patterns:
        if re.search(pattern, title_lower) or re.search(pattern, content_lower[:500]):
            return "supplements"

    # Check for amendment patterns
    amendment_patterns = [
        r'amendment\s+no\.',
        r'amendment\s+\d+',
        r'first\s+amendment',
        r'second\s+amendment',
        r'third\s+amendment',
        r'\d+(?:st|nd|rd|th)\s+amendment',
        r'amended\s+and\s+restated',
        r'restatement',
    ]
    for pattern in amendment_patterns:
        if re.search(pattern, title_lower) or re.search(pattern, content_lower[:500]):
            return "amends"

    # Check for related documents (not primary governing docs)
    related_patterns = [
        r"officer'?s?\s+certificate",
        r'form\s+of\s+note',
        r'form\s+of\s+bond',
        r'notation\s+of\s+guarantee',
        r'guarantee\s+agreement',
    ]
    for pattern in related_patterns:
        if re.search(pattern, title_lower):
            return "related"

    # Default to 'governs' for base indentures and original credit agreements
    return "governs"


@dataclass
class MatchSignal:
    """Individual signal contributing to a match score."""
    signal_type: str  # coupon_title, coupon_content, maturity_title, etc.
    value_found: str
    value_expected: str
    confidence_boost: float
    location: str  # title, content


@dataclass
class MatchResult:
    """Result of matching a debt instrument to a document."""
    debt_instrument_id: UUID
    document_section_id: UUID
    match_confidence: float
    match_method: str  # coupon_maturity, facility_type, full_text, manual
    match_evidence: dict
    signals: list[MatchSignal] = field(default_factory=list)
    relationship_type: str = "governs"


@dataclass
class CompanyMatchReport:
    """Summary of matching results for a company."""
    company_id: UUID
    ticker: str
    total_instruments: int
    matched_high_confidence: int  # >= 0.70
    matched_low_confidence: int   # 0.50 - 0.69
    unmatched: int                # < 0.50
    matches: list[MatchResult]
    unmatched_instruments: list[UUID]


def extract_cusips_from_text(text: str) -> set[str]:
    """
    Extract CUSIP identifiers from text.

    CUSIP format: 9 alphanumeric characters (e.g., 037833EQ9)
    Common patterns in documents:
    - "CUSIP NO: 02557T AE9" (with space)
    - "CUSIP No. 037833EQ9"
    - "CUSIP: 037833EQ9"

    Returns set of normalized CUSIPs (uppercase, no spaces).
    """
    cusips = set()

    # Pattern 1: CUSIP followed by 9-char code (may have space in middle)
    # Matches: "CUSIP NO: 02557T AE9", "CUSIP No. 037833EQ9"
    pattern1 = r'CUSIP[:\s.No]*\s*([0-9A-Z]{5,6})\s*([0-9A-Z]{2,4})'
    for match in re.finditer(pattern1, text, re.IGNORECASE):
        cusip = (match.group(1) + match.group(2)).upper()
        if len(cusip) == 9:
            cusips.add(cusip)

    # Pattern 2: CUSIP followed by solid 9-char code
    pattern2 = r'CUSIP[:\s.No]*\s*([0-9A-Z]{9})\b'
    for match in re.finditer(pattern2, text, re.IGNORECASE):
        cusips.add(match.group(1).upper())

    return cusips


def extract_isins_from_text(text: str) -> set[str]:
    """
    Extract ISIN identifiers from text.

    ISIN format: 2-letter country code + 9-char identifier + 1 check digit
    US ISINs: US + 9-char CUSIP + check digit (e.g., US037833EQ92)

    Returns set of ISINs (uppercase).
    """
    isins = set()

    # Pattern: 2 letters + 10 alphanumeric (common: US + 10 chars)
    pattern = r'\b([A-Z]{2}[0-9A-Z]{10})\b'
    for match in re.finditer(pattern, text):
        isin = match.group(1).upper()
        # Validate it looks like an ISIN (starts with country code)
        if isin[:2].isalpha():
            isins.add(isin)

    return isins


def normalize_cusip(cusip: str) -> str:
    """Normalize CUSIP by removing spaces and converting to uppercase."""
    if not cusip:
        return ""
    return cusip.replace(" ", "").upper()


def cusip_from_isin(isin: str) -> str:
    """Extract CUSIP from US ISIN (characters 2-11)."""
    if not isin or len(isin) != 12:
        return ""
    if isin[:2].upper() == "US":
        return isin[2:11].upper()
    return ""


def extract_coupon_from_text(text: str) -> list[float]:
    """
    Extract coupon rates from text.

    Patterns matched:
    - "5.750% Senior Notes"
    - "5 3/4% Senior Notes"
    - "5.75 percent"
    - "coupon of 5.75%"

    Returns list of coupon rates as decimals (e.g., 5.75).
    """
    coupons = []

    # Pattern 1: Decimal percent (5.750%, 5.75%)
    decimal_pattern = r'(\d+\.?\d*)\s*%'
    for match in re.finditer(decimal_pattern, text):
        try:
            rate = float(match.group(1))
            if 0 < rate < 25:  # Reasonable coupon range
                coupons.append(rate)
        except ValueError:
            pass

    # Pattern 2: Fraction (5 3/4%)
    fraction_pattern = r'(\d+)\s+(\d)/(\d)\s*%'
    for match in re.finditer(fraction_pattern, text):
        try:
            whole = int(match.group(1))
            num = int(match.group(2))
            denom = int(match.group(3))
            rate = whole + (num / denom)
            if 0 < rate < 25:
                coupons.append(rate)
        except (ValueError, ZeroDivisionError):
            pass

    return list(set(coupons))


def extract_maturity_years_from_text(text: str) -> list[int]:
    """
    Extract maturity years from text.

    Patterns matched:
    - "due 2029"
    - "due February 2029"
    - "maturing 2029"
    - "maturity date of February 15, 2029"
    - "2029 Notes"

    Returns list of years (4-digit integers).
    """
    years = []

    # Pattern 1: "due YYYY" or "due Month YYYY"
    due_pattern = r'due\s+(?:\w+\s+)?(\d{4})'
    for match in re.finditer(due_pattern, text, re.IGNORECASE):
        year = int(match.group(1))
        if 2000 < year < 2100:
            years.append(year)

    # Pattern 2: "maturing/maturity YYYY"
    maturity_pattern = r'matur(?:ing|ity)[^0-9]*(\d{4})'
    for match in re.finditer(maturity_pattern, text, re.IGNORECASE):
        year = int(match.group(1))
        if 2000 < year < 2100:
            years.append(year)

    # Pattern 3: "YYYY Notes" or "Notes due YYYY"
    notes_pattern = r'(\d{4})\s+(?:senior\s+)?notes'
    for match in re.finditer(notes_pattern, text, re.IGNORECASE):
        year = int(match.group(1))
        if 2000 < year < 2100:
            years.append(year)

    return list(set(years))


def extract_seniority_terms(text: str) -> list[str]:
    """
    Extract seniority-related terms from text.

    Returns list of normalized seniority terms found.
    """
    terms = []

    patterns = {
        "senior_secured": r'\bsenior\s+secured\b',
        "senior_unsecured": r'\bsenior\s+unsecured\b',
        "first_lien": r'\bfirst[- ]lien\b',
        "second_lien": r'\bsecond[- ]lien\b',
        "subordinated": r'\bsubordinated\b',
        "senior": r'\bsenior\b',
    }

    text_lower = text.lower()
    for term, pattern in patterns.items():
        if re.search(pattern, text_lower):
            terms.append(term)

    return terms


def extract_note_descriptions(text: str) -> list[str]:
    """
    Extract full note descriptions from text.

    Patterns matched:
    - "5.25% Senior Notes due 2030"
    - "5.250% Notes due 2030"
    - "5 3/4% Senior Secured Notes due February 2030"
    - "1.750% SENIOR NOTE DUE APRIL 20, 2032" (with day)
    - "$500,000,000 4.800% Senior Notes due 2029"

    Returns list of normalized descriptions (e.g., "5.25% notes 2030").
    """
    descriptions = []

    # Pattern 1: rate% [qualifier] notes due [month] [day,] year
    # Handles: "5.25% Senior Notes due 2030", "1.750% SENIOR NOTE DUE APRIL 20, 2032"
    pattern = r'(\d+\.?\d*)\s*%\s*(senior\s+secured\s+|senior\s+unsecured\s+|senior\s+|subordinated\s+)?notes?\s+due\s+(?:\w+\s+)?(?:\d{1,2},?\s+)?(\d{4})'

    for match in re.finditer(pattern, text, re.IGNORECASE):
        rate = match.group(1)
        year = match.group(3)
        try:
            rate_float = float(rate)
            normalized = f"{rate_float:.2f}% notes {year}"
            descriptions.append(normalized)
        except ValueError:
            pass

    # Pattern 2: $amount rate% [qualifier] notes due year
    # Handles: "$500,000,000 4.800% Senior Notes due 2029"
    amount_pattern = r'\$[\d,]+\s+(\d+\.?\d*)\s*%\s*(senior\s+|subordinated\s+)?notes?\s+due\s+(?:\w+\s+)?(?:\d{1,2},?\s+)?(\d{4})'
    for match in re.finditer(amount_pattern, text, re.IGNORECASE):
        rate = match.group(1)
        year = match.group(3)
        try:
            rate_float = float(rate)
            normalized = f"{rate_float:.2f}% notes {year}"
            descriptions.append(normalized)
        except ValueError:
            pass

    # Pattern 3: Fraction format: "5 3/4% Notes due 2030"
    fraction_pattern = r'(\d+)\s+(\d)/(\d)\s*%\s*(senior\s+|subordinated\s+)?notes?\s+due\s+(?:\w+\s+)?(?:\d{1,2},?\s+)?(\d{4})'
    for match in re.finditer(fraction_pattern, text, re.IGNORECASE):
        try:
            whole = int(match.group(1))
            num = int(match.group(2))
            denom = int(match.group(3))
            rate_float = whole + (num / denom)
            year = match.group(5)
            normalized = f"{rate_float:.2f}% notes {year}"
            descriptions.append(normalized)
        except (ValueError, ZeroDivisionError):
            pass

    # Pattern 4: Notes due year at rate% (alternative ordering)
    # Handles: "Notes due 2032 at 1.750%"
    alt_pattern = r'notes?\s+due\s+(?:\w+\s+)?(?:\d{1,2},?\s+)?(\d{4})\s+(?:at\s+)?(\d+\.?\d*)\s*%'
    for match in re.finditer(alt_pattern, text, re.IGNORECASE):
        year = match.group(1)
        rate = match.group(2)
        try:
            rate_float = float(rate)
            normalized = f"{rate_float:.2f}% notes {year}"
            descriptions.append(normalized)
        except ValueError:
            pass

    return list(set(descriptions))


def find_note_description_match(
    instrument: DebtInstrument,
    indentures: list[DocumentSection],
) -> Optional[MatchResult]:
    """
    Find an indenture that matches the instrument's full note description.

    This matches the full "X.XX% Notes due YYYY" pattern, which is more precise
    than matching coupon and maturity year separately since it requires both
    to match in the same document context.

    Returns MatchResult with confidence 0.85 if found, None otherwise.
    """
    if not indentures:
        return None

    # Extract note description from instrument name
    inst_descriptions = extract_note_descriptions(instrument.name or "")

    # Also construct description from coupon + maturity if available
    if instrument.interest_rate and instrument.maturity_date:
        coupon = instrument.interest_rate / 100  # Convert from bps to percent
        year = instrument.maturity_date.year
        constructed = f"{coupon:.2f}% notes {year}"
        if constructed not in inst_descriptions:
            inst_descriptions.append(constructed)

    if not inst_descriptions:
        return None

    for indenture in indentures:
        # Check title first (higher confidence)
        title = indenture.section_title or ""
        title_descriptions = extract_note_descriptions(title)

        for inst_desc in inst_descriptions:
            if inst_desc in title_descriptions:
                return MatchResult(
                    debt_instrument_id=instrument.id,
                    document_section_id=indenture.id,
                    match_confidence=0.90,  # High confidence for full description match in title
                    match_method="note_description",
                    match_evidence={
                        "signals": [{
                            "type": "note_description_match",
                            "value_found": inst_desc,
                            "value_expected": inst_desc,
                            "boost": 0.90,
                            "location": "title",
                        }],
                        "document_title": title[:200],
                        "instrument_name": instrument.name,
                    },
                    signals=[MatchSignal(
                        signal_type="note_description_match",
                        value_found=inst_desc,
                        value_expected=inst_desc,
                        confidence_boost=0.90,
                        location="title"
                    )],
                    relationship_type="governs",
                )

        # Check content (first 30K chars)
        content = (indenture.content or "")[:30000]
        content_descriptions = extract_note_descriptions(content)

        for inst_desc in inst_descriptions:
            if inst_desc in content_descriptions:
                return MatchResult(
                    debt_instrument_id=instrument.id,
                    document_section_id=indenture.id,
                    match_confidence=0.75,  # Good confidence for full description match in content
                    match_method="note_description",
                    match_evidence={
                        "signals": [{
                            "type": "note_description_match",
                            "value_found": inst_desc,
                            "value_expected": inst_desc,
                            "boost": 0.75,
                            "location": "content",
                        }],
                        "document_title": title[:200],
                        "instrument_name": instrument.name,
                    },
                    signals=[MatchSignal(
                        signal_type="note_description_match",
                        value_found=inst_desc,
                        value_expected=inst_desc,
                        confidence_boost=0.75,
                        location="content"
                    )],
                    relationship_type="governs",
                )

    return None


def normalize_issuer_name(name: str) -> str:
    """
    Normalize an issuer name for matching.

    Removes common suffixes (Inc., Corp., LLC, etc.) and normalizes whitespace.
    """
    if not name:
        return ""

    # Convert to lowercase
    normalized = name.lower()

    # Remove common corporate suffixes
    suffixes = [
        r',?\s*inc\.?$', r',?\s*corp\.?$', r',?\s*corporation$',
        r',?\s*llc\.?$', r',?\s*l\.?l\.?c\.?$',
        r',?\s*lp\.?$', r',?\s*l\.?p\.?$',
        r',?\s*ltd\.?$', r',?\s*limited$',
        r',?\s*co\.?$', r',?\s*company$',
        r',?\s*plc\.?$', r',?\s*n\.?v\.?$',
        r',?\s*s\.?a\.?$', r',?\s*gmbh$',
    ]

    for suffix in suffixes:
        normalized = re.sub(suffix, '', normalized, flags=re.IGNORECASE)

    # Remove extra whitespace
    normalized = ' '.join(normalized.split())

    return normalized.strip()


def find_issuer_date_match(
    instrument: DebtInstrument,
    indentures: list[DocumentSection],
    issuer_name: str,
) -> Optional[MatchResult]:
    """
    Find an indenture that contains the issuer name AND matches a date.

    This strategy matches when:
    1. The issuer entity name appears in the indenture content
    2. AND either the maturity year or issue date matches

    This is useful when we have the issuer name but lack CUSIP/ISIN.

    Confidence:
    - Issuer + exact filing date match: 0.80
    - Issuer + maturity year in content: 0.70
    - Issuer + filing date within 30 days: 0.65

    Returns MatchResult if found, None otherwise.
    """
    if not indentures or not issuer_name:
        return None

    normalized_issuer = normalize_issuer_name(issuer_name)
    if len(normalized_issuer) < 3:  # Too short to match reliably
        return None

    inst_maturity_year = instrument.maturity_date.year if instrument.maturity_date else None
    inst_issue_date = instrument.issue_date

    best_match = None
    best_confidence = 0.0

    for indenture in indentures:
        content = (indenture.content or "")[:50000].lower()
        title = (indenture.section_title or "").lower()

        # Check if issuer name appears in content or title
        # Use normalized name for matching
        issuer_found = normalized_issuer in content or normalized_issuer in title

        # Also try matching just the key words (e.g., "gilead sciences" for "Gilead Sciences, Inc.")
        if not issuer_found:
            # Try first two words of normalized name
            name_parts = normalized_issuer.split()
            if len(name_parts) >= 2:
                key_name = ' '.join(name_parts[:2])
                if len(key_name) >= 5:
                    issuer_found = key_name in content or key_name in title

        if not issuer_found:
            continue

        # Issuer found - now check for date matching
        signals = []
        confidence = 0.0

        # Check 1: Exact filing date match with issue date (highest confidence)
        if inst_issue_date and indenture.filing_date:
            days_diff = abs((indenture.filing_date - inst_issue_date).days)
            if days_diff == 0:
                confidence = 0.80
                signals.append(MatchSignal(
                    signal_type="issuer_issue_date_match",
                    value_found=f"{issuer_name} + {indenture.filing_date}",
                    value_expected=f"{issuer_name} + {inst_issue_date}",
                    confidence_boost=0.80,
                    location="content+metadata"
                ))
            elif days_diff <= 7:
                confidence = 0.70
                signals.append(MatchSignal(
                    signal_type="issuer_issue_date_near",
                    value_found=f"{issuer_name} + {indenture.filing_date}",
                    value_expected=f"{issuer_name} + {inst_issue_date} ({days_diff} days)",
                    confidence_boost=0.70,
                    location="content+metadata"
                ))
            elif days_diff <= 30:
                confidence = 0.60
                signals.append(MatchSignal(
                    signal_type="issuer_issue_date_near",
                    value_found=f"{issuer_name} + {indenture.filing_date}",
                    value_expected=f"{issuer_name} + {inst_issue_date} ({days_diff} days)",
                    confidence_boost=0.60,
                    location="content+metadata"
                ))

        # Check 2: Maturity year in content
        if not signals and inst_maturity_year:
            content_years = extract_maturity_years_from_text(content[:30000])
            if inst_maturity_year in content_years:
                confidence = 0.65
                signals.append(MatchSignal(
                    signal_type="issuer_maturity_match",
                    value_found=f"{issuer_name} + {inst_maturity_year}",
                    value_expected=f"{issuer_name} + {inst_maturity_year}",
                    confidence_boost=0.65,
                    location="content"
                ))

        if signals and confidence > best_confidence:
            best_confidence = confidence
            best_match = MatchResult(
                debt_instrument_id=instrument.id,
                document_section_id=indenture.id,
                match_confidence=confidence,
                match_method="issuer_date",
                match_evidence={
                    "signals": [{
                        "type": s.signal_type,
                        "value_found": s.value_found,
                        "value_expected": s.value_expected,
                        "boost": s.confidence_boost,
                        "location": s.location,
                    } for s in signals],
                    "document_title": (indenture.section_title or "")[:200],
                    "instrument_name": instrument.name,
                    "issuer_name": issuer_name,
                },
                signals=signals,
                relationship_type="governs",
            )

    return best_match


def extract_facility_types(text: str) -> list[str]:
    """
    Extract facility type keywords from text.

    Returns list of facility types found.
    """
    types = []

    patterns = {
        "revolving": r'\brevolv(?:ing|er)\b',
        "term_loan": r'\bterm\s+loan\b',
        "term_loan_b": r'\bterm\s+loan\s+b\b',
        "term_loan_a": r'\bterm\s+loan\s+a\b',
        "abl": r'\babl\b|\basset[- ]based\b',
        "delayed_draw": r'\bdelayed\s+draw\b',
    }

    text_lower = text.lower()
    for facility_type, pattern in patterns.items():
        if re.search(pattern, text_lower):
            types.append(facility_type)

    return types


def extract_commitment_amounts(text: str) -> list[int]:
    """
    Extract commitment/principal amounts from text.

    Returns list of amounts in cents.
    """
    amounts = []

    # Pattern: $X,XXX million or $X.X billion
    patterns = [
        (r'\$\s*([\d,]+(?:\.\d+)?)\s*billion', 1_000_000_000_00),
        (r'\$\s*([\d,]+(?:\.\d+)?)\s*million', 1_000_000_00),
        (r'\$\s*([\d,]+(?:\.\d+)?)\s*(?:thousand|k)', 1_000_00),
    ]

    for pattern, multiplier in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            try:
                value_str = match.group(1).replace(",", "")
                value = float(value_str)
                amount_cents = int(value * multiplier)
                if amount_cents > 0:
                    amounts.append(amount_cents)
            except ValueError:
                pass

    return amounts


def find_cusip_isin_match(
    instrument: DebtInstrument,
    documents: list[DocumentSection],
) -> Optional[MatchResult]:
    """
    Find a document that contains the instrument's CUSIP or ISIN.

    This is a high-confidence match - if the identifier appears in the document,
    it's almost certainly the governing document.

    Returns MatchResult with confidence 0.95 if found, None otherwise.
    """
    if not documents:
        return None

    # Get instrument identifiers
    inst_cusip = normalize_cusip(instrument.cusip) if instrument.cusip else ""
    inst_isin = instrument.isin.upper() if instrument.isin else ""

    # If we have ISIN but not CUSIP, derive CUSIP from ISIN
    if inst_isin and not inst_cusip:
        inst_cusip = cusip_from_isin(inst_isin)

    if not inst_cusip and not inst_isin:
        return None

    for doc in documents:
        content = doc.content or ""
        signals = []

        # Check for CUSIP match
        if inst_cusip:
            doc_cusips = extract_cusips_from_text(content)
            if inst_cusip in doc_cusips:
                signals.append(MatchSignal(
                    signal_type="cusip_match",
                    value_found=inst_cusip,
                    value_expected=inst_cusip,
                    confidence_boost=0.95,
                    location="content"
                ))

        # Check for ISIN match
        if inst_isin and not signals:
            doc_isins = extract_isins_from_text(content)
            if inst_isin in doc_isins:
                signals.append(MatchSignal(
                    signal_type="isin_match",
                    value_found=inst_isin,
                    value_expected=inst_isin,
                    confidence_boost=0.95,
                    location="content"
                ))

        if signals:
            return MatchResult(
                debt_instrument_id=instrument.id,
                document_section_id=doc.id,
                match_confidence=0.95,
                match_method="identifier",
                match_evidence={
                    "signals": [
                        {
                            "type": s.signal_type,
                            "value_found": s.value_found,
                            "value_expected": s.value_expected,
                            "boost": s.confidence_boost,
                            "location": s.location,
                        }
                        for s in signals
                    ],
                    "document_title": (doc.section_title or "")[:200],
                    "instrument_name": instrument.name,
                },
                signals=signals,
                relationship_type="governs",
            )

    return None


def find_issue_date_match(
    instrument: DebtInstrument,
    documents: list[DocumentSection],
    tolerance_days: int = 0,
) -> Optional[MatchResult]:
    """
    Find a document whose filing date matches the instrument's issue date.

    Indentures are typically filed on or very close to the bond issue date,
    making this a strong linkage signal.

    Args:
        instrument: Debt instrument to match
        documents: List of documents to search
        tolerance_days: How many days difference to allow (0 = exact match)

    Returns:
        MatchResult with confidence based on date proximity:
        - Exact match (0 days): 0.85 confidence
        - Within 3 days: 0.75 confidence
        - Within 7 days: 0.65 confidence
    """
    if not documents or not instrument.issue_date:
        return None

    inst_issue_date = instrument.issue_date

    best_match = None
    best_days_diff = float('inf')

    for doc in documents:
        if not doc.filing_date:
            continue

        days_diff = abs((doc.filing_date - inst_issue_date).days)

        if days_diff <= tolerance_days and days_diff < best_days_diff:
            best_days_diff = days_diff

            # Confidence based on date proximity
            if days_diff == 0:
                confidence = 0.85
            elif days_diff <= 3:
                confidence = 0.75
            elif days_diff <= 7:
                confidence = 0.65
            else:
                confidence = 0.55

            best_match = MatchResult(
                debt_instrument_id=instrument.id,
                document_section_id=doc.id,
                match_confidence=confidence,
                match_method="issue_date",
                match_evidence={
                    "signals": [
                        {
                            "type": "issue_date_match",
                            "value_found": str(doc.filing_date),
                            "value_expected": str(inst_issue_date),
                            "boost": confidence,
                            "location": "metadata",
                            "days_difference": days_diff,
                        }
                    ],
                    "document_title": (doc.section_title or "")[:200],
                    "instrument_name": instrument.name,
                },
                signals=[MatchSignal(
                    signal_type="issue_date_match",
                    value_found=str(doc.filing_date),
                    value_expected=str(inst_issue_date),
                    confidence_boost=confidence,
                    location="metadata"
                )],
                relationship_type="governs",
            )

    return best_match


def find_best_indenture_match(
    instrument: DebtInstrument,
    indentures: list[DocumentSection],
    filing_date_tolerance_days: int = 30,
) -> Optional[MatchResult]:
    """
    Find the best matching indenture for a bond instrument.

    Scoring signals:
    - Coupon rate match (title): +0.35
    - Coupon rate match (content): +0.20
    - Maturity year match (title): +0.35
    - Maturity year match (content): +0.25 (increased - 87% of indentures have this)
    - Seniority term match (title): +0.15
    - Filing date within tolerance: +0.10
    - Instrument name words in title: +0.15
    """
    if not indentures:
        return None

    # Get instrument characteristics
    inst_coupon = instrument.interest_rate / 100 if instrument.interest_rate else None
    inst_maturity_year = instrument.maturity_date.year if instrument.maturity_date else None
    inst_issue_date = instrument.issue_date
    inst_seniority = instrument.seniority or ""
    inst_name_words = set(instrument.name.lower().split())

    best_match: Optional[MatchResult] = None
    best_score = 0.0

    for indenture in indentures:
        score = 0.0
        signals = []

        title = indenture.section_title or ""
        # Search first 50K chars for maturity/coupon info (increased from 10K)
        # Maturity year patterns appear in 87% of indentures when searching deeper
        content_preview = indenture.content[:50000] if indenture.content else ""

        # Extract features from title and content
        title_coupons = extract_coupon_from_text(title)
        content_coupons = extract_coupon_from_text(content_preview)
        title_years = extract_maturity_years_from_text(title)
        content_years = extract_maturity_years_from_text(content_preview)
        title_seniority = extract_seniority_terms(title)

        # Coupon match - title (+0.35)
        if inst_coupon and title_coupons:
            for tc in title_coupons:
                if abs(tc - inst_coupon) < 0.01:  # Exact match
                    score += 0.35
                    signals.append(MatchSignal(
                        signal_type="coupon_match",
                        value_found=str(tc),
                        value_expected=str(inst_coupon),
                        confidence_boost=0.35,
                        location="title"
                    ))
                    break

        # Coupon match - content (+0.20)
        if inst_coupon and content_coupons and not any(s.signal_type == "coupon_match" for s in signals):
            for cc in content_coupons:
                if abs(cc - inst_coupon) < 0.01:
                    score += 0.20
                    signals.append(MatchSignal(
                        signal_type="coupon_match",
                        value_found=str(cc),
                        value_expected=str(inst_coupon),
                        confidence_boost=0.20,
                        location="content"
                    ))
                    break

        # Maturity year match - title (+0.35)
        if inst_maturity_year and title_years:
            if inst_maturity_year in title_years:
                score += 0.35
                signals.append(MatchSignal(
                    signal_type="maturity_match",
                    value_found=str(inst_maturity_year),
                    value_expected=str(inst_maturity_year),
                    confidence_boost=0.35,
                    location="title"
                ))

        # Maturity year match - content (+0.30, increased from 0.20)
        # 87% of indentures have maturity year in content, making this a strong signal
        if inst_maturity_year and content_years and not any(s.signal_type == "maturity_match" for s in signals):
            if inst_maturity_year in content_years:
                score += 0.30
                signals.append(MatchSignal(
                    signal_type="maturity_match",
                    value_found=str(inst_maturity_year),
                    value_expected=str(inst_maturity_year),
                    confidence_boost=0.30,
                    location="content"
                ))

        # Seniority match - title (+0.15)
        if inst_seniority and title_seniority:
            inst_seniority_normalized = inst_seniority.lower().replace("_", " ").replace("-", " ")
            for ts in title_seniority:
                ts_normalized = ts.replace("_", " ")
                if ts_normalized in inst_seniority_normalized or inst_seniority_normalized in ts_normalized:
                    score += 0.15
                    signals.append(MatchSignal(
                        signal_type="seniority_match",
                        value_found=ts,
                        value_expected=inst_seniority,
                        confidence_boost=0.15,
                        location="title"
                    ))
                    break

        # Filing date proximity (+0.10)
        if inst_issue_date and indenture.filing_date:
            days_diff = abs((indenture.filing_date - inst_issue_date).days)
            if days_diff <= filing_date_tolerance_days:
                score += 0.10
                signals.append(MatchSignal(
                    signal_type="filing_date_proximity",
                    value_found=str(indenture.filing_date),
                    value_expected=str(inst_issue_date),
                    confidence_boost=0.10,
                    location="metadata"
                ))

        # Instrument name words in title (+0.15)
        title_words = set(title.lower().split())
        overlap = inst_name_words & title_words
        # Filter out common words
        meaningful_overlap = overlap - {"the", "and", "or", "of", "to", "for", "in", "a", "an"}
        if len(meaningful_overlap) >= 2:
            score += 0.15
            signals.append(MatchSignal(
                signal_type="name_overlap",
                value_found=", ".join(meaningful_overlap),
                value_expected=instrument.name,
                confidence_boost=0.15,
                location="title"
            ))

        if score > best_score:
            best_score = score
            best_match = MatchResult(
                debt_instrument_id=instrument.id,
                document_section_id=indenture.id,
                match_confidence=min(score, 1.0),  # Cap at 1.0
                match_method="coupon_maturity",
                match_evidence={
                    "signals": [
                        {
                            "type": s.signal_type,
                            "value_found": s.value_found,
                            "value_expected": s.value_expected,
                            "boost": s.confidence_boost,
                            "location": s.location,
                        }
                        for s in signals
                    ],
                    "document_title": title[:200],
                    "instrument_name": instrument.name,
                },
                signals=signals,
                relationship_type="governs",
            )

    return best_match


def find_best_credit_agreement_match(
    instrument: DebtInstrument,
    credit_agreements: list[DocumentSection],
    filing_date_tolerance_days: int = 60,
) -> Optional[MatchResult]:
    """
    Find the best matching credit agreement for a loan instrument.

    Scoring signals:
    - Facility type keyword (title): +0.30
    - Facility type keyword (content): +0.15
    - Commitment amount match (content): +0.25
    - Filing date proximity: +0.15
    - "Amended and Restated" (title): +0.10
    """
    if not credit_agreements:
        return None

    # Get instrument characteristics
    inst_commitment = instrument.commitment
    inst_issue_date = instrument.issue_date
    inst_type = instrument.instrument_type or ""
    inst_name = instrument.name or ""

    # Determine expected facility type keywords
    expected_keywords = []
    if "revolv" in inst_type.lower() or "revolver" in inst_type.lower():
        expected_keywords.extend(["revolving", "revolver"])
    if "term_loan" in inst_type.lower():
        expected_keywords.append("term_loan")
        if "_b" in inst_type.lower():
            expected_keywords.append("term_loan_b")
        elif "_a" in inst_type.lower():
            expected_keywords.append("term_loan_a")
    if "abl" in inst_type.lower():
        expected_keywords.append("abl")

    # Also check instrument name
    if "revolv" in inst_name.lower():
        expected_keywords.extend(["revolving", "revolver"])
    if "term loan" in inst_name.lower():
        expected_keywords.append("term_loan")

    best_match: Optional[MatchResult] = None
    best_score = 0.0

    for agreement in credit_agreements:
        score = 0.0
        signals = []

        title = agreement.section_title or ""
        content_preview = agreement.content[:15000] if agreement.content else ""

        # Extract features
        title_facilities = extract_facility_types(title)
        content_facilities = extract_facility_types(content_preview)
        content_amounts = extract_commitment_amounts(content_preview)

        # Facility type match - title (+0.30)
        if expected_keywords and title_facilities:
            for keyword in expected_keywords:
                if keyword in title_facilities:
                    score += 0.30
                    signals.append(MatchSignal(
                        signal_type="facility_type_match",
                        value_found=keyword,
                        value_expected=inst_type,
                        confidence_boost=0.30,
                        location="title"
                    ))
                    break

        # Facility type match - content (+0.15)
        if expected_keywords and content_facilities and not any(s.signal_type == "facility_type_match" for s in signals):
            for keyword in expected_keywords:
                if keyword in content_facilities:
                    score += 0.15
                    signals.append(MatchSignal(
                        signal_type="facility_type_match",
                        value_found=keyword,
                        value_expected=inst_type,
                        confidence_boost=0.15,
                        location="content"
                    ))
                    break

        # Commitment amount match (+0.25)
        if inst_commitment and content_amounts:
            for amount in content_amounts:
                # Allow 10% tolerance on amount matching
                if abs(amount - inst_commitment) / max(inst_commitment, 1) < 0.10:
                    score += 0.25
                    signals.append(MatchSignal(
                        signal_type="commitment_match",
                        value_found=str(amount),
                        value_expected=str(inst_commitment),
                        confidence_boost=0.25,
                        location="content"
                    ))
                    break

        # Filing date proximity (+0.15)
        if inst_issue_date and agreement.filing_date:
            days_diff = abs((agreement.filing_date - inst_issue_date).days)
            if days_diff <= filing_date_tolerance_days:
                score += 0.15
                signals.append(MatchSignal(
                    signal_type="filing_date_proximity",
                    value_found=str(agreement.filing_date),
                    value_expected=str(inst_issue_date),
                    confidence_boost=0.15,
                    location="metadata"
                ))

        # "Amended and Restated" in title (+0.10)
        if "amended" in title.lower() and "restated" in title.lower():
            score += 0.10
            signals.append(MatchSignal(
                signal_type="amended_restated",
                value_found="Yes",
                value_expected="N/A",
                confidence_boost=0.10,
                location="title"
            ))

        # If no specific signals, give a base score for having a credit agreement
        # for the same company (implicit match via company_id)
        if not signals:
            score += 0.20
            signals.append(MatchSignal(
                signal_type="company_match",
                value_found="same_company",
                value_expected="same_company",
                confidence_boost=0.20,
                location="metadata"
            ))

        if score > best_score:
            best_score = score
            best_match = MatchResult(
                debt_instrument_id=instrument.id,
                document_section_id=agreement.id,
                match_confidence=min(score, 1.0),
                match_method="facility_type",
                match_evidence={
                    "signals": [
                        {
                            "type": s.signal_type,
                            "value_found": s.value_found,
                            "value_expected": s.value_expected,
                            "boost": s.confidence_boost,
                            "location": s.location,
                        }
                        for s in signals
                    ],
                    "document_title": title[:200],
                    "instrument_name": instrument.name,
                },
                signals=signals,
                relationship_type="governs",
            )

    return best_match


def find_all_matching_documents(
    instrument: DebtInstrument,
    documents: list[DocumentSection],
    issuer_name: str = None,
    min_confidence: float = 0.40,
) -> list[MatchResult]:
    """
    Find ALL documents that match an instrument, not just the best one.

    This allows linking multiple documents to a single instrument:
    - Base indenture (governs)
    - Supplemental indentures (supplements)
    - Amendments (amends)

    Returns list of MatchResults sorted by confidence (highest first).
    """
    if not documents:
        return []

    all_matches = []
    seen_doc_ids = set()

    # Get instrument characteristics
    inst_cusip = instrument.cusip.upper().replace(" ", "") if instrument.cusip else ""
    inst_isin = instrument.isin.upper() if instrument.isin else ""
    inst_coupon = instrument.interest_rate / 100 if instrument.interest_rate else None
    inst_maturity_year = instrument.maturity_date.year if instrument.maturity_date else None
    inst_issue_date = instrument.issue_date

    # If maturity_date is NULL but name contains "due YYYY", extract it
    if not inst_maturity_year and instrument.name:
        name_years = extract_maturity_years_from_text(instrument.name)
        if name_years:
            inst_maturity_year = name_years[0]  # Take first match

    # If coupon is NULL but name contains "X.XX%", extract it
    if not inst_coupon and instrument.name:
        name_coupons = extract_coupon_from_text(instrument.name)
        if name_coupons:
            inst_coupon = name_coupons[0]  # Take first match

    # Extract note descriptions from instrument
    inst_descriptions = extract_note_descriptions(instrument.name or "")
    if inst_coupon and inst_maturity_year:
        constructed = f"{inst_coupon:.2f}% notes {inst_maturity_year}"
        if constructed not in inst_descriptions:
            inst_descriptions.append(constructed)

    # Normalize issuer name for matching
    normalized_issuer = normalize_issuer_name(issuer_name) if issuer_name else ""

    for doc in documents:
        title = doc.section_title or ""
        content = doc.content or ""
        content_preview = content[:50000]

        signals = []
        confidence = 0.0
        match_method = "content"

        # Check CUSIP/ISIN match (highest confidence)
        if inst_cusip or inst_isin:
            doc_cusips = extract_cusips_from_text(content_preview)
            doc_isins = extract_isins_from_text(content_preview)

            if inst_cusip and inst_cusip in doc_cusips:
                confidence = max(confidence, 0.95)
                match_method = "identifier"
                signals.append(MatchSignal(
                    signal_type="cusip_match",
                    value_found=inst_cusip,
                    value_expected=inst_cusip,
                    confidence_boost=0.95,
                    location="content"
                ))
            elif inst_isin and inst_isin in doc_isins:
                confidence = max(confidence, 0.95)
                match_method = "identifier"
                signals.append(MatchSignal(
                    signal_type="isin_match",
                    value_found=inst_isin,
                    value_expected=inst_isin,
                    confidence_boost=0.95,
                    location="content"
                ))

        # Check note description match
        if inst_descriptions:
            title_descriptions = extract_note_descriptions(title)
            content_descriptions = extract_note_descriptions(content_preview[:30000])

            for inst_desc in inst_descriptions:
                if inst_desc in title_descriptions:
                    confidence = max(confidence, 0.90)
                    match_method = "note_description"
                    signals.append(MatchSignal(
                        signal_type="note_description_match",
                        value_found=inst_desc,
                        value_expected=inst_desc,
                        confidence_boost=0.90,
                        location="title"
                    ))
                    break
                elif inst_desc in content_descriptions:
                    confidence = max(confidence, 0.75)
                    match_method = "note_description"
                    signals.append(MatchSignal(
                        signal_type="note_description_match",
                        value_found=inst_desc,
                        value_expected=inst_desc,
                        confidence_boost=0.75,
                        location="content"
                    ))
                    break

        # Check issue date match
        if inst_issue_date and doc.filing_date:
            days_diff = abs((doc.filing_date - inst_issue_date).days)
            if days_diff == 0:
                confidence = max(confidence, 0.85)
                match_method = "issue_date"
                signals.append(MatchSignal(
                    signal_type="issue_date_match",
                    value_found=str(doc.filing_date),
                    value_expected=str(inst_issue_date),
                    confidence_boost=0.85,
                    location="metadata"
                ))
            elif days_diff <= 7:
                conf = 0.75
                confidence = max(confidence, conf)
                if match_method == "content":
                    match_method = "issue_date"
                signals.append(MatchSignal(
                    signal_type="issue_date_near",
                    value_found=str(doc.filing_date),
                    value_expected=f"{inst_issue_date} ({days_diff} days)",
                    confidence_boost=conf,
                    location="metadata"
                ))

        # Check issuer name + maturity year
        if normalized_issuer and len(normalized_issuer) >= 3:
            content_lower = content_preview.lower()
            title_lower = title.lower()

            issuer_found = normalized_issuer in content_lower or normalized_issuer in title_lower
            if not issuer_found:
                name_parts = normalized_issuer.split()
                if len(name_parts) >= 2:
                    key_name = ' '.join(name_parts[:2])
                    if len(key_name) >= 5:
                        issuer_found = key_name in content_lower or key_name in title_lower

            if issuer_found and inst_maturity_year:
                content_years = extract_maturity_years_from_text(content_preview[:30000])
                if inst_maturity_year in content_years:
                    conf = 0.65
                    confidence = max(confidence, conf)
                    if match_method == "content":
                        match_method = "issuer_date"
                    signals.append(MatchSignal(
                        signal_type="issuer_maturity_match",
                        value_found=f"{issuer_name} + {inst_maturity_year}",
                        value_expected=f"{issuer_name} + {inst_maturity_year}",
                        confidence_boost=conf,
                        location="content"
                    ))

        # Check coupon + maturity separately (lower confidence)
        if inst_coupon and inst_maturity_year:
            title_coupons = extract_coupon_from_text(title)
            content_coupons = extract_coupon_from_text(content_preview[:30000])
            title_years = extract_maturity_years_from_text(title)
            content_years = extract_maturity_years_from_text(content_preview[:30000])

            coupon_match = any(abs(tc - inst_coupon) < 0.01 for tc in title_coupons + content_coupons)
            maturity_match = inst_maturity_year in (title_years + content_years)

            if coupon_match and maturity_match:
                conf = 0.55
                confidence = max(confidence, conf)
                signals.append(MatchSignal(
                    signal_type="coupon_maturity_match",
                    value_found=f"{inst_coupon}% + {inst_maturity_year}",
                    value_expected=f"{inst_coupon}% + {inst_maturity_year}",
                    confidence_boost=conf,
                    location="content"
                ))

        # Multi-tranche indenture matching: check if our constructed description
        # appears in the document's list of note descriptions (higher confidence)
        if inst_coupon and inst_maturity_year:
            constructed_desc = f"{inst_coupon:.2f}% notes {inst_maturity_year}"
            # Extract all note descriptions from document content
            doc_descriptions = extract_note_descriptions(content_preview[:50000])

            if constructed_desc in doc_descriptions:
                # This is a strong match - document explicitly mentions this exact bond
                conf = 0.80
                confidence = max(confidence, conf)
                if match_method == "content":
                    match_method = "multi_tranche"
                signals.append(MatchSignal(
                    signal_type="multi_tranche_match",
                    value_found=constructed_desc,
                    value_expected=constructed_desc,
                    confidence_boost=conf,
                    location="content"
                ))
            else:
                # Check with tolerance for coupon rounding (e.g., 5.75% vs 5.750%)
                for doc_desc in doc_descriptions:
                    # Parse doc description: "X.XX% notes YYYY"
                    desc_match = re.match(r'(\d+\.?\d*)\s*%\s*notes\s+(\d{4})', doc_desc)
                    if desc_match:
                        doc_coupon = float(desc_match.group(1))
                        doc_year = int(desc_match.group(2))
                        if abs(doc_coupon - inst_coupon) < 0.02 and doc_year == inst_maturity_year:
                            conf = 0.78
                            confidence = max(confidence, conf)
                            if match_method == "content":
                                match_method = "multi_tranche"
                            signals.append(MatchSignal(
                                signal_type="multi_tranche_fuzzy",
                                value_found=doc_desc,
                                value_expected=constructed_desc,
                                confidence_boost=conf,
                                location="content"
                            ))
                            break

        # Amount matching for bonds: check if principal/outstanding amount appears
        inst_amount = instrument.outstanding or instrument.principal
        if inst_amount and inst_amount > 0:
            doc_amounts = extract_commitment_amounts(content_preview[:30000])
            for amount in doc_amounts:
                # Allow 5% tolerance for amount matching
                if abs(amount - inst_amount) / max(inst_amount, 1) < 0.05:
                    # Amount match alone is weak, but combined with other signals is strong
                    conf = 0.45
                    confidence = max(confidence, conf)
                    signals.append(MatchSignal(
                        signal_type="amount_match",
                        value_found=str(amount),
                        value_expected=str(inst_amount),
                        confidence_boost=conf,
                        location="content"
                    ))
                    # Boost confidence if we also have coupon or maturity match
                    if any(s.signal_type in ('coupon_maturity_match', 'multi_tranche_match', 'multi_tranche_fuzzy')
                           for s in signals):
                        confidence = min(confidence + 0.10, 0.90)
                    break

        # If we have a match above threshold, add it
        if confidence >= min_confidence and doc.id not in seen_doc_ids:
            seen_doc_ids.add(doc.id)

            # Determine relationship type based on document title/content
            relationship = classify_document_relationship(title, content_preview[:2000])

            all_matches.append(MatchResult(
                debt_instrument_id=instrument.id,
                document_section_id=doc.id,
                match_confidence=confidence,
                match_method=match_method,
                match_evidence={
                    "signals": [
                        {
                            "type": s.signal_type,
                            "value_found": s.value_found,
                            "value_expected": s.value_expected,
                            "boost": s.confidence_boost,
                            "location": s.location,
                        }
                        for s in signals
                    ],
                    "document_title": title[:200],
                    "instrument_name": instrument.name,
                },
                signals=signals,
                relationship_type=relationship,
            ))

    # Sort by confidence (highest first), then by relationship type priority
    relationship_priority = {"governs": 0, "supplements": 1, "amends": 2, "related": 3}
    all_matches.sort(key=lambda m: (-m.match_confidence, relationship_priority.get(m.relationship_type, 4)))

    return all_matches


def match_instrument_to_debt_footnotes(
    instrument: DebtInstrument,
    footnotes: list[DocumentSection],
    min_confidence: float = 0.50,
) -> list[MatchResult]:
    """
    Match an instrument to debt footnotes as a fallback when no indenture match.

    Debt footnotes list bonds with their coupon rates and maturities, e.g.:
    "3.000% Senior Notes due May 2027"

    Lower confidence (0.65) since footnotes describe but don't govern.
    """
    if not footnotes:
        return []

    matches = []

    # Get instrument characteristics
    inst_coupon = instrument.interest_rate / 100 if instrument.interest_rate else None
    inst_maturity_year = instrument.maturity_date.year if instrument.maturity_date else None

    # Extract from name if missing
    if not inst_maturity_year and instrument.name:
        name_years = extract_maturity_years_from_text(instrument.name)
        if name_years:
            inst_maturity_year = name_years[0]

    if not inst_coupon and instrument.name:
        name_coupons = extract_coupon_from_text(instrument.name)
        if name_coupons:
            inst_coupon = name_coupons[0]

    # Need both coupon and maturity to match
    if not inst_coupon or not inst_maturity_year:
        return []

    # Build coupon pattern
    coupon_str = f"{inst_coupon:.3f}".rstrip('0').rstrip('.')
    coupon_pattern = re.escape(coupon_str) + r"\s*%"

    for doc in footnotes:
        content = doc.content or ""
        if len(content) < 100:
            continue

        # Look for coupon rate in content
        coupon_matches = list(re.finditer(coupon_pattern, content, re.IGNORECASE))
        if not coupon_matches:
            coupon_short = f"{inst_coupon:.2f}".rstrip('0').rstrip('.')
            coupon_pattern_short = re.escape(coupon_short) + r"\s*%"
            coupon_matches = list(re.finditer(coupon_pattern_short, content, re.IGNORECASE))

        if not coupon_matches:
            continue

        # Check if maturity year appears near coupon (within 200 chars)
        maturity_pattern = rf"\b{inst_maturity_year}\b"
        for coupon_match in coupon_matches:
            start = max(0, coupon_match.start() - 50)
            end = min(len(content), coupon_match.end() + 200)
            context = content[start:end]

            if re.search(maturity_pattern, context):
                if re.search(r'\b(due|notes?|senior|bonds?)\b', context, re.IGNORECASE):
                    matched_text = context.strip()[:150]

                    matches.append(MatchResult(
                        debt_instrument_id=instrument.id,
                        document_section_id=doc.id,
                        match_confidence=0.65,
                        match_method="debt_footnote",
                        match_evidence={
                            "coupon": f"{inst_coupon:.3f}%",
                            "maturity_year": inst_maturity_year,
                            "matched_text": matched_text,
                        },
                        signals=[MatchSignal(
                            signal_type="debt_footnote_match",
                            value_found=matched_text[:80],
                            value_expected=f"{inst_coupon:.2f}% due {inst_maturity_year}",
                            confidence_boost=0.65,
                            location="debt_footnote"
                        )],
                        relationship_type="references",
                    ))
                    break

    matches.sort(key=lambda m: -m.match_confidence)
    return matches[:3]


async def match_debt_instruments_to_documents(
    session: AsyncSession,
    company_id: UUID,
    min_confidence: float = 0.0,
) -> CompanyMatchReport:
    """
    Match all debt instruments for a company to their governing documents.

    Args:
        session: Database session
        company_id: Company UUID
        min_confidence: Minimum confidence to include in matches (default 0.0 = all)

    Returns:
        CompanyMatchReport with all matches and unmatched instruments
    """
    # Get company
    result = await session.execute(
        select(Company).where(Company.id == company_id)
    )
    company = result.scalar_one_or_none()
    if not company:
        raise ValueError(f"Company not found: {company_id}")

    # Get all debt instruments for company with their issuer entities
    from sqlalchemy.orm import selectinload
    result = await session.execute(
        select(DebtInstrument)
        .options(selectinload(DebtInstrument.issuer))
        .where(DebtInstrument.company_id == company_id)
        .where(DebtInstrument.is_active == True)
    )
    instruments = list(result.scalars().all())

    # Get all indentures and credit agreements for company
    result = await session.execute(
        select(DocumentSection)
        .where(DocumentSection.company_id == company_id)
        .where(DocumentSection.section_type == "indenture")
    )
    indentures = list(result.scalars().all())

    result = await session.execute(
        select(DocumentSection)
        .where(DocumentSection.company_id == company_id)
        .where(DocumentSection.section_type == "credit_agreement")
    )
    credit_agreements = list(result.scalars().all())

    # Also get debt footnotes for fallback matching
    result = await session.execute(
        select(DocumentSection)
        .where(DocumentSection.company_id == company_id)
        .where(DocumentSection.section_type == "debt_footnote")
    )
    debt_footnotes = list(result.scalars().all())

    matches = []
    unmatched = []
    high_confidence_count = 0
    low_confidence_count = 0
    instruments_with_matches = set()

    for instrument in instruments:
        inst_type = instrument.instrument_type.lower() if instrument.instrument_type else ""

        # Determine if bond or loan
        is_bond = inst_type in BOND_TYPES or any(bt in inst_type for bt in BOND_TYPES)
        is_loan = inst_type in LOAN_TYPES or any(lt in inst_type for lt in LOAN_TYPES)

        # Get issuer name for matching
        issuer_name = instrument.issuer.name if instrument.issuer else None

        # Find ALL matching documents for this instrument
        if is_bond and indentures:
            instrument_matches = find_all_matching_documents(
                instrument, indentures, issuer_name, min_confidence
            )
        elif is_loan and credit_agreements:
            instrument_matches = find_all_matching_documents(
                instrument, credit_agreements, issuer_name, min_confidence
            )
        else:
            instrument_matches = []

        # Fallback: try debt footnote matching if no indenture/credit agreement match
        if not instrument_matches and is_bond and debt_footnotes:
            instrument_matches = match_instrument_to_debt_footnotes(
                instrument, debt_footnotes, min_confidence
            )

        if instrument_matches:
            instruments_with_matches.add(instrument.id)
            matches.extend(instrument_matches)

            # Count the best match for statistics
            best_match = instrument_matches[0]  # Already sorted by confidence
            if best_match.match_confidence >= 0.70:
                high_confidence_count += 1
            elif best_match.match_confidence >= 0.50:
                low_confidence_count += 1
        else:
            unmatched.append(instrument.id)

    return CompanyMatchReport(
        company_id=company_id,
        ticker=company.ticker,
        total_instruments=len(instruments),
        matched_high_confidence=high_confidence_count,
        matched_low_confidence=low_confidence_count,
        unmatched=len(unmatched),
        matches=matches,
        unmatched_instruments=unmatched,
    )


async def store_document_links(
    session: AsyncSession,
    matches: list[MatchResult],
    created_by: str = "algorithm",
    replace_existing: bool = False,
) -> int:
    """
    Store document links in the database.

    Args:
        session: Database session
        matches: List of MatchResults to store
        created_by: Attribution for who created the links
        replace_existing: If True, delete existing links first

    Returns:
        Number of links created
    """
    if not matches:
        return 0

    # Get set of debt instrument IDs we're updating
    instrument_ids = {m.debt_instrument_id for m in matches}

    if replace_existing:
        # Delete existing links for these instruments
        from sqlalchemy import delete
        await session.execute(
            delete(DebtInstrumentDocument)
            .where(DebtInstrumentDocument.debt_instrument_id.in_(instrument_ids))
        )

    created = 0
    for match in matches:
        # Only store matches with confidence >= 0.50
        if match.match_confidence < 0.50:
            continue

        # Check if this link already exists
        from sqlalchemy import select
        existing = await session.execute(
            select(DebtInstrumentDocument.id).where(
                DebtInstrumentDocument.debt_instrument_id == match.debt_instrument_id,
                DebtInstrumentDocument.document_section_id == match.document_section_id
            )
        )
        if existing.scalar_one_or_none():
            continue  # Skip existing links

        link = DebtInstrumentDocument(
            debt_instrument_id=match.debt_instrument_id,
            document_section_id=match.document_section_id,
            relationship_type=match.relationship_type,
            match_confidence=Decimal(str(round(match.match_confidence, 3))),
            match_method=match.match_method,
            match_evidence=match.match_evidence,
            is_verified=False,
            created_by=created_by,
        )
        session.add(link)
        created += 1

    if created > 0:
        await session.commit()

    return created


async def get_unlinked_instruments(
    session: AsyncSession,
    company_id: Optional[UUID] = None,
    instrument_type: Optional[str] = None,
) -> list[tuple[DebtInstrument, str]]:
    """
    Get debt instruments that don't have any document links.

    Returns list of (DebtInstrument, company_ticker) tuples.
    """
    from sqlalchemy.orm import selectinload

    query = (
        select(DebtInstrument, Company.ticker)
        .join(Company)
        .outerjoin(DebtInstrumentDocument)
        .where(DebtInstrumentDocument.id.is_(None))
        .where(DebtInstrument.is_active == True)
    )

    if company_id:
        query = query.where(DebtInstrument.company_id == company_id)

    if instrument_type:
        query = query.where(DebtInstrument.instrument_type == instrument_type)

    result = await session.execute(query)
    return [(row[0], row[1]) for row in result.fetchall()]


def extract_issuer_from_document(title: str, content: str) -> Optional[str]:
    """
    Extract the issuer name from a document title or content.

    Common patterns in indenture titles:
    - "INDENTURE between [ISSUER] and [TRUSTEE]"
    - "[ISSUER] Indenture dated..."
    - "Indenture dated... [ISSUER] as Issuer"
    """
    title = title or ""
    content = (content or "")[:10000]

    # Pattern 1: "between X and Y as Trustee" or "between X, as Issuer"
    between_pattern = r'between\s+([A-Z][A-Za-z0-9\s,\.&\']+?)(?:\s*,?\s*(?:as\s+(?:Issuer|Company|Borrower)|and\s+))'
    match = re.search(between_pattern, title + " " + content[:2000])
    if match:
        issuer = match.group(1).strip()
        # Clean up trailing punctuation and common suffixes
        issuer = re.sub(r',?\s*$', '', issuer)
        if len(issuer) > 3 and len(issuer) < 100:
            return issuer

    # Pattern 2: "ISSUER NAME Indenture" at start
    start_pattern = r'^([A-Z][A-Za-z0-9\s,\.&\']+?)\s+(?:Indenture|INDENTURE)'
    match = re.search(start_pattern, title)
    if match:
        issuer = match.group(1).strip()
        if len(issuer) > 3 and len(issuer) < 100:
            return issuer

    # Pattern 3: Look for "Issuer:" or "Borrower:" in content
    issuer_label_pattern = r'(?:Issuer|Borrower|Company)[:\s]+([A-Z][A-Za-z0-9\s,\.&\']+?)(?:\n|,|\.|$)'
    match = re.search(issuer_label_pattern, content[:5000])
    if match:
        issuer = match.group(1).strip()
        if len(issuer) > 3 and len(issuer) < 100:
            return issuer

    return None


def extract_dates_from_document(title: str, content: str) -> dict:
    """
    Extract issue date and maturity date from a document.

    Returns dict with:
    - issue_date: date object or None
    - maturity_year: int or None
    - maturity_date: date object or None
    """
    from datetime import datetime

    title = title or ""
    content = (content or "")[:30000]
    full_text = title + " " + content

    result = {
        "issue_date": None,
        "maturity_year": None,
        "maturity_date": None,
    }

    # Extract maturity years (already have this function)
    maturity_years = extract_maturity_years_from_text(full_text)
    if maturity_years:
        # Take the most common or latest year
        result["maturity_year"] = max(maturity_years)

    # Try to extract full maturity date
    # Pattern: "due [Month] [Day], [Year]" or "maturing [Month] [Day], [Year]"
    mat_date_pattern = r'(?:due|matur\w*)\s+(?:on\s+)?([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})'
    match = re.search(mat_date_pattern, full_text, re.IGNORECASE)
    if match:
        try:
            month_str = match.group(1)
            day = int(match.group(2))
            year = int(match.group(3))
            # Parse month name
            month_map = {
                'january': 1, 'february': 2, 'march': 3, 'april': 4,
                'may': 5, 'june': 6, 'july': 7, 'august': 8,
                'september': 9, 'october': 10, 'november': 11, 'december': 12
            }
            month = month_map.get(month_str.lower())
            if month and 2000 <= year <= 2100:
                result["maturity_date"] = date(year, month, day)
                result["maturity_year"] = year
        except (ValueError, KeyError):
            pass

    # Try to extract issue date from "dated [Month] [Day], [Year]" or "dated as of [date]"
    issue_date_pattern = r'(?:dated|dated\s+as\s+of)\s+([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})'
    match = re.search(issue_date_pattern, full_text, re.IGNORECASE)
    if match:
        try:
            month_str = match.group(1)
            day = int(match.group(2))
            year = int(match.group(3))
            month_map = {
                'january': 1, 'february': 2, 'march': 3, 'april': 4,
                'may': 5, 'june': 6, 'july': 7, 'august': 8,
                'september': 9, 'october': 10, 'november': 11, 'december': 12
            }
            month = month_map.get(month_str.lower())
            if month and 2000 <= year <= 2100:
                result["issue_date"] = date(year, month, day)
        except (ValueError, KeyError):
            pass

    return result


@dataclass
class DocumentMatchResult:
    """Result of matching a document to debt instruments."""
    document_section_id: UUID
    debt_instrument_id: UUID
    match_confidence: float
    match_method: str
    match_evidence: dict
    relationship_type: str = "governs"


def match_document_to_instruments(
    document: DocumentSection,
    instruments: list[DebtInstrument],
    min_confidence: float = 0.40,
) -> list[DocumentMatchResult]:
    """
    Match a single document to potential debt instruments.

    This is the REVERSE of the normal matching - start from document,
    extract identifiers, and find matching instruments.

    Returns list of DocumentMatchResult sorted by confidence.
    """
    if not instruments:
        return []

    title = document.section_title or ""
    content = document.content or ""
    content_preview = content[:50000]

    # Extract identifiers from document
    doc_cusips = extract_cusips_from_text(content_preview)
    doc_isins = extract_isins_from_text(content_preview)
    doc_coupons = extract_coupon_from_text(title + " " + content_preview[:10000])
    doc_maturity_years = extract_maturity_years_from_text(title + " " + content_preview[:30000])
    doc_note_descriptions = extract_note_descriptions(title + " " + content_preview[:5000])
    doc_issuer = extract_issuer_from_document(title, content_preview)
    doc_dates = extract_dates_from_document(title, content_preview)

    # Normalize issuer for matching
    normalized_doc_issuer = normalize_issuer_name(doc_issuer) if doc_issuer else ""

    matches = []

    for instrument in instruments:
        signals = []
        confidence = 0.0
        match_method = "reverse_match"

        # Get instrument characteristics
        inst_cusip = instrument.cusip.upper().replace(" ", "") if instrument.cusip else ""
        inst_isin = instrument.isin.upper() if instrument.isin else ""
        inst_coupon = instrument.interest_rate / 100 if instrument.interest_rate else None
        inst_maturity_year = instrument.maturity_date.year if instrument.maturity_date else None
        inst_maturity_date = instrument.maturity_date
        inst_issue_date = instrument.issue_date
        inst_name = instrument.name or ""

        # Get issuer name from instrument's issuer entity
        inst_issuer_name = None
        if hasattr(instrument, 'issuer') and instrument.issuer:
            inst_issuer_name = instrument.issuer.name
        normalized_inst_issuer = normalize_issuer_name(inst_issuer_name) if inst_issuer_name else ""

        # Strategy 1: CUSIP/ISIN match (highest confidence)
        if inst_cusip and inst_cusip in doc_cusips:
            confidence = max(confidence, 0.95)
            match_method = "identifier"
            signals.append({
                "type": "cusip_match",
                "doc_value": inst_cusip,
                "inst_value": inst_cusip,
                "boost": 0.95
            })
        elif inst_isin and inst_isin in doc_isins:
            confidence = max(confidence, 0.95)
            match_method = "identifier"
            signals.append({
                "type": "isin_match",
                "doc_value": inst_isin,
                "inst_value": inst_isin,
                "boost": 0.95
            })

        # Strategy 2: Note description match
        if doc_note_descriptions:
            inst_descriptions = extract_note_descriptions(inst_name)
            if inst_coupon and inst_maturity_year:
                constructed = f"{inst_coupon:.2f}% notes {inst_maturity_year}"
                if constructed not in inst_descriptions:
                    inst_descriptions.append(constructed)

            for doc_desc in doc_note_descriptions:
                if doc_desc in inst_descriptions:
                    confidence = max(confidence, 0.85)
                    match_method = "note_description"
                    signals.append({
                        "type": "note_description_match",
                        "doc_value": doc_desc,
                        "inst_value": doc_desc,
                        "boost": 0.85
                    })
                    break

        # Strategy 3: Issuer name + maturity year match
        if normalized_doc_issuer and normalized_inst_issuer:
            # Check if issuers match
            issuer_match = (
                normalized_doc_issuer in normalized_inst_issuer or
                normalized_inst_issuer in normalized_doc_issuer
            )
            # Also try matching key words
            if not issuer_match:
                doc_parts = normalized_doc_issuer.split()[:2]
                inst_parts = normalized_inst_issuer.split()[:2]
                if len(doc_parts) >= 2 and len(inst_parts) >= 2:
                    issuer_match = ' '.join(doc_parts) == ' '.join(inst_parts)

            if issuer_match:
                # Issuer matches - now check dates
                if inst_maturity_year and inst_maturity_year in doc_maturity_years:
                    conf = 0.75
                    confidence = max(confidence, conf)
                    if match_method == "reverse_match":
                        match_method = "issuer_maturity"
                    signals.append({
                        "type": "issuer_maturity_match",
                        "doc_value": f"{doc_issuer} + {inst_maturity_year}",
                        "inst_value": f"{inst_issuer_name} + {inst_maturity_year}",
                        "boost": conf
                    })
                elif inst_issue_date and doc_dates.get("issue_date"):
                    days_diff = abs((doc_dates["issue_date"] - inst_issue_date).days)
                    if days_diff <= 7:
                        conf = 0.80 if days_diff == 0 else 0.70
                        confidence = max(confidence, conf)
                        if match_method == "reverse_match":
                            match_method = "issuer_issue_date"
                        signals.append({
                            "type": "issuer_issue_date_match",
                            "doc_value": f"{doc_issuer} + {doc_dates['issue_date']}",
                            "inst_value": f"{inst_issuer_name} + {inst_issue_date}",
                            "boost": conf
                        })

        # Strategy 4: Issue date match (filing date)
        if inst_issue_date and document.filing_date:
            days_diff = abs((document.filing_date - inst_issue_date).days)
            if days_diff == 0:
                conf = 0.70
                confidence = max(confidence, conf)
                signals.append({
                    "type": "filing_issue_date_match",
                    "doc_value": str(document.filing_date),
                    "inst_value": str(inst_issue_date),
                    "boost": conf
                })
            elif days_diff <= 7:
                conf = 0.55
                confidence = max(confidence, conf)
                signals.append({
                    "type": "filing_issue_date_near",
                    "doc_value": str(document.filing_date),
                    "inst_value": f"{inst_issue_date} ({days_diff} days)",
                    "boost": conf
                })

        # Strategy 5: Coupon + maturity year match (without issuer)
        if inst_coupon and inst_maturity_year:
            coupon_match = any(abs(dc - inst_coupon) < 0.01 for dc in doc_coupons)
            maturity_match = inst_maturity_year in doc_maturity_years

            if coupon_match and maturity_match:
                conf = 0.50
                confidence = max(confidence, conf)
                signals.append({
                    "type": "coupon_maturity_match",
                    "doc_value": f"{doc_coupons} + {doc_maturity_years}",
                    "inst_value": f"{inst_coupon}% + {inst_maturity_year}",
                    "boost": conf
                })

        # Strategy 6: Maturity date exact match
        if inst_maturity_date and doc_dates.get("maturity_date"):
            if inst_maturity_date == doc_dates["maturity_date"]:
                conf = 0.65
                confidence = max(confidence, conf)
                signals.append({
                    "type": "maturity_date_exact_match",
                    "doc_value": str(doc_dates["maturity_date"]),
                    "inst_value": str(inst_maturity_date),
                    "boost": conf
                })

        if confidence >= min_confidence and signals:
            # Determine relationship type
            relationship = classify_document_relationship(title, content_preview[:2000])

            matches.append(DocumentMatchResult(
                document_section_id=document.id,
                debt_instrument_id=instrument.id,
                match_confidence=confidence,
                match_method=match_method,
                match_evidence={
                    "signals": signals,
                    "document_title": title[:200],
                    "instrument_name": inst_name,
                    "doc_issuer_extracted": doc_issuer,
                    "doc_maturity_years": list(doc_maturity_years)[:5] if doc_maturity_years else [],
                    "doc_coupons": doc_coupons[:5] if doc_coupons else [],
                },
                relationship_type=relationship,
            ))

    # Sort by confidence (highest first)
    matches.sort(key=lambda m: -m.match_confidence)

    return matches


async def match_unlinked_documents_to_instruments(
    session: AsyncSession,
    company_id: Optional[UUID] = None,
    min_confidence: float = 0.40,
) -> dict:
    """
    Find unlinked documents and match them to debt instruments.

    This is the REVERSE approach - start from documents without links
    and find matching instruments.

    Returns dict with:
    - total_unlinked_docs: int
    - matched_docs: int
    - new_links: list of DocumentMatchResult
    - unmatched_docs: list of document IDs
    """
    from sqlalchemy.orm import selectinload

    # Get all documents (indentures and credit agreements) that have NO links
    linked_doc_ids_query = select(DebtInstrumentDocument.document_section_id).distinct()
    linked_result = await session.execute(linked_doc_ids_query)
    linked_doc_ids = {row[0] for row in linked_result.fetchall()}

    # Query for unlinked documents
    doc_query = (
        select(DocumentSection)
        .where(DocumentSection.section_type.in_(['indenture', 'credit_agreement']))
    )
    if company_id:
        doc_query = doc_query.where(DocumentSection.company_id == company_id)

    doc_result = await session.execute(doc_query)
    all_docs = list(doc_result.scalars().all())

    unlinked_docs = [d for d in all_docs if d.id not in linked_doc_ids]

    # Get all debt instruments with their issuers
    inst_query = (
        select(DebtInstrument)
        .options(selectinload(DebtInstrument.issuer))
        .where(DebtInstrument.is_active == True)
    )
    if company_id:
        inst_query = inst_query.where(DebtInstrument.company_id == company_id)

    inst_result = await session.execute(inst_query)
    all_instruments = list(inst_result.scalars().all())

    # Group instruments by company_id for efficient matching
    instruments_by_company = {}
    for inst in all_instruments:
        if inst.company_id not in instruments_by_company:
            instruments_by_company[inst.company_id] = []
        instruments_by_company[inst.company_id].append(inst)

    all_new_links = []
    matched_doc_ids = set()
    unmatched_doc_ids = []

    for doc in unlinked_docs:
        # Only match against instruments from the same company
        company_instruments = instruments_by_company.get(doc.company_id, [])

        if not company_instruments:
            unmatched_doc_ids.append(doc.id)
            continue

        # Filter by document type -> instrument type
        if doc.section_type == "indenture":
            # Indentures match to bond-type instruments
            filtered_instruments = [
                i for i in company_instruments
                if i.instrument_type and (
                    i.instrument_type.lower() in BOND_TYPES or
                    any(bt in i.instrument_type.lower() for bt in BOND_TYPES)
                )
            ]
        else:
            # Credit agreements match to loan-type instruments
            filtered_instruments = [
                i for i in company_instruments
                if i.instrument_type and (
                    i.instrument_type.lower() in LOAN_TYPES or
                    any(lt in i.instrument_type.lower() for lt in LOAN_TYPES)
                )
            ]

        if not filtered_instruments:
            unmatched_doc_ids.append(doc.id)
            continue

        # Match this document against relevant instruments
        matches = match_document_to_instruments(doc, filtered_instruments, min_confidence)

        if matches:
            matched_doc_ids.add(doc.id)
            all_new_links.extend(matches)
        else:
            unmatched_doc_ids.append(doc.id)

    return {
        "total_unlinked_docs": len(unlinked_docs),
        "matched_docs": len(matched_doc_ids),
        "new_links": all_new_links,
        "unmatched_docs": unmatched_doc_ids,
    }


async def store_reverse_match_links(
    session: AsyncSession,
    matches: list[DocumentMatchResult],
    created_by: str = "reverse_algorithm",
    skip_existing: bool = True,
) -> int:
    """
    Store links from reverse matching in the database.

    Args:
        session: Database session
        matches: List of DocumentMatchResults to store
        created_by: Attribution for who created the links
        skip_existing: If True, skip links that already exist

    Returns:
        Number of links created
    """
    if not matches:
        return 0

    # Get existing links if skip_existing
    existing_pairs = set()
    if skip_existing:
        existing_query = select(
            DebtInstrumentDocument.debt_instrument_id,
            DebtInstrumentDocument.document_section_id
        )
        result = await session.execute(existing_query)
        existing_pairs = {(row[0], row[1]) for row in result.fetchall()}

    created = 0
    for match in matches:
        # Skip if already exists
        if skip_existing and (match.debt_instrument_id, match.document_section_id) in existing_pairs:
            continue

        # Only store matches with confidence >= 0.50
        if match.match_confidence < 0.50:
            continue

        link = DebtInstrumentDocument(
            debt_instrument_id=match.debt_instrument_id,
            document_section_id=match.document_section_id,
            relationship_type=match.relationship_type,
            match_confidence=Decimal(str(round(match.match_confidence, 3))),
            match_method=match.match_method,
            match_evidence=match.match_evidence,
            is_verified=False,
            created_by=created_by,
        )
        session.add(link)
        created += 1

    if created > 0:
        await session.commit()

    return created
