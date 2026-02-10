"""
Unit tests for entity name normalization.

Tests the _normalize_name function used in guarantee extraction
and entity matching across the codebase.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.identifier_utils import normalize_entity_name as _normalize_name


class TestNormalizeName:
    """Tests for _normalize_name function."""

    @pytest.mark.unit
    def test_empty_string(self):
        """Empty string returns empty string."""
        assert _normalize_name("") == ""

    @pytest.mark.unit
    def test_none_returns_empty(self):
        """None returns empty string."""
        assert _normalize_name(None) == ""

    @pytest.mark.unit
    def test_lowercase_conversion(self):
        """Names are converted to lowercase."""
        assert _normalize_name("APPLE") == "apple"
        assert _normalize_name("Apple") == "apple"
        assert _normalize_name("aPpLe") == "apple"

    @pytest.mark.unit
    def test_strip_whitespace(self):
        """Leading/trailing whitespace is removed."""
        assert _normalize_name("  Apple  ") == "apple"
        assert _normalize_name("\tMicrosoft\n") == "microsoft"

    @pytest.mark.unit
    def test_remove_inc_suffix(self):
        """', Inc.' and variations are removed."""
        assert _normalize_name("Apple, Inc.") == "apple"
        assert _normalize_name("Apple, Inc") == "apple"
        assert _normalize_name("Apple Inc.") == "apple"
        assert _normalize_name("Apple Inc") == "apple"

    @pytest.mark.unit
    def test_remove_llc_suffix(self):
        """', LLC' and variations are removed."""
        assert _normalize_name("Charter Communications, LLC") == "charter communications"
        assert _normalize_name("Charter Communications LLC") == "charter communications"

    @pytest.mark.unit
    def test_remove_lp_suffix(self):
        """', L.P.' and variations are removed."""
        assert _normalize_name("Enterprise Products, L.P.") == "enterprise products"
        assert _normalize_name("Enterprise Products L.P.") == "enterprise products"
        assert _normalize_name("Enterprise Products, LP") == "enterprise products"
        assert _normalize_name("Enterprise Products LP") == "enterprise products"

    @pytest.mark.unit
    def test_remove_ltd_suffix(self):
        """', Ltd.' and variations are removed."""
        assert _normalize_name("British Company, Ltd.") == "british company"
        assert _normalize_name("British Company, Ltd") == "british company"
        assert _normalize_name("British Company Ltd.") == "british company"
        assert _normalize_name("British Company Ltd") == "british company"

    @pytest.mark.unit
    def test_remove_corp_suffix(self):
        """', Corp.' and variations are removed."""
        assert _normalize_name("Microsoft Corporation") == "microsoft"  # normalize_entity_name removes Corporation suffix too
        assert _normalize_name("Microsoft, Corp.") == "microsoft"
        assert _normalize_name("Microsoft, Corp") == "microsoft"
        assert _normalize_name("Microsoft Corp.") == "microsoft"
        assert _normalize_name("Microsoft Corp") == "microsoft"

    @pytest.mark.unit
    def test_remove_punctuation(self):
        """Commas and periods are removed from final result."""
        assert _normalize_name("A.B.C. Company") == "abc company"
        assert _normalize_name("Company, Name") == "company name"

    @pytest.mark.unit
    def test_preserves_internal_spaces(self):
        """Internal spaces are preserved."""
        assert _normalize_name("Charter Communications Holdings") == "charter communications holdings"

    @pytest.mark.unit
    def test_real_company_names(self, sample_entity_names):
        """Test with real company name variations."""
        # Apple variations should all normalize similarly
        apple_names = [n for n in sample_entity_names if "apple" in n.lower()]
        normalized = [_normalize_name(n) for n in apple_names]
        assert all(n == "apple" for n in normalized)

        # Microsoft variations
        ms_names = [n for n in sample_entity_names if "microsoft" in n.lower()]
        normalized = [_normalize_name(n) for n in ms_names]
        # Corp suffix removed, Corporation not (it's not in the suffix list)
        assert all("microsoft" in n for n in normalized)


class TestNormalizeNameEdgeCases:
    """Edge case tests for name normalization."""

    @pytest.mark.unit
    def test_only_suffix(self):
        """String that is only a suffix - suffix patterns at end are removed."""
        assert _normalize_name(", Inc.") == ""
        # Note: "LLC" alone doesn't match " llc" suffix pattern, so stays
        assert _normalize_name("LLC") == "llc"
        # But with leading space/comma it's removed
        assert _normalize_name("A, LLC") == "a"

    @pytest.mark.unit
    def test_multiple_suffixes(self):
        """Only the matching suffix at end is removed."""
        # This tests current behavior - only one suffix removed
        result = _normalize_name("Company Inc Corp")
        assert result == "company inc"  # Only Corp removed

    @pytest.mark.unit
    def test_suffix_in_middle(self):
        """Suffix pattern in middle of name is not removed."""
        result = _normalize_name("Inc Industries Corp")
        assert "inc industries" in result

    @pytest.mark.unit
    def test_unicode_characters(self):
        """Unicode characters are handled."""
        assert _normalize_name("Société Générale") == "société générale"

    @pytest.mark.unit
    def test_numbers_preserved(self):
        """Numbers in names are preserved."""
        assert _normalize_name("3M Company") == "3m company"
        assert _normalize_name("21st Century Fox") == "21st century fox"

    @pytest.mark.unit
    def test_ampersand(self):
        """Ampersand is preserved."""
        assert _normalize_name("AT&T Inc.") == "at&t"
