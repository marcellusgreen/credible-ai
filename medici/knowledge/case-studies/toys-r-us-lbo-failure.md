# Case Study: Toys "R" Us — LBO, Disruption, and Liquidation

The Toys "R" Us bankruptcy illustrates how leveraged buyout debt can cripple a company's ability to adapt to competitive disruption. It demonstrates the interplay between capital access failure (Cause #1) and deteriorating operating performance (Cause #2) from the distress framework.

## The Leveraged Buyout (2005)

In 2005, KKR, Bain Capital, and Vornado Realty Trust acquired Toys "R" Us for $6.6 billion plus assumption of $1 billion in existing debt. The sponsors contributed $1.3 billion in equity and financed the remainder with debt, increasing total leverage from $1 billion to $6.2 billion — 82.7% of total capital.

**Post-LBO capital structure:**
- ~$6.2 billion total debt
- Annual interest expense: ~$450 million
- Interest coverage was thin from day one — the company needed consistent operating cash flow just to service debt

**Key analytical point:** The LBO created a capital structure that left no margin of safety. Any decline in operating performance would threaten the company's ability to service debt. This is the classic Moyer warning: leverage amplifies both upside and downside.

## Operating Deterioration (2005–2017)

The company faced compounding competitive pressures it couldn't invest to counter:

**Amazon and e-commerce:** In 2000, Toys "R" Us had signed an exclusive agreement with Amazon to be the sole toy seller on the platform. This deal soured and headed to litigation. By the time the company tried to build its own e-commerce capabilities, Amazon had built an insurmountable lead.

**Walmart and Target:** Mass-market retailers used toys as loss leaders to drive store traffic. Toys "R" Us couldn't match their pricing without destroying already-thin margins.

**Category decline:** The shift toward digital entertainment and electronics reduced demand for traditional toys.

**The debt trap:** With $450 million in annual interest payments, management had no capital to invest in store renovations, e-commerce technology, or competitive pricing. The company "stuck with the structure implemented by the LBO" for over a decade without meaningful innovation. The LBO prioritized cost cutting over developing new business approaches.

## Applying the Distress Framework

**Cause diagnosis:** This was a dual-cause distress — both capital structure (excessive LBO debt) and operating deterioration (competitive disruption). The debate over which was primary matters for investment analysis:

| Perspective | Implication for Creditors |
|-------------|--------------------------|
| Debt caused failure | Business had going-concern value; restructuring could have worked if debt was reduced |
| Disruption caused failure | Business was fundamentally impaired; even a clean balance sheet might not have saved it |

Columbia Law professors Casey and Gotberg argued it was "death by disruption, not debt" — the company's business model had become obsolete regardless of capital structure. Their key insight: "bankruptcy law does not cure economic failings."

**For credit analysts, the practical lesson:** When evaluating a distressed LBO, ask whether reducing debt would actually fix the business. If the operating model is broken, even a reorganization with significant debt reduction won't generate adequate going-concern value. In such cases, liquidation analysis (Mode 3 from the valuation framework) becomes the relevant valuation mode.

## The Chapter 11 Filing and Liquidation (2017–2018)

**Filing:** September 18, 2017 — with approximately $4.9 billion in debt, including $400 million due in 2018 and $1.7 billion due in 2019 (a classic maturity wall).

**Attempted reorganization failed:** The company entered Chapter 11 expecting to reorganize, but holiday sales in late 2017 were weak. Vendors tightened trade terms. Liquidation of all U.S. stores was announced in March 2018.

**Recovery analysis:** The liquidation outcome was catastrophic for unsecured creditors. Secured lenders had claims on specific store assets and inventory, but even their recoveries were impaired by the rapid liquidation timeline. General unsecured creditors recovered pennies on the dollar.

**Why liquidation, not reorganization?** The going-concern value (Mode 1) was below liquidation value (Mode 3) because the underlying retail business couldn't generate sufficient EBITDA to support any meaningful capital structure. This is the exact scenario described in the distress valuation framework: when liquidation value exceeds going-concern value, the company should be sold or liquidated.

## Lessons for Credit Investors

### 1. LBO Leverage Creates Fragility
Post-LBO interest coverage must be evaluated not just against current earnings but against a stress scenario. Toys "R" Us had adequate coverage at acquisition but zero margin for the competitive deterioration that followed.

### 2. Maturity Walls Kill
The $400M (2018) and $1.7B (2019) maturities created a ticking clock. When the business couldn't generate enough cash to refinance, and credit markets wouldn't extend new financing, the company was forced to file. Near-term maturities give creditors de facto seniority outside bankruptcy (Truth #1 from the risks framework).

### 3. Distinguish Cyclical from Structural Decline
Cyclical operating deterioration can be ridden out — structural decline cannot. Toys "R" Us faced a structural shift in retail that no amount of restructuring could reverse.

### 4. Trade Vendor Behavior Accelerates Distress
When vendors tightened trade terms after the filing, it destroyed the company's ability to stock inventory for the critical holiday season. Critical vendor risk (from the risks framework) can work in reverse — vendor withdrawal accelerates the death spiral.

### 5. Liquidation Recovery Rates Matter
For asset-heavy retailers, the liquidation analysis should consider: lease rejection values (below-market leases can be an asset), inventory recovery (30-50% for finished goods), and real estate values (location-dependent). Goodwill and brand value — which represented much of the $6.6 billion acquisition price — are worth zero in liquidation.

## Medici Tools Application

When analyzing a potential Toys "R" Us scenario in the DebtStack database:

- **`search_companies`** — Check `leverage_ratio` (was extreme post-LBO), `interest_coverage` (thin margin), sector trends
- **`search_bonds`** — Map the maturity wall using `maturity_before`; identify secured vs. unsecured tranches
- **`search_pricing`** — Look for bonds trading below $60 (distressed recovery pricing) and large spreads between secured and unsecured
- **`search_documents`** — Search `query="going concern"` in 10-K filings; check `section_type="risk_factors"` for competitive risks
