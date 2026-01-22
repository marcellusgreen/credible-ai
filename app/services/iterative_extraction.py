"""
Iterative Extraction Service with QA Feedback Loop.

Optimizes for speed, accuracy, and cost by:
1. Starting with cheap Tier 1 extraction
2. Running targeted QA checks
3. Only escalating/re-extracting sections that failed QA
4. Caching successful extractions to avoid re-work
5. Using targeted prompts to fix specific issues

Flow:
┌─────────────────────────────────────────────────────────────┐
│ Initial Extraction (Gemini ~$0.01)                          │
└─────────────────────┬───────────────────────────────────────┘
                      ▼
┌─────────────────────────────────────────────────────────────┐
│ Quick QA Checks (~$0.005)                                   │
│ - Internal consistency (free)                               │
│ - Entity spot check                                         │
│ - Debt spot check                                           │
└─────────────────────┬───────────────────────────────────────┘
                      ▼
              ┌───────────────┐
              │ Score >= 90%? │──Yes──► Done! (~$0.015 total)
              └───────┬───────┘
                      │ No
                      ▼
┌─────────────────────────────────────────────────────────────┐
│ Targeted Fix (Gemini ~$0.005 per issue)                     │
│ - Fix specific issues identified by QA                      │
│ - Merge fixes into extraction                               │
└─────────────────────┬───────────────────────────────────────┘
                      ▼
              ┌───────────────┐
              │ Iteration < 3?│──No───► Escalate to Claude
              └───────┬───────┘
                      │ Yes
                      ▼
              [Loop back to QA]

Max cost for simple company: ~$0.03
Max cost for complex company: ~$0.20 (with Claude escalation)
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from app.services.tiered_extraction import (
    TieredExtractionService,
    GeminiClient,
    ClaudeClient,
    ModelTier,
    calculate_cost,
)
from app.services.utils import parse_json_robust
from app.services.qa_agent import QAAgent, QAReport, QACheck, QACheckStatus


class IterationAction(Enum):
    ACCEPT = "accept"
    FIX_ENTITIES = "fix_entities"
    FIX_DEBT = "fix_debt"
    FIX_STRUCTURE = "fix_structure"
    ESCALATE = "escalate"


@dataclass
class IterationResult:
    """Result of a single iteration."""
    iteration: int
    action: IterationAction
    extraction: dict
    qa_score: float
    issues_fixed: list[str]
    cost: float
    duration_seconds: float


@dataclass
class IterativeExtractionResult:
    """Final result of iterative extraction."""
    ticker: str
    extraction: dict
    final_qa_score: float
    iterations: list[IterationResult]
    total_cost: float
    total_duration: float
    final_model: str
    qa_report: QAReport


# Targeted fix prompts
ENTITY_FIX_PROMPT = """The following entities were extracted but have issues. Please fix them.

CURRENT EXTRACTION:
{current_entities}

ISSUES FOUND:
{issues}

EXHIBIT 21 (Subsidiary List):
{exhibit_21}

Please provide ONLY the corrected/additional entities as a JSON array.
Match entity names EXACTLY as they appear in Exhibit 21.
Include any missing entities from the list.

Return JSON:
{{
  "fixed_entities": [
    {{
      "name": "Exact legal name from Exhibit 21",
      "entity_type": "subsidiary|holdco|finco|spv|jv|vie",
      "jurisdiction": "State/Country from Exhibit 21",
      "owners": [{{"parent_name": "Parent Name", "ownership_pct": 100}}],
      "is_material": true
    }}
  ],
  "entities_to_remove": ["Entity names that should be removed"],
  "explanation": "Brief explanation of changes"
}}"""


DEBT_FIX_PROMPT = """The following debt instruments were extracted but have issues. Please fix them.

CURRENT EXTRACTION:
{current_debt}

ISSUES FOUND:
{issues}

FILING CONTENT (Debt sections):
{debt_content}

Please provide ONLY the corrected/additional debt instruments as a JSON array.
Remember: amounts in CENTS ($1B = 100000000000), rates in BASIS POINTS (5% = 500).

Return JSON:
{{
  "fixed_debt": [
    {{
      "name": "Instrument name",
      "issuer_name": "Issuer entity name",
      "instrument_type": "term_loan|revolver|senior_notes|commercial_paper|etc",
      "seniority": "senior_secured|senior_unsecured|subordinated",
      "security_type": "first_lien|second_lien|unsecured",
      "principal": 100000000000,
      "outstanding": 100000000000,
      "rate_type": "fixed|floating",
      "interest_rate": 500,
      "maturity_date": "2029-01-15"
    }}
  ],
  "debt_to_update": [
    {{
      "name": "Existing instrument to update",
      "field": "outstanding",
      "old_value": 100000000000,
      "new_value": 150000000000
    }}
  ],
  "explanation": "Brief explanation of changes"
}}"""


COMPLETENESS_FIX_PROMPT = """The extraction may be missing some items. Please identify and extract any missing entities or debt.

CURRENT EXTRACTION SUMMARY:
- Entities: {entity_count}
- Debt instruments: {debt_count}

POTENTIALLY MISSING:
{missing_items}

FILING CONTENT:
{filing_content}

Search for and extract any missing items. Return JSON:
{{
  "additional_entities": [
    {{
      "name": "Entity name",
      "entity_type": "subsidiary|holdco|finco|spv",
      "jurisdiction": "State/Country",
      "owners": [{{"parent_name": "Parent", "ownership_pct": 100}}]
    }}
  ],
  "additional_debt": [
    {{
      "name": "Instrument name",
      "issuer_name": "Issuer",
      "instrument_type": "type",
      "outstanding": 100000000000
    }}
  ],
  "explanation": "What was found and added"
}}"""


# Combined fix prompt - fixes ALL issues in one call for speed
COMBINED_FIX_PROMPT = """Fix ALL the issues found in this extraction. Address entities, debt, and completeness in ONE response.

CURRENT EXTRACTION:
- Entities ({entity_count}): {current_entities}
- Debt ({debt_count}): {current_debt}

ALL ISSUES FOUND:
{all_issues}

REFERENCE DOCUMENTS:
=== Exhibit 21 (Subsidiaries) ===
{exhibit_21}

=== Debt Sections from Filings ===
{debt_content}

FIX EVERYTHING in one response. Return JSON:
{{
  "fixed_entities": [
    {{
      "name": "Entity name matching Exhibit 21 exactly",
      "entity_type": "subsidiary|holdco|finco|spv|jv|vie",
      "jurisdiction": "State/Country",
      "formation_type": "Corp|LLC|Ltd|LP",
      "owners": [{{"parent_name": "Parent", "ownership_pct": 100, "ownership_type": "direct"}}],
      "is_material": true,
      "is_domestic": true
    }}
  ],
  "entities_to_remove": ["Names of incorrect entities to remove"],
  "fixed_debt": [
    {{
      "name": "Debt instrument name",
      "issuer_name": "Issuer entity name",
      "instrument_type": "term_loan|revolver|senior_notes|subordinated_notes|commercial_paper",
      "seniority": "senior_secured|senior_unsecured|subordinated",
      "security_type": "first_lien|second_lien|unsecured",
      "principal": 100000000000,
      "outstanding": 100000000000,
      "rate_type": "fixed|floating",
      "interest_rate": 500,
      "maturity_date": "2029-01-15",
      "guarantor_names": []
    }}
  ],
  "debt_to_update": [
    {{"name": "Existing debt name", "field": "outstanding", "new_value": 150000000000}}
  ],
  "explanation": "Summary of all fixes made"
}}

IMPORTANT:
- Amounts in CENTS ($1B = 100000000000)
- Rates in BASIS POINTS (5% = 500)
- Match entity names EXACTLY as in Exhibit 21
- Only include items that need fixing, not the entire extraction"""


class IterativeExtractionService:
    """
    Iterative extraction with QA feedback loop.

    Optimizes for:
    - Speed: Parallel QA checks, targeted fixes only
    - Accuracy: Iterative improvement until quality threshold met
    - Cost: Start cheap, escalate only when needed
    """

    def __init__(
        self,
        gemini_api_key: str,
        anthropic_api_key: str,
        sec_api_key: Optional[str] = None,
        max_iterations: int = 3,
        quality_threshold: float = 85.0,
    ):
        self.gemini_api_key = gemini_api_key
        self.anthropic_api_key = anthropic_api_key
        self.sec_api_key = sec_api_key
        self.max_iterations = max_iterations
        self.quality_threshold = quality_threshold

        # Initialize clients
        self.gemini = GeminiClient(gemini_api_key) if gemini_api_key else None
        self.qa_agent = QAAgent(gemini_api_key) if gemini_api_key else None
        self.claude = ClaudeClient(anthropic_api_key) if anthropic_api_key else None

        self.total_cost = 0.0

    def _call_gemini_fix(self, prompt: str) -> dict:
        """Call Gemini for a targeted fix."""
        import asyncio

        response = self.gemini.model.generate_content(prompt)

        # Track cost
        usage = response.usage_metadata
        if usage:
            cost = (usage.prompt_token_count * 0.10 + usage.candidates_token_count * 0.40) / 1_000_000
            self.total_cost += cost

        return parse_json_robust(response.text)

    def _merge_entity_fixes(self, extraction: dict, fixes: dict) -> dict:
        """Merge entity fixes into extraction."""
        entities = extraction.get("entities", [])
        entity_names = {e.get("name") for e in entities}

        # Remove entities marked for removal
        to_remove = set(fixes.get("entities_to_remove", []))
        entities = [e for e in entities if e.get("name") not in to_remove]

        # Add/update fixed entities
        for fixed in fixes.get("fixed_entities", []):
            name = fixed.get("name")
            if name in entity_names:
                # Update existing
                for i, e in enumerate(entities):
                    if e.get("name") == name:
                        entities[i] = {**e, **fixed}
                        break
            else:
                # Add new
                entities.append(fixed)

        extraction["entities"] = entities
        return extraction

    def _merge_debt_fixes(self, extraction: dict, fixes: dict) -> dict:
        """Merge debt fixes into extraction."""
        debt = extraction.get("debt_instruments", [])
        debt_names = {d.get("name") for d in debt}

        # Update existing debt
        for update in fixes.get("debt_to_update", []):
            name = update.get("name")
            field = update.get("field")
            new_value = update.get("new_value")
            for d in debt:
                if d.get("name") == name and field:
                    d[field] = new_value

        # Add fixed/new debt
        for fixed in fixes.get("fixed_debt", []):
            name = fixed.get("name")
            if name in debt_names:
                # Update existing
                for i, d in enumerate(debt):
                    if d.get("name") == name:
                        debt[i] = {**d, **fixed}
                        break
            else:
                # Add new
                debt.append(fixed)

        extraction["debt_instruments"] = debt
        return extraction

    def _merge_completeness_fixes(self, extraction: dict, fixes: dict) -> dict:
        """Merge completeness fixes into extraction."""
        # Add additional entities
        entities = extraction.get("entities", [])
        entity_names = {e.get("name") for e in entities}
        for entity in fixes.get("additional_entities", []):
            if entity.get("name") not in entity_names:
                entities.append(entity)
        extraction["entities"] = entities

        # Add additional debt
        debt = extraction.get("debt_instruments", [])
        debt_names = {d.get("name") for d in debt}
        for d in fixes.get("additional_debt", []):
            if d.get("name") not in debt_names:
                debt.append(d)
        extraction["debt_instruments"] = debt

        return extraction

    def _merge_combined_fixes(self, extraction: dict, fixes: dict) -> dict:
        """Merge all fixes (entities, debt, completeness) from combined fix call."""
        # Apply entity fixes
        extraction = self._merge_entity_fixes(extraction, fixes)

        # Apply debt fixes
        extraction = self._merge_debt_fixes(extraction, fixes)

        # Apply any additional items from completeness
        if "additional_entities" in fixes:
            extraction = self._merge_completeness_fixes(extraction, fixes)

        return extraction

    def _collect_all_issues(self, qa_report: QAReport) -> str:
        """Collect all issues from QA report for combined fix prompt."""
        issues = []
        for check in qa_report.checks:
            if check.status in (QACheckStatus.FAIL, QACheckStatus.WARN):
                issue_text = f"[{check.status.value.upper()}] {check.name}: {check.message}"
                if check.details:
                    issue_text += f"\n  Details: {json.dumps(check.details)[:500]}"
                issues.append(issue_text)
        return "\n".join(issues) if issues else "No specific issues identified"

    def _determine_action(self, qa_report: QAReport) -> IterationAction:
        """Determine what action to take based on QA results."""
        if qa_report.overall_score >= self.quality_threshold:
            return IterationAction.ACCEPT

        # Check which areas need fixing
        for check in qa_report.checks:
            if check.status == QACheckStatus.FAIL:
                if "entity" in check.name.lower():
                    return IterationAction.FIX_ENTITIES
                elif "debt" in check.name.lower():
                    return IterationAction.FIX_DEBT
                elif "completeness" in check.name.lower():
                    return IterationAction.FIX_ENTITIES  # Start with entities

        # Check warnings
        for check in qa_report.checks:
            if check.status == QACheckStatus.WARN:
                if "entity" in check.name.lower():
                    return IterationAction.FIX_ENTITIES
                elif "debt" in check.name.lower():
                    return IterationAction.FIX_DEBT
                elif "completeness" in check.name.lower():
                    return IterationAction.FIX_ENTITIES

        return IterationAction.ACCEPT

    async def extract_with_feedback(
        self,
        ticker: str,
        cik: str,
        filings: dict[str, str],
    ) -> IterativeExtractionResult:
        """
        Run iterative extraction with QA feedback loop.

        Args:
            ticker: Stock ticker
            cik: SEC CIK number
            filings: Pre-downloaded filing content

        Returns:
            IterativeExtractionResult with final extraction and metrics
        """
        start_time = datetime.now()
        iterations: list[IterationResult] = []

        print(f"\n  Starting iterative extraction for {ticker}...")

        # Step 1: Initial extraction with Gemini
        print(f"\n  [Iteration 0] Initial extraction...")
        iter_start = datetime.now()

        # Combine filings for extraction
        # For large filings, extract debt-related sections to avoid missing debt info
        from app.services.tiered_extraction import EXTRACTION_PROMPT_TEMPLATE, SYSTEM_PROMPT, extract_debt_sections

        context_parts = []
        # Prioritize 10-K and Exhibit 21 for structure
        priority_keys = sorted(filings.keys(), key=lambda k: (
            0 if '10-K' in k else (1 if 'exhibit_21' in k else (2 if '10-Q' in k else 3))
        ))

        total_chars = 0
        max_total = 100000  # Limit total context to avoid output truncation

        for k in priority_keys:
            v = filings[k]
            if total_chars >= max_total:
                break

            if len(v) > 100000:  # Large filing - extract key sections
                # Get debt sections specifically
                debt_sections = extract_debt_sections(v, max_chars=30000)
                part = f"=== {k} (debt sections) ===\n{debt_sections}"
            elif 'exhibit_21' in k.lower():
                # Include full exhibit 21 (subsidiaries list)
                part = f"=== {k} ===\n{v[:50000]}"
            else:
                part = f"=== {k} ===\n{v[:20000]}"

            context_parts.append(part)
            total_chars += len(part)

        context = "\n\n".join(context_parts)

        prompt = EXTRACTION_PROMPT_TEMPLATE.format(context=context[:max_total])

        # Smart model selection: use Claude for complex companies (large Exhibit 21)
        # This avoids Gemini output truncation for companies with many subsidiaries
        exhibit_21_size = 0
        for key, content in filings.items():
            if "exhibit_21" in key.lower():
                exhibit_21_size = len(content)
                break

        # If Exhibit 21 is large (>30KB), company likely has many subsidiaries
        # Use Claude directly to avoid truncation issues
        use_claude_directly = exhibit_21_size > 30000

        initial_model = "gemini-2.0-flash"
        if use_claude_directly and self.claude:
            print(f"    Large company detected (Exhibit 21: {exhibit_21_size//1000}KB), using Claude...")
            initial_model = "claude-sonnet"
            extraction, tokens_in, tokens_out = await self.claude.extract_sonnet(context[:150000])
            initial_cost = calculate_cost(ModelTier.TIER2_SONNET, tokens_in, tokens_out)
        else:
            # Try Gemini first, fall back to Claude if rate limited or JSON truncated
            try:
                extraction, tokens_in, tokens_out = await self.gemini.extract(context[:150000])
                initial_cost = calculate_cost(ModelTier.TIER1_GEMINI, tokens_in, tokens_out)
            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "quota" in error_str.lower() or "ResourceExhausted" in error_str:
                    print(f"    Gemini rate limited, falling back to Claude...")
                elif "Could not parse JSON" in error_str or "truncat" in error_str.lower():
                    print(f"    Gemini output truncated, falling back to Claude...")
                else:
                    raise  # Re-raise other errors
                initial_model = "claude-sonnet"
                extraction, tokens_in, tokens_out = await self.claude.extract_sonnet(context[:150000])
                initial_cost = calculate_cost(ModelTier.TIER2_SONNET, tokens_in, tokens_out)
        self.total_cost += initial_cost

        print(f"    Extracted ({initial_model}): {len(extraction.get('entities', []))} entities, "
              f"{len(extraction.get('debt_instruments', []))} debt, cost: ${initial_cost:.4f}")

        # Step 2: QA check
        print(f"    Running QA...")
        qa_report = await self.qa_agent.run_qa(extraction, filings)
        self.total_cost += self.qa_agent.total_cost

        print(f"    QA Score: {qa_report.overall_score:.0f}%")

        iterations.append(IterationResult(
            iteration=0,
            action=IterationAction.ACCEPT if qa_report.overall_score >= self.quality_threshold else IterationAction.FIX_ENTITIES,
            extraction=extraction.copy(),
            qa_score=qa_report.overall_score,
            issues_fixed=[],
            cost=initial_cost,
            duration_seconds=(datetime.now() - iter_start).total_seconds(),
        ))

        # Step 3: Iterative improvement
        current_extraction = extraction
        current_qa = qa_report

        for i in range(1, self.max_iterations + 1):
            if current_qa.overall_score >= self.quality_threshold:
                print(f"\n  Quality threshold met ({current_qa.overall_score:.0f}% >= {self.quality_threshold}%)")
                break

            print(f"\n  [Iteration {i}] Fixing all issues in single call...")
            iter_start = datetime.now()
            iter_cost = 0.0
            issues_fixed = []

            # Collect ALL issues for combined fix
            all_issues = self._collect_all_issues(current_qa)

            # Get reference content
            exhibit_21 = ""
            for key in filings:
                if "exhibit_21" in key.lower():
                    exhibit_21 = filings[key][:30000]
                    break

            debt_content = "\n".join(
                f"=== {k} ===\n{v[:15000]}"
                for k, v in filings.items()
                if any(t in k.lower() for t in ["10-k", "10-q", "8-k"])
            )[:40000]

            # Build combined fix prompt
            prompt = COMBINED_FIX_PROMPT.format(
                entity_count=len(current_extraction.get("entities", [])),
                debt_count=len(current_extraction.get("debt_instruments", [])),
                current_entities=json.dumps(current_extraction.get("entities", [])[:20], indent=2)[:3000],
                current_debt=json.dumps(current_extraction.get("debt_instruments", [])[:15], indent=2)[:3000],
                all_issues=all_issues,
                exhibit_21=exhibit_21 if exhibit_21 else "Not available",
                debt_content=debt_content,
            )

            try:
                # Single LLM call to fix everything
                fixes = self._call_gemini_fix(prompt)
                current_extraction = self._merge_combined_fixes(current_extraction, fixes)
                issues_fixed.append(f"Combined fix: {fixes.get('explanation', 'applied')}")
                print(f"    Applied combined fixes")
            except Exception as e:
                print(f"    Combined fix failed: {e}")

            # Re-run QA
            print(f"    Re-running QA...")
            self.qa_agent.total_cost = 0  # Reset for this iteration
            current_qa = await self.qa_agent.run_qa(current_extraction, filings)
            iter_cost = self.qa_agent.total_cost
            self.total_cost += iter_cost

            print(f"    New QA Score: {current_qa.overall_score:.0f}% (was {iterations[-1].qa_score:.0f}%)")

            iterations.append(IterationResult(
                iteration=i,
                action=IterationAction.FIX_ENTITIES,  # Combined fix covers all
                extraction=current_extraction.copy(),
                qa_score=current_qa.overall_score,
                issues_fixed=issues_fixed,
                cost=iter_cost,
                duration_seconds=(datetime.now() - iter_start).total_seconds(),
            ))

            # Check if we're not making progress
            if i > 1 and current_qa.overall_score <= iterations[-2].qa_score:
                print(f"    No improvement, stopping iterations")
                break

        # Step 4: Escalate if still below threshold or no debt found
        # Escalation order: Gemini 2.5 Pro -> Claude Sonnet
        final_model = "gemini-2.0-flash"
        has_debt = len(current_extraction.get('debt_instruments', [])) > 0
        needs_escalation = (
            current_qa.overall_score < self.quality_threshold or
            (not has_debt and self.quality_threshold >= 80)  # Most companies should have debt
        )

        if needs_escalation and self.gemini:
            # First try Gemini 2.5 Pro (cheaper than Claude)
            escalation_reason = "no debt instruments found" if not has_debt else f"quality below {self.quality_threshold}%"
            print(f"\n  [Escalation] {escalation_reason}, trying Gemini 2.5 Pro...")
            iter_start = datetime.now()

            try:
                # Build escalation context
                issues_summary = []
                for check in current_qa.checks:
                    if check.status in (QACheckStatus.FAIL, QACheckStatus.WARN):
                        issues_summary.append(f"- {check.name}: {check.message}")

                escalated, tokens_in, tokens_out = await self.gemini.extract_pro(
                    context=context[:100000],
                    previous_extraction=current_extraction,
                    issues=issues_summary,
                )

                escalation_cost = calculate_cost(ModelTier.TIER1_5_GEMINI_PRO, tokens_in, tokens_out)
                self.total_cost += escalation_cost

                # QA the escalated result
                self.qa_agent.total_cost = 0
                escalated_qa = await self.qa_agent.run_qa(escalated, filings)
                self.total_cost += self.qa_agent.total_cost

                escalated_debt_count = len(escalated.get('debt_instruments', []))
                current_debt_count = len(current_extraction.get('debt_instruments', []))

                print(f"    Gemini Pro extraction: {len(escalated.get('entities', []))} entities, "
                      f"{escalated_debt_count} debt")
                print(f"    QA Score: {escalated_qa.overall_score:.0f}%")

                # Use escalated result if:
                # 1. It has a higher QA score, OR
                # 2. It found debt when current has none (and QA is acceptable >= 60%)
                use_escalated = (
                    escalated_qa.overall_score > current_qa.overall_score or
                    (escalated_debt_count > 0 and current_debt_count == 0 and escalated_qa.overall_score >= 60)
                )

                if use_escalated:
                    current_extraction = escalated
                    current_qa = escalated_qa
                    has_debt = escalated_debt_count > 0
                    final_model = "gemini-1.5-pro"

                    iterations.append(IterationResult(
                        iteration=len(iterations),
                        action=IterationAction.ESCALATE,
                        extraction=current_extraction.copy(),
                        qa_score=current_qa.overall_score,
                        issues_fixed=["Escalated to Gemini 2.5 Pro"],
                        cost=escalation_cost,
                        duration_seconds=(datetime.now() - iter_start).total_seconds(),
                    ))

            except Exception as e:
                print(f"    Gemini Pro escalation failed: {e}")

        # Check if we still need Claude escalation
        has_debt = len(current_extraction.get('debt_instruments', [])) > 0
        still_needs_escalation = (
            current_qa.overall_score < self.quality_threshold or
            (not has_debt and self.quality_threshold >= 80)
        )

        if still_needs_escalation and self.claude:
            # Try Claude as final escalation
            escalation_reason = "no debt instruments found" if not has_debt else f"quality below {self.quality_threshold}%"
            print(f"\n  [Escalation] {escalation_reason}, escalating to Claude...")
            iter_start = datetime.now()

            try:
                # Build escalation context
                issues_summary = []
                for check in current_qa.checks:
                    if check.status in (QACheckStatus.FAIL, QACheckStatus.WARN):
                        issues_summary.append(f"- {check.name}: {check.message}")

                escalated, tokens_in, tokens_out = await self.claude.extract_sonnet(
                    context=context[:100000],
                    previous_extraction=current_extraction,
                    issues=issues_summary,
                )

                escalation_cost = calculate_cost(ModelTier.TIER2_SONNET, tokens_in, tokens_out)
                self.total_cost += escalation_cost

                # QA the escalated result
                self.qa_agent.total_cost = 0
                escalated_qa = await self.qa_agent.run_qa(escalated, filings)
                self.total_cost += self.qa_agent.total_cost

                escalated_debt_count = len(escalated.get('debt_instruments', []))
                current_debt_count = len(current_extraction.get('debt_instruments', []))

                print(f"    Claude extraction: {len(escalated.get('entities', []))} entities, "
                      f"{escalated_debt_count} debt")
                print(f"    QA Score: {escalated_qa.overall_score:.0f}%")

                # Use escalated result if:
                # 1. It has a higher QA score, OR
                # 2. It found debt when current has none (and QA is acceptable >= 60%)
                use_escalated = (
                    escalated_qa.overall_score > current_qa.overall_score or
                    (escalated_debt_count > 0 and current_debt_count == 0 and escalated_qa.overall_score >= 60)
                )

                if use_escalated:
                    current_extraction = escalated
                    current_qa = escalated_qa
                    final_model = "claude-sonnet-4"

                    iterations.append(IterationResult(
                        iteration=len(iterations),
                        action=IterationAction.ESCALATE,
                        extraction=current_extraction.copy(),
                        qa_score=current_qa.overall_score,
                        issues_fixed=["Escalated to Claude Sonnet"],
                        cost=escalation_cost,
                        duration_seconds=(datetime.now() - iter_start).total_seconds(),
                    ))

            except Exception as e:
                print(f"    Claude escalation failed: {e}")

        total_duration = (datetime.now() - start_time).total_seconds()

        # Add metadata to extraction
        current_extraction["_iterative"] = {
            "iterations": len(iterations),
            "final_qa_score": current_qa.overall_score,
            "total_cost": self.total_cost,
            "final_model": final_model,
        }

        return IterativeExtractionResult(
            ticker=ticker,
            extraction=current_extraction,
            final_qa_score=current_qa.overall_score,
            iterations=iterations,
            total_cost=self.total_cost,
            total_duration=total_duration,
            final_model=final_model,
            qa_report=current_qa,
        )
