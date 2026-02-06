#!/usr/bin/env python3
"""
Expand Bond Pricing Coverage - Four Phase Approach

Phase 1: Price existing ISINs (instruments with ISINs ready to price)
Phase 2: Derive ISINs from CUSIPs and validate with Finnhub
Phase 3: Discover ISINs from SEC filings (FWP prospectuses)
Phase 4: Discover bonds from Finnhub by issuer CUSIP prefix (most comprehensive)

Usage:
    python scripts/expand_bond_pricing.py --analyze
    python scripts/expand_bond_pricing.py --phase1 --dry-run
    python scripts/expand_bond_pricing.py --phase1
    python scripts/expand_bond_pricing.py --phase2 --dry-run
    python scripts/expand_bond_pricing.py --phase3 --ticker AVGO --dry-run
    python scripts/expand_bond_pricing.py --phase4 --ticker AAPL
    python scripts/expand_bond_pricing.py --phase4 --all-companies --limit 10
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
from asyncio import Semaphore, Lock
from dotenv import load_dotenv
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

# Load environment variables BEFORE importing app modules
load_dotenv()

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
    validate_cusip,
    calculate_cusip_check_digit,
    extract_identifiers_from_text,
)


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

    try:
        resp = await client.get(
            f"{FINNHUB_BASE_URL}/bond/profile",
            params={"isin": isin, "token": FINNHUB_API_KEY},
        )
        if resp.status_code == 200:
            data = resp.json()
            if data and data.get("isin"):
                return True, data
    except Exception:
        pass
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

    try:
        resp = await client.get(
            f"{FINNHUB_BASE_URL}/bond/price",
            params={
                "isin": isin,
                "from": int(from_date.timestamp()),
                "to": int(to_date.timestamp()),
                "token": FINNHUB_API_KEY,
            },
        )
        if resp.status_code == 200:
            data = resp.json()
            if data and data.get("c"):
                closes = data["c"]
                timestamps = data.get("t", [])
                volumes = data.get("v", [])
                return {
                    "last_price": Decimal(str(closes[-1])) if closes else None,
                    "last_trade_date": datetime.fromtimestamp(timestamps[-1]) if timestamps else None,
                    "volume": sum(volumes) if volumes else None,
                }
    except Exception:
        pass
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


# Known CUSIP issuer codes for companies without CUSIPs in our database
# Format: ticker -> 6-char issuer code (or list of codes for multi-issuer companies)
# These can be looked up from SEC filings, bond prospectuses, or financial data providers
# Note: Foreign issuers start with letter (G=Cayman, U=Luxembourg, etc.)
KNOWN_ISSUER_CODES = {
    # Original entries
    "AVGO": "11135F",   # Broadcom Inc
    "PLD": "69360J",    # Prologis
    "TMO": "883556",    # Thermo Fisher Scientific
    "DHR": "23331A",    # Danaher
    "CSCO": "17275R",   # Cisco Systems
    "RTX": "75513E",    # RTX Corporation (Raytheon)
    "HON": "438516",    # Honeywell
    "LIN": "53803L",    # Linde
    "IBM": "459200",    # IBM
    "GILD": "375558",   # Gilead Sciences
    "ABBV": "00287Y",   # AbbVie
    "NRG": "629377",    # NRG Energy
    "SPG": "82835P",    # Simon Property Group
    "BKNG": "741503",   # Booking Holdings
    "COP": "20826F",    # ConocoPhillips
    "PG": "742718",     # Procter & Gamble
    "SCHW": "808513",   # Charles Schwab
    "ACN": "00846U",    # Accenture
    "BKR": "05723K",    # Baker Hughes
    "CRM": "79466L",    # Salesforce
    "NVDA": "67066G",   # NVIDIA
    "MCD": "580135",    # McDonald's
    "CB": "125523",     # Chubb Limited
    "CLF": "18551P",    # Cleveland-Cliffs
    "PH": "701094",     # Parker-Hannifin
    "MGM": "552953",    # MGM Resorts
    "WMT": "931142",    # Walmart
    "FANG": "25278X",   # Diamondback Energy
    "F": "345370",      # Ford Motor
    "BA": "097023",     # Boeing
    "MRK": "58933Y",    # Merck
    "PFE": "717081",    # Pfizer
    "XOM": "30231G",    # Exxon Mobil
    "VZ": "92343V",     # Verizon
    "LMT": "539830",    # Lockheed Martin
    # Additional major companies (added Feb 2026)
    "AAL": "02376R",    # American Airlines
    "ABT": "002824",    # Abbott Laboratories
    "AEP": "025537",    # American Electric Power
    "AMC": "00165C",    # AMC Entertainment
    "AMZN": "023135",   # Amazon
    "AXP": "025816",    # American Express
    "BAC": "060505",    # Bank of America
    "BSX": "101137",    # Boston Scientific
    "BX": "09260D",     # Blackstone Inc (C Corp)
    "C": "172967",      # Citigroup
    "CAR": "053773",    # Avis Budget Car Rental LLC
    "CCL": "143658",    # Carnival
    "CHTR": "161175",   # Charter Communications Operating LLC
    "CZR": "12769G",    # Caesars Entertainment
    "DE": "244199",     # Deere & Company
    "DIS": "254687",    # Disney
    "DISH": "25470M",   # DISH Network
    "GE": "369604",     # General Electric
    "GS": "38141G",     # Goldman Sachs
    "HSY": "427866",    # Hershey
    "HTZ": "428040",    # Hertz Corporation
    "JPM": "46625H",    # JPMorgan Chase
    "KDP": "49271V",    # Keurig Dr Pepper
    "LLY": "532457",    # Eli Lilly
    "LOW": "548661",    # Lowe's
    "LUMN": "550241",   # Lumen Technologies
    "LYV": "538034",    # Live Nation
    "MAR": "571903",    # Marriott
    "MRVL": "573874",   # Marvell Technology
    "MS": "617446",     # Morgan Stanley
    "NFLX": "64110L",   # Netflix
    "NKE": "654106",    # Nike
    "ORCL": "68389X",   # Oracle
    "SBUX": "855244",   # Starbucks
    "T": "00206R",      # AT&T
    "TGT": "87612G",    # Target
    "TMUS": "872590",   # T-Mobile
    "TSLA": "88160R",   # Tesla
    "UNH": "91324P",    # UnitedHealth
    "UNP": "907818",    # Union Pacific
    "UPS": "911312",    # UPS
    "USB": "903312",    # US Bancorp
    "WFC": "949746",    # Wells Fargo
    # More companies (Feb 2026 batch 2)
    "ATUS": "U0207A",   # Altice US Finance (foreign)
    "BHC": "071734",    # Bausch Health
    "COST": "22160K",   # Costco
    "CVNA": "12690B",   # Carvana
    "FYBR": "35906A",   # Frontier Communications
    "IHRT": "45174H",   # iHeartMedia
    "MDLZ": "609207",   # Mondelez
    "META": "30303M",   # Meta (Facebook)
    "MSFT": "594918",   # Microsoft
    "NCLH": "62886H",   # Norwegian Cruise Line
    "NEE": "65339K",    # NextEra Energy Capital Holdings
    "PARA": "92556H",   # Paramount Global
    "RIG": "893830",    # Transocean Ltd (also G90073, G9008B - foreign)
    "SLG": "78449A",    # SL Green Realty
    "SPGI": "78409V",   # S&P Global
    "SYK": "863667",    # Stryker
    "THC": "88033G",    # Tenet Healthcare
    "UAL": "910047",    # United Airlines
    "V": "92826C",      # Visa
    "VAL": "G9460G",    # Valaris (foreign)
    "VNO": "929043",    # Vornado Realty
    "WYNN": "983130",   # Wynn Las Vegas LLC
    "X": "912909",      # United States Steel
    "XEL": "98389B",    # Xcel Energy
    "LIN": "53522K",    # Linde Inc (US subsidiary)
    "PLD": "74340X",    # Prologis LP
    # New issuer codes researched Feb 2026
    "AAPL": "037833",   # Apple Inc
    "DAL": "247361",    # Delta Air Lines
    "DO": "25271C",     # Diamond Offshore Drilling
    "NE": "65504L",     # Noble Corporation
    "NEM": "651639",    # Newmont Corporation
    "TXN": "882508",    # Texas Instruments
    "NXPI": "N66000",   # NXP Semiconductors
    "INTU": "46124H",   # Intuit Inc
    "APA": "037411",    # APA Corporation (Apache)
    "CEG": "21038S",    # Constellation Energy
    "ON": "682189",     # ON Semiconductor
    "ROP": "776743",    # Roper Technologies
    "GEHC": "36266G",   # GE HealthCare Technologies
    "PCAR": "69371R",   # PACCAR Financial
    "ADSK": "052769",   # Autodesk
    "AMAT": "038222",   # Applied Materials
    "KLAC": "482480",   # KLA Corporation
    "CDNS": "127387",   # Cadence Design Systems
    "SNPS": "871607",   # Synopsys
    "REGN": "75886F",   # Regeneron Pharmaceuticals
    "DXCM": "252131",   # DexCom Inc
    "ABNB": "009066",   # Airbnb
    "CRWD": "22788C",   # CrowdStrike
    "NOW": "81762P",    # ServiceNow
    "WDAY": "98138H",   # Workday
    "PANW": "697435",   # Palo Alto Networks
    "FTNT": "34959E",   # Fortinet
    "ZS": "98980G",     # Zscaler
    "EA": "285512",     # Electronic Arts
    "TTWO": "874054",   # Take-Two Interactive
    "DASH": "25809K",   # DoorDash
    "ORLY": "67103H",   # O'Reilly Automotive
    "TJX": "872540",    # TJX Companies
    "CTAS": "17252M",   # Cintas Corporation
    "PAYX": "704326",   # Paychex
    "AXON": "05464C",   # Axon Enterprise
    "APP": "03831W",    # AppLovin
    "GEV": "369604",    # GE Vernova
    "MSTR": "594972",   # MicroStrategy
    "CSGP": "U22041",   # CoStar Group
    "CNK": "17243V",    # Cinemark Holdings
    "FUN": "150191",    # Cedar Fair / Six Flags
    "CRWV": "21873S",   # CoreWeave
    "DDOG": "23804J",   # Datadog (convertible notes)
    "TEAM": "04930M",   # Atlassian (convertible notes)
}

# Companies with multiple issuer codes (subsidiaries, foreign entities, etc.)
# These are scanned in addition to the primary code in KNOWN_ISSUER_CODES
ADDITIONAL_ISSUER_CODES = {
    "RIG": ["G90073", "G9008B"],  # Transocean Inc (Cayman), other subsidiary
    "CHTR": ["16117P"],           # Charter Communications Holdings
    "ATUS": ["12686C"],           # CSC Holdings
    "BX": ["09253U", "09261H", "09261X"],  # Legacy LP, Private Credit Fund, Secured Lending Fund
    "CAR": ["U05375"],            # Avis Budget (Reg S)
    "HTZ": ["U42804"],            # Hertz (Reg S, post-bankruptcy bonds)
    "NEE": ["65339F"],            # NextEra Energy parent
    "WFC": ["33738M", "92976G"],  # Wells Fargo Bank subsidiaries
    "WYNN": ["983133"],           # Wynn Resorts Finance LLC
    "DAL": ["247367", "U24740"],  # Delta securitization, older bonds
    "APA": ["03746A", "U0379Q"],  # APA different bond tranches
    "CEG": ["30161M"],            # Constellation Energy older bonds
    "GEHC": ["U36364"],           # GE Healthcare Holding
    "TSLA": ["U8810L"],           # Tesla bonds (different from stock CUSIP)
    "AAL": ["023771", "U02413"],  # American Airlines bonds
}


async def phase4_discover_from_finnhub(
    async_session,
    ticker: Optional[str] = None,
    dry_run: bool = False,
    limit: Optional[int] = None,
    parallel: int = 1,
):
    """
    Phase 4: Discover bonds directly from Finnhub by iterating CUSIP issue codes.

    For each company, we:
    1. Get their issuer code (first 6 chars of any known CUSIP, or from lookup table)
    2. Iterate through all possible issue codes (AA-ZZ, 00-99)
    3. Query Finnhub bond/profile for each potential CUSIP
    4. Save discovered bonds with ISINs and pricing

    Note: Runs sequentially to avoid database connection timeouts with Neon serverless.
    """
    print("\n" + "=" * 70)
    print("PHASE 4: DISCOVER BONDS FROM FINNHUB BY ISSUER")
    print("=" * 70)

    if not FINNHUB_API_KEY:
        print("ERROR: FINNHUB_API_KEY not set")
        return

    # Get companies - first try from existing CUSIPs, then from lookup table
    async with async_session() as session:
        if ticker:
            # Single company - get issuer code from existing CUSIP or lookup table
            query = text("""
                SELECT DISTINCT c.id, c.ticker, SUBSTRING(di.cusip, 1, 6) as issuer_code
                FROM companies c
                JOIN entities e ON e.company_id = c.id
                JOIN debt_instruments di ON di.issuer_id = e.id
                WHERE c.ticker = :ticker
                AND di.cusip IS NOT NULL AND LENGTH(di.cusip) = 9
                LIMIT 1
            """)
            result = await session.execute(query, {"ticker": ticker.upper()})
            companies = result.fetchall()

            # If no CUSIP found, check lookup table
            if not companies:
                if ticker.upper() in KNOWN_ISSUER_CODES:
                    # Get company ID
                    result = await session.execute(
                        text("SELECT id FROM companies WHERE ticker = :ticker"),
                        {"ticker": ticker.upper()},
                    )
                    company_id = result.scalar()
                    if company_id:
                        companies = [(company_id, ticker.upper(), KNOWN_ISSUER_CODES[ticker.upper()])]
                        print(f"Using known issuer code from lookup table for {ticker.upper()}")
        else:
            # All companies - get from existing CUSIPs
            query = text("""
                SELECT DISTINCT c.id, c.ticker, SUBSTRING(di.cusip, 1, 6) as issuer_code
                FROM companies c
                JOIN entities e ON e.company_id = c.id
                JOIN debt_instruments di ON di.issuer_id = e.id
                WHERE di.cusip IS NOT NULL AND LENGTH(di.cusip) = 9
                ORDER BY c.ticker
            """)
            result = await session.execute(query)
            companies_with_cusips = result.fetchall()

            # Also add companies from lookup table that don't have CUSIPs
            result = await session.execute(
                text("SELECT id, ticker FROM companies ORDER BY ticker")
            )
            all_companies = {row[1]: row[0] for row in result.fetchall()}

            # Build final list - prioritize existing CUSIPs, add from lookup table
            companies_dict = {row[1]: (row[0], row[1], row[2]) for row in companies_with_cusips}
            for ticker_code, issuer_code in KNOWN_ISSUER_CODES.items():
                if ticker_code not in companies_dict and ticker_code in all_companies:
                    companies_dict[ticker_code] = (all_companies[ticker_code], ticker_code, issuer_code)

            companies = sorted(companies_dict.values(), key=lambda x: x[1])

            if limit:
                companies = companies[:limit]

    print(f"Found {len(companies)} companies with CUSIP issuer codes")

    if not companies:
        print("No companies with CUSIP data to process")
        return

    # Generate 2-character issue codes
    # Corporate bonds typically use A-H as first character (most common)
    # Only search A-H to stay within API rate limits (~288 calls per company)
    issue_codes = []
    for first in "ABCDEFGH":
        for second in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
            issue_codes.append(f"{first}{second}")

    print(f"Will test up to {len(issue_codes)} issue codes per company")
    print(f"Estimated time: ~{len(companies) * 5} minutes ({len(companies)} companies × ~5 min each)")

    stats = {
        "companies_processed": 0,
        "bonds_discovered": 0,
        "bonds_new": 0,
        "api_calls": 0,
    }

    # Process companies sequentially to avoid DB connection issues
    for idx, (company_id, company_ticker, issuer_code) in enumerate(companies):
        # Get all issuer codes for this company (primary + additional)
        all_issuer_codes = [issuer_code]
        if company_ticker in ADDITIONAL_ISSUER_CODES:
            all_issuer_codes.extend(ADDITIONAL_ISSUER_CODES[company_ticker])

        print(f"\n[{idx+1}/{len(companies)}] {company_ticker} (Issuers: {', '.join(all_issuer_codes)})", flush=True)

        # Get existing ISINs for this company to avoid duplicates
        async with async_session() as session:
            result = await session.execute(
                text("""
                    SELECT di.isin, di.cusip
                    FROM debt_instruments di
                    JOIN entities e ON di.issuer_id = e.id
                    WHERE e.company_id = :company_id
                    AND di.isin IS NOT NULL
                """),
                {"company_id": company_id},
            )
            existing = {row[0] for row in result.fetchall()}

        discovered = []
        api_calls = 0

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Scan all issuer codes for this company
            for current_issuer_code in all_issuer_codes:
                for issue in issue_codes:
                    base = f"{current_issuer_code}{issue}"
                    try:
                        check = calculate_cusip_check_digit(base)
                        cusip = base + check
                    except ValueError:
                        continue

                    # Query Finnhub
                    url = f"{FINNHUB_BASE_URL}/bond/profile"
                    params = {"cusip": cusip, "token": FINNHUB_API_KEY}

                    try:
                        resp = await client.get(url, params=params)
                        api_calls += 1

                        if resp.status_code == 429:
                            # Rate limited - wait and retry
                            print(f"  Rate limited, waiting 65s...", flush=True)
                            await asyncio.sleep(65)
                            resp = await client.get(url, params=params)
                            api_calls += 1

                        if resp.status_code == 200:
                            data = resp.json()
                            if data and data.get("isin"):
                                isin = data["isin"]
                                if isin not in existing:
                                    discovered.append({
                                        "cusip": cusip,
                                        "isin": isin,
                                        "coupon": data.get("coupon"),
                                        "maturity": data.get("maturityDate"),
                                        "description": data.get("debtType"),
                                        "amount_outstanding": data.get("amountOutstanding"),
                                        "issue_date": data.get("issueDate"),
                                        "security_level": data.get("securityLevel"),
                                        "bond_type": data.get("bondType"),
                                    })
                                    existing.add(isin)
                    except Exception:
                        pass

                    # Rate limit - ~55 calls/minute
                    await asyncio.sleep(1.1)

        print(f"  Discovered {len(discovered)} bonds ({api_calls} API calls)", flush=True)

        bonds_new = 0

        # Save discovered bonds
        if discovered and not dry_run:
            async with async_session() as session:
                # Get the root entity for this company
                result = await session.execute(
                    text("""
                        SELECT id FROM entities
                        WHERE company_id = :company_id AND is_root = true
                        LIMIT 1
                    """),
                    {"company_id": company_id},
                )
                root_entity = result.scalar()

                if not root_entity:
                    result = await session.execute(
                        text("""
                            SELECT id FROM entities
                            WHERE company_id = :company_id
                            LIMIT 1
                        """),
                        {"company_id": company_id},
                    )
                    root_entity = result.scalar()

                if root_entity:
                    for bond in discovered:
                        # Check if this ISIN already exists
                        result = await session.execute(
                            text("SELECT id FROM debt_instruments WHERE isin = :isin"),
                            {"isin": bond["isin"]},
                        )
                        if result.scalar():
                            continue

                        # Parse maturity date
                        maturity_date = None
                        if bond["maturity"]:
                            try:
                                maturity_date = datetime.strptime(bond["maturity"], "%Y-%m-%d").date()
                            except:
                                pass

                        # Skip matured bonds
                        if maturity_date and maturity_date < date.today():
                            continue

                        # Parse issue date
                        issue_date = None
                        if bond["issue_date"]:
                            try:
                                issue_date = datetime.strptime(bond["issue_date"], "%Y-%m-%d").date()
                            except:
                                pass

                        # Create instrument name
                        name_parts = []
                        if bond["coupon"]:
                            name_parts.append(f"{bond['coupon']}%")
                        if bond["description"]:
                            name_parts.append(bond["description"])
                        if bond["maturity"]:
                            name_parts.append(f"due {bond['maturity'][:4]}")
                        name = " ".join(name_parts) if name_parts else f"Bond {bond['cusip']}"

                        # Determine seniority
                        seniority = "senior_unsecured"
                        sec_level = (bond.get("security_level") or "").lower()
                        desc = (bond.get("description") or "").lower()
                        if "secured" in desc or "secured" in sec_level:
                            seniority = "senior_secured"
                        elif "subordinated" in sec_level or "subordinated" in desc:
                            if "senior" in sec_level or "senior" in desc:
                                seniority = "senior_subordinated"
                            else:
                                seniority = "subordinated"

                        # Create the instrument
                        new_instrument = DebtInstrument(
                            company_id=company_id,
                            issuer_id=root_entity,
                            name=name,
                            instrument_type="bond",
                            cusip=bond["cusip"],
                            isin=bond["isin"],
                            interest_rate=int(bond["coupon"] * 100) if bond["coupon"] else None,
                            maturity_date=maturity_date,
                            issue_date=issue_date,
                            outstanding=bond["amount_outstanding"],
                            seniority=seniority,
                            is_active=True,
                            attributes={"source": "finnhub_discovery"},
                        )
                        session.add(new_instrument)
                        bonds_new += 1

                    await session.commit()

        # Show some discovered bonds
        for bond in discovered[:3]:
            print(f"    {bond['isin']}: {bond['coupon']}% due {bond['maturity']} - {bond['description']}", flush=True)
        if len(discovered) > 3:
            print(f"    ... and {len(discovered) - 3} more", flush=True)

        # Update stats
        stats["companies_processed"] += 1
        stats["bonds_discovered"] += len(discovered)
        stats["bonds_new"] += bonds_new
        stats["api_calls"] += api_calls

        # Progress summary every 5 companies
        if (idx + 1) % 5 == 0:
            print(f"\n[Progress] {stats['companies_processed']}/{len(companies)} companies, "
                  f"{stats['bonds_discovered']} bonds discovered, {stats['bonds_new']} new", flush=True)

    print("\n" + "-" * 70)
    print("PHASE 4 SUMMARY")
    print("-" * 70)
    print(f"Companies processed:  {stats['companies_processed']}")
    print(f"API calls made:       {stats['api_calls']}")
    print(f"Bonds discovered:     {stats['bonds_discovered']}")
    print(f"New bonds added:      {stats['bonds_new']}")
    if dry_run:
        print("\n[DRY RUN - No data was saved]")

    # If we added new bonds, suggest running Phase 1 to price them
    if stats["bonds_new"] > 0:
        print(f"\nRun --phase1 to price the {stats['bonds_new']} newly discovered bonds")


async def main():
    parser = argparse.ArgumentParser(
        description="Expand bond pricing coverage using four-phase approach"
    )
    parser.add_argument("--analyze", action="store_true", help="Analyze current coverage")
    parser.add_argument("--phase1", action="store_true", help="Phase 1: Price existing ISINs")
    parser.add_argument("--phase2", action="store_true", help="Phase 2: Derive ISINs from CUSIPs")
    parser.add_argument("--phase3", action="store_true", help="Phase 3: Discover ISINs from SEC")
    parser.add_argument("--phase4", action="store_true", help="Phase 4: Discover bonds from Finnhub by issuer")
    parser.add_argument("--all", action="store_true", help="Run all phases")
    parser.add_argument("--ticker", help="Limit to specific company (Phase 3/4)")
    parser.add_argument("--limit", type=int, help="Limit number of instruments/companies")
    parser.add_argument("--parallel", type=int, default=5, help="Number of companies to process in parallel for Phase 4 (default: 5)")
    parser.add_argument("--dry-run", action="store_true", help="Don't save to database")

    args = parser.parse_args()

    if not any([args.analyze, args.phase1, args.phase2, args.phase3, args.phase4, args.all]):
        parser.error("Must specify --analyze, --phase1, --phase2, --phase3, --phase4, or --all")

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

    # Use NullPool to avoid connection issues with Neon serverless
    # Each session creates a fresh connection
    from sqlalchemy.pool import NullPool
    engine = create_async_engine(
        database_url,
        echo=False,
        poolclass=NullPool,
    )
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

        if args.all or args.phase4:
            await phase4_discover_from_finnhub(
                async_session,
                ticker=args.ticker,
                dry_run=args.dry_run,
                limit=args.limit if not args.ticker else None,
                parallel=args.parallel,
            )

    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
