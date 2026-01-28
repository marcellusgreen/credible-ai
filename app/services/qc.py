"""
Quality Control Service for DebtStack.ai

Runs validation checks on extracted data.
"""

from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entity, DebtInstrument, Guarantee, CompanyFinancials


async def run_qc_checks(
    session: AsyncSession,
    company_id: UUID,
    ticker: str
) -> dict:
    """
    Run basic QC checks on the extracted data.

    Args:
        session: Database session
        company_id: Company UUID
        ticker: Stock ticker

    Returns:
        Dict with counts and issues list
    """
    issues = []

    # Check for entities
    result = await session.execute(
        select(func.count()).select_from(Entity).where(Entity.company_id == company_id)
    )
    entity_count = result.scalar()
    if entity_count == 0:
        issues.append("No entities extracted")

    # Check for holdco
    result = await session.execute(
        select(Entity).where(Entity.company_id == company_id, Entity.is_root == True)
    )
    if not result.scalar_one_or_none():
        issues.append("No root entity (holdco) identified")

    # Check for debt instruments
    result = await session.execute(
        select(func.count()).select_from(DebtInstrument).where(DebtInstrument.company_id == company_id)
    )
    debt_count = result.scalar()
    if debt_count == 0:
        issues.append("No debt instruments extracted")

    # Check for guarantees
    result = await session.execute(
        select(func.count()).select_from(Guarantee)
        .join(DebtInstrument)
        .where(DebtInstrument.company_id == company_id)
    )
    guarantee_count = result.scalar()

    # Check for financials
    result = await session.execute(
        select(func.count()).select_from(CompanyFinancials).where(CompanyFinancials.company_id == company_id)
    )
    financial_count = result.scalar()

    return {
        "entities": entity_count,
        "debt_instruments": debt_count,
        "guarantees": guarantee_count,
        "financials": financial_count,
        "issues": issues,
        "passed": len(issues) == 0,
    }
