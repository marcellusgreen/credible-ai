#!/usr/bin/env python3
"""
Extract parent-child ownership relationships from indentures and credit agreements.

Supplements Exhibit 21 hierarchy extraction by finding ownership mentions in legal documents
that specify which entities own other entities.

Usage:
    # Single company (dry run)
    python scripts/extract_ownership_from_docs.py --ticker ON

    # Single company (save to database)
    python scripts/extract_ownership_from_docs.py --ticker ON --save-db

    # All companies with orphan entities
    python scripts/extract_ownership_from_docs.py --all --save-db
"""

import argparse
import asyncio
from typing import Optional
from uuid import UUID

import google.generativeai as genai
from sqlalchemy import select, func, or_

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

EXTRACT_OWNERSHIP_PROMPT = """You are analyzing SEC filings (indentures, credit agreements) to extract corporate ownership relationships.

COMPANY: {company_name} ({ticker})

ENTITIES IN DATABASE (need to find parent relationships):
{orphan_entities}

POTENTIAL PARENT ENTITIES (already have hierarchy position):
{parent_entities}

DOCUMENT CONTENT:
{document_content}

TASK: Find parent-child ownership relationships for the orphan entities listed above.

Look for patterns like:
- "X is a wholly-owned subsidiary of Y"
- "X, a direct subsidiary of Y"
- "X, an indirect subsidiary of Y"
- "X is owned by Y"
- "Y owns 100% of X"
- Schedule of Subsidiaries showing ownership chains
- Guarantor lists showing which entities own which

For each orphan entity, try to determine its immediate parent from the potential parents list OR from another orphan (creating a chain).

Return JSON:
{{
  "ownership_relationships": [
    {{
      "child_entity": "EXACT name from orphan entities list",
      "parent_entity": "EXACT name from parent entities OR orphan entities list",
      "ownership_type": "direct" or "indirect",
      "ownership_pct": 100,
      "evidence": "Quote showing the relationship"
    }}
  ],
  "notes": "Any observations about the corporate structure"
}}

CRITICAL RULES:
1. child_entity MUST be an EXACT match to a name in the orphan entities list
2. parent_entity MUST be an EXACT match to a name in either the parent entities OR orphan entities list
3. If you cannot find a parent, DO NOT include that entity
4. Only include relationships you're confident about based on explicit document language
5. "direct" means the parent directly owns the child; "indirect" means through intermediaries

Return ONLY the JSON object."""


# =============================================================================
# LLM CLIENT
# =============================================================================

class GeminiOwnershipExtractor:
    """Use Gemini to extract ownership relationships."""

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

    async def extract_ownership(
        self,
        company_name: str,
        ticker: str,
        orphan_entities: str,
        parent_entities: str,
        document_content: str,
    ) -> dict:
        """Extract ownership relationships from documents."""

        prompt = EXTRACT_OWNERSHIP_PROMPT.format(
            company_name=company_name,
            ticker=ticker,
            orphan_entities=orphan_entities,
            parent_entities=parent_entities,
            document_content=document_content[:100000],
        )

        try:
            response = self.model.generate_content(prompt)
            result = parse_json_robust(response.text)
            return result
        except Exception as e:
            print(f"    LLM error: {e}")
            return {"ownership_relationships": [], "notes": f"Error: {e}"}


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


async def get_orphan_entities(db: AsyncSession, company_id: UUID) -> list[Entity]:
    """Get entities without parent_id (excluding root company)."""
    result = await db.execute(
        select(Entity)
        .where(Entity.company_id == company_id)
        .where(Entity.parent_id.is_(None))
        .where(or_(Entity.structure_tier.is_(None), Entity.structure_tier != 1))
    )
    return list(result.scalars())


async def get_parent_entities(db: AsyncSession, company_id: UUID) -> list[Entity]:
    """Get entities that have parents assigned (can be potential parents for orphans)."""
    result = await db.execute(
        select(Entity)
        .where(Entity.company_id == company_id)
        .where(or_(
            Entity.parent_id.isnot(None),
            Entity.structure_tier == 1,
        ))
    )
    return list(result.scalars())


async def get_document_content(db: AsyncSession, company_id: UUID) -> str:
    """Get relevant document sections for ownership extraction."""
    content_parts = []

    # Priority: exhibit_21, guarantor_list, credit_agreement, indenture
    section_types = ['exhibit_21', 'guarantor_list', 'credit_agreement', 'indenture']

    for section_type in section_types:
        sections = await db.execute(
            select(DocumentSection)
            .where(DocumentSection.company_id == company_id)
            .where(DocumentSection.section_type == section_type)
            .order_by(DocumentSection.filing_date.desc())
            .limit(3)
        )

        for section in sections.scalars():
            content_parts.append(f"\n=== {section_type.upper()} ({section.filing_date}) ===\n")
            # For large docs, extract ownership-related sections
            if section.content_length > 50000:
                content_parts.append(extract_ownership_sections(section.content))
            else:
                content_parts.append(section.content[:30000])

        if sum(len(p) for p in content_parts) > 90000:
            break

    return "\n".join(content_parts)


def extract_ownership_sections(content: str) -> str:
    """Extract ownership-related sections from large documents."""
    import re

    extracted_parts = []

    # Keywords to look for
    ownership_keywords = [
        r'subsidiar',
        r'wholly.?owned',
        r'parent',
        r'ownership',
        r'guarantor',
        r'organizational\s+structure',
        r'corporate\s+structure',
        r'schedule.*subsidiar',
    ]

    lines = content.split('\n')
    in_relevant_section = False
    section_buffer = []

    for line in lines:
        line_lower = line.lower()

        # Check if this line starts a relevant section
        is_header = (
            len(line.strip()) < 100 and
            (line.strip().isupper() or
             re.match(r'^(section\s+)?\d+[\.\d]*\s+', line_lower) or
             re.match(r'^schedule\s+', line_lower))
        )

        if is_header:
            if in_relevant_section and section_buffer:
                section_text = '\n'.join(section_buffer)
                if len(section_text) > 100:
                    extracted_parts.append(section_text[:10000])

            in_relevant_section = any(re.search(kw, line_lower) for kw in ownership_keywords)
            section_buffer = [line] if in_relevant_section else []
        elif in_relevant_section:
            section_buffer.append(line)

    if in_relevant_section and section_buffer:
        section_text = '\n'.join(section_buffer)
        if len(section_text) > 100:
            extracted_parts.append(section_text[:10000])

    if extracted_parts:
        return '\n\n---\n\n'.join(extracted_parts)

    return content[:30000]


# =============================================================================
# MAIN PROCESSING
# =============================================================================

async def process_company(
    db: AsyncSession,
    company: Company,
    extractor: GeminiOwnershipExtractor,
    save_db: bool = False,
) -> dict:
    """Process a single company to extract ownership relationships."""

    print(f"\n[{company.ticker}] {company.name}")

    # Get orphan entities
    orphans = await get_orphan_entities(db, company.id)
    if not orphans:
        print("  No orphan entities")
        return {"status": "skipped", "reason": "no_orphans"}

    print(f"  {len(orphans)} orphan entities")

    # Get potential parents
    parents = await get_parent_entities(db, company.id)
    print(f"  {len(parents)} potential parent entities")

    # Build entity lookup (include both orphans and parents)
    entity_by_name = {}
    for e in orphans + parents:
        entity_by_name[normalize_name(e.name)] = e
        if e.legal_name:
            entity_by_name[normalize_name(e.legal_name)] = e

    # Get document content
    content = await get_document_content(db, company.id)
    if not content or len(content) < 1000:
        print("  Insufficient document content")
        return {"status": "skipped", "reason": "no_documents"}

    print(f"  Document content: {len(content):,} chars")

    # Format entity lists
    orphan_list = "\n".join([f"- {e.name}" for e in orphans[:100]])
    parent_list = "\n".join([f"- {e.name}" for e in parents[:50]])

    # Call LLM
    print("  Calling Gemini...")
    result = await extractor.extract_ownership(
        company_name=company.name,
        ticker=company.ticker,
        orphan_entities=orphan_list,
        parent_entities=parent_list,
        document_content=content,
    )

    relationships = result.get("ownership_relationships", []) or []
    print(f"  Found {len(relationships)} ownership relationships")

    if result.get("notes"):
        print(f"  Notes: {result['notes'][:150]}")

    # Process results
    stats = {"updated": 0, "links_created": 0}

    for item in relationships:
        if not item:
            continue

        try:
            child_name = item.get("child_entity", "") or ""
            parent_name = item.get("parent_entity", "") or ""
            ownership_type = item.get("ownership_type", "direct") or "direct"
            ownership_pct = item.get("ownership_pct", 100) or 100

            child_entity = match_entity_name(child_name, entity_by_name)
            parent_entity = match_entity_name(parent_name, entity_by_name)

            if not child_entity:
                print(f"    NO MATCH (child): '{child_name[:50]}'")
                continue
            if not parent_entity:
                print(f"    NO MATCH (parent): '{parent_name[:50]}'")
                continue

            if child_entity.id == parent_entity.id:
                continue  # Skip self-references

            print(f"    {child_entity.name[:40]} <- {parent_entity.name[:30]} [{ownership_type}]")

            if save_db:
                # Update parent_id if not set
                if child_entity.parent_id is None:
                    child_entity.parent_id = parent_entity.id
                    stats["updated"] += 1

                # Check/create ownership_link
                existing = await db.scalar(
                    select(OwnershipLink).where(
                        OwnershipLink.child_entity_id == child_entity.id,
                        OwnershipLink.parent_entity_id == parent_entity.id,
                    )
                )

                if not existing:
                    link = OwnershipLink(
                        parent_entity_id=parent_entity.id,
                        child_entity_id=child_entity.id,
                        ownership_pct=ownership_pct,
                        ownership_type=ownership_type,
                    )
                    db.add(link)
                    stats["links_created"] += 1

        except Exception as e:
            print(f"    Error processing relationship: {e}")

    if save_db:
        await db.commit()
        print(f"  Saved: {stats['updated']} parent updates, {stats['links_created']} links created")

    return {
        "status": "success",
        "orphans": len(orphans),
        "relationships_found": len(relationships),
        "stats": stats,
    }


async def get_companies_with_orphans(db: AsyncSession, limit: Optional[int] = None) -> list[Company]:
    """Get companies that have orphan entities."""
    query = (
        select(Company, func.count(Entity.id).label("orphan_count"))
        .join(Entity, Entity.company_id == Company.id)
        .where(Entity.parent_id.is_(None))
        .where(or_(Entity.structure_tier.is_(None), Entity.structure_tier != 1))
        .group_by(Company.id)
        .having(func.count(Entity.id) > 0)
        .order_by(func.count(Entity.id).desc())
    )

    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    return [(c, count) for c, count in result]


async def main():
    parser = argparse.ArgumentParser(description="Extract ownership relationships from legal documents")
    parser.add_argument("--ticker", type=str, help="Process single company by ticker")
    parser.add_argument("--all", action="store_true", help="Process all companies with orphan entities")
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

    extractor = GeminiOwnershipExtractor(settings.gemini_api_key)

    print_header("OWNERSHIP EXTRACTION FROM DOCUMENTS")
    print(f"Mode: {'SAVE TO DB' if args.save_db else 'DRY RUN'}")

    total_stats = {
        "companies_processed": 0,
        "companies_skipped": 0,
        "relationships_found": 0,
        "parents_updated": 0,
        "links_created": 0,
    }

    async with get_db_session() as db:
        if args.ticker:
            company = await db.scalar(
                select(Company).where(Company.ticker == args.ticker.upper())
            )
            if not company:
                print(f"Company {args.ticker} not found")
                return

            result = await process_company(db, company, extractor, args.save_db)
            if result["status"] == "success":
                total_stats["companies_processed"] = 1
                total_stats["relationships_found"] = result.get("relationships_found", 0)
                stats = result.get("stats", {})
                total_stats["parents_updated"] = stats.get("updated", 0)
                total_stats["links_created"] = stats.get("links_created", 0)
        else:
            companies = await get_companies_with_orphans(db, limit=args.limit)
            print(f"Found {len(companies)} companies with orphan entities")

            for company, orphan_count in companies:
                try:
                    result = await process_company(db, company, extractor, args.save_db)

                    if result["status"] == "success":
                        total_stats["companies_processed"] += 1
                        total_stats["relationships_found"] += result.get("relationships_found", 0)
                        stats = result.get("stats", {})
                        total_stats["parents_updated"] += stats.get("updated", 0)
                        total_stats["links_created"] += stats.get("links_created", 0)
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
    print(f"Relationships found: {total_stats['relationships_found']}")
    print(f"Parent updates: {total_stats['parents_updated']}")
    print(f"Ownership links created: {total_stats['links_created']}")


if __name__ == "__main__":
    run_async(main())
