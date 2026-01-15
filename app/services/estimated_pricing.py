"""
Estimated Bond Pricing Service

Calculates estimated bond prices based on:
1. Current treasury yields (fetched from Treasury.gov)
2. Credit spreads by rating and maturity
3. Coupon rate and time to maturity

This provides a reasonable estimate when real-time TRACE data is unavailable.
Actual market prices may differ due to liquidity, supply/demand, and other factors.

Future: Will be replaced/supplemented by Finnhub API for real TRACE data.
"""

from datetime import date
from decimal import Decimal
from typing import Optional, Tuple
from uuid import UUID

from pydantic import BaseModel

from app.services.yield_calculation import (
    get_treasury_yield,
    select_treasury_benchmark,
    calculate_ytm,
)


# Credit spread curves by rating (in basis points)
# Based on historical corporate bond spreads
# Format: {rating: {years_bucket: spread_bps}}
CREDIT_SPREADS = {
    # Investment Grade
    "AAA": {1: 20, 3: 30, 5: 40, 7: 50, 10: 60, 30: 80},
    "AA+": {1: 30, 3: 40, 5: 55, 7: 65, 10: 75, 30: 95},
    "AA": {1: 35, 3: 50, 5: 65, 7: 75, 10: 85, 30: 105},
    "AA-": {1: 40, 3: 55, 5: 70, 7: 85, 10: 95, 30: 115},
    "A+": {1: 50, 3: 65, 5: 85, 7: 100, 10: 115, 30: 140},
    "A": {1: 60, 3: 80, 5: 100, 7: 120, 10: 135, 30: 165},
    "A-": {1: 75, 3: 95, 5: 120, 7: 140, 10: 160, 30: 195},
    "BBB+": {1: 90, 3: 115, 5: 145, 7: 170, 10: 195, 30: 240},
    "BBB": {1: 110, 3: 140, 5: 175, 7: 205, 10: 235, 30: 290},
    "BBB-": {1: 140, 3: 175, 5: 215, 7: 255, 10: 295, 30: 365},

    # High Yield
    "BB+": {1: 200, 3: 250, 5: 300, 7: 350, 10: 400, 30: 500},
    "BB": {1: 250, 3: 310, 5: 370, 7: 430, 10: 490, 30: 610},
    "BB-": {1: 320, 3: 390, 5: 460, 7: 530, 10: 600, 30: 750},
    "B+": {1: 400, 3: 480, 5: 560, 7: 640, 10: 720, 30: 900},
    "B": {1: 500, 3: 590, 5: 680, 7: 770, 10: 860, 30: 1080},
    "B-": {1: 620, 3: 720, 5: 820, 7: 920, 10: 1020, 30: 1280},
    "CCC+": {1: 800, 3: 900, 5: 1000, 7: 1100, 10: 1200, 30: 1500},
    "CCC": {1: 1000, 3: 1100, 5: 1200, 7: 1300, 10: 1400, 30: 1750},
    "CCC-": {1: 1250, 3: 1350, 5: 1450, 7: 1550, 10: 1650, 30: 2000},
    "CC": {1: 1500, 3: 1600, 5: 1700, 7: 1800, 10: 1900, 30: 2300},
    "C": {1: 2000, 3: 2100, 5: 2200, 7: 2300, 10: 2400, 30: 2800},
    "D": {1: 3000, 3: 3000, 5: 3000, 7: 3000, 10: 3000, 30: 3000},

    # Default for unrated - assume BB level
    "NR": {1: 250, 3: 310, 5: 370, 7: 430, 10: 490, 30: 610},
}

# S&P to Moody's rating mapping
RATING_MAP = {
    # S&P -> normalized
    "AAA": "AAA", "Aaa": "AAA",
    "AA+": "AA+", "Aa1": "AA+",
    "AA": "AA", "Aa2": "AA",
    "AA-": "AA-", "Aa3": "AA-",
    "A+": "A+", "A1": "A+",
    "A": "A", "A2": "A",
    "A-": "A-", "A3": "A-",
    "BBB+": "BBB+", "Baa1": "BBB+",
    "BBB": "BBB", "Baa2": "BBB",
    "BBB-": "BBB-", "Baa3": "BBB-",
    "BB+": "BB+", "Ba1": "BB+",
    "BB": "BB", "Ba2": "BB",
    "BB-": "BB-", "Ba3": "BB-",
    "B+": "B+", "B1": "B+",
    "B": "B", "B2": "B",
    "B-": "B-", "B3": "B-",
    "CCC+": "CCC+", "Caa1": "CCC+",
    "CCC": "CCC", "Caa2": "CCC",
    "CCC-": "CCC-", "Caa3": "CCC-",
    "CC": "CC", "Ca": "CC",
    "C": "C",
    "D": "D",
}


class EstimatedPrice(BaseModel):
    """Estimated bond price and yield data."""

    cusip: Optional[str] = None
    debt_instrument_id: Optional[UUID] = None

    # Estimated values
    estimated_price: Decimal  # Clean price as % of par
    estimated_ytm_bps: int  # Yield to maturity in basis points
    estimated_spread_bps: int  # Spread to treasury in basis points
    treasury_benchmark: str  # e.g., "5Y", "10Y"
    treasury_yield_pct: float  # Benchmark treasury yield

    # Inputs used
    coupon_rate_pct: float
    years_to_maturity: float
    credit_rating: str
    assumed_spread_bps: int  # Spread assumed based on rating

    # Metadata
    is_estimated: bool = True
    estimation_method: str = "credit_spread_model"
    confidence: str = "low"  # low, medium, high


def normalize_rating(rating: Optional[str]) -> str:
    """Normalize credit rating to S&P format."""
    if not rating:
        return "NR"

    rating = rating.strip().upper()

    # Try direct lookup
    if rating in CREDIT_SPREADS:
        return rating

    # Try mapping
    for key, normalized in RATING_MAP.items():
        if rating.upper() == key.upper():
            return normalized

    # Default to NR (not rated)
    return "NR"


def get_credit_spread(rating: str, years_to_maturity: float) -> int:
    """
    Get credit spread in basis points for a rating and maturity.

    Interpolates between maturity buckets.
    """
    rating = normalize_rating(rating)
    spreads = CREDIT_SPREADS.get(rating, CREDIT_SPREADS["NR"])

    # Find surrounding buckets
    buckets = sorted(spreads.keys())

    if years_to_maturity <= buckets[0]:
        return spreads[buckets[0]]

    if years_to_maturity >= buckets[-1]:
        return spreads[buckets[-1]]

    # Interpolate
    for i in range(len(buckets) - 1):
        if buckets[i] <= years_to_maturity <= buckets[i + 1]:
            lower_years = buckets[i]
            upper_years = buckets[i + 1]
            lower_spread = spreads[lower_years]
            upper_spread = spreads[upper_years]

            # Linear interpolation
            ratio = (years_to_maturity - lower_years) / (upper_years - lower_years)
            spread = lower_spread + ratio * (upper_spread - lower_spread)
            return int(spread)

    return spreads[buckets[-1]]


def calculate_price_from_yield(
    ytm_pct: float,
    coupon_rate_pct: float,
    years_to_maturity: float,
    face_value: float = 100.0,
    frequency: int = 2,
) -> float:
    """
    Calculate clean price given a yield to maturity.

    This is the inverse of the YTM calculation.
    """
    if years_to_maturity <= 0:
        return face_value

    # Number of remaining coupon periods
    n = int(years_to_maturity * frequency) + 1

    # Coupon payment per period
    coupon = (coupon_rate_pct / 100) * face_value / frequency

    # Yield per period
    y = ytm_pct / 100 / frequency

    if abs(y) < 0.0001:
        # Near-zero yield - simple sum
        price = coupon * n + face_value
    else:
        # Present value of coupons (annuity formula)
        pv_coupons = coupon * (1 - (1 + y) ** (-n)) / y

        # Present value of principal
        pv_principal = face_value / ((1 + y) ** n)

        price = pv_coupons + pv_principal

    return price


async def estimate_bond_price(
    coupon_rate_pct: float,
    maturity_date: date,
    credit_rating: Optional[str] = None,
    cusip: Optional[str] = None,
    debt_instrument_id: Optional[UUID] = None,
) -> EstimatedPrice:
    """
    Estimate bond price based on treasury yields and credit spreads.

    Args:
        coupon_rate_pct: Annual coupon rate as percentage (e.g., 5.5 for 5.5%)
        maturity_date: Bond maturity date
        credit_rating: S&P or Moody's rating (optional, defaults to BB)
        cusip: Optional CUSIP for reference
        debt_instrument_id: Optional debt instrument ID

    Returns:
        EstimatedPrice with estimated price and yield data
    """
    # Calculate years to maturity
    today = date.today()
    if maturity_date <= today:
        # Matured bond
        return EstimatedPrice(
            cusip=cusip,
            debt_instrument_id=debt_instrument_id,
            estimated_price=Decimal("100.00"),
            estimated_ytm_bps=0,
            estimated_spread_bps=0,
            treasury_benchmark="N/A",
            treasury_yield_pct=0,
            coupon_rate_pct=coupon_rate_pct,
            years_to_maturity=0,
            credit_rating=normalize_rating(credit_rating),
            assumed_spread_bps=0,
            confidence="high",
        )

    years_to_maturity = (maturity_date - today).days / 365.25

    # Get treasury benchmark and yield
    benchmark = select_treasury_benchmark(years_to_maturity)
    treasury_yield = await get_treasury_yield(benchmark)

    # Get credit spread based on rating
    normalized_rating = normalize_rating(credit_rating)
    credit_spread_bps = get_credit_spread(normalized_rating, years_to_maturity)

    # Calculate estimated yield
    estimated_ytm_pct = treasury_yield + (credit_spread_bps / 100)

    # Calculate estimated price from yield
    estimated_price = calculate_price_from_yield(
        ytm_pct=estimated_ytm_pct,
        coupon_rate_pct=coupon_rate_pct,
        years_to_maturity=years_to_maturity,
    )

    # Determine confidence level
    if normalized_rating == "NR":
        confidence = "low"
    elif normalized_rating.startswith(("AAA", "AA", "A")):
        confidence = "medium"  # IG spreads are more predictable
    else:
        confidence = "low"  # HY spreads are more volatile

    return EstimatedPrice(
        cusip=cusip,
        debt_instrument_id=debt_instrument_id,
        estimated_price=Decimal(str(round(estimated_price, 3))),
        estimated_ytm_bps=int(estimated_ytm_pct * 100),
        estimated_spread_bps=credit_spread_bps,
        treasury_benchmark=benchmark,
        treasury_yield_pct=treasury_yield,
        coupon_rate_pct=coupon_rate_pct,
        years_to_maturity=round(years_to_maturity, 2),
        credit_rating=normalized_rating,
        assumed_spread_bps=credit_spread_bps,
        confidence=confidence,
    )


async def estimate_prices_batch(
    bonds: list[dict],
) -> list[EstimatedPrice]:
    """
    Estimate prices for multiple bonds.

    Args:
        bonds: List of dicts with keys:
            - coupon_rate_pct: float
            - maturity_date: date
            - credit_rating: str (optional)
            - cusip: str (optional)
            - debt_instrument_id: UUID (optional)

    Returns:
        List of EstimatedPrice objects
    """
    results = []
    for bond in bonds:
        result = await estimate_bond_price(
            coupon_rate_pct=bond["coupon_rate_pct"],
            maturity_date=bond["maturity_date"],
            credit_rating=bond.get("credit_rating"),
            cusip=bond.get("cusip"),
            debt_instrument_id=bond.get("debt_instrument_id"),
        )
        results.append(result)
    return results


# CLI test
if __name__ == "__main__":
    import asyncio

    async def test():
        print("Testing Estimated Bond Pricing")
        print("=" * 60)

        # Test cases
        test_bonds = [
            {"coupon": 5.5, "maturity": date(2030, 6, 15), "rating": "BBB"},
            {"coupon": 4.0, "maturity": date(2028, 3, 1), "rating": "A"},
            {"coupon": 7.5, "maturity": date(2029, 9, 15), "rating": "BB"},
            {"coupon": 3.25, "maturity": date(2027, 12, 1), "rating": "AA"},
            {"coupon": 8.75, "maturity": date(2031, 5, 1), "rating": "B"},
        ]

        for bond in test_bonds:
            result = await estimate_bond_price(
                coupon_rate_pct=bond["coupon"],
                maturity_date=bond["maturity"],
                credit_rating=bond["rating"],
            )

            print(f"\n{bond['coupon']}% due {bond['maturity']} ({bond['rating']})")
            print(f"  Estimated Price: {result.estimated_price}")
            print(f"  Estimated YTM:   {result.estimated_ytm_bps/100:.2f}%")
            print(f"  Spread:          +{result.estimated_spread_bps}bps over {result.treasury_benchmark}")
            print(f"  Treasury Yield:  {result.treasury_yield_pct:.2f}%")
            print(f"  Confidence:      {result.confidence}")

    asyncio.run(test())
