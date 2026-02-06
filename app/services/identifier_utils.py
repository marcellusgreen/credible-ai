"""
Bond Identifier Utilities

Utilities for working with bond identifiers (CUSIP, ISIN, FIGI) and
entity/debt name matching.

CUSIP: Committee on Uniform Securities Identification Procedures
- 9 characters: 6-char issuer + 2-char issue + 1 check digit
- US/Canada securities

ISIN: International Securities Identification Number
- 12 characters: 2-char country + 9-char NSIN + 1 check digit
- For US bonds: US + 9-digit CUSIP + check digit
"""

from difflib import SequenceMatcher
from typing import Optional
from uuid import UUID


def calculate_cusip_check_digit(base: str) -> str:
    """
    Calculate CUSIP check digit using Luhn algorithm variant.

    The CUSIP check digit is calculated by:
    1. Converting characters to numbers (0-9 stay as is, A-Z become 10-35, * = 36, @ = 37, # = 38)
    2. Doubling every second digit
    3. If doubled value > 9, subtract 9
    4. Sum all digits, check digit = (10 - (sum mod 10)) mod 10

    Args:
        base: First 8 characters of CUSIP

    Returns:
        Single digit check character
    """
    if len(base) != 8:
        raise ValueError("CUSIP base must be 8 characters")

    def char_to_num(c: str) -> int:
        if c.isdigit():
            return int(c)
        elif c.isalpha():
            return ord(c.upper()) - ord("A") + 10
        elif c == "*":
            return 36
        elif c == "@":
            return 37
        elif c == "#":
            return 38
        else:
            raise ValueError(f"Invalid CUSIP character: {c}")

    total = 0
    for i, char in enumerate(base):
        value = char_to_num(char)
        if i % 2 == 1:  # Double every second position (0-indexed)
            value *= 2
        # Sum the digits of the value
        total += value // 10 + value % 10

    check = (10 - (total % 10)) % 10
    return str(check)


def validate_cusip(cusip: str) -> bool:
    """
    Validate a CUSIP identifier.

    Args:
        cusip: 9-character CUSIP

    Returns:
        True if valid, False otherwise
    """
    if not cusip or len(cusip) != 9:
        return False

    try:
        base = cusip[:8]
        check = cusip[8]
        expected = calculate_cusip_check_digit(base)
        return check == expected
    except (ValueError, IndexError):
        return False


def calculate_isin_check_digit(base: str) -> str:
    """
    Calculate ISIN check digit using the Luhn algorithm (modulus 10 "double-add-double").

    ISIN format: 2-letter country code + 9-char NSIN + 1 check digit
    For US bonds: US + 9-digit CUSIP + check digit

    The algorithm:
    1. Convert letters to numbers (A=10, B=11, ..., Z=35)
    2. Starting from the rightmost digit, double every second digit
    3. If doubling produces a number > 9, add the digits (or subtract 9)
    4. Sum all the digits
    5. Check digit = (10 - (sum mod 10)) mod 10

    Args:
        base: 11 character string (country code + NSIN without check digit)

    Returns:
        Single digit check character
    """
    if len(base) != 11:
        raise ValueError("ISIN base must be 11 characters")

    # Convert letters to numbers (A=10, B=11, ..., Z=35)
    def char_to_digits(c: str) -> str:
        if c.isdigit():
            return c
        return str(ord(c.upper()) - ord("A") + 10)

    # Convert to numeric string (letters become 2-digit numbers)
    numeric = "".join(char_to_digits(c) for c in base)

    # Now apply Luhn algorithm
    # The key insight: we need to double alternate digits starting from
    # the position that would be second-to-last in the final ISIN (with check digit)

    # The numeric string length varies (letters expand to 2 digits)
    # For Luhn, we need to determine which digits to double based on final parity

    # ISIN Luhn: Starting from the rightmost character of the converted string,
    # multiply odd-positioned digits by 2 (1st, 3rd, 5th from right, 0-indexed: 0, 2, 4...)
    # But we're computing check digit, so we need to account for that.

    # Standard approach: multiply every second digit from right, starting from the second position
    digits = [int(d) for d in numeric]
    n = len(digits)

    # Since we're computing check digit, the final number string will have one more digit
    # So we need to double digits at positions that will be at odd positions (1, 3, 5, ...)
    # in the final string. This means even positions (0, 2, 4, ...) in our current string.

    total = 0
    for i, d in enumerate(reversed(digits)):
        # i is the position from right (0, 1, 2, ...)
        # For ISIN with check digit, we double positions 1, 3, 5, ... from right (odd i)
        # But since check digit isn't added yet, we double at even i (0, 2, 4, ...)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d

    check = (10 - (total % 10)) % 10
    return str(check)


def validate_isin(isin: str) -> bool:
    """
    Validate an ISIN identifier.

    Args:
        isin: 12-character ISIN

    Returns:
        True if valid, False otherwise
    """
    if not isin or len(isin) != 12:
        return False

    # Check country code is letters
    if not isin[:2].isalpha():
        return False

    try:
        base = isin[:11]
        check = isin[11]
        expected = calculate_isin_check_digit(base)
        return check == expected
    except (ValueError, IndexError):
        return False


def cusip_to_isin(cusip: str, country: str = "US") -> Optional[str]:
    """
    Convert a 9-character CUSIP to a 12-character ISIN.

    Args:
        cusip: 9-character CUSIP
        country: 2-letter country code (default: US)

    Returns:
        12-character ISIN or None if invalid input
    """
    if not cusip or len(cusip) != 9:
        return None

    # Clean and validate
    cusip = cusip.upper().strip()
    country = country.upper().strip()

    if len(country) != 2 or not country.isalpha():
        return None

    # Build base (country + CUSIP)
    base = country + cusip

    # Calculate check digit
    try:
        check = calculate_isin_check_digit(base)
        return base + check
    except ValueError:
        return None


def isin_to_cusip(isin: str) -> Optional[str]:
    """
    Extract CUSIP from a US ISIN.

    Args:
        isin: 12-character ISIN starting with "US"

    Returns:
        9-character CUSIP or None if not a US ISIN
    """
    if not isin or len(isin) != 12:
        return None

    if not isin.upper().startswith("US"):
        return None

    return isin[2:11]


def normalize_cusip(cusip: str) -> Optional[str]:
    """
    Normalize a CUSIP to standard format.

    Handles common variations:
    - Lowercase letters
    - Spaces or dashes
    - Missing check digit (recalculates)

    Args:
        cusip: CUSIP in various formats

    Returns:
        Normalized 9-character CUSIP or None if invalid
    """
    if not cusip:
        return None

    # Clean up
    cusip = cusip.upper().strip().replace(" ", "").replace("-", "")

    if len(cusip) == 8:
        # Missing check digit - calculate it
        try:
            check = calculate_cusip_check_digit(cusip)
            return cusip + check
        except ValueError:
            return None
    elif len(cusip) == 9:
        # Validate existing check digit
        if validate_cusip(cusip):
            return cusip
        # Try recalculating check digit
        try:
            check = calculate_cusip_check_digit(cusip[:8])
            return cusip[:8] + check
        except ValueError:
            return None
    else:
        return None


def normalize_isin(isin: str) -> Optional[str]:
    """
    Normalize an ISIN to standard format.

    Args:
        isin: ISIN in various formats

    Returns:
        Normalized 12-character ISIN or None if invalid
    """
    if not isin:
        return None

    # Clean up
    isin = isin.upper().strip().replace(" ", "").replace("-", "")

    if len(isin) == 12 and validate_isin(isin):
        return isin

    return None


# =============================================================================
# ENTITY AND DEBT NAME MATCHING
# =============================================================================

# Common corporate suffixes to strip for matching
CORPORATE_SUFFIXES = [
    ', inc.', ', inc', ' inc.', ' inc',
    ', llc', ' llc', ', l.l.c.', ' l.l.c.',
    ', l.p.', ' l.p.', ', lp', ' lp',
    ', ltd.', ', ltd', ' ltd.', ' ltd',
    ', corp.', ', corp', ' corp.', ' corp',
    ', co.', ', co', ' co.', ' co',
    ', n.v.', ' n.v.', ', nv', ' nv',
    ', b.v.', ' b.v.', ', bv', ' bv',
    ', s.a.', ' s.a.', ', sa', ' sa',
    ', gmbh', ' gmbh',
    ', plc', ' plc',
    ', limited', ' limited',
    ', corporation', ' corporation',
    ', incorporated', ' incorporated',
]


def normalize_entity_name(name: str) -> str:
    """
    Normalize entity name for matching.

    Removes common corporate suffixes, punctuation, and normalizes case.
    Used for matching entity names across different documents.

    For the full-featured normalize_name (with "the " removal, suffix
    replacement, etc.), use app.services.utils.normalize_name instead.

    Args:
        name: Entity name to normalize

    Returns:
        Normalized lowercase name
    """
    if not name:
        return ""
    name = name.lower().strip()
    for suffix in CORPORATE_SUFFIXES:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    return name.replace(',', '').replace('.', '').strip()


def fuzzy_match_entity(
    name: str,
    entity_map: dict[str, UUID],
    threshold: float = 0.85,
) -> Optional[UUID]:
    """
    Find entity ID by fuzzy name matching.

    Args:
        name: Entity name to match
        entity_map: Dict mapping normalized names to entity IDs
        threshold: Minimum similarity ratio (default: 0.85)

    Returns:
        Entity UUID if match found, None otherwise
    """
    if not name:
        return None

    normalized = normalize_entity_name(name)

    # Exact match first
    if normalized in entity_map:
        return entity_map[normalized]

    # Try original lowercase
    name_lower = name.lower().strip()
    if name_lower in entity_map:
        return entity_map[name_lower]

    # Fuzzy match
    best_ratio = 0.0
    best_match = None
    for key, entity_id in entity_map.items():
        ratio = SequenceMatcher(None, normalized, key).ratio()
        if ratio > best_ratio and ratio >= threshold:
            best_ratio = ratio
            best_match = entity_id

    return best_match


def fuzzy_match_debt_name(
    name1: str,
    name2: str,
    threshold: float = 0.6,
) -> bool:
    """
    Check if two debt instrument names are similar enough to match.

    Uses substring matching and SequenceMatcher for fuzzy comparison.

    Args:
        name1: First debt name
        name2: Second debt name
        threshold: Minimum similarity ratio (default: 0.6)

    Returns:
        True if names match
    """
    if not name1 or not name2:
        return False

    name1 = name1.lower().strip()
    name2 = name2.lower().strip()

    # Exact match
    if name1 == name2:
        return True

    # Substring match
    if name1 in name2 or name2 in name1:
        return True

    # Fuzzy match
    ratio = SequenceMatcher(None, name1, name2).ratio()
    return ratio >= threshold


def build_entity_map(entities: list) -> dict[str, UUID]:
    """
    Build a name->ID lookup map from entity list.

    Creates entries for both normalized and lowercase original names.

    Args:
        entities: List of Entity objects with 'id' and 'name' attributes

    Returns:
        Dict mapping normalized names to entity IDs
    """
    entity_map = {}
    for entity in entities:
        if entity.name:
            # Add normalized version
            entity_map[normalize_entity_name(entity.name)] = entity.id
            # Add lowercase original
            entity_map[entity.name.lower().strip()] = entity.id
    return entity_map


# =============================================================================
# BOND IDENTIFIER EXTRACTION
# =============================================================================


def extract_identifiers_from_text(text: str) -> dict[str, set[str]]:
    """
    Extract potential CUSIP and ISIN identifiers from text.

    Useful for parsing SEC filings to find bond identifiers.

    Args:
        text: Text content to search

    Returns:
        Dict with 'cusips' and 'isins' sets
    """
    import re

    results = {"cusips": set(), "isins": set()}

    if not text:
        return results

    # ISIN pattern: 2 letters + 9 alphanumeric + 1 digit
    isin_pattern = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b")

    # CUSIP pattern: 9 alphanumeric (must have at least one letter to avoid false positives)
    # More specific: 6 chars (some letters) + 2 chars + 1 check
    cusip_pattern = re.compile(r"\b([0-9A-Z]{6}[0-9A-Z]{2}[0-9])\b")

    # Find ISINs
    for match in isin_pattern.finditer(text):
        isin = match.group(1)
        if validate_isin(isin):
            results["isins"].add(isin)

    # Find CUSIPs (but not those that are part of ISINs)
    for match in cusip_pattern.finditer(text):
        cusip = match.group(1)
        # Skip if it's just numbers (too many false positives)
        if cusip.isdigit():
            continue
        # Skip if it looks like it's part of an ISIN we already found
        is_part_of_isin = any(isin[2:11] == cusip for isin in results["isins"])
        if not is_part_of_isin:
            # Validate or normalize
            normalized = normalize_cusip(cusip)
            if normalized:
                results["cusips"].add(normalized)

    return results


# Test functions
if __name__ == "__main__":
    # Test CUSIP validation
    print("Testing CUSIP validation:")
    test_cusips = [
        "037833100",  # AAPL
        "594918104",  # MSFT
        "38141G104",  # GOOGL
        "INVALID99",  # Invalid
    ]
    for cusip in test_cusips:
        valid = validate_cusip(cusip)
        print(f"  {cusip}: {'Valid' if valid else 'Invalid'}")

    print("\nTesting CUSIP to ISIN conversion:")
    for cusip in test_cusips[:3]:
        isin = cusip_to_isin(cusip)
        valid = validate_isin(isin) if isin else False
        print(f"  {cusip} -> {isin} ({'Valid' if valid else 'Invalid'})")

    print("\nTesting identifier extraction:")
    sample_text = """
    The securities are registered under CUSIP 037833EQ9 and ISIN US037833EQ92.
    Additional notes carry CUSIP 037833CH1 (ISIN US037833CH12).
    """
    found = extract_identifiers_from_text(sample_text)
    print(f"  CUSIPs found: {found['cusips']}")
    print(f"  ISINs found: {found['isins']}")
