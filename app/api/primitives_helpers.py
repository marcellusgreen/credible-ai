"""
Shared Helpers for Primitives API

Common utilities used across all primitive endpoints:
- CSV export
- ETag caching
- Field selection/filtering
- Sorting
- Common query helpers
"""

import csv
import hashlib
import io
import json
from typing import Optional, List, Set

from fastapi import HTTPException, Response
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy import desc, asc


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

FINANCIALS_FIELDS = {
    "ticker", "company_name",
    "fiscal_year", "fiscal_quarter", "period_end_date", "filing_type",
    # Income Statement
    "revenue", "cost_of_revenue", "gross_profit", "operating_income", "ebitda",
    "interest_expense", "net_income", "depreciation_amortization",
    # Balance Sheet
    "cash", "total_current_assets", "total_assets",
    "total_current_liabilities", "total_debt", "total_liabilities", "stockholders_equity",
    # Cash Flow
    "operating_cash_flow", "investing_cash_flow", "financing_cash_flow", "capex",
    # Derived
    "free_cash_flow", "gross_margin", "operating_margin", "net_margin",
}

COLLATERAL_FIELDS = {
    "id", "debt_instrument_id",
    "bond_name", "bond_cusip", "company_ticker", "company_name",
    "collateral_type", "description", "estimated_value", "priority",
    "instrument_seniority", "instrument_security_type",
}

COVENANT_FIELDS = {
    "id", "ticker", "company_name",
    "instrument_name", "cusip",
    "covenant_type", "covenant_name",
    "test_metric", "threshold_value", "threshold_type", "test_frequency",
    "description", "has_step_down", "step_down_schedule", "cure_period_days",
    "put_price_pct", "extraction_confidence", "source_document_date",
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
