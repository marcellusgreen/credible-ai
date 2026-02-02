"""
Treasury Yield History Service

Fetches and stores historical US Treasury yield curve data for accurate
spread calculations on historical bond prices.

Data sources:
- Primary: US Treasury.gov (free, public data with full history)
- Alternative: Finnhub bond yield curve API (if available)

Benchmarks stored: 1M, 3M, 6M, 1Y, 2Y, 3Y, 5Y, 7Y, 10Y, 20Y, 30Y
"""

from dataclasses import dataclass
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional
import csv
from io import StringIO

import httpx
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from app.models import TreasuryYieldHistory
from app.services.bond_pricing import FINNHUB_API_KEY, FINNHUB_BASE_URL


# Treasury benchmarks we track
BENCHMARKS = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]

# Mapping from Treasury.gov CSV headers to our benchmark keys
TREASURY_GOV_HEADER_MAP = {
    "1 Mo": "1M",
    "2 Mo": "2M",
    "3 Mo": "3M",
    "4 Mo": "4M",
    "6 Mo": "6M",
    "1 Yr": "1Y",
    "2 Yr": "2Y",
    "3 Yr": "3Y",
    "5 Yr": "5Y",
    "7 Yr": "7Y",
    "10 Yr": "10Y",
    "20 Yr": "20Y",
    "30 Yr": "30Y",
}


@dataclass
class TreasuryYieldPoint:
    """Single treasury yield observation."""
    yield_date: date
    benchmark: str
    yield_pct: Decimal


@dataclass
class TreasuryFetchResult:
    """Result of fetching treasury yields."""
    yields: list[TreasuryYieldPoint]
    error: Optional[str] = None


@dataclass
class TreasuryYieldStats:
    """Statistics about treasury yield history."""
    total_records: int
    unique_dates: int
    min_date: Optional[date]
    max_date: Optional[date]
    benchmarks_covered: list[str]


def parse_treasury_gov_csv(csv_text: str) -> list[TreasuryYieldPoint]:
    """
    Parse Treasury.gov CSV format into TreasuryYieldPoint list.

    CSV format:
    Date,1 Mo,2 Mo,3 Mo,4 Mo,6 Mo,1 Yr,2 Yr,3 Yr,5 Yr,7 Yr,10 Yr,20 Yr,30 Yr
    01/02/2024,5.55,5.52,5.47,5.43,5.27,4.80,4.33,4.08,3.92,3.95,3.95,4.24,4.08
    """
    yields = []
    reader = csv.DictReader(StringIO(csv_text))

    for row in reader:
        # Parse date (MM/DD/YYYY format)
        date_str = row.get("Date", "").strip()
        if not date_str:
            continue

        try:
            yield_date = datetime.strptime(date_str, "%m/%d/%Y").date()
        except ValueError:
            continue

        # Parse each benchmark yield
        for header, benchmark in TREASURY_GOV_HEADER_MAP.items():
            if benchmark not in BENCHMARKS:
                continue

            value_str = row.get(header, "").strip()
            if not value_str or value_str == "N/A":
                continue

            try:
                yield_pct = Decimal(value_str)
                yields.append(TreasuryYieldPoint(
                    yield_date=yield_date,
                    benchmark=benchmark,
                    yield_pct=yield_pct,
                ))
            except (ValueError, TypeError):
                continue

    return yields


async def fetch_treasury_gov_yields(year: int) -> TreasuryFetchResult:
    """
    Fetch treasury yields from Treasury.gov for a given year.

    Args:
        year: Year to fetch (e.g., 2024)

    Returns:
        TreasuryFetchResult with yields list and optional error
    """
    url = f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all"
    params = {"type": "daily_treasury_yield_curve"}

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url, params=params)

            if resp.status_code == 404:
                return TreasuryFetchResult(yields=[], error=f"No data for year {year}")

            if resp.status_code != 200:
                return TreasuryFetchResult(yields=[], error=f"HTTP {resp.status_code}")

            yields = parse_treasury_gov_csv(resp.text)
            return TreasuryFetchResult(yields=yields, error=None)

    except httpx.TimeoutException:
        return TreasuryFetchResult(yields=[], error="Timeout")
    except Exception as e:
        return TreasuryFetchResult(yields=[], error=str(e)[:100])


async def fetch_finnhub_yield_curve(bond_code: str = "US") -> TreasuryFetchResult:
    """
    Fetch treasury yield curve from Finnhub API.

    Note: Finnhub bond yield curve API may have different data format.
    This is a fallback/alternative source.

    Args:
        bond_code: Bond code (default "US" for US Treasury)

    Returns:
        TreasuryFetchResult with yields list and optional error
    """
    if not FINNHUB_API_KEY:
        return TreasuryFetchResult(yields=[], error="FINNHUB_API_KEY not configured")

    url = f"{FINNHUB_BASE_URL}/bond/yield-curve"
    params = {
        "code": bond_code,
        "token": FINNHUB_API_KEY,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params)

            if resp.status_code != 200:
                return TreasuryFetchResult(yields=[], error=f"HTTP {resp.status_code}")

            data = resp.json()

            if not data or "data" not in data:
                return TreasuryFetchResult(yields=[], error="No data returned")

            # Parse Finnhub format: {"code": "US", "data": [{"d": "2024-01-02", "v": {...}}]}
            yields = []
            for item in data.get("data", []):
                date_str = item.get("d")
                if not date_str:
                    continue

                try:
                    yield_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue

                # Finnhub may return yields in different format - adapt as needed
                values = item.get("v", {})
                for benchmark in BENCHMARKS:
                    if benchmark in values:
                        try:
                            yield_pct = Decimal(str(values[benchmark]))
                            yields.append(TreasuryYieldPoint(
                                yield_date=yield_date,
                                benchmark=benchmark,
                                yield_pct=yield_pct,
                            ))
                        except (ValueError, TypeError):
                            continue

            return TreasuryFetchResult(yields=yields, error=None)

    except httpx.TimeoutException:
        return TreasuryFetchResult(yields=[], error="Timeout")
    except Exception as e:
        return TreasuryFetchResult(yields=[], error=str(e)[:100])


async def get_existing_yield_dates(session: AsyncSession) -> set[date]:
    """Get all dates that already have treasury yield data."""
    result = await session.execute(
        select(TreasuryYieldHistory.yield_date).distinct()
    )
    return {row[0] for row in result.fetchall()}


async def bulk_insert_yields(
    session: AsyncSession,
    yields: list[TreasuryYieldPoint],
    source: str = "treasury.gov",
) -> int:
    """
    Bulk insert treasury yields with upsert.

    Args:
        session: Database session
        yields: List of TreasuryYieldPoint objects
        source: Data source identifier

    Returns:
        Number of records processed
    """
    if not yields:
        return 0

    records = [
        {
            "yield_date": y.yield_date,
            "benchmark": y.benchmark,
            "yield_pct": y.yield_pct,
            "source": source,
        }
        for y in yields
    ]

    stmt = insert(TreasuryYieldHistory).values(records)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_treasury_yield_date_benchmark",
        set_={
            "yield_pct": stmt.excluded.yield_pct,
            "source": stmt.excluded.source,
        }
    )

    await session.execute(stmt)
    return len(records)


async def save_treasury_yields(
    session: AsyncSession,
    yields: list[TreasuryYieldPoint],
    existing_dates: set[date] = None,
    source: str = "treasury.gov",
) -> int:
    """
    Save treasury yields to database, skipping existing dates.

    Args:
        session: Database session
        yields: List of TreasuryYieldPoint objects
        existing_dates: Set of dates to skip
        source: Data source identifier

    Returns:
        Number of records saved
    """
    if not yields:
        return 0

    if existing_dates is None:
        existing_dates = set()

    # Filter out existing dates
    new_yields = [y for y in yields if y.yield_date not in existing_dates]

    if not new_yields:
        return 0

    # Batch insert
    BATCH_SIZE = 500
    saved = 0
    for i in range(0, len(new_yields), BATCH_SIZE):
        batch = new_yields[i:i + BATCH_SIZE]
        saved += await bulk_insert_yields(session, batch, source)

    return saved


async def backfill_treasury_yields(
    session: AsyncSession,
    from_year: int,
    to_year: int,
    dry_run: bool = False,
) -> dict:
    """
    Backfill treasury yields from Treasury.gov for a range of years.

    Args:
        session: Database session
        from_year: Start year (e.g., 2021)
        to_year: End year (e.g., 2024)
        dry_run: If True, don't save to database

    Returns:
        dict with statistics: years_processed, total_yields, saved, errors
    """
    stats = {
        "years_processed": 0,
        "total_yields": 0,
        "saved": 0,
        "errors": [],
    }

    existing_dates = await get_existing_yield_dates(session) if not dry_run else set()

    for year in range(from_year, to_year + 1):
        result = await fetch_treasury_gov_yields(year)

        if result.error:
            stats["errors"].append(f"{year}: {result.error}")
            continue

        stats["years_processed"] += 1
        stats["total_yields"] += len(result.yields)

        if dry_run:
            # Count how many would be new
            new_count = sum(1 for y in result.yields if y.yield_date not in existing_dates)
            stats["saved"] += new_count
        else:
            saved = await save_treasury_yields(
                session, result.yields, existing_dates, source="treasury.gov"
            )
            stats["saved"] += saved
            # Update existing dates for next iteration
            existing_dates.update(y.yield_date for y in result.yields)

    if not dry_run:
        await session.commit()

    return stats


async def get_treasury_yield_for_date(
    session: AsyncSession,
    yield_date: date,
    benchmark: str,
) -> Optional[Decimal]:
    """
    Get treasury yield for a specific date and benchmark.

    If exact date not found, returns the most recent yield before that date.

    Args:
        session: Database session
        yield_date: Date to lookup
        benchmark: Benchmark tenor (e.g., "5Y", "10Y")

    Returns:
        Yield as percentage, or None if not found
    """
    # Try exact date first
    result = await session.execute(
        select(TreasuryYieldHistory.yield_pct)
        .where(TreasuryYieldHistory.yield_date == yield_date)
        .where(TreasuryYieldHistory.benchmark == benchmark)
    )
    row = result.first()
    if row:
        return row[0]

    # Fall back to most recent date before
    result = await session.execute(
        select(TreasuryYieldHistory.yield_pct)
        .where(TreasuryYieldHistory.yield_date < yield_date)
        .where(TreasuryYieldHistory.benchmark == benchmark)
        .order_by(TreasuryYieldHistory.yield_date.desc())
        .limit(1)
    )
    row = result.first()
    return row[0] if row else None


async def get_treasury_curve_for_date(
    session: AsyncSession,
    yield_date: date,
) -> dict[str, Decimal]:
    """
    Get full treasury yield curve for a specific date.

    Args:
        session: Database session
        yield_date: Date to lookup

    Returns:
        Dict of benchmark -> yield_pct
    """
    result = await session.execute(
        select(TreasuryYieldHistory.benchmark, TreasuryYieldHistory.yield_pct)
        .where(TreasuryYieldHistory.yield_date == yield_date)
    )

    curve = {}
    for row in result.fetchall():
        curve[row[0]] = row[1]

    # If no data for exact date, try most recent
    if not curve:
        result = await session.execute(
            select(TreasuryYieldHistory.yield_date)
            .where(TreasuryYieldHistory.yield_date <= yield_date)
            .order_by(TreasuryYieldHistory.yield_date.desc())
            .limit(1)
        )
        row = result.first()
        if row:
            return await get_treasury_curve_for_date(session, row[0])

    return curve


async def get_treasury_yield_stats(session: AsyncSession) -> TreasuryYieldStats:
    """Get statistics about treasury yield history table."""
    total_records = await session.scalar(
        select(func.count()).select_from(TreasuryYieldHistory)
    )

    unique_dates = await session.scalar(
        select(func.count(func.distinct(TreasuryYieldHistory.yield_date)))
    )

    min_date = await session.scalar(
        select(func.min(TreasuryYieldHistory.yield_date))
    )

    max_date = await session.scalar(
        select(func.max(TreasuryYieldHistory.yield_date))
    )

    benchmarks_result = await session.execute(
        select(TreasuryYieldHistory.benchmark).distinct()
    )
    benchmarks_covered = sorted([row[0] for row in benchmarks_result.fetchall()])

    return TreasuryYieldStats(
        total_records=total_records or 0,
        unique_dates=unique_dates or 0,
        min_date=min_date,
        max_date=max_date,
        benchmarks_covered=benchmarks_covered,
    )
