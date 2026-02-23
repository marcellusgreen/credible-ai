# Distress Valuation: Three Modes

Distress investors must value companies using three distinct modes — going concern, resource conversion, and liquidation. A single-mode valuation is incomplete. The going-concern value sets the upside; the liquidation value sets the floor; resource conversion identifies hidden value in separable assets.

## Mode 1: Going-Concern Valuation (EBITDA Multiple)

The standard approach for companies expected to continue operating. Value the enterprise as a whole based on its earnings capacity.

### Step 1: Calculate Adjusted EBITDA

Start with reported EBITDA, then make critical adjustments:

**Maintenance capex adjustment (most important):** Replace book depreciation with actual required maintenance capital expenditure. Use median 5-year capex as a proxy. EBITDA overstates earnings when depreciation is less than required capex; understates when depreciation exceeds required capex.

**Restructuring charges:** Determine which are truly one-time vs. recurring. Not all restructuring charges are cash charges — add back the non-cash portion.

**Asset impairment charges:** Non-cash, should be added back. Note they generate NOLs that are deferred tax assets for the reorganized company.

**Operating leases (EBITDAR):** For companies that lease rather than own, add back rent expense. Capitalizing lease expense creates a long-term debt equivalent. In Chapter 11, below-market leases can become significant assets.

### Step 2: Apply a Valuation Multiple

The multiple depends on growth rate and discount rate:

| Discount Rate | 0% Growth | 3% Growth | 5% Growth | 8% Growth |
|--------------|-----------|-----------|-----------|-----------|
| 10% | 9.1x | 11.7x | 14.1x | 18.4x |
| 15% | 6.5x | 7.9x | 9.1x | 11.3x |
| 20% | 4.9x | 5.8x | 6.4x | 7.7x |
| 25% | 4.0x | 4.5x | 4.9x | 5.8x |

**Critical rule for distressed companies:** The EBITDA multiple will always be lower than comparable healthy peers. In practice, distressed companies trade at 50-70% of peer multiples. Example: healthy peers at 7.6-8.8x; distressed company valued at 4.6x.

### Step 3: Enterprise Value to Claims Waterfall

Enterprise Value = EBITDA multiple result. Then subtract claims in order of priority:
1. Administrative claims (super-priority — professional fees, DIP financing)
2. Secured claims (up to collateral value)
3. Priority unsecured claims (taxes, employee claims)
4. General unsecured claims
5. Subordinated claims
6. Equity

The **fulcrum security** is the class where value runs out — above it, full recovery; below it, zero or partial recovery.

## Mode 2: Resource Conversion Valuation

Looks beyond the consolidated going concern to identify separable assets worth more sold or converted than kept.

**Segment-level analysis:** Break the company into business segments. Identify underperforming segments dragging consolidated EBITDA and assets that could be sold without impairing ongoing earnings.

**Below-market leases:** In Chapter 11, the debtor has 210 days to assume or reject unexpired leases. Below-market leases can be assigned to third parties for substantial cash. This can be a major hidden asset — hundreds of millions in value that doesn't appear on the balance sheet.

**NOL preservation (tax asset):**
- Net operating losses can be carried back 2 years and forward 20 years
- Section 382 limits NOL use after a change of ownership
- Exception: if prepetition creditors/shareholders end up owning 50%+ of reorganized company, and debt was "old and cold" (held 18+ months before filing), the Section 382 limitation may be avoided
- Present value of NOL tax savings appears as "deferred tax asset" on the balance sheet

**Key principle:** A company may be worth more broken up than as a going concern. The distress investor should always consider whether parts are worth more than the whole.

## Mode 3: Liquidation Valuation

Required by Section 1129(a)(7) — any Chapter 11 plan must show that each creditor receives at least as much as they would in a Chapter 7 liquidation. This sets the absolute floor for recovery.

### Typical Liquidation Recovery Rates by Asset Type

| Asset | Typical Recovery | Notes |
|-------|-----------------|-------|
| Cash | 100% | If available |
| Accounts receivable (trade) | 50-70% | Quality of receivables matters |
| Inventory (finished goods) | 30-50% | Depends on specialty vs. commodity |
| Inventory (raw materials) | 20-40% | Less liquid than finished goods |
| Land and buildings | 70-100% | Location dependent |
| Machinery and equipment | 50-80% | Specialized = lower recovery |
| Specialized tooling/dies | 10-25% | Very illiquid |
| Goodwill | 0% | Always zero in liquidation |
| Intangible assets | 0-10% | Trademarks may have some value |
| Foreign assets | 5-15% | Cross-border enforcement difficulty |

**Key takeaway:** The gap between book value and liquidation value can be enormous — a typical manufacturer may recover only 25-35% of book asset value in liquidation. Goodwill and intangibles are worthless. Physical assets trade at deep discounts.

### Liquidation Waterfall

After computing gross liquidation value, deduct in order:
1. Liquidation costs (trustee fees, wind-down expenses)
2. Secured claims (up to collateral value)
3. Administrative priority claims
4. Priority tax claims
5. Priority employee claims
6. General unsecured claims

**Professional cost warning:** In small cases, professional costs can consume the entire estate, rendering any recovery for unsecured creditors impossible. Time in Chapter 11 is the enemy of unsecured creditors.

## Connecting the Three Modes

| Mode | When It Dominates | Key Metric |
|------|------------------|------------|
| Going concern | Sound business, fixable capital structure | EBITDA multiple × adjusted EBITDA |
| Resource conversion | Sum-of-parts > going concern; hidden assets | Segment EBITDA + separable asset values |
| Liquidation | Business fundamentally impaired; sets floor | Asset-by-asset recovery analysis |

The distress investor always computes all three and compares. The going-concern value must exceed the liquidation value for reorganization to make sense. If the liquidation value exceeds going-concern value, the company should be sold or liquidated.

## Medici Tools

### `search_companies` — Going-Concern Inputs
- `leverage_ratio`, `net_leverage_ratio` — current multiple of debt to EBITDA
- `interest_coverage` — ability to service debt from operations
- `sector`, `industry` — for selecting comparable company multiples
- Compare company's implied EV/EBITDA to sector peers

### `search_bonds` — Claims Waterfall Construction
- `seniority` — secured, senior unsecured, subordinated
- `outstanding` — size of each claim class
- `issuer_type` — holdco vs opco (affects structural priority)
- Sum claims at each level to build the waterfall

### `search_pricing` — Market-Implied Valuation
- Bond prices as % of par imply the market's expected recovery
- Secured bonds at $95+ = market expects full recovery on secured claims
- Unsecured bonds at $40-60 = market expects partial recovery; fulcrum may be nearby
- Large price gaps between secured and unsecured = market pricing structural subordination

### `get_corporate_structure` — Resource Conversion Inputs
- Identify subsidiary-level assets and debt
- `debt_at_entity` at each level reveals claims structure for liquidation analysis
- Subsidiaries with significant assets but no debt = potential resource conversion targets
