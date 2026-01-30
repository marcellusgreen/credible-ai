"""
Pytest configuration and fixtures for DebtStack tests.

Fixtures provide:
- Mock database sessions
- Sample test data
- API client configuration
"""

import os
import sys
from uuid import uuid4

import pytest

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =============================================================================
# Sample Data Fixtures
# =============================================================================

@pytest.fixture
def sample_entity_names():
    """Sample entity names for testing name normalization."""
    return [
        "Apple Inc.",
        "Apple, Inc.",
        "APPLE INC",
        "Apple",
        "Microsoft Corporation",
        "Microsoft Corp.",
        "Microsoft Corp",
        "Amazon.com, Inc.",
        "Amazon.com Inc",
        "Charter Communications, LLC",
        "Charter Communications LLC",
        "Some Company, L.P.",
        "Some Company LP",
        "British Company, Ltd.",
        "British Company Ltd",
    ]


@pytest.fixture
def sample_entity_map():
    """Sample entity map for fuzzy matching tests."""
    return {
        "apple": uuid4(),
        "microsoft": uuid4(),
        "amazon": uuid4(),
        "charter communications": uuid4(),
        "alphabet": uuid4(),
        "meta platforms": uuid4(),
    }


@pytest.fixture
def sample_debt_names():
    """Sample debt instrument names for parsing tests."""
    return [
        "5.250% Senior Notes due 2027",
        "4.50% Notes due March 2030",
        "3.875% Senior Secured Notes due 2029",
        "6.625% Debentures due 2045",
        "Term Loan B due 2028",
        "Revolving Credit Facility",
        "$1.5 billion Term Loan",
        "2.95% Notes due 2026",
        "Floating Rate Notes due 2025",
        "5 1/2% Senior Notes due 2028",
        "4.125% Senior Notes due February 15, 2030",
        "Senior Notes due 2031",
        "Credit Agreement dated 2022",
    ]


@pytest.fixture
def sample_indenture_content():
    """Sample indenture document content."""
    return """
    INDENTURE

    Dated as of May 15, 2020

    Between

    CHARTER COMMUNICATIONS, INC.
    as Issuer

    and

    THE BANK OF NEW YORK MELLON TRUST COMPANY, N.A.
    as Trustee

    $1,000,000,000
    5.250% Senior Notes due 2027

    This Indenture (this "Indenture"), dated as of May 15, 2020, is between
    Charter Communications, Inc., a Delaware corporation (the "Company"),
    and The Bank of New York Mellon Trust Company, N.A., as trustee.

    The Notes will bear interest at 5.250% per annum, payable semi-annually.
    The Notes will mature on May 15, 2027.
    """


@pytest.fixture
def sample_credit_agreement_content():
    """Sample credit agreement document content."""
    return """
    CREDIT AGREEMENT

    Dated as of April 1, 2023

    Among

    CHARTER COMMUNICATIONS OPERATING, LLC,
    as Borrower,

    CHARTER COMMUNICATIONS, INC.,
    as Holdings,

    THE LENDERS PARTY HERETO

    and

    BANK OF AMERICA, N.A.,
    as Administrative Agent

    $3,000,000,000 REVOLVING CREDIT FACILITY
    $2,000,000,000 TERM LOAN A FACILITY

    This CREDIT AGREEMENT is entered into as of April 1, 2023.

    The Revolving Credit Facility shall mature on April 1, 2028.
    The Term Loan A Facility shall mature on April 1, 2028.
    """


# =============================================================================
# API Test Fixtures
# =============================================================================

@pytest.fixture
def api_base_url():
    """Base URL for API tests."""
    return os.getenv("TEST_API_URL", "https://credible-ai-production.up.railway.app")


@pytest.fixture
def api_key():
    """API key for authenticated requests."""
    return os.getenv("DEBTSTACK_API_KEY") or os.getenv("TEST_API_KEY")


# =============================================================================
# Mock Fixtures
# =============================================================================

@pytest.fixture
def mock_debt_instrument():
    """Factory for mock debt instrument objects."""
    def _create(
        name="5.250% Senior Notes due 2027",
        instrument_type="senior_unsecured_notes",
        interest_rate=5.25,
        maturity_year=2027,
    ):
        class MockInstrument:
            def __init__(self):
                self.id = uuid4()
                self.name = name
                self.instrument_type = instrument_type
                self.interest_rate = interest_rate
                self.maturity_date = None
        return MockInstrument()
    return _create


@pytest.fixture
def mock_document_section():
    """Factory for mock document section objects."""
    def _create(
        section_type="indenture",
        title="Indenture for 5.250% Notes",
        content="Sample indenture content...",
    ):
        class MockDocument:
            def __init__(self):
                self.id = uuid4()
                self.section_type = section_type
                self.title = title
                self.content = content
        return MockDocument()
    return _create
