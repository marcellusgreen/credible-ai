#!/usr/bin/env python3
"""
Extract ISINs from SEC filings and update debt instruments.

ISINs are found in:
- FWP (Free Writing Prospectus) filings - most structured, has pricing terms
- 424B2/424B5 (Prospectus supplements)

ISIN format: 2-letter country code + 9-char local ID + check digit
US ISIN: US + 9-char CUSIP + check digit
Example: US437076DH27 -> CUSIP: 437076DH2

This script:
1. Extracts ISINs from SEC prospectuses
2. Matches to existing debt instruments by coupon + maturity
3. Stores ISIN/CUSIP in database for later price fetching

Usage:
    python scripts/extract_isins.py --ticker HD              # Single company
    python scripts/extract_isins.py --ticker HD --save       # Save to database
    python scripts/extract_isins.py --all --limit 50         # Batch process
    python scripts/extract_isins.py --all --save             # Batch + save
"""

import argparse
import asyncio
import os
import re
import sys
from dataclasses import dataclass
from datetime import date
from typing import Optional

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models import Company, DebtInstrument, Entity
from app.services.extraction import SecApiClient

settings = get_settings()

# Month name to number mapping
MONTHS = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}


def parse_date_from_text(text: str) -> Optional[date]:
    """
    Parse a date from text like "May 12, 2025" or "September 15, 2030".
    Returns None if parsing fails.
    """
    # Pattern: Month Day, Year (e.g., "May 12, 2025")
    match = re.search(r'([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})', text)
    if match:
        month_name = match.group(1).lower()
        day = int(match.group(2))
        year = int(match.group(3))
        month = MONTHS.get(month_name)
        if month and 1 <= day <= 31 and 2000 <= year <= 2100:
            try:
                return date(year, month, day)
            except ValueError:
                pass
    return None


@dataclass
class BondInfo:
    """Bond information extracted from prospectus."""
    isin: str
    cusip: Optional[str]
    coupon_rate: float  # e.g., 4.65 for 4.650%
    maturity_year: int
    maturity_date: Optional[date] = None  # Full maturity date if available
    issue_date: Optional[date] = None  # Settlement/issue date
    principal_amount: Optional[int] = None  # in dollars


def extract_bonds_from_fwp(content: str) -> list[BondInfo]:
    """
    Extract bond information from FWP (Free Writing Prospectus).

    FWP structure examples:
    1. HD format: CUSIP / ISIN: 437076 DK5 / US437076DK55
    2. AMGN format: CUSIP / ISIN: 2025 Notes: 031162 DM9 / US031162DM91
    """
    bonds = []
    clean = ' '.join(content.split())

    # Pattern 1: Standard format - CUSIP / ISIN: XXXXXX XXX / USXXXXXXXXXX
    patterns = [
        # Standard: CUSIP / ISIN: 437076 DK5 / US437076DK55
        re.compile(
            r'CUSIP\s*/?\s*ISIN[:\s]+([0-9A-Z]{6})\s*([0-9A-Z]{3})\s*/\s*(US[0-9A-Z]{10})',
            re.IGNORECASE
        ),
        # With note label: CUSIP / ISIN: 2025 Notes: 031162 DM9 / US031162DM91
        re.compile(
            r'CUSIP\s*/?\s*ISIN[:\s]+\d{4}\s+Notes?[:\s]+([0-9A-Z]{6})\s*([0-9A-Z]{3})\s*/\s*(US[0-9A-Z]{10})',
            re.IGNORECASE
        ),
        # Just ISIN after label: 2030 Notes: US031162DM91
        re.compile(
            r'\d{4}\s+Notes?[:\s]+(US[0-9A-Z]{10})',
            re.IGNORECASE
        ),
    ]

    for pattern in patterns:
        for match in pattern.finditer(clean):
            groups = match.groups()

            if len(groups) == 3:
                cusip = groups[0] + groups[1]
                isin = groups[2].upper()
            elif len(groups) == 1:
                isin = groups[0].upper()
                cusip = isin[2:11] if isin.startswith('US') else None
            else:
                continue

            # Look backwards for bond details
            start = max(0, match.start() - 3000)
            context = clean[start:match.start()]

            # Find "X.XXX% Notes due YYYY" pattern
            note_matches = list(re.finditer(
                r'(\d{1,2}\.\d+)%\s+(?:Senior\s+)?Notes?\s+due\s+(?:[A-Za-z]+\s+)?(\d{4})',
                context, re.IGNORECASE
            ))

            if note_matches:
                last = note_matches[-1]
                coupon = float(last.group(1))
                year = int(last.group(2))

                # Sanity check: coupons are typically 0-15%, anything higher is likely a price
                if coupon > 15:
                    continue

                # Look for principal amount - FWP format: "Principal Amount: $1,500,000,000"
                principal = None
                principal_patterns = [
                    r'Principal\s+Amount[:\s]+\$\s*([\d,]+)',
                    r'Aggregate\s+Principal\s+Amount[:\s]+\$\s*([\d,]+)',
                ]
                for pp in principal_patterns:
                    principal_matches = list(re.finditer(pp, context, re.IGNORECASE))
                    if principal_matches:
                        # Get the last match (closest to ISIN)
                        last_match = principal_matches[-1]
                        try:
                            principal = int(last_match.group(1).replace(',', ''))
                        except ValueError:
                            pass
                        if principal:
                            break

                # Look for maturity date - find the LAST (closest to ISIN) match
                # e.g., "Maturity Date: October 15, 2027"
                maturity_date = None
                maturity_patterns = [
                    r'Maturity\s*(?:Date)?[:\s]+([A-Za-z]+\s+\d{1,2},?\s+\d{4})',
                ]
                for mp in maturity_patterns:
                    mat_matches = list(re.finditer(mp, context, re.IGNORECASE))
                    if mat_matches:
                        # Get the last match (closest to ISIN)
                        last_match = mat_matches[-1]
                        maturity_date = parse_date_from_text(last_match.group(1))
                        if maturity_date:
                            break

                # Look for issue/settlement date - find the LAST match
                issue_date = None
                issue_patterns = [
                    r'Settlement\s*(?:Date)?[:\s]+([A-Za-z]+\s+\d{1,2},?\s+\d{4})',
                    r'Issue\s*(?:Date)?[:\s]+([A-Za-z]+\s+\d{1,2},?\s+\d{4})',
                    r'Trade\s*(?:Date)?[:\s]+([A-Za-z]+\s+\d{1,2},?\s+\d{4})',
                ]
                for ip in issue_patterns:
                    issue_matches = list(re.finditer(ip, context, re.IGNORECASE))
                    if issue_matches:
                        last_match = issue_matches[-1]
                        issue_date = parse_date_from_text(last_match.group(1))
                        if issue_date:
                            break

                bonds.append(BondInfo(
                    isin=isin,
                    cusip=cusip,
                    coupon_rate=coupon,
                    maturity_year=year,
                    maturity_date=maturity_date,
                    issue_date=issue_date,
                    principal_amount=principal,
                ))

    # Fallback: Find any US ISIN and look backwards
    if not bonds:
        for match in re.finditer(r'(US[0-9A-Z]{10})', clean):
            isin = match.group(1).upper()
            cusip = isin[2:11]

            start = max(0, match.start() - 2000)
            context = clean[start:match.start()]

            note_matches = list(re.finditer(
                r'(\d{1,2}\.\d+)%\s+(?:Senior\s+)?Notes?\s+due\s+(?:[A-Za-z]+\s+)?(\d{4})',
                context, re.IGNORECASE
            ))

            if note_matches:
                last = note_matches[-1]
                coupon = float(last.group(1))
                year = int(last.group(2))
                # Skip if coupon is unreasonably high (likely a price)
                if coupon <= 15:
                    bonds.append(BondInfo(isin=isin, cusip=cusip, coupon_rate=coupon, maturity_year=year))

    # Deduplicate by ISIN
    seen = set()
    unique = []
    for b in bonds:
        if b.isin not in seen:
            seen.add(b.isin)
            unique.append(b)

    return unique


def is_bond_type(instrument: DebtInstrument) -> bool:
    """
    Check if a debt instrument is a bond/note type that would have an ISIN.

    Bonds/notes have ISINs. These do NOT:
    - Term loans, revolving credit facilities (bank debt)
    - Commercial paper, finance leases, mortgages
    """
    name = (instrument.name or "").lower()

    # Positive indicators - likely a bond
    bond_keywords = ['note', 'bond', 'debenture', '%']
    if any(kw in name for kw in bond_keywords):
        return True

    # Negative indicators - definitely not a bond
    non_bond_keywords = ['loan', 'credit', 'revolver', 'facility', 'term ',
                         'commercial paper', 'lease', 'mortgage']
    if any(kw in name for kw in non_bond_keywords):
        return False

    # If name has a coupon pattern like "X.XX% ... due YYYY", it's a bond
    if re.search(r'\d+\.\d+%.*due\s+\d{4}', name, re.IGNORECASE):
        return True

    return False


def match_bond_to_instrument(bond: BondInfo, instruments: list[DebtInstrument]) -> Optional[DebtInstrument]:
    """
    Match extracted bond to database instrument using flexible matching.

    Matching priority:
    1. Issue date year+month match + coupon match
    2. Exact maturity date match (year, month, day)
    3. Maturity year + coupon rate match (fallback for placeholder dates)

    Only matches against bond-type instruments (not loans, credit facilities, etc.).
    """
    coupon_bps = int(bond.coupon_rate * 100)

    for inst in instruments:
        # Skip if already has ISIN
        if inst.isin:
            continue

        # Skip non-bond instruments (loans, credit facilities, etc.)
        if not is_bond_type(inst):
            continue

        # Priority 1: Issue date year+month match + coupon
        if inst.issue_date and bond.issue_date:
            if (inst.issue_date.year == bond.issue_date.year and
                inst.issue_date.month == bond.issue_date.month):
                # Year+month match - use coupon to disambiguate if multiple
                if inst.interest_rate is not None:
                    if abs(inst.interest_rate - coupon_bps) <= 10:
                        return inst
                else:
                    # No coupon in DB, accept year+month match
                    return inst

        # Priority 2: Exact maturity date match (not placeholder like 12/31)
        if inst.maturity_date and bond.maturity_date:
            # Skip if DB has placeholder date (month=12, day=31)
            is_placeholder = (inst.maturity_date.month == 12 and inst.maturity_date.day == 31)
            if not is_placeholder and inst.maturity_date == bond.maturity_date:
                return inst

    # Priority 3: Maturity year + coupon match (for placeholder dates)
    for inst in instruments:
        if inst.isin:
            continue
        if not is_bond_type(inst):
            continue

        # Get maturity year
        inst_year = inst.maturity_date.year if inst.maturity_date else None
        if not inst_year:
            # Try extracting from name
            year_match = re.search(r'20\d{2}', inst.name or "")
            if year_match:
                inst_year = int(year_match.group(0))

        if inst_year and inst_year == bond.maturity_year:
            # Year matches - require coupon to also match
            if inst.interest_rate is not None:
                if abs(inst.interest_rate - coupon_bps) <= 10:  # 10 bps tolerance
                    return inst

    return None


async def extract_isins_for_company(
    db: AsyncSession,
    sec_client: SecApiClient,
    company: Company,
    save: bool = False,
    create_missing: bool = False,
) -> dict:
    """Extract ISINs for a company and optionally save to database."""

    results = {
        "ticker": company.ticker,
        "filings_checked": 0,
        "bonds_extracted": [],
        "bonds_matched": 0,
        "bonds_updated": 0,
        "bonds_created": 0,
        "bond_instruments": 0,
        "non_bond_instruments": 0,
        "errors": [],
    }

    # Get company's debt instruments
    debt_result = await db.execute(
        select(DebtInstrument)
        .where(DebtInstrument.company_id == company.id)
        .where(DebtInstrument.is_active == True)
    )
    instruments = list(debt_result.scalars().all())

    if not instruments:
        results["errors"].append("No debt instruments found")
        return results

    # Count bond vs non-bond instruments
    for inst in instruments:
        if is_bond_type(inst):
            results["bond_instruments"] += 1
        else:
            results["non_bond_instruments"] += 1

    # Skip if no bond-type instruments
    if results["bond_instruments"] == 0:
        results["errors"].append("No bond-type instruments (only loans/facilities)")
        return results

    # Search FWP and prospectus filings
    all_bonds: list[BondInfo] = []

    for form_type in ["FWP", "424B2", "424B5"]:
        try:
            filings = sec_client.get_filings_by_ticker(
                ticker=company.ticker,
                form_types=[form_type],
                max_filings=50,  # Get more filings for better coverage
            )

            for filing in filings:
                results["filings_checked"] += 1
                url = filing.get("linkToFilingDetails") or filing.get("linkToHtml")

                if not url:
                    continue

                try:
                    content = sec_client.get_filing_content(url)
                    if content:
                        bonds = extract_bonds_from_fwp(content)
                        all_bonds.extend(bonds)
                except Exception as e:
                    results["errors"].append(f"Error reading {form_type}: {str(e)[:50]}")

        except Exception as e:
            results["errors"].append(f"Error fetching {form_type}: {str(e)[:50]}")

    # Deduplicate by ISIN
    seen = set()
    unique_bonds = []
    for bond in all_bonds:
        if bond.isin not in seen:
            seen.add(bond.isin)
            unique_bonds.append(bond)

    results["bonds_extracted"] = unique_bonds

    # Match to instruments
    matches = []
    for bond in unique_bonds:
        inst = match_bond_to_instrument(bond, instruments)
        if inst:
            matches.append((bond, inst))
            results["bonds_matched"] += 1

    # Update database if saving
    if save and matches:
        for bond, inst in matches:
            inst.isin = bond.isin
            inst.cusip = bond.cusip
            # Update dates if we have better data from FWP
            if bond.maturity_date and not inst.maturity_date:
                inst.maturity_date = bond.maturity_date
            if bond.issue_date and not inst.issue_date:
                inst.issue_date = bond.issue_date
            # Update principal/outstanding if we have it and DB doesn't
            if bond.principal_amount and not inst.outstanding:
                principal_cents = bond.principal_amount * 100
                inst.principal = principal_cents
                inst.outstanding = principal_cents
            results["bonds_updated"] += 1

        await db.commit()

    # Create new debt instruments for unmatched bonds if requested
    if save and create_missing:
        matched_isins = {bond.isin for bond, _ in matches}
        # Also check existing ISINs in DB
        existing_isins = {inst.isin for inst in instruments if inst.isin}

        # Get parent entity (root entity without parent) to use as issuer
        # Use first() in case there are multiple root entities (e.g., dual-listed companies)
        parent_result = await db.execute(
            select(Entity)
            .where(Entity.company_id == company.id)
            .where(Entity.parent_id.is_(None))
            .limit(1)
        )
        parent_entity = parent_result.scalar_one_or_none()

        if not parent_entity:
            results["errors"].append("No parent entity found for creating bonds")
        else:
            for bond in unique_bonds:
                # Skip if already matched or already exists
                if bond.isin in matched_isins or bond.isin in existing_isins:
                    continue

                # Skip matured bonds (maturity year in the past)
                if bond.maturity_year < 2025:
                    continue

                # Create new debt instrument
                # Principal from FWP is original issuance, use as outstanding (may differ for older bonds)
                principal_cents = bond.principal_amount * 100 if bond.principal_amount else None
                new_inst = DebtInstrument(
                    company_id=company.id,
                    issuer_id=parent_entity.id,
                    name=f"{bond.coupon_rate:.3f}% Notes due {bond.maturity_year}",
                    instrument_type="senior_notes",
                    seniority="senior_unsecured",
                    security_type="unsecured",
                    currency="USD",
                    rate_type="fixed",
                    interest_rate=int(bond.coupon_rate * 100),
                    principal=principal_cents,
                    outstanding=principal_cents,  # Assume outstanding = principal at issuance
                    issue_date=bond.issue_date,
                    maturity_date=bond.maturity_date,
                    isin=bond.isin,
                    cusip=bond.cusip,
                    is_active=True,
                )
                db.add(new_inst)
                results["bonds_created"] += 1

            if results["bonds_created"] > 0:
                await db.commit()

    return results


async def main():
    parser = argparse.ArgumentParser(description="Extract ISINs from SEC filings")
    parser.add_argument("--ticker", help="Single ticker to process")
    parser.add_argument("--all", action="store_true", help="Process all companies with debt")
    parser.add_argument("--limit", type=int, default=200, help="Max companies to process")
    parser.add_argument("--save", action="store_true", help="Save matches to database")
    parser.add_argument("--create-missing", action="store_true",
                       help="Create new debt instruments for unmatched bonds from FWPs")
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
        elif args.all:
            # Get companies with debt instruments lacking ISINs
            from sqlalchemy import func
            result = await db.execute(
                select(Company)
                .join(DebtInstrument)
                .where(DebtInstrument.isin.is_(None))
                .where(DebtInstrument.is_active == True)
                .group_by(Company.id)
                .order_by(func.count(DebtInstrument.id).desc())
                .limit(args.limit)
            )
            companies = list(result.scalars().all())
        else:
            parser.print_help()
            return

        print(f"Processing {len(companies)} companies...")
        if args.save:
            print("(SAVE MODE - ISINs will be saved to database)")
            if args.create_missing:
                print("(CREATE MISSING - new bonds will be created for unmatched FWP bonds)")
        else:
            print("(DRY RUN - use --save to persist ISINs)")
        print()

        total_extracted = 0
        total_matched = 0
        total_updated = 0
        total_created = 0

        for i, company in enumerate(companies):
            print(f"[{i+1}/{len(companies)}] {company.ticker}: {company.name[:35]}...")

            result = await extract_isins_for_company(db, sec_client, company, args.save, args.create_missing)

            db_info = f"DB: {result['bond_instruments']} bonds"
            if result['non_bond_instruments'] > 0:
                db_info += f", {result['non_bond_instruments']} loans/other"

            if result['bonds_extracted']:
                created_info = f" | Created: {result['bonds_created']}" if result['bonds_created'] > 0 else ""
                print(f"  {db_info} | Filings: {result['filings_checked']} | Extracted: {len(result['bonds_extracted'])} | Matched: {result['bonds_matched']}{created_info}")
                for bond in result['bonds_extracted'][:5]:
                    print(f"    {bond.coupon_rate:.3f}% due {bond.maturity_year} | ISIN: {bond.isin}")
                if len(result['bonds_extracted']) > 5:
                    print(f"    ... and {len(result['bonds_extracted']) - 5} more")
            else:
                print(f"  {db_info} | No ISINs extracted from {result['filings_checked']} filings")

            if result['errors'] and len(result['errors']) <= 2:
                for err in result['errors']:
                    print(f"  Error: {err}")

            total_extracted += len(result['bonds_extracted'])
            total_matched += result['bonds_matched']
            total_updated += result['bonds_updated']
            total_created += result['bonds_created']

        print(f"\n{'='*60}")
        print(f"SUMMARY")
        print(f"{'='*60}")
        print(f"Companies processed: {len(companies)}")
        print(f"Total bonds extracted: {total_extracted}")
        print(f"Total matched to DB: {total_matched}")
        if args.save:
            print(f"Total ISINs saved: {total_updated}")
            if args.create_missing:
                print(f"Total bonds created: {total_created}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
