"""
DebtStack Core Utilities
========================

This module provides shared utility functions used across all extraction services.
It consolidates common functionality to avoid duplication and ensure consistency.

MODULES OVERVIEW
----------------
This file (utils.py) contains:
    - JSON parsing from LLM responses
    - Entity name normalization
    - Date parsing utilities
    - Generic section extraction

For filing-specific utilities, see extraction_utils.py:
    - SEC filing HTML/XBRL cleaning
    - Filing content combining
    - Debt section extraction
    - LLM cost tracking

USAGE
-----
    from app.services.utils import (
        parse_json_robust,    # Parse JSON from LLM output
        normalize_name,       # Normalize entity names for matching
        parse_date,           # Parse date strings to date objects
        extract_sections,     # Extract sections by keywords
    )

    # Also re-exports from extraction_utils for convenience:
    from app.services.utils import (
        clean_filing_html,    # Clean SEC filing HTML
        combine_filings,      # Combine multiple filings
    )
"""

import json
import re
from datetime import date, datetime
from typing import Optional

# Re-export from extraction_utils for backwards compatibility
from app.services.extraction_utils import (
    clean_filing_html,
    combine_filings,
    extract_debt_sections,
    truncate_content,
    calculate_cost,
    ModelTier,
    LLMUsage,
)

__all__ = [
    # Core utilities (defined here)
    'parse_json_robust',
    'normalize_name',
    'parse_date',
    'extract_sections',
    'clean_html',
    # Re-exported from extraction_utils
    'clean_filing_html',
    'combine_filings',
    'extract_debt_sections',
    'truncate_content',
    'calculate_cost',
    'ModelTier',
    'LLMUsage',
]


# =============================================================================
# JSON PARSING
# =============================================================================

def parse_json_robust(content: str) -> dict:
    """
    Parse JSON from LLM Response
    ============================

    Robustly extracts and parses JSON from LLM output, handling common issues
    that occur when models generate JSON responses.

    STEPS
    -----
    1. Try direct JSON.loads() on the content
    2. Extract JSON from markdown code blocks (```json ... ```)
    3. Find JSON object pattern ({...}) in text
    4. Clean common issues:
       - Remove JavaScript-style comments (// and /* */)
       - Remove trailing commas before } or ]
       - Fix unquoted keys
       - Replace single quotes with double quotes
    5. Attempt to fix truncated JSON by closing brackets

    HANDLES
    -------
    - Markdown code blocks: ```json {...} ```
    - Trailing commas: {"a": 1,} -> {"a": 1}
    - Single quotes: {'a': 'b'} -> {"a": "b"}
    - Unquoted keys: {a: 1} -> {"a": 1}
    - Comments: {"a": 1 // comment} -> {"a": 1}
    - Truncated JSON: {"a": [1,2 -> {"a": [1,2]}
    - List wrapping: [{"a": 1}] -> {"a": 1}

    PARAMETERS
    ----------
    content : str
        Raw LLM response text that may contain JSON

    RETURNS
    -------
    dict
        Parsed JSON as a dictionary

    RAISES
    ------
    ValueError
        If JSON cannot be parsed after all cleanup attempts

    EXAMPLE
    -------
        response = '''Here is the data:
        ```json
        {"name": "Test", "value": 123,}
        ```
        '''
        data = parse_json_robust(response)
        # Returns: {"name": "Test", "value": 123}
    """
    def ensure_dict(result):
        """Ensure result is a dict, unwrap single-element lists."""
        if isinstance(result, dict):
            return result
        if isinstance(result, list) and len(result) >= 1 and isinstance(result[0], dict):
            return result[0]
        raise ValueError(f"Expected dict but got {type(result)}: {str(result)[:200]}")

    # Step 1: Try direct parse
    try:
        return ensure_dict(json.loads(content))
    except json.JSONDecodeError:
        pass

    # Step 2: Extract from markdown code block
    code_block = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', content)
    if code_block:
        try:
            return ensure_dict(json.loads(code_block.group(1)))
        except json.JSONDecodeError:
            content = code_block.group(1)

    # Step 3: Find JSON object in text
    json_match = re.search(r'\{[\s\S]*\}', content)
    if json_match:
        content = json_match.group(0)

    # Step 4: Clean common issues
    cleaned = content
    cleaned = re.sub(r'//.*?(?=\n|$)', '', cleaned)  # Remove // comments
    cleaned = re.sub(r'/\*[\s\S]*?\*/', '', cleaned)  # Remove /* */ comments
    cleaned = re.sub(r',(\s*[}\]])', r'\1', cleaned)  # Remove trailing commas

    try:
        return ensure_dict(json.loads(cleaned))
    except json.JSONDecodeError:
        pass

    # Try fixing unquoted keys
    cleaned_keys = re.sub(r'(?<=[{,\s])(\w+)(?=\s*:)', r'"\1"', cleaned)
    try:
        return ensure_dict(json.loads(cleaned_keys))
    except json.JSONDecodeError:
        pass

    # Try replacing single quotes
    cleaned_quotes = cleaned.replace("'", '"')
    try:
        return ensure_dict(json.loads(cleaned_quotes))
    except json.JSONDecodeError:
        pass

    # Step 5: Fix truncated JSON
    open_braces = cleaned.count('{') - cleaned.count('}')
    open_brackets = cleaned.count('[') - cleaned.count(']')

    if open_braces > 0 or open_brackets > 0:
        fixed = cleaned.rstrip().rstrip(',')

        # Find last complete object
        last_complete = max(fixed.rfind('},'), fixed.rfind('],'))
        if last_complete > len(fixed) // 2:
            fixed = fixed[:last_complete + 1]

        # Close remaining brackets
        open_braces = fixed.count('{') - fixed.count('}')
        open_brackets = fixed.count('[') - fixed.count(']')
        fixed = fixed.rstrip().rstrip(',')
        fixed += ']' * open_brackets + '}' * open_braces

        try:
            return ensure_dict(json.loads(fixed))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse JSON: {content[:500]}...")


# =============================================================================
# NAME NORMALIZATION
# =============================================================================

def normalize_name(name: str) -> str:
    """
    Normalize Entity Name for Matching
    ===================================

    Converts entity names to a normalized form for fuzzy matching.
    Used to match entities across different sources (Exhibit 21, indentures,
    credit agreements) where names may have minor variations.

    STEPS
    -----
    1. Convert to lowercase
    2. Strip leading/trailing whitespace
    3. Normalize multiple spaces to single space
    4. Remove "the " prefix
    5. Normalize common suffix variations:
       - ", Inc." / ", Inc" / " Inc." -> " inc"
       - ", LLC" / ", L.L.C." -> " llc"
       - ", Ltd." / ", Limited" -> " ltd"
       - ", Corp." / ", Corporation" -> " corp"
       - ", L.P." / ", LP" -> " lp"
    6. Remove trailing periods

    PARAMETERS
    ----------
    name : str
        Entity name to normalize

    RETURNS
    -------
    str
        Normalized name for comparison

    EXAMPLES
    --------
        normalize_name("The ABC Corporation, Inc.")
        # Returns: "abc corporation inc"

        normalize_name("XYZ Holdings, L.L.C.")
        # Returns: "xyz holdings llc"

        normalize_name("  Foo  Bar  Ltd.  ")
        # Returns: "foo bar ltd"
    """
    if not name:
        return ""

    # Lowercase and strip
    normalized = name.lower().strip()

    # Normalize whitespace
    normalized = re.sub(r'\s+', ' ', normalized)

    # Remove "the " prefix
    if normalized.startswith("the "):
        normalized = normalized[4:]

    # Normalize suffixes (order matters - check longer ones first)
    suffix_map = [
        (', corporation', ' corp'),
        (' corporation', ' corp'),
        (', limited', ' ltd'),
        (' limited', ' ltd'),
        (', l.l.c.', ' llc'),
        (' l.l.c.', ' llc'),
        (', inc.', ' inc'),
        (', inc', ' inc'),
        (' inc.', ' inc'),
        (', llc', ' llc'),
        (', ltd.', ' ltd'),
        (', ltd', ' ltd'),
        (' ltd.', ' ltd'),
        (', corp.', ' corp'),
        (', corp', ' corp'),
        (' corp.', ' corp'),
        (', co.', ' co'),
        (' co.', ' co'),
        (', l.p.', ' lp'),
        (' l.p.', ' lp'),
        (', lp', ' lp'),
    ]

    for old, new in suffix_map:
        if normalized.endswith(old):
            normalized = normalized[:-len(old)] + new
            break

    return normalized.rstrip('.')


# =============================================================================
# DATE PARSING
# =============================================================================

def parse_date(date_str: Optional[str]) -> Optional[date]:
    """
    Parse Date String to Date Object
    =================================

    Parses various date string formats commonly found in SEC filings
    into Python date objects.

    SUPPORTED FORMATS
    -----------------
    - ISO format: "2025-12-31", "2025/12/31"
    - US format: "12/31/2025", "12-31-2025"
    - Text format: "December 31, 2025", "Dec 31, 2025"
    - Compact: "20251231"

    PARAMETERS
    ----------
    date_str : str or None
        Date string to parse

    RETURNS
    -------
    date or None
        Parsed date object, or None if parsing fails

    EXAMPLES
    --------
        parse_date("2025-12-31")
        # Returns: date(2025, 12, 31)

        parse_date("December 31, 2025")
        # Returns: date(2025, 12, 31)

        parse_date(None)
        # Returns: None
    """
    if not date_str:
        return None

    date_str = str(date_str).strip()

    # Try common formats
    formats = [
        "%Y-%m-%d",      # 2025-12-31
        "%Y/%m/%d",      # 2025/12/31
        "%m/%d/%Y",      # 12/31/2025
        "%m-%d-%Y",      # 12-31-2025
        "%B %d, %Y",     # December 31, 2025
        "%b %d, %Y",     # Dec 31, 2025
        "%d %B %Y",      # 31 December 2025
        "%d %b %Y",      # 31 Dec 2025
        "%Y%m%d",        # 20251231
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue

    # Try extracting just the year for partial dates like "2025"
    year_match = re.match(r'^(\d{4})$', date_str)
    if year_match:
        return date(int(year_match.group(1)), 12, 31)

    return None


# =============================================================================
# SECTION EXTRACTION
# =============================================================================

def extract_sections(
    content: str,
    keywords: list[str],
    context_before: int = 2000,
    context_after: int = 5000,
    max_chars: int = 50000,
    max_per_keyword: int = 3,
) -> str:
    """
    Extract Sections by Keywords
    ============================

    Searches content for keywords and extracts surrounding context.
    Used to pull relevant sections from large documents.

    STEPS
    -----
    1. Convert content to lowercase for matching
    2. For each keyword, find all occurrences
    3. Extract context window around each match
    4. Skip overlapping sections
    5. Combine sections up to max_chars limit

    PARAMETERS
    ----------
    content : str
        Full document content to search
    keywords : list[str]
        Keywords to search for (case-insensitive)
    context_before : int
        Characters to include before each match (default: 2000)
    context_after : int
        Characters to include after each match (default: 5000)
    max_chars : int
        Maximum total characters to return (default: 50000)
    max_per_keyword : int
        Maximum matches per keyword (default: 3)

    RETURNS
    -------
    str
        Extracted sections combined with separators

    EXAMPLE
    -------
        content = "...long document with debt information..."
        sections = extract_sections(
            content,
            keywords=["senior notes", "credit facility"],
            context_before=1000,
            context_after=3000,
        )
    """
    content_lower = content.lower()
    sections = []
    positions = []  # Track (start, end) to avoid overlaps

    def add_section(pos: int) -> None:
        start = max(0, pos - context_before)
        end = min(len(content), pos + context_after)

        # Check for overlap
        for existing_start, existing_end in positions:
            if start < existing_end and end > existing_start:
                return

        positions.append((start, end))
        sections.append(content[start:end])

    for keyword in keywords:
        idx = 0
        count = 0
        while idx < len(content_lower) and count < max_per_keyword:
            pos = content_lower.find(keyword.lower(), idx)
            if pos == -1:
                break
            add_section(pos)
            idx = pos + len(keyword)
            count += 1

    if not sections:
        return content[:max_chars]

    combined = "\n\n--- SECTION ---\n\n".join(sections)
    return combined[:max_chars]


# =============================================================================
# SIMPLE HTML CLEANING
# =============================================================================

def clean_html(content: str) -> str:
    """
    Simple HTML Tag Removal
    =======================

    Removes HTML tags and decodes common entities.
    For full SEC filing cleaning, use clean_filing_html() instead.

    STEPS
    -----
    1. Remove all HTML tags (<...>)
    2. Decode common HTML entities (&nbsp;, &amp;, etc.)
    3. Normalize whitespace

    PARAMETERS
    ----------
    content : str
        HTML content to clean

    RETURNS
    -------
    str
        Plain text with HTML removed

    NOTE
    ----
    For SEC filings with iXBRL, use clean_filing_html() which handles
    XBRL-specific elements and hidden sections.
    """
    content = re.sub(r'<[^>]+>', ' ', content)
    content = re.sub(r'&nbsp;', ' ', content)
    content = re.sub(r'&amp;', '&', content)
    content = re.sub(r'&lt;', '<', content)
    content = re.sub(r'&gt;', '>', content)
    content = re.sub(r'\s+', ' ', content)
    return content.strip()
