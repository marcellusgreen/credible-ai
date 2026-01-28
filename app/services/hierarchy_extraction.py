"""
Ownership Hierarchy Extraction Service for DebtStack.ai

Extracts corporate ownership hierarchy from SEC Exhibit 21 filings.
Parses HTML indentation to determine parent-child relationships.
"""

import asyncio
import html
import re
from dataclasses import dataclass
from typing import Optional
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entity, OwnershipLink


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
# EXHIBIT 21 FETCHING
# =============================================================================

async def fetch_exhibit21_html(cik: str, client: httpx.AsyncClient) -> Optional[str]:
    """
    Fetch raw Exhibit 21 HTML from SEC EDGAR.
    Returns the raw HTML without any cleaning, so we can parse indentation.
    """
    cik_clean = cik.lstrip('0')
    cik_padded = cik.zfill(10)

    filings_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"

    try:
        await asyncio.sleep(0.2)  # Rate limit
        response = await client.get(filings_url)
        if response.status_code != 200:
            return None

        data = response.json()
        filings = data.get("filings", {}).get("recent", {})

        forms = filings.get("form", [])
        accession_numbers = filings.get("accessionNumber", [])

        ten_k_accession = None
        for i, form in enumerate(forms):
            if form == "10-K":
                ten_k_accession = accession_numbers[i]
                break

        if not ten_k_accession:
            return None

        accession_formatted = ten_k_accession.replace("-", "")

        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{accession_formatted}/index.json"
        await asyncio.sleep(0.2)
        response = await client.get(index_url)
        if response.status_code != 200:
            return None

        index_data = response.json()
        items = index_data.get("directory", {}).get("item", [])

        exhibit_candidates = []
        for item in items:
            name = item.get("name", "").lower()
            if not name.endswith((".htm", ".html")):
                continue

            if name.startswith('r') and name[1:].startswith('2'):
                continue

            is_ex21 = False
            if re.search(r'ex[hx]?[-_]?21', name):
                is_ex21 = True
            elif re.search(r'exhibit[-_]?21', name):
                is_ex21 = True
            elif re.search(r'21[-_]?1\.htm', name):
                is_ex21 = True

            if 'ex321' in name or 'exx321' in name:
                is_ex21 = False

            if is_ex21:
                exhibit_candidates.append(item['name'])

        for candidate in exhibit_candidates:
            exhibit_url = f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{accession_formatted}/{candidate}"
            response = await client.get(exhibit_url)
            if response.status_code == 200:
                content = response.text.lower()
                if 'subsidiary' in content or 'jurisdiction' in content or 'delaware' in content:
                    return response.text

        return None

    except Exception as e:
        print(f"      Error fetching Exhibit 21: {e}")
        return None


# =============================================================================
# HTML PARSING
# =============================================================================

def detect_indent_from_html(raw_html: str) -> list[SubsidiaryEntry]:
    """
    Parse HTML to extract subsidiaries with their indentation levels.
    Handles both table-based (<tr>/<td>) and div-based layouts.
    """
    entries = []

    clean = re.sub(r'<script[^>]*>.*?</script>', '', raw_html, flags=re.IGNORECASE | re.DOTALL)
    clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.IGNORECASE | re.DOTALL)

    # Skip patterns for headers/noise
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
        r'^list of subsidiar',
        r'^meta platforms',  # Company name header
        r'^document$',
    ]

    def should_skip(text: str) -> bool:
        text_lower = text.lower().strip()
        if any(re.match(p, text_lower) for p in skip_patterns):
            return True
        if text.isupper() and len(text) < 20:
            return True
        return False

    def extract_jurisdiction_from_text(text: str) -> tuple[str, str]:
        """Extract jurisdiction from parentheses at end of name, e.g., 'Company LLC (Delaware)'"""
        match = re.search(r'\(([^)]+)\)\s*$', text)
        if match:
            jurisdiction = match.group(1).strip()
            name = text[:match.start()].strip()
            return name, jurisdiction
        return text, None

    # Try table-based parsing first
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', clean, flags=re.IGNORECASE | re.DOTALL)

    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, flags=re.IGNORECASE | re.DOTALL)

        if not cells:
            continue

        empty_cell_count = 0
        name_cell_idx = 0

        for i, cell in enumerate(cells):
            cell_text = re.sub(r'<[^>]+>', '', cell)
            cell_text = html.unescape(cell_text)
            cell_text = cell_text.strip()

            if not cell_text or cell_text.replace('\xa0', '').strip() == '':
                empty_cell_count += 1
            else:
                name_cell_idx = i
                break

        if name_cell_idx >= len(cells):
            continue

        name_cell = cells[name_cell_idx]

        nbsp_pattern = r'^[\s]*(?:&#160;|&nbsp;|\xa0)+'
        nbsp_match = re.match(nbsp_pattern, name_cell, re.IGNORECASE)
        nbsp_count = 0
        if nbsp_match:
            nbsp_text = nbsp_match.group(0)
            nbsp_count = nbsp_text.count('&#160;') + nbsp_text.count('&nbsp;') + nbsp_text.count('\xa0')

        indent_level = empty_cell_count + (nbsp_count // 2)

        padding_match = re.search(r'padding-left:\s*(\d+)', name_cell, re.IGNORECASE)
        if padding_match:
            padding_px = int(padding_match.group(1))
            indent_level = max(indent_level, padding_px // 15)

        margin_match = re.search(r'margin-left:\s*(\d+)', name_cell, re.IGNORECASE)
        if margin_match:
            margin_px = int(margin_match.group(1))
            indent_level = max(indent_level, margin_px // 15)

        text = re.sub(r'<[^>]+>', '', name_cell)
        text = html.unescape(text)
        text = ' '.join(text.split())

        if not text or len(text) < 3:
            continue

        if should_skip(text):
            continue

        # Try to get jurisdiction from adjacent cell first
        jurisdiction = None
        for j in range(name_cell_idx + 1, min(name_cell_idx + 3, len(cells))):
            jur_text = re.sub(r'<[^>]+>', '', cells[j])
            jur_text = html.unescape(jur_text).strip()
            if jur_text and len(jur_text) > 1 and len(jur_text) < 50:
                if jur_text.replace('\xa0', '').strip():
                    jurisdiction = jur_text
                    break

        # If no jurisdiction in adjacent cell, try extracting from name
        if not jurisdiction:
            text, jurisdiction = extract_jurisdiction_from_text(text)

        entries.append(SubsidiaryEntry(
            name=text,
            jurisdiction=jurisdiction,
            indent_level=indent_level,
            raw_line=name_cell[:100],
        ))

    # If no table rows found, try div-based parsing (like META's format)
    if not entries:
        # Look for divs containing font tags with subsidiary names
        # Pattern: <div><font ...>Company Name (Jurisdiction)</font></div>
        div_pattern = r'<div[^>]*>\s*<font[^>]*>([^<]+)</font>\s*</div>'
        div_matches = re.findall(div_pattern, clean, flags=re.IGNORECASE)

        for match in div_matches:
            text = html.unescape(match)
            text = ' '.join(text.split())

            if not text or len(text) < 3:
                continue

            if should_skip(text):
                continue

            # Extract jurisdiction from parentheses
            name, jurisdiction = extract_jurisdiction_from_text(text)

            if not name or len(name) < 3:
                continue

            # All div-based entries are at the same level (flat list)
            entries.append(SubsidiaryEntry(
                name=name,
                jurisdiction=jurisdiction,
                indent_level=0,  # Flat structure
                raw_line=text[:100],
            ))

    return entries


def build_hierarchy_from_entries(entries: list[SubsidiaryEntry], root_company_name: str) -> list[HierarchyRelationship]:
    """
    Build parent-child relationships from indented entries.
    """
    relationships = []

    if not entries:
        return relationships

    parent_stack = [(0, root_company_name)]

    for entry in entries:
        while parent_stack and parent_stack[-1][0] >= entry.indent_level:
            parent_stack.pop()

        if parent_stack:
            parent_name = parent_stack[-1][1]
        else:
            parent_name = root_company_name

        if entry.indent_level <= 1:
            ownership_type = 'direct'
        else:
            ownership_type = 'indirect'

        relationships.append(HierarchyRelationship(
            parent_name=parent_name,
            child_name=entry.name,
            ownership_type=ownership_type,
        ))

        parent_stack.append((entry.indent_level, entry.name))

    return relationships


def normalize_name_for_matching(name: str) -> str:
    """Normalize entity name for fuzzy matching."""
    name = name.lower()
    for suffix in [', inc.', ', inc', ' inc.', ' inc', ', llc', ' llc',
                   ', ltd.', ', ltd', ' ltd.', ' ltd', ', corp.', ', corp',
                   ' corp.', ' corp', ', corporation', ' corporation',
                   ', l.l.c.', ' l.l.c.', ', limited', ' limited']:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    name = re.sub(r'[^\w\s]', ' ', name)
    name = ' '.join(name.split())
    return name.strip()


# =============================================================================
# MAIN EXTRACTION FUNCTION
# =============================================================================

# US states for determining is_domestic
US_STATES = [
    'Delaware', 'California', 'Texas', 'New York', 'Nevada', 'Florida',
    'Ohio', 'Illinois', 'Pennsylvania', 'Georgia', 'North Carolina',
    'Virginia', 'Michigan', 'New Jersey', 'Washington', 'Arizona',
    'Massachusetts', 'Maryland', 'Colorado', 'Minnesota'
]


async def extract_ownership_hierarchy(
    session: AsyncSession,
    company_id: UUID,
    ticker: str,
    cik: str,
    company_name: str
) -> dict:
    """
    Extract ownership hierarchy from Exhibit 21 indentation.

    FULL implementation that:
    1. Fetches raw Exhibit 21 HTML from SEC EDGAR
    2. Parses indentation using detect_indent_from_html()
    3. Builds hierarchy using build_hierarchy_from_entries()
    4. Creates missing entities found in Exhibit 21
    5. Updates parent_id and creates OwnershipLink records

    Args:
        session: Database session
        company_id: Company UUID
        ticker: Stock ticker
        cik: SEC CIK number
        company_name: Company name (for root entity matching)

    Returns:
        Dict with extraction stats
    """
    stats = {
        'entries_found': 0,
        'relationships_built': 0,
        'entities_created': 0,
        'entities_updated': 0,
        'links_created': 0,
        'not_matched': [],
    }

    headers = {
        "User-Agent": "DebtStack research@debtstack.ai",
        "Accept-Encoding": "gzip, deflate",
    }

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        html_content = await fetch_exhibit21_html(cik, client)

    if not html_content:
        print(f"      No Exhibit 21 found")
        return stats

    # Parse indentation to get entries
    entries = detect_indent_from_html(html_content)
    stats['entries_found'] = len(entries)

    if not entries:
        print(f"      No entities parsed from Exhibit 21")
        return stats

    print(f"      Found {len(entries)} entities in Exhibit 21")

    # Build hierarchy from indentation
    relationships = build_hierarchy_from_entries(entries, company_name)
    stats['relationships_built'] = len(relationships)

    # Get existing entities
    result = await session.execute(
        select(Entity).where(Entity.company_id == company_id)
    )
    existing_entities = result.scalars().all()

    # Build lookup by normalized name
    entity_by_name = {}
    for e in existing_entities:
        normalized = normalize_name_for_matching(e.name)
        entity_by_name[normalized] = e
        entity_by_name[e.name.lower()] = e

    # Create missing entities from Exhibit 21
    entities_to_create = []
    for entry in entries:
        normalized = normalize_name_for_matching(entry.name)
        if normalized not in entity_by_name and entry.name.lower() not in entity_by_name:
            # Entity doesn't exist, create it
            entity_id = uuid4()
            entity = Entity(
                id=entity_id,
                company_id=company_id,
                name=entry.name,
                slug=re.sub(r'[^a-z0-9]+', '-', entry.name.lower())[:255],
                entity_type='subsidiary',
                jurisdiction=entry.jurisdiction,
                structure_tier=3,
                is_domestic=entry.jurisdiction in US_STATES if entry.jurisdiction else True,
            )
            session.add(entity)
            entities_to_create.append(entity)
            entity_by_name[normalized] = entity
            entity_by_name[entry.name.lower()] = entity
            stats['entities_created'] += 1

    if entities_to_create:
        await session.flush()
        print(f"      Created {len(entities_to_create)} new entities")

    # Mark root entity
    result = await session.execute(
        select(Entity).where(Entity.company_id == company_id, Entity.entity_type == 'holdco')
    )
    holdco = result.scalar_one_or_none()
    if holdco:
        holdco.is_root = True
        entity_by_name[normalize_name_for_matching(company_name)] = holdco
        entity_by_name[company_name.lower()] = holdco

    # Update hierarchy relationships
    for rel in relationships:
        parent_normalized = normalize_name_for_matching(rel.parent_name)
        parent_entity = entity_by_name.get(parent_normalized) or entity_by_name.get(rel.parent_name.lower())

        child_normalized = normalize_name_for_matching(rel.child_name)
        child_entity = entity_by_name.get(child_normalized) or entity_by_name.get(rel.child_name.lower())

        if not child_entity:
            stats['not_matched'].append(rel.child_name)
            continue

        if not parent_entity:
            if normalize_name_for_matching(rel.parent_name) == normalize_name_for_matching(company_name):
                parent_entity = holdco
            else:
                stats['not_matched'].append(f"Parent: {rel.parent_name}")
                continue

        # Update entity.parent_id
        if parent_entity and child_entity.parent_id != parent_entity.id:
            child_entity.parent_id = parent_entity.id
            stats['entities_updated'] += 1

        # Check for existing ownership_link
        if parent_entity:
            existing_link = await session.scalar(
                select(OwnershipLink).where(
                    OwnershipLink.child_entity_id == child_entity.id
                )
            )

            if not existing_link:
                new_link = OwnershipLink(
                    id=uuid4(),
                    parent_entity_id=parent_entity.id,
                    child_entity_id=child_entity.id,
                    ownership_pct=100.0,
                    ownership_type=rel.ownership_type,
                )
                session.add(new_link)
                stats['links_created'] += 1

    await session.commit()

    print(f"      Updated {stats['entities_updated']} entity parents, created {stats['links_created']} links")
    if stats['not_matched']:
        print(f"      Not matched: {len(stats['not_matched'])} entities")

    return stats
