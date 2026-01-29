"""
Ownership Hierarchy Extraction Service for DebtStack.ai

Unified extraction that:
1. Parses Exhibit 21 from SEC EDGAR for complete subsidiary list
2. Extracts parent-child relationships from indentation (if available)
3. Enriches with ownership info from indentures/credit agreements via LLM
"""

import asyncio
import html
import re
from dataclasses import dataclass
from typing import Optional
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entity, OwnershipLink, DocumentSection


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class SubsidiaryEntry:
    """A subsidiary extracted from Exhibit 21."""
    name: str
    jurisdiction: Optional[str]
    indent_level: int  # 0 = flat/root, 1+ = nested depth
    raw_line: str


@dataclass
class HierarchyRelationship:
    """A parent-child relationship."""
    parent_name: str
    child_name: str
    ownership_type: str  # 'direct' or 'indirect'


# =============================================================================
# CONSTANTS
# =============================================================================

US_JURISDICTIONS = {
    'Alabama', 'Alaska', 'Arizona', 'Arkansas', 'California', 'Colorado',
    'Connecticut', 'Delaware', 'Florida', 'Georgia', 'Hawaii', 'Idaho',
    'Illinois', 'Indiana', 'Iowa', 'Kansas', 'Kentucky', 'Louisiana',
    'Maine', 'Maryland', 'Massachusetts', 'Michigan', 'Minnesota',
    'Mississippi', 'Missouri', 'Montana', 'Nebraska', 'Nevada',
    'New Hampshire', 'New Jersey', 'New Mexico', 'New York',
    'North Carolina', 'North Dakota', 'Ohio', 'Oklahoma', 'Oregon',
    'Pennsylvania', 'Rhode Island', 'South Carolina', 'South Dakota',
    'Tennessee', 'Texas', 'Utah', 'Vermont', 'Virginia', 'Washington',
    'West Virginia', 'Wisconsin', 'Wyoming', 'District of Columbia',
    'Puerto Rico', 'Virgin Islands', 'Guam',
}

SKIP_PATTERNS = [
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
    r'^document$',
]


# =============================================================================
# EXHIBIT 21 FETCHING
# =============================================================================

async def fetch_exhibit21_html(cik: str, client: httpx.AsyncClient) -> Optional[str]:
    """Fetch raw Exhibit 21 HTML from SEC EDGAR."""
    cik_clean = cik.lstrip('0')
    cik_padded = cik.zfill(10)

    try:
        # Get company filings
        await asyncio.sleep(0.2)
        response = await client.get(f"https://data.sec.gov/submissions/CIK{cik_padded}.json")
        if response.status_code != 200:
            return None

        data = response.json()
        filings = data.get("filings", {}).get("recent", {})
        forms = filings.get("form", [])
        accessions = filings.get("accessionNumber", [])

        # Find latest 10-K
        ten_k_accession = None
        for i, form in enumerate(forms):
            if form == "10-K":
                ten_k_accession = accessions[i]
                break

        if not ten_k_accession:
            return None

        # Get filing index
        accession_fmt = ten_k_accession.replace("-", "")
        await asyncio.sleep(0.2)
        response = await client.get(
            f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{accession_fmt}/index.json"
        )
        if response.status_code != 200:
            return None

        items = response.json().get("directory", {}).get("item", [])

        # Find Exhibit 21 file
        exhibit_candidates = []
        for item in items:
            name = item.get("name", "").lower()
            if not name.endswith((".htm", ".html")):
                continue
            # Skip iXBRL viewer files
            if name.startswith('r') and name[1:].startswith('2'):
                continue
            # Match ex21, exh21, exhibit21, etc.
            if re.search(r'ex[hx]?[-_]?21|exhibit[-_]?21|21[-_]?1\.htm', name):
                if 'ex321' not in name and 'exx321' not in name:
                    exhibit_candidates.append(item['name'])

        # Fetch and validate each candidate
        for candidate in exhibit_candidates:
            url = f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{accession_fmt}/{candidate}"
            response = await client.get(url)
            if response.status_code == 200:
                content_lower = response.text.lower()
                if any(kw in content_lower for kw in ['subsidiary', 'jurisdiction', 'delaware']):
                    return response.text

        return None

    except Exception as e:
        print(f"      Error fetching Exhibit 21: {e}")
        return None


# =============================================================================
# HTML PARSING
# =============================================================================

def _should_skip(text: str) -> bool:
    """Check if text is a header/noise that should be skipped."""
    text_lower = text.lower().strip()
    if any(re.match(p, text_lower) for p in SKIP_PATTERNS):
        return True
    # Skip short all-caps (likely headers)
    if text.isupper() and len(text) < 20:
        return True
    return False


def _extract_jurisdiction(text: str) -> tuple[str, Optional[str]]:
    """Extract jurisdiction from parentheses, e.g., 'Company LLC (Delaware)'"""
    match = re.search(r'\(([^)]+)\)\s*$', text)
    if match:
        return text[:match.start()].strip(), match.group(1).strip()
    return text, None


def _parse_table_format(html_content: str) -> list[SubsidiaryEntry]:
    """Parse table-based Exhibit 21 format."""
    entries = []
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html_content, flags=re.IGNORECASE | re.DOTALL)

    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, flags=re.IGNORECASE | re.DOTALL)
        if not cells:
            continue

        # Find first non-empty cell and count empty cells before it
        empty_count = 0
        name_idx = 0
        for i, cell in enumerate(cells):
            cell_text = re.sub(r'<[^>]+>', '', cell)
            cell_text = html.unescape(cell_text).strip().replace('\xa0', '')
            if cell_text:
                name_idx = i
                break
            empty_count += 1

        if name_idx >= len(cells):
            continue

        name_cell = cells[name_idx]

        # Calculate indent from empty cells + nbsp + CSS padding
        nbsp_count = name_cell.count('&#160;') + name_cell.count('&nbsp;') + name_cell.count('\xa0')
        indent = empty_count + (nbsp_count // 2)

        # Check CSS padding/margin
        for prop in ['padding-left', 'margin-left']:
            match = re.search(rf'{prop}:\s*(\d+)', name_cell, re.IGNORECASE)
            if match:
                indent = max(indent, int(match.group(1)) // 15)

        # Extract text
        text = html.unescape(re.sub(r'<[^>]+>', '', name_cell))
        text = ' '.join(text.split())

        if not text or len(text) < 3 or _should_skip(text):
            continue

        # Get jurisdiction from adjacent cell or parentheses
        jurisdiction = None
        for j in range(name_idx + 1, min(name_idx + 3, len(cells))):
            jur_text = html.unescape(re.sub(r'<[^>]+>', '', cells[j])).strip()
            if jur_text and 1 < len(jur_text) < 50 and jur_text.replace('\xa0', ''):
                jurisdiction = jur_text
                break

        if not jurisdiction:
            text, jurisdiction = _extract_jurisdiction(text)

        entries.append(SubsidiaryEntry(
            name=text, jurisdiction=jurisdiction,
            indent_level=indent, raw_line=name_cell[:100]
        ))

    return entries


def _parse_paragraph_format(html_content: str) -> list[SubsidiaryEntry]:
    """Parse paragraph-based format (like HCA's state-grouped list)."""
    entries = []

    # Try left-aligned paragraphs first, then generic
    p_matches = re.findall(
        r'<p[^>]*text-align:\s*left[^>]*>\s*<font[^>]*>([^<]+)</font>',
        html_content, flags=re.IGNORECASE
    )
    if not p_matches:
        p_matches = re.findall(
            r'<p[^>]*>\s*<font[^>]*>([^<]{5,100})</font>',
            html_content, flags=re.IGNORECASE
        )

    current_jurisdiction = None
    us_states_upper = {s.upper() for s in US_JURISDICTIONS}

    for match in p_matches:
        text = ' '.join(html.unescape(match).split())
        if not text or len(text) < 3:
            continue

        # Check if this is a state header
        if text.isupper() and len(text.split()) <= 2 and text in us_states_upper:
            current_jurisdiction = text.title()
            continue

        if _should_skip(text):
            continue

        name, paren_jurisdiction = _extract_jurisdiction(text)
        jurisdiction = paren_jurisdiction or current_jurisdiction

        if not name or len(name) < 3 or (name.isupper() and len(name) < 30):
            continue

        entries.append(SubsidiaryEntry(
            name=name, jurisdiction=jurisdiction,
            indent_level=0, raw_line=text[:100]
        ))

    return entries


def _parse_div_format(html_content: str) -> list[SubsidiaryEntry]:
    """Parse div-based format (like META's)."""
    entries = []
    matches = re.findall(
        r'<div[^>]*>\s*<font[^>]*>([^<]+)</font>\s*</div>',
        html_content, flags=re.IGNORECASE
    )

    for match in matches:
        text = ' '.join(html.unescape(match).split())
        if not text or len(text) < 3 or _should_skip(text):
            continue

        name, jurisdiction = _extract_jurisdiction(text)
        if not name or len(name) < 3:
            continue

        entries.append(SubsidiaryEntry(
            name=name, jurisdiction=jurisdiction,
            indent_level=0, raw_line=text[:100]
        ))

    return entries


def parse_exhibit21(raw_html: str) -> list[SubsidiaryEntry]:
    """Parse Exhibit 21 HTML to extract subsidiaries."""
    # Clean HTML
    clean = re.sub(r'<script[^>]*>.*?</script>', '', raw_html, flags=re.IGNORECASE | re.DOTALL)
    clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.IGNORECASE | re.DOTALL)

    # Try parsing formats in order of preference
    entries = _parse_table_format(clean)
    if not entries:
        entries = _parse_div_format(clean)
    if not entries:
        entries = _parse_paragraph_format(clean)

    return entries


def build_hierarchy(entries: list[SubsidiaryEntry], root_name: str) -> list[HierarchyRelationship]:
    """Build parent-child relationships from indented entries using stack."""
    if not entries:
        return []

    relationships = []
    parent_stack = [(0, root_name)]  # (indent_level, name)

    for entry in entries:
        # Pop parents at same or deeper level
        while parent_stack and parent_stack[-1][0] >= entry.indent_level:
            parent_stack.pop()

        parent_name = parent_stack[-1][1] if parent_stack else root_name
        ownership_type = 'direct' if entry.indent_level <= 1 else 'indirect'

        relationships.append(HierarchyRelationship(
            parent_name=parent_name,
            child_name=entry.name,
            ownership_type=ownership_type,
        ))

        parent_stack.append((entry.indent_level, entry.name))

    return relationships


# =============================================================================
# NAME MATCHING
# =============================================================================

def normalize_name(name: str) -> str:
    """Normalize entity name for matching."""
    name = name.lower()
    for suffix in [', inc.', ', inc', ' inc.', ' inc', ', llc', ' llc',
                   ', ltd.', ', ltd', ' ltd.', ' ltd', ', corp.', ', corp',
                   ' corp.', ' corp', ', corporation', ' corporation',
                   ', l.l.c.', ' l.l.c.', ', limited', ' limited',
                   ', l.p.', ' l.p.', ', lp', ' lp']:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    return ' '.join(re.sub(r'[^\w\s]', ' ', name).split()).strip()


# =============================================================================
# MAIN EXTRACTION
# =============================================================================

async def extract_ownership_hierarchy(
    session: AsyncSession,
    company_id: UUID,
    ticker: str,
    cik: str,
    company_name: str
) -> dict:
    """
    Extract complete ownership hierarchy.

    Phase 1: Parse Exhibit 21 for subsidiaries and indentation-based hierarchy
    Phase 2: Enrich with parent-child relationships from legal documents via LLM

    Returns dict with extraction stats.
    """
    stats = {
        'entries_found': 0,
        'entities_created': 0,
        'entities_updated': 0,
        'links_created': 0,
    }

    # Fetch Exhibit 21
    headers = {"User-Agent": "DebtStack research@debtstack.ai"}
    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        html_content = await fetch_exhibit21_html(cik, client)

    if not html_content:
        print(f"      No Exhibit 21 found")
        return stats

    # Parse entries
    entries = parse_exhibit21(html_content)
    stats['entries_found'] = len(entries)

    if not entries:
        print(f"      No entities parsed from Exhibit 21")
        return stats

    print(f"      Found {len(entries)} entities in Exhibit 21")

    # Build hierarchy from indentation
    relationships = build_hierarchy(entries, company_name)

    # Get existing entities and build lookup
    result = await session.execute(
        select(Entity).where(Entity.company_id == company_id)
    )
    existing = {normalize_name(e.name): e for e in result.scalars()}
    for e in list(existing.values()):
        existing[e.name.lower()] = e

    # Get holdco for root
    result = await session.execute(
        select(Entity).where(
            Entity.company_id == company_id,
            Entity.entity_type == 'holdco'
        )
    )
    holdco = result.scalar_one_or_none()
    if holdco:
        holdco.is_root = True
        existing[normalize_name(company_name)] = holdco
        existing[company_name.lower()] = holdco

    # Create missing entities (batch)
    new_entities = []
    for entry in entries:
        norm = normalize_name(entry.name)
        if norm not in existing and entry.name.lower() not in existing:
            entity = Entity(
                id=uuid4(),
                company_id=company_id,
                name=entry.name,
                slug=re.sub(r'[^a-z0-9]+', '-', entry.name.lower())[:255],
                entity_type='subsidiary',
                jurisdiction=entry.jurisdiction,
                structure_tier=3,
                is_domestic=entry.jurisdiction in US_JURISDICTIONS if entry.jurisdiction else True,
            )
            session.add(entity)
            new_entities.append(entity)
            existing[norm] = entity
            existing[entry.name.lower()] = entity

    if new_entities:
        await session.flush()
        stats['entities_created'] = len(new_entities)
        print(f"      Created {len(new_entities)} new entities")

    # Get existing ownership links for efficiency (batch query)
    result = await session.execute(
        select(OwnershipLink.child_entity_id).where(
            OwnershipLink.child_entity_id.in_([e.id for e in existing.values()])
        )
    )
    entities_with_links = {row[0] for row in result}

    # Update hierarchy relationships
    for rel in relationships:
        child = existing.get(normalize_name(rel.child_name)) or existing.get(rel.child_name.lower())
        parent = existing.get(normalize_name(rel.parent_name)) or existing.get(rel.parent_name.lower())

        if not parent and normalize_name(rel.parent_name) == normalize_name(company_name):
            parent = holdco

        if not child or not parent:
            continue

        # Update parent_id
        if child.parent_id != parent.id:
            child.parent_id = parent.id
            stats['entities_updated'] += 1

        # Create ownership link if needed
        if child.id not in entities_with_links:
            session.add(OwnershipLink(
                id=uuid4(),
                parent_entity_id=parent.id,
                child_entity_id=child.id,
                ownership_pct=100.0,
                ownership_type=rel.ownership_type,
            ))
            entities_with_links.add(child.id)
            stats['links_created'] += 1

    await session.commit()
    print(f"      Updated {stats['entities_updated']} parents, created {stats['links_created']} links")

    # Phase 2: Extract from legal documents for remaining orphans
    if stats['entities_created'] > 0:
        doc_stats = await _extract_ownership_from_docs(
            session, company_id, ticker, company_name, existing
        )
        stats['doc_relationships'] = doc_stats.get('relationships_found', 0)
        stats['doc_parents_updated'] = doc_stats.get('parents_updated', 0)

    return stats


async def _extract_ownership_from_docs(
    session: AsyncSession,
    company_id: UUID,
    ticker: str,
    company_name: str,
    entity_lookup: dict,
) -> dict:
    """Extract parent-child relationships from legal documents via LLM."""
    from app.core.config import get_settings

    stats = {'relationships_found': 0, 'parents_updated': 0, 'links_created': 0}

    settings = get_settings()
    if not settings.gemini_api_key:
        return stats

    # Get orphan entities
    result = await session.execute(
        select(Entity)
        .where(Entity.company_id == company_id)
        .where(Entity.parent_id.is_(None))
        .where(Entity.entity_type != 'holdco')
        .where(or_(Entity.structure_tier.is_(None), Entity.structure_tier != 1))
    )
    orphans = list(result.scalars())

    if not orphans:
        return stats

    # Get potential parents
    result = await session.execute(
        select(Entity)
        .where(Entity.company_id == company_id)
        .where(or_(
            Entity.parent_id.isnot(None),
            Entity.structure_tier == 1,
            Entity.entity_type == 'holdco',
        ))
    )
    parents = list(result.scalars())

    if not parents:
        return stats

    # Get document content
    result = await session.execute(
        select(DocumentSection)
        .where(DocumentSection.company_id == company_id)
        .where(DocumentSection.section_type.in_([
            'indenture', 'credit_agreement', 'guarantor_list'
        ]))
        .order_by(DocumentSection.filing_date.desc())
        .limit(5)
    )
    docs = list(result.scalars())

    if not docs:
        return stats

    # Build content (extract relevant sections from large docs)
    content_parts = []
    for doc in docs:
        if len(doc.content) > 50000:
            content_parts.append(f"=== {doc.section_type.upper()} ===\n{_extract_ownership_sections(doc.content)}")
        else:
            content_parts.append(f"=== {doc.section_type.upper()} ===\n{doc.content[:30000]}")
    content = "\n\n".join(content_parts)

    if len(content) < 1000:
        return stats

    print(f"      Extracting ownership from {len(docs)} docs ({len(orphans)} orphans)...")

    # Update entity lookup
    for e in parents:
        norm = normalize_name(e.name)
        if norm not in entity_lookup:
            entity_lookup[norm] = e
        if e.name.lower() not in entity_lookup:
            entity_lookup[e.name.lower()] = e

    # Call LLM
    import google.generativeai as genai
    from app.services.utils import parse_json_robust

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(
        model_name="gemini-2.0-flash",
        generation_config={"temperature": 0.1, "response_mime_type": "application/json"}
    )

    orphan_list = "\n".join([f"- {e.name}" for e in orphans[:100]])
    parent_list = "\n".join([f"- {e.name}" for e in parents[:50]])

    prompt = f"""Find parent-child ownership relationships in these SEC filings.

COMPANY: {company_name} ({ticker})

ORPHAN ENTITIES (need parent):
{orphan_list}

POTENTIAL PARENTS:
{parent_list}

DOCUMENTS:
{content[:80000]}

Look for: "X is a subsidiary of Y", "X owned by Y", guarantor lists.

Return JSON:
{{"ownership_relationships": [{{"child_entity": "exact orphan name", "parent_entity": "exact parent name", "ownership_type": "direct"}}]}}"""

    try:
        response = model.generate_content(prompt)
        result_data = parse_json_robust(response.text)

        for rel in result_data.get('ownership_relationships', []):
            child_name = (rel.get('child_entity') or '').strip()
            parent_name = (rel.get('parent_entity') or '').strip()

            child = entity_lookup.get(normalize_name(child_name)) or entity_lookup.get(child_name.lower())
            parent = entity_lookup.get(normalize_name(parent_name)) or entity_lookup.get(parent_name.lower())

            if not child or not parent or child.id == parent.id:
                continue

            stats['relationships_found'] += 1

            if child.parent_id is None:
                child.parent_id = parent.id
                stats['parents_updated'] += 1

        await session.commit()

        if stats['relationships_found'] > 0:
            print(f"      From docs: {stats['relationships_found']} relationships, {stats['parents_updated']} parents")

    except Exception as e:
        print(f"      Doc extraction error: {e}")

    return stats


def _extract_ownership_sections(content: str) -> str:
    """Extract ownership-related sections from large documents."""
    keywords = [r'subsidiar', r'wholly.?owned', r'parent', r'ownership', r'guarantor']

    parts = []
    lines = content.split('\n')
    in_section = False
    buffer = []

    for line in lines:
        line_lower = line.lower()
        is_header = len(line.strip()) < 100 and (
            line.strip().isupper() or
            re.match(r'^(section\s+)?\d+[\.\d]*\s+', line_lower) or
            re.match(r'^schedule\s+', line_lower)
        )

        if is_header:
            if in_section and buffer:
                text = '\n'.join(buffer)
                if len(text) > 100:
                    parts.append(text[:10000])
            in_section = any(re.search(kw, line_lower) for kw in keywords)
            buffer = [line] if in_section else []
        elif in_section:
            buffer.append(line)

    if in_section and buffer:
        text = '\n'.join(buffer)
        if len(text) > 100:
            parts.append(text[:10000])

    return '\n\n---\n\n'.join(parts) if parts else content[:30000]


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse
    import sys

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.orm import sessionmaker

    # Fix Windows encoding
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    # Add parent to path for imports
    sys.path.insert(0, str(__file__).replace('app/services/hierarchy_extraction.py', ''))

    from app.core.config import get_settings
    from app.models import Company

    async def main():
        parser = argparse.ArgumentParser(description="Extract ownership hierarchy from Exhibit 21")
        parser.add_argument("--ticker", help="Company ticker")
        parser.add_argument("--all", action="store_true", help="Process all companies")
        parser.add_argument("--limit", type=int, help="Limit companies")
        args = parser.parse_args()

        if not args.ticker and not args.all:
            print("Usage: python -m app.services.hierarchy_extraction --ticker CHTR")
            print("       python -m app.services.hierarchy_extraction --all [--limit N]")
            return

        settings = get_settings()
        engine = create_async_engine(
            settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
        )
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with async_session() as db:
            if args.ticker:
                companies = [await db.scalar(
                    select(Company).where(Company.ticker == args.ticker.upper())
                )]
            else:
                result = await db.execute(
                    select(Company).order_by(Company.ticker)
                )
                companies = list(result.scalars())
                if args.limit:
                    companies = companies[:args.limit]

        print(f"Processing {len(companies)} companies")
        total_created = 0
        total_updated = 0

        for company in companies:
            if not company:
                continue
            async with async_session() as db:
                print(f"[{company.ticker}] {company.name}")
                cik = company.cik or ''
                if not cik:
                    print("  No CIK, skipping")
                    continue
                stats = await extract_ownership_hierarchy(
                    db, company.id, company.ticker, cik, company.name
                )
                print(f"  Entities: +{stats.get('entities_created', 0)}, "
                      f"Parents: {stats.get('entities_updated', 0)}, "
                      f"Links: {stats.get('links_created', 0)}")
                total_created += stats.get('entities_created', 0)
                total_updated += stats.get('entities_updated', 0)
            await asyncio.sleep(0.5)

        print(f"\nTotal entities created: {total_created}")
        print(f"Total parents updated: {total_updated}")
        await engine.dispose()

    asyncio.run(main())
