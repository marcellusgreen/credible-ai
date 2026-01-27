"""
Shared utilities for extraction services.

This module consolidates common functionality used across:
- extraction.py
- tiered_extraction.py
- iterative_extraction.py
- financial_extraction.py

Includes:
- Filing content cleaning and combining
- LLM model cost tracking
- Extraction validation helpers
- Database session utilities
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


# =============================================================================
# MODEL CONFIGURATION
# =============================================================================

class ModelTier(Enum):
    """Available LLM model tiers for extraction."""
    GEMINI_FLASH = "gemini-2.0-flash"
    GEMINI_PRO = "gemini-2.5-pro"
    CLAUDE_SONNET = "claude-sonnet"
    CLAUDE_OPUS = "claude-opus"
    DEEPSEEK = "deepseek"


# Cost per 1M tokens (input, output)
MODEL_COSTS = {
    ModelTier.GEMINI_FLASH: (0.10, 0.40),
    ModelTier.GEMINI_PRO: (1.25, 10.00),
    ModelTier.CLAUDE_SONNET: (3.00, 15.00),
    ModelTier.CLAUDE_OPUS: (15.00, 75.00),
    ModelTier.DEEPSEEK: (0.27, 1.10),
}


def calculate_cost(model: ModelTier, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost for an LLM call in dollars."""
    input_cost, output_cost = MODEL_COSTS.get(model, (0, 0))
    return (input_tokens * input_cost + output_tokens * output_cost) / 1_000_000


@dataclass
class LLMUsage:
    """Track LLM usage across multiple calls."""
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    @property
    def cost(self) -> float:
        """Calculate total cost in dollars."""
        for tier in ModelTier:
            if tier.value in self.model.lower():
                return calculate_cost(tier, self.input_tokens, self.output_tokens)
        return 0.0

    def add_call(self, input_tokens: int, output_tokens: int) -> None:
        """Record a new LLM call."""
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.calls += 1


# =============================================================================
# FILING CONTENT CLEANING
# =============================================================================

def clean_filing_html(content: str) -> str:
    """
    Clean HTML/XBRL content from SEC filings to extract readable text.

    Handles:
    - XML declarations and DOCTYPE
    - Script and style blocks
    - iXBRL hidden sections
    - XBRL element extraction
    - HTML entity decoding
    - Whitespace normalization
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

    # Remove XBRL hidden sections
    content = re.sub(r'<ix:hidden[^>]*>[\s\S]*?</ix:hidden>', '', content, flags=re.IGNORECASE)

    # Extract text from XBRL elements
    content = re.sub(r'<ix:[^>]*>([^<]*)</ix:[^>]*>', r'\1', content)

    # Remove remaining HTML/XML tags
    content = re.sub(r'<[^>]+>', ' ', content)

    # Decode HTML entities
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
    """Truncate content to max characters, preferring sentence boundaries."""
    if len(content) <= max_chars:
        return content

    # Try to cut at sentence boundary
    truncated = content[:max_chars]
    last_period = truncated.rfind('. ')
    if last_period > max_chars * 0.8:
        return truncated[:last_period + 1]
    return truncated


# =============================================================================
# FILING CONTENT COMBINING
# =============================================================================

# Priority order for combining filings
FILING_PRIORITY = [
    '10-K', '10-Q',              # Main filings
    'exhibit_21',                # Subsidiary list
    '8-K',                       # Material events
    'indenture', 'credit_agreement',  # Debt documents
]


def combine_filings(
    filings: dict[str, str],
    max_chars: int = 300_000,
    include_headers: bool = True
) -> str:
    """
    Combine multiple SEC filings into a single context string.

    Prioritizes filings in order: 10-K > 10-Q > Exhibit 21 > 8-K > debt documents.
    Includes section headers for LLM context.

    Args:
        filings: Dict mapping filing type to content
        max_chars: Maximum total characters
        include_headers: Whether to include section headers

    Returns:
        Combined filing content string
    """
    if not filings:
        return ""

    # Sort filings by priority
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

        # Clean and truncate content
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
    "debt - ",           # "Note 9 - Debt -" style
    "long-term debt -",
    "debt and credit",
    "borrowings -",
    "notes and debentures",
    "senior notes due",  # Specific instrument names
    "% notes due",       # e.g., "8.75% Notes due 2030"
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

# Priority 3: JV and complex ownership keywords (for complete structure extraction)
JV_VIE_KEYWORDS = [
    "joint venture",
    "equity method",
    "unconsolidated",
    "variable interest entit",  # matches "entity" and "entities"
    "vie",
    "50% owned",
    "50/50",
    "unrestricted subsidiar",   # matches "subsidiary" and "subsidiaries"
]


def extract_debt_sections(content: str, max_chars: int = 50_000) -> str:
    """
    Extract debt-related sections from filing content.

    Looks for common debt disclosure patterns and extracts surrounding context.
    Prioritizes debt footnotes that list individual instruments.
    Also captures JV/VIE keywords for complete structure extraction.

    Args:
        content: Full filing content
        max_chars: Maximum characters to extract

    Returns:
        Extracted debt-related content
    """
    content_lower = content.lower()
    sections = []
    section_positions = []

    def add_section(pos: int, context_before: int = 2000, context_after: int = 8000) -> None:
        """Add a section around a position, checking for overlap."""
        start = max(0, pos - context_before)
        end = min(len(content), pos + context_after)

        # Check for overlap with existing sections
        for existing_start, existing_end in section_positions:
            if start < existing_end and end > existing_start:
                return  # Overlapping - skip

        section_positions.append((start, end))
        sections.append(content[start:end])

    # First pass: priority keywords (larger context window)
    for keyword in DEBT_KEYWORDS_PRIORITY:
        idx = 0
        while idx < len(content_lower):
            pos = content_lower.find(keyword, idx)
            if pos == -1:
                break
            add_section(pos, context_before=1000, context_after=10000)
            idx = pos + len(keyword)

    # Second pass: general keywords (smaller context, limited per keyword)
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

    # Third pass: JV/VIE keywords (important for complete structure)
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
    Validate basic extraction structure.

    Returns:
        Tuple of (is_valid, list of error messages)
    """
    errors = []

    # Check required top-level keys
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

    # Check for holdco entity
    if 'entities' in extraction and isinstance(extraction['entities'], list):
        has_holdco = any(
            e.get('entity_type') == 'holdco'
            for e in extraction['entities']
        )
        if not has_holdco:
            errors.append("No 'holdco' entity found")

    return len(errors) == 0, errors


def validate_entity_references(extraction: dict) -> tuple[bool, list[str]]:
    """
    Validate that all entity references (parents, issuers, guarantors) exist.

    Returns:
        Tuple of (is_valid, list of error messages)
    """
    errors = []

    entities = extraction.get('entities', [])
    entity_names = {e.get('name', '').lower() for e in entities if e.get('name')}

    # Check parent references
    for entity in entities:
        for owner in entity.get('owners', []):
            parent_name = owner.get('parent_name', '').lower()
            if parent_name and parent_name not in entity_names:
                errors.append(f"Entity '{entity.get('name')}' references unknown parent '{owner.get('parent_name')}'")

    # Check issuer references
    for debt in extraction.get('debt_instruments', []):
        issuer_name = debt.get('issuer_name', '').lower()
        if issuer_name and issuer_name not in entity_names:
            errors.append(f"Debt '{debt.get('name')}' references unknown issuer '{debt.get('issuer_name')}'")

    # Check guarantor references
    for debt in extraction.get('debt_instruments', []):
        for guarantor in debt.get('guarantor_names', []):
            if guarantor.lower() not in entity_names:
                errors.append(f"Debt '{debt.get('name')}' references unknown guarantor '{guarantor}'")

    return len(errors) == 0, errors


def validate_debt_amounts(extraction: dict, max_total_cents: int = 200_000_000_000_000) -> tuple[bool, list[str]]:
    """
    Validate debt amounts are reasonable.

    Args:
        extraction: Extraction dict
        max_total_cents: Maximum reasonable total debt in cents ($2T default)

    Returns:
        Tuple of (is_valid, list of error messages)
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
                break  # Only count once per instrument

    if total_debt > max_total_cents:
        errors.append(f"Total debt ({total_debt / 100:.0f} dollars) exceeds reasonable maximum")

    return len(errors) == 0, errors
