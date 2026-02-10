#!/usr/bin/env python3
"""
Full sequential pipeline to discover and price senior secured bonds.
Runs Phase 4 (Finnhub discovery) for all target companies one at a time,
then Phase 3 (SEC filing ISINs), Phase 2 (CUSIP->ISIN), and Phase 1 (pricing).
"""
import subprocess
import sys
import time
from datetime import datetime


def run_phase(args, description):
    sep = "=" * 70
    now = datetime.now().strftime("%H:%M:%S")
    print("")
    print(sep)
    print(now + " - " + description)
    print(sep, flush=True)

    cmd = [sys.executable, "scripts/expand_bond_pricing.py"] + args
    result = subprocess.run(cmd, capture_output=False, text=True)

    if result.returncode != 0:
        print("  WARNING: Exit code " + str(result.returncode) + " for " + description)
    else:
        print("  DONE: " + description)

    # Brief pause between runs to let Neon DB connections settle
    time.sleep(3)
    return result.returncode


def main():
    start = datetime.now()
    print("Pipeline started at " + start.strftime("%H:%M:%S"))
    print("=" * 70)

    # Phase 4: Discover bonds from Finnhub by subsidiary issuer codes
    # Companies ordered by number of missing secured bonds (descending)
    # HCA already completed separately
    phase4_companies = [
        ("BHC", 10),
        ("THC", 7),
        ("LUMN", 7),
        ("EXC", 7),
        ("NRG", 7),
        ("HTZ", 7),
        ("CHTR", 6),
        ("CLF", 5),
        ("MGM", 5),
        ("CVNA", 5),
        ("CAR", 4),
        ("AMC", 4),
        ("FYBR", 4),
        ("DISH", 4),
        ("RIG", 4),
        ("DAL", 4),
        ("WYNN", 3),
        ("UAL", 3),
        ("SLG", 3),
        ("DO", 2),
        ("VAL", 2),
        ("NCLH", 2),
        ("IHRT", 2),
        ("CNK", 2),
        ("FUN", 2),
        ("NE", 2),
    ]

    total_companies = len(phase4_companies)
    for i, (ticker, missing) in enumerate(phase4_companies):
        num = str(i + 1) + "/" + str(total_companies)
        desc = "Phase 4 [" + num + "]: " + ticker + " (" + str(missing) + " missing secured bonds)"
        run_phase(["--phase4", "--ticker", ticker], desc)

    # Phase 3: Discover ISINs from SEC filings (especially foreign issuers)
    run_phase(["--phase3"], "Phase 3: Discover ISINs from SEC filings (all companies)")

    # Phase 2: Derive ISINs from CUSIPs
    run_phase(["--phase2"], "Phase 2: Derive ISINs from CUSIPs")

    # Phase 1: Price all instruments with ISINs
    run_phase(["--phase1"], "Phase 1: Price all instruments with ISINs")

    end = datetime.now()
    elapsed = end - start
    print("")
    print("=" * 70)
    print("PIPELINE COMPLETE")
    print("Started:  " + start.strftime("%H:%M:%S"))
    print("Finished: " + end.strftime("%H:%M:%S"))
    print("Elapsed:  " + str(elapsed))
    print("=" * 70)


if __name__ == "__main__":
    main()
