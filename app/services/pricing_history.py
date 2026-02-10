"""
Bond Pricing History Service

Handles historical bond pricing data:
- Backfilling historical prices from Finnhub TRACE data
- Daily snapshots to bond_pricing_history table
- Querying historical pricing for Business tier

FINRA TRACE data availability:
- Intraday data: Up to 3 years back
- End-of-day data: Up to 10 years back
"""

from dataclasses import dataclass
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

import httpx
from sqlalchemy import select, func, and_, exists
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from app.models import DebtInstrument, Company, BondPricing, BondPricingHistory
from app.services.bond_pricing import FINNHUB_API_KEY, FINNHUB_BASE_URL
from app.services.yield_calculation import calculate_ytm, select_treasury_benchmark


# Default backfill period (3 years - limit of FINRA TRACE intraday data)
DEFAULT_BACKFILL_DAYS = 3 * 365

# Batch size for bulk inserts
BATCH_SIZE = 100


@dataclass
class PricePoint:
    """Single price observation."""
    price_date: date
    price: Decimal
    volume: Optional[int] = None
    ytm_bps: Optional[int] = None
    spread_bps: Optional[int] = None


@dataclass
class FetchResult:
    """Result of fetching historical prices from API."""
    prices: list[PricePoint]
    error: Optional[str] = None


@dataclass
class BackfillResult:
    """Result of backfilling a single bond."""
    isin: str
    cusip: Optional[str]
    name: str
    days_requested: int
    prices_found: int = 0
    prices_saved: int = 0
    skipped_existing: int = 0
    error: Optional[str] = None


@dataclass
class SnapshotStats:
    """Statistics from daily snapshot operation."""
    total_current: int = 0
    copied: int = 0
    skipped_existing: int = 0
    errors: int = 0


@dataclass
class HistoryStats:
    """Statistics about pricing history table."""
    isin_count: int
    history_count: int
    instruments_with_history: int
    coverage_pct: float
    min_date: Optional[date]
    max_date: Optional[date]


def parse_finnhub_candles(data: dict) -> list[PricePoint]:
    """
    Parse Finnhub candle response into PricePoint list.

    Finnhub returns: {c: [closes], h: [highs], l: [lows], o: [opens],
                      t: [timestamps], v: [volumes], s: "ok"}
    """
    if not data or data.get("s") == "no_data" or "c" not in data or not data["c"]:
        return []

    closes = data.get("c", [])
    timestamps = data.get("t", [])
    volumes = data.get("v", [])

    prices = []
    for i, ts in enumerate(timestamps):
        if i < len(closes) and closes[i] is not None:
            prices.append(PricePoint(
                price_date=datetime.fromtimestamp(ts).date(),
                price=Decimal(str(closes[i])),
                volume=volumes[i] if i < len(volumes) else None,
            ))

    return prices


async def fetch_historical_candles(
    isin: str,
    from_date: date,
    to_date: date,
    client: httpx.AsyncClient,
) -> FetchResult:
    """
    Fetch historical candle data from Finnhub for a date range.

    Args:
        isin: ISIN identifier
        from_date: Start date
        to_date: End date
        client: httpx client for connection reuse

    Returns:
        FetchResult with prices list and optional error
    """
    if not FINNHUB_API_KEY:
        return FetchResult(prices=[], error="FINNHUB_API_KEY not configured")

    url = f"{FINNHUB_BASE_URL}/bond/price"
    params = {
        "isin": isin,
        "from": int(datetime.combine(from_date, datetime.min.time()).timestamp()),
        "to": int(datetime.combine(to_date, datetime.max.time()).timestamp()),
        "token": FINNHUB_API_KEY,
    }

    try:
        resp = await client.get(url, params=params)

        if resp.status_code == 401:
            return FetchResult(prices=[], error="API key invalid")
        if resp.status_code == 403:
            return FetchResult(prices=[], error="Premium subscription required")
        if resp.status_code == 429:
            return FetchResult(prices=[], error="Rate limit exceeded")
        if resp.status_code != 200:
            return FetchResult(prices=[], error=f"HTTP {resp.status_code}")

        prices = parse_finnhub_candles(resp.json())
        return FetchResult(prices=prices, error=None)

    except httpx.TimeoutException:
        return FetchResult(prices=[], error="Timeout")
    except Exception as e:
        return FetchResult(prices=[], error=str(e)[:100])


def calculate_ytm_for_price(
    price: Decimal,
    coupon_rate: float,
    maturity_date: date,
    price_date: date,
) -> Optional[int]:
    """
    Calculate YTM in basis points for a historical price.

    Returns None if calculation fails or data is insufficient.
    """
    if not coupon_rate or not maturity_date or maturity_date <= price_date:
        return None

    try:
        ytm_pct = calculate_ytm(
            price=float(price),
            coupon_rate=coupon_rate / 100,  # bps to percent
            maturity_date=maturity_date,
            settlement_date=price_date,
        )
        return int(ytm_pct * 100)  # percent to bps
    except Exception:
        return None


def calculate_spread_for_price(
    ytm_bps: int,
    maturity_date: date,
    price_date: date,
    treasury_curve: dict[str, Decimal],
) -> Optional[int]:
    """
    Calculate spread to treasury in basis points using historical treasury yields.

    Args:
        ytm_bps: Bond yield to maturity in basis points
        maturity_date: Bond maturity date
        price_date: Date of the price observation
        treasury_curve: Dict of benchmark -> yield_pct for the price date

    Returns:
        Spread in basis points, or None if calculation fails
    """
    if not ytm_bps or not maturity_date or not treasury_curve:
        return None

    try:
        # Calculate years to maturity from price date
        years_to_maturity = (maturity_date - price_date).days / 365.25

        # Select appropriate benchmark
        benchmark = select_treasury_benchmark(years_to_maturity)

        # Get treasury yield for that benchmark
        treasury_yield = treasury_curve.get(benchmark)
        if treasury_yield is None:
            return None

        # Calculate spread: bond yield - treasury yield (both in bps)
        ytm_pct = ytm_bps / 100  # convert to percentage
        spread_bps = int((ytm_pct - float(treasury_yield)) * 100)

        return spread_bps
    except Exception:
        return None


async def get_bonds_with_isin(
    session: AsyncSession,
    ticker: str = None,
    limit: int = None,
    resume_from: str = None,
) -> list[DebtInstrument]:
    """
    Get all active bonds with ISINs.

    Args:
        session: Database session
        ticker: Filter by company ticker
        limit: Max bonds to return
        resume_from: Resume from this ISIN (for interrupted runs)

    Returns:
        List of DebtInstrument objects
    """
    query = (
        select(DebtInstrument)
        .where(DebtInstrument.isin.isnot(None))
        .where(DebtInstrument.is_active == True)
        .order_by(DebtInstrument.isin)
    )

    if ticker:
        query = (
            query
            .join(DebtInstrument.issuer)
            .join(Company, Company.id == DebtInstrument.issuer.property.mapper.class_.company_id)
            .where(Company.ticker == ticker.upper())
        )

    if resume_from:
        query = query.where(DebtInstrument.isin >= resume_from)

    if limit:
        query = query.limit(limit)

    result = await session.execute(query)
    return list(result.scalars().all())


async def get_existing_history_dates(
    session: AsyncSession,
    debt_instrument_id: UUID,
) -> set[date]:
    """Get dates that already have pricing data for this instrument."""
    result = await session.execute(
        select(BondPricingHistory.price_date)
        .where(BondPricingHistory.debt_instrument_id == debt_instrument_id)
    )
    return {row[0] for row in result.fetchall()}


async def bulk_insert_history(
    session: AsyncSession,
    records: list[dict],
) -> int:
    """
    Bulk insert/upsert records to bond_pricing_history.

    Uses PostgreSQL ON CONFLICT for efficiency.

    Args:
        session: Database session
        records: List of dicts with column values

    Returns:
        Number of records processed
    """
    if not records:
        return 0

    stmt = insert(BondPricingHistory).values(records)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_bond_pricing_history_instrument_date",
        set_={
            "price": stmt.excluded.price,
            "ytm_bps": stmt.excluded.ytm_bps,
            "spread_bps": stmt.excluded.spread_bps,
            "volume": stmt.excluded.volume,
            "price_source": stmt.excluded.price_source,
            "updated_at": datetime.utcnow(),
        }
    )

    await session.execute(stmt)
    return len(records)


async def save_historical_prices(
    session: AsyncSession,
    debt_instrument_id: UUID,
    cusip: str,
    prices: list[PricePoint],
    coupon_rate: float = None,
    maturity_date: date = None,
    existing_dates: set[date] = None,
    calculate_yields: bool = True,
    treasury_curves: dict[date, dict[str, Decimal]] = None,
) -> int:
    """
    Save historical prices to bond_pricing_history table.

    Uses bulk insert for efficiency.

    Args:
        session: Database session
        debt_instrument_id: UUID of the debt instrument
        cusip: CUSIP identifier
        prices: List of PricePoint objects
        coupon_rate: Coupon rate in basis points for YTM calculation
        maturity_date: Maturity date for YTM calculation
        existing_dates: Set of dates to skip (already in DB)
        calculate_yields: Whether to calculate YTM (slower but more complete)
        treasury_curves: Dict of date -> {benchmark -> yield_pct} for spread calculation

    Returns:
        Count of records inserted/updated
    """
    if not prices:
        return 0

    if existing_dates is None:
        existing_dates = set()

    if treasury_curves is None:
        treasury_curves = {}

    # Filter out dates we already have
    new_prices = [p for p in prices if p.price_date not in existing_dates]

    if not new_prices:
        return 0

    # Build records for bulk insert
    records = []
    for p in new_prices:
        ytm_bps = None
        spread_bps = None

        if calculate_yields and coupon_rate and maturity_date:
            ytm_bps = calculate_ytm_for_price(p.price, coupon_rate, maturity_date, p.price_date)

            # Calculate spread if we have treasury curve for this date
            if ytm_bps is not None and p.price_date in treasury_curves:
                spread_bps = calculate_spread_for_price(
                    ytm_bps, maturity_date, p.price_date, treasury_curves[p.price_date]
                )

        records.append({
            "debt_instrument_id": debt_instrument_id,
            "cusip": cusip,
            "price_date": p.price_date,
            "price": p.price,
            "ytm_bps": ytm_bps,
            "spread_bps": spread_bps,
            "volume": p.volume,
            "price_source": "Finnhub",
        })

    # Insert in batches
    saved = 0
    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i:i + BATCH_SIZE]
        saved += await bulk_insert_history(session, batch)

    return saved


async def backfill_bond_history(
    session: AsyncSession,
    bond: DebtInstrument,
    from_date: date,
    to_date: date,
    client: httpx.AsyncClient,
    dry_run: bool = False,
    calculate_yields: bool = True,
    treasury_curves: dict[date, dict[str, Decimal]] = None,
) -> BackfillResult:
    """
    Backfill historical prices for a single bond.

    Args:
        session: Database session
        bond: DebtInstrument to backfill
        from_date: Start date
        to_date: End date
        client: httpx client for API calls
        dry_run: If True, don't save to database
        calculate_yields: Whether to calculate YTM for each price
        treasury_curves: Dict of date -> {benchmark -> yield_pct} for spread calculation

    Returns:
        BackfillResult with statistics
    """
    result = BackfillResult(
        isin=bond.isin,
        cusip=bond.cusip,
        name=bond.name[:50] if bond.name else "Unknown",
        days_requested=(to_date - from_date).days,
    )

    # Get existing dates to avoid re-fetching
    existing_dates = await get_existing_history_dates(session, bond.id)
    result.skipped_existing = len(existing_dates)

    # Fetch historical prices from Finnhub
    fetch_result = await fetch_historical_candles(bond.isin, from_date, to_date, client)

    if fetch_result.error:
        result.error = fetch_result.error
        return result

    result.prices_found = len(fetch_result.prices)

    if not fetch_result.prices:
        return result

    if dry_run:
        # Count how many would be new
        result.prices_saved = sum(
            1 for p in fetch_result.prices if p.price_date not in existing_dates
        )
        return result

    # Save to database
    result.prices_saved = await save_historical_prices(
        session=session,
        debt_instrument_id=bond.id,
        cusip=bond.cusip,
        prices=fetch_result.prices,
        coupon_rate=bond.interest_rate,
        maturity_date=bond.maturity_date,
        existing_dates=existing_dates,
        calculate_yields=calculate_yields,
        treasury_curves=treasury_curves,
    )

    await session.commit()
    return result


async def copy_current_to_history(
    session: AsyncSession,
    price_date: date = None,
    dry_run: bool = False,
) -> SnapshotStats:
    """
    Copy current prices from bond_pricing to bond_pricing_history.

    Uses bulk operations for efficiency.

    Args:
        session: Database session
        price_date: Date for the snapshot (defaults to today)
        dry_run: If True, don't save to database

    Returns:
        SnapshotStats with operation statistics
    """
    if price_date is None:
        price_date = date.today()

    stats = SnapshotStats()

    # Get all current prices with a single query
    result = await session.execute(
        select(BondPricing)
        .where(BondPricing.last_price.isnot(None))
    )
    current_prices = result.scalars().all()
    stats.total_current = len(current_prices)

    if not current_prices:
        return stats

    # Get existing history records for this date in one query
    existing_result = await session.execute(
        select(BondPricingHistory.debt_instrument_id)
        .where(BondPricingHistory.price_date == price_date)
    )
    existing_ids = {row[0] for row in existing_result.fetchall()}

    # Build records for new entries only
    records = []
    for bp in current_prices:
        if bp.debt_instrument_id in existing_ids:
            stats.skipped_existing += 1
            continue

        records.append({
            "debt_instrument_id": bp.debt_instrument_id,
            "cusip": bp.cusip,
            "price_date": price_date,
            "price": bp.last_price,
            "ytm_bps": bp.ytm_bps,
            "spread_bps": bp.spread_to_treasury_bps,
            "volume": bp.last_trade_volume,
            "price_source": bp.price_source,
        })

    if dry_run:
        stats.copied = len(records)
        return stats

    # Bulk insert new records
    if records:
        try:
            stats.copied = await bulk_insert_history(session, records)
            await session.commit()
        except Exception as e:
            stats.errors = len(records)

    return stats


async def get_pricing_history_stats(session: AsyncSession) -> HistoryStats:
    """
    Get statistics about bond_pricing_history table.

    Uses parallel queries for efficiency.
    """
    # Run all stat queries
    isin_count = await session.scalar(
        select(func.count()).select_from(DebtInstrument)
        .where(DebtInstrument.isin.isnot(None))
        .where(DebtInstrument.is_active == True)
    )

    history_count = await session.scalar(
        select(func.count()).select_from(BondPricingHistory)
    )

    instruments_with_history = await session.scalar(
        select(func.count(func.distinct(BondPricingHistory.debt_instrument_id)))
    )

    min_date = await session.scalar(select(func.min(BondPricingHistory.price_date)))
    max_date = await session.scalar(select(func.max(BondPricingHistory.price_date)))

    coverage_pct = (instruments_with_history / isin_count * 100) if isin_count else 0

    return HistoryStats(
        isin_count=isin_count,
        history_count=history_count,
        instruments_with_history=instruments_with_history,
        coverage_pct=coverage_pct,
        min_date=min_date,
        max_date=max_date,
    )


async def backfill_company_history(
    session: AsyncSession,
    company_id: UUID,
    days: int = 365,
    dry_run: bool = False,
) -> dict:
    """
    Backfill historical prices for all bonds belonging to a company.

    Args:
        session: Database session
        company_id: Company UUID
        days: Number of days to backfill (default 365)
        dry_run: If True, don't save to database

    Returns:
        Dict with backfill statistics
    """
    from sqlalchemy import text

    stats = {
        "bonds_processed": 0,
        "bonds_with_data": 0,
        "prices_found": 0,
        "prices_saved": 0,
        "errors": 0,
    }

    # Get all bonds with ISINs for this company
    result = await session.execute(
        text("""
            SELECT di.id, di.isin, di.cusip, di.name, di.interest_rate, di.maturity_date
            FROM debt_instruments di
            JOIN entities e ON di.issuer_id = e.id
            WHERE e.company_id = :company_id
              AND di.isin IS NOT NULL
              AND di.is_active = true
        """),
        {"company_id": company_id}
    )
    bonds = result.fetchall()

    if not bonds:
        return stats

    to_date = date.today()
    from_date = to_date - timedelta(days=days)

    async with httpx.AsyncClient(timeout=30.0) as client:
        for bond_row in bonds:
            bond_id, isin, cusip, name, interest_rate, maturity_date = bond_row
            stats["bonds_processed"] += 1

            # Create a simple bond object for backfill_bond_history
            class BondProxy:
                def __init__(self):
                    self.id = bond_id
                    self.isin = isin
                    self.cusip = cusip
                    self.name = name
                    self.interest_rate = interest_rate
                    self.maturity_date = maturity_date

            bond = BondProxy()

            try:
                result = await backfill_bond_history(
                    session=session,
                    bond=bond,
                    from_date=from_date,
                    to_date=to_date,
                    client=client,
                    dry_run=dry_run,
                    calculate_yields=True,
                )

                if result.error:
                    stats["errors"] += 1
                else:
                    stats["prices_found"] += result.prices_found
                    stats["prices_saved"] += result.prices_saved
                    if result.prices_found > 0:
                        stats["bonds_with_data"] += 1

            except Exception as e:
                stats["errors"] += 1

    return stats


async def get_bond_price_history(
    session: AsyncSession,
    cusip: str = None,
    debt_instrument_id: UUID = None,
    from_date: date = None,
    to_date: date = None,
    limit: int = 365,
) -> list[BondPricingHistory]:
    """
    Get historical prices for a bond (Business tier endpoint).

    Args:
        session: Database session
        cusip: CUSIP identifier
        debt_instrument_id: UUID of debt instrument
        from_date: Start date filter
        to_date: End date filter
        limit: Max records to return

    Returns:
        List of BondPricingHistory records, ordered by date descending
    """
    query = select(BondPricingHistory)

    if debt_instrument_id:
        query = query.where(BondPricingHistory.debt_instrument_id == debt_instrument_id)
    elif cusip:
        query = query.where(BondPricingHistory.cusip == cusip)
    else:
        raise ValueError("Must provide cusip or debt_instrument_id")

    if from_date:
        query = query.where(BondPricingHistory.price_date >= from_date)

    if to_date:
        query = query.where(BondPricingHistory.price_date <= to_date)

    query = query.order_by(BondPricingHistory.price_date.desc()).limit(limit)

    result = await session.execute(query)
    return list(result.scalars().all())
