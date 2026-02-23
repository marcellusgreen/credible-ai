# Distress Investing Risks

Understanding the risks specific to distressed debt is essential for evaluating recovery prospects. Standard credit metrics are necessary but insufficient — the real risks in distressed situations involve legal priority alteration, valuation disputes, and restructuring process hazards.

## Priority Alteration Risks

The absolute priority rule says senior claims get paid before junior claims. But in practice, several legal mechanisms can alter this order:

### Equitable Subordination

Courts can subordinate a claim on equitable grounds, pushing a senior claim to the back of the line. Three requirements (from the Mobile Steel test):
1. The creditor engaged in inequitable conduct
2. That conduct injured other creditors or conferred an unfair advantage
3. Subordination is consistent with the bankruptcy code

**When to worry:** Rarely applied to arms-length creditors. Most commonly applies to insider lenders or lenders who exercised control over the debtor. If a bank amended loan terms repeatedly while extracting collateral or fees that disadvantaged other creditors, its claim may be subordinated.

### Substantive Consolidation

Courts can pool the assets and liabilities of separate legal entities as if merged, eliminating structural seniority advantages that creditors of a particular subsidiary may hold.

**Red flags that increase consolidation risk:**
- Extensive intercompany loans and guarantees
- Shared bank accounts and cash management
- Creditors who relied on the consolidated group's credit, not a specific entity
- Commingled operations with no meaningful entity-level separation

**Impact on investors:** If a subsidiary's secured creditors benefit from that subsidiary's assets being ring-fenced, substantive consolidation destroys that advantage by pooling everything together. This is a material risk when evaluating holdco vs opco bond investments.

### Fraudulent Conveyance

Any transfer within 2 years before bankruptcy can be voided if the debtor received less than reasonably equivalent value AND was insolvent, undercapitalized, or unable to pay debts as they came due.

**Highest risk:** Upstream guarantees (subsidiary guarantees parent debt) and cross-stream guarantees (affiliate guarantees another affiliate's debt). These are routinely challenged in Chapter 11 because the guaranteeing entity received no direct consideration.

**Implication for guarantee analysis:** When evaluating guarantee coverage on bonds, consider that guarantees could be voided as fraudulent conveyances if the guarantor was insolvent when the guarantee was issued. Guarantees from thinly-capitalized subsidiaries are less reliable than guarantees from well-capitalized ones.

### Critical Vendor Payments

Courts routinely approve paying pre-petition trade debt to "critical vendors" during first-day hearings. These payments give pre-petition trade claims effective priority over bonds and other unsecured claims. The cash comes from DIP financing or existing cash — either way, it reduces the pool available for general unsecured creditors.

## Valuation Risks

Valuation disputes arise at nearly every stage of a Chapter 11 case and directly affect creditor recoveries.

### Collateral Valuation

Two standards depending on the debtor's intent:
- **Continued use:** Replacement value (cost to obtain a like asset) — typically higher
- **Disposition/sale:** Liquidation value — typically lower

A secured creditor is only secured up to the value of its collateral. If the collateral is worth less than the claim, the deficiency becomes an unsecured claim. Collateral can deteriorate during the case through: use and consumption by the debtor, worsening economic conditions, or inadequate maintenance.

### Enterprise Valuation Conflicts

Different parties push for different enterprise values:
- **Junior classes** (equity, subordinated debt) want high valuations — makes them in-the-money
- **Senior classes** want conservative valuations — ensures plan feasibility and their own recovery
- **Control shareholders** fight contentiously for high valuations — may delay plan confirmation

**Investment implication:** Published enterprise valuations in disclosure statements reflect negotiated outcomes, not objective truth. The actual recovery for each class depends on where valuation uncertainty resolves.

## Reorganization Process Risks

### Blocking Positions

An impaired class accepts a Chapter 11 plan when at least one-half in number AND two-thirds in amount of claims actually voting approve. A holder of slightly more than one-third in dollar amount holds a blocking position — enough to prevent consensual approval.

**Strategic value:** A blocking position gives the holder significant leverage to negotiate better terms. The threat of blocking forces the plan proponent to address the holder's concerns or face a contested confirmation.

**Cram-down risk:** A plan can still be confirmed over objection through cram-down (Section 1129(b)) if at least one impaired class voted for the plan, there is no unfair discrimination, and the plan is "fair and equitable." This limits but does not eliminate the blocking position's leverage.

### Professional Cost Drag

Professional fees are administrative expenses with super-priority — they must be paid in cash before any distribution to creditors. They come directly out of the pool available to unsecured creditors.

**Key rules:**
- In small cases, professional costs can consume the entire estate, preventing any unsecured recovery
- Time in Chapter 11 is the enemy of unsecured creditors — every month burns more cash on professionals
- Prepackaged Chapter 11 plans within the exclusivity period preserve the most value for unsecured creditors
- In large cases, professional fees may not affect feasibility but increase DIP financing needs, which itself has super-priority

### DIP Financing Priority

Debtor-in-possession financing receives super-priority administrative expense status, and sometimes even priming liens that jump ahead of existing secured creditors. This is essential for the debtor's continued operations but dilutes existing creditors' positions.

## The Five Basic Truths

### Truth 1: Creditors' Right to Cash Payment
Outside of Chapter 11 or Chapter 7, no one can take away a corporate creditor's right to a money payment on schedule. The Trust Indenture Act (Section 316(b)) protects this right for publicly traded bonds. This means short-term maturities give a creditor de facto seniority outside bankruptcy — the company must pay cash or file.

### Truth 2: Chapter 11 Rules Influence All Restructurings
Whether a recapitalization occurs in-court or out-of-court, Chapter 11 rules — particularly absolute priority and the period of exclusivity — influence the outcome. A voluntary exchange offer can only succeed if creditors are shown meaningful downside from non-participation.

### Truth 3: Investment Value in Distress
In distress, investment value is the present worth of a future cash bailout from any source: company payments, sale to a market, or control/governance value. Non-dividend-paying common stock received in a reorganization has value only if it provides a market exit or elements of control.

### Truth 4: Restructurings Are Costly
Professional expenses are super-priority administrative claims paid in cash. Academic estimates of 3% of pre-filing assets understate the true impact because professional fees should be measured against the cash available for distribution, not total assets. The benefits of Chapter 11 (no interest on unsecured claims during the case, NOL preservation, automatic stay, DIP financing access) must be netted against these costs.

### Truth 5: Creditors Have Only Contractual Rights
Creditors get only what is spelled out in loan agreements and indentures. Residual rights (board duties of care and fair dealing) flow to equity owners of solvent companies. When a corporation enters the zone of insolvency, duties shift from protecting owners to protecting creditors. The credit analyst must focus on the actual legal documents, not on implied duties or management goodwill.

## Medici Tools

### `search_bonds` — Priority and Claims Analysis
- `seniority` — secured, senior unsecured, subordinated positions in the waterfall
- `security_type` — first lien, second lien, unsecured
- `has_guarantors` — filter for bonds with guarantee protection (but evaluate guarantee quality)
- `outstanding` — size of each claim class affects recovery arithmetic

### `get_guarantors` — Guarantee Quality Assessment
- Check which entities guarantee each bond
- Cross-reference guarantor financial health — guarantees from well-capitalized subs are more reliable
- Consider fraudulent conveyance risk on upstream/cross-stream guarantees
- Guarantor coverage percentage tells you how much of the enterprise stands behind the bond

### `get_corporate_structure` — Consolidation Risk Assessment
- `is_unrestricted` — entities outside the credit group (assets potentially beyond creditors' reach)
- Extensive intercompany relationships suggest higher substantive consolidation risk
- Separate entity-level debt suggests structural priority — but consolidation risk could eliminate this advantage
- `is_vie` — variable interest entities may be legally separate despite consolidation

### `search_documents` — Legal Document Analysis
- `section_type="indenture"` — covenant protections, guarantee provisions, collateral descriptions
- `section_type="credit_agreement"` — loan terms, security interests, intercreditor arrangements
- Search `query="fraudulent conveyance"` or `query="substantive consolidation"` — check for disclosed risks
- Search `query="critical vendor"` — assess whether trade claims may jump the queue

### `search_pricing` — Market's Distress Assessment
- Bonds below $60 = market pricing distressed recovery
- Large price gap between secured and unsecured = market pricing priority differences
- Yields above 15-20% = market pricing material default probability
- Compare recovery implied by bond price to your own valuation analysis
