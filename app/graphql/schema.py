"""
GraphQL Schema for DebtStack API

Provides flexible, composable queries for credit data.
Users can request exactly the fields they need and traverse relationships.
"""

import strawberry
from strawberry.types import Info
from typing import Optional, List
from decimal import Decimal
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select, and_, or_
from sqlalchemy.orm import selectinload

from app.core.database import async_session_maker
from app.models import (
    Company as CompanyModel,
    Entity as EntityModel,
    DebtInstrument as DebtModel,
    Guarantee as GuaranteeModel,
    BondPricing as BondPricingModel,
    CompanyMetrics as MetricsModel,
    CompanyFinancials as FinancialsModel,
)


# =============================================================================
# TYPES
# =============================================================================


@strawberry.type
class BondPricing:
    """Bond pricing data (TRACE or estimated)."""
    last_price: Optional[float] = None
    ytm_pct: Optional[float] = None
    spread_to_treasury_bps: Optional[int] = None
    treasury_benchmark: Optional[str] = None
    price_source: Optional[str] = None
    staleness_days: Optional[int] = None
    fetched_at: Optional[datetime] = None


@strawberry.type
class Guarantor:
    """Entity that guarantees a debt instrument."""
    id: strawberry.ID
    name: str
    entity_type: Optional[str] = None
    jurisdiction: Optional[str] = None
    guarantee_type: Optional[str] = None


@strawberry.type
class Bond:
    """Debt instrument (bond, loan, credit facility)."""
    id: strawberry.ID
    name: str
    instrument_type: str
    seniority: str
    security_type: Optional[str] = None

    # Amounts (converted from cents to dollars)
    commitment: Optional[float] = None
    principal: Optional[float] = None
    outstanding: Optional[float] = None
    currency: str = "USD"

    # Interest terms
    rate_type: Optional[str] = None
    interest_rate_pct: Optional[float] = None  # Converted from bps
    spread_bps: Optional[int] = None
    benchmark: Optional[str] = None
    floor_bps: Optional[int] = None

    # Dates
    issue_date: Optional[date] = None
    maturity_date: Optional[date] = None

    # Identifiers
    cusip: Optional[str] = None
    isin: Optional[str] = None

    # Status
    is_active: bool = True

    # Internal references for resolvers
    _issuer_id: strawberry.Private[Optional[UUID]] = None
    _company_ticker: strawberry.Private[Optional[str]] = None
    _db_id: strawberry.Private[Optional[UUID]] = None

    @strawberry.field
    async def issuer(self) -> Optional["Entity"]:
        """The entity that issued this debt."""
        if not self._issuer_id:
            return None
        async with async_session_maker() as session:
            result = await session.execute(
                select(EntityModel).where(EntityModel.id == self._issuer_id)
            )
            entity = result.scalar_one_or_none()
            if entity:
                return entity_to_graphql(entity)
        return None

    @strawberry.field
    async def guarantors(self) -> List[Guarantor]:
        """Entities that guarantee this debt."""
        if not self._db_id:
            return []
        async with async_session_maker() as session:
            result = await session.execute(
                select(GuaranteeModel, EntityModel)
                .join(EntityModel, GuaranteeModel.guarantor_id == EntityModel.id)
                .where(GuaranteeModel.debt_instrument_id == self._db_id)
            )
            rows = result.all()
            return [
                Guarantor(
                    id=strawberry.ID(str(entity.id)),
                    name=entity.name,
                    entity_type=entity.entity_type,
                    jurisdiction=entity.jurisdiction,
                    guarantee_type=guarantee.guarantee_type,
                )
                for guarantee, entity in rows
            ]

    @strawberry.field
    async def pricing(self) -> Optional[BondPricing]:
        """Current pricing for this bond."""
        if not self._db_id:
            return None
        async with async_session_maker() as session:
            result = await session.execute(
                select(BondPricingModel)
                .where(BondPricingModel.debt_instrument_id == self._db_id)
            )
            p = result.scalar_one_or_none()
            if p:
                return BondPricing(
                    last_price=float(p.last_price) if p.last_price else None,
                    ytm_pct=p.ytm_bps / 100 if p.ytm_bps else None,
                    spread_to_treasury_bps=p.spread_to_treasury_bps,
                    treasury_benchmark=p.treasury_benchmark,
                    price_source=p.price_source,
                    staleness_days=p.staleness_days,
                    fetched_at=p.fetched_at,
                )
        return None

    @strawberry.field
    async def company(self) -> Optional["Company"]:
        """The company this debt belongs to."""
        if not self._company_ticker:
            return None
        async with async_session_maker() as session:
            result = await session.execute(
                select(CompanyModel).where(CompanyModel.ticker == self._company_ticker)
            )
            company = result.scalar_one_or_none()
            if company:
                return company_to_graphql(company)
        return None


@strawberry.type
class Entity:
    """Legal entity within a corporate structure."""
    id: strawberry.ID
    name: str
    entity_type: str
    jurisdiction: Optional[str] = None
    formation_type: Optional[str] = None
    ownership_pct: Optional[float] = None
    structure_tier: Optional[int] = None

    # Status flags
    is_guarantor: bool = False
    is_borrower: bool = False
    is_restricted: bool = True
    is_unrestricted: bool = False
    is_vie: bool = False

    # Internal references
    _parent_id: strawberry.Private[Optional[UUID]] = None
    _company_id: strawberry.Private[Optional[UUID]] = None
    _db_id: strawberry.Private[Optional[UUID]] = None

    @strawberry.field
    async def parent(self) -> Optional["Entity"]:
        """Parent entity in the corporate hierarchy."""
        if not self._parent_id:
            return None
        async with async_session_maker() as session:
            result = await session.execute(
                select(EntityModel).where(EntityModel.id == self._parent_id)
            )
            entity = result.scalar_one_or_none()
            if entity:
                return entity_to_graphql(entity)
        return None

    @strawberry.field
    async def children(self) -> List["Entity"]:
        """Child entities in the corporate hierarchy."""
        if not self._db_id:
            return []
        async with async_session_maker() as session:
            result = await session.execute(
                select(EntityModel).where(EntityModel.parent_id == self._db_id)
            )
            entities = result.scalars().all()
            return [entity_to_graphql(e) for e in entities]

    @strawberry.field
    async def debt(self) -> List[Bond]:
        """Debt instruments issued by this entity."""
        if not self._db_id:
            return []
        async with async_session_maker() as session:
            result = await session.execute(
                select(DebtModel).where(DebtModel.issuer_id == self._db_id)
            )
            instruments = result.scalars().all()
            return [debt_to_graphql(d) for d in instruments]


@strawberry.type
class CompanyMetrics:
    """Pre-computed metrics for screening and filtering."""
    total_debt: Optional[float] = None
    secured_debt: Optional[float] = None
    unsecured_debt: Optional[float] = None
    leverage_ratio: Optional[float] = None
    interest_coverage: Optional[float] = None
    entity_count: Optional[int] = None
    guarantor_count: Optional[int] = None
    subordination_risk: Optional[str] = None
    has_structural_sub: bool = False
    has_floating_rate: bool = False
    has_near_term_maturity: bool = False
    nearest_maturity: Optional[date] = None


@strawberry.type
class Financial:
    """Quarterly financial statement data."""
    fiscal_year: int
    fiscal_quarter: int
    period_end_date: date
    filing_type: Optional[str] = None

    # Income statement (converted to dollars)
    revenue: Optional[float] = None
    ebitda: Optional[float] = None
    operating_income: Optional[float] = None
    interest_expense: Optional[float] = None
    net_income: Optional[float] = None

    # Balance sheet
    cash: Optional[float] = None
    total_assets: Optional[float] = None
    total_debt: Optional[float] = None
    total_liabilities: Optional[float] = None
    stockholders_equity: Optional[float] = None

    # Cash flow
    operating_cash_flow: Optional[float] = None
    capex: Optional[float] = None


@strawberry.type
class Company:
    """Public company with corporate structure and debt."""
    ticker: str
    name: str
    sector: Optional[str] = None
    industry: Optional[str] = None
    cik: Optional[str] = None

    # Internal reference
    _db_id: strawberry.Private[Optional[UUID]] = None

    @strawberry.field
    async def entities(
        self,
        entity_type: Optional[str] = None,
        jurisdiction: Optional[str] = None,
        is_guarantor: Optional[bool] = None,
        is_vie: Optional[bool] = None,
        is_unrestricted: Optional[bool] = None,
    ) -> List[Entity]:
        """All entities in the corporate structure, with optional filters."""
        if not self._db_id:
            return []
        async with async_session_maker() as session:
            query = select(EntityModel).where(EntityModel.company_id == self._db_id)

            if entity_type:
                query = query.where(EntityModel.entity_type == entity_type)
            if jurisdiction:
                query = query.where(EntityModel.jurisdiction.ilike(f"%{jurisdiction}%"))
            if is_guarantor is not None:
                query = query.where(EntityModel.is_guarantor == is_guarantor)
            if is_vie is not None:
                query = query.where(EntityModel.is_vie == is_vie)
            if is_unrestricted is not None:
                query = query.where(EntityModel.is_unrestricted == is_unrestricted)

            result = await session.execute(query)
            entities = result.scalars().all()
            return [entity_to_graphql(e) for e in entities]

    @strawberry.field
    async def hierarchy(self) -> Optional[Entity]:
        """Root entity (holdco) of the corporate hierarchy."""
        if not self._db_id:
            return None
        async with async_session_maker() as session:
            # Find the root entity (no parent, usually holdco)
            result = await session.execute(
                select(EntityModel)
                .where(EntityModel.company_id == self._db_id)
                .where(EntityModel.parent_id.is_(None))
                .limit(1)
            )
            entity = result.scalar_one_or_none()
            if entity:
                return entity_to_graphql(entity)
        return None

    @strawberry.field
    async def debt(
        self,
        seniority: Optional[str] = None,
        security_type: Optional[str] = None,
        instrument_type: Optional[str] = None,
        min_outstanding: Optional[float] = None,
        maturity_before: Optional[date] = None,
        maturity_after: Optional[date] = None,
        has_pricing: Optional[bool] = None,
    ) -> List[Bond]:
        """All debt instruments, with optional filters."""
        if not self._db_id:
            return []
        async with async_session_maker() as session:
            query = select(DebtModel).where(DebtModel.company_id == self._db_id)

            if seniority:
                query = query.where(DebtModel.seniority == seniority)
            if security_type:
                query = query.where(DebtModel.security_type == security_type)
            if instrument_type:
                query = query.where(DebtModel.instrument_type == instrument_type)
            if min_outstanding:
                query = query.where(DebtModel.outstanding >= int(min_outstanding * 100))
            if maturity_before:
                query = query.where(DebtModel.maturity_date <= maturity_before)
            if maturity_after:
                query = query.where(DebtModel.maturity_date >= maturity_after)
            if has_pricing is not None:
                if has_pricing:
                    query = query.where(DebtModel.cusip.isnot(None))
                else:
                    query = query.where(DebtModel.cusip.is_(None))

            result = await session.execute(query)
            instruments = result.scalars().all()
            return [debt_to_graphql(d, company_ticker=self.ticker) for d in instruments]

    @strawberry.field
    async def metrics(self) -> Optional[CompanyMetrics]:
        """Pre-computed credit metrics."""
        async with async_session_maker() as session:
            result = await session.execute(
                select(MetricsModel).where(MetricsModel.ticker == self.ticker)
            )
            m = result.scalar_one_or_none()
            if m:
                return CompanyMetrics(
                    total_debt=m.total_debt / 100 if m.total_debt else None,
                    secured_debt=m.secured_debt / 100 if m.secured_debt else None,
                    unsecured_debt=m.unsecured_debt / 100 if m.unsecured_debt else None,
                    leverage_ratio=float(m.leverage_ratio) if m.leverage_ratio else None,
                    interest_coverage=float(m.interest_coverage) if m.interest_coverage else None,
                    entity_count=m.entity_count,
                    guarantor_count=m.guarantor_count,
                    subordination_risk=m.subordination_risk,
                    has_structural_sub=m.has_structural_sub,
                    has_floating_rate=m.has_floating_rate,
                    has_near_term_maturity=m.has_near_term_maturity,
                    nearest_maturity=m.nearest_maturity,
                )
        return None

    @strawberry.field
    async def financials(self, quarters: int = 4) -> List[Financial]:
        """Quarterly financial statements."""
        if not self._db_id:
            return []
        async with async_session_maker() as session:
            result = await session.execute(
                select(FinancialsModel)
                .where(FinancialsModel.company_id == self._db_id)
                .order_by(FinancialsModel.period_end_date.desc())
                .limit(quarters)
            )
            rows = result.scalars().all()
            return [
                Financial(
                    fiscal_year=f.fiscal_year,
                    fiscal_quarter=f.fiscal_quarter,
                    period_end_date=f.period_end_date,
                    filing_type=f.filing_type,
                    revenue=f.revenue / 100 if f.revenue else None,
                    ebitda=f.ebitda / 100 if f.ebitda else None,
                    operating_income=f.operating_income / 100 if f.operating_income else None,
                    interest_expense=f.interest_expense / 100 if f.interest_expense else None,
                    net_income=f.net_income / 100 if f.net_income else None,
                    cash=f.cash_and_equivalents / 100 if f.cash_and_equivalents else None,
                    total_assets=f.total_assets / 100 if f.total_assets else None,
                    total_debt=f.total_debt / 100 if f.total_debt else None,
                    total_liabilities=f.total_liabilities / 100 if f.total_liabilities else None,
                    stockholders_equity=f.stockholders_equity / 100 if f.stockholders_equity else None,
                    operating_cash_flow=f.operating_cash_flow / 100 if f.operating_cash_flow else None,
                    capex=f.capex / 100 if f.capex else None,
                )
                for f in rows
            ]


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def company_to_graphql(c: CompanyModel) -> Company:
    """Convert SQLAlchemy Company to GraphQL type."""
    return Company(
        ticker=c.ticker,
        name=c.name,
        sector=c.sector,
        industry=c.industry,
        cik=c.cik,
        _db_id=c.id,
    )


def entity_to_graphql(e: EntityModel) -> Entity:
    """Convert SQLAlchemy Entity to GraphQL type."""
    return Entity(
        id=strawberry.ID(str(e.id)),
        name=e.name,
        entity_type=e.entity_type,
        jurisdiction=e.jurisdiction,
        formation_type=e.formation_type,
        ownership_pct=float(e.ownership_pct) if e.ownership_pct else None,
        structure_tier=e.structure_tier,
        is_guarantor=e.is_guarantor,
        is_borrower=e.is_borrower,
        is_restricted=e.is_restricted,
        is_unrestricted=e.is_unrestricted,
        is_vie=e.is_vie,
        _parent_id=e.parent_id,
        _company_id=e.company_id,
        _db_id=e.id,
    )


def debt_to_graphql(d: DebtModel, company_ticker: Optional[str] = None) -> Bond:
    """Convert SQLAlchemy DebtInstrument to GraphQL type."""
    return Bond(
        id=strawberry.ID(str(d.id)),
        name=d.name,
        instrument_type=d.instrument_type,
        seniority=d.seniority,
        security_type=d.security_type,
        commitment=d.commitment / 100 if d.commitment else None,
        principal=d.principal / 100 if d.principal else None,
        outstanding=d.outstanding / 100 if d.outstanding else None,
        currency=d.currency,
        rate_type=d.rate_type,
        interest_rate_pct=d.interest_rate / 100 if d.interest_rate else None,
        spread_bps=d.spread_bps,
        benchmark=d.benchmark,
        floor_bps=d.floor_bps,
        issue_date=d.issue_date,
        maturity_date=d.maturity_date,
        cusip=d.cusip,
        isin=d.isin,
        is_active=d.is_active,
        _issuer_id=d.issuer_id,
        _company_ticker=company_ticker,
        _db_id=d.id,
    )


# =============================================================================
# QUERIES
# =============================================================================


@strawberry.type
class Query:
    """Root query type for DebtStack GraphQL API."""

    @strawberry.field
    async def company(self, ticker: str) -> Optional[Company]:
        """Get a company by ticker symbol."""
        async with async_session_maker() as session:
            result = await session.execute(
                select(CompanyModel).where(CompanyModel.ticker == ticker.upper())
            )
            company = result.scalar_one_or_none()
            if company:
                return company_to_graphql(company)
        return None

    @strawberry.field
    async def companies(
        self,
        sector: Optional[str] = None,
        has_structural_sub: Optional[bool] = None,
        min_leverage: Optional[float] = None,
        max_leverage: Optional[float] = None,
        limit: int = 50,
    ) -> List[Company]:
        """Search companies with filters."""
        async with async_session_maker() as session:
            query = select(CompanyModel)

            if sector:
                query = query.where(CompanyModel.sector.ilike(f"%{sector}%"))

            # Join metrics for leverage/risk filters
            if has_structural_sub is not None or min_leverage or max_leverage:
                query = query.join(MetricsModel, CompanyModel.ticker == MetricsModel.ticker)
                if has_structural_sub is not None:
                    query = query.where(MetricsModel.has_structural_sub == has_structural_sub)
                if min_leverage:
                    query = query.where(MetricsModel.leverage_ratio >= min_leverage)
                if max_leverage:
                    query = query.where(MetricsModel.leverage_ratio <= max_leverage)

            query = query.limit(limit)
            result = await session.execute(query)
            companies = result.scalars().all()
            return [company_to_graphql(c) for c in companies]

    @strawberry.field
    async def bonds(
        self,
        seniority: Optional[str] = None,
        security_type: Optional[str] = None,
        sector: Optional[str] = None,
        min_ytm_pct: Optional[float] = None,
        max_ytm_pct: Optional[float] = None,
        min_spread_bps: Optional[int] = None,
        max_spread_bps: Optional[int] = None,
        maturity_before: Optional[date] = None,
        maturity_after: Optional[date] = None,
        limit: int = 50,
    ) -> List[Bond]:
        """Search bonds across all companies."""
        async with async_session_maker() as session:
            query = select(DebtModel, CompanyModel.ticker).join(
                CompanyModel, DebtModel.company_id == CompanyModel.id
            )

            if seniority:
                query = query.where(DebtModel.seniority == seniority)
            if security_type:
                query = query.where(DebtModel.security_type == security_type)
            if sector:
                query = query.where(CompanyModel.sector.ilike(f"%{sector}%"))
            if maturity_before:
                query = query.where(DebtModel.maturity_date <= maturity_before)
            if maturity_after:
                query = query.where(DebtModel.maturity_date >= maturity_after)

            # Pricing filters require join
            if any([min_ytm_pct, max_ytm_pct, min_spread_bps, max_spread_bps]):
                query = query.join(
                    BondPricingModel,
                    DebtModel.id == BondPricingModel.debt_instrument_id
                )
                if min_ytm_pct:
                    query = query.where(BondPricingModel.ytm_bps >= int(min_ytm_pct * 100))
                if max_ytm_pct:
                    query = query.where(BondPricingModel.ytm_bps <= int(max_ytm_pct * 100))
                if min_spread_bps:
                    query = query.where(BondPricingModel.spread_to_treasury_bps >= min_spread_bps)
                if max_spread_bps:
                    query = query.where(BondPricingModel.spread_to_treasury_bps <= max_spread_bps)

            query = query.limit(limit)
            result = await session.execute(query)
            rows = result.all()
            return [debt_to_graphql(d, company_ticker=ticker) for d, ticker in rows]

    @strawberry.field
    async def entities(
        self,
        entity_type: Optional[str] = None,
        jurisdiction: Optional[str] = None,
        is_guarantor: Optional[bool] = None,
        is_vie: Optional[bool] = None,
        is_unrestricted: Optional[bool] = None,
        limit: int = 50,
    ) -> List[Entity]:
        """Search entities across all companies."""
        async with async_session_maker() as session:
            query = select(EntityModel)

            if entity_type:
                query = query.where(EntityModel.entity_type == entity_type)
            if jurisdiction:
                query = query.where(EntityModel.jurisdiction.ilike(f"%{jurisdiction}%"))
            if is_guarantor is not None:
                query = query.where(EntityModel.is_guarantor == is_guarantor)
            if is_vie is not None:
                query = query.where(EntityModel.is_vie == is_vie)
            if is_unrestricted is not None:
                query = query.where(EntityModel.is_unrestricted == is_unrestricted)

            query = query.limit(limit)
            result = await session.execute(query)
            entities = result.scalars().all()
            return [entity_to_graphql(e) for e in entities]


# =============================================================================
# SCHEMA
# =============================================================================

schema = strawberry.Schema(query=Query)
