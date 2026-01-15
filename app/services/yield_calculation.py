"""
Bond Yield Calculation Service

Calculates yield-to-maturity (YTM) and spread to treasury for corporate bonds.
Uses Newton-Raphson method for YTM calculation.

Treasury yields are fetched from Treasury.gov or cached values.
"""

import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional, Tuple
import os

import httpx

# Treasury yield cache (benchmark -> (yield_pct, timestamp))
_treasury_cache: dict[str, Tuple[float, datetime]] = {}
CACHE_TTL_HOURS = 1  # Refresh treasury yields hourly


# Treasury benchmarks by years to maturity
BENCHMARKS = {
    "1M": 0.083,  # 1 month ≈ 0.083 years
    "3M": 0.25,
    "6M": 0.5,
    "1Y": 1,
    "2Y": 2,
    "3Y": 3,
    "5Y": 5,
    "7Y": 7,
    "10Y": 10,
    "20Y": 20,
    "30Y": 30,
}


def calculate_ytm(
    price: float,  # Clean price as % of par (e.g., 92.5)
    coupon_rate: float,  # Annual coupon as % (e.g., 5.5 for 5.5%)
    maturity_date: date,
    settlement_date: Optional[date] = None,
    face_value: float = 100.0,
    frequency: int = 2,  # Semi-annual coupon payments
    max_iterations: int = 100,
    tolerance: float = 0.0001,
) -> float:
    """
    Calculate yield to maturity using Newton-Raphson method.

    Args:
        price: Clean price as percentage of par (e.g., 92.5 means 92.5% of face value)
        coupon_rate: Annual coupon rate as percentage (e.g., 5.5 for 5.5%)
        maturity_date: Bond maturity date
        settlement_date: Settlement date (defaults to today)
        face_value: Face value (usually 100)
        frequency: Coupon frequency per year (2 = semi-annual)
        max_iterations: Maximum Newton-Raphson iterations
        tolerance: Convergence tolerance

    Returns:
        Yield to maturity as percentage (e.g., 6.82 for 6.82%)
    """
    if settlement_date is None:
        settlement_date = date.today()

    # Validate inputs
    if price <= 0:
        raise ValueError(f"Price must be positive: {price}")
    if maturity_date <= settlement_date:
        raise ValueError("Maturity date must be after settlement date")

    # Calculate time to maturity
    days_to_maturity = (maturity_date - settlement_date).days
    years_to_maturity = days_to_maturity / 365.25

    if years_to_maturity < 0.01:  # Less than ~4 days
        # For very short maturities, simple return
        return ((face_value - price) / price) * (365.25 / days_to_maturity) * 100

    # Number of remaining coupon periods
    n = int(years_to_maturity * frequency) + 1

    # Coupon payment per period
    coupon = (coupon_rate / 100) * face_value / frequency

    # Initial guess: coupon rate adjusted for premium/discount
    if price > face_value:
        # Premium bond: yield < coupon
        ytm = (coupon_rate / 100) * 0.8
    elif price < face_value:
        # Discount bond: yield > coupon
        discount_yield = (face_value - price) / price / years_to_maturity
        ytm = (coupon_rate / 100) + discount_yield
    else:
        ytm = coupon_rate / 100

    # Newton-Raphson iteration
    for _ in range(max_iterations):
        # Calculate present value of cash flows
        pv = 0.0
        dpv = 0.0  # Derivative of PV with respect to yield

        for i in range(1, n + 1):
            # Time to this payment in periods
            discount_factor = (1 + ytm / frequency) ** i

            # Present value of coupon
            pv += coupon / discount_factor
            dpv += -i * coupon / (frequency * (1 + ytm / frequency) ** (i + 1))

        # Present value of principal at maturity
        pv += face_value / ((1 + ytm / frequency) ** n)
        dpv += -n * face_value / (frequency * (1 + ytm / frequency) ** (n + 1))

        # Difference from target price
        diff = pv - price

        # Check convergence
        if abs(diff) < tolerance:
            break

        # Newton-Raphson update
        if abs(dpv) < 1e-10:  # Avoid division by very small numbers
            break

        ytm = ytm - diff / dpv

        # Keep yield in reasonable bounds
        ytm = max(-0.5, min(2.0, ytm))  # -50% to 200%

    return ytm * 100  # Return as percentage


def select_treasury_benchmark(years_to_maturity: float) -> str:
    """
    Select appropriate treasury benchmark based on years to maturity.

    Returns benchmark key (e.g., "5Y", "10Y").
    """
    if years_to_maturity <= 0.25:
        return "3M"
    elif years_to_maturity <= 0.75:
        return "6M"
    elif years_to_maturity <= 1.5:
        return "1Y"
    elif years_to_maturity <= 2.5:
        return "2Y"
    elif years_to_maturity <= 4:
        return "3Y"
    elif years_to_maturity <= 6:
        return "5Y"
    elif years_to_maturity <= 8.5:
        return "7Y"
    elif years_to_maturity <= 15:
        return "10Y"
    elif years_to_maturity <= 25:
        return "20Y"
    else:
        return "30Y"


async def fetch_treasury_yields() -> dict[str, float]:
    """
    Fetch current treasury yields from Treasury.gov.

    Returns dict of benchmark -> yield as percentage.
    """
    # Treasury.gov XML feed for daily treasury rates
    url = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/2025/all?type=daily_treasury_yield_curve"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()

            # Parse CSV - get most recent row
            lines = resp.text.strip().split("\n")
            if len(lines) < 2:
                return {}

            headers = lines[0].split(",")
            latest = lines[-1].split(",")

            yields = {}
            for i, header in enumerate(headers):
                header = header.strip().strip('"')
                if i < len(latest):
                    try:
                        value = float(latest[i].strip().strip('"'))
                        # Map header to our benchmark keys
                        if "1 Mo" in header:
                            yields["1M"] = value
                        elif "3 Mo" in header:
                            yields["3M"] = value
                        elif "6 Mo" in header:
                            yields["6M"] = value
                        elif "1 Yr" in header:
                            yields["1Y"] = value
                        elif "2 Yr" in header:
                            yields["2Y"] = value
                        elif "3 Yr" in header:
                            yields["3Y"] = value
                        elif "5 Yr" in header:
                            yields["5Y"] = value
                        elif "7 Yr" in header:
                            yields["7Y"] = value
                        elif "10 Yr" in header:
                            yields["10Y"] = value
                        elif "20 Yr" in header:
                            yields["20Y"] = value
                        elif "30 Yr" in header:
                            yields["30Y"] = value
                    except ValueError:
                        continue

            return yields

    except Exception:
        # Return fallback values if fetch fails
        # These are approximate historical averages
        return {
            "1M": 4.5,
            "3M": 4.5,
            "6M": 4.4,
            "1Y": 4.3,
            "2Y": 4.2,
            "3Y": 4.1,
            "5Y": 4.0,
            "7Y": 4.0,
            "10Y": 4.0,
            "20Y": 4.2,
            "30Y": 4.2,
        }


async def get_treasury_yield(benchmark: str) -> float:
    """
    Get treasury yield for a benchmark, using cache.

    Returns yield as percentage (e.g., 4.25 for 4.25%).
    """
    global _treasury_cache

    # Check cache
    if benchmark in _treasury_cache:
        yield_pct, timestamp = _treasury_cache[benchmark]
        age = datetime.now() - timestamp
        if age < timedelta(hours=CACHE_TTL_HOURS):
            return yield_pct

    # Fetch fresh yields
    yields = await fetch_treasury_yields()

    # Update cache
    now = datetime.now()
    for bm, yld in yields.items():
        _treasury_cache[bm] = (yld, now)

    return yields.get(benchmark, 4.0)  # Default to 4% if not found


async def calculate_spread_to_treasury(
    ytm: float,  # YTM as percentage (e.g., 6.82)
    maturity_date: date,
) -> Tuple[int, str]:
    """
    Calculate spread to nearest treasury benchmark.

    Args:
        ytm: Yield to maturity as percentage
        maturity_date: Bond maturity date

    Returns:
        Tuple of (spread_bps, benchmark_key)
    """
    # Calculate years to maturity
    years = (maturity_date - date.today()).days / 365.25

    # Select benchmark
    benchmark = select_treasury_benchmark(years)

    # Get treasury yield
    treasury_yield = await get_treasury_yield(benchmark)

    # Calculate spread in basis points
    spread_bps = int((ytm - treasury_yield) * 100)

    return spread_bps, benchmark


async def calculate_ytm_and_spread(
    price: float,
    coupon_rate: float,
    maturity_date: date,
    settlement_date: Optional[date] = None,
) -> Tuple[int, int, str]:
    """
    Calculate YTM and spread to treasury in one call.

    Args:
        price: Clean price as % of par
        coupon_rate: Annual coupon as % (e.g., 5.5)
        maturity_date: Bond maturity date
        settlement_date: Settlement date (defaults to today)

    Returns:
        Tuple of (ytm_bps, spread_bps, benchmark)
    """
    # Calculate YTM
    ytm_pct = calculate_ytm(
        price=price,
        coupon_rate=coupon_rate,
        maturity_date=maturity_date,
        settlement_date=settlement_date,
    )

    # Convert to basis points
    ytm_bps = int(ytm_pct * 100)

    # Calculate spread
    spread_bps, benchmark = await calculate_spread_to_treasury(ytm_pct, maturity_date)

    return ytm_bps, spread_bps, benchmark


def calculate_modified_duration(
    ytm: float,  # YTM as percentage
    coupon_rate: float,  # Coupon as percentage
    years_to_maturity: float,
    frequency: int = 2,
) -> float:
    """
    Calculate modified duration (price sensitivity to yield changes).

    Returns duration in years.
    """
    # Convert to decimals
    y = ytm / 100
    c = coupon_rate / 100

    # Macaulay duration approximation
    if abs(y) < 0.0001:  # Near-zero yield
        mac_duration = years_to_maturity
    else:
        n = years_to_maturity * frequency
        p = 1 / frequency

        # Weighted average time of cash flows
        numerator = (1 + y/frequency) / (y/frequency) - (1 + y/frequency + n * (c/frequency - y/frequency)) / (c/frequency * ((1 + y/frequency)**n - 1) + y/frequency)
        mac_duration = numerator / frequency

    # Modified duration
    mod_duration = mac_duration / (1 + y / frequency)

    return abs(mod_duration)


def calculate_dollar_duration(
    price: float,  # Clean price as % of par
    modified_duration: float,
) -> float:
    """
    Calculate dollar duration (DV01 per $100 face).

    Returns price change for 1bp yield change.
    """
    # DV01 = Modified Duration * Price * 0.0001
    return modified_duration * price * 0.0001


# Utility functions for display

def format_yield(bps: Optional[int]) -> str:
    """Format yield in basis points as percentage string."""
    if bps is None:
        return "N/A"
    return f"{bps / 100:.2f}%"


def format_spread(bps: Optional[int], benchmark: Optional[str] = None) -> str:
    """Format spread in basis points with optional benchmark."""
    if bps is None:
        return "N/A"

    sign = "+" if bps >= 0 else ""
    spread_str = f"{sign}{bps}bps"

    if benchmark:
        spread_str += f" over {benchmark}"

    return spread_str


def get_staleness_indicator(days: Optional[int]) -> str:
    """Get staleness indicator for display."""
    if days is None:
        return "unknown"
    elif days <= 1:
        return "fresh"
    elif days <= 7:
        return "recent"
    elif days <= 30:
        return "stale"
    else:
        return "very_stale"
