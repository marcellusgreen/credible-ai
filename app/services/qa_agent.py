"""
QA Agent for verifying extraction accuracy.

Performs 5 verification checks against source filings:
1. Internal Consistency - validates parent/issuer/guarantor references exist (no LLM)
2. Entity Verification - confirms subsidiaries match Exhibit 21
3. Debt Verification - confirms debt amounts match filing footnotes
4. Completeness Check - looks for missed entities/debt
5. Structure Verification - validates hierarchy makes sense

Uses Gemini for verification to keep costs low (~$0.006 per QA run).

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
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

import httpx


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

EXTRACTED DEBT:
{debt_json}

SOURCE FILINGS (Debt sections):
{debt_content}

For each debt instrument, verify:
1. Does the instrument exist in the filings?
2. Is the outstanding/principal amount correct? (Remember: extracted amounts are in CENTS)
3. Is the interest rate correct? (Remember: extracted rates are in BASIS POINTS)
4. Is the maturity date correct?
5. Is the seniority/security type correct?

IMPORTANT: Amounts in extraction are in CENTS. $1 billion = 100,000,000,000 cents.
Interest rates are in BASIS POINTS. 5.00% = 500 bps.

Return JSON:
{{
  "verified_debt": [
    {{
      "name": "Instrument Name",
      "found_in_filing": true,
      "amount_correct": true,
      "extracted_amount_cents": 100000000000,
      "filing_amount_dollars": "1,000,000,000",
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
      "extracted_cents": 100000000000,
      "expected_cents": 150000000000,
      "difference_pct": 50
    }}
  ],
  "summary": "Brief summary of debt verification"
}}"""


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


def clean_html(content: str) -> str:
    """Strip HTML tags and clean up whitespace."""
    # Remove HTML tags
    content = re.sub(r'<[^>]+>', ' ', content)
    # Remove HTML entities
    content = re.sub(r'&nbsp;', ' ', content)
    content = re.sub(r'&amp;', '&', content)
    content = re.sub(r'&lt;', '<', content)
    content = re.sub(r'&gt;', '>', content)
    # Clean up whitespace
    content = re.sub(r'\s+', ' ', content)
    return content.strip()


def parse_json_robust(content: str) -> dict:
    """
    Robustly parse JSON from LLM response, handling common issues:
    - Markdown code blocks
    - Trailing commas
    - Single quotes
    - Unquoted keys
    - Comments
    - Truncated JSON
    """
    def ensure_dict(result):
        """Ensure result is a dict, unwrap if it's a list with one dict."""
        if isinstance(result, dict):
            return result
        if isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict):
            return result[0]
        if isinstance(result, list) and len(result) > 0 and isinstance(result[0], dict):
            return result[0]
        raise ValueError(f"Expected dict but got {type(result)}: {str(result)[:200]}")

    # Try direct parse first
    try:
        result = json.loads(content)
        return ensure_dict(result)
    except json.JSONDecodeError:
        pass

    # Try extracting from code block
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', content)
    if json_match:
        try:
            result = json.loads(json_match.group(1))
            return ensure_dict(result)
        except json.JSONDecodeError:
            content = json_match.group(1)

    # Try finding JSON object
    json_match = re.search(r'\{[\s\S]*\}', content)
    if json_match:
        content = json_match.group(0)

    # Clean up common JSON issues
    cleaned = content

    # Remove JavaScript-style comments
    cleaned = re.sub(r'//.*?(?=\n|$)', '', cleaned)
    cleaned = re.sub(r'/\*[\s\S]*?\*/', '', cleaned)

    # Remove trailing commas before } or ]
    cleaned = re.sub(r',(\s*[}\]])', r'\1', cleaned)

    # Try parsing cleaned content
    try:
        result = json.loads(cleaned)
        return ensure_dict(result)
    except json.JSONDecodeError:
        pass

    # Try fixing unquoted keys
    cleaned2 = re.sub(r'(?<=[{,\s])(\w+)(?=\s*:)', r'"\1"', cleaned)
    try:
        result = json.loads(cleaned2)
        return ensure_dict(result)
    except json.JSONDecodeError:
        pass

    # Try replacing single quotes with double quotes
    cleaned3 = cleaned.replace("'", '"')
    try:
        result = json.loads(cleaned3)
        return ensure_dict(result)
    except json.JSONDecodeError:
        pass

    # Last resort: try to fix truncated JSON by closing brackets
    open_braces = cleaned.count('{') - cleaned.count('}')
    open_brackets = cleaned.count('[') - cleaned.count(']')

    if open_braces > 0 or open_brackets > 0:
        fixed = cleaned.rstrip().rstrip(',')
        fixed += ']' * open_brackets
        fixed += '}' * open_braces
        try:
            result = json.loads(fixed)
            return ensure_dict(result)
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse JSON from response: {content[:1000]}")


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
            model_name="gemini-2.0-flash-exp",
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

    def check_internal_consistency(self, extraction: dict) -> QACheck:
        """Check internal consistency without using LLM."""

        issues = []

        entities = extraction.get("entities", [])
        debt = extraction.get("debt_instruments", [])

        def normalize_name(name: str) -> str:
            """Normalize entity name for matching (case-insensitive, ignore trailing punctuation)."""
            if not name:
                return ""
            # Lowercase and strip whitespace
            normalized = name.lower().strip()
            # Remove trailing periods (Ltd. vs Ltd)
            normalized = normalized.rstrip('.')
            return normalized

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

        # Run checks (sequentially with delays to avoid Gemini rate limits)
        # Gemini free tier: 10 requests per minute per model
        # Add 7 second delay between LLM calls to stay under limit

        # 1. Internal consistency (no LLM needed)
        print(f"    [1/5] Internal consistency...")
        checks.append(self.check_internal_consistency(extraction))

        # 2. Entity verification (uses LLM)
        print(f"    [2/5] Entity verification...")
        checks.append(self.verify_entities(extraction, exhibit_21))
        await asyncio.sleep(7)  # Rate limit delay

        # 3. Debt verification (uses LLM)
        print(f"    [3/5] Debt verification...")
        checks.append(self.verify_debt(extraction, debt_content))
        await asyncio.sleep(7)  # Rate limit delay

        # 4. Completeness check (uses LLM)
        print(f"    [4/5] Completeness check...")
        checks.append(self.check_completeness(extraction, all_content))
        await asyncio.sleep(7)  # Rate limit delay

        # 5. Structure verification (uses LLM)
        print(f"    [5/5] Structure verification...")
        checks.append(self.verify_structure(extraction, all_content))

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
