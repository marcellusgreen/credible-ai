#!/usr/bin/env python3
"""Re-extract core data for MISSING_ALL companies to get outstanding amounts."""
import subprocess
import sys
import time

# MISSING_ALL companies sorted by total debt (highest first)
# FOX already done
COMPANIES = [
    # Biggest impact
    'C',      # $316B (Citigroup - bank, may not extract well)
    'MS',     # $332B (Morgan Stanley - bank)
    'ET',     # $63B (Energy Transfer)
    'USB',    # $63B (US Bancorp - bank)
    'COF',    # $38B (Capital One - bank)
    'PG',     # $37B (Procter & Gamble)
    'PLD',    # $35B (Prologis REIT)
    'UAL',    # $27B (United Airlines)
    # Smaller
    'CSGP',   # $1B (CoStar)
    'TEAM',   # $1B (Atlassian)
    'PANW',   # $0.5B (Palo Alto Networks)
    'TTD',    # $0.07B (Trade Desk)
    'CPRT',   # $0B (Copart)
]

def run_company(ticker):
    """Run core extraction for a single company."""
    print(f"\n{'='*60}")
    print(f"RE-EXTRACTING: {ticker}")
    print(f"{'='*60}")

    cmd = [
        sys.executable, "scripts/extract_iterative.py",
        "--ticker", ticker,
        "--step", "core",
        "--save-db"
    ]

    try:
        result = subprocess.run(
            cmd,
            timeout=300,  # 5 min max per company
            capture_output=True,
            text=True,
            cwd="."
        )

        # Print last 15 lines of output
        lines = result.stdout.strip().split('\n')
        for line in lines[-15:]:
            print(f"  {line}")

        if result.returncode != 0:
            # Print error info
            err_lines = result.stderr.strip().split('\n')
            for line in err_lines[-5:]:
                print(f"  [ERR] {line}")

        return result.returncode == 0

    except subprocess.TimeoutExpired:
        print(f"  [TIMEOUT] {ticker} took >5 minutes")
        return False
    except Exception as e:
        print(f"  [ERROR] {e}")
        return False


if __name__ == "__main__":
    successes = []
    failures = []

    for ticker in COMPANIES:
        ok = run_company(ticker)
        if ok:
            successes.append(ticker)
        else:
            failures.append(ticker)
        time.sleep(2)  # Brief pause between companies

    print(f"\n{'='*60}")
    print(f"BATCH COMPLETE")
    print(f"{'='*60}")
    print(f"Success: {len(successes)} - {', '.join(successes)}")
    print(f"Failed:  {len(failures)} - {', '.join(failures)}")
