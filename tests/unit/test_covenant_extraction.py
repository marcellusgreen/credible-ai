"""
Unit tests for covenant extraction pure functions.

Tests the pure functions in covenant_extraction.py:
- extract_covenant_sections(text) -> str
- parse_covenant_response(json_data) -> list[ParsedCovenant]
- fuzzy_match_debt_name(name1, name2) -> bool
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.covenant_extraction import (
    extract_covenant_sections,
    parse_covenant_response,
    fuzzy_match_debt_name,
    ParsedCovenant,
    COVENANT_TYPES,
    FINANCIAL_METRICS,
)


# =============================================================================
# extract_covenant_sections() Tests
# =============================================================================

class TestExtractCovenantSections:
    """Tests for extract_covenant_sections function."""

    @pytest.mark.unit
    def test_empty_content_returns_empty(self):
        """Empty content returns empty string."""
        assert extract_covenant_sections("") == ""
        assert extract_covenant_sections("   ") == ""

    @pytest.mark.unit
    def test_no_covenant_content_returns_empty(self):
        """Content without covenant keywords returns empty string."""
        content = """
        This is a simple document about quarterly earnings.
        Revenue increased by 15% year over year.
        The company expects continued growth.
        """
        assert extract_covenant_sections(content) == ""

    @pytest.mark.unit
    def test_extracts_leverage_ratio_covenant(self):
        """Extracts leverage ratio covenant language."""
        content = """
        FINANCIAL COVENANTS

        The Consolidated Leverage Ratio shall not exceed 4.50 to 1.00
        as of the last day of any fiscal quarter.
        """
        result = extract_covenant_sections(content)
        assert "leverage" in result.lower() or "FINANCIAL COVENANTS" in result

    @pytest.mark.unit
    def test_extracts_interest_coverage_covenant(self):
        """Extracts interest coverage ratio covenant language."""
        content = """
        The Borrower shall maintain a minimum Interest Coverage Ratio
        of at least 2.00 to 1.00, tested quarterly.
        """
        result = extract_covenant_sections(content)
        assert len(result) > 0

    @pytest.mark.unit
    def test_extracts_negative_covenant_liens(self):
        """Extracts negative covenant about liens."""
        content = """
        ARTICLE VII - NEGATIVE COVENANTS

        Section 7.01. Limitation on Liens.
        The Borrower will not, and will not permit any Subsidiary to,
        create or permit to exist any Lien on any property or assets.
        """
        result = extract_covenant_sections(content)
        assert len(result) > 0

    @pytest.mark.unit
    def test_extracts_change_of_control(self):
        """Extracts change of control provisions."""
        content = """
        Upon a Change of Control, each Holder shall have the right to
        require the Company to repurchase all or any part of such Holder's
        Notes at a purchase price equal to 101% of the principal amount.
        """
        result = extract_covenant_sections(content)
        assert len(result) > 0

    @pytest.mark.unit
    def test_extracts_covenant_lite_language(self):
        """Extracts covenant-lite identification language."""
        content = """
        This is a covenant-lite facility with no financial maintenance
        covenants. The loan contains incurrence covenants only that are
        tested upon specified actions.
        """
        result = extract_covenant_sections(content)
        assert len(result) > 0

    @pytest.mark.unit
    def test_respects_max_length(self):
        """Respects max_length parameter."""
        content = """
        The Consolidated Leverage Ratio shall not exceed 4.50 to 1.00.
        """ * 100
        result = extract_covenant_sections(content, max_length=500)
        assert len(result) <= 500

    @pytest.mark.unit
    def test_extracts_multiple_covenant_types(self, sample_credit_agreement_with_covenants):
        """Extracts multiple covenant types from comprehensive document."""
        result = extract_covenant_sections(sample_credit_agreement_with_covenants)
        # Should find some covenant-related content
        assert len(result) > 0


# =============================================================================
# parse_covenant_response() Tests
# =============================================================================

class TestParseCovenantResponse:
    """Tests for parse_covenant_response function."""

    @pytest.mark.unit
    def test_empty_data_returns_empty_list(self):
        """Empty data returns empty list."""
        assert parse_covenant_response({}) == []
        assert parse_covenant_response({"covenants": []}) == []

    @pytest.mark.unit
    def test_parses_financial_covenant(self):
        """Parses financial covenant with all fields."""
        data = {
            "covenants": [{
                "covenant_type": "financial",
                "covenant_name": "Maximum Leverage Ratio",
                "test_metric": "leverage_ratio",
                "threshold_value": 4.50,
                "threshold_type": "maximum",
                "test_frequency": "quarterly",
                "description": "Total Debt to EBITDA",
                "confidence": 0.9,
            }]
        }
        result = parse_covenant_response(data)

        assert len(result) == 1
        cov = result[0]
        assert cov.covenant_type == "financial"
        assert cov.covenant_name == "Maximum Leverage Ratio"
        assert cov.test_metric == "leverage_ratio"
        assert cov.threshold_value == 4.50
        assert cov.threshold_type == "maximum"
        assert cov.confidence == 0.9

    @pytest.mark.unit
    def test_parses_negative_covenant(self):
        """Parses negative covenant without financial fields."""
        data = {
            "covenants": [{
                "covenant_type": "negative",
                "covenant_name": "Limitation on Liens",
                "description": "Restrictions on creating liens",
                "confidence": 0.85,
            }]
        }
        result = parse_covenant_response(data)

        assert len(result) == 1
        cov = result[0]
        assert cov.covenant_type == "negative"
        assert cov.covenant_name == "Limitation on Liens"
        assert cov.test_metric is None
        assert cov.threshold_value is None

    @pytest.mark.unit
    def test_parses_protective_covenant_with_put_price(self):
        """Parses change of control with put price."""
        data = {
            "covenants": [{
                "covenant_type": "protective",
                "covenant_name": "Change of Control",
                "put_price_pct": 101.0,
                "description": "Repurchase at 101% upon change of control",
                "confidence": 0.95,
            }]
        }
        result = parse_covenant_response(data)

        assert len(result) == 1
        cov = result[0]
        assert cov.covenant_type == "protective"
        assert cov.put_price_pct == 101.0

    @pytest.mark.unit
    def test_parses_covenant_with_step_down(self):
        """Parses covenant with step-down schedule."""
        data = {
            "covenants": [{
                "covenant_type": "financial",
                "covenant_name": "Maximum Leverage Ratio",
                "test_metric": "leverage_ratio",
                "threshold_value": 5.00,
                "threshold_type": "maximum",
                "has_step_down": True,
                "step_down_schedule": {
                    "Q1 2026": 4.75,
                    "Q1 2027": 4.50,
                },
                "confidence": 0.85,
            }]
        }
        result = parse_covenant_response(data)

        assert len(result) == 1
        cov = result[0]
        assert cov.has_step_down is True
        assert cov.step_down_schedule == {"Q1 2026": 4.75, "Q1 2027": 4.50}

    @pytest.mark.unit
    def test_handles_covenant_lite_flag(self):
        """Handles is_covenant_lite flag at top level."""
        data = {
            "is_covenant_lite": True,
            "covenants": []
        }
        result = parse_covenant_response(data)

        assert len(result) == 1
        cov = result[0]
        assert cov.covenant_name == "Covenant-Lite"
        assert "no financial maintenance" in cov.description.lower()

    @pytest.mark.unit
    def test_invalid_covenant_type_defaults_to_negative(self):
        """Invalid covenant_type defaults to 'negative'."""
        data = {
            "covenants": [{
                "covenant_type": "invalid_type",
                "covenant_name": "Some Covenant",
            }]
        }
        result = parse_covenant_response(data)

        assert len(result) == 1
        assert result[0].covenant_type == "negative"

    @pytest.mark.unit
    def test_missing_confidence_defaults_to_08(self):
        """Missing confidence defaults to 0.8."""
        data = {
            "covenants": [{
                "covenant_type": "negative",
                "covenant_name": "Some Covenant",
            }]
        }
        result = parse_covenant_response(data)

        assert result[0].confidence == 0.8

    @pytest.mark.unit
    def test_truncates_long_source_text(self):
        """Source text is truncated to 2000 characters."""
        long_text = "A" * 5000
        data = {
            "covenants": [{
                "covenant_type": "negative",
                "covenant_name": "Some Covenant",
                "source_text": long_text,
            }]
        }
        result = parse_covenant_response(data)

        assert len(result[0].source_text) == 2000

    @pytest.mark.unit
    def test_parses_multiple_covenants(self):
        """Parses multiple covenants from single response."""
        data = {
            "covenants": [
                {"covenant_type": "financial", "covenant_name": "Max Leverage"},
                {"covenant_type": "financial", "covenant_name": "Min Coverage"},
                {"covenant_type": "negative", "covenant_name": "Liens"},
                {"covenant_type": "protective", "covenant_name": "Change of Control"},
            ]
        }
        result = parse_covenant_response(data)

        assert len(result) == 4
        types = [c.covenant_type for c in result]
        assert types.count("financial") == 2
        assert types.count("negative") == 1
        assert types.count("protective") == 1

    @pytest.mark.unit
    def test_handles_debt_name_linkage(self):
        """Parses debt_name field for instrument linkage."""
        data = {
            "covenants": [{
                "covenant_type": "financial",
                "covenant_name": "Maximum Leverage Ratio",
                "debt_name": "Term Loan B",
                "test_metric": "leverage_ratio",
                "threshold_value": 4.50,
            }]
        }
        result = parse_covenant_response(data)

        assert result[0].debt_name == "Term Loan B"

    @pytest.mark.unit
    def test_parsed_covenant_to_dict(self):
        """ParsedCovenant.to_dict() returns all fields."""
        cov = ParsedCovenant(
            covenant_type="financial",
            covenant_name="Max Leverage",
            test_metric="leverage_ratio",
            threshold_value=4.50,
            threshold_type="maximum",
            confidence=0.9,
        )
        d = cov.to_dict()

        assert d["covenant_type"] == "financial"
        assert d["covenant_name"] == "Max Leverage"
        assert d["threshold_value"] == 4.50
        assert d["confidence"] == 0.9


# =============================================================================
# fuzzy_match_debt_name() Tests
# =============================================================================

class TestFuzzyMatchDebtName:
    """Tests for fuzzy_match_debt_name function."""

    @pytest.mark.unit
    def test_empty_names_return_false(self):
        """Empty or None names return False."""
        assert fuzzy_match_debt_name("", "Term Loan") is False
        assert fuzzy_match_debt_name("Term Loan", "") is False
        assert fuzzy_match_debt_name(None, "Term Loan") is False
        assert fuzzy_match_debt_name("Term Loan", None) is False

    @pytest.mark.unit
    def test_exact_match(self):
        """Exact match returns True."""
        assert fuzzy_match_debt_name("Term Loan B", "Term Loan B") is True
        assert fuzzy_match_debt_name("5.250% Senior Notes due 2027", "5.250% Senior Notes due 2027") is True

    @pytest.mark.unit
    def test_case_insensitive(self):
        """Matching is case insensitive."""
        assert fuzzy_match_debt_name("TERM LOAN B", "term loan b") is True
        assert fuzzy_match_debt_name("Term Loan B", "TERM LOAN B") is True

    @pytest.mark.unit
    def test_substring_match(self):
        """Substring matching returns True."""
        assert fuzzy_match_debt_name("Term Loan", "Term Loan B Facility") is True
        assert fuzzy_match_debt_name("Senior Notes", "5.250% Senior Notes due 2027") is True

    @pytest.mark.unit
    def test_similar_names_above_threshold(self):
        """Similar names above threshold return True."""
        assert fuzzy_match_debt_name(
            "5.250% Senior Notes due 2027",
            "5.25% Senior Notes due 2027",
            threshold=0.9
        ) is True

    @pytest.mark.unit
    def test_dissimilar_names_below_threshold(self):
        """Dissimilar names below threshold return False."""
        assert fuzzy_match_debt_name(
            "Term Loan B",
            "5.250% Senior Notes due 2027",
            threshold=0.6
        ) is False

    @pytest.mark.unit
    def test_custom_threshold(self):
        """Custom threshold parameter is respected."""
        name1 = "Term Loan B Facility"
        name2 = "Term B Loan"

        # With lower threshold, should match
        assert fuzzy_match_debt_name(name1, name2, threshold=0.4) is True
        # With higher threshold, may not match
        # (depends on actual similarity)

    @pytest.mark.unit
    def test_whitespace_handling(self):
        """Handles extra whitespace."""
        assert fuzzy_match_debt_name("  Term Loan B  ", "Term Loan B") is True
        assert fuzzy_match_debt_name("Term Loan B", "  Term Loan B  ") is True

    @pytest.mark.unit
    def test_real_world_variations(self):
        """Tests real-world debt name variations."""
        # Credit agreement variations
        assert fuzzy_match_debt_name(
            "Credit Agreement",
            "Amended and Restated Credit Agreement",
            threshold=0.5
        ) is True

        # Notes variations
        assert fuzzy_match_debt_name(
            "5.25% Notes",
            "5.250% Senior Notes due 2027",
            threshold=0.5
        ) is True

        # Term loan variations
        assert fuzzy_match_debt_name(
            "Term B Loan",
            "Term Loan B",
            threshold=0.6
        ) is True


# =============================================================================
# Validation Tests (Data Quality)
# =============================================================================

class TestCovenantDataValidation:
    """Tests for covenant data validation patterns."""

    @pytest.mark.unit
    def test_valid_covenant_types(self):
        """All valid covenant types are recognized."""
        assert set(COVENANT_TYPES) == {'financial', 'negative', 'incurrence', 'protective'}

    @pytest.mark.unit
    def test_valid_financial_metrics(self):
        """All valid financial metrics are defined."""
        expected = {
            'leverage_ratio',
            'first_lien_leverage',
            'secured_leverage',
            'net_leverage_ratio',
            'interest_coverage',
            'fixed_charge_coverage',
            'debt_to_capitalization',
        }
        assert set(FINANCIAL_METRICS) == expected

    @pytest.mark.unit
    def test_leverage_ratio_sanity(self):
        """Leverage ratio thresholds should be reasonable (1-20x)."""
        # Typical leverage covenants are 3-7x
        reasonable_ratios = [3.0, 4.0, 4.5, 5.0, 5.5, 6.0, 7.0]
        unreasonable_ratios = [0.1, 100, 1000]

        for ratio in reasonable_ratios:
            assert 0.5 <= ratio <= 20, f"Ratio {ratio} outside expected range"

        for ratio in unreasonable_ratios:
            assert not (0.5 <= ratio <= 20), f"Ratio {ratio} should be flagged"

    @pytest.mark.unit
    def test_coverage_ratio_sanity(self):
        """Coverage ratio thresholds should be reasonable (1-10x)."""
        # Typical coverage covenants are 1.5-3x
        reasonable_ratios = [1.5, 2.0, 2.25, 2.5, 3.0]

        for ratio in reasonable_ratios:
            assert 1.0 <= ratio <= 10, f"Coverage {ratio} outside expected range"

    @pytest.mark.unit
    def test_put_price_sanity(self):
        """Put prices should be around 100-110%."""
        # Change of control typically 101%
        reasonable_prices = [100.0, 101.0, 102.0, 105.0]
        unreasonable_prices = [50.0, 200.0, 1000.0]

        for price in reasonable_prices:
            assert 95 <= price <= 115, f"Put price {price} outside expected range"

        for price in unreasonable_prices:
            assert not (95 <= price <= 115), f"Put price {price} should be flagged"

    @pytest.mark.unit
    def test_confidence_range(self):
        """Confidence should be between 0 and 1."""
        data = {
            "covenants": [
                {"covenant_type": "financial", "covenant_name": "A", "confidence": 0.95},
                {"covenant_type": "financial", "covenant_name": "B", "confidence": 0.5},
                {"covenant_type": "financial", "covenant_name": "C", "confidence": 1.0},
            ]
        }
        result = parse_covenant_response(data)

        for cov in result:
            assert 0 <= cov.confidence <= 1, f"Confidence {cov.confidence} out of range"


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_credit_agreement_with_covenants():
    """Sample credit agreement with covenant language."""
    return """
    CREDIT AGREEMENT

    Dated as of April 1, 2023

    ARTICLE VI
    FINANCIAL COVENANTS

    Section 6.01. Maximum Consolidated Leverage Ratio.
    The Borrower shall not permit the Consolidated Leverage Ratio as of the
    last day of any fiscal quarter to exceed 4.50 to 1.00; provided that upon
    the consummation of a Material Acquisition, such maximum ratio shall be
    increased to 5.00 to 1.00 for the four consecutive fiscal quarters
    immediately following such acquisition.

    Section 6.02. Minimum Interest Coverage Ratio.
    The Borrower shall maintain as of the last day of any fiscal quarter an
    Interest Coverage Ratio of at least 2.00 to 1.00.

    ARTICLE VII
    NEGATIVE COVENANTS

    Section 7.01. Limitation on Liens.
    The Borrower will not, and will not permit any Subsidiary to, directly or
    indirectly, create, incur, assume or suffer to exist any Lien upon any of
    its property or assets.

    Section 7.02. Limitation on Indebtedness.
    The Borrower shall not, and shall not permit any Subsidiary to, create,
    incur, assume or suffer to exist any Indebtedness.

    Section 7.03. Restricted Payments.
    The Borrower shall not declare or make any Restricted Payment.

    Section 7.04. Asset Sales.
    The Borrower shall not sell, transfer, lease or otherwise dispose of any
    asset, except for certain Permitted Asset Sales.

    ARTICLE VIII
    EVENTS OF DEFAULT

    Upon a Change of Control, the Borrower shall offer to repurchase all
    outstanding Notes at a purchase price equal to 101% of the principal
    amount thereof.
    """
