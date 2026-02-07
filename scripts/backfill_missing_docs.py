#!/usr/bin/env python3
"""
Backfill missing document sections for companies with unlinked debt instruments.

This script specifically targets companies that have debt instruments without
document links, downloading additional SEC filings (8-K, 10-Q) that may contain
the missing indentures and credit agreements.

Usage:
    # Process top 20 companies with most unlinked instruments
    python scripts/backfill_missing_docs.py --top 20

    # Process specific company
    python scripts/backfill_missing_docs.py --ticker PLD

    # Dry run
    python scripts/backfill_missing_docs.py --top 10 --dry-run
"""

import argparse
import asyncio
import os
import re
from datetime import date

from sqlalchemy import text

from script_utils import get_db_session, print_header, run_async
from app.models import Company, DocumentSection
from app.services.extraction import SecApiClient
from app.services.section_extraction import ExtractedSection, store_sections


async def get_companies_needing_docs(session, limit: int = 20) -> list[dict]:
    """Get companies with the most unlinked debt instruments."""
    result = await session.execute(text('''
        SELECT
            c.id,
            c.ticker,
            c.cik,
            COUNT(*) as unmatched_count,
            COUNT(*) FILTER (WHERE di.instrument_type NOT IN ('revolver', 'term_loan_a', 'term_loan_b', 'term_loan')) as notes,
            COUNT(*) FILTER (WHERE di.instrument_type IN ('revolver', 'term_loan_a', 'term_loan_b', 'term_loan')) as facilities
        FROM debt_instruments di
        JOIN entities e ON e.id = di.issuer_id
        JOIN companies c ON c.id = e.company_id
        LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
        WHERE di.is_active = true AND did.id IS NULL
        AND di.instrument_type IN (
            'senior_notes', 'senior_secured_notes', 'senior_unsecured_notes',
            'convertible_notes', 'subordinated_notes', 'debentures',
            'revolver', 'term_loan_a', 'term_loan_b', 'term_loan'
        )
        GROUP BY c.id, c.ticker, c.cik
        ORDER BY unmatched_count DESC
        LIMIT :limit
    '''), {'limit': limit})

    return [dict(row._mapping) for row in result.fetchall()]


async def get_existing_doc_dates(session, company_id) -> set[str]:
    """Get filing dates of existing document sections for a company."""
    result = await session.execute(text('''
        SELECT DISTINCT filing_date::text
        FROM document_sections
        WHERE company_id = :company_id
        AND section_type IN ('indenture', 'credit_agreement')
    '''), {'company_id': str(company_id)})
    return {row[0] for row in result.fetchall()}


async def download_additional_filings(
    ticker: str,
    cik: str,
    sec_api_key: str,
    existing_dates: set[str]
) -> dict[str, str]:
    """Download additional filings that aren't already in the database."""
    sec_client = SecApiClient(sec_api_key)

    all_filings = {}

    # Get more historical indentures (EX-4 exhibits)
    print(f"    Fetching historical indentures...")
    indenture_filings = sec_client.get_historical_indentures(ticker, cik=cik, max_filings=150)

    # Get more 8-K filings (for credit agreements)
    print(f"    Fetching 8-K filings...")
    eight_k_filings = sec_client.get_filings_by_ticker(
        ticker,
        form_types=["8-K"],
        max_filings=50,
        cik=cik
    )

    # Combine and deduplicate
    seen = set()
    filings_to_process = []

    for filing in indenture_filings + eight_k_filings:
        accession = filing.get("accessionNo", "")
        if accession not in seen:
            seen.add(accession)
            # Check if we already have documents from this filing date
            filing_date = filing.get("filedAt", "")[:10]
            if filing_date not in existing_dates:
                filings_to_process.append(filing)

    print(f"    Found {len(filings_to_process)} new filings to process")

    # Download exhibits
    downloaded_count = 0
    for filing in filings_to_process[:50]:  # Limit to 50 new filings
        accession = filing.get("accessionNo", "")
        filing_date = filing.get("filedAt", "")[:10]

        # Get document list for this filing
        docs = filing.get("documentFormatFiles", [])

        for doc in docs:
            doc_type = doc.get("type", "")
            doc_url = doc.get("documentUrl", "")

            if not doc_url:
                continue

            # EX-4 exhibits (indentures)
            if "EX-4" in doc_type or (doc_type.startswith("4") and "." in doc_type):
                key = f"indenture_{filing_date}_{doc_type.replace('.', '_')}"
                if key in all_filings:
                    continue
                try:
                    # Use synchronous get_filing_content which handles SEC-API rendering
                    content = sec_client.get_filing_content(doc_url)
                    if content and len(content) > 1000:
                        all_filings[key] = content
                        downloaded_count += 1
                        print(f"      [{downloaded_count}] Downloaded: {key}")
                except Exception as e:
                    print(f"      Failed {key}: {e}")
                await asyncio.sleep(0.15)  # Rate limiting

            # EX-10 exhibits (credit agreements)
            elif "EX-10" in doc_type or (doc_type.startswith("10") and "." in doc_type):
                # Skip employment/compensation documents
                desc = doc.get("description", "").upper()
                exclude_keywords = ["EMPLOYMENT", "COMPENSATION", "BONUS", "INCENTIVE",
                                    "SEVERANCE", "BENEFIT", "LEASE", "SUBLEASE", "AMENDMENT TO EMPLOY"]
                if any(kw in desc for kw in exclude_keywords):
                    continue

                key = f"credit_agreement_{filing_date}_{doc_type.replace('.', '_')}"
                if key in all_filings:
                    continue
                try:
                    content = sec_client.get_filing_content(doc_url)
                    if content and len(content) > 1000:
                        all_filings[key] = content
                        downloaded_count += 1
                        print(f"      [{downloaded_count}] Downloaded: {key}")
                except Exception as e:
                    print(f"      Failed {key}: {e}")
                await asyncio.sleep(0.15)  # Rate limiting

    return all_filings


def extract_sections_from_filings(filings: dict[str, str], ticker: str) -> list[ExtractedSection]:
    """Extract document sections from downloaded filings."""
    sections = []

    for key, content in filings.items():
        if not content or len(content) < 1000:
            continue

        # Skip binary/PDF content
        if content.startswith('%PDF') or '\x00' in content[:100]:
            continue

        # Parse key to get date and type
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', key)
        if not date_match:
            continue

        try:
            filing_date = date.fromisoformat(date_match.group(1))
        except ValueError:
            continue

        # Determine section type and extract title
        if key.startswith("indenture"):
            section_type = "indenture"
            # Try to extract title from content
            title_match = re.search(
                r'(?i)((?:(?:\d+(?:st|nd|rd|th)\s+)?Supplemental\s+)?Indenture[^.]{0,150})',
                content[:3000]
            )
            title = title_match.group(1).strip()[:250] if title_match else f"Indenture ({key})"
        elif key.startswith("credit_agreement"):
            section_type = "credit_agreement"
            title_match = re.search(
                r'(?i)((?:Amended\s+and\s+Restated\s+)?(?:Credit|Loan|Facility)\s+Agreement[^.]{0,100})',
                content[:2000]
            )
            title = title_match.group(1).strip()[:250] if title_match else f"Credit Agreement ({key})"
        else:
            continue

        # Truncate content if too long
        section_content = content[:500000]
        if len(content) > 500000:
            section_content += "\n\n[TRUNCATED]"

        sections.append(ExtractedSection(
            section_type=section_type,
            section_title=title,
            content=section_content,
            doc_type="8-K",
            filing_date=filing_date,
            sec_filing_url=None
        ))

    return sections


async def process_company(
    session,
    company: dict,
    sec_api_key: str,
    dry_run: bool = False
) -> dict:
    """Process a single company to backfill document sections."""
    ticker = company['ticker']
    cik = company['cik']
    company_id = company['id']

    print(f"\n[{ticker}] Processing ({company['unmatched_count']} unmatched instruments)...")

    if not cik:
        return {"ticker": ticker, "status": "skip", "reason": "no CIK"}

    # Get existing document dates
    existing_dates = await get_existing_doc_dates(session, company_id)
    print(f"  Existing doc dates: {len(existing_dates)}")

    # Download additional filings
    try:
        filings = await download_additional_filings(ticker, cik, sec_api_key, existing_dates)
    except Exception as e:
        return {"ticker": ticker, "status": "error", "reason": f"download failed: {e}"}

    if not filings:
        return {"ticker": ticker, "status": "skip", "reason": "no new filings found"}

    print(f"  Downloaded {len(filings)} new filings")

    # Extract sections
    sections = extract_sections_from_filings(filings, ticker)
    print(f"  Extracted {len(sections)} sections")

    if not sections:
        return {"ticker": ticker, "status": "skip", "reason": "no sections extracted"}

    # Count by type
    indentures = sum(1 for s in sections if s.section_type == "indenture")
    credit_agreements = sum(1 for s in sections if s.section_type == "credit_agreement")
    print(f"    Indentures: {indentures}, Credit Agreements: {credit_agreements}")

    if dry_run:
        return {
            "ticker": ticker,
            "status": "dry_run",
            "indentures": indentures,
            "credit_agreements": credit_agreements
        }

    # Store sections
    try:
        # Pass company_id directly (it's already a UUID)
        stored = await store_sections(session, company_id, sections, replace_existing=False)
        await session.commit()
        return {
            "ticker": ticker,
            "status": "success",
            "stored": stored,
            "indentures": indentures,
            "credit_agreements": credit_agreements
        }
    except Exception as e:
        await session.rollback()
        return {"ticker": ticker, "status": "error", "reason": str(e)}


async def main():
    parser = argparse.ArgumentParser(description='Backfill missing document sections')
    parser.add_argument('--top', type=int, default=20, help='Process top N companies by unmatched count')
    parser.add_argument('--ticker', type=str, help='Process specific ticker')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done')
    args = parser.parse_args()

    sec_api_key = os.environ.get('SEC_API_KEY')
    if not sec_api_key:
        print("ERROR: SEC_API_KEY environment variable required")
        return

    print_header("BACKFILL MISSING DOCUMENT SECTIONS")
    print(f"Dry run: {args.dry_run}")

    async with get_db_session() as session:
        if args.ticker:
            # Get specific company
            result = await session.execute(text('''
                SELECT c.id, c.ticker, c.cik, 0 as unmatched_count, 0 as notes, 0 as facilities
                FROM companies c WHERE c.ticker = :ticker
            '''), {'ticker': args.ticker})
            row = result.fetchone()
            if not row:
                print(f"Company {args.ticker} not found")
                return
            companies = [dict(row._mapping)]
        else:
            companies = await get_companies_needing_docs(session, args.top)

        print(f"\nProcessing {len(companies)} companies...")

        results = []
        for company in companies:
            result = await process_company(session, company, sec_api_key, args.dry_run)
            results.append(result)

        # Summary
        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)

        success = [r for r in results if r.get('status') == 'success']
        errors = [r for r in results if r.get('status') == 'error']
        skipped = [r for r in results if r.get('status') == 'skip']
        dry_run_results = [r for r in results if r.get('status') == 'dry_run']

        if args.dry_run:
            print(f"\n[DRY RUN] Would process:")
            total_indentures = sum(r.get('indentures', 0) for r in dry_run_results)
            total_cas = sum(r.get('credit_agreements', 0) for r in dry_run_results)
            print(f"  Total new indentures: {total_indentures}")
            print(f"  Total new credit agreements: {total_cas}")
        else:
            print(f"\nSuccess: {len(success)}")
            for r in success:
                print(f"  {r['ticker']}: {r.get('stored', 0)} sections stored")

            if errors:
                print(f"\nErrors: {len(errors)}")
                for r in errors:
                    print(f"  {r['ticker']}: {r.get('reason', 'unknown')}")

            if skipped:
                print(f"\nSkipped: {len(skipped)}")
                for r in skipped:
                    print(f"  {r['ticker']}: {r.get('reason', 'unknown')}")


if __name__ == "__main__":
    run_async(main())
