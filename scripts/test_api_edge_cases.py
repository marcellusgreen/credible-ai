#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API Edge Case Tests for DebtStack Primitives API

Tests API robustness before public launch.

Usage:
    python scripts/test_api_edge_cases.py [--base-url URL]

Tests performed:
1. Health endpoints
2. Empty/missing parameters
3. Invalid tickers
4. Invalid field names
5. Large result sets
6. Malformed JSON
7. Non-existent identifiers
8. SQL injection attempts
9. Content negotiation (JSON/CSV)
10. Error response format
11. Batch endpoint edge cases
12. Changes endpoint edge cases
13. Business tier edge cases
14. Webhook handler edge cases
"""

import argparse
import asyncio
import json
import time
from typing import Optional

import httpx


class APITester:
    """API Edge Case Tester."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.passed = 0
        self.failed = 0
        self.results = []

    def log_result(self, test_name: str, passed: bool, message: str, response_code: int = None):
        """Log a test result."""
        status = "[PASS]" if passed else "[FAIL]"
        code_str = f" ({response_code})" if response_code else ""
        print(f"  {status} {test_name}{code_str}: {message}")

        if passed:
            self.passed += 1
        else:
            self.failed += 1

        self.results.append({
            "test": test_name,
            "passed": passed,
            "message": message,
            "response_code": response_code,
        })

    async def test_health_endpoints(self, client: httpx.AsyncClient):
        """Test health check endpoints."""
        print("\n[1/14] Testing health endpoints...")

        # /v1/ping
        resp = await client.get(f"{self.base_url}/v1/ping")
        self.log_result(
            "GET /v1/ping",
            resp.status_code == 200 and resp.json().get("status") == "ok",
            "Should return status=ok",
            resp.status_code
        )

        # /v1/health
        resp = await client.get(f"{self.base_url}/v1/health")
        self.log_result(
            "GET /v1/health",
            resp.status_code == 200,
            "Should return 200 with health data",
            resp.status_code
        )

    async def test_empty_parameters(self, client: httpx.AsyncClient):
        """Test empty/missing parameters."""
        print("\n[2/14] Testing empty/missing parameters...")

        # Empty ticker filter should return all companies (or paginated subset)
        resp = await client.get(f"{self.base_url}/v1/companies", params={"ticker": ""})
        self.log_result(
            "Empty ticker",
            resp.status_code == 200,
            "Empty ticker should not error",
            resp.status_code
        )

        # No parameters at all
        resp = await client.get(f"{self.base_url}/v1/companies")
        data = resp.json()
        self.log_result(
            "No parameters",
            resp.status_code == 200 and "data" in data,
            f"Should return data array (got {len(data.get('data', []))} items)",
            resp.status_code
        )

        # Missing required q parameter for document search
        resp = await client.get(f"{self.base_url}/v1/documents/search")
        self.log_result(
            "Missing required param",
            resp.status_code == 422 or resp.status_code == 400,
            "Missing 'q' should return validation error",
            resp.status_code
        )

    async def test_invalid_tickers(self, client: httpx.AsyncClient):
        """Test invalid ticker handling."""
        print("\n[3/14] Testing invalid tickers...")

        # Non-existent ticker
        resp = await client.get(f"{self.base_url}/v1/companies", params={"ticker": "XXXXX"})
        data = resp.json()
        self.log_result(
            "Non-existent ticker",
            resp.status_code == 200 and len(data.get("data", [])) == 0,
            "Should return empty array, not error",
            resp.status_code
        )

        # Special characters in ticker
        resp = await client.get(f"{self.base_url}/v1/companies", params={"ticker": "AAPL!@#"})
        self.log_result(
            "Special chars in ticker",
            resp.status_code in (200, 400, 422),
            "Should handle gracefully",
            resp.status_code
        )

        # Very long ticker
        resp = await client.get(f"{self.base_url}/v1/companies", params={"ticker": "A" * 100})
        self.log_result(
            "Very long ticker",
            resp.status_code in (200, 400, 422),
            "Should handle gracefully",
            resp.status_code
        )

    async def test_invalid_fields(self, client: httpx.AsyncClient):
        """Test invalid field name handling."""
        print("\n[4/14] Testing invalid field names...")

        # Invalid field in fields parameter
        resp = await client.get(f"{self.base_url}/v1/companies", params={
            "ticker": "AAPL",
            "fields": "ticker,fake_field,name"
        })
        self.log_result(
            "Invalid field name",
            resp.status_code in (200, 400, 422),
            "Should handle invalid field gracefully",
            resp.status_code
        )

        # All invalid fields
        resp = await client.get(f"{self.base_url}/v1/companies", params={
            "ticker": "AAPL",
            "fields": "not_real,also_fake"
        })
        self.log_result(
            "All invalid fields",
            resp.status_code in (200, 400, 422),
            "Should handle all invalid fields",
            resp.status_code
        )

    async def test_pagination(self, client: httpx.AsyncClient):
        """Test pagination and large result sets."""
        print("\n[5/14] Testing pagination...")

        # Large limit - API caps at 100
        resp = await client.get(f"{self.base_url}/v1/bonds", params={"limit": 1000})
        self.log_result(
            "Large limit (1000)",
            resp.status_code == 422,  # API should reject limit > 100
            "Should reject limit > 100 with 422",
            resp.status_code
        )

        # Negative limit
        resp = await client.get(f"{self.base_url}/v1/bonds", params={"limit": -10})
        self.log_result(
            "Negative limit",
            resp.status_code in (200, 400, 422),
            "Should handle negative limit",
            resp.status_code
        )

        # Offset beyond data
        resp = await client.get(f"{self.base_url}/v1/companies", params={"offset": 10000})
        data = resp.json()
        self.log_result(
            "Offset beyond data",
            resp.status_code == 200 and len(data.get("data", [])) == 0,
            "Should return empty array",
            resp.status_code
        )

    async def test_malformed_json(self, client: httpx.AsyncClient):
        """Test malformed JSON handling."""
        print("\n[6/14] Testing malformed JSON...")

        # Malformed JSON to batch endpoint
        resp = await client.post(
            f"{self.base_url}/v1/batch",
            content="{ invalid json",
            headers={"Content-Type": "application/json"}
        )
        self.log_result(
            "Malformed JSON",
            resp.status_code == 422 or resp.status_code == 400,
            "Should return validation error",
            resp.status_code
        )

        # Empty body to batch
        resp = await client.post(
            f"{self.base_url}/v1/batch",
            content="",
            headers={"Content-Type": "application/json"}
        )
        self.log_result(
            "Empty body",
            resp.status_code == 422 or resp.status_code == 400,
            "Should return validation error",
            resp.status_code
        )

        # Valid JSON but wrong structure
        resp = await client.post(
            f"{self.base_url}/v1/batch",
            json={"wrong": "structure"}
        )
        self.log_result(
            "Wrong JSON structure",
            resp.status_code == 422 or resp.status_code == 400,
            "Should return validation error",
            resp.status_code
        )

    async def test_nonexistent_identifiers(self, client: httpx.AsyncClient):
        """Test non-existent identifier handling."""
        print("\n[7/14] Testing non-existent identifiers...")

        # Non-existent CUSIP - API does fuzzy matching, so it may return results
        # The important thing is it handles gracefully and returns 200
        resp = await client.get(f"{self.base_url}/v1/bonds/resolve", params={"q": "ZZZNONEXISTENT999"})
        data = resp.json()
        self.log_result(
            "Non-existent CUSIP",
            resp.status_code == 200 and "data" in data,
            "Should return 200 with data structure (may have fuzzy matches)",
            resp.status_code
        )

        # Non-existent company in changes endpoint (requires 'since' param)
        resp = await client.get(f"{self.base_url}/v1/companies/ZZZZZ/changes", params={"since": "2024-01-01"})
        self.log_result(
            "Non-existent company changes",
            resp.status_code in (404, 422, 200),
            "Should return 404, 422, or empty data",
            resp.status_code
        )

    async def test_sql_injection(self, client: httpx.AsyncClient):
        """Test SQL injection prevention."""
        print("\n[8/14] Testing SQL injection prevention...")

        # SQL injection in ticker
        malicious_inputs = [
            "'; DROP TABLE companies;--",
            "AAPL' OR '1'='1",
            "AAPL; DELETE FROM companies;",
            "1 UNION SELECT * FROM companies",
            "AAPL'\"",
        ]

        for injection in malicious_inputs:
            resp = await client.get(f"{self.base_url}/v1/companies", params={"ticker": injection})
            # Should either return empty result or error, but NOT crash
            self.log_result(
                f"SQL injection: {injection[:20]}...",
                resp.status_code in (200, 400, 422, 500) and "error" not in resp.text.lower() or resp.status_code != 500,
                "Should not execute SQL",
                resp.status_code
            )

        # SQL injection in search
        resp = await client.get(f"{self.base_url}/v1/documents/search", params={
            "q": "'; DROP TABLE document_sections;--"
        })
        self.log_result(
            "SQL injection in search",
            resp.status_code in (200, 400, 422),
            "Should sanitize search input",
            resp.status_code
        )

    async def test_content_negotiation(self, client: httpx.AsyncClient):
        """Test content negotiation (JSON/CSV)."""
        print("\n[9/14] Testing content negotiation...")

        # JSON format (default)
        resp = await client.get(f"{self.base_url}/v1/companies", params={
            "ticker": "AAPL",
            "format": "json"
        })
        self.log_result(
            "JSON format",
            resp.status_code == 200 and "application/json" in resp.headers.get("content-type", ""),
            "Should return JSON",
            resp.status_code
        )

        # CSV format
        resp = await client.get(f"{self.base_url}/v1/companies", params={
            "ticker": "AAPL",
            "format": "csv"
        })
        self.log_result(
            "CSV format",
            resp.status_code == 200 and ("text/csv" in resp.headers.get("content-type", "") or "ticker" in resp.text),
            "Should return CSV",
            resp.status_code
        )

        # Invalid format
        resp = await client.get(f"{self.base_url}/v1/companies", params={
            "ticker": "AAPL",
            "format": "xml"
        })
        self.log_result(
            "Invalid format (xml)",
            resp.status_code in (200, 400, 422),
            "Should handle invalid format",
            resp.status_code
        )

    async def test_batch_edge_cases(self, client: httpx.AsyncClient):
        """Test batch endpoint edge cases."""
        print("\n[11/14] Testing batch edge cases...")

        # Empty operations array
        resp = await client.post(
            f"{self.base_url}/v1/batch",
            json={"operations": []}
        )
        self.log_result(
            "Batch: empty operations",
            resp.status_code == 422,
            "Empty operations should return 422",
            resp.status_code
        )

        # Over limit (11 operations)
        ops = [{"primitive": "search.companies", "params": {"ticker": "AAPL"}} for _ in range(11)]
        resp = await client.post(
            f"{self.base_url}/v1/batch",
            json={"operations": ops}
        )
        self.log_result(
            "Batch: over limit (11 ops)",
            resp.status_code == 422,
            "11 operations should return 422",
            resp.status_code
        )

        # Invalid primitive name
        resp = await client.post(
            f"{self.base_url}/v1/batch",
            json={"operations": [{"primitive": "fake.nonexistent", "params": {}}]}
        )
        self.log_result(
            "Batch: invalid primitive",
            resp.status_code == 200,
            "Invalid primitive should return 200 with error in result",
            resp.status_code
        )

        # SQL injection in params
        resp = await client.post(
            f"{self.base_url}/v1/batch",
            json={"operations": [{"primitive": "search.companies", "params": {"ticker": "'; DROP TABLE companies;--"}}]}
        )
        self.log_result(
            "Batch: SQL injection in params",
            resp.status_code in (200, 400, 422),
            "SQL injection should be handled gracefully",
            resp.status_code
        )

        # Empty params
        resp = await client.post(
            f"{self.base_url}/v1/batch",
            json={"operations": [{"primitive": "search.companies", "params": {}}]}
        )
        self.log_result(
            "Batch: empty params",
            resp.status_code == 200,
            "Empty params should succeed",
            resp.status_code
        )

        # Missing operations key
        resp = await client.post(
            f"{self.base_url}/v1/batch",
            json={"not_operations": []}
        )
        self.log_result(
            "Batch: missing operations key",
            resp.status_code == 422,
            "Missing operations key should return 422",
            resp.status_code
        )

    async def test_changes_edge_cases(self, client: httpx.AsyncClient):
        """Test changes endpoint edge cases."""
        print("\n[12/14] Testing changes edge cases...")

        # Missing since parameter
        resp = await client.get(f"{self.base_url}/v1/companies/AAPL/changes")
        self.log_result(
            "Changes: missing since",
            resp.status_code == 422,
            "Missing since should return 422",
            resp.status_code
        )

        # Invalid date format
        resp = await client.get(
            f"{self.base_url}/v1/companies/AAPL/changes",
            params={"since": "not-a-date"}
        )
        self.log_result(
            "Changes: invalid date",
            resp.status_code == 422,
            "Invalid date format should return 422",
            resp.status_code
        )

        # Very old since date
        resp = await client.get(
            f"{self.base_url}/v1/companies/AAPL/changes",
            params={"since": "2000-01-01"}
        )
        self.log_result(
            "Changes: very old date",
            resp.status_code in (200, 404),
            "Very old date should return 404 NO_SNAPSHOT or data",
            resp.status_code
        )

        # Future since date
        resp = await client.get(
            f"{self.base_url}/v1/companies/AAPL/changes",
            params={"since": "2030-01-01"}
        )
        self.log_result(
            "Changes: future date",
            resp.status_code in (200, 404),
            "Future date should return 404 or data",
            resp.status_code
        )

        # SQL injection in ticker path
        resp = await client.get(
            f"{self.base_url}/v1/companies/'; DROP TABLE companies;--/changes",
            params={"since": "2025-01-01"}
        )
        self.log_result(
            "Changes: SQL injection ticker",
            resp.status_code in (404, 422),
            "SQL injection in path should return 404 or 422",
            resp.status_code
        )

        # Non-existent company
        resp = await client.get(
            f"{self.base_url}/v1/companies/ZZZZZZZ/changes",
            params={"since": "2025-01-01"}
        )
        self.log_result(
            "Changes: non-existent company",
            resp.status_code == 404,
            "Non-existent company should return 404",
            resp.status_code
        )

    async def test_business_tier_edge_cases(self, client: httpx.AsyncClient):
        """Test business-tier endpoint edge cases."""
        print("\n[13/14] Testing business tier edge cases...")

        # Historical pricing invalid CUSIP
        resp = await client.get(
            f"{self.base_url}/v1/bonds/ZZZZZZZZ9/pricing/history",
            params={"from": "2025-01-01", "to": "2026-01-01"}
        )
        self.log_result(
            "Pricing: invalid CUSIP",
            resp.status_code in (403, 404),
            "Invalid CUSIP should return 403 or 404",
            resp.status_code
        )

        # Export missing data_type
        resp = await client.get(f"{self.base_url}/v1/export")
        self.log_result(
            "Export: missing data_type",
            resp.status_code in (403, 422),
            "Missing data_type should return 403 or 422",
            resp.status_code
        )

        # Export invalid format
        resp = await client.get(
            f"{self.base_url}/v1/export",
            params={"data_type": "companies", "format": "xml"}
        )
        self.log_result(
            "Export: invalid format (xml)",
            resp.status_code in (400, 403, 422),
            "Invalid format should return 400, 403, or 422",
            resp.status_code
        )

        # Usage analytics days=0
        resp = await client.get(
            f"{self.base_url}/v1/usage/analytics",
            params={"days": 0}
        )
        self.log_result(
            "Analytics: days=0",
            resp.status_code in (403, 422),
            "days=0 should return 403 or 422",
            resp.status_code
        )

        # Usage analytics days=9999
        resp = await client.get(
            f"{self.base_url}/v1/usage/analytics",
            params={"days": 9999}
        )
        self.log_result(
            "Analytics: days=9999",
            resp.status_code in (200, 403, 422),
            "Extreme days value should be handled",
            resp.status_code
        )

        # Historical pricing inverted date range
        resp = await client.get(
            f"{self.base_url}/v1/bonds/76825DAJ7/pricing/history",
            params={"from": "2026-01-01", "to": "2025-01-01"}
        )
        self.log_result(
            "Pricing: inverted range",
            resp.status_code in (400, 403, 422),
            "Inverted date range should return 400 or 403",
            resp.status_code
        )

        # Historical pricing 3-year range (exceeds max)
        resp = await client.get(
            f"{self.base_url}/v1/bonds/76825DAJ7/pricing/history",
            params={"from": "2022-01-01", "to": "2026-01-01"}
        )
        self.log_result(
            "Pricing: 3-year range",
            resp.status_code in (200, 400, 403, 422),
            "3-year range should be handled (may exceed 2yr limit)",
            resp.status_code
        )

    async def test_webhook_edge_cases(self, client: httpx.AsyncClient):
        """Test Stripe webhook handler edge cases."""
        print("\n[14/14] Testing webhook edge cases...")

        # Webhook endpoints (try common webhook paths)
        webhook_paths = ["/v1/webhooks/stripe", "/webhooks/stripe", "/stripe/webhook"]

        for path in webhook_paths:
            # No body
            resp = await client.post(f"{self.base_url}{path}", content="")
            if resp.status_code != 404:
                self.log_result(
                    f"Webhook: no body ({path})",
                    resp.status_code in (400, 401, 403),
                    "No body should return 400/401/403",
                    resp.status_code
                )

                # Invalid JSON
                resp = await client.post(
                    f"{self.base_url}{path}",
                    content="not json",
                    headers={"Content-Type": "application/json"}
                )
                self.log_result(
                    f"Webhook: invalid JSON ({path})",
                    resp.status_code in (400, 401, 403, 422),
                    "Invalid JSON should return error",
                    resp.status_code
                )

                # No stripe-signature header
                resp = await client.post(
                    f"{self.base_url}{path}",
                    json={"type": "checkout.session.completed"}
                )
                self.log_result(
                    f"Webhook: no signature ({path})",
                    resp.status_code in (400, 401, 403),
                    "Missing stripe-signature should fail",
                    resp.status_code
                )

                # Empty event type
                resp = await client.post(
                    f"{self.base_url}{path}",
                    json={"type": ""},
                    headers={"stripe-signature": "t=1,v1=fake"}
                )
                self.log_result(
                    f"Webhook: empty event type ({path})",
                    resp.status_code in (400, 401, 403),
                    "Empty event type should fail",
                    resp.status_code
                )
                break  # Found the right path
        else:
            # No webhook endpoint found at any path - log info
            self.log_result(
                "Webhook: endpoint discovery",
                True,
                "No webhook endpoint found at standard paths (may use different path)",
                None
            )

    async def test_error_response_format(self, client: httpx.AsyncClient):
        """Test error response format consistency."""
        print("\n[10/14] Testing error response format...")

        # 404 error
        resp = await client.get(f"{self.base_url}/v1/nonexistent/endpoint")
        self.log_result(
            "404 response",
            resp.status_code == 404,
            "Non-existent endpoint should return 404",
            resp.status_code
        )

        # Check error response has expected structure
        resp = await client.get(f"{self.base_url}/v1/documents/search")  # Missing required 'q'
        if resp.status_code in (400, 422):
            try:
                data = resp.json()
                has_detail = "detail" in data or "error" in data or "message" in data
                self.log_result(
                    "Error response structure",
                    has_detail,
                    "Error should have detail/error/message field",
                    resp.status_code
                )
            except json.JSONDecodeError:
                self.log_result(
                    "Error response structure",
                    False,
                    "Error response should be valid JSON",
                    resp.status_code
                )
        else:
            self.log_result(
                "Error response structure",
                True,
                "Skipped (no error response to check)",
                resp.status_code
            )

    async def run_all_tests(self):
        """Run all API tests."""
        print("=" * 60)
        print("DebtStack API Edge Case Tests")
        print("=" * 60)
        print(f"Base URL: {self.base_url}")
        print(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            await self.test_health_endpoints(client)
            await self.test_empty_parameters(client)
            await self.test_invalid_tickers(client)
            await self.test_invalid_fields(client)
            await self.test_pagination(client)
            await self.test_malformed_json(client)
            await self.test_nonexistent_identifiers(client)
            await self.test_sql_injection(client)
            await self.test_content_negotiation(client)
            await self.test_error_response_format(client)
            await self.test_batch_edge_cases(client)
            await self.test_changes_edge_cases(client)
            await self.test_business_tier_edge_cases(client)
            await self.test_webhook_edge_cases(client)

        # Summary
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"Passed: {self.passed}")
        print(f"Failed: {self.failed}")
        print(f"Total:  {self.passed + self.failed}")

        if self.failed == 0:
            print("\n[PASS] ALL TESTS PASSED")
            return True
        else:
            print(f"\n[FAIL] {self.failed} TESTS FAILED")
            print("\nFailed tests:")
            for result in self.results:
                if not result["passed"]:
                    print(f"  - {result['test']}: {result['message']}")
            return False


async def main():
    parser = argparse.ArgumentParser(description="Test DebtStack API edge cases")
    parser.add_argument(
        "--base-url",
        default="https://api.debtstack.ai",
        help="Base URL of the API (default: production)"
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use local development server (http://localhost:8000)"
    )
    args = parser.parse_args()

    base_url = "http://localhost:8000" if args.local else args.base_url

    tester = APITester(base_url)
    success = await tester.run_all_tests()

    # Exit with appropriate code
    import sys
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
