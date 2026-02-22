# Multi-Tool Credit Analysis Workflow

A systematic approach to comprehensive credit analysis using Medici's API tools. Follow this sequence to build a complete picture — each step informs the next, and skipping steps leads to blind spots.

## The Seven-Step Workflow

### Step 1: Company Overview — `search_companies`

**Start here.** Get the lay of the land before diving into specifics.

```
search_companies(ticker="XYZ")
```

**Key fields to examine:**
- `leverage_ratio`, `net_leverage_ratio` — how leveraged is this credit?
- `interest_coverage` — can it service its debt?
- `secured_leverage` — how much leverage has collateral priority?
- `total_debt`, `secured_debt`, `unsecured_debt` — absolute debt levels
- `sector`, `industry` — sector context for interpreting metrics
- `rating_bucket`, `sp_rating`, `moodys_rating` — agency view
- `debt_due_1yr`, `debt_due_2yr`, `debt_due_3yr` — near-term maturity pressure
- `nearest_maturity`, `weighted_avg_maturity` — maturity profile summary

**Risk flags — check all of these:**
- `has_structural_sub` — structural subordination present?
- `has_floating_rate` — interest rate exposure?
- `has_near_term_maturity` — maturity wall approaching?
- `has_holdco_debt` / `has_opco_debt` — multi-level debt structure?
- `has_unrestricted_subs` — entities outside the credit group?

**What you learn:** The overall credit profile — is this investment grade, leveraged, or distressed? What risk factors should you dig into?

---

### Step 2: Debt Stack — `search_bonds`

**Map the full capital structure.** See every debt instrument, its priority, and its terms.

```
search_bonds(ticker="XYZ", limit=50)
```

**Key fields to examine:**
- `seniority` — senior_secured, senior_unsecured, subordinated
- `security_type` — first_lien, second_lien, unsecured
- `instrument_type` — term_loan_b, senior_notes, revolver, etc.
- `issuer_type` — holdco, opco, subsidiary
- `outstanding` — amount at each priority level
- `coupon_rate`, `rate_type` — fixed vs floating, cost of debt
- `maturity_date` — when does each tranche come due?
- `is_active` — filter out retired instruments

**Build the stack:** Sort by seniority and issuer_type. Sum `outstanding` at each level. This gives you the claims waterfall — who gets paid first and how much is ahead of each tranche.

**What you learn:** The complete debt structure — total claims at each priority level, maturity distribution, fixed vs floating mix, holdco vs opco breakdown.

---

### Step 3: Corporate Structure — `get_corporate_structure`

**Check for structural subordination and entity complexity.** Critical if Step 1 flagged `has_structural_sub` or the company has both holdco and opco debt.

```
get_corporate_structure(ticker="XYZ")
```

**Key fields to examine at each entity:**
- `debt_at_entity` — where does debt sit in the hierarchy?
- `is_guarantor` — which entities guarantee debt at other levels?
- `is_unrestricted` — entities outside the credit group?
- `is_vie` — variable interest entities (consolidated but potentially unreachable)?

**What you learn:** Whether holdco creditors are at risk of structural subordination, how guarantees mitigate that risk, and whether unrestricted subs or VIEs create claims leakage.

---

### Step 4: Covenants — `search_covenants`

**Assess creditor protection and headroom.** This step answers: what guardrails exist and how close is the company to tripping them?

```
search_covenants(ticker="XYZ")
```

**Key fields to examine:**
- `covenant_type` — financial, negative, protective
- `test_metric` — which ratio is tested (leverage_ratio, interest_coverage, etc.)
- `threshold_value` — the covenant level
- `threshold_type` — max (cannot exceed) or min (must maintain)
- `has_step_down` / `step_down_schedule` — does the covenant tighten over time?
- `cure_period_days` — grace period after breach

**Calculate headroom:** Compare `threshold_value` to the corresponding metric from Step 1 (e.g., covenant leverage threshold vs actual `leverage_ratio`). Report headroom in both absolute ratio terms and as a percentage.

**What you learn:** Quality of creditor protection (maintenance vs incurrence, tight vs loose), how much room the company has before a breach, and whether covenants are tightening via step-downs.

---

### Step 5: Market Pricing — `search_pricing`

**See what the market thinks.** Pricing reveals the market's real-time assessment of credit risk and recovery expectations.

```
search_pricing(ticker="XYZ")
```

**Key fields to examine:**
- `last_price` — relative to par ($100): above $90 = healthy, $60-80 = stressed, below $60 = distressed
- `ytm_bps` — yield to maturity in basis points; spread_bps for spread to treasury
- `last_trade_date` / `staleness_days` — is the pricing fresh?
- `price_source` — TRACE (real trades) vs other sources

**Cross-tranche comparison:** Compare pricing across seniority levels. If secured bonds trade at $95 and unsecured at $55, the market is pricing the fulcrum between those tranches.

**What you learn:** Market's view of default probability and expected recovery at each level of the capital structure.

---

### Step 6: SEC Filings — `search_documents`

**Get supporting detail from primary sources.** Use this for specific questions that structured data doesn't fully answer.

```
search_documents(ticker="XYZ", section_type="debt_footnote")
search_documents(ticker="XYZ", section_type="mda_liquidity")
search_documents(ticker="XYZ", section_type="covenants")
```

**When to use each section_type:**
- `debt_footnote` — detailed debt schedule, maturity breakdowns, interest rate details not in structured data
- `credit_agreement` — full credit agreement terms, basket sizes, definitions
- `covenants` — covenant language and definitions
- `mda_liquidity` — management's discussion of liquidity, cash flow, and capital allocation plans
- `indenture` — bond indenture provisions
- `guarantor_list` — full list of subsidiary guarantors
- `exhibit_21` — complete subsidiary listing

**What you learn:** Nuances and details that don't fit in structured fields — maturity schedules, covenant definitions, management's stated plans, and risk disclosures in their own words.

---

### Step 7: Recent Changes — `get_changes`

**Track trajectory.** A point-in-time snapshot is incomplete without understanding the direction of travel.

```
get_changes(ticker="XYZ", since="YYYY-MM-DD")
```

Use a date 3-6 months back for recent trajectory, or 12 months for a fuller picture.

**Key changes to look for:**
- **Metric changes:** Leverage rising or falling? Coverage improving or deteriorating?
- **New issuances:** Has the company been borrowing more? What terms?
- **Matured debt:** Has it been paying down or refinancing?
- **Pricing movements:** Spreads widening or tightening?

**What you learn:** Whether the credit is improving, stable, or deteriorating — and how fast.

---

## Workflow Variations

### Quick Credit Screen (3 tools)
For a fast initial assessment when time is limited:
1. `search_companies` — metrics and risk flags
2. `search_bonds` — debt stack and maturities
3. `search_pricing` — market view

### Structural Subordination Deep Dive (4 tools)
When the question is specifically about holdco/opco risk:
1. `get_corporate_structure` — entity hierarchy and debt location
2. `search_bonds` with `issuer_type` filter — holdco vs opco issuances
3. `get_guarantors` — guarantee coverage on holdco bonds
4. `search_documents` with `section_type="guarantor_list"` — full guarantor details

### Distress Assessment (5 tools)
When evaluating a credit that appears to be in trouble:
1. `search_companies` — current metrics and risk flags
2. `search_pricing` — how does the market price the risk?
3. `search_bonds` — maturity wall analysis (use `maturity_before` filter)
4. `search_covenants` — headroom calculation
5. `get_changes` — trajectory over last 6 months

### Peer Comparison (2 tools)
When comparing credits within a sector:
1. `search_companies` with `sector` filter — pull multiple companies, sort by metric
2. `search_pricing` with multiple tickers — compare relative value across issuers

## Presentation Guidelines

When presenting credit analysis results:

- **Lead with the conclusion:** "XYZ is a BB-rated leveraged credit at 5.2x leverage with adequate but narrowing covenant headroom."
- **Organize by priority:** Start with the most important findings (distress flags, structural risks), then supporting detail.
- **Quantify everything:** Don't say "highly leveraged" — say "5.2x leveraged vs 3.8x sector median."
- **Flag data gaps:** If pricing is stale (high `staleness_days`) or a tool returned limited data, say so.
- **Connect the dots:** A maturity wall is more concerning when combined with widening spreads and declining coverage. Present findings as an interconnected picture, not isolated data points.
