"""
SEC EDGAR filing extraction service.

Downloads multiple filings (10-K, 10-Q, 8-K) and extracts corporate structure + debt data using Claude.
"""

import hashlib
import json
import re
from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID, uuid4

import anthropic
import httpx
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Company, CompanyCache, CompanyMetrics, DebtInstrument, Entity, Guarantee, OwnershipLink


def clean_filing_html(content: str) -> str:
    """
    Clean HTML/XBRL content from SEC filings to extract readable text.
    Handles inline XBRL (iXBRL) format used in modern SEC filings.
    """
    if not content:
        return ""

    # Check if it's already clean text (not HTML)
    if not content.strip().startswith('<') and not content.strip().startswith('<?xml'):
        return content

    # Remove XML declaration and DOCTYPE
    content = re.sub(r'<\?xml[^>]*\?>', '', content)
    content = re.sub(r'<!DOCTYPE[^>]*>', '', content)

    # Remove script and style blocks
    content = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', content, flags=re.IGNORECASE)
    content = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', content, flags=re.IGNORECASE)

    # Remove XBRL hidden sections (often contain duplicate/metadata)
    content = re.sub(r'<ix:hidden[^>]*>[\s\S]*?</ix:hidden>', '', content, flags=re.IGNORECASE)

    # Extract text from XBRL elements (ix:nonNumeric, ix:nonFraction, etc.)
    # These contain the actual displayed values
    content = re.sub(r'<ix:[^>]*>([^<]*)</ix:[^>]*>', r'\1', content)

    # Remove all remaining HTML/XML tags but preserve content
    content = re.sub(r'<[^>]+>', ' ', content)

    # Decode common HTML entities
    content = content.replace('&nbsp;', ' ')
    content = content.replace('&amp;', '&')
    content = content.replace('&lt;', '<')
    content = content.replace('&gt;', '>')
    content = content.replace('&quot;', '"')
    content = content.replace('&#39;', "'")
    content = content.replace('&apos;', "'")
    content = content.replace('&#x2019;', "'")
    content = content.replace('&#x2014;', '-')
    content = content.replace('&#x2013;', '-')

    # Decode numeric HTML entities
    content = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), content)
    content = re.sub(r'&#x([0-9a-fA-F]+);', lambda m: chr(int(m.group(1), 16)), content)

    # Clean up whitespace
    content = re.sub(r'\s+', ' ', content)
    content = re.sub(r'\n\s*\n', '\n\n', content)

    return content.strip()


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
# SEC EDGAR CLIENT
# =============================================================================


class FilingInfo(BaseModel):
    """Information about a single SEC filing."""
    form_type: str
    filing_date: str
    accession_number: str
    primary_document: str
    description: str = ""


# =============================================================================
# SEC-API.IO CLIENT (Fast, no rate limits)
# =============================================================================


class SecApiClient:
    """
    Client for SEC-API.io - faster alternative to direct SEC EDGAR access.

    Get your free API key at: https://sec-api.io/
    Set SEC_API_KEY environment variable.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.query_api = None
        self.render_api = None
        self._init_apis()

    def _init_apis(self):
        """Initialize SEC-API clients."""
        try:
            from sec_api import QueryApi, RenderApi
            self.query_api = QueryApi(api_key=self.api_key)
            self.render_api = RenderApi(api_key=self.api_key)
        except ImportError:
            print("  [WARN] sec-api package not installed. Run: pip install sec-api")

    def get_filings_by_ticker(
        self,
        ticker: str,
        form_types: list[str] = None,
        max_filings: int = 15,
        cik: str = None
    ) -> list[dict]:
        """
        Get recent filings for a company by ticker or CIK.

        Returns list of filing metadata with URLs.
        Falls back to CIK search if ticker returns no results.
        """
        if not self.query_api:
            return []

        if form_types is None:
            form_types = ["10-K", "10-Q", "8-K"]

        # Build query for multiple form types
        form_query = " OR ".join([f'formType:"{ft}"' for ft in form_types])

        # First try by ticker
        query = {
            "query": {
                "query_string": {
                    "query": f'ticker:{ticker} AND ({form_query})'
                }
            },
            "from": "0",
            "size": str(max_filings),
            "sort": [{"filedAt": {"order": "desc"}}]
        }

        try:
            response = self.query_api.get_filings(query)
            filings = response.get("filings", [])

            # If no results by ticker and CIK provided, try by CIK
            if not filings and cik:
                cik_num = cik.lstrip("0")  # Remove leading zeros for query
                query["query"]["query_string"]["query"] = f'cik:{cik_num} AND ({form_query})'
                response = self.query_api.get_filings(query)
                filings = response.get("filings", [])

            return filings
        except Exception as e:
            print(f"  [FAIL] SEC-API query failed: {e}")
            return []

    def get_filing_content(self, filing_url: str) -> str:
        """Download filing content as text."""
        if not self.render_api:
            return ""

        try:
            # RenderApi converts SEC filing to clean text
            content = self.render_api.get_filing(filing_url)
            # Clean any remaining HTML/XBRL if render didn't fully convert
            if content and (content.strip().startswith('<') or content.strip().startswith('<?xml')):
                content = clean_filing_html(content)
            return content
        except Exception as e:
            print(f"  [FAIL] SEC-API render failed: {e}")
            return ""

    def get_exhibit_21(self, ticker: str) -> str:
        """
        Specifically fetch Exhibit 21 (subsidiaries list) from latest 10-K.
        This is the most important document for corporate structure.
        """
        if not self.query_api:
            return ""

        # Query for 10-K filings with Exhibit 21
        query = {
            "query": {
                "query_string": {
                    "query": f'ticker:{ticker} AND formType:"10-K" AND documentFormatFiles.type:"EX-21"'
                }
            },
            "from": "0",
            "size": "1",
            "sort": [{"filedAt": {"order": "desc"}}]
        }

        try:
            response = self.query_api.get_filings(query)
            filings = response.get("filings", [])

            if not filings:
                return ""

            # Find Exhibit 21 URL in the filing
            for doc in filings[0].get("documentFormatFiles", []):
                doc_type = doc.get("type", "").upper()
                if "21" in doc_type or "SUBSIDIARIES" in doc.get("description", "").upper():
                    exhibit_url = doc.get("documentUrl", "")
                    if exhibit_url:
                        return self.get_filing_content(exhibit_url)

            return ""
        except Exception as e:
            print(f"  [FAIL] SEC-API Exhibit 21 fetch failed: {e}")
            return ""

    async def get_all_relevant_filings(
        self,
        ticker: str,
        include_exhibits: bool = True,
        cik: str = None
    ) -> dict[str, str]:
        """
        Get all relevant filings for comprehensive extraction.

        Returns dict with filing content keyed by type and date.
        Ensures we always get at least one 10-K for comprehensive debt info.
        """
        filings_content = {}

        # First, get the most recent 10-K (critical for debt/structure)
        ten_k_filings = self.get_filings_by_ticker(
            ticker,
            form_types=["10-K"],
            max_filings=1,
            cik=cik
        )

        # Then get recent 10-Q and 8-K filings
        other_filings = self.get_filings_by_ticker(
            ticker,
            form_types=["10-Q", "8-K"],
            max_filings=10,
            cik=cik
        )

        # Combine: 10-K first, then others (deduplicate by accessionNo)
        seen = set()
        filings = []
        for f in ten_k_filings + other_filings:
            acc = f.get("accessionNo", f.get("filedAt"))
            if acc not in seen:
                seen.add(acc)
                filings.append(f)

        print(f"  Found {len(filings)} filings via SEC-API")

        for filing in filings:
            form_type = filing.get("formType", "")
            filed_at = filing.get("filedAt", "")[:10]  # Just the date part
            key = f"{form_type}_{filed_at}"

            # Get main filing content
            filing_url = filing.get("linkToFilingDetails", "")
            if filing_url:
                content = self.get_filing_content(filing_url)
                if content:
                    filings_content[key] = content
                    print(f"    [OK] Downloaded {key}")

            # For 10-K, also get Exhibit 21 (subsidiaries)
            if include_exhibits and form_type == "10-K":
                for doc in filing.get("documentFormatFiles", []):
                    doc_type = doc.get("type", "").upper()
                    if "21" in doc_type:
                        exhibit_url = doc.get("documentUrl", "")
                        if exhibit_url:
                            ex_content = self.get_filing_content(exhibit_url)
                            if ex_content:
                                ex_key = f"exhibit_21_{filed_at}"
                                filings_content[ex_key] = ex_content
                                print(f"      [OK] Downloaded Exhibit 21")
                    # Also get credit agreements (Exhibit 10)
                    elif "10" in doc_type and "CREDIT" in doc.get("description", "").upper():
                        exhibit_url = doc.get("documentUrl", "")
                        if exhibit_url:
                            ex_content = self.get_filing_content(exhibit_url)
                            if ex_content:
                                ex_key = f"exhibit_10_{filed_at}_{doc_type}"
                                filings_content[ex_key] = ex_content
                                print(f"      [OK] Downloaded {doc_type}")

        return filings_content


class SECEdgarClient:
    """Client for fetching filings from SEC EDGAR."""

    BASE_URL = "https://data.sec.gov"
    ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"
    USER_AGENT = "Credible.ai contact@credible.ai"

    def __init__(self):
        self.client = httpx.AsyncClient(
            headers={"User-Agent": self.USER_AGENT},
            timeout=60.0,
            follow_redirects=True,
        )

    async def close(self):
        await self.client.aclose()

    async def get_company_filings(self, cik: str) -> dict:
        """Get list of filings for a company by CIK."""
        cik_padded = cik.zfill(10)
        url = f"{self.BASE_URL}/submissions/CIK{cik_padded}.json"
        response = await self.client.get(url)
        response.raise_for_status()
        return response.json()

    async def get_recent_filings(
        self,
        cik: str,
        form_types: list[str] = None,
        max_filings: int = 20,
        lookback_days: int = 365
    ) -> list[FilingInfo]:
        """
        Get recent filings of specified types.

        Args:
            cik: Company CIK number
            form_types: List of form types to include (e.g., ["10-K", "10-Q", "8-K"])
            max_filings: Maximum number of filings to return
            lookback_days: Only include filings from the last N days
        """
        if form_types is None:
            form_types = ["10-K", "10-Q", "8-K"]

        filings_data = await self.get_company_filings(cik)
        recent = filings_data["filings"]["recent"]

        cutoff_date = datetime.now().date() - timedelta(days=lookback_days)

        filings = []
        for i, form in enumerate(recent["form"]):
            if form in form_types:
                filing_date = datetime.strptime(recent["filingDate"][i], "%Y-%m-%d").date()

                if filing_date >= cutoff_date:
                    filings.append(FilingInfo(
                        form_type=form,
                        filing_date=recent["filingDate"][i],
                        accession_number=recent["accessionNumber"][i],
                        primary_document=recent["primaryDocument"][i],
                        description=recent.get("primaryDocDescription", [""])[i] if "primaryDocDescription" in recent else "",
                    ))

                if len(filings) >= max_filings:
                    break

        return filings

    async def download_filing(self, cik: str, filing: FilingInfo) -> str:
        """Download a single filing's content."""
        accession_no_dashes = filing.accession_number.replace("-", "")
        doc_url = f"{self.ARCHIVES_URL}/{cik}/{accession_no_dashes}/{filing.primary_document}"

        response = await self.client.get(doc_url)
        response.raise_for_status()
        return response.text

    async def get_filing_exhibits(self, cik: str, filing: FilingInfo) -> list[dict]:
        """Get list of exhibits for a filing."""
        accession_no_dashes = filing.accession_number.replace("-", "")
        index_url = f"{self.ARCHIVES_URL}/{cik}/{accession_no_dashes}/index.json"

        try:
            response = await self.client.get(index_url)
            response.raise_for_status()
            data = response.json()

            exhibits = []
            for item in data.get("directory", {}).get("item", []):
                name = item.get("name", "")
                # Look for exhibits (credit agreements, subsidiary lists, etc.)
                if any(x in name.lower() for x in ["ex10", "ex21", "ex99", "exhibit"]):
                    exhibits.append({
                        "name": name,
                        "url": f"{self.ARCHIVES_URL}/{cik}/{accession_no_dashes}/{name}",
                        "type": item.get("type", ""),
                    })
            return exhibits
        except Exception:
            return []

    async def download_exhibit(self, url: str) -> str:
        """Download an exhibit by URL."""
        response = await self.client.get(url)
        response.raise_for_status()
        return response.text

    async def get_latest_10k(self, cik: str) -> tuple[str, str, str]:
        """
        Get the latest 10-K filing content.
        Returns: (filing_content, accession_number, filing_date)
        """
        filings = await self.get_recent_filings(cik, form_types=["10-K"], max_filings=1, lookback_days=400)

        if not filings:
            raise ValueError(f"No 10-K filing found for CIK {cik}")

        filing = filings[0]
        content = await self.download_filing(cik, filing)

        return content, filing.accession_number, filing.filing_date

    async def get_all_relevant_filings(
        self,
        cik: str,
        include_exhibits: bool = True
    ) -> dict[str, str]:
        """
        Get all relevant filings for comprehensive extraction.

        Returns dict with keys like:
            "10-K_2024-02-15": "filing content...",
            "10-Q_2024-05-10": "filing content...",
            "8-K_2024-06-01": "filing content...",
            "exhibit_10.1_credit_agreement": "exhibit content...",
        """
        filings_content = {}

        # Get recent filings
        filings = await self.get_recent_filings(
            cik,
            form_types=["10-K", "10-Q", "8-K"],
            max_filings=15,
            lookback_days=400
        )

        print(f"  Found {len(filings)} relevant filings")

        import asyncio
        for filing in filings:
            key = f"{filing.form_type}_{filing.filing_date}"
            try:
                # SEC rate limit: max 10 requests per second, so we wait 150ms between requests
                await asyncio.sleep(0.15)
                content = await self.download_filing(cik, filing)
                filings_content[key] = content
                print(f"    [OK] Downloaded {key}")

                # Get exhibits for 8-Ks (often contain credit agreements)
                if include_exhibits and filing.form_type == "8-K":
                    exhibits = await self.get_filing_exhibits(cik, filing)
                    for exhibit in exhibits[:3]:  # Limit exhibits per filing
                        try:
                            await asyncio.sleep(0.15)
                            ex_content = await self.download_exhibit(exhibit["url"])
                            ex_key = f"exhibit_{filing.filing_date}_{exhibit['name']}"
                            filings_content[ex_key] = ex_content
                            print(f"      [OK] Downloaded exhibit: {exhibit['name']}")
                        except Exception as e:
                            print(f"      [FAIL] Failed to download exhibit: {exhibit['name']}")

            except Exception as e:
                print(f"    [FAIL] Failed to download {key}: {e}")

        return filings_content


# Import timedelta
from datetime import timedelta


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
            filings = await self.sec_api.get_all_relevant_filings(ticker, include_exhibits=True)

        # Fall back to direct SEC EDGAR if SEC-API didn't work
        if not filings:
            print(f"\n  Fetching filings from SEC EDGAR (direct)...")
            filings = await self.edgar.get_all_relevant_filings(cik, include_exhibits=True)

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
            if owner.parent_name not in entity_name_to_id:
                # External owner (e.g., JV partner outside the company)
                continue

            parent_entity_id = entity_name_to_id[owner.parent_name]

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
    for ext_debt in extraction.debt_instruments:
        issuer_id = entity_name_to_id.get(ext_debt.issuer_name)
        if not issuer_id:
            # Issuer not found, skip this debt
            print(f"  Warning: Issuer '{ext_debt.issuer_name}' not found for debt '{ext_debt.name}'")
            continue

        debt_id = uuid4()
        debt = DebtInstrument(
            id=debt_id,
            company_id=company_id,
            issuer_id=issuer_id,
            name=ext_debt.name,
            slug=slugify(ext_debt.name),
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
            issue_date=parse_date(ext_debt.issue_date),
            maturity_date=parse_date(ext_debt.maturity_date),
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
    def build_entity_tree(entity: Entity) -> dict:
        children = [e for e in entities if e.parent_id == entity.id]

        # Get full debt instrument details for this entity
        entity_debt_instruments = [d for d in debt_instruments if d.issuer_id == entity.id]
        debt_details = []
        for d in entity_debt_instruments:
            guarantor_ids = guarantees_by_debt.get(d.id, [])
            guarantor_names = [entity_by_id[g].name for g in guarantor_ids if g in entity_by_id]

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
            "debt_at_entity": {
                "total": sum(d.outstanding or 0 for d in entity_debt_instruments),
                "instrument_count": len(entity_debt_instruments),
                "instruments": debt_details,
            },
            "children": [build_entity_tree(c) for c in children],
        }

    # Find root entities (no parent)
    root_entities = [e for e in entities if e.parent_id is None]
    structure_tree = [build_entity_tree(e) for e in root_entities]

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
        "meta": {
            "as_of_date": filing_date.isoformat() if filing_date else None,
            "confidence": "high",
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
        metrics.total_debt = total_debt
        metrics.secured_debt = secured_debt
        metrics.unsecured_debt = unsecured_debt
        metrics.entity_count = len(entities)
        metrics.guarantor_count = sum(1 for e in entities if e.is_guarantor)
        metrics.nearest_maturity = nearest_maturity
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
            total_debt=total_debt,
            secured_debt=secured_debt,
            unsecured_debt=unsecured_debt,
            entity_count=len(entities),
            guarantor_count=sum(1 for e in entities if e.is_guarantor),
            nearest_maturity=nearest_maturity,
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
