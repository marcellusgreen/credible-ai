#!/usr/bin/env python3
"""
Batch extract guarantees and collateral for secured debt instruments missing data.
"""

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

# Companies with secured debt missing guarantee data
COMPANIES_MISSING_GUARANTEES = [
    "ATUS", "BAC", "CHTR", "CZR", "DAL", "FYBR", "GILD", "GS", "INTU", "JPM",
    "LUMN", "MSTR", "NCLH", "NEE", "ON", "PARA", "SCHW", "SPG", "TSLA", "TTD",
    "UNP", "VAL", "VNO", "WFC", "WYNN", "XEL"
]

# Companies with secured debt missing collateral data
COMPANIES_MISSING_COLLATERAL = [
    "ATUS", "AXP", "BAC", "CB", "CRWD", "DAL", "EXC", "F", "GE", "GILD", "GS",
    "HCA", "JPM", "NE", "NEE", "ON", "PARA", "SCHW", "TMUS", "VAL", "WFC", "WYNN", "XEL"
]


def run_extraction(script: str, ticker: str) -> tuple[str, bool, str]:
    """Run extraction script for a ticker."""
    try:
        result = subprocess.run(
            [sys.executable, f"scripts/{script}", "--ticker", ticker],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
            cwd=Path(__file__).parent.parent
        )
        success = result.returncode == 0
        output = result.stdout + result.stderr
        return ticker, success, output
    except subprocess.TimeoutExpired:
        return ticker, False, "Timeout"
    except Exception as e:
        return ticker, False, str(e)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=["guarantees", "collateral", "both"], default="both")
    parser.add_argument("--limit", type=int, help="Limit number of companies")
    args = parser.parse_args()

    if args.type in ["guarantees", "both"]:
        companies = COMPANIES_MISSING_GUARANTEES[:args.limit] if args.limit else COMPANIES_MISSING_GUARANTEES
        print(f"\n{'='*60}")
        print(f"EXTRACTING GUARANTEES FOR {len(companies)} COMPANIES")
        print(f"{'='*60}\n")

        success_count = 0
        for i, ticker in enumerate(companies, 1):
            print(f"[{i}/{len(companies)}] Processing {ticker}...")
            ticker, success, output = run_extraction("extract_guarantees.py", ticker)
            if success:
                success_count += 1
                # Extract summary from output
                if "New guarantees:" in output:
                    for line in output.split("\n"):
                        if "New guarantees:" in line or "Existing guarantees:" in line:
                            print(f"  {line.strip()}")
            else:
                print(f"  FAILED: {output[:200]}")
            print()

        print(f"\nGuarantee extraction complete: {success_count}/{len(companies)} succeeded")

    if args.type in ["collateral", "both"]:
        companies = COMPANIES_MISSING_COLLATERAL[:args.limit] if args.limit else COMPANIES_MISSING_COLLATERAL
        print(f"\n{'='*60}")
        print(f"EXTRACTING COLLATERAL FOR {len(companies)} COMPANIES")
        print(f"{'='*60}\n")

        success_count = 0
        for i, ticker in enumerate(companies, 1):
            print(f"[{i}/{len(companies)}] Processing {ticker}...")
            ticker, success, output = run_extraction("extract_collateral.py", ticker)
            if success:
                success_count += 1
                # Extract summary from output
                if "New collateral records:" in output or "collateral" in output.lower():
                    for line in output.split("\n"):
                        if "collateral" in line.lower() and ("New" in line or "Existing" in line or "Created" in line):
                            print(f"  {line.strip()}")
            else:
                print(f"  FAILED: {output[:200]}")
            print()

        print(f"\nCollateral extraction complete: {success_count}/{len(companies)} succeeded")


if __name__ == "__main__":
    main()
