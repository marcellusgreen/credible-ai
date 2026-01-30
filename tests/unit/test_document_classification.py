"""
Unit tests for document classification and type detection.

Tests logic for determining whether a debt instrument should match
to an indenture vs credit agreement based on instrument type.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# Document type mapping logic (extracted for testing)
def get_expected_document_type(instrument_type: str) -> str | None:
    """
    Determine what document type an instrument should link to.

    Returns:
        'indenture' - for bonds, notes, debentures
        'credit_agreement' - for loans, revolvers, facilities
        None - for unknown types
    """
    if not instrument_type:
        return None

    instrument_type = instrument_type.lower()

    # Notes and bonds -> indentures
    if any(t in instrument_type for t in [
        'note', 'bond', 'debenture', 'secured_note', 'unsecured_note'
    ]):
        return 'indenture'

    # Loans and facilities -> credit agreements
    if any(t in instrument_type for t in [
        'loan', 'revolver', 'revolving', 'facility', 'credit',
        'term_loan', 'delayed_draw'
    ]):
        return 'credit_agreement'

    return None


def is_credit_facility(name: str) -> bool:
    """Check if instrument name indicates a credit facility."""
    if not name:
        return False

    name_lower = name.lower()
    indicators = [
        'revolving', 'revolver', 'credit facility', 'credit agreement',
        'term loan', 'delayed draw', 'swingline', 'letter of credit',
        'borrowing base', 'working capital'
    ]
    return any(ind in name_lower for ind in indicators)


def is_bond_or_note(name: str) -> bool:
    """Check if instrument name indicates a bond or note."""
    if not name:
        return False

    name_lower = name.lower()

    # Explicit bond/note indicators
    indicators = [
        'senior note', 'senior secured note', 'senior unsecured note',
        'subordinated note', 'bond', 'debenture', '% note'
    ]
    if any(ind in name_lower for ind in indicators):
        return True

    # Rate + maturity pattern suggests a note
    import re
    if re.search(r'\d+\.?\d*\s*%.*due\s*\d{4}', name_lower):
        return True

    return False


class TestGetExpectedDocumentType:
    """Tests for document type determination."""

    @pytest.mark.unit
    def test_notes_need_indentures(self):
        """Note instruments should link to indentures."""
        assert get_expected_document_type("senior_unsecured_notes") == "indenture"
        assert get_expected_document_type("senior_secured_notes") == "indenture"
        assert get_expected_document_type("subordinated_notes") == "indenture"
        assert get_expected_document_type("notes") == "indenture"

    @pytest.mark.unit
    def test_bonds_need_indentures(self):
        """Bond instruments should link to indentures."""
        assert get_expected_document_type("bond") == "indenture"
        assert get_expected_document_type("corporate_bond") == "indenture"
        assert get_expected_document_type("high_yield_bond") == "indenture"

    @pytest.mark.unit
    def test_debentures_need_indentures(self):
        """Debentures should link to indentures."""
        assert get_expected_document_type("debenture") == "indenture"
        assert get_expected_document_type("convertible_debenture") == "indenture"

    @pytest.mark.unit
    def test_loans_need_credit_agreements(self):
        """Loan instruments should link to credit agreements."""
        assert get_expected_document_type("term_loan") == "credit_agreement"
        assert get_expected_document_type("term_loan_a") == "credit_agreement"
        assert get_expected_document_type("term_loan_b") == "credit_agreement"
        assert get_expected_document_type("delayed_draw_term_loan") == "credit_agreement"

    @pytest.mark.unit
    def test_revolvers_need_credit_agreements(self):
        """Revolving facilities should link to credit agreements."""
        assert get_expected_document_type("revolver") == "credit_agreement"
        assert get_expected_document_type("revolving_credit") == "credit_agreement"
        assert get_expected_document_type("revolving_facility") == "credit_agreement"

    @pytest.mark.unit
    def test_facilities_need_credit_agreements(self):
        """Credit facilities should link to credit agreements."""
        assert get_expected_document_type("credit_facility") == "credit_agreement"
        assert get_expected_document_type("facility") == "credit_agreement"

    @pytest.mark.unit
    def test_unknown_returns_none(self):
        """Unknown types return None."""
        assert get_expected_document_type("unknown_type") is None
        assert get_expected_document_type("") is None
        assert get_expected_document_type(None) is None

    @pytest.mark.unit
    def test_case_insensitive(self):
        """Type matching is case insensitive."""
        assert get_expected_document_type("SENIOR_NOTES") == "indenture"
        assert get_expected_document_type("Term_Loan") == "credit_agreement"


class TestIsCreditFacility:
    """Tests for credit facility detection from names."""

    @pytest.mark.unit
    def test_revolving_facilities(self):
        """Revolving facilities detected."""
        assert is_credit_facility("$1.5B Revolving Credit Facility") is True
        assert is_credit_facility("Revolver") is True
        assert is_credit_facility("Senior Secured Revolving Facility") is True

    @pytest.mark.unit
    def test_term_loans(self):
        """Term loans detected."""
        assert is_credit_facility("Term Loan B") is True
        assert is_credit_facility("$500M Term Loan A Facility") is True
        assert is_credit_facility("Delayed Draw Term Loan") is True

    @pytest.mark.unit
    def test_credit_agreements(self):
        """Credit agreement references detected."""
        assert is_credit_facility("2023 Credit Facility") is True
        assert is_credit_facility("Credit Agreement dated 2022") is True

    @pytest.mark.unit
    def test_not_credit_facility(self):
        """Notes and bonds are not credit facilities."""
        assert is_credit_facility("5.25% Senior Notes due 2027") is False
        assert is_credit_facility("6.625% Debentures due 2045") is False
        assert is_credit_facility("Senior Secured Notes") is False

    @pytest.mark.unit
    def test_empty_string(self):
        """Empty string returns False."""
        assert is_credit_facility("") is False
        assert is_credit_facility(None) is False


class TestIsBondOrNote:
    """Tests for bond/note detection from names."""

    @pytest.mark.unit
    def test_explicit_notes(self):
        """Explicit note names detected."""
        assert is_bond_or_note("5.25% Senior Notes due 2027") is True
        assert is_bond_or_note("Senior Secured Notes") is True
        assert is_bond_or_note("Subordinated Notes") is True

    @pytest.mark.unit
    def test_bonds(self):
        """Bonds detected."""
        assert is_bond_or_note("Corporate Bond") is True
        assert is_bond_or_note("High Yield Bond 2029") is True

    @pytest.mark.unit
    def test_debentures(self):
        """Debentures detected."""
        assert is_bond_or_note("6.625% Debentures due 2045") is True
        assert is_bond_or_note("Convertible Debentures") is True

    @pytest.mark.unit
    def test_rate_maturity_pattern(self):
        """Rate + maturity pattern detected as note."""
        assert is_bond_or_note("4.50% due 2030") is True
        assert is_bond_or_note("5.250% due 2027") is True

    @pytest.mark.unit
    def test_not_bond_or_note(self):
        """Credit facilities are not bonds/notes."""
        assert is_bond_or_note("Revolving Credit Facility") is False
        assert is_bond_or_note("Term Loan B") is False
        assert is_bond_or_note("Credit Agreement") is False

    @pytest.mark.unit
    def test_empty_string(self):
        """Empty string returns False."""
        assert is_bond_or_note("") is False
        assert is_bond_or_note(None) is False


class TestDocumentClassificationRealWorld:
    """Real-world classification tests."""

    @pytest.mark.unit
    def test_ambiguous_names(self):
        """Test names that could be ambiguous."""
        # "Senior Secured" without Notes/Bond is ambiguous
        # But with a rate pattern, it's likely a note
        assert is_bond_or_note("5.25% Senior Secured due 2029") is True

    @pytest.mark.unit
    def test_commercial_paper(self):
        """Commercial paper programs."""
        # CP is typically not linked to indentures or credit agreements
        assert is_credit_facility("Commercial Paper Program") is False
        assert is_bond_or_note("Commercial Paper Program") is False

    @pytest.mark.unit
    def test_mixed_facility(self):
        """Facilities that include both revolver and term loan."""
        # These should be classified as credit facilities
        assert is_credit_facility("$2B Revolving and Term Loan Facility") is True
