#!/usr/bin/env python3
"""
Extract credit ratings from SEC filings and update CompanyMetrics.

This script searches 10-K/10-Q filings for credit rating disclosures
and extracts S&P and Moody's ratings.

Usage:
    python scripts/extract_ratings.py                    # All companies
    python scripts/extract_ratings.py --ticker CHTR     # Single company
    python scripts/extract_ratings.py --dry-run         # Preview without saving
"""

import argparse
import asyncio
import os
import re
import sys
from collections import Counter

from dotenv import load_dotenv

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models import Company, CompanyMetrics
from app.services.extraction import SecApiClient, clean_filing_html

settings = get_settings()

# Initialize SEC API client (will be created in main)
sec_client = None


# S&P rating scale (investment grade: BBB- and above)
SP_RATINGS = [
    "AAA", "AA+", "AA", "AA-",
    "A+", "A", "A-",
    "BBB+", "BBB", "BBB-",
    "BB+", "BB", "BB-",
    "B+", "B", "B-",
    "CCC+", "CCC", "CCC-",
    "CC", "C", "D"
]

# Moody's rating scale
MOODYS_RATINGS = [
    "Aaa", "Aa1", "Aa2", "Aa3",
    "A1", "A2", "A3",
    "Baa1", "Baa2", "Baa3",
    "Ba1", "Ba2", "Ba3",
    "B1", "B2", "B3",
    "Caa1", "Caa2", "Caa3",
    "Ca", "C"
]

# Rating bucket mapping
def get_rating_bucket(sp_rating: str = None, moodys_rating: str = None) -> str:
    """Determine rating bucket from S&P or Moody's rating."""
    rating = sp_rating or ""
    rating_upper = rating.upper()

    if rating_upper.startswith(("AAA", "AA", "A")) and not rating_upper.startswith("A-"):
        return "IG"  # Investment Grade A and above
    if rating_upper.startswith("BBB"):
        return "IG"  # Investment Grade BBB
    if rating_upper.startswith("BB"):
        return "HY-BB"  # High Yield BB
    if rating_upper.startswith("B") and not rating_upper.startswith("BB"):
        return "HY-B"  # High Yield B
    if rating_upper.startswith(("CCC", "CC", "C", "D")):
        return "HY-CCC"  # High Yield CCC and below

    # Try Moody's if S&P not available
    if moodys_rating:
        moody = moodys_rating.lower()
        if moody.startswith(("aaa", "aa", "a")) and not moody.startswith("a3"):
            return "IG"
        if moody.startswith("baa"):
            return "IG"
        if moody.startswith("ba"):
            return "HY-BB"
        if moody.startswith("b") and not moody.startswith("ba"):
            return "HY-B"
        if moody.startswith(("caa", "ca", "c")):
            return "HY-CCC"

    return "NR"  # Not Rated


def extract_ratings_from_text(text: str) -> dict:
    """
    Extract S&P and Moody's ratings from filing text.

    Returns dict with 'sp_rating' and 'moodys_rating' keys.
    """
    results = {
        "sp_rating": None,
        "moodys_rating": None,
        "sp_matches": [],
        "moodys_matches": [],
    }

    if not text:
        return results

    # Build S&P rating pattern - must be careful about word boundaries
    sp_rating_re = "|".join(re.escape(r) for r in SP_RATINGS)

    # S&P patterns - various ways ratings are disclosed
    sp_patterns = [
        # "S&P: BB+" or "S&P Global Ratings: BB+"
        rf"S&P[^A-Za-z0-9]{{0,30}}({sp_rating_re})\b",
        # "Standard & Poor's: BB+"
        rf"Standard\s*&?\s*Poor[^A-Za-z0-9]{{0,20}}({sp_rating_re})\b",
        # "BB+ (S&P)"
        rf"\b({sp_rating_re})\s*\(S&P",
        # "rated BB+ by S&P"
        rf"rated\s+({sp_rating_re})\s+by\s+S&P",
        # "S&P rating of BB+"
        rf"S&P\s+rating\s+of\s+({sp_rating_re})\b",
    ]

    # Build Moody's rating pattern
    moodys_rating_re = "|".join(re.escape(r) for r in MOODYS_RATINGS)

    # Moody's patterns
    moodys_patterns = [
        # "Moody's: Ba1"
        rf"Moody[^A-Za-z0-9]{{0,20}}({moodys_rating_re})\b",
        # "Ba1 (Moody's)"
        rf"\b({moodys_rating_re})\s*\(Moody",
        # "rated Ba1 by Moody's"
        rf"rated\s+({moodys_rating_re})\s+by\s+Moody",
        # "Moody's rating of Ba1"
        rf"Moody[^A-Za-z0-9]{{0,10}}rating\s+of\s+({moodys_rating_re})\b",
    ]

    # Find all S&P matches
    for pattern in sp_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            rating = match.group(1).upper()
            # Normalize: ensure proper case
            for standard in SP_RATINGS:
                if rating == standard.upper():
                    rating = standard
                    break
            results["sp_matches"].append(rating)

    # Find all Moody's matches
    for pattern in moodys_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            rating = match.group(1)
            # Normalize to proper case
            for standard in MOODYS_RATINGS:
                if rating.lower() == standard.lower():
                    rating = standard
                    break
            results["moodys_matches"].append(rating)

    # Take most common rating (in case of multiple mentions)
    if results["sp_matches"]:
        counter = Counter(results["sp_matches"])
        results["sp_rating"] = counter.most_common(1)[0][0]

    if results["moodys_matches"]:
        counter = Counter(results["moodys_matches"])
        results["moodys_rating"] = counter.most_common(1)[0][0]

    return results


async def extract_ratings_for_company(
    db: AsyncSession,
    company: Company,
    dry_run: bool = False,
) -> dict:
    """Extract ratings for a single company from its 10-K filing."""

    ticker = company.ticker

    # Get filing text using SEC API client
    try:
        # Get 10-K filings
        filings = sec_client.get_filings_by_ticker(
            ticker=ticker,
            form_types=["10-K"],
            max_filings=1,
        )

        if not filings:
            # Try 10-Q as fallback
            filings = sec_client.get_filings_by_ticker(
                ticker=ticker,
                form_types=["10-Q"],
                max_filings=1,
            )

        if not filings:
            return {"ticker": ticker, "error": "No filing found"}

        # Get filing content - filings is a list of dicts from SEC-API
        # The linkToFilingDetails field has the URL
        filing = filings[0]
        filing_url = filing.get("linkToFilingDetails") or filing.get("linkToHtml")
        if not filing_url:
            return {"ticker": ticker, "error": "No filing URL in response"}

        content = sec_client.get_filing_content(filing_url)
        text = clean_filing_html(content) if content else None

    except Exception as e:
        return {"ticker": ticker, "error": str(e)}

    if not text:
        return {"ticker": ticker, "error": "Could not get filing content"}

    # Extract ratings
    ratings = extract_ratings_from_text(text)

    # Calculate bucket
    rating_bucket = get_rating_bucket(ratings["sp_rating"], ratings["moodys_rating"])

    result = {
        "ticker": ticker,
        "sp_rating": ratings["sp_rating"],
        "moodys_rating": ratings["moodys_rating"],
        "rating_bucket": rating_bucket,
        "sp_match_count": len(ratings["sp_matches"]),
        "moodys_match_count": len(ratings["moodys_matches"]),
    }

    if not dry_run and (ratings["sp_rating"] or ratings["moodys_rating"]):
        # Update CompanyMetrics
        metrics_result = await db.execute(
            select(CompanyMetrics).where(CompanyMetrics.ticker == ticker)
        )
        metrics = metrics_result.scalar_one_or_none()

        if metrics:
            if ratings["sp_rating"]:
                metrics.sp_rating = ratings["sp_rating"]
            if ratings["moodys_rating"]:
                metrics.moodys_rating = ratings["moodys_rating"]
            if rating_bucket != "NR":
                metrics.rating_bucket = rating_bucket
            await db.flush()

    return result


async def main():
    global sec_client

    parser = argparse.ArgumentParser(description="Extract credit ratings from SEC filings")
    parser.add_argument("--ticker", help="Single ticker to process")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of companies")
    args = parser.parse_args()

    # Initialize SEC API client
    api_key = os.environ.get("SEC_API_KEY")
    if not api_key:
        print("ERROR: SEC_API_KEY environment variable not set")
        return
    sec_client = SecApiClient(api_key)

    # Create async engine
    database_url = settings.database_url
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        # Get companies to process
        if args.ticker:
            result = await db.execute(
                select(Company).where(Company.ticker == args.ticker.upper())
            )
            companies = list(result.scalars().all())
            if not companies:
                print(f"Company {args.ticker} not found")
                return
        else:
            query = select(Company).order_by(Company.ticker)
            if args.limit > 0:
                query = query.limit(args.limit)
            result = await db.execute(query)
            companies = list(result.scalars().all())

        print(f"Processing {len(companies)} companies...")
        if args.dry_run:
            print("(DRY RUN - no changes will be saved)")
        print()

        found_count = 0
        for company in companies:
            try:
                result = await extract_ratings_for_company(db, company, args.dry_run)

                sp = result.get("sp_rating", "N/A") or "N/A"
                moodys = result.get("moodys_rating", "N/A") or "N/A"
                bucket = result.get("rating_bucket", "NR")
                error = result.get("error", "")

                if sp != "N/A" or moodys != "N/A":
                    found_count += 1
                    print(f"  {company.ticker:6} | S&P: {sp:6} | Moody's: {moodys:6} | Bucket: {bucket}")
                elif error:
                    print(f"  {company.ticker:6} | ERROR: {error}")
                else:
                    print(f"  {company.ticker:6} | No ratings found")

            except Exception as e:
                print(f"  {company.ticker:6} | ERROR: {e}")

        if not args.dry_run:
            await db.commit()
            print(f"\nCommitted changes. Found ratings for {found_count}/{len(companies)} companies")
        else:
            print(f"\nDry run complete. Would update {found_count}/{len(companies)} companies")


if __name__ == "__main__":
    asyncio.run(main())
