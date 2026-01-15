# API Coverage Analysis & Recommendations

## Executive Summary

The current API exposes ~75% of the extracted data. Key gaps:
1. **OwnershipLink** table (JVs, complex ownership) is completely unexposed
2. **JSONB attributes** fields contain valuable data but aren't accessible
3. **Cross-company search** is limited - can't find "all VIEs" or "all Delaware entities"
4. **Aggregation endpoints** are missing - no sector-level analysis

---

## Current State: Data vs. API Coverage

### Tables & Coverage

| Table | Records | API Exposure | Gap |
|-------|---------|--------------|-----|
| Company | 178 | 90% | CIK/LEI/attributes hidden |
| Entity | 3,085 | 85% | formation_date, is_material, is_domestic hidden |
| DebtInstrument | 1,805 | 90% | commitment, is_drawn, attributes hidden |
| BondPricing | 30 | 100% | Full coverage |
| OwnershipLink | ? | **0%** | **Completely unexposed** |
| Guarantee | ~500 | 100% | Full coverage |
| CompanyFinancials | ~50 | 100% | Full coverage |
| ObligorGroupFinancials | ~10 | 100% | Full coverage |
| CompanyMetrics | 178 | 95% | leverage_ratio, interest_coverage hidden in list |

---

## Recommended New/Enhanced Endpoints

### Priority 1: Expose Hidden Data

#### 1.1 Add `/v1/ownership` endpoint
```
GET /v1/companies/{ticker}/ownership
```
Expose the OwnershipLink table to show:
- Joint ventures and JV partners
- Multiple parent relationships
- Economic vs voting ownership
- Consolidation methods
- Historical ownership changes

**Use case**: "Show me all JVs for RIG" or "Which entities have <100% ownership?"

#### 1.2 Add `/v1/search/entities` endpoint
```
GET /v1/search/entities?jurisdiction=Delaware&entity_type=spv&is_vie=true
```
Search entities across ALL companies:
- Filter by jurisdiction, entity_type, is_vie, is_unrestricted
- Find all SPVs, all Delaware entities, all VIEs in database

**Use case**: "Find all VIEs in the database" or "All Cayman Islands entities"

#### 1.3 Enhance `/v1/companies/{ticker}` to include identifiers
Add to response:
```json
{
  "identifiers": {
    "cik": "0001451505",
    "lei": "549300XYZ123",
    "attributes": {...}  // or specific useful attributes
  }
}
```

### Priority 2: Better Search & Filtering

#### 2.1 Enhance `/v1/search/debt` with more filters
Add parameters:
- `issuer_type`: Filter by issuer entity type (holdco, opco, spv)
- `has_guarantors`: Boolean - debt with/without guarantors
- `min_coupon_bps` / `max_coupon_bps`: Filter by coupon rate
- `is_drawn`: For revolvers - drawn vs undrawn
- `currency`: Filter by currency
- `has_cusip`: Boolean - tradeable bonds only

#### 2.2 Enhance `/v1/search/companies` with more filters
Add parameters:
- `min_leverage` / `max_leverage`: Filter by leverage ratio
- `min_interest_coverage` / `max_interest_coverage`
- `rating_bucket`: IG, HY-BB, HY-B, HY-CCC
- `has_vie`: Companies with VIE structures
- `has_jv`: Companies with joint ventures
- `min_entity_count` / `max_entity_count`

### Priority 3: Aggregation & Analytics

#### 3.1 Add `/v1/analytics/sector-summary`
```
GET /v1/analytics/sector-summary
GET /v1/analytics/sector-summary/{sector}
```
Returns:
- Average leverage by sector
- Total debt by sector
- Spread distribution by sector
- Maturity wall by sector

#### 3.2 Add `/v1/analytics/spread-distribution`
```
GET /v1/analytics/spread-distribution?seniority=senior_secured
```
Returns spread percentiles (10th, 25th, 50th, 75th, 90th) for:
- All bonds
- By seniority
- By rating bucket
- By sector

**Use case**: "Where does this bond's spread rank vs. peers?"

#### 3.3 Add `/v1/analytics/maturity-wall`
```
GET /v1/analytics/maturity-wall?sector=Energy
```
Aggregate maturity wall across all companies (optionally by sector).

### Priority 4: Relationship-Focused Endpoints

#### 4.1 Add `/v1/companies/{ticker}/hierarchy`
```
GET /v1/companies/{ticker}/hierarchy
```
Returns a tree structure of the corporate hierarchy:
```json
{
  "holdco": {
    "name": "Transocean Ltd.",
    "children": [
      {
        "name": "Transocean Inc.",
        "ownership_pct": 100,
        "debt_at_entity": 500000000000,
        "children": [...]
      }
    ]
  }
}
```
More intuitive than flat entity list for understanding structure.

#### 4.2 Add `/v1/companies/{ticker}/debt-by-entity`
```
GET /v1/companies/{ticker}/debt-by-entity
```
Groups debt by issuing entity with totals:
```json
{
  "entities": [
    {
      "entity": {"name": "Transocean Inc.", "type": "opco"},
      "debt_total": 500000000000,
      "secured": 200000000000,
      "unsecured": 300000000000,
      "instruments": [...]
    }
  ]
}
```

### Priority 5: Convenience Endpoints

#### 5.1 Add `/v1/companies/{ticker}/summary`
One-call endpoint that returns everything an agent needs:
```json
{
  "company": {...},
  "key_metrics": {
    "total_debt": ...,
    "leverage_ratio": ...,
    "nearest_maturity": ...
  },
  "structure_summary": {
    "entity_count": ...,
    "holdco_debt": ...,
    "opco_debt": ...
  },
  "top_debt": [...],  // Largest 5 instruments
  "risk_flags": {...}
}
```

#### 5.2 Add `/v1/watchlist` (stateless)
```
GET /v1/watchlist?tickers=RIG,ATUS,CHTR&metrics=leverage,nearest_maturity,spread
```
Returns just the requested metrics for a list of tickers - efficient for dashboards.

---

## Implementation Priority

### Phase 1 (High Impact, Moderate Effort)
1. ✅ `/v1/search/entities` - New cross-company entity search
2. ✅ `/v1/companies/{ticker}/ownership` - Expose OwnershipLink
3. ✅ Enhance `/v1/search/debt` with issuer_type, has_guarantors, min_coupon

### Phase 2 (Analytics Value-Add)
4. `/v1/analytics/sector-summary`
5. `/v1/analytics/spread-distribution`
6. `/v1/analytics/maturity-wall` (aggregate)

### Phase 3 (UX Improvements)
7. `/v1/companies/{ticker}/hierarchy` (tree view)
8. `/v1/companies/{ticker}/summary` (all-in-one)
9. `/v1/watchlist` (bulk metrics)

### Phase 4 (Complete Coverage)
10. Enhance `/v1/search/companies` with leverage, rating filters
11. Expose JSONB attributes via dedicated endpoint or query param
12. Add historical endpoints (if we store history)

---

## Agent Demo Tool Updates

When API is enhanced, update agent tools:

| Current Tool | Enhancement |
|--------------|-------------|
| `get_company_structure` | Add ownership details from OwnershipLink |
| `get_company_debt` | Group by entity, show issuer type |
| `search_bonds_by_yield` | Add issuer_type, has_guarantors filters |
| **NEW** `search_entities` | Cross-company entity search |
| **NEW** `get_sector_analysis` | Sector-level aggregations |

---

## Questions to Consider

1. **Historical data**: Do we want to track changes over time? (ownership changes, debt refinancings)
2. **Alerts/webhooks**: Should API support notifications for maturity events?
3. **Export formats**: CSV/Excel export for institutional users?
4. **Batch endpoints**: Bulk data fetch for analytics platforms?
