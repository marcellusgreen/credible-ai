"""
CUSIP Mapping Service

Maps extracted debt instruments to CUSIPs using multiple strategies:
1. Extract CUSIP from ISIN (for US securities: ISIN[2:11])
2. Match via OpenFIGI using issuer ticker + coupon + maturity

OpenFIGI API Documentation: https://www.openfigi.com/api
Rate Limits: 5 req/min (free), 25 req/min (with API key)
"""

import asyncio
import os
import re
from datetime import date
from typing import Optional
from uuid import UUID

import httpx
from pydantic import BaseModel
from rapidfuzz import fuzz
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DebtInstrument, Company


# OpenFIGI API configuration
OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
OPENFIGI_API_KEY = os.getenv("OPENFIGI_API_KEY")  # Optional, increases rate limit

# Rate limiting
REQUESTS_PER_MINUTE = 25 if OPENFIGI_API_KEY else 5
REQUEST_DELAY = 60 / REQUESTS_PER_MINUTE + 0.1  # Seconds between requests


class CUSIPMappingResult(BaseModel):
    """Result of attempting to map a bond to a CUSIP."""

    debt_instrument_id: UUID
    bond_name: str
    success: bool
    cusip: Optional[str] = None
    isin: Optional[str] = None
    figi: Optional[str] = None
    method: Optional[str] = None  # "isin_extraction", "openfigi_match"
    match_score: float = 0.0
    error: Optional[str] = None


def extract_cusip_from_isin(isin: str) -> Optional[str]:
    """
    Extract CUSIP from a US ISIN.

    US ISIN format: US + 9-digit CUSIP + 1 check digit
    Example: US037833EP27 -> 037833EP2

    Returns CUSIP if valid US ISIN, None otherwise.
    """
    if not isin:
        return None

    isin = isin.strip().upper()

    # Must be 12 characters starting with US
    if len(isin) != 12:
        return None

    if not isin.startswith("US"):
        return None

    # Extract CUSIP: characters 2-11 (skip US prefix and check digit)
    cusip = isin[2:11]

    # Validate CUSIP format (9 alphanumeric characters)
    if not re.match(r'^[0-9A-Z]{9}$', cusip):
        return None

    return cusip


def parse_coupon_from_ticker(ticker: str) -> Optional[float]:
    """
    Parse coupon rate from OpenFIGI ticker format.

    Example: "AAPL 3.05 07/31/29" -> 3.05
    """
    if not ticker:
        return None

    parts = ticker.split()
    if len(parts) >= 2:
        try:
            return float(parts[1])
        except ValueError:
            pass
    return None


def parse_maturity_from_ticker(ticker: str) -> Optional[date]:
    """
    Parse maturity date from OpenFIGI ticker format.

    Example: "AAPL 3.05 07/31/29" -> 2029-07-31
    """
    if not ticker:
        return None

    # Look for date pattern MM/DD/YY
    match = re.search(r'(\d{2})/(\d{2})/(\d{2,4})$', ticker)
    if match:
        month, day, year = match.groups()
        year = int(year)
        if year < 100:
            # Convert 2-digit year (assume 20xx for years < 50, 19xx otherwise)
            year = 2000 + year if year < 50 else 1900 + year
        try:
            return date(year, int(month), int(day))
        except ValueError:
            pass
    return None


async def query_openfigi_by_ticker(
    ticker: str,
    security_type: str = "Corp",
) -> list[dict]:
    """
    Query OpenFIGI for bonds by base ticker.

    Returns list of bond matches with FIGI and descriptive info.
    """
    headers = {"Content-Type": "application/json"}
    if OPENFIGI_API_KEY:
        headers["X-OPENFIGI-APIKEY"] = OPENFIGI_API_KEY

    query = {
        "idType": "BASE_TICKER",
        "idValue": ticker.upper(),
        "securityType2": security_type,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(OPENFIGI_URL, json=[query], headers=headers)

        if resp.status_code == 429:
            raise Exception("OpenFIGI rate limit exceeded")

        resp.raise_for_status()
        results = resp.json()

    if results and results[0].get("data"):
        return results[0]["data"]
    return []


def match_bond_to_figi(
    bond: DebtInstrument,
    figi_bonds: list[dict],
    company_name: str,
) -> Optional[tuple[dict, float]]:
    """
    Find best matching FIGI bond for a debt instrument.

    Returns (best_match, score) or None if no good match.
    """
    if not figi_bonds:
        return None

    # Get bond characteristics
    bond_coupon = bond.interest_rate / 100 if bond.interest_rate else None
    bond_maturity = bond.maturity_date

    best_match = None
    best_score = 0.0

    for figi_bond in figi_bonds:
        score = 0.0
        ticker = figi_bond.get("ticker", "")

        # Parse coupon and maturity from FIGI ticker
        figi_coupon = parse_coupon_from_ticker(ticker)
        figi_maturity = parse_maturity_from_ticker(ticker)

        # Score coupon match (40 points max)
        if bond_coupon and figi_coupon:
            coupon_diff = abs(bond_coupon - figi_coupon)
            if coupon_diff < 0.01:  # Exact match
                score += 40
            elif coupon_diff < 0.1:  # Very close
                score += 35
            elif coupon_diff < 0.25:  # Close
                score += 25
            elif coupon_diff < 0.5:
                score += 15

        # Score maturity match (40 points max)
        if bond_maturity and figi_maturity:
            days_diff = abs((bond_maturity - figi_maturity).days)
            if days_diff < 7:  # Within a week
                score += 40
            elif days_diff < 30:  # Within a month
                score += 35
            elif days_diff < 90:  # Within 3 months
                score += 25
            elif days_diff < 365:  # Within a year
                score += 15

        # Score issuer name match (20 points max)
        figi_name = figi_bond.get("name", "")
        name_similarity = fuzz.token_set_ratio(company_name.lower(), figi_name.lower())
        score += (name_similarity / 100) * 20

        if score > best_score:
            best_score = score
            best_match = figi_bond

    if best_match and best_score >= 50:  # Minimum threshold
        return best_match, best_score

    return None


async def map_bond_to_cusip(
    bond: DebtInstrument,
    company_name: str,
    company_ticker: str,
    figi_cache: Optional[list[dict]] = None,
) -> CUSIPMappingResult:
    """
    Map a single bond to a CUSIP using available strategies.

    Strategy priority:
    1. If bond already has CUSIP, return it
    2. If bond has ISIN, extract CUSIP from it
    3. Match via OpenFIGI using ticker + coupon + maturity
    """
    # Strategy 1: Already has CUSIP
    if bond.cusip:
        return CUSIPMappingResult(
            debt_instrument_id=bond.id,
            bond_name=bond.name,
            success=True,
            cusip=bond.cusip,
            isin=bond.isin,
            method="existing",
            match_score=100.0,
        )

    # Strategy 2: Extract from ISIN
    if bond.isin:
        cusip = extract_cusip_from_isin(bond.isin)
        if cusip:
            return CUSIPMappingResult(
                debt_instrument_id=bond.id,
                bond_name=bond.name,
                success=True,
                cusip=cusip,
                isin=bond.isin,
                method="isin_extraction",
                match_score=100.0,
            )

    # Strategy 3: OpenFIGI matching (for bonds with coupon and maturity)
    if bond.interest_rate and bond.maturity_date:
        try:
            # Use cache or fetch
            if figi_cache is None:
                figi_bonds = await query_openfigi_by_ticker(company_ticker)
            else:
                figi_bonds = figi_cache

            result = match_bond_to_figi(bond, figi_bonds, company_name)
            if result:
                figi_bond, score = result
                # Note: OpenFIGI doesn't return CUSIP, only FIGI
                # But we can store the FIGI for reference
                return CUSIPMappingResult(
                    debt_instrument_id=bond.id,
                    bond_name=bond.name,
                    success=False,  # No CUSIP, but we have FIGI
                    figi=figi_bond.get("figi"),
                    method="openfigi_match",
                    match_score=score,
                    error="FIGI found but CUSIP not available (proprietary)",
                )
        except Exception as e:
            return CUSIPMappingResult(
                debt_instrument_id=bond.id,
                bond_name=bond.name,
                success=False,
                error=f"OpenFIGI error: {str(e)}",
            )

    return CUSIPMappingResult(
        debt_instrument_id=bond.id,
        bond_name=bond.name,
        success=False,
        error="No ISIN available and insufficient data for OpenFIGI match",
    )


async def map_company_bonds(
    session: AsyncSession,
    ticker: str,
    dry_run: bool = False,
) -> list[CUSIPMappingResult]:
    """
    Map all unmapped bonds for a company.

    Args:
        session: Database session
        ticker: Company ticker
        dry_run: If True, don't save to database

    Returns:
        List of mapping results
    """
    # Get company
    result = await session.execute(
        select(Company).where(Company.ticker == ticker.upper())
    )
    company = result.scalar_one_or_none()

    if not company:
        raise ValueError(f"Company not found: {ticker}")

    # Get tradeable bonds (notes/bonds, not loans)
    tradeable_types = [
        "senior_notes", "notes", "bonds", "debentures",
        "convertible_notes", "senior_secured_notes", "subordinated_notes"
    ]

    result = await session.execute(
        select(DebtInstrument)
        .where(DebtInstrument.company_id == company.id)
        .where(DebtInstrument.instrument_type.in_(tradeable_types))
        .where(DebtInstrument.is_active == True)
    )
    bonds = list(result.scalars().all())

    if not bonds:
        return []

    # Pre-fetch OpenFIGI data for company (one API call)
    try:
        figi_cache = await query_openfigi_by_ticker(ticker)
    except Exception:
        figi_cache = []

    # Map each bond
    results = []
    for bond in bonds:
        result = await map_bond_to_cusip(
            bond=bond,
            company_name=company.name,
            company_ticker=ticker,
            figi_cache=figi_cache,
        )
        results.append(result)

    # Update database if not dry run
    if not dry_run:
        updated = 0
        for result in results:
            if result.success and result.cusip:
                stmt = (
                    update(DebtInstrument)
                    .where(DebtInstrument.id == result.debt_instrument_id)
                    .values(
                        cusip=result.cusip,
                        isin=result.isin if result.isin else DebtInstrument.isin,
                    )
                )
                await session.execute(stmt)
                updated += 1

        if updated > 0:
            await session.commit()

    return results


async def get_unmapped_bonds(
    session: AsyncSession,
    ticker: Optional[str] = None,
    limit: int = 100,
) -> list[tuple[DebtInstrument, str, str]]:
    """
    Get tradeable bonds without CUSIPs.

    Returns list of (DebtInstrument, company_name, company_ticker) tuples.
    """
    tradeable_types = [
        "senior_notes", "notes", "bonds", "debentures",
        "convertible_notes", "senior_secured_notes", "subordinated_notes"
    ]

    query = (
        select(DebtInstrument, Company.name, Company.ticker)
        .join(Company)
        .where(DebtInstrument.cusip.is_(None))
        .where(DebtInstrument.instrument_type.in_(tradeable_types))
        .where(DebtInstrument.is_active == True)
        .limit(limit)
    )

    if ticker:
        query = query.where(Company.ticker == ticker.upper())

    result = await session.execute(query)
    return [(row[0], row[1], row[2]) for row in result.fetchall()]
