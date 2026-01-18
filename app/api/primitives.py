"""
Primitives API for DebtStack.ai

6 core primitives optimized for AI agents:
1. GET /v1/companies - Horizontal company search
2. GET /v1/bonds - Horizontal bond search
3. GET /v1/bonds/resolve - Bond identifier resolution
4. POST /v1/entities/traverse - Graph traversal
5. GET /v1/pricing - Bond pricing data
6. GET /v1/documents/search - Full-text search across SEC filings
"""

import csv
import hashlib
import io
import json
import re
from datetime import date, datetime
from typing import Optional, List, Set
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Body, Header, Response
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_, and_, desc, asc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import (
    Company, CompanyMetrics, Entity, DebtInstrument,
    Guarantee, BondPricing, DocumentSection, ExtractionMetadata,
)

router = APIRouter()


# =============================================================================
# CSV EXPORT HELPER
# =============================================================================

def flatten_dict(d: dict, parent_key: str = '', sep: str = '_') -> dict:
    """Flatten nested dictionary for CSV export."""
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


def to_csv_response(data: List[dict], filename: str = "export.csv") -> StreamingResponse:
    """Convert list of dicts to CSV streaming response."""
    if not data:
        # Return empty CSV with just headers
        output = io.StringIO()
        output.write("")
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    # Flatten nested dicts
    flat_data = [flatten_dict(row) for row in data]

    # Get all unique keys across all rows
    all_keys = set()
    for row in flat_data:
        all_keys.update(row.keys())
    fieldnames = sorted(all_keys)

    # Write CSV
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(flat_data)

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# =============================================================================
# ETAG CACHING HELPER
# =============================================================================

def generate_etag(data: dict | list) -> str:
    """Generate ETag from response data using MD5 hash."""
    # Serialize to JSON with sorted keys for consistent hashing
    content = json.dumps(data, sort_keys=True, default=str)
    return hashlib.md5(content.encode()).hexdigest()


def check_etag(if_none_match: Optional[str], etag: str) -> bool:
    """Check if client's ETag matches (return True if 304 should be returned)."""
    if not if_none_match:
        return False
    # Handle multiple ETags in If-None-Match header
    client_etags = [e.strip().strip('"') for e in if_none_match.split(',')]
    return etag in client_etags or '*' in client_etags


def etag_response(data: dict, if_none_match: Optional[str] = None) -> Response:
    """Return JSON response with ETag header, or 304 if unchanged."""
    etag = generate_etag(data)

    if check_etag(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": f'"{etag}"'})

    return JSONResponse(
        content=data,
        headers={"ETag": f'"{etag}"', "Cache-Control": "private, max-age=60"}
    )


# =============================================================================
# FIELD SELECTION HELPER
# =============================================================================

# Available fields for each resource type
COMPANY_FIELDS = {
    "ticker", "name", "sector", "industry", "cik",
    "total_debt", "secured_debt", "unsecured_debt", "net_debt",
    "leverage_ratio", "net_leverage_ratio", "interest_coverage", "secured_leverage",
    "entity_count", "guarantor_count",
    "subordination_risk", "subordination_score",
    "has_structural_sub", "has_floating_rate", "has_near_term_maturity",
    "has_holdco_debt", "has_opco_debt", "has_unrestricted_subs",
    "nearest_maturity", "weighted_avg_maturity",
    "debt_due_1yr", "debt_due_2yr", "debt_due_3yr",
    "sp_rating", "moodys_rating", "rating_bucket",
}

BOND_FIELDS = {
    "id", "name", "cusip", "isin",
    "company_ticker", "company_name", "company_sector",
    "issuer_name", "issuer_type", "issuer_id",
    "instrument_type", "seniority", "security_type",
    "commitment", "principal", "outstanding", "currency",
    "rate_type", "coupon_rate", "spread_bps", "benchmark", "floor_bps",
    "issue_date", "issue_date_estimated", "maturity_date",
    "is_active", "is_drawn",
    "pricing", "guarantor_count",
}

PRICING_FIELDS = {
    "cusip", "isin", "bond_name",
    "company_ticker", "company_name",
    "last_price", "last_trade_date", "last_trade_volume",
    "ytm", "ytm_bps", "spread", "spread_bps", "treasury_benchmark",
    "price_source", "staleness_days",
    "coupon_rate", "maturity_date", "seniority",
}


def parse_fields(fields_param: Optional[str], available: Set[str]) -> Optional[Set[str]]:
    """Parse comma-separated fields parameter and validate against available fields."""
    if not fields_param:
        return None  # Return all fields

    requested = {f.strip() for f in fields_param.split(",") if f.strip()}
    invalid = requested - available
    if invalid:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_FIELDS",
                "message": f"Invalid fields: {', '.join(invalid)}",
                "available_fields": sorted(available)
            }
        )
    return requested


def filter_dict(data: dict, fields: Optional[Set[str]]) -> dict:
    """Filter dictionary to only include requested fields."""
    if fields is None:
        return data
    return {k: v for k, v in data.items() if k in fields}


def parse_comma_list(value: Optional[str], uppercase: bool = True) -> List[str]:
    """Parse comma-separated string into list, optionally uppercasing."""
    if not value:
        return []
    items = [item.strip() for item in value.split(",") if item.strip()]
    return [item.upper() for item in items] if uppercase else items


def apply_sort(query, sort: str, column_map: dict, default_column):
    """Apply sort to query based on sort parameter (prefix '-' for descending)."""
    sort_desc = sort.startswith("-")
    sort_field = sort[1:] if sort_desc else sort
    sort_column = column_map.get(sort_field, default_column)

    if sort_desc:
        return query.order_by(desc(sort_column).nulls_last())
    return query.order_by(asc(sort_column).nulls_last())


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
        if include_metadata and c.id in metadata_map:
            meta = metadata_map[c.id]
            company_data["_metadata"] = {
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
            }

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

    # Determine if we need pricing join
    needs_pricing = any([min_ytm, max_ytm, min_spread, max_spread, has_pricing])
    pricing_sort = sort.replace("-", "").startswith("pricing")
    needs_pricing = needs_pricing or pricing_sort

    # Build base query
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
    if needs_pricing:
        if min_ytm is not None:
            filters.append(BondPricing.ytm_bps >= int(min_ytm * 100))
        if max_ytm is not None:
            filters.append(BondPricing.ytm_bps <= int(max_ytm * 100))
        if min_spread is not None:
            filters.append(BondPricing.spread_to_treasury_bps >= min_spread)
        if max_spread is not None:
            filters.append(BondPricing.spread_to_treasury_bps <= max_spread)
        if has_pricing is True:
            filters.append(BondPricing.last_price.isnot(None))
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

    # Count query
    count_query = select(func.count(DebtInstrument.id.distinct())).select_from(DebtInstrument).join(
        Company, DebtInstrument.company_id == Company.id
    ).join(
        Entity, DebtInstrument.issuer_id == Entity.id
    )
    if needs_pricing:
        count_query = count_query.outerjoin(
            BondPricing, DebtInstrument.id == BondPricing.debt_instrument_id
        )
    if filters:
        count_query = count_query.where(and_(*filters))

    total = await db.scalar(count_query)

    # Apply sorting
    sort_column_map = {
        "maturity_date": DebtInstrument.maturity_date,
        "coupon_rate": DebtInstrument.interest_rate,
        "outstanding": DebtInstrument.outstanding,
        "name": DebtInstrument.name,
        "issuer_type": Entity.entity_type,
        "company_ticker": Company.ticker,
    }
    if needs_pricing:
        sort_column_map["pricing.ytm"] = BondPricing.ytm_bps
        sort_column_map["pricing.spread"] = BondPricing.spread_to_treasury_bps
        sort_column_map["pricing.last_price"] = BondPricing.last_price

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

    # Build response
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
        }

        # Add pricing if available
        if pricing:
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
        elif needs_pricing or (selected_fields and "pricing" in selected_fields):
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


@router.get("/pricing", tags=["Primitives"])
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
    Search bond pricing data from FINRA TRACE.

    Use `format=csv` for CSV export.

    **Example:** Get pricing for all RIG bonds:
    ```
    GET /v1/pricing?ticker=RIG
    ```

    **Example:** Export pricing to CSV:
    ```
    GET /v1/pricing?format=csv
    ```
    """
    selected_fields = parse_fields(fields, PRICING_FIELDS)

    query = select(BondPricing, DebtInstrument, Company).join(
        DebtInstrument, BondPricing.debt_instrument_id == DebtInstrument.id
    ).join(
        Company, DebtInstrument.company_id == Company.id
    ).where(BondPricing.last_price.isnot(None))

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
        }
    }
    return etag_response(response_data, if_none_match)


# =============================================================================
# PRIMITIVE 6: documents.search
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
    "credit_agreement", "guarantor_list", "covenants",
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
    section_type: Optional[str] = Query(None, description="Section type: exhibit_21, debt_footnote, mda_liquidity, credit_agreement, guarantor_list, covenants"),
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
    - `search.bonds` - Search bonds (maps to GET /v1/bonds)
    - `resolve.bond` - Resolve bond identifier (maps to GET /v1/bonds/resolve)
    - `traverse.entities` - Graph traversal (maps to POST /v1/entities/traverse)
    - `search.pricing` - Search pricing (maps to GET /v1/pricing)
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

    # Execute all operations in parallel
    tasks = [execute_operation(i, op) for i, op in enumerate(request.operations)]
    results = await asyncio.gather(*tasks)

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

        if include_metadata and c.id in metadata_map:
            meta = metadata_map[c.id]
            company_data["_metadata"] = {
                "qa_score": float(meta.qa_score) if meta.qa_score else None,
                "extraction_method": meta.extraction_method,
                "warnings": meta.warnings if meta.warnings else [],
            }

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
            filters.append(BondPricing.last_price.isnot(None))

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

        if pricing:
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
    ).where(BondPricing.last_price.isnot(None))

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
