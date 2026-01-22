#!/usr/bin/env python3
"""
Batch extraction script for S&P 100 and NASDAQ 100 index companies.

Organized in phases by market cap priority.

Usage:
    python scripts/batch_index.py --phase 1           # Top 50 by market cap
    python scripts/batch_index.py --phase 2           # Remaining ~90 companies
    python scripts/batch_index.py --phase all         # All companies
    python scripts/batch_index.py --list              # Just list companies, don't extract
    python scripts/batch_index.py --ticker TSLA       # Single company
    python scripts/batch_index.py --tickers AAPL,VZ,T --force  # Re-extract specific companies
"""

import argparse
import subprocess
import sys
import time
import re
from pathlib import Path
from datetime import datetime

# Phase 1: Top 50 S&P 100 / NASDAQ 100 by market cap (excluding already extracted)
# These are the most likely to be searched
PHASE_1 = [
    # Mega caps
    ("AAPL", "0000320193"),    # Apple
    ("MSFT", "0000789019"),    # Microsoft
    ("GOOGL", "0001652044"),   # Alphabet
    ("NVDA", "0001045810"),    # NVIDIA
    ("META", "0001326801"),    # Meta
    ("TSLA", "0001318605"),    # Tesla
    ("AVGO", "0001730168"),    # Broadcom
    ("LLY", "0000059478"),     # Eli Lilly
    ("WMT", "0000104169"),     # Walmart
    ("V", "0001403161"),       # Visa
    ("ORCL", "0001341439"),    # Oracle
    ("XOM", "0000034088"),     # Exxon Mobil
    ("JNJ", "0000200406"),     # Johnson & Johnson
    ("MA", "0001141391"),      # Mastercard
    ("COST", "0000909832"),    # Costco
    # Large caps
    ("ABBV", "0001551152"),    # AbbVie
    ("MU", "0000723125"),      # Micron Technology
    ("NFLX", "0001065280"),    # Netflix
    ("HD", "0000354950"),      # Home Depot
    ("AMD", "0000002488"),     # AMD
    ("PG", "0000080424"),      # Procter & Gamble
    ("CVX", "0000093410"),     # Chevron
    ("UNH", "0000731766"),     # UnitedHealth
    ("KO", "0000021344"),      # Coca-Cola
    ("WFC", "0000072971"),     # Wells Fargo
    # Tech/Semis
    ("CSCO", "0000858877"),    # Cisco
    ("MS", "0000895421"),      # Morgan Stanley
    ("IBM", "0000051143"),     # IBM
    ("LRCX", "0000707549"),    # Lam Research
    ("MRK", "0000310158"),     # Merck
    ("RTX", "0000101829"),     # RTX Corporation
    ("PM", "0001413329"),      # Philip Morris
    ("AXP", "0000004962"),     # American Express
    ("AMAT", "0000006951"),    # Applied Materials
    ("CRM", "0001108524"),     # Salesforce
    # More large caps
    ("TMO", "0000097745"),     # Thermo Fisher
    ("INTC", "0000050863"),    # Intel
    ("MCD", "0000063908"),     # McDonald's
    ("TMUS", "0001283699"),    # T-Mobile
    ("ABT", "0000001800"),     # Abbott
    ("C", "0000831001"),       # Citigroup
    ("LIN", "0001707925"),     # Linde
    ("DIS", "0001744489"),     # Disney
    ("ISRG", "0001035267"),    # Intuitive Surgical
    ("PEP", "0000077476"),     # PepsiCo
    # Tech/Growth
    ("KLAC", "0000319201"),    # KLA Corporation
    ("SCHW", "0000316709"),    # Charles Schwab
    ("QCOM", "0000804328"),    # Qualcomm
    ("UBER", "0001543151"),    # Uber
    ("TJX", "0000109198"),     # TJX Companies
    ("AMGN", "0000318154"),    # Amgen
    ("INTU", "0000896878"),    # Intuit
    ("BKNG", "0001075531"),    # Booking Holdings
    ("TXN", "0000097476"),     # Texas Instruments
    ("NEE", "0000753308"),     # NextEra Energy
]

# Phase 2: Remaining S&P 100 / NASDAQ 100 companies
PHASE_2 = [
    # Financials
    ("ACN", "0001467373"),     # Accenture
    ("BLK", "0001364742"),     # BlackRock
    ("DHR", "0000313616"),     # Danaher
    ("VZ", "0000732712"),      # Verizon
    ("T", "0000732717"),       # AT&T
    ("SPGI", "0000064040"),    # S&P Global
    ("COF", "0000927628"),     # Capital One
    ("BX", "0001393818"),      # Blackstone
    # Healthcare
    ("GILD", "0000882095"),    # Gilead Sciences
    ("PFE", "0000078003"),     # Pfizer
    ("BSX", "0000885725"),     # Boston Scientific
    ("SYK", "0000310764"),     # Stryker
    ("MDT", "0001613103"),     # Medtronic
    ("VRTX", "0000875320"),    # Vertex Pharmaceuticals
    ("REGN", "0000872589"),    # Regeneron
    ("BIIB", "0000875045"),    # Biogen
    ("DXCM", "0001093557"),    # DexCom
    ("IDXX", "0000874716"),    # IDEXX Labs
    ("GEHC", "0001932393"),    # GE HealthCare
    # Industrials/Defense
    ("UNP", "0000100885"),     # Union Pacific
    ("HON", "0000773840"),     # Honeywell
    ("DE", "0000315189"),      # Deere & Company
    ("LMT", "0000936468"),     # Lockheed Martin
    ("ETN", "0001551182"),     # Eaton
    ("RTX", "0000101829"),     # RTX (if not already)
    ("PH", "0000076334"),      # Parker Hannifin
    ("PCAR", "0000075362"),    # PACCAR
    ("CSX", "0000277948"),     # CSX Corporation
    ("ODFL", "0000878927"),    # Old Dominion Freight
    ("FAST", "0000815556"),    # Fastenal
    ("CTAS", "0000723254"),    # Cintas
    # Tech/Software
    ("ANET", "0001596532"),    # Arista Networks
    ("NOW", "0001373715"),     # ServiceNow
    ("ADI", "0000006281"),     # Analog Devices
    ("PANW", "0001327567"),    # Palo Alto Networks
    ("ADBE", "0000796343"),    # Adobe
    ("CRWD", "0001535527"),    # CrowdStrike
    ("SNPS", "0000883241"),    # Synopsys
    ("CDNS", "0000813672"),    # Cadence Design
    ("ADSK", "0000769397"),    # Autodesk
    ("WDAY", "0001327811"),    # Workday
    ("FTNT", "0001262039"),    # Fortinet
    ("DDOG", "0001561550"),    # Datadog
    ("ZS", "0001713683"),      # Zscaler
    ("TEAM", "0001650372"),    # Atlassian
    # Consumer
    ("LOW", "0000060667"),     # Lowe's
    ("SBUX", "0000829224"),    # Starbucks
    ("ORLY", "0000898173"),    # O'Reilly Auto
    ("ROST", "0000745732"),    # Ross Stores
    ("MAR", "0001048286"),     # Marriott
    ("ABNB", "0001559720"),    # Airbnb
    ("LULU", "0001397187"),    # Lululemon
    ("MNST", "0000865752"),    # Monster Beverage
    ("MDLZ", "0001103982"),    # Mondelez
    ("CPRT", "0000900075"),    # Copart
    # Energy
    ("COP", "0001163165"),     # ConocoPhillips
    ("FANG", "0001539838"),    # Diamondback Energy
    ("BKR", "0001701605"),     # Baker Hughes
    # Utilities/REITs
    ("CEG", "0001868275"),     # Constellation Energy
    ("AEP", "0000004904"),     # American Electric Power
    ("EXC", "0001109357"),     # Exelon
    ("XEL", "0000072903"),     # Xcel Energy
    ("PLD", "0001045609"),     # Prologis
    ("WELL", "0000766704"),    # Welltower
    ("NEM", "0001164727"),     # Newmont
    ("PGR", "0000080661"),     # Progressive
    ("CB", "0000896159"),      # Chubb
    # Other Tech/Media
    ("PYPL", "0001633917"),    # PayPal
    ("EA", "0000712515"),      # Electronic Arts
    ("TTWO", "0000946581"),    # Take-Two Interactive
    ("MRVL", "0001058057"),    # Marvell Technology
    ("NXPI", "0001413447"),    # NXP Semiconductors
    ("MCHP", "0000827054"),    # Microchip Technology
    ("ON", "0000880807"),      # ON Semiconductor
    ("GFS", "0001709048"),     # GlobalFoundries
    # Growth/Other
    ("PLTR", "0001321655"),    # Palantir
    ("DASH", "0001792789"),    # DoorDash
    ("AXON", "0001069183"),    # Axon Enterprise
    ("MSTR", "0001050446"),    # MicroStrategy
    ("TTD", "0001671933"),     # The Trade Desk
    ("VRSK", "0001442145"),    # Verisk Analytics
    ("ROP", "0000882835"),     # Roper Technologies
    ("CSGP", "0001674101"),    # CoStar Group
    ("CDW", "0001402057"),     # CDW Corporation
    ("PAYX", "0000723531"),    # Paychex
    ("CTSH", "0001058290"),    # Cognizant
    ("APP", "0001498098"),     # AppLovin
    ("GEV", "0001996350"),     # GE Vernova
    ("APH", "0000820313"),     # Amphenol
    # Media/Airlines
    ("FOX", "0001754301"),     # Fox Corporation
    ("UAL", "0000100517"),     # United Airlines
]

def get_existing_tickers():
    """Get list of tickers that already have extraction results."""
    results_dir = Path(__file__).parent.parent / "results"
    existing = set()
    for f in results_dir.glob("*_iterative.json"):
        ticker = f.stem.replace("_iterative", "").upper()
        existing.add(ticker)
    return existing

def run_extraction(ticker: str, cik: str, threshold: int = 80) -> tuple[str, bool, str, float]:
    """Run extraction for a single company. Returns (ticker, success, message, duration)."""
    start_time = time.time()

    try:
        result = subprocess.run(
            [
                sys.executable,
                "scripts/extract_iterative.py",
                "--ticker", ticker,
                "--cik", cik,
                "--save-db",
                "--threshold", str(threshold)
            ],
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
            cwd=Path(__file__).parent.parent
        )

        duration = time.time() - start_time
        output = result.stdout + result.stderr

        if "EXTRACTION COMPLETE" in output:
            match = re.search(r"Final QA Score: (\d+)%", output)
            qa_score = match.group(1) if match else "?"
            return (ticker, True, f"{qa_score}% QA", duration)
        elif "Error" in output or result.returncode != 0:
            error_msg = output.split("Error:")[-1][:100] if "Error:" in output else "Unknown error"
            return (ticker, False, error_msg.strip(), duration)
        else:
            return (ticker, True, "Completed", duration)

    except subprocess.TimeoutExpired:
        return (ticker, False, "Timeout (10 min)", time.time() - start_time)
    except Exception as e:
        return (ticker, False, str(e)[:100], time.time() - start_time)


def main():
    parser = argparse.ArgumentParser(description="Batch extraction for index companies")
    parser.add_argument("--phase", default="1",
                       help="Phase to extract: 1, 2, or all")
    parser.add_argument("--delay", type=int, default=15,
                       help="Delay between extractions in seconds (default: 15)")
    parser.add_argument("--threshold", type=int, default=80,
                       help="QA threshold percentage (default: 80)")
    parser.add_argument("--list", action="store_true",
                       help="Just list companies, don't extract")
    parser.add_argument("--ticker", type=str,
                       help="Extract a single ticker")
    parser.add_argument("--tickers", type=str,
                       help="Comma-separated list of tickers to extract (e.g., AAPL,VZ,T)")
    parser.add_argument("--skip-existing", action="store_true", default=True,
                       help="Skip companies that already have results (default: True)")
    parser.add_argument("--force", action="store_true",
                       help="Force re-extraction even if results exist (overrides --skip-existing)")
    args = parser.parse_args()

    # Get existing extractions
    existing = get_existing_tickers()

    # Build CIK lookup from all phases
    all_companies = PHASE_1 + PHASE_2
    cik_lookup = {t: c for t, c in all_companies}

    # Determine which companies to extract
    if args.tickers:
        # Multiple tickers mode (comma-separated)
        ticker_list = [t.strip().upper() for t in args.tickers.split(",")]
        companies = []
        for ticker in ticker_list:
            if ticker in cik_lookup:
                companies.append((ticker, cik_lookup[ticker]))
            else:
                print(f"Warning: {ticker} not found in index lists, skipping")
        if not companies:
            print("No valid tickers found")
            sys.exit(1)
    elif args.ticker:
        # Single ticker mode
        companies = [(t, c) for t, c in all_companies if t == args.ticker.upper()]
        if not companies:
            print(f"Ticker {args.ticker} not found in index lists")
            sys.exit(1)
    elif args.phase == "1":
        companies = PHASE_1
    elif args.phase == "2":
        companies = PHASE_2
    elif args.phase == "all":
        companies = PHASE_1 + PHASE_2
    else:
        print(f"Unknown phase: {args.phase}")
        print("Available: 1, 2, all")
        sys.exit(1)

    # Filter out existing if requested (unless --force is set)
    if args.skip_existing and not args.ticker and not args.tickers and not args.force:
        original_count = len(companies)
        companies = [(t, c) for t, c in companies if t not in existing]
        skipped = original_count - len(companies)
        if skipped > 0:
            print(f"Skipping {skipped} companies that already have results")
    elif args.force:
        print(f"Force mode: will re-extract even if results exist")

    # List mode
    if args.list:
        print(f"\n{'='*60}")
        print(f"INDEX COMPANIES - Phase {args.phase}")
        print(f"{'='*60}")
        print(f"Total: {len(companies)} companies")
        print(f"Already extracted: {len(existing)}")
        print()
        for ticker, cik in companies:
            status = "[EXISTS]" if ticker in existing else "[NEW]"
            print(f"  {status} {ticker}: {cik}")
        return

    # Extract mode
    print(f"\n{'='*60}")
    print(f"INDEX BATCH EXTRACTION - Phase {args.phase}")
    print(f"{'='*60}")
    print(f"Companies to extract: {len(companies)}")
    print(f"Delay between extractions: {args.delay}s")
    print(f"QA Threshold: {args.threshold}%")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    results = []
    total_duration = 0

    for i, (ticker, cik) in enumerate(companies):
        print(f"\n[{i+1}/{len(companies)}] Extracting {ticker} (CIK: {cik})")
        print("-" * 40)

        ticker, success, msg, duration = run_extraction(ticker, cik, args.threshold)
        total_duration += duration

        status = "[OK]" if success else "[FAIL]"
        print(f"{status} {ticker}: {msg} ({duration:.1f}s)")
        results.append((ticker, success, msg, duration))

        # Delay between extractions (except for last one)
        if i < len(companies) - 1:
            print(f"Waiting {args.delay}s...")
            time.sleep(args.delay)

    # Summary
    print(f"\n{'='*60}")
    print("BATCH EXTRACTION SUMMARY")
    print(f"{'='*60}")
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Total duration: {total_duration/60:.1f} minutes")
    print()

    success = [r for r in results if r[1]]
    failed = [r for r in results if not r[1]]

    print(f"Successful ({len(success)}/{len(results)}):")
    for ticker, _, msg, duration in success:
        print(f"  [OK] {ticker}: {msg} ({duration:.1f}s)")

    if failed:
        print(f"\nFailed ({len(failed)}):")
        for ticker, _, msg, duration in failed:
            print(f"  [FAIL] {ticker}: {msg}")

    # Estimate cost
    avg_cost = 0.02  # Approximate cost per extraction
    print(f"\nEstimated cost: ${len(success) * avg_cost:.2f}")


if __name__ == "__main__":
    main()
