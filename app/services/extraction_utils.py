"""
SEC Filing Extraction Utilities
===============================

This module provides utilities for processing SEC EDGAR filings.
It handles the technical aspects of cleaning and combining filing content.

MODULES OVERVIEW
----------------
This file (extraction_utils.py) contains:
    - SEC filing HTML/XBRL cleaning
    - Filing content combining with priority ordering
    - Debt-specific section extraction
    - LLM cost tracking

For general utilities, see utils.py:
    - JSON parsing from LLM responses
    - Entity name normalization
    - Date parsing

USAGE
-----
    from app.services.extraction_utils import (
        clean_filing_html,     # Clean SEC filing HTML/XBRL
        combine_filings,       # Combine multiple filings by priority
        extract_debt_sections, # Extract debt-related content
        LLMUsage,             # Track LLM API costs
    )
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


# =============================================================================
# LLM MODEL CONFIGURATION
# =============================================================================

class ModelTier(Enum):
    """
    Available LLM Model Tiers
    =========================

    Enumeration of supported LLM models for extraction tasks.
    Used for cost tracking and model selection.

    TIERS
    -----
    GEMINI_FLASH : Fast, cheap model for simple extractions
    GEMINI_PRO   : Higher quality for complex extractions
    CLAUDE_SONNET: Good balance of quality and cost
    CLAUDE_OPUS  : Highest quality, most expensive
    DEEPSEEK     : Cost-effective alternative
    """
    GEMINI_FLASH = "gemini-2.0-flash"
    GEMINI_PRO = "gemini-2.5-pro"
    CLAUDE_SONNET = "claude-sonnet"
    CLAUDE_OPUS = "claude-opus"
    DEEPSEEK = "deepseek"


# Cost per 1M tokens (input_cost, output_cost) in USD
MODEL_COSTS = {
    ModelTier.GEMINI_FLASH: (0.10, 0.40),
    ModelTier.GEMINI_PRO: (1.25, 10.00),
    ModelTier.CLAUDE_SONNET: (3.00, 15.00),
    ModelTier.CLAUDE_OPUS: (15.00, 75.00),
    ModelTier.DEEPSEEK: (0.27, 1.10),
}


def calculate_cost(model: ModelTier, input_tokens: int, output_tokens: int) -> float:
    """
    Calculate LLM API Cost
    ======================

    Calculates the cost in USD for an LLM API call based on token counts.

    PARAMETERS
    ----------
    model : ModelTier
        The model used for the API call
    input_tokens : int
        Number of input/prompt tokens
    output_tokens : int
        Number of output/completion tokens

    RETURNS
    -------
    float
        Cost in USD

    EXAMPLE
    -------
        cost = calculate_cost(ModelTier.GEMINI_FLASH, 10000, 2000)
        # Returns: 0.0018 (about 0.2 cents)
    """
    input_cost, output_cost = MODEL_COSTS.get(model, (0, 0))
    return (input_tokens * input_cost + output_tokens * output_cost) / 1_000_000


@dataclass
class LLMUsage:
    """
    LLM Usage Tracker
    =================

    Tracks cumulative LLM API usage and costs across multiple calls.
    Useful for monitoring extraction costs per company.

    ATTRIBUTES
    ----------
    model : str
        Model identifier string
    input_tokens : int
        Total input tokens used
    output_tokens : int
        Total output tokens used
    calls : int
        Number of API calls made

    EXAMPLE
    -------
        usage = LLMUsage(model="gemini-2.0-flash")
        usage.add_call(input_tokens=5000, output_tokens=1000)
        usage.add_call(input_tokens=3000, output_tokens=500)
        print(f"Total cost: ${usage.cost:.4f}")
    """
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    @property
    def cost(self) -> float:
        """Calculate total cost in USD."""
        for tier in ModelTier:
            if tier.value in self.model.lower():
                return calculate_cost(tier, self.input_tokens, self.output_tokens)
        return 0.0

    def add_call(self, input_tokens: int, output_tokens: int) -> None:
        """Record a new LLM API call."""
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.calls += 1


# =============================================================================
# SEC FILING HTML/XBRL CLEANING
# =============================================================================

def clean_filing_html(content: str) -> str:
    """
    Clean SEC Filing HTML/XBRL Content
    ===================================

    Extracts readable text from SEC EDGAR filing HTML, handling modern
    iXBRL (inline XBRL) format used since 2019.

    STEPS
    -----
    1. Check if content is already clean text (not HTML)
    2. Remove XML declaration and DOCTYPE
    3. Remove <script> and <style> blocks
    4. Remove XBRL hidden sections (<ix:hidden>)
    5. Extract text from XBRL elements (<ix:nonNumeric>, etc.)
    6. Remove remaining HTML/XML tags
    7. Decode HTML entities (&nbsp;, &#x2019;, etc.)
    8. Normalize whitespace

    HANDLES
    -------
    - Standard HTML tags
    - iXBRL (inline XBRL) elements
    - Hidden XBRL metadata sections
    - Numeric and named HTML entities
    - XML declarations and DOCTYPE

    PARAMETERS
    ----------
    content : str
        Raw SEC filing content (HTML/XBRL)

    RETURNS
    -------
    str
        Clean, readable text extracted from the filing

    EXAMPLE
    -------
        raw_html = '<html><body><ix:nonNumeric>Revenue</ix:nonNumeric>...</body></html>'
        clean_text = clean_filing_html(raw_html)
        # Returns: "Revenue ..."

    NOTE
    ----
    This function is specifically designed for SEC EDGAR filings.
    For simple HTML, use clean_html() from utils.py instead.
    """
    if not content:
        return ""

    # Already clean text (not HTML)
    if not content.strip().startswith('<') and not content.strip().startswith('<?xml'):
        return content

    # Remove XML declaration and DOCTYPE
    content = re.sub(r'<\?xml[^>]*\?>', '', content)
    content = re.sub(r'<!DOCTYPE[^>]*>', '', content)

    # Remove script and style blocks
    content = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', content, flags=re.IGNORECASE)
    content = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', content, flags=re.IGNORECASE)

    # Remove XBRL hidden sections (contain metadata, not displayed)
    content = re.sub(r'<ix:hidden[^>]*>[\s\S]*?</ix:hidden>', '', content, flags=re.IGNORECASE)

    # Extract text from XBRL elements (preserve the displayed values)
    content = re.sub(r'<ix:[^>]*>([^<]*)</ix:[^>]*>', r'\1', content)

    # Remove remaining HTML/XML tags
    content = re.sub(r'<[^>]+>', ' ', content)

    # Decode common HTML entities
    entities = {
        '&nbsp;': ' ', '&amp;': '&', '&lt;': '<', '&gt;': '>',
        '&quot;': '"', '&#39;': "'", '&apos;': "'",
        '&#x2019;': "'", '&#x2014;': '-', '&#x2013;': '-',
    }
    for entity, char in entities.items():
        content = content.replace(entity, char)

    # Decode numeric HTML entities
    content = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), content)
    content = re.sub(r'&#x([0-9a-fA-F]+);', lambda m: chr(int(m.group(1), 16)), content)

    # Normalize whitespace
    content = re.sub(r'\s+', ' ', content)
    content = re.sub(r'\n\s*\n', '\n\n', content)

    return content.strip()


def truncate_content(content: str, max_chars: int) -> str:
    """
    Truncate Content at Sentence Boundary
    =====================================

    Truncates content to a maximum length, preferring to cut at
    sentence boundaries for cleaner LLM context.

    STEPS
    -----
    1. Return content unchanged if under max_chars
    2. Find last sentence boundary (". ") in final 20% of allowed text
    3. Cut at sentence boundary if found, otherwise at max_chars

    PARAMETERS
    ----------
    content : str
        Content to truncate
    max_chars : int
        Maximum characters allowed

    RETURNS
    -------
    str
        Truncated content

    EXAMPLE
    -------
        text = "First sentence. Second sentence. Third sentence is long."
        truncate_content(text, 35)
        # Returns: "First sentence. Second sentence."
    """
    if len(content) <= max_chars:
        return content

    truncated = content[:max_chars]
    last_period = truncated.rfind('. ')
    if last_period > max_chars * 0.8:
        return truncated[:last_period + 1]
    return truncated


# =============================================================================
# FILING CONTENT COMBINING
# =============================================================================

# Priority order for combining filings (most important first)
FILING_PRIORITY = [
    '10-K', '10-Q',                        # Primary financial filings
    'exhibit_21',                          # Subsidiary list
    '8-K',                                 # Material events
    'indenture', 'credit_agreement',       # Debt documents
]


def combine_filings(
    filings: dict[str, str],
    max_chars: int = 300_000,
    include_headers: bool = True
) -> str:
    """
    Combine Multiple SEC Filings into LLM Context
    ==============================================

    Combines multiple SEC filings into a single context string for LLM
    processing, prioritizing the most important filings.

    STEPS
    -----
    1. Sort filings by priority (10-K > 10-Q > Exhibit 21 > 8-K > debt docs)
    2. Allocate characters per filing based on count
    3. For each filing:
       a. Clean HTML/XBRL content
       b. Truncate to allocated size
       c. Add section header if enabled
    4. Combine until max_chars reached

    PRIORITY ORDER
    --------------
    1. 10-K (annual report - most comprehensive)
    2. 10-Q (quarterly report)
    3. Exhibit 21 (subsidiary list)
    4. 8-K (material events)
    5. Indentures, credit agreements

    PARAMETERS
    ----------
    filings : dict[str, str]
        Mapping of filing type to raw content
    max_chars : int
        Maximum total characters (default: 300,000)
    include_headers : bool
        Whether to add section headers (default: True)

    RETURNS
    -------
    str
        Combined filing content with headers

    EXAMPLE
    -------
        filings = {
            "10-K_2024": raw_10k_content,
            "8-K_2024-12-01": raw_8k_content,
        }
        combined = combine_filings(filings, max_chars=200000)
        # Returns combined content with headers like:
        # ============================================================
        # 10-K_2024
        # ============================================================
        # [10-K content...]
        #
        # ============================================================
        # 8-K_2024-12-01
        # ============================================================
        # [8-K content...]
    """
    if not filings:
        return ""

    # Sort by priority
    def priority_key(item: tuple[str, str]) -> int:
        filing_type = item[0].lower()
        for i, pattern in enumerate(FILING_PRIORITY):
            if pattern.lower() in filing_type:
                return i
        return len(FILING_PRIORITY)

    sorted_filings = sorted(filings.items(), key=priority_key)

    combined = []
    total_chars = 0
    chars_per_filing = max_chars // max(len(sorted_filings), 1)

    for filing_type, content in sorted_filings:
        if total_chars >= max_chars:
            break

        # Clean and truncate
        cleaned = clean_filing_html(content)
        remaining = max_chars - total_chars
        available = max(chars_per_filing, remaining // 2)
        truncated = truncate_content(cleaned, available)

        if truncated:
            if include_headers:
                combined.append(f"\n{'='*60}\n{filing_type.upper()}\n{'='*60}\n")
            combined.append(truncated)
            total_chars += len(truncated) + (70 if include_headers else 0)

    return '\n'.join(combined)


# =============================================================================
# DEBT SECTION EXTRACTION
# =============================================================================

# Priority 1: Debt footnote headings (usually contain the table of instruments)
DEBT_KEYWORDS_PRIORITY = [
    "debt - ",               # "Note 9 - Debt -" style
    "long-term debt -",
    "debt and credit",
    "borrowings -",
    "notes and debentures",
    "senior notes due",      # Specific instrument names
    "% notes due",           # e.g., "8.75% Notes due 2030"
    "credit agreement",
]

# Priority 2: General debt keywords
DEBT_KEYWORDS_GENERAL = [
    "long-term debt",
    "notes payable",
    "senior notes",
    "credit facility",
    "term loan",
    "revolving credit",
    "debt maturity",
    "indebtedness",
    "secured credit",
    "aggregate principal",
    "principal amount",
]

# Priority 3: JV and complex ownership keywords
JV_VIE_KEYWORDS = [
    "joint venture",
    "equity method",
    "unconsolidated",
    "variable interest entit",
    "vie",
    "50% owned",
    "50/50",
    "unrestricted subsidiar",
]


def extract_debt_sections(content: str, max_chars: int = 50_000) -> str:
    """
    Extract Debt-Related Sections from SEC Filing
    ==============================================

    Searches filing content for debt disclosures and extracts surrounding
    context. Optimized for finding the complete debt table and details.

    STEPS
    -----
    1. Search for priority keywords (debt footnote headings) with large context
    2. Search for general debt keywords with smaller context
    3. Search for JV/VIE keywords (important for complete structure)
    4. Skip overlapping sections
    5. Combine up to 15 sections within max_chars

    KEYWORD PRIORITIES
    ------------------
    Priority 1 (10k context): Debt footnote headers, specific instrument names
    Priority 2 (5k context): General debt terms, credit facility mentions
    Priority 3 (4k context): JV/VIE keywords for ownership structure

    PARAMETERS
    ----------
    content : str
        Full filing content to search
    max_chars : int
        Maximum characters to return (default: 50,000)

    RETURNS
    -------
    str
        Extracted debt-related sections

    EXAMPLE
    -------
        filing_content = "... Note 10 - Long-Term Debt ... Term Loan B ..."
        debt_sections = extract_debt_sections(filing_content)
        # Returns sections containing debt disclosures
    """
    content_lower = content.lower()
    sections = []
    section_positions = []

    def add_section(pos: int, context_before: int = 2000, context_after: int = 8000) -> None:
        """Add section around position, checking for overlap."""
        start = max(0, pos - context_before)
        end = min(len(content), pos + context_after)

        # Check for overlap with existing sections
        for existing_start, existing_end in section_positions:
            if start < existing_end and end > existing_start:
                return  # Skip overlapping section

        section_positions.append((start, end))
        sections.append(content[start:end])

    # Pass 1: Priority keywords (larger context)
    for keyword in DEBT_KEYWORDS_PRIORITY:
        idx = 0
        while idx < len(content_lower):
            pos = content_lower.find(keyword, idx)
            if pos == -1:
                break
            add_section(pos, context_before=1000, context_after=10000)
            idx = pos + len(keyword)

    # Pass 2: General keywords (smaller context, limited per keyword)
    for keyword in DEBT_KEYWORDS_GENERAL:
        idx = 0
        count = 0
        while idx < len(content_lower) and count < 3:
            pos = content_lower.find(keyword, idx)
            if pos == -1:
                break
            add_section(pos, context_before=2000, context_after=5000)
            idx = pos + len(keyword)
            count += 1

    # Pass 3: JV/VIE keywords
    for keyword in JV_VIE_KEYWORDS:
        idx = 0
        count = 0
        while idx < len(content_lower) and count < 2:
            pos = content_lower.find(keyword, idx)
            if pos == -1:
                break
            add_section(pos, context_before=1500, context_after=4000)
            idx = pos + len(keyword)
            count += 1

    if not sections:
        return content[:max_chars]

    # Combine sections
    combined = "\n\n--- DEBT SECTION ---\n\n".join(sections[:15])
    return combined[:max_chars]


# =============================================================================
# VALIDATION HELPERS
# =============================================================================

def validate_extraction_structure(extraction: dict) -> tuple[bool, list[str]]:
    """
    Validate Basic Extraction Structure
    ====================================

    Checks that an LLM extraction result has the required structure.

    CHECKS
    ------
    1. Has 'entities' key with non-empty list
    2. Has 'debt_instruments' key with list
    3. Has at least one 'holdco' entity

    PARAMETERS
    ----------
    extraction : dict
        Extraction result from LLM

    RETURNS
    -------
    tuple[bool, list[str]]
        (is_valid, list of error messages)

    EXAMPLE
    -------
        is_valid, errors = validate_extraction_structure(extraction)
        if not is_valid:
            print(f"Validation failed: {errors}")
    """
    errors = []

    if 'entities' not in extraction:
        errors.append("Missing 'entities' key")
    elif not isinstance(extraction['entities'], list):
        errors.append("'entities' must be a list")
    elif len(extraction['entities']) == 0:
        errors.append("'entities' list is empty")

    if 'debt_instruments' not in extraction:
        errors.append("Missing 'debt_instruments' key")
    elif not isinstance(extraction['debt_instruments'], list):
        errors.append("'debt_instruments' must be a list")

    # Check for holdco
    if 'entities' in extraction and isinstance(extraction['entities'], list):
        has_holdco = any(e.get('entity_type') == 'holdco' for e in extraction['entities'])
        if not has_holdco:
            errors.append("No 'holdco' entity found")

    return len(errors) == 0, errors


def validate_entity_references(extraction: dict) -> tuple[bool, list[str]]:
    """
    Validate Entity References
    ==========================

    Checks that all entity references (parents, issuers, guarantors) exist.

    CHECKS
    ------
    1. All parent_name references match existing entities
    2. All issuer_name references match existing entities
    3. All guarantor_names match existing entities

    PARAMETERS
    ----------
    extraction : dict
        Extraction result from LLM

    RETURNS
    -------
    tuple[bool, list[str]]
        (is_valid, list of error messages)
    """
    errors = []
    entities = extraction.get('entities', [])
    entity_names = {e.get('name', '').lower() for e in entities if e.get('name')}

    # Check parent references
    for entity in entities:
        for owner in entity.get('owners', []):
            parent_name = owner.get('parent_name', '').lower()
            if parent_name and parent_name not in entity_names:
                errors.append(f"Entity '{entity.get('name')}' references unknown parent")

    # Check issuer references
    for debt in extraction.get('debt_instruments', []):
        issuer_name = debt.get('issuer_name', '').lower()
        if issuer_name and issuer_name not in entity_names:
            errors.append(f"Debt '{debt.get('name')}' references unknown issuer")

    # Check guarantor references
    for debt in extraction.get('debt_instruments', []):
        for guarantor in debt.get('guarantor_names', []):
            if guarantor.lower() not in entity_names:
                errors.append(f"Debt '{debt.get('name')}' references unknown guarantor")

    return len(errors) == 0, errors


def validate_debt_amounts(
    extraction: dict,
    max_total_cents: int = 200_000_000_000_000
) -> tuple[bool, list[str]]:
    """
    Validate Debt Amounts
    =====================

    Checks that debt amounts are reasonable (not negative, not too large).

    PARAMETERS
    ----------
    extraction : dict
        Extraction result from LLM
    max_total_cents : int
        Maximum reasonable total debt in cents ($2T default)

    RETURNS
    -------
    tuple[bool, list[str]]
        (is_valid, list of error messages)
    """
    errors = []
    total_debt = 0

    for debt in extraction.get('debt_instruments', []):
        for field in ['outstanding', 'principal', 'commitment']:
            amount = debt.get(field)
            if amount is not None:
                if not isinstance(amount, (int, float)):
                    errors.append(f"Debt '{debt.get('name')}' has non-numeric {field}")
                elif amount < 0:
                    errors.append(f"Debt '{debt.get('name')}' has negative {field}")
                elif amount > max_total_cents:
                    errors.append(f"Debt '{debt.get('name')}' has unreasonably large {field}")
                else:
                    total_debt += amount
                break

    if total_debt > max_total_cents:
        errors.append(f"Total debt exceeds reasonable maximum")

    return len(errors) == 0, errors
