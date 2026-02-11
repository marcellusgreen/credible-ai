"""
Primitives API for DebtStack.ai

NOTE: Pricing data only includes actual TRACE market data, not estimated values.

9 core primitives optimized for AI agents:
1. GET /v1/companies - Horizontal company search
2. GET /v1/bonds - Horizontal bond search (includes pricing)
3. GET /v1/bonds/resolve - Bond identifier resolution
4. POST /v1/entities/traverse - Graph traversal
5. GET /v1/documents/search - Full-text search across SEC filings
6. POST /v1/batch - Batch operations
7. GET /v1/companies/{ticker}/changes - Diff/changelog since date
8. GET /v1/financials - Quarterly financial statements (income, balance sheet, cash flow)
9. GET /v1/collateral - Collateral securing debt instruments

DEPRECATED:
- GET /v1/pricing - Use GET /v1/bonds?has_pricing=true instead
"""

import re
from datetime import date, datetime
from typing import Optional, List, Set
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Body, Header, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_, and_, desc, asc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.auth import (
    require_auth, check_tier_access, get_endpoint_cost,
    check_and_deduct_credits, normalize_tier,
)
from app.models import (
    Company, CompanyMetrics, CompanySnapshot, Entity, DebtInstrument,
    Guarantee, BondPricing, DocumentSection, ExtractionMetadata,
    CompanyFinancials, Collateral, Covenant, User, UsageLog,
)

# Import shared helpers
from app.api.primitives_helpers import (
    # CSV export
    flatten_dict,
    to_csv_response,
    # ETag caching
    generate_etag,
    check_etag,
    etag_response,
    # Field selection
    COMPANY_FIELDS,
    BOND_FIELDS,
    PRICING_FIELDS,
    FINANCIALS_FIELDS,
    COLLATERAL_FIELDS,
    COVENANT_FIELDS,
    parse_fields,
    filter_dict,
    parse_comma_list,
    apply_sort,
)

router = APIRouter()


# =============================================================================
# PRIMITIVE 1: search.companies
# =============================================================================


@router.get("/companies", tags=["Primitives"])
async def search_companies(
    # Ticker filter (supports comma-separated list)
    ticker: Optional[str] = Query(None, description="Comma-separated tickers (e.g., AAPL,MSFT,GOOGL)"),
    # Classification filters
    sector: Optional[str] = Query(None, description="Filter by sector"),
    industry: Optional[str] = Query(None, description="Filter by industry"),
    rating_bucket: Optional[str] = Query(None, description="Rating bucket: IG, HY-BB, HY-B, HY-CCC, NR"),
    # Leverage filters
    min_leverage: Optional[float] = Query(None, description="Minimum leverage ratio"),
    max_leverage: Optional[float] = Query(None, description="Maximum leverage ratio"),
    min_net_leverage: Optional[float] = Query(None, description="Minimum net leverage ratio"),
    max_net_leverage: Optional[float] = Query(None, description="Maximum net leverage ratio"),
    # Debt amount filters
    min_debt: Optional[int] = Query(None, description="Minimum total debt (cents)"),
    max_debt: Optional[int] = Query(None, description="Maximum total debt (cents)"),
    # Boolean filters
    has_structural_sub: Optional[bool] = Query(None, description="Has structural subordination"),
    has_floating_rate: Optional[bool] = Query(None, description="Has floating rate debt"),
    has_near_term_maturity: Optional[bool] = Query(None, description="Debt maturing within 24 months"),
    has_holdco_debt: Optional[bool] = Query(None, description="Has holdco-level debt"),
    has_opco_debt: Optional[bool] = Query(None, description="Has opco-level debt"),
    # Field selection
    fields: Optional[str] = Query(None, description="Comma-separated fields to return"),
    # Sorting
    sort: str = Query("ticker", description="Sort field, prefix with - for descending (e.g., -net_leverage_ratio)"),
    # Pagination
    limit: int = Query(50, ge=1, le=100, description="Results per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    # Export format
    format: str = Query("json", description="Response format: json or csv"),
    # Metadata inclusion
    include_metadata: bool = Query(False, description="Include extraction metadata (qa_score, timestamps, warnings)"),
    # ETag support
    if_none_match: Optional[str] = Header(None, description="ETag for conditional request"),
    db: AsyncSession = Depends(get_db),
):
    """
    Search companies with powerful filtering and field selection.

    Supports filtering by sector, leverage, ratings, risk flags, and more.
    Use `fields` parameter to request only the data you need.
    Use `format=csv` for CSV export (useful for bulk data).
    Use `include_metadata=true` for extraction quality info.

    **Example:** Find MAG7 company with highest leverage:
    ```
    GET /v1/companies?ticker=AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA&fields=ticker,name,net_leverage_ratio&sort=-net_leverage_ratio&limit=1
    ```

    **Example:** Get company with metadata:
    ```
    GET /v1/companies?ticker=AAPL&include_metadata=true
    ```
    """
    # Parse and validate fields
    selected_fields = parse_fields(fields, COMPANY_FIELDS)

    # Build query
    query = select(CompanyMetrics, Company).join(
        Company, CompanyMetrics.company_id == Company.id
    )
    count_query = select(func.count()).select_from(CompanyMetrics)

    filters = []

    # Ticker filter (comma-separated)
    ticker_list = parse_comma_list(ticker)
    if ticker_list:
        filters.append(CompanyMetrics.ticker.in_(ticker_list))

    # Classification filters
    if sector:
        filters.append(CompanyMetrics.sector.ilike(f"%{sector}%"))
    if industry:
        filters.append(CompanyMetrics.industry.ilike(f"%{industry}%"))
    if rating_bucket:
        filters.append(CompanyMetrics.rating_bucket == rating_bucket)

    # Leverage filters
    if min_leverage is not None:
        filters.append(CompanyMetrics.leverage_ratio >= min_leverage)
    if max_leverage is not None:
        filters.append(CompanyMetrics.leverage_ratio <= max_leverage)
    if min_net_leverage is not None:
        filters.append(CompanyMetrics.net_leverage_ratio >= min_net_leverage)
    if max_net_leverage is not None:
        filters.append(CompanyMetrics.net_leverage_ratio <= max_net_leverage)

    # Debt amount filters
    if min_debt is not None:
        filters.append(CompanyMetrics.total_debt >= min_debt)
    if max_debt is not None:
        filters.append(CompanyMetrics.total_debt <= max_debt)

    # Boolean filters
    if has_structural_sub is not None:
        filters.append(CompanyMetrics.has_structural_sub == has_structural_sub)
    if has_floating_rate is not None:
        filters.append(CompanyMetrics.has_floating_rate == has_floating_rate)
    if has_near_term_maturity is not None:
        filters.append(CompanyMetrics.has_near_term_maturity == has_near_term_maturity)
    if has_holdco_debt is not None:
        filters.append(CompanyMetrics.has_holdco_debt == has_holdco_debt)
    if has_opco_debt is not None:
        filters.append(CompanyMetrics.has_opco_debt == has_opco_debt)

    if filters:
        query = query.where(and_(*filters))
        count_query = count_query.join(
            Company, CompanyMetrics.company_id == Company.id
        ).where(and_(*filters))

    # Get total count
    total = await db.scalar(count_query)

    # Apply sorting
    sort_column_map = {
        "ticker": CompanyMetrics.ticker,
        "name": Company.name,
        "sector": CompanyMetrics.sector,
        "total_debt": CompanyMetrics.total_debt,
        "leverage_ratio": CompanyMetrics.leverage_ratio,
        "net_leverage_ratio": CompanyMetrics.net_leverage_ratio,
        "interest_coverage": CompanyMetrics.interest_coverage,
        "entity_count": CompanyMetrics.entity_count,
        "nearest_maturity": CompanyMetrics.nearest_maturity,
        "subordination_score": CompanyMetrics.subordination_score,
    }
    query = apply_sort(query, sort, sort_column_map, CompanyMetrics.ticker)

    # Apply pagination
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    rows = result.all()

    # Fetch metadata if requested
    metadata_map = {}
    if include_metadata:
        company_ids = [c.id for _, c in rows]
        if company_ids:
            meta_result = await db.execute(
                select(ExtractionMetadata).where(ExtractionMetadata.company_id.in_(company_ids))
            )
            for meta in meta_result.scalars():
                metadata_map[meta.company_id] = meta

    # Build response with field selection
    data = []
    for m, c in rows:
        company_data = {
            "ticker": m.ticker,
            "name": c.name,
            "sector": m.sector,
            "industry": m.industry,
            "cik": c.cik,
            "total_debt": m.total_debt,
            "secured_debt": m.secured_debt,
            "unsecured_debt": m.unsecured_debt,
            "net_debt": m.net_debt,
            "leverage_ratio": float(m.leverage_ratio) if m.leverage_ratio else None,
            "net_leverage_ratio": float(m.net_leverage_ratio) if m.net_leverage_ratio else None,
            "interest_coverage": float(m.interest_coverage) if m.interest_coverage else None,
            "secured_leverage": float(m.secured_leverage) if m.secured_leverage else None,
            "entity_count": m.entity_count,
            "guarantor_count": m.guarantor_count,
            "subordination_risk": m.subordination_risk,
            "subordination_score": float(m.subordination_score) if m.subordination_score else None,
            "has_structural_sub": m.has_structural_sub,
            "has_floating_rate": m.has_floating_rate,
            "has_near_term_maturity": m.has_near_term_maturity,
            "has_holdco_debt": m.has_holdco_debt,
            "has_opco_debt": m.has_opco_debt,
            "has_unrestricted_subs": m.has_unrestricted_subs,
            "nearest_maturity": m.nearest_maturity.isoformat() if m.nearest_maturity else None,
            "weighted_avg_maturity": float(m.weighted_avg_maturity) if m.weighted_avg_maturity else None,
            "debt_due_1yr": m.debt_due_1yr,
            "debt_due_2yr": m.debt_due_2yr,
            "debt_due_3yr": m.debt_due_3yr,
            "sp_rating": m.sp_rating,
            "moodys_rating": m.moodys_rating,
            "rating_bucket": m.rating_bucket,
        }

        # Add metadata if requested
        if include_metadata:
            metadata = {}
            # Add extraction metadata if available
            if c.id in metadata_map:
                meta = metadata_map[c.id]
                metadata.update({
                    "qa_score": float(meta.qa_score) if meta.qa_score else None,
                    "extraction_method": meta.extraction_method,
                    "data_version": meta.data_version,
                    "structure_extracted_at": meta.structure_extracted_at.isoformat() if meta.structure_extracted_at else None,
                    "debt_extracted_at": meta.debt_extracted_at.isoformat() if meta.debt_extracted_at else None,
                    "financials_extracted_at": meta.financials_extracted_at.isoformat() if meta.financials_extracted_at else None,
                    "pricing_updated_at": meta.pricing_updated_at.isoformat() if meta.pricing_updated_at else None,
                    "source_10k_date": meta.source_10k_date.isoformat() if meta.source_10k_date else None,
                    "source_10q_url": meta.source_10q_url,
                    "field_confidence": meta.field_confidence,
                    "warnings": meta.warnings if meta.warnings else [],
                })
            # Add leverage data quality info from source_filings
            if m.source_filings:
                sf = m.source_filings
                metadata["leverage_data_quality"] = {
                    "ebitda_source": sf.get("ebitda_source"),  # "annual_10k" or "quarterly_sum"
                    "ebitda_quarters": sf.get("ebitda_quarters"),
                    "ebitda_quarters_with_da": sf.get("ebitda_quarters_with_da"),
                    "is_annualized": sf.get("is_annualized", False),
                    "ebitda_estimated": sf.get("ebitda_estimated", False),
                    "ttm_quarters": sf.get("ttm_quarters", []),
                    "computed_at": sf.get("computed_at"),
                }
            company_data["_metadata"] = metadata

        data.append(filter_dict(company_data, selected_fields))

    # Return CSV if requested
    if format.lower() == "csv":
        return to_csv_response(data, filename="companies.csv")

    response_data = {
        "data": data,
        "meta": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "fields": list(selected_fields) if selected_fields else "all",
            "data_sources": {
                "total_debt": "SEC financial statements (10-K/10-Q balance sheet)",
                "leverage_ratio": "total_debt / TTM EBITDA from SEC filings",
                "ebitda": "TTM from SEC filings; for banks, this is PPNR (check ebitda_type in /financials)",
            },
        }
    }
    return etag_response(response_data, if_none_match)


# =============================================================================
# PRIMITIVE 2: search.bonds
# =============================================================================


@router.get("/bonds", tags=["Primitives"])
async def search_bonds(
    # Company/identifier filters
    ticker: Optional[str] = Query(None, description="Company ticker(s), comma-separated"),
    cusip: Optional[str] = Query(None, description="CUSIP(s), comma-separated"),
    sector: Optional[str] = Query(None, description="Company sector"),
    # Classification filters
    seniority: Optional[str] = Query(None, description="senior_secured, senior_unsecured, subordinated"),
    security_type: Optional[str] = Query(None, description="first_lien, second_lien, unsecured"),
    instrument_type: Optional[str] = Query(None, description="term_loan_b, senior_notes, revolver, etc."),
    issuer_type: Optional[str] = Query(None, description="Issuer entity type: holdco, opco, subsidiary"),
    rate_type: Optional[str] = Query(None, description="fixed, floating"),
    currency: Optional[str] = Query(None, description="Currency code (USD, EUR)"),
    # Rate filters
    min_coupon: Optional[float] = Query(None, description="Minimum coupon rate (%)"),
    max_coupon: Optional[float] = Query(None, description="Maximum coupon rate (%)"),
    # Yield/pricing filters
    min_ytm: Optional[float] = Query(None, description="Minimum yield to maturity (%)"),
    max_ytm: Optional[float] = Query(None, description="Maximum yield to maturity (%)"),
    min_spread: Optional[int] = Query(None, description="Minimum spread to treasury (bps)"),
    max_spread: Optional[int] = Query(None, description="Maximum spread to treasury (bps)"),
    # Maturity filters
    maturity_before: Optional[date] = Query(None, description="Maturity before date (YYYY-MM-DD)"),
    maturity_after: Optional[date] = Query(None, description="Maturity after date (YYYY-MM-DD)"),
    # Amount filters
    min_outstanding: Optional[int] = Query(None, description="Minimum outstanding (cents)"),
    max_outstanding: Optional[int] = Query(None, description="Maximum outstanding (cents)"),
    # Boolean filters
    has_pricing: Optional[bool] = Query(None, description="Has pricing data"),
    has_guarantors: Optional[bool] = Query(None, description="Has guarantor entities"),
    has_cusip: Optional[bool] = Query(None, description="Has CUSIP (tradeable)"),
    is_active: Optional[bool] = Query(True, description="Is active (default: true)"),
    # Field selection
    fields: Optional[str] = Query(None, description="Comma-separated fields to return"),
    # Sorting
    sort: str = Query("maturity_date", description="Sort field (prefix - for desc)"),
    # Pagination
    limit: int = Query(50, ge=1, le=100, description="Results per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    # Export format
    format: str = Query("json", description="Response format: json or csv"),
    # ETag support
    if_none_match: Optional[str] = Header(None, description="ETag for conditional request"),
    db: AsyncSession = Depends(get_db),
):
    """
    Search bonds across all companies with comprehensive filtering.

    Use `format=csv` for CSV export (useful for bulk data).

    **Example:** Find senior unsecured bonds yielding >8%:
    ```
    GET /v1/bonds?seniority=senior_unsecured&min_ytm=8.0&has_pricing=true&sort=-pricing.ytm
    ```

    **Example:** Export all bonds to CSV:
    ```
    GET /v1/bonds?format=csv&limit=100
    ```
    """
    # Parse and validate fields
    selected_fields = parse_fields(fields, BOND_FIELDS)

    # Determine if we need pricing filters (always include pricing data via outer join)
    needs_pricing_filter = any([min_ytm, max_ytm, min_spread, max_spread, has_pricing])
    pricing_sort = sort.replace("-", "").startswith("pricing")

    # Always join pricing data so it's available in response
    query = select(DebtInstrument, Company, Entity, BondPricing).join(
        Company, DebtInstrument.company_id == Company.id
    ).join(
        Entity, DebtInstrument.issuer_id == Entity.id
    ).outerjoin(
        BondPricing, DebtInstrument.id == BondPricing.debt_instrument_id
    )

    filters = []

    # Active filter (default true)
    if is_active is not None:
        filters.append(DebtInstrument.is_active == is_active)

    # Ticker filter
    ticker_list = parse_comma_list(ticker)
    if ticker_list:
        filters.append(Company.ticker.in_(ticker_list))

    # CUSIP filter
    cusip_list = parse_comma_list(cusip)
    if cusip_list:
        filters.append(DebtInstrument.cusip.in_(cusip_list))

    # Classification filters
    if sector:
        filters.append(Company.sector.ilike(f"%{sector}%"))
    if seniority:
        filters.append(DebtInstrument.seniority == seniority)
    if security_type:
        filters.append(DebtInstrument.security_type == security_type)
    if instrument_type:
        filters.append(DebtInstrument.instrument_type == instrument_type)
    if issuer_type:
        filters.append(Entity.entity_type == issuer_type)
    if rate_type:
        filters.append(DebtInstrument.rate_type == rate_type)
    if currency:
        filters.append(DebtInstrument.currency == currency.upper())

    # Rate filters (stored in bps)
    if min_coupon is not None:
        filters.append(DebtInstrument.interest_rate >= int(min_coupon * 100))
    if max_coupon is not None:
        filters.append(DebtInstrument.interest_rate <= int(max_coupon * 100))

    # Maturity filters
    if maturity_before:
        filters.append(DebtInstrument.maturity_date <= maturity_before)
    if maturity_after:
        filters.append(DebtInstrument.maturity_date >= maturity_after)

    # Amount filters
    if min_outstanding is not None:
        filters.append(DebtInstrument.outstanding >= min_outstanding)
    if max_outstanding is not None:
        filters.append(DebtInstrument.outstanding <= max_outstanding)

    # CUSIP presence filter
    if has_cusip is True:
        filters.append(DebtInstrument.cusip.isnot(None))
    elif has_cusip is False:
        filters.append(DebtInstrument.cusip.is_(None))

    # Pricing filters
    if needs_pricing_filter:
        if min_ytm is not None:
            filters.append(BondPricing.ytm_bps >= int(min_ytm * 100))
        if max_ytm is not None:
            filters.append(BondPricing.ytm_bps <= int(max_ytm * 100))
        if min_spread is not None:
            filters.append(BondPricing.spread_to_treasury_bps >= min_spread)
        if max_spread is not None:
            filters.append(BondPricing.spread_to_treasury_bps <= max_spread)
        if has_pricing is True:
            # Only actual TRACE pricing, not estimated
            filters.append(BondPricing.last_price.isnot(None))
            filters.append(BondPricing.price_source == "TRACE")
        elif has_pricing is False:
            filters.append(or_(BondPricing.last_price.is_(None), BondPricing.id.is_(None)))

    # Guarantors filter (subquery)
    if has_guarantors is not None:
        guarantor_subq = select(Guarantee.debt_instrument_id).distinct()
        if has_guarantors:
            filters.append(DebtInstrument.id.in_(guarantor_subq))
        else:
            filters.append(DebtInstrument.id.notin_(guarantor_subq))

    if filters:
        query = query.where(and_(*filters))

    # Count query (always join pricing for consistent filtering)
    count_query = select(func.count(DebtInstrument.id.distinct())).select_from(DebtInstrument).join(
        Company, DebtInstrument.company_id == Company.id
    ).join(
        Entity, DebtInstrument.issuer_id == Entity.id
    ).outerjoin(
        BondPricing, DebtInstrument.id == BondPricing.debt_instrument_id
    )
    if filters:
        count_query = count_query.where(and_(*filters))

    total = await db.scalar(count_query)

    # Apply sorting (pricing columns always available since we always join)
    sort_column_map = {
        "maturity_date": DebtInstrument.maturity_date,
        "coupon_rate": DebtInstrument.interest_rate,
        "outstanding": DebtInstrument.outstanding,
        "name": DebtInstrument.name,
        "issuer_type": Entity.entity_type,
        "company_ticker": Company.ticker,
        "pricing.ytm": BondPricing.ytm_bps,
        "pricing.spread": BondPricing.spread_to_treasury_bps,
        "pricing.last_price": BondPricing.last_price,
    }

    query = apply_sort(query, sort, sort_column_map, DebtInstrument.maturity_date)

    # Apply pagination
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    rows = result.all()

    # Get guarantor counts for returned bonds
    bond_ids = [row[0].id for row in rows]
    guarantor_counts = {}
    if bond_ids:
        gc_result = await db.execute(
            select(Guarantee.debt_instrument_id, func.count(Guarantee.id))
            .where(Guarantee.debt_instrument_id.in_(bond_ids))
            .group_by(Guarantee.debt_instrument_id)
        )
        guarantor_counts = {row[0]: row[1] for row in gc_result}

    # Get collateral for returned bonds
    collateral_by_bond = {}
    if bond_ids:
        from app.models import Collateral
        coll_result = await db.execute(
            select(Collateral)
            .where(Collateral.debt_instrument_id.in_(bond_ids))
        )
        for coll in coll_result.scalars().all():
            if coll.debt_instrument_id not in collateral_by_bond:
                collateral_by_bond[coll.debt_instrument_id] = []
            collateral_by_bond[coll.debt_instrument_id].append({
                "type": coll.collateral_type,
                "description": coll.description,
                "priority": coll.priority,
                "estimated_value": coll.estimated_value,
            })

    # Build response (always have 4 elements since we always join pricing)
    data = []
    for row in rows:
        d, c, issuer, pricing = row

        bond_data = {
            "id": str(d.id),
            "name": d.name,
            "cusip": d.cusip,
            "isin": d.isin,
            "company_ticker": c.ticker,
            "company_name": c.name,
            "company_sector": c.sector,
            "issuer_name": issuer.name,
            "issuer_type": issuer.entity_type,
            "issuer_id": str(issuer.id),
            "instrument_type": d.instrument_type,
            "seniority": d.seniority,
            "security_type": d.security_type,
            "commitment": d.commitment,
            "principal": d.principal,
            "outstanding": d.outstanding,
            "currency": d.currency,
            "rate_type": d.rate_type,
            "coupon_rate": d.interest_rate / 100 if d.interest_rate else None,
            "spread_bps": d.spread_bps,
            "benchmark": d.benchmark,
            "floor_bps": d.floor_bps,
            "issue_date": d.issue_date.isoformat() if d.issue_date else None,
            "issue_date_estimated": d.issue_date_estimated,
            "maturity_date": d.maturity_date.isoformat() if d.maturity_date else None,
            "is_active": d.is_active,
            "is_drawn": d.is_drawn,
            "guarantor_count": guarantor_counts.get(d.id, 0),
            "guarantee_data_confidence": d.guarantee_data_confidence,
            "collateral": collateral_by_bond.get(d.id, []),
            "collateral_data_confidence": d.collateral_data_confidence,
        }

        # Add pricing data (only actual TRACE pricing, not estimated)
        if pricing and pricing.last_price is not None and pricing.price_source == "TRACE":
            bond_data["pricing"] = {
                "last_price": float(pricing.last_price) if pricing.last_price else None,
                "last_trade_date": pricing.last_trade_date.isoformat() if pricing.last_trade_date else None,
                "ytm": pricing.ytm_bps / 100 if pricing.ytm_bps else None,
                "ytm_bps": pricing.ytm_bps,
                "spread": pricing.spread_to_treasury_bps,
                "spread_bps": pricing.spread_to_treasury_bps,
                "treasury_benchmark": pricing.treasury_benchmark,
                "price_source": pricing.price_source,
                "staleness_days": pricing.staleness_days,
            }
        else:
            bond_data["pricing"] = None

        data.append(filter_dict(bond_data, selected_fields))

    # Return CSV if requested
    if format.lower() == "csv":
        return to_csv_response(data, filename="bonds.csv")

    response_data = {
        "data": data,
        "meta": {
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    }
    return etag_response(response_data, if_none_match)


# =============================================================================
# PRIMITIVE 6: resolve.bond
# =============================================================================


@router.get("/bonds/resolve", tags=["Primitives"])
async def resolve_bond(
    q: Optional[str] = Query(None, description="Free-text search (e.g., 'RIG 8% 2027')"),
    cusip: Optional[str] = Query(None, description="Exact CUSIP lookup"),
    isin: Optional[str] = Query(None, description="Exact ISIN lookup"),
    ticker: Optional[str] = Query(None, description="Company ticker"),
    coupon: Optional[float] = Query(None, description="Coupon rate (%)"),
    maturity_year: Optional[int] = Query(None, description="Maturity year"),
    match_mode: str = Query("fuzzy", description="Match mode: exact, fuzzy"),
    limit: int = Query(5, ge=1, le=20, description="Max matches to return"),
    db: AsyncSession = Depends(get_db),
):
    """
    Resolve bond identifiers - map between descriptions, CUSIPs, and issuers.

    **Examples:**
    - Find CUSIP for a bond: `GET /v1/bonds/resolve?q=RIG%208%25%202027`
    - Lookup by CUSIP: `GET /v1/bonds/resolve?cusip=89157VAG8`
    """
    if not any([q, cusip, isin, ticker]):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "MISSING_PARAMETER",
                "message": "At least one of q, cusip, isin, or ticker is required"
            }
        )

    query = select(DebtInstrument, Company, Entity).join(
        Company, DebtInstrument.company_id == Company.id
    ).join(
        Entity, DebtInstrument.issuer_id == Entity.id
    ).where(DebtInstrument.is_active == True)

    filters = []
    exact_match = False

    # Exact identifier lookups
    if cusip:
        filters.append(DebtInstrument.cusip == cusip.upper())
        exact_match = True
    elif isin:
        filters.append(DebtInstrument.isin == isin.upper())
        exact_match = True
    else:
        # Fuzzy/text search
        if ticker:
            filters.append(Company.ticker == ticker.upper())

        if coupon is not None:
            # Allow +/- 0.5% tolerance for fuzzy matching
            if match_mode == "exact":
                filters.append(DebtInstrument.interest_rate == int(coupon * 100))
            else:
                coupon_bps = int(coupon * 100)
                filters.append(DebtInstrument.interest_rate.between(coupon_bps - 50, coupon_bps + 50))

        if maturity_year is not None:
            # Filter to bonds maturing in that year
            year_start = date(maturity_year, 1, 1)
            year_end = date(maturity_year, 12, 31)
            filters.append(DebtInstrument.maturity_date.between(year_start, year_end))

        # Free-text search
        if q:
            # Parse the query string for patterns
            q_upper = q.upper()

            # Try to extract ticker (first word if it's short)
            words = q_upper.split()
            if words and len(words[0]) <= 5 and not words[0].replace(".", "").isdigit():
                filters.append(or_(
                    Company.ticker == words[0],
                    Company.ticker.ilike(f"%{words[0]}%")
                ))

            # Look for percentage pattern (e.g., "8%" or "8.5%")
            pct_match = re.search(r'(\d+\.?\d*)\s*%', q)
            if pct_match:
                coupon_val = float(pct_match.group(1))
                coupon_bps = int(coupon_val * 100)
                if match_mode == "exact":
                    filters.append(DebtInstrument.interest_rate == coupon_bps)
                else:
                    filters.append(DebtInstrument.interest_rate.between(coupon_bps - 25, coupon_bps + 25))

            # Look for year pattern (e.g., "2027" or "due 2027")
            year_match = re.search(r'(?:due\s+)?(\d{4})', q)
            if year_match:
                year = int(year_match.group(1))
                if 2020 <= year <= 2060:
                    year_start = date(year, 1, 1)
                    year_end = date(year, 12, 31)
                    filters.append(DebtInstrument.maturity_date.between(year_start, year_end))

            # Also search in bond name
            filters.append(or_(
                DebtInstrument.name.ilike(f"%{q}%"),
                True  # Don't fail if name doesn't match
            ))

    if filters:
        query = query.where(and_(*filters))

    query = query.limit(limit)
    result = await db.execute(query)
    rows = result.all()

    # Calculate confidence scores
    matches = []
    for d, c, issuer in rows:
        confidence = 1.0 if exact_match else 0.8

        # Boost confidence based on match quality
        if cusip and d.cusip == cusip.upper():
            confidence = 1.0
        elif isin and d.isin == isin.upper():
            confidence = 1.0

        # Get guarantor count
        gc_result = await db.execute(
            select(func.count(Guarantee.id)).where(Guarantee.debt_instrument_id == d.id)
        )
        guarantor_count = gc_result.scalar() or 0

        matches.append({
            "confidence": confidence,
            "bond": {
                "id": str(d.id),
                "name": d.name,
                "cusip": d.cusip,
                "isin": d.isin,
                "company_ticker": c.ticker,
                "company_name": c.name,
                "coupon_rate": d.interest_rate / 100 if d.interest_rate else None,
                "maturity_date": d.maturity_date.isoformat() if d.maturity_date else None,
                "seniority": d.seniority,
                "issuer": {
                    "name": issuer.name,
                    "entity_type": issuer.entity_type,
                },
                "outstanding": d.outstanding,
                "guarantor_count": guarantor_count,
            }
        })

    # Sort by confidence
    matches.sort(key=lambda x: x["confidence"], reverse=True)

    # Generate suggestions if no exact match
    suggestions = []
    if not exact_match and matches:
        # Check if user might have meant a different year
        if maturity_year or (q and re.search(r'\d{4}', q)):
            actual_years = set(m["bond"]["maturity_date"][:4] for m in matches if m["bond"]["maturity_date"])
            if actual_years:
                suggestions.append(f"Found bonds maturing in: {', '.join(sorted(actual_years))}")

    return {
        "data": {
            "query": q or cusip or isin or f"ticker={ticker}",
            "matches": matches,
            "exact_match": exact_match and len(matches) > 0,
            "suggestions": suggestions if suggestions else None,
        }
    }


# =============================================================================
# PRIMITIVE 4: traverse.entities
# =============================================================================


class TraversalStart(BaseModel):
    type: str = Field(..., description="Start type: company, bond, or entity")
    id: str = Field(..., description="Identifier: ticker, CUSIP, or entity UUID")


class TraversalRequest(BaseModel):
    start: TraversalStart
    relationships: List[str] = Field(
        default=["subsidiaries"],
        description="Relationships to traverse: guarantees, subsidiaries, parents, debt"
    )
    direction: str = Field(default="outbound", description="Direction: outbound, inbound, both")
    depth: int = Field(default=3, ge=1, le=10, description="Max traversal depth")
    filters: Optional[dict] = Field(default=None, description="Entity filters")
    fields: Optional[List[str]] = Field(default=None, description="Fields to return")


@router.post("/entities/traverse", tags=["Primitives"])
async def traverse_entities(
    request: TraversalRequest = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Traverse entity relationships for guarantor chains, org structure, and subordination analysis.

    **Example:** Find guarantors for a bond:
    ```json
    {
      "start": {"type": "bond", "id": "89157VAG8"},
      "relationships": ["guarantees"],
      "direction": "inbound"
    }
    ```

    **Example:** Get full corporate structure:
    ```json
    {
      "start": {"type": "company", "id": "RIG"},
      "relationships": ["subsidiaries"],
      "direction": "outbound",
      "depth": 10
    }
    ```
    """
    # Resolve start point
    start_data = {}
    company_id = None
    entity_ids = []
    debt_id = None

    if request.start.type == "company":
        # Find company by ticker
        result = await db.execute(
            select(Company).where(Company.ticker == request.start.id.upper())
        )
        company = result.scalar_one_or_none()
        if not company:
            raise HTTPException(
                status_code=404,
                detail={"code": "INVALID_TICKER", "message": f"Company '{request.start.id}' not found"}
            )
        start_data = {"type": "company", "id": request.start.id.upper(), "name": company.name}
        company_id = company.id

        # Get root entities for this company
        root_result = await db.execute(
            select(Entity).where(Entity.company_id == company_id, Entity.parent_id.is_(None))
        )
        entity_ids = [e.id for e in root_result.scalars().all()]

    elif request.start.type == "bond":
        # Find bond by CUSIP or ID
        if len(request.start.id) == 9:  # CUSIP
            result = await db.execute(
                select(DebtInstrument, Company).join(Company, DebtInstrument.company_id == Company.id)
                .where(DebtInstrument.cusip == request.start.id.upper())
            )
        else:  # UUID
            try:
                debt_uuid = UUID(request.start.id)
                result = await db.execute(
                    select(DebtInstrument, Company).join(Company, DebtInstrument.company_id == Company.id)
                    .where(DebtInstrument.id == debt_uuid)
                )
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "INVALID_ID", "message": "Invalid bond identifier"}
                )

        row = result.first()
        if not row:
            raise HTTPException(
                status_code=404,
                detail={"code": "INVALID_CUSIP", "message": f"Bond '{request.start.id}' not found"}
            )

        debt, company = row
        start_data = {
            "type": "bond",
            "id": debt.cusip or str(debt.id),
            "name": debt.name,
            "company": company.ticker
        }
        debt_id = debt.id
        company_id = company.id

    elif request.start.type == "entity":
        # Find entity by UUID
        try:
            entity_uuid = UUID(request.start.id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"code": "INVALID_ID", "message": "Invalid entity UUID"}
            )

        result = await db.execute(
            select(Entity, Company).join(Company, Entity.company_id == Company.id)
            .where(Entity.id == entity_uuid)
        )
        row = result.first()
        if not row:
            raise HTTPException(
                status_code=404,
                detail={"code": "INVALID_ENTITY", "message": f"Entity '{request.start.id}' not found"}
            )

        entity, company = row
        start_data = {
            "type": "entity",
            "id": str(entity.id),
            "name": entity.name,
            "company": company.ticker
        }
        entity_ids = [entity.id]
        company_id = company.id
    else:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_TYPE", "message": f"Invalid start type: {request.start.type}"}
        )

    # Process relationships
    traversal_results = []

    for relationship in request.relationships:
        if relationship == "guarantees":
            # Find guarantors for a bond or bonds guaranteed by an entity
            if debt_id:
                # Inbound: find entities that guarantee this bond
                result = await db.execute(
                    select(Guarantee, Entity)
                    .join(Entity, Guarantee.guarantor_id == Entity.id)
                    .where(Guarantee.debt_instrument_id == debt_id)
                )
                entities = []
                for guarantee, entity in result:
                    entity_data = {
                        "id": str(entity.id),
                        "name": entity.name,
                        "entity_type": entity.entity_type,
                        "jurisdiction": entity.jurisdiction,
                        "is_guarantor": entity.is_guarantor,
                        "guarantee_type": guarantee.guarantee_type,
                    }
                    if request.filters:
                        # Apply filters
                        if request.filters.get("entity_type") and entity.entity_type not in request.filters["entity_type"]:
                            continue
                        if request.filters.get("is_guarantor") is not None and entity.is_guarantor != request.filters["is_guarantor"]:
                            continue
                    entities.append(entity_data)

                traversal_results.append({
                    "relationship": "guarantees",
                    "direction": "inbound",
                    "entities": entities
                })

        elif relationship == "subsidiaries":
            # Find child entities
            if not company_id:
                continue

            # BFS traversal
            visited = set()
            current_level = entity_ids if entity_ids else []
            all_entities = []
            current_depth = 0

            # If starting from company, get all root entities first
            if not entity_ids:
                root_result = await db.execute(
                    select(Entity).where(Entity.company_id == company_id, Entity.parent_id.is_(None))
                )
                current_level = [e.id for e in root_result.scalars().all()]

                # Add root entities
                for eid in current_level:
                    e_result = await db.execute(select(Entity).where(Entity.id == eid))
                    e = e_result.scalar_one_or_none()
                    if e:
                        all_entities.append({
                            "id": str(e.id),
                            "name": e.name,
                            "entity_type": e.entity_type,
                            "jurisdiction": e.jurisdiction,
                            "is_guarantor": e.is_guarantor,
                            "is_borrower": e.is_borrower,
                            "is_vie": e.is_vie,
                            "is_unrestricted": e.is_unrestricted,
                            "parent_id": None,
                            "depth": 0,
                        })
                        visited.add(e.id)

            while current_level and current_depth < request.depth:
                current_depth += 1

                # Get children of current level
                result = await db.execute(
                    select(Entity).where(
                        Entity.parent_id.in_(current_level),
                        Entity.id.notin_(visited) if visited else True
                    )
                )
                children = result.scalars().all()

                next_level = []
                for child in children:
                    if child.id in visited:
                        continue
                    visited.add(child.id)

                    # Apply filters
                    if request.filters:
                        if request.filters.get("entity_type"):
                            allowed_types = request.filters["entity_type"]
                            if isinstance(allowed_types, str):
                                allowed_types = [allowed_types]
                            if child.entity_type not in allowed_types:
                                continue
                        if request.filters.get("is_guarantor") is not None:
                            if child.is_guarantor != request.filters["is_guarantor"]:
                                continue
                        if request.filters.get("is_vie") is not None:
                            if child.is_vie != request.filters["is_vie"]:
                                continue
                        if request.filters.get("jurisdiction"):
                            if not child.jurisdiction or request.filters["jurisdiction"].lower() not in child.jurisdiction.lower():
                                continue

                    entity_data = {
                        "id": str(child.id),
                        "name": child.name,
                        "entity_type": child.entity_type,
                        "jurisdiction": child.jurisdiction,
                        "is_guarantor": child.is_guarantor,
                        "is_borrower": child.is_borrower,
                        "is_vie": child.is_vie,
                        "is_unrestricted": child.is_unrestricted,
                        "parent_id": str(child.parent_id) if child.parent_id else None,
                        "depth": current_depth,
                    }

                    # Get debt at entity if requested
                    if request.fields and "debt_at_entity" in request.fields:
                        debt_result = await db.execute(
                            select(func.sum(DebtInstrument.outstanding), func.count(DebtInstrument.id))
                            .where(DebtInstrument.issuer_id == child.id)
                        )
                        debt_row = debt_result.first()
                        entity_data["debt_at_entity"] = {
                            "total_outstanding": debt_row[0] if debt_row else 0,
                            "instrument_count": debt_row[1] if debt_row else 0,
                        }

                    all_entities.append(entity_data)
                    next_level.append(child.id)

                current_level = next_level

            traversal_results.append({
                "relationship": "subsidiaries",
                "direction": "outbound",
                "entities": all_entities,
                "depth_reached": current_depth,
            })

        elif relationship == "parents":
            # Traverse upward to parent entities
            if not entity_ids:
                continue

            all_parents = []
            current = entity_ids[0] if entity_ids else None
            current_depth = 0

            while current and current_depth < request.depth:
                result = await db.execute(
                    select(Entity).where(Entity.id == current)
                )
                entity = result.scalar_one_or_none()
                if not entity or not entity.parent_id:
                    break

                parent_result = await db.execute(
                    select(Entity).where(Entity.id == entity.parent_id)
                )
                parent = parent_result.scalar_one_or_none()
                if not parent:
                    break

                all_parents.append({
                    "id": str(parent.id),
                    "name": parent.name,
                    "entity_type": parent.entity_type,
                    "jurisdiction": parent.jurisdiction,
                    "is_guarantor": parent.is_guarantor,
                    "depth": current_depth + 1,
                })

                current = parent.id
                current_depth += 1

            traversal_results.append({
                "relationship": "parents",
                "direction": "inbound",
                "entities": all_parents,
            })

        elif relationship == "debt":
            # Find debt issued at entity/entities
            if entity_ids:
                target_ids = entity_ids
            elif company_id:
                # Get all entity IDs for company
                result = await db.execute(
                    select(Entity.id).where(Entity.company_id == company_id)
                )
                target_ids = [r[0] for r in result.all()]
            else:
                continue

            result = await db.execute(
                select(DebtInstrument)
                .where(DebtInstrument.issuer_id.in_(target_ids), DebtInstrument.is_active == True)
                .order_by(DebtInstrument.maturity_date)
            )
            debts = result.scalars().all()

            debt_list = []
            for d in debts:
                debt_list.append({
                    "id": str(d.id),
                    "name": d.name,
                    "cusip": d.cusip,
                    "issuer_id": str(d.issuer_id),
                    "seniority": d.seniority,
                    "outstanding": d.outstanding,
                    "maturity_date": d.maturity_date.isoformat() if d.maturity_date else None,
                })

            traversal_results.append({
                "relationship": "debt",
                "direction": "outbound",
                "instruments": debt_list,
            })

    # Build summary
    summary = {}
    for tr in traversal_results:
        if "entities" in tr:
            summary[f"{tr['relationship']}_count"] = len(tr["entities"])
        elif "instruments" in tr:
            summary[f"{tr['relationship']}_count"] = len(tr["instruments"])

    return {
        "data": {
            "start": start_data,
            "traversal": traversal_results[0] if len(traversal_results) == 1 else traversal_results,
            "summary": summary,
        }
    }


# =============================================================================
# PRIMITIVE 5: search.pricing
# =============================================================================


@router.get("/pricing", tags=["Primitives"], deprecated=True)
async def search_pricing(
    ticker: Optional[str] = Query(None, description="Company ticker(s), comma-separated"),
    cusip: Optional[str] = Query(None, description="CUSIP(s), comma-separated"),
    min_ytm: Optional[float] = Query(None, description="Minimum YTM (%)"),
    max_ytm: Optional[float] = Query(None, description="Maximum YTM (%)"),
    min_spread: Optional[int] = Query(None, description="Minimum spread (bps)"),
    max_spread: Optional[int] = Query(None, description="Maximum spread (bps)"),
    max_staleness: Optional[int] = Query(None, description="Maximum staleness days"),
    fields: Optional[str] = Query(None, description="Fields to return"),
    sort: str = Query("-ytm", description="Sort field"),
    limit: int = Query(50, ge=1, le=100, description="Results per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    format: str = Query("json", description="Response format: json or csv"),
    # ETag support
    if_none_match: Optional[str] = Header(None, description="ETag for conditional request"),
    db: AsyncSession = Depends(get_db),
):
    """
    **DEPRECATED**: Use `GET /v1/bonds?has_pricing=true` instead.

    This endpoint will be removed in a future version.
    The /v1/bonds endpoint now includes pricing data for all bonds.

    ---

    Search bond pricing data from FINRA TRACE.

    Use `format=csv` for CSV export.

    **Example:** Get pricing for all RIG bonds:
    ```
    GET /v1/pricing?ticker=RIG
    ```

    **Migration:** Use instead:
    ```
    GET /v1/bonds?ticker=RIG&has_pricing=true
    ```
    """
    selected_fields = parse_fields(fields, PRICING_FIELDS)

    query = select(BondPricing, DebtInstrument, Company).join(
        DebtInstrument, BondPricing.debt_instrument_id == DebtInstrument.id
    ).join(
        Company, DebtInstrument.company_id == Company.id
    ).where(BondPricing.last_price.isnot(None)).where(
        BondPricing.price_source == "TRACE"  # Only actual market data, not estimated
    )

    filters = []

    # Ticker filter
    ticker_list = parse_comma_list(ticker)
    if ticker_list:
        filters.append(Company.ticker.in_(ticker_list))

    # CUSIP filter
    cusip_list = parse_comma_list(cusip)
    if cusip_list:
        filters.append(BondPricing.cusip.in_(cusip_list))

    # YTM filters
    if min_ytm is not None:
        filters.append(BondPricing.ytm_bps >= int(min_ytm * 100))
    if max_ytm is not None:
        filters.append(BondPricing.ytm_bps <= int(max_ytm * 100))

    # Spread filters
    if min_spread is not None:
        filters.append(BondPricing.spread_to_treasury_bps >= min_spread)
    if max_spread is not None:
        filters.append(BondPricing.spread_to_treasury_bps <= max_spread)

    # Staleness filter
    if max_staleness is not None:
        filters.append(or_(
            BondPricing.staleness_days <= max_staleness,
            BondPricing.staleness_days.is_(None)
        ))

    if filters:
        query = query.where(and_(*filters))

    # Count
    count_query = select(func.count()).select_from(BondPricing).join(
        DebtInstrument, BondPricing.debt_instrument_id == DebtInstrument.id
    ).join(
        Company, DebtInstrument.company_id == Company.id
    ).where(BondPricing.last_price.isnot(None))
    if filters:
        count_query = count_query.where(and_(*filters))

    total = await db.scalar(count_query)

    # Apply sorting
    sort_column_map = {
        "ytm": BondPricing.ytm_bps,
        "spread": BondPricing.spread_to_treasury_bps,
        "last_price": BondPricing.last_price,
        "staleness_days": BondPricing.staleness_days,
        "maturity_date": DebtInstrument.maturity_date,
        "company_ticker": Company.ticker,
    }
    query = apply_sort(query, sort, sort_column_map, BondPricing.ytm_bps)

    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    rows = result.all()

    data = []
    for pricing, debt, company in rows:
        item = {
            "cusip": pricing.cusip or debt.cusip,
            "isin": debt.isin,
            "bond_name": debt.name,
            "company_ticker": company.ticker,
            "company_name": company.name,
            "last_price": float(pricing.last_price) if pricing.last_price else None,
            "last_trade_date": pricing.last_trade_date.isoformat() if pricing.last_trade_date else None,
            "last_trade_volume": pricing.last_trade_volume,
            "ytm": pricing.ytm_bps / 100 if pricing.ytm_bps else None,
            "ytm_bps": pricing.ytm_bps,
            "spread": pricing.spread_to_treasury_bps,
            "spread_bps": pricing.spread_to_treasury_bps,
            "treasury_benchmark": pricing.treasury_benchmark,
            "price_source": pricing.price_source,
            "staleness_days": pricing.staleness_days,
            "coupon_rate": debt.interest_rate / 100 if debt.interest_rate else None,
            "maturity_date": debt.maturity_date.isoformat() if debt.maturity_date else None,
            "seniority": debt.seniority,
        }
        data.append(filter_dict(item, selected_fields))

    # Return CSV if requested
    if format.lower() == "csv":
        return to_csv_response(data, filename="pricing.csv")

    response_data = {
        "data": data,
        "meta": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "as_of": datetime.utcnow().isoformat() + "Z",
        },
        "_deprecation": {
            "warning": "This endpoint is deprecated. Use GET /v1/bonds?has_pricing=true instead.",
            "migration": "GET /v1/bonds?has_pricing=true&ticker=XXX",
            "removal_date": "2026-06-01",
        }
    }
    return etag_response(response_data, if_none_match)


# =============================================================================
# PRIMITIVE 5: documents.search
# =============================================================================

# Available fields for document search
DOCUMENT_FIELDS = {
    "id", "ticker", "company_name",
    "doc_type", "filing_date", "section_type", "section_title",
    "snippet", "content", "content_length",
    "relevance_score", "sec_filing_url",
}

# Valid section types
VALID_SECTION_TYPES = {
    "exhibit_21", "debt_footnote", "mda_liquidity",
    "credit_agreement", "indenture", "guarantor_list", "covenants",
}

# Valid doc types
VALID_DOC_TYPES = {"10-K", "10-Q", "8-K"}


@router.get("/documents/search", tags=["Primitives"])
async def search_documents(
    # Search query (required)
    q: str = Query(..., min_length=2, description="Full-text search query (required)"),
    # Filters
    ticker: Optional[str] = Query(None, description="Comma-separated tickers (e.g., AAPL,MSFT)"),
    doc_type: Optional[str] = Query(None, description="Document type: 10-K, 10-Q, 8-K"),
    section_type: Optional[str] = Query(None, description="Section type: exhibit_21, debt_footnote, mda_liquidity, credit_agreement, indenture, guarantor_list, covenants"),
    filed_after: Optional[date] = Query(None, description="Min filing date (YYYY-MM-DD)"),
    filed_before: Optional[date] = Query(None, description="Max filing date (YYYY-MM-DD)"),
    # Field selection
    fields: Optional[str] = Query(None, description="Comma-separated fields to return"),
    # Sorting
    sort: str = Query("-relevance", description="Sort field: -relevance (default), -filing_date, filing_date"),
    # Pagination
    limit: int = Query(50, ge=1, le=100, description="Results per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    # Export format
    format: str = Query("json", description="Response format: json or csv"),
    # ETag support
    if_none_match: Optional[str] = Header(None, description="ETag for conditional request"),
    db: AsyncSession = Depends(get_db),
):
    """
    Full-text search across SEC filing sections.

    Searches debt footnotes, MD&A liquidity sections, credit agreements,
    subsidiary lists, guarantor information, and covenant disclosures.

    Uses PostgreSQL full-text search with relevance ranking.
    Search terms are stemmed and matched intelligently.

    **Section Types:**
    - `exhibit_21`: Subsidiary list from 10-K Exhibit 21
    - `debt_footnote`: Long-term debt details from Notes
    - `mda_liquidity`: Liquidity and Capital Resources from MD&A
    - `credit_agreement`: Credit facility terms from 8-K
    - `indenture`: Bond indentures with covenants, events of default, redemption provisions
    - `guarantor_list`: Guarantor subsidiaries from Notes
    - `covenants`: Financial covenants from Notes/Exhibits

    **Example:** Find mentions of "subordinated" in debt footnotes:
    ```
    GET /v1/documents/search?q=subordinated&section_type=debt_footnote
    ```

    **Example:** Search for covenant mentions in RIG filings:
    ```
    GET /v1/documents/search?q=covenant&ticker=RIG
    ```

    **Example:** Find recent credit agreement amendments:
    ```
    GET /v1/documents/search?q=amended%20credit&section_type=credit_agreement&filed_after=2024-01-01
    ```
    """
    # Parse and validate fields
    selected_fields = parse_fields(fields, DOCUMENT_FIELDS)

    # Validate section_type
    if section_type:
        section_types = parse_comma_list(section_type, uppercase=False)
        invalid_sections = set(section_types) - VALID_SECTION_TYPES
        if invalid_sections:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "INVALID_SECTION_TYPE",
                    "message": f"Invalid section types: {', '.join(invalid_sections)}",
                    "valid_types": sorted(VALID_SECTION_TYPES)
                }
            )
    else:
        section_types = []

    # Validate doc_type
    if doc_type:
        doc_types = parse_comma_list(doc_type)
        invalid_docs = set(doc_types) - VALID_DOC_TYPES
        if invalid_docs:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "INVALID_DOC_TYPE",
                    "message": f"Invalid doc types: {', '.join(invalid_docs)}",
                    "valid_types": sorted(VALID_DOC_TYPES)
                }
            )
    else:
        doc_types = []

    # Build the search query using PostgreSQL full-text search
    # plainto_tsquery converts plain text to a tsquery
    # ts_rank_cd provides relevance scoring (cover density ranking)
    # ts_headline generates snippets with highlighted matches

    from sqlalchemy import text, literal_column

    # Base query with full-text search
    # We use raw SQL for the FTS functions since SQLAlchemy doesn't have native support
    search_query = text("""
        SELECT
            ds.id,
            c.ticker,
            c.name as company_name,
            ds.doc_type,
            ds.filing_date,
            ds.section_type,
            ds.section_title,
            ds.content,
            ds.content_length,
            ds.sec_filing_url,
            ts_rank_cd(ds.search_vector, plainto_tsquery('english', :query)) as relevance_score,
            ts_headline('english', ds.content, plainto_tsquery('english', :query),
                'MaxWords=50, MinWords=20, MaxFragments=1, StartSel=<b>, StopSel=</b>') as snippet
        FROM document_sections ds
        JOIN companies c ON ds.company_id = c.id
        WHERE ds.search_vector @@ plainto_tsquery('english', :query)
    """)

    # Build dynamic WHERE conditions
    conditions = []
    params = {"query": q}

    # Ticker filter
    ticker_list = parse_comma_list(ticker)
    if ticker_list:
        conditions.append("c.ticker = ANY(:tickers)")
        params["tickers"] = ticker_list

    # Doc type filter
    if doc_types:
        conditions.append("ds.doc_type = ANY(:doc_types)")
        params["doc_types"] = doc_types

    # Section type filter
    if section_types:
        conditions.append("ds.section_type = ANY(:section_types)")
        params["section_types"] = section_types

    # Date filters
    if filed_after:
        conditions.append("ds.filing_date >= :filed_after")
        params["filed_after"] = filed_after
    if filed_before:
        conditions.append("ds.filing_date <= :filed_before")
        params["filed_before"] = filed_before

    # Build full query with conditions
    where_clause = ""
    if conditions:
        where_clause = " AND " + " AND ".join(conditions)

    # Determine sort order
    if sort == "-relevance" or sort == "relevance":
        order_clause = "ORDER BY relevance_score DESC"
    elif sort == "-filing_date":
        order_clause = "ORDER BY ds.filing_date DESC"
    elif sort == "filing_date":
        order_clause = "ORDER BY ds.filing_date ASC"
    else:
        order_clause = "ORDER BY relevance_score DESC"

    # Full query with pagination
    full_query = text(f"""
        SELECT
            ds.id,
            c.ticker,
            c.name as company_name,
            ds.doc_type,
            ds.filing_date,
            ds.section_type,
            ds.section_title,
            ds.content,
            ds.content_length,
            ds.sec_filing_url,
            ts_rank_cd(ds.search_vector, plainto_tsquery('english', :query)) as relevance_score,
            ts_headline('english', ds.content, plainto_tsquery('english', :query),
                'MaxWords=50, MinWords=20, MaxFragments=1, StartSel=<b>, StopSel=</b>') as snippet
        FROM document_sections ds
        JOIN companies c ON ds.company_id = c.id
        WHERE ds.search_vector @@ plainto_tsquery('english', :query)
        {where_clause}
        {order_clause}
        LIMIT :limit OFFSET :offset
    """)

    params["limit"] = limit
    params["offset"] = offset

    # Count query
    count_query = text(f"""
        SELECT COUNT(*)
        FROM document_sections ds
        JOIN companies c ON ds.company_id = c.id
        WHERE ds.search_vector @@ plainto_tsquery('english', :query)
        {where_clause}
    """)

    # Execute queries
    result = await db.execute(full_query, params)
    rows = result.fetchall()

    # Get count (remove limit/offset params for count query)
    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    total_result = await db.execute(count_query, count_params)
    total = total_result.scalar()

    # Build response
    data = []
    for row in rows:
        item = {
            "id": str(row.id),
            "ticker": row.ticker,
            "company_name": row.company_name,
            "doc_type": row.doc_type,
            "filing_date": row.filing_date.isoformat() if row.filing_date else None,
            "section_type": row.section_type,
            "section_title": row.section_title,
            "snippet": row.snippet,
            "content": row.content if (selected_fields is None or "content" in selected_fields) else None,
            "content_length": row.content_length,
            "relevance_score": round(float(row.relevance_score), 4) if row.relevance_score else 0,
            "sec_filing_url": row.sec_filing_url,
        }

        # Remove content from default response to keep it compact
        if selected_fields is None:
            item.pop("content", None)

        data.append(filter_dict(item, selected_fields))

    # Return CSV if requested
    if format.lower() == "csv":
        return to_csv_response(data, filename="documents.csv")

    response_data = {
        "data": data,
        "meta": {
            "query": q,
            "total": total,
            "limit": limit,
            "offset": offset,
            "filters": {
                "ticker": ticker_list if ticker_list else None,
                "doc_type": doc_types if doc_types else None,
                "section_type": section_types if section_types else None,
                "filed_after": filed_after.isoformat() if filed_after else None,
                "filed_before": filed_before.isoformat() if filed_before else None,
            }
        }
    }
    return etag_response(response_data, if_none_match)


# =============================================================================
# PRIMITIVE 7: batch
# =============================================================================


class BatchOperation(BaseModel):
    """A single operation in a batch request."""
    primitive: str = Field(..., description="Primitive name: search.companies, search.bonds, resolve.bond, traverse.entities, search.pricing, search.documents")
    params: dict = Field(default_factory=dict, description="Parameters for the primitive")


class BatchRequest(BaseModel):
    """Batch request containing multiple operations."""
    operations: List[BatchOperation] = Field(..., min_length=1, max_length=10, description="List of operations (1-10)")


class BatchOperationResult(BaseModel):
    """Result of a single batch operation."""
    operation_id: int
    status: str  # "success" or "error"
    data: Optional[dict] = None
    error: Optional[dict] = None


@router.post("/batch", tags=["Primitives"])
async def batch_operations(
    request: BatchRequest = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Execute multiple primitive operations in a single request.

    Accepts up to 10 operations and executes them in parallel.
    Each operation is independent - failures in one don't affect others.

    **Supported Primitives:**
    - `search.companies` - Search companies (maps to GET /v1/companies)
    - `search.bonds` - Search bonds (maps to GET /v1/bonds) - includes pricing data
    - `resolve.bond` - Resolve bond identifier (maps to GET /v1/bonds/resolve)
    - `traverse.entities` - Graph traversal (maps to POST /v1/entities/traverse)
    - `search.pricing` - **DEPRECATED** - Use search.bonds with has_pricing=true instead
    - `search.documents` - Search documents (maps to GET /v1/documents/search)

    **Example Request:**
    ```json
    {
      "operations": [
        {"primitive": "search.companies", "params": {"ticker": "AAPL,MSFT", "fields": "ticker,net_leverage_ratio"}},
        {"primitive": "search.bonds", "params": {"ticker": "TSLA", "has_pricing": true}},
        {"primitive": "resolve.bond", "params": {"q": "RIG 8% 2027"}}
      ]
    }
    ```

    **Example Response:**
    ```json
    {
      "results": [
        {"operation_id": 0, "status": "success", "data": {...}},
        {"operation_id": 1, "status": "success", "data": {...}},
        {"operation_id": 2, "status": "error", "error": {"code": "NOT_FOUND", "message": "..."}}
      ],
      "meta": {
        "total_operations": 3,
        "successful": 2,
        "failed": 1,
        "duration_ms": 234
      }
    }
    ```
    """
    import asyncio
    import time

    start_time = time.time()

    # Map primitive names to handler functions
    primitive_handlers = {
        "search.companies": _batch_search_companies,
        "search.bonds": _batch_search_bonds,
        "resolve.bond": _batch_resolve_bond,
        "traverse.entities": _batch_traverse_entities,
        "search.pricing": _batch_search_pricing,
        "search.documents": _batch_search_documents,
    }

    async def execute_operation(op_id: int, op: BatchOperation) -> BatchOperationResult:
        """Execute a single operation and return result."""
        handler = primitive_handlers.get(op.primitive)
        if not handler:
            return BatchOperationResult(
                operation_id=op_id,
                status="error",
                error={
                    "code": "INVALID_PRIMITIVE",
                    "message": f"Unknown primitive: {op.primitive}",
                    "valid_primitives": list(primitive_handlers.keys())
                }
            )

        try:
            result = await handler(op.params, db)
            return BatchOperationResult(
                operation_id=op_id,
                status="success",
                data=result
            )
        except HTTPException as e:
            return BatchOperationResult(
                operation_id=op_id,
                status="error",
                error=e.detail if isinstance(e.detail, dict) else {"code": "ERROR", "message": str(e.detail)}
            )
        except Exception as e:
            return BatchOperationResult(
                operation_id=op_id,
                status="error",
                error={"code": "INTERNAL_ERROR", "message": str(e)}
            )

    # Execute operations sequentially to avoid concurrent connection issues
    # (asyncpg doesn't support concurrent operations on a single connection)
    results = []
    for i, op in enumerate(request.operations):
        result = await execute_operation(i, op)
        results.append(result)

    duration_ms = int((time.time() - start_time) * 1000)
    successful = sum(1 for r in results if r.status == "success")
    failed = len(results) - successful

    return {
        "results": [r.model_dump() for r in results],
        "meta": {
            "total_operations": len(request.operations),
            "successful": successful,
            "failed": failed,
            "duration_ms": duration_ms,
        }
    }


# =============================================================================
# BATCH OPERATION HANDLERS
# =============================================================================


async def _batch_search_companies(params: dict, db: AsyncSession) -> dict:
    """Execute search.companies primitive."""
    # Build query similar to search_companies endpoint
    ticker = params.get("ticker")
    sector = params.get("sector")
    industry = params.get("industry")
    rating_bucket = params.get("rating_bucket")
    min_leverage = params.get("min_leverage")
    max_leverage = params.get("max_leverage")
    min_net_leverage = params.get("min_net_leverage")
    max_net_leverage = params.get("max_net_leverage")
    min_debt = params.get("min_debt")
    max_debt = params.get("max_debt")
    has_structural_sub = params.get("has_structural_sub")
    has_floating_rate = params.get("has_floating_rate")
    has_near_term_maturity = params.get("has_near_term_maturity")
    fields_param = params.get("fields")
    sort = params.get("sort", "ticker")
    limit = min(params.get("limit", 50), 100)
    offset = params.get("offset", 0)
    include_metadata = params.get("include_metadata", False)

    selected_fields = parse_fields(fields_param, COMPANY_FIELDS) if fields_param else None

    query = select(CompanyMetrics, Company).join(
        Company, CompanyMetrics.company_id == Company.id
    )

    filters = []

    ticker_list = parse_comma_list(ticker) if ticker else []
    if ticker_list:
        filters.append(CompanyMetrics.ticker.in_(ticker_list))
    if sector:
        filters.append(CompanyMetrics.sector.ilike(f"%{sector}%"))
    if industry:
        filters.append(CompanyMetrics.industry.ilike(f"%{industry}%"))
    if rating_bucket:
        filters.append(CompanyMetrics.rating_bucket == rating_bucket)
    if min_leverage is not None:
        filters.append(CompanyMetrics.leverage_ratio >= min_leverage)
    if max_leverage is not None:
        filters.append(CompanyMetrics.leverage_ratio <= max_leverage)
    if min_net_leverage is not None:
        filters.append(CompanyMetrics.net_leverage_ratio >= min_net_leverage)
    if max_net_leverage is not None:
        filters.append(CompanyMetrics.net_leverage_ratio <= max_net_leverage)
    if min_debt is not None:
        filters.append(CompanyMetrics.total_debt >= min_debt)
    if max_debt is not None:
        filters.append(CompanyMetrics.total_debt <= max_debt)
    if has_structural_sub is not None:
        filters.append(CompanyMetrics.has_structural_sub == has_structural_sub)
    if has_floating_rate is not None:
        filters.append(CompanyMetrics.has_floating_rate == has_floating_rate)
    if has_near_term_maturity is not None:
        filters.append(CompanyMetrics.has_near_term_maturity == has_near_term_maturity)

    if filters:
        query = query.where(and_(*filters))

    # Count
    count_query = select(func.count()).select_from(CompanyMetrics)
    if filters:
        count_query = count_query.join(Company, CompanyMetrics.company_id == Company.id).where(and_(*filters))
    total = await db.scalar(count_query)

    # Sort
    sort_column_map = {
        "ticker": CompanyMetrics.ticker,
        "name": Company.name,
        "total_debt": CompanyMetrics.total_debt,
        "leverage_ratio": CompanyMetrics.leverage_ratio,
        "net_leverage_ratio": CompanyMetrics.net_leverage_ratio,
    }
    query = apply_sort(query, sort, sort_column_map, CompanyMetrics.ticker)
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    rows = result.all()

    # Fetch metadata if requested
    metadata_map = {}
    if include_metadata:
        company_ids = [c.id for _, c in rows]
        if company_ids:
            meta_result = await db.execute(
                select(ExtractionMetadata).where(ExtractionMetadata.company_id.in_(company_ids))
            )
            for meta in meta_result.scalars():
                metadata_map[meta.company_id] = meta

    data = []
    for m, c in rows:
        company_data = {
            "ticker": m.ticker,
            "name": c.name,
            "sector": m.sector,
            "total_debt": m.total_debt,
            "leverage_ratio": float(m.leverage_ratio) if m.leverage_ratio else None,
            "net_leverage_ratio": float(m.net_leverage_ratio) if m.net_leverage_ratio else None,
            "has_structural_sub": m.has_structural_sub,
            "has_floating_rate": m.has_floating_rate,
            "has_near_term_maturity": m.has_near_term_maturity,
        }

        if include_metadata:
            metadata = {}
            if c.id in metadata_map:
                meta = metadata_map[c.id]
                metadata.update({
                    "qa_score": float(meta.qa_score) if meta.qa_score else None,
                    "extraction_method": meta.extraction_method,
                    "warnings": meta.warnings if meta.warnings else [],
                })
            # Add leverage data quality info
            if m.source_filings:
                sf = m.source_filings
                metadata["leverage_data_quality"] = {
                    "ebitda_quarters": sf.get("ebitda_quarters"),
                    "ebitda_quarters_with_da": sf.get("ebitda_quarters_with_da"),
                    "is_annualized": sf.get("is_annualized", False),
                    "ebitda_estimated": sf.get("ebitda_estimated", False),
                    "ttm_quarters": sf.get("ttm_quarters", []),
                }
            company_data["_metadata"] = metadata

        data.append(filter_dict(company_data, selected_fields) if selected_fields else company_data)

    return {"data": data, "meta": {"total": total, "limit": limit, "offset": offset}}


async def _batch_search_bonds(params: dict, db: AsyncSession) -> dict:
    """Execute search.bonds primitive."""
    ticker = params.get("ticker")
    cusip = params.get("cusip")
    seniority = params.get("seniority")
    instrument_type = params.get("instrument_type")
    rate_type = params.get("rate_type")
    min_coupon = params.get("min_coupon")
    max_coupon = params.get("max_coupon")
    min_ytm = params.get("min_ytm")
    max_ytm = params.get("max_ytm")
    has_pricing = params.get("has_pricing")
    is_active = params.get("is_active", True)
    limit = min(params.get("limit", 50), 100)
    offset = params.get("offset", 0)

    needs_pricing = any([min_ytm, max_ytm, has_pricing])

    if needs_pricing:
        query = select(DebtInstrument, Company, Entity, BondPricing).join(
            Company, DebtInstrument.company_id == Company.id
        ).join(
            Entity, DebtInstrument.issuer_id == Entity.id
        ).outerjoin(
            BondPricing, DebtInstrument.id == BondPricing.debt_instrument_id
        )
    else:
        query = select(DebtInstrument, Company, Entity).join(
            Company, DebtInstrument.company_id == Company.id
        ).join(
            Entity, DebtInstrument.issuer_id == Entity.id
        )

    filters = []
    if is_active is not None:
        filters.append(DebtInstrument.is_active == is_active)

    ticker_list = parse_comma_list(ticker) if ticker else []
    if ticker_list:
        filters.append(Company.ticker.in_(ticker_list))

    cusip_list = parse_comma_list(cusip) if cusip else []
    if cusip_list:
        filters.append(DebtInstrument.cusip.in_(cusip_list))

    if seniority:
        filters.append(DebtInstrument.seniority == seniority)
    if instrument_type:
        filters.append(DebtInstrument.instrument_type == instrument_type)
    if rate_type:
        filters.append(DebtInstrument.rate_type == rate_type)
    if min_coupon is not None:
        filters.append(DebtInstrument.interest_rate >= int(min_coupon * 100))
    if max_coupon is not None:
        filters.append(DebtInstrument.interest_rate <= int(max_coupon * 100))

    if needs_pricing:
        if min_ytm is not None:
            filters.append(BondPricing.ytm_bps >= int(min_ytm * 100))
        if max_ytm is not None:
            filters.append(BondPricing.ytm_bps <= int(max_ytm * 100))
        if has_pricing is True:
            # Only actual TRACE pricing, not estimated
            filters.append(BondPricing.last_price.isnot(None))
            filters.append(BondPricing.price_source == "TRACE")

    if filters:
        query = query.where(and_(*filters))

    query = query.order_by(DebtInstrument.maturity_date).offset(offset).limit(limit)

    result = await db.execute(query)
    rows = result.all()

    data = []
    for row in rows:
        if needs_pricing:
            d, c, issuer, pricing = row
        else:
            d, c, issuer = row
            pricing = None

        bond_data = {
            "id": str(d.id),
            "name": d.name,
            "cusip": d.cusip,
            "company_ticker": c.ticker,
            "issuer_name": issuer.name,
            "seniority": d.seniority,
            "coupon_rate": d.interest_rate / 100 if d.interest_rate else None,
            "maturity_date": d.maturity_date.isoformat() if d.maturity_date else None,
            "outstanding": d.outstanding,
        }

        # Only include actual TRACE pricing, not estimated
        if pricing and pricing.price_source == "TRACE":
            bond_data["pricing"] = {
                "last_price": float(pricing.last_price) if pricing.last_price else None,
                "ytm": pricing.ytm_bps / 100 if pricing.ytm_bps else None,
                "spread_bps": pricing.spread_to_treasury_bps,
            }

        data.append(bond_data)

    return {"data": data, "meta": {"limit": limit, "offset": offset}}


async def _batch_resolve_bond(params: dict, db: AsyncSession) -> dict:
    """Execute resolve.bond primitive."""
    q = params.get("q")
    cusip = params.get("cusip")
    isin = params.get("isin")
    ticker = params.get("ticker")
    coupon = params.get("coupon")
    maturity_year = params.get("maturity_year")
    limit = min(params.get("limit", 5), 20)

    if not any([q, cusip, isin, ticker]):
        raise HTTPException(
            status_code=400,
            detail={"code": "MISSING_PARAMETER", "message": "At least one of q, cusip, isin, or ticker is required"}
        )

    query = select(DebtInstrument, Company, Entity).join(
        Company, DebtInstrument.company_id == Company.id
    ).join(
        Entity, DebtInstrument.issuer_id == Entity.id
    ).where(DebtInstrument.is_active == True)

    filters = []
    exact_match = False

    if cusip:
        filters.append(DebtInstrument.cusip == cusip.upper())
        exact_match = True
    elif isin:
        filters.append(DebtInstrument.isin == isin.upper())
        exact_match = True
    else:
        if ticker:
            filters.append(Company.ticker == ticker.upper())
        if coupon is not None:
            coupon_bps = int(coupon * 100)
            filters.append(DebtInstrument.interest_rate.between(coupon_bps - 50, coupon_bps + 50))
        if maturity_year is not None:
            year_start = date(maturity_year, 1, 1)
            year_end = date(maturity_year, 12, 31)
            filters.append(DebtInstrument.maturity_date.between(year_start, year_end))
        if q:
            q_upper = q.upper()
            words = q_upper.split()
            if words and len(words[0]) <= 5:
                filters.append(or_(Company.ticker == words[0], Company.ticker.ilike(f"%{words[0]}%")))
            pct_match = re.search(r'(\d+\.?\d*)\s*%', q)
            if pct_match:
                coupon_val = float(pct_match.group(1))
                coupon_bps = int(coupon_val * 100)
                filters.append(DebtInstrument.interest_rate.between(coupon_bps - 25, coupon_bps + 25))
            year_match = re.search(r'(\d{4})', q)
            if year_match:
                year = int(year_match.group(1))
                if 2020 <= year <= 2060:
                    year_start = date(year, 1, 1)
                    year_end = date(year, 12, 31)
                    filters.append(DebtInstrument.maturity_date.between(year_start, year_end))

    if filters:
        query = query.where(and_(*filters))

    query = query.limit(limit)
    result = await db.execute(query)
    rows = result.all()

    matches = []
    for d, c, issuer in rows:
        confidence = 1.0 if exact_match else 0.8
        matches.append({
            "confidence": confidence,
            "bond": {
                "id": str(d.id),
                "name": d.name,
                "cusip": d.cusip,
                "company_ticker": c.ticker,
                "coupon_rate": d.interest_rate / 100 if d.interest_rate else None,
                "maturity_date": d.maturity_date.isoformat() if d.maturity_date else None,
                "seniority": d.seniority,
            }
        })

    return {"data": {"query": q or cusip or isin, "matches": matches, "exact_match": exact_match}}


async def _batch_traverse_entities(params: dict, db: AsyncSession) -> dict:
    """Execute traverse.entities primitive (simplified version)."""
    start = params.get("start", {})
    relationships = params.get("relationships", ["subsidiaries"])
    depth = min(params.get("depth", 3), 10)

    start_type = start.get("type")
    start_id = start.get("id")

    if start_type != "company":
        raise HTTPException(
            status_code=400,
            detail={"code": "UNSUPPORTED", "message": "Batch traverse only supports company start type"}
        )

    result = await db.execute(
        select(Company).where(Company.ticker == start_id.upper())
    )
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"Company '{start_id}' not found"}
        )

    # Get entities for the company
    entity_result = await db.execute(
        select(Entity).where(Entity.company_id == company.id)
    )
    entities = entity_result.scalars().all()

    entity_list = []
    for e in entities:
        entity_list.append({
            "id": str(e.id),
            "name": e.name,
            "entity_type": e.entity_type,
            "is_guarantor": e.is_guarantor,
            "is_borrower": e.is_borrower,
            "parent_id": str(e.parent_id) if e.parent_id else None,
        })

    return {
        "data": {
            "start": {"type": "company", "id": start_id.upper(), "name": company.name},
            "traversal": {
                "relationship": "subsidiaries",
                "entities": entity_list,
            },
            "summary": {"entity_count": len(entity_list)}
        }
    }


async def _batch_search_pricing(params: dict, db: AsyncSession) -> dict:
    """Execute search.pricing primitive."""
    ticker = params.get("ticker")
    cusip = params.get("cusip")
    min_ytm = params.get("min_ytm")
    max_ytm = params.get("max_ytm")
    limit = min(params.get("limit", 50), 100)
    offset = params.get("offset", 0)

    query = select(BondPricing, DebtInstrument, Company).join(
        DebtInstrument, BondPricing.debt_instrument_id == DebtInstrument.id
    ).join(
        Company, DebtInstrument.company_id == Company.id
    ).where(BondPricing.last_price.isnot(None)).where(
        BondPricing.price_source == "TRACE"  # Only actual market data, not estimated
    )

    filters = []

    ticker_list = parse_comma_list(ticker) if ticker else []
    if ticker_list:
        filters.append(Company.ticker.in_(ticker_list))

    cusip_list = parse_comma_list(cusip) if cusip else []
    if cusip_list:
        filters.append(BondPricing.cusip.in_(cusip_list))

    if min_ytm is not None:
        filters.append(BondPricing.ytm_bps >= int(min_ytm * 100))
    if max_ytm is not None:
        filters.append(BondPricing.ytm_bps <= int(max_ytm * 100))

    if filters:
        query = query.where(and_(*filters))

    query = query.order_by(desc(BondPricing.ytm_bps)).offset(offset).limit(limit)

    result = await db.execute(query)
    rows = result.all()

    data = []
    for pricing, debt, company in rows:
        data.append({
            "cusip": pricing.cusip or debt.cusip,
            "bond_name": debt.name,
            "company_ticker": company.ticker,
            "last_price": float(pricing.last_price) if pricing.last_price else None,
            "ytm": pricing.ytm_bps / 100 if pricing.ytm_bps else None,
            "spread_bps": pricing.spread_to_treasury_bps,
            "maturity_date": debt.maturity_date.isoformat() if debt.maturity_date else None,
        })

    return {"data": data, "meta": {"limit": limit, "offset": offset}}


async def _batch_search_documents(params: dict, db: AsyncSession) -> dict:
    """Execute search.documents primitive."""
    q = params.get("q")
    if not q:
        raise HTTPException(
            status_code=400,
            detail={"code": "MISSING_PARAMETER", "message": "q (search query) is required"}
        )

    ticker = params.get("ticker")
    doc_type = params.get("doc_type")
    section_type = params.get("section_type")
    limit = min(params.get("limit", 50), 100)
    offset = params.get("offset", 0)

    from sqlalchemy import text

    # Build dynamic WHERE conditions
    conditions = []
    query_params = {"query": q, "limit": limit, "offset": offset}

    ticker_list = parse_comma_list(ticker) if ticker else []
    if ticker_list:
        conditions.append("c.ticker = ANY(:tickers)")
        query_params["tickers"] = ticker_list

    if doc_type:
        doc_types = parse_comma_list(doc_type)
        conditions.append("ds.doc_type = ANY(:doc_types)")
        query_params["doc_types"] = doc_types

    if section_type:
        section_types = parse_comma_list(section_type, uppercase=False)
        conditions.append("ds.section_type = ANY(:section_types)")
        query_params["section_types"] = section_types

    where_clause = ""
    if conditions:
        where_clause = " AND " + " AND ".join(conditions)

    full_query = text(f"""
        SELECT
            ds.id,
            c.ticker,
            ds.doc_type,
            ds.section_type,
            ts_rank_cd(ds.search_vector, plainto_tsquery('english', :query)) as relevance_score,
            ts_headline('english', ds.content, plainto_tsquery('english', :query),
                'MaxWords=30, MinWords=10, MaxFragments=1, StartSel=<b>, StopSel=</b>') as snippet
        FROM document_sections ds
        JOIN companies c ON ds.company_id = c.id
        WHERE ds.search_vector @@ plainto_tsquery('english', :query)
        {where_clause}
        ORDER BY relevance_score DESC
        LIMIT :limit OFFSET :offset
    """)

    result = await db.execute(full_query, query_params)
    rows = result.fetchall()

    data = []
    for row in rows:
        data.append({
            "id": str(row.id),
            "ticker": row.ticker,
            "doc_type": row.doc_type,
            "section_type": row.section_type,
            "relevance_score": round(float(row.relevance_score), 4) if row.relevance_score else 0,
            "snippet": row.snippet,
        })

    return {"data": data, "meta": {"query": q, "limit": limit, "offset": offset}}


# =============================================================================
# PRIMITIVE 8: search.financials
# =============================================================================


@router.get("/financials", tags=["Primitives"])
async def search_financials(
    # Company filters
    ticker: Optional[str] = Query(None, description="Company ticker(s), comma-separated"),
    sector: Optional[str] = Query(None, description="Company sector"),
    # Period filters
    fiscal_year: Optional[int] = Query(None, description="Fiscal year (e.g., 2025)"),
    fiscal_quarter: Optional[int] = Query(None, ge=1, le=4, description="Fiscal quarter (1-4)"),
    period: Optional[str] = Query(None, description="Period shorthand: TTM, latest, or specific (e.g., 2025Q3)"),
    filing_type: Optional[str] = Query(None, description="Filing type: 10-K, 10-Q"),
    # Date filters
    period_after: Optional[date] = Query(None, description="Period end after date"),
    period_before: Optional[date] = Query(None, description="Period end before date"),
    # Metric filters
    min_revenue: Optional[int] = Query(None, description="Minimum revenue (cents)"),
    min_ebitda: Optional[int] = Query(None, description="Minimum EBITDA (cents)"),
    min_cash: Optional[int] = Query(None, description="Minimum cash (cents)"),
    # Field selection
    fields: Optional[str] = Query(None, description="Comma-separated fields to return"),
    # Sorting
    sort: str = Query("-period_end_date", description="Sort field (prefix - for desc)"),
    # Pagination
    limit: int = Query(50, ge=1, le=200, description="Results per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    # Export format
    format: str = Query("json", description="Response format: json or csv"),
    # ETag support
    if_none_match: Optional[str] = Header(None, description="ETag for conditional request"),
    db: AsyncSession = Depends(get_db),
):
    """
    Search quarterly financial statements across all companies.

    Returns income statement, balance sheet, and cash flow data from 10-K/10-Q filings.
    All amounts are in **cents** (divide by 100 for dollars, by 100_000_000_000 for billions).

    **Example:** Get TTM financials for AAPL:
    ```
    GET /v1/financials?ticker=AAPL&period=TTM
    ```

    **Example:** Get all Q3 2025 financials for Tech sector:
    ```
    GET /v1/financials?sector=Technology&fiscal_year=2025&fiscal_quarter=3
    ```

    **Example:** Export to CSV for analysis:
    ```
    GET /v1/financials?ticker=AAPL,MSFT,GOOGL&period=TTM&format=csv
    ```

    **Period shortcuts:**
    - `TTM`: Trailing Twelve Months (sums last 4 quarters for 10-Q, or uses 10-K annual)
    - `latest`: Most recent quarter only
    - `2025Q3`: Specific quarter
    """
    selected_fields = parse_fields(fields, FINANCIALS_FIELDS)

    # Build base query
    query = select(CompanyFinancials, Company).join(
        Company, CompanyFinancials.company_id == Company.id
    )
    count_query = select(func.count()).select_from(CompanyFinancials).join(
        Company, CompanyFinancials.company_id == Company.id
    )

    filters = []

    # Ticker filter
    ticker_list = parse_comma_list(ticker)
    if ticker_list:
        filters.append(Company.ticker.in_(ticker_list))

    # Sector filter
    if sector:
        filters.append(Company.sector.ilike(f"%{sector}%"))

    # Period filters
    if fiscal_year:
        filters.append(CompanyFinancials.fiscal_year == fiscal_year)
    if fiscal_quarter:
        filters.append(CompanyFinancials.fiscal_quarter == fiscal_quarter)
    if filing_type:
        filters.append(CompanyFinancials.filing_type == filing_type.upper())

    # Date filters
    if period_after:
        filters.append(CompanyFinancials.period_end_date >= period_after)
    if period_before:
        filters.append(CompanyFinancials.period_end_date <= period_before)

    # Metric filters
    if min_revenue is not None:
        filters.append(CompanyFinancials.revenue >= min_revenue)
    if min_ebitda is not None:
        filters.append(CompanyFinancials.ebitda >= min_ebitda)
    if min_cash is not None:
        filters.append(CompanyFinancials.cash_and_equivalents >= min_cash)

    # Apply filters
    if filters:
        query = query.where(and_(*filters))
        count_query = count_query.where(and_(*filters))

    # Handle special period shortcuts
    if period:
        period_upper = period.upper()
        if period_upper == "LATEST":
            # Get only the most recent quarter per company
            subquery = (
                select(
                    CompanyFinancials.company_id,
                    func.max(CompanyFinancials.period_end_date).label("max_date")
                )
                .group_by(CompanyFinancials.company_id)
                .subquery()
            )
            query = query.join(
                subquery,
                and_(
                    CompanyFinancials.company_id == subquery.c.company_id,
                    CompanyFinancials.period_end_date == subquery.c.max_date
                )
            )
        elif period_upper == "TTM":
            # TTM will be computed in post-processing
            pass
        elif "Q" in period_upper:
            # Parse specific quarter like "2025Q3"
            try:
                year = int(period_upper[:4])
                quarter = int(period_upper[5])
                filters.append(CompanyFinancials.fiscal_year == year)
                filters.append(CompanyFinancials.fiscal_quarter == quarter)
                query = query.where(and_(
                    CompanyFinancials.fiscal_year == year,
                    CompanyFinancials.fiscal_quarter == quarter
                ))
            except (ValueError, IndexError):
                raise HTTPException(
                    status_code=400,
                    detail={"code": "INVALID_PERIOD", "message": f"Invalid period format: {period}. Use TTM, latest, or YYYYQN (e.g., 2025Q3)"}
                )

    # Sorting
    sort_column_map = {
        "period_end_date": CompanyFinancials.period_end_date,
        "ticker": Company.ticker,
        "revenue": CompanyFinancials.revenue,
        "ebitda": CompanyFinancials.ebitda,
        "total_debt": CompanyFinancials.total_debt,
        "cash": CompanyFinancials.cash_and_equivalents,
        "fiscal_year": CompanyFinancials.fiscal_year,
    }
    query = apply_sort(query, sort, sort_column_map, CompanyFinancials.period_end_date)

    # Count
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    # Pagination
    query = query.limit(limit).offset(offset)

    # Execute
    result = await db.execute(query)
    rows = result.all()

    # Handle TTM aggregation
    if period and period.upper() == "TTM":
        # Group by company and compute TTM sums
        ttm_data = await _compute_ttm_financials(db, ticker_list, selected_fields)
        if format.lower() == "csv":
            return to_csv_response(ttm_data, filename="financials_ttm.csv")
        return etag_response({"data": ttm_data, "meta": {"total": len(ttm_data), "period": "TTM"}}, if_none_match)

    # Build response
    data = []
    for row in rows:
        fin, company = row

        # Compute derived metrics
        free_cash_flow = None
        if fin.operating_cash_flow is not None and fin.capex is not None:
            free_cash_flow = fin.operating_cash_flow - abs(fin.capex)

        gross_margin = None
        if fin.gross_profit is not None and fin.revenue and fin.revenue > 0:
            gross_margin = round(fin.gross_profit / fin.revenue * 100, 2)

        operating_margin = None
        if fin.operating_income is not None and fin.revenue and fin.revenue > 0:
            operating_margin = round(fin.operating_income / fin.revenue * 100, 2)

        net_margin = None
        if fin.net_income is not None and fin.revenue and fin.revenue > 0:
            net_margin = round(fin.net_income / fin.revenue * 100, 2)

        fin_data = {
            "ticker": company.ticker,
            "company_name": company.name,
            "fiscal_year": fin.fiscal_year,
            "fiscal_quarter": fin.fiscal_quarter,
            "period_end_date": fin.period_end_date.isoformat() if fin.period_end_date else None,
            "filing_type": fin.filing_type,
            # Income Statement
            "revenue": fin.revenue,
            "cost_of_revenue": fin.cost_of_revenue,
            "gross_profit": fin.gross_profit,
            "operating_income": fin.operating_income,
            "ebitda": fin.ebitda,
            "ebitda_type": fin.ebitda_type,  # "ebitda" or "ppnr" (for banks)
            "interest_expense": fin.interest_expense,
            "net_income": fin.net_income,
            "depreciation_amortization": fin.depreciation_amortization,
            # Bank-specific (null for non-banks)
            "net_interest_income": fin.net_interest_income,
            "non_interest_income": fin.non_interest_income,
            "provision_for_credit_losses": fin.provision_for_credit_losses,
            # Balance Sheet
            "cash": fin.cash_and_equivalents,
            "total_current_assets": fin.total_current_assets,
            "total_assets": fin.total_assets,
            "total_current_liabilities": fin.total_current_liabilities,
            "total_debt": fin.total_debt,
            "total_liabilities": fin.total_liabilities,
            "stockholders_equity": fin.stockholders_equity,
            # Cash Flow
            "operating_cash_flow": fin.operating_cash_flow,
            "investing_cash_flow": fin.investing_cash_flow,
            "financing_cash_flow": fin.financing_cash_flow,
            "capex": fin.capex,
            # Derived
            "free_cash_flow": free_cash_flow,
            "gross_margin": gross_margin,
            "operating_margin": operating_margin,
            "net_margin": net_margin,
        }

        data.append(filter_dict(fin_data, selected_fields))

    # Return CSV if requested
    if format.lower() == "csv":
        return to_csv_response(data, filename="financials.csv")

    response_data = {
        "data": data,
        "meta": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "data_sources": {
                "all_fields": "Extracted from SEC 10-K and 10-Q filings",
                "total_debt": "Balance sheet total debt (short-term + long-term)",
                "ebitda": "Operating income + D&A; for banks (ebitda_type='ppnr'), this is Pre-Provision Net Revenue",
                "ebitda_type": "'ebitda' for operating companies, 'ppnr' for banks/financial institutions",
            },
        }
    }
    return etag_response(response_data, if_none_match)


async def _compute_ttm_financials(db: AsyncSession, ticker_list: List[str], selected_fields: Optional[Set[str]]) -> List[dict]:
    """Compute TTM (Trailing Twelve Months) financials for companies."""
    # Get latest 4 quarters for each company
    if ticker_list:
        companies_query = select(Company).where(Company.ticker.in_(ticker_list))
    else:
        companies_query = select(Company)

    companies_result = await db.execute(companies_query)
    companies = companies_result.scalars().all()

    ttm_data = []
    for company in companies:
        # Get the most recent financial record to check filing type
        latest_query = select(CompanyFinancials).where(
            CompanyFinancials.company_id == company.id
        ).order_by(desc(CompanyFinancials.period_end_date)).limit(1)

        latest_result = await db.execute(latest_query)
        latest = latest_result.scalar_one_or_none()

        if not latest:
            continue

        # If latest is 10-K, use it directly (already annual)
        if latest.filing_type == "10-K":
            fin_data = {
                "ticker": company.ticker,
                "company_name": company.name,
                "period": "TTM",
                "ttm_source": "10-K",
                "period_end_date": latest.period_end_date.isoformat() if latest.period_end_date else None,
                "revenue": latest.revenue,
                "ebitda": latest.ebitda,
                "operating_income": latest.operating_income,
                "net_income": latest.net_income,
                "interest_expense": latest.interest_expense,
                "depreciation_amortization": latest.depreciation_amortization,
                "cash": latest.cash_and_equivalents,
                "total_assets": latest.total_assets,
                "total_debt": latest.total_debt,
                "total_liabilities": latest.total_liabilities,
                "stockholders_equity": latest.stockholders_equity,
                "operating_cash_flow": latest.operating_cash_flow,
                "capex": latest.capex,
            }
            # Compute free cash flow
            if latest.operating_cash_flow is not None and latest.capex is not None:
                fin_data["free_cash_flow"] = latest.operating_cash_flow - abs(latest.capex)

            ttm_data.append(filter_dict(fin_data, selected_fields) if selected_fields else fin_data)
        else:
            # Sum last 4 quarters of 10-Q data
            quarters_query = select(CompanyFinancials).where(
                CompanyFinancials.company_id == company.id
            ).order_by(desc(CompanyFinancials.period_end_date)).limit(4)

            quarters_result = await db.execute(quarters_query)
            quarters = quarters_result.scalars().all()

            if not quarters:
                continue

            # Sum income statement items (flow items)
            def safe_sum(items):
                filtered = [x for x in items if x is not None]
                return sum(filtered) if filtered else None

            revenue = safe_sum([q.revenue for q in quarters])
            ebitda = safe_sum([q.ebitda for q in quarters])
            operating_income = safe_sum([q.operating_income for q in quarters])
            net_income = safe_sum([q.net_income for q in quarters])
            interest_expense = safe_sum([q.interest_expense for q in quarters])
            da = safe_sum([q.depreciation_amortization for q in quarters])
            ocf = safe_sum([q.operating_cash_flow for q in quarters])
            capex = safe_sum([q.capex for q in quarters])

            # Balance sheet items: use most recent (not summed)
            most_recent = quarters[0]

            fin_data = {
                "ticker": company.ticker,
                "company_name": company.name,
                "period": "TTM",
                "ttm_source": f"{len(quarters)}_quarters",
                "ttm_quarters": len(quarters),
                "period_end_date": most_recent.period_end_date.isoformat() if most_recent.period_end_date else None,
                "revenue": revenue,
                "ebitda": ebitda,
                "operating_income": operating_income,
                "net_income": net_income,
                "interest_expense": interest_expense,
                "depreciation_amortization": da,
                "cash": most_recent.cash_and_equivalents,
                "total_assets": most_recent.total_assets,
                "total_debt": most_recent.total_debt,
                "total_liabilities": most_recent.total_liabilities,
                "stockholders_equity": most_recent.stockholders_equity,
                "operating_cash_flow": ocf,
                "capex": capex,
            }
            # Compute free cash flow
            if ocf is not None and capex is not None:
                fin_data["free_cash_flow"] = ocf - abs(capex)

            ttm_data.append(filter_dict(fin_data, selected_fields) if selected_fields else fin_data)

    return ttm_data


# =============================================================================
# PRIMITIVE 9: search.collateral
# =============================================================================


@router.get("/collateral", tags=["Primitives"])
async def search_collateral(
    # Company/instrument filters
    ticker: Optional[str] = Query(None, description="Company ticker(s), comma-separated"),
    debt_id: Optional[str] = Query(None, description="Debt instrument ID(s), comma-separated"),
    cusip: Optional[str] = Query(None, description="Bond CUSIP(s), comma-separated"),
    # Collateral filters
    collateral_type: Optional[str] = Query(None, description="Type: real_estate, equipment, receivables, inventory, securities, vehicles, ip, cash, general_lien"),
    priority: Optional[str] = Query(None, description="Priority: first_lien, second_lien"),
    has_valuation: Optional[bool] = Query(None, description="Has estimated value"),
    min_value: Optional[int] = Query(None, description="Minimum estimated value (cents)"),
    # Instrument filters
    seniority: Optional[str] = Query(None, description="Bond seniority: senior_secured, senior_unsecured"),
    security_type: Optional[str] = Query(None, description="Security type: first_lien, second_lien"),
    # Field selection
    fields: Optional[str] = Query(None, description="Comma-separated fields to return"),
    # Sorting
    sort: str = Query("collateral_type", description="Sort field (prefix - for desc)"),
    # Pagination
    limit: int = Query(50, ge=1, le=200, description="Results per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    # Export format
    format: str = Query("json", description="Response format: json or csv"),
    # ETag support
    if_none_match: Optional[str] = Header(None, description="ETag for conditional request"),
    db: AsyncSession = Depends(get_db),
):
    """
    Search collateral securing debt instruments.

    Returns collateral items with types, descriptions, estimated values, and priority.
    Use for recovery analysis and LTV calculations.

    **Collateral Types:**
    - `real_estate`: Property, buildings, land
    - `equipment`: Machinery, manufacturing equipment
    - `receivables`: Accounts receivable, trade receivables
    - `inventory`: Stock, finished goods
    - `securities`: Stocks, bonds, other securities
    - `vehicles`: Fleet, aircraft, vessels
    - `ip`: Patents, trademarks, intellectual property
    - `cash`: Cash deposits, restricted cash
    - `general_lien`: Blanket lien on all assets

    **Example:** Find all first-lien collateral for RIG:
    ```
    GET /v1/collateral?ticker=RIG&priority=first_lien
    ```

    **Example:** Find equipment collateral with valuations:
    ```
    GET /v1/collateral?collateral_type=equipment&has_valuation=true
    ```

    **Example:** Get collateral for a specific bond:
    ```
    GET /v1/collateral?cusip=893830AK8
    ```
    """
    selected_fields = parse_fields(fields, COLLATERAL_FIELDS)

    # Build query with joins
    query = select(Collateral, DebtInstrument, Company).join(
        DebtInstrument, Collateral.debt_instrument_id == DebtInstrument.id
    ).join(
        Company, DebtInstrument.company_id == Company.id
    )
    count_query = select(func.count()).select_from(Collateral).join(
        DebtInstrument, Collateral.debt_instrument_id == DebtInstrument.id
    ).join(
        Company, DebtInstrument.company_id == Company.id
    )

    filters = []

    # Company filters
    ticker_list = parse_comma_list(ticker)
    if ticker_list:
        filters.append(Company.ticker.in_(ticker_list))

    # Debt instrument filters
    if debt_id:
        debt_ids = [d.strip() for d in debt_id.split(",") if d.strip()]
        try:
            debt_uuids = [UUID(d) for d in debt_ids]
            filters.append(Collateral.debt_instrument_id.in_(debt_uuids))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"code": "INVALID_UUID", "message": "Invalid debt_id format. Must be UUID."}
            )

    cusip_list = parse_comma_list(cusip)
    if cusip_list:
        filters.append(DebtInstrument.cusip.in_(cusip_list))

    # Collateral filters
    if collateral_type:
        type_list = parse_comma_list(collateral_type, uppercase=False)
        filters.append(Collateral.collateral_type.in_(type_list))

    if priority:
        filters.append(Collateral.priority == priority)

    if has_valuation is True:
        filters.append(Collateral.estimated_value.isnot(None))
    elif has_valuation is False:
        filters.append(Collateral.estimated_value.is_(None))

    if min_value is not None:
        filters.append(Collateral.estimated_value >= min_value)

    # Instrument filters
    if seniority:
        filters.append(DebtInstrument.seniority == seniority)
    if security_type:
        filters.append(DebtInstrument.security_type == security_type)

    # Apply filters
    if filters:
        query = query.where(and_(*filters))
        count_query = count_query.where(and_(*filters))

    # Sorting
    sort_column_map = {
        "collateral_type": Collateral.collateral_type,
        "priority": Collateral.priority,
        "estimated_value": Collateral.estimated_value,
        "ticker": Company.ticker,
        "bond_name": DebtInstrument.name,
    }
    query = apply_sort(query, sort, sort_column_map, Collateral.collateral_type)

    # Count
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    # Pagination
    query = query.limit(limit).offset(offset)

    # Execute
    result = await db.execute(query)
    rows = result.all()

    # Build response
    data = []
    for row in rows:
        coll, debt, company = row

        coll_data = {
            "id": str(coll.id),
            "debt_instrument_id": str(coll.debt_instrument_id),
            "bond_name": debt.name,
            "bond_cusip": debt.cusip,
            "company_ticker": company.ticker,
            "company_name": company.name,
            "collateral_type": coll.collateral_type,
            "description": coll.description,
            "estimated_value": coll.estimated_value,
            "priority": coll.priority,
            "instrument_seniority": debt.seniority,
            "instrument_security_type": debt.security_type,
        }

        data.append(filter_dict(coll_data, selected_fields))

    # Return CSV if requested
    if format.lower() == "csv":
        return to_csv_response(data, filename="collateral.csv")

    # Compute summary stats
    total_value = sum(c.get("estimated_value") or 0 for c in data)
    types_found = list(set(c.get("collateral_type") for c in data if c.get("collateral_type")))

    response_data = {
        "data": data,
        "meta": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "total_estimated_value": total_value if total_value > 0 else None,
            "collateral_types": sorted(types_found),
        }
    }
    return etag_response(response_data, if_none_match)


# =============================================================================
# PRIMITIVE 10: changes (diff/changelog)
# =============================================================================


@router.get("/companies/{ticker}/changes", tags=["Primitives"])
async def get_company_changes(
    ticker: str,
    since: date = Query(..., description="Compare changes since this date (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get changes to a company's data since a specified date.

    Compares current data against the nearest snapshot on or before the `since` date.
    Returns deltas including new bonds, entity changes, metric changes, and pricing movements.

    **Example:** Get changes since Q4 2025:
    ```
    GET /v1/companies/CHTR/changes?since=2025-10-01
    ```

    **Response includes:**
    - `new_debt`: Bonds/loans added after the snapshot
    - `removed_debt`: Bonds/loans no longer active
    - `entity_changes`: Subsidiaries added or removed
    - `metric_changes`: Significant changes to leverage, debt totals
    - `pricing_changes`: YTM movements >50bps (if pricing data available)
    """
    ticker = ticker.upper()

    # Get company
    company_result = await db.execute(
        select(Company).where(Company.ticker == ticker)
    )
    company = company_result.scalar_one_or_none()
    if not company:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"Company '{ticker}' not found"}
        )

    # Find nearest snapshot on or before the since date
    snapshot_result = await db.execute(
        select(CompanySnapshot)
        .where(
            CompanySnapshot.company_id == company.id,
            CompanySnapshot.snapshot_date <= since
        )
        .order_by(desc(CompanySnapshot.snapshot_date))
        .limit(1)
    )
    snapshot = snapshot_result.scalar_one_or_none()

    if not snapshot:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NO_SNAPSHOT",
                "message": f"No snapshot found on or before {since}. Earliest snapshot may be after this date."
            }
        )

    # Get current data
    # Current entities
    current_entities_result = await db.execute(
        select(Entity).where(Entity.company_id == company.id)
    )
    current_entities = {str(e.id): e for e in current_entities_result.scalars().all()}

    # Current debt instruments
    current_debt_result = await db.execute(
        select(DebtInstrument).where(
            DebtInstrument.company_id == company.id,
            DebtInstrument.is_active == True
        )
    )
    current_debt = {str(d.id): d for d in current_debt_result.scalars().all()}

    # Current metrics
    current_metrics_result = await db.execute(
        select(CompanyMetrics).where(CompanyMetrics.company_id == company.id)
    )
    current_metrics = current_metrics_result.scalar_one_or_none()

    # Current pricing
    current_pricing = {}
    if current_debt:
        pricing_result = await db.execute(
            select(BondPricing, DebtInstrument)
            .join(DebtInstrument, BondPricing.debt_instrument_id == DebtInstrument.id)
            .where(DebtInstrument.company_id == company.id)
        )
        for pricing, debt in pricing_result.all():
            current_pricing[str(debt.id)] = pricing

    # Parse snapshot data
    snapshot_entities = {e["id"]: e for e in (snapshot.entities_snapshot or [])}
    snapshot_debt = {d["id"]: d for d in (snapshot.debt_snapshot or [])}
    snapshot_metrics = snapshot.metrics_snapshot or {}

    # Calculate changes
    changes = {
        "new_debt": [],
        "removed_debt": [],
        "entity_changes": {
            "added": [],
            "removed": [],
        },
        "metric_changes": [],
        "pricing_changes": [],
    }

    # --- Debt changes ---
    current_debt_ids = set(current_debt.keys())
    snapshot_debt_ids = set(snapshot_debt.keys())

    # New debt
    for debt_id in current_debt_ids - snapshot_debt_ids:
        d = current_debt[debt_id]
        changes["new_debt"].append({
            "id": debt_id,
            "name": d.name,
            "cusip": d.cusip,
            "instrument_type": d.instrument_type,
            "seniority": d.seniority,
            "principal": d.principal,
            "interest_rate": d.interest_rate / 100 if d.interest_rate else None,
            "maturity_date": d.maturity_date.isoformat() if d.maturity_date else None,
            "issue_date": d.issue_date.isoformat() if d.issue_date else None,
        })

    # Removed debt
    for debt_id in snapshot_debt_ids - current_debt_ids:
        d = snapshot_debt[debt_id]
        changes["removed_debt"].append({
            "id": debt_id,
            "name": d.get("name"),
            "cusip": d.get("cusip"),
            "instrument_type": d.get("instrument_type"),
            "maturity_date": d.get("maturity_date"),
        })

    # --- Entity changes ---
    current_entity_ids = set(current_entities.keys())
    snapshot_entity_ids = set(snapshot_entities.keys())

    # Added entities
    for entity_id in current_entity_ids - snapshot_entity_ids:
        e = current_entities[entity_id]
        changes["entity_changes"]["added"].append({
            "id": entity_id,
            "name": e.name,
            "entity_type": e.entity_type,
            "is_guarantor": e.is_guarantor,
            "jurisdiction": e.jurisdiction,
        })

    # Removed entities
    for entity_id in snapshot_entity_ids - current_entity_ids:
        e = snapshot_entities[entity_id]
        changes["entity_changes"]["removed"].append({
            "id": entity_id,
            "name": e.get("name"),
            "entity_type": e.get("entity_type"),
        })

    # --- Metric changes ---
    if current_metrics and snapshot_metrics:
        # Total debt change
        current_total_debt = current_metrics.total_debt or 0
        snapshot_total_debt = snapshot_metrics.get("total_debt") or 0
        if current_total_debt != snapshot_total_debt:
            debt_change = current_total_debt - snapshot_total_debt
            debt_change_pct = (debt_change / snapshot_total_debt * 100) if snapshot_total_debt else None
            if abs(debt_change) > 100_000_000_00:  # >$1B change
                changes["metric_changes"].append({
                    "metric": "total_debt",
                    "previous": snapshot_total_debt,
                    "current": current_total_debt,
                    "change": debt_change,
                    "change_pct": round(debt_change_pct, 1) if debt_change_pct else None,
                })

        # Leverage ratio change
        current_leverage = float(current_metrics.leverage_ratio) if current_metrics.leverage_ratio else None
        snapshot_leverage = snapshot_metrics.get("leverage_ratio")
        if current_leverage and snapshot_leverage:
            leverage_change = current_leverage - snapshot_leverage
            if abs(leverage_change) > 0.5:  # >0.5x change
                changes["metric_changes"].append({
                    "metric": "leverage_ratio",
                    "previous": snapshot_leverage,
                    "current": current_leverage,
                    "change": round(leverage_change, 2),
                })

        # Subordination risk change
        current_sub_risk = current_metrics.subordination_risk
        snapshot_sub_risk = snapshot_metrics.get("subordination_risk")
        if current_sub_risk != snapshot_sub_risk:
            changes["metric_changes"].append({
                "metric": "subordination_risk",
                "previous": snapshot_sub_risk,
                "current": current_sub_risk,
            })

    # --- Pricing changes (>50bps YTM movement) ---
    # Note: We don't have historical pricing in snapshots yet, so this compares
    # against any pricing stored in the snapshot's debt data (if available)
    for debt_id, pricing in current_pricing.items():
        if pricing.ytm_bps:
            # For now, just report current pricing for bonds that exist in both
            if debt_id in snapshot_debt:
                changes["pricing_changes"].append({
                    "debt_id": debt_id,
                    "name": current_debt[debt_id].name if debt_id in current_debt else None,
                    "cusip": pricing.cusip,
                    "current_ytm": pricing.ytm_bps / 100,
                    "current_price": float(pricing.last_price) if pricing.last_price else None,
                    "note": "Historical pricing comparison not yet available",
                })

    # Build response
    response = {
        "ticker": ticker,
        "company_name": company.name,
        "since": since.isoformat(),
        "snapshot_date": snapshot.snapshot_date.isoformat(),
        "snapshot_type": snapshot.snapshot_type,
        "changes": changes,
        "summary": {
            "new_debt_count": len(changes["new_debt"]),
            "removed_debt_count": len(changes["removed_debt"]),
            "entities_added": len(changes["entity_changes"]["added"]),
            "entities_removed": len(changes["entity_changes"]["removed"]),
            "metric_changes_count": len(changes["metric_changes"]),
            "has_changes": any([
                changes["new_debt"],
                changes["removed_debt"],
                changes["entity_changes"]["added"],
                changes["entity_changes"]["removed"],
                changes["metric_changes"],
            ])
        }
    }

    return response


# =============================================================================
# PRIMITIVE 11: search.covenants
# =============================================================================


@router.get("/covenants", tags=["Primitives"])
async def search_covenants(
    # Company/instrument filters
    ticker: Optional[str] = Query(None, description="Company ticker(s), comma-separated"),
    debt_id: Optional[str] = Query(None, description="Debt instrument ID(s), comma-separated"),
    cusip: Optional[str] = Query(None, description="Bond CUSIP(s), comma-separated"),
    # Covenant type filters
    covenant_type: Optional[str] = Query(None, description="Type: financial, negative, incurrence, protective"),
    covenant_name: Optional[str] = Query(None, description="Covenant name (partial match)"),
    test_metric: Optional[str] = Query(None, description="Financial metric: leverage_ratio, first_lien_leverage, interest_coverage, fixed_charge_coverage"),
    # Threshold filters (for financial covenants)
    max_threshold: Optional[float] = Query(None, description="Maximum threshold value (e.g., 5.0 for <=5.0x leverage)"),
    min_threshold: Optional[float] = Query(None, description="Minimum threshold value"),
    threshold_type: Optional[str] = Query(None, description="Threshold type: maximum, minimum"),
    # Quality filters
    min_confidence: Optional[float] = Query(None, ge=0, le=1, description="Minimum extraction confidence (0-1)"),
    # Field selection
    fields: Optional[str] = Query(None, description="Comma-separated fields to return"),
    # Sorting
    sort: str = Query("covenant_type", description="Sort field (prefix - for desc)"),
    # Pagination
    limit: int = Query(50, ge=1, le=200, description="Results per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    # Export format
    format: str = Query("json", description="Response format: json or csv"),
    # ETag support
    if_none_match: Optional[str] = Header(None, description="ETag for conditional request"),
    db: AsyncSession = Depends(get_db),
):
    """
    Search structured covenant data across companies and instruments.

    Returns extracted covenant information from credit agreements and indentures,
    including financial maintenance covenants, negative covenants, and change of control provisions.

    **Covenant Types:**
    - `financial`: Leverage ratios, coverage ratios with numerical thresholds
    - `negative`: Restrictions on liens, debt, payments, asset sales
    - `incurrence`: Tests that apply when taking new debt or actions
    - `protective`: Change of control, make-whole provisions

    **Financial Metrics:**
    - `leverage_ratio`: Total Debt / EBITDA
    - `first_lien_leverage`: First Lien Debt / EBITDA
    - `secured_leverage`: Secured Debt / EBITDA
    - `net_leverage_ratio`: Net Debt / EBITDA
    - `interest_coverage`: EBITDA / Interest Expense
    - `fixed_charge_coverage`: (EBITDA - CapEx) / Fixed Charges

    **Example:** Get all financial covenants for CHTR:
    ```
    GET /v1/covenants?ticker=CHTR&covenant_type=financial
    ```

    **Example:** Find companies with leverage ratio thresholds <=5x:
    ```
    GET /v1/covenants?test_metric=leverage_ratio&max_threshold=5.0
    ```

    **Example:** Find change of control covenants:
    ```
    GET /v1/covenants?covenant_name=change_of_control
    ```

    **Example:** Export to CSV:
    ```
    GET /v1/covenants?ticker=CHTR,ATUS&format=csv
    ```
    """
    selected_fields = parse_fields(fields, COVENANT_FIELDS)

    # Build query with optional joins
    query = select(Covenant, Company, DebtInstrument).join(
        Company, Covenant.company_id == Company.id
    ).outerjoin(
        DebtInstrument, Covenant.debt_instrument_id == DebtInstrument.id
    )
    count_query = select(func.count()).select_from(Covenant).join(
        Company, Covenant.company_id == Company.id
    ).outerjoin(
        DebtInstrument, Covenant.debt_instrument_id == DebtInstrument.id
    )

    filters = []

    # Company filters
    ticker_list = parse_comma_list(ticker)
    if ticker_list:
        filters.append(Company.ticker.in_(ticker_list))

    # Debt instrument filters
    if debt_id:
        debt_ids = [d.strip() for d in debt_id.split(",") if d.strip()]
        try:
            debt_uuids = [UUID(d) for d in debt_ids]
            filters.append(Covenant.debt_instrument_id.in_(debt_uuids))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"code": "INVALID_UUID", "message": "Invalid debt_id format. Must be UUID."}
            )

    cusip_list = parse_comma_list(cusip)
    if cusip_list:
        filters.append(DebtInstrument.cusip.in_(cusip_list))

    # Covenant type filters
    if covenant_type:
        type_list = parse_comma_list(covenant_type, uppercase=False)
        filters.append(Covenant.covenant_type.in_(type_list))

    if covenant_name:
        filters.append(Covenant.covenant_name.ilike(f"%{covenant_name}%"))

    if test_metric:
        metric_list = parse_comma_list(test_metric, uppercase=False)
        filters.append(Covenant.test_metric.in_(metric_list))

    # Threshold filters
    if max_threshold is not None:
        filters.append(Covenant.threshold_value <= max_threshold)
    if min_threshold is not None:
        filters.append(Covenant.threshold_value >= min_threshold)
    if threshold_type:
        filters.append(Covenant.threshold_type == threshold_type)

    # Quality filters
    if min_confidence is not None:
        filters.append(Covenant.extraction_confidence >= min_confidence)

    # Apply filters
    if filters:
        query = query.where(and_(*filters))
        count_query = count_query.where(and_(*filters))

    # Sorting
    sort_column_map = {
        "covenant_type": Covenant.covenant_type,
        "covenant_name": Covenant.covenant_name,
        "test_metric": Covenant.test_metric,
        "threshold_value": Covenant.threshold_value,
        "ticker": Company.ticker,
        "confidence": Covenant.extraction_confidence,
        "created_at": Covenant.created_at,
    }
    query = apply_sort(query, sort, sort_column_map, Covenant.covenant_type)

    # Count
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    # Pagination
    query = query.limit(limit).offset(offset)

    # Execute
    result = await db.execute(query)
    rows = result.all()

    # Build response
    data = []
    for row in rows:
        covenant, company, debt_instrument = row

        cov_data = {
            "id": str(covenant.id),
            "ticker": company.ticker,
            "company_name": company.name,
            "instrument_name": debt_instrument.name if debt_instrument else None,
            "cusip": debt_instrument.cusip if debt_instrument else None,
            "covenant_type": covenant.covenant_type,
            "covenant_name": covenant.covenant_name,
            "test_metric": covenant.test_metric,
            "threshold_value": float(covenant.threshold_value) if covenant.threshold_value else None,
            "threshold_type": covenant.threshold_type,
            "test_frequency": covenant.test_frequency,
            "description": covenant.description,
            "has_step_down": covenant.has_step_down,
            "step_down_schedule": covenant.step_down_schedule,
            "cure_period_days": covenant.cure_period_days,
            "put_price_pct": float(covenant.put_price_pct) if covenant.put_price_pct else None,
            "extraction_confidence": float(covenant.extraction_confidence) if covenant.extraction_confidence else None,
            "source_document_date": None,  # Would need to join source_document for this
        }

        data.append(filter_dict(cov_data, selected_fields))

    # Return CSV if requested
    if format.lower() == "csv":
        return to_csv_response(data, filename="covenants.csv")

    # Compute summary stats
    types_found = list(set(c.get("covenant_type") for c in data if c.get("covenant_type")))
    metrics_found = list(set(c.get("test_metric") for c in data if c.get("test_metric")))

    response_data = {
        "data": data,
        "meta": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "covenant_types": sorted(types_found),
            "test_metrics": sorted(metrics_found) if metrics_found else None,
        }
    }
    return etag_response(response_data, if_none_match)


# =============================================================================
# PRIMITIVE 12: compare.covenants
# =============================================================================


@router.get("/covenants/compare", tags=["Primitives"])
async def compare_covenants(
    # Required: tickers to compare
    ticker: str = Query(..., description="Company tickers to compare, comma-separated (2-10 tickers)"),
    # Filter by metric
    test_metric: Optional[str] = Query(None, description="Financial metric to compare: leverage_ratio, first_lien_leverage, interest_coverage"),
    covenant_type: Optional[str] = Query(None, description="Covenant type: financial, negative, incurrence, protective"),
    # ETag support
    if_none_match: Optional[str] = Header(None, description="ETag for conditional request"),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Compare covenants across multiple companies.

    **Business tier only** - Returns 403 for Pay-as-You-Go and Pro users.

    Returns a side-by-side comparison of covenant data for the specified companies.
    Useful for peer analysis and relative value assessment.

    **Example:** Compare leverage ratios across cable companies:
    ```
    GET /v1/covenants/compare?ticker=CHTR,ATUS,LUMN&test_metric=leverage_ratio
    ```

    **Example:** Compare all financial covenants:
    ```
    GET /v1/covenants/compare?ticker=CHTR,ATUS&covenant_type=financial
    ```

    **Response includes:**
    - Per-company covenant summary
    - Direct comparison of matching covenants
    - Covenant presence matrix (which company has which covenant)
    """
    # Check Business tier access
    allowed, error_msg = check_tier_access(user, "/v1/covenants/compare")
    if not allowed:
        raise HTTPException(status_code=403, detail=error_msg)
    ticker_list = parse_comma_list(ticker)
    if not ticker_list:
        raise HTTPException(
            status_code=400,
            detail={"code": "MISSING_TICKERS", "message": "At least one ticker is required"}
        )
    if len(ticker_list) > 10:
        raise HTTPException(
            status_code=400,
            detail={"code": "TOO_MANY_TICKERS", "message": "Maximum 10 tickers for comparison"}
        )

    # Get companies
    companies_result = await db.execute(
        select(Company).where(Company.ticker.in_(ticker_list))
    )
    companies = {c.ticker: c for c in companies_result.scalars().all()}

    missing = set(ticker_list) - set(companies.keys())
    if missing:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"Companies not found: {', '.join(sorted(missing))}"}
        )

    # Build query for covenants
    filters = [Company.ticker.in_(ticker_list)]

    if test_metric:
        metric_list = parse_comma_list(test_metric, uppercase=False)
        filters.append(Covenant.test_metric.in_(metric_list))

    if covenant_type:
        type_list = parse_comma_list(covenant_type, uppercase=False)
        filters.append(Covenant.covenant_type.in_(type_list))

    query = select(Covenant, Company).join(
        Company, Covenant.company_id == Company.id
    ).where(and_(*filters)).order_by(
        Company.ticker, Covenant.covenant_type, Covenant.covenant_name
    )

    result = await db.execute(query)
    rows = result.all()

    # Organize by company
    by_company = {t: [] for t in ticker_list}
    for covenant, company in rows:
        cov_data = {
            "covenant_type": covenant.covenant_type,
            "covenant_name": covenant.covenant_name,
            "test_metric": covenant.test_metric,
            "threshold_value": float(covenant.threshold_value) if covenant.threshold_value else None,
            "threshold_type": covenant.threshold_type,
            "test_frequency": covenant.test_frequency,
            "has_step_down": covenant.has_step_down,
            "put_price_pct": float(covenant.put_price_pct) if covenant.put_price_pct else None,
        }
        by_company[company.ticker].append(cov_data)

    # Build comparison matrix for specific metrics
    comparison_matrix = []
    if test_metric:
        # For each metric, create a row comparing all companies
        metric_list = parse_comma_list(test_metric, uppercase=False)
        for metric in metric_list:
            row = {"metric": metric}
            for t in ticker_list:
                # Find the covenant for this metric in this company
                matching = [c for c in by_company[t] if c.get("test_metric") == metric]
                if matching:
                    cov = matching[0]
                    row[t] = {
                        "threshold_value": cov.get("threshold_value"),
                        "threshold_type": cov.get("threshold_type"),
                        "test_frequency": cov.get("test_frequency"),
                    }
                else:
                    row[t] = None
            comparison_matrix.append(row)

    # Build presence matrix (which companies have which covenants)
    all_covenant_names = set()
    for covenants in by_company.values():
        for c in covenants:
            all_covenant_names.add(c.get("covenant_name"))

    presence_matrix = []
    for name in sorted(all_covenant_names):
        row = {"covenant_name": name}
        for t in ticker_list:
            row[t] = any(c.get("covenant_name") == name for c in by_company[t])
        presence_matrix.append(row)

    response_data = {
        "tickers": ticker_list,
        "companies": {
            t: {
                "name": companies[t].name,
                "covenant_count": len(by_company[t]),
                "covenants": by_company[t],
            }
            for t in ticker_list
        },
        "comparison_matrix": comparison_matrix if comparison_matrix else None,
        "presence_matrix": presence_matrix,
        "meta": {
            "tickers_compared": len(ticker_list),
            "total_covenants": sum(len(v) for v in by_company.values()),
            "filter_test_metric": test_metric,
            "filter_covenant_type": covenant_type,
        }
    }

    return etag_response(response_data, if_none_match)
