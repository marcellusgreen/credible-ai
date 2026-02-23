# Causes of Financial Distress

Financial distress occurs when a company's ability to meet current or future financial obligations becomes materially impaired. The ability-to-pay test is more meaningful than balance-sheet insolvency — a company can be balance-sheet solvent but still unable to refinance maturing debt.

## The Four Triggers

There are four primary causes of financial distress, each with different analytical implications for creditors:

### 1. Lack of Access to Capital Markets

The dominant cause in credit crises. Companies whose business model depends on continuous capital markets access can enter distress rapidly even without technical insolvency.

**Pattern:** The company has a sound underlying business but its capital structure requires periodic refinancing. When credit markets freeze or the company's perceived creditworthiness deteriorates, it cannot roll over maturing debt.

**Examples:** Commercial paper issuers who can't refinance, companies with near-term maturity walls during credit crunches, holding companies with all liabilities concentrated at the parent while liquid assets sit at subsidiaries.

**Analytical implication:** If the business is fundamentally sound, this type of distress is often curable through restructuring. Focus on whether the capital structure can be fixed — the going-concern value likely exceeds liquidation value significantly.

### 2. Deterioration of Operating Performance

Caused by cyclical downturns, cost inflation, competition, regulation/deregulation, uncompetitive products, unrealistic business plans, or poor management. Rarely a single factor — usually a combination.

**Warning signals in the data:**
- Interest coverage falling quarter over quarter (e.g., from 5x to below 3x)
- Leverage spiking from acquisitions funded with debt
- Gross margins deteriorating steadily
- Customer or revenue concentration increasing
- EBITDA declining while debt stays flat or grows

**Analytical implication:** More dangerous than capital access failure because the underlying business may not support the debt at any capital structure. Assess whether operating problems are cyclical (recoverable) or structural (permanent impairment).

### 3. Deterioration of GAAP Performance

A critical distinction: GAAP losses may not represent cash losses. Mark-to-market accounting on derivatives, impairment charges on goodwill, and restructuring charges can create massive reported losses that don't reflect economic reality.

**Key principle:** Focus on what the GAAP numbers mean, not just what they are. A company reporting billions in mark-to-market losses may have perfectly adequate cash flow to service debt.

**Analytical implication:** Distress triggered by non-cash GAAP deterioration may be curable with a capital infusion or exchange offer. The fulcrum security may be the common stock, not the debt — meaning bondholders may recover well.

### 4. Large Off-Balance-Sheet Contingent Liabilities

Categories include: tort claims (asbestos, environmental), fraud liabilities, SPE/SIV liabilities, complex derivative contracts, and long-term pension/healthcare obligations.

**Pattern:** A company with no operating problems and adequate capitalization files for Chapter 11 solely to manage runaway contingent liabilities (e.g., USG Corporation filed in 2001 purely to manage asbestos litigation costs).

**Analytical implication:** The company's operating business may be worth full value — the distress is purely a claims management problem. Secured creditors and short-term maturities are typically safe.

## Analytical Decision Framework

Setting the initial analytical posture based on the cause of distress:

| Cause | Assumption | Focus |
|-------|-----------|-------|
| Capital access failure, sound business | Reorganization likely and feasible | Going-concern valuation; recovery analysis |
| Business fundamentals in question | Worst case: Chapter 11 liquidating plan or Chapter 7 | Only secured/reinstated claims; liquidation value |
| Sudden liability ballooning | Worst case for unsecured | Focus on secured claims and reinstatement probability |

**Critical threshold:** For adequately secured (oversecured) creditors, understanding the cause of distress is largely academic — their claims will be reinstated or paid in full regardless. The analysis matters most for unsecured claims whose recovery depends on the nature and resolution of the distress.

## Medici Tools

### `search_companies` — Distress Signal Detection
- `leverage_ratio` spiking over time → possible acquisition-funded deterioration
- `interest_coverage` declining quarter over quarter → operating deterioration
- `has_near_term_maturity` + tight credit markets → capital access risk
- `debt_due_1yr`, `debt_due_2yr` → near-term refinancing pressure

### `get_changes` — Trajectory Analysis
- Track `leverage_ratio`, `interest_coverage` trends over 6-12 months
- Rising leverage + declining coverage = classic operating deterioration pattern
- Sudden metric jumps may indicate M&A-funded leverage spike

### `search_bonds` — Maturity Wall Assessment
- Use `maturity_before` filter to identify near-term maturities
- Large maturities in next 12-18 months = refinancing risk
- Compare maturity wall to available liquidity and FCF

### `search_documents` — Root Cause Investigation
- `section_type="mda_liquidity"` — management's own assessment of liquidity and going concern
- `section_type="risk_factors"` — disclosed risks including contingent liabilities
- Search `query="going concern"` — auditor's doubt about continued operations
