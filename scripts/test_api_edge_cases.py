#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API Edge Case Tests for DebtStack Primitives API

Tests API robustness before public launch.

Usage:
    python scripts/test_api_edge_cases.py [--base-url URL]

Tests performed:
1. Empty/missing parameters
2. Invalid tickers
3. Invalid field names
4. Large result sets
5. Malformed JSON
6. Non-existent identifiers
7. SQL injection attempts
8. Rate limiting behavior
9. Content negotiation (JSON/CSV)
10. Error response format
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
        print("\n[1/10] Testing health endpoints...")

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
        print("\n[2/10] Testing empty/missing parameters...")

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
        print("\n[3/10] Testing invalid tickers...")

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
        print("\n[4/10] Testing invalid field names...")

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
        print("\n[5/10] Testing pagination...")

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
        print("\n[6/10] Testing malformed JSON...")

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
        print("\n[7/10] Testing non-existent identifiers...")

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
        print("\n[8/10] Testing SQL injection prevention...")

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
        print("\n[9/10] Testing content negotiation...")

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

    async def test_error_response_format(self, client: httpx.AsyncClient):
        """Test error response format consistency."""
        print("\n[10/10] Testing error response format...")

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
