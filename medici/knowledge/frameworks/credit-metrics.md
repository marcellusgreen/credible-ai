# Credit Metric Analysis

Assess fundamental credit quality through financial ratios. The numbers alone aren't enough — context matters: what leverage means for a utility is different from what it means for a tech company, and trajectory matters as much as the current level.

## Leverage Ratio (Total Debt / EBITDA)

The single most important credit metric. Tells you how many years of earnings it would take to repay all debt, assuming all EBITDA went to debt repayment.

| Leverage | Interpretation |
|----------|---------------|
| < 2x | Conservative; typical of investment-grade industrials |
| 2-3x | Moderate; comfortable for most sectors |
| 3-4x | Elevated; borderline IG / upper HY territory |
| 4-6x | Leveraged; typical of LBO/sponsor-backed companies |
| 6-8x | Highly leveraged; limited margin for error |
| > 8x | Distressed territory; debt likely exceeds enterprise value |

**Nuances:**
- Leverage is a point-in-time snapshot — a company at 5x and deleveraging is very different from one at 5x and re-leveraging
- EBITDA can be inflated by add-backs; always consider whether EBITDA is sustainable
- Cyclical businesses may look low-leverage at peak earnings but are actually much more leveraged through-cycle

## Net Leverage Ratio (Net Debt / EBITDA)

Adjusts for cash on hand: Net Debt = Total Debt minus Cash. Companies with large cash balances (tech, pharma) may have high gross leverage but manageable net leverage.

**When to use net vs gross:**
- Net leverage is more relevant when cash is truly available to repay debt (unrestricted, onshore, not needed for operations)
- Gross leverage is more conservative and appropriate when cash is trapped in foreign subsidiaries, restricted, or needed for working capital

## Interest Coverage (EBITDA / Interest Expense)

Measures the company's ability to service its debt — can it pay the interest bill from current earnings?

| Coverage | Interpretation |
|----------|---------------|
| > 4x | Strong; comfortable margin |
| 2-4x | Adequate; typical for leveraged credits |
| 1.5-2x | Thin; little room for earnings decline |
| 1-1.5x | Danger zone; at risk of missing payments |
| < 1x | Cannot cover interest from operations; burning cash or borrowing to pay interest |

**Rising rate risk:** Companies with floating-rate debt see coverage deteriorate as rates rise, even if EBITDA is stable. Check the `has_floating_rate` flag and `rate_type` on bonds.

## Secured Leverage

Secured Debt / EBITDA — how much of the leverage has collateral backing. Important for unsecured creditors because it tells them how much of the enterprise value is spoken for before they get anything.

- A company with 6x total leverage but only 2x secured leverage leaves significant cushion for unsecured creditors
- A company with 6x total and 5x secured leverage leaves almost nothing for unsecured holders

## Fixed Charge Coverage

Broadens the coverage analysis beyond just interest to include mandatory cash obligations:

**Fixed Charges** = Interest Expense + Required Amortization + Capex (maintenance) + Taxes

This is a stricter test of cash flow adequacy. A company might have 2x interest coverage but only 0.8x fixed charge coverage — meaning it can pay interest but not all its mandatory obligations.

## Maturity Profile

The timing of when debt comes due is as important as the total amount. A company with manageable leverage can still fail if too much debt matures at once and it can't refinance.

**Maturity wall:** A concentration of maturities in a short period. Dangerous when:
- Credit markets are tight (hard to refinance)
- The company's credit quality has deteriorated
- Multiple tranches mature within 12-18 months

**Key profile metrics:**
- `debt_due_1yr` / `debt_due_2yr` / `debt_due_3yr` — near-term maturities
- `nearest_maturity` — when the first debt comes due
- `weighted_avg_maturity` — overall maturity profile length

**Healthy profile:** Laddered maturities spread over 5-10 years, no single year with more than 20-25% of total debt maturing.

## Free Cash Flow Adequacy

Can the company organically deleverage — that is, pay down debt from internally generated cash flow without needing to access capital markets?

- **Positive FCF** means the company can reduce debt over time even if capital markets are closed
- **Negative FCF** means the company needs external financing to survive — a dangerous position in distress
- Compare FCF to near-term maturities: can the company pay off upcoming debt from cash flow alone?

## Sector Context

Credit metrics must be interpreted in sector context. The same leverage ratio means very different things across industries:

| Sector | Typical Metric | Why |
|--------|---------------|-----|
| Banks | PPNR (Pre-Provision Net Revenue) | No traditional EBITDA; use pre-provision earnings |
| REITs | FFO (Funds from Operations) | Depreciation is large and non-cash; EBITDA overstates |
| Utilities | Regulated returns, FFO/Debt | Stable regulated cash flows support higher leverage |
| Energy | Debt/EBITDAX | Exploration expense add-back; commodity price sensitivity |
| Tech | Net leverage preferred | Often hold large cash balances |
| Healthcare | Rent-adjusted leverage | Operating leases are material |

DebtStack stores the industry-specific metric in the EBITDA field and indicates the type via the `ebitda_type` field ("ebitda", "ppnr", "ffo", "noi").

## Trajectory Matters

A single point-in-time metric is less informative than the direction of travel. Key questions:

- Is leverage rising or falling quarter over quarter?
- Is coverage improving or deteriorating?
- Is the maturity profile extending (good) or compressing (bad)?
- Are credit ratings on upgrade or downgrade watch?

## Medici Tools

### `search_companies` — Core Credit Metrics
- `leverage_ratio` — Total Debt / EBITDA
- `net_leverage_ratio` — Net Debt / EBITDA
- `interest_coverage` — EBITDA / Interest Expense
- `secured_leverage` — Secured Debt / EBITDA
- `total_debt`, `secured_debt`, `unsecured_debt`, `net_debt` — absolute debt levels
- `sector`, `industry` — for peer comparison and sector context
- `rating_bucket` — IG, HY-BB, HY-B, HY-CCC, NR
- `sp_rating`, `moodys_rating` — specific agency ratings
- Sort by any metric for screening: e.g., `sort=-leverage_ratio` for most leveraged first
- Filter ranges: `min_leverage`, `max_leverage`, `min_net_leverage`, `max_net_leverage`

### `search_companies` — Maturity Profile
- `debt_due_1yr`, `debt_due_2yr`, `debt_due_3yr` — near-term maturities (amounts in cents)
- `nearest_maturity` — date of first upcoming maturity
- `weighted_avg_maturity` — weighted average maturity in years
- `has_near_term_maturity` — boolean flag for debt maturing within 24 months

### `search_bonds` — Maturity Wall Analysis
- Filter by `ticker` and use `maturity_before` to find bonds maturing before a specific date
- `outstanding` at each maturity = size of the maturity wall
- `rate_type` filter ("fixed" vs "floating") to assess interest rate exposure

### `search_companies` — Risk Flags
- `has_floating_rate` — boolean for floating rate debt exposure
- Use in combination with interest coverage to assess rate sensitivity

### `get_changes` — Metric Trajectory
- Pass `ticker` and `since` date to see how metrics have moved
- Returns `metric_changes` showing leverage, coverage trends over time
- Rising leverage or declining coverage = deteriorating credit quality
- Useful for spotting inflection points

### `search_companies` — Peer Comparison
- Filter by `sector` or `industry` to pull comparable companies
- Compare `leverage_ratio`, `interest_coverage` across peers
- Sort by metric to rank: `sort=leverage_ratio&sector=Energy` shows least to most leveraged energy companies
