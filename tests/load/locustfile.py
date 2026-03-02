"""
DebtStack API Load Tests — Main Locust File

Simulates realistic API usage patterns across 4 user personas:
- CreditAnalystUser: Discovery workflow (search → resolve → traverse)
- ScreeningUser: Bulk screening with filters
- ResearchUser: Deep dive (documents, structure, changes)
- BatchUser: Multi-primitive batch operations

Covers all 10 public Primitives endpoints.

Usage:
    # Headless (5 users, 1 user/sec spawn, 60s duration)
    locust -f tests/load/locustfile.py --headless -u 5 -r 1 -t 60s

    # Web UI
    locust -f tests/load/locustfile.py
    # Then open http://localhost:8089
"""

import json
import logging
import os
import random

from locust import HttpUser, between, events, task

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_KEY = os.getenv("DEBTSTACK_API_KEY") or os.getenv("TEST_API_KEY", "")
BASE_URL = os.getenv("DEBTSTACK_API_URL", "https://api.debtstack.ai")

# Realistic tickers from the eval suite / database
TICKERS = ["AAPL", "CHTR", "RIG", "CVS", "T", "VZ", "BA", "F", "GM", "AAL",
           "MSFT", "GOOGL", "AMZN", "META", "JPM", "BAC", "GS", "WFC"]

SECTORS = ["Technology", "Energy", "Healthcare", "Telecommunications",
           "Financial Services", "Consumer Discretionary", "Industrials"]

SENIORITIES = ["senior_secured", "senior_unsecured", "subordinated"]

BOND_QUERIES = [
    "RIG 8% 2027",
    "CHTR 5.25 2027",
    "CVS 5.05 2048",
    "BA 3.625 2031",
    "T 4.35 2045",
]

DOC_KEYWORDS = [
    "covenant", "leverage ratio", "restricted payments",
    "change of control", "collateral", "guarantor",
    "subordination", "indenture", "credit agreement",
]

TRAVERSE_QUERIES = [
    {"start": {"type": "company", "id": "CHTR"}, "relationships": ["guarantees"], "direction": "inbound"},
    {"start": {"type": "company", "id": "RIG"}, "relationships": ["guarantees"], "direction": "inbound"},
    {"start": {"type": "company", "id": "CVS"}, "relationships": ["subsidiaries"], "direction": "outbound"},
    {"start": {"type": "company", "id": "BA"}, "relationships": ["subsidiaries"], "direction": "outbound"},
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def random_ticker():
    return random.choice(TICKERS)

def random_tickers(n=3):
    return ",".join(random.sample(TICKERS, min(n, len(TICKERS))))

def validate_response(response, name=""):
    """Check response has expected structure.

    Most endpoints return {"data": [...]}, but some (e.g. /v1/bonds/resolve)
    return {"data": {...}} with a dict. Both are accepted.
    """
    if response.status_code == 200:
        try:
            body = response.json()
            if "data" not in body:
                response.failure(f"[{name}] Missing 'data' in response")
                return False
            data = body["data"]
            if isinstance(data, list):
                logger.debug("[%s] OK — %d items, %d bytes",
                             name, len(data), len(response.content))
            elif isinstance(data, dict):
                logger.debug("[%s] OK — dict response, %d bytes",
                             name, len(response.content))
            else:
                response.failure(f"[{name}] 'data' has unexpected type: {type(data).__name__}")
                return False
            return True
        except json.JSONDecodeError:
            response.failure(f"[{name}] Invalid JSON")
            return False
    elif response.status_code == 429:
        # Rate limited — not a test failure, but log it
        logger.info("[%s] Rate limited (429)", name)
        response.success()
        return True
    elif response.status_code in (400, 404):
        # Some endpoints return 400/404 for valid queries (e.g., ticker has no
        # covenants, no changes snapshot). Not a load-test failure.
        logger.debug("[%s] HTTP %d (expected for some data)", name, response.status_code)
        response.success()
        return True
    else:
        response.failure(f"[{name}] HTTP {response.status_code}")
        return False


def validate_batch_response(response, name=""):
    """Check batch response structure. Batch returns {"results": [...]}."""
    if response.status_code == 200:
        try:
            body = response.json()
            results = body.get("data", body.get("results", []))
            if not results:
                response.failure(f"[{name}] Batch returned no results")
            else:
                response.success()
        except json.JSONDecodeError:
            response.failure(f"[{name}] Invalid JSON")
    elif response.status_code == 429:
        logger.info("[%s] Rate limited (429)", name)
        response.success()
    else:
        response.failure(f"[{name}] HTTP {response.status_code}")


# ---------------------------------------------------------------------------
# Event hooks
# ---------------------------------------------------------------------------

@events.init.add_listener
def on_init(environment, **kwargs):
    if not API_KEY:
        logger.warning(
            "DEBTSTACK_API_KEY not set — requests will fail with 401. "
            "Set it via: export DEBTSTACK_API_KEY=ds_..."
        )


# ---------------------------------------------------------------------------
# User Classes
# ---------------------------------------------------------------------------

class DebtStackUser(HttpUser):
    """Base class with auth headers and shared config."""

    abstract = True
    host = BASE_URL
    wait_time = between(1, 3)

    def on_start(self):
        self.client.headers.update({
            "X-API-Key": API_KEY,
            "Accept": "application/json",
        })


class CreditAnalystUser(DebtStackUser):
    """Discovery workflow: search companies → search bonds → resolve → traverse.

    Simulates an analyst discovering a company's debt structure.
    Weight 3 = most common user type.
    """

    weight = 3

    @task(3)
    def search_companies_by_ticker(self):
        """Look up a specific company."""
        ticker = random_ticker()
        with self.client.get(
            "/v1/companies",
            params={"ticker": ticker, "fields": "ticker,name,sector,net_leverage_ratio,total_debt"},
            name="/v1/companies?ticker=X",
            catch_response=True,
        ) as resp:
            validate_response(resp, f"companies?ticker={ticker}")

    @task(3)
    def search_bonds_by_ticker(self):
        """Get bonds for a company."""
        ticker = random_ticker()
        with self.client.get(
            "/v1/bonds",
            params={
                "ticker": ticker,
                "fields": "name,cusip,coupon_rate,maturity_date,seniority,outstanding",
                "limit": "50",
            },
            name="/v1/bonds?ticker=X",
            catch_response=True,
        ) as resp:
            validate_response(resp, f"bonds?ticker={ticker}")

    @task(2)
    def resolve_bond(self):
        """Free-text bond resolution."""
        query = random.choice(BOND_QUERIES)
        with self.client.get(
            "/v1/bonds/resolve",
            params={"q": query},
            name="/v1/bonds/resolve",
            catch_response=True,
        ) as resp:
            validate_response(resp, f"bonds/resolve?q={query}")

    @task(1)
    def traverse_guarantors(self):
        """Get guarantor structure for a company."""
        tq = random.choice(TRAVERSE_QUERIES)
        with self.client.post(
            "/v1/entities/traverse",
            json=tq,
            name="/v1/entities/traverse",
            catch_response=True,
        ) as resp:
            validate_response(resp, f"entities/traverse ({tq['start']['id']})")


class ScreeningUser(DebtStackUser):
    """Bulk screening: filter bonds and companies by financial criteria.

    Simulates a portfolio manager screening the universe.
    Weight 2.
    """

    weight = 2

    @task(3)
    def screen_bonds_by_yield(self):
        """Screen bonds by yield range."""
        min_ytm = round(random.uniform(5.0, 10.0), 1)
        with self.client.get(
            "/v1/bonds",
            params={
                "min_ytm": str(min_ytm),
                "has_pricing": "true",
                "fields": "name,cusip,coupon_rate,pricing,seniority",
                "limit": "50",
            },
            name="/v1/bonds?min_ytm=X",
            catch_response=True,
        ) as resp:
            validate_response(resp, f"bonds?min_ytm={min_ytm}")

    @task(2)
    def screen_bonds_by_seniority(self):
        """Screen bonds by seniority type."""
        seniority = random.choice(SENIORITIES)
        with self.client.get(
            "/v1/bonds",
            params={
                "seniority": seniority,
                "fields": "name,cusip,coupon_rate,seniority,outstanding",
                "limit": "50",
            },
            name="/v1/bonds?seniority=X",
            catch_response=True,
        ) as resp:
            validate_response(resp, f"bonds?seniority={seniority}")

    @task(3)
    def screen_companies_by_leverage(self):
        """Screen companies by leverage range."""
        min_lev = round(random.uniform(3.0, 8.0), 1)
        with self.client.get(
            "/v1/companies",
            params={
                "min_leverage": str(min_lev),
                "fields": "ticker,name,net_leverage_ratio,total_debt,sector",
                "sort": "-net_leverage_ratio",
                "limit": "20",
            },
            name="/v1/companies?min_leverage=X",
            catch_response=True,
        ) as resp:
            validate_response(resp, f"companies?min_leverage={min_lev}")

    @task(2)
    def screen_companies_by_sector(self):
        """Screen companies by sector."""
        sector = random.choice(SECTORS)
        with self.client.get(
            "/v1/companies",
            params={
                "sector": sector,
                "fields": "ticker,name,sector,net_leverage_ratio",
                "limit": "50",
            },
            name="/v1/companies?sector=X",
            catch_response=True,
        ) as resp:
            validate_response(resp, f"companies?sector={sector}")

    @task(1)
    def get_financials(self):
        """Get financial data for a company."""
        ticker = random_ticker()
        with self.client.get(
            "/v1/financials",
            params={
                "ticker": ticker,
                "fields": "ticker,revenue,ebitda,total_debt,cash",
                "limit": "4",
            },
            name="/v1/financials?ticker=X",
            catch_response=True,
        ) as resp:
            validate_response(resp, f"financials?ticker={ticker}")


class ResearchUser(DebtStackUser):
    """Deep-dive research: documents, covenants, collateral, changes.

    Simulates an analyst doing detailed credit research.
    Weight 1.
    """

    weight = 1

    @task(3)
    def search_documents(self):
        """Search SEC filing documents."""
        keyword = random.choice(DOC_KEYWORDS)
        ticker = random_ticker()
        with self.client.get(
            "/v1/documents/search",
            params={
                "q": keyword,
                "ticker": ticker,
                "mode": "keyword",
                "limit": "10",
            },
            name="/v1/documents/search",
            catch_response=True,
        ) as resp:
            validate_response(resp, f"documents/search?q={keyword}&ticker={ticker}")

    @task(2)
    def get_covenants(self):
        """Get covenant data for a company."""
        ticker = random_ticker()
        with self.client.get(
            "/v1/covenants",
            params={
                "ticker": ticker,
                "fields": "ticker,type,description,threshold",
            },
            name="/v1/covenants?ticker=X",
            catch_response=True,
        ) as resp:
            validate_response(resp, f"covenants?ticker={ticker}")

    @task(2)
    def get_collateral(self):
        """Get collateral data for a company."""
        ticker = random_ticker()
        with self.client.get(
            "/v1/collateral",
            params={"ticker": ticker},
            name="/v1/collateral?ticker=X",
            catch_response=True,
        ) as resp:
            validate_response(resp, f"collateral?ticker={ticker}")

    @task(1)
    def get_changes(self):
        """Get recent changes for a company."""
        ticker = random_ticker()
        with self.client.get(
            f"/v1/companies/{ticker}/changes",
            params={"since": "2025-01-01"},
            name="/v1/companies/{ticker}/changes",
            catch_response=True,
        ) as resp:
            validate_response(resp, f"companies/{ticker}/changes")

    @task(1)
    def traverse_structure(self):
        """Get corporate structure."""
        ticker = random_ticker()
        with self.client.post(
            "/v1/entities/traverse",
            json={
                "start": {"type": "company", "id": ticker},
                "relationships": ["subsidiaries"],
                "direction": "outbound",
            },
            name="/v1/entities/traverse",
            catch_response=True,
        ) as resp:
            validate_response(resp, f"entities/traverse ({ticker})")


class BatchUser(DebtStackUser):
    """Batch operations: multi-primitive calls.

    Simulates an AI agent making batch requests.
    Weight 1.
    """

    weight = 1

    @task(3)
    def batch_company_overview(self):
        """Batch: get company + bonds + financials in one call."""
        ticker = random_ticker()
        with self.client.post(
            "/v1/batch",
            json={
                "operations": [
                    {
                        "primitive": "/v1/companies",
                        "params": {"ticker": ticker, "fields": "ticker,name,net_leverage_ratio"},
                    },
                    {
                        "primitive": "/v1/bonds",
                        "params": {"ticker": ticker, "fields": "name,cusip,coupon_rate", "limit": "10"},
                    },
                    {
                        "primitive": "/v1/financials",
                        "params": {"ticker": ticker, "limit": "1"},
                    },
                ],
            },
            name="/v1/batch (3 ops)",
            catch_response=True,
        ) as resp:
            validate_batch_response(resp, "batch (3 ops)")

    @task(2)
    def batch_multi_ticker(self):
        """Batch: compare multiple companies."""
        tickers = random.sample(TICKERS, 3)
        with self.client.post(
            "/v1/batch",
            json={
                "operations": [
                    {
                        "primitive": "/v1/companies",
                        "params": {"ticker": t, "fields": "ticker,name,net_leverage_ratio,total_debt"},
                    }
                    for t in tickers
                ],
            },
            name="/v1/batch (multi-ticker)",
            catch_response=True,
        ) as resp:
            validate_batch_response(resp, "batch (multi-ticker)")

    @task(1)
    def batch_credit_analysis(self):
        """Batch: full credit analysis (company + bonds + covenants + collateral)."""
        ticker = random_ticker()
        with self.client.post(
            "/v1/batch",
            json={
                "operations": [
                    {
                        "primitive": "/v1/companies",
                        "params": {"ticker": ticker},
                    },
                    {
                        "primitive": "/v1/bonds",
                        "params": {"ticker": ticker, "limit": "20"},
                    },
                    {
                        "primitive": "/v1/covenants",
                        "params": {"ticker": ticker},
                    },
                    {
                        "primitive": "/v1/collateral",
                        "params": {"ticker": ticker},
                    },
                ],
            },
            name="/v1/batch (credit analysis)",
            catch_response=True,
        ) as resp:
            validate_batch_response(resp, "batch (credit analysis)")
