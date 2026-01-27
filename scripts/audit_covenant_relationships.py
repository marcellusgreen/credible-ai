#!/usr/bin/env python3
"""
Audit covenant relationship extraction data quality.

Checks:
1. Unrestricted subsidiary counts and distribution
2. Cross-default link symmetry and validity
3. Guarantee condition completeness
4. Non-guarantor disclosure percentage validity

Usage:
    python scripts/audit_covenant_relationships.py
    python scripts/audit_covenant_relationships.py --verbose
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models import (
    Company,
    CompanyMetrics,
    CrossDefaultLink,
    DebtInstrument,
    Entity,
    Guarantee,
)


async def audit_unrestricted_subsidiaries(db: AsyncSession, verbose: bool = False):
    """Audit unrestricted subsidiary data."""
    print("\n" + "=" * 60)
    print("UNRESTRICTED SUBSIDIARIES")
    print("=" * 60)

    # Total count
    total = await db.scalar(
        select(func.count(Entity.id)).where(Entity.is_unrestricted == True)
    )
    print(f"Total unrestricted subsidiaries: {total}")

    # By company
    result = await db.execute(
        select(Company.ticker, Company.name, func.count(Entity.id).label("count"))
        .join(Entity, Entity.company_id == Company.id)
        .where(Entity.is_unrestricted == True)
        .group_by(Company.id)
        .order_by(func.count(Entity.id).desc())
        .limit(20)
    )

    companies_with_unrestricted = list(result)
    print(f"Companies with unrestricted subs: {len(companies_with_unrestricted)}")

    if verbose:
        print("\nTop 20 companies by unrestricted sub count:")
        for ticker, name, count in companies_with_unrestricted:
            print(f"  {ticker}: {count} ({name[:40]})")

    # Check for potential issues
    result = await db.execute(
        select(Entity.name, Company.ticker)
        .join(Company, Entity.company_id == Company.id)
        .where(Entity.is_unrestricted == True)
        .where(Entity.is_guarantor == True)
    )
    unrestricted_guarantors = list(result)

    if unrestricted_guarantors:
        print(f"\nWARNING: {len(unrestricted_guarantors)} entities are both unrestricted AND guarantors")
        if verbose:
            for name, ticker in unrestricted_guarantors[:10]:
                print(f"  [{ticker}] {name[:50]}")


async def audit_cross_default_links(db: AsyncSession, verbose: bool = False):
    """Audit cross-default link data."""
    print("\n" + "=" * 60)
    print("CROSS-DEFAULT LINKS")
    print("=" * 60)

    # Total count by type
    result = await db.execute(
        select(
            CrossDefaultLink.relationship_type,
            func.count(CrossDefaultLink.id).label("count")
        )
        .group_by(CrossDefaultLink.relationship_type)
    )

    for rel_type, count in result:
        print(f"{rel_type}: {count}")

    # Total
    total = await db.scalar(select(func.count(CrossDefaultLink.id)))
    print(f"Total cross-default links: {total}")

    # By company (via source debt)
    result = await db.execute(
        select(Company.ticker, func.count(CrossDefaultLink.id).label("count"))
        .join(DebtInstrument, CrossDefaultLink.source_debt_id == DebtInstrument.id)
        .join(Company, DebtInstrument.company_id == Company.id)
        .group_by(Company.id)
        .order_by(func.count(CrossDefaultLink.id).desc())
        .limit(20)
    )

    companies_with_links = list(result)
    print(f"Companies with cross-default links: {len(companies_with_links)}")

    if verbose:
        print("\nTop 20 companies by cross-default link count:")
        for ticker, count in companies_with_links:
            print(f"  {ticker}: {count}")

    # Check bilateral symmetry
    bilateral = await db.execute(
        select(CrossDefaultLink)
        .where(CrossDefaultLink.is_bilateral == True)
        .where(CrossDefaultLink.target_debt_id.isnot(None))
    )

    bilateral_links = list(bilateral.scalars())
    print(f"\nBilateral links: {len(bilateral_links)}")

    # Check for missing reciprocal links
    missing_reciprocal = 0
    for link in bilateral_links:
        reciprocal = await db.scalar(
            select(CrossDefaultLink).where(
                CrossDefaultLink.source_debt_id == link.target_debt_id,
                CrossDefaultLink.target_debt_id == link.source_debt_id,
                CrossDefaultLink.relationship_type == link.relationship_type,
            )
        )
        if not reciprocal:
            missing_reciprocal += 1

    if missing_reciprocal:
        print(f"WARNING: {missing_reciprocal} bilateral links missing reciprocal entry")

    # Threshold amount distribution
    result = await db.execute(
        select(CrossDefaultLink.threshold_amount)
        .where(CrossDefaultLink.threshold_amount.isnot(None))
    )

    thresholds = [r[0] for r in result if r[0]]
    if thresholds:
        min_threshold = min(thresholds) / 100  # Convert cents to dollars
        max_threshold = max(thresholds) / 100
        avg_threshold = sum(thresholds) / len(thresholds) / 100
        print(f"\nThreshold amounts (where specified):")
        print(f"  Min: ${min_threshold:,.0f}")
        print(f"  Max: ${max_threshold:,.0f}")
        print(f"  Avg: ${avg_threshold:,.0f}")


async def audit_guarantee_conditions(db: AsyncSession, verbose: bool = False):
    """Audit guarantee conditions data."""
    print("\n" + "=" * 60)
    print("GUARANTEE CONDITIONS")
    print("=" * 60)

    # Count guarantees with conditions
    # conditions != '{}' check
    result = await db.execute(
        text("SELECT COUNT(*) FROM guarantees WHERE conditions IS NOT NULL AND conditions != '{}'")
    )
    with_conditions = result.scalar()

    total = await db.scalar(select(func.count(Guarantee.id)))

    print(f"Guarantees with conditions: {with_conditions} / {total} ({100*with_conditions/total:.1f}%)")

    # Sample some conditions
    if verbose:
        result = await db.execute(
            text("""
                SELECT g.id, g.conditions, d.name as debt_name, e.name as entity_name
                FROM guarantees g
                JOIN debt_instruments d ON g.debt_instrument_id = d.id
                JOIN entities e ON g.guarantor_id = e.id
                WHERE g.conditions IS NOT NULL AND g.conditions != '{}'
                LIMIT 10
            """)
        )

        print("\nSample guarantee conditions:")
        for row in result:
            print(f"  {row.debt_name[:30]} <- {row.entity_name[:30]}")
            print(f"    {row.conditions}")


async def audit_non_guarantor_disclosure(db: AsyncSession, verbose: bool = False):
    """Audit non-guarantor disclosure data."""
    print("\n" + "=" * 60)
    print("NON-GUARANTOR DISCLOSURE")
    print("=" * 60)

    # Count companies with disclosure
    result = await db.execute(
        text("SELECT COUNT(*) FROM company_metrics WHERE non_guarantor_disclosure IS NOT NULL")
    )
    with_disclosure = result.scalar()

    total = await db.scalar(select(func.count(CompanyMetrics.ticker)))

    print(f"Companies with non-guarantor disclosure: {with_disclosure} / {total} ({100*with_disclosure/total:.1f}%)")

    # Check percentage validity
    result = await db.execute(
        text("""
            SELECT ticker, non_guarantor_disclosure
            FROM company_metrics
            WHERE non_guarantor_disclosure IS NOT NULL
        """)
    )

    invalid_percentages = []
    for row in result:
        disclosure = row.non_guarantor_disclosure
        if disclosure:
            for key in ['ebitda_pct', 'assets_pct', 'revenue_pct']:
                pct = disclosure.get(key)
                if pct is not None and (pct < 0 or pct > 100):
                    invalid_percentages.append((row.ticker, key, pct))

    if invalid_percentages:
        print(f"\nWARNING: {len(invalid_percentages)} invalid percentages (outside 0-100 range)")
        if verbose:
            for ticker, key, pct in invalid_percentages:
                print(f"  [{ticker}] {key}: {pct}")

    # Sample disclosures
    if verbose:
        result = await db.execute(
            text("""
                SELECT ticker, non_guarantor_disclosure
                FROM company_metrics
                WHERE non_guarantor_disclosure IS NOT NULL
                LIMIT 10
            """)
        )

        print("\nSample non-guarantor disclosures:")
        for row in result:
            print(f"  {row.ticker}: {row.non_guarantor_disclosure}")


async def generate_summary_queries(db: AsyncSession):
    """Print SQL queries for verification."""
    print("\n" + "=" * 60)
    print("VERIFICATION QUERIES")
    print("=" * 60)

    queries = [
        ("Unrestricted subs", "SELECT COUNT(*) FROM entities WHERE is_unrestricted = true;"),
        ("Guarantees with conditions", "SELECT COUNT(*) FROM guarantees WHERE conditions != '{}';"),
        ("Cross-default links", "SELECT COUNT(*) FROM cross_default_links;"),
        ("Non-guarantor disclosure", "SELECT COUNT(*) FROM company_metrics WHERE non_guarantor_disclosure IS NOT NULL;"),
    ]

    print("\nRun these queries to verify extraction results:\n")
    for name, query in queries:
        result = await db.execute(text(query))
        count = result.scalar()
        print(f"-- {name}: {count}")
        print(f"{query}\n")


async def main():
    parser = argparse.ArgumentParser(description="Audit covenant relationship data quality")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")

    args = parser.parse_args()

    settings = get_settings()

    engine = create_async_engine(
        settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
    )
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    print("=" * 60)
    print("COVENANT RELATIONSHIP DATA AUDIT")
    print("=" * 60)

    async with async_session() as db:
        await audit_unrestricted_subsidiaries(db, args.verbose)
        await audit_cross_default_links(db, args.verbose)
        await audit_guarantee_conditions(db, args.verbose)
        await audit_non_guarantor_disclosure(db, args.verbose)
        await generate_summary_queries(db)

    print("\n" + "=" * 60)
    print("AUDIT COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
