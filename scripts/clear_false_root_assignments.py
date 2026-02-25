#!/usr/bin/env python3
"""
Clear false parent_id=root assignments for entities with no evidence.

Analysis revealed ~12,009 entities across ~93 companies have parent_id = root_entity_id
with NO evidence — they were default-assigned by hierarchy_extraction.py when parsing
flat Exhibit 21 lists (no indentation). Because all enrichment scripts filter on
parent_id IS NULL, these companies were entirely skipped by GLEIF, UK Companies House,
prospectus fetch, and intermediate ownership LLM extraction.

Entities with evidence (intermediate_source, gleif_lei, companies_house_number, or
key entity status) are preserved — approximately 2,589 such entities.

This script sets parent_id = NULL and deletes corresponding OwnershipLink rows for
entities where the root assignment has no supporting evidence, making them eligible
for enrichment.

Usage:
    # Analyze — show per-company breakdown of false root assignments
    python scripts/clear_false_root_assignments.py --analyze

    # Single company dry run
    python scripts/clear_false_root_assignments.py --ticker O

    # Single company dry run with entity names
    python scripts/clear_false_root_assignments.py --ticker O --verbose

    # Single company, persist to DB
    python scripts/clear_false_root_assignments.py --ticker O --save

    # All companies, dry run
    python scripts/clear_false_root_assignments.py --all

    # All companies, persist
    python scripts/clear_false_root_assignments.py --all --save

    # Limit to first N companies
    python scripts/clear_false_root_assignments.py --all --limit 10
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, func, and_, delete

from script_utils import (
    create_fix_parser,
    get_db_session,
    get_all_companies,
    get_company_by_ticker,
    print_header,
    print_subheader,
    print_summary,
    print_progress,
    run_async,
)
from app.models import Company, Entity, OwnershipLink


# =============================================================================
# EVIDENCE CHECKS
# =============================================================================

# Attribute keys that indicate evidence for the root assignment
EVIDENCE_ATTRIBUTE_KEYS = [
    "intermediate_source",
    "gleif_lei",
    "gleif_parent_lei",
    "companies_house_number",
]


def entity_has_evidence(entity) -> str | None:
    """
    Check if an entity's root assignment has supporting evidence.

    Returns a string describing the evidence source, or None if no evidence.
    """
    attrs = entity.attributes or {}

    # Check JSONB attributes for enrichment evidence
    for key in EVIDENCE_ATTRIBUTE_KEYS:
        if attrs.get(key):
            return f"attribute:{key}"

    # Key entities: structure_tier <= 2 means holdco (1) or intermediate (2)
    if entity.structure_tier is not None and entity.structure_tier <= 2:
        return f"structure_tier:{entity.structure_tier}"

    # Check if entity is a guarantor or borrower (key entity)
    if entity.is_guarantor:
        return "is_guarantor"
    if entity.is_borrower:
        return "is_borrower"

    return None


def ownership_link_has_evidence(link) -> bool:
    """Check if an OwnershipLink has source attribution."""
    attrs = link.attributes or {}

    # Check for any meaningful source
    source = attrs.get("source", "")
    if source and source not in ("", "no_attributes", "exhibit_21", "hierarchy_extraction"):
        return True

    # Check for specific evidence keys
    for key in ["gleif_child_lei", "gleif_parent_lei", "ch_company_number",
                 "psc_name", "evidence", "intermediate_source"]:
        if attrs.get(key):
            return True

    return False


# =============================================================================
# CORE PROCESSING
# =============================================================================

async def get_root_entities(db, company_id: UUID) -> list:
    """Get all root entities for a company (some companies have multiple)."""
    result = await db.execute(
        select(Entity).where(
            and_(
                Entity.company_id == company_id,
                Entity.is_root.is_(True),
            )
        )
    )
    return result.scalars().all()


async def get_direct_to_root_entities(db, company_id: UUID, root_entity_ids: list[UUID]):
    """Get all non-root entities whose parent_id points directly to any root entity."""
    if not root_entity_ids:
        return []
    result = await db.execute(
        select(Entity).where(
            and_(
                Entity.company_id == company_id,
                Entity.parent_id.in_(root_entity_ids),
                Entity.is_root.is_(False),
            )
        ).order_by(Entity.name)
    )
    return result.scalars().all()


async def get_ownership_links_to_root(db, root_entity_ids: list[UUID], child_entity_ids: list[UUID]):
    """Get OwnershipLink rows where any root is parent and child is in the given list."""
    if not child_entity_ids or not root_entity_ids:
        return []
    result = await db.execute(
        select(OwnershipLink).where(
            and_(
                OwnershipLink.parent_entity_id.in_(root_entity_ids),
                OwnershipLink.child_entity_id.in_(child_entity_ids),
            )
        )
    )
    return result.scalars().all()


async def process_company(db, company_id: UUID, ticker: str, save: bool, verbose: bool) -> dict:
    """
    Process a single company: clear false root assignments.

    Returns stats dict with counts.
    """
    stats = {
        "total_direct_to_root": 0,
        "with_evidence": 0,
        "safe_to_clear": 0,
        "entities_cleared": 0,
        "links_deleted": 0,
    }

    # Get root entities (some companies have multiple)
    roots = await get_root_entities(db, company_id)
    if not roots:
        if verbose:
            print(f"  {ticker}: No root entity found, skipping")
        return stats

    root_ids = [r.id for r in roots]

    # Get all non-root entities pointing directly to any root
    direct_entities = await get_direct_to_root_entities(db, company_id, root_ids)
    stats["total_direct_to_root"] = len(direct_entities)

    if not direct_entities:
        return stats

    # Separate entities with evidence from those without
    entities_to_clear = []
    for entity in direct_entities:
        evidence = entity_has_evidence(entity)
        if evidence:
            stats["with_evidence"] += 1
            if verbose:
                print(f"    KEEP: {entity.name[:60]:<60}  ({evidence})")
        else:
            entities_to_clear.append(entity)

    stats["safe_to_clear"] = len(entities_to_clear)

    if not entities_to_clear:
        return stats

    if verbose:
        for entity in entities_to_clear[:20]:
            print(f"    CLEAR: {entity.name[:60]}")
        if len(entities_to_clear) > 20:
            print(f"    ... and {len(entities_to_clear) - 20} more")

    # Get ownership links to delete
    clear_ids = [e.id for e in entities_to_clear]
    links = await get_ownership_links_to_root(db, root_ids, clear_ids)

    # Filter links: only delete those without evidence
    links_to_delete = []
    links_kept = 0
    for link in links:
        if ownership_link_has_evidence(link):
            links_kept += 1
            if verbose:
                print(f"    KEEP LINK: {link.child_entity_id} (has source evidence)")
        else:
            links_to_delete.append(link)

    if save:
        # Batch clear entities — commit every 50 to avoid Neon timeout
        BATCH_SIZE = 50
        pending = 0

        for entity in entities_to_clear:
            entity.parent_id = None
            stats["entities_cleared"] += 1
            pending += 1

            if pending >= BATCH_SIZE:
                await db.commit()
                pending = 0

        # Delete ownership links
        for link in links_to_delete:
            await db.delete(link)
            stats["links_deleted"] += 1
            pending += 1

            if pending >= BATCH_SIZE:
                await db.commit()
                pending = 0

        # Final commit for remaining
        if pending > 0:
            await db.commit()
    else:
        stats["entities_cleared"] = len(entities_to_clear)
        stats["links_deleted"] = len(links_to_delete)

    return stats


# =============================================================================
# ANALYZE MODE
# =============================================================================

async def run_analyze():
    """Show per-company breakdown of false root assignments."""
    print_header("CLEAR FALSE ROOT ASSIGNMENTS - ANALYSIS")

    async with get_db_session() as db:
        # Get total entity counts for context
        total_entities = await db.scalar(select(func.count()).select_from(Entity))
        total_with_parent = await db.scalar(
            select(func.count()).select_from(Entity).where(Entity.parent_id.isnot(None))
        )
        total_orphans = await db.scalar(
            select(func.count()).select_from(Entity).where(
                and_(Entity.parent_id.is_(None), Entity.is_root.is_(False))
            )
        )
        total_roots = await db.scalar(
            select(func.count()).select_from(Entity).where(Entity.is_root.is_(True))
        )

        print(f"\nGlobal Entity Stats:")
        print(f"  Total entities:      {total_entities:,}")
        print(f"  Root entities:       {total_roots:,}")
        print(f"  With parent_id:      {total_with_parent:,}")
        print(f"  Orphans (no parent): {total_orphans:,}")

        # Get all companies with entities pointing to root
        result = await db.execute(
            select(
                Company.ticker,
                Company.id,
                Company.name,
            )
            .join(Entity, Entity.company_id == Company.id)
            .where(
                and_(
                    Entity.is_root.is_(False),
                    Entity.parent_id.isnot(None),
                )
            )
            .group_by(Company.id, Company.ticker, Company.name)
            .order_by(Company.ticker)
        )
        companies = result.all()

        print(f"\nAnalyzing {len(companies)} companies with non-root entities that have parents...")
        print()

        grand_total_direct = 0
        grand_with_evidence = 0
        grand_safe_to_clear = 0
        companies_with_clearable = 0

        rows = []

        for ticker, company_id, company_name in companies:
            roots = await get_root_entities(db, company_id)
            if not roots:
                continue

            root_ids = [r.id for r in roots]
            direct_entities = await get_direct_to_root_entities(db, company_id, root_ids)
            if not direct_entities:
                continue

            with_evidence = 0
            safe_to_clear = 0
            for entity in direct_entities:
                if entity_has_evidence(entity):
                    with_evidence += 1
                else:
                    safe_to_clear += 1

            total_direct = len(direct_entities)
            grand_total_direct += total_direct
            grand_with_evidence += with_evidence
            grand_safe_to_clear += safe_to_clear

            if safe_to_clear > 0:
                companies_with_clearable += 1
                rows.append((ticker, total_direct, with_evidence, safe_to_clear))

        # Print table sorted by safe_to_clear descending
        rows.sort(key=lambda r: r[3], reverse=True)

        print_subheader("Companies with False Root Assignments (Safe to Clear)")
        print(f"  {'Ticker':<8} {'Direct-Root':>12} {'With Evidence':>14} {'Safe to Clear':>14}")
        print(f"  {'-'*8} {'-'*12} {'-'*14} {'-'*14}")

        for ticker, total_direct, with_evidence, safe_to_clear in rows[:50]:
            print(f"  {ticker:<8} {total_direct:>12,} {with_evidence:>14,} {safe_to_clear:>14,}")

        if len(rows) > 50:
            print(f"  ... and {len(rows) - 50} more companies")

        print_summary({
            "Companies analyzed": len(companies),
            "Companies with clearable entities": companies_with_clearable,
            "Total direct-to-root (non-root)": f"{grand_total_direct:,}",
            "With evidence (KEEP)": f"{grand_with_evidence:,}",
            "Safe to clear": f"{grand_safe_to_clear:,}",
            "Current orphan count": f"{total_orphans:,}",
            "Projected orphan count": f"{total_orphans + grand_safe_to_clear:,}",
        })


# =============================================================================
# MAIN
# =============================================================================

async def main():
    parser = create_fix_parser(
        "Clear false parent_id=root assignments for entities with no evidence"
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Show per-company breakdown of false root assignments (no processing)"
    )
    args = parser.parse_args()

    # Analysis mode
    if args.analyze:
        await run_analyze()
        return

    # Require --ticker or --all
    if not args.ticker and not getattr(args, "all", False):
        print("Error: Must specify --ticker, --all, or --analyze")
        return

    mode = "SAVE TO DB" if args.save else "DRY RUN"
    print_header("CLEAR FALSE ROOT ASSIGNMENTS")
    print(f"Mode: {mode}")
    print()

    # Get company list
    async with get_db_session() as db:
        if args.ticker:
            company = await get_company_by_ticker(db, args.ticker)
            if not company:
                print(f"Company not found: {args.ticker}")
                return
            companies = [company]
        else:
            companies = await get_all_companies(db)
            if args.limit > 0:
                companies = companies[:args.limit]

        # Extract company info before closing session
        company_info = [(c.id, c.ticker, c.name) for c in companies]

    total = len(company_info)
    print(f"Processing {total} company(ies)...")
    print()

    # Aggregate stats
    total_stats = {
        "companies_processed": 0,
        "companies_with_clears": 0,
        "total_direct_to_root": 0,
        "with_evidence": 0,
        "entities_cleared": 0,
        "links_deleted": 0,
    }

    for i, (company_id, ticker, name) in enumerate(company_info):
        print_progress(i + 1, total, ticker)

        try:
            async with get_db_session() as db:
                stats = await process_company(
                    db=db,
                    company_id=company_id,
                    ticker=ticker,
                    save=args.save,
                    verbose=args.verbose,
                )

                total_stats["companies_processed"] += 1
                total_stats["total_direct_to_root"] += stats["total_direct_to_root"]
                total_stats["with_evidence"] += stats["with_evidence"]
                total_stats["entities_cleared"] += stats["entities_cleared"]
                total_stats["links_deleted"] += stats["links_deleted"]

                if stats["entities_cleared"] > 0:
                    total_stats["companies_with_clears"] += 1

                if stats["safe_to_clear"] > 0 or args.verbose:
                    action = "Cleared" if args.save else "Would clear"
                    print(f"\n  {ticker}: {action} {stats['entities_cleared']:,} entities "
                          f"(kept {stats['with_evidence']:,} with evidence, "
                          f"deleted {stats['links_deleted']:,} links)")

        except Exception as e:
            print(f"\n  Error processing {ticker}: {e}")

    print()
    print_summary({
        "Companies processed": total_stats["companies_processed"],
        "Companies with clears": total_stats["companies_with_clears"],
        "Total direct-to-root entities": f"{total_stats['total_direct_to_root']:,}",
        "Kept (with evidence)": f"{total_stats['with_evidence']:,}",
        "Entities cleared": f"{total_stats['entities_cleared']:,}" + (" (saved)" if args.save else " (dry run)"),
        "Links deleted": f"{total_stats['links_deleted']:,}" + (" (saved)" if args.save else " (dry run)"),
    })


if __name__ == "__main__":
    run_async(main())
