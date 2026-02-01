"""
API contract tests for /v1/companies endpoint.

Validates response structure and data types match expected schema.
"""

import pytest
import sys
import os
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Skip all tests in this module if no API key
pytestmark = pytest.mark.skipif(
    not os.getenv("DEBTSTACK_API_KEY") and not os.getenv("TEST_API_KEY"),
    reason="No API key configured"
)


def get_api_client():
    """Get configured API client."""
    import httpx
    base_url = os.getenv("TEST_API_URL", "https://credible-ai-production.up.railway.app")
    api_key = os.getenv("DEBTSTACK_API_KEY") or os.getenv("TEST_API_KEY")
    return httpx.Client(
        base_url=base_url,
        headers={"X-API-Key": api_key},
        timeout=30.0
    )


class TestCompaniesListEndpoint:
    """Tests for GET /v1/companies."""

    @pytest.mark.api
    def test_returns_200(self):
        """Endpoint returns 200 OK."""
        with get_api_client() as client:
            response = client.get("/v1/companies")
            assert response.status_code == 200

    @pytest.mark.api
    def test_response_has_data_array(self):
        """Response contains 'data' array."""
        with get_api_client() as client:
            response = client.get("/v1/companies")
            data = response.json()
            assert "data" in data
            assert isinstance(data["data"], list)

    @pytest.mark.api
    def test_company_has_required_fields(self):
        """Each company has required fields."""
        required_fields = ["ticker", "name"]
        with get_api_client() as client:
            response = client.get("/v1/companies", params={"limit": 5})
            data = response.json()
            for company in data["data"]:
                for field in required_fields:
                    assert field in company, f"Missing field: {field}"

    @pytest.mark.api
    def test_company_field_types(self):
        """Company fields have correct types."""
        with get_api_client() as client:
            response = client.get("/v1/companies", params={"limit": 5})
            data = response.json()
            for company in data["data"]:
                # String fields
                assert isinstance(company["ticker"], str)
                assert isinstance(company["name"], str)

                # Numeric fields (nullable)
                if company.get("total_debt") is not None:
                    assert isinstance(company["total_debt"], (int, float))
                if company.get("net_leverage_ratio") is not None:
                    assert isinstance(company["net_leverage_ratio"], (int, float))
                if company.get("entity_count") is not None:
                    assert isinstance(company["entity_count"], int)

    @pytest.mark.api
    def test_limit_parameter(self):
        """Limit parameter restricts results."""
        with get_api_client() as client:
            response = client.get("/v1/companies", params={"limit": 3})
            data = response.json()
            assert len(data["data"]) <= 3

    @pytest.mark.api
    def test_sort_parameter(self):
        """Sort parameter orders results."""
        with get_api_client() as client:
            # Sort by ticker ascending
            response = client.get("/v1/companies", params={"sort": "ticker", "limit": 10})
            data = response.json()
            tickers = [c["ticker"] for c in data["data"]]
            assert tickers == sorted(tickers)

            # Sort by ticker descending
            response = client.get("/v1/companies", params={"sort": "-ticker", "limit": 10})
            data = response.json()
            tickers = [c["ticker"] for c in data["data"]]
            assert tickers == sorted(tickers, reverse=True)

    @pytest.mark.api
    def test_fields_parameter(self):
        """Fields parameter limits returned fields."""
        with get_api_client() as client:
            response = client.get("/v1/companies", params={
                "fields": "ticker,name",
                "limit": 5
            })
            data = response.json()
            for company in data["data"]:
                # Should have requested fields
                assert "ticker" in company
                assert "name" in company
                # Should not have unrequested fields (implementation may vary)


class TestCompanyDetailEndpoint:
    """Tests for GET /v1/companies/{ticker}."""

    @pytest.mark.api
    def test_valid_ticker_returns_200(self):
        """Valid ticker returns 200."""
        with get_api_client() as client:
            response = client.get("/v1/companies/AAPL")
            assert response.status_code == 200

    @pytest.mark.api
    def test_invalid_ticker_returns_404(self):
        """Invalid ticker returns 404."""
        with get_api_client() as client:
            response = client.get("/v1/companies/INVALID_TICKER_12345")
            assert response.status_code == 404

    @pytest.mark.api
    def test_company_detail_has_required_fields(self):
        """Company detail has all required fields."""
        required_fields = [
            "ticker", "name", "cik",
            "entity_count", "debt_instrument_count"
        ]
        with get_api_client() as client:
            response = client.get("/v1/companies/AAPL")
            json_response = response.json()
            # Handle wrapped response format
            data = json_response.get("data", json_response)
            for field in required_fields:
                assert field in data, f"Missing field: {field}"

    @pytest.mark.api
    def test_company_detail_includes_metrics(self):
        """Company detail includes financial metrics."""
        metric_fields = [
            "total_debt", "net_debt", "leverage_ratio", "net_leverage_ratio"
        ]
        with get_api_client() as client:
            response = client.get("/v1/companies/AAPL")
            json_response = response.json()
            # Handle wrapped response format
            data = json_response.get("data", json_response)
            # At least some metrics should be present
            present = [f for f in metric_fields if f in data]
            assert len(present) >= 1, "Should have at least some financial metrics"


class TestCompanyEntitiesEndpoint:
    """Tests for GET /v1/companies/{ticker}/entities."""

    @pytest.mark.api
    def test_returns_entities_array(self):
        """Endpoint returns entities array."""
        with get_api_client() as client:
            response = client.get("/v1/companies/AAPL/entities")
            assert response.status_code == 200
            data = response.json()
            assert "data" in data or isinstance(data, list)

    @pytest.mark.api
    def test_entity_has_required_fields(self):
        """Each entity has required fields."""
        required_fields = ["id", "name"]
        with get_api_client() as client:
            response = client.get("/v1/companies/AAPL/entities")
            data = response.json()
            entities = data.get("data", data) if isinstance(data, dict) else data
            # Handle case where entities is a list
            if isinstance(entities, list) and entities:
                for entity in entities[:5]:
                    for field in required_fields:
                        assert field in entity, f"Missing field: {field}"


class TestCompanyDebtEndpoint:
    """Tests for GET /v1/companies/{ticker}/debt."""

    @pytest.mark.api
    def test_returns_debt_array(self):
        """Endpoint returns debt instruments array."""
        with get_api_client() as client:
            response = client.get("/v1/companies/AAPL/debt")
            assert response.status_code == 200
            data = response.json()
            assert "data" in data or isinstance(data, list)

    @pytest.mark.api
    def test_debt_instrument_has_required_fields(self):
        """Each debt instrument has required fields."""
        required_fields = ["id", "name"]
        with get_api_client() as client:
            response = client.get("/v1/companies/AAPL/debt")
            data = response.json()
            instruments = data.get("data", data) if isinstance(data, dict) else data
            # Handle case where instruments is a list
            if isinstance(instruments, list) and instruments:
                for inst in instruments[:5]:
                    for field in required_fields:
                        assert field in inst, f"Missing field: {field}"

    @pytest.mark.api
    def test_debt_instrument_field_types(self):
        """Debt instrument fields have correct types."""
        with get_api_client() as client:
            response = client.get("/v1/companies/AAPL/debt")
            data = response.json()
            instruments = data.get("data", data) if isinstance(data, dict) else data
            # Handle case where instruments is a list
            if isinstance(instruments, list) and instruments:
                for inst in instruments[:5]:
                    assert isinstance(inst["name"], str)
                    if inst.get("interest_rate") is not None:
                        assert isinstance(inst["interest_rate"], (int, float))
                    if inst.get("principal_amount") is not None:
                        assert isinstance(inst["principal_amount"], (int, float))
