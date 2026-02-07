"""
Audit script for ownership data coverage.

Checks:
1. How many entities have parent_id set vs NULL
2. How deep is the hierarchy (max tier depth)
3. ownership_links table usage
4. Companies with flat vs nested structures
"""

from sqlalchemy import select, func, and_

from script_utils import get_db_session, print_header, run_async
from app.models import Company, Entity, OwnershipLink


async def audit_ownership():
    """Run ownership data audit."""
    async with get_db_session() as db:
        print_header("OWNERSHIP DATA AUDIT")

        # 1. Basic entity counts
        total_entities = await db.scalar(select(func.count()).select_from(Entity))
        total_companies = await db.scalar(select(func.count()).select_from(Company))

        print(f"\n[*] BASIC COUNTS")
        print(f"  Total companies: {total_companies}")
        print(f"  Total entities: {total_entities}")
        print(f"  Avg entities/company: {total_entities / total_companies:.1f}")

        # 2. Parent ID analysis
        entities_with_parent = await db.scalar(
            select(func.count()).select_from(Entity).where(Entity.parent_id.isnot(None))
        )
        entities_without_parent = total_entities - entities_with_parent

        print(f"\n[*] PARENT_ID COVERAGE")
        print(f"  Entities with parent_id: {entities_with_parent} ({entities_with_parent/total_entities*100:.1f}%)")
        print(f"  Entities without parent_id (roots): {entities_without_parent} ({entities_without_parent/total_entities*100:.1f}%)")

        # 3. Structure tier distribution
        tier_result = await db.execute(
            select(
                Entity.structure_tier,
                func.count(Entity.id).label("count")
            )
            .group_by(Entity.structure_tier)
            .order_by(Entity.structure_tier)
        )
        tiers = tier_result.all()

        print(f"\n[*] STRUCTURE TIER DISTRIBUTION")
        for tier, count in tiers:
            tier_label = {1: "HoldCo", 2: "Intermediate", 3: "OpCo", None: "Unknown"}.get(tier, f"Tier {tier}")
            print(f"  Tier {tier} ({tier_label}): {count} entities ({count/total_entities*100:.1f}%)")

        # 4. Ownership links analysis
        total_links = await db.scalar(select(func.count()).select_from(OwnershipLink))
        jv_links = await db.scalar(
            select(func.count()).select_from(OwnershipLink).where(OwnershipLink.is_joint_venture == True)
        )
        partial_ownership = await db.scalar(
            select(func.count()).select_from(OwnershipLink).where(
                and_(
                    OwnershipLink.ownership_pct.isnot(None),
                    OwnershipLink.ownership_pct < 100
                )
            )
        )

        print(f"\n[*] OWNERSHIP_LINKS TABLE")
        print(f"  Total ownership links: {total_links}")
        print(f"  Joint venture links: {jv_links}")
        print(f"  Partial ownership (<100%): {partial_ownership}")

        # 5. Ownership type distribution
        if total_links > 0:
            type_result = await db.execute(
                select(
                    OwnershipLink.ownership_type,
                    func.count(OwnershipLink.id).label("count")
                )
                .group_by(OwnershipLink.ownership_type)
                .order_by(func.count(OwnershipLink.id).desc())
            )
            types = type_result.all()

            print(f"\n[*] OWNERSHIP TYPE DISTRIBUTION")
            for otype, count in types:
                print(f"  {otype or 'NULL'}: {count}")

        # 6. Per-company hierarchy depth analysis
        # Get companies with their max entity tier
        depth_result = await db.execute(
            select(
                Company.ticker,
                func.count(Entity.id).label("entity_count"),
                func.max(Entity.structure_tier).label("max_tier"),
                func.count(Entity.id).filter(Entity.parent_id.isnot(None)).label("with_parent"),
            )
            .join(Entity, Entity.company_id == Company.id)
            .group_by(Company.id, Company.ticker)
            .order_by(func.count(Entity.id).desc())
            .limit(20)
        )
        companies = depth_result.all()

        print(f"\n[*] TOP 20 COMPANIES BY ENTITY COUNT (Hierarchy Analysis)")
        print(f"  {'Ticker':<8} {'Entities':>8} {'Max Tier':>9} {'With Parent':>12} {'Parent %':>10}")
        print(f"  {'-'*8} {'-'*8} {'-'*9} {'-'*12} {'-'*10}")

        for ticker, entity_count, max_tier, with_parent in companies:
            parent_pct = (with_parent / entity_count * 100) if entity_count > 0 else 0
            print(f"  {ticker:<8} {entity_count:>8} {max_tier or 'N/A':>9} {with_parent:>12} {parent_pct:>9.1f}%")

        # 7. Identify "flat" companies (many entities but few have parents)
        flat_result = await db.execute(
            select(
                Company.ticker,
                func.count(Entity.id).label("entity_count"),
                func.count(Entity.id).filter(Entity.parent_id.isnot(None)).label("with_parent"),
            )
            .join(Entity, Entity.company_id == Company.id)
            .group_by(Company.id, Company.ticker)
            .having(
                and_(
                    func.count(Entity.id) > 10,  # At least 10 entities
                    func.count(Entity.id).filter(Entity.parent_id.isnot(None)) < func.count(Entity.id) * 0.3  # <30% have parents
                )
            )
            .order_by(func.count(Entity.id).desc())
        )
        flat_companies = flat_result.all()

        print(f"\n[*] 'FLAT' COMPANIES (>10 entities, <30% have parents)")
        print(f"  Found {len(flat_companies)} companies with flat structures:")
        for ticker, entity_count, with_parent in flat_companies[:10]:
            parent_pct = (with_parent / entity_count * 100) if entity_count > 0 else 0
            print(f"  - {ticker}: {entity_count} entities, {with_parent} with parents ({parent_pct:.1f}%)")
        if len(flat_companies) > 10:
            print(f"  ... and {len(flat_companies) - 10} more")

        # 8. Example: Show CHTR hierarchy
        print(f"\n[*] EXAMPLE: CHTR ENTITY HIERARCHY")
        chtr = await db.scalar(select(Company).where(Company.ticker == "CHTR"))
        if chtr:
            entities_result = await db.execute(
                select(Entity)
                .where(Entity.company_id == chtr.id)
                .order_by(Entity.structure_tier, Entity.name)
            )
            chtr_entities = entities_result.scalars().all()

            # Build parent lookup
            entity_by_id = {e.id: e for e in chtr_entities}

            # Show first 15 entities with their parent
            print(f"  {'Entity Name':<50} {'Tier':>5} {'Parent':>30}")
            print(f"  {'-'*50} {'-'*5} {'-'*30}")
            for e in chtr_entities[:15]:
                parent_name = entity_by_id.get(e.parent_id, None)
                parent_str = parent_name.name[:28] + ".." if parent_name and len(parent_name.name) > 30 else (parent_name.name if parent_name else "NULL (root)")
                name_str = e.name[:48] + ".." if len(e.name) > 50 else e.name
                print(f"  {name_str:<50} {e.structure_tier or 'N/A':>5} {parent_str:>30}")
            if len(chtr_entities) > 15:
                print(f"  ... and {len(chtr_entities) - 15} more entities")

        # 9. Summary and recommendations
        print(f"\n" + "=" * 70)
        print("SUMMARY & RECOMMENDATIONS")
        print("=" * 70)

        # Calculate key metrics
        pct_with_parent = entities_with_parent / total_entities * 100

        print(f"\n[+] What's Working:")
        print(f"   - ownership_links table has {total_links} records")
        print(f"   - JV relationships tracked: {jv_links}")
        print(f"   - Partial ownership tracked: {partial_ownership}")

        print(f"\n[!]  Issues Identified:")
        if pct_with_parent < 50:
            print(f"   - Only {pct_with_parent:.1f}% of entities have parent_id set (target: >80%)")
        print(f"   - {len(flat_companies)} companies have 'flat' structures (many entities, few parents)")

        print(f"\n[>] Recommended Actions:")
        print(f"   1. Re-extract top 20 companies with enhanced hierarchy prompt")
        print(f"   2. Parse 10-K 'Organizational Structure' sections for ownership chains")
        print(f"   3. Use entity name patterns to infer parent-child (e.g., 'X Holdings' -> 'X Operating')")
        print(f"   4. Manual curation for top 10 complex companies")


if __name__ == "__main__":
    run_async(audit_ownership())
