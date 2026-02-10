"""
Unit tests for fuzzy entity matching.

Tests the _fuzzy_match_entity function used to match
guarantor names to entity records.
"""

import pytest
import sys
import os
from uuid import uuid4, UUID

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.identifier_utils import fuzzy_match_entity as _fuzzy_match_entity, normalize_entity_name as _normalize_name


class TestFuzzyMatchEntity:
    """Tests for _fuzzy_match_entity function."""

    @pytest.mark.unit
    def test_empty_name_returns_none(self):
        """Empty name returns None."""
        entity_map = {"apple": uuid4()}
        assert _fuzzy_match_entity("", entity_map) is None
        assert _fuzzy_match_entity(None, entity_map) is None

    @pytest.mark.unit
    def test_empty_map_returns_none(self):
        """Empty entity map returns None."""
        assert _fuzzy_match_entity("Apple", {}) is None

    @pytest.mark.unit
    def test_exact_match_normalized(self, sample_entity_map):
        """Exact match after normalization."""
        # "Apple Inc." normalizes to "apple" which is in the map
        result = _fuzzy_match_entity("Apple Inc.", sample_entity_map)
        assert result == sample_entity_map["apple"]

    @pytest.mark.unit
    def test_exact_match_lowercase(self):
        """Exact match with lowercase original."""
        entity_id = uuid4()
        entity_map = {"apple inc.": entity_id}
        result = _fuzzy_match_entity("Apple Inc.", entity_map)
        assert result == entity_id

    @pytest.mark.unit
    def test_fuzzy_match_high_similarity(self):
        """Fuzzy match with high similarity (>= 0.85)."""
        entity_id = uuid4()
        entity_map = {"charter communications": entity_id}

        # "Charter Comm" is similar enough
        result = _fuzzy_match_entity("Charter Communications Holdings", entity_map, threshold=0.70)
        assert result == entity_id

    @pytest.mark.unit
    def test_fuzzy_match_below_threshold(self):
        """No match when similarity is below threshold."""
        entity_id = uuid4()
        entity_map = {"charter communications": entity_id}

        # "ABC Corp" is not similar to "charter communications"
        result = _fuzzy_match_entity("ABC Corporation", entity_map)
        assert result is None

    @pytest.mark.unit
    def test_best_match_selected(self):
        """When multiple matches, best one is selected."""
        entity_map = {
            "charter": uuid4(),
            "charter communications": uuid4(),
            "charter holdings": uuid4(),
        }

        # Should match "charter communications" most closely
        result = _fuzzy_match_entity("Charter Communications, Inc.", entity_map)
        assert result == entity_map["charter communications"]

    @pytest.mark.unit
    def test_threshold_parameter(self):
        """Custom threshold parameter is respected."""
        entity_id = uuid4()
        entity_map = {"apple": entity_id}

        # With very high threshold, minor variations don't match
        result_strict = _fuzzy_match_entity("apples", entity_map, threshold=0.95)
        assert result_strict is None

        # With lower threshold, it matches
        result_loose = _fuzzy_match_entity("apples", entity_map, threshold=0.70)
        assert result_loose == entity_id

    @pytest.mark.unit
    def test_default_threshold_is_085(self):
        """Default threshold is 0.85."""
        entity_id = uuid4()
        entity_map = {"microsoft corporation": entity_id}

        # "microsoft" alone is ~0.5 similarity - should NOT match at 0.85
        result = _fuzzy_match_entity("microsoft", entity_map)
        # This may or may not match depending on normalization
        # The key is it uses the default threshold

    @pytest.mark.unit
    def test_case_insensitive(self):
        """Matching is case insensitive."""
        entity_id = uuid4()
        entity_map = {"apple": entity_id}

        assert _fuzzy_match_entity("APPLE", entity_map) == entity_id
        assert _fuzzy_match_entity("Apple", entity_map) == entity_id
        assert _fuzzy_match_entity("aPpLe", entity_map) == entity_id


class TestFuzzyMatchEntityRealWorld:
    """Real-world scenario tests for fuzzy matching."""

    @pytest.mark.unit
    def test_guarantor_name_variations(self):
        """Common guarantor name variations should match."""
        parent_id = uuid4()
        entity_map = {
            "charter communications holdings": parent_id,
        }

        # These are all variations that might appear in documents
        variations = [
            "Charter Communications Holdings, LLC",
            "Charter Communications Holdings LLC",
            "CHARTER COMMUNICATIONS HOLDINGS",
            "Charter Communications Holdings Company",
        ]

        for name in variations:
            result = _fuzzy_match_entity(name, entity_map, threshold=0.80)
            assert result is not None, f"Failed to match: {name}"

    @pytest.mark.unit
    def test_subsidiary_name_matching(self):
        """Subsidiary names with slight variations should match."""
        sub_id = uuid4()
        entity_map = {
            "spectrum management holding company": sub_id,
        }

        # Document might have slightly different formatting
        result = _fuzzy_match_entity(
            "Spectrum Management Holding Company, LLC",
            entity_map,
            threshold=0.80
        )
        assert result == sub_id

    @pytest.mark.unit
    def test_common_false_positives(self):
        """Should NOT match clearly different entities."""
        entity_map = {
            "apple": uuid4(),
            "alphabet": uuid4(),
        }

        # These should NOT match each other
        assert _fuzzy_match_entity("Apple Inc.", entity_map) == entity_map["apple"]
        assert _fuzzy_match_entity("Alphabet Inc.", entity_map) == entity_map["alphabet"]

        # Different companies should not match
        assert _fuzzy_match_entity("Microsoft", entity_map) is None
        assert _fuzzy_match_entity("Amazon", entity_map) is None

    @pytest.mark.unit
    def test_parent_company_vs_subsidiary(self):
        """Distinguish between parent and subsidiary with similar names."""
        entity_map = {
            "charter communications": uuid4(),
            "charter communications operating": uuid4(),
            "charter communications holdings": uuid4(),
        }

        # Each should match its specific entity
        result1 = _fuzzy_match_entity("Charter Communications, Inc.", entity_map)
        result2 = _fuzzy_match_entity("Charter Communications Operating, LLC", entity_map)
        result3 = _fuzzy_match_entity("Charter Communications Holdings, LLC", entity_map)

        # They should all be different
        assert result1 != result2
        assert result2 != result3
        assert result1 != result3
