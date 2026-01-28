# Demo Scenarios

Test cases that validate database coverage and API functionality. These scenarios serve dual purposes:
1. **Documentation** - Show real-world API usage patterns
2. **Testing** - Validate data quality and API correctness

Run `scripts/test_demo_scenarios.py` to execute all scenarios against the API.

---

## Scenario 1: Leverage Leaderboard

**Use Case:** Which companies have the highest leverage?

**Endpoint:** `GET /v1/companies`

**Query:**
```
?fields=ticker,name,net_leverage_ratio,total_debt
&sort=-net_leverage_ratio
&limit=10
```

**Example Request:**
```bash
curl "https://api.debtstack.ai/v1/companies?fields=ticker,name,net_leverage_ratio,total_debt&sort=-net_leverage_ratio&limit=10" \
  -H "X-API-Key: $DEBTSTACK_API_KEY"
```

**Expected Results:**
- Returns companies sorted by leverage (highest first)
- Top companies should include high-leverage names (CZR ~8x, LUMN ~7x, SPG ~6x)
- At least 100 companies have `net_leverage_ratio` populated

**Validation Checks:**
- [ ] Response contains `data` array
- [ ] At least 10 results returned
- [ ] Results are sorted descending by `net_leverage_ratio`
- [ ] All returned companies have non-null `net_leverage_ratio`
- [ ] Leverage values are reasonable (0.1x to 20x range)

---

## Scenario 2: Bond Screener

**Use Case:** Find bonds by yield, seniority, and pricing availability.

**Endpoint:** `GET /v1/bonds`

**Query:**
```
?seniority=senior_secured
&has_pricing=true
&fields=name,ticker,cusip,coupon_rate,maturity_date,pricing
&sort=-pricing.ytm
&limit=20
```

**Example Request:**
```bash
curl "https://api.debtstack.ai/v1/bonds?seniority=senior_secured&has_pricing=true&fields=name,ticker,cusip,coupon_rate,maturity_date,pricing&sort=-pricing.ytm&limit=20" \
  -H "X-API-Key: $DEBTSTACK_API_KEY"
```

**Expected Results:**
- Returns secured bonds with pricing data
- Sorted by yield to maturity (highest first)
- Should include energy/drilling companies (RIG, VAL) with high yields

**Validation Checks:**
- [ ] Response contains `data` array
- [ ] All returned bonds have `seniority` = "senior_secured"
- [ ] All returned bonds have `pricing` object with `ytm` field
- [ ] Results are sorted descending by YTM
- [ ] At least 10 priced secured bonds exist

---

## Scenario 3: Corporate Structure Explorer

**Use Case:** Visualize entity hierarchy and guarantee relationships.

**Endpoint:** `POST /v1/entities/traverse`

**Request Body:**
```json
{
  "start": {"type": "company", "ticker": "CHTR"},
  "relationships": ["parent_of", "guarantees"],
  "depth": 2
}
```

**Example Request:**
```bash
curl -X POST "https://api.debtstack.ai/v1/entities/traverse" \
  -H "X-API-Key: $DEBTSTACK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"start": {"type": "company", "ticker": "CHTR"}, "relationships": ["parent_of", "guarantees"], "depth": 2}'
```

**Expected Results:**
- Returns hierarchical entity structure for Charter Communications
- Shows parent-child relationships
- Includes guarantee relationships linking entities to debt

**Validation Checks:**
- [ ] Response contains entity tree structure
- [ ] Root entity is Charter Communications (holdco)
- [ ] At least 5 child entities returned
- [ ] Guarantee relationships are included
- [ ] Entity types are valid (holdco, opco, subsidiary, etc.)

---

## Scenario 4: Document Search

**Use Case:** Search SEC filings for specific covenant language.

**Endpoint:** `GET /v1/documents/search`

**Query:**
```
?q=change+of+control
&ticker=CHTR
&section_type=indenture
&limit=5
```

**Example Request:**
```bash
curl "https://api.debtstack.ai/v1/documents/search?q=change+of+control&ticker=CHTR&section_type=indenture&limit=5" \
  -H "X-API-Key: $DEBTSTACK_API_KEY"
```

**Expected Results:**
- Returns indenture sections mentioning "change of control"
- Snippets show relevant text with search term highlighted
- Source document metadata included

**Validation Checks:**
- [ ] Response contains `data` array with search results
- [ ] All results are from `section_type` = "indenture"
- [ ] All results are for ticker "CHTR"
- [ ] Results contain snippet text with "change of control"
- [ ] At least 1 result returned

**Additional Search Terms to Test:**
- "event of default" - ~3,600 documents expected
- "collateral" - ~1,750 documents expected
- "restricted payment" - ~460 documents expected

---

## Scenario 5: AI Agent Workflow (Two-Phase)

**Use Case:** Demonstrate the discovery → deep dive pattern for AI agents.

### Phase 1: Discovery

**Endpoint:** `GET /v1/bonds`

**Query:**
```
?min_ytm=800
&seniority=senior_secured
&has_pricing=true
&fields=name,ticker,cusip,ytm_pct,collateral
&limit=10
```

**Expected Results:**
- High-yield secured bonds for agent to present to user
- Collateral information for quick assessment

### Phase 2: Deep Dive

After user selects a bond (e.g., RIG), search for specific information:

**Endpoint:** `GET /v1/documents/search`

**Query:**
```
?q=event+of+default
&ticker=RIG
&section_type=indenture
```

**Expected Results:**
- Specific covenant language from RIG's indentures
- Agent can summarize default triggers for user

**Validation Checks:**
- [ ] Phase 1 returns actionable bond list
- [ ] Phase 2 returns relevant document snippets
- [ ] End-to-end flow completes in <2 seconds total

---

## Scenario 6: Maturity Wall

**Use Case:** Visualize a company's debt maturity profile.

**Endpoint:** `GET /v1/companies/{ticker}/maturity-waterfall`

**Example Request:**
```bash
curl "https://api.debtstack.ai/v1/companies/CHTR/maturity-waterfall" \
  -H "X-API-Key: $DEBTSTACK_API_KEY"
```

**Expected Results:**
- Debt amounts bucketed by maturity year
- Breakdown by seniority (secured vs unsecured)
- Nearest maturity date and amount highlighted

**Validation Checks:**
- [ ] Response contains maturity buckets (2025, 2026, 2027, etc.)
- [ ] Each bucket has `amount` field (in cents)
- [ ] Amounts sum to approximately `total_debt` for company
- [ ] `nearest_maturity` field present with date and amount
- [ ] `weighted_avg_maturity` calculated correctly

---

## Scenario 7: Physical Asset-Backed Bonds

**Use Case:** Find high-yield bonds secured by tangible physical assets (not just "general lien").

**Endpoint:** `GET /v1/bonds`

**Query:**
```
?min_ytm=800
&seniority=senior_secured
&has_pricing=true
&fields=name,ticker,ytm_pct,collateral,maturity_date
&limit=50
```

**Post-Processing:** Filter results where `collateral.type` is one of:
- `equipment` - Machinery, drilling rigs
- `real_estate` - Property, buildings
- `vehicles` - Aircraft, ships, trucks
- `energy_assets` - Oil/gas reserves, pipelines

**Example Request:**
```bash
curl "https://api.debtstack.ai/v1/bonds?min_ytm=800&seniority=senior_secured&has_pricing=true&fields=name,ticker,ytm_pct,collateral,maturity_date&limit=50" \
  -H "X-API-Key: $DEBTSTACK_API_KEY"
```

**Expected Results:**
- Bonds from drilling companies (RIG, VAL) with equipment collateral
- Airline bonds (AAL) with aircraft collateral
- REIT bonds with real estate collateral

**Validation Checks:**
- [ ] At least 5 bonds have physical collateral types
- [ ] Collateral descriptions are meaningful (not empty)
- [ ] YTM values are >= 8%
- [ ] Companies match expected sectors (Energy, Airlines, REITs)

---

## Scenario 8: Yield Per Turn of Leverage

**Use Case:** Find the best risk-adjusted yield - bonds offering the most yield per turn of issuer leverage.

**Endpoint:** `GET /v1/bonds`

**Query:**
```
?seniority=senior_secured
&has_pricing=true
&fields=name,ticker,cusip,ytm_pct,issuer_leverage,issuer_name
&limit=100
```

**Post-Processing:**
1. Filter to bonds where both `ytm_pct` and `issuer_leverage` are non-null
2. Compute: `yield_per_turn = ytm_pct / issuer_leverage`
3. Sort descending by `yield_per_turn`

**Example Request:**
```bash
curl "https://api.debtstack.ai/v1/bonds?seniority=senior_secured&has_pricing=true&fields=name,ticker,cusip,ytm_pct,issuer_leverage,issuer_name&limit=100" \
  -H "X-API-Key: $DEBTSTACK_API_KEY"
```

**Expected Results:**
- Top bonds have high yield but low issuer leverage
- A 10% yield from 2x levered company (5.0 Y/L) beats 10% from 5x levered company (2.0 Y/L)
- Energy companies with low leverage should rank well

**Validation Checks:**
- [ ] At least 20 bonds have both YTM and issuer leverage
- [ ] Computed `yield_per_turn` values are reasonable (0.5 to 10 range)
- [ ] Results include mix of industries
- [ ] Issuer leverage values match company metrics

---

## Summary: Database Coverage Requirements

| Scenario | Minimum Data Required |
|----------|----------------------|
| Leverage Leaderboard | 100+ companies with `net_leverage_ratio` |
| Bond Screener | 10+ secured bonds with pricing |
| Corporate Structure | CHTR with 5+ entities, guarantee links |
| Document Search | 1,000+ indenture sections indexed |
| AI Agent Workflow | Bonds + documents for same companies |
| Maturity Wall | Maturity dates on 80%+ of instruments |
| Physical Asset-Backed | 5+ bonds with physical collateral types |
| Yield Per Leverage | 20+ bonds with pricing AND issuer leverage |

---

## Running Tests

```bash
# Run all scenarios
python scripts/test_demo_scenarios.py

# Run specific scenario
python scripts/test_demo_scenarios.py --scenario 2

# Run against local API
python scripts/test_demo_scenarios.py --api-url http://localhost:8000

# Verbose output
python scripts/test_demo_scenarios.py --verbose
```
