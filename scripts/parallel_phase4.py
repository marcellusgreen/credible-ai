#!/usr/bin/env python
"""Run Phase 4 bond discovery in parallel with controlled concurrency."""

import asyncio
import subprocess
import sys
from datetime import datetime

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)

# Companies to process (excluding already completed: AAPL, DAL, DO, NE, NEM, TXN)
COMPANIES = [
    'NXPI', 'INTU',
    'APA', 'CEG', 'ON', 'ROP', 'GEHC', 'PCAR', 'ADSK', 'AMAT',
    'KLAC', 'CDNS', 'SNPS', 'REGN', 'DXCM', 'ABNB', 'CRWD', 'NOW',
    'WDAY', 'PANW', 'FTNT', 'ZS', 'EA', 'TTWO', 'DASH', 'ORLY',
    'TJX', 'CTAS', 'PAYX', 'AXON', 'APP', 'GEV', 'MSTR', 'CSGP',
    'CNK', 'FUN', 'CRWV'
]

# Max concurrent workers (Finnhub rate limit is ~55/min, each company does 288 calls over ~5 min)
MAX_WORKERS = 3

async def run_phase4(ticker: str, semaphore: asyncio.Semaphore) -> dict:
    """Run Phase 4 for a single company."""
    async with semaphore:
        start = datetime.now()
        print(f"[{start.strftime('%H:%M:%S')}] Starting {ticker}...")

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            'scripts/expand_bond_pricing.py',
            '--phase4',
            '--ticker', ticker,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await proc.communicate()
        elapsed = (datetime.now() - start).total_seconds()

        # Parse output for summary (handle encoding issues)
        try:
            output = stdout.decode('utf-8', errors='replace')
        except:
            output = ""
        new_bonds = 0
        discovered = 0

        for line in output.split('\n'):
            if 'New bonds added:' in line:
                try:
                    new_bonds = int(line.split(':')[1].strip())
                except:
                    pass
            if 'Bonds discovered:' in line:
                try:
                    discovered = int(line.split(':')[1].strip())
                except:
                    pass

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Finished {ticker}: {discovered} discovered, {new_bonds} new ({elapsed:.0f}s)")

        return {
            'ticker': ticker,
            'discovered': discovered,
            'new_bonds': new_bonds,
            'elapsed': elapsed,
            'success': proc.returncode == 0
        }

async def main():
    print(f"=" * 70)
    print(f"PARALLEL PHASE 4: {len(COMPANIES)} companies, {MAX_WORKERS} workers")
    print(f"=" * 70)

    semaphore = asyncio.Semaphore(MAX_WORKERS)

    # Run all companies with controlled concurrency
    tasks = [run_phase4(ticker, semaphore) for ticker in COMPANIES]
    results = await asyncio.gather(*tasks)

    # Summary
    print(f"\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)

    total_discovered = sum(r['discovered'] for r in results)
    total_new = sum(r['new_bonds'] for r in results)
    successful = sum(1 for r in results if r['success'])

    print(f"Companies processed: {successful}/{len(COMPANIES)}")
    print(f"Total bonds discovered: {total_discovered}")
    print(f"New bonds added: {total_new}")

    # List companies with new bonds
    new_bond_companies = [r for r in results if r['new_bonds'] > 0]
    if new_bond_companies:
        print(f"\nCompanies with new bonds:")
        for r in new_bond_companies:
            print(f"  {r['ticker']}: {r['new_bonds']} new bonds")

if __name__ == '__main__':
    asyncio.run(main())
