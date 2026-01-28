"""
Tiered Extraction Service
=========================

Cost-optimized extraction using tiered LLM models with escalation.

MODEL TIERS
-----------
- Tier 1 (Gemini Flash): $0.10/$0.40 per 1M tokens - bulk extraction
- Tier 1.5 (Gemini Pro): $1.25/$10.00 per 1M tokens - intermediate escalation
- Tier 2 (Claude Sonnet): $3/$15 per 1M tokens - validation/medium complexity
- Tier 3 (Claude Opus): $15/$75 per 1M tokens - complex structures/QA

Target: <$0.03 per company average, >90% accuracy

RELATIONSHIP TO OTHER MODULES
-----------------------------
- This module provides: LLM clients, prompts, validation, TieredExtractionService
- iterative_extraction.py wraps this with QA feedback loop
- llm_utils.py provides lower-level LLM utilities (generic)

TYPICAL USAGE
-------------
    # Direct usage (rare)
    service = TieredExtractionService(gemini_key, anthropic_key, sec_api_key)
    result = await service.extract(ticker, cik)

    # Via iterative extraction (recommended)
    from app.services.iterative_extraction import IterativeExtractionService
    service = IterativeExtractionService(...)
    result = await service.extract(ticker, cik, filings)
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

import anthropic
import httpx

from app.services.extraction import (
    SecApiClient,
    SECEdgarClient,
    ExtractionResult,
    ExtractedEntity,
    ExtractedDebtInstrument,
)
from app.services.utils import parse_json_robust
from app.services.extraction_utils import (
    extract_debt_sections as _extract_debt_sections,
    clean_filing_html,
    combine_filings,
    truncate_content,
    validate_extraction_structure,
    validate_entity_references,
    validate_debt_amounts as _validate_debt_amounts,
)


# =============================================================================
# CONFIGURATION
# =============================================================================

class ModelTier(Enum):
    TIER1_DEEPSEEK = "deepseek"
    TIER1_GEMINI = "gemini"
    TIER1_5_GEMINI_PRO = "gemini-pro"  # Gemini 2.5 Pro for escalation
    TIER2_SONNET = "sonnet"
    TIER3_OPUS = "opus"


class Complexity(Enum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


# Cost per 1M tokens (input/output)
MODEL_COSTS = {
    ModelTier.TIER1_DEEPSEEK: {"input": 0.27, "output": 1.10},
    ModelTier.TIER1_GEMINI: {"input": 0.10, "output": 0.40},  # Gemini 2.0 Flash
    ModelTier.TIER1_5_GEMINI_PRO: {"input": 1.25, "output": 10.00},  # Gemini 2.5 Pro
    ModelTier.TIER2_SONNET: {"input": 3.00, "output": 15.00},
    ModelTier.TIER3_OPUS: {"input": 15.00, "output": 75.00},
}

# Known complex/simple companies for manual override
KNOWN_COMPLEX = {
    "GE", "UTX", "HON",  # Multi-tier holdco structures
    "DLTR", "DG",  # Retail with complex debt
    "RIG", "DO", "NE", "VAL",  # Offshore drilling with complex debt
}

KNOWN_SIMPLE = {
    "AAPL", "MSFT", "GOOGL", "META", "AMZN",  # Clean tech companies
    "COST", "WMT",  # Simple structures
}


# Use shared utility - re-export for backwards compatibility
extract_debt_sections = _extract_debt_sections

# PE-heavy sectors (SIC codes)
PE_HEAVY_SECTORS = {"5912", "5311", "8062", "8011", "5411"}


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ExtractionAttempt:
    """Record of a single extraction attempt."""
    tier: ModelTier
    model_name: str
    result: Optional[dict]
    tokens_in: int
    tokens_out: int
    cost: float
    duration_seconds: float
    error: Optional[str] = None


@dataclass
class ValidationResult:
    """Result of validating an extraction."""
    checks: dict[str, bool]
    score: float
    action: str  # 'accept', 'escalate_tier2', 'escalate_tier3'
    reason: str
    issues: list[str] = field(default_factory=list)


@dataclass
class ExtractionMetrics:
    """Metrics for a completed extraction."""
    ticker: str
    complexity: Complexity
    attempts: list[ExtractionAttempt]
    final_tier: ModelTier
    total_cost: float
    total_duration: float
    validation_score: float
    entity_count: int
    debt_count: int


# =============================================================================
# PROMPTS
# =============================================================================

SYSTEM_PROMPT = """You are a credit analyst extracting corporate structure and debt information from SEC filings.

RULES:
1. Only include information explicitly stated in the filing
2. Use exact legal entity names from Exhibit 21.1 or credit agreements
3. All monetary amounts in USD CENTS (multiply dollars by 100): $500M = 50000000000
4. Interest rates in basis points: 8.50% = 850
5. If uncertain, include in "uncertainties" array
6. Return valid JSON matching the schema exactly

ENTITY TYPES:
- holdco: Ultimate parent or holding company
- opco: Main operating company
- finco: Finance subsidiary (issues debt)
- subsidiary: Operating subsidiary
- spv: Special purpose vehicle
- jv: Joint venture
- vie: Variable interest entity

SENIORITY (in order of priority):
- senior_secured: Has collateral, paid first
- senior_unsecured: No collateral, paid after secured
- subordinated: Contractually junior
- junior_subordinated: Most junior

SECURITY TYPES:
- first_lien: First priority on collateral
- second_lien: Second priority
- unsecured: No collateral"""


EXTRACTION_PROMPT_TEMPLATE = """Extract corporate structure AND ALL INDIVIDUAL DEBT INSTRUMENTS from these SEC filings.

<filings>
{context}
</filings>

CRITICAL - EXTRACT INDIVIDUAL DEBT INSTRUMENTS, NOT TOTALS:
You MUST extract EACH INDIVIDUAL debt instrument separately. Do NOT just report "Long-term debt" totals.

Example of WRONG output (too aggregated):
- "Long-term debt" with total amount

Example of CORRECT output (individual instruments):
- "8.75% Senior Notes due 2030" with specific amount
- "11.50% Senior Secured Notes due 2027" with specific amount
- "Term Loan due 2027" with specific amount
- "Credit Facility" with commitment and drawn amounts

WHERE TO FIND INDIVIDUAL DEBT INSTRUMENTS:
1. "Note X - Debt" in financial statement footnotes - usually has a TABLE with each instrument
2. Look for tables showing maturity schedule with interest rates
3. Credit agreements mentioned in 8-K filings
4. Look for specific percentages like "5.00% Notes" or "SOFR + 2.50%"
5. Bond indentures and debt descriptions in MD&A

REQUIRED: For each debt instrument extract:
- The SPECIFIC name (e.g., "8.75% Senior Notes due 2030", not just "Senior Notes")
- The interest rate:
  - For FIXED rate: interest_rate in basis points (e.g., 850 for 8.50%)
  - For FLOATING rate: spread_bps over benchmark (e.g., 225 for SOFR + 2.25%), benchmark name (SOFR, Prime, etc.)
  - For FLOATING rate with floor: floor_bps - the minimum interest rate floor. Look for phrases like:
    - "SOFR floor of 0.50%" = floor_bps: 50
    - "subject to a 0.75% floor" = floor_bps: 75
    - "with a floor of 1.00%" = floor_bps: 100
    - "SOFR (with a 0% floor)" = floor_bps: 0
    - Most term loans have floors; revolvers often do not
- The maturity date
- The issue_date - IMPORTANT: Look for when the debt was originally issued (e.g., "issued in June 2020", "entered into on March 15, 2021"). Common places to find this:
  - Debt footnote descriptions (e.g., "On May 15, 2020, the Company issued...")
  - Credit agreement dates (e.g., "Credit Agreement dated as of...")
  - If not explicitly stated, estimate from bond name: "5.00% Senior Notes due 2030" issued ~2020 (10-year bonds)
- The principal/outstanding amount for THAT SPECIFIC instrument

JOINT VENTURES AND COMPLEX OWNERSHIP - IMPORTANT:
Look carefully for these in MD&A, Notes to Financial Statements, and Exhibit 21:
1. **Joint Ventures (JVs)**: Entities with <100% ownership, often described as:
   - "joint venture", "JV", "50% owned", "50/50 partnership"
   - "equity method investment", "equity method investee"
   - "unconsolidated affiliate", "unconsolidated subsidiary"
   - Partnerships with named external partners
2. **VIEs (Variable Interest Entities)**: Often securitization vehicles, trusts, or special purpose entities
   - Look for "variable interest entity", "VIE", "primary beneficiary"
   - Common in auto finance (securitization trusts), real estate (property JVs)
3. **Unrestricted Subsidiaries**: Subsidiaries excluded from credit agreement covenants
   - Look for "unrestricted subsidiary" in debt footnotes or credit agreements

For JVs, ALWAYS capture:
- The JV entity name
- ownership_pct (e.g., 50 for 50%)
- is_joint_venture: true
- jv_partner_name: "Name of the external JV partner" (e.g., "LG Energy Solution")
- consolidation_method: "equity" (for equity method JVs) or "full" (for consolidated JVs)

Return JSON with this exact structure:

{{
  "company_name": "Legal parent company name",
  "ticker": "TICKER",
  "sector": "Industry sector",
  "entities": [
    {{
      "name": "Full legal entity name",
      "entity_type": "holdco|opco|finco|subsidiary|spv|jv|vie",
      "jurisdiction": "State/country",
      "formation_type": "LLC|Corp|LP|Ltd|Inc",
      "owners": [
        {{
          "parent_name": "Parent entity name (must match another entity exactly)",
          "ownership_pct": 100,
          "ownership_type": "direct|indirect|economic_only|voting_only",
          "is_joint_venture": false,
          "jv_partner_name": null
        }}
      ],
      "consolidation_method": "full|equity|proportional|vie|unconsolidated",
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
      "name": "Descriptive name (e.g., 'Term Loan B' or '5.00% Senior Notes due 2029')",
      "issuer_name": "Entity name that issued this debt (usually parent company)",
      "cusip": "9-char CUSIP if disclosed (e.g., '037833EP2') or null",
      "isin": "12-char ISIN if disclosed (e.g., 'US037833EP27') or null",
      "instrument_type": "term_loan_b|term_loan_a|revolver|senior_notes|senior_secured_notes|subordinated_notes|abl|convertible_notes|commercial_paper",
      "seniority": "senior_secured|senior_unsecured|subordinated",
      "security_type": "first_lien|second_lien|unsecured",
      "commitment": null,
      "principal": 75000000000,
      "outstanding": 75000000000,
      "currency": "USD",
      "rate_type": "fixed|floating",
      "interest_rate": 500,
      "spread_bps": 225,
      "benchmark": "SOFR",
      "floor_bps": 50,
      "issue_date": "2023-06-15",
      "maturity_date": "2029-06-15",
      "guarantor_names": ["Entity Name 1", "Entity Name 2"]
    }}
  ],
  "uncertainties": []
}}

CRITICAL REMINDERS:
- All amounts in CENTS (multiply dollars by 100): $1 billion = 100000000000 cents
- First entity should be ultimate parent with owners: []
- All other entities must reference a parent that exists
- ALWAYS populate "outstanding" for debt (use principal if not stated separately)
- guarantor_names must exactly match entity names
- EVERY company with public filings has some form of debt - find it!
- If you see amounts like "$98.3 billion" in debt, that's 9830000000000 cents
- For bonds/notes: Extract CUSIP (9 chars, e.g., "037833EP2") and ISIN (12 chars, e.g., "US037833EP27") if disclosed in the filing. These are often in debt footnotes or prospectuses. CUSIP can be derived from US ISIN by removing "US" prefix and last check digit.

Return ONLY the JSON object."""


ESCALATION_PROMPT_TEMPLATE = """A previous extraction attempt had validation issues. Re-extract with careful attention to the flagged problems.

VALIDATION ISSUES FROM PREVIOUS ATTEMPT:
{issues}

PREVIOUS EXTRACTION (may have errors):
{previous_extraction}

<filings>
{context}
</filings>

Please extract again, paying special attention to:
1. Entity hierarchy - ensure all parent references exist in the entities list
2. Guarantor references - all guarantor_names must match entity names exactly
3. Issuer references - issuer_name must match an entity name exactly
4. Debt amounts - verify against the filing, use CENTS not dollars
5. Outstanding amounts - MUST be populated for every debt instrument

Return the corrected JSON extraction."""


PREMIUM_PROMPT_TEMPLATE = """This is a complex corporate structure requiring careful analysis. Previous extraction attempts have failed validation.

PREVIOUS ATTEMPTS SUMMARY:
{attempts_summary}

KNOWN ISSUES:
{known_issues}

I need you to carefully analyze these filings and extract the complete structure.

<exhibit_21_subsidiaries>
{exhibit_21}
</exhibit_21_subsidiaries>

<filing_content>
{context}
</filing_content>

SPECIFIC INSTRUCTIONS:
1. Start with Exhibit 21 to get the complete subsidiary list
2. Cross-reference debt footnotes for guarantor information
3. Identify the EXACT legal entity that is the borrower/issuer for each facility
4. Note any unrestricted subsidiaries explicitly
5. If entities appear in debt docs but not Exhibit 21, include them with a note in uncertainties
6. All amounts MUST be in CENTS (multiply dollars by 100)
7. EVERY debt instrument MUST have an outstanding amount

Return complete JSON extraction with detailed uncertainties for anything unclear."""


# =============================================================================
# COMPLEXITY CLASSIFIER
# =============================================================================

def classify_complexity(
    ticker: str,
    filing_metadata: dict,
    exhibit_21_content: Optional[str] = None,
) -> Complexity:
    """
    Classify company complexity before extraction.
    Uses filing metadata and exhibit content, not full filing.
    """
    # Manual overrides
    if ticker.upper() in KNOWN_COMPLEX:
        return Complexity.COMPLEX
    if ticker.upper() in KNOWN_SIMPLE:
        return Complexity.SIMPLE

    # Count subsidiaries from Exhibit 21 if available
    subsidiary_count = 0
    if exhibit_21_content:
        # Rough count based on common patterns
        subsidiary_count = len(re.findall(
            r'(?:LLC|Inc\.|Corp\.|Ltd\.|LP|Limited|GmbH|S\.A\.|B\.V\.)',
            exhibit_21_content,
            re.IGNORECASE
        ))

    # Get metadata signals
    filing_size_mb = filing_metadata.get('file_size', 0) / 1_000_000
    sic_code = filing_metadata.get('sic_code', '')
    credit_exhibits = filing_metadata.get('credit_agreement_count', 0)

    # Complex indicators
    if subsidiary_count > 50:
        return Complexity.COMPLEX
    if credit_exhibits > 3:
        return Complexity.COMPLEX
    if sic_code in PE_HEAVY_SECTORS:
        return Complexity.COMPLEX
    if filing_size_mb > 15:
        return Complexity.COMPLEX

    # Medium indicators
    if subsidiary_count > 20:
        return Complexity.MEDIUM
    if credit_exhibits > 1:
        return Complexity.MEDIUM
    if filing_size_mb > 8:
        return Complexity.MEDIUM

    return Complexity.SIMPLE


# =============================================================================
# VALIDATION
# =============================================================================
# Note: These validation functions return bool for simple pass/fail checks.
# For more detailed validation with error messages, see extraction_utils.py:
#   validate_extraction_structure, validate_entity_references, validate_debt_amounts


def validate_hierarchy(entities: list[dict]) -> bool:
    """Check that all parent references are valid."""
    names = {e.get('name') for e in entities}

    for entity in entities:
        owners = entity.get('owners', [])
        for owner in owners:
            parent_name = owner.get('parent_name')
            if parent_name and parent_name not in names:
                return False
    return True


def validate_issuer_references(extraction: dict) -> bool:
    """Check that all issuer names reference valid entities."""
    entity_names = {e.get('name') for e in extraction.get('entities', [])}

    for debt in extraction.get('debt_instruments', []):
        issuer = debt.get('issuer_name')
        if issuer and issuer not in entity_names:
            return False
    return True


def validate_guarantor_references(extraction: dict) -> bool:
    """Check that all guarantor names reference valid entities."""
    entity_names = {e.get('name') for e in extraction.get('entities', [])}

    for debt in extraction.get('debt_instruments', []):
        for guarantor in debt.get('guarantor_names', []):
            if guarantor not in entity_names:
                return False
    return True


def validate_debt_amounts(extraction: dict) -> bool:
    """Sanity check debt amounts."""
    total_debt = 0
    for debt in extraction.get('debt_instruments', []):
        amount = debt.get('outstanding') or debt.get('principal') or 0
        if amount < 0:
            return False
        total_debt += amount

    # Should be between $0 and $2T (in cents)
    max_reasonable = 200_000_000_000_000  # $2T in cents
    return 0 <= total_debt <= max_reasonable


def validate_has_outstanding(extraction: dict) -> bool:
    """Check that debt instruments have outstanding amounts."""
    instruments = extraction.get('debt_instruments', [])
    if not instruments:
        return True  # No debt is valid

    # At least 50% should have outstanding populated
    with_outstanding = sum(1 for d in instruments if d.get('outstanding') is not None)
    return with_outstanding >= len(instruments) * 0.5


def validate_extraction(extraction: dict, complexity: Complexity) -> ValidationResult:
    """
    Validate extraction quality. Determine if escalation needed.
    """
    # Check if debt was extracted (most public companies have debt)
    has_debt = len(extraction.get('debt_instruments', [])) > 0

    checks = {
        'has_entities': len(extraction.get('entities', [])) > 0,
        'has_holdco': any(
            e.get('entity_type') == 'holdco'
            for e in extraction.get('entities', [])
        ),
        'hierarchy_valid': validate_hierarchy(extraction.get('entities', [])),
        'issuer_refs_valid': validate_issuer_references(extraction),
        'guarantor_refs_valid': validate_guarantor_references(extraction),
        'debt_amounts_reasonable': validate_debt_amounts(extraction),
        'has_outstanding_amounts': validate_has_outstanding(extraction),
        'has_company_name': bool(extraction.get('company_name')),
        'has_debt_instruments': has_debt,  # Most companies should have debt
    }

    # Collect specific issues
    issues = [check for check, passed in checks.items() if not passed]

    # Calculate score
    total_score = sum(checks.values()) / len(checks) if checks else 0

    # Critical checks that must pass
    critical_checks = ['has_entities', 'has_holdco', 'hierarchy_valid', 'has_company_name']
    critical_pass = all(checks.get(c, False) for c in critical_checks)

    # Determine action based on score and complexity
    if not critical_pass:
        action = 'escalate_tier3'
        reason = f'Critical validation failed: {", ".join(issues)}'
    elif total_score < 0.6:
        action = 'escalate_tier3'
        reason = f'Very low validation score: {total_score:.0%}'
    elif total_score < 0.75:
        action = 'escalate_tier2'
        reason = f'Low validation score: {total_score:.0%}'
    elif not has_debt and complexity != Complexity.SIMPLE:
        # Most public companies have debt - escalate if none found for non-simple companies
        action = 'escalate_tier2'
        reason = 'No debt instruments found for non-simple company'
    elif complexity == Complexity.COMPLEX and total_score < 0.9:
        action = 'escalate_tier2'
        reason = f'Complex company below 90% threshold: {total_score:.0%}'
    elif total_score < 0.85:
        action = 'escalate_tier2'
        reason = f'Borderline score: {total_score:.0%}'
    else:
        action = 'accept'
        reason = f'Passed validation: {total_score:.0%}'

    return ValidationResult(
        checks=checks,
        score=total_score,
        action=action,
        reason=reason,
        issues=issues,
    )


# =============================================================================
# MODEL CLIENTS
# =============================================================================

class DeepSeekClient:
    """Client for DeepSeek API (OpenAI-compatible)."""

    BASE_URL = "https://api.deepseek.com/v1"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=120.0,
        )

    async def close(self):
        await self.client.aclose()

    async def extract(self, context: str) -> tuple[dict, int, int]:
        """
        Run extraction with DeepSeek.
        Returns: (result_dict, tokens_in, tokens_out)
        """
        prompt = EXTRACTION_PROMPT_TEMPLATE.format(context=context)

        response = await self.client.post(
            "/chat/completions",
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        data = response.json()

        content = data["choices"][0]["message"]["content"]
        result = parse_json_robust(content)

        usage = data.get("usage", {})
        tokens_in = usage.get("prompt_tokens", 0)
        tokens_out = usage.get("completion_tokens", 0)

        return result, tokens_in, tokens_out


class GeminiClient:
    """Client for Google Gemini API."""

    def __init__(self, api_key: str):
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        self.genai = genai
        # Use Gemini 1.5 Flash for higher rate limits
        self.model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            generation_config={
                "temperature": 0.1,
                "response_mime_type": "application/json",
                "max_output_tokens": 32000,  # Increase for complex companies with many entities (banks, etc.)
            },
            system_instruction=SYSTEM_PROMPT,
        )

    async def extract(self, context: str) -> tuple[dict, int, int]:
        """
        Run extraction with Gemini.
        Returns: (result_dict, tokens_in, tokens_out)
        """
        prompt = EXTRACTION_PROMPT_TEMPLATE.format(context=context)

        # Gemini SDK is synchronous, run in executor
        import asyncio
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self.model.generate_content(prompt)
        )

        content = response.text
        result = parse_json_robust(content)

        # Get token counts from usage metadata
        usage = response.usage_metadata
        tokens_in = usage.prompt_token_count if usage else 0
        tokens_out = usage.candidates_token_count if usage else 0

        return result, tokens_in, tokens_out

    async def extract_pro(
        self,
        context: str,
        previous_extraction: Optional[dict] = None,
        issues: Optional[list[str]] = None,
    ) -> tuple[dict, int, int]:
        """
        Run extraction with Gemini 1.5 Pro (intermediate escalation tier).
        More capable than Flash for complex debt structures.
        Returns: (result_dict, tokens_in, tokens_out)
        """
        # Create Gemini 2.5 Pro model for this call
        pro_model = self.genai.GenerativeModel(
            model_name="gemini-2.5-pro",
            generation_config={
                "temperature": 0.1,
                "response_mime_type": "application/json",
                "max_output_tokens": 32000,
            },
            system_instruction=SYSTEM_PROMPT,
        )

        if previous_extraction and issues:
            prompt = ESCALATION_PROMPT_TEMPLATE.format(
                context=context,
                previous_extraction=json.dumps(previous_extraction, indent=2),
                issues=json.dumps(issues, indent=2),
            )
        else:
            prompt = EXTRACTION_PROMPT_TEMPLATE.format(context=context)

        # Gemini SDK is synchronous, run in executor
        import asyncio
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: pro_model.generate_content(prompt)
        )

        content = response.text
        result = parse_json_robust(content)

        # Get token counts from usage metadata
        usage = response.usage_metadata
        tokens_in = usage.prompt_token_count if usage else 0
        tokens_out = usage.candidates_token_count if usage else 0

        return result, tokens_in, tokens_out


class ClaudeClient:
    """Client for Claude API (Sonnet and Opus)."""

    def __init__(self, api_key: str):
        self.client = anthropic.Anthropic(api_key=api_key)

    async def extract_sonnet(
        self,
        context: str,
        previous_extraction: Optional[dict] = None,
        issues: Optional[list[str]] = None,
    ) -> tuple[dict, int, int]:
        """Run extraction with Claude Sonnet (Tier 2)."""

        if previous_extraction and issues:
            prompt = ESCALATION_PROMPT_TEMPLATE.format(
                context=context,
                previous_extraction=json.dumps(previous_extraction, indent=2),
                issues=json.dumps(issues, indent=2),
            )
        else:
            prompt = EXTRACTION_PROMPT_TEMPLATE.format(context=context)

        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )

        content = response.content[0].text
        # Extract JSON from response
        result = self._parse_json_response(content)

        return result, response.usage.input_tokens, response.usage.output_tokens

    async def extract_opus(
        self,
        context: str,
        exhibit_21: str,
        previous_attempts: list[dict],
        issues: list[str],
    ) -> tuple[dict, int, int]:
        """Run extraction with Claude Opus (Tier 3)."""

        attempts_summary = json.dumps([
            {
                "tier": a.get("tier", "unknown"),
                "entity_count": len(a.get("entities", [])),
                "debt_count": len(a.get("debt_instruments", [])),
            }
            for a in previous_attempts
        ], indent=2)

        prompt = PREMIUM_PROMPT_TEMPLATE.format(
            context=context,
            exhibit_21=exhibit_21 or "Not available",
            attempts_summary=attempts_summary,
            known_issues=json.dumps(issues, indent=2),
        )

        response = self.client.messages.create(
            model="claude-opus-4-20250514",
            max_tokens=12000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )

        content = response.content[0].text
        result = self._parse_json_response(content)

        return result, response.usage.input_tokens, response.usage.output_tokens

    def _parse_json_response(self, content: str) -> dict:
        """Parse JSON from response, handling markdown code blocks and common errors."""
        return parse_json_robust(content)


# =============================================================================
# COST CALCULATION
# =============================================================================

def calculate_cost(tier: ModelTier, tokens_in: int, tokens_out: int) -> float:
    """Calculate cost in dollars for an API call."""
    costs = MODEL_COSTS[tier]
    cost_in = (tokens_in / 1_000_000) * costs["input"]
    cost_out = (tokens_out / 1_000_000) * costs["output"]
    return cost_in + cost_out


# =============================================================================
# TIERED EXTRACTION SERVICE
# =============================================================================

class TieredExtractionService:
    """
    Cost-optimized extraction service using tiered models.

    Flow:
    1. Classify complexity
    2. Try Tier 1 (Gemini or DeepSeek) - $0.01-0.05
    3. Validate result
    4. If needed, escalate to Tier 2 (Sonnet) - $0.15
    5. If still failing, escalate to Tier 3 (Opus) - $0.50+
    """

    def __init__(
        self,
        anthropic_api_key: str,
        deepseek_api_key: Optional[str] = None,
        gemini_api_key: Optional[str] = None,
        sec_api_key: Optional[str] = None,
        tier1_model: str = "gemini",  # "gemini" or "deepseek"
    ):
        self.claude = ClaudeClient(anthropic_api_key)
        self.deepseek = DeepSeekClient(deepseek_api_key) if deepseek_api_key else None
        self.gemini = GeminiClient(gemini_api_key) if gemini_api_key else None
        self.sec_api = SecApiClient(sec_api_key) if sec_api_key else None
        self.edgar = SECEdgarClient()
        self.tier1_model = tier1_model

    async def close(self):
        if self.deepseek:
            await self.deepseek.close()
        await self.edgar.close()

    def _clean_content(self, content: str, max_chars: int = 150000) -> str:
        """Clean and truncate filing content. Uses shared utility."""
        from app.services.extraction_utils import clean_filing_html, truncate_content
        cleaned = clean_filing_html(content)
        return truncate_content(cleaned, max_chars)

    def _combine_filings(self, filings: dict[str, str], max_chars: int = 300000) -> str:
        """Combine filings into extraction context. Uses shared utility."""
        from app.services.extraction_utils import combine_filings
        return combine_filings(filings, max_chars=max_chars, include_headers=True)

    async def extract_company(
        self,
        ticker: str,
        cik: str,
        skip_tier1: bool = False,
    ) -> tuple[dict, ExtractionMetrics]:
        """
        Full tiered extraction pipeline.

        Args:
            ticker: Stock ticker
            cik: SEC CIK number
            skip_tier1: If True, start at Tier 2 (for known complex)

        Returns:
            (extraction_result, metrics)
        """
        start_time = datetime.now()
        attempts: list[ExtractionAttempt] = []

        # Step 1: Download filings
        print(f"\n  Fetching filings...")
        filings = {}
        exhibit_21 = ""

        if self.sec_api and self.sec_api.query_api:
            filings = await self.sec_api.get_all_relevant_filings(ticker)
            # Try to get Exhibit 21 specifically
            exhibit_21 = self.sec_api.get_exhibit_21(ticker)

        if not filings:
            filings = await self.edgar.get_all_relevant_filings(cik)

        if not filings:
            raise ValueError(f"No filings found for {ticker}")

        # Combine filings into context
        context = self._combine_filings(filings)
        print(f"  Combined filings: {len(context):,} characters")

        # Step 2: Classify complexity
        filing_metadata = {
            'file_size': len(context),
            'sic_code': '',
            'credit_agreement_count': sum(1 for k in filings if 'exhibit_10' in k.lower()),
        }
        complexity = classify_complexity(ticker, filing_metadata, exhibit_21)
        print(f"  Complexity: {complexity.value}")

        # Skip Tier 1 for known complex companies
        if ticker.upper() in KNOWN_COMPLEX:
            skip_tier1 = True

        result = None
        validation = None

        # Step 3: Tier 1 extraction (Gemini or DeepSeek)
        tier1_client = None
        tier1_tier = None
        tier1_name = None

        # Select Tier 1 model based on configuration and availability
        if not skip_tier1:
            if self.tier1_model == "gemini" and self.gemini:
                tier1_client = self.gemini
                tier1_tier = ModelTier.TIER1_GEMINI
                tier1_name = "gemini-2.0-flash"
            elif self.tier1_model == "deepseek" and self.deepseek:
                tier1_client = self.deepseek
                tier1_tier = ModelTier.TIER1_DEEPSEEK
                tier1_name = "deepseek-v3"
            elif self.gemini:
                # Fallback to Gemini if preferred model not available
                tier1_client = self.gemini
                tier1_tier = ModelTier.TIER1_GEMINI
                tier1_name = "gemini-2.0-flash"
            elif self.deepseek:
                # Fallback to DeepSeek
                tier1_client = self.deepseek
                tier1_tier = ModelTier.TIER1_DEEPSEEK
                tier1_name = "deepseek-v3"

        if tier1_client:
            print(f"\n  Tier 1: {tier1_name} extraction...")
            tier1_start = datetime.now()

            try:
                result, tokens_in, tokens_out = await tier1_client.extract(context)
                cost = calculate_cost(tier1_tier, tokens_in, tokens_out)
                duration = (datetime.now() - tier1_start).total_seconds()

                attempts.append(ExtractionAttempt(
                    tier=tier1_tier,
                    model_name=tier1_name,
                    result=result,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost=cost,
                    duration_seconds=duration,
                ))

                print(f"    Entities: {len(result.get('entities', []))}, "
                      f"Debt: {len(result.get('debt_instruments', []))}, "
                      f"Cost: ${cost:.4f}")

                # Validate
                validation = validate_extraction(result, complexity)
                print(f"    Validation: {validation.score:.0%} - {validation.action}")

                if validation.action == 'accept':
                    return self._finalize(result, attempts, complexity, start_time)

            except Exception as e:
                print(f"    [FAIL] {tier1_name} error: {e}")
                attempts.append(ExtractionAttempt(
                    tier=tier1_tier,
                    model_name=tier1_name,
                    result=None,
                    tokens_in=0,
                    tokens_out=0,
                    cost=0,
                    duration_seconds=(datetime.now() - tier1_start).total_seconds(),
                    error=str(e),
                ))

        # Step 4: Tier 2 extraction (Claude Sonnet)
        print(f"\n  Tier 2: Claude Sonnet extraction...")
        tier2_start = datetime.now()

        try:
            previous = result if result else None
            issues = validation.issues if validation else []

            result, tokens_in, tokens_out = await self.claude.extract_sonnet(
                context, previous, issues
            )
            cost = calculate_cost(ModelTier.TIER2_SONNET, tokens_in, tokens_out)
            duration = (datetime.now() - tier2_start).total_seconds()

            attempts.append(ExtractionAttempt(
                tier=ModelTier.TIER2_SONNET,
                model_name="claude-sonnet-4",
                result=result,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost=cost,
                duration_seconds=duration,
            ))

            print(f"    Entities: {len(result.get('entities', []))}, "
                  f"Debt: {len(result.get('debt_instruments', []))}, "
                  f"Cost: ${cost:.4f}")

            # Validate
            validation = validate_extraction(result, complexity)
            print(f"    Validation: {validation.score:.0%} - {validation.action}")

            if validation.action == 'accept':
                return self._finalize(result, attempts, complexity, start_time)

        except Exception as e:
            print(f"    [FAIL] Sonnet error: {e}")
            attempts.append(ExtractionAttempt(
                tier=ModelTier.TIER2_SONNET,
                model_name="claude-sonnet-4",
                result=None,
                tokens_in=0,
                tokens_out=0,
                cost=0,
                duration_seconds=(datetime.now() - tier2_start).total_seconds(),
                error=str(e),
            ))

        # Step 5: Tier 3 extraction (Claude Opus)
        if validation and validation.action == 'escalate_tier3' or complexity == Complexity.COMPLEX:
            print(f"\n  Tier 3: Claude Opus extraction...")
            tier3_start = datetime.now()

            try:
                previous_results = [a.result for a in attempts if a.result]
                issues = validation.issues if validation else []

                result, tokens_in, tokens_out = await self.claude.extract_opus(
                    context, exhibit_21, previous_results, issues
                )
                cost = calculate_cost(ModelTier.TIER3_OPUS, tokens_in, tokens_out)
                duration = (datetime.now() - tier3_start).total_seconds()

                attempts.append(ExtractionAttempt(
                    tier=ModelTier.TIER3_OPUS,
                    model_name="claude-opus-4",
                    result=result,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost=cost,
                    duration_seconds=duration,
                ))

                print(f"    Entities: {len(result.get('entities', []))}, "
                      f"Debt: {len(result.get('debt_instruments', []))}, "
                      f"Cost: ${cost:.4f}")

                validation = validate_extraction(result, complexity)
                print(f"    Validation: {validation.score:.0%} - {validation.action}")

            except Exception as e:
                print(f"    [FAIL] Opus error: {e}")
                attempts.append(ExtractionAttempt(
                    tier=ModelTier.TIER3_OPUS,
                    model_name="claude-opus-4",
                    result=None,
                    tokens_in=0,
                    tokens_out=0,
                    cost=0,
                    duration_seconds=(datetime.now() - tier3_start).total_seconds(),
                    error=str(e),
                ))

        # Return best result we have
        if result:
            return self._finalize(result, attempts, complexity, start_time)

        # No valid result
        raise ValueError(f"All extraction attempts failed for {ticker}")

    def _finalize(
        self,
        result: dict,
        attempts: list[ExtractionAttempt],
        complexity: Complexity,
        start_time: datetime,
    ) -> tuple[dict, ExtractionMetrics]:
        """Finalize extraction and compute metrics."""

        total_cost = sum(a.cost for a in attempts)
        total_duration = (datetime.now() - start_time).total_seconds()
        final_attempt = next((a for a in reversed(attempts) if a.result), attempts[-1])

        # Get final validation score
        validation = validate_extraction(result, complexity)

        metrics = ExtractionMetrics(
            ticker=result.get('ticker', ''),
            complexity=complexity,
            attempts=attempts,
            final_tier=final_attempt.tier,
            total_cost=total_cost,
            total_duration=total_duration,
            validation_score=validation.score,
            entity_count=len(result.get('entities', [])),
            debt_count=len(result.get('debt_instruments', [])),
        )

        # Add metadata to result
        result['_extraction'] = {
            'attempts': len(attempts),
            'models_used': [a.model_name for a in attempts],
            'final_model': final_attempt.model_name,
            'total_cost': total_cost,
            'complexity': complexity.value,
            'validation_score': validation.score,
        }

        return result, metrics


# =============================================================================
# METRICS TRACKING
# =============================================================================

class ExtractionTracker:
    """Track extraction costs and quality metrics across runs."""

    def __init__(self):
        self.extractions: list[ExtractionMetrics] = []

    def record(self, metrics: ExtractionMetrics):
        self.extractions.append(metrics)

    def summary(self) -> dict:
        if not self.extractions:
            return {}

        costs = [e.total_cost for e in self.extractions]
        scores = [e.validation_score for e in self.extractions]

        by_tier = {}
        for e in self.extractions:
            tier = e.final_tier.value
            by_tier[tier] = by_tier.get(tier, 0) + 1

        by_complexity = {}
        for e in self.extractions:
            c = e.complexity.value
            by_complexity[c] = by_complexity.get(c, 0) + 1

        escalation_count = sum(1 for e in self.extractions if len(e.attempts) > 1)

        return {
            'total_companies': len(self.extractions),
            'total_cost': sum(costs),
            'avg_cost': sum(costs) / len(costs),
            'max_cost': max(costs),
            'min_cost': min(costs),
            'avg_validation_score': sum(scores) / len(scores),
            'by_final_tier': by_tier,
            'by_complexity': by_complexity,
            'escalation_rate': escalation_count / len(self.extractions),
        }

    def print_summary(self):
        s = self.summary()
        if not s:
            print("No extractions recorded")
            return

        print("\n" + "=" * 60)
        print("EXTRACTION SUMMARY")
        print("=" * 60)
        print(f"Total companies: {s['total_companies']}")
        print(f"Total cost: ${s['total_cost']:.2f}")
        print(f"Avg cost: ${s['avg_cost']:.4f}")
        print(f"Cost range: ${s['min_cost']:.4f} - ${s['max_cost']:.4f}")
        print(f"Avg validation score: {s['avg_validation_score']:.0%}")
        print(f"Escalation rate: {s['escalation_rate']:.0%}")
        print(f"\nBy final tier: {s['by_final_tier']}")
        print(f"By complexity: {s['by_complexity']}")
