"""
Ground Truth Data Management

Loads and manages ground truth datasets for eval validation.

Ground Truth Strategy:
1. Tier 1 (Database-Verified): Direct DB queries for extracted SEC data
2. Tier 2 (Cross-Table Consistency): Recalculate from component data
3. Tier 3 (Document Text Verification): Full-text search in source documents
"""

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

# Import models for ground truth queries
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.models import (
    Company, CompanyMetrics, CompanyFinancials,
    DebtInstrument, BondPricing, Entity, Guarantee,
    Collateral, Covenant, DocumentSection,
)


@dataclass
class GroundTruth:
    """Container for ground truth data with source tracking."""
    value: Any
    source: str  # e.g., "company_financials.ttm_ebitda", "debt_instruments.sum"
    tier: int  # 1=DB-verified, 2=cross-table, 3=document
    confidence: float = 1.0


class GroundTruthManager:
    """Manages ground truth data retrieval from database."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # =========================================================================
    # COMPANY GROUND TRUTH (Tier 1 & 2)
    # =========================================================================

    async def get_company_leverage(self, ticker: str) -> Optional[GroundTruth]:
        """
        Get company leverage from stored metrics.
        Tier 1: Direct from company_metrics table.
        """
        result = await self.db.execute(
            select(CompanyMetrics).where(CompanyMetrics.ticker == ticker.upper())
        )
        metrics = result.scalar_one_or_none()
        if not metrics or not metrics.net_leverage_ratio:
            return None

        return GroundTruth(
            value=float(metrics.net_leverage_ratio),
            source="company_metrics.net_leverage_ratio",
            tier=1,
        )

    async def get_company_total_debt(self, ticker: str) -> Optional[GroundTruth]:
        """
        Get company total debt from metrics.
        Tier 1: Direct from company_metrics table.
        """
        result = await self.db.execute(
            select(CompanyMetrics).where(CompanyMetrics.ticker == ticker.upper())
        )
        metrics = result.scalar_one_or_none()
        if not metrics or not metrics.total_debt:
            return None

        return GroundTruth(
            value=metrics.total_debt,
            source="company_metrics.total_debt",
            tier=1,
        )

    async def calculate_debt_sum_from_instruments(self, ticker: str) -> Optional[GroundTruth]:
        """
        Calculate total debt by summing active debt instruments.
        Tier 2: Cross-table consistency check.
        """
        # Get company ID
        company_result = await self.db.execute(
            select(Company.id).where(Company.ticker == ticker.upper())
        )
        company_id = company_result.scalar_one_or_none()
        if not company_id:
            return None

        # Sum outstanding amounts from active instruments
        sum_result = await self.db.execute(
            select(func.sum(DebtInstrument.outstanding))
            .where(
                DebtInstrument.company_id == company_id,
                DebtInstrument.is_active == True,
            )
        )
        total = sum_result.scalar()
        if total is None:
            return None

        return GroundTruth(
            value=int(total),
            source="sum(debt_instruments.outstanding)",
            tier=2,
        )

    async def get_company_cash(self, ticker: str) -> Optional[GroundTruth]:
        """
        Get company cash from latest financials.
        Tier 1: Direct from company_financials table.
        """
        company_result = await self.db.execute(
            select(Company.id).where(Company.ticker == ticker.upper())
        )
        company_id = company_result.scalar_one_or_none()
        if not company_id:
            return None

        # Get most recent quarterly financials
        fin_result = await self.db.execute(
            select(CompanyFinancials)
            .where(CompanyFinancials.company_id == company_id)
            .order_by(CompanyFinancials.period_end_date.desc())
            .limit(1)
        )
        financials = fin_result.scalar_one_or_none()
        if not financials or not financials.cash_and_equivalents:
            return None

        return GroundTruth(
            value=financials.cash_and_equivalents,
            source="company_financials.cash_and_equivalents",
            tier=1,
        )

    # =========================================================================
    # BOND GROUND TRUTH (Tier 1)
    # =========================================================================

    async def get_bond_by_cusip(self, cusip: str) -> Optional[dict]:
        """
        Get bond details by CUSIP.
        Returns dict with all ground truth fields.
        """
        result = await self.db.execute(
            select(DebtInstrument, Company)
            .join(Company, DebtInstrument.company_id == Company.id)
            .where(DebtInstrument.cusip == cusip.upper())
        )
        row = result.first()
        if not row:
            return None

        debt, company = row
        return {
            "cusip": debt.cusip,
            "name": debt.name,
            "ticker": company.ticker,
            "interest_rate": debt.interest_rate,  # in bps
            "maturity_date": debt.maturity_date,
            "outstanding": debt.outstanding,
            "seniority": debt.seniority,
            "instrument_type": debt.instrument_type,
        }

    async def get_bond_pricing(self, cusip: str) -> Optional[GroundTruth]:
        """
        Get bond pricing data.
        Tier 1: Direct from bond_pricing table.
        """
        result = await self.db.execute(
            select(BondPricing).where(BondPricing.cusip == cusip.upper())
        )
        pricing = result.scalar_one_or_none()
        if not pricing:
            return None

        return GroundTruth(
            value={
                "last_price": float(pricing.last_price) if pricing.last_price else None,
                "ytm_bps": pricing.ytm_bps,
                "last_trade_date": pricing.last_trade_date,
                "spread_bps": pricing.spread_to_treasury_bps,
            },
            source="bond_pricing",
            tier=1,
        )

    # =========================================================================
    # ENTITY GROUND TRUTH (Tier 1)
    # =========================================================================

    async def get_guarantor_count(self, ticker: str) -> Optional[GroundTruth]:
        """
        Count guarantor entities for a company.
        Tier 1: Direct from entities + guarantees tables.
        """
        company_result = await self.db.execute(
            select(Company.id).where(Company.ticker == ticker.upper())
        )
        company_id = company_result.scalar_one_or_none()
        if not company_id:
            return None

        # Count unique guarantors
        count_result = await self.db.execute(
            select(func.count(func.distinct(Entity.id)))
            .where(
                Entity.company_id == company_id,
                Entity.is_guarantor == True,
            )
        )
        count = count_result.scalar()

        return GroundTruth(
            value=count or 0,
            source="entities.is_guarantor=true",
            tier=1,
        )

    async def get_entity_count(self, ticker: str) -> Optional[GroundTruth]:
        """
        Count total entities for a company.
        Tier 1: Direct from entities table.
        """
        company_result = await self.db.execute(
            select(Company.id).where(Company.ticker == ticker.upper())
        )
        company_id = company_result.scalar_one_or_none()
        if not company_id:
            return None

        count_result = await self.db.execute(
            select(func.count(Entity.id))
            .where(Entity.company_id == company_id)
        )
        count = count_result.scalar()

        return GroundTruth(
            value=count or 0,
            source="count(entities)",
            tier=1,
        )

    async def get_guarantors_for_debt(self, debt_id: UUID) -> Optional[GroundTruth]:
        """
        Get guarantor IDs for a specific debt instrument.
        Tier 1: Direct from guarantees table.
        """
        result = await self.db.execute(
            select(Guarantee.guarantor_id)
            .where(Guarantee.debt_instrument_id == debt_id)
        )
        guarantor_ids = [str(row[0]) for row in result.all()]

        return GroundTruth(
            value=guarantor_ids,
            source="guarantees.guarantor_id",
            tier=1,
        )

    # =========================================================================
    # FINANCIALS GROUND TRUTH (Tier 1)
    # =========================================================================

    async def get_quarterly_financials(
        self, ticker: str, fiscal_year: int, fiscal_quarter: int
    ) -> Optional[GroundTruth]:
        """
        Get specific quarter financials.
        Tier 1: Direct from company_financials table.
        """
        company_result = await self.db.execute(
            select(Company.id).where(Company.ticker == ticker.upper())
        )
        company_id = company_result.scalar_one_or_none()
        if not company_id:
            return None

        fin_result = await self.db.execute(
            select(CompanyFinancials)
            .where(
                CompanyFinancials.company_id == company_id,
                CompanyFinancials.fiscal_year == fiscal_year,
                CompanyFinancials.fiscal_quarter == fiscal_quarter,
            )
        )
        financials = fin_result.scalar_one_or_none()
        if not financials:
            return None

        return GroundTruth(
            value={
                "revenue": financials.revenue,
                "operating_income": financials.operating_income,
                "ebitda": financials.ebitda,
                "net_income": financials.net_income,
                "total_debt": financials.total_debt,
                "cash_and_equivalents": financials.cash_and_equivalents,
                "period_end_date": financials.period_end_date,
            },
            source=f"company_financials.{fiscal_year}Q{fiscal_quarter}",
            tier=1,
        )

    # =========================================================================
    # COLLATERAL GROUND TRUTH (Tier 1)
    # =========================================================================

    async def get_collateral_for_debt(self, debt_id: UUID) -> Optional[GroundTruth]:
        """
        Get collateral records for a debt instrument.
        Tier 1: Direct from collateral table.
        """
        result = await self.db.execute(
            select(Collateral).where(Collateral.debt_instrument_id == debt_id)
        )
        collateral = result.scalars().all()

        return GroundTruth(
            value=[{
                "type": c.collateral_type,
                "description": c.description,
                "priority": c.priority,
                "estimated_value": c.estimated_value,
            } for c in collateral],
            source="collateral",
            tier=1,
        )

    # =========================================================================
    # COVENANT GROUND TRUTH (Tier 1)
    # =========================================================================

    async def get_covenants_for_company(self, ticker: str) -> Optional[GroundTruth]:
        """
        Get covenant records for a company.
        Tier 1: Direct from covenants table.
        """
        company_result = await self.db.execute(
            select(Company.id).where(Company.ticker == ticker.upper())
        )
        company_id = company_result.scalar_one_or_none()
        if not company_id:
            return None

        result = await self.db.execute(
            select(Covenant).where(Covenant.company_id == company_id)
        )
        covenants = result.scalars().all()

        return GroundTruth(
            value=[{
                "covenant_type": c.covenant_type,
                "covenant_name": c.covenant_name,
                "test_metric": c.test_metric,
                "threshold_value": float(c.threshold_value) if c.threshold_value else None,
                "threshold_type": c.threshold_type,
            } for c in covenants],
            source="covenants",
            tier=1,
        )

    # =========================================================================
    # DOCUMENT SEARCH GROUND TRUTH (Tier 3)
    # =========================================================================

    async def search_document_text(
        self, ticker: str, search_term: str, section_type: Optional[str] = None
    ) -> Optional[GroundTruth]:
        """
        Search document sections for text.
        Tier 3: Document text verification.
        """
        company_result = await self.db.execute(
            select(Company.id).where(Company.ticker == ticker.upper())
        )
        company_id = company_result.scalar_one_or_none()
        if not company_id:
            return None

        query = select(DocumentSection).where(
            DocumentSection.company_id == company_id,
            DocumentSection.content.ilike(f"%{search_term}%"),
        )
        if section_type:
            query = query.where(DocumentSection.section_type == section_type)

        result = await self.db.execute(query)
        docs = result.scalars().all()

        return GroundTruth(
            value=[{
                "id": str(d.id),
                "section_type": d.section_type,
                "content_length": d.content_length,
                "contains_term": search_term.lower() in d.content.lower(),
            } for d in docs],
            source=f"document_sections (search: {search_term})",
            tier=3,
        )
