#!/usr/bin/env python3
"""
Fix companies that have CIK numbers as their ticker.

Maps CIK numbers to proper stock ticker symbols.
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models import Company, CompanyMetrics

settings = get_settings()

# CIK to Ticker mapping for well-known companies
# Source: SEC EDGAR company search
CIK_TO_TICKER = {
    "0000001800": "ABT",      # Abbott Laboratories
    "0000002488": "AMD",      # Advanced Micro Devices
    "0000004904": "AEP",      # American Electric Power
    "0000004962": "AXP",      # American Express
    "0000006281": "ADI",      # Analog Devices
    "0000006951": "AMAT",     # Applied Materials
    "0000012927": "BA",       # Boeing
    "0000018230": "CAT",      # Caterpillar
    "0000021344": "KO",       # Coca-Cola
    "0000047111": "HSY",      # Hershey
    "0000050863": "INTC",     # Intel
    "0000051143": "IBM",      # IBM
    "0000059478": "LLY",      # Eli Lilly
    "0000060667": "LOW",      # Lowe's
    "0000063908": "MCD",      # McDonald's
    "0000064040": "SPGI",     # S&P Global
    "0000070318": "THC",      # Tenet Healthcare
    "0000070858": "BAC",      # Bank of America
    "0000072903": "XEL",      # Xcel Energy
    "0000072971": "WFC",      # Wells Fargo
    "0000075362": "PCAR",     # PACCAR
    "0000076334": "PH",       # Parker-Hannifin
    "0000077476": "PEP",      # PepsiCo
    "0000078003": "PFE",      # Pfizer
    "0000080424": "PG",       # Procter & Gamble
    "0000080661": "PGR",      # Progressive
    "0000093410": "CVX",      # Chevron
    "0000097476": "TXN",      # Texas Instruments
    "0000097745": "TMO",      # Thermo Fisher
    "0000100885": "UNP",      # Union Pacific
    "0000101829": "RTX",      # RTX (Raytheon)
    "0000104169": "WMT",      # Walmart
    "0000109198": "TJX",      # TJX Companies
    "0000200406": "JNJ",      # Johnson & Johnson
    "0000277948": "CSX",      # CSX
    "0000310158": "MRK",      # Merck
    "0000310764": "SYK",      # Stryker
    "0000313616": "DHR",      # Danaher
    "0000315189": "DE",       # Deere
    "0000316709": "SCHW",     # Charles Schwab
    "0000318154": "AMGN",     # Amgen
    "0000319201": "KLAC",     # KLA
    "0000354950": "HD",       # Home Depot
    "0000707549": "LRCX",     # Lam Research
    "0000712515": "EA",       # Electronic Arts
    "0000723125": "MU",       # Micron
    "0000723254": "CTAS",     # Cintas
    "0000723531": "PAYX",     # Paychex
    "0000731766": "UNH",      # UnitedHealth
    "0000732712": "VZ",       # Verizon
    "0000732717": "T",        # AT&T
    "0000745732": "ROST",     # Ross Stores
    "0000753308": "NEE",      # NextEra Energy
    "0000766704": "WELL",     # Welltower
    "0000769397": "ADSK",     # Autodesk
    "0000773840": "HON",      # Honeywell
    "0000789570": "MGM",      # MGM Resorts
    "0000796343": "ADBE",     # Adobe
    "0000804328": "QCOM",     # Qualcomm
    "0000815556": "FAST",     # Fastenal
    "0000820313": "APH",      # Amphenol
    "0000827054": "MCHP",     # Microchip
    "0000829224": "SBUX",     # Starbucks
    "0000831001": "C",        # Citigroup
    "0000858877": "CSCO",     # Cisco
    "0000865752": "MNST",     # Monster Beverage
    "0000872589": "REGN",     # Regeneron
    "0000875045": "BIIB",     # Biogen
    "0000875320": "VRTX",     # Vertex
    "0000878927": "ODFL",     # Old Dominion
    "0000880807": "ON",       # ON Semiconductor
    "0000882095": "GILD",     # Gilead
    "0000882835": "ROP",      # Roper
    "0000883241": "SNPS",     # Synopsys
    "0000884887": "RCL",      # Royal Caribbean
    "0000885725": "BSX",      # Boston Scientific
    "0000895421": "MS",       # Morgan Stanley
    "0000896159": "CB",       # Chubb
    "0000896878": "INTU",     # Intuit
    "0000900075": "CPRT",     # Copart
    "0000909832": "COST",     # Costco
    "0000927628": "COF",      # Capital One
    "0000936468": "LMT",      # Lockheed Martin
    "0000946581": "TTWO",     # Take-Two
    "0001035267": "ISRG",     # Intuitive Surgical
    "0001040971": "SLG",      # SL Green
    "0001045609": "PLD",      # Prologis
    "0001048286": "MAR",      # Marriott
    "0001050446": "MSTR",     # MicroStrategy (now Strategy)
    "0001058057": "MRVL",     # Marvell
    "0001065280": "NFLX",     # Netflix
    "0001069183": "AXON",     # Axon
    "0001075531": "BKNG",     # Booking
    "0001103982": "MDLZ",     # Mondelez
    "0001108524": "CRM",      # Salesforce
    "0001109357": "EXC",      # Exelon
    "0001141391": "MA",       # Mastercard
    "0001163165": "COP",      # ConocoPhillips
    "0001164727": "NEM",      # Newmont
    "0001174922": "WYNN",     # Wynn Resorts
    "0001262039": "FTNT",     # Fortinet
    "0001283699": "TMUS",     # T-Mobile
    "0001318605": "TSLA",     # Tesla
    "0001321655": "PLTR",     # Palantir
    "0001327567": "PANW",     # Palo Alto Networks
    "0001327811": "WDAY",     # Workday
    "0001364742": "BLK",      # BlackRock
    "0001393818": "BX",       # Blackstone
    "0001397187": "LULU",     # Lululemon
    "0001402057": "CDW",      # CDW
    "0001403161": "V",        # Visa
    "0001413329": "PM",       # Philip Morris
    "0001413447": "NXPI",     # NXP
    "0001418135": "KDP",      # Keurig Dr Pepper
    "0001442145": "VRSK",     # Verisk
    "0001467373": "ACN",      # Accenture
    "0001498098": "APP",      # AppLovin
    "0001513761": "NCLH",     # Norwegian Cruise
    "0001535527": "CRWD",     # CrowdStrike
    "0001539838": "FANG",     # Diamondback
    "0001543151": "UBER",     # Uber
    "0001551152": "ABBV",     # AbbVie
    "0001551182": "ETN",      # Eaton
    "0001559720": "ABNB",     # Airbnb
    "0001561550": "DDOG",     # Datadog
    "0001596532": "ANET",     # Arista
    "0001613103": "MDT",      # Medtronic
    "0001633917": "PYPL",     # PayPal
    "0001650372": "TEAM",     # Atlassian
    "0001671933": "TTD",      # Trade Desk
    "0001701605": "BKR",      # Baker Hughes
    "0001707925": "LIN",      # Linde
    "0001709048": "MU",       # Micron (duplicate CIK entry)
    "0001713683": "ZS",       # Zscaler
    "0001744489": "DIS",      # Disney
    "0001792789": "DASH",     # DoorDash
    "0001868275": "CEG",      # Constellation Energy
    "0001932393": "GEHC",     # GE HealthCare
}


async def fix_tickers(dry_run: bool = False):
    """Update companies with CIK as ticker to use proper ticker symbols."""

    database_url = settings.database_url
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        # Get all companies with CIK as ticker
        result = await db.execute(select(Company).order_by(Company.ticker))
        companies = list(result.scalars().all())

        cik_companies = [c for c in companies if c.ticker and c.ticker.isdigit()]

        print(f"Found {len(cik_companies)} companies with CIK as ticker")
        print()

        updated = 0
        not_mapped = []

        for company in cik_companies:
            old_ticker = company.ticker
            new_ticker = CIK_TO_TICKER.get(old_ticker)

            if new_ticker:
                # Check if new ticker already exists
                existing = await db.execute(
                    select(Company).where(Company.ticker == new_ticker)
                )
                if existing.scalar_one_or_none():
                    print(f"  SKIP {old_ticker} -> {new_ticker}: ticker already exists")
                    continue

                if not dry_run:
                    # Update Company
                    company.ticker = new_ticker
                    company.cik = old_ticker  # Store CIK properly

                    # Update CompanyMetrics if exists
                    await db.execute(
                        update(CompanyMetrics)
                        .where(CompanyMetrics.ticker == old_ticker)
                        .values(ticker=new_ticker)
                    )

                name_safe = company.name[:40].encode('ascii', 'replace').decode('ascii')
                print(f"  {old_ticker} -> {new_ticker}: {name_safe}")
                updated += 1
            else:
                not_mapped.append((old_ticker, company.name))

        if not dry_run:
            await db.commit()

        print()
        print(f"{'Would update' if dry_run else 'Updated'}: {updated} companies")

        if not_mapped:
            print(f"\nNot mapped ({len(not_mapped)}):")
            for cik, name in not_mapped:
                name_safe = name[:50].encode('ascii', 'replace').decode('ascii')
                print(f"  {cik}: {name_safe}")

        await engine.dispose()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    args = parser.parse_args()

    asyncio.run(fix_tickers(dry_run=args.dry_run))
