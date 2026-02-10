"""
Financial Extraction Service for DebtStack.ai

Extracts quarterly financial data from SEC 10-Q and 10-K filings.
"""

import asyncio
import json
import os
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Company, CompanyFinancials
from app.services.extraction import SecApiClient
from app.services.utils import clean_filing_html, parse_json_robust

# Import API clients
import anthropic
import google.generativeai as genai


# =============================================================================
# PROMPTS
# =============================================================================

SYSTEM_PROMPT = """You are a financial analyst extracting quarterly financial data from SEC 10-Q/10-K filings.

Your job is to extract specific financial metrics from the filing's financial statements:
- Consolidated Statements of Operations (Income Statement)
- Consolidated Balance Sheets
- Consolidated Statements of Cash Flows

CRITICAL RULES:
1. Extract the EXACT numbers as shown in the filing - DO NOT convert units
   - If the filing says "in millions" and shows "41,352", return 41352
   - If the filing says "in thousands" and shows "41,352,000", return 41352000
   - Just extract the raw numeric values exactly as presented
2. Use QUARTERLY figures for income statement and cash flow (not year-to-date, unless Q4)
3. Use PERIOD END figures for balance sheet
4. Return null for any metric you cannot find - do not estimate or guess
5. If EBITDA is not directly disclosed, calculate it as: operating_income + depreciation_amortization
"""

FINANCIAL_EXTRACTION_PROMPT = """
Extract quarterly financial data from this SEC filing.

COMPANY: {company_name} ({ticker})
FILING TYPE: {filing_type}
PERIOD END: {period_end}

FILING CONTENT:
{filing_content}

Extract and return this JSON structure:

{{
  "fiscal_year": {fiscal_year},
  "fiscal_quarter": {fiscal_quarter},
  "period_end_date": "{period_end}",

  // INCOME STATEMENT (quarterly figures - use EXACT numbers from filing)
  "revenue": null,                    // Net sales, revenues, or total revenues
  "cost_of_revenue": null,            // Cost of sales, cost of goods sold
  "gross_profit": null,               // Revenue minus cost of revenue
  "operating_income": null,           // Operating income/loss (EBIT)
  "interest_expense": null,           // Interest expense (positive number)
  "income_tax_expense": null,         // Income tax expense/benefit (positive = expense)
  "net_income": null,                 // Net income/loss attributable to company
  "depreciation_amortization": null,  // D&A (from cash flow statement - see tips below)
  "ebitda": null,                     // ONLY if explicitly disclosed - otherwise leave null

  // BALANCE SHEET (as of period end - use EXACT numbers from filing)
  "cash_and_equivalents": null,       // Cash and cash equivalents
  "total_current_assets": null,       // Total current assets
  "total_assets": null,               // Total assets
  "total_current_liabilities": null,  // Total current liabilities
  "total_debt": null,                 // Total debt (short + long term)
  "total_liabilities": null,          // Total liabilities
  "stockholders_equity": null,        // Total stockholders' equity

  // CASH FLOW (quarterly figures - use EXACT numbers from filing)
  "operating_cash_flow": null,        // Net cash from operating activities
  "investing_cash_flow": null,        // Net cash from investing activities (usually negative)
  "financing_cash_flow": null,        // Net cash from financing activities
  "capex": null,                      // Capital expenditures (positive number)

  "uncertainties": []                 // List any metrics that couldn't be found
}}

EXTRACTION TIPS:
- Revenue: Look for "Net sales", "Revenues", "Total net revenues"
- Interest expense: Usually below operating income, may be "Interest expense, net"
- Total debt: May need to sum "Current portion of long-term debt" + "Long-term debt"

CRITICAL - DEPRECIATION & AMORTIZATION (D&A):
D&A is essential for computing EBITDA. It is typically found in the CASH FLOW STATEMENT:
- Look in "Cash flows from operating activities" section
- Usually in the first 5-10 lines after "Net income" as non-cash adjustments
- Common labels include:
  * "Depreciation and amortization"
  * "Depreciation"
  * "Amortization of intangibles"
  * "Depreciation and impairment"
  * "Depreciation, amortization and impairment"
  * "Depreciation of property, plant and equipment"
- IMPORTANT: May be split into MULTIPLE LINES - you must SUM ALL depreciation and amortization items
  * Example: "Depreciation of equipment 3,675" + "Depreciation of property 6,412" = 10,087 total
- Add together ALL lines containing "depreciation" or "amortization" (excluding amortization of debt costs)

EBITDA CALCULATION:
- If EBITDA is explicitly disclosed (rare), use that value
- Otherwise, DO NOT calculate EBITDA - leave it null, we will compute it as: operating_income + depreciation_amortization

IMPORTANT:
- Extract the EXACT numbers as shown in the filing tables
- DO NOT convert or multiply the numbers - just extract them as presented
- The filing header will say "in millions" or "in thousands" - we handle conversion separately
- For 10-Q filings, extract QUARTERLY data (3 months)
- For 10-K filings, you may need to calculate Q4 = Full Year - 9 Month YTD
- Return null (not 0) for missing data
"""

# Bank/Financial Institution specific prompt
BANK_FINANCIAL_EXTRACTION_PROMPT = """
Extract quarterly financial data from this SEC filing for a BANK/FINANCIAL INSTITUTION.

COMPANY: {company_name} ({ticker})
FILING TYPE: {filing_type}
PERIOD END: {period_end}

FILING CONTENT:
{filing_content}

IMPORTANT: This is a bank or financial institution. Banks have different income structure:
- NO traditional "revenue" or "cost of revenue"
- Instead, Net Interest Income = Interest Income - Interest Expense
- Non-Interest Income = Fees, commissions, trading gains, etc.
- Provision for Credit Losses = Expense for expected loan defaults
- Non-Interest Expense = Operating costs (salaries, occupancy, technology, etc.)

Extract and return this JSON structure:

{{
  "fiscal_year": {fiscal_year},
  "fiscal_quarter": {fiscal_quarter},
  "period_end_date": "{period_end}",

  // BANK INCOME STATEMENT (quarterly figures - use EXACT numbers from filing)
  "net_interest_income": null,           // Net interest income (CRITICAL for banks)
  "non_interest_income": null,           // Non-interest income (fees, trading, etc.)
  "non_interest_expense": null,          // Non-interest expense (salaries, occupancy, etc.)
  "provision_for_credit_losses": null,   // Provision for credit losses (positive = expense)
  "operating_income": null,              // Income before income taxes / Pre-tax income
  "interest_expense": null,              // Interest expense (from income statement, not NII calc)
  "income_tax_expense": null,            // Income tax expense
  "net_income": null,                    // Net income attributable to company

  // For banks, set these to null (they don't apply)
  "revenue": null,
  "cost_of_revenue": null,
  "gross_profit": null,
  "depreciation_amortization": null,
  "ebitda": null,

  // BALANCE SHEET (as of period end - use EXACT numbers from filing)
  "cash_and_equivalents": null,          // Cash and cash equivalents
  "total_current_assets": null,          // Total current assets (if disclosed)
  "total_assets": null,                  // Total assets
  "total_current_liabilities": null,     // Total current liabilities (if disclosed)
  "total_debt": null,                    // Long-term debt (NOT deposits)
  "total_liabilities": null,             // Total liabilities
  "stockholders_equity": null,           // Total stockholders' equity

  // CASH FLOW (quarterly figures - use EXACT numbers from filing)
  "operating_cash_flow": null,           // Net cash from operating activities
  "investing_cash_flow": null,           // Net cash from investing activities
  "financing_cash_flow": null,           // Net cash from financing activities
  "capex": null,                         // Capital expenditures (positive number)

  "uncertainties": []                    // List any metrics that couldn't be found
}}

BANK-SPECIFIC EXTRACTION TIPS:
- Net Interest Income: Usually the FIRST line in the income statement for banks
  - Look for "Net interest income" or "Net interest income after provision"
  - This is calculated as Interest Income - Interest Expense
- Non-Interest Income: Second major revenue source
  - Includes: service charges, trading revenue, asset management fees, etc.
  - Look for "Total non-interest income" or "Non-interest revenue"
- Non-Interest Expense: Operating costs for the bank
  - Look for "Total non-interest expense" or "Total noninterest expense"
  - Includes: salaries, employee benefits, occupancy, equipment, technology
- Provision for Credit Losses: NOT interest expense - this is loan loss reserves
  - Often shown right after Net Interest Income
- Total Debt for banks: Focus on "Long-term debt" or "Senior notes"
  - Do NOT include customer deposits or wholesale funding

IMPORTANT:
- Extract the EXACT numbers as shown in the filing tables
- DO NOT convert or multiply the numbers - just extract them as presented
- The filing header will say "in millions" or "in thousands" - we handle conversion separately
- Return null (not 0) for missing data
"""

# Keywords to find financial sections in bank filings
BANK_FINANCIAL_SECTIONS_KEYWORDS = [
    "consolidated statements of income",
    "consolidated balance sheets",
    "consolidated statements of cash flows",
    "net interest income",
    "non-interest income",
    "noninterest income",
    "provision for credit losses",
    "allowance for credit losses",
    "total interest income",
    "total interest expense",
    "total noninterest expense",
    "total deposits",
    "loans and leases",
    "total assets",
    "stockholders' equity",
]

FINANCIAL_SECTIONS_KEYWORDS = [
    "consolidated statements of operations",
    "consolidated balance sheets",
    "consolidated statements of cash flows",
    "cash flows from operating activities",  # Where D&A is found
    "statements of income",
    "financial statements",
    "net revenues",
    "total revenues",
    "cost of sales",
    "operating income",
    "interest expense",
    "net income",
    "total assets",
    "total liabilities",
    "depreciation and amortization",  # D&A line item
    "stockholders' equity",
    "cash and cash equivalents",
    "depreciation and amortization",
    "capital expenditures",
]


def detect_filing_scale(content: str) -> int:
    """
    Detect the scale used in SEC filing financial statements.

    SEC filings explicitly state scale in headers like:
    - "in millions" or "(in millions)"
    - "in thousands" or "(in thousands)"
    - "in billions" (rare)
    - "dollars in millions"

    Returns multiplier to convert stated values to cents:
    - 1 if already in dollars (no scale stated)
    - 100 for dollars to cents
    - 100_000 for thousands to cents ($1,000 = 100,000 cents)
    - 100_000_000 for millions to cents ($1M = 100M cents)
    - 100_000_000_000 for billions to cents ($1B = 100B cents)
    """
    content_lower = content.lower()

    # Key data points that appear in actual financial statements (not TOC)
    # We search backwards from these to find the scale indicator
    data_markers = [
        "total assets",
        "total liabilities",
        "total revenues",
        "net revenues",
        "total net revenues",
        "net income",
        "net sales",
    ]

    # Scale patterns with their multipliers
    # IMPORTANT: Exclude patterns that relate to shares, not dollars
    thousands_patterns = [
        r'dollars\s+in\s+thousands',
        r'\(\s*in\s+thousands\s*,?\s*except',  # "(in thousands, except per share)"
        r'\(\s*\$?\s*in\s+thousands\s*\)',
        r'amounts\s+in\s+thousands',
        r'in\s+thousands\s+of\s+u\.?s\.?\s+dollars',  # "In thousands of U.S. dollars"
        r'\(in\s+thousands\s+of\s+u',  # Partial match for "(In thousands of U.S. dollars"
        r'\(\s*\$\s*000\s*,?\s*except',  # "($000, except per share)" - common notation
        r'\$\s*000\s*,?\s*except',  # "$000, except per share"
        r'\(\s*\$\s*and\s+shares\s+in\s+000',  # "($ and shares in 000"
        r'in\s+000\s*,?\s*except',  # "in 000, except per share"
    ]

    # Patterns that indicate thousands but might be for shares (exclude these)
    thousands_share_patterns = [
        r'shares?\s+in\s+thousands',
        r'in\s+thousands\s+of\s+shares',
        r'thousands\s+of\s+shares',
    ]

    millions_patterns = [
        r'dollars\s+in\s+millions',
        r'\(\s*in\s+millions\s*,?\s*except',  # "(in millions, except per share)"
        r'\(\s*\$?\s*in\s+millions\s*\)',
        r'\bin\s+millions\b(?!\s+of\s+shares)',
        r'\(\s*millions\s*\)',
        r'amounts\s+in\s+millions',
        r'in\s+millions\s+of\s+u\.?s\.?\s+dollars',  # "In millions of U.S. dollars"
        r'millions\s+(?:september|december|march|june|january|february|april|may|july|august|october|november)',  # "millions September 30" (OXY format)
        r'millions,?\s+except\s+per[- ]share',  # "millions, except per-share amounts"
        r'\(\s*millions\s+of\s+dollars',  # "(Millions of dollars" (CVX format)
        r'millions\s+of\s+dollars',  # "Millions of dollars"
    ]

    # PRIORITY 1: Search near financial statement headers first (most reliable)
    # These are explicit statements in the financial statement headers
    # NOTE: We search ALL occurrences of headers, not just the first, because
    # the first might be in a Table of Contents without scale info
    financial_headers = [
        "consolidated balance sheet",
        "condensed consolidated balance sheet",
        "consolidated statements of operations",
        "condensed consolidated statements of operations",
        "consolidated statements of income",
        "condensed consolidated statements of earnings",
        "consolidated financial statements",
        "condensed consolidated financial statements",
    ]

    for header in financial_headers:
        # Find ALL occurrences of this header
        idx = 0
        while idx < len(content_lower):
            header_pos = content_lower.find(header, idx)
            if header_pos == -1:
                break

            # Search 500 chars after header (scale is usually right after the title)
            search_region = content_lower[header_pos:header_pos + 500]

            # Check thousands first (specific to this header)
            for pattern in thousands_patterns:
                if re.search(pattern, search_region):
                    return 100_000

            # Then millions (specific to this header)
            for pattern in millions_patterns:
                if re.search(pattern, search_region):
                    return 100_000_000

            idx = header_pos + len(header)

    # PRIORITY 2: Find data markers and search backwards for scale indicator
    # The scale indicator closest to actual data wins
    best_scale = None
    best_distance = float('inf')

    for marker in data_markers:
        marker_pos = content_lower.find(marker)
        if marker_pos == -1:
            continue

        # Search in the 3000 chars BEFORE the data marker (scale usually in header above)
        lookback_start = max(0, marker_pos - 3000)
        lookback_region = content_lower[lookback_start:marker_pos]

        # Check for thousands (search from end of region to find closest)
        # But skip if it's related to shares
        for pattern in thousands_patterns:
            matches = list(re.finditer(pattern, lookback_region))
            if matches:
                # Last match is closest to data
                last_match = matches[-1]
                # Check if this is a share-related pattern (skip if so)
                match_context = lookback_region[max(0, last_match.start()-20):last_match.end()+30]
                is_share_related = any(re.search(sp, match_context) for sp in thousands_share_patterns)
                if is_share_related:
                    continue

                distance = marker_pos - (lookback_start + last_match.end())
                if distance < best_distance:
                    best_distance = distance
                    best_scale = 100_000  # thousands to cents

        # Check for millions
        for pattern in millions_patterns:
            matches = list(re.finditer(pattern, lookback_region))
            if matches:
                last_match = matches[-1]
                distance = marker_pos - (lookback_start + last_match.end())
                if distance < best_distance:
                    best_distance = distance
                    best_scale = 100_000_000  # millions to cents

    if best_scale is not None:
        return best_scale

    # Last resort fallback: search entire document, prefer millions
    for pattern in millions_patterns:
        if re.search(pattern, content_lower):
            return 100_000_000

    for pattern in thousands_patterns:
        if re.search(pattern, content_lower):
            return 100_000

    # Default: assume values are in dollars if no scale indicator found
    # This should be rare - most SEC filings have explicit scale indicators
    # If this happens, the extraction should be reviewed
    print("  WARNING: No scale indicator found in filing - assuming dollars")
    return 100


# =============================================================================
# PYDANTIC MODELS
# =============================================================================

def _coerce_to_int(v):
    """Coerce float/string to int, handling LLM output variations."""
    if v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(round(v))
    if isinstance(v, str):
        # Handle string numbers like "1209.3" or "-14.9"
        try:
            return int(round(float(v)))
        except ValueError:
            return None
    return None


class ExtractedFinancials(BaseModel):
    """Validated financial extraction result."""

    model_config = {"coerce_numbers_to_str": False}

    fiscal_year: int
    fiscal_quarter: int = Field(ge=1, le=4)
    period_end_date: str

    # Income Statement
    revenue: Optional[int] = None
    cost_of_revenue: Optional[int] = None
    gross_profit: Optional[int] = None
    operating_income: Optional[int] = None
    interest_expense: Optional[int] = None
    income_tax_expense: Optional[int] = None
    net_income: Optional[int] = None
    depreciation_amortization: Optional[int] = None
    ebitda: Optional[int] = None

    # Balance Sheet
    cash_and_equivalents: Optional[int] = None
    total_current_assets: Optional[int] = None
    total_assets: Optional[int] = None
    total_current_liabilities: Optional[int] = None
    total_debt: Optional[int] = None
    total_liabilities: Optional[int] = None
    stockholders_equity: Optional[int] = None

    # Cash Flow
    operating_cash_flow: Optional[int] = None
    investing_cash_flow: Optional[int] = None
    financing_cash_flow: Optional[int] = None
    capex: Optional[int] = None

    # Bank/Financial Institution specific
    net_interest_income: Optional[int] = None
    non_interest_income: Optional[int] = None
    non_interest_expense: Optional[int] = None  # For PPNR calculation
    provision_for_credit_losses: Optional[int] = None

    # Metadata
    ebitda_type: Optional[str] = None  # "ebitda" or "ppnr" (for banks)

    uncertainties: list[str] = Field(default_factory=list)

    # Source metadata (set by extraction, not LLM)
    source_filing_url: Optional[str] = None

    @field_validator(
        "revenue", "cost_of_revenue", "gross_profit", "operating_income",
        "interest_expense", "income_tax_expense", "net_income",
        "depreciation_amortization", "ebitda", "cash_and_equivalents",
        "total_current_assets", "total_assets", "total_current_liabilities",
        "total_debt", "total_liabilities", "stockholders_equity",
        "operating_cash_flow", "investing_cash_flow", "financing_cash_flow", "capex",
        "net_interest_income", "non_interest_income", "non_interest_expense", "provision_for_credit_losses",
        mode="before"
    )
    @classmethod
    def coerce_numbers(cls, v):
        return _coerce_to_int(v)

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

def extract_financial_sections(
    filing_content: str,
    max_chars: int = 150000,
    keywords: Optional[list[str]] = None,
) -> str:
    """Extract financial statement sections from a large filing."""
    if len(filing_content) <= max_chars:
        return filing_content

    # Use provided keywords or default
    section_keywords = keywords if keywords is not None else FINANCIAL_SECTIONS_KEYWORDS

    # Find sections containing financial data
    content_lower = filing_content.lower()
    sections = []

    for keyword in section_keywords:
        # Find all occurrences of the keyword
        start = 0
        while True:
            idx = content_lower.find(keyword, start)
            if idx == -1:
                break

            # Extract context around the keyword (20K chars)
            section_start = max(0, idx - 1000)
            section_end = min(len(filing_content), idx + 20000)
            section = filing_content[section_start:section_end]

            if section not in sections:
                sections.append(section)

            start = idx + len(keyword)

            # Limit sections to avoid context overflow
            if sum(len(s) for s in sections) > max_chars:
                break

        if sum(len(s) for s in sections) > max_chars:
            break

    if not sections:
        # Fallback: return first chunk
        return filing_content[:max_chars]

    combined = "\n\n---\n\n".join(sections)
    return combined[:max_chars]


def determine_fiscal_period(period_end_date: str, filing_type: str) -> tuple[int, int]:
    """
    Determine fiscal year and quarter from the period end date.

    Args:
        period_end_date: The period end date (not filing date) in YYYY-MM-DD format
        filing_type: "10-K" or "10-Q"

    Note: Companies file 10-Qs about 40 days after quarter end:
    - Q1 (Jan-Mar) filed in Apr/May
    - Q2 (Apr-Jun) filed in Jul/Aug
    - Q3 (Jul-Sep) filed in Oct/Nov
    - Q4 (Oct-Dec) covered in 10-K filed in Jan/Feb/Mar
    """
    try:
        d = datetime.strptime(period_end_date, "%Y-%m-%d")
    except ValueError:
        # Default to current year Q4
        today = date.today()
        return today.year, 4

    year = d.year
    month = d.month

    if filing_type == "10-K":
        # 10-K is annual report, Q4
        quarter = 4
    else:
        # 10-Q - determine quarter from period end month
        # Q1: Jan-Mar (period ends Mar)
        # Q2: Apr-Jun (period ends Jun)
        # Q3: Jul-Sep (period ends Sep)
        if month <= 3:
            quarter = 1
        elif month <= 6:
            quarter = 2
        elif month <= 9:
            quarter = 3
        else:
            # Oct-Dec would be Q4, but that's usually in 10-K
            quarter = 4

    return year, quarter


async def extract_financials(
    ticker: str,
    cik: Optional[str] = None,
    filing_type: str = "10-Q",
    use_claude: bool = False,
    filing_data: Optional[dict] = None,  # Optional: pass specific filing metadata
    is_financial_institution: bool = False,  # Banks, insurance, asset managers
) -> Optional[ExtractedFinancials]:
    """
    Extract financial data from a 10-Q or 10-K filing.

    Args:
        ticker: Stock ticker symbol
        cik: SEC Central Index Key (optional)
        filing_type: "10-Q" or "10-K"
        use_claude: Use Claude instead of Gemini for extraction
        filing_data: Optional filing metadata dict (if not provided, fetches latest)
        is_financial_institution: If True, use bank-specific extraction prompt

    Returns:
        ExtractedFinancials object or None if extraction failed
    """
    sec_api_key = os.getenv("SEC_API_KEY")
    if not sec_api_key:
        print("SEC_API_KEY not set")
        return None

    sec_client = SecApiClient(api_key=sec_api_key)

    # Use provided filing or fetch the latest
    if filing_data:
        filing = filing_data
    else:
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
    filing_date = filing.get("filedAt", "")[:10]  # When filed with SEC
    period_end = filing.get("periodOfReport", filing_date)  # Period covered by filing

    # Download filing content
    content = sec_client.get_filing_content(filing_url)
    if not content:
        print(f"Failed to download filing content for {ticker}")
        return None

    # Clean HTML if needed
    if content.strip().startswith("<") or content.strip().startswith("<?xml"):
        content = clean_filing_html(content)

    # Detect scale from filing BEFORE truncating content
    filing_scale = detect_filing_scale(content)
    scale_name = {
        100: "dollars",
        100_000: "thousands",
        100_000_000: "millions",
        100_000_000_000: "billions",
    }.get(filing_scale, "unknown")
    print(f"  Detected filing scale: {scale_name} (multiplier: {filing_scale:,})")

    # Extract financial sections (use bank-specific keywords for financial institutions)
    if is_financial_institution:
        content = extract_financial_sections(content, keywords=BANK_FINANCIAL_SECTIONS_KEYWORDS)
        print(f"  Using bank-specific extraction for {ticker}")
    else:
        content = extract_financial_sections(content)

    # Determine fiscal period from the period end date (not filing date)
    fiscal_year, fiscal_quarter = determine_fiscal_period(period_end, filing_type)

    # Get company name (try to extract from filing or use ticker)
    company_name = ticker  # Fallback

    # Build prompt - use bank-specific prompt for financial institutions
    prompt_template = BANK_FINANCIAL_EXTRACTION_PROMPT if is_financial_institution else FINANCIAL_EXTRACTION_PROMPT
    prompt = prompt_template.format(
        company_name=company_name,
        ticker=ticker,
        filing_type=filing_type,
        period_end=period_end,
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

        # Handle case where Gemini returns an array with single object
        if isinstance(data, list) and len(data) > 0:
            data = data[0]

        if data:
            # DEBUG: Show raw extracted values before scaling
            if os.getenv("DEBUG_FINANCIALS"):
                print(f"  DEBUG Raw extraction (before scaling):")
                for k in ["revenue", "operating_income", "net_income", "total_debt"]:
                    print(f"    {k}: {data.get(k)}")

            # Apply the detected scale from the filing
            data = apply_filing_scale(data, filing_scale)

            # Calculate EBITDA if not provided
            if data.get("ebitda") is None:
                operating = data.get("operating_income")
                da = data.get("depreciation_amortization")
                if operating is not None and da is not None:
                    data["ebitda"] = operating + da
                elif operating is not None:
                    # Fallback: use operating income as EBITDA proxy when D&A unavailable
                    # This is a lower bound estimate (EBITDA >= Operating Income)
                    # Track this in uncertainties so downstream can assess data quality
                    data["ebitda"] = operating
                    uncertainties = data.get("uncertainties", [])
                    uncertainties.append("EBITDA estimated from operating_income (D&A not found)")
                    data["uncertainties"] = uncertainties

            # Track extraction completeness for data quality assessment
            uncertainties = data.get("uncertainties", [])
            critical_fields = {
                "revenue": "Revenue not extracted",
                "operating_income": "Operating income not extracted",
                "total_debt": "Total debt not extracted",
                "depreciation_amortization": "D&A not extracted (EBITDA may be understated)",
            }
            for field, warning in critical_fields.items():
                if data.get(field) is None and warning not in uncertainties:
                    uncertainties.append(warning)
            data["uncertainties"] = uncertainties

            # Include source filing URL for provenance tracking
            data["source_filing_url"] = filing_url

            return ExtractedFinancials(**data)
    except Exception as e:
        print(f"Failed to parse financial extraction: {e}")
        print(f"Response: {response_text[:500]}...")

    return None


async def extract_ttm_financials(
    ticker: str,
    cik: Optional[str] = None,
    use_claude: bool = False,
    is_financial_institution: bool = False,
) -> list[ExtractedFinancials]:
    """
    Extract trailing twelve months (4 quarters) of financial data.

    Fetches the most recent 10-K and three 10-Qs to get full TTM coverage.
    For Q4, extracts from 10-K by subtracting 9-month 10-Q from full year.

    Args:
        ticker: Stock ticker symbol
        cik: SEC Central Index Key (optional)
        use_claude: Use Claude instead of Gemini for extraction
        is_financial_institution: If True, use bank-specific extraction prompt

    Returns:
        List of ExtractedFinancials for up to 4 quarters, most recent first
    """
    import time

    sec_api_key = os.getenv("SEC_API_KEY")
    if not sec_api_key:
        print("SEC_API_KEY not set")
        return []

    sec_client = SecApiClient(api_key=sec_api_key)
    results = []

    # Fetch recent 10-Qs (up to 3 for Q1, Q2, Q3)
    print(f"\n--- Fetching 10-Q filings for {ticker} ---")
    filings_10q = sec_client.get_filings_by_ticker(
        ticker,
        form_types=["10-Q"],
        max_filings=3,
        cik=cik,
    )

    # Fetch most recent 10-K (for Q4)
    print(f"--- Fetching 10-K filings for {ticker} ---")
    filings_10k = sec_client.get_filings_by_ticker(
        ticker,
        form_types=["10-K"],
        max_filings=1,
        cik=cik,
    )

    all_filings = []
    for f in filings_10q:
        all_filings.append(("10-Q", f))
    for f in filings_10k:
        all_filings.append(("10-K", f))

    print(f"Found {len(filings_10q)} 10-Qs and {len(filings_10k)} 10-Ks")

    for filing_type, filing in all_filings:
        filing_date = filing.get("filedAt", "")[:10]
        period_end = filing.get("periodOfReport", filing_date)
        print(f"\n--- Extracting {filing_type} filed {filing_date} (period: {period_end}) ---")

        result = await extract_financials(
            ticker=ticker,
            cik=cik,
            filing_type=filing_type,
            use_claude=use_claude,
            filing_data=filing,  # Pass the specific filing
            is_financial_institution=is_financial_institution,
        )

        if result:
            results.append(result)
            print(f"  Extracted: Q{result.fiscal_quarter} {result.fiscal_year}")
        else:
            print(f"  Failed to extract")

        # Rate limiting between API calls
        time.sleep(2)

    # Sort by year/quarter descending (most recent first)
    results.sort(key=lambda x: (x.fiscal_year, x.fiscal_quarter), reverse=True)

    return results


def apply_filing_scale(data: dict, scale_multiplier: int) -> dict:
    """
    Apply the detected filing scale to convert raw extracted values to cents.

    Args:
        data: Dictionary of extracted financial data (raw values from filing)
        scale_multiplier: Multiplier detected from filing (e.g., 100_000_000 for "in millions")

    Returns:
        Data with all monetary values converted to cents
    """
    monetary_fields = [
        "revenue", "cost_of_revenue", "gross_profit", "operating_income",
        "interest_expense", "net_income", "depreciation_amortization", "ebitda",
        "cash_and_equivalents", "total_current_assets", "total_assets",
        "total_current_liabilities", "total_debt", "total_liabilities",
        "stockholders_equity", "operating_cash_flow", "investing_cash_flow",
        "financing_cash_flow", "capex",
        # Bank/Financial institution fields
        "net_interest_income", "non_interest_income", "non_interest_expense", "provision_for_credit_losses",
    ]

    for field in monetary_fields:
        value = data.get(field)
        if value is None:
            continue

        # Convert string values to numbers first
        if isinstance(value, str):
            try:
                # Remove commas and convert
                value = int(value.replace(",", ""))
            except (ValueError, TypeError):
                data[field] = None
                continue

        # Apply the scale multiplier to convert to cents
        data[field] = int(value * scale_multiplier)

    return data


async def save_financials_to_db(
    session: AsyncSession,
    ticker: str,
    financials: ExtractedFinancials,
    source_filing: Optional[str] = None,
) -> Optional[CompanyFinancials]:
    """Save extracted financials to database."""
    # Find company
    result = await session.execute(
        select(Company).where(Company.ticker == ticker)
    )
    company = result.scalar_one_or_none()

    if not company:
        print(f"Company not found: {ticker}")
        return None

    # Use source_filing from parameter, or fall back to URL embedded in financials
    filing_url = source_filing or financials.source_filing_url

    # Check if record already exists
    result = await session.execute(
        select(CompanyFinancials).where(
            CompanyFinancials.company_id == company.id,
            CompanyFinancials.fiscal_year == financials.fiscal_year,
            CompanyFinancials.fiscal_quarter == financials.fiscal_quarter,
        )
    )
    existing = result.scalar_one_or_none()

    # Calculate PPNR for banks if we have the required fields
    # PPNR = Net Interest Income + Non-Interest Income - Non-Interest Expense
    ppnr = None
    ebitda_type = financials.ebitda_type or "ebitda"  # Default to traditional EBITDA

    if company.is_financial_institution:
        nii = financials.net_interest_income
        noi = financials.non_interest_income
        nie = financials.non_interest_expense
        if nii is not None and noi is not None and nie is not None:
            ppnr = nii + noi - nie
            ebitda_type = "ppnr"
        elif nii is not None:
            # Fallback: use NII as rough PPNR proxy if we don't have all components
            ppnr = nii
            ebitda_type = "ppnr"

    if existing:
        # Update existing record
        for field in [
            "revenue", "cost_of_revenue", "gross_profit", "operating_income",
            "interest_expense", "net_income", "depreciation_amortization",
            "cash_and_equivalents", "total_current_assets", "total_assets",
            "total_current_liabilities", "total_debt", "total_liabilities",
            "stockholders_equity", "operating_cash_flow", "investing_cash_flow",
            "financing_cash_flow", "capex",
            # Bank/Financial institution fields
            "net_interest_income", "non_interest_income", "non_interest_expense", "provision_for_credit_losses",
        ]:
            value = getattr(financials, field)
            if value is not None:
                setattr(existing, field, value)

        # Set EBITDA/PPNR based on company type
        if company.is_financial_institution and ppnr is not None:
            existing.ebitda = ppnr
            existing.ebitda_type = "ppnr"
        elif financials.ebitda is not None:
            existing.ebitda = financials.ebitda
            existing.ebitda_type = "ebitda"

        existing.source_filing = filing_url
        existing.extracted_at = datetime.utcnow()
        await session.commit()
        return existing

    # Create new record
    # For banks, use PPNR as ebitda; for others, use traditional ebitda
    final_ebitda = ppnr if (company.is_financial_institution and ppnr is not None) else financials.ebitda
    final_ebitda_type = "ppnr" if (company.is_financial_institution and ppnr is not None) else "ebitda"

    record = CompanyFinancials(
        company_id=company.id,
        fiscal_year=financials.fiscal_year,
        fiscal_quarter=financials.fiscal_quarter,
        period_end_date=datetime.strptime(financials.period_end_date, "%Y-%m-%d").date(),
        filing_type="10-Q" if financials.fiscal_quarter < 4 else "10-K",
        revenue=financials.revenue,
        cost_of_revenue=financials.cost_of_revenue,
        gross_profit=financials.gross_profit,
        operating_income=financials.operating_income,
        interest_expense=financials.interest_expense,
        net_income=financials.net_income,
        depreciation_amortization=financials.depreciation_amortization,
        ebitda=final_ebitda,
        ebitda_type=final_ebitda_type,
        cash_and_equivalents=financials.cash_and_equivalents,
        total_current_assets=financials.total_current_assets,
        total_assets=financials.total_assets,
        total_current_liabilities=financials.total_current_liabilities,
        total_debt=financials.total_debt,
        total_liabilities=financials.total_liabilities,
        stockholders_equity=financials.stockholders_equity,
        operating_cash_flow=financials.operating_cash_flow,
        investing_cash_flow=financials.investing_cash_flow,
        financing_cash_flow=financials.financing_cash_flow,
        capex=financials.capex,
        source_filing=filing_url,
        # Bank/Financial institution fields
        net_interest_income=financials.net_interest_income,
        non_interest_income=financials.non_interest_income,
        non_interest_expense=financials.non_interest_expense,
        provision_for_credit_losses=financials.provision_for_credit_losses,
    )
    session.add(record)
    await session.commit()

    return record


async def get_latest_financials(
    session: AsyncSession,
    company_id: UUID,
) -> Optional[CompanyFinancials]:
    """Get the most recent financial data for a company."""
    result = await session.execute(
        select(CompanyFinancials)
        .where(CompanyFinancials.company_id == company_id)
        .order_by(
            CompanyFinancials.fiscal_year.desc(),
            CompanyFinancials.fiscal_quarter.desc(),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_trailing_financials(
    session: AsyncSession,
    company_id: UUID,
    quarters: int = 4,
) -> list[CompanyFinancials]:
    """Get trailing N quarters of financial data."""
    result = await session.execute(
        select(CompanyFinancials)
        .where(CompanyFinancials.company_id == company_id)
        .order_by(
            CompanyFinancials.fiscal_year.desc(),
            CompanyFinancials.fiscal_quarter.desc(),
        )
        .limit(quarters)
    )
    return list(result.scalars().all())


def calculate_ttm_metrics(financials: list[CompanyFinancials]) -> dict:
    """
    Calculate trailing twelve month (TTM) metrics from quarterly data.

    Returns dict with TTM values for income statement items.
    """
    if len(financials) < 4:
        return {}

    ttm = {
        "revenue": 0,
        "operating_income": 0,
        "ebitda": 0,
        "interest_expense": 0,
        "net_income": 0,
    }

    for q in financials[:4]:
        if q.revenue:
            ttm["revenue"] += q.revenue
        if q.operating_income:
            ttm["operating_income"] += q.operating_income
        if q.ebitda:
            ttm["ebitda"] += q.ebitda
        if q.interest_expense:
            ttm["interest_expense"] += q.interest_expense
        if q.net_income:
            ttm["net_income"] += q.net_income

    return ttm


def calculate_credit_ratios(
    total_debt: int,
    cash: int,
    ebitda: int,
    interest_expense: int,
    operating_income: int,
) -> dict:
    """
    Calculate key credit ratios.

    Args:
        total_debt: Total debt in cents
        cash: Cash and equivalents in cents
        ebitda: EBITDA in cents (should be TTM)
        interest_expense: Interest expense in cents (should be TTM)
        operating_income: Operating income in cents (should be TTM)

    Returns:
        Dict with calculated ratios
    """
    ratios = {}

    if ebitda and ebitda > 0:
        ratios["leverage_ratio"] = round(total_debt / ebitda, 2)
        ratios["net_leverage_ratio"] = round((total_debt - cash) / ebitda, 2)

    if interest_expense and interest_expense > 0:
        if ebitda:
            ratios["interest_coverage_ebitda"] = round(ebitda / interest_expense, 2)
        if operating_income:
            ratios["interest_coverage_ebit"] = round(operating_income / interest_expense, 2)

    return ratios


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse
    import sys

    from sqlalchemy import select, func
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker

    # Fix Windows encoding
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    # Add parent to path for imports
    sys.path.insert(0, str(__file__).replace('app/services/financial_extraction.py', ''))

    from app.core.config import get_settings

    async def main():
        parser = argparse.ArgumentParser(description="Extract financial data from SEC filings")
        parser.add_argument("--ticker", help="Company ticker")
        parser.add_argument("--all", action="store_true", help="Process all companies")
        parser.add_argument("--limit", type=int, help="Limit companies")
        parser.add_argument("--ttm", action="store_true", help="Extract TTM (4 quarters)")
        parser.add_argument("--save-db", action="store_true", help="Save to database")
        parser.add_argument("--claude", action="store_true", help="Use Claude instead of Gemini")
        args = parser.parse_args()

        if not args.ticker and not args.all:
            print("Usage: python -m app.services.financial_extraction --ticker CHTR --ttm --save-db")
            print("       python -m app.services.financial_extraction --all --limit 10 --ttm --save-db")
            return

        settings = get_settings()
        engine = create_async_engine(
            settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
        )
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with async_session() as db:
            if args.ticker:
                result = await db.execute(
                    select(Company).where(Company.ticker == args.ticker.upper())
                )
                companies = [result.scalar_one_or_none()]
            else:
                # Get companies without recent financials
                result = await db.execute(
                    select(Company)
                    .outerjoin(CompanyFinancials, CompanyFinancials.company_id == Company.id)
                    .group_by(Company.id)
                    .having(func.count(CompanyFinancials.id) < 4)
                    .order_by(Company.ticker)
                )
                companies = list(result.scalars())
                if args.limit:
                    companies = companies[:args.limit]

        print(f"Processing {len(companies)} companies")
        total_quarters = 0

        for company in companies:
            if not company:
                continue

            print(f"\n[{company.ticker}] {company.name}")
            cik = company.cik or ''

            if args.ttm:
                # Extract 4 quarters
                financials = await extract_ttm_financials(
                    company.ticker, cik, use_claude=args.claude
                )
                if financials:
                    print(f"  Extracted {len(financials)} quarters")
                    if args.save_db:
                        async with async_session() as db:
                            for fin in financials:
                                await save_financials_to_db(db, company.ticker, fin)
                            print(f"  Saved to database")
                    total_quarters += len(financials)
            else:
                # Extract latest quarter only
                fin = await extract_financials(
                    company.ticker, cik, use_claude=args.claude
                )
                if fin:
                    print(f"  Extracted Q{fin.fiscal_quarter} {fin.fiscal_year}")
                    if args.save_db:
                        async with async_session() as db:
                            await save_financials_to_db(db, company.ticker, fin)
                            print(f"  Saved to database")
                    total_quarters += 1

            await asyncio.sleep(1)

        print(f"\nTotal quarters extracted: {total_quarters}")
        await engine.dispose()

    asyncio.run(main())
