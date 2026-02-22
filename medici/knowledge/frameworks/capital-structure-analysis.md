# Capital Structure Priority & Claims Analysis

Understand who gets paid first, how much debt sits ahead of you, and what's left for junior creditors. The capital structure is a waterfall — in distress, priority determines recovery.

## Priority of Claims

When a company can't pay all its debts, claims are satisfied in strict priority order. Think of it as a stack from top (safest) to bottom (riskiest):

1. **Secured debt** (first lien → second lien) — paid first from collateral proceeds; any deficiency becomes an unsecured claim
2. **Administrative claims** — DIP financing, professional fees in bankruptcy
3. **Senior unsecured debt** — pari passu among holders at the same level; no collateral backing
4. **Subordinated debt** — contractually agrees to be paid after senior unsecured
5. **Equity** — residual claim; wiped out unless all debt is repaid in full

**Key principle:** Seniority labels on the bond matter less than actual lien position and structural priority. A "senior unsecured" note at a holding company can be effectively junior to "senior unsecured" notes at an operating subsidiary (see structural subordination).

## Secured vs Unsecured Debt

- **Secured debt** has a lien on specific collateral (assets, receivables, equity pledges). In default, secured creditors can seize collateral or get paid first from its sale.
- **First lien** has priority over **second lien** on the same collateral pool.
- Unsecured creditors share pro-rata in whatever enterprise value remains after secured claims are satisfied.
- A secured creditor with a deficiency claim (collateral worth less than their claim) becomes an unsecured creditor for the shortfall.

## Holdco vs Opco Debt

One of the most critical distinctions in capital structure analysis:

- **Operating company (opco) debt** is issued by the entity that owns the revenue-generating assets. Opco creditors have a direct claim on those assets.
- **Holding company (holdco) debt** is issued by the parent. The parent's only asset is typically its equity interest in the opco.
- In distress, opco creditors get paid from opco assets first. Holdco creditors only receive what's left — the residual equity value of the opco after opco debts are satisfied.
- This means holdco senior unsecured debt is structurally subordinated to opco senior unsecured debt, even though both carry the "senior unsecured" label.

## Guarantor Analysis

Guarantees can bridge the holdco/opco gap:

- If operating subsidiaries **guarantee** holdco debt, those guarantees give holdco creditors a direct claim against opco assets (alongside opco creditors).
- Guarantees effectively elevate holdco debt in the priority waterfall — but only if the guaranteeing entities have real assets and the guarantees are enforceable.
- Watch for **limited guarantees** or guarantees that can be released under certain conditions (e.g., a ratings-based release).
- Guarantor coverage ratio matters: if 90% of assets sit in guarantor entities, the guarantee is meaningful; if only 20% do, it's weak protection.

## Reading the Capital Structure Stack

When analyzing a company's capital structure, build the stack from most senior to most junior:

1. Pull all debt instruments and sort by seniority
2. Note the outstanding amount at each level — this tells you total claims at each priority
3. Identify which entity issued each instrument (holdco vs opco)
4. Check which entities guarantee each instrument
5. Compare total claims to estimated enterprise value — where the value "breaks" in the stack is the fulcrum security

## Medici Tools

### `search_bonds` — Build the Debt Stack
- Filter by `ticker` to get all bonds for a company
- Use `seniority` filter ("senior_secured", "senior_unsecured", "subordinated") to segment by priority
- Sort by seniority to see the stack in order: `sort=seniority`
- `issuer_type` filter ("holdco", "opco", "subsidiary") separates debt by entity level
- `security_type` ("first_lien", "second_lien", "unsecured") shows lien position
- `outstanding` field shows the dollar amount at each level of the stack

### `get_corporate_structure` — Entity Hierarchy & Debt Location
- Pass `ticker` to get the full parent/subsidiary tree
- Shows `debt_at_entity` for each entity — reveals where debt actually sits
- `is_guarantor` flag identifies which entities back debt at other levels
- Essential for identifying holdco vs opco debt distribution

### `get_guarantors` — Guarantee Chain for a Bond
- Pass a `bond_id` (CUSIP) to see which entities guarantee that specific bond
- Cross-reference with corporate structure to assess guarantee quality
- Compare guarantor entity assets to the bond's outstanding amount

### `search_companies` — Quick Risk Flags
- `has_structural_sub` — boolean flag indicating structural subordination exists
- `has_holdco_debt` / `has_opco_debt` — quickly see if both levels carry debt
- `total_debt`, `secured_debt`, `unsecured_debt` — aggregate capital structure summary
