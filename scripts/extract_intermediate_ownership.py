#!/usr/bin/env python3
"""
Extract intermediate parent-child ownership relationships from indentures and credit agreements.

Fixes the issue where all entities point to the root company by finding the actual
intermediate ownership chains (e.g., "CCO Holdings Capital Corp is a subsidiary of CCO Holdings, LLC").

Usage:
    # Single company (dry run)
    python scripts/extract_intermediate_ownership.py --ticker CHTR

    # Single company (save to database)
    python scripts/extract_intermediate_ownership.py --ticker CHTR --save-db

    # All companies
    python scripts/extract_intermediate_ownership.py --all --save-db
"""

import argparse
import asyncio
from typing import Optional
from uuid import UUID

import google.generativeai as genai
from sqlalchemy import select, func

from script_utils import get_db_session, print_header, run_async
from app.core.config import get_settings
from app.models import (
    Company,
    DocumentSection,
    Entity,
    OwnershipLink,
)
from app.services.utils import parse_json_robust


# =============================================================================
# PROMPTS
# =============================================================================

EXTRACT_HIERARCHY_PROMPT = """You are analyzing SEC filings (indentures, credit agreements) to extract the INTERMEDIATE corporate ownership hierarchy.

COMPANY: {company_name} ({ticker})

ALL ENTITIES (need to determine parent-child relationships between them):
{entity_list}

DOCUMENT CONTENT:
{document_content}

TASK: Find the DIRECT parent-child relationships between entities in the list above.

Look for patterns like:
- "X, a wholly-owned subsidiary of Y"
- "X, a direct subsidiary of Y"
- "X is a subsidiary of Y"
- "Y, the direct parent of X"
- "X is owned by Y"
- Organizational structure descriptions
- Guarantor hierarchy descriptions

CRITICAL: We need to find which entity is the DIRECT parent of each entity - not the ultimate parent.

For example, if the documents say:
- "CCO Holdings Capital Corp., a wholly-owned subsidiary of CCO Holdings, LLC"
- "CCO Holdings, LLC, a subsidiary of Charter Communications, Inc."

Then CCO Holdings Capital Corp's direct parent is CCO Holdings, LLC (NOT Charter Communications, Inc.)

Return JSON:
{{
  "ownership_chain": [
    {{
      "child": "EXACT entity name from list",
      "direct_parent": "EXACT entity name from list (the immediate parent, not ultimate parent)",
      "evidence": "Quote showing the direct relationship"
    }}
  ],
  "root_entity": "Name of the ultimate parent company (top of hierarchy)",
  "notes": "Any observations about the corporate structure"
}}

RULES:
1. child and direct_parent MUST be EXACT matches to names in the entity list
2. Only include relationships where you find DIRECT parent evidence
3. Do NOT assume the root company is the direct parent unless explicitly stated
4. If entity A is subsidiary of B, and B is subsidiary of C, report A->B and B->C separately
5. Skip any relationship you're not confident about

Return ONLY the JSON object."""


# =============================================================================
# LLM CLIENT
# =============================================================================

class GeminiHierarchyExtractor:
    """Use Gemini to extract ownership hierarchy."""

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

    async def extract_hierarchy(
        self,
        company_name: str,
        ticker: str,
        entity_list: str,
        document_content: str,
    ) -> dict:
        """Extract ownership hierarchy from documents."""

        prompt = EXTRACT_HIERARCHY_PROMPT.format(
            company_name=company_name,
            ticker=ticker,
            entity_list=entity_list,
            document_content=document_content[:120000],
        )

        try:
            response = self.model.generate_content(prompt)
            result = parse_json_robust(response.text)
            return result
        except Exception as e:
            print(f"    LLM error: {e}")
            return {"ownership_chain": [], "notes": f"Error: {e}"}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def normalize_name(name: str) -> str:
    """Normalize entity name for fuzzy matching."""
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

    # Partial match
    for key, entity in entity_by_name.items():
        if normalized in key or key in normalized:
            return entity

    return None


async def get_document_content(db: AsyncSession, company_id: UUID) -> str:
    """Get relevant document sections for hierarchy extraction."""
    content_parts = []

    # Priority: credit_agreement (has org structure), indenture (has guarantor hierarchy)
    section_types = ['credit_agreement', 'indenture', 'guarantor_list']

    for section_type in section_types:
        sections = await db.execute(
            select(DocumentSection)
            .where(DocumentSection.company_id == company_id)
            .where(DocumentSection.section_type == section_type)
            .order_by(DocumentSection.content_length.desc())  # Largest first (more complete)
            .limit(3)
        )

        for section in sections.scalars():
            content_parts.append(f"\n=== {section_type.upper()} ({section.filing_date}) ===\n")
            # Extract ownership-related sections from large docs
            if section.content_length > 50000:
                content_parts.append(extract_hierarchy_sections(section.content))
            else:
                content_parts.append(section.content[:40000])

        if sum(len(p) for p in content_parts) > 100000:
            break

    return "\n".join(content_parts)


def extract_hierarchy_sections(content: str) -> str:
    """Extract hierarchy-related sections from large documents."""
    import re

    extracted_parts = []

    # Keywords indicating ownership/hierarchy info
    hierarchy_keywords = [
        r'wholly.?owned',
        r'direct.*subsidiary',
        r'indirect.*subsidiary',
        r'subsidiary\s+of',
        r'parent\s+of',
        r'owned\s+by',
        r'organizational\s+structure',
        r'corporate\s+structure',
        r'guarantor',
        r'the\s+issuer',
        r'the\s+company',
        r'holdings?\s*,?\s*llc',
        r'capital\s+corp',
        r'operating\s*,?\s*llc',
    ]

    lines = content.split('\n')

    # Extract paragraphs containing hierarchy keywords
    current_para = []
    for line in lines:
        if line.strip():
            current_para.append(line)
        else:
            if current_para:
                para_text = ' '.join(current_para)
                para_lower = para_text.lower()
                if any(re.search(kw, para_lower) for kw in hierarchy_keywords):
                    extracted_parts.append(para_text[:2000])
                current_para = []

    # Don't forget last paragraph
    if current_para:
        para_text = ' '.join(current_para)
        para_lower = para_text.lower()
        if any(re.search(kw, para_lower) for kw in hierarchy_keywords):
            extracted_parts.append(para_text[:2000])

    if extracted_parts:
        return '\n\n---\n\n'.join(extracted_parts[:50])  # Limit number of excerpts

    return content[:40000]


# =============================================================================
# MAIN PROCESSING
# =============================================================================

async def process_company(
    db: AsyncSession,
    company: Company,
    extractor: GeminiHierarchyExtractor,
    save_db: bool = False,
) -> dict:
    """Process a single company to extract intermediate ownership."""

    print(f"\n[{company.ticker}] {company.name}")

    # Get all entities
    entities = await db.execute(
        select(Entity).where(Entity.company_id == company.id)
    )
    entities = list(entities.scalars())

    if len(entities) < 2:
        print("  Less than 2 entities, skipping")
        return {"status": "skipped", "reason": "too_few_entities"}

    print(f"  {len(entities)} entities")

    # Build entity lookup
    entity_by_name = {}
    entity_by_id = {}
    for e in entities:
        entity_by_name[normalize_name(e.name)] = e
        entity_by_id[e.id] = e
        if e.legal_name:
            entity_by_name[normalize_name(e.legal_name)] = e

    # Find root entity (structure_tier = 1 with no parent)
    root = next((e for e in entities if e.structure_tier == 1 and e.parent_id is None), None)
    if not root:
        root = next((e for e in entities if e.structure_tier == 1), None)

    # Get document content
    content = await get_document_content(db, company.id)
    if not content or len(content) < 1000:
        print("  Insufficient document content")
        return {"status": "skipped", "reason": "no_documents"}

    print(f"  Document content: {len(content):,} chars")

    # Format entity list
    entity_list = "\n".join([f"- {e.name} (tier: {e.structure_tier}, type: {e.entity_type})" for e in entities])

    # Call LLM
    print("  Calling Gemini...")
    result = await extractor.extract_hierarchy(
        company_name=company.name,
        ticker=company.ticker,
        entity_list=entity_list,
        document_content=content,
    )

    ownership_chain = result.get("ownership_chain", []) or []
    print(f"  Found {len(ownership_chain)} intermediate relationships")

    if result.get("notes"):
        print(f"  Notes: {result['notes'][:150]}")

    # Process results
    stats = {"updated": 0, "links_updated": 0}

    for item in ownership_chain:
        if not item:
            continue

        try:
            child_name = item.get("child", "") or ""
            parent_name = item.get("direct_parent", "") or ""
            evidence = item.get("evidence", "") or ""

            child_entity = match_entity_name(child_name, entity_by_name)
            parent_entity = match_entity_name(parent_name, entity_by_name)

            if not child_entity:
                print(f"    NO MATCH (child): '{child_name[:40]}'")
                continue
            if not parent_entity:
                print(f"    NO MATCH (parent): '{parent_name[:40]}'")
                continue

            if child_entity.id == parent_entity.id:
                continue  # Skip self-references

            # Check if this is a NEW intermediate relationship (not just pointing to root)
            current_parent = entity_by_id.get(child_entity.parent_id)
            current_parent_name = current_parent.name if current_parent else "None"

            # Only update if we're finding a more specific (intermediate) parent
            is_new_info = (
                child_entity.parent_id is None or
                child_entity.parent_id == root.id if root else False or
                parent_entity.id != child_entity.parent_id
            )

            if parent_entity.structure_tier and child_entity.structure_tier:
                # Parent should be at a higher tier (lower number) than child
                if parent_entity.structure_tier >= child_entity.structure_tier:
                    print(f"    SKIP (tier mismatch): {child_entity.name[:30]} tier {child_entity.structure_tier} -> {parent_entity.name[:30]} tier {parent_entity.structure_tier}")
                    continue

            print(f"    {child_entity.name[:35]} -> {parent_entity.name[:30]}")
            if current_parent_name != parent_entity.name:
                print(f"      (was: {current_parent_name[:30]})")

            if save_db and is_new_info:
                # Update parent_id
                child_entity.parent_id = parent_entity.id
                stats["updated"] += 1

                # Update or create ownership_link
                existing_link = await db.scalar(
                    select(OwnershipLink).where(
                        OwnershipLink.child_entity_id == child_entity.id
                    )
                )

                if existing_link:
                    existing_link.parent_entity_id = parent_entity.id
                    existing_link.ownership_type = "direct"
                    stats["links_updated"] += 1
                else:
                    link = OwnershipLink(
                        parent_entity_id=parent_entity.id,
                        child_entity_id=child_entity.id,
                        ownership_pct=100,
                        ownership_type="direct",
                    )
                    db.add(link)
                    stats["links_updated"] += 1

        except Exception as e:
            print(f"    Error: {e}")

    if save_db:
        await db.commit()
        print(f"  Saved: {stats['updated']} parent updates, {stats['links_updated']} links")

    return {
        "status": "success",
        "entities": len(entities),
        "relationships_found": len(ownership_chain),
        "stats": stats,
    }


async def get_companies_to_process(db: AsyncSession, ticker: Optional[str] = None, limit: Optional[int] = None) -> list[Company]:
    """Get companies to process."""
    if ticker:
        company = await db.scalar(select(Company).where(Company.ticker == ticker.upper()))
        return [company] if company else []

    # Get companies with multiple entities
    query = (
        select(Company)
        .join(Entity, Entity.company_id == Company.id)
        .group_by(Company.id)
        .having(func.count(Entity.id) >= 2)
        .order_by(Company.ticker)
    )

    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    return list(result.scalars())


async def main():
    parser = argparse.ArgumentParser(description="Extract intermediate ownership relationships")
    parser.add_argument("--ticker", type=str, help="Process single company by ticker")
    parser.add_argument("--all", action="store_true", help="Process all companies")
    parser.add_argument("--limit", type=int, help="Limit number of companies")
    parser.add_argument("--save-db", action="store_true", help="Save changes to database")

    args = parser.parse_args()

    if not args.ticker and not args.all:
        print("Error: Must specify --ticker or --all")
        return

    settings = get_settings()

    if not settings.gemini_api_key:
        print("Error: GEMINI_API_KEY not set")
        return

    extractor = GeminiHierarchyExtractor(settings.gemini_api_key)

    print_header("INTERMEDIATE OWNERSHIP EXTRACTION")
    print(f"Mode: {'SAVE TO DB' if args.save_db else 'DRY RUN'}")

    total_stats = {
        "companies_processed": 0,
        "companies_skipped": 0,
        "relationships_found": 0,
        "parents_updated": 0,
        "links_updated": 0,
    }

    async with get_db_session() as db:
        companies = await get_companies_to_process(db, args.ticker, args.limit)
        print(f"Found {len(companies)} companies to process")

        for company in companies:
            try:
                result = await process_company(db, company, extractor, args.save_db)

                if result["status"] == "success":
                    total_stats["companies_processed"] += 1
                    total_stats["relationships_found"] += result.get("relationships_found", 0)
                    stats = result.get("stats", {})
                    total_stats["parents_updated"] += stats.get("updated", 0)
                    total_stats["links_updated"] += stats.get("links_updated", 0)
                else:
                    total_stats["companies_skipped"] += 1

            except Exception as e:
                print(f"  Error: {e}")
                total_stats["companies_skipped"] += 1

            await asyncio.sleep(1)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Companies processed: {total_stats['companies_processed']}")
    print(f"Companies skipped: {total_stats['companies_skipped']}")
    print(f"Intermediate relationships found: {total_stats['relationships_found']}")
    print(f"Parent updates: {total_stats['parents_updated']}")
    print(f"Ownership links updated: {total_stats['links_updated']}")


if __name__ == "__main__":
    run_async(main())
