#!/usr/bin/env python3
"""Analyze why cache amounts aren't matching to DB instruments."""
import json
import re
from pathlib import Path

from sqlalchemy import text
from script_utils import get_db_session, print_header, run_async


def extract_rate_year(name):
    """Extract rate and maturity year from name string."""
    rate_match = re.search(r'(\d+\.?\d*)\s*%', name or '')
    year_match = re.search(r'(20\d{2})', name or '')
    return (rate_match.group(1) if rate_match else None,
            year_match.group(1) if year_match else None)


async def main():
    print_header("CACHE-TO-DB MATCHING GAP ANALYSIS")

    tickers_to_check = ['HD', 'DUK', 'UNH', 'CMCSA', 'LOW', 'PEP', 'ORCL']

    async with get_db_session() as session:
        for ticker in tickers_to_check:
            cache_path = Path(f"results/{ticker.lower()}_iterative.json")
            if not cache_path.exists():
                continue

            with open(cache_path) as f:
                data = json.load(f)
            cache_instruments = data.get("debt_instruments", [])

            # Build cache rate+year lookup
            cache_by_ry = {}
            for inst in cache_instruments:
                name = inst.get("name", "")
                amount = None
                for field in ["outstanding", "principal", "outstanding_amount",
                              "principal_amount", "outstanding_amount_cents",
                              "face_value_cents", "principal_amount_cents"]:
                    val = inst.get(field)
                    if val is not None and val > 0:
                        amount = int(val)
                        break
                if amount:
                    rate, year = extract_rate_year(name)
                    if rate and year:
                        # Normalize rate: "2.70" -> "2.7", "5.125" -> "5.125"
                        rate_norm = str(float(rate))
                        cache_by_ry[(rate_norm, year)] = (name, amount)

            # Get DB instruments missing amounts
            result = await session.execute(text("""
                SELECT di.name, di.interest_rate, di.maturity_date, di.outstanding,
                       COALESCE(di.attributes->>'source', 'sec') as source
                FROM debt_instruments di
                JOIN companies c ON c.id = di.company_id
                WHERE c.ticker = :ticker
                  AND di.is_active = true
                  AND (di.outstanding IS NULL OR di.outstanding = 0)
                ORDER BY di.name
            """), {"ticker": ticker})
            db_missing = result.fetchall()

            if not db_missing:
                continue

            print(f"\n{'='*70}")
            print(f"{ticker}: {len(db_missing)} instruments missing amounts, {len(cache_by_ry)} cache rate+year entries")
            print(f"{'='*70}")

            matched = 0
            for row in db_missing:
                name, interest_rate, maturity_date, outstanding, source = row

                # Try DB fields for rate+year
                db_rate = None
                db_year = None
                if interest_rate:
                    db_rate = str(interest_rate / 100.0)
                    # Normalize: "2.7" not "2.7000000000000002"
                    db_rate = str(round(float(db_rate), 4))
                    # Remove trailing zeros: "2.7000" -> "2.7"
                    if '.' in db_rate:
                        db_rate = db_rate.rstrip('0').rstrip('.')
                    db_rate = str(float(db_rate))
                if maturity_date:
                    db_year = str(maturity_date.year)

                # Also try from name
                name_rate, name_year = extract_rate_year(name)
                if name_rate:
                    name_rate = str(float(name_rate))

                rate_to_try = db_rate or name_rate
                year_to_try = db_year or name_year

                cache_match = cache_by_ry.get((rate_to_try, year_to_try)) if rate_to_try and year_to_try else None

                status = "MATCH" if cache_match else "NO MATCH"
                if cache_match:
                    matched += 1

                display_name = (name or "???")[:45]
                rate_disp = f"r={rate_to_try}" if rate_to_try else "r=?"
                year_disp = f"y={year_to_try}" if year_to_try else "y=?"
                src = "FH" if source == "finnhub_discovery" else "SEC"
                cache_info = f"-> {cache_match[0][:30]}=${cache_match[1]/1e11:.2f}B" if cache_match else ""
                print(f"  [{src}] {display_name:45s} {rate_disp:12s} {year_disp:7s} {status:8s} {cache_info}")

            print(f"  --- {matched}/{len(db_missing)} matchable ---")


if __name__ == "__main__":
    run_async(main())
