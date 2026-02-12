#!/usr/bin/env python3
"""Deeper analysis of missing outstanding - what's actionable."""
import json
import re
from pathlib import Path
from collections import defaultdict

from sqlalchemy import text
from script_utils import get_db_session, print_header, print_subheader, run_async


def normalize_for_matching(name: str) -> str:
    """Normalize instrument name for fuzzy matching."""
    s = name.lower().strip()
    s = re.sub(r'[%\-\u2013\u2014,()]', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    s = s.replace('senior unsecured ', '').replace('senior secured ', '')
    s = s.replace(' notes ', ' ').replace(' note ', ' ')
    s = s.replace(' due ', ' ')
    return s.strip()


def extract_rate_year(name: str):
    """Extract rate and year from instrument name."""
    rate_match = re.search(r'(\d+\.?\d*)\s*%', name or '')
    year_match = re.search(r'(20\d{2})', name or '')
    rate = rate_match.group(1) if rate_match else None
    year = year_match.group(1) if year_match else None
    return rate, year


async def main():
    print_header("MISSING OUTSTANDING - ACTIONABLE ANALYSIS")

    async with get_db_session() as session:
        # Get all active instruments missing amounts
        result = await session.execute(text("""
            SELECT di.id, c.ticker, di.name, di.interest_rate, di.maturity_date,
                   di.cusip, di.isin, di.outstanding,
                   COALESCE(di.attributes->>'source', 'sec') as source
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            WHERE di.is_active = true
              AND (di.outstanding IS NULL OR di.outstanding = 0)
            ORDER BY c.ticker, di.name
        """))
        missing_instruments = result.fetchall()

        # Get all cache files and extract amounts
        cache_by_ticker = {}
        results_dir = Path("results")
        for cache_file in results_dir.glob("*_iterative.json"):
            ticker = cache_file.stem.replace("_iterative", "").upper()
            with open(cache_file) as f:
                data = json.load(f)
            instruments = data.get("debt_instruments", [])
            amounts = {}
            for inst in instruments:
                name = inst.get("name") or inst.get("instrument_name") or ""
                if not name:
                    continue
                # Try all known field name variants
                amount = None
                for field in ["outstanding", "principal", "outstanding_amount",
                              "principal_amount", "outstanding_amount_cents",
                              "face_value_cents", "original_amount_cents",
                              "outstanding_principal", "outstanding_principal_amount_cents",
                              "principal_amount_cents", "principal_amount_outstanding",
                              "principal_amount_initial", "face_amount", "drawn_amount"]:
                    val = inst.get(field)
                    if val is not None and val > 0:
                        amount = int(val)
                        break
                if amount:
                    amounts[name] = amount
                    rate, year = extract_rate_year(name)
                    if rate and year:
                        amounts[f"_rateyr_{rate}_{year}"] = amount
            if amounts:
                cache_by_ticker[ticker] = amounts

        # Try to match missing instruments to cache by rate+year
        matched_by_cache = 0
        unmatched = 0
        matched_tickers = defaultdict(lambda: {"matched": 0, "unmatched": 0, "amount": 0})

        for row in missing_instruments:
            di_id, ticker, name, interest_rate, maturity_date, cusip, isin, outstanding, source = row
            cache = cache_by_ticker.get(ticker, {})

            # Try exact name match
            found = False
            if name and name in cache:
                matched_by_cache += 1
                matched_tickers[ticker]["matched"] += 1
                matched_tickers[ticker]["amount"] += cache[name]
                found = True
                continue

            # Try rate+year match
            if not found:
                rate, year = extract_rate_year(name or "")
                # Also try from DB fields
                if not rate and interest_rate:
                    rate = f"{interest_rate / 100:.2f}".rstrip("0").rstrip(".")
                if not year and maturity_date:
                    year = str(maturity_date.year)

                if rate and year:
                    cache_key = f"_rateyr_{rate}_{year}"
                    if cache_key in cache:
                        matched_by_cache += 1
                        matched_tickers[ticker]["matched"] += 1
                        matched_tickers[ticker]["amount"] += cache[cache_key]
                        found = True
                        continue

            if not found:
                unmatched += 1
                matched_tickers[ticker]["unmatched"] += 1

        print(f"\nTotal missing instruments: {len(missing_instruments)}")
        print(f"Matchable to cache (by name or rate+year): {matched_by_cache}")
        print(f"Unmatched (no cache data): {unmatched}")
        total_matched_amount = sum(t["amount"] for t in matched_tickers.values())
        print(f"Estimated amount from cache matches: ${total_matched_amount / 1e11:.2f}B")

        print_subheader("BY TICKER - CACHE MATCHES")
        sorted_tickers = sorted(matched_tickers.items(),
                                key=lambda x: x[1]["amount"], reverse=True)
        for ticker, stats in sorted_tickers[:30]:
            if stats["matched"] > 0:
                print(f"  {ticker:6s}: {stats['matched']:3d} matched, {stats['unmatched']:3d} unmatched, ${stats['amount'] / 1e11:.2f}B")

        # Categorize unmatched by source
        fh_unmatched = 0
        sec_unmatched = 0
        sec_null_revolvers = 0
        for row in missing_instruments:
            di_id, ticker, name, interest_rate, maturity_date, cusip, isin, outstanding, source = row
            cache = cache_by_ticker.get(ticker, {})
            rate, year = extract_rate_year(name or "")
            if not rate and interest_rate:
                rate = f"{interest_rate / 100:.2f}".rstrip("0").rstrip(".")
            if not year and maturity_date:
                year = str(maturity_date.year)

            found = (name and name in cache) or (rate and year and f"_rateyr_{rate}_{year}" in cache)
            if not found:
                if source == "finnhub_discovery":
                    fh_unmatched += 1
                else:
                    sec_unmatched += 1
                    if "revolver" in (name or "").lower() or "revolving" in (name or "").lower():
                        sec_null_revolvers += 1

        print(f"\n  Unmatched breakdown:")
        print(f"    Finnhub (no SEC match): {fh_unmatched}")
        print(f"    SEC (no cache amount):  {sec_unmatched}")
        print(f"      of which revolvers:   {sec_null_revolvers}")

        # Phase 2 candidates: companies with cache but no amounts in cache
        print_subheader("PHASE 2 RE-EXTRACTION CANDIDATES")
        print("Companies with missing amounts that have NO usable cache data:")
        result = await session.execute(text("""
            WITH latest_financials AS (
                SELECT DISTINCT ON (company_id)
                    company_id, total_debt
                FROM company_financials
                WHERE total_debt IS NOT NULL AND total_debt > 0
                ORDER BY company_id, fiscal_year DESC, fiscal_quarter DESC
            ),
            missing_stats AS (
                SELECT di.company_id, c.ticker,
                       COUNT(*) as missing_count,
                       SUM(COALESCE(di.outstanding, 0)) as current_sum
                FROM debt_instruments di
                JOIN companies c ON c.id = di.company_id
                WHERE di.is_active = true
                GROUP BY di.company_id, c.ticker
            )
            SELECT ms.ticker, ms.missing_count,
                   ms.current_sum, lf.total_debt,
                   ROUND(ms.current_sum::numeric / NULLIF(lf.total_debt, 0) * 100, 1) as coverage_pct
            FROM missing_stats ms
            JOIN latest_financials lf ON lf.company_id = ms.company_id
            WHERE ms.current_sum < lf.total_debt * 0.5
            ORDER BY (lf.total_debt - ms.current_sum) DESC
            LIMIT 25
        """))
        print(f"  {'Ticker':6s} {'Instruments':>5s} {'Current':>10s} {'TotalDebt':>10s} {'Coverage':>8s}")
        print(f"  {'-'*6} {'-'*5} {'-'*10} {'-'*10} {'-'*8}")
        for r in result.fetchall():
            tick, count, current, total_debt, coverage = r
            print(f"  {tick:6s} {count:5d} ${float(current)/1e11:8.2f}B ${float(total_debt)/1e11:8.2f}B {float(coverage):>7.1f}%")


if __name__ == "__main__":
    run_async(main())
