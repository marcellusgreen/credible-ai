"""
Staleness & Data Freshness Evaluation Tests

8 use cases validating data freshness across 4 categories:
1. Pricing staleness distribution - Are most bonds fresh (<7 days)?
2. Severe pricing staleness - Are any bonds severely stale (>30 days)?
3. Pricing source distribution - Is most pricing from TRACE (not estimated)?
4. Financials recency - Is the newest financial record within 9 months?
5. Financials coverage - Do most companies have financial data?
6. Company metrics freshness - Are computed metrics recent (<90 days)?
7. Pricing history recent snapshots - Are daily snapshots running?
8. Pricing history no large gaps - Are there gaps in the last 30 days?
"""

import pytest
import httpx
from datetime import datetime, date, timedelta

from tests.eval.scoring import EvalResult, PrimitiveScore


PRIMITIVE = "staleness"


# =============================================================================
# CATEGORY 1: BOND PRICING STALENESS
# =============================================================================

@pytest.mark.eval
@pytest.mark.requires_pricing
def test_pricing_staleness_distribution(api_client: httpx.Client):
    """Fail if >20% of bonds have staleness >7 days. Report median and p90."""
    max_staleness_days = 7
    max_stale_pct = 0.20

    response = api_client.get("/v1/bonds", params={
        "has_pricing": "true",
        "fields": "name,cusip,pricing",
        "limit": "100",
    })
    response.raise_for_status()
    data = response.json()

    bonds = data["data"]
    if not bonds:
        pytest.skip("No bonds with pricing data")

    staleness_values = []
    for bond in bonds:
        pricing = bond.get("pricing")
        if pricing and pricing.get("staleness_days") is not None:
            staleness_values.append(pricing["staleness_days"])

    if not staleness_values:
        pytest.skip("No bonds with staleness_days field")

    staleness_values.sort()
    n = len(staleness_values)
    median = staleness_values[n // 2]
    p90 = staleness_values[int(n * 0.9)]

    stale_count = sum(1 for s in staleness_values if s > max_staleness_days)
    stale_pct = stale_count / n

    assert stale_pct <= max_stale_pct, (
        f"{stale_count}/{n} ({stale_pct:.0%}) bonds have staleness >{max_staleness_days} days "
        f"(threshold: {max_stale_pct:.0%}). Median={median}d, P90={p90}d"
    )


@pytest.mark.eval
@pytest.mark.requires_pricing
def test_pricing_staleness_severe(api_client: httpx.Client):
    """Fail if >5% of priced bonds have staleness >30 days (broken pipeline)."""
    severe_threshold_days = 30
    max_severe_pct = 0.05

    response = api_client.get("/v1/bonds", params={
        "has_pricing": "true",
        "fields": "name,cusip,pricing",
        "limit": "100",
    })
    response.raise_for_status()
    data = response.json()

    bonds = data["data"]
    if not bonds:
        pytest.skip("No bonds with pricing data")

    priced_bonds = []
    severely_stale = []
    for bond in bonds:
        pricing = bond.get("pricing")
        if pricing and pricing.get("staleness_days") is not None:
            priced_bonds.append(bond)
            if pricing["staleness_days"] > severe_threshold_days:
                severely_stale.append({
                    "name": bond.get("name"),
                    "cusip": bond.get("cusip"),
                    "staleness_days": pricing["staleness_days"],
                })

    if not priced_bonds:
        pytest.skip("No bonds with staleness_days field")

    severe_pct = len(severely_stale) / len(priced_bonds)

    assert severe_pct <= max_severe_pct, (
        f"{len(severely_stale)}/{len(priced_bonds)} ({severe_pct:.0%}) bonds "
        f"have staleness >{severe_threshold_days} days (threshold: {max_severe_pct:.0%}). "
        f"Examples: {severely_stale[:5]}"
    )


@pytest.mark.eval
@pytest.mark.requires_pricing
def test_pricing_source_distribution(api_client: httpx.Client):
    """Fail if >40% of priced bonds have source == 'estimated'."""
    max_estimated_pct = 0.40

    response = api_client.get("/v1/bonds", params={
        "has_pricing": "true",
        "fields": "name,cusip,pricing",
        "limit": "100",
    })
    response.raise_for_status()
    data = response.json()

    bonds = data["data"]
    if not bonds:
        pytest.skip("No bonds with pricing data")

    source_counts = {}
    total_with_source = 0
    for bond in bonds:
        pricing = bond.get("pricing")
        if pricing and pricing.get("price_source"):
            source = pricing["price_source"].lower()
            source_counts[source] = source_counts.get(source, 0) + 1
            total_with_source += 1
        elif pricing and pricing.get("source"):
            source = pricing["source"].lower()
            source_counts[source] = source_counts.get(source, 0) + 1
            total_with_source += 1

    if total_with_source == 0:
        pytest.skip("No bonds with pricing source info")

    estimated_count = source_counts.get("estimated", 0)
    estimated_pct = estimated_count / total_with_source

    assert estimated_pct <= max_estimated_pct, (
        f"{estimated_count}/{total_with_source} ({estimated_pct:.0%}) bonds have "
        f"estimated pricing (threshold: {max_estimated_pct:.0%}). "
        f"Source distribution: {source_counts}"
    )


# =============================================================================
# CATEGORY 2: FINANCIAL DATA FRESHNESS
# =============================================================================

@pytest.mark.eval
def test_financials_recency(api_client: httpx.Client):
    """Fail if the newest financial record is older than 9 months from today."""
    max_age_days = 270  # ~9 months

    response = api_client.get("/v1/financials", params={
        "fields": "ticker,fiscal_year,fiscal_quarter,period_end_date",
        "limit": "100",
    })
    response.raise_for_status()
    data = response.json()

    records = data["data"]
    if not records:
        pytest.skip("No financials data available")

    newest_date = None
    for record in records:
        period_end = record.get("period_end_date")
        if period_end:
            try:
                d = datetime.strptime(period_end[:10], "%Y-%m-%d").date()
                if newest_date is None or d > newest_date:
                    newest_date = d
            except (ValueError, TypeError):
                continue

    if newest_date is None:
        pytest.skip("No records with period_end_date")

    today = date.today()
    age_days = (today - newest_date).days

    assert age_days <= max_age_days, (
        f"Newest financial record is {age_days} days old (period_end_date={newest_date}). "
        f"Threshold: {max_age_days} days (~9 months). "
        f"SEC filing lag may explain some staleness, but this exceeds tolerance."
    )


@pytest.mark.eval
def test_financials_coverage(api_client: httpx.Client):
    """Fail if >25% of sampled companies have zero financial quarters."""
    max_missing_pct = 0.25

    # Get a sample of companies
    companies_response = api_client.get("/v1/companies", params={
        "fields": "ticker",
        "limit": "50",
    })
    companies_response.raise_for_status()
    companies_data = companies_response.json()

    companies = companies_data["data"]
    if not companies:
        pytest.skip("No companies found")

    tickers = [c["ticker"] for c in companies if c.get("ticker")]
    if not tickers:
        pytest.skip("No tickers found in company data")

    # Check financials for each ticker (batch via comma-separated)
    # Process in chunks to stay within query limits
    missing_tickers = []
    checked_count = 0

    for ticker in tickers[:20]:  # Sample 20 to keep request count reasonable
        fin_response = api_client.get("/v1/financials", params={
            "ticker": ticker,
            "fields": "ticker,fiscal_year,fiscal_quarter",
            "limit": "1",
        })
        fin_response.raise_for_status()
        fin_data = fin_response.json()

        checked_count += 1
        if not fin_data["data"]:
            missing_tickers.append(ticker)

    if checked_count == 0:
        pytest.skip("Could not check any companies")

    missing_pct = len(missing_tickers) / checked_count

    assert missing_pct <= max_missing_pct, (
        f"{len(missing_tickers)}/{checked_count} ({missing_pct:.0%}) companies "
        f"have zero financial quarters (threshold: {max_missing_pct:.0%}). "
        f"Missing: {missing_tickers}"
    )


# =============================================================================
# CATEGORY 3: METRICS & COMPUTED DATA FRESHNESS
# =============================================================================

@pytest.mark.eval
def test_company_metrics_freshness(api_client: httpx.Client):
    """Fail if >30% of companies with metrics have stale computation dates (>90 days)."""
    max_stale_pct = 0.30
    max_age_days = 90

    response = api_client.get("/v1/companies", params={
        "include_metadata": "true",
        "fields": "ticker,net_leverage_ratio",
        "limit": "50",
    })
    response.raise_for_status()
    data = response.json()

    companies = data["data"]
    if not companies:
        pytest.skip("No companies found")

    today = date.today()
    companies_with_metrics = 0
    stale_companies = []

    for company in companies:
        metadata = company.get("_metadata", {})
        leverage_quality = metadata.get("leverage_data_quality", {})

        computed_at = leverage_quality.get("computed_at")
        if not computed_at:
            continue

        companies_with_metrics += 1

        try:
            # Handle ISO format timestamps
            computed_date = datetime.fromisoformat(
                computed_at.replace("Z", "+00:00")
            ).date()
            age_days = (today - computed_date).days
            if age_days > max_age_days:
                stale_companies.append({
                    "ticker": company.get("ticker"),
                    "computed_at": computed_at,
                    "age_days": age_days,
                })
        except (ValueError, TypeError):
            continue

    if companies_with_metrics == 0:
        pytest.skip("No companies with metrics computation timestamps")

    stale_pct = len(stale_companies) / companies_with_metrics

    assert stale_pct <= max_stale_pct, (
        f"{len(stale_companies)}/{companies_with_metrics} ({stale_pct:.0%}) companies "
        f"have metrics older than {max_age_days} days (threshold: {max_stale_pct:.0%}). "
        f"Examples: {stale_companies[:5]}"
    )


# =============================================================================
# CATEGORY 4: PRICING HISTORY CONTINUITY
# =============================================================================

@pytest.mark.eval
@pytest.mark.requires_pricing
def test_pricing_history_has_recent_snapshots(api_client: httpx.Client):
    """Verify daily snapshot job is running: a priced bond has snapshots within 7 days."""
    max_days_since_snapshot = 7

    # Find a bond with pricing to test against
    bonds_response = api_client.get("/v1/bonds", params={
        "has_pricing": "true",
        "has_cusip": "true",
        "fields": "name,cusip,pricing",
        "limit": "5",
    })
    bonds_response.raise_for_status()
    bonds_data = bonds_response.json()

    bonds = bonds_data["data"]
    if not bonds:
        pytest.skip("No bonds with pricing and CUSIP")

    today = date.today()
    from_date = (today - timedelta(days=30)).isoformat()
    to_date = today.isoformat()

    # Try each bond until we find one with history
    for bond in bonds:
        cusip = bond.get("cusip")
        if not cusip:
            continue

        history_response = api_client.get(
            f"/v1/bonds/{cusip}/pricing/history",
            params={"from": from_date, "to": to_date},
        )

        # Skip if endpoint returns 403 (Business tier only) or 404
        if history_response.status_code in (403, 404):
            continue

        if history_response.status_code != 200:
            continue

        history_data = history_response.json()
        snapshots = history_data.get("data", [])

        if not snapshots:
            continue

        # Find the most recent snapshot date
        most_recent = None
        for snap in snapshots:
            snap_date_str = snap.get("price_date") or snap.get("date")
            if snap_date_str:
                try:
                    snap_date = datetime.strptime(snap_date_str[:10], "%Y-%m-%d").date()
                    if most_recent is None or snap_date > most_recent:
                        most_recent = snap_date
                except (ValueError, TypeError):
                    continue

        if most_recent is None:
            continue

        days_since = (today - most_recent).days

        assert days_since <= max_days_since_snapshot, (
            f"Most recent pricing snapshot for {cusip} is {days_since} days old "
            f"({most_recent}). Daily snapshots should produce data within "
            f"{max_days_since_snapshot} days."
        )
        return  # Pass: found a bond with recent snapshot

    pytest.skip(
        "Could not find a bond with accessible pricing history "
        "(may require Business tier API key)"
    )


@pytest.mark.eval
@pytest.mark.requires_pricing
def test_pricing_history_no_large_gaps(api_client: httpx.Client):
    """Verify no gaps larger than 7 calendar days in last 30 days of pricing history."""
    max_gap_calendar_days = 7

    # Find a bond with pricing to test against
    bonds_response = api_client.get("/v1/bonds", params={
        "has_pricing": "true",
        "has_cusip": "true",
        "fields": "name,cusip,pricing",
        "limit": "5",
    })
    bonds_response.raise_for_status()
    bonds_data = bonds_response.json()

    bonds = bonds_data["data"]
    if not bonds:
        pytest.skip("No bonds with pricing and CUSIP")

    today = date.today()
    from_date = (today - timedelta(days=30)).isoformat()
    to_date = today.isoformat()

    for bond in bonds:
        cusip = bond.get("cusip")
        if not cusip:
            continue

        history_response = api_client.get(
            f"/v1/bonds/{cusip}/pricing/history",
            params={"from": from_date, "to": to_date},
        )

        # Skip if endpoint returns 403 (Business tier only) or 404
        if history_response.status_code in (403, 404):
            continue

        if history_response.status_code != 200:
            continue

        history_data = history_response.json()
        snapshots = history_data.get("data", [])

        if len(snapshots) < 2:
            continue

        # Parse and sort snapshot dates
        snap_dates = []
        for snap in snapshots:
            snap_date_str = snap.get("price_date") or snap.get("date")
            if snap_date_str:
                try:
                    snap_dates.append(
                        datetime.strptime(snap_date_str[:10], "%Y-%m-%d").date()
                    )
                except (ValueError, TypeError):
                    continue

        snap_dates.sort()

        if len(snap_dates) < 2:
            continue

        # Check for gaps
        max_gap = 0
        gap_start = None
        gap_end = None
        for i in range(1, len(snap_dates)):
            gap = (snap_dates[i] - snap_dates[i - 1]).days
            if gap > max_gap:
                max_gap = gap
                gap_start = snap_dates[i - 1]
                gap_end = snap_dates[i]

        assert max_gap <= max_gap_calendar_days, (
            f"Pricing history for {cusip} has a {max_gap}-day gap "
            f"({gap_start} to {gap_end}). "
            f"Max allowed gap: {max_gap_calendar_days} calendar days."
        )
        return  # Pass: found a bond with continuous history

    pytest.skip(
        "Could not find a bond with accessible pricing history "
        "(may require Business tier API key)"
    )


# =============================================================================
# AGGREGATE SCORING
# =============================================================================

def collect_staleness_score() -> PrimitiveScore:
    """Collect all test results into a PrimitiveScore."""
    return PrimitiveScore(primitive=PRIMITIVE)
