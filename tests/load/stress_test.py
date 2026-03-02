"""
DebtStack API Stress / Breaking Point Test

Ramps users from 1 → 50 → 100+ in stages to find the degradation point.
Uses a custom LoadTestShape for step-load pattern.

Monitors:
- Response time percentiles (p50, p95, p99)
- Error rates and timeout rates
- When p95 latency exceeds thresholds (2s, 5s, 10s)

Usage:
    # Run with default shape (5-minute test)
    locust -f tests/load/stress_test.py --headless -t 300s

    # Customize via env vars
    MAX_USERS=200 STEP_DURATION=45 locust -f tests/load/stress_test.py \\
        --headless -t 600s

    # Web UI for real-time monitoring
    locust -f tests/load/stress_test.py
"""

import json
import logging
import os
import random
import time

from locust import HttpUser, LoadTestShape, between, events, task

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_KEY = os.getenv("DEBTSTACK_API_KEY", "")
BASE_URL = os.getenv("DEBTSTACK_API_URL", "https://api.debtstack.ai")

# Step-load configuration
MAX_USERS = int(os.getenv("MAX_USERS", "100"))
STEP_USERS = int(os.getenv("STEP_USERS", "10"))     # Users added per step
STEP_DURATION = int(os.getenv("STEP_DURATION", "30"))  # Seconds per step
SPAWN_RATE = float(os.getenv("SPAWN_RATE", "2"))     # Users per second during ramp

# Latency thresholds (milliseconds)
LATENCY_WARN_MS = 2000     # 2s — warning threshold
LATENCY_DANGER_MS = 5000   # 5s — danger threshold
LATENCY_CRITICAL_MS = 10000  # 10s — critical / breaking point

# Test data
TICKERS = ["AAPL", "CHTR", "RIG", "CVS", "T", "VZ", "BA", "F", "GM", "AAL",
           "MSFT", "GOOGL", "AMZN", "META", "JPM", "BAC", "GS", "WFC"]


# ---------------------------------------------------------------------------
# Performance tracker
# ---------------------------------------------------------------------------

class PerformanceTracker:
    """Tracks response times and detects degradation."""

    def __init__(self):
        self.step_results = []
        self.current_step_times = []
        self.current_step_errors = 0
        self.current_step_requests = 0
        self.current_step = 0
        self.current_users = 0
        self.breached_warn = False
        self.breached_danger = False
        self.breached_critical = False
        self.breaking_point_users = None

    def record(self, response_time_ms, is_error=False):
        self.current_step_times.append(response_time_ms)
        self.current_step_requests += 1
        if is_error:
            self.current_step_errors += 1

    def finalize_step(self, user_count):
        """Finalize current step and check thresholds."""
        if not self.current_step_times:
            return

        times = sorted(self.current_step_times)
        n = len(times)

        p50 = times[int(n * 0.50)] if n > 0 else 0
        p95 = times[int(n * 0.95)] if n > 0 else 0
        p99 = times[int(n * 0.99)] if n > 0 else 0
        avg = sum(times) / n if n > 0 else 0
        error_rate = self.current_step_errors / self.current_step_requests if self.current_step_requests > 0 else 0

        step_data = {
            "step": self.current_step,
            "users": user_count,
            "requests": self.current_step_requests,
            "errors": self.current_step_errors,
            "error_rate": error_rate,
            "p50_ms": p50,
            "p95_ms": p95,
            "p99_ms": p99,
            "avg_ms": avg,
            "min_ms": times[0] if times else 0,
            "max_ms": times[-1] if times else 0,
        }
        self.step_results.append(step_data)

        # Log step summary
        status = "OK"
        if p95 >= LATENCY_CRITICAL_MS:
            status = "CRITICAL"
            if not self.breached_critical:
                self.breached_critical = True
                self.breaking_point_users = user_count
                logger.error(
                    "BREAKING POINT at %d users — p95=%.0fms exceeds %dms",
                    user_count, p95, LATENCY_CRITICAL_MS,
                )
        elif p95 >= LATENCY_DANGER_MS:
            status = "DANGER"
            if not self.breached_danger:
                self.breached_danger = True
                logger.warning(
                    "DANGER threshold at %d users — p95=%.0fms exceeds %dms",
                    user_count, p95, LATENCY_DANGER_MS,
                )
        elif p95 >= LATENCY_WARN_MS:
            status = "WARN"
            if not self.breached_warn:
                self.breached_warn = True
                logger.warning(
                    "WARNING threshold at %d users — p95=%.0fms exceeds %dms",
                    user_count, p95, LATENCY_WARN_MS,
                )

        logger.info(
            "Step %d | %d users | %d reqs | p50=%.0fms p95=%.0fms p99=%.0fms | "
            "errors=%.1f%% | %s",
            self.current_step, user_count, self.current_step_requests,
            p50, p95, p99, error_rate * 100, status,
        )

        # Reset for next step
        self.current_step += 1
        self.current_step_times = []
        self.current_step_errors = 0
        self.current_step_requests = 0
        self.current_users = user_count

    def summary(self):
        """Print final summary."""
        lines = [
            "",
            "=" * 70,
            "STRESS TEST RESULTS",
            "=" * 70,
            f"{'Step':>4} | {'Users':>5} | {'Reqs':>5} | {'p50':>7} | {'p95':>7} | {'p99':>7} | {'Errors':>7} | Status",
            "-" * 70,
        ]
        for s in self.step_results:
            status = "OK"
            if s["p95_ms"] >= LATENCY_CRITICAL_MS:
                status = "CRITICAL"
            elif s["p95_ms"] >= LATENCY_DANGER_MS:
                status = "DANGER"
            elif s["p95_ms"] >= LATENCY_WARN_MS:
                status = "WARN"

            lines.append(
                f"{s['step']:>4} | {s['users']:>5} | {s['requests']:>5} | "
                f"{s['p50_ms']:>6.0f}ms | {s['p95_ms']:>6.0f}ms | {s['p99_ms']:>6.0f}ms | "
                f"{s['error_rate']*100:>5.1f}% | {status}"
            )

        lines.append("-" * 70)

        if self.breaking_point_users:
            lines.append(
                f"BREAKING POINT: {self.breaking_point_users} users "
                f"(p95 > {LATENCY_CRITICAL_MS}ms)"
            )
        else:
            lines.append(
                f"No breaking point found up to {MAX_USERS} users "
                f"(p95 stayed under {LATENCY_CRITICAL_MS}ms)"
            )

        lines.append("=" * 70)
        return "\n".join(lines)


tracker = PerformanceTracker()


# ---------------------------------------------------------------------------
# Custom Load Shape — Step Load
# ---------------------------------------------------------------------------

class StepLoadShape(LoadTestShape):
    """Step-load pattern: ramp users in stages.

    Each stage adds STEP_USERS users, holds for STEP_DURATION seconds,
    then steps up again until MAX_USERS is reached.

    Example with defaults (STEP_USERS=10, STEP_DURATION=30, MAX_USERS=100):
      0-30s:   10 users
      30-60s:  20 users
      60-90s:  30 users
      ...
      270-300s: 100 users
    """

    _last_step = -1

    def tick(self):
        run_time = self.get_run_time()

        current_step = int(run_time // STEP_DURATION)
        target_users = min((current_step + 1) * STEP_USERS, MAX_USERS)

        # Log step transitions and finalize previous step
        if current_step != self._last_step:
            if self._last_step >= 0:
                # Finalize the previous step
                prev_users = min((self._last_step + 1) * STEP_USERS, MAX_USERS)
                tracker.finalize_step(prev_users)
            logger.info(
                "Step %d: ramping to %d users (t=%.0fs)",
                current_step, target_users, run_time,
            )
            self._last_step = current_step

        return target_users, SPAWN_RATE


# ---------------------------------------------------------------------------
# Event hooks
# ---------------------------------------------------------------------------

@events.init.add_listener
def on_init(environment, **kwargs):
    if not API_KEY:
        logger.warning(
            "DEBTSTACK_API_KEY not set — stress test will fail. "
            "Set it via: export DEBTSTACK_API_KEY=ds_..."
        )

    total_steps = MAX_USERS // STEP_USERS
    total_time = total_steps * STEP_DURATION
    logger.info(
        "Stress test: %d → %d users in %d steps of %d users, "
        "%ds per step (%ds total)",
        STEP_USERS, MAX_USERS, total_steps, STEP_USERS,
        STEP_DURATION, total_time,
    )


@events.request.add_listener
def on_request(request_type, name, response_time, response_length,
               exception, **kwargs):
    """Record every request for percentile tracking."""
    is_error = exception is not None
    if not is_error:
        response = kwargs.get("response")
        if response and response.status_code >= 400 and response.status_code != 429:
            is_error = True
    tracker.record(response_time, is_error=is_error)


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    # Finalize last step
    if tracker.current_step_times:
        target_users = min((tracker.current_step + 1) * STEP_USERS, MAX_USERS)
        tracker.finalize_step(target_users)

    logger.info(tracker.summary())


# ---------------------------------------------------------------------------
# User Class
# ---------------------------------------------------------------------------

class StressTestUser(HttpUser):
    """Mixed workload user for stress testing.

    Simulates a variety of API calls to stress different endpoints.
    Shorter wait times than normal load test to increase pressure.
    """

    host = BASE_URL
    wait_time = between(0.5, 1.5)  # Faster than normal to build pressure

    def on_start(self):
        self.client.headers.update({
            "X-API-Key": API_KEY,
            "Accept": "application/json",
        })

    def _validate(self, resp, name):
        if resp.status_code == 200:
            try:
                body = resp.json()
                if "data" not in body:
                    resp.failure(f"[{name}] Missing 'data'")
                else:
                    resp.success()
            except json.JSONDecodeError:
                resp.failure(f"[{name}] Invalid JSON")
        elif resp.status_code == 429:
            resp.success()  # Rate limited is expected under stress
        else:
            resp.failure(f"[{name}] HTTP {resp.status_code}")

    @task(5)
    def get_company(self):
        """Lightweight company lookup — most common operation."""
        ticker = random.choice(TICKERS)
        with self.client.get(
            "/v1/companies",
            params={"ticker": ticker, "fields": "ticker,name,net_leverage_ratio"},
            name="/v1/companies",
            catch_response=True,
        ) as resp:
            self._validate(resp, "companies")

    @task(4)
    def get_bonds(self):
        """Bond search — moderate weight."""
        ticker = random.choice(TICKERS)
        with self.client.get(
            "/v1/bonds",
            params={
                "ticker": ticker,
                "fields": "name,cusip,coupon_rate,seniority",
                "limit": "20",
            },
            name="/v1/bonds",
            catch_response=True,
        ) as resp:
            self._validate(resp, "bonds")

    @task(3)
    def get_financials(self):
        """Financial data lookup."""
        ticker = random.choice(TICKERS)
        with self.client.get(
            "/v1/financials",
            params={"ticker": ticker, "limit": "1"},
            name="/v1/financials",
            catch_response=True,
        ) as resp:
            self._validate(resp, "financials")

    @task(2)
    def search_documents(self):
        """Document search — heavier operation."""
        keywords = ["covenant", "leverage", "collateral", "guarantor", "indenture"]
        with self.client.get(
            "/v1/documents/search",
            params={
                "q": random.choice(keywords),
                "ticker": random.choice(TICKERS),
                "mode": "keyword",
                "limit": "5",
            },
            name="/v1/documents/search",
            catch_response=True,
        ) as resp:
            self._validate(resp, "documents/search")

    @task(2)
    def get_covenants(self):
        """Covenant lookup."""
        ticker = random.choice(TICKERS)
        with self.client.get(
            "/v1/covenants",
            params={"ticker": ticker},
            name="/v1/covenants",
            catch_response=True,
        ) as resp:
            self._validate(resp, "covenants")

    @task(1)
    def resolve_bond(self):
        """Bond resolution — free-text."""
        queries = ["RIG 8% 2027", "CHTR 5.25 2027", "BA 3.625 2031"]
        with self.client.get(
            "/v1/bonds/resolve",
            params={"q": random.choice(queries)},
            name="/v1/bonds/resolve",
            catch_response=True,
        ) as resp:
            self._validate(resp, "bonds/resolve")

    @task(1)
    def traverse_entities(self):
        """Entity traversal — heavier operation."""
        ticker = random.choice(TICKERS)
        with self.client.post(
            "/v1/entities/traverse",
            json={"start_ticker": ticker, "direction": "down"},
            name="/v1/entities/traverse",
            catch_response=True,
        ) as resp:
            self._validate(resp, "entities/traverse")

    @task(1)
    def get_collateral(self):
        """Collateral lookup."""
        ticker = random.choice(TICKERS)
        with self.client.get(
            "/v1/collateral",
            params={"ticker": ticker},
            name="/v1/collateral",
            catch_response=True,
        ) as resp:
            self._validate(resp, "collateral")

    @task(1)
    def batch_request(self):
        """Batch request — heaviest operation."""
        ticker = random.choice(TICKERS)
        with self.client.post(
            "/v1/batch",
            json={
                "operations": [
                    {"primitive": "/v1/companies", "params": {"ticker": ticker}},
                    {"primitive": "/v1/bonds", "params": {"ticker": ticker, "limit": "5"}},
                ],
            },
            name="/v1/batch",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code == 429:
                resp.success()
            else:
                resp.failure(f"Batch HTTP {resp.status_code}")
