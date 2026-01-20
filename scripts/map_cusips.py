"""
Map debt instruments to CUSIPs.

Strategies:
1. Extract CUSIP from ISIN (for US securities: remove "US" prefix and check digit)
2. Match via OpenFIGI using issuer ticker + coupon + maturity

Usage:
    python scripts/map_cusips.py --ticker AAPL          # Single company
    python scripts/map_cusips.py --all                  # All unmapped bonds
    python scripts/map_cusips.py --ticker AAPL --dry-run # Preview only
"""

import argparse
import asyncio
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

load_dotenv()


async def get_session():
    """Create database session."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL not set")

    engine = create_async_engine(database_url)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return async_session(), engine


async def map_single_company(ticker: str, dry_run: bool = False):
    """Map CUSIPs for a single company."""
    from app.services.cusip_mapping import map_company_bonds

    print(f"\n{'='*60}")
    print(f"Mapping CUSIPs for {ticker}")
    print(f"{'='*60}")

    session, engine = await get_session()
    try:
        results = await map_company_bonds(session, ticker, dry_run=dry_run)

        if not results:
            print(f"No tradeable bonds found for {ticker}")
            return

        print(f"\nResults:")
        print("-" * 60)

        success_count = 0
        figi_count = 0
        for result in results:
            if result.success:
                success_count += 1
                print(f"  [OK] {result.bond_name[:50]}")
                print(f"       CUSIP: {result.cusip}")
                print(f"       Method: {result.method}")
            elif result.figi:
                figi_count += 1
                print(f"  [FIGI] {result.bond_name[:50]}")
                print(f"       FIGI: {result.figi}")
                print(f"       Score: {result.match_score:.1f}%")
                print(f"       Note: {result.error}")
            else:
                print(f"  [X] {result.bond_name[:50]}")
                print(f"       Error: {result.error}")

        print("-" * 60)
        print(f"Total: {len(results)} bonds")
        print(f"  CUSIPs found: {success_count}")
        print(f"  FIGIs matched (no CUSIP): {figi_count}")
        print(f"  Failed: {len(results) - success_count - figi_count}")

        if dry_run:
            print("\n[DRY RUN] No changes saved to database")
        elif success_count > 0:
            print(f"\nSaved {success_count} CUSIPs to database")

    finally:
        await session.close()
        await engine.dispose()


async def map_all_unmapped(dry_run: bool = False, limit: int = 100):
    """Map CUSIPs for all unmapped bonds."""
    from app.services.cusip_mapping import get_unmapped_bonds, map_bond_to_cusip
    from sqlalchemy import update
    from app.models import DebtInstrument

    print(f"\n{'='*60}")
    print(f"Mapping CUSIPs for all unmapped bonds")
    print(f"Limit: {limit} bonds")
    print(f"{'='*60}")

    session, engine = await get_session()
    try:
        # Get unmapped bonds
        bonds_data = await get_unmapped_bonds(session, limit=limit)

        if not bonds_data:
            print("No unmapped bonds found")
            return

        print(f"Found {len(bonds_data)} unmapped bonds")

        # Group by company for efficient OpenFIGI caching
        by_company = {}
        for bond, company_name, company_ticker in bonds_data:
            if company_ticker not in by_company:
                by_company[company_ticker] = {
                    "name": company_name,
                    "bonds": []
                }
            by_company[company_ticker]["bonds"].append(bond)

        print(f"From {len(by_company)} companies")

        results = []
        success_count = 0

        for ticker, data in by_company.items():
            print(f"\n[{ticker}] {data['name']}")

            # Pre-fetch OpenFIGI data
            from app.services.cusip_mapping import query_openfigi_by_ticker
            try:
                figi_cache = await query_openfigi_by_ticker(ticker)
            except Exception:
                figi_cache = []

            for bond in data["bonds"]:
                result = await map_bond_to_cusip(
                    bond=bond,
                    company_name=data["name"],
                    company_ticker=ticker,
                    figi_cache=figi_cache,
                )
                results.append(result)

                status = "[OK]" if result.success else ("[FIGI]" if result.figi else "[X]")
                print(f"  {status} {bond.name[:45]}...")

                if result.success:
                    success_count += 1
                    # Save to database if not dry run
                    if not dry_run:
                        stmt = (
                            update(DebtInstrument)
                            .where(DebtInstrument.id == result.debt_instrument_id)
                            .values(
                                cusip=result.cusip,
                                isin=result.isin if result.isin else DebtInstrument.isin,
                            )
                        )
                        await session.execute(stmt)

        if not dry_run and success_count > 0:
            await session.commit()

        # Summary
        figi_count = sum(1 for r in results if r.figi and not r.success)
        print(f"\n{'='*60}")
        print(f"SUMMARY")
        print(f"{'='*60}")
        print(f"Total processed: {len(results)}")
        print(f"CUSIPs found: {success_count}")
        print(f"FIGIs matched (no CUSIP): {figi_count}")
        print(f"Failed: {len(results) - success_count - figi_count}")

        if dry_run:
            print("\n[DRY RUN] No changes saved to database")
        elif success_count > 0:
            print(f"\nSaved {success_count} CUSIPs to database")

    finally:
        await session.close()
        await engine.dispose()


async def main():
    parser = argparse.ArgumentParser(
        description="Map debt instruments to CUSIPs"
    )
    parser.add_argument("--ticker", type=str, help="Stock ticker (e.g., AAPL)")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Map all unmapped bonds",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only, don't save to database",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum bonds to process (for --all mode)",
    )

    args = parser.parse_args()

    # Check for API key
    if os.getenv("OPENFIGI_API_KEY"):
        print("Using OpenFIGI API key (25 req/min)")
    else:
        print("No API key - using free tier (5 req/min)")

    if args.ticker:
        await map_single_company(args.ticker.upper(), dry_run=args.dry_run)
    elif args.all:
        await map_all_unmapped(dry_run=args.dry_run, limit=args.limit)
    else:
        parser.print_help()
        print("\nExamples:")
        print("  python scripts/map_cusips.py --ticker AAPL")
        print("  python scripts/map_cusips.py --ticker AAPL --dry-run")
        print("  python scripts/map_cusips.py --all --limit 50")
        print()
        print("Note: CUSIPs are proprietary (owned by ABA/CGS).")
        print("Best results come from extracting CUSIPs/ISINs during SEC filing extraction.")
        print("The ISIN -> CUSIP conversion works for US securities.")


if __name__ == "__main__":
    asyncio.run(main())
