#!/usr/bin/env python3
"""
Create point-in-time snapshots of company data for historical tracking.

Usage:
    # Create snapshot for all companies (quarterly)
    python scripts/create_snapshot.py --all --type quarterly

    # Create snapshot for specific ticker
    python scripts/create_snapshot.py --ticker AAPL

    # Create monthly snapshot
    python scripts/create_snapshot.py --all --type monthly

    # Dry run (preview without saving)
    python scripts/create_snapshot.py --all --dry-run
"""

import argparse
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from script_utils import get_db_session, print_header, run_async
from app.models import (
    Company, CompanyMetrics, CompanyFinancials, CompanySnapshot,
    Entity, DebtInstrument
)


def serialize_for_json(obj):
    """Convert objects to JSON-serializable format."""
    if obj is None:
        return None
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: serialize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [serialize_for_json(v) for v in obj]
    return obj


async def create_company_snapshot(
    session: AsyncSession,
    company: Company,
    snapshot_type: str = "quarterly",
    snapshot_date: date = None,
    dry_run: bool = False
) -> dict:
    """Create a snapshot for a single company."""
    if snapshot_date is None:
        snapshot_date = date.today()

    # Check if snapshot already exists for this date
    existing = await session.execute(
        select(CompanySnapshot).where(
            CompanySnapshot.company_id == company.id,
            CompanySnapshot.snapshot_date == snapshot_date
        )
    )
    if existing.scalar_one_or_none():
        return {"status": "skipped", "reason": "snapshot already exists"}

    # Get entities
    entities_result = await session.execute(
        select(Entity).where(Entity.company_id == company.id)
    )
    entities = entities_result.scalars().all()

    entities_snapshot = []
    guarantor_count = 0
    for e in entities:
        entities_snapshot.append({
            "id": str(e.id),
            "name": e.name,
            "entity_type": e.entity_type,
            "is_guarantor": e.is_guarantor,
            "is_borrower": e.is_borrower,
            "parent_id": str(e.parent_id) if e.parent_id else None,
            "jurisdiction": e.jurisdiction,
        })
        if e.is_guarantor:
            guarantor_count += 1

    # Get debt instruments
    debt_result = await session.execute(
        select(DebtInstrument).where(
            DebtInstrument.company_id == company.id,
            DebtInstrument.is_active == True
        )
    )
    debt_instruments = debt_result.scalars().all()

    debt_snapshot = []
    total_debt = 0
    for d in debt_instruments:
        debt_snapshot.append({
            "id": str(d.id),
            "name": d.name,
            "cusip": d.cusip,
            "instrument_type": d.instrument_type,
            "seniority": d.seniority,
            "principal": d.principal,
            "outstanding": d.outstanding,
            "interest_rate": d.interest_rate,
            "maturity_date": serialize_for_json(d.maturity_date),
        })
        if d.outstanding:
            total_debt += d.outstanding
        elif d.principal:
            total_debt += d.principal

    # Get metrics
    metrics_result = await session.execute(
        select(CompanyMetrics).where(CompanyMetrics.company_id == company.id)
    )
    metrics = metrics_result.scalar_one_or_none()

    metrics_snapshot = None
    if metrics:
        metrics_snapshot = {
            "total_debt": metrics.total_debt,
            "secured_debt": metrics.secured_debt,
            "unsecured_debt": metrics.unsecured_debt,
            "net_debt": metrics.net_debt,
            "leverage_ratio": serialize_for_json(metrics.leverage_ratio),
            "net_leverage_ratio": serialize_for_json(metrics.net_leverage_ratio),
            "interest_coverage": serialize_for_json(metrics.interest_coverage),
            "has_structural_sub": metrics.has_structural_sub,
            "subordination_risk": metrics.subordination_risk,
            "nearest_maturity": serialize_for_json(metrics.nearest_maturity),
        }

    # Get latest financials
    financials_result = await session.execute(
        select(CompanyFinancials)
        .where(CompanyFinancials.company_id == company.id)
        .order_by(CompanyFinancials.period_end_date.desc())
        .limit(1)
    )
    financials = financials_result.scalar_one_or_none()

    financials_snapshot = None
    if financials:
        financials_snapshot = {
            "fiscal_year": financials.fiscal_year,
            "fiscal_quarter": financials.fiscal_quarter,
            "period_end_date": serialize_for_json(financials.period_end_date),
            "revenue": financials.revenue,
            "ebitda": financials.ebitda,
            "net_income": financials.net_income,
            "total_assets": financials.total_assets,
            "total_debt": financials.total_debt,
            "cash_and_equivalents": financials.cash_and_equivalents,
        }

    if dry_run:
        return {
            "status": "dry_run",
            "ticker": company.ticker,
            "entity_count": len(entities_snapshot),
            "debt_count": len(debt_snapshot),
            "total_debt": total_debt,
            "guarantor_count": guarantor_count,
        }

    # Create snapshot
    snapshot = CompanySnapshot(
        id=uuid4(),
        company_id=company.id,
        ticker=company.ticker,
        snapshot_date=snapshot_date,
        snapshot_type=snapshot_type,
        entities_snapshot=entities_snapshot,
        debt_snapshot=debt_snapshot,
        metrics_snapshot=metrics_snapshot,
        financials_snapshot=financials_snapshot,
        entity_count=len(entities_snapshot),
        debt_instrument_count=len(debt_snapshot),
        total_debt=total_debt,
        guarantor_count=guarantor_count,
    )
    session.add(snapshot)

    return {
        "status": "created",
        "ticker": company.ticker,
        "entity_count": len(entities_snapshot),
        "debt_count": len(debt_snapshot),
        "total_debt": total_debt,
        "guarantor_count": guarantor_count,
    }


async def main():
    parser = argparse.ArgumentParser(description="Create company snapshots")
    parser.add_argument("--ticker", help="Single ticker to snapshot")
    parser.add_argument("--all", action="store_true", help="Snapshot all companies")
    parser.add_argument("--type", default="quarterly", choices=["quarterly", "monthly", "manual"],
                        help="Snapshot type (default: quarterly)")
    parser.add_argument("--date", help="Snapshot date (YYYY-MM-DD, default: today)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    args = parser.parse_args()

    if not args.ticker and not args.all:
        parser.error("Either --ticker or --all is required")

    # Parse date
    snapshot_date = date.today()
    if args.date:
        snapshot_date = date.fromisoformat(args.date)

    print_header("CREATE COMPANY SNAPSHOTS")

    async with get_db_session() as session:
        # Get companies to snapshot
        if args.ticker:
            result = await session.execute(
                select(Company).where(Company.ticker == args.ticker.upper())
            )
            companies = [result.scalar_one_or_none()]
            if not companies[0]:
                print(f"Company {args.ticker} not found")
                return
        else:
            result = await session.execute(select(Company).order_by(Company.ticker))
            companies = result.scalars().all()

        print(f"Creating {args.type} snapshots for {len(companies)} companies...")
        print(f"Snapshot date: {snapshot_date}")
        print("-" * 60)

        created = 0
        skipped = 0

        for company in companies:
            result = await create_company_snapshot(
                session, company,
                snapshot_type=args.type,
                snapshot_date=snapshot_date,
                dry_run=args.dry_run
            )

            status = result["status"]
            if status == "created" or status == "dry_run":
                created += 1
                print(f"[{company.ticker}] {status}: {result['entity_count']} entities, "
                      f"{result['debt_count']} debt instruments, "
                      f"${result['total_debt'] / 100_000_000_000:.1f}B debt")
            else:
                skipped += 1
                print(f"[{company.ticker}] {status}: {result.get('reason', '')}")

        if not args.dry_run:
            await session.commit()

        print("-" * 60)
        print(f"Created: {created}, Skipped: {skipped}")


if __name__ == "__main__":
    run_async(main())
