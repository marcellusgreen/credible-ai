#!/usr/bin/env python3
"""
Cleanup mis-classified document sections.

Identifies and fixes documents that are incorrectly classified as indentures:
1. Description of Securities (Exhibit 4.x that describes equity, not debt)
2. Failed extractions (directory listings, very short content)
3. Benefit/compensation plans (not debt documents)

Usage:
    # Dry run - see what would be changed
    python scripts/cleanup_misclassified_docs.py --dry-run

    # Actually make changes
    python scripts/cleanup_misclassified_docs.py --execute
"""

import argparse
import asyncio
import io
import os
import sys

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from app.core.database import async_session_maker


async def cleanup_documents(dry_run: bool = True):
    async with async_session_maker() as session:
        print("=" * 70)
        print("DOCUMENT CLEANUP")
        print("=" * 70)
        print(f"Mode: {'DRY RUN' if dry_run else 'EXECUTE'}")
        print()

        # 1. Reclassify Description of Securities
        print("1. DESCRIPTION OF SECURITIES (reclassify to 'desc_securities')")
        print("-" * 70)

        result = await session.execute(text("""
            SELECT id, section_title
            FROM document_sections
            WHERE section_type = 'indenture'
              AND (content LIKE '%DESCRIPTION OF THE REGISTRANT%'
                   OR content LIKE '%DESCRIPTION OF COMMON STOCK%'
                   OR content LIKE '%DESCRIPTION OF CAPITAL STOCK%'
                   OR content LIKE '%Description of the Registrant%'
                   OR section_title LIKE '%DESCRIPTION OF THE REGISTRANT%')
        """))
        desc_securities = result.fetchall()
        print(f"   Found: {len(desc_securities)} documents")

        if not dry_run and desc_securities:
            ids = [str(row[0]) for row in desc_securities]
            # Use IN clause with proper UUID casting for asyncpg
            placeholders = ", ".join([f"'{id}'::uuid" for id in ids])
            await session.execute(text(f"""
                UPDATE document_sections
                SET section_type = 'desc_securities'
                WHERE id IN ({placeholders})
            """))
            print(f"   Reclassified: {len(desc_securities)} documents")

        # 2. Delete failed extractions (directory listings)
        print()
        print("2. FAILED EXTRACTIONS - Directory Listings (delete)")
        print("-" * 70)

        result = await session.execute(text("""
            SELECT id, section_title
            FROM document_sections
            WHERE section_type = 'indenture'
              AND (content LIKE '%Directory List of%'
                   OR content LIKE '%Skip to Main Content%About What We Do%')
        """))
        dir_listings = result.fetchall()
        print(f"   Found: {len(dir_listings)} documents")

        if not dry_run and dir_listings:
            ids = [str(row[0]) for row in dir_listings]
            placeholders = ", ".join([f"'{id}'::uuid" for id in ids])
            # First delete any links to these documents
            await session.execute(text(f"""
                DELETE FROM debt_instrument_documents
                WHERE document_section_id IN ({placeholders})
            """))
            # Then delete the documents
            await session.execute(text(f"""
                DELETE FROM document_sections
                WHERE id IN ({placeholders})
            """))
            print(f"   Deleted: {len(dir_listings)} documents")

        # 3. Delete very short content (likely failed extractions)
        print()
        print("3. VERY SHORT CONTENT < 200 chars (delete)")
        print("-" * 70)

        result = await session.execute(text("""
            SELECT id, section_title, LENGTH(content) as len
            FROM document_sections
            WHERE section_type = 'indenture'
              AND LENGTH(content) < 200
        """))
        short_docs = result.fetchall()
        print(f"   Found: {len(short_docs)} documents")

        if not dry_run and short_docs:
            ids = [str(row[0]) for row in short_docs]
            placeholders = ", ".join([f"'{id}'::uuid" for id in ids])
            await session.execute(text(f"""
                DELETE FROM debt_instrument_documents
                WHERE document_section_id IN ({placeholders})
            """))
            await session.execute(text(f"""
                DELETE FROM document_sections
                WHERE id IN ({placeholders})
            """))
            print(f"   Deleted: {len(short_docs)} documents")

        # 4. Reclassify benefit/compensation plans that are clearly not indentures
        print()
        print("4. BENEFIT PLANS - Deferred Bonus/Compensation (reclassify to 'other')")
        print("-" * 70)

        result = await session.execute(text("""
            SELECT id, section_title
            FROM document_sections
            WHERE section_type = 'indenture'
              AND (content LIKE '%McDONALDS EXCESS BENEFIT AND DEFERRED BONUS PLAN%'
                   OR content LIKE '%DEFERRED COMPENSATION PLAN%'
                   OR content LIKE '%EMPLOYEE STOCK PURCHASE PLAN%'
                   OR content LIKE '%STOCK OPTION PLAN%'
                   OR content LIKE '%RESTRICTED STOCK UNIT%')
              AND content NOT LIKE '%Notes due%'
              AND content NOT LIKE '%Indenture%dated%'
        """))
        benefit_plans = result.fetchall()
        print(f"   Found: {len(benefit_plans)} documents")

        if not dry_run and benefit_plans:
            ids = [str(row[0]) for row in benefit_plans]
            placeholders = ", ".join([f"'{id}'::uuid" for id in ids])
            # Delete links first
            await session.execute(text(f"""
                DELETE FROM debt_instrument_documents
                WHERE document_section_id IN ({placeholders})
            """))
            # Then delete the documents (these aren't debt-related at all)
            await session.execute(text(f"""
                DELETE FROM document_sections
                WHERE id IN ({placeholders})
            """))
            print(f"   Deleted: {len(benefit_plans)} documents")

        # Commit if not dry run
        if not dry_run:
            await session.commit()
            print()
            print("=" * 70)
            print("CHANGES COMMITTED")
            print("=" * 70)

        # Summary
        print()
        print("=" * 70)
        print("SUMMARY")
        print("=" * 70)

        # Get updated counts
        result = await session.execute(text("""
            SELECT section_type, COUNT(*)
            FROM document_sections
            GROUP BY section_type
            ORDER BY COUNT(*) DESC
        """))
        print("Document counts by type:")
        for row in result.fetchall():
            print(f"   {row[0]:<25} {row[1]:>5}")


async def main():
    parser = argparse.ArgumentParser(description="Cleanup mis-classified documents")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be changed without making changes")
    parser.add_argument("--execute", action="store_true", help="Actually make the changes")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Either --dry-run or --execute is required")

    await cleanup_documents(dry_run=not args.execute)


if __name__ == "__main__":
    asyncio.run(main())
