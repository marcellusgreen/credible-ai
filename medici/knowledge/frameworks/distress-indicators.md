# Early Warning Signs of Credit Distress

Spot trouble before it becomes obvious. Distress rarely arrives overnight — it builds through a series of deteriorating signals across market pricing, financial metrics, and structural changes. The goal is to identify credits moving from "healthy" to "stressed" to "distressed" and act accordingly.

## Market Pricing Signals

Markets often price in distress before it shows up in financial statements:

### Yield and Spread Widening
- **Spreads widening** relative to peers or the broader market = the market is pricing in higher default probability
- Compare the bond's spread to its sector average — a company trading 200bps wide of peers has company-specific risk
- Rapid widening (100bps+ in a month) is an acute warning signal

### Bond Price Levels

Bond prices relative to par give a quick read on market expectations:

| Price Range | Market View |
|------------|------------|
| $90-100 | Normal; no distress concern |
| $80-90 | Caution; credit deterioration priced in |
| $70-80 | Stressed; meaningful default risk |
| $60-70 | Distressed; default considered likely |
| $40-60 | Deeply distressed; low recovery expected |
| < $40 | Near-zero recovery; equity-like optionality only |

**Key thresholds:**
- **Below $80:** The bond is "stressed" — institutional investors start paying close attention
- **Below $60:** The bond is "distressed" — dedicated distressed funds are the marginal buyer
- **Spread > 1000bps (10%):** Commonly used as the CCC/distressed threshold

### Price Divergence Across Tranches
When secured bonds trade near par but unsecured bonds trade at $0.50, the market is telling you the fulcrum security sits between those tranches. Divergence reveals where the market thinks recovery breaks.

## Fundamental Deterioration Signals

### Rising Leverage / Declining Coverage
- Leverage ratio trending up quarter over quarter = earnings declining or debt increasing
- Interest coverage trending down = less room to service debt
- The combination of rising leverage + declining coverage is the classic deterioration pattern
- Watch for sudden jumps — a 1x increase in leverage in a single quarter signals a material event (asset impairment, EBITDA miss, debt-funded acquisition)

### Approaching Maturity Wall
- Significant debt maturing within 12-24 months with no refinancing plan
- `has_near_term_maturity` flag is the first alert
- Check `debt_due_1yr` and `debt_due_2yr` relative to total debt — if >30% matures soon, it's a concentration risk
- Most dangerous when combined with widening spreads (can't refinance at reasonable cost) or declining earnings (can't pay down from cash flow)

### Floating Rate Exposure in Rising Rate Environment
- Companies with significant floating-rate debt see interest expense rise mechanically with rates
- A company at 2x coverage with 70% floating rate debt can quickly deteriorate to 1.5x or below
- Check `has_floating_rate` flag and filter bonds by `rate_type="floating"`

### Cash Flow Warning Signs
- Negative free cash flow for multiple consecutive quarters
- Cash balance declining without corresponding debt reduction
- Reliance on revolver draws to fund operations (check if revolving credit facility is increasingly drawn)

## Covenant and Structural Warning Signs

### Narrowing Covenant Headroom
- Track the gap between current metrics and covenant thresholds over time
- Headroom shrinking quarter over quarter = on a path to tripping covenants
- Companies that are close to tripping covenants often negotiate amendments — watch for covenant modification disclosures

### Increasing Structural Complexity
- New unrestricted subsidiary designations — may signal asset-stripping or value shifting
- Transfer of assets to entities outside the credit group
- New VIE structures appearing
- Guarantee releases — reduction in guarantor coverage weakens creditor protection

### Rating Agency Actions
- Downgrade to CCC or below = rating agencies see a realistic path to default
- Negative outlook or watch = downgrade likely within 6-12 months
- Multiple notch downgrades in a single action = sudden, severe deterioration

## The Distress Timeline

Credit distress typically follows a recognizable sequence:

1. **Earnings miss / EBITDA decline** — leverage ticks up
2. **Spread widening** — market reacts before next earnings release
3. **Rating downgrade or negative watch** — agencies catch up
4. **Covenant headroom narrows** — approaching trip levels
5. **Maturity wall approaches** — refinancing becomes the central question
6. **Revolver draw / cash burn** — company consuming liquidity
7. **Advisor engagement** — company hires restructuring advisors (often disclosed late)
8. **Covenant breach / payment miss** — formal distress event
9. **Restructuring negotiations** — out-of-court exchange or Chapter 11

**The earlier you identify a credit on this path, the more options exist** — sell at higher prices, hedge exposure, or position for the restructuring.

## Sector-Specific Stress Indicators

Different sectors have different early warning signals:

- **Energy:** Commodity price collapse, reserve writedowns, hedging rolling off
- **Retail:** Same-store sales decline, inventory buildup, lease obligation stress
- **Healthcare:** Reimbursement rate cuts, regulatory risk, litigation liability
- **Real estate:** Occupancy declining, rent rolls deteriorating, DSCR below 1.0x
- **Technology:** Customer churn, ARR decline, burn rate acceleration

## Medici Tools

### `search_pricing` — Market Distress Signals
- `last_price` — bonds trading below $80 are stressed, below $60 distressed
- `ytm_bps` and `spread_bps` — elevated yields/spreads = higher perceived default risk
- Compare spreads across a company's tranches — divergence reveals the fulcrum
- `staleness_days` — illiquid bonds (high staleness) can be a warning sign themselves
- Filter by `min_ytm` to screen for bonds with yields above a distress threshold

### `get_changes` — Trajectory & Deterioration
- `pricing_changes` — identifies spread widening or price drops over a time period
- `metric_changes` — leverage rising, coverage declining
- Set `since` to 3 months or 6 months ago to see recent trajectory
- New issuances appearing (more borrowing) or maturities approaching without refinancing

### `search_companies` — Risk Flag Screening
- `has_near_term_maturity` — flag for maturities within 24 months
- `has_floating_rate` — exposure to rising rates
- `has_structural_sub` — structural complexity adding risk
- `has_unrestricted_subs` — potential for value leakage
- Filter by `rating_bucket="HY-CCC"` to screen for lowest-rated credits
- Sort by `sort=-leverage_ratio` to find the most leveraged names
- Combine filters: `has_near_term_maturity=true&min_leverage=6` finds highly leveraged companies with approaching maturities

### `search_bonds` — Maturity Wall Analysis
- `maturity_before` filter to find bonds maturing before a specific date
- `outstanding` amounts maturing = size of the refinancing challenge
- `rate_type="floating"` to quantify floating rate exposure
- `has_pricing=true` to focus on bonds with observable market prices

### `search_covenants` — Headroom Monitoring
- Pull covenant thresholds and compare to current company metrics
- `test_metric` and `threshold_value` vs actual `leverage_ratio` or `interest_coverage`
- Tight headroom + deteriorating metrics = imminent trip
- `cure_period_days` — how long does the company have if it trips
