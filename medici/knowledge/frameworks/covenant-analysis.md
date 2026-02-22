# Covenant Analysis

Covenants are the contractual guardrails that protect creditors. They restrict what the company can do and create early warning triggers when credit quality deteriorates. Strong covenants protect downside; weak or absent covenants leave creditors exposed.

## Maintenance vs Incurrence Covenants

The most important distinction in covenant analysis:

**Maintenance covenants** are tested periodically (usually quarterly). The company must continuously satisfy the test — if it fails, it's in default (or must cure). These are the strongest form of protection because they create early intervention points.

- Example: "Total leverage shall not exceed 5.0x, tested quarterly"
- If the company's leverage hits 5.1x at quarter-end, it's in technical default
- Gives creditors leverage to renegotiate terms before the situation gets worse

**Incurrence covenants** are only tested when the company takes a specific action (issuing new debt, making a distribution, acquiring a company). The company can breach the ratio passively without triggering default — the covenant only fires if the company actively tries to do something.

- Example: "The company may not incur additional debt if pro forma leverage would exceed 4.5x"
- If leverage drifts to 6.0x from declining EBITDA, no default is triggered
- Only matters when the company wants to borrow more or pay a dividend

**Bottom line:** Maintenance covenants protect creditors proactively. Incurrence covenants only prevent the company from making things worse. A credit with only incurrence covenants (common in high-yield bonds) has weaker protection than one with maintenance covenants (common in bank loans).

## Key Test Metrics

Covenants typically test one or more of these financial metrics:

| Test Metric | What It Measures |
|------------|-----------------|
| Total leverage ratio | Total Debt / EBITDA — overall indebtedness |
| First lien leverage | First Lien Debt / EBITDA — senior secured capacity |
| Interest coverage | EBITDA / Interest Expense — debt serviceability |
| Fixed charge coverage | (EBITDA - Capex) / Fixed Charges — broader cash adequacy |
| Secured leverage | Secured Debt / EBITDA — collateral utilization |

## Covenant Headroom

Headroom is the gap between the company's current metric and the covenant threshold. It answers: "How much room does the company have before it trips a covenant?"

**Calculating headroom:**
- Current leverage: 4.2x; Covenant threshold: 5.5x → Headroom = 1.3x (comfortable)
- Current leverage: 5.3x; Covenant threshold: 5.5x → Headroom = 0.2x (tight — one bad quarter trips it)

**Interpreting headroom:**
- **Wide headroom (>1.5x):** Covenant provides limited near-term protection; the company can deteriorate significantly before the guardrail bites
- **Moderate headroom (0.5-1.5x):** Healthy balance — protective without being immediately constraining
- **Tight headroom (<0.5x):** Company is near the trip wire; creditors are close to gaining leverage
- **Negative headroom:** Covenant already breached (in default or waived)

## Restricted Payments and Debt Incurrence Baskets

Beyond financial covenants, credit agreements contain **negative covenants** that restrict specific actions:

- **Restricted payments:** Limits on dividends, share buybacks, and payments to equity holders. Protects creditors from value leaking to equity before debt is repaid.
- **Debt incurrence baskets:** Caps on how much additional debt the company can take on. Often structured as a fixed dollar amount plus a ratio-based test.
- **Asset sale covenants:** Require proceeds from asset sales to be used to repay debt rather than distributed to equity.
- **Affiliate transaction limits:** Prevent value transfers to related entities (especially sponsor-owned companies).

**Basket sizing matters:** A $500M restricted payment basket at a $10B revenue company is modest; the same basket at a $1B revenue company is enormous.

## Change of Control Provisions

Bondholder protections in M&A situations:

- **Put right (101 put):** Most common — if the company undergoes a change of control, bondholders can put their bonds back to the issuer at 101% of par. Protects against LBOs that pile on additional debt.
- **Double trigger:** Some provisions require both a change of control AND a ratings downgrade to trigger the put.
- **Portability:** Some leveraged loan agreements allow change of control without triggering default if certain conditions are met (e.g., pro forma leverage below a threshold). This is borrower-friendly.

## Covenant-Lite (Cov-Lite) Loans

A cov-lite loan has **no maintenance financial covenants** — only incurrence-based tests. This structure became dominant in leveraged lending during periods of easy credit.

**Implications for creditors:**
- No quarterly testing means no early warning system
- The company can deteriorate significantly before any covenant triggers
- Creditors lose the ability to force renegotiation at early stages of distress
- By the time an incurrence test is triggered (company tries to borrow more), the situation may already be severe

**When analyzing a cov-lite credit:** Rely more heavily on market signals (bond prices, spread widening) and financial metric trajectory since covenant-based early warnings won't be available.

## Step-Downs

Some covenants tighten over time with scheduled step-downs:

- Year 1: Max leverage 6.0x
- Year 2: Max leverage 5.5x
- Year 3: Max leverage 5.0x

Step-downs are creditor-friendly — they require the company to deleverage on schedule. Check the `step_down_schedule` field for the tightening timeline.

## Cure Rights and Grace Periods

When a covenant is breached, the company may have options before it's a full event of default:

- **Equity cure:** The sponsor can inject equity to bring the ratio back into compliance. Usually limited (e.g., 2-3 times over the life of the loan).
- **Grace period:** A window (typically 15-30 days) after the breach to cure or negotiate a waiver.
- **Waiver/amendment:** Creditors can agree to waive the breach or amend the threshold — usually in exchange for a fee and tighter terms going forward.

## Medici Tools

### `search_covenants` — Structured Covenant Data
- Filter by `ticker` to get all covenants for a company
- `covenant_type` — "financial", "negative", "protective"
- `test_metric` — the specific ratio being tested (leverage_ratio, interest_coverage, etc.)
- `threshold_value` — the covenant threshold level
- `threshold_type` — max (cannot exceed) or min (must maintain above)
- `has_step_down` — boolean flag for step-down schedules
- `step_down_schedule` — the tightening timeline
- `cure_period_days` — how long the company has to cure a breach

### `search_companies` + `search_covenants` — Headroom Calculation
- Get current `leverage_ratio` or `interest_coverage` from `search_companies`
- Get covenant `threshold_value` and `test_metric` from `search_covenants`
- Headroom = threshold_value - current_metric (for max tests) or current_metric - threshold_value (for min tests)
- Present headroom in both absolute ratio terms and as a percentage of the threshold

### `search_documents` — Full Covenant Language
- `section_type="covenants"` — pulls covenant sections from SEC filings
- `section_type="credit_agreement"` — full credit agreement text for detailed review
- Search `query="change of control"` — find change of control provisions
- Search `query="restricted payments"` — find distribution limitations
- Search `query="covenant lite"` or `query="no financial maintenance"` — identify cov-lite structures

### `get_changes` — Covenant Trajectory
- Track how current metrics are trending relative to covenant thresholds
- If leverage is rising toward the covenant level, flag the narrowing headroom
- Combine with step-down schedules to assess whether the company can meet tightening thresholds
