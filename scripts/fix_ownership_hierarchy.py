#!/usr/bin/env python3
"""
Fix ownership hierarchy using ONLY explicit ownership statements from SEC filings.

No inferences - only extracts relationships that are explicitly stated in documents.

Usage:
    # Single company (dry run)
    python scripts/fix_ownership_hierarchy.py --ticker CHTR

    # Single company (save to database)
    python scripts/fix_ownership_hierarchy.py --ticker CHTR --save-db

    # All companies
    python scripts/fix_ownership_hierarchy.py --all --save-db
"""

import argparse
import asyncio
import os
import re
import sys
from typing import Optional
from uuid import UUID

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import google.generativeai as genai
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models import (
    Company,
    DocumentSection,
    Entity,
    OwnershipLink,
)
from app.services.qa_agent import parse_json_robust


# =============================================================================
# PROMPTS
# =============================================================================

EXTRACT_HIERARCHY_PROMPT = """You are analyzing SEC filings to extract EXPLICIT corporate ownership statements.

COMPANY: {company_name} ({ticker})

ENTITIES:
{entity_list}

DOCUMENT CONTENT:
{document_content}

TASK: Find ONLY relationships that are EXPLICITLY stated in the documents.

Valid evidence patterns:
- "X, a wholly-owned subsidiary of Y"
- "X, a direct subsidiary of Y"
- "X, an indirect subsidiary of Y"
- "X is a subsidiary of Y"
- "Y, the direct parent of X"
- "X is owned by Y"
- "Y owns X"
- "The Issuer is a direct wholly-owned subsidiary of Holdings"

DO NOT INCLUDE:
- Inferred relationships based on naming patterns
- Assumed relationships based on corporate structure
- Relationships where you're guessing based on context

Return JSON:
{{
  "relationships": [
    {{
      "child": "EXACT entity name from list",
      "parent": "EXACT entity name from list",
      "ownership_type": "direct" or "indirect" or null,
      "evidence": "EXACT quote from document showing this relationship"
    }}
  ],
  "notes": "Any observations"
}}

RULES:
1. Names must EXACTLY match the entity list
2. Evidence must be a direct quote, not a summary
3. Only include relationships with explicit documentary evidence
4. If you cannot find explicit evidence, return empty relationships array
5. ownership_type:
   - "direct" ONLY if evidence explicitly says "direct subsidiary" or "direct parent"
   - "indirect" ONLY if evidence explicitly says "indirect subsidiary"
   - null if evidence just says "subsidiary" without specifying direct/indirect

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
                "temperature": 0.0,
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
            return {"relationships": [], "notes": f"Error: {e}"}


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
    """Get all relevant document sections."""
    content_parts = []

    # Get all document types that might have ownership info
    section_types = ['credit_agreement', 'indenture', 'guarantor_list', 'exhibit_21', 'exhibit_22']

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
            # Extract ownership-related paragraphs from large docs
            if section.content_length > 50000:
                content_parts.append(extract_ownership_paragraphs(section.content))
            else:
                content_parts.append(section.content)

        if sum(len(p) for p in content_parts) > 100000:
            break

    return "\n".join(content_parts)


def extract_ownership_paragraphs(content: str) -> str:
    """Extract paragraphs containing explicit ownership language."""
    extracted = []

    # Only look for explicit ownership patterns
    ownership_patterns = [
        r'wholly.?owned\s+subsidiary',
        r'direct\s+subsidiary',
        r'indirect\s+subsidiary',
        r'subsidiary\s+of',
        r'parent\s+of',
        r'owned\s+by',
        r'owns\s+\d+%',
        r'100%\s+owned',
    ]

    lines = content.split('\n')
    current_para = []

    for line in lines:
        if line.strip():
            current_para.append(line)
        else:
            if current_para:
                para_text = ' '.join(current_para)
                para_lower = para_text.lower()
                if any(re.search(pat, para_lower) for pat in ownership_patterns):
                    extracted.append(para_text[:3000])
                current_para = []

    if current_para:
        para_text = ' '.join(current_para)
        para_lower = para_text.lower()
        if any(re.search(pat, para_lower) for pat in ownership_patterns):
            extracted.append(para_text[:3000])

    if extracted:
        return '\n\n---\n\n'.join(extracted[:50])

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
    """Process a single company to fix ownership hierarchy."""

    print(f"\n[{company.ticker}] {company.name}")

    # Get all entities
    entities_result = await db.execute(
        select(Entity).where(Entity.company_id == company.id)
    )
    entities = list(entities_result.scalars())

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

    # Get document content
    content = await get_document_content(db, company.id)
    if not content or len(content) < 500:
        print("  No document content")
        return {"status": "skipped", "reason": "no_documents"}

    print(f"  Document content: {len(content):,} chars")

    # Format entity list
    entity_list = "\n".join([f"- {e.name}" for e in entities])

    # Call LLM
    print("  Extracting explicit ownership statements...")
    result = await extractor.extract_hierarchy(
        company_name=company.name,
        ticker=company.ticker,
        entity_list=entity_list,
        document_content=content,
    )

    relationships = result.get("relationships", []) or []
    print(f"  Found {len(relationships)} explicit relationships")

    if result.get("notes"):
        print(f"  Notes: {result['notes'][:100]}")

    stats = {"updated": 0, "links_updated": 0}

    for item in relationships:
        if not item:
            continue

        child_name = item.get("child", "") or ""
        parent_name = item.get("parent", "") or ""
        evidence = item.get("evidence", "") or ""
        ownership_type = item.get("ownership_type")  # Can be "direct", "indirect", or None

        child_entity = match_entity_name(child_name, entity_by_name)
        parent_entity = match_entity_name(parent_name, entity_by_name)

        if not child_entity:
            print(f"    NO MATCH (child): '{child_name[:40]}'")
            continue
        if not parent_entity:
            print(f"    NO MATCH (parent): '{parent_name[:40]}'")
            continue

        if child_entity.id == parent_entity.id:
            continue

        # Validate tier if available
        if parent_entity.structure_tier and child_entity.structure_tier:
            if parent_entity.structure_tier >= child_entity.structure_tier:
                print(f"    SKIP (tier mismatch): {child_entity.name[:30]} tier {child_entity.structure_tier} -> {parent_entity.name[:30]} tier {parent_entity.structure_tier}")
                continue

        current_parent = entity_by_id.get(child_entity.parent_id)
        current_parent_name = current_parent.name[:25] if current_parent else "None"

        type_str = f" [{ownership_type}]" if ownership_type else ""
        print(f"    {child_entity.name[:35]} -> {parent_entity.name[:25]}{type_str}")
        print(f"      Evidence: \"{evidence[:80]}...\"")
        if current_parent_name != parent_entity.name[:25]:
            print(f"      (was: {current_parent_name})")

        if save_db:
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
                # Only update ownership_type if we have explicit evidence
                if ownership_type:
                    existing_link.ownership_type = ownership_type
                else:
                    existing_link.ownership_type = None  # Unknown
            else:
                link = OwnershipLink(
                    parent_entity_id=parent_entity.id,
                    child_entity_id=child_entity.id,
                    ownership_pct=100,
                    ownership_type=ownership_type,  # Can be None if not explicitly stated
                )
                db.add(link)
            stats["links_updated"] += 1

    if save_db:
        await db.commit()
        print(f"\n  Saved: {stats['updated']} updates, {stats['links_updated']} links")

    return {
        "status": "success",
        "entities": len(entities),
        "relationships_found": len(relationships),
        "stats": stats,
    }


async def get_companies_to_process(db: AsyncSession, ticker: Optional[str] = None, limit: Optional[int] = None) -> list[Company]:
    """Get companies to process."""
    if ticker:
        company = await db.scalar(select(Company).where(Company.ticker == ticker.upper()))
        return [company] if company else []

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
    parser = argparse.ArgumentParser(description="Fix ownership hierarchy (explicit statements only)")
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

    engine = create_async_engine(
        settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
    )
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    extractor = GeminiHierarchyExtractor(settings.gemini_api_key)

    print("=" * 70)
    print("FIX OWNERSHIP HIERARCHY (Explicit Statements Only)")
    print("=" * 70)
    print(f"Mode: {'SAVE TO DB' if args.save_db else 'DRY RUN'}")

    total_stats = {
        "companies_processed": 0,
        "companies_skipped": 0,
        "relationships_found": 0,
        "updated": 0,
    }

    async with async_session() as db:
        companies = await get_companies_to_process(db, args.ticker, args.limit)
        print(f"Found {len(companies)} companies to process")

    for company in companies:
        try:
            # Use fresh session for each company to avoid state issues
            async with async_session() as db:
                result = await process_company(db, company, extractor, args.save_db)

                if result["status"] == "success":
                    total_stats["companies_processed"] += 1
                    total_stats["relationships_found"] += result.get("relationships_found", 0)
                    stats = result.get("stats", {})
                    total_stats["updated"] += stats.get("updated", 0)
                else:
                    total_stats["companies_skipped"] += 1

        except Exception as e:
            print(f"  Error: {e}")
            import traceback
            traceback.print_exc()
            total_stats["companies_skipped"] += 1

        await asyncio.sleep(1)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Companies processed: {total_stats['companies_processed']}")
    print(f"Companies skipped: {total_stats['companies_skipped']}")
    print(f"Explicit relationships found: {total_stats['relationships_found']}")
    print(f"Parent updates applied: {total_stats['updated']}")


if __name__ == "__main__":
    asyncio.run(main())
