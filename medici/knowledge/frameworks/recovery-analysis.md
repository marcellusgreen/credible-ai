# Recovery Analysis & Fulcrum Security

Estimate what creditors actually recover in distress. The fulcrum security is the tranche where recovery transitions from full to partial — everything senior recovers par, everything junior gets nothing.

## Enterprise Value vs Total Claims

Recovery analysis starts with two numbers:

- **Enterprise value (EV):** What the company is worth as a going concern (or in liquidation). This is the total pie available to creditors.
- **Total claims:** The sum of all debt obligations across the capital structure.

If EV > total claims, all creditors recover in full and equity retains value. If EV < total claims, losses start at the bottom of the capital structure and work upward.

## The Recovery Waterfall

Value distributes top-down through the capital structure:

1. **Secured creditors** recover first, up to the value of their collateral (or their full claim if collateral covers it)
2. **Any remaining value** flows to unsecured creditors, distributed pro-rata by claim size at each priority level
3. **Subordinated debt** only recovers after all senior unsecured claims are satisfied
4. **Equity** is last — typically wiped out in true distress

**Example waterfall:**
- EV = $500M
- First lien debt: $200M → recovers $200M (100%)
- Senior unsecured: $400M → recovers $300M (75%)
- Subordinated: $100M → recovers $0 (0%)
- Equity: wiped out

## Identifying the Fulcrum Security

The fulcrum security is the tranche in the capital structure where recovery drops below 100%. It's the security that "controls" the restructuring because its holders have the strongest incentive to negotiate — they have something to gain and something to lose.

**How to find it:**
1. Stack all claims by priority (secured → senior unsecured → subordinated)
2. Subtract claims cumulatively from enterprise value, starting at the top
3. The tranche where cumulative claims exceed EV is the fulcrum

**Why it matters:**
- Creditors senior to the fulcrum expect full recovery — they're likely to accept a deal
- Creditors junior to the fulcrum expect zero — they have little leverage
- Holders of the fulcrum security are the swing vote in any restructuring negotiation
- Distressed debt investors specifically target the fulcrum security for upside optionality

## Par Value vs Market Value of Claims

- **Par value** is the face amount owed — what the bond contract says
- **Market value** is what the bond trades at today — what the market thinks recovery will be
- A bond trading at $0.60 on the dollar implies the market expects ~60% recovery
- The gap between par and market value across tranches reveals the market's view of where the fulcrum sits

## Bond Prices as Recovery Signals

Bond prices relative to par communicate market expectations:

| Price Range | Signal |
|------------|--------|
| $95-100 | Normal/investment grade; no distress priced in |
| $80-95 | Some credit concern; spreads widening |
| $60-80 | Stressed — meaningful default risk priced in |
| $40-60 | Distressed — default likely, recovery uncertain |
| $20-40 | Deeply distressed — low recovery expected |
| < $20 | Near-zero recovery expected |

## Secured Creditor Advantages

Secured creditors have structural advantages beyond just priority:

- **Collateral protection** — direct claim on specific assets, not just the general estate
- **DIP financing priority** — secured lenders often provide debtor-in-possession financing, which gets super-priority status
- **Adequate protection** — secured creditors can demand protection of their collateral value during bankruptcy
- **Credit bid rights** — can bid their debt (at face value) to acquire collateral in a Section 363 sale

## Medici Tools

### `search_bonds` — Build the Claims Stack
- Filter by `ticker` and sort by `seniority` to see the full debt stack
- `outstanding` field at each seniority level gives total claims per tranche
- Sum outstanding across all tranches = total claims against the company
- Compare total claims to estimated enterprise value to find the fulcrum

### `search_pricing` — Market-Implied Recovery
- `last_price` relative to par ($100) reveals market's recovery expectation
- Bonds trading well below par signal the market expects losses at that level
- `ytm` and `spread` (in basis points via `ytm_bps`, `spread_bps`) — elevated yields confirm distress pricing
- Compare pricing across tranches: if senior secured trades at $95 and senior unsecured at $50, the market sees the fulcrum between those tranches
- `last_trade_date` and `staleness_days` — check that pricing is recent enough to be meaningful

### `search_companies` — Aggregate Debt Metrics
- `total_debt` — total claim size (compare to estimated EV)
- `secured_debt` and `unsecured_debt` — quick split of claims by type
- `leverage_ratio` — extremely high leverage (>8x) means EV is unlikely to cover all claims

### `get_corporate_structure` — Entity-Level Claims
- `debt_at_entity` for each entity shows how claims distribute across the org structure
- Critical for complex structures: opco creditors recover from opco assets before holdco creditors see anything
- Entity-level analysis can reveal that the true fulcrum is different when you account for structural subordination

### `search_documents` — Debt Schedule Details
- Use `section_type="debt_footnote"` to find detailed debt schedules from SEC filings
- Debt footnotes often contain maturity schedules, interest rates, and covenant details not captured in structured data
- `section_type="mda_liquidity"` for management's discussion of liquidity and debt capacity
