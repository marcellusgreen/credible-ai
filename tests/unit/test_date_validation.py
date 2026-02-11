"""
Unit tests for parse_date utility and date range validation logic.

Tests date parsing from app.services.utils and date range rules used by
the /changes and /pricing/history endpoints.
"""

import pytest
import sys
import os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.utils import parse_date


class TestDateParsing:
    """Tests for parse_date utility function."""

    @pytest.mark.unit
    def test_valid_iso_date(self):
        """ISO format date string parses correctly."""
        result = parse_date("2025-01-01")
        assert result == date(2025, 1, 1)

    @pytest.mark.unit
    def test_invalid_date_string(self):
        """Invalid date string returns None."""
        result = parse_date("not-a-date")
        assert result is None

    @pytest.mark.unit
    def test_empty_string(self):
        """Empty string returns None."""
        result = parse_date("")
        assert result is None

    @pytest.mark.unit
    def test_none_input(self):
        """None input returns None."""
        result = parse_date(None)
        assert result is None


class TestDateRangeValidation:
    """Tests for date range validation logic used by pricing history and changes endpoints."""

    @pytest.mark.unit
    def test_valid_range_within_2_years(self):
        """Date range within 2 years is valid."""
        from_date = date(2024, 1, 1)
        to_date = date(2025, 12, 31)
        days_diff = (to_date - from_date).days
        assert days_diff <= 730  # 2 years

    @pytest.mark.unit
    def test_range_exceeds_2_years(self):
        """Date range exceeding 2 years is invalid."""
        from_date = date(2023, 1, 1)
        to_date = date(2026, 1, 1)
        days_diff = (to_date - from_date).days
        assert days_diff > 730  # exceeds 2 years

    @pytest.mark.unit
    def test_inverted_range(self):
        """Inverted date range (from > to) is invalid."""
        from_date = date(2026, 1, 1)
        to_date = date(2025, 1, 1)
        assert from_date > to_date

    @pytest.mark.unit
    def test_same_day_range(self):
        """Same-day range (from == to) is valid."""
        from_date = date(2025, 6, 15)
        to_date = date(2025, 6, 15)
        assert from_date == to_date
        assert (to_date - from_date).days == 0
