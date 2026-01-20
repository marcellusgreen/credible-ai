# DebtStack API Endpoint Design

Based on FinancialDatasets.ai's comprehensive GET structure, here's a complete API design for DebtStack.

## Design Principles

1. **RESTful** - Clean resource-based URLs
2. **Comprehensive** - Cover all credit analysis use cases
3. **Queryable** - Rich filtering and search options
4. **Fast** - Pre-computed responses with ETag caching
5. **AI-friendly** - JSON structures optimized for LLM consumption

---

## 1. Company Endpoints

### GET /v1/companies
**List all companies with optional filtering**

**Query Parameters:**
- `sector` - Filter by sector (e.g., Technology, Energy)
- `has_secured_debt` - Filter by secured debt presence (true/false)
- `min_debt_amount` - Minimum total debt (in cents)
- `max_debt_amount` - Maximum total debt (in cents)
- `has_structural_sub` - Has structural subordination (true/false)
- `limit` - Number of results (default: 100, max: 1000)
- `offset` - Pagination offset

**Response:**
```json
{
  "companies": [
    {
      "ticker": "AAPL",
      "name": "Apple Inc.",
      "cik": "0000320193",
      "sector": "Technology",
      "total_debt": 10650000000000,
      "entity_count": 20,
      "debt_instrument_count": 7,
      "has_structural_subordination": false,
      "has_secured_debt": false,
      "last_updated": "2025-01-10T12:00:00Z"
    }
  ],
  "total": 38,
  "limit": 100,
  "offset": 0
}
```

---

### GET /v1/companies/{ticker}
**Company overview with key metrics**

**Response:**
```json
{
  "company": {
    "ticker": "RIG",
    "name": "Transocean Ltd.",
    "cik": "0001451505",
    "sector": "Energy",
    "industry": "Offshore Drilling",
    "description": "Offshore contract drilling services"
  },
  "metrics": {
    "total_debt": 688100000000,
    "secured_debt": 688100000000,
    "unsecured_debt": 0,
    "entity_count": 17,
    "debt_instrument_count": 15,
    "guarantee_count": 45,
    "covenant_count": 8,
    "has_structural_subordination": true,
    "subordination_score": 75
  },
  "data_freshness": {
    "last_extraction": "2025-01-10T12:00:00Z",
    "source_filing": "10-K",
    "filing_date": "2024-12-31",
    "qa_score": 88
  }
}
```

---

### GET /v1/companies/{ticker}/structure
**Complete corporate structure with debt at each entity** (EXISTING)

**Response:**
```json
{
  "company": {
    "ticker": "RIG",
    "name": "Transocean Ltd."
  },
  "structure": {
    "name": "Transocean Ltd.",
    "entity_id": "ent_123",
    "type": "holdco",
    "jurisdiction": "Switzerland",
    "is_guarantor": true,
    "debt_at_entity": {
      "total": 0,
      "count": 0,
      "instruments": []
    },
    "children": [
      {
        "name": "Transocean International Limited",
        "entity_id": "ent_124",
        "type": "finco",
        "jurisdiction": "Cayman Islands",
        "is_guarantor": false,
        "debt_at_entity": {
          "total": 688100000000,
          "count": 8,
          "instruments": [...]
        },
        "children": []
      }
    ]
  }
}
```

---

### GET /v1/companies/{ticker}/debt
**All debt instruments** (EXISTING - ENHANCED)

**Query Parameters:**
- `seniority` - Filter by seniority (senior_secured, senior_unsecured, etc.)
- `security_type` - Filter by security (first_lien, second_lien, unsecured)
- `min_rate` - Minimum interest rate in bps
- `max_rate` - Maximum interest rate in bps
- `maturity_before` - Maturity date before (ISO date)
- `maturity_after` - Maturity date after (ISO date)

**Response:**
```json
{
  "company": {
    "ticker": "RIG",
    "name": "Transocean Ltd."
  },
  "summary": {
    "total_outstanding": 688100000000,
    "total_count": 15,
    "secured_count": 8,
    "unsecured_count": 7,
    "weighted_avg_rate": 865,
    "weighted_avg_maturity": "2029-08-15"
  },
  "debt_instruments": [
    {
      "id": "debt_456",
      "name": "8.75% Senior Secured Notes due 2030",
      "issuer": {
        "entity_id": "ent_124",
        "name": "Transocean International Limited"
      },
      "amount_outstanding": 68810000000,
      "currency": "USD",
      "seniority": "senior_secured",
      "security_type": "first_lien",
      "interest_rate": 875,
      "interest_type": "fixed",
      "maturity_date": "2030-02-01",
      "guarantors": [
        {
          "entity_id": "ent_123",
          "name": "Transocean Ltd."
        }
      ],
      "covenants": [
        {
          "type": "leverage",
          "threshold": 5.0,
          "description": "Maximum consolidated leverage ratio"
        }
      ],
      "collateral": {
        "description": "First priority liens on substantially all assets",
        "includes": ["vessels", "equipment", "subsidiaries"]
      }
    }
  ]
}
```

---

### GET /v1/companies/{ticker}/metrics
**Detailed credit metrics**

**Response:**
```json
{
  "company": {
    "ticker": "RIG",
    "name": "Transocean Ltd."
  },
  "debt_metrics": {
    "total_debt": 688100000000,
    "secured_debt": 688100000000,
    "unsecured_debt": 0,
    "secured_percentage": 100.0,
    "short_term_debt": 0,
    "long_term_debt": 688100000000
  },
  "structure_metrics": {
    "entity_count": 17,
    "debt_issuing_entities": 1,
    "guarantor_count": 5,
    "has_structural_subordination": true,
    "subordination_score": 75,
    "restricted_subsidiaries": 12,
    "unrestricted_subsidiaries": 3
  },
  "covenant_metrics": {
    "financial_covenants": 5,
    "negative_covenants": 12,
    "most_restrictive_leverage": 5.0,
    "most_restrictive_coverage": 2.5
  },
  "maturity_profile": {
    "next_maturity": "2027-01-15",
    "maturities_next_12m": 0,
    "maturities_next_24m": 0,
    "maturities_2_5y": 235000000000,
    "maturities_5y_plus": 453100000000
  }
}
```

---

### GET /v1/companies/{ticker}/covenants
**All covenants across all debt**

**Query Parameters:**
- `type` - Filter by covenant type (leverage, interest_coverage, capex, etc.)
- `instrument_id` - Filter by specific debt instrument

**Response:**
```json
{
  "company": {
    "ticker": "RIG",
    "name": "Transocean Ltd."
  },
  "covenants": [
    {
      "id": "cov_789",
      "instrument_id": "debt_456",
      "instrument_name": "8.75% Senior Secured Notes due 2030",
      "type": "leverage",
      "category": "financial",
      "description": "Maximum consolidated leverage ratio",
      "threshold": 5.0,
      "threshold_unit": "ratio",
      "test_frequency": "quarterly",
      "grace_period_days": 30,
      "breach_consequences": "Event of default"
    },
    {
      "id": "cov_790",
      "instrument_id": "debt_456",
      "type": "interest_coverage",
      "category": "financial",
      "description": "Minimum consolidated interest coverage ratio",
      "threshold": 2.5,
      "threshold_unit": "ratio",
      "test_frequency": "quarterly"
    },
    {
      "id": "cov_791",
      "instrument_id": "debt_456",
      "type": "restricted_payments",
      "category": "negative",
      "description": "Limitations on restricted payments and dividends",
      "threshold": 50000000000,
      "threshold_unit": "cents",
      "exceptions": ["permitted payments", "basket amounts"]
    }
  ],
  "summary": {
    "total_count": 8,
    "financial_count": 5,
    "negative_count": 3,
    "most_restrictive": {
      "leverage": 5.0,
      "interest_coverage": 2.5
    }
  }
}
```

---

### GET /v1/companies/{ticker}/guarantees
**All guarantee relationships**

**Response:**
```json
{
  "company": {
    "ticker": "RIG",
    "name": "Transocean Ltd."
  },
  "guarantees": [
    {
      "id": "guar_321",
      "debt_instrument_id": "debt_456",
      "debt_instrument_name": "8.75% Senior Secured Notes due 2030",
      "obligor": {
        "entity_id": "ent_124",
        "name": "Transocean International Limited",
        "type": "finco"
      },
      "guarantor": {
        "entity_id": "ent_123",
        "name": "Transocean Ltd.",
        "type": "holdco"
      },
      "guarantee_type": "full_and_unconditional",
      "is_secured": true,
      "is_joint_and_several": true
    }
  ],
  "summary": {
    "total_guarantees": 45,
    "unique_guarantors": 5,
    "guaranteed_debt_amount": 688100000000
  }
}
```

---

### GET /v1/companies/{ticker}/intercreditor
**Intercreditor agreement details**

**Response:**
```json
{
  "company": {
    "ticker": "RIG",
    "name": "Transocean Ltd."
  },
  "intercreditor_agreements": [
    {
      "id": "inter_654",
      "name": "First Lien/Second Lien Intercreditor Agreement",
      "parties": [
        {
          "role": "first_lien",
          "instruments": ["debt_456", "debt_457"],
          "total_amount": 500000000000
        },
        {
          "role": "second_lien",
          "instruments": ["debt_458"],
          "total_amount": 188100000000
        }
      ],
      "payment_waterfall": [
        {
          "priority": 1,
          "description": "First Lien Senior Secured Notes",
          "amount": 500000000000
        },
        {
          "priority": 2,
          "description": "Second Lien Senior Secured Notes",
          "amount": 188100000000
        }
      ],
      "restrictions": [
        "Second lien cannot take enforcement action without first lien consent",
        "Standstill period: 180 days after first lien enforcement",
        "Shared collateral with different priority"
      ]
    }
  ]
}
```

---

## 2. Entity Endpoints

### GET /v1/companies/{ticker}/entities
**List all entities in corporate structure**

**Query Parameters:**
- `type` - Filter by entity type (holdco, finco, opco, subsidiary)
- `is_guarantor` - Filter by guarantor status (true/false)
- `has_debt` - Filter entities with debt (true/false)
- `jurisdiction` - Filter by jurisdiction

**Response:**
```json
{
  "company": {
    "ticker": "RIG",
    "name": "Transocean Ltd."
  },
  "entities": [
    {
      "entity_id": "ent_123",
      "name": "Transocean Ltd.",
      "type": "holdco",
      "jurisdiction": "Switzerland",
      "parent_id": null,
      "is_guarantor": true,
      "debt_count": 0,
      "total_debt_at_entity": 0,
      "children_count": 1
    },
    {
      "entity_id": "ent_124",
      "name": "Transocean International Limited",
      "type": "finco",
      "jurisdiction": "Cayman Islands",
      "parent_id": "ent_123",
      "is_guarantor": false,
      "debt_count": 8,
      "total_debt_at_entity": 688100000000,
      "children_count": 5
    }
  ],
  "summary": {
    "total_entities": 17,
    "holdcos": 1,
    "fincos": 1,
    "opcos": 8,
    "subsidiaries": 7,
    "guarantors": 5
  }
}
```

---

### GET /v1/companies/{ticker}/entities/{entity_id}
**Details for specific entity**

**Response:**
```json
{
  "company": {
    "ticker": "RIG",
    "name": "Transocean Ltd."
  },
  "entity": {
    "entity_id": "ent_124",
    "name": "Transocean International Limited",
    "type": "finco",
    "jurisdiction": "Cayman Islands",
    "incorporation_date": "2008-05-12",
    "is_guarantor": false,
    "is_restricted_subsidiary": true,
    "is_unrestricted_subsidiary": false,
    "parent": {
      "entity_id": "ent_123",
      "name": "Transocean Ltd.",
      "ownership_percentage": 100.0
    },
    "debt_at_entity": {
      "count": 8,
      "total": 688100000000,
      "secured": 688100000000,
      "unsecured": 0
    },
    "guarantees_provided": [],
    "guarantees_received": [
      {
        "guarantor_id": "ent_123",
        "guarantor_name": "Transocean Ltd.",
        "debt_instrument_id": "debt_456"
      }
    ],
    "children": [
      {
        "entity_id": "ent_125",
        "name": "Transocean Operating LLC",
        "type": "opco"
      }
    ]
  }
}
```

---

### GET /v1/companies/{ticker}/entities/{entity_id}/debt
**Debt issued at specific entity**

**Response:**
```json
{
  "company": {
    "ticker": "RIG",
    "name": "Transocean Ltd."
  },
  "entity": {
    "entity_id": "ent_124",
    "name": "Transocean International Limited",
    "type": "finco"
  },
  "debt_instruments": [
    {
      "id": "debt_456",
      "name": "8.75% Senior Secured Notes due 2030",
      "amount_outstanding": 68810000000,
      "seniority": "senior_secured",
      "maturity_date": "2030-02-01"
    }
  ],
  "summary": {
    "total_debt": 688100000000,
    "instrument_count": 8
  }
}
```

---

## 3. Debt Instrument Endpoints

### GET /v1/companies/{ticker}/debt/{instrument_id}
**Detailed information for specific debt instrument**

**Response:**
```json
{
  "company": {
    "ticker": "RIG",
    "name": "Transocean Ltd."
  },
  "debt_instrument": {
    "id": "debt_456",
    "name": "8.75% Senior Secured Notes due 2030",
    "issuer": {
      "entity_id": "ent_124",
      "name": "Transocean International Limited",
      "type": "finco"
    },
    "amount_outstanding": 68810000000,
    "original_amount": 75000000000,
    "currency": "USD",
    "seniority": "senior_secured",
    "security_type": "first_lien",
    "interest_rate": 875,
    "interest_type": "fixed",
    "interest_payment_frequency": "semi-annual",
    "interest_payment_dates": ["02-01", "08-01"],
    "issue_date": "2023-02-01",
    "maturity_date": "2030-02-01",
    "callable": true,
    "call_date": "2025-02-01",
    "call_premium": 104.375,
    "putable": false,
    "convertible": false,
    "guarantors": [
      {
        "entity_id": "ent_123",
        "name": "Transocean Ltd.",
        "guarantee_type": "full_and_unconditional"
      }
    ],
    "covenants": [
      {
        "id": "cov_789",
        "type": "leverage",
        "threshold": 5.0,
        "description": "Maximum consolidated leverage ratio"
      }
    ],
    "collateral": {
      "description": "First priority liens on substantially all assets",
      "includes": ["vessels", "equipment", "subsidiaries"],
      "exceptions": ["certain excluded assets"],
      "value_estimate": null
    },
    "restrictive_features": [
      "Change of control put at 101%",
      "Asset sale provisions",
      "Limitations on liens",
      "Limitations on restricted payments"
    ]
  }
}
```

---

## 4. Search & Filter Endpoints

### GET /v1/search/companies
**Search companies by various criteria**

**Query Parameters:**
- `q` - Text search (name or ticker)
- `sector` - Filter by sector
- `min_debt` - Minimum total debt
- `max_debt` - Maximum total debt
- `has_secured_debt` - Has secured debt (true/false)
- `has_high_yield` - Has high yield debt (rate > 7%)
- `maturity_within_months` - Has debt maturing within N months
- `sort_by` - Sort field (total_debt, entity_count, etc.)
- `sort_order` - asc or desc
- `limit` - Results limit
- `offset` - Pagination offset

**Response:**
```json
{
  "results": [
    {
      "ticker": "RIG",
      "name": "Transocean Ltd.",
      "sector": "Energy",
      "total_debt": 688100000000,
      "secured_percentage": 100.0,
      "match_reason": "Has secured debt, Energy sector"
    }
  ],
  "total": 12,
  "filters_applied": {
    "sector": "Energy",
    "has_secured_debt": true
  }
}
```

---

### GET /v1/search/debt
**Search debt instruments across all companies**

**Query Parameters:**
- `seniority` - Filter by seniority
- `security_type` - Filter by security
- `min_rate` - Minimum rate
- `max_rate` - Maximum rate
- `maturity_before` - Maturity before date
- `maturity_after` - Maturity after date
- `currency` - Filter by currency
- `callable` - Is callable (true/false)
- `sort_by` - Sort field
- `sort_order` - asc or desc
- `limit` - Results limit
- `offset` - Pagination offset

**Response:**
```json
{
  "results": [
    {
      "id": "debt_456",
      "name": "8.75% Senior Secured Notes due 2030",
      "company_ticker": "RIG",
      "company_name": "Transocean Ltd.",
      "amount_outstanding": 68810000000,
      "interest_rate": 875,
      "maturity_date": "2030-02-01",
      "seniority": "senior_secured"
    }
  ],
  "total": 156,
  "filters_applied": {
    "min_rate": 700,
    "seniority": "senior_secured"
  }
}
```

---

### GET /v1/search/covenants
**Search covenants across all companies**

**Query Parameters:**
- `type` - Covenant type
- `min_threshold` - Minimum threshold value
- `max_threshold` - Maximum threshold value
- `category` - financial or negative
- `company_ticker` - Filter by company

**Response:**
```json
{
  "results": [
    {
      "id": "cov_789",
      "company_ticker": "RIG",
      "company_name": "Transocean Ltd.",
      "instrument_name": "8.75% Senior Secured Notes due 2030",
      "type": "leverage",
      "threshold": 5.0,
      "description": "Maximum consolidated leverage ratio"
    }
  ],
  "total": 45,
  "summary": {
    "avg_leverage_threshold": 5.2,
    "avg_coverage_threshold": 2.8
  }
}
```

---

## 5. Comparison & Analytics Endpoints

### GET /v1/compare/companies
**Compare multiple companies side-by-side**

**Query Parameters:**
- `tickers` - Comma-separated tickers (e.g., "RIG,VAL,DO")

**Response:**
```json
{
  "comparison": [
    {
      "ticker": "RIG",
      "name": "Transocean Ltd.",
      "total_debt": 688100000000,
      "secured_percentage": 100.0,
      "entity_count": 17,
      "weighted_avg_rate": 865,
      "next_maturity": "2027-01-15"
    },
    {
      "ticker": "VAL",
      "name": "Valaris Limited",
      "total_debt": 520000000000,
      "secured_percentage": 85.5,
      "entity_count": 12,
      "weighted_avg_rate": 823,
      "next_maturity": "2028-04-30"
    }
  ],
  "aggregates": {
    "avg_total_debt": 604050000000,
    "avg_secured_percentage": 92.75,
    "avg_entity_count": 14.5
  }
}
```

---

### GET /v1/analytics/sector/{sector}
**Sector-wide analytics**

**Response:**
```json
{
  "sector": "Energy",
  "companies_count": 8,
  "aggregates": {
    "total_debt": 3500000000000,
    "avg_debt_per_company": 437500000000,
    "median_debt": 420000000000,
    "avg_secured_percentage": 75.5,
    "avg_weighted_rate": 845,
    "avg_entity_count": 15
  },
  "distributions": {
    "seniority": {
      "senior_secured": 65.2,
      "senior_unsecured": 30.1,
      "subordinated": 4.7
    },
    "maturity_buckets": {
      "0-2_years": 12.5,
      "2-5_years": 45.8,
      "5-10_years": 35.2,
      "10+_years": 6.5
    }
  }
}
```

---

### GET /v1/analytics/trends/rates
**Interest rate trends across database**

**Query Parameters:**
- `sector` - Filter by sector
- `seniority` - Filter by seniority
- `start_date` - Start date for historical data
- `end_date` - End date

**Response:**
```json
{
  "metrics": {
    "current_avg_rate": 825,
    "current_median_rate": 800,
    "min_rate": 450,
    "max_rate": 1250
  },
  "by_seniority": {
    "senior_secured": {
      "avg_rate": 785,
      "count": 120
    },
    "senior_unsecured": {
      "avg_rate": 950,
      "count": 85
    }
  },
  "by_sector": {
    "Energy": {
      "avg_rate": 845,
      "count": 65
    },
    "Telecommunications": {
      "avg_rate": 775,
      "count": 42
    }
  }
}
```

---

## 6. Data Freshness & Quality Endpoints

### GET /v1/status
**API status and data coverage**

**Response:**
```json
{
  "status": "operational",
  "version": "1.0.0",
  "data_coverage": {
    "total_companies": 38,
    "total_entities": 779,
    "total_debt_instruments": 330,
    "total_guarantees": 890,
    "total_covenants": 245
  },
  "by_sector": {
    "Technology": 5,
    "Energy": 8,
    "Telecommunications": 5
  },
  "data_freshness": {
    "last_update": "2025-01-10T12:00:00Z",
    "avg_data_age_days": 15,
    "oldest_data_date": "2024-11-30"
  },
  "extraction_quality": {
    "avg_qa_score": 87.5,
    "companies_above_85": 35,
    "companies_below_85": 3
  }
}
```

---

### GET /v1/companies/{ticker}/extraction-report
**Detailed extraction and QA report**

**Response:**
```json
{
  "company": {
    "ticker": "RIG",
    "name": "Transocean Ltd."
  },
  "extraction": {
    "extraction_date": "2025-01-10T12:00:00Z",
    "source_filings": [
      {
        "type": "10-K",
        "date": "2024-12-31",
        "url": "https://sec.gov/..."
      }
    ],
    "extraction_method": "iterative_qa",
    "iterations": 2,
    "total_cost_cents": 301,
    "duration_seconds": 90
  },
  "qa_report": {
    "overall_score": 88,
    "checks": [
      {
        "name": "Internal Consistency",
        "status": "PASS",
        "score": 20,
        "details": "All references valid"
      },
      {
        "name": "Entity Verification",
        "status": "PASS",
        "score": 20,
        "details": "85% entities matched Exhibit 21"
      },
      {
        "name": "Debt Verification",
        "status": "WARN",
        "score": 15,
        "details": "2 instruments with minor amount discrepancies"
      },
      {
        "name": "Completeness",
        "status": "PASS",
        "score": 18,
        "details": "90% completeness confirmed"
      },
      {
        "name": "Structure Verification",
        "status": "PASS",
        "score": 20,
        "details": "Valid hierarchy confirmed"
      }
    ]
  }
}
```

---

## 7. Bulk & Export Endpoints

### GET /v1/export/companies
**Export all company data**

**Query Parameters:**
- `format` - json, csv, or parquet
- `include` - Comma-separated list (structure, debt, covenants, guarantees)

**Response:**
- Returns downloadable file with all requested data

---

### GET /v1/export/companies/{ticker}
**Export complete company dataset**

**Query Parameters:**
- `format` - json, csv, or xlsx

**Response:**
- Returns downloadable file with complete company data

---

## Summary of Endpoints

### Company Level (13 endpoints)
- GET /v1/companies
- GET /v1/companies/{ticker}
- GET /v1/companies/{ticker}/structure
- GET /v1/companies/{ticker}/debt
- GET /v1/companies/{ticker}/metrics
- GET /v1/companies/{ticker}/covenants
- GET /v1/companies/{ticker}/guarantees
- GET /v1/companies/{ticker}/intercreditor
- GET /v1/companies/{ticker}/entities
- GET /v1/companies/{ticker}/entities/{entity_id}
- GET /v1/companies/{ticker}/entities/{entity_id}/debt
- GET /v1/companies/{ticker}/debt/{instrument_id}
- GET /v1/companies/{ticker}/extraction-report

### Search & Filter (3 endpoints)
- GET /v1/search/companies
- GET /v1/search/debt
- GET /v1/search/covenants

### Analytics & Comparison (3 endpoints)
- GET /v1/compare/companies
- GET /v1/analytics/sector/{sector}
- GET /v1/analytics/trends/rates

### System (1 endpoint)
- GET /v1/status

### Export (2 endpoints)
- GET /v1/export/companies
- GET /v1/export/companies/{ticker}

**Total: 22 GET endpoints**

---

## Implementation Priority

### Phase 1: Core Endpoints (Week 1-2)
Already have:
- ✓ GET /v1/companies
- ✓ GET /v1/companies/{ticker}
- ✓ GET /v1/companies/{ticker}/structure
- ✓ GET /v1/companies/{ticker}/debt

Need to add:
- GET /v1/companies/{ticker}/metrics
- GET /v1/companies/{ticker}/covenants
- GET /v1/companies/{ticker}/guarantees

### Phase 2: Entity & Instrument Details (Week 3)
- GET /v1/companies/{ticker}/entities
- GET /v1/companies/{ticker}/entities/{entity_id}
- GET /v1/companies/{ticker}/debt/{instrument_id}

### Phase 3: Search (Week 4)
- GET /v1/search/companies
- GET /v1/search/debt
- GET /v1/search/covenants

### Phase 4: Analytics (Week 5-6)
- GET /v1/compare/companies
- GET /v1/analytics/sector/{sector}
- GET /v1/analytics/trends/rates
- GET /v1/status

### Phase 5: Advanced Features (Week 7+)
- GET /v1/companies/{ticker}/intercreditor
- GET /v1/companies/{ticker}/extraction-report
- GET /v1/export/* endpoints

---

## Notes for AI Agent Optimization

1. **All amounts in cents** - Avoid floating point issues
2. **All rates in basis points** - 850 bps = 8.50%
3. **ISO 8601 dates** - YYYY-MM-DD format
4. **Consistent IDs** - entity_id, debt_id, covenant_id prefixes
5. **Rich filtering** - Every list endpoint supports filters
6. **Pagination** - All list endpoints support limit/offset
7. **Caching headers** - ETags on all GET requests
8. **Rate limiting** - Documented in headers

This structure gives credit analysts and AI agents everything they need to:
- Analyze individual companies deeply
- Search across companies for patterns
- Compare peer companies
- Screen for investment opportunities
- Monitor covenant compliance
- Track structural subordination risks
