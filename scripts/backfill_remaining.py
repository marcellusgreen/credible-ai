#!/usr/bin/env python3
"""Backfill document sections for remaining companies."""

import subprocess
import sys

COMPANIES = ['APA', 'CCL', 'CHS', 'CHTR', 'CZR', 'DAL', 'DISH', 'DO', 'DVN',
             'F', 'FOX', 'FYBR', 'GE', 'GM', 'GOOGL', 'GS', 'HCA', 'JPM', 'KHC',
             'KSS', 'LUMN', 'M', 'META', 'MSFT', 'NE', 'NVDA', 'OXY', 'PARA',
             'SPG', 'SWN', 'UAL', 'VAL', 'WBD']

def main():
    results = {'success': [], 'error': []}

    for i, ticker in enumerate(COMPANIES):
        print(f"\n{'='*60}")
        print(f"[{i+1}/{len(COMPANIES)}] Processing {ticker}")
        print('='*60)

        try:
            result = subprocess.run(
                [sys.executable, 'scripts/backfill_document_sections.py', '--ticker', ticker],
                capture_output=False,
                timeout=300  # 5 minutes per company
            )
            if result.returncode == 0:
                results['success'].append(ticker)
            else:
                results['error'].append(ticker)
        except subprocess.TimeoutExpired:
            print(f"  TIMEOUT for {ticker}")
            results['error'].append(ticker)
        except Exception as e:
            print(f"  ERROR for {ticker}: {e}")
            results['error'].append(ticker)

    print(f"\n{'='*60}")
    print("FINAL SUMMARY")
    print('='*60)
    print(f"Success: {len(results['success'])}")
    print(f"Errors: {len(results['error'])}")
    if results['error']:
        print(f"Failed companies: {results['error']}")

if __name__ == "__main__":
    main()
