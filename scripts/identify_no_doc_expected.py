#!/usr/bin/env python3
"""
Identify unlinked instruments that legitimately don't have public documents.

This script uses FACT-BASED criteria to mark instruments, NOT just because
they couldn't be matched. Each category has a clear business reason.
"""

import argparse
import asyncio
import sys
import io
import os
import re

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from app.core.database import async_session_maker


# Categories with clear business reasons for no public document
NO_DOC_CATEGORIES = {
    # 1. Commercial Paper - Short-term, no indenture filed
    "commercial_paper": {
        "types": ["commercial_paper", "commercial_paper_program"],
        "name_patterns": [
            r"commercial\s*paper",
            r"\bcp\b\s*program",
        ],
        "reason": "Commercial paper is short-term unsecured debt without indentures"
    },

    # 2. Bilateral/Bank facilities - Private agreements, not filed publicly
    "bilateral_facility": {
        "types": [],
        "name_patterns": [
            r"bilateral",
            r"uncommitted",
            r"bank\s*credit\s*facilit",
            r"bank\s*borrowing",
            r"other\s*bank",
            r"short.?term\s*loan\s*agreement",
            r"working\s*capital\s*facilit",
        ],
        "reason": "Bilateral/uncommitted facilities are private bank arrangements"
    },

    # 3. Intercompany debt - Internal, no public filing
    "intercompany": {
        "types": [],
        "name_patterns": [
            r"intercompany",
            r"related\s*party",
            r"affiliate",
        ],
        "reason": "Intercompany debt is internal and not publicly filed"
    },

    # 4. Foreign/Local credit lines - Often governed by local law, not SEC filed
    "foreign_local_facility": {
        "types": ["credit_line", "line_of_credit"],
        "name_patterns": [
            r"local\s*(credit|facilit|line)",
            r"foreign\s*(credit|facilit|line)",
            r"non.?u\.?s\.?\s*(credit|facilit|borrowing)",
            r"international\s*(credit|facilit)",
            r"yen.?denominated",
            r"euro.?denominated",
            r"EUR\s*(fixed|floating)",
            r"GBP\s*(fixed|floating)",
        ],
        "reason": "Foreign/local facilities governed by non-US law, not SEC filed"
    },

    # 5. Securitizations - Separate SPV documents, not standard indentures
    "securitization": {
        "types": ["receivables_facility", "abs", "asset_backed"],
        "name_patterns": [
            r"securitization",
            r"receivable.?\s*facilit",
            r"receivable.?\s*funding",
            r"asset.?backed",
            r"floor\s*plan",
            r"conduit",
        ],
        "reason": "Securitizations use SPV structures with separate documentation"
    },

    # 6. Finance leases - Lease agreements, not debt indentures
    "finance_lease": {
        "types": ["finance_lease", "capital_lease"],
        "name_patterns": [
            r"finance\s*lease",
            r"capital\s*lease",
            r"lease\s*obligation",
            r"sale.?leaseback",
            r"equipment\s*obligation",
        ],
        "reason": "Finance leases are governed by lease agreements, not debt indentures"
    },

    # 7. Government/Export financing - Special programs with separate docs
    "government_financing": {
        "types": [],
        "name_patterns": [
            r"export\s*(credit|finance)",
            r"government\s*(loan|guarantee)",
            r"payroll\s*support",
            r"FHLB",
            r"federal\s*home\s*loan",
        ],
        "reason": "Government/export financing has specialized documentation"
    },

    # 8. Project finance / Nonrecourse - Ring-fenced, separate docs
    "project_finance": {
        "types": [],
        "name_patterns": [
            r"nonrecourse",
            r"non.?recourse",
            r"project\s*financ",
            r"wind\s*(farm|project)",
            r"solar\s*(farm|project)",
        ],
        "reason": "Nonrecourse/project finance is ring-fenced with separate docs"
    },

    # 9. Letters of credit - Support instruments, not standalone debt
    "letters_of_credit": {
        "types": [],
        "name_patterns": [
            r"^letter.?of.?credit",
            r"^l/?c\s*facilit",
            r"standby\s*letter",
        ],
        "reason": "Letters of credit are support instruments, not standalone debt"
    },

    # 10. Other/Miscellaneous buckets - Aggregated line items
    "aggregated_other": {
        "types": ["other"],
        "name_patterns": [
            r"^other\s*(long.?term|short.?term)?\s*(debt|borrowing|financ|note)",
            r"^other\s*revolv",
            r"^miscellaneous",
            r"various\s*(maturit|rate)",
        ],
        "reason": "Aggregated 'other' line items represent multiple small instruments"
    },
}


async def identify_no_doc_expected(dry_run: bool = True):
    async with async_session_maker() as session:
        print("=" * 80)
        print("IDENTIFY INSTRUMENTS WITHOUT PUBLIC DOCUMENTS (FACT-BASED)")
        print("=" * 80)
        print(f"Mode: {'DRY RUN' if dry_run else 'EXECUTE'}")
        print()

        # Get all unlinked instruments
        result = await session.execute(text("""
            SELECT
                di.id,
                di.name,
                di.instrument_type,
                c.ticker
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
              AND did.id IS NULL
              AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL)
            ORDER BY c.ticker, di.name
        """))

        unlinked = result.fetchall()
        print(f"Total unlinked instruments to analyze: {len(unlinked)}")
        print()

        # Categorize each instrument
        to_mark = {}  # category -> list of (id, ticker, name, reason)

        for row in unlinked:
            inst_id, name, inst_type, ticker = row
            name_lower = (name or "").lower()
            inst_type_lower = (inst_type or "").lower()

            for category, criteria in NO_DOC_CATEGORIES.items():
                matched = False
                reason = criteria["reason"]

                # Check by instrument type
                if inst_type_lower in [t.lower() for t in criteria["types"]]:
                    matched = True

                # Check by name pattern
                if not matched:
                    for pattern in criteria["name_patterns"]:
                        if re.search(pattern, name_lower, re.IGNORECASE):
                            matched = True
                            break

                if matched:
                    if category not in to_mark:
                        to_mark[category] = []
                    to_mark[category].append((inst_id, ticker, name, reason))
                    break  # Only categorize once

        # Report findings
        total_to_mark = 0
        for category, instruments in sorted(to_mark.items()):
            print(f"\n{category.upper()} ({len(instruments)} instruments)")
            print(f"  Reason: {NO_DOC_CATEGORIES[category]['reason']}")
            print("-" * 80)

            for inst_id, ticker, name, reason in instruments[:10]:
                print(f"  {ticker:6} | {name[:60] if name else 'NULL'}")

            if len(instruments) > 10:
                print(f"  ... and {len(instruments) - 10} more")

            total_to_mark += len(instruments)

        print()
        print("=" * 80)
        print(f"TOTAL TO MARK: {total_to_mark}")
        print("=" * 80)

        # Execute if not dry run
        if not dry_run and total_to_mark > 0:
            all_ids = []
            for category, instruments in to_mark.items():
                for inst_id, ticker, name, reason in instruments:
                    all_ids.append((inst_id, category))

            print(f"\nMarking {len(all_ids)} instruments...")

            # Group by category for batch updates
            for category, instruments in to_mark.items():
                reason = NO_DOC_CATEGORIES[category]["reason"]
                # Escape single quotes in reason
                reason_escaped = reason.replace("'", "''")
                ids = [str(inst_id) for inst_id, _, _, _ in instruments]
                placeholders = ", ".join([f"'{id}'::uuid" for id in ids])
                await session.execute(text(f"""
                    UPDATE debt_instruments
                    SET attributes = COALESCE(attributes, '{{}}'::jsonb) ||
                        jsonb_build_object('no_document_expected', true, 'no_doc_reason', '{reason_escaped}')
                    WHERE id IN ({placeholders})
                """))

            await session.commit()
            print(f"Marked {len(all_ids)} instruments")

        # Show updated metrics
        print()
        print("=" * 80)
        print("UPDATED COVERAGE METRICS")
        print("=" * 80)

        result = await session.execute(text("""
            SELECT
                COUNT(DISTINCT di.id) as total,
                COUNT(DISTINCT did.debt_instrument_id) as linked,
                COUNT(DISTINCT CASE WHEN di.attributes->>'no_document_expected' = 'true' THEN di.id END) as no_doc_expected
            FROM debt_instruments di
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
        """))
        row = result.fetchone()

        total = row[0]
        linked = row[1]
        no_doc = row[2]
        linkable = total - no_doc

        print(f"Total active instruments:     {total}")
        print(f"No document expected:         {no_doc}")
        print(f"Linkable instruments:         {linkable}")
        print(f"Actually linked:              {linked}")
        print()
        print(f"Raw coverage:                 {linked/total*100:.1f}%")
        print(f"Adjusted coverage:            {linked/linkable*100:.1f}% (excluding no-doc-expected)")


async def main():
    parser = argparse.ArgumentParser(description="Identify instruments without public documents")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be marked")
    parser.add_argument("--execute", action="store_true", help="Actually mark instruments")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Either --dry-run or --execute is required")

    await identify_no_doc_expected(dry_run=not args.execute)


if __name__ == "__main__":
    asyncio.run(main())
