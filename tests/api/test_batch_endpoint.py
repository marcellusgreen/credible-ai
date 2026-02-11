"""
API contract tests for POST /v1/batch endpoint.

Validates response structure, status codes, and error handling for batch operations.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Skip all tests in this module if no API key
pytestmark = pytest.mark.skipif(
    not os.getenv("DEBTSTACK_API_KEY") and not os.getenv("TEST_API_KEY"),
    reason="No API key configured"
)


def get_api_client():
    """Get configured API client."""
    import httpx
    base_url = os.getenv("TEST_API_URL", "https://api.debtstack.ai")
    api_key = os.getenv("DEBTSTACK_API_KEY") or os.getenv("TEST_API_KEY")
    return httpx.Client(
        base_url=base_url,
        headers={"X-API-Key": api_key},
        timeout=30.0
    )


def _valid_batch_payload(n=1):
    """Build a valid batch payload with n operations."""
    return {
        "operations": [
            {"primitive": "search.companies", "params": {"ticker": "AAPL"}}
            for _ in range(n)
        ]
    }


class TestBatchEndpoint:
    """Tests for POST /v1/batch."""

    @pytest.mark.api
    def test_returns_200_with_valid_request(self):
        """POST valid batch returns 200 OK."""
        with get_api_client() as client:
            response = client.post("/v1/batch", json=_valid_batch_payload(1))
            assert response.status_code == 200

    @pytest.mark.api
    def test_response_has_results_array(self):
        """Response contains 'results' key with a list."""
        with get_api_client() as client:
            response = client.post("/v1/batch", json=_valid_batch_payload(1))
            data = response.json()
            assert "results" in data
            assert isinstance(data["results"], list)

    @pytest.mark.api
    def test_response_has_meta(self):
        """Response meta has total_operations, successful, failed, duration_ms."""
        with get_api_client() as client:
            response = client.post("/v1/batch", json=_valid_batch_payload(2))
            data = response.json()
            assert "meta" in data
            meta = data["meta"]
            assert "total_operations" in meta
            assert "successful" in meta
            assert "failed" in meta
            assert "duration_ms" in meta

    @pytest.mark.api
    def test_operation_result_has_required_fields(self):
        """Each result has operation_id, status, and data or error."""
        with get_api_client() as client:
            response = client.post("/v1/batch", json=_valid_batch_payload(1))
            data = response.json()
            for result in data["results"]:
                assert "operation_id" in result
                assert "status" in result
                assert "data" in result or "error" in result

    @pytest.mark.api
    def test_rejects_empty_operations(self):
        """Empty operations list returns 422."""
        with get_api_client() as client:
            response = client.post("/v1/batch", json={"operations": []})
            assert response.status_code == 422

    @pytest.mark.api
    def test_rejects_over_10_operations(self):
        """11 operations returns 422."""
        with get_api_client() as client:
            response = client.post("/v1/batch", json=_valid_batch_payload(11))
            assert response.status_code == 422

    @pytest.mark.api
    def test_rejects_malformed_json(self):
        """Malformed JSON body returns 422 or 400."""
        with get_api_client() as client:
            response = client.post(
                "/v1/batch",
                content="{ invalid json",
                headers={"Content-Type": "application/json"}
            )
            assert response.status_code in (400, 422)

    @pytest.mark.api
    def test_rejects_missing_primitive_field(self):
        """Operation without primitive field returns 422."""
        with get_api_client() as client:
            response = client.post("/v1/batch", json={
                "operations": [{"params": {"ticker": "AAPL"}}]
            })
            assert response.status_code == 422

    @pytest.mark.api
    def test_invalid_primitive_returns_error_status(self):
        """Invalid primitive name returns result with status=='error' and error code."""
        with get_api_client() as client:
            response = client.post("/v1/batch", json={
                "operations": [{"primitive": "fake.primitive", "params": {}}]
            })
            data = response.json()
            # Batch should still return 200, but the operation result should show error
            assert response.status_code == 200
            assert len(data["results"]) == 1
            result = data["results"][0]
            assert result["status"] == "error"
            assert "error" in result
            assert result["error"].get("code") == "INVALID_PRIMITIVE"

    @pytest.mark.api
    def test_operation_ids_match_order(self):
        """3 operations return operation_ids 0, 1, 2 in order."""
        with get_api_client() as client:
            response = client.post("/v1/batch", json=_valid_batch_payload(3))
            data = response.json()
            ids = [r["operation_id"] for r in data["results"]]
            assert ids == [0, 1, 2]
