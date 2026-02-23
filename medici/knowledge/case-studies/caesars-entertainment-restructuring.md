# Case Study: Caesars Entertainment — Asset Stripping, Creditor Wars, and the OpCo/PropCo Split

The Caesars Entertainment bankruptcy is the defining case study for structural subordination, fraudulent conveyance, and the tension between sponsor interests and creditor rights. It demonstrates nearly every risk from the distress investing risks framework in a single case.

## The Leveraged Buyout (2008)

Apollo Global Management and TPG Capital acquired Caesars Entertainment (then Harrah's Entertainment) in January 2008 for approximately $30.7 billion — on the eve of the global financial crisis. Apollo invested $1.7 billion in equity.

**Post-LBO capital structure:**
- ~$24 billion in total debt at close
- Debt loaded primarily at Caesars Entertainment Operating Company (CEOC), the operating subsidiary
- The parent, Caesars Entertainment Corporation (CEC), sat above CEOC in the corporate structure
- Annual interest expense: ~$1.7 billion

**Timing was catastrophic:** The LBO closed just as the 2008 financial crisis began, devastating the Las Vegas and Atlantic City gaming markets. Revenue declined sharply while the massive debt load remained.

## The Asset Transfer Controversy (2010–2014)

This is where the Caesars case becomes a textbook for the distress investing risks framework.

**What Apollo allegedly did:** Over several years, Apollo and TPG orchestrated a series of transactions that moved valuable assets out of CEOC (where creditors had claims) and into CEC-controlled entities (where creditors did not). These included:

- Transferring ownership interests in several casino properties from CEOC to CEC affiliates
- Moving the online gaming business (a growth asset) outside CEOC's reach
- Selling properties at allegedly below-market valuations to related entities
- Guaranteeing new CEC-level debt with CEOC assets

**Fraudulent conveyance framework application:** These transfers are the exact scenario described in the risks framework — transfers within 2 years of bankruptcy where the debtor received less than reasonably equivalent value while insolvent. CEOC creditors alleged that CEOC was insolvent when these transfers occurred, that CEOC received inadequate consideration, and that the transfers were designed to benefit the sponsors at creditors' expense.

**Equitable subordination risk:** Creditors argued that Apollo and TPG, as controlling shareholders, exercised control over CEOC in a way that harmed creditors. Under the Mobile Steel test from the risks framework, a controlling party that engages in inequitable conduct that injures creditors can have its claims subordinated.

## The Chapter 11 Filing (January 2015)

CEOC filed for Chapter 11 in Chicago with $18.4 billion in debt. Eighteen casino properties were included — Caesars Palace Las Vegas, multiple Atlantic City casinos, Harrah's and Horseshoe branded properties.

**Debt structure at filing:**
- First-lien secured debt (highest priority)
- Second-lien secured debt
- Unsecured notes (including subordinated)
- Massive unsecured claims from the alleged fraudulent transfers

**The creditor battle lines:**
- ~80% of first-lien noteholders supported the restructuring plan
- Junior creditor hedge funds (collectively owed $41 million) opposed the deal
- Distressed debt investors (including Elliott Management and Appaloosa Management) had accumulated large positions and demanded a full investigation of the asset transfers

## The Restructuring: OpCo/PropCo/REIT Split

The plan divided CEOC into two entities:
1. **OpCo** — the operating company running casino-hotels
2. **PropCo** — a newly formed publicly-traded REIT owning real property assets

**This is resource conversion (Mode 2) in action.** The company was worth more split into operating and real estate components than as a consolidated entity. The REIT structure unlocked tax-advantaged real estate value that was embedded in the consolidated enterprise.

**Restructuring economics:**
- Total debt reduced from $18.4 billion to $8.6 billion (~$10 billion reduction)
- Annual interest expense reduced from $1.7 billion to ~$450 million (75% decrease)
- Apollo and TPG received full reimbursement of their equity
- CEOC emerged from bankruptcy in October 2017

## Recovery Analysis

**Recovery by class:**
| Class | Recovery | Notes |
|-------|----------|-------|
| First-lien secured | Near par | Fully covered by collateral value |
| Second-lien secured | Substantial | Benefited from enterprise value exceeding first-lien claims |
| Unsecured creditors | Partial | Received mix of cash, new debt, and equity |
| Junior creditors | Minimal to zero | Half of other creditors faced complete claim elimination |
| Equity (Apollo/TPG) | Full reimbursement | Controversial — sponsors recovered despite creditor losses |

**Key controversy:** The sponsors recovering their equity while unsecured creditors took losses violated the spirit (if not the letter) of absolute priority. The settlement reflected the litigation risk — Apollo agreed to contribute assets and cash to settle the fraudulent transfer claims rather than face a trial.

## Lessons for Credit Investors

### 1. Structural Subordination Is Real
CEOC creditors had claims against the operating subsidiary. Assets moved to the parent or affiliates were beyond their reach. The corporate structure framework's warning about holdco vs. opco debt proved critical. Always check `get_corporate_structure` to understand where your claims sit.

### 2. Fraudulent Conveyance Is a Live Risk in Sponsor-Backed Deals
When a PE sponsor controls a distressed company, watch for: asset sales to affiliates at below-market prices, guarantees benefiting the parent at the subsidiary's expense, and value transfers that leave the operating entity undercapitalized. These are the exact fraudulent conveyance red flags from the risks framework.

### 3. Blocking Positions Drive Outcomes
Distressed debt investors who accumulated blocking positions (one-third+ of a class) had leverage to force investigations into the asset transfers and negotiate better terms. A $41 million position could block a multi-billion-dollar restructuring plan. The blocking position dynamic from the risks framework was the primary negotiating tool.

### 4. Resource Conversion Can Unlock Significant Value
The OpCo/PropCo/REIT split created value that didn't exist in the consolidated entity. For gaming, hospitality, and real estate companies, always assess whether a sum-of-parts valuation (Mode 2) exceeds going-concern value (Mode 1).

### 5. Professional Costs Were Enormous
The case ran from January 2015 to October 2017 — nearly three years. Professional fees consumed hundreds of millions of dollars, consistent with Truth #4 from the risks framework: restructurings are costly, and time in Chapter 11 is the enemy of unsecured creditors.

### 6. Sponsor Behavior Matters for Unsecured Recovery
The settlement between Apollo and the unsecured creditor committee was driven by the fraudulent conveyance claims. Without the threat of litigation over the asset transfers, unsecured recoveries would have been lower. Credit analysts evaluating sponsor-backed distressed debt should always assess whether sponsor conduct creates potential clawback claims.

## Medici Tools Application

When analyzing a potential Caesars-like scenario:

- **`get_corporate_structure`** — Map holdco vs. opco debt; identify `is_unrestricted` entities where assets may be beyond creditors' reach
- **`search_bonds`** — Compare `issuer_type` (holdco vs. opco) and `seniority` levels; look for structural subordination
- **`get_guarantors`** — Check which entities guarantee each bond; assess upstream/cross-stream guarantee risk
- **`search_documents`** — Search `query="related party transaction"` or `query="affiliate"` to identify potential asset transfers; check `section_type="risk_factors"` for disclosed litigation
- **`search_pricing`** — Large price gaps between secured and unsecured tranches indicate the market is pricing structural subordination
