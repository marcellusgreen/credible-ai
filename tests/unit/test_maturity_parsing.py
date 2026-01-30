"""
Unit tests for maturity date parsing from instrument names.

Tests the extract_maturity_year function and related parsing logic.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.fix_missing_maturity_dates import extract_maturity_year
from app.services.document_linking import _extract_year_from_name, _extract_rate_from_name


class TestExtractMaturityYear:
    """Tests for extract_maturity_year function."""

    @pytest.mark.unit
    def test_empty_string(self):
        """Empty string returns None."""
        assert extract_maturity_year("") is None
        assert extract_maturity_year(None) is None

    @pytest.mark.unit
    def test_due_year_simple(self):
        """'due YYYY' pattern."""
        assert extract_maturity_year("5.250% Notes due 2027") == 2027
        assert extract_maturity_year("Senior Notes due 2030") == 2030
        assert extract_maturity_year("Term Loan due 2025") == 2025

    @pytest.mark.unit
    def test_due_month_year(self):
        """'due Month YYYY' pattern."""
        assert extract_maturity_year("Notes due March 2030") == 2030
        assert extract_maturity_year("Notes due January 2025") == 2025
        assert extract_maturity_year("Notes due December 2029") == 2029

    @pytest.mark.unit
    def test_due_full_date(self):
        """'due MM/DD/YYYY' pattern."""
        assert extract_maturity_year("Notes due 3/15/2030") == 2030
        assert extract_maturity_year("Notes due 12/1/2028") == 2028

    @pytest.mark.unit
    def test_notes_year(self):
        """'notes YYYY' pattern."""
        assert extract_maturity_year("2027 Notes") == 2027
        assert extract_maturity_year("Senior Secured 2030 Notes") == 2030

    @pytest.mark.unit
    def test_debentures_year(self):
        """'debentures YYYY' pattern."""
        assert extract_maturity_year("6.625% Debentures 2045") == 2045
        assert extract_maturity_year("Convertible Debentures 2028") == 2028

    @pytest.mark.unit
    def test_complex_names(self):
        """Complex instrument names with multiple components."""
        assert extract_maturity_year("4.125% Senior Notes due February 15, 2030") == 2030
        assert extract_maturity_year("5.250% Senior Secured Notes due 2029") == 2029
        assert extract_maturity_year("3.875% Euro Senior Notes due March 2033") == 2033

    @pytest.mark.unit
    def test_no_year_returns_none(self):
        """Names without year return None."""
        assert extract_maturity_year("Revolving Credit Facility") is None
        assert extract_maturity_year("$1.5 billion Term Loan") is None
        assert extract_maturity_year("Credit Agreement") is None
        assert extract_maturity_year("Senior Notes") is None

    @pytest.mark.unit
    def test_year_sanity_check(self):
        """Years outside valid range are rejected."""
        # Year 2019 should be rejected (before 2020)
        assert extract_maturity_year("Notes due 2019") is None
        assert extract_maturity_year("Notes due 2015") is None

        # Year 2101+ should be rejected
        assert extract_maturity_year("Notes due 2101") is None

    @pytest.mark.unit
    def test_case_insensitive(self):
        """Matching is case insensitive."""
        assert extract_maturity_year("NOTES DUE 2027") == 2027
        assert extract_maturity_year("notes Due 2027") == 2027
        assert extract_maturity_year("Notes DUE 2027") == 2027


class TestExtractYearFromName:
    """Tests for _extract_year_from_name from document_linking."""

    @pytest.mark.unit
    def test_due_pattern(self):
        """'due YYYY' pattern."""
        assert _extract_year_from_name("Notes due 2025") == "2025"
        assert _extract_year_from_name("5.25% Notes due 2030") == "2030"

    @pytest.mark.unit
    def test_maturing_pattern(self):
        """'maturing YYYY' pattern."""
        assert _extract_year_from_name("Notes maturing 2028") == "2028"
        assert _extract_year_from_name("Bonds maturing in 2030") is None  # "in" breaks pattern

    @pytest.mark.unit
    def test_no_match(self):
        """No year pattern returns None."""
        assert _extract_year_from_name("Revolving Credit Facility") is None
        assert _extract_year_from_name("Term Loan") is None
        assert _extract_year_from_name("") is None


class TestExtractRateFromName:
    """Tests for _extract_rate_from_name from document_linking."""

    @pytest.mark.unit
    def test_decimal_rate(self):
        """Decimal rate pattern."""
        assert _extract_rate_from_name("5.250% Notes due 2027") == "5.250"
        assert _extract_rate_from_name("4.5% Senior Notes") == "4.5"
        assert _extract_rate_from_name("3.875% Notes") == "3.875"

    @pytest.mark.unit
    def test_integer_rate(self):
        """Integer rate pattern."""
        assert _extract_rate_from_name("5% Notes due 2025") == "5"
        assert _extract_rate_from_name("10% Senior Secured Notes") == "10"

    @pytest.mark.unit
    def test_no_rate(self):
        """No rate returns None."""
        assert _extract_rate_from_name("Revolving Credit Facility") is None
        assert _extract_rate_from_name("Term Loan B") is None
        assert _extract_rate_from_name("Floating Rate Notes") is None

    @pytest.mark.unit
    def test_rate_with_spaces(self):
        """Rate with spaces before percent sign."""
        assert _extract_rate_from_name("5.25 % Notes") == "5.25"


class TestMaturityParsingRealWorld:
    """Real-world test cases from actual instrument names."""

    @pytest.mark.unit
    def test_real_instrument_names(self, sample_debt_names):
        """Test with real instrument name samples."""
        expected_years = {
            "5.250% Senior Notes due 2027": 2027,
            "4.50% Notes due March 2030": 2030,
            "3.875% Senior Secured Notes due 2029": 2029,
            "6.625% Debentures due 2045": 2045,
            "Term Loan B due 2028": 2028,
            "Revolving Credit Facility": None,
            "$1.5 billion Term Loan": None,
            "2.95% Notes due 2026": 2026,
            "Floating Rate Notes due 2025": 2025,
            "5 1/2% Senior Notes due 2028": 2028,
            "4.125% Senior Notes due February 15, 2030": 2030,
            "Senior Notes due 2031": 2031,
            "Credit Agreement dated 2022": None,  # "dated" not "due"
        }

        for name, expected in expected_years.items():
            result = extract_maturity_year(name)
            assert result == expected, f"Failed for '{name}': got {result}, expected {expected}"

    @pytest.mark.unit
    def test_fractional_rate_names(self):
        """Instruments with fractional rates like '5 1/2%'."""
        # These have unusual rate formats but standard year formats
        assert extract_maturity_year("5 1/2% Senior Notes due 2028") == 2028
        assert extract_maturity_year("6 3/4% Notes due 2029") == 2029
        assert extract_maturity_year("4 7/8% Debentures due 2045") == 2045
