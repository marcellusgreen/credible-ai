#!/usr/bin/env python3
"""
Extract intermediate parent-child ownership relationships using LLM (Gemini Flash).

Reads indentures, credit agreements, and guarantor lists to find actual corporate
structure (e.g., "CCO Holdings Capital Corp., a wholly-owned subsidiary of CCO Holdings, LLC").

SAFETY: This script is strictly additive — it only assigns parents to orphan entities
(parent_id IS NULL, is_root=false). Entities that already have a parent_id are NEVER
modified, even if set during the same run by a prior batch.

Usage:
    # Analyze orphan counts and document availability
    python scripts/extract_intermediate_ownership.py --analyze

    # Single company (dry run)
    python scripts/extract_intermediate_ownership.py --ticker CHTR

    # Single company (save to database)
    python scripts/extract_intermediate_ownership.py --ticker CHTR --save

    # All companies with orphans (dry run)
    python scripts/extract_intermediate_ownership.py --all

    # All companies (save to database)
    python scripts/extract_intermediate_ownership.py --all --save

    # Custom batch size for large companies
    python scripts/extract_intermediate_ownership.py --all --save --batch-size 150
"""

import asyncio
import re
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import google.generativeai as genai
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from script_utils import (
    create_fix_parser,
    get_db_session,
    print_header,
    print_subheader,
    print_summary,
    run_async,
)
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

ORPHAN ENTITIES (need to determine their direct parent):
{orphan_list}

KNOWN PARENTS (entities that already have a parent assigned or are the root — use these as possible parents for orphans):
{known_parent_list}

DOCUMENT CONTENT:
{document_content}

TASK: Find the DIRECT parent-child relationships for the ORPHAN ENTITIES listed above.

Look for patterns like:
- "X, a wholly-owned subsidiary of Y"
- "X, a direct subsidiary of Y"
- "X is a subsidiary of Y"
- "Y, the direct parent of X"
- "X is owned by Y"
- Organizational structure descriptions
- Guarantor hierarchy descriptions

CRITICAL: We need to find which entity is the DIRECT parent of each orphan — not the ultimate parent.

For example, if the documents say:
- "CCO Holdings Capital Corp., a wholly-owned subsidiary of CCO Holdings, LLC"
- "CCO Holdings, LLC, a subsidiary of Charter Communications, Inc."

Then CCO Holdings Capital Corp's direct parent is CCO Holdings, LLC (NOT Charter Communications, Inc.)

The direct_parent can be ANY entity from either the ORPHAN or KNOWN PARENTS list.

Return JSON:
{{
  "ownership_chain": [
    {{
      "child": "EXACT entity name from the ORPHAN list",
      "direct_parent": "EXACT entity name from ORPHAN or KNOWN PARENTS list",
      "evidence": "Quote showing the direct relationship"
    }}
  ],
  "root_entity": "Name of the ultimate parent company (top of hierarchy)",
  "notes": "Any observations about the corporate structure"
}}

RULES:
1. child MUST be an EXACT match to a name in the ORPHAN ENTITIES list
2. direct_parent MUST be an EXACT match to a name in either list
3. Only include relationships where you find DIRECT parent evidence in the documents
4. Do NOT assume the root company is the direct parent unless explicitly stated
5. If entity A is subsidiary of B, and B is subsidiary of C, report A->B and B->C separately
6. Skip any relationship you're not confident about

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
        orphan_list: str,
        known_parent_list: str,
        document_content: str,
    ) -> dict:
        """Extract ownership hierarchy from documents."""

        prompt = EXTRACT_HIERARCHY_PROMPT.format(
            company_name=company_name,
            ticker=ticker,
            orphan_list=orphan_list,
            known_parent_list=known_parent_list,
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
    """Normalize entity name for matching."""
    if not name:
        return ""
    return name.lower().replace(',', '').replace('.', '').replace("'", "").strip()


def match_entity_name(name: str, entity_by_name: dict) -> Optional[Entity]:
    """Find matching entity by name (exact then partial/substring, no fuzzy)."""
    if not name:
        return None

    normalized = normalize_name(name)

    # Direct match
    if normalized in entity_by_name:
        return entity_by_name[normalized]

    # Partial match (substring containment)
    for key, entity in entity_by_name.items():
        if normalized in key or key in normalized:
            return entity

    return None


async def get_orphan_entities(db: AsyncSession, company_id: UUID) -> list[Entity]:
    """Get orphan entities: parent_id IS NULL AND is_root = false."""
    result = await db.execute(
        select(Entity).where(
            and_(
                Entity.company_id == company_id,
                Entity.parent_id.is_(None),
                Entity.is_root.is_(False),
            )
        ).order_by(Entity.name)
    )
    return list(result.scalars())


async def get_all_entities(db: AsyncSession, company_id: UUID) -> list[Entity]:
    """Get all entities for a company (for lookup and known parents)."""
    result = await db.execute(
        select(Entity).where(Entity.company_id == company_id)
    )
    return list(result.scalars())


async def get_document_content(db: AsyncSession, company_id: UUID) -> str:
    """Get relevant document sections for hierarchy extraction."""
    content_parts = []

    # Priority: prospectus (explicit ownership), credit_agreement, indenture, guarantor_list
    section_types = ['prospectus', 'credit_agreement', 'indenture', 'guarantor_list']

    for section_type in section_types:
        sections = await db.execute(
            select(DocumentSection)
            .where(DocumentSection.company_id == company_id)
            .where(DocumentSection.section_type == section_type)
            .order_by(DocumentSection.content_length.desc())  # Largest first
            .limit(5)
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
        return '\n\n---\n\n'.join(extracted_parts[:50])

    return content[:40000]


async def get_document_section_counts(db: AsyncSession, company_id: UUID) -> dict:
    """Count document sections by type for a company."""
    result = await db.execute(
        select(
            DocumentSection.section_type,
            func.count(DocumentSection.id),
        )
        .where(DocumentSection.company_id == company_id)
        .where(
            DocumentSection.section_type.in_(
                ['prospectus', 'credit_agreement', 'indenture', 'guarantor_list']
            )
        )
        .group_by(DocumentSection.section_type)
    )
    return dict(result.fetchall())


# =============================================================================
# MAIN PROCESSING
# =============================================================================

def build_entity_lookup(entities: list[Entity]) -> dict:
    """Build normalized name → Entity lookup from a list of entities."""
    entity_by_name = {}
    for e in entities:
        entity_by_name[normalize_name(e.name)] = e
        if e.legal_name:
            entity_by_name[normalize_name(e.legal_name)] = e
    return entity_by_name


def format_entity_list(entities: list[Entity]) -> str:
    """Format entity list for LLM prompt."""
    return "\n".join(
        f"- {e.name} (tier: {e.structure_tier}, type: {e.entity_type})"
        for e in entities
    )


async def process_company(
    company_id: UUID,
    company_name: str,
    ticker: str,
    extractor: GeminiHierarchyExtractor,
    save: bool = False,
    verbose: bool = False,
    batch_size: int = 200,
) -> dict:
    """Process a single company to extract intermediate ownership for orphans only.

    Opens a fresh DB session per company (Neon serverless pattern).
    """

    print(f"\n[{ticker}] {company_name}")

    stats = {
        "orphans": 0,
        "relationships_found": 0,
        "parents_assigned": 0,
        "links_created": 0,
        "skipped_already_set": 0,
        "skipped_tier_mismatch": 0,
        "skipped_no_match": 0,
        "skipped_self_ref": 0,
        "batches": 0,
    }

    # Fresh session for this company
    async with get_db_session() as db:
        # Get orphans only
        orphans = await get_orphan_entities(db, company_id)

        if not orphans:
            print("  No orphan entities, skipping")
            return {"status": "skipped", "reason": "no_orphans", "stats": stats}

        stats["orphans"] = len(orphans)
        print(f"  {len(orphans)} orphan entities")

        # Get all entities (for lookup + known parents list)
        all_entities = await get_all_entities(db, company_id)
        entity_by_id = {e.id: e for e in all_entities}

        # Build lookup from ALL entities (orphans can point to any entity as parent)
        entity_by_name = build_entity_lookup(all_entities)

        # Known parents = entities with parent_id set OR is_root=True
        known_parents = [
            e for e in all_entities
            if e.parent_id is not None or e.is_root
        ]

        # Get document content
        content = await get_document_content(db, company_id)
        if not content or len(content) < 1000:
            print("  Insufficient document content")
            return {"status": "skipped", "reason": "no_documents", "stats": stats}

        print(f"  Document content: {len(content):,} chars")
        print(f"  Known parents: {len(known_parents)}, Orphans: {len(orphans)}")

        # Format known parents list (same for all batches)
        known_parent_list = format_entity_list(known_parents)

        # Batch orphans if too many
        orphan_batches = []
        for i in range(0, len(orphans), batch_size):
            orphan_batches.append(orphans[i:i + batch_size])

        if len(orphan_batches) > 1:
            print(f"  Splitting {len(orphans)} orphans into {len(orphan_batches)} batches of ~{batch_size}")

        all_results = []

        for batch_idx, batch in enumerate(orphan_batches):
            stats["batches"] += 1

            if len(orphan_batches) > 1:
                print(f"  Batch {batch_idx + 1}/{len(orphan_batches)} ({len(batch)} orphans)")

            orphan_list = format_entity_list(batch)

            # Call LLM
            print("  Calling Gemini...")
            result = await extractor.extract_hierarchy(
                company_name=company_name,
                ticker=ticker,
                orphan_list=orphan_list,
                known_parent_list=known_parent_list,
                document_content=content,
            )

            ownership_chain = result.get("ownership_chain", []) or []
            print(f"  Found {len(ownership_chain)} relationships in this batch")
            stats["relationships_found"] += len(ownership_chain)

            if result.get("notes") and verbose:
                print(f"  Notes: {result['notes'][:200]}")

            all_results.extend(ownership_chain)

            # Rate limit between batches
            if batch_idx < len(orphan_batches) - 1:
                await asyncio.sleep(1)

        # Process all results — batch commits every 25 to avoid Neon idle timeout
        COMMIT_BATCH_SIZE = 25
        pending_saves = 0

        for item in all_results:
            if not item:
                continue

            try:
                child_name = item.get("child", "") or ""
                parent_name = item.get("direct_parent", "") or ""
                evidence = item.get("evidence", "") or ""

                child_entity = match_entity_name(child_name, entity_by_name)
                parent_entity = match_entity_name(parent_name, entity_by_name)

                if not child_entity:
                    if verbose:
                        print(f"    NO MATCH (child): '{child_name[:50]}'")
                    stats["skipped_no_match"] += 1
                    continue
                if not parent_entity:
                    if verbose:
                        print(f"    NO MATCH (parent): '{parent_name[:50]}'")
                    stats["skipped_no_match"] += 1
                    continue

                if child_entity.id == parent_entity.id:
                    stats["skipped_self_ref"] += 1
                    continue

                # SAFETY CHECK: Re-verify child is still an orphan before writing
                # (another batch within the same run may have set it)
                if child_entity.parent_id is not None:
                    if verbose:
                        print(f"    SKIP (already has parent): {child_entity.name[:40]}")
                    stats["skipped_already_set"] += 1
                    continue

                # Tier check: parent should be at a higher tier (lower number) than child
                if parent_entity.structure_tier and child_entity.structure_tier:
                    if parent_entity.structure_tier >= child_entity.structure_tier:
                        if verbose:
                            print(f"    SKIP (tier mismatch): {child_entity.name[:30]} tier {child_entity.structure_tier} -> {parent_entity.name[:30]} tier {parent_entity.structure_tier}")
                        stats["skipped_tier_mismatch"] += 1
                        continue

                # Log the assignment
                print(f"    {child_entity.name[:40]} -> {parent_entity.name[:35]}")
                if verbose and evidence:
                    print(f"      Evidence: {evidence[:120]}")

                if save:
                    # SAFETY: Double-check by re-fetching from DB
                    fresh_child = await db.get(Entity, child_entity.id)
                    if not fresh_child or fresh_child.parent_id is not None:
                        if verbose:
                            print(f"      SKIP (parent_id set in DB): {child_entity.name[:40]}")
                        stats["skipped_already_set"] += 1
                        continue

                    # Set parent
                    fresh_child.parent_id = parent_entity.id

                    # Tag attributes
                    attrs = dict(fresh_child.attributes or {})
                    attrs["intermediate_source"] = "llm_gemini"
                    attrs["intermediate_enrichment_date"] = datetime.now(timezone.utc).isoformat()
                    attrs["intermediate_parent_name"] = parent_entity.name
                    attrs["intermediate_evidence"] = evidence[:500]
                    fresh_child.attributes = attrs

                    # Also update in-memory entity so future iterations see the change
                    child_entity.parent_id = parent_entity.id

                    stats["parents_assigned"] += 1
                    pending_saves += 1

                    # Create OwnershipLink
                    existing_link = await db.scalar(
                        select(OwnershipLink).where(
                            OwnershipLink.child_entity_id == child_entity.id
                        )
                    )

                    if not existing_link:
                        link = OwnershipLink(
                            parent_entity_id=parent_entity.id,
                            child_entity_id=child_entity.id,
                            ownership_type="direct",
                            attributes={
                                "source": "intermediate_llm",
                                "extracted_at": datetime.now(timezone.utc).isoformat(),
                                "evidence": evidence[:500],
                            },
                        )
                        db.add(link)
                        stats["links_created"] += 1
                    elif verbose:
                        print(f"      OwnershipLink already exists, skipping link creation")

                    # Batch commit to avoid Neon idle timeout on large companies
                    if pending_saves >= COMMIT_BATCH_SIZE:
                        await db.commit()
                        pending_saves = 0

                else:
                    # Dry run: still update in-memory for consistent counting
                    child_entity.parent_id = parent_entity.id
                    stats["parents_assigned"] += 1

            except Exception as e:
                print(f"    Error processing relationship: {e}")
                # Try to recover the session for remaining items
                if save:
                    try:
                        await db.rollback()
                        pending_saves = 0
                    except Exception:
                        pass

        if save:
            if pending_saves > 0:
                await db.commit()
            print(f"  Saved: {stats['parents_assigned']} parent assignments, {stats['links_created']} ownership links")
        else:
            print(f"  Would assign: {stats['parents_assigned']} parents (dry run)")

    return {
        "status": "success",
        "stats": stats,
    }


# =============================================================================
# ANALYZE MODE
# =============================================================================

async def run_analyze(ticker: Optional[str] = None, limit: int = 0):
    """Show orphan counts and document availability per company."""

    print_header("INTERMEDIATE OWNERSHIP - ANALYSIS")

    async with get_db_session() as db:
        # Total orphans across all companies
        total_orphans = await db.scalar(
            select(func.count(Entity.id)).where(
                and_(Entity.parent_id.is_(None), Entity.is_root.is_(False))
            )
        )
        total_entities = await db.scalar(select(func.count(Entity.id)))
        total_with_parent = await db.scalar(
            select(func.count(Entity.id)).where(Entity.parent_id.isnot(None))
        )

        print(f"Total entities: {total_entities:,}")
        print(f"  With parent_id set: {total_with_parent:,}")
        print(f"  Orphans (parent_id=NULL, is_root=false): {total_orphans:,}")
        print()

        # Get companies with orphans
        query = (
            select(
                Company.id,
                Company.ticker,
                Company.name,
                func.count(Entity.id).label("orphan_count"),
            )
            .join(Entity, Entity.company_id == Company.id)
            .where(
                and_(
                    Entity.parent_id.is_(None),
                    Entity.is_root.is_(False),
                )
            )
            .group_by(Company.id, Company.ticker, Company.name)
            .order_by(func.count(Entity.id).desc())
        )

        if ticker:
            query = query.where(Company.ticker == ticker.upper())
        if limit and limit > 0:
            query = query.limit(limit)

        result = await db.execute(query)
        companies = result.fetchall()

        print(f"Companies with orphans: {len(companies)}")
        print()

        print_subheader("ORPHANS AND DOCUMENTS BY COMPANY")
        print(f"{'Ticker':<8} {'Orphans':>8} {'CreditAg':>10} {'Indenture':>10} {'GuarantL':>10}  Name")
        print("-" * 90)

        total_docs = 0
        for row in companies:
            doc_counts = await get_document_section_counts(db, row.id)
            ca = doc_counts.get('credit_agreement', 0)
            ind = doc_counts.get('indenture', 0)
            gl = doc_counts.get('guarantor_list', 0)
            total_docs += ca + ind + gl
            print(f"{row.ticker:<8} {row.orphan_count:>8} {ca:>10} {ind:>10} {gl:>10}  {row.name[:40]}")

        print("-" * 90)
        total_orphan_count = sum(r.orphan_count for r in companies)
        print(f"{'TOTAL':<8} {total_orphan_count:>8} {total_docs:>10} sections across all types")


# =============================================================================
# GET COMPANIES
# =============================================================================

async def get_companies_with_orphans(
    db: AsyncSession,
    ticker: Optional[str] = None,
    limit: int = 0,
) -> list[tuple]:
    """Get companies that have orphan entities, returning (id, ticker, name)."""
    query = (
        select(
            Company.id,
            Company.ticker,
            Company.name,
        )
        .join(Entity, Entity.company_id == Company.id)
        .where(
            and_(
                Entity.parent_id.is_(None),
                Entity.is_root.is_(False),
            )
        )
        .group_by(Company.id, Company.ticker, Company.name)
        .having(func.count(Entity.id) >= 1)
        .order_by(Company.ticker)
    )

    if ticker:
        query = query.where(Company.ticker == ticker.upper())
    if limit and limit > 0:
        query = query.limit(limit)

    result = await db.execute(query)
    return result.fetchall()


# =============================================================================
# MAIN
# =============================================================================

async def main():
    parser = create_fix_parser("Extract intermediate ownership relationships via LLM")
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Show orphan statistics and document availability",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Max orphans per LLM batch (default: 200)",
    )

    args = parser.parse_args()

    # Analysis mode
    if args.analyze:
        await run_analyze(args.ticker, args.limit)
        return

    if not args.ticker and not getattr(args, "all", False):
        print("Error: Must specify --ticker, --all, or --analyze")
        return

    settings = get_settings()

    if not settings.gemini_api_key:
        print("Error: GEMINI_API_KEY not set")
        return

    extractor = GeminiHierarchyExtractor(settings.gemini_api_key)

    print_header("INTERMEDIATE OWNERSHIP EXTRACTION (LLM)")
    print(f"Mode: {'SAVE TO DB' if args.save else 'DRY RUN'}")
    print(f"Batch size: {args.batch_size}")
    print()

    # Get companies with orphans (in a session that closes before processing)
    async with get_db_session() as db:
        companies = await get_companies_with_orphans(db, args.ticker, args.limit)

    if not companies:
        if args.ticker:
            print(f"No orphan entities found for {args.ticker}")
        else:
            print("No companies with orphan entities found")
        return

    print(f"Found {len(companies)} companies with orphan entities")

    total_stats = {
        "companies_processed": 0,
        "companies_skipped": 0,
        "total_orphans": 0,
        "total_relationships_found": 0,
        "total_parents_assigned": 0,
        "total_links_created": 0,
        "total_batches": 0,
    }

    for row in companies:
        try:
            result = await process_company(
                company_id=row.id,
                company_name=row.name,
                ticker=row.ticker,
                extractor=extractor,
                save=args.save,
                verbose=args.verbose,
                batch_size=args.batch_size,
            )

            if result["status"] == "success":
                total_stats["companies_processed"] += 1
                s = result["stats"]
                total_stats["total_orphans"] += s["orphans"]
                total_stats["total_relationships_found"] += s["relationships_found"]
                total_stats["total_parents_assigned"] += s["parents_assigned"]
                total_stats["total_links_created"] += s["links_created"]
                total_stats["total_batches"] += s["batches"]
            else:
                total_stats["companies_skipped"] += 1

        except Exception as e:
            print(f"  Error: {e}")
            import traceback
            traceback.print_exc()
            total_stats["companies_skipped"] += 1

        # Rate limit between companies
        await asyncio.sleep(1)

    # Summary
    print_summary({
        "Companies processed": total_stats["companies_processed"],
        "Companies skipped": total_stats["companies_skipped"],
        "Total orphans analyzed": total_stats["total_orphans"],
        "LLM batches": total_stats["total_batches"],
        "Relationships found by LLM": total_stats["total_relationships_found"],
        "Parents assigned": total_stats["total_parents_assigned"] if args.save else f"{total_stats['total_parents_assigned']} (dry run)",
        "Ownership links created": total_stats["total_links_created"] if args.save else "N/A (dry run)",
    })


if __name__ == "__main__":
    run_async(main())
