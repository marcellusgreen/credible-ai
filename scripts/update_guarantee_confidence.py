#!/usr/bin/env python3
"""
Update guarantee_data_confidence for debt instruments based on data quality.

Confidence levels:
- verified: From Exhibit 22.1 (SEC-mandated guarantor list)
- extracted: From LLM extraction with cross-referenced documents
- partial: Some guarantee data but may be incomplete
- unknown: No guarantee analysis performed

Usage:
    python scripts/update_guarantee_confidence.py [--verbose]
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models import Company, DebtInstrument, Guarantee


async def update_confidence_levels(verbose: bool = False):
    """Update guarantee_data_confidence based on actual data quality."""
    settings = get_settings()
    url = settings.database_url.replace('postgresql://', 'postgresql+asyncpg://', 1)
    engine = create_async_engine(url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    stats = {
        "verified": 0,
        "extracted": 0,
        "partial": 0,
        "unknown": 0,
    }

    async with async_session() as db:
        # Get all companies with their debt and guarantee stats
        result = await db.execute(text('''
            WITH debt_stats AS (
                SELECT
                    d.company_id,
                    d.id as debt_id,
                    d.seniority,
                    COUNT(g.id) as guarantee_count,
                    -- Check if company has Exhibit 22 data
                    EXISTS(
                        SELECT 1 FROM document_sections ds
                        WHERE ds.company_id = d.company_id
                        AND ds.section_type = 'exhibit_21'
                        AND LENGTH(ds.content) > 5000
                    ) as has_exhibit_data
                FROM debt_instruments d
                LEFT JOIN guarantees g ON g.debt_instrument_id = d.id
                WHERE d.is_active = true
                GROUP BY d.company_id, d.id, d.seniority
            )
            SELECT
                c.ticker,
                ds.debt_id,
                ds.seniority,
                ds.guarantee_count,
                ds.has_exhibit_data,
                (SELECT COUNT(*) FROM entities e WHERE e.company_id = c.id AND e.is_guarantor = true) as guarantor_entities
            FROM debt_stats ds
            JOIN companies c ON c.id = ds.company_id
            ORDER BY c.ticker
        '''))

        rows = result.fetchall()
        updates = []

        for row in rows:
            ticker, debt_id, seniority, guarantee_count, has_exhibit_data, guarantor_entities = row

            # Determine confidence level
            if seniority == 'senior_unsecured' and guarantee_count == 0:
                # Senior unsecured may legitimately have no guarantees
                confidence = 'extracted'
            elif has_exhibit_data and guarantee_count > 0 and guarantor_entities >= 10:
                # Has Exhibit 22 data and multiple guarantor entities
                confidence = 'verified'
            elif guarantee_count > 0:
                # Has some guarantees but not verified from Exhibit 22
                confidence = 'extracted'
            elif seniority == 'senior_secured' and guarantee_count == 0:
                # Secured debt should have guarantees but doesn't
                confidence = 'partial'
            else:
                confidence = 'unknown'

            updates.append((debt_id, confidence))
            stats[confidence] += 1

            if verbose:
                print(f"  {ticker}: {seniority[:20]:<20} -> {confidence} (guarantees: {guarantee_count})")

        # Batch update
        for debt_id, confidence in updates:
            await db.execute(
                update(DebtInstrument)
                .where(DebtInstrument.id == debt_id)
                .values(guarantee_data_confidence=confidence)
            )

        await db.commit()

    await engine.dispose()

    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    print(f"Verified:  {stats['verified']:>5} (from Exhibit 22, high confidence)")
    print(f"Extracted: {stats['extracted']:>5} (from LLM, medium confidence)")
    print(f"Partial:   {stats['partial']:>5} (incomplete data)")
    print(f"Unknown:   {stats['unknown']:>5} (no analysis)")
    print(f"Total:     {sum(stats.values()):>5}")


async def main():
    parser = argparse.ArgumentParser(description="Update guarantee data confidence levels")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    print("Updating guarantee_data_confidence levels...")
    await update_confidence_levels(verbose=args.verbose)


if __name__ == "__main__":
    asyncio.run(main())
