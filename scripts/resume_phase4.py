#!/usr/bin/env python3
"""
Resume Phase 4 bond discovery for companies not yet processed.
Runs expand_bond_pricing.py --phase4 --ticker for each remaining company.
"""

import asyncio
import subprocess
import sys
import os
from datetime import datetime

# Companies already processed by Phase 4 (got new ISINs after Feb 3 2026)
ALREADY_DONE = {
    'ABBV','ACN','ADBE','ADI','AMD','AMGN','APH','AVGO','BA','BIIB','BKNG','BLK',
    'CAT','CB','CDW','COF','COP','CRM','CSCO','CSX','DHR','DVN','EXC','F','FOX',
    'GILD','GM','GOOGL','HCA','HD','HON','IBM','INTC','JNJ','KHC','KO','KSS',
    'LMT','LRCX','M','MA','MCD','MCHP','MDT','MGM','MRK','MU','NRG','NVDA',
    'OXY','PEP','PFE','PG','PGR','PH','PM','PYPL','QCOM','ROST','RTX','SCHW',
    'SWN','TMO','TMUS',
    # Also skip AAL - it was processed at start of aborted second run
    'AAL',
}

# All companies to process (from KNOWN_ISSUER_CODES + companies with existing CUSIPs)
ALL_TARGETS = [
    # From KNOWN_ISSUER_CODES
    'ABT','AEP','AMC','AMZN','ATUS','AXP','BAC','BHC','BKR','BSX','BX','C',
    'CAR','CCL','CHTR','CLF','COST','CVNA','CZR','DE','DIS','DISH','FANG',
    'FYBR','GE','GS','HSY','HTZ','IHRT','JPM','KDP','LIN','LLY','LOW','LUMN',
    'LYV','MAR','MDLZ','META','MRVL','MS','MSFT','NCLH','NEE','NFLX','NKE',
    'ORCL','PARA','PLD','RIG','SBUX','SLG','SPG','SPGI','SYK','T','TGT','THC',
    'TSLA','UAL','UNH','UNP','UPS','USB','V','VAL','VNO','VZ','WFC','WMT',
    'WYNN','X','XEL','XOM',
    # Companies with existing CUSIPs not in lookup table
    'AMAT','CRWD','CVX','DAL','DO','EA','ETN','GEHC','PAYX','RCL','REGN',
    'TTWO','TXN','UBER','VRSK','WBD','WDAY','WELL',
]

def main():
    remaining = sorted(set(ALL_TARGETS) - ALREADY_DONE)
    print(f"Remaining companies to process: {len(remaining)}")
    print(f"Estimated time: ~{len(remaining) * 5} minutes ({len(remaining)} companies x ~5 min each)")
    print(f"Companies: {', '.join(remaining)}")
    print(f"Started at: {datetime.now()}")
    print("=" * 70)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(script_dir, "expand_bond_pricing.py")

    stats = {"processed": 0, "failed": 0, "skipped": 0}

    for idx, ticker in enumerate(remaining):
        print(f"\n[{idx+1}/{len(remaining)}] Processing {ticker}...", flush=True)
        try:
            result = subprocess.run(
                [sys.executable, script_path, "--phase4", "--ticker", ticker],
                capture_output=True,
                text=True,
                timeout=600,  # 10 min timeout per company
                cwd=os.path.dirname(script_dir),
            )
            # Print stdout (contains discovery results)
            if result.stdout:
                for line in result.stdout.strip().split('\n'):
                    if line.strip():
                        print(f"  {line.strip()}", flush=True)
            if result.returncode != 0 and result.stderr:
                print(f"  ERROR: {result.stderr[:200]}", flush=True)
                stats["failed"] += 1
            else:
                stats["processed"] += 1
        except subprocess.TimeoutExpired:
            print(f"  TIMEOUT after 10 minutes", flush=True)
            stats["failed"] += 1
        except Exception as e:
            print(f"  ERROR: {e}", flush=True)
            stats["failed"] += 1

    print("\n" + "=" * 70)
    print(f"RESUME COMPLETE at {datetime.now()}")
    print(f"Processed: {stats['processed']}")
    print(f"Failed: {stats['failed']}")
    print("=" * 70)

if __name__ == "__main__":
    main()
