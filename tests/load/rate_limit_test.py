"""
DebtStack API Rate Limit Validation Tests

Validates that rate limiting works correctly:
- Fires requests at known rates to test 60/100/500 rpm tier boundaries
- Checks for 429 responses with correct headers (X-RateLimit-Limit, Retry-After)
- Verifies rate limit headers are present on every response
- Tests rate limit window reset behavior

WARNING: This test intentionally triggers rate limits against the target API.
         Use with caution against production. Ensure your API key is on the
         correct tier before running.

Usage:
    # Single user, 2-minute run (validates current tier's limit)
    locust -f tests/load/rate_limit_test.py --headless -u 1 -r 1 -t 120s

    # Test specific scenario via tags:
    locust -f tests/load/rate_limit_test.py --headless -u 1 -r 1 -t 120s \\
        --tags under-limit

    # With custom rate limit override (for testing a known tier)
    EXPECTED_RATE_LIMIT=100 locust -f tests/load/rate_limit_test.py \\
        --headless -u 1 -r 1 -t 120s
"""

import logging
import os
import time

from locust import HttpUser, constant_pacing, events, task, tag

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_KEY = os.getenv("DEBTSTACK_API_KEY") or os.getenv("TEST_API_KEY", "")
BASE_URL = os.getenv("DEBTSTACK_API_URL", "https://api.debtstack.ai")

# Expected rate limit for the API key's tier (rpm)
# pay-as-you-go=60, pro=100, business=500
EXPECTED_RATE_LIMIT = int(os.getenv("EXPECTED_RATE_LIMIT", "60"))

# Rate limit headers to check
RATE_LIMIT_HEADERS = [
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
]


# ---------------------------------------------------------------------------
# Counters for tracking rate limit behavior
# ---------------------------------------------------------------------------

class RateLimitStats:
    """Track rate limit statistics across the test run."""

    def __init__(self):
        self.total_requests = 0
        self.status_200 = 0
        self.status_429 = 0
        self.other_errors = 0
        self.missing_headers = 0
        self.retry_after_values = []
        self.rate_limit_header_values = []
        self.first_429_at_request = None
        self.window_start = time.time()

    def reset_window(self):
        self.total_requests = 0
        self.status_200 = 0
        self.status_429 = 0
        self.other_errors = 0
        self.first_429_at_request = None
        self.window_start = time.time()

    def summary(self):
        elapsed = time.time() - self.window_start
        rpm = (self.total_requests / elapsed * 60) if elapsed > 0 else 0
        return (
            f"Requests: {self.total_requests} ({rpm:.0f} rpm) | "
            f"200s: {self.status_200} | 429s: {self.status_429} | "
            f"Errors: {self.other_errors} | "
            f"Missing headers: {self.missing_headers}"
        )


stats = RateLimitStats()


# ---------------------------------------------------------------------------
# Event hooks
# ---------------------------------------------------------------------------

@events.init.add_listener
def on_init(environment, **kwargs):
    if not API_KEY:
        logger.warning(
            "DEBTSTACK_API_KEY not set — rate limit tests will fail. "
            "Set it via: export DEBTSTACK_API_KEY=ds_..."
        )
    logger.info(
        "Rate limit test configured: expected_limit=%d rpm, target=%s",
        EXPECTED_RATE_LIMIT, BASE_URL,
    )


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    logger.info("=== Rate Limit Test Summary ===")
    logger.info(stats.summary())
    if stats.rate_limit_header_values:
        reported_limits = set(stats.rate_limit_header_values)
        logger.info("Reported rate limits: %s", reported_limits)
    if stats.retry_after_values:
        logger.info(
            "Retry-After values: min=%.1f, max=%.1f, avg=%.1f",
            min(stats.retry_after_values),
            max(stats.retry_after_values),
            sum(stats.retry_after_values) / len(stats.retry_after_values),
        )
    if stats.first_429_at_request:
        logger.info(
            "First 429 occurred at request #%d (expected around #%d)",
            stats.first_429_at_request, EXPECTED_RATE_LIMIT,
        )


# ---------------------------------------------------------------------------
# User Classes
# ---------------------------------------------------------------------------

class UnderLimitUser(HttpUser):
    """Fire requests comfortably under the rate limit to verify no 429s.

    Uses constant_pacing to control exact request rate.
    At 60 rpm limit, fires at ~45 rpm (0.75 req/sec → 1.33s between requests).
    """

    host = BASE_URL
    # Pace at 75% of expected limit → should never trigger 429
    wait_time = constant_pacing(60.0 / (EXPECTED_RATE_LIMIT * 0.75))

    def on_start(self):
        self.client.headers.update({
            "X-API-Key": API_KEY,
            "Accept": "application/json",
        })

    @tag("under-limit")
    @task
    def request_under_limit(self):
        """Make a lightweight request that should NOT be rate limited."""
        stats.total_requests += 1

        with self.client.get(
            "/v1/companies",
            params={"ticker": "AAPL", "fields": "ticker,name"},
            name="/v1/companies (under limit)",
            catch_response=True,
        ) as resp:
            self._check_response(resp, expect_429=False)

    def _check_response(self, resp, expect_429=False):
        """Validate response and track rate limit headers."""
        # Track status codes
        if resp.status_code == 200:
            stats.status_200 += 1
        elif resp.status_code == 429:
            stats.status_429 += 1
            if stats.first_429_at_request is None:
                stats.first_429_at_request = stats.total_requests
                logger.warning(
                    "First 429 at request #%d", stats.total_requests
                )
        else:
            stats.other_errors += 1

        # Check rate limit headers
        headers_lower = {k.lower(): v for k, v in resp.headers.items()}

        has_all_headers = True
        for header in RATE_LIMIT_HEADERS:
            if header not in headers_lower:
                has_all_headers = False

        if not has_all_headers:
            stats.missing_headers += 1

        # Extract header values for analysis
        if "x-ratelimit-limit" in headers_lower:
            try:
                stats.rate_limit_header_values.append(
                    int(headers_lower["x-ratelimit-limit"])
                )
            except (ValueError, TypeError):
                pass

        if resp.status_code == 429:
            retry_after = headers_lower.get("retry-after")
            if retry_after:
                try:
                    stats.retry_after_values.append(float(retry_after))
                except (ValueError, TypeError):
                    pass

        # Validate expectations
        if resp.status_code == 429 and not expect_429:
            resp.failure(
                f"Got 429 at request #{stats.total_requests} — "
                f"expected to stay under limit ({EXPECTED_RATE_LIMIT} rpm)"
            )
        elif resp.status_code == 200:
            resp.success()
        elif resp.status_code == 429:
            resp.success()  # Expected 429
        else:
            resp.failure(f"Unexpected HTTP {resp.status_code}")


class OverLimitUser(HttpUser):
    """Fire requests above the rate limit to verify 429s appear.

    Fires at ~120% of the expected rate limit.
    """

    host = BASE_URL
    # Pace at 120% of expected limit → should trigger 429s
    wait_time = constant_pacing(60.0 / (EXPECTED_RATE_LIMIT * 1.2))

    def on_start(self):
        self.client.headers.update({
            "X-API-Key": API_KEY,
            "Accept": "application/json",
        })

    @tag("over-limit")
    @task
    def request_over_limit(self):
        """Make requests that SHOULD eventually trigger rate limiting."""
        stats.total_requests += 1

        with self.client.get(
            "/v1/companies",
            params={"ticker": "AAPL", "fields": "ticker,name"},
            name="/v1/companies (over limit)",
            catch_response=True,
        ) as resp:
            # After enough requests, we expect some 429s
            if resp.status_code == 200:
                stats.status_200 += 1
                resp.success()
            elif resp.status_code == 429:
                stats.status_429 += 1
                if stats.first_429_at_request is None:
                    stats.first_429_at_request = stats.total_requests
                    logger.info(
                        "First 429 at request #%d (expected ~%d)",
                        stats.total_requests, EXPECTED_RATE_LIMIT,
                    )

                # Validate 429 response has proper headers
                headers_lower = {k.lower(): v for k, v in resp.headers.items()}

                if "retry-after" not in headers_lower:
                    resp.failure("429 response missing Retry-After header")
                else:
                    try:
                        retry_val = float(headers_lower["retry-after"])
                        stats.retry_after_values.append(retry_val)
                        resp.success()
                    except (ValueError, TypeError):
                        resp.failure(
                            f"Invalid Retry-After value: "
                            f"{headers_lower['retry-after']}"
                        )

                if "x-ratelimit-limit" in headers_lower:
                    try:
                        limit_val = int(headers_lower["x-ratelimit-limit"])
                        stats.rate_limit_header_values.append(limit_val)
                    except (ValueError, TypeError):
                        pass
            else:
                stats.other_errors += 1
                resp.failure(f"Unexpected HTTP {resp.status_code}")


class BurstUser(HttpUser):
    """Burst test: send a rapid burst, then wait for window reset.

    Sends requests as fast as possible for a few seconds, then pauses
    for 60s to let the rate limit window reset, then bursts again.
    """

    host = BASE_URL
    wait_time = constant_pacing(0.1)  # 10 req/sec burst

    def on_start(self):
        self.client.headers.update({
            "X-API-Key": API_KEY,
            "Accept": "application/json",
        })
        self._burst_count = 0
        self._burst_limit = EXPECTED_RATE_LIMIT + 10  # Go slightly over
        self._in_cooldown = False
        self._cooldown_start = 0

    @tag("burst")
    @task
    def burst_request(self):
        """Send burst of requests, then cooldown."""
        # If in cooldown, check if 60s has elapsed
        if self._in_cooldown:
            elapsed = time.time() - self._cooldown_start
            if elapsed < 65:  # Wait 65s for safety margin
                time.sleep(1)
                return
            else:
                logger.info("Cooldown complete — starting new burst")
                self._in_cooldown = False
                self._burst_count = 0
                stats.reset_window()

        self._burst_count += 1
        stats.total_requests += 1

        with self.client.get(
            "/v1/companies",
            params={"ticker": "AAPL", "fields": "ticker,name"},
            name="/v1/companies (burst)",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                stats.status_200 += 1
                resp.success()
            elif resp.status_code == 429:
                stats.status_429 += 1
                resp.success()  # Expected after exceeding limit

                if stats.first_429_at_request is None:
                    stats.first_429_at_request = stats.total_requests
                    logger.info(
                        "Burst: first 429 at request #%d", stats.total_requests
                    )
            else:
                stats.other_errors += 1
                resp.failure(f"Unexpected HTTP {resp.status_code}")

        # After burst limit, enter cooldown
        if self._burst_count >= self._burst_limit:
            logger.info(
                "Burst complete: %d requests sent, %d 429s. "
                "Entering 65s cooldown for window reset.",
                self._burst_count, stats.status_429,
            )
            self._in_cooldown = True
            self._cooldown_start = time.time()


class HeaderValidationUser(HttpUser):
    """Validates rate limit headers are present on every response.

    Fires at a slow, safe rate and checks every response for required headers.
    """

    host = BASE_URL
    wait_time = constant_pacing(5.0)  # 12 rpm — well under any tier

    def on_start(self):
        self.client.headers.update({
            "X-API-Key": API_KEY,
            "Accept": "application/json",
        })

    @tag("headers")
    @task
    def validate_headers(self):
        """Check every response has rate limit headers."""
        stats.total_requests += 1

        with self.client.get(
            "/v1/companies",
            params={"ticker": "AAPL", "fields": "ticker,name"},
            name="/v1/companies (header check)",
            catch_response=True,
        ) as resp:
            if resp.status_code not in (200, 429):
                stats.other_errors += 1
                resp.failure(f"HTTP {resp.status_code}")
                return

            if resp.status_code == 200:
                stats.status_200 += 1
            else:
                stats.status_429 += 1

            headers_lower = {k.lower(): v for k, v in resp.headers.items()}

            missing = []
            for header in RATE_LIMIT_HEADERS:
                if header not in headers_lower:
                    missing.append(header)

            if missing:
                stats.missing_headers += 1
                resp.failure(f"Missing rate limit headers: {missing}")
            else:
                # Validate header values are reasonable
                try:
                    limit = int(headers_lower["x-ratelimit-limit"])
                    remaining = int(headers_lower["x-ratelimit-remaining"])

                    if limit not in (60, 100, 500):
                        logger.warning(
                            "Unexpected rate limit value: %d (expected 60/100/500)",
                            limit,
                        )

                    if remaining < 0:
                        resp.failure(f"Negative remaining: {remaining}")
                    elif remaining > limit:
                        resp.failure(
                            f"Remaining ({remaining}) > limit ({limit})"
                        )
                    else:
                        resp.success()
                except (ValueError, TypeError) as e:
                    resp.failure(f"Invalid header values: {e}")
