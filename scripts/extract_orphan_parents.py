#!/usr/bin/env python3
"""
Extract parent relationships for orphan guarantor/issuer entities using LLM.

For entities that are guarantors or issuers but have no parent_id set,
this script uses Gemini to read the Exhibit 21/22 and indentures to
determine the correct parent entity.

Usage:
    # Process top 10 companies with most orphan guarantors
    python scripts/extract_orphan_parents.py --top 10 --save-db

    # Process single company
    python scripts/extract_orphan_parents.py --ticker CEG --save-db

    # Dry run (no database changes)
    python scripts/extract_orphan_parents.py --ticker CEG
"""

import argparse
import asyncio
from uuid import UUID

import google.generativeai as genai
from sqlalchemy import select, func, or_

from script_utils import get_db_session, print_header, run_async
from app.core.config import get_settings
from app.models import Company, Entity, Guarantee, DocumentSection, OwnershipLink
from app.services.utils import parse_json_robust


# =============================================================================
# PROMPTS
# =============================================================================

EXTRACT_PARENT_PROMPT = """You are analyzing SEC filings to determine parent-child corporate ownership relationships.

COMPANY: {company_name} ({ticker})

ORPHAN ENTITIES (need parent assignment):
{orphan_entities}

AVAILABLE PARENT ENTITIES (already in database):
{available_parents}

SEC FILING CONTENT:
{filing_content}

TASK: For each orphan entity, determine its immediate parent entity from the available parents list.

RULES:
1. The parent MUST be one of the entities in the "Available Parent Entities" list
2. If you cannot determine the parent with confidence, use the root company as parent
3. Look for clues like:
   - "wholly-owned subsidiary of [Parent]"
   - "[Entity] is a direct/indirect subsidiary of [Parent]"
   - Indentation in Exhibit 21 showing hierarchy
   - Credit agreement definitions of "Restricted Subsidiaries"
   - Guarantor structure descriptions

Return JSON:
{{
  "assignments": [
    {{
      "orphan_name": "Exact name of orphan entity",
      "parent_name": "Exact name of parent from available list",
      "ownership_type": "direct" or "indirect",
      "confidence": "high" or "medium" or "low",
      "evidence": "Brief quote or description of evidence"
    }}
  ],
  "notes": "Any observations about the corporate structure"
}}

IMPORTANT:
- Use EXACT entity names from the lists provided
- If multiple possible parents exist, choose the most direct/immediate one
- "indirect" means the orphan is a subsidiary of a subsidiary
- If the entity is directly owned by the root company, ownership_type is "direct"

Return ONLY the JSON object."""


# =============================================================================
# LLM CLIENT
# =============================================================================

class GeminiParentExtractor:
    """Use Gemini to extract parent relationships."""

    def __init__(self, api_key: str):
        genai.configure(api_key=api_key)
        # Use Gemini 1.5 Flash for cost efficiency
        self.model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            generation_config={
                "temperature": 0.1,
                "response_mime_type": "application/json",
                "max_output_tokens": 8000,
            }
        )

    async def extract_parents(
        self,
        company_name: str,
        ticker: str,
        orphan_entities: list[dict],
        available_parents: list[dict],
        filing_content: str,
    ) -> dict:
        """Extract parent assignments for orphan entities."""

        # Format orphan entities
        orphan_str = "\n".join([
            f"- {e['name']} (type: {e['entity_type']}, tier: {e['tier']})"
            for e in orphan_entities
        ])

        # Format available parents
        parent_str = "\n".join([
            f"- {e['name']} (type: {e['entity_type']}, tier: {e['tier']})"
            for e in available_parents
        ])

        prompt = EXTRACT_PARENT_PROMPT.format(
            company_name=company_name,
            ticker=ticker,
            orphan_entities=orphan_str,
            available_parents=parent_str,
            filing_content=filing_content[:50000],  # Limit content size
        )

        try:
            response = self.model.generate_content(prompt)
            result = parse_json_robust(response.text)
            return result
        except Exception as e:
            print(f"    LLM error: {e}")
            return {"assignments": [], "notes": f"Error: {e}"}


# =============================================================================
# MAIN LOGIC
# =============================================================================

async def get_orphan_guarantors(db: AsyncSession, company_id: UUID) -> list[Entity]:
    """Get entities that are guarantors but have no parent_id."""
    result = await db.execute(
        select(Entity)
        .join(Guarantee, Guarantee.guarantor_id == Entity.id)
        .where(Entity.company_id == company_id)
        .where(Entity.parent_id.is_(None))
        .distinct()
    )
    return list(result.scalars())


async def get_available_parents(db: AsyncSession, company_id: UUID) -> list[Entity]:
    """Get entities that could be parents (have parent_id set or are root)."""
    result = await db.execute(
        select(Entity)
        .where(Entity.company_id == company_id)
        .where(or_(
            Entity.parent_id.isnot(None),
            Entity.structure_tier == 1,
        ))
    )
    return list(result.scalars())


async def get_filing_content(db: AsyncSession, company_id: UUID) -> str:
    """Get relevant filing content for parent extraction."""
    content_parts = []

    # Priority order: exhibit_21, exhibit_22, guarantor_list, credit_agreement
    section_types = ['exhibit_21', 'exhibit_22', 'guarantor_list', 'credit_agreement', 'indenture']

    for section_type in section_types:
        sections = await db.execute(
            select(DocumentSection)
            .where(DocumentSection.company_id == company_id)
            .where(DocumentSection.section_type == section_type)
            .order_by(DocumentSection.filing_date.desc())
            .limit(2)
        )

        for section in sections.scalars():
            content_parts.append(f"\n=== {section_type.upper()} ({section.filing_date}) ===\n")
            content_parts.append(section.content[:15000])  # Limit per section

        if len(content_parts) > 5:
            break  # Enough content

    return "\n".join(content_parts)


async def process_company(
    db: AsyncSession,
    company: Company,
    extractor: GeminiParentExtractor,
    save_db: bool = False,
) -> dict:
    """Process a single company to fill orphan parent relationships."""

    print(f"\n[{company.ticker}] {company.name}")

    # Get orphan guarantors
    orphans = await get_orphan_guarantors(db, company.id)
    if not orphans:
        print("  No orphan guarantors")
        return {"status": "skipped", "reason": "no_orphans"}

    print(f"  Found {len(orphans)} orphan guarantors")

    # Get available parents
    parents = await get_available_parents(db, company.id)
    if not parents:
        print("  No available parents")
        return {"status": "skipped", "reason": "no_parents"}

    print(f"  Found {len(parents)} available parents")

    # Get filing content
    content = await get_filing_content(db, company.id)
    if not content:
        print("  No filing content")
        return {"status": "skipped", "reason": "no_content"}

    print(f"  Filing content: {len(content)} chars")

    # Build entity lookup
    all_entities = await db.execute(
        select(Entity).where(Entity.company_id == company.id)
    )
    entity_by_name = {}
    for e in all_entities.scalars():
        entity_by_name[e.name.lower().strip()] = e
        # Also add normalized version
        normalized = e.name.lower().replace(',', '').replace('.', '').strip()
        entity_by_name[normalized] = e

    # Prepare data for LLM
    orphan_data = [
        {"name": e.name, "entity_type": e.entity_type or "unknown", "tier": e.structure_tier or "?"}
        for e in orphans
    ]
    parent_data = [
        {"name": e.name, "entity_type": e.entity_type or "unknown", "tier": e.structure_tier or "?"}
        for e in parents
    ]

    # Call LLM
    print("  Calling Gemini...")
    result = await extractor.extract_parents(
        company_name=company.name,
        ticker=company.ticker,
        orphan_entities=orphan_data,
        available_parents=parent_data,
        filing_content=content,
    )

    assignments = result.get("assignments", [])
    print(f"  Got {len(assignments)} assignments")

    # Apply assignments
    updated = 0
    created = 0

    for assignment in assignments:
        orphan_name = assignment.get("orphan_name", "").lower().strip()
        parent_name = assignment.get("parent_name", "").lower().strip()
        ownership_type = assignment.get("ownership_type", "direct")
        confidence = assignment.get("confidence", "low")

        # Find entities
        orphan_entity = entity_by_name.get(orphan_name)
        parent_entity = entity_by_name.get(parent_name)

        if not orphan_entity:
            # Try normalized
            orphan_normalized = orphan_name.replace(',', '').replace('.', '')
            orphan_entity = entity_by_name.get(orphan_normalized)

        if not parent_entity:
            parent_normalized = parent_name.replace(',', '').replace('.', '')
            parent_entity = entity_by_name.get(parent_normalized)

        if not orphan_entity or not parent_entity:
            continue

        print(f"    {orphan_entity.name[:40]} -> {parent_entity.name[:30]} [{confidence}]")

        if save_db:
            # Update parent_id
            if orphan_entity.parent_id != parent_entity.id:
                orphan_entity.parent_id = parent_entity.id
                updated += 1

            # Check/create ownership_link
            existing = await db.scalar(
                select(OwnershipLink).where(
                    OwnershipLink.child_entity_id == orphan_entity.id
                )
            )

            if existing:
                existing.parent_entity_id = parent_entity.id
                existing.ownership_type = ownership_type
            else:
                link = OwnershipLink(
                    parent_entity_id=parent_entity.id,
                    child_entity_id=orphan_entity.id,
                    ownership_pct=100.0,
                    ownership_type=ownership_type,
                )
                db.add(link)
                created += 1

    if save_db:
        await db.commit()
        print(f"  Updated {updated} entities, created {created} links")

    return {
        "status": "success",
        "orphans": len(orphans),
        "assignments": len(assignments),
        "updated": updated,
        "created": created,
    }


async def main():
    parser = argparse.ArgumentParser(description="Extract parent relationships for orphan entities")
    parser.add_argument("--ticker", type=str, help="Process single company")
    parser.add_argument("--top", type=int, default=10, help="Process top N companies with most orphans")
    parser.add_argument("--save-db", action="store_true", help="Save changes to database")

    args = parser.parse_args()

    settings = get_settings()

    if not settings.gemini_api_key:
        print("Error: GEMINI_API_KEY not set")
        return

    extractor = GeminiParentExtractor(settings.gemini_api_key)

    print_header("ORPHAN PARENT EXTRACTION")
    print(f"Mode: {'SAVE TO DB' if args.save_db else 'DRY RUN'}")

    async with get_db_session() as db:
        if args.ticker:
            company = await db.scalar(
                select(Company).where(Company.ticker == args.ticker.upper())
            )
            if not company:
                print(f"Company {args.ticker} not found")
                return
            await process_company(db, company, extractor, args.save_db)
        else:
            # Get top companies with orphan guarantors
            result = await db.execute(
                select(Company, func.count(Entity.id).label("orphan_count"))
                .join(Entity, Entity.company_id == Company.id)
                .join(Guarantee, Guarantee.guarantor_id == Entity.id)
                .where(Entity.parent_id.is_(None))
                .group_by(Company.id)
                .order_by(func.count(Entity.id).desc())
                .limit(args.top)
            )

            companies = [(c, count) for c, count in result]
            print(f"Processing top {len(companies)} companies with orphan guarantors")

            for company, orphan_count in companies:
                try:
                    await process_company(db, company, extractor, args.save_db)
                except Exception as e:
                    print(f"  Error: {e}")

                # Rate limit
                await asyncio.sleep(1)

    print("\n" + "=" * 70)
    print("COMPLETE")


if __name__ == "__main__":
    run_async(main())
