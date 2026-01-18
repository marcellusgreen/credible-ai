#!/usr/bin/env python3
"""
Backfill extraction_metadata table for all existing companies.

Creates metadata records based on existing data in:
- company_cache (extraction timestamps)
- company_financials (financial extraction dates)
- bond_pricing (pricing update dates)
- debt_instruments (uncertainties from issue_date_estimated)
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta
from uuid import uuid4

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.models import (
    Company, CompanyCache, CompanyFinancials, CompanyMetrics,
    DebtInstrument, BondPricing, ExtractionMetadata
)


async def backfill_metadata():
    """Backfill extraction metadata for all companies."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set")
        return

    # Convert to async URL
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # Get all companies
        result = await session.execute(select(Company))
        companies = result.scalars().all()
        print(f"Processing {len(companies)} companies...")

        created = 0
        updated = 0

        for company in companies:
            # Check if metadata already exists
            existing = await session.execute(
                select(ExtractionMetadata).where(ExtractionMetadata.company_id == company.id)
            )
            metadata = existing.scalar_one_or_none()

            if metadata:
                updated += 1
            else:
                metadata = ExtractionMetadata(
                    id=uuid4(),
                    company_id=company.id,
                    extraction_method="gemini",  # Default
                    extraction_attempts=1,
                    data_version=1,
                    stale_after_days=90,
                )
                session.add(metadata)
                created += 1

            # Get cache info for timestamps
            cache_result = await session.execute(
                select(CompanyCache).where(CompanyCache.company_id == company.id)
            )
            cache = cache_result.scalar_one_or_none()
            if cache:
                metadata.structure_extracted_at = cache.computed_at
                metadata.debt_extracted_at = cache.computed_at
                if cache.source_filing_date:
                    metadata.source_10k_date = cache.source_filing_date

            # Get latest financials extraction date
            fin_result = await session.execute(
                select(CompanyFinancials)
                .where(CompanyFinancials.company_id == company.id)
                .order_by(CompanyFinancials.extracted_at.desc())
                .limit(1)
            )
            financials = fin_result.scalar_one_or_none()
            if financials:
                metadata.financials_extracted_at = financials.extracted_at
                if financials.source_filing:
                    metadata.source_10q_url = financials.source_filing

            # Get latest pricing update
            pricing_result = await session.execute(
                select(func.max(BondPricing.fetched_at))
                .join(DebtInstrument, BondPricing.debt_instrument_id == DebtInstrument.id)
                .where(DebtInstrument.company_id == company.id)
            )
            latest_pricing = pricing_result.scalar()
            if latest_pricing:
                metadata.pricing_updated_at = latest_pricing

            # Calculate field confidence based on available data
            field_confidence = {}
            warnings = []

            # Check for estimated issue dates
            est_result = await session.execute(
                select(func.count(DebtInstrument.id))
                .where(
                    DebtInstrument.company_id == company.id,
                    DebtInstrument.issue_date_estimated == True
                )
            )
            estimated_count = est_result.scalar() or 0
            total_debt_result = await session.execute(
                select(func.count(DebtInstrument.id))
                .where(DebtInstrument.company_id == company.id)
            )
            total_debt = total_debt_result.scalar() or 0

            if total_debt > 0:
                debt_confidence = 1.0 - (estimated_count / total_debt) * 0.2
                field_confidence["debt_instruments"] = round(debt_confidence, 2)
                if estimated_count > 0:
                    warnings.append(f"{estimated_count} estimated issue dates")

            # Check for missing CUSIPs
            cusip_result = await session.execute(
                select(func.count(DebtInstrument.id))
                .where(
                    DebtInstrument.company_id == company.id,
                    DebtInstrument.cusip.is_(None),
                    DebtInstrument.is_active == True
                )
            )
            missing_cusips = cusip_result.scalar() or 0
            if missing_cusips > 0:
                warnings.append(f"{missing_cusips} missing CUSIPs")

            # Set QA score based on data completeness
            qa_score = 0.85  # Default baseline
            if cache:
                qa_score += 0.05
            if financials:
                qa_score += 0.05
            if total_debt > 0 and estimated_count == 0:
                qa_score += 0.05
            qa_score = min(qa_score, 1.0)

            metadata.qa_score = qa_score
            metadata.field_confidence = field_confidence
            metadata.warnings = warnings

        await session.commit()
        print(f"Created: {created}, Updated: {updated}")


if __name__ == "__main__":
    asyncio.run(backfill_metadata())
