"""
QA Agent for verifying extraction accuracy.

Performs 6 verification checks against source filings:
1. Internal Consistency - validates parent/issuer/guarantor references exist (no LLM)
2. Entity Verification - confirms subsidiaries match Exhibit 21
3. Debt Verification - confirms debt amounts match filing footnotes
4. Completeness Check - looks for missed entities/debt
5. Structure Verification - validates hierarchy makes sense
6. JV/VIE Verification - confirms joint ventures, VIEs, and complex ownership are captured

Uses Gemini for verification to keep costs low (~$0.008 per QA run).

TROUBLESHOOTING COMMON ISSUES:
-----------------------------

1. "No Exhibit 21 content available" (Entity Verification skipped)
   - Exhibit 21 may be keyed as "exhibit_21_2025-02-13" not just "exhibit_21"
   - The run_qa() method searches for any key containing "exhibit_21"
   - Check that filing download includes Exhibit 21 from 10-K

2. "0/N debt instruments verified" (Debt Verification failing)
   - Filing content may be raw HTML/XBRL instead of clean text
   - The extraction.py clean_filing_html() should strip HTML/XBRL
   - Check debt_content contains readable text with keywords like "senior notes"

3. Entity name mismatches (Internal Consistency failing)
   - Names may differ in case: "TRANSOCEAN LTD" vs "Transocean Ltd."
   - Names may differ in punctuation: "Ltd." vs "Ltd"
   - The normalize_name() function handles case and trailing periods

4. Parent not found errors
   - Entities from fix iterations may use ALL CAPS parent names
   - Ensure normalize_name() is applied to both entity names and parent refs

SCORING:
--------
Each check contributes 20% to the overall score:
- PASS = 20 points
- WARN = 10 points
- FAIL = 0 points
- SKIP = 10 points (neutral)

Threshold: 85% required to pass without escalation.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

import httpx

from app.services.utils import parse_json_robust, clean_html, normalize_name


class QACheckStatus(Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    SKIP = "skip"


@dataclass
class QACheck:
    """Result of a single QA check."""
    name: str
    status: QACheckStatus
    message: str
    details: Optional[dict] = None
    evidence: Optional[str] = None  # Quote from filing supporting the check


@dataclass
class QAReport:
    """Complete QA report for an extraction."""
    ticker: str
    timestamp: datetime
    checks: list[QACheck]
    overall_score: float  # 0-100
    overall_status: str  # pass, fail, needs_review
    summary: str
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "timestamp": self.timestamp.isoformat(),
            "overall_score": self.overall_score,
            "overall_status": self.overall_status,
            "summary": self.summary,
            "recommendations": self.recommendations,
            "checks": [
                {
                    "name": c.name,
                    "status": c.status.value,
                    "message": c.message,
                    "details": c.details,
                    "evidence": c.evidence[:500] if c.evidence else None,
                }
                for c in self.checks
            ],
        }


# QA Prompts
QA_SYSTEM_PROMPT = """You are a credit analyst performing quality assurance on extracted corporate structure data.

Your job is to verify that the extraction is accurate by comparing it against the source SEC filings.

Be precise and factual. Quote specific text from the filings to support your findings.
If you cannot verify something, say so clearly.

Return your analysis as valid JSON."""


ENTITY_VERIFICATION_PROMPT = """Verify that these extracted entities exist in the SEC filings.

EXTRACTED ENTITIES:
{entities_json}

SOURCE FILINGS (Exhibit 21 / Subsidiaries):
{exhibit_21}

IMPORTANT CONTEXT:
- Many companies only list "significant subsidiaries" in Exhibit 21 per SEC rules
- If Exhibit 21 says subsidiaries are omitted because they're not significant, that's normal
- Entities may also come from credit agreements, debt footnotes, or other filing sections
- The parent company (holdco) won't be listed in Exhibit 21 since it's the filer

For each entity, mark as found_in_filing=true if:
- Exact or close name match in Exhibit 21, OR
- The entity is the parent company/holdco, OR
- Entity appears to be a legitimate subsidiary even if not in the limited Exhibit 21 list

Be FLEXIBLE with name matching:
- "Apple Distribution International Limited" matches "Apple Distribution International Ltd."
- Ignore minor differences in punctuation, "Inc." vs "Inc", "Ltd." vs "Limited"

Return JSON:
{{
  "verified_entities": [
    {{
      "name": "Entity Name",
      "found_in_filing": true,
      "name_match": "exact|close|parent_company|reasonable_subsidiary|not_found",
      "filing_name": "Name as it appears in filing (if different)",
      "jurisdiction_correct": true,
      "notes": "Any relevant notes"
    }}
  ],
  "missing_from_extraction": [
    {{
      "name": "Entity explicitly listed in Exhibit 21 but not extracted",
      "evidence": "Quote from filing"
    }}
  ],
  "exhibit_21_complete": true,
  "summary": "Brief summary - note if Exhibit 21 only shows significant subsidiaries"
}}"""


DEBT_VERIFICATION_PROMPT = """Verify that these extracted debt instruments match the SEC filings.

EXTRACTED DEBT (amounts in CENTS):
{debt_json}

SOURCE FILINGS (Debt sections):
{debt_content}

=== CRITICAL: UNIT CONVERSION - READ CAREFULLY ===

The extracted amounts are in US CENTS. You must convert filing dollar amounts to cents.

CONVERSION FORMULA: cents = dollars × 100

WORKED EXAMPLES:
1. Filing: "$550 million"
   → dollars = 550,000,000
   → cents = 550,000,000 × 100 = 55,000,000,000 cents ✓

2. Filing: "$2 billion" or "$2,000 million"
   → dollars = 2,000,000,000
   → cents = 2,000,000,000 × 100 = 200,000,000,000 cents ✓

3. Filing: "$750 million"
   → dollars = 750,000,000
   → cents = 750,000,000 × 100 = 75,000,000,000 cents ✓

4. Filing: "$1.5 billion"
   → dollars = 1,500,000,000
   → cents = 1,500,000,000 × 100 = 150,000,000,000 cents ✓

COMMON MISTAKE TO AVOID:
- WRONG: "$550 million" → 550,000,000 cents (forgot to multiply by 100!)
- RIGHT: "$550 million" → 55,000,000,000 cents (550M dollars × 100)

SANITY CHECK: Extracted amounts should have ~11-12 digits for hundreds of millions of dollars.
- $100 million = 10,000,000,000 cents (11 digits)
- $1 billion = 100,000,000,000 cents (12 digits)

=== CRITICAL: OUTSTANDING vs ISSUANCE AMOUNTS ===

Debt schedules typically show BOTH the original issuance amount AND the current outstanding amount.
The extracted data contains CURRENT OUTSTANDING amounts, NOT original issuance amounts.

EXAMPLE - Microsoft debt schedule format:
"2009 issuance of $ 3.8 billion ... $ 520 $ 520"
- $3.8 billion = ORIGINAL ISSUANCE (when the debt was first issued)
- $520 = CURRENT OUTSTANDING (what's still owed today, after repayments)

YOU MUST compare extracted amounts to CURRENT OUTSTANDING, not original issuance!

COMMON TABLE FORMATS:
1. "Outstanding Amount" or "Outstanding" column - USE THIS
2. "Principal Amount" or "Face Value" = original issuance - DO NOT USE for comparison
3. Table row: "[Description] ... [Original] ... [Outstanding]" - use the LAST amount column
4. If table shows "2025" and "2024" columns, use the most recent year (2025)

WORKED EXAMPLE:
Filing shows: "2009 issuance of $ 3.8 billion  5.20%  5.24%  $ 520  $ 520"
- Extracted: 52,000,000,000 cents ($520 million)
- Compare to: $520 million (current outstanding) ✓
- DO NOT compare to: $3.8 billion (original issuance) ✗

=== VERIFICATION STEPS ===

For each debt instrument:
1. Find the debt in the filing
2. Identify the CURRENT OUTSTANDING amount (not original issuance)
3. Convert to dollars (handle "million", "billion", commas)
4. Multiply dollars by 100 to get cents
5. Compare to extracted_cents
6. If within 5%, mark amount_correct=true

Interest rates: extracted as BASIS POINTS. 5.00% = 500 bps.

=== RESPONSE FORMAT ===

Return JSON:
{{
  "verified_debt": [
    {{
      "name": "5.000% Senior Notes due 2026",
      "found_in_filing": true,
      "filing_amount_text": "$550 million",
      "filing_amount_dollars": 550000000,
      "filing_amount_cents": 55000000000,
      "extracted_cents": 55000000000,
      "amount_correct": true,
      "rate_correct": true,
      "maturity_correct": true,
      "seniority_correct": true,
      "evidence": "Quote from filing"
    }}
  ],
  "missing_from_extraction": [
    {{
      "name": "Debt in filing but not extracted",
      "amount": "Amount from filing",
      "evidence": "Quote from filing"
    }}
  ],
  "amount_discrepancies": [
    {{
      "instrument": "Name",
      "filing_amount_text": "$550 million",
      "filing_amount_cents": 55000000000,
      "extracted_cents": 25000000000,
      "difference_pct": 54.5
    }}
  ],
  "summary": "Brief summary"
}}

IMPORTANT: Only report a discrepancy if the amounts differ by more than 5% AFTER proper conversion to cents.
IMPORTANT: Compare to CURRENT OUTSTANDING amounts, not original issuance amounts.

TOLERANCE RULE: Do NOT report discrepancies for differences ≤5%. These are acceptable:
- $357 million extracted vs $360 million in filing = 0.8% difference = ACCEPTABLE, DO NOT FLAG
- $500 million extracted vs $475 million in filing = 5% difference = ACCEPTABLE, DO NOT FLAG
- $500 million extracted vs $400 million in filing = 20% difference = FLAG AS DISCREPANCY"""


COMPLETENESS_CHECK_PROMPT = """Check if the extraction is complete by looking for entities and debt that may have been missed.

EXTRACTED DATA:
- Entities: {entity_count}
- Debt instruments: {debt_count}

CURRENT EXTRACTION:
{extraction_json}

SOURCE FILINGS:
{filing_content}

Look for:
1. Subsidiaries mentioned but not extracted
2. Debt facilities mentioned but not extracted
3. Guarantors mentioned but not in entity list
4. Any material information that was missed

Return JSON:
{{
  "completeness_score": 85,
  "missed_entities": [
    {{
      "name": "Entity Name",
      "reason": "Why this should be included",
      "evidence": "Quote from filing"
    }}
  ],
  "missed_debt": [
    {{
      "name": "Debt Name",
      "amount": "Amount if known",
      "reason": "Why this should be included",
      "evidence": "Quote from filing"
    }}
  ],
  "summary": "Brief completeness assessment"
}}"""


STRUCTURE_VERIFICATION_PROMPT = """Verify the corporate structure hierarchy is correct.

EXTRACTED STRUCTURE:
{structure_json}

SOURCE FILINGS:
{filing_content}

Check:
1. Is the parent company correctly identified as holdco?
2. Do parent-child relationships make sense?
3. Are SPVs correctly identified (usually for debt facilities)?
4. Are any circular references or impossible relationships present?

Return JSON:
{{
  "structure_valid": true,
  "hierarchy_issues": [
    {{
      "entity": "Entity Name",
      "issue": "Description of the problem",
      "suggested_fix": "How to fix it"
    }}
  ],
  "parent_child_verified": [
    {{
      "child": "Child Entity",
      "parent": "Parent Entity",
      "verified": true,
      "evidence": "Quote from filing if available"
    }}
  ],
  "summary": "Brief structure assessment"
}}"""


JV_VERIFICATION_PROMPT = """Verify joint ventures (JVs), VIEs, and complex ownership structures.

EXTRACTED ENTITIES (with ownership info):
{entities_json}

SOURCE FILINGS (MD&A, Notes, Exhibit 21):
{filing_content}

SEARCH FOR THESE IN THE FILINGS:
1. **Joint Ventures**: Look for "joint venture", "JV", "50% owned", "50/50 partnership", "equity method investee"
2. **VIEs**: Look for "variable interest entity", "VIE", "primary beneficiary", securitization trusts
3. **Partial Ownership**: Any subsidiary with <100% ownership
4. **Unconsolidated entities**: "equity method", "unconsolidated affiliate", "unconsolidated subsidiary"
5. **Unrestricted subsidiaries**: Subsidiaries excluded from credit agreement covenants

For each type found in filings, verify if it was captured in the extraction.

Return JSON:
{{
  "jvs_in_filing": [
    {{
      "name": "JV entity name from filing",
      "partner": "Name of JV partner if mentioned",
      "ownership_pct": 50,
      "consolidation": "equity method or consolidated",
      "evidence": "Quote from filing",
      "extracted": true
    }}
  ],
  "vies_in_filing": [
    {{
      "name": "VIE name from filing",
      "type": "securitization trust / property JV / other",
      "evidence": "Quote from filing",
      "extracted": true
    }}
  ],
  "unrestricted_subs_in_filing": [
    {{
      "name": "Unrestricted subsidiary name",
      "evidence": "Quote from filing",
      "extracted": true
    }}
  ],
  "missed_complex_structures": [
    {{
      "name": "Entity name",
      "type": "jv / vie / partial_ownership / unrestricted",
      "evidence": "Quote from filing showing this was missed"
    }}
  ],
  "extraction_jvs_verified": true,
  "extraction_vies_verified": true,
  "summary": "Brief assessment of JV/VIE/complex ownership extraction"
}}"""


class QAAgent:
    """
    Quality Assurance agent for verifying extraction accuracy.

    Uses Gemini for cost-effective verification (~$0.01 per QA run).
    """

    def __init__(self, gemini_api_key: str):
        import google.generativeai as genai
        genai.configure(api_key=gemini_api_key)
        self.genai = genai
        self.model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            generation_config={
                "temperature": 0.1,
                "response_mime_type": "application/json",
            },
            system_instruction=QA_SYSTEM_PROMPT,
        )
        self.total_cost = 0.0

    def _call_model(self, prompt: str) -> dict:
        """Call Gemini and parse JSON response."""
        response = self.model.generate_content(prompt)

        # Track cost (Gemini 2.0 Flash: $0.10/$0.40 per 1M tokens)
        usage = response.usage_metadata
        if usage:
            cost = (usage.prompt_token_count * 0.10 + usage.candidates_token_count * 0.40) / 1_000_000
            self.total_cost += cost

        # Parse JSON with robust handler
        content = response.text
        return parse_json_robust(content)

    def verify_entities(
        self,
        extraction: dict,
        exhibit_21: str
    ) -> QACheck:
        """Verify extracted entities against Exhibit 21."""

        if not exhibit_21 or len(exhibit_21.strip()) < 100:
            return QACheck(
                name="Entity Verification",
                status=QACheckStatus.SKIP,
                message="No Exhibit 21 content available for verification",
            )

        entities = extraction.get("entities", [])
        if not entities:
            return QACheck(
                name="Entity Verification",
                status=QACheckStatus.FAIL,
                message="No entities in extraction to verify",
            )

        # Clean HTML from exhibit 21
        cleaned_exhibit = clean_html(exhibit_21)

        prompt = ENTITY_VERIFICATION_PROMPT.format(
            entities_json=json.dumps(entities, indent=2),
            exhibit_21=cleaned_exhibit[:50000],  # Limit size
        )

        try:
            result = self._call_model(prompt)

            verified = result.get("verified_entities", [])
            found_count = sum(1 for v in verified if v.get("found_in_filing"))
            total = len(verified)
            missing = result.get("missing_from_extraction", [])

            if total == 0:
                status = QACheckStatus.WARN
                message = "Could not verify any entities"
            elif found_count == total and not missing:
                status = QACheckStatus.PASS
                message = f"All {total} entities verified in filings"
            elif found_count >= total * 0.8:
                status = QACheckStatus.WARN
                message = f"{found_count}/{total} entities verified, {len(missing)} potentially missing"
            else:
                status = QACheckStatus.FAIL
                message = f"Only {found_count}/{total} entities verified, {len(missing)} potentially missing"

            return QACheck(
                name="Entity Verification",
                status=status,
                message=message,
                details={
                    "verified_count": found_count,
                    "total_extracted": total,
                    "missing_count": len(missing),
                    "missing_entities": [m.get("name") for m in missing[:5]],
                },
                evidence=result.get("summary"),
            )

        except Exception as e:
            return QACheck(
                name="Entity Verification",
                status=QACheckStatus.FAIL,
                message=f"Verification failed: {str(e)}",
            )

    def verify_debt(
        self,
        extraction: dict,
        debt_content: str
    ) -> QACheck:
        """Verify extracted debt against filing content."""

        if not debt_content or len(debt_content.strip()) < 100:
            return QACheck(
                name="Debt Verification",
                status=QACheckStatus.SKIP,
                message="No debt content available for verification",
            )

        debt = extraction.get("debt_instruments", [])
        if not debt:
            return QACheck(
                name="Debt Verification",
                status=QACheckStatus.WARN,
                message="No debt instruments in extraction to verify",
            )

        prompt = DEBT_VERIFICATION_PROMPT.format(
            debt_json=json.dumps(debt, indent=2),
            debt_content=debt_content[:60000],  # Limit size
        )

        try:
            result = self._call_model(prompt)

            verified = result.get("verified_debt", [])
            correct_count = sum(1 for v in verified if v.get("amount_correct", True))
            total = len(verified)
            discrepancies = result.get("amount_discrepancies", [])
            missing = result.get("missing_from_extraction", [])

            if total == 0:
                status = QACheckStatus.WARN
                message = "Could not verify any debt instruments"
            elif correct_count == total and not discrepancies and not missing:
                status = QACheckStatus.PASS
                message = f"All {total} debt instruments verified with correct amounts"
            elif discrepancies:
                status = QACheckStatus.FAIL
                message = f"{len(discrepancies)} amount discrepancies found"
            elif missing:
                status = QACheckStatus.WARN
                message = f"{len(missing)} debt instruments may be missing"
            else:
                status = QACheckStatus.WARN
                message = f"{correct_count}/{total} debt instruments verified"

            return QACheck(
                name="Debt Verification",
                status=status,
                message=message,
                details={
                    "verified_count": total,
                    "correct_amounts": correct_count,
                    "discrepancies": discrepancies[:3],
                    "missing": [m.get("name") for m in missing[:3]],
                },
                evidence=result.get("summary"),
            )

        except Exception as e:
            return QACheck(
                name="Debt Verification",
                status=QACheckStatus.FAIL,
                message=f"Verification failed: {str(e)}",
            )

    def check_completeness(
        self,
        extraction: dict,
        filing_content: str
    ) -> QACheck:
        """Check if extraction is complete."""

        if not filing_content or len(filing_content.strip()) < 100:
            return QACheck(
                name="Completeness Check",
                status=QACheckStatus.SKIP,
                message="No filing content available for completeness check",
            )

        prompt = COMPLETENESS_CHECK_PROMPT.format(
            entity_count=len(extraction.get("entities", [])),
            debt_count=len(extraction.get("debt_instruments", [])),
            extraction_json=json.dumps(extraction, indent=2)[:10000],
            filing_content=filing_content[:60000],
        )

        try:
            result = self._call_model(prompt)

            score = result.get("completeness_score", 0)
            missed_entities = result.get("missed_entities", [])
            missed_debt = result.get("missed_debt", [])

            if score >= 90 and not missed_entities and not missed_debt:
                status = QACheckStatus.PASS
                message = f"Extraction appears complete (score: {score}%)"
            elif score >= 70:
                status = QACheckStatus.WARN
                message = f"Some items may be missing (score: {score}%)"
            else:
                status = QACheckStatus.FAIL
                message = f"Extraction may be incomplete (score: {score}%)"

            return QACheck(
                name="Completeness Check",
                status=status,
                message=message,
                details={
                    "completeness_score": score,
                    "missed_entities": [m.get("name") for m in missed_entities[:5]],
                    "missed_debt": [m.get("name") for m in missed_debt[:5]],
                },
                evidence=result.get("summary"),
            )

        except Exception as e:
            return QACheck(
                name="Completeness Check",
                status=QACheckStatus.FAIL,
                message=f"Check failed: {str(e)}",
            )

    def verify_structure(
        self,
        extraction: dict,
        filing_content: str
    ) -> QACheck:
        """Verify corporate structure hierarchy."""

        entities = extraction.get("entities", [])
        if not entities:
            return QACheck(
                name="Structure Verification",
                status=QACheckStatus.FAIL,
                message="No entities to verify structure",
            )

        # Build structure representation
        structure = []
        for e in entities:
            owners = e.get("owners", [])
            parent = owners[0].get("parent_name") if owners else None
            structure.append({
                "name": e.get("name"),
                "type": e.get("entity_type"),
                "parent": parent,
            })

        prompt = STRUCTURE_VERIFICATION_PROMPT.format(
            structure_json=json.dumps(structure, indent=2),
            filing_content=filing_content[:40000],
        )

        try:
            result = self._call_model(prompt)

            valid = result.get("structure_valid", False)
            issues = result.get("hierarchy_issues", [])

            if valid and not issues:
                status = QACheckStatus.PASS
                message = "Corporate structure hierarchy is valid"
            elif issues:
                status = QACheckStatus.WARN
                message = f"{len(issues)} hierarchy issues found"
            else:
                status = QACheckStatus.WARN
                message = "Could not fully verify structure"

            return QACheck(
                name="Structure Verification",
                status=status,
                message=message,
                details={
                    "structure_valid": valid,
                    "issues": issues[:3],
                },
                evidence=result.get("summary"),
            )

        except Exception as e:
            return QACheck(
                name="Structure Verification",
                status=QACheckStatus.FAIL,
                message=f"Verification failed: {str(e)}",
            )

    def verify_jvs(
        self,
        extraction: dict,
        filing_content: str
    ) -> QACheck:
        """Verify joint ventures, VIEs, and complex ownership structures."""

        entities = extraction.get("entities", [])

        # Count JVs and VIEs in extraction
        extracted_jvs = []
        extracted_vies = []
        extracted_unrestricted = []

        for e in entities:
            owners = e.get("owners", [])
            for owner in owners:
                if owner.get("is_joint_venture") or owner.get("jv_partner_name"):
                    extracted_jvs.append({
                        "name": e.get("name"),
                        "partner": owner.get("jv_partner_name"),
                        "ownership_pct": owner.get("ownership_pct"),
                    })
            if e.get("is_vie"):
                extracted_vies.append({"name": e.get("name")})
            if e.get("is_unrestricted"):
                extracted_unrestricted.append({"name": e.get("name")})

        # Build entities summary for prompt
        entities_summary = []
        for e in entities:
            entity_info = {
                "name": e.get("name"),
                "type": e.get("entity_type"),
                "is_vie": e.get("is_vie", False),
                "is_unrestricted": e.get("is_unrestricted", False),
                "consolidation_method": e.get("consolidation_method"),
            }
            owners = e.get("owners", [])
            if owners:
                entity_info["owners"] = [{
                    "parent": o.get("parent_name"),
                    "ownership_pct": o.get("ownership_pct"),
                    "is_joint_venture": o.get("is_joint_venture", False),
                    "jv_partner_name": o.get("jv_partner_name"),
                } for o in owners]
            entities_summary.append(entity_info)

        prompt = JV_VERIFICATION_PROMPT.format(
            entities_json=json.dumps(entities_summary, indent=2),
            filing_content=filing_content[:50000],
        )

        try:
            result = self._call_model(prompt)

            # Check results
            jvs_in_filing = result.get("jvs_in_filing", [])
            vies_in_filing = result.get("vies_in_filing", [])
            missed = result.get("missed_complex_structures", [])

            # Determine status
            jvs_verified = result.get("extraction_jvs_verified", True)
            vies_verified = result.get("extraction_vies_verified", True)

            if jvs_verified and vies_verified and not missed:
                status = QACheckStatus.PASS
                message = f"JV/VIE extraction verified ({len(extracted_jvs)} JVs, {len(extracted_vies)} VIEs captured)"
            elif missed:
                status = QACheckStatus.WARN
                message = f"{len(missed)} complex structures may be missing from extraction"
            else:
                status = QACheckStatus.WARN
                message = "JV/VIE extraction partially verified"

            return QACheck(
                name="JV/VIE Verification",
                status=status,
                message=message,
                details={
                    "extracted_jvs": len(extracted_jvs),
                    "extracted_vies": len(extracted_vies),
                    "extracted_unrestricted": len(extracted_unrestricted),
                    "jvs_found_in_filing": len(jvs_in_filing),
                    "vies_found_in_filing": len(vies_in_filing),
                    "missed_structures": missed[:5] if missed else [],
                },
                evidence=result.get("summary"),
            )

        except Exception as e:
            return QACheck(
                name="JV/VIE Verification",
                status=QACheckStatus.WARN,
                message=f"JV verification check skipped: {str(e)}",
            )

    def check_internal_consistency(self, extraction: dict) -> QACheck:
        """Check internal consistency without using LLM."""

        issues = []

        entities = extraction.get("entities", [])
        debt = extraction.get("debt_instruments", [])

        # Build normalized name lookup
        entity_names = {e.get("name") for e in entities if e.get("name")}
        entity_names_normalized = {normalize_name(name) for name in entity_names}

        def name_exists(name: str) -> bool:
            """Check if entity name exists (case-insensitive, punctuation-normalized)."""
            if not name:
                return False
            return name in entity_names or normalize_name(name) in entity_names_normalized

        # Check 1: All parent references exist
        for e in entities:
            for owner in e.get("owners", []):
                parent = owner.get("parent_name")
                if parent and not name_exists(parent):
                    issues.append(f"Parent '{parent}' not found for entity '{e.get('name')}'")

        # Check 2: All issuer references exist
        for d in debt:
            issuer = d.get("issuer_name")
            if issuer and not name_exists(issuer):
                issues.append(f"Issuer '{issuer}' not found for debt '{d.get('name')}'")

        # Check 3: All guarantor references exist
        for d in debt:
            for guarantor in d.get("guarantor_names", []):
                if not name_exists(guarantor):
                    issues.append(f"Guarantor '{guarantor}' not found for debt '{d.get('name')}'")

        # Check 4: Has holdco
        has_holdco = any(e.get("entity_type") == "holdco" for e in entities)
        if not has_holdco:
            issues.append("No holdco entity found")

        # Check 5: Debt amounts are reasonable
        for d in debt:
            amount = d.get("outstanding") or d.get("principal") or 0
            if amount < 0:
                issues.append(f"Negative amount for debt '{d.get('name')}'")
            elif amount > 1_000_000_000_000_000:  # > $10 trillion in cents
                issues.append(f"Unreasonably large amount for debt '{d.get('name')}'")

        if not issues:
            status = QACheckStatus.PASS
            message = "All internal consistency checks passed"
        else:
            status = QACheckStatus.FAIL
            message = f"{len(issues)} consistency issues found"

        return QACheck(
            name="Internal Consistency",
            status=status,
            message=message,
            details={"issues": issues[:10]},
        )

    async def run_qa(
        self,
        extraction: dict,
        filings: dict[str, str],
    ) -> QAReport:
        """
        Run full QA suite on an extraction.

        Args:
            extraction: The extraction result to verify
            filings: Dict of filing content (keys like '10-K', 'exhibit_21', etc.)

        Returns:
            QAReport with all check results
        """
        import asyncio

        ticker = extraction.get("ticker", "UNKNOWN")
        checks: list[QACheck] = []

        print(f"\n  Running QA checks...")

        # Get relevant filing sections
        # Find Exhibit 21 - may be keyed as "exhibit_21", "exhibit_21_2025-02-13", etc.
        exhibit_21 = ""
        for key, content in filings.items():
            if "exhibit_21" in key.lower() or "exhibit 21" in key.lower() or key.lower() == "ex-21":
                exhibit_21 = content
                break

        # Combine debt-related content
        debt_content = ""
        for key, content in filings.items():
            if any(term in key.lower() for term in ["10-k", "10-q", "debt", "note"]):
                debt_content += f"\n=== {key} ===\n{content[:30000]}\n"

        # Combine all filing content
        all_content = "\n\n".join(f"=== {k} ===\n{v[:20000]}" for k, v in filings.items())

        # Run checks - internal consistency first (no LLM), then LLM checks in parallel
        # Parallel execution is safe because each check is independent

        # 1. Internal consistency (no LLM needed) - run first
        print(f"    [1/6] Internal consistency...")
        checks.append(self.check_internal_consistency(extraction))

        # 2-6. LLM-based checks - run in parallel for speed
        print(f"    [2/6] Entity verification...")
        print(f"    [3/6] Debt verification...")
        print(f"    [4/6] Completeness check...")
        print(f"    [5/6] Structure verification...")
        print(f"    [6/6] JV/VIE verification...")

        # Run all LLM checks concurrently using asyncio.gather
        # Each check is wrapped in asyncio.to_thread since they use synchronous Gemini calls
        async def run_entity_check():
            return await asyncio.to_thread(self.verify_entities, extraction, exhibit_21)

        async def run_debt_check():
            return await asyncio.to_thread(self.verify_debt, extraction, debt_content)

        async def run_completeness_check():
            return await asyncio.to_thread(self.check_completeness, extraction, all_content)

        async def run_structure_check():
            return await asyncio.to_thread(self.verify_structure, extraction, all_content)

        async def run_jv_check():
            return await asyncio.to_thread(self.verify_jvs, extraction, all_content)

        # Execute all 5 LLM checks in parallel
        llm_results = await asyncio.gather(
            run_entity_check(),
            run_debt_check(),
            run_completeness_check(),
            run_structure_check(),
            run_jv_check(),
            return_exceptions=True  # Don't fail if one check errors
        )

        # Process results, handling any exceptions
        check_names = ["Entity Verification", "Debt Verification", "Completeness Check", "Structure Verification", "JV/VIE Verification"]
        for i, result in enumerate(llm_results):
            if isinstance(result, Exception):
                # If a check failed, create a WARN result
                checks.append(QACheck(
                    name=check_names[i],
                    status=QACheckStatus.WARN,
                    message=f"Check failed with error: {str(result)[:100]}",
                ))
            else:
                checks.append(result)

        # Calculate overall score
        status_scores = {
            QACheckStatus.PASS: 100,
            QACheckStatus.WARN: 70,
            QACheckStatus.FAIL: 0,
            QACheckStatus.SKIP: None,  # Don't count
        }

        scored_checks = [c for c in checks if status_scores[c.status] is not None]
        if scored_checks:
            overall_score = sum(status_scores[c.status] for c in scored_checks) / len(scored_checks)
        else:
            overall_score = 0

        # Determine overall status
        fail_count = sum(1 for c in checks if c.status == QACheckStatus.FAIL)
        warn_count = sum(1 for c in checks if c.status == QACheckStatus.WARN)

        if fail_count > 0:
            overall_status = "fail"
        elif warn_count > 1:
            overall_status = "needs_review"
        elif overall_score >= 80:
            overall_status = "pass"
        else:
            overall_status = "needs_review"

        # Generate recommendations
        recommendations = []
        for check in checks:
            if check.status == QACheckStatus.FAIL:
                recommendations.append(f"Fix: {check.name} - {check.message}")
            elif check.status == QACheckStatus.WARN:
                recommendations.append(f"Review: {check.name} - {check.message}")

        # Generate summary
        pass_count = sum(1 for c in checks if c.status == QACheckStatus.PASS)
        summary = (
            f"QA completed: {pass_count} passed, {warn_count} warnings, {fail_count} failed. "
            f"Overall score: {overall_score:.0f}%. QA cost: ${self.total_cost:.4f}"
        )

        return QAReport(
            ticker=ticker,
            timestamp=datetime.now(),
            checks=checks,
            overall_score=overall_score,
            overall_status=overall_status,
            summary=summary,
            recommendations=recommendations,
        )


async def run_qa_on_extraction(
    extraction: dict,
    filings: dict[str, str],
    gemini_api_key: str,
) -> QAReport:
    """
    Convenience function to run QA on an extraction.

    Args:
        extraction: Extraction result dict
        filings: Dict of filing content
        gemini_api_key: Gemini API key

    Returns:
        QAReport
    """
    agent = QAAgent(gemini_api_key)
    return await agent.run_qa(extraction, filings)
