#!/usr/bin/env python3
"""
Expand Bond Pricing Coverage - Three Phase Approach

Phase 1: Price existing ISINs (509 instruments ready to price)
Phase 2: Derive ISINs from CUSIPs and validate with Finnhub
Phase 3: Discover ISINs from SEC filings (FWP prospectuses)

Usage:
    python scripts/expand_bond_pricing.py --analyze
    python scripts/expand_bond_pricing.py --phase1 --dry-run
    python scripts/expand_bond_pricing.py --phase1
    python scripts/expand_bond_pricing.py --phase2 --dry-run
    python scripts/expand_bond_pricing.py --phase3 --ticker AVGO --dry-run
    python scripts/expand_bond_pricing.py --all
"""

import argparse
import asyncio
import os
import re
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

import httpx
from dotenv import load_dotenv
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import DebtInstrument, Company, BondPricing
from app.services.bond_pricing import (
    FINNHUB_API_KEY,
    FINNHUB_BASE_URL,
    REQUEST_DELAY,
    fetch_finnhub_price,
    fetch_finnhub_bond_profile,
    save_bond_pricing,
)
from app.services.yield_calculation import calculate_ytm_and_spread
from app.services.identifier_utils import (
    cusip_to_isin,
    validate_isin,
    extract_identifiers_from_text,
)

load_dotenv()


async def fetch_and_validate_isin(
    client: httpx.AsyncClient,
    isin: str,
) -> tuple[bool, Optional[dict]]:
    """
    Validate ISIN by fetching profile from Finnhub.

    Returns:
        (is_valid, profile_data) tuple
    """
    if not FINNHUB_API_KEY:
        return False, None

    url = f"{FINNHUB_BASE_URL}/bond/profile"
    params = {"isin": isin, "token": FINNHUB_API_KEY}

    try:
        resp = await client.get(url, params=params)
        if resp.status_code == 200:
            data = resp.json()
            if data and data.get("isin"):
                return True, data
        return False, None
    except Exception:
        return False, None


async def fetch_pricing_for_isin(
    client: httpx.AsyncClient,
    isin: str,
    days_back: int = 30,
) -> Optional[dict]:
    """
    Fetch pricing data from Finnhub for an ISIN.

    Returns dict with price data or None.
    """
    if not FINNHUB_API_KEY:
        return None

    to_date = datetime.now()
    from_date = to_date - timedelta(days=days_back)

    url = f"{FINNHUB_BASE_URL}/bond/price"
    params = {
        "isin": isin,
        "from": int(from_date.timestamp()),
        "to": int(to_date.timestamp()),
        "token": FINNHUB_API_KEY,
    }

    try:
        resp = await client.get(url, params=params)
        if resp.status_code == 200:
            data = resp.json()
            if data and "c" in data and data["c"]:
                closes = data.get("c", [])
                timestamps = data.get("t", [])
                volumes = data.get("v", [])

                return {
                    "last_price": Decimal(str(closes[-1])) if closes else None,
                    "last_trade_date": datetime.fromtimestamp(timestamps[-1]) if timestamps else None,
                    "volume": sum(volumes) if volumes else None,
                }
        return None
    except Exception:
        return None


async def phase1_price_existing_isins(
    async_session,
    dry_run: bool = False,
    limit: Optional[int] = None,
):
    """
    Phase 1: Update pricing for all instruments that already have ISINs.
    """
    print("\n" + "=" * 70)
    print("PHASE 1: PRICE EXISTING ISINs")
    print("=" * 70)

    async with async_session() as session:
        # Get instruments with ISINs
        query = text("""
            SELECT di.id, di.isin, di.cusip, di.name, di.interest_rate, di.maturity_date,
                   c.ticker
            FROM debt_instruments di
            JOIN entities e ON di.issuer_id = e.id
            JOIN companies c ON e.company_id = c.id
            WHERE di.is_active = true
            AND di.isin IS NOT NULL AND di.isin <> ''
            AND di.maturity_date > CURRENT_DATE
            ORDER BY c.ticker, di.maturity_date
        """)

        if limit:
            query = text(str(query) + f" LIMIT {limit}")

        result = await session.execute(query)
        instruments = result.fetchall()

    print(f"Found {len(instruments)} instruments with ISINs")

    if not FINNHUB_API_KEY:
        print("ERROR: FINNHUB_API_KEY not set")
        return

    stats = {
        "total": len(instruments),
        "priced": 0,
        "no_data": 0,
        "errors": 0,
        "yields_calculated": 0,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, row in enumerate(instruments):
            inst_id, isin, cusip, name, rate, maturity, ticker = row

            # Fetch pricing
            pricing = await fetch_pricing_for_isin(client, isin)

            if pricing and pricing.get("last_price"):
                stats["priced"] += 1

                # Calculate YTM if we have rate and maturity
                ytm_bps = None
                spread_bps = None
                benchmark = None

                if rate and maturity:
                    try:
                        ytm_bps, spread_bps, benchmark = await calculate_ytm_and_spread(
                            price=float(pricing["last_price"]),
                            coupon_rate=rate / 100,  # bps to decimal
                            maturity_date=maturity,
                        )
                        stats["yields_calculated"] += 1
                    except Exception:
                        pass

                status = f"Price: {pricing['last_price']:.2f}"
                if ytm_bps:
                    status += f", YTM: {ytm_bps/100:.2f}%"
                if spread_bps:
                    status += f", Spread: {spread_bps}bps"

                if not dry_run:
                    async with async_session() as session:
                        # Check if pricing record exists
                        existing = await session.execute(
                            select(BondPricing).where(BondPricing.debt_instrument_id == inst_id)
                        )
                        bp = existing.scalar_one_or_none()

                        if bp:
                            bp.last_price = pricing["last_price"]
                            bp.last_trade_date = pricing.get("last_trade_date")
                            bp.last_trade_volume = pricing.get("volume")
                            bp.price_source = "TRACE"
                            bp.fetched_at = datetime.now()
                            if ytm_bps:
                                bp.ytm_bps = ytm_bps
                                bp.spread_to_treasury_bps = spread_bps
                                bp.treasury_benchmark = benchmark
                                bp.calculated_at = datetime.now()
                        else:
                            bp = BondPricing(
                                debt_instrument_id=inst_id,
                                cusip=cusip,
                                last_price=pricing["last_price"],
                                last_trade_date=pricing.get("last_trade_date"),
                                last_trade_volume=pricing.get("volume"),
                                ytm_bps=ytm_bps,
                                spread_to_treasury_bps=spread_bps,
                                treasury_benchmark=benchmark,
                                price_source="TRACE",
                                calculated_at=datetime.now() if ytm_bps else None,
                            )
                            session.add(bp)

                        await session.commit()
            else:
                stats["no_data"] += 1
                status = "No data"

            print(f"[{i+1}/{len(instruments)}] {ticker} {isin}: {status}")

            # Rate limit
            if i < len(instruments) - 1:
                await asyncio.sleep(REQUEST_DELAY)

    print("\n" + "-" * 70)
    print("PHASE 1 SUMMARY")
    print("-" * 70)
    print(f"Total instruments:    {stats['total']}")
    print(f"Priced successfully:  {stats['priced']}")
    print(f"No data available:    {stats['no_data']}")
    print(f"Yields calculated:    {stats['yields_calculated']}")
    if dry_run:
        print("\n[DRY RUN - No data was saved]")


async def phase2_derive_isins_from_cusips(
    async_session,
    dry_run: bool = False,
    limit: Optional[int] = None,
):
    """
    Phase 2: Derive ISINs from CUSIPs and validate with Finnhub.
    """
    print("\n" + "=" * 70)
    print("PHASE 2: DERIVE ISINs FROM CUSIPs")
    print("=" * 70)

    async with async_session() as session:
        # Get instruments with CUSIPs but no ISINs
        query = text("""
            SELECT di.id, di.cusip, di.name, di.interest_rate, di.maturity_date,
                   c.ticker
            FROM debt_instruments di
            JOIN entities e ON di.issuer_id = e.id
            JOIN companies c ON e.company_id = c.id
            WHERE di.is_active = true
            AND di.cusip IS NOT NULL AND di.cusip <> '' AND LENGTH(di.cusip) = 9
            AND (di.isin IS NULL OR di.isin = '')
            AND di.maturity_date > CURRENT_DATE
            ORDER BY c.ticker, di.maturity_date
        """)

        if limit:
            query = text(str(query) + f" LIMIT {limit}")

        result = await session.execute(query)
        instruments = result.fetchall()

    print(f"Found {len(instruments)} instruments with CUSIPs but no ISINs")

    if len(instruments) == 0:
        print("No instruments to process in Phase 2")
        return

    if not FINNHUB_API_KEY:
        print("ERROR: FINNHUB_API_KEY not set")
        return

    stats = {
        "total": len(instruments),
        "valid_isin": 0,
        "invalid_isin": 0,
        "priced": 0,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, row in enumerate(instruments):
            inst_id, cusip, name, rate, maturity, ticker = row

            # Derive ISIN from CUSIP
            derived_isin = cusip_to_isin(cusip)
            if not derived_isin:
                print(f"[{i+1}/{len(instruments)}] {ticker} {cusip}: Invalid CUSIP format")
                continue

            # Validate with Finnhub
            is_valid, profile = await fetch_and_validate_isin(client, derived_isin)

            if is_valid:
                stats["valid_isin"] += 1

                # Try to get pricing
                pricing = await fetch_pricing_for_isin(client, derived_isin)
                if pricing and pricing.get("last_price"):
                    stats["priced"] += 1
                    status = f"Valid! Price: {pricing['last_price']:.2f}"
                else:
                    status = "Valid ISIN, no recent pricing"

                if not dry_run:
                    async with async_session() as session:
                        # Update instrument with derived ISIN
                        await session.execute(
                            update(DebtInstrument)
                            .where(DebtInstrument.id == inst_id)
                            .values(isin=derived_isin)
                        )
                        await session.commit()
            else:
                stats["invalid_isin"] += 1
                status = "ISIN not found in Finnhub"

            print(f"[{i+1}/{len(instruments)}] {ticker} {cusip} -> {derived_isin}: {status}")

            # Rate limit
            if i < len(instruments) - 1:
                await asyncio.sleep(REQUEST_DELAY)

    print("\n" + "-" * 70)
    print("PHASE 2 SUMMARY")
    print("-" * 70)
    print(f"Total instruments:    {stats['total']}")
    print(f"Valid ISINs found:    {stats['valid_isin']}")
    print(f"Invalid ISINs:        {stats['invalid_isin']}")
    print(f"With pricing data:    {stats['priced']}")
    if dry_run:
        print("\n[DRY RUN - No data was saved]")


async def phase3_discover_isins_from_sec(
    async_session,
    ticker: Optional[str] = None,
    dry_run: bool = False,
    limit: Optional[int] = None,
):
    """
    Phase 3: Discover ISINs from SEC filings (FWP prospectuses).

    This searches SEC filings for ISIN patterns and matches them to our instruments.
    """
    print("\n" + "=" * 70)
    print("PHASE 3: DISCOVER ISINs FROM SEC FILINGS")
    print("=" * 70)

    # Get companies with instruments lacking identifiers
    async with async_session() as session:
        if ticker:
            query = text("""
                SELECT c.id, c.ticker, c.cik, COUNT(*) as no_id_count
                FROM debt_instruments di
                JOIN entities e ON di.issuer_id = e.id
                JOIN companies c ON e.company_id = c.id
                WHERE di.is_active = true
                AND (di.isin IS NULL OR di.isin = '')
                AND c.ticker = :ticker
                GROUP BY c.id, c.ticker, c.cik
            """)
            result = await session.execute(query, {"ticker": ticker.upper()})
        else:
            query = text("""
                SELECT c.id, c.ticker, c.cik, COUNT(*) as no_id_count
                FROM debt_instruments di
                JOIN entities e ON di.issuer_id = e.id
                JOIN companies c ON e.company_id = c.id
                WHERE di.is_active = true
                AND (di.isin IS NULL OR di.isin = '')
                GROUP BY c.id, c.ticker, c.cik
                ORDER BY no_id_count DESC
            """)
            if limit:
                query = text(str(query) + f" LIMIT {limit}")
            result = await session.execute(query)

        companies = result.fetchall()

    print(f"Found {len(companies)} companies with instruments lacking ISINs")

    if not companies:
        print("No companies to process")
        return

    # ISIN pattern: 2 letters + 9 alphanumeric + 1 digit
    isin_pattern = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b")
    # CUSIP pattern: 9 alphanumeric
    cusip_pattern = re.compile(r"\b([0-9A-Z]{6}[0-9A-Z]{2}[0-9])\b")

    stats = {
        "companies_processed": 0,
        "isins_discovered": 0,
        "isins_matched": 0,
        "isins_validated": 0,
    }

    for company_id, company_ticker, cik, no_id_count in companies:
        print(f"\n--- {company_ticker} (CIK: {cik}, {no_id_count} instruments without ISIN) ---")

        if not cik:
            print("  No CIK available, skipping")
            continue

        # Get instruments for this company
        async with async_session() as session:
            result = await session.execute(
                text("""
                    SELECT di.id, di.name, di.interest_rate, di.maturity_date
                    FROM debt_instruments di
                    JOIN entities e ON di.issuer_id = e.id
                    JOIN companies c ON e.company_id = c.id
                    WHERE di.is_active = true
                    AND (di.isin IS NULL OR di.isin = '')
                    AND c.id = :company_id
                """),
                {"company_id": company_id},
            )
            instruments = result.fetchall()

        # Search SEC for ISINs
        # For now, we'll use the document_sections table which already has SEC content
        async with async_session() as session:
            result = await session.execute(
                text("""
                    SELECT ds.content, ds.section_type, ds.filing_date
                    FROM document_sections ds
                    JOIN companies c ON ds.company_id = c.id
                    WHERE c.id = :company_id
                    AND (ds.section_type IN ('indenture', 'credit_agreement', 'debt_footnote')
                         OR ds.content ILIKE '%ISIN%' OR ds.content ILIKE '%CUSIP%')
                    ORDER BY ds.filing_date DESC
                    LIMIT 20
                """),
                {"company_id": company_id},
            )
            sections = result.fetchall()

        discovered_isins = set()
        discovered_cusips = set()
        for content, section_type, filing_date in sections:
            if content:
                # Find ISINs
                for isin in isin_pattern.findall(content):
                    if isin.startswith("US") and validate_isin(isin):
                        discovered_isins.add(isin)

                # Find CUSIPs
                for cusip in cusip_pattern.findall(content):
                    if not cusip.isdigit():  # Skip pure numbers (false positives)
                        # Check if this CUSIP is part of an ISIN we found
                        is_part_of_isin = any(isin[2:11] == cusip for isin in discovered_isins)
                        if not is_part_of_isin:
                            discovered_cusips.add(cusip)

        # Convert CUSIPs to ISINs
        for cusip in discovered_cusips:
            derived_isin = cusip_to_isin(cusip)
            if derived_isin and validate_isin(derived_isin):
                discovered_isins.add(derived_isin)

        # Also use the identifier utility for more robust extraction
        for content, section_type, filing_date in sections:
            if content:
                ids = extract_identifiers_from_text(content)
                for isin in ids["isins"]:
                    if isin.startswith("US"):
                        discovered_isins.add(isin)

        if discovered_isins:
            print(f"  Discovered {len(discovered_isins)} potential ISINs from SEC filings")
            stats["isins_discovered"] += len(discovered_isins)

            # Try to match ISINs to instruments
            if FINNHUB_API_KEY:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    for isin in list(discovered_isins)[:10]:  # Limit API calls
                        is_valid, profile = await fetch_and_validate_isin(client, isin)
                        if is_valid and profile:
                            stats["isins_validated"] += 1
                            coupon = profile.get("coupon")
                            maturity = profile.get("maturityDate")
                            print(f"    {isin}: Valid - Coupon: {coupon}, Maturity: {maturity}")

                            # Try to match to our instruments by coupon/maturity
                            for inst_id, inst_name, inst_rate, inst_maturity in instruments:
                                # Match by coupon rate (within 0.1%)
                                if coupon and inst_rate:
                                    rate_match = abs(coupon - inst_rate / 100) < 0.1
                                else:
                                    rate_match = False

                                # Match by maturity year
                                if maturity and inst_maturity:
                                    try:
                                        finnhub_mat = datetime.strptime(maturity, "%Y-%m-%d").date()
                                        year_match = finnhub_mat.year == inst_maturity.year
                                    except:
                                        year_match = False
                                else:
                                    year_match = False

                                if rate_match and year_match:
                                    stats["isins_matched"] += 1
                                    print(f"      MATCHED to: {inst_name[:50]}...")

                                    if not dry_run:
                                        async with async_session() as session:
                                            await session.execute(
                                                update(DebtInstrument)
                                                .where(DebtInstrument.id == inst_id)
                                                .values(isin=isin)
                                            )
                                            await session.commit()
                                    break

                        await asyncio.sleep(REQUEST_DELAY)
        else:
            print("  No ISINs found in SEC filings")

        stats["companies_processed"] += 1

    print("\n" + "-" * 70)
    print("PHASE 3 SUMMARY")
    print("-" * 70)
    print(f"Companies processed:  {stats['companies_processed']}")
    print(f"ISINs discovered:     {stats['isins_discovered']}")
    print(f"ISINs validated:      {stats['isins_validated']}")
    print(f"ISINs matched:        {stats['isins_matched']}")
    if dry_run:
        print("\n[DRY RUN - No data was saved]")


async def main():
    parser = argparse.ArgumentParser(
        description="Expand bond pricing coverage using three-phase approach"
    )
    parser.add_argument("--analyze", action="store_true", help="Analyze current coverage")
    parser.add_argument("--phase1", action="store_true", help="Phase 1: Price existing ISINs")
    parser.add_argument("--phase2", action="store_true", help="Phase 2: Derive ISINs from CUSIPs")
    parser.add_argument("--phase3", action="store_true", help="Phase 3: Discover ISINs from SEC")
    parser.add_argument("--all", action="store_true", help="Run all phases")
    parser.add_argument("--ticker", help="Limit to specific company (Phase 3)")
    parser.add_argument("--limit", type=int, help="Limit number of instruments/companies")
    parser.add_argument("--dry-run", action="store_true", help="Don't save to database")

    args = parser.parse_args()

    if not any([args.analyze, args.phase1, args.phase2, args.phase3, args.all]):
        parser.error("Must specify --analyze, --phase1, --phase2, --phase3, or --all")

    # Check API key
    if not FINNHUB_API_KEY and not args.analyze:
        print("WARNING: FINNHUB_API_KEY not set - pricing features will be limited")

    # Database connection
    database_url = os.getenv("DATABASE_URL", "")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    if not database_url:
        print("Error: DATABASE_URL not set")
        sys.exit(1)

    engine = create_async_engine(database_url, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    try:
        if args.analyze:
            # Import and run analysis
            from analyze_bond_coverage import analyze_coverage
            await analyze_coverage()
            return

        if args.all or args.phase1:
            await phase1_price_existing_isins(
                async_session,
                dry_run=args.dry_run,
                limit=args.limit,
            )

        if args.all or args.phase2:
            await phase2_derive_isins_from_cusips(
                async_session,
                dry_run=args.dry_run,
                limit=args.limit,
            )

        if args.all or args.phase3:
            await phase3_discover_isins_from_sec(
                async_session,
                ticker=args.ticker,
                dry_run=args.dry_run,
                limit=args.limit if not args.ticker else None,
            )

    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
