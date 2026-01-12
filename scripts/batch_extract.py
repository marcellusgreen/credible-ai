#!/usr/bin/env python3
"""
Batch extraction script for multiple companies.

Usage:
    python scripts/batch_extract.py
    python scripts/batch_extract.py --batch telecom
    python scripts/batch_extract.py --batch all
"""

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

# Company batches with CIKs
BATCHES = {
    "telecom": [
        ("CHTR", "0001271561"),   # Charter Communications
        ("LUMN", "0000018926"),   # Lumen Technologies
        ("DISH", "0001001082"),   # DISH Network
        ("FYBR", "0001349436"),   # Frontier Communications
    ],
    "offshore": [
        ("VAL", "0001660134"),    # Valaris
        ("DO", "0000949039"),     # Diamond Offshore
        ("NE", "0001603923"),     # Noble Corporation
    ],
    "airlines": [
        ("AAL", "0000006201"),    # American Airlines
        ("UAL", "0000100517"),    # United Airlines
        ("DAL", "0000027904"),    # Delta Air Lines
    ],
    "gaming": [
        ("CZR", "0001590895"),    # Caesars Entertainment
        ("MGM", "0000789570"),    # MGM Resorts
        ("WYNN", "0001174922"),   # Wynn Resorts
    ],
    "retail": [
        ("M", "0000794367"),      # Macy's
        ("KSS", "0000885639"),    # Kohl's
        ("BBWI", "0001579298"),   # Bath & Body Works
    ],
    "healthcare": [
        ("HCA", "0000860730"),    # HCA Healthcare
        ("THC", "0000070318"),    # Tenet Healthcare
        ("CHS", "0001108109"),    # Community Health Systems
    ],
    "energy": [
        ("OXY", "0000797468"),    # Occidental Petroleum
        ("DVN", "0001090012"),    # Devon Energy
        ("APA", "0000006769"),    # APA Corporation
        ("SWN", "0000007332"),    # Southwestern Energy
    ],
    "media": [
        ("PARA", "0000813828"),   # Paramount Global
        ("WBD", "0001437107"),    # Warner Bros Discovery
        ("FOX", "0001754301"),    # Fox Corporation
    ],
    "autos": [
        ("F", "0000037996"),      # Ford
        ("GM", "0001467858"),     # General Motors
    ],
    "tech": [
        ("GOOGL", "0001652044"),  # Alphabet
        ("AMZN", "0001018724"),   # Amazon
        ("META", "0001326801"),   # Meta
    ],
    "banks": [
        ("JPM", "0000019617"),    # JPMorgan Chase
        ("GS", "0000886982"),     # Goldman Sachs
        ("BAC", "0000070858"),    # Bank of America
    ],
    "industrials": [
        ("GE", "0000040545"),     # GE Aerospace
        ("BA", "0000012927"),     # Boeing
        ("CAT", "0000018230"),    # Caterpillar
    ],
    "consumer": [
        ("KHC", "0001637459"),    # Kraft Heinz
        ("KDP", "0001418135"),    # Keurig Dr Pepper
        ("HSY", "0000047111"),    # Hershey
    ],
    "reits": [
        ("SPG", "0001063761"),    # Simon Property Group
        ("VNO", "0000899629"),    # Vornado Realty
        ("SLG", "0001040971"),    # SL Green Realty
    ],
    "cruises": [
        ("CCL", "0000815097"),    # Carnival
        ("RCL", "0000884887"),    # Royal Caribbean
        ("NCLH", "0001513761"),   # Norwegian Cruise Line
    ],
}

def run_extraction(ticker: str, cik: str) -> tuple[str, bool, str]:
    """Run extraction for a single company."""
    print(f"\n{'='*60}")
    print(f"Extracting {ticker} (CIK: {cik})")
    print(f"{'='*60}")

    try:
        result = subprocess.run(
            [
                sys.executable,
                "scripts/extract_iterative.py",
                "--ticker", ticker,
                "--cik", cik,
                "--save-db"
            ],
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
            cwd=Path(__file__).parent.parent
        )

        # Check for success indicators in output
        output = result.stdout + result.stderr

        if "EXTRACTION COMPLETE" in output:
            # Extract QA score
            import re
            match = re.search(r"Final QA Score: (\d+)%", output)
            qa_score = match.group(1) if match else "?"
            print(f"[OK] {ticker}: {qa_score}% QA")
            return (ticker, True, f"{qa_score}% QA")
        elif "Error" in output or result.returncode != 0:
            error_msg = output.split("Error:")[-1][:100] if "Error:" in output else "Unknown error"
            print(f"[FAIL] {ticker}: {error_msg}")
            return (ticker, False, error_msg.strip())
        else:
            print(f"[WARN] {ticker}: Completed with warnings")
            return (ticker, True, "Completed with warnings")

    except subprocess.TimeoutExpired:
        print(f"[FAIL] {ticker}: Timeout after 10 minutes")
        return (ticker, False, "Timeout")
    except Exception as e:
        print(f"[FAIL] {ticker}: {str(e)}")
        return (ticker, False, str(e))


def main():
    parser = argparse.ArgumentParser(description="Batch extraction")
    parser.add_argument("--batch", default="all",
                       help=f"Batch to extract: {', '.join(BATCHES.keys())}, or 'all'")
    parser.add_argument("--delay", type=int, default=30,
                       help="Delay between extractions in seconds (default: 30)")
    args = parser.parse_args()

    # Determine which batches to run
    if args.batch == "all":
        batches_to_run = list(BATCHES.keys())
    elif args.batch in BATCHES:
        batches_to_run = [args.batch]
    else:
        print(f"Unknown batch: {args.batch}")
        print(f"Available: {', '.join(BATCHES.keys())}, all")
        sys.exit(1)

    # Collect all companies
    companies = []
    for batch_name in batches_to_run:
        companies.extend(BATCHES[batch_name])

    print(f"\n{'='*60}")
    print(f"BATCH EXTRACTION: {len(companies)} companies")
    print(f"{'='*60}")
    print(f"Batches: {', '.join(batches_to_run)}")
    print(f"Delay between extractions: {args.delay}s")
    print()

    results = []
    for i, (ticker, cik) in enumerate(companies):
        print(f"\n[{i+1}/{len(companies)}] ", end="")
        result = run_extraction(ticker, cik)
        results.append(result)

        # Delay between extractions (except for last one)
        if i < len(companies) - 1:
            print(f"Waiting {args.delay}s before next extraction...")
            import time
            time.sleep(args.delay)

    # Summary
    print(f"\n{'='*60}")
    print("BATCH EXTRACTION SUMMARY")
    print(f"{'='*60}")

    success = [r for r in results if r[1]]
    failed = [r for r in results if not r[1]]

    print(f"\nSuccessful ({len(success)}):")
    for ticker, _, msg in success:
        print(f"  [OK] {ticker}: {msg}")

    if failed:
        print(f"\nFailed ({len(failed)}):")
        for ticker, _, msg in failed:
            print(f"  [FAIL] {ticker}: {msg}")

    print(f"\nTotal: {len(success)}/{len(companies)} succeeded")


if __name__ == "__main__":
    main()
