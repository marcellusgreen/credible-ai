"""
Obligor Group Extraction Service for DebtStack.ai

Extracts SEC Rule 13-01 Summarized Financial Information for Obligor Groups
from 10-Q and 10-K filings. This reveals "hidden" credit data showing what
assets/income creditors can actually claim vs. what leaks to unrestricted subs.
"""

import os
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Company, ObligorGroupFinancials
from app.services.extraction import SecApiClient, clean_filing_html
from app.services.qa_agent import parse_json_robust

# Import API clients
import anthropic
import google.generativeai as genai


# =============================================================================
# PROMPTS
# =============================================================================

SYSTEM_PROMPT = """You are a credit analyst extracting SEC Rule 13-01 Summarized Financial Information from SEC filings.

Your job is to find and extract the "Obligor Group" financial data - this is the combined financials of the
debt Issuer + all Guarantors, which shows what assets/income creditors can actually claim.

WHERE TO FIND THIS DATA:
1. Notes to Financial Statements - Look for:
   - "Summarized Financial Information" (SEC Rule 13-01 specific title)
   - "Guarantor Financial Information"
   - "Supplemental Financial Information"
   - "Condensed Consolidating Financial Information"
   - Usually Note 18-22 in most 10-Q/10-K filings

2. Look for tables showing:
   - "Parent Company" or "Issuer"
   - "Guarantor Subsidiaries" or "Guarantors"
   - "Non-Guarantor Subsidiaries"
   - "Consolidating Eliminations"
   - "Consolidated Total"

3. The Obligor Group = Issuer + Guarantors (combined, after eliminations)

CRITICAL RULES:
1. All amounts must be in USD CENTS (multiply dollars by 100)
   - $1 million = 100,000,000 cents
   - $1 billion = 100,000,000,000 cents
2. Use QUARTERLY figures for income statement items (not year-to-date, unless Q4)
3. Use PERIOD END figures for balance sheet items
4. Return null for any metric you cannot find - do not estimate or guess
5. Note which debt instruments the disclosure relates to (often stated at top of note)

WHAT INDICATES "ASSET LEAKAGE":
- Non-Guarantor Subsidiaries hold significant assets that creditors cannot claim
- Obligor Group assets << Consolidated assets indicates leakage to unrestricted subs
- High intercompany receivables in Obligor Group that are owed by non-guarantors is risky
"""

OBLIGOR_GROUP_EXTRACTION_PROMPT = """
Extract SEC Rule 13-01 Summarized Financial Information (Obligor Group data) from this SEC filing.

COMPANY: {company_name} ({ticker})
FILING TYPE: {filing_type}
PERIOD END: {period_end}

FILING CONTENT:
{filing_content}

First, search for sections titled:
- "Summarized Financial Information"
- "Guarantor Financial Information"
- "Supplemental Financial Information"
- "Condensed Consolidating Financial Information"

If found, extract and return this JSON structure:

{{
  "found_disclosure": true,
  "disclosure_note_number": "Note 18",
  "debt_description": "Description of which debt instruments this obligor group supports",

  "fiscal_year": {fiscal_year},
  "fiscal_quarter": {fiscal_quarter},
  "period_end_date": "{period_end}",

  // OBLIGOR GROUP (Issuer + Guarantors Combined) - BALANCE SHEET (in cents)
  "og_total_assets": null,
  "og_total_liabilities": null,
  "og_stockholders_equity": null,
  "og_intercompany_receivables": null,

  // OBLIGOR GROUP - INCOME STATEMENT (quarterly, in cents)
  "og_revenue": null,
  "og_operating_income": null,
  "og_ebitda": null,
  "og_net_income": null,

  // CONSOLIDATED TOTALS (for leakage calculation, in cents)
  "consolidated_total_assets": null,
  "consolidated_revenue": null,
  "consolidated_ebitda": null,

  // NON-GUARANTOR SUBSIDIARIES (if disclosed separately, in cents)
  "non_guarantor_assets": null,
  "non_guarantor_revenue": null,

  "uncertainties": []
}}

If NO Rule 13-01 / Summarized Financial Information / Guarantor Financial Information is found, return:
{{
  "found_disclosure": false,
  "reason": "Brief explanation of what was searched for and not found",
  "fiscal_year": {fiscal_year},
  "fiscal_quarter": {fiscal_quarter},
  "period_end_date": "{period_end}",
  "uncertainties": []
}}

EXTRACTION TIPS:
- Some companies disclose Issuer and Guarantors separately - you need to SUM them for Obligor Group totals
- Watch for "Eliminations" column - subtract eliminations from the sum
- "Intercompany" items between Issuer/Guarantors should be eliminated; only keep intercompany with non-guarantors
- The "Combined" or "Obligor Group" column (if present) is what we want
- Non-Guarantor column shows what creditors CANNOT claim

IMPORTANT:
- All amounts in USD CENTS
- Return null (not 0) for missing data
- Note in uncertainties any values that seem unclear or potentially misread
- Include which specific debt instruments (by name) the disclosure relates to if stated
"""

OBLIGOR_GROUP_KEYWORDS = [
    "summarized financial information",
    "guarantor financial information",
    "supplemental financial information",
    "condensed consolidating",
    "obligor group",
    "issuer and guarantor",
    "non-guarantor subsidiaries",
    "subsidiary guarantors",
    "parent company only",
    "combined guarantor",
    "rule 13-01",
    "guarantor of the notes",
    "guarantee the notes",
]


# =============================================================================
# PYDANTIC MODELS
# =============================================================================

class ExtractedObligorGroup(BaseModel):
    """Validated obligor group extraction result."""

    found_disclosure: bool
    reason: Optional[str] = None
    disclosure_note_number: Optional[str] = None
    debt_description: Optional[str] = None

    fiscal_year: int
    fiscal_quarter: int = Field(ge=1, le=4)
    period_end_date: str

    # Obligor Group Balance Sheet
    og_total_assets: Optional[int] = None
    og_total_liabilities: Optional[int] = None
    og_stockholders_equity: Optional[int] = None
    og_intercompany_receivables: Optional[int] = None

    # Obligor Group Income Statement
    og_revenue: Optional[int] = None
    og_operating_income: Optional[int] = None
    og_ebitda: Optional[int] = None
    og_net_income: Optional[int] = None

    # Consolidated (for comparison)
    consolidated_total_assets: Optional[int] = None
    consolidated_revenue: Optional[int] = None
    consolidated_ebitda: Optional[int] = None

    # Non-Guarantor
    non_guarantor_assets: Optional[int] = None
    non_guarantor_revenue: Optional[int] = None

    uncertainties: list[str] = Field(default_factory=list)

    @field_validator("period_end_date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"Invalid date format: {v}. Expected YYYY-MM-DD")
        return v


# =============================================================================
# EXTRACTION FUNCTIONS
# =============================================================================

def extract_obligor_group_sections(filing_content: str, max_chars: int = 200000) -> str:
    """Extract sections containing Rule 13-01 / Summarized Financial Information."""
    if len(filing_content) <= max_chars:
        return filing_content

    content_lower = filing_content.lower()
    sections = []

    for keyword in OBLIGOR_GROUP_KEYWORDS:
        start = 0
        while True:
            idx = content_lower.find(keyword, start)
            if idx == -1:
                break

            # Extract larger context around obligor group disclosures
            # These tables can be quite large
            section_start = max(0, idx - 2000)
            section_end = min(len(filing_content), idx + 35000)
            section = filing_content[section_start:section_end]

            if section not in sections:
                sections.append(section)

            start = idx + len(keyword)

            if sum(len(s) for s in sections) > max_chars:
                break

        if sum(len(s) for s in sections) > max_chars:
            break

    if not sections:
        # Fallback: return first chunk
        return filing_content[:max_chars]

    combined = "\n\n---\n\n".join(sections)
    return combined[:max_chars]


def calculate_leakage(
    og_value: Optional[int],
    consolidated_value: Optional[int]
) -> Optional[Decimal]:
    """
    Calculate leakage percentage.
    Leakage = (Consolidated - Obligor Group) / Consolidated * 100

    Returns percentage as Decimal or None if calculation not possible.
    """
    if og_value is None or consolidated_value is None:
        return None
    if consolidated_value <= 0:
        return None

    leakage = (consolidated_value - og_value) / consolidated_value * 100
    return Decimal(str(round(leakage, 2)))


def determine_fiscal_period(filing_date: str, filing_type: str) -> tuple[int, int]:
    """Determine fiscal year and quarter from filing date."""
    try:
        d = datetime.strptime(filing_date, "%Y-%m-%d")
    except ValueError:
        today = date.today()
        return today.year, 4

    year = d.year
    month = d.month

    if filing_type == "10-K":
        quarter = 4
    else:
        if month <= 3:
            quarter = 4
            year -= 1
        elif month <= 6:
            quarter = 1
        elif month <= 9:
            quarter = 2
        else:
            quarter = 3

    return year, quarter


async def extract_obligor_group(
    ticker: str,
    cik: Optional[str] = None,
    filing_type: str = "10-Q",
    use_claude: bool = False,
) -> Optional[ExtractedObligorGroup]:
    """
    Extract Rule 13-01 Summarized Financial Information from SEC filing.

    Args:
        ticker: Stock ticker symbol
        cik: SEC Central Index Key (optional)
        filing_type: "10-Q" or "10-K"
        use_claude: Use Claude instead of Gemini for extraction

    Returns:
        ExtractedObligorGroup or None if extraction failed
    """
    # Fetch filing
    sec_api_key = os.getenv("SEC_API_KEY")
    if not sec_api_key:
        print("SEC_API_KEY not set")
        return None

    sec_client = SecApiClient(api_key=sec_api_key)
    filings = sec_client.get_filings_by_ticker(
        ticker,
        form_types=[filing_type],
        max_filings=1,
        cik=cik,
    )

    if not filings:
        print(f"No {filing_type} filings found for {ticker}")
        return None

    filing = filings[0]
    filing_url = filing.get("linkToFilingDetails", "")
    filing_date = filing.get("filedAt", "")[:10]

    # Download and clean filing
    content = sec_client.get_filing_content(filing_url)
    if not content:
        print(f"Failed to download filing for {ticker}")
        return None

    if content.strip().startswith("<") or content.strip().startswith("<?xml"):
        content = clean_filing_html(content)

    # Extract obligor group sections
    content = extract_obligor_group_sections(content)

    # Determine fiscal period
    fiscal_year, fiscal_quarter = determine_fiscal_period(filing_date, filing_type)

    # Build prompt
    prompt = OBLIGOR_GROUP_EXTRACTION_PROMPT.format(
        company_name=ticker,
        ticker=ticker,
        filing_type=filing_type,
        period_end=filing_date,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
        filing_content=content,
    )

    # Call LLM
    if use_claude:
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        response_text = response.content[0].text
    else:
        # Configure Gemini
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            generation_config={
                "temperature": 0.1,
                "response_mime_type": "application/json",
                "max_output_tokens": 4000,
            },
            system_instruction=SYSTEM_PROMPT,
        )
        response = model.generate_content(prompt)
        response_text = response.text

    # Parse response
    try:
        data = parse_json_robust(response_text)
        if data:
            return ExtractedObligorGroup(**data)
    except Exception as e:
        print(f"Failed to parse obligor group extraction: {e}")
        print(f"Response: {response_text[:500]}...")

    return None


async def save_obligor_group_to_db(
    session: AsyncSession,
    ticker: str,
    og_data: ExtractedObligorGroup,
    source_filing: Optional[str] = None,
) -> Optional[ObligorGroupFinancials]:
    """Save extracted obligor group financials to database."""
    # Find company
    result = await session.execute(
        select(Company).where(Company.ticker == ticker)
    )
    company = result.scalar_one_or_none()

    if not company:
        print(f"Company not found: {ticker}")
        return None

    # Calculate leakage metrics
    asset_leakage = calculate_leakage(
        og_data.og_total_assets,
        og_data.consolidated_total_assets
    )
    revenue_leakage = calculate_leakage(
        og_data.og_revenue,
        og_data.consolidated_revenue
    )
    ebitda_leakage = calculate_leakage(
        og_data.og_ebitda,
        og_data.consolidated_ebitda
    )

    # Check if record already exists
    result = await session.execute(
        select(ObligorGroupFinancials).where(
            ObligorGroupFinancials.company_id == company.id,
            ObligorGroupFinancials.fiscal_year == og_data.fiscal_year,
            ObligorGroupFinancials.fiscal_quarter == og_data.fiscal_quarter,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        # Update existing record
        existing.disclosure_note_number = og_data.disclosure_note_number
        existing.debt_description = og_data.debt_description
        existing.og_total_assets = og_data.og_total_assets
        existing.og_total_liabilities = og_data.og_total_liabilities
        existing.og_stockholders_equity = og_data.og_stockholders_equity
        existing.og_intercompany_receivables = og_data.og_intercompany_receivables
        existing.og_revenue = og_data.og_revenue
        existing.og_operating_income = og_data.og_operating_income
        existing.og_ebitda = og_data.og_ebitda
        existing.og_net_income = og_data.og_net_income
        existing.consolidated_total_assets = og_data.consolidated_total_assets
        existing.consolidated_revenue = og_data.consolidated_revenue
        existing.consolidated_ebitda = og_data.consolidated_ebitda
        existing.non_guarantor_assets = og_data.non_guarantor_assets
        existing.non_guarantor_revenue = og_data.non_guarantor_revenue
        existing.asset_leakage_pct = asset_leakage
        existing.revenue_leakage_pct = revenue_leakage
        existing.ebitda_leakage_pct = ebitda_leakage
        existing.source_filing = source_filing
        existing.uncertainties = og_data.uncertainties
        existing.extracted_at = datetime.utcnow()
        await session.commit()
        return existing

    # Create new record
    record = ObligorGroupFinancials(
        company_id=company.id,
        fiscal_year=og_data.fiscal_year,
        fiscal_quarter=og_data.fiscal_quarter,
        period_end_date=datetime.strptime(og_data.period_end_date, "%Y-%m-%d").date(),
        filing_type=og_data.fiscal_quarter < 4 and "10-Q" or "10-K",
        disclosure_note_number=og_data.disclosure_note_number,
        debt_description=og_data.debt_description,
        og_total_assets=og_data.og_total_assets,
        og_total_liabilities=og_data.og_total_liabilities,
        og_stockholders_equity=og_data.og_stockholders_equity,
        og_intercompany_receivables=og_data.og_intercompany_receivables,
        og_revenue=og_data.og_revenue,
        og_operating_income=og_data.og_operating_income,
        og_ebitda=og_data.og_ebitda,
        og_net_income=og_data.og_net_income,
        consolidated_total_assets=og_data.consolidated_total_assets,
        consolidated_revenue=og_data.consolidated_revenue,
        consolidated_ebitda=og_data.consolidated_ebitda,
        non_guarantor_assets=og_data.non_guarantor_assets,
        non_guarantor_revenue=og_data.non_guarantor_revenue,
        asset_leakage_pct=asset_leakage,
        revenue_leakage_pct=revenue_leakage,
        ebitda_leakage_pct=ebitda_leakage,
        source_filing=source_filing,
        uncertainties=og_data.uncertainties,
    )
    session.add(record)
    await session.commit()

    return record


async def get_latest_obligor_group(
    session: AsyncSession,
    company_id: UUID,
) -> Optional[ObligorGroupFinancials]:
    """Get the most recent obligor group financial data for a company."""
    result = await session.execute(
        select(ObligorGroupFinancials)
        .where(ObligorGroupFinancials.company_id == company_id)
        .order_by(
            ObligorGroupFinancials.fiscal_year.desc(),
            ObligorGroupFinancials.fiscal_quarter.desc(),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()
