# Structural Subordination Risk

Debt at a holding company is structurally subordinated to debt at operating subsidiaries — even when both are labeled "senior unsecured." This is one of the most misunderstood and underappreciated risks in credit analysis.

## Why Holdco Debt Is Riskier

The mechanics are straightforward:

1. The operating company (opco) owns the revenue-generating assets
2. The holding company (holdco) sits above and its primary asset is its equity interest in the opco
3. In distress or liquidation, opco creditors have a **direct claim** on opco assets
4. Holdco creditors can only reach the **residual equity value** of the opco — whatever's left after all opco debts are paid

This means a "senior unsecured" note at the holdco is effectively junior to a "senior unsecured" note at the opco, despite both carrying the same seniority label. The subordination is structural (driven by legal entity hierarchy), not contractual.

**Example:**
- Opco assets: $1B
- Opco debt (senior unsecured): $800M → recovers $800M (100%) from opco assets
- Remaining opco value: $200M → this flows up to holdco as equity value
- Holdco debt (senior unsecured): $500M → recovers only $200M (40%)

The holdco debt gets a much lower recovery despite being the same seniority label, purely because of entity structure.

## Identifying Structural Subordination

Look for these patterns:

1. **Holdco issues debt, opco also has debt:** The classic pattern. Any debt at the opco sits structurally ahead of holdco debt.
2. **Multiple opcos with debt:** Even more complex — each opco's creditors have first claim on that opco's assets.
3. **Foreign subsidiaries with local debt:** Debt incurred by foreign subsidiaries may sit structurally ahead, especially if cross-border enforcement is difficult.
4. **JV debt:** Joint venture entities may have debt that has first claim on JV assets, reducing what flows to the parent.

**Red flags:**
- Company has both `has_holdco_debt` and `has_opco_debt` = structural subordination is present
- High `subordination_score` = significant portion of value may be trapped below holdco creditors
- Large amount of `debt_at_entity` at subsidiary levels vs holdco level

## Guarantees as Mitigation

Guarantees can reduce or eliminate structural subordination:

- If opco subsidiaries **guarantee** holdco debt, those guarantees give holdco creditors a direct claim against opco assets — they stand alongside opco creditors, not behind them
- **Full guarantees** from all material subsidiaries effectively eliminates structural subordination — holdco creditors can claim against the same assets as opco creditors
- **Partial guarantees** (only some subsidiaries guarantee) reduce but don't eliminate the risk — check what percentage of total assets sit in guarantor entities

**Guarantee quality assessment:**
- How many subsidiaries guarantee vs total? (guarantor_count vs entity_count)
- What percentage of revenue/assets sit in guarantor entities?
- Are the guarantees full and unconditional, or limited?
- Can guarantees be released (e.g., if a subsidiary is designated as unrestricted)?

## Unrestricted Subsidiaries

Unrestricted subsidiaries are entities that sit **outside the credit group.** Their assets are not available to creditors of the restricted group, and they are not subject to the covenants in the credit agreement.

**Why they matter:**
- Assets transferred to unrestricted subs are effectively removed from the creditor pool
- The company can use unrestricted subs to house valuable assets beyond creditors' reach
- Private equity sponsors sometimes use unrestricted subs for J.Crew-style asset stripping (moving IP or valuable assets to an unrestricted entity)

**Warning signs:**
- `is_unrestricted` flag on subsidiaries in the corporate structure
- `has_unrestricted_subs` flag on the company
- Growth in unrestricted sub activity over time (check `get_changes`)
- Large assets or revenue in unrestricted entities

## Variable Interest Entities (VIEs)

VIEs are consolidated in financial statements for accounting purposes but may not be available for creditors' claims:

- VIEs are legally separate entities — the parent may consolidate them under GAAP but doesn't necessarily own them or control their assets for bankruptcy purposes
- Creditors of the parent can't necessarily reach VIE assets
- Common in China-based companies (VIE structures used to comply with foreign ownership restrictions), but also in structured finance, joint ventures, and special purpose entities
- Check `is_vie` flag in the corporate structure

## Assessing Severity

Not all structural subordination is equally dangerous. Assess severity by:

1. **Asset distribution:** What percentage of total assets sit at the opco vs holdco level? If 95% of assets are at opco and there's significant opco debt, holdco creditors face severe structural subordination.

2. **Opco debt quantum:** How much opco debt sits ahead of holdco creditors? A small amount of opco debt is less concerning than a large amount.

3. **Guarantee coverage:** Do opco entities guarantee holdco debt? Strong guarantees mitigate the structural risk.

4. **Dividend restrictions:** Can opco freely upstream cash to holdco? Restrictions on intercompany distributions can trap cash at the opco level.

5. **Regulatory barriers:** For regulated industries (banks, insurance, utilities), regulators may restrict cash movement from operating subs to the holdco.

## Medici Tools

### `get_corporate_structure` — Primary Tool for Structural Analysis
- Pass `ticker` to get the full parent-subsidiary hierarchy
- `debt_at_entity` at each level reveals where debt sits in the structure
- `is_guarantor` flag shows which entities guarantee debt at other levels
- `is_vie` flag identifies variable interest entities
- `is_unrestricted` flag identifies entities outside the credit group
- Walk the tree: holdco at top, opcos/subsidiaries below; debt at each level = claims against that entity's assets

### `search_companies` — Risk Flags & Scores
- `has_structural_sub` — boolean indicating structural subordination is present
- `subordination_score` — numeric score quantifying the severity of structural subordination
- `subordination_risk` — risk assessment level
- `has_holdco_debt` / `has_opco_debt` — quickly identify if both entity levels carry debt
- `has_unrestricted_subs` — flag for unrestricted subsidiary presence
- `entity_count`, `guarantor_count` — gauge structural complexity and guarantee coverage

### `search_bonds` — Holdco vs Opco Issuances
- Filter by `issuer_type` ("holdco", "opco", "subsidiary") to separate debt by entity level
- Compare `outstanding` amounts at each level to assess relative exposure
- `seniority` and `security_type` within each entity level for complete priority picture
- `has_guarantors` filter to find bonds with guarantee protection

### `get_guarantors` — Guarantee Analysis
- Pass a specific `bond_id` (CUSIP) to see all guarantor entities for that bond
- Cross-reference guarantor list with the corporate structure to assess what percentage of assets the guarantors represent
- Compare guarantor coverage between holdco bonds (should have opco guarantees) and opco bonds (inherently have direct asset access)

### `search_documents` — Supporting Detail
- `section_type="guarantor_list"` — find the full guarantor list from SEC filings
- `section_type="exhibit_21"` — subsidiary listing that maps the full entity structure
- Search `query="unrestricted subsidiary"` — find unrestricted sub designations and limitations
- Search `query="structural subordination"` — find issuer's own disclosure of the risk (often in risk factors)
