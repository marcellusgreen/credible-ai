#!/usr/bin/env python3
"""
Fetch 424B prospectus supplements and extract ownership-relevant sections.

424B2/424B5 prospectus supplements explicitly describe the full corporate chain
because investors need the credit structure. They contain standardized sections
like "The Issuer", "The Guarantors", and "Organizational Structure" with language
like "CCO Holdings Capital Corp., a wholly-owned subsidiary of CCO Holdings, LLC".

This script:
1. Fetches 424B2 and 424B5 filings via SEC-API
2. Extracts ownership-relevant sections (named sections + keyword paragraphs)
3. Stores them as DocumentSection rows with section_type="prospectus"

Usage:
    # Analyze 424B availability and orphan counts
    python scripts/fetch_prospectus_sections.py --analyze

    # Single company (dry run)
    python scripts/fetch_prospectus_sections.py --ticker CHTR --verbose

    # Single company (save to database)
    python scripts/fetch_prospectus_sections.py --ticker CHTR --save

    # All companies with orphans (save)
    python scripts/fetch_prospectus_sections.py --all --save

    # Limit filings per form type
    python scripts/fetch_prospectus_sections.py --ticker CHTR --save --max-filings 20
"""

import asyncio
import re
import time
from datetime import date
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func, and_

from script_utils import (
    create_fix_parser,
    get_db_session,
    print_header,
    print_subheader,
    print_summary,
    run_async,
)
from app.core.config import get_settings
from app.models import Company, DocumentSection, Entity
from app.services.extraction import SecApiClient
from app.services.section_extraction import ExtractedSection, store_sections


# =============================================================================
# PROSPECTUS SECTION EXTRACTION
# =============================================================================

# Named section patterns — standard sections in 424B prospectus supplements
# NOTE: SEC-API renders content as continuous text (no newlines), so patterns
# must NOT require newlines. We use \s+ between header and body.
PROSPECTUS_SECTION_PATTERNS = [
    # "The Issuer" / "About the Issuer"
    (r'(?i)((?:About\s+)?The\s+Issuer)\s+(.{200,}?)(?=(?:About\s+)?The\s+Guarantors?|The\s+Notes|The\s+Offering|Risk\s+Factors|Use\s+of\s+Proceeds|About\s+This|Description\s+of|Summary\s+of|Table\s+of\s+Contents|\Z)',
     "The Issuer"),
    # "The Guarantors" / "About the Guarantors"
    (r'(?i)((?:About\s+)?The\s+Guarantors?)\s+(.{200,}?)(?=The\s+Notes|The\s+Offering|Risk\s+Factors|Use\s+of\s+Proceeds|About\s+This|Description\s+of(?:\s+the)?\s+Notes|Summary\s+of|Table\s+of\s+Contents|\Z)',
     "The Guarantors"),
    # "Organizational Structure" / "Corporate Structure"
    (r'(?i)((?:Organizational|Corporate)\s+Structure)\s+(.{200,}?)(?=The\s+Notes|The\s+Offering|Risk\s+Factors|Use\s+of\s+Proceeds|Summary\s+of|Table\s+of\s+Contents|Description\s+of|\Z)',
     "Organizational Structure"),
    # "Description of Notes" — often contains issuer/guarantor chain
    (r'(?i)(Description\s+of\s+(?:the\s+)?Notes)\s+(.{200,}?)(?=Risk\s+Factors|Use\s+of\s+Proceeds|Plan\s+of\s+Distribution|Certain\s+U\.?S\.?\s+Federal|Legal\s+Matters|Table\s+of\s+Contents|\Z)',
     "Description of Notes"),
]

# Ownership keywords for paragraph-level fallback extraction
OWNERSHIP_KEYWORDS = [
    r'wholly[- ]owned\s+subsidiary',
    r'direct\s+subsidiary',
    r'indirect\s+subsidiary',
    r'subsidiary\s+of',
    r'parent\s+of',
    r'the\s+issuer\s+is',
    r'owned\s+by',
    r'organized\s+under',
    r'a\s+subsidiary',
    r'holding\s+company',
    r'organizational\s+structure',
    r'corporate\s+structure',
    r'guarantor',
    r'the\s+company\s+is\s+a',
]

MAX_SECTION_CHARS = 20000


def extract_prospectus_ownership_content(content: str, verbose: bool = False) -> Optional[str]:
    """
    Extract ownership-relevant content from a 424B prospectus supplement.

    Strategy 1: Named section extraction (standard prospectus sections).
    Strategy 2: Keyword paragraph fallback if named sections yield <2K chars.

    Returns combined extracted text or None if insufficient ownership content.
    """
    if not content or len(content) < 500:
        return None

    extracted_parts = []
    total_chars = 0

    # Strategy 1: Named section extraction
    for pattern, label in PROSPECTUS_SECTION_PATTERNS:
        match = re.search(pattern, content, re.DOTALL)
        if match:
            section_text = match.group(2).strip()
            if len(section_text) > 200:
                # Cap each section
                section_text = section_text[:MAX_SECTION_CHARS]
                extracted_parts.append(f"=== {label} ===\n{section_text}")
                total_chars += len(section_text)
                if verbose:
                    print(f"      Found section: {label} ({len(section_text):,} chars)")

    # Strategy 2: Keyword window fallback if named sections insufficient
    # SEC-API renders content as one continuous string (no newlines/paragraphs),
    # so we extract windows of text around ownership keyword matches.
    if total_chars < 2000:
        if verbose:
            print(f"      Named sections yielded {total_chars:,} chars, trying keyword window fallback")

        content_lower = content.lower()
        keyword_windows = []
        seen_positions = set()  # Avoid overlapping windows
        window_radius = 500  # chars before and after keyword match

        for kw in OWNERSHIP_KEYWORDS:
            for match in re.finditer(kw, content_lower):
                # Check if this position overlaps with an existing window
                center = match.start()
                if any(abs(center - pos) < window_radius for pos in seen_positions):
                    continue

                start = max(0, center - window_radius)
                end = min(len(content), center + window_radius)
                window = content[start:end].strip()
                keyword_windows.append(window)
                seen_positions.add(center)

                if len(keyword_windows) >= 30:
                    break
            if len(keyword_windows) >= 30:
                break

        if keyword_windows:
            keyword_text = '\n\n---\n\n'.join(keyword_windows)
            extracted_parts.append(f"=== Keyword Windows ===\n{keyword_text}")
            total_chars += len(keyword_text)
            if verbose:
                print(f"      Keyword fallback: {len(keyword_windows)} windows ({len(keyword_text):,} chars)")

    if not extracted_parts:
        return None

    combined = '\n\n'.join(extracted_parts)

    # Sanity check: must contain at least one ownership keyword
    combined_lower = combined.lower()
    has_ownership = any(re.search(kw, combined_lower) for kw in OWNERSHIP_KEYWORDS)
    if not has_ownership:
        if verbose:
            print("      No ownership keywords found in extracted content, skipping")
        return None

    return combined


# =============================================================================
# COMPANY PROCESSING
# =============================================================================

async def process_company(
    company_id: UUID,
    name: str,
    ticker: str,
    cik: str,
    sec_client: SecApiClient,
    save: bool = False,
    verbose: bool = False,
    max_filings: int = 50,
) -> dict:
    """Process a single company: fetch 424B filings, extract ownership sections, store."""

    stats = {
        "filings_checked": 0,
        "filings_with_content": 0,
        "sections_extracted": 0,
        "sections_stored": 0,
        "errors": [],
    }

    print(f"\n[{ticker}] {name}")

    all_sections: list[ExtractedSection] = []

    for form_type in ["424B2", "424B5"]:
        try:
            filings = sec_client.get_filings_by_ticker(
                ticker=ticker,
                form_types=[form_type],
                max_filings=max_filings,
                cik=cik,
            )

            if verbose:
                print(f"    {form_type}: {len(filings)} filings found")

            for filing in filings:
                stats["filings_checked"] += 1
                url = filing.get("linkToFilingDetails") or filing.get("linkToHtml")
                filing_date_str = filing.get("filedAt", "")[:10]

                if not url:
                    continue

                # Parse filing date
                try:
                    filing_date_obj = date.fromisoformat(filing_date_str)
                except (ValueError, TypeError):
                    if verbose:
                        print(f"      Skipping filing with bad date: {filing_date_str}")
                    continue

                try:
                    content = sec_client.get_filing_content(url)
                    if not content or len(content) < 1000:
                        continue

                    ownership_content = extract_prospectus_ownership_content(content, verbose)
                    if ownership_content:
                        stats["filings_with_content"] += 1
                        section = ExtractedSection(
                            section_type="prospectus",
                            section_title=f"Prospectus Supplement - Ownership Structure ({form_type})",
                            content=ownership_content,
                            doc_type=form_type,
                            filing_date=filing_date_obj,
                            sec_filing_url=url,
                        )
                        all_sections.append(section)

                        if verbose:
                            print(f"      {filing_date_str}: {len(ownership_content):,} chars extracted")
                    elif verbose:
                        print(f"      {filing_date_str}: no ownership content")

                except Exception as e:
                    stats["errors"].append(f"{form_type} {filing_date_str}: {str(e)[:60]}")
                    if verbose:
                        print(f"      Error: {e}")

        except Exception as e:
            stats["errors"].append(f"{form_type} query: {str(e)[:60]}")
            if verbose:
                print(f"    Error querying {form_type}: {e}")

    if not all_sections:
        print(f"  No prospectus ownership content found ({stats['filings_checked']} filings checked)")
        return stats

    # Deduplicate by date: keep largest section per filing date
    by_date: dict[date, ExtractedSection] = {}
    for section in all_sections:
        existing = by_date.get(section.filing_date)
        if not existing or len(section.content) > len(existing.content):
            by_date[section.filing_date] = section

    deduped = list(by_date.values())
    stats["sections_extracted"] = len(deduped)

    print(f"  {stats['filings_checked']} filings checked, {stats['filings_with_content']} with content, {len(deduped)} unique dates")

    if save:
        async with get_db_session() as db:
            stored = await store_sections(db, company_id, deduped, replace_existing=True)
            stats["sections_stored"] = stored
            print(f"  Stored {stored} prospectus sections")
    else:
        print(f"  Would store {len(deduped)} sections (dry run)")
        if verbose:
            for s in deduped[:5]:
                print(f"    {s.filing_date} ({s.doc_type}): {len(s.content):,} chars")
            if len(deduped) > 5:
                print(f"    ... and {len(deduped) - 5} more")

    if stats["errors"] and verbose:
        for err in stats["errors"][:3]:
            print(f"  Error: {err}")

    return stats


# =============================================================================
# ANALYZE MODE
# =============================================================================

async def run_analyze(sec_client: SecApiClient, ticker: Optional[str] = None, limit: int = 0):
    """Show orphan count + existing prospectus section count per company."""

    print_header("PROSPECTUS SECTIONS - ANALYSIS")

    async with get_db_session() as db:
        # Get companies with orphans
        query = (
            select(
                Company.id,
                Company.ticker,
                Company.name,
                Company.cik,
                func.count(Entity.id).label("orphan_count"),
            )
            .join(Entity, Entity.company_id == Company.id)
            .where(
                and_(
                    Entity.parent_id.is_(None),
                    Entity.is_root.is_(False),
                )
            )
            .group_by(Company.id, Company.ticker, Company.name, Company.cik)
            .order_by(func.count(Entity.id).desc())
        )

        if ticker:
            query = query.where(Company.ticker == ticker.upper())
        if limit and limit > 0:
            query = query.limit(limit)

        result = await db.execute(query)
        companies = result.fetchall()

        print(f"Companies with orphans: {len(companies)}")
        print()

        print_subheader("ORPHANS AND PROSPECTUS SECTIONS BY COMPANY")
        print(f"{'Ticker':<8} {'Orphans':>8} {'Prospectus':>11} {'CIK':<12} Name")
        print("-" * 85)

        total_orphans = 0
        total_prospectus = 0

        for row in companies:
            # Count existing prospectus sections
            ps_result = await db.execute(
                select(func.count(DocumentSection.id))
                .where(DocumentSection.company_id == row.id)
                .where(DocumentSection.section_type == 'prospectus')
            )
            ps_count = ps_result.scalar() or 0
            total_prospectus += ps_count
            total_orphans += row.orphan_count

            has_cik = "Yes" if row.cik else "No"
            print(f"{row.ticker:<8} {row.orphan_count:>8} {ps_count:>11} {(row.cik or 'N/A'):<12} {row.name[:35]}")

        print("-" * 85)
        print(f"{'TOTAL':<8} {total_orphans:>8} {total_prospectus:>11}")


# =============================================================================
# GET COMPANIES
# =============================================================================

async def get_companies_with_orphans(
    db,
    ticker: Optional[str] = None,
    limit: int = 0,
) -> list:
    """Get companies that have orphan entities, returning (id, ticker, name, cik)."""
    query = (
        select(
            Company.id,
            Company.ticker,
            Company.name,
            Company.cik,
        )
        .join(Entity, Entity.company_id == Company.id)
        .where(
            and_(
                Entity.parent_id.is_(None),
                Entity.is_root.is_(False),
            )
        )
        .group_by(Company.id, Company.ticker, Company.name, Company.cik)
        .having(func.count(Entity.id) >= 1)
        .order_by(Company.ticker)
    )

    if ticker:
        query = query.where(Company.ticker == ticker.upper())
    if limit and limit > 0:
        query = query.limit(limit)

    result = await db.execute(query)
    return result.fetchall()


# =============================================================================
# MAIN
# =============================================================================

async def main():
    parser = create_fix_parser("Fetch 424B prospectus sections for intermediate ownership extraction")
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Show orphan statistics and existing prospectus section counts",
    )
    parser.add_argument(
        "--max-filings",
        type=int,
        default=50,
        help="Max filings to fetch per form type per company (default: 50)",
    )

    args = parser.parse_args()

    settings = get_settings()

    if not settings.sec_api_key:
        print("Error: SEC_API_KEY not set")
        return

    sec_client = SecApiClient(settings.sec_api_key)

    # Analysis mode
    if args.analyze:
        await run_analyze(sec_client, args.ticker, args.limit)
        return

    if not args.ticker and not getattr(args, "all", False):
        print("Error: Must specify --ticker, --all, or --analyze")
        return

    print_header("FETCH PROSPECTUS SECTIONS FOR OWNERSHIP EXTRACTION")
    print(f"Mode: {'SAVE TO DB' if args.save else 'DRY RUN'}")
    print(f"Max filings per form type: {args.max_filings}")
    print()

    # Get companies with orphans (in a session that closes before processing)
    async with get_db_session() as db:
        companies = await get_companies_with_orphans(db, args.ticker, args.limit)

    if not companies:
        if args.ticker:
            print(f"No orphan entities found for {args.ticker}")
        else:
            print("No companies with orphan entities found")
        return

    print(f"Found {len(companies)} companies with orphan entities")

    total_stats = {
        "companies_processed": 0,
        "companies_skipped": 0,
        "total_filings_checked": 0,
        "total_sections_extracted": 0,
        "total_sections_stored": 0,
    }

    for i, row in enumerate(companies):
        try:
            stats = await process_company(
                company_id=row.id,
                name=row.name,
                ticker=row.ticker,
                cik=row.cik or "",
                sec_client=sec_client,
                save=args.save,
                verbose=args.verbose,
                max_filings=args.max_filings,
            )

            total_stats["companies_processed"] += 1
            total_stats["total_filings_checked"] += stats["filings_checked"]
            total_stats["total_sections_extracted"] += stats["sections_extracted"]
            total_stats["total_sections_stored"] += stats["sections_stored"]

        except Exception as e:
            print(f"  Error: {e}")
            import traceback
            traceback.print_exc()
            total_stats["companies_skipped"] += 1

        # Rate limit between companies
        if i < len(companies) - 1:
            time.sleep(0.5)

    # Summary
    print_summary({
        "Companies processed": total_stats["companies_processed"],
        "Companies skipped": total_stats["companies_skipped"],
        "Total filings checked": total_stats["total_filings_checked"],
        "Sections extracted": total_stats["total_sections_extracted"],
        "Sections stored": total_stats["total_sections_stored"] if args.save else f"{total_stats['total_sections_extracted']} (dry run)",
    })


if __name__ == "__main__":
    run_async(main())
