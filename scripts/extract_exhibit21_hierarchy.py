#!/usr/bin/env python3
"""
Extract corporate ownership hierarchy from SEC Exhibit 21 HTML indentation.

SEC filings state: "Indentation reflects the principal parent of each subsidiary."
This script parses the raw HTML to detect indentation levels and build the
parent-child ownership tree.

Indentation is typically encoded via:
- Nested table cells with &nbsp; padding
- CSS margin-left or padding-left
- Multiple whitespace characters

Usage:
    # Single company
    python scripts/extract_exhibit21_hierarchy.py --ticker CHTR --save-db

    # All companies
    python scripts/extract_exhibit21_hierarchy.py --all --save-db

    # Dry run (no database changes)
    python scripts/extract_exhibit21_hierarchy.py --ticker CHTR
"""

import argparse
import asyncio
import html
import os
import re
import sys
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models import Company, Entity, OwnershipLink


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class SubsidiaryEntry:
    """A subsidiary extracted from Exhibit 21 with indentation level."""
    name: str
    jurisdiction: Optional[str]
    indent_level: int  # 0 = root, 1 = direct, 2 = grandchild, etc.
    raw_line: str  # Original text for debugging


@dataclass
class HierarchyRelationship:
    """A parent-child relationship derived from indentation."""
    parent_name: str
    child_name: str
    ownership_type: str  # 'direct' or 'indirect'


# =============================================================================
# SEC EDGAR FETCHING
# =============================================================================

async def fetch_exhibit21_html(cik: str, client: httpx.AsyncClient) -> Optional[str]:
    """
    Fetch raw Exhibit 21 HTML from SEC EDGAR.

    Returns the raw HTML without any cleaning, so we can parse indentation.
    """
    # Normalize CIK (remove leading zeros for URL, but keep for some endpoints)
    cik_clean = cik.lstrip('0')
    cik_padded = cik.zfill(10)

    # First, get the list of filings to find latest 10-K
    filings_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"

    try:
        # Small delay to avoid rate limiting
        await asyncio.sleep(0.2)

        response = await client.get(filings_url)
        if response.status_code != 200:
            print(f"    Failed to fetch filings index: {response.status_code}")
            return None

        data = response.json()
        filings = data.get("filings", {}).get("recent", {})

        # Find most recent 10-K
        forms = filings.get("form", [])
        accession_numbers = filings.get("accessionNumber", [])

        ten_k_accession = None
        for i, form in enumerate(forms):
            if form == "10-K":
                ten_k_accession = accession_numbers[i]
                print(f"    Found 10-K: {ten_k_accession} (at index {i})")
                break

        if not ten_k_accession:
            print(f"    No 10-K filing found in {len(forms)} filings")
            return None

        # Format accession number for URL (remove dashes)
        accession_formatted = ten_k_accession.replace("-", "")

        # Get the filing index to find Exhibit 21
        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{accession_formatted}/index.json"
        print(f"    Fetching index: {index_url}")

        await asyncio.sleep(0.2)  # Rate limit
        response = await client.get(index_url)
        if response.status_code != 200:
            print(f"    Failed to fetch filing index: {response.status_code}")
            return None

        index_data = response.json()
        items = index_data.get("directory", {}).get("item", [])
        print(f"    Found {len(items)} files in filing")

        # Find Exhibit 21 in the filing
        # Look for patterns like: ex-21, ex21, exh-21, exh21, exhibit21, *-211.htm (EX-21.1)
        # Also: exx21 (double x), ex_21, etc.
        exhibit_candidates = []
        for item in items:
            name = item.get("name", "").lower()
            if not name.endswith((".htm", ".html")):
                continue

            # Skip files that are clearly not Exhibit 21
            # R21.htm is usually a financial data file, not subsidiary list
            if name.startswith('r') and name[1:].startswith('2'):
                continue

            # Match various Exhibit 21 naming patterns
            # - ex-21, ex21, exh-21, exh21, exx21 (double x)
            # - exhibit21, exhibit-21
            # - *211.htm, *21-1.htm (EX-21.1 format)
            is_ex21 = False

            # Pattern: ex followed by optional characters, then 21
            if re.search(r'ex[hx]?[-_]?21', name):
                is_ex21 = True
            # Pattern: exhibit-21 or exhibit21
            elif re.search(r'exhibit[-_]?21', name):
                is_ex21 = True
            # Pattern: ends with 211.htm (EX-21.1 format)
            elif re.search(r'21[-_]?1\.htm', name):
                is_ex21 = True

            # Exclude ex321 (signature exhibit) unless it also has 21
            if 'ex321' in name or 'exx321' in name:
                is_ex21 = False

            if is_ex21:
                exhibit_candidates.append(item['name'])
                print(f"    Candidate: {item['name']}")

        # Prefer the most specific match (exh-211 > ex21 > anything with 21)
        for candidate in exhibit_candidates:
            exhibit_url = f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{accession_formatted}/{candidate}"
            response = await client.get(exhibit_url)
            if response.status_code == 200:
                # Verify it's actually a subsidiary list, not a signature page
                content = response.text.lower()
                if 'subsidiary' in content or 'jurisdiction' in content or 'delaware' in content:
                    print(f"    Found Exhibit 21: {candidate}")
                    return response.text
                else:
                    print(f"    Skipped {candidate} (not subsidiary list)")

        print(f"    No Exhibit 21 file found in 10-K")
        return None

    except Exception as e:
        print(f"    Error fetching Exhibit 21: {e}")
        return None


# =============================================================================
# HTML PARSING
# =============================================================================

def detect_indent_from_html(raw_html: str) -> list[SubsidiaryEntry]:
    """
    Parse HTML to extract subsidiaries with their indentation levels.

    Handles multiple indentation encoding methods:
    1. &#160; or &nbsp; entities before text
    2. Empty <td> cells before the name cell
    3. CSS padding-left or margin-left
    4. Leading whitespace in text
    """
    entries = []

    # Remove script/style tags
    clean = re.sub(r'<script[^>]*>.*?</script>', '', raw_html, flags=re.IGNORECASE | re.DOTALL)
    clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.IGNORECASE | re.DOTALL)

    # Find table rows
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', clean, flags=re.IGNORECASE | re.DOTALL)

    for row in rows:
        # Extract all cells
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, flags=re.IGNORECASE | re.DOTALL)

        if not cells:
            continue

        # Method 1: Count empty leading cells (common indentation method)
        # Empty cells = just whitespace, &nbsp;, &#160;, or truly empty
        empty_cell_count = 0
        name_cell_idx = 0

        for i, cell in enumerate(cells):
            cell_text = re.sub(r'<[^>]+>', '', cell)  # Remove tags
            cell_text = html.unescape(cell_text)
            cell_text = cell_text.strip()

            # Check if cell is essentially empty (only whitespace/nbsp)
            if not cell_text or cell_text.replace('\xa0', '').strip() == '':
                empty_cell_count += 1
            else:
                name_cell_idx = i
                break

        # The name is in the first non-empty cell
        if name_cell_idx >= len(cells):
            continue

        name_cell = cells[name_cell_idx]

        # Method 2: Count &#160; or &nbsp; at start of cell content
        # These are used for visual indentation within a cell
        nbsp_pattern = r'^[\s]*(?:&#160;|&nbsp;|\xa0)+'
        nbsp_match = re.match(nbsp_pattern, name_cell, re.IGNORECASE)
        nbsp_count = 0
        if nbsp_match:
            # Count how many nbsp entities
            nbsp_text = nbsp_match.group(0)
            nbsp_count = nbsp_text.count('&#160;') + nbsp_text.count('&nbsp;') + nbsp_text.count('\xa0')

        # Calculate indent level
        # Empty cells contribute more than nbsp (each empty cell = 1 level)
        # 2-3 nbsp = 1 indent level
        indent_level = empty_cell_count + (nbsp_count // 2)

        # Method 3: Check CSS styles
        padding_match = re.search(r'padding-left:\s*(\d+)', name_cell, re.IGNORECASE)
        if padding_match:
            padding_px = int(padding_match.group(1))
            indent_level = max(indent_level, padding_px // 15)

        margin_match = re.search(r'margin-left:\s*(\d+)', name_cell, re.IGNORECASE)
        if margin_match:
            margin_px = int(margin_match.group(1))
            indent_level = max(indent_level, margin_px // 15)

        # Extract clean text from name cell
        text = re.sub(r'<[^>]+>', '', name_cell)  # Remove HTML tags
        text = html.unescape(text)  # Decode HTML entities
        text = ' '.join(text.split())  # Normalize whitespace

        # Skip empty or very short
        if not text or len(text) < 3:
            continue

        # Skip common non-entity text (headers, page numbers, etc.)
        skip_patterns = [
            r'^name\s*(of\s*)?(jurisdiction|country)?$',
            r'^name\s+jurisdiction',
            r'^subsidiary',
            r'^exhibit',
            r'^page\s*\d',
            r'^\d+$',
            r'^state.*incorporation',
            r'^jurisdiction\s*(of)?',
            r'^country\s*(name)?',
            r'^\*+$',
            r'^the following',
            r'^indentation',
        ]
        if any(re.match(p, text.lower().strip()) for p in skip_patterns):
            continue

        # Skip if text looks like a header (all caps, very short)
        if text.isupper() and len(text) < 20:
            continue

        # Extract jurisdiction from subsequent cells
        jurisdiction = None
        for j in range(name_cell_idx + 1, min(name_cell_idx + 3, len(cells))):
            jur_text = re.sub(r'<[^>]+>', '', cells[j])
            jur_text = html.unescape(jur_text).strip()
            # Jurisdiction is typically a state/country name
            if jur_text and len(jur_text) > 1 and len(jur_text) < 50:
                # Skip if it's just nbsp
                if jur_text.replace('\xa0', '').strip():
                    jurisdiction = jur_text
                    break

        entries.append(SubsidiaryEntry(
            name=text,
            jurisdiction=jurisdiction,
            indent_level=indent_level,
            raw_line=name_cell[:100],
        ))

    return entries


def build_hierarchy_from_entries(entries: list[SubsidiaryEntry], root_company_name: str) -> list[HierarchyRelationship]:
    """
    Build parent-child relationships from indented entries.

    Uses a stack to track the current parent at each indentation level.
    """
    relationships = []

    if not entries:
        return relationships

    # Stack of (indent_level, entity_name) - tracks current parent at each level
    parent_stack = [(0, root_company_name)]

    for entry in entries:
        # Pop stack until we find a parent with lower indent level
        while parent_stack and parent_stack[-1][0] >= entry.indent_level:
            parent_stack.pop()

        # Current parent is top of stack (or root if empty)
        if parent_stack:
            parent_name = parent_stack[-1][1]
            parent_level = parent_stack[-1][0]
        else:
            parent_name = root_company_name
            parent_level = 0

        # Determine if direct or indirect based on indent level
        # Level 1 = direct subsidiary of root
        # Level 2+ = indirect (subsidiary of a subsidiary)
        if entry.indent_level <= 1:
            ownership_type = 'direct'
        else:
            ownership_type = 'indirect'

        relationships.append(HierarchyRelationship(
            parent_name=parent_name,
            child_name=entry.name,
            ownership_type=ownership_type,
        ))

        # Push this entry as potential parent for next entries
        parent_stack.append((entry.indent_level, entry.name))

    return relationships


# =============================================================================
# DATABASE OPERATIONS
# =============================================================================

def normalize_name_for_matching(name: str) -> str:
    """Normalize entity name for fuzzy matching."""
    name = name.lower()
    # Remove common suffixes
    for suffix in [', inc.', ', inc', ' inc.', ' inc', ', llc', ' llc',
                   ', ltd.', ', ltd', ' ltd.', ' ltd', ', corp.', ', corp',
                   ' corp.', ' corp', ', corporation', ' corporation',
                   ', l.l.c.', ' l.l.c.', ', limited', ' limited']:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    # Remove punctuation and extra spaces
    name = re.sub(r'[^\w\s]', ' ', name)
    name = ' '.join(name.split())
    return name.strip()


async def update_hierarchy_for_company(
    db: AsyncSession,
    company: Company,
    relationships: list[HierarchyRelationship],
    save_db: bool = False,
) -> dict:
    """
    Update entity parent_id and ownership_links based on hierarchy relationships.

    Returns stats about what was updated.
    """
    stats = {
        'entities_updated': 0,
        'links_created': 0,
        'links_updated': 0,
        'not_found': [],
    }

    # Get all entities for this company
    result = await db.execute(
        select(Entity).where(Entity.company_id == company.id)
    )
    entities = result.scalars().all()

    # Build lookup by normalized name
    entity_by_name = {}
    for e in entities:
        normalized = normalize_name_for_matching(e.name)
        entity_by_name[normalized] = e
        # Also add original name
        entity_by_name[e.name.lower()] = e

    for rel in relationships:
        # Find parent entity
        parent_normalized = normalize_name_for_matching(rel.parent_name)
        parent_entity = entity_by_name.get(parent_normalized)

        # Try exact match if normalized didn't work
        if not parent_entity:
            parent_entity = entity_by_name.get(rel.parent_name.lower())

        # Find child entity
        child_normalized = normalize_name_for_matching(rel.child_name)
        child_entity = entity_by_name.get(child_normalized)

        if not child_entity:
            child_entity = entity_by_name.get(rel.child_name.lower())

        if not child_entity:
            stats['not_found'].append(rel.child_name)
            continue

        if not parent_entity:
            # Parent might be the root company itself
            if normalize_name_for_matching(rel.parent_name) == normalize_name_for_matching(company.name):
                parent_entity = None  # Will find root entity
            else:
                stats['not_found'].append(f"Parent: {rel.parent_name}")
                continue

        if save_db:
            # Update entity.parent_id
            if parent_entity:
                if child_entity.parent_id != parent_entity.id:
                    child_entity.parent_id = parent_entity.id
                    stats['entities_updated'] += 1

            # Check for existing ownership_link
            existing_link = await db.scalar(
                select(OwnershipLink).where(
                    OwnershipLink.child_entity_id == child_entity.id
                )
            )

            if existing_link:
                # Update ownership_type if different
                if existing_link.ownership_type != rel.ownership_type:
                    existing_link.ownership_type = rel.ownership_type
                    stats['links_updated'] += 1
            elif parent_entity:
                # Create new ownership_link
                new_link = OwnershipLink(
                    parent_entity_id=parent_entity.id,
                    child_entity_id=child_entity.id,
                    ownership_pct=100.0,  # Assume 100% unless stated otherwise
                    ownership_type=rel.ownership_type,
                )
                db.add(new_link)
                stats['links_created'] += 1

    if save_db:
        await db.commit()

    return stats


# =============================================================================
# MAIN
# =============================================================================

async def process_company(
    db: AsyncSession,
    company: Company,
    client: httpx.AsyncClient,
    save_db: bool = False,
) -> dict:
    """Process a single company."""
    print(f"\n[{company.ticker}] {company.name}")

    if not company.cik:
        print(f"  No CIK, skipping")
        return {'status': 'skipped', 'reason': 'no_cik'}

    # Fetch raw Exhibit 21 HTML
    html_content = await fetch_exhibit21_html(company.cik, client)

    if not html_content:
        return {'status': 'skipped', 'reason': 'no_exhibit21'}

    # Parse indentation to get entries
    entries = detect_indent_from_html(html_content)
    print(f"  Found {len(entries)} entities in Exhibit 21")

    if not entries:
        return {'status': 'skipped', 'reason': 'no_entities_parsed'}

    # Show sample of entries with indent levels
    print(f"  Sample entries:")
    for entry in entries[:5]:
        indent_marker = "  " * entry.indent_level
        print(f"    L{entry.indent_level}: {indent_marker}{entry.name[:50]}")
    if len(entries) > 5:
        print(f"    ... and {len(entries) - 5} more")

    # Build hierarchy from indentation
    relationships = build_hierarchy_from_entries(entries, company.name)
    print(f"  Built {len(relationships)} parent-child relationships")

    # Show sample relationships
    direct_count = sum(1 for r in relationships if r.ownership_type == 'direct')
    indirect_count = sum(1 for r in relationships if r.ownership_type == 'indirect')
    print(f"  Direct: {direct_count}, Indirect: {indirect_count}")

    # Update database
    stats = await update_hierarchy_for_company(db, company, relationships, save_db)

    if save_db:
        print(f"  Updated: {stats['entities_updated']} entities, {stats['links_created']} new links, {stats['links_updated']} updated links")
    else:
        print(f"  [DRY RUN] Would update: {len(relationships)} relationships")

    if stats['not_found']:
        print(f"  Not matched: {len(stats['not_found'])} entities")

    return {'status': 'success', 'stats': stats}


async def main():
    parser = argparse.ArgumentParser(description="Extract ownership hierarchy from Exhibit 21 HTML")
    parser.add_argument("--ticker", type=str, help="Process single company by ticker")
    parser.add_argument("--all", action="store_true", help="Process all companies")
    parser.add_argument("--save-db", action="store_true", help="Save changes to database")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of companies (for testing)")

    args = parser.parse_args()

    if not args.ticker and not args.all:
        print("Error: Must specify --ticker or --all")
        return

    settings = get_settings()
    engine = create_async_engine(
        settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
    )
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    print("=" * 70)
    print("EXHIBIT 21 HIERARCHY EXTRACTION")
    print("=" * 70)
    print(f"Mode: {'SAVE TO DB' if args.save_db else 'DRY RUN'}")

    # HTTP client with SEC-required headers
    headers = {
        "User-Agent": "DebtStack research@debtstack.ai",
        "Accept-Encoding": "gzip, deflate",
    }

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        async with async_session() as db:
            if args.ticker:
                company = await db.scalar(
                    select(Company).where(Company.ticker == args.ticker.upper())
                )
                if not company:
                    print(f"Company {args.ticker} not found")
                    return
                await process_company(db, company, client, args.save_db)
            else:
                query = select(Company).order_by(Company.ticker)
                if args.limit:
                    query = query.limit(args.limit)

                result = await db.execute(query)
                companies = result.scalars().all()

                print(f"Processing {len(companies)} companies...")

                success = 0
                skipped = 0

                for company in companies:
                    try:
                        result = await process_company(db, company, client, args.save_db)
                        if result['status'] == 'success':
                            success += 1
                        else:
                            skipped += 1
                    except Exception as e:
                        print(f"  Error: {e}")
                        skipped += 1

                    # Rate limit: SEC asks for max 10 requests/second
                    await asyncio.sleep(0.15)

                print("\n" + "=" * 70)
                print(f"COMPLETE: {success} processed, {skipped} skipped")


if __name__ == "__main__":
    asyncio.run(main())
