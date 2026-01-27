"""
Shared utility functions for extraction services.

Core utilities:
- parse_json_robust: Parse JSON from LLM responses
- normalize_name: Normalize entity names for matching

For filing-related utilities, see extraction_utils.py:
- clean_filing_html: Clean SEC filing HTML/XBRL
- combine_filings: Combine multiple filings
- extract_debt_sections: Extract debt-related content
"""

import json
import re

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
    'parse_json_robust',
    'normalize_name',
    'clean_html',
    'clean_filing_html',
    'combine_filings',
    'extract_debt_sections',
    'truncate_content',
    'calculate_cost',
    'ModelTier',
    'LLMUsage',
]


def parse_json_robust(content: str) -> dict:
    """
    Robustly parse JSON from LLM response, handling common issues:
    - Markdown code blocks
    - Trailing commas
    - Single quotes
    - Unquoted keys
    - Comments
    - Truncated JSON
    - List wrapping (unwrap if single-element list containing dict)
    """
    def ensure_dict(result):
        """Ensure result is a dict, unwrap if it's a list with one dict."""
        if isinstance(result, dict):
            return result
        if isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict):
            return result[0]
        if isinstance(result, list) and len(result) > 0 and isinstance(result[0], dict):
            return result[0]
        raise ValueError(f"Expected dict but got {type(result)}: {str(result)[:200]}")

    # Try direct parse first
    try:
        result = json.loads(content)
        return ensure_dict(result)
    except json.JSONDecodeError:
        pass

    # Try extracting from code block
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', content)
    if json_match:
        try:
            result = json.loads(json_match.group(1))
            return ensure_dict(result)
        except json.JSONDecodeError:
            content = json_match.group(1)

    # Try finding JSON object
    json_match = re.search(r'\{[\s\S]*\}', content)
    if json_match:
        content = json_match.group(0)

    # Clean up common JSON issues
    cleaned = content

    # Remove JavaScript-style comments
    cleaned = re.sub(r'//.*?(?=\n|$)', '', cleaned)
    cleaned = re.sub(r'/\*[\s\S]*?\*/', '', cleaned)

    # Remove trailing commas before } or ]
    cleaned = re.sub(r',(\s*[}\]])', r'\1', cleaned)

    # Try parsing cleaned content
    try:
        result = json.loads(cleaned)
        return ensure_dict(result)
    except json.JSONDecodeError:
        pass

    # Try fixing unquoted keys
    cleaned2 = re.sub(r'(?<=[{,\s])(\w+)(?=\s*:)', r'"\1"', cleaned)
    try:
        result = json.loads(cleaned2)
        return ensure_dict(result)
    except json.JSONDecodeError:
        pass

    # Try replacing single quotes with double quotes
    cleaned3 = cleaned.replace("'", '"')
    try:
        result = json.loads(cleaned3)
        return ensure_dict(result)
    except json.JSONDecodeError:
        pass

    # Last resort: try to fix truncated JSON by closing brackets
    open_braces = cleaned.count('{') - cleaned.count('}')
    open_brackets = cleaned.count('[') - cleaned.count(']')

    if open_braces > 0 or open_brackets > 0:
        fixed = cleaned.rstrip().rstrip(',')

        # Try to fix truncated JSON more aggressively
        # Look for last complete entity/debt object
        last_complete_brace = fixed.rfind('},')
        last_complete_bracket = fixed.rfind('],')

        if last_complete_brace > 0 or last_complete_bracket > 0:
            cut_point = max(last_complete_brace, last_complete_bracket)
            if cut_point > len(fixed) // 2:  # Only if we're keeping most of the content
                fixed = fixed[:cut_point + 1]

        # Recount after potential truncation
        open_braces = fixed.count('{') - fixed.count('}')
        open_brackets = fixed.count('[') - fixed.count(']')

        fixed = fixed.rstrip().rstrip(',')
        fixed += ']' * open_brackets
        fixed += '}' * open_braces
        try:
            result = json.loads(fixed)
            return ensure_dict(result)
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse JSON from response: {content[:1000]}")


def clean_html(content: str) -> str:
    """Strip HTML tags and clean up whitespace."""
    # Remove HTML tags
    content = re.sub(r'<[^>]+>', ' ', content)
    # Remove HTML entities
    content = re.sub(r'&nbsp;', ' ', content)
    content = re.sub(r'&amp;', '&', content)
    content = re.sub(r'&lt;', '<', content)
    content = re.sub(r'&gt;', '>', content)
    # Clean up whitespace
    content = re.sub(r'\s+', ' ', content)
    return content.strip()


def normalize_name(name: str) -> str:
    """
    Normalize entity name for matching.

    Handles:
    - Case insensitivity (ABC Corp -> abc corp)
    - Trailing punctuation (Ltd. -> Ltd)
    - Multiple spaces (Foo  Bar -> Foo Bar)
    - Common suffix variations (Inc. vs Inc, LLC vs L.L.C.)
    - The/the prefix variations
    - Commas before suffixes (Foo, Inc. -> Foo Inc)
    """
    if not name:
        return ""

    # Lowercase and strip whitespace
    normalized = name.lower().strip()

    # Normalize whitespace (multiple spaces -> single space)
    normalized = re.sub(r'\s+', ' ', normalized)

    # Remove "the " prefix if present
    if normalized.startswith("the "):
        normalized = normalized[4:]

    # Normalize common suffix variations (order matters - check longer suffixes first)
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

    # Remove trailing periods
    normalized = normalized.rstrip('.')

    return normalized
