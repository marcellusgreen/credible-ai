#!/usr/bin/env python3
"""
Extract covenant relationship data from indentures and credit agreements.

Extracts:
1. Unrestricted subsidiary designations (updates entities.is_unrestricted)
2. Guarantee release/add conditions (updates guarantees.conditions)
3. Cross-default/cross-acceleration links (creates cross_default_links records)
4. Non-guarantor disclosure percentages (updates company_metrics.non_guarantor_disclosure)

Usage:
    # Single company (dry run)
    python scripts/extract_covenant_relationships.py --ticker CHTR

    # Single company (save to database)
    python scripts/extract_covenant_relationships.py --ticker CHTR --save-db

    # Batch all companies
    python scripts/extract_covenant_relationships.py --all --save-db

    # Batch with limit
    python scripts/extract_covenant_relationships.py --all --limit 10 --save-db
"""

import argparse
import asyncio
from decimal import Decimal
from typing import Optional
from uuid import UUID

import google.generativeai as genai
from sqlalchemy import select, func

from script_utils import get_db_session, print_header, run_async
from app.core.config import get_settings
from app.models import (
    Company,
    CompanyMetrics,
    CrossDefaultLink,
    DebtInstrument,
    DocumentSection,
    Entity,
    Guarantee,
)
from app.services.utils import parse_json_robust


# =============================================================================
# PROMPTS
# =============================================================================

EXTRACT_RELATIONSHIPS_PROMPT = """You are analyzing SEC filings (indentures and credit agreements) to extract covenant relationship data.

COMPANY: {company_name} ({ticker})

ENTITIES IN DATABASE (use these EXACT names when matching):
{entity_list}

DEBT INSTRUMENTS IN DATABASE (use these EXACT names when matching):
{debt_list}

DOCUMENT CONTENT:
{document_content}

TASK: Extract the following relationship data from the documents:

1. **UNRESTRICTED SUBSIDIARIES**: Identify any entities from the ENTITIES list above that are explicitly designated as "unrestricted subsidiaries" or outside the covenant group.

2. **GUARANTEE CONDITIONS**: Find conditions that trigger guarantee release or addition. You MUST specify BOTH a debt_name (from DEBT INSTRUMENTS list) AND a guarantor_name (from ENTITIES list). Common triggers:
   - Release upon sale of guarantor
   - Release upon rating upgrade
   - Release upon achieving financial metrics
   - Addition upon acquisition of new subsidiary

3. **CROSS-DEFAULT TRIGGERS**: Find cross-default and cross-acceleration provisions linking to other debt:
   - Look for "cross-default" or "Event of Default" language that references other debt
   - Note threshold amounts (e.g., "default on debt exceeding $50 million")
   - Note if it's bilateral (applies both ways)

4. **NON-GUARANTOR DISCLOSURE**: Find percentages like:
   - "Non-guarantor subsidiaries represent X% of consolidated EBITDA"
   - "Non-guarantor subsidiaries hold X% of total assets"

Return JSON with this structure:
{{
  "unrestricted_subsidiaries": [
    {{
      "entity_name": "EXACT name from ENTITIES list above",
      "evidence": "Quote from document"
    }}
  ],
  "guarantee_conditions": [
    {{
      "debt_name": "EXACT name from DEBT INSTRUMENTS list above",
      "guarantor_name": "EXACT name from ENTITIES list above",
      "release_triggers": ["sale_of_guarantor", "rating_upgrade", "asset_sale"],
      "add_triggers": ["acquisition", "designation_as_restricted"],
      "evidence": "Quote from document"
    }}
  ],
  "cross_default_links": [
    {{
      "source_debt_name": "EXACT name from DEBT INSTRUMENTS list above",
      "target_debt_name": "EXACT name from DEBT INSTRUMENTS list (or null if general cross-default to all debt)",
      "relationship_type": "cross_default" or "cross_acceleration" or "pari_passu",
      "threshold_amount_millions": 50,
      "threshold_description": "any debt exceeding $50 million",
      "is_bilateral": true or false,
      "evidence": "Quote from document"
    }}
  ],
  "non_guarantor_disclosure": {{
    "ebitda_pct": 15.3,
    "assets_pct": 12.1,
    "revenue_pct": 10.5,
    "source_note": "Note 18 - Guarantor Information",
    "evidence": "Quote from document"
  }},
  "notes": "Any observations about the covenant structure"
}}

CRITICAL RULES:
1. entity_name and guarantor_name MUST be an EXACT match to one of the names in the ENTITIES list - do NOT return null
2. debt_name and source_debt_name MUST be an EXACT match to one of the names in the DEBT INSTRUMENTS list - do NOT return null
3. If you cannot find a matching entity/debt name, SKIP that finding entirely (don't include it)
4. Threshold amounts should be in MILLIONS (e.g., "$50 million" -> 50)
5. Only include findings you're confident about based on explicit document language
6. If no findings for a category, return an empty array [] or null

Return ONLY the JSON object."""


# =============================================================================
# LLM CLIENT
# =============================================================================

class GeminiRelationshipExtractor:
    """Use Gemini to extract covenant relationship data."""

    def __init__(self, api_key: str):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            generation_config={
                "temperature": 0.1,
                "response_mime_type": "application/json",
                "max_output_tokens": 16000,
            }
        )

    async def extract_relationships(
        self,
        company_name: str,
        ticker: str,
        entity_list: str,
        debt_list: str,
        document_content: str,
    ) -> dict:
        """Extract relationship data from documents."""

        prompt = EXTRACT_RELATIONSHIPS_PROMPT.format(
            company_name=company_name,
            ticker=ticker,
            entity_list=entity_list,
            debt_list=debt_list,
            document_content=document_content[:100000],  # Limit to 100K chars
        )

        try:
            response = self.model.generate_content(prompt)
            result = parse_json_robust(response.text)
            return result
        except Exception as e:
            print(f"    LLM error: {e}")
            return {
                "unrestricted_subsidiaries": [],
                "guarantee_conditions": [],
                "cross_default_links": [],
                "non_guarantor_disclosure": None,
                "notes": f"Error: {e}"
            }


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def normalize_name(name: str) -> str:
    """Normalize entity/debt name for fuzzy matching."""
    if not name:
        return ""
    return name.lower().replace(',', '').replace('.', '').replace("'", "").strip()


def match_entity_name(name: str, entity_by_name: dict) -> Optional[Entity]:
    """Find matching entity by name (fuzzy)."""
    if not name:
        return None

    normalized = normalize_name(name)

    # Direct match
    if normalized in entity_by_name:
        return entity_by_name[normalized]

    # Partial match (entity name contains search term or vice versa)
    for key, entity in entity_by_name.items():
        if normalized in key or key in normalized:
            return entity

    return None


def match_debt_name(name: str, debt_by_name: dict) -> Optional[DebtInstrument]:
    """Find matching debt instrument by name (fuzzy)."""
    if not name:
        return None

    normalized = normalize_name(name)

    # Direct match
    if normalized in debt_by_name:
        return debt_by_name[normalized]

    # Partial match
    for key, debt in debt_by_name.items():
        if normalized in key or key in normalized:
            return debt

    return None


async def get_companies_with_documents(db: AsyncSession, ticker: Optional[str] = None, limit: Optional[int] = None) -> list[Company]:
    """Get companies that have indentures or credit agreements."""
    query = (
        select(Company)
        .join(DocumentSection, DocumentSection.company_id == Company.id)
        .where(DocumentSection.section_type.in_(['indenture', 'credit_agreement']))
        .group_by(Company.id)
        .having(func.count(DocumentSection.id) > 0)
        .order_by(Company.ticker)
    )

    if ticker:
        query = select(Company).where(Company.ticker == ticker.upper())

    if limit and not ticker:
        query = query.limit(limit)

    result = await db.execute(query)
    return list(result.scalars().unique())


async def get_document_content(db: AsyncSession, company_id: UUID) -> str:
    """Get relevant document sections for a company, prioritizing linked documents."""
    from app.models import DebtInstrumentDocument

    content_parts = []
    seen_doc_ids = set()

    # First, get documents that are actually linked to debt instruments
    # These are the most relevant for covenant extraction
    # Use subquery to get unique documents, then join for debt names
    from sqlalchemy import distinct

    linked_docs = await db.execute(
        select(DocumentSection, DebtInstrument.name.label("debt_name"))
        .join(DebtInstrumentDocument, DocumentSection.id == DebtInstrumentDocument.document_section_id)
        .join(DebtInstrument, DebtInstrumentDocument.debt_instrument_id == DebtInstrument.id)
        .where(DebtInstrument.company_id == company_id)
        .where(DocumentSection.section_type.in_(['credit_agreement', 'indenture']))
        .order_by(DocumentSection.content_length.desc())
    )

    for section, debt_name in linked_docs:
        if section.id in seen_doc_ids:
            continue
        seen_doc_ids.add(section.id)

        content_parts.append(f"\n=== {section.section_type.upper()} (linked to: {debt_name}) ({section.filing_date}) ===\n")
        # For large credit agreements, extract covenant-related sections
        if section.content_length > 50000:
            content_parts.append(extract_covenant_sections(section.content))
        else:
            content_parts.append(section.content[:30000])

        # Stop if we have enough content
        if sum(len(p) for p in content_parts) > 90000:
            break

    # If we don't have enough linked docs, fall back to general sections
    if sum(len(p) for p in content_parts) < 20000:
        section_types = ['covenants', 'guarantor_list']
        for section_type in section_types:
            sections = await db.execute(
                select(DocumentSection)
                .where(DocumentSection.company_id == company_id)
                .where(DocumentSection.section_type == section_type)
                .where(DocumentSection.id.notin_(seen_doc_ids))
                .order_by(DocumentSection.filing_date.desc())
                .limit(2)
            )

            for section in sections.scalars():
                content_parts.append(f"\n=== {section_type.upper()} ({section.filing_date}) ===\n")
                content_parts.append(section.content[:20000])

    return "\n".join(content_parts)


def extract_covenant_sections(content: str) -> str:
    """Extract covenant-related sections from large documents like credit agreements."""
    import re

    extracted_parts = []

    # Keywords to look for in section headers
    covenant_keywords = [
        r'cross.?default',
        r'event.?of.?default',
        r'unrestricted.?subsidiar',
        r'restricted.?subsidiar',
        r'guarantee',
        r'guarantor',
        r'release',
        r'covenant',
        r'non.?guarantor',
        r'pari.?passu',
        r'subordinat',
    ]

    # Find sections containing these keywords
    lines = content.split('\n')
    in_relevant_section = False
    section_buffer = []
    section_start = 0

    for i, line in enumerate(lines):
        line_lower = line.lower()

        # Check if this line starts a relevant section (looks like a header)
        is_header = (
            len(line.strip()) < 100 and
            (line.strip().isupper() or
             re.match(r'^(section\s+)?\d+[\.\d]*\s+', line_lower) or
             re.match(r'^article\s+[ivxlcdm\d]+', line_lower))
        )

        if is_header:
            # Save previous section if it was relevant
            if in_relevant_section and section_buffer:
                section_text = '\n'.join(section_buffer)
                if len(section_text) > 100:  # Minimum content threshold
                    extracted_parts.append(section_text[:8000])  # Limit per section

            # Check if new section is relevant
            in_relevant_section = any(re.search(kw, line_lower) for kw in covenant_keywords)
            section_buffer = [line] if in_relevant_section else []
        elif in_relevant_section:
            section_buffer.append(line)

    # Don't forget the last section
    if in_relevant_section and section_buffer:
        section_text = '\n'.join(section_buffer)
        if len(section_text) > 100:
            extracted_parts.append(section_text[:8000])

    # If we found relevant sections, return them
    if extracted_parts:
        return '\n\n---\n\n'.join(extracted_parts)

    # Fallback: return first 30K chars if no sections found
    return content[:30000]


async def get_entities(db: AsyncSession, company_id: UUID) -> tuple[list[Entity], dict]:
    """Get all entities for a company and build name lookup."""
    result = await db.execute(
        select(Entity).where(Entity.company_id == company_id)
    )
    entities = list(result.scalars())

    entity_by_name = {}
    for e in entities:
        entity_by_name[normalize_name(e.name)] = e
        if e.legal_name:
            entity_by_name[normalize_name(e.legal_name)] = e

    return entities, entity_by_name


async def get_debt_instruments(db: AsyncSession, company_id: UUID) -> tuple[list[DebtInstrument], dict]:
    """Get all debt instruments for a company and build name lookup."""
    result = await db.execute(
        select(DebtInstrument).where(DebtInstrument.company_id == company_id)
    )
    debts = list(result.scalars())

    debt_by_name = {}
    for d in debts:
        debt_by_name[normalize_name(d.name)] = d

    return debts, debt_by_name


async def get_guarantees(db: AsyncSession, company_id: UUID) -> list[Guarantee]:
    """Get all guarantees for a company's debt instruments."""
    result = await db.execute(
        select(Guarantee)
        .join(DebtInstrument, Guarantee.debt_instrument_id == DebtInstrument.id)
        .where(DebtInstrument.company_id == company_id)
    )
    return list(result.scalars())


# =============================================================================
# MAIN PROCESSING
# =============================================================================

async def process_company(
    db: AsyncSession,
    company: Company,
    extractor: GeminiRelationshipExtractor,
    save_db: bool = False,
) -> dict:
    """Process a single company to extract covenant relationships."""

    print(f"\n[{company.ticker}] {company.name}")

    # Get entities and debt instruments
    entities, entity_by_name = await get_entities(db, company.id)
    debts, debt_by_name = await get_debt_instruments(db, company.id)
    guarantees = await get_guarantees(db, company.id)

    if not entities or not debts:
        print("  No entities or debt instruments")
        return {"status": "skipped", "reason": "no_data"}

    print(f"  {len(entities)} entities, {len(debts)} debt instruments, {len(guarantees)} guarantees")

    # Get document content
    content = await get_document_content(db, company.id)
    if not content or len(content) < 1000:
        print("  Insufficient document content")
        return {"status": "skipped", "reason": "no_documents"}

    print(f"  Document content: {len(content):,} chars")

    # Format entity and debt lists for LLM
    entity_list = "\n".join([
        f"- {e.name} (type: {e.entity_type}, unrestricted: {e.is_unrestricted})"
        for e in entities[:100]  # Limit to prevent context overflow
    ])

    debt_list = "\n".join([
        f"- {d.name} (type: {d.instrument_type}, seniority: {d.seniority})"
        for d in debts[:50]
    ])

    # Call LLM
    print("  Calling Gemini...")
    result = await extractor.extract_relationships(
        company_name=company.name,
        ticker=company.ticker,
        entity_list=entity_list,
        debt_list=debt_list,
        document_content=content,
    )

    # Debug: print what we got
    print(f"  Results: {len(result.get('unrestricted_subsidiaries', []) or [])} unrestricted, "
          f"{len(result.get('guarantee_conditions', []) or [])} conditions, "
          f"{len(result.get('cross_default_links', []) or [])} cross-defaults, "
          f"non-guarantor: {result.get('non_guarantor_disclosure') is not None}")

    if result.get('notes'):
        print(f"  Notes: {result['notes'][:200]}")

    # Debug: print actual extracted data
    if result.get('unrestricted_subsidiaries'):
        print(f"  DEBUG unrestricted: {result['unrestricted_subsidiaries'][:2]}")
    if result.get('guarantee_conditions'):
        print(f"  DEBUG conditions: {result['guarantee_conditions'][:2]}")

    # Process results
    stats = {
        "unrestricted_updated": 0,
        "conditions_added": 0,
        "cross_default_created": 0,
        "non_guarantor_updated": False,
    }

    # 1. Update unrestricted subsidiaries
    unrestricted = result.get("unrestricted_subsidiaries", []) or []
    for item in unrestricted:
        try:
            if not item:
                continue
            entity_name = item.get("entity_name", "") or ""
            entity = match_entity_name(entity_name, entity_by_name)

            if entity:
                print(f"    Unrestricted: {entity.name[:50]}")
                if save_db and not entity.is_unrestricted:
                    entity.is_unrestricted = True
                    stats["unrestricted_updated"] += 1
            else:
                print(f"    Unrestricted (NO MATCH): '{entity_name[:50]}'")
        except Exception as e:
            print(f"    Error processing unrestricted item {item}: {e}")

    # 2. Update guarantee conditions
    conditions = result.get("guarantee_conditions", []) or []
    for item in conditions:
        try:
            if not item:
                continue
            debt_name = item.get("debt_name", "") or ""
            guarantor_name = item.get("guarantor_name", "") or ""
            release_triggers = item.get("release_triggers", []) or []
            add_triggers = item.get("add_triggers", []) or []

            debt = match_debt_name(debt_name, debt_by_name)
            guarantor = match_entity_name(guarantor_name, entity_by_name)

            if not debt:
                print(f"    Conditions (NO DEBT MATCH): '{debt_name[:50] if debt_name else 'EMPTY'}'")
                continue
            if not guarantor:
                print(f"    Conditions (NO GUARANTOR MATCH): '{guarantor_name[:50] if guarantor_name else 'EMPTY'}'")
                continue

            # Find matching guarantee
            found_guarantee = False
            for g in guarantees:
                if g.debt_instrument_id == debt.id and g.guarantor_id == guarantor.id:
                    print(f"    Conditions for {debt.name[:30]} <- {guarantor.name[:30]}")
                    if save_db:
                        g.conditions = {
                            "release_triggers": release_triggers,
                            "add_triggers": add_triggers,
                        }
                        stats["conditions_added"] += 1
                    found_guarantee = True
                    break

            if not found_guarantee:
                print(f"    Conditions (NO GUARANTEE LINK): {debt.name[:30]} <- {guarantor.name[:30]}")
        except Exception as e:
            print(f"    Error processing condition item {item}: {e}")

    # 3. Create cross-default links
    cross_defaults = result.get("cross_default_links", []) or []
    for item in cross_defaults:
        if not item:
            continue
        source_name = item.get("source_debt_name", "") or ""
        target_name = item.get("target_debt_name")
        rel_type = item.get("relationship_type", "cross_default") or "cross_default"
        threshold_millions = item.get("threshold_amount_millions")
        threshold_desc = item.get("threshold_description", "") or ""
        is_bilateral = item.get("is_bilateral", False) or False
        evidence = item.get("evidence", "") or ""

        source_debt = match_debt_name(source_name, debt_by_name)
        target_debt = match_debt_name(target_name, debt_by_name) if target_name else None

        if source_debt:
            print(f"    Cross-default: {source_debt.name[:40]} -> {target_name or 'general'}")

            if save_db:
                # Check if link already exists
                existing = await db.scalar(
                    select(CrossDefaultLink).where(
                        CrossDefaultLink.source_debt_id == source_debt.id,
                        CrossDefaultLink.target_debt_id == (target_debt.id if target_debt else None),
                        CrossDefaultLink.relationship_type == rel_type,
                    )
                )

                if not existing:
                    link = CrossDefaultLink(
                        source_debt_id=source_debt.id,
                        target_debt_id=target_debt.id if target_debt else None,
                        relationship_type=rel_type,
                        threshold_amount=int(threshold_millions * 100_000_000) if threshold_millions else None,  # Convert millions to cents
                        threshold_description=threshold_desc,
                        is_bilateral=is_bilateral,
                        confidence=Decimal("0.8"),  # Default confidence
                        evidence=evidence[:500] if evidence else None,
                    )
                    db.add(link)
                    stats["cross_default_created"] += 1

    # 4. Update non-guarantor disclosure
    ng_disclosure = result.get("non_guarantor_disclosure")
    if ng_disclosure and any(ng_disclosure.get(k) for k in ['ebitda_pct', 'assets_pct', 'revenue_pct']):
        print(f"    Non-guarantor disclosure: EBITDA {ng_disclosure.get('ebitda_pct')}%, Assets {ng_disclosure.get('assets_pct')}%")

        if save_db:
            metrics = await db.scalar(
                select(CompanyMetrics).where(CompanyMetrics.ticker == company.ticker)
            )
            if metrics:
                metrics.non_guarantor_disclosure = {
                    k: v for k, v in ng_disclosure.items() if v is not None
                }
                stats["non_guarantor_updated"] = True

    if save_db:
        await db.commit()
        print(f"  Saved: {stats['unrestricted_updated']} unrestricted, {stats['conditions_added']} conditions, {stats['cross_default_created']} cross-defaults")

    return {
        "status": "success",
        "result": result,
        "stats": stats,
    }


async def main():
    parser = argparse.ArgumentParser(description="Extract covenant relationships from indentures/credit agreements")
    parser.add_argument("--ticker", type=str, help="Process single company by ticker")
    parser.add_argument("--all", action="store_true", help="Process all companies with documents")
    parser.add_argument("--limit", type=int, help="Limit number of companies to process")
    parser.add_argument("--save-db", action="store_true", help="Save changes to database")

    args = parser.parse_args()

    if not args.ticker and not args.all:
        print("Error: Must specify --ticker or --all")
        return

    settings = get_settings()

    if not settings.gemini_api_key:
        print("Error: GEMINI_API_KEY not set")
        return

    extractor = GeminiRelationshipExtractor(settings.gemini_api_key)

    print_header("COVENANT RELATIONSHIP EXTRACTION")
    print(f"Mode: {'SAVE TO DB' if args.save_db else 'DRY RUN'}")

    total_stats = {
        "companies_processed": 0,
        "companies_skipped": 0,
        "unrestricted_total": 0,
        "conditions_total": 0,
        "cross_default_total": 0,
        "non_guarantor_total": 0,
    }

    async with get_db_session() as db:
        companies = await get_companies_with_documents(
            db,
            ticker=args.ticker,
            limit=args.limit
        )

        print(f"Found {len(companies)} companies to process")

        for company in companies:
            try:
                result = await process_company(db, company, extractor, args.save_db)

                if result["status"] == "success":
                    total_stats["companies_processed"] += 1
                    stats = result.get("stats", {})
                    total_stats["unrestricted_total"] += stats.get("unrestricted_updated", 0)
                    total_stats["conditions_total"] += stats.get("conditions_added", 0)
                    total_stats["cross_default_total"] += stats.get("cross_default_created", 0)
                    if stats.get("non_guarantor_updated"):
                        total_stats["non_guarantor_total"] += 1
                else:
                    total_stats["companies_skipped"] += 1

            except Exception as e:
                print(f"  Error: {e}")
                total_stats["companies_skipped"] += 1

            # Rate limit
            await asyncio.sleep(1)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Companies processed: {total_stats['companies_processed']}")
    print(f"Companies skipped: {total_stats['companies_skipped']}")
    print(f"Unrestricted subs flagged: {total_stats['unrestricted_total']}")
    print(f"Guarantee conditions added: {total_stats['conditions_total']}")
    print(f"Cross-default links created: {total_stats['cross_default_total']}")
    print(f"Non-guarantor disclosures: {total_stats['non_guarantor_total']}")


if __name__ == "__main__":
    run_async(main())
