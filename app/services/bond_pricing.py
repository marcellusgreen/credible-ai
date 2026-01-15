"""
Bond Pricing Service

Fetches bond pricing data using a tiered approach:
1. Finnhub API (TRACE data via ISIN) - requires premium subscription
2. Estimated pricing based on treasury yields + credit spreads (fallback)

Finnhub API Documentation: https://finnhub.io/docs/api/bond-price
"""

import asyncio
import os
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

import httpx
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DebtInstrument, BondPricing, Company


# Finnhub API configuration
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
FINNHUB_BASE_URL = "https://finnhub.io/api/v1"

# Rate limiting
REQUEST_DELAY = 0.5  # Seconds between requests


class BondPrice(BaseModel):
    """Bond pricing data."""

    cusip: Optional[str] = None
    isin: Optional[str] = None

    # Pricing
    last_price: Optional[Decimal] = None  # Clean price as % of par
    last_trade_date: Optional[datetime] = None
    high_price: Optional[Decimal] = None
    low_price: Optional[Decimal] = None
    volume: Optional[int] = None

    # Yield (if available)
    yield_pct: Optional[Decimal] = None

    # Metadata
    source: str = "unknown"  # "finnhub", "estimated", "manual"
    is_estimated: bool = False
    error: Optional[str] = None


async def fetch_finnhub_price(isin: str) -> BondPrice:
    """
    Fetch bond price from Finnhub API using ISIN.

    Finnhub's bond/price endpoint requires ISIN and returns candlestick data.
    """
    if not FINNHUB_API_KEY:
        return BondPrice(
            isin=isin,
            source="finnhub",
            error="FINNHUB_API_KEY not configured",
        )

    # Get price data for last 30 days
    to_date = datetime.now()
    from_date = to_date - timedelta(days=30)

    url = f"{FINNHUB_BASE_URL}/bond/price"
    params = {
        "isin": isin,
        "from": int(from_date.timestamp()),
        "to": int(to_date.timestamp()),
        "token": FINNHUB_API_KEY,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, params=params)

            if resp.status_code == 401:
                return BondPrice(
                    isin=isin,
                    source="finnhub",
                    error="Finnhub API key invalid or expired",
                )

            if resp.status_code == 403:
                return BondPrice(
                    isin=isin,
                    source="finnhub",
                    error="Finnhub premium subscription required for bond data",
                )

            if resp.status_code == 429:
                return BondPrice(
                    isin=isin,
                    source="finnhub",
                    error="Finnhub rate limit exceeded",
                )

            if resp.status_code != 200:
                return BondPrice(
                    isin=isin,
                    source="finnhub",
                    error=f"Finnhub API error: HTTP {resp.status_code}",
                )

            data = resp.json()

            # Finnhub returns candle data: {c: [closes], t: [timestamps], ...}
            if not data or "c" not in data or not data["c"]:
                return BondPrice(
                    isin=isin,
                    source="finnhub",
                    error="No price data available",
                )

            closes = data.get("c", [])
            timestamps = data.get("t", [])
            highs = data.get("h", [])
            lows = data.get("l", [])
            volumes = data.get("v", [])

            # Get most recent price
            last_price = Decimal(str(closes[-1])) if closes else None
            last_timestamp = datetime.fromtimestamp(timestamps[-1]) if timestamps else None

            # Get high/low from recent data
            high_price = Decimal(str(max(highs))) if highs else None
            low_price = Decimal(str(min(lows))) if lows else None

            # Sum volume
            total_volume = sum(volumes) if volumes else None

            return BondPrice(
                isin=isin,
                last_price=last_price,
                last_trade_date=last_timestamp,
                high_price=high_price,
                low_price=low_price,
                volume=total_volume,
                source="finnhub",
                is_estimated=False,
            )

        except httpx.TimeoutException:
            return BondPrice(isin=isin, source="finnhub", error="Request timeout")
        except Exception as e:
            return BondPrice(isin=isin, source="finnhub", error=f"API error: {str(e)}")


async def fetch_finnhub_tick_data(isin: str, trade_date: date = None) -> BondPrice:
    """
    Fetch bond tick data from Finnhub TRACE endpoint.

    This provides individual trade-level data.
    """
    if not FINNHUB_API_KEY:
        return BondPrice(
            isin=isin,
            source="finnhub",
            error="FINNHUB_API_KEY not configured",
        )

    if trade_date is None:
        trade_date = date.today() - timedelta(days=1)  # Yesterday

    url = f"{FINNHUB_BASE_URL}/bond/tick"
    params = {
        "isin": isin,
        "date": trade_date.strftime("%Y-%m-%d"),
        "limit": 100,
        "skip": 0,
        "exchange": "trace",
        "token": FINNHUB_API_KEY,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, params=params)

            if resp.status_code != 200:
                return BondPrice(
                    isin=isin,
                    source="finnhub",
                    error=f"Finnhub tick API error: HTTP {resp.status_code}",
                )

            data = resp.json()

            if not data or "data" not in data or not data["data"]:
                return BondPrice(
                    isin=isin,
                    source="finnhub",
                    error="No tick data available",
                )

            ticks = data["data"]

            # Get most recent tick
            latest = ticks[-1]
            prices = [Decimal(str(t.get("p", 0))) for t in ticks if t.get("p")]

            return BondPrice(
                isin=isin,
                last_price=Decimal(str(latest.get("p"))) if latest.get("p") else None,
                last_trade_date=datetime.fromtimestamp(latest.get("t", 0) / 1000) if latest.get("t") else None,
                high_price=max(prices) if prices else None,
                low_price=min(prices) if prices else None,
                volume=sum(t.get("v", 0) for t in ticks),
                source="finnhub",
                is_estimated=False,
            )

        except Exception as e:
            return BondPrice(isin=isin, source="finnhub", error=f"Tick API error: {str(e)}")


async def get_bond_price(
    cusip: Optional[str] = None,
    isin: Optional[str] = None,
    coupon_rate_pct: Optional[float] = None,
    maturity_date: Optional[date] = None,
    credit_rating: Optional[str] = None,
    use_estimated_fallback: bool = True,
) -> BondPrice:
    """
    Get bond price using tiered approach.

    1. Try Finnhub API if ISIN available
    2. Fall back to estimated pricing if enabled

    Args:
        cusip: CUSIP identifier
        isin: ISIN identifier (preferred for Finnhub)
        coupon_rate_pct: Coupon rate for estimated pricing
        maturity_date: Maturity date for estimated pricing
        credit_rating: Credit rating for estimated pricing
        use_estimated_fallback: Whether to use estimated pricing as fallback

    Returns:
        BondPrice with real or estimated data
    """
    # Try Finnhub first if we have ISIN
    if isin and FINNHUB_API_KEY:
        result = await fetch_finnhub_price(isin)
        if result.last_price is not None:
            result.cusip = cusip
            return result

    # Fall back to estimated pricing
    if use_estimated_fallback and coupon_rate_pct and maturity_date:
        from app.services.estimated_pricing import estimate_bond_price

        estimated = await estimate_bond_price(
            coupon_rate_pct=coupon_rate_pct,
            maturity_date=maturity_date,
            credit_rating=credit_rating,
            cusip=cusip,
        )

        return BondPrice(
            cusip=cusip,
            isin=isin,
            last_price=estimated.estimated_price,
            yield_pct=Decimal(str(estimated.estimated_ytm_bps / 100)),
            source="estimated",
            is_estimated=True,
        )

    # No data available
    return BondPrice(
        cusip=cusip,
        isin=isin,
        source="none",
        error="No pricing data available and insufficient data for estimation",
    )


def calculate_staleness(last_trade_date: Optional[datetime]) -> int:
    """Calculate days since last trade."""
    if not last_trade_date:
        return 999  # Unknown staleness

    delta = datetime.now() - last_trade_date
    return max(0, delta.days)


async def save_bond_pricing(
    session: AsyncSession,
    debt_instrument_id: UUID,
    cusip: Optional[str],
    price: BondPrice,
    ytm_bps: Optional[int] = None,
    spread_bps: Optional[int] = None,
    treasury_benchmark: Optional[str] = None,
) -> Optional[BondPricing]:
    """
    Save or update bond pricing record.
    """
    # Check for existing record
    result = await session.execute(
        select(BondPricing).where(BondPricing.debt_instrument_id == debt_instrument_id)
    )
    existing = result.scalar_one_or_none()

    staleness = calculate_staleness(price.last_trade_date)
    source = "estimated" if price.is_estimated else "TRACE"

    if existing:
        # Update existing record
        existing.last_price = price.last_price
        existing.last_trade_date = price.last_trade_date
        existing.last_trade_volume = price.volume
        existing.staleness_days = staleness
        existing.price_source = source
        existing.fetched_at = datetime.now()
        if ytm_bps is not None:
            existing.ytm_bps = ytm_bps
            existing.calculated_at = datetime.now()
        if spread_bps is not None:
            existing.spread_to_treasury_bps = spread_bps
            existing.treasury_benchmark = treasury_benchmark
        record = existing
    else:
        # Create new record
        record = BondPricing(
            debt_instrument_id=debt_instrument_id,
            cusip=cusip,
            last_price=price.last_price,
            last_trade_date=price.last_trade_date,
            last_trade_volume=price.volume,
            staleness_days=staleness,
            ytm_bps=ytm_bps,
            spread_to_treasury_bps=spread_bps,
            treasury_benchmark=treasury_benchmark,
            price_source=source,
            calculated_at=datetime.now() if ytm_bps is not None else None,
        )
        session.add(record)

    await session.commit()
    await session.refresh(record)
    return record


async def get_bonds_needing_pricing(
    session: AsyncSession,
    ticker: Optional[str] = None,
    stale_only: bool = False,
    stale_days: int = 1,
    limit: int = 100,
) -> list[DebtInstrument]:
    """
    Get bonds that need pricing updates.

    Returns bonds that:
    - Have maturity dates in the future
    - Have coupon rates (for estimation)
    - Optionally filtered by company ticker
    - Optionally filtered to only stale prices
    """
    # Tradeable instrument types
    tradeable_types = [
        "senior_notes", "notes", "bonds", "debentures",
        "convertible_notes", "senior_secured_notes", "subordinated_notes"
    ]

    query = (
        select(DebtInstrument)
        .where(DebtInstrument.instrument_type.in_(tradeable_types))
        .where(DebtInstrument.is_active == True)
        .where(DebtInstrument.maturity_date > date.today())
        .where(DebtInstrument.interest_rate.isnot(None))
        .limit(limit)
    )

    if ticker:
        query = query.join(Company).where(Company.ticker == ticker.upper())

    result = await session.execute(query)
    bonds = list(result.scalars().all())

    if stale_only:
        # Filter to only stale bonds
        filtered = []
        for bond in bonds:
            # Check if has recent pricing
            pricing_result = await session.execute(
                select(BondPricing)
                .where(BondPricing.debt_instrument_id == bond.id)
            )
            pricing = pricing_result.scalar_one_or_none()

            if not pricing:
                # No pricing at all
                filtered.append(bond)
            elif pricing.staleness_days is None or pricing.staleness_days >= stale_days:
                # Stale pricing
                filtered.append(bond)
            elif pricing.fetched_at:
                # Check if data is old
                age = datetime.now() - pricing.fetched_at.replace(tzinfo=None)
                if age.days >= stale_days:
                    filtered.append(bond)

        bonds = filtered

    return bonds


async def update_company_pricing(
    session: AsyncSession,
    ticker: str,
) -> dict:
    """
    Update pricing for all bonds of a company.

    Returns summary dict with counts.
    """
    from app.services.yield_calculation import calculate_ytm_and_spread

    # Get company
    result = await session.execute(
        select(Company).where(Company.ticker == ticker.upper())
    )
    company = result.scalar_one_or_none()

    if not company:
        raise ValueError(f"Company not found: {ticker}")

    # Get bonds needing pricing
    bonds = await get_bonds_needing_pricing(session, ticker=ticker)

    results = {
        "ticker": ticker,
        "bonds_checked": len(bonds),
        "prices_found": 0,
        "prices_estimated": 0,
        "prices_failed": 0,
        "yields_calculated": 0,
    }

    for bond in bonds:
        # Get price (Finnhub or estimated)
        price = await get_bond_price(
            cusip=bond.cusip,
            isin=bond.isin,
            coupon_rate_pct=bond.interest_rate / 100 if bond.interest_rate else None,
            maturity_date=bond.maturity_date,
            credit_rating=None,  # TODO: Add rating to bond model
        )

        if price.last_price:
            if price.is_estimated:
                results["prices_estimated"] += 1
            else:
                results["prices_found"] += 1

            # Calculate yield
            ytm_bps = None
            spread_bps = None
            benchmark = None

            if bond.interest_rate and bond.maturity_date:
                try:
                    ytm_bps, spread_bps, benchmark = await calculate_ytm_and_spread(
                        price=float(price.last_price),
                        coupon_rate=bond.interest_rate / 100,  # bps to %
                        maturity_date=bond.maturity_date,
                    )
                    results["yields_calculated"] += 1
                except Exception:
                    pass

            # Save pricing
            await save_bond_pricing(
                session=session,
                debt_instrument_id=bond.id,
                cusip=bond.cusip,
                price=price,
                ytm_bps=ytm_bps,
                spread_bps=spread_bps,
                treasury_benchmark=benchmark,
            )
        else:
            results["prices_failed"] += 1

        # Rate limit
        await asyncio.sleep(REQUEST_DELAY)

    return results


# Test function
async def test_pricing():
    """Test the pricing service."""
    print("Testing Bond Pricing Service")
    print("=" * 60)

    # Test Finnhub (will likely fail without premium)
    print("\n1. Testing Finnhub API...")
    test_isin = "US037833EP27"  # Apple bond
    result = await fetch_finnhub_price(test_isin)
    print(f"   ISIN: {test_isin}")
    print(f"   Price: {result.last_price}")
    print(f"   Source: {result.source}")
    print(f"   Error: {result.error}")

    # Test estimated pricing
    print("\n2. Testing Estimated Pricing...")
    result = await get_bond_price(
        cusip="037833EP2",
        isin=test_isin,
        coupon_rate_pct=5.5,
        maturity_date=date(2030, 6, 15),
        credit_rating="AA+",
    )
    print(f"   Price: {result.last_price}")
    print(f"   Yield: {result.yield_pct}%")
    print(f"   Source: {result.source}")
    print(f"   Estimated: {result.is_estimated}")

    print("\n" + "=" * 60)
    print("Test complete")


if __name__ == "__main__":
    asyncio.run(test_pricing())
