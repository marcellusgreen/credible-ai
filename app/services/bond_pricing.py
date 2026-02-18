"""
Bond Pricing Service

Fetches bond pricing data using a tiered approach:
1. Finnhub API (TRACE data via ISIN) - requires premium subscription
2. Historical TRACE pricing from bond_pricing_history (fallback)
3. Estimated pricing based on treasury yields + credit spreads (fallback)

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

from app.models import DebtInstrument, BondPricing, BondPricingHistory, Company


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


class BondProfile(BaseModel):
    """Bond profile data from Finnhub."""

    isin: Optional[str] = None
    cusip: Optional[str] = None
    figi: Optional[str] = None

    # Amounts (in dollars)
    amount_outstanding: Optional[int] = None  # Current outstanding
    original_offering: Optional[int] = None  # Original issuance amount

    # Bond details
    coupon: Optional[float] = None
    coupon_type: Optional[str] = None
    maturity_date: Optional[date] = None
    issue_date: Optional[date] = None
    dated_date: Optional[date] = None

    # Classification
    bond_type: Optional[str] = None
    debt_type: Optional[str] = None
    security_level: Optional[str] = None
    asset_type: Optional[str] = None

    # Other
    callable: Optional[bool] = None
    payment_frequency: Optional[str] = None
    offering_price: Optional[float] = None

    # Metadata
    source: str = "finnhub"
    error: Optional[str] = None


async def fetch_finnhub_bond_profile(isin: str) -> BondProfile:
    """
    Fetch bond profile data from Finnhub API using ISIN.

    Returns bond metadata including amount outstanding and original offering.
    Finnhub API: https://finnhub.io/docs/api/bond-profile
    """
    if not FINNHUB_API_KEY:
        return BondProfile(
            isin=isin,
            error="FINNHUB_API_KEY not configured",
        )

    url = f"{FINNHUB_BASE_URL}/bond/profile"
    params = {
        "isin": isin,
        "token": FINNHUB_API_KEY,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, params=params)

            if resp.status_code == 401:
                return BondProfile(
                    isin=isin,
                    error="Finnhub API key invalid or expired",
                )

            if resp.status_code == 403:
                return BondProfile(
                    isin=isin,
                    error="Finnhub premium subscription required for bond data",
                )

            if resp.status_code == 429:
                return BondProfile(
                    isin=isin,
                    error="Finnhub rate limit exceeded",
                )

            if resp.status_code != 200:
                return BondProfile(
                    isin=isin,
                    error=f"Finnhub API error: HTTP {resp.status_code}",
                )

            data = resp.json()

            if not data:
                return BondProfile(
                    isin=isin,
                    error="No profile data available",
                )

            # Parse dates
            maturity_date = None
            if data.get("maturityDate"):
                try:
                    maturity_date = datetime.strptime(data["maturityDate"], "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    pass

            issue_date = None
            if data.get("issueDate"):
                try:
                    issue_date = datetime.strptime(data["issueDate"], "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    pass

            dated_date = None
            if data.get("datedDate"):
                try:
                    dated_date = datetime.strptime(data["datedDate"], "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    pass

            # Parse amounts (Finnhub returns as numbers)
            amount_outstanding = None
            if data.get("amountOutstanding"):
                try:
                    amount_outstanding = int(float(data["amountOutstanding"]))
                except (ValueError, TypeError):
                    pass

            original_offering = None
            if data.get("originalOffering"):
                try:
                    original_offering = int(float(data["originalOffering"]))
                except (ValueError, TypeError):
                    pass

            return BondProfile(
                isin=isin,
                cusip=data.get("cusip"),
                figi=data.get("figi"),
                amount_outstanding=amount_outstanding,
                original_offering=original_offering,
                coupon=data.get("coupon"),
                coupon_type=data.get("couponType"),
                maturity_date=maturity_date,
                issue_date=issue_date,
                dated_date=dated_date,
                bond_type=data.get("bondType"),
                debt_type=data.get("debtType"),
                security_level=data.get("securityLevel"),
                asset_type=data.get("assetType"),
                callable=data.get("callable"),
                payment_frequency=data.get("paymentFrequency"),
                offering_price=data.get("offeringPrice"),
                source="finnhub",
            )

        except httpx.TimeoutException:
            return BondProfile(isin=isin, error="Request timeout")
        except Exception as e:
            return BondProfile(isin=isin, error=f"API error: {str(e)}")


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
            max_retries = 3
            for attempt in range(max_retries):
                resp = await client.get(url, params=params)

                if resp.status_code == 429:
                    if attempt < max_retries - 1:
                        wait = int(resp.headers.get("retry-after", 2 ** (attempt + 1)))
                        await asyncio.sleep(wait)
                        continue
                    return BondPrice(
                        isin=isin,
                        source="finnhub",
                        error="Finnhub rate limit exceeded after retries",
                    )

                break  # Got a non-429 response

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


async def get_latest_historical_trace_price(
    session: AsyncSession,
    debt_instrument_id: UUID,
) -> Optional[BondPrice]:
    """
    Get the most recent historical TRACE price for a bond.

    Used as fallback when Finnhub returns no current data but we have
    historical pricing from previous trades.
    """
    result = await session.execute(
        select(BondPricingHistory)
        .where(BondPricingHistory.debt_instrument_id == debt_instrument_id)
        .where(BondPricingHistory.price.isnot(None))
        .where(BondPricingHistory.price_source == "TRACE")
        .order_by(BondPricingHistory.price_date.desc())
        .limit(1)
    )
    hist = result.scalar_one_or_none()
    if not hist:
        return None

    return BondPrice(
        cusip=hist.cusip,
        last_price=hist.price,
        last_trade_date=datetime.combine(hist.price_date, datetime.min.time()),
        volume=hist.volume,
        yield_pct=Decimal(str(hist.ytm_bps / 100)) if hist.ytm_bps else None,
        source="finnhub",
        is_estimated=False,
    )


async def get_bond_price(
    cusip: Optional[str] = None,
    isin: Optional[str] = None,
    coupon_rate_pct: Optional[float] = None,
    maturity_date: Optional[date] = None,
    credit_rating: Optional[str] = None,
    use_estimated_fallback: bool = True,
    session: Optional[AsyncSession] = None,
    debt_instrument_id: Optional[UUID] = None,
) -> BondPrice:
    """
    Get bond price using tiered approach.

    1. Try Finnhub API if ISIN available
    2. Fall back to most recent historical TRACE price if available
    3. Fall back to estimated pricing if enabled

    Args:
        cusip: CUSIP identifier
        isin: ISIN identifier (preferred for Finnhub)
        coupon_rate_pct: Coupon rate for estimated pricing
        maturity_date: Maturity date for estimated pricing
        credit_rating: Credit rating for estimated pricing
        use_estimated_fallback: Whether to use estimated pricing as fallback
        session: Database session (needed for historical fallback)
        debt_instrument_id: Bond ID (needed for historical fallback)

    Returns:
        BondPrice with real or estimated data
    """
    # Try Finnhub first if we have ISIN
    if isin and FINNHUB_API_KEY:
        result = await fetch_finnhub_price(isin)
        if result.last_price is not None:
            result.cusip = cusip
            return result

    # Fall back to most recent historical TRACE price
    if session and debt_instrument_id:
        hist_price = await get_latest_historical_trace_price(session, debt_instrument_id)
        if hist_price is not None:
            hist_price.cusip = cusip
            hist_price.isin = isin
            return hist_price

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
    require_isin: bool = False,
) -> list[DebtInstrument]:
    """
    Get bonds that need pricing updates.

    Returns bonds that:
    - Have maturity dates in the future
    - Have coupon rates (for estimation)
    - Optionally filtered by company ticker
    - Optionally filtered to only stale prices (using a single JOIN, not N+1)
    - Optionally filtered to only bonds with ISINs (needed for Finnhub)
    """
    # Tradeable instrument types
    tradeable_types = [
        "senior_notes", "notes", "bonds", "debentures",
        "convertible_notes", "senior_secured_notes", "subordinated_notes"
    ]

    if stale_only:
        # Single query with LEFT JOIN to check staleness — avoids N+1
        from sqlalchemy import or_, func

        query = (
            select(DebtInstrument)
            .outerjoin(BondPricing, DebtInstrument.id == BondPricing.debt_instrument_id)
            .where(DebtInstrument.instrument_type.in_(tradeable_types))
            .where(DebtInstrument.is_active == True)
            .where(DebtInstrument.maturity_date > date.today())
            .where(DebtInstrument.interest_rate.isnot(None))
            .where(
                or_(
                    # No pricing record at all
                    BondPricing.id.is_(None),
                    # Staleness exceeds threshold
                    BondPricing.staleness_days.is_(None),
                    BondPricing.staleness_days >= stale_days,
                    # fetched_at is old
                    BondPricing.fetched_at.is_(None),
                    BondPricing.fetched_at < func.now() - timedelta(days=stale_days),
                )
            )
        )
    else:
        query = (
            select(DebtInstrument)
            .where(DebtInstrument.instrument_type.in_(tradeable_types))
            .where(DebtInstrument.is_active == True)
            .where(DebtInstrument.maturity_date > date.today())
            .where(DebtInstrument.interest_rate.isnot(None))
        )

    if ticker:
        query = query.join(Company).where(Company.ticker == ticker.upper())

    if require_isin:
        query = query.where(DebtInstrument.isin.isnot(None))

    # Order by staleness so oldest-fetched bonds get updated first
    query = query.order_by(DebtInstrument.id).limit(limit)

    result = await session.execute(query)
    return list(result.scalars().all())


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
        # Get price (Finnhub, historical TRACE, or estimated)
        price = await get_bond_price(
            cusip=bond.cusip,
            isin=bond.isin,
            coupon_rate_pct=bond.interest_rate / 100 if bond.interest_rate else None,
            maturity_date=bond.maturity_date,
            credit_rating=None,  # TODO: Add rating to bond model
            session=session,
            debt_instrument_id=bond.id,
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
